"""Agent run event log — Round U, PR U.1 (Event schema and recorder).

One JSON object per line in ``events.jsonl``: every agent-team run
(rules-mode NEPSE/global teams, the autonomous runner) can record a
schema-validated, versioned, hash-chained trace of what it did —
``run_started``, ``plan``, ``step_started``, ``tool_call``,
``tool_result``, ``evidence_ref``, ``debate_turn``, ``gate_result``,
``memo_section``, ``run_finished`` and ``error``.

The schema is pre-registered in
``docs/u1-event-schema-protocol.md`` — this module implements that
protocol, it does not define it. The hash-chain rule follows
``toolkit/decision_log.py`` (PR 7.0): append-only rows, ``prev_hash``
is the sha256 of the previous row's canonical JSON bytes, the first
row stores ``"genesis"``, and ``verify_events`` fails loudly on a
broken link.

Stdlib only. No network, no LLM, nothing fetched: every payload value
comes from the caller or from the run being recorded. The recorder is
opt-in — the hooks in ``toolkit/rules_mode.py`` and
``toolkit/autonomous.py`` take ``recorder=None`` by default, and with
no recorder their behaviour is byte-identical to before.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "EVENT_TYPES",
    "GENESIS_PREV_HASH",
    "SCHEMA_VERSION",
    "EventRecorder",
    "canonical_event_bytes",
    "event_hash",
    "load_events",
    "validate_event",
    "verify_chain",
    "verify_events",
]

SCHEMA_VERSION = "1.0.0"

# prev_hash of the first row in a log.
GENESIS_PREV_HASH = "genesis"

EVENT_TYPES = (
    "run_started",
    "plan",
    "step_started",
    "tool_call",
    "tool_result",
    "evidence_ref",
    "debate_turn",
    "gate_result",
    "memo_section",
    "run_finished",
    "error",
)

# Common required fields on every event.
_COMMON_REQUIRED = ("schema_version", "run_id", "seq", "ts", "type")

# Per-type required fields: (field, kind) where kind is "str" (a
# non-empty string), "steps" (a non-empty list of strings) or "enum"
# (a value from the third element's set).
_REQUIRED = {
    "run_started": (("team", "str"),),
    "plan": (("steps", "steps"),),
    "step_started": (("step", "str"),),
    "tool_call": (("tool", "str"),),
    "tool_result": (("tool", "str"),
                    ("status", "enum", ("ok", "failed"))),
    "evidence_ref": (("source", "str"),),
    "debate_turn": (("speaker", "str"), ("text", "str")),
    "gate_result": (("gate", "str"),
                    ("verdict", "enum", ("PASS", "BLOCK"))),
    "memo_section": (("section", "str"),),
    "run_finished": (("status", "enum", ("ok", "failed")),),
    "error": (("error", "str"),),
}

# Per-type optional fields: (field, kind). An optional field that IS
# present must still have the right kind.
_OPTIONAL = {
    "run_started": (("symbol", "str"), ("symbols", "steps"),
                    ("exchange", "str"), ("market", "str")),
    "plan": (("symbols", "steps"),),
    "step_started": (("symbol", "str"),),
    "tool_call": (("step", "str"), ("symbol", "str")),
    "tool_result": (("step", "str"), ("symbol", "str"),
                    ("n_points", "int"), ("detail", "str")),
    "evidence_ref": (("symbol", "str"), ("step", "str"),
                     ("last_date", "str"), ("n_points", "int")),
    "debate_turn": (("stance", "str"), ("step", "str")),
    "gate_result": (("symbol", "str"), ("reason", "str")),
    "memo_section": (("symbol", "str"), ("status", "str")),
    "run_finished": (("symbol", "str"), ("symbols", "steps")),
    "error": (("symbol", "str"), ("step", "str")),
}


def _is_non_empty_str(value):
    return isinstance(value, str) and bool(value.strip())


def _is_steps(value):
    return (isinstance(value, list) and bool(value)
            and all(_is_non_empty_str(v) for v in value))


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check_field(event, field, kind, allowed=None):
    """Validate one field's kind. Returns an error string or None."""
    value = event.get(field)
    if kind == "str":
        if not _is_non_empty_str(value):
            return f"{field} must be a non-empty string, got {value!r}"
    elif kind == "steps":
        if not _is_steps(value):
            return (f"{field} must be a non-empty list of non-empty "
                    f"strings, got {value!r}")
    elif kind == "int":
        if not _is_int(value):
            return f"{field} must be an integer, got {value!r}"
    elif kind == "enum" and value not in allowed:
        return (f"{field} must be one of {sorted(allowed)}, "
                f"got {value!r}")
    return None


