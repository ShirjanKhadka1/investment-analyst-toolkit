"""Decision memory + public scoreboard — Engine 1.4 (Phase 1).

Pattern adapted (reimplemented from scratch, no code copied) from
TradingAgents' memory log + settlement vs a benchmark (Apache-2.0,
https://github.com/TauricResearch/TradingAgents — see
toolkit/SOURCES_MERGE_2026-11-07.md and docs/SOURCES_MERGE_2026-11-07.md
in finance-research-agents): every call is logged before its outcome
is known, settled after its horizon against realised prices, and
scored in public with losses included.

Rules that make this honest:
- The scoreboard starts EMPTY. Until a real call is logged and
  settled, every metric reads ``n=0 / no calls settled yet``. No
  history is invented, and synthetic fixtures in the tests never
  reach the published scoreboard.
- Settlement needs realised prices and an index return supplied by
  the caller, with the index source labelled. This module never
  invents index history: if the caller cannot name the index source,
  settlement refuses.
- A call is settled only after its horizon has passed (``as_of``
  date >= call date + horizon). Settling early is refused.
- Point-in-time: a call is scored from the first close *after* its
  date, so same-day news cannot leak into the score.
- Append-only with a hash chain (PR 7.0, after the November 2026
  overwrite incident): every row stores ``prev_hash`` — the sha256
  of the previous row's canonical JSON bytes (the first row stores
  ``"genesis"``). Settlement and baseline updates APPEND a new row
  (``record_type`` ``"settlement"`` / ``"baseline"``) instead of
  rewriting the call row, so a published log only ever grows.
  ``load_calls`` merges those rows back into their call for
  readers. ``verify_chain`` fails loudly on any broken link, which
  is how a tampered old row is caught. Old rows without
  ``prev_hash`` (written before PR 7.0) still read fine — the
  chain check starts at the first row that carries one.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta
from pathlib import Path

__all__ = [
    "GENESIS_PREV_HASH",
    "SCHEMA_VERSION",
    "append_reflection",
    "canonical_row_bytes",
    "load_calls",
    "load_reflections",
    "load_rows",
    "render_scoreboard_md",
    "row_hash",
    "scoreboard",
    "settle_call",
    "update_call",
    "verify_chain",
    "write_call",
]

SCHEMA_VERSION = "1.5.0"

# prev_hash of the first row in a log. A row without prev_hash was
# written before PR 7.0; the chain check starts at the first row
# that carries one (see verify_chain).
GENESIS_PREV_HASH = "genesis"

_REQUIRED = ("date", "symbol", "exchange", "thesis", "horizon_days",
             "expected", "sources", "confidence")
_EXPECTED = ("up", "down", "range")


def _parse_date(value, field):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{field} must be a YYYY-MM-DD date, got {value!r}") from None


def _call_id(call):
    """Deterministic id: sha256 of the call's content (no timestamps)."""
    payload = json.dumps(call, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def canonical_row_bytes(row):
    """Canonical bytes of one log row: compact JSON, sorted keys.

    The hash chain hashes THESE bytes, not the literal file line
    (the file is written with default separators; the canonical
    form is what both the writer and the verifier hash, so
    whitespace can never break a link).
    """
    return json.dumps(row, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")


def row_hash(row):
    """sha256 hex of a row's canonical bytes."""
    return hashlib.sha256(canonical_row_bytes(row)).hexdigest()


def load_rows(path):
    """Read all raw rows from a JSONL log, in file order, unmerged.

    Missing file -> empty list. Unlike ``load_calls``, settlement
    and baseline rows are returned as their own rows here — this is
    the view the chain check and the publish invariant use.
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
    # but the publish invariant compares bytes and git stores LF —
    # the log must be LF on every OS (PR 7.0 Windows fix).
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return row


def verify_chain(path):
    """Check the hash chain of a log. Returns a list of problems.

    Empty list means the chain holds. Checks, per row in order:
    a row carrying ``prev_hash`` must link to the hash of the row
    before it (the first row links to ``"genesis"``), and a call
    row's ``call_id`` must still match its content — so editing
    even the last row is caught. Rows written before PR 7.0 carry
    no ``prev_hash`` and are skipped for the link check, but they
    still anchor the next row's link.
    """
    rows = load_rows(path)
    problems = []
    expected = GENESIS_PREV_HASH
    for i, row in enumerate(rows):
        if "prev_hash" in row and row["prev_hash"] != expected:
            problems.append(
                f"row {i}: prev_hash mismatch — expected "
                f"{expected[:12]}…, found "
                f"{str(row['prev_hash'])[:12]}… "
                f"(row {i - 1} changed, or this row was inserted)")
        if row.get("record_type", "call") == "call" \
                and "call_id" in row:
            want = _call_id({k: v for k, v in row.items()
                             if k != "call_id"})
            if want != row["call_id"]:
                problems.append(
                    f"row {i}: call_id mismatch — the call's content "
                    "changed after it was logged")
        expected = row_hash(row)
    return problems


def write_call(path, call):
    """Append one call to a JSONL decision log. Returns the call dict.

    Required fields: date (YYYY-MM-DD), symbol, exchange (the market
    label, e.g. ``NEPSE`` or ``NYSE`` — REQUIRED since Round 3: a
    write without an exchange is refused, so a global call can never
    be silently labelled NEPSE), thesis, horizon_days (positive int),
    expected (``up`` / ``down`` / ``range``), sources (non-empty list
    of source labels), confidence (0..1, the stated probability the
    expected direction/range is right). Optional: expected_low /
    expected_high (for ``range`` calls), entry_close (the first close
    after the call date — the point-in-time reference price).
    Raises ValueError on any missing/invalid field. Never overwrites.
    Reads of logs written before the exchange requirement
    (``load_calls``) still default a missing exchange to ``NEPSE`` —
    the default lives only on the read path, never on writes.
    """
    if not isinstance(call, dict):
        raise ValueError("call must be a dict")  # noqa: TRY004
    for field in _REQUIRED:
        if field not in call or call[field] in (None, ""):
            raise ValueError(f"call is missing required field: {field}")
    if not isinstance(call["exchange"], str) \
            or not call["exchange"].strip():
        raise ValueError("exchange must be a non-empty string, "
                         "e.g. 'NEPSE'")
    _parse_date(call["date"], "date")
    horizon = call["horizon_days"]
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise ValueError(f"horizon_days must be a positive integer, got {horizon!r}")
    if call["expected"] not in _EXPECTED:
        raise ValueError(f"expected must be one of {_EXPECTED}, got {call['expected']!r}")
    if call["expected"] == "range" and (
            call.get("expected_low") is None or call.get("expected_high") is None):
        raise ValueError("range calls need expected_low and expected_high")
    sources = call["sources"]
    if not isinstance(sources, list) or not sources or any(
            not isinstance(s, str) or not s for s in sources):
        raise ValueError("sources must be a non-empty list of source labels")
    confidence = call["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        # Toolkit convention is ValueError for all input validation.
        raise ValueError(  # noqa: TRY004
            f"confidence must be a number in [0, 1], got {confidence!r}")
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError(f"confidence must be in [0, 1], got {confidence!r}")
    record = {"schema_version": SCHEMA_VERSION,
              "exchange": call["exchange"],
              "status": "open", **call}
    record["record_type"] = "call"
    # Chain link first: the call_id covers it, so moving a call to a
    # different position in the log changes its id.
    record["prev_hash"] = _chain_prev_hash(path)
    record["call_id"] = _call_id({k: v for k, v in record.items()
                                  if k != "call_id"})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": see _append_row — the log must be LF on every OS.
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    return record


def load_calls(path):
    """Read all calls from a JSONL log, settlement/baselines merged.

    Missing file -> empty list. Settlement rows (``record_type``
    ``"settlement"``) and baseline rows (``"baseline"``) are folded
    into their call by ``call_id`` — the returned list holds one
    entry per call, in log order, so existing readers (scoreboard,
    render, the daily script) see settled calls exactly as before.
    The raw rows stay untouched; use ``load_rows`` for them.

    Legacy read default: a record written before the exchange
    requirement (Round 3) has no ``exchange`` field; it reads back
    as ``NEPSE``. Writes never default — see ``write_call``.
    """
    calls = {}
    order = []
    for row in load_rows(path):
        kind = row.get("record_type", "call")
        if kind == "call":
            call = dict(row)
            call.setdefault("exchange", "NEPSE")
            calls[call["call_id"]] = call
            order.append(call["call_id"])
            continue
        if kind not in ("settlement", "baseline"):
            continue  # unknown row kind: ignore, never crash a read
        target = calls.get(row.get("call_id"))
        if target is None:
            continue  # orphan update for a call not in this log
        for key, value in row.items():
            if key not in ("record_type", "prev_hash", "call_id"):
                target[key] = value
        if kind == "settlement":
            target["status"] = "settled"
    return [calls[call_id] for call_id in order]


# Fields the daily track-record script may add to a SETTLED call after
# settlement: the baseline outcomes, computed from the same window.
# Nothing else may be updated — a call's identity, direction, entry
# and horizon are immutable once logged.
_UPDATEABLE = ("naive_expected", "naive_hit", "bnh_hit")


def update_call(path, call_id, **fields):
    """Add baseline outcomes to an already-settled call.

    Only ``naive_expected`` (``"up"``/``"down"``), ``naive_hit`` and
    ``bnh_hit`` (booleans) may be set, and only on a settled call.
    Refuses: unknown call_id, an open call, any other field, a bad
    naive direction. Returns the updated call. A call is never
    backfilled this way — baselines are scored outcomes, not edits
    to the prediction.

    Append-only (PR 7.0): the baselines APPEND a ``"baseline"``
    row chained to the previous row. The call row itself is never
    rewritten.
    """
    unknown = sorted(set(fields) - set(_UPDATEABLE))
    if unknown:
        raise ValueError(f"only {_UPDATEABLE} may be updated, "
                         f"not {unknown}")
    _check_baselines(fields.get("naive_expected"),
                     fields.get("naive_hit"), fields.get("bnh_hit"))
    calls = load_calls(path)
    target = None
    for call in calls:
        if call.get("call_id") == call_id:
            target = call
            break
    if target is None:
        raise ValueError(f"no call with call_id {call_id!r} in this log")
    if target.get("status") != "settled":
        raise ValueError(f"baselines are scored outcomes — call {call_id} "
                         "is still open")
    _append_row(path, {"record_type": "baseline", "call_id": call_id,
                       **fields})
    return next(c for c in load_calls(path)
                if c.get("call_id") == call_id)


def _check_baselines(naive_expected, naive_hit, bnh_hit):
    """Validate baseline outcomes (shared by settle/update)."""
    if naive_expected is not None \
            and naive_expected not in ("up", "down"):
        raise ValueError("naive_expected must be 'up' or 'down', got "
                         f"{naive_expected!r}")
    for name, value in (("naive_hit", naive_hit), ("bnh_hit", bnh_hit)):
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean, got {value!r}")


def settle_call(path, call_id, realised_close, entry_close,
                index_return, index_source, as_of,
                naive_expected=None, naive_hit=None, bnh_hit=None):
    """Settle one open call after its horizon. Returns the settled call.

    ``realised_close`` / ``entry_close`` are the realised close at
    the horizon and the first close after the call date; ``index_return``
    is the NEPSE index return over the same window as a fraction, and
    ``index_source`` names where that index series came from (a
    labelled source, or a clearly-labelled synthetic demo series —
    never an invented history). ``as_of`` is the settlement date.
    The optional baseline outcomes (``naive_expected``,
    ``naive_hit``, ``bnh_hit``) ride on the same settlement row
    when the caller already knows them; otherwise ``update_call``
    appends them later. Refuses: unknown call_id, already-settled
    call, settlement before the horizon has passed, missing index
    source, non-positive prices.

    Append-only (PR 7.0): settlement APPENDS a ``"settlement"``
    row chained to the previous row. The call row itself is never
    rewritten — ``load_calls`` folds the settlement back into the
    call for readers.
    """
    calls = load_calls(path)
    target = None
    for call in calls:
        if call.get("call_id") == call_id:
            target = call
            break
    if target is None:
        raise ValueError(f"no call with call_id {call_id!r} in this log")
    if target.get("status") == "settled":
        raise ValueError(f"call {call_id} is already settled")
    if not index_source or not isinstance(index_source, str):
        raise ValueError("index_source is required — never settle "
                         "against an unnamed index series")
    for name, value in (("realised_close", realised_close),
                        ("entry_close", entry_close)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a positive number, got {value!r}")
    if not isinstance(index_return, (int, float)) \
            or not math.isfinite(index_return):
        raise ValueError(f"index_return must be a finite number, got {index_return!r}")
    call_date = _parse_date(target["date"], "date")
    settle_date = _parse_date(as_of, "as_of")
    horizon_end = call_date + timedelta(days=int(target["horizon_days"]))
    if settle_date < horizon_end:
        raise ValueError(
            f"cannot settle before the horizon ends: call date "
            f"{call_date} + {target['horizon_days']}d = {horizon_end}, "
            f"as_of is {settle_date}")
    realised = float(realised_close) / float(entry_close) - 1.0
    alpha = realised - float(index_return)
    expected = target["expected"]
    if expected == "up":
        hit = realised > 0
        outcome_up = 1 if realised > 0 else 0
    elif expected == "down":
        hit = realised < 0
        outcome_up = 1 if realised > 0 else 0
    else:
        hit = float(target["expected_low"]) <= float(realised_close) \
            <= float(target["expected_high"])
        outcome_up = 1 if realised > 0 else 0
    confidence = float(target["confidence"])
    # Brier score on the stated probability of "up": for a "down" call
    # the implied P(up) is 1 - confidence; for "range" the confidence
    # is about the range, so Brier is not defined and stays None.
    if expected == "up":
        p_up = confidence
    elif expected == "down":
        p_up = 1.0 - confidence
    else:
        p_up = None
    brier = (p_up - outcome_up) ** 2 if p_up is not None else None
    row = {"record_type": "settlement", "call_id": call_id,
           "settled_date": settle_date.isoformat(),
           "realised_close": float(realised_close),
           "entry_close": float(entry_close),
           "realised_return": realised, "index_return": float(index_return),
           "index_source": index_source, "alpha": alpha,
           "hit": bool(hit), "brier": brier}
    if naive_expected is not None or naive_hit is not None \
            or bnh_hit is not None:
        _check_baselines(naive_expected, naive_hit, bnh_hit)
        row["naive_expected"] = naive_expected
        row["naive_hit"] = naive_hit
        row["bnh_hit"] = bnh_hit
    _append_row(path, row)
    return next(c for c in load_calls(path)
                if c.get("call_id") == call_id)


def scoreboard(calls):
    """Aggregate settled calls. Empty input -> all metrics n=0."""
    settled = [c for c in calls if c.get("status") == "settled"]
    n = len(settled)
    if n == 0:
        return {"n_settled": 0, "n_open": len(calls), "hit_rate": None,
                "average_alpha": None, "brier_score": None,
                "wins": 0, "losses": 0,
                "note": "no calls settled yet — n=0"}
    hits = sum(1 for c in settled if c.get("hit"))
    briers = [c["brier"] for c in settled if c.get("brier") is not None]
    return {"n_settled": n, "n_open": len(calls) - n,
            "hit_rate": hits / n,
            "average_alpha": sum(c["alpha"] for c in settled) / n,
            "brier_score": (sum(briers) / len(briers)) if briers else None,
            "wins": hits, "losses": n - hits,
            "note": "losses included — settled calls only, open calls "
                    "never count as wins"}


def _fmt(value, suffix=""):
    if value is None:
        return "n=0 — no calls settled yet"
    if suffix == "%":
        return f"{value * 100:.1f}%"
    return f"{value:.4f}"


def render_scoreboard_md(calls, index_source_label="TO VERIFY — no index "
                         "source named yet"):
    """Render the public track-record page from the log's calls."""
    board = scoreboard(calls)
    lines = [
        "# Track record — decision memory scoreboard",
        "",
        ("> Research only, not investment advice. This page scores past "
         "research calls after their horizons, with losses included. "
         "It is a record, not a recommendation."),
        "",
        (f"- Calls settled: **{board['n_settled']}** "
         f"(open: {board['n_open']})"),
        f"- Hit rate: **{_fmt(board['hit_rate'], '%')}**",
        f"- Average alpha vs NEPSE index: **{_fmt(board['average_alpha'])}**",
        f"- Brier score: **{_fmt(board['brier_score'])}**",
        f"- Wins / losses: **{board['wins']} / {board['losses']}**",
        "",
        (f"Index source for settlements: {index_source_label}. "
         "A settlement against an unnamed index series is refused by "
         "`toolkit/decision_log.py` — index history is never invented."),
        "",
    ]
    if board["n_settled"] == 0:
        lines += [("No calls have been logged and settled yet. The "
                   "scoreboard stays at n=0 until a real call exists, "
                   "is logged before its outcome is known, and is "
                   "settled after its horizon."),
                  ""]
    else:
        lines += ["## Settled calls", "",
                  "| Date | Symbol | Expected | Realised | Alpha | Hit |",
                  "|---|---|---|---|---|---|"]
        for call in calls:
            if call.get("status") != "settled":
                continue
            lines.append(
                f"| {call['date']} | {call['symbol']} | {call['expected']} | "
                 f"{call['realised_return']:.2%} | {call['alpha']:.2%} | "
                 f"{'yes' if call['hit'] else 'no'} |")
        lines.append("")
    lines += [("Regenerated by `toolkit/decision_log.py` "
               "(`render_scoreboard_md`) from the decision log — "
               "never edited by hand."),
              ""]
    return "\n".join(lines)


def append_reflection(path, call_id, text, author="portfolio-manager"):
    """Append one reflection to a reflections file (JSONL).

    Format: one JSON object per line — ``call_id``, ``date``,
    ``author`` and ``text``. Reflections are written after a call
    settles and are read by the Portfolio Manager agent before the
    next run on the same symbol (see its agent file).
    """
    if not text or not str(text).strip():
        raise ValueError("reflection text must not be empty")
    record = {"date": date.today().isoformat(), "call_id": call_id,
              "author": author, "text": str(text).strip()}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": see _append_row — the log must be LF on every OS.
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def load_reflections(path):
    """Read all reflections. Missing file -> empty list."""
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
