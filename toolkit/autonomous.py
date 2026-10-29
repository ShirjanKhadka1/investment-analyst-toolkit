"""Autonomous auto-updating research runner — research only, no trades.

run_once(config): for each symbol, fetch fresh data (fallback chain +
freshness gate), validate it, run predictive auto-selection + performance,
and write a timestamped report + a `latest` report. One symbol failing
never kills the whole run: its error is recorded, its previous report is
kept and marked STALE, and no number is invented to fill the gap.

run_loop(config): repeats run_once on an interval. Stop it with Ctrl-C;
it never trades, never spends money, and never sends anything anywhere.

Config (dict): symbols (list), output_dir (str), max_age_days (int, default 7),
fetchers (optional list of (name, fn) for tests/production), now (optional
datetime for tests). Keys come from the environment only (see data.get_api_key).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os

from . import archive as archive_mod
from . import data as data_mod
from . import tracker as tracker_mod
from .checks import validate_series
from .performance import performance_report
from .portfolio import returns_from_prices
from .predictive import (
    anomaly_flags,
    auto_select_model,
    ensemble_forecast,
    volatility_regime,
)


def _audit_hash(prev, entry):
    payload = json.dumps({"prev": prev, "entry": entry}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def run_once(config):
    symbols = config.get("symbols") or []
    if not symbols:
        raise ValueError("config['symbols'] must be a non-empty list")
    out_dir = config.get("output_dir", "autonomous_reports")
    max_age = config.get("max_age_days", 7)
    today = config.get("today") or datetime.date.today()
    stamp = (config.get("now") or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    fetchers = config.get("fetchers")
    if fetchers is None:
        fetchers = [("yahoo", data_mod.fetch_yahoo_prices), ("stooq", lambda s: data_mod.fetch_stooq_prices(s.lower() + ".us"))]
    summary = {"run_at": stamp, "symbols": {}}
    audit_path = os.path.join(out_dir, "audit.jsonl")
    prev_hash = "GENESIS"
    if os.path.exists(audit_path):
        with open(audit_path, encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
            if lines:
                prev_hash = json.loads(lines[-1])["hash"]
    for symbol in symbols:
        try:
            fresh = data_mod.fetch_fresh_prices(symbol, fetchers, max_age_days=max_age, today=today)
            closes = [c for _, c in fresh["pairs"]]
            # Accuracy loop: score yesterday's predictions against today's actual, then record today's prediction.
            ledger_path = os.path.join(out_dir, "predictions.jsonl")
            scored = tracker_mod.score_pending(ledger_path, symbol, closes[-1], fresh["pairs"][-1][0])
            tracker_mod.record_prediction(ledger_path, symbol, 0.0, "placeholder", "x", "x", closes[-1]) if False else None
            validate_series(f"closes for {symbol}", closes, min_length=30)
            selection = auto_select_model(closes, train_size=max(10, len(closes) // 2))
            ens = ensemble_forecast(closes, series_for_weights=closes, train_size=max(10, len(closes) // 2))
            perf = performance_report(returns_from_prices(closes), closes)
            anomalies = anomaly_flags(closes)
            regime = volatility_regime(closes) if len(closes) >= 22 else {"regime": "unknown (too short)", "latest_std": None, "median_std": None}
            tracker_mod.record_prediction(ledger_path, symbol, ens["forecast"], selection["best_model"], stamp, "next_step", closes[-1])
            acc = tracker_mod.accuracy_report(ledger_path, symbol)
            body = {"symbol": symbol, "status": "fresh", "provenance": fresh["provenance"],
                    "scored_this_run": len(scored), "accuracy": acc,
                    "best_model": selection["best_model"], "best_mae": selection["best_mae"],
                    "beats_naive": selection["beats_naive"], "ensemble_forecast": ens["forecast"],
                    "volatility_regime": regime["regime"], "anomalies": anomalies,
                    "performance": perf, "disclaimer": "Research only. Not investment advice. No trade is made by this system."}
            report = f"# Autonomous Research Update — {symbol}\n\nRun: {stamp} | Status: FRESH\n\nSource: {fresh['provenance']['source']} | Last date: {fresh['provenance']['last_date']} ({fresh['provenance']['age_days']} days old) | Points: {fresh['provenance']['n_points']}\n\nBest predictive model (walk-forward MAE): {selection['best_model']} (MAE {selection['best_mae']:.4f}, beats naive: {selection['beats_naive']})\n\nEnsemble forecast (next step): {ens['forecast']:.4f} ({ens['weighting']})\n\nVolatility regime: {regime['regime']} | Anomalies flagged: {len(anomalies)}\n\nPerformance: {perf}\n\n*Research only. Not investment advice. No trade is made by this system.*\n"
        except Exception as e:  # noqa: BLE001 — per-symbol failure must not kill the run
            body = {"symbol": symbol, "status": "STALE_OR_FAILED", "error": str(e),
                    "note": "Previous report kept unchanged. No data was invented."}
            report = f"# Autonomous Research Update — {symbol}\n\nRun: {stamp} | Status: STALE_OR_FAILED\n\nError: {e}\n\nPrevious report kept unchanged. No data was invented.\n\n*Research only. Not investment advice.*\n"
        summary["symbols"][symbol] = body
        _write(os.path.join(out_dir, symbol, f"{stamp}.md"), report)
        # Last-known-good rule: latest.md is updated ONLY on a fresh
        # run. On failure the timestamped report above IS the STALE
        # marker, and the previous latest.md is kept unchanged — a
        # failed run must never overwrite the last good report with
        # an error page, and stale output is never published as fresh.
        if body["status"] == "fresh":
            _write(os.path.join(out_dir, symbol, "latest.md"), report)
        archive_mod.archive_run(out_dir, symbol, report, body, stamp)
        entry = {"symbol": symbol, "status": body["status"], "run_at": stamp}
        h = _audit_hash(prev_hash, entry)
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"prev_hash": prev_hash, "hash": h, **entry}) + "\n")
        prev_hash = h
    _write(os.path.join(out_dir, f"summary-{stamp}.json"), json.dumps(summary, indent=2))
    return summary


def run_loop(config, max_runs=None, sleep_fn=None):
    """Repeat run_once every config['interval_seconds'] (default 86400 = daily).
    sleep_fn is injectable for tests; pass max_runs to bound a run."""
    import time
    interval = config.get("interval_seconds", 86400)
    if interval <= 0:
        raise ValueError("interval_seconds must be positive")
    runs = 0
    while max_runs is None or runs < max_runs:
        run_once(config)
        runs += 1
        if max_runs is not None and runs >= max_runs:
            break
        (sleep_fn or time.sleep)(interval)
    return runs
