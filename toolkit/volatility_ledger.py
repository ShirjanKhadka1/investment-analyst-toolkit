"""Forward volatility ledger (Round E, E.2).

A daily append-only ledger of one-day-ahead variance forecasts
(EWMA, GARCH-lite and the naive baseline) for the exact 7-ticker
watchlist, scored only on data after the protocol merges. The
protocol is docs/volatility-ledger-protocol.md — pre-registered
before any row existed; this module implements it, nothing else.

Shape (same as toolkit/forecast_ledger.py, PR 8.1): a forecast
row is written once and NEVER changes; a score is a SEPARATE
appended row that references the forecast id — scoring never
rewrites a forecast row. The hash chain is reused, not
reimplemented: canonical bytes and the row hash come from
toolkit/decision_log.py (canonical_row_bytes / row_hash), the
same chain the track record uses. The chain is tamper-evident:
it makes a rewrite visible, it does not prevent one — the repo
owner could still rewrite the file, and the git history plus
the verifier is what exposes a changed earlier row.

A forecast row exists ONLY for a complete origin bar: the
runner (scripts/volatility_ledger_daily.py) applies the track
record's bar_complete rule before appending, and append_forecast
refuses a row whose bar_complete is not True. An incomplete or
missing bar is a missed day, never a forecast.

No claim before the threshold: ledger_counts reports, per
symbol, how many forecasts are scored; threshold_met is True
only when every watchlist symbol has at least 100 scored
observations (MIN_SCORED_PER_SYMBOL). Before that, the ledger
holds forecasts and scores only — no verdict is computed here
at all. The verdict rule, when the threshold is met, is the
backtest's rule (docs/volatility-forecast-eval-protocol.md):
pooled mean QLIKE lower than the baseline AND the 95%
paired-bootstrap CI on the mean QLIKE difference entirely below
zero, else "no edge".
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
from pathlib import Path

from .decision_log import GENESIS_PREV_HASH, canonical_row_bytes, row_hash
from .volatility import MODELS as VOLATILITY_MODELS
from .volatility import mse_variance as _mse_variance
from .volatility import qlike as _qlike

__all__ = [
    "DATA_BRANCH",
    "LEDGER_FILE",
    "LEDGER_SCHEMA_VERSION",
    "MIN_SCORED_PER_SYMBOL",
    "MISSED_FILE",
    "MODELS",
    "WATCHLIST",
    "append_forecast",
    "append_score",
    "forecast_id",
    "ledger_counts",
    "load_forecasts",
    "load_ledger",
    "load_rows",
    "load_scores",
    "score_forecast",
    "verify_ledger",
]

LEDGER_SCHEMA_VERSION = "1.0.0"

# The exact 7-ticker watchlist approved by the owner on 13 Nov
# 2026 (docs/volatility-ledger-protocol.md). No additions.
WATCHLIST = ("SPY", "AAPL", "VT", "NIFTYBEES.NS", "ISF.L",
             "EURUSD=X", "BTC-USD")
MODELS = VOLATILITY_MODELS
DATA_BRANCH = "data/volatility-ledger"
LEDGER_FILE = "volatility-ledger.jsonl"
MISSED_FILE = "missed_days.jsonl"

# Claim threshold (protocol): no verdict before 100 scored
# observations per symbol.
MIN_SCORED_PER_SYMBOL = 100

_FORECAST_REQUIRED = ("symbol", "forecast_date", "origin_date",
                      "origin_close", "model", "forecast_variance",
                      "annualised_vol", "bar_complete", "source")
_SCORE_STATUSES = ("scored", "failed")


def forecast_id(forecast):
    """Deterministic id: sha256 of the row's content (no id field).

    The id covers prev_hash (set before the id is computed, the
    same order decision_log.write_call uses), so moving a forecast
    to a different position in the log changes its id.
    """
    payload = {k: v for k, v in forecast.items() if k != "forecast_id"}
    return hashlib.sha256(canonical_row_bytes(payload)).hexdigest()[:16]


def load_rows(path):
    """Read all raw rows from a ledger, in file order, unmerged.

    Missing file -> empty list. Score rows are returned as their
    own rows here — this is the view the chain check uses.
    """
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _chain_prev_hash(path):
    """prev_hash for a new row: the hash of the last row, or genesis."""
    rows = load_rows(path)
    if not rows:
        return GENESIS_PREV_HASH
    return row_hash(rows[-1])


def _append_row(path, row):
    """Append one row, chaining it to the previous row. Returns it."""
    row = dict(row)
    row["prev_hash"] = _chain_prev_hash(path)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": text mode translates "\n" to CRLF on Windows,
    # but the chain hashes canonical bytes, not file bytes — the
    # log must still be LF on every OS so the file is stable.
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return row


def _validate_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(  # noqa: TRY004 — toolkit convention is
            # ValueError for all input validation (decision_log.py).
            f"{field} must be a number, got {value!r}")


def _validate_date(value, field):
    try:
        _dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(
            f"{field} must be a YYYY-MM-DD date, got {value!r}"
            ) from exc


def append_forecast(path, forecast):
    """Append one forecast row. Returns the stored row.

    Required fields: symbol, forecast_date, origin_date,
    origin_close, model, forecast_variance, annualised_vol,
    bar_complete, source. The symbol must be on the watchlist,
    the model one of naive / ewma / garch_lite, both dates ISO,
    forecast_variance and annualised_vol positive numbers, and
    bar_complete must be True — a forecast from an incomplete
    bar is refused here, never logged. Never overwrites:
    appending the same forecast twice writes a second row with
    a different id (its prev_hash differs), it never replaces
    the first.
    """
    if not isinstance(forecast, dict):
        raise ValueError(  # noqa: TRY004 — toolkit convention is
            # ValueError for all input validation (decision_log.py).
            "forecast must be a dict")
    for field in _FORECAST_REQUIRED:
        if field not in forecast or forecast[field] in (None, ""):
            raise ValueError(
                f"forecast is missing required field: {field}")
    if forecast["symbol"] not in WATCHLIST:
        raise ValueError(
            f"symbol {forecast['symbol']!r} is not on the volatility "
            f"ledger watchlist {list(WATCHLIST)}")
    if forecast["model"] not in MODELS:
        raise ValueError(
            f"unknown volatility model {forecast['model']!r}; "
            f"expected one of {list(MODELS)}")
    _validate_date(forecast["forecast_date"], "forecast_date")
    _validate_date(forecast["origin_date"], "origin_date")
    _validate_number(forecast["origin_close"], "origin_close")
    _validate_number(forecast["forecast_variance"],
                     "forecast_variance")
    _validate_number(forecast["annualised_vol"], "annualised_vol")
    if forecast["forecast_variance"] <= 0:
        raise ValueError(
            "forecast_variance must be > 0, got "
            f"{forecast['forecast_variance']!r}")
    if forecast["annualised_vol"] <= 0:
        raise ValueError(
            "annualised_vol must be > 0, got "
            f"{forecast['annualised_vol']!r}")
    if forecast["bar_complete"] is not True:
        raise ValueError(
            "bar_complete must be True: a forecast from an "
            "incomplete bar is never logged — record a missed day "
            "instead")
    if not isinstance(forecast["source"], str) \
            or not forecast["source"].strip():
        raise ValueError("source must be a non-empty string")
    record = {"schema_version": LEDGER_SCHEMA_VERSION,
              "record_type": "forecast", **forecast}
    record["prev_hash"] = _chain_prev_hash(path)
    record["forecast_id"] = forecast_id(record)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    return record


def score_forecast(forecast, realised_variance, actual_date):
    """Score dict for one forecast row (pure: appends nothing).

    realised_variance is the squared log return of the first bar
    after the origin bar; actual_date is that bar's date. QLIKE
    is primary, MSE on variance secondary — the same two scores
    as the backtest. Raises on a negative realised variance or
    a bad date instead of scoring nonsense.
    """
    _validate_date(actual_date, "actual_date")
    _validate_number(realised_variance, "realised_variance")
    if realised_variance < 0:
        raise ValueError(
            f"realised_variance must be >= 0, got {realised_variance!r}")
    return {
        "actual_date": str(actual_date),
        "realised_variance": float(realised_variance),
        "qlike": _qlike(realised_variance,
                        forecast["forecast_variance"]),
        "mse_variance": _mse_variance(
            realised_variance, forecast["forecast_variance"]),
        "status": "scored",
    }


def append_score(path, forecast_id_value, score):
    """Append one score row referencing a forecast id. Returns it.

    Required fields: actual_date, realised_variance, qlike,
    mse_variance, status ("scored" or "failed"). Refuses an
    unknown forecast id, and refuses a second score for the same
    forecast — a forecast is scored once; a correction is a new
    forecast row, never an edit or a second score. Never
    rewrites a forecast row.
    """
    if not isinstance(score, dict):
        raise ValueError(  # noqa: TRY004 — toolkit convention is
            # ValueError for all input validation (decision_log.py).
            "score must be a dict")
    if not isinstance(forecast_id_value, str) or not forecast_id_value:
        raise ValueError("forecast_id must be a non-empty string")
    for field in ("actual_date", "realised_variance", "qlike",
                  "mse_variance", "status"):
        if field not in score or score[field] in (None, ""):
            raise ValueError(
                f"score is missing required field: {field}")
    if score["status"] not in _SCORE_STATUSES:
        raise ValueError(
            f"status must be one of {_SCORE_STATUSES}, got "
            f"{score['status']!r}")
    _validate_date(score["actual_date"], "actual_date")
    _validate_number(score["realised_variance"], "realised_variance")
    _validate_number(score["qlike"], "qlike")
    _validate_number(score["mse_variance"], "mse_variance")
    rows = load_rows(path)
    ids = {r.get("forecast_id") for r in rows
           if r.get("record_type") == "forecast"}
    if forecast_id_value not in ids:
        raise ValueError(
            f"unknown forecast_id {forecast_id_value!r}: scores may "
            "only reference forecasts already in this ledger")
    if any(r.get("record_type") == "score"
           and r.get("forecast_id") == forecast_id_value for r in rows):
        raise ValueError(
            f"forecast {forecast_id_value!r} already has a score: "
            "a forecast is scored once, never re-scored")
    record = {"schema_version": LEDGER_SCHEMA_VERSION,
              "record_type": "score", "forecast_id": forecast_id_value,
              **score}
    return _append_row(path, record)


def load_forecasts(path):
    """Forecast rows only, in file order."""
    return [r for r in load_rows(path)
            if r.get("record_type") == "forecast"]


def load_scores(path):
    """Score rows only, in file order."""
    return [r for r in load_rows(path)
            if r.get("record_type") == "score"]


def load_ledger(path):
    """Merged view: one entry per forecast, scores attached.

    Returns a list of forecast dicts, each with a "scores" list
    (zero or one score — a forecast is scored at most once).
    """
    forecasts = load_forecasts(path)
    by_id = {}
    for score in load_scores(path):
        by_id.setdefault(score["forecast_id"], []).append(score)
    out = []
    for forecast in forecasts:
        entry = dict(forecast)
        entry["scores"] = by_id.get(forecast["forecast_id"], [])
        out.append(entry)
    return out


def ledger_counts(path):
    """Counts for the claim threshold, per symbol and pooled.

    Returns {"per_symbol": {symbol: {"forecasts", "scored"}},
    "scored_total", "threshold_met"} where threshold_met is True
    only when EVERY watchlist symbol has at least
    MIN_SCORED_PER_SYMBOL scored observations. No verdict is
    computed here — before the threshold the ledger reports
    counts only.
    """
    forecasts = load_forecasts(path)
    scores = load_scores(path)
    scored_ids = {s["forecast_id"] for s in scores
                  if s.get("status") == "scored"}
    per_symbol = {}
    for symbol in WATCHLIST:
        rows = [f for f in forecasts if f["symbol"] == symbol]
        per_symbol[symbol] = {
            "forecasts": len(rows),
            "scored": sum(1 for f in rows
                          if f["forecast_id"] in scored_ids),
        }
    scored_total = sum(v["scored"] for v in per_symbol.values())
    threshold_met = all(
        v["scored"] >= MIN_SCORED_PER_SYMBOL
        for v in per_symbol.values())
    return {"per_symbol": per_symbol,
            "scored_total": scored_total,
            "threshold_met": threshold_met}


def verify_ledger(path):
    """Check the ledger. Returns a list of problems.

    Empty list means the ledger holds. Checks, per row in order:
    a row's prev_hash must link to the hash of the row before it
    (the first row links to "genesis"); a forecast row's
    forecast_id must still match its content (so editing even the
    last row is caught); every score must reference a forecast
    that exists in the ledger; no forecast id may appear twice;
    no forecast may carry two scores; every forecast row's symbol
    must be on the watchlist and its bar_complete must be True.
    """
    rows = load_rows(path)
    problems = []
    expected = GENESIS_PREV_HASH
    seen_ids = set()
    scored_ids = set()
    for i, row in enumerate(rows):
        if "prev_hash" in row and row["prev_hash"] != expected:
            problems.append(
                f"row {i}: prev_hash mismatch — expected "
                f"{expected[:12]}…, found "
                f"{str(row['prev_hash'])[:12]}… "
                f"(row {i - 1} changed, or this row was inserted)")
        record_type = row.get("record_type", "forecast")
        if record_type == "forecast":
            fid = row.get("forecast_id")
            if fid is not None:
                if fid in seen_ids:
                    problems.append(
                        f"row {i}: duplicate forecast_id {fid!r}")
                seen_ids.add(fid)
                want = forecast_id(row)
                if want != fid:
                    problems.append(
                        f"row {i}: forecast_id mismatch — the "
                        "forecast's content changed after it was logged")
            if row.get("symbol") not in WATCHLIST:
                problems.append(
                    f"row {i}: symbol {row.get('symbol')!r} is not on "
                    "the volatility ledger watchlist")
            if row.get("bar_complete") is not True:
                problems.append(
                    f"row {i}: bar_complete is not True — a forecast "
                    "from an incomplete bar must never be logged")
        elif record_type == "score":
            fid = row.get("forecast_id")
            if fid not in seen_ids:
                problems.append(
                    f"row {i}: score references unknown forecast_id "
                    f"{fid!r}")
            elif fid in scored_ids:
                problems.append(
                    f"row {i}: second score for forecast_id {fid!r}")
            else:
                scored_ids.add(fid)
        expected = row_hash(row)
    return problems


def annualised_vol(forecast_variance, periods_per_year=252):
    """Annualised volatility implied by a daily variance forecast.

    The same 252-day convention is reported for every symbol,
    including BTC-USD and EURUSD=X (a 24-hour market has no
    252-day session year; the figure is a comparability
    convention, and the ledger doc says so). Raises on a
    negative variance or a non-positive year length.
    """
    if isinstance(forecast_variance, bool) \
            or not isinstance(forecast_variance, (int, float)):
        raise ValueError(  # noqa: TRY004 — toolkit convention is
            # ValueError for all input validation (decision_log.py).
            f"forecast_variance must be a number, got "
            f"{forecast_variance!r}")
    if forecast_variance < 0:
        raise ValueError(
            f"forecast_variance must be >= 0, got {forecast_variance!r}")
    if periods_per_year <= 0:
        raise ValueError(
            f"periods_per_year must be positive, got {periods_per_year}")
    return math.sqrt(forecast_variance * periods_per_year)
