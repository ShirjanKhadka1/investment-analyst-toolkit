"""Append-only forecast ledger (PR 8.1).

A forecast row is written once and NEVER changes. A score is a
SEPARATE appended row that references the forecast id — scoring
never rewrites a forecast row. This is the fix for the
reports/daily/predictions.jsonl pattern, where scoring updates
rows in place and the strict append-only check fails by design.

The hash chain is reused, not reimplemented: canonical bytes and
the row hash come from toolkit/decision_log.py
(canonical_row_bytes / row_hash), the same chain the track record
uses. The chain is tamper-evident: it makes a rewrite visible, it does
not prevent one — the repo owner could still rewrite the file,
and the git history plus the verifier is what exposes a changed
earlier row.

Scope (PR 8.1): this module + its tests + the migration note
(docs/forecast-ledger-migration.md). It does NOT modify the
existing predictions.jsonl, does NOT change the autonomous
workflow that writes it, and does NOT touch any track-record
file. Switching the workflow to this ledger is a separate
proposal, not applied here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .decision_log import GENESIS_PREV_HASH, canonical_row_bytes, row_hash

__all__ = [
    "LEDGER_SCHEMA_VERSION",
    "append_forecast",
    "append_score",
    "forecast_id",
    "load_forecasts",
    "load_ledger",
    "load_rows",
    "load_scores",
    "migrate_development_period",
    "verify_ledger",
]

LEDGER_SCHEMA_VERSION = "1.0.0"

# The old ledger whose rows migrate as a labelled development
# period (see docs/forecast-ledger-migration.md). The migration
# reads it; it never writes to it.
DEVELOPMENT_PERIOD_SOURCE = "reports/daily/predictions.jsonl"

_FORECAST_REQUIRED = ("symbol", "predicted_at", "predicted_for",
                      "forecast", "last_close", "model")
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


def append_forecast(path, forecast):
    """Append one forecast row. Returns the stored row.

    Required fields: symbol, predicted_at, predicted_for,
    forecast, last_close, model. Optional: source (a label for
    where the forecast came from), development_period (True when
    the row migrated from the pre-protocol ledger — see
    migrate_development_period). Raises on any missing/invalid
    field. Never overwrites: appending the same forecast twice
    writes a second row with a different id (its prev_hash
    differs), it never replaces the first.
    """
    if not isinstance(forecast, dict):
        raise ValueError(  # noqa: TRY004 — toolkit convention is
            # ValueError for all input validation (decision_log.py).
            "forecast must be a dict")
    for field in _FORECAST_REQUIRED:
        if field not in forecast or forecast[field] in (None, ""):
            raise ValueError(f"forecast is missing required field: {field}")
    if not isinstance(forecast["symbol"], str) \
            or not forecast["symbol"].strip():
        raise ValueError("symbol must be a non-empty string")
    if not isinstance(forecast["model"], str) \
            or not forecast["model"].strip():
        raise ValueError("model must be a non-empty string")
    _validate_number(forecast["forecast"], "forecast")
    _validate_number(forecast["last_close"], "last_close")
    return _append_forecast_row(path, forecast)


def _append_forecast_row(path, forecast):
    """Append one forecast row with its id chained in. Returns it."""
    record = {"schema_version": LEDGER_SCHEMA_VERSION,
              "record_type": "forecast", **forecast}
    record["prev_hash"] = _chain_prev_hash(path)
    record["forecast_id"] = forecast_id(record)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    return record


def append_score(path, forecast_id_value, score):
    """Append one score row referencing a forecast id. Returns it.

    Required fields: actual, actual_date, status ("scored" or
    "failed"). Optional: error, direction_correct, scored_at.
    Refuses an unknown forecast id, and refuses a second score
    for the same forecast — a forecast is scored once; a
    correction is a new forecast row, never an edit or a second
    score. Never rewrites a forecast row.
    """
    if not isinstance(score, dict):
        raise ValueError(  # noqa: TRY004 — toolkit convention is
            # ValueError for all input validation (decision_log.py).
            "score must be a dict")
    if not isinstance(forecast_id_value, str) or not forecast_id_value:
        raise ValueError("forecast_id must be a non-empty string")
    for field in ("actual", "actual_date", "status"):
        if field not in score or score[field] in (None, ""):
            raise ValueError(f"score is missing required field: {field}")
    if score["status"] not in _SCORE_STATUSES:
        raise ValueError(
            f"status must be one of {_SCORE_STATUSES}, got "
            f"{score['status']!r}")
    _validate_number(score["actual"], "actual")
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


def verify_ledger(path):
    """Check the ledger. Returns a list of problems.

    Empty list means the ledger holds. Checks, per row in order:
    a row's prev_hash must link to the hash of the row before it
    (the first row links to "genesis"); a forecast row's
    forecast_id must still match its content (so editing even the
    last row is caught); every score must reference a forecast
    that exists in the ledger; no forecast id may appear twice;
    no forecast may carry two scores.
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


def migrate_development_period(source_path, dest_path):
    """Carry the old ledger over as a labelled development period.

    Reads reports/daily/predictions.jsonl (or any ledger in that
    shape) and writes a NEW append-only ledger at dest_path: one
    forecast row per old row (labelled development_period=True,
    source=DEVELOPMENT_PERIOD_SOURCE), plus one score row for each
    old row that already has an actual (status scored/failed).
    Pending rows migrate as unscored forecasts. The source file is
    never modified. The destination must not already hold rows —
    migration runs once; re-running would double the ledger.
    Returns {"forecasts_written", "scores_written", "pending",
    "source", "dest"}.
    """
    dest = Path(dest_path)
    if load_rows(dest):
        raise ValueError(
            f"destination ledger {str(dest_path)!r} already holds rows: "
            "migration runs once, it never appends to an existing ledger")
    source = Path(source_path)
    if not source.exists():
        raise ValueError(f"source ledger not found: {str(source_path)!r}")
    old_rows = [json.loads(line) for line in
                source.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    n_scores = 0
    n_pending = 0
    for old in old_rows:
        stored = _append_forecast_row(dest, {
            "symbol": old["symbol"],
            "predicted_at": old["predicted_at"],
            "predicted_for": old.get("predicted_for", "next_step"),
            "forecast": old["forecast"],
            "last_close": old["last_close"],
            "model": old["model"],
            "source": DEVELOPMENT_PERIOD_SOURCE,
            "development_period": True,
        })
        if old.get("actual") is not None:
            append_score(dest, stored["forecast_id"], {
                "actual": old["actual"],
                "actual_date": old.get("actual_date", ""),
                "error": old.get("error"),
                "direction_correct": old.get("direction_correct"),
                "status": old.get("status", "scored"),
            })
            n_scores += 1
        else:
            n_pending += 1
    return {"forecasts_written": len(old_rows),
            "scores_written": n_scores, "pending": n_pending,
            "source": str(source_path), "dest": str(dest_path)}