def _parse_ts(value):
    """Parse an ISO 8601 timestamp. Returns a datetime or None.

    ``datetime.fromisoformat`` on Python 3.10 does not accept the
    ``Z`` suffix, so it is normalised to ``+00:00`` first. The
    timestamp must be timezone-aware — a naive timestamp is refused.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _now_iso():
    """Current UTC time as an ISO 8601 ``...Z`` string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_event_bytes(event):
    """Canonical bytes of one event: compact JSON, sorted keys.

    The hash chain hashes THESE bytes, not the literal file line —
    the same rule as ``toolkit/decision_log.py``, so whitespace can
    never break a link.
    """
    return json.dumps(event, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")


def event_hash(event):
    """sha256 hex of an event's canonical bytes."""
    return hashlib.sha256(canonical_event_bytes(event)).hexdigest()


def validate_event(event):
    """Validate one event against the U.1 schema. Raises ValueError.

    Checks: the event is a dict; ``type`` is one of the eleven event
    types; every common field is present and valid (``schema_version``
    is ``"1.0.0"``, ``run_id`` non-empty, ``seq`` a non-negative
    integer, ``ts`` a timezone-aware ISO 8601 timestamp); every
    required per-type field is present with the right kind; any
    optional field present has the right kind; and the event is
    JSON-serialisable. Pure: no reads, no writes, no network.
    """
    if not isinstance(event, dict):
        # Toolkit convention is ValueError for all input validation.
        raise ValueError(  # noqa: TRY004
            f"event must be a dict, got {type(event).__name__}")
    for field in _COMMON_REQUIRED:
        if field not in event:
            raise ValueError(f"event is missing required field: {field}")
    event_type = event.get("type")
    if event_type not in EVENT_TYPES:
        raise ValueError(
            f"event type must be one of {list(EVENT_TYPES)}, "
            f"got {event_type!r}")
    if event.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must be {SCHEMA_VERSION!r}, got "
            f"{event.get('schema_version')!r}")
    if not _is_non_empty_str(event.get("run_id")):
        raise ValueError(
            f"run_id must be a non-empty string, got "
            f"{event.get('run_id')!r}")
    seq = event.get("seq")
    if not _is_int(seq) or seq < 0:
        raise ValueError(f"seq must be a non-negative integer, got {seq!r}")
    if _parse_ts(event.get("ts")) is None:
        raise ValueError(
            f"ts must be a timezone-aware ISO 8601 timestamp, got "
            f"{event.get('ts')!r}")
    for field, kind, *rest in _REQUIRED[event_type]:
        if field not in event:
            raise ValueError(
                f"{event_type} event is missing required field: {field}")
        problem = _check_field(event, field, kind,
                               rest[0] if rest else None)
        if problem is not None:
            raise ValueError(f"{event_type} event: {problem}")
    for field, kind, *rest in _OPTIONAL[event_type]:
        if field in event:
            problem = _check_field(event, field, kind,
                                   rest[0] if rest else None)
            if problem is not None:
                raise ValueError(f"{event_type} event: {problem}")
    try:
        json.dumps(event)
    except (TypeError, ValueError):
        raise ValueError(
            "event must be JSON-serialisable") from None


