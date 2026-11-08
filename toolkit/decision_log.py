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
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta
from pathlib import Path

__all__ = [
    "SCHEMA_VERSION",
    "append_reflection",
    "load_calls",
    "load_reflections",
    "render_scoreboard_md",
    "scoreboard",
    "settle_call",
    "write_call",
]

SCHEMA_VERSION = "1.4.0"

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
    record["call_id"] = _call_id({k: v for k, v in record.items()
                                  if k != "call_id"})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    return record


def load_calls(path):
    """Read all calls from a JSONL log. Missing file -> empty list.

    Legacy read default: a record written before the exchange
    requirement (Round 3) has no ``exchange`` field; it reads back
    as ``NEPSE``. Writes never default — see ``write_call``.
    """
    path = Path(path)
    if not path.exists():
        return []
    calls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            call = json.loads(line)
            call.setdefault("exchange", "NEPSE")
            calls.append(call)
    return calls


def _write_all(path, calls):
    path = Path(path)
    with path.open("w", encoding="utf-8") as fh:
        for call in calls:
            fh.write(json.dumps(call, sort_keys=True, default=str) + "\n")


def settle_call(path, call_id, realised_close, entry_close,
                index_return, index_source, as_of):
    """Settle one open call after its horizon. Returns the settled call.

    ``realised_close`` / ``entry_close`` are the realised close at the
    horizon and the first close after the call date; ``index_return``
    is the NEPSE index return over the same window as a fraction, and
    ``index_source`` names where that index series came from (a
    labelled source, or a clearly-labelled synthetic demo series —
    never an invented history). ``as_of`` is the settlement date.
    Refuses: unknown call_id, already-settled call, settlement before
    the horizon has passed, missing index source, non-positive prices.
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
    target.update({"status": "settled", "settled_date": settle_date.isoformat(),
                   "realised_close": float(realised_close),
                   "entry_close": float(entry_close),
                   "realised_return": realised, "index_return": float(index_return),
                   "index_source": index_source, "alpha": alpha,
                   "hit": bool(hit), "brier": brier})
    _write_all(path, calls)
    return target


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
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def load_reflections(path):
    """Read all reflections. Missing file -> empty list."""
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
