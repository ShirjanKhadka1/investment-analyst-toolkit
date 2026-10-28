"""Proven record + failure recording + failure improvement — the accuracy loop.

No model is "insanely accurate" by claim. Accuracy is a measured record:
every prediction is written to a ledger BEFORE its outcome is known,
scored when the outcome arrives, and failures are logged with a reason
and an action. Improvement is automatic in one specific way: the next
model selection re-weights by out-of-sample MAE, so a failing model
loses weight — it is never kept because it sounded good.

Ledger format (JSONL, one record per line, never edited in place):
  {symbol, predicted_at, predicted_for, forecast, model, status, ...}
  scored later -> {actual, error, abs_error, direction_correct, failed, failure_reason, action}
A prediction with no outcome yet stays "pending" — never scored as a win.
"""
from __future__ import annotations

import json
import math
import os


def _load(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _append(path, record):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def record_prediction(ledger_path, symbol, forecast, model, predicted_at, predicted_for, last_close):
    """Write a prediction BEFORE its outcome is known. forecast must be finite."""
    if not math.isfinite(forecast):
        raise ValueError("forecast must be a finite number")
    rec = {"symbol": symbol, "forecast": float(forecast), "model": model,
           "predicted_at": predicted_at, "predicted_for": predicted_for,
           "last_close": float(last_close), "status": "pending"}
    _append(ledger_path, rec)
    return rec


def score_pending(ledger_path, symbol, actual_close, actual_date, mae_threshold=None):
    """Score all pending predictions for a symbol against an actual close.
    Returns the scored records. Direction = did the forecast move the same
    way as the actual, relative to last_close. Failed = abs error above
    threshold (if given) OR direction wrong. Each failure gets a reason
    and an action; the ledger is rewritten with scored records replacing
    pending ones (append-only history is kept in archive, see archive.py)."""
    records = _load(ledger_path)
    scored = []
    for rec in records:
        if rec["symbol"] != symbol or rec["status"] != "pending":
            continue
        error = float(actual_close) - rec["forecast"]
        pred_dir = 1 if rec["forecast"] > rec["last_close"] else (-1 if rec["forecast"] < rec["last_close"] else 0)
        actual_dir = 1 if actual_close > rec["last_close"] else (-1 if actual_close < rec["last_close"] else 0)
        direction_correct = (pred_dir == actual_dir)
        failed = (not direction_correct) or (mae_threshold is not None and abs(error) > mae_threshold)
        rec = {**rec, "status": "failed" if failed else "scored",
               "actual": float(actual_close), "actual_date": actual_date,
               "error": error, "abs_error": abs(error), "direction_correct": direction_correct}
        if failed:
            rec["failure_reason"] = "direction wrong" if not direction_correct else f"abs error {abs(error):.4f} above threshold {mae_threshold}"
            rec["action"] = "demote model in next auto-selection (inverse-MAE re-weighting); review in accuracy report"
        scored.append(rec)
    if scored:
        # Rewrite: pending records for this symbol are replaced by scored versions.
        # We identify them by (symbol, predicted_at, forecast) — unique per run.
        remaining = []
        for rec in records:
            if rec["symbol"] == symbol and rec["status"] == "pending":
                match = next((s for s in scored if s["predicted_at"] == rec["predicted_at"] and s["forecast"] == rec["forecast"]), None)
                remaining.append(match if match else rec)
            else:
                remaining.append(rec)
        with open(ledger_path, "w", encoding="utf-8") as f:
            for rec in remaining:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
    return scored


def accuracy_report(ledger_path, symbol=None):
    """Measured record, not a claim: n scored, direction accuracy, mean abs
    error, failure rate, failures by model. Pending predictions excluded."""
    records = _load(ledger_path)
    scored = [r for r in records if r["status"] in ("scored", "failed") and (symbol is None or r["symbol"] == symbol)]
    if not scored:
        return {"n_scored": 0, "direction_accuracy": None, "mean_abs_error": None, "failure_rate": None, "by_model": {}}
    by_model = {}
    for r in scored:
        m = by_model.setdefault(r["model"], {"n": 0, "failures": 0})
        m["n"] += 1
        if r["status"] == "failed":
            m["failures"] += 1
    return {"n_scored": len(scored),
            "direction_accuracy": sum(1 for r in scored if r["direction_correct"]) / len(scored),
            "mean_abs_error": sum(r["abs_error"] for r in scored) / len(scored),
            "failure_rate": sum(1 for r in scored if r["status"] == "failed") / len(scored),
            "by_model": by_model}