def load_events(path):
    """Read all events from a JSONL log, in file order.

    Missing file -> empty list. Lines that are not valid JSON are
    returned as-is? No — a malformed line is a broken log, so it
    raises the JSON error instead of silently skipping it.
    """
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify_events(path):
    """Check the schema and hash chain of an event log.

    Returns a list of problems; an empty list means the log is valid.
    Per row, in order: the row validates against the schema; ``seq``
    equals its 0-based file position; ``prev_hash`` equals the hash
    of the previous row (``"genesis"`` for the first). A tampered,
    inserted or reordered row is reported, never silently passed.
    A missing file verifies as an empty (valid) log.
    """
    rows = load_events(path)
    problems = []
    expected_prev = GENESIS_PREV_HASH
    for i, row in enumerate(rows):
        try:
            validate_event(row)
        except ValueError as exc:
            problems.append(f"row {i}: invalid event — {exc}")
        if not isinstance(row, dict):
            # A non-dict row cannot carry seq/prev_hash; the chain
            # still advances over its hash so later rows are checked
            # against the file as it stands.
            expected_prev = event_hash(row)
            continue
        if row.get("seq") != i:
            problems.append(
                f"row {i}: seq mismatch — expected {i}, found "
                f"{row.get('seq')!r} (row inserted, deleted or reordered)")
        if row.get("prev_hash") != expected_prev:
            problems.append(
                f"row {i}: prev_hash mismatch — expected "
                f"{expected_prev[:12]}…, found "
                f"{str(row.get('prev_hash'))[:12]}… "
                f"(row {i - 1} changed, or this row was inserted)")
        expected_prev = event_hash(row)
    return problems


def verify_chain(path):
    """Alias for ``verify_events`` — same name as decision_log."""
    return verify_events(path)


class EventRecorder:
    """Append-only, hash-chained recorder for one agent-team run.

    ``path`` is the ``events.jsonl`` to append to; ``run_id``
    identifies the run and is stamped on every event. ``now`` is an
    injectable clock for determinism: ``None`` (current UTC time), a
    fixed ISO 8601 string (every event gets it), or a zero-argument
    callable returning an ISO 8601 string. Appending to an existing
    file continues its ``seq`` and chain. The recorder never fetches,
    never calls an LLM and never invents a number.
    """

    def __init__(self, path, run_id, now=None):
        if not _is_non_empty_str(run_id):
            raise ValueError(
                f"run_id must be a non-empty string, got {run_id!r}")
        if now is not None and not (
                isinstance(now, str) or callable(now)):
            raise ValueError(
                "now must be None, an ISO 8601 string, or a callable "
                f"returning one, got {now!r}")
        if isinstance(now, str) and _parse_ts(now) is None:
            raise ValueError(
                f"now must be a timezone-aware ISO 8601 timestamp, got "
                f"{now!r}")
        self.path = Path(path)
        self.run_id = run_id
        self._now = now
        rows = load_events(self.path)
        self._seq = len(rows)
        self._prev_hash = (event_hash(rows[-1]) if rows
                           else GENESIS_PREV_HASH)

    def _ts(self):
        if self._now is None:
            return _now_iso()
        if isinstance(self._now, str):
            return self._now
        ts = self._now()
        if _parse_ts(ts) is None:
            raise ValueError(
                f"clock returned a bad timestamp: {ts!r}")
        return ts

    def record(self, event_type, payload=None, **fields):
        """Append one event and return the recorded event dict.

        ``payload`` (a dict) and ``fields`` (keyword arguments) are
        merged as the event's per-type fields; a ``ts`` in either
        overrides the clock for this one event. Raises ValueError on
        a bad event — a failed record is never silently written.
        """
        merged = dict(payload or {})
        merged.update(fields)
        reserved = {"schema_version", "run_id", "seq", "type",
                    "prev_hash"} & set(merged)
        if reserved:
            raise ValueError(
                "record() sets schema_version, run_id, seq, type and "
                f"prev_hash itself; caller passed {sorted(reserved)}")
        ts = merged.pop("ts", None) or self._ts()
        event = {"schema_version": SCHEMA_VERSION,
                 "run_id": self.run_id,
                 "seq": self._seq,
                 "ts": ts,
                 "type": event_type,
                 **merged}
        validate_event(event)
        event["prev_hash"] = self._prev_hash
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n": the log must be LF on every OS (same rule as
        # decision_log — the publish invariant compares bytes).
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
        self._prev_hash = event_hash(event)
        self._seq += 1
        return event
