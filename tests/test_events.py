"""Tests for toolkit/events.py — Round U, PR U.1 (event schema and recorder).

No network: every test runs offline against local files and the
deterministic rules team. The fixture-run test invokes
scripts/run_fake_agent_events.py as a subprocess (offline only).
"""

import datetime
import json
import subprocess
import sys
from pathlib import Path

import pytest

from toolkit import autonomous, events, rules_mode

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "examples" / "agent-run-events.jsonl"
FIXTURE_SCRIPT = REPO_ROOT / "scripts" / "run_fake_agent_events.py"

TS = "2026-11-14T12:00:00Z"
RUN_ID = "test-run"

# Minimal valid payload per event type (common fields added by
# _good_event). debate_turn has no hook emitting it yet — the schema
# reserves it for a later Round U PR, and it still validates.
MINIMAL = {
    "run_started": {"team": "nepse_rules", "symbol": "DEMO"},
    "plan": {"steps": ["technical", "fundamentals"]},
    "step_started": {"step": "technical"},
    "tool_call": {"tool": "technical_read"},
    "tool_result": {"tool": "technical_read", "status": "ok"},
    "evidence_ref": {"source": "Paper A"},
    "debate_turn": {"speaker": "bull", "text": "growth is strong"},
    "gate_result": {"gate": "freshness", "verdict": "PASS"},
    "memo_section": {"section": "autonomous_report"},
    "run_finished": {"status": "ok"},
    "error": {"error": "boom"},
}


def _good_event(event_type, **overrides):
    event = {"schema_version": events.SCHEMA_VERSION, "run_id": RUN_ID,
             "seq": 0, "ts": TS, "type": event_type,
             **MINIMAL[event_type]}
    event.update(overrides)
    return event


@pytest.mark.parametrize("event_type", list(events.EVENT_TYPES))
def test_validate_accepts_every_event_type(event_type):
    events.validate_event(_good_event(event_type))


def test_validate_rejects_missing_type():
    event = _good_event("plan")
    del event["type"]
    with pytest.raises(ValueError, match="missing required field: type"):
        events.validate_event(event)


def test_validate_rejects_unknown_type():
    with pytest.raises(ValueError, match="must be one of"):
        events.validate_event(_good_event("plan", type="teleport"))


def test_validate_rejects_missing_required_field():
    event = _good_event("run_started")
    del event["team"]
    with pytest.raises(ValueError, match="missing required field: team"):
        events.validate_event(event)
    event = _good_event("tool_result")
    del event["status"]
    with pytest.raises(ValueError, match="missing required field: status"):
        events.validate_event(event)


def test_validate_rejects_bad_timestamp():
    with pytest.raises(ValueError, match="ts must be"):
        events.validate_event(_good_event("plan", ts="not a timestamp"))
    # Timezone-naive timestamps are refused — every event must pin
    # its instant to UTC.
    with pytest.raises(ValueError, match="ts must be"):
        events.validate_event(_good_event("plan", ts="2026-11-14T12:00:00"))


def test_validate_rejects_bad_seq():
    for bad in (-1, "0", True, 1.5):
        with pytest.raises(ValueError, match="seq must be"):
            events.validate_event(_good_event("plan", seq=bad))


def test_validate_rejects_wrong_schema_version():
    with pytest.raises(ValueError, match="schema_version must be"):
        events.validate_event(_good_event("plan", schema_version="9.9"))


def test_validate_rejects_empty_run_id_and_non_dict():
    with pytest.raises(ValueError, match="run_id must be"):
        events.validate_event(_good_event("plan", run_id=""))
    with pytest.raises(ValueError, match="must be a dict"):
        events.validate_event(["not", "a", "dict"])


def test_validate_rejects_bad_enum_values():
    with pytest.raises(ValueError, match="status must be one of"):
        events.validate_event(
            _good_event("tool_result", status="weird"))
    with pytest.raises(ValueError, match="verdict must be one of"):
        events.validate_event(
            _good_event("gate_result", verdict="MAYBE"))


def test_validate_rejects_non_serialisable_event():
    event = _good_event("plan")
    event["steps"] = {"not", "a", "list"}
    with pytest.raises(ValueError):
        events.validate_event(event)


def test_recorder_appends_valid_jsonl_with_chain(tmp_path):
    path = tmp_path / "events.jsonl"
    rec = events.EventRecorder(path, RUN_ID, now=TS)
    rec.record("run_started", team="nepse_rules", symbol="DEMO")
    rec.record("plan", steps=["technical"])
    rec.record("run_finished", status="ok")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    rows = [json.loads(line) for line in lines]
    assert [row["seq"] for row in rows] == [0, 1, 2]
    assert rows[0]["prev_hash"] == events.GENESIS_PREV_HASH
    assert rows[1]["prev_hash"] == events.event_hash(rows[0])
    assert rows[2]["prev_hash"] == events.event_hash(rows[1])
    assert all(row["schema_version"] == "1.0.0" for row in rows)
    assert all(row["run_id"] == RUN_ID for row in rows)
    assert events.verify_events(path) == []
    assert events.verify_chain(path) == []


def test_recorder_appends_to_existing_file_continues_chain(tmp_path):
    path = tmp_path / "events.jsonl"
    events.EventRecorder(path, RUN_ID, now=TS).record(
        "run_started", team="nepse_rules")
    rec = events.EventRecorder(path, RUN_ID, now=TS)
    rec.record("run_finished", status="ok")
    rows = events.load_events(path)
    assert [row["seq"] for row in rows] == [0, 1]
    assert rows[1]["prev_hash"] == events.event_hash(rows[0])
    assert events.verify_events(path) == []


def test_recorder_fixed_clock_is_deterministic(tmp_path):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    for path in (first, second):
        rec = events.EventRecorder(path, RUN_ID, now=TS)
        rec.record("run_started", team="nepse_rules", symbol="DEMO")
        rec.record("plan", steps=["technical", "fundamentals"])
        rec.record("run_finished", status="ok")
    assert first.read_bytes() == second.read_bytes()


def _write_rows(path, rows):
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n"
                            for row in rows), encoding="utf-8")


def test_verify_fails_on_tampered_row(tmp_path):
    path = tmp_path / "events.jsonl"
    rec = events.EventRecorder(path, RUN_ID, now=TS)
    rec.record("run_started", team="nepse_rules")
    rec.record("run_finished", status="ok")
    rows = events.load_events(path)
    rows[0]["team"] = "someone_else"
    _write_rows(path, rows)
    problems = events.verify_events(path)
    assert problems
    assert any("row 0" in p for p in problems)


def test_verify_fails_on_broken_prev_hash(tmp_path):
    path = tmp_path / "events.jsonl"
    rec = events.EventRecorder(path, RUN_ID, now=TS)
    rec.record("run_started", team="nepse_rules")
    rec.record("run_finished", status="ok")
    rows = events.load_events(path)
    rows[1]["prev_hash"] = "0" * 64
    _write_rows(path, rows)
    problems = events.verify_events(path)
    assert any("prev_hash mismatch" in p for p in problems)


def test_verify_fails_on_reordered_rows(tmp_path):
    path = tmp_path / "events.jsonl"
    rec = events.EventRecorder(path, RUN_ID, now=TS)
    rec.record("run_started", team="nepse_rules")
    rec.record("plan", steps=["technical"])
    rec.record("run_finished", status="ok")
    rows = events.load_events(path)
    rows[0], rows[1] = rows[1], rows[0]
    _write_rows(path, rows)
    assert events.verify_events(path)


def test_load_events_missing_file_returns_empty(tmp_path):
    assert events.load_events(tmp_path / "nope.jsonl") == []
    assert events.verify_events(tmp_path / "nope.jsonl") == []


def test_recorder_rejects_bad_run_id_and_bad_clock():
    with pytest.raises(ValueError, match="run_id must be"):
        events.EventRecorder("x.jsonl", "")
    with pytest.raises(ValueError, match="now must be"):
        events.EventRecorder("x.jsonl", RUN_ID, now="not a timestamp")


def _rules_inputs():
    return {
        "closes": [100.0 + i for i in range(30)],
        "sector": "banks",
        "metrics": {"npl_pct": 3.2, "capital_adequacy_pct": 12.5},
        "headlines": [
            {"text": "Bank reports record profit growth", "lang": "en",
             "source": "Paper A"},
            {"text": "Market opens on Monday", "lang": "en"},
        ],
        "broker_rows": [{"broker": 58, "buy_value": 900.0,
                         "sell_value": 100.0}],
        "macro": {"nrb_policy_rate_pct": 5.5},
        "actions": [{"type": "bonus", "detail": "10% bonus",
                     "date": "2026-11-14"}],
    }


def test_hooks_do_not_change_rules_team_output(tmp_path):
    path = tmp_path / "events.jsonl"
    rec = events.EventRecorder(path, RUN_ID, now=TS)
    with_recorder = rules_mode.run_rules_team(
        "DEMO", "NEPSE", _rules_inputs(), recorder=rec)
    without = rules_mode.run_rules_team("DEMO", "NEPSE", _rules_inputs())
    assert with_recorder == without
    rows = events.load_events(path)
    types = [row["type"] for row in rows]
    assert types[0] == "run_started"
    assert types[1] == "plan"
    assert types[-1] == "run_finished"
    assert types.count("step_started") == 6
    assert types.count("tool_call") == 6
    assert types.count("tool_result") == 6
    assert types.count("evidence_ref") == 1  # only one sourced headline
    assert events.verify_events(path) == []


def test_hooks_do_not_change_global_team_output(tmp_path):
    path = tmp_path / "events.jsonl"
    rec = events.EventRecorder(path, RUN_ID, now=TS)
    inputs = {"closes": [100.0 + i for i in range(30)],
              "headlines": [{"text": "Markets rise", "source": "Wire"}]}
    with_recorder = rules_mode.run_global_rules_team(
        "AAPL", "us", inputs, recorder=rec)
    without = rules_mode.run_global_rules_team("AAPL", "us", inputs)
    assert with_recorder == without
    rows = events.load_events(path)
    types = [row["type"] for row in rows]
    assert types[0] == "run_started"
    assert types[-1] == "run_finished"
    assert types.count("step_started") == 7
    assert events.verify_events(path) == []


def test_hooks_record_error_and_reraise(tmp_path):
    path = tmp_path / "events.jsonl"
    rec = events.EventRecorder(path, RUN_ID, now=TS)
    # A one-bar close list makes technical_read raise.
    with pytest.raises(ValueError, match="at least 2 closes"):
        rules_mode.run_rules_team("DEMO", "NEPSE", {"closes": [100.0]},
                                  recorder=rec)
    rows = events.load_events(path)
    assert rows[-1]["type"] == "error"
    assert "at least 2 closes" in rows[-1]["error"]
    assert events.verify_events(path) == []


def _autonomous_config(out_dir):
    today = datetime.date(2026, 10, 28)
    start = datetime.date(2026, 9, 19)
    pairs = [((start + datetime.timedelta(days=i)).isoformat(),
              100.0 + i * 0.5) for i in range(40)]

    def router(symbol):
        if symbol == "BAD":
            raise RuntimeError("network down")
        return pairs

    return {"symbols": ["GOOD", "BAD"], "output_dir": str(out_dir),
            "max_age_days": 7, "today": today,
            "now": datetime.datetime(2026, 10, 28, 12, 0,
                                     tzinfo=datetime.timezone.utc),
            "fetchers": [("router", router)]}


def test_hooks_do_not_change_autonomous_run_once(tmp_path):
    plain_dir = tmp_path / "plain"
    recorded_dir = tmp_path / "recorded"
    plain = autonomous.run_once(_autonomous_config(plain_dir))
    rec = events.EventRecorder(tmp_path / "events.jsonl", RUN_ID, now=TS)
    recorded = autonomous.run_once(
        _autonomous_config(recorded_dir), recorder=rec)
    assert recorded == plain
    assert recorded["symbols"]["GOOD"]["status"] == "fresh"
    assert recorded["symbols"]["BAD"]["status"] == "STALE_OR_FAILED"
    rows = events.load_events(tmp_path / "events.jsonl")
    types = [row["type"] for row in rows]
    for expected in ("run_started", "plan", "step_started", "tool_call",
                     "tool_result", "evidence_ref", "gate_result",
                     "memo_section", "error", "run_finished"):
        assert expected in types, expected
    assert types[0] == "run_started"
    assert types[-1] == "run_finished"
    assert events.verify_events(tmp_path / "events.jsonl") == []


def test_fixture_script_produces_valid_events(tmp_path):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    for target in (first, second):
        proc = subprocess.run(
            [sys.executable, str(FIXTURE_SCRIPT), "--output", str(target)],
            check=False, cwd=REPO_ROOT, capture_output=True, text=True,
            timeout=60)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "chain OK" in proc.stdout
    assert first.read_bytes() == second.read_bytes()  # deterministic
    rows = events.load_events(first)
    assert len(rows) == 23
    assert rows[0]["type"] == "run_started"
    assert rows[-1]["type"] == "run_finished"
    assert events.verify_events(first) == []


def test_committed_fixture_verifies():
    assert FIXTURE.exists()
    rows = events.load_events(FIXTURE)
    assert rows[0]["run_id"] == "u1-fixture-run"
    assert events.verify_events(FIXTURE) == []


def test_recorder_default_clock_stamps_real_utc_time(tmp_path):
    path = tmp_path / "events.jsonl"
    rec = events.EventRecorder(path, RUN_ID)  # no clock: UTC now
    event = rec.record("run_started", team="nepse_rules")
    assert event["ts"].endswith("Z")
    assert events._parse_ts(event["ts"]) is not None
    assert events.verify_events(path) == []


def test_recorder_callable_clock_and_bad_clock_return(tmp_path):
    path = tmp_path / "events.jsonl"
    calls = {"n": 0}

    def clock():
        calls["n"] += 1
        return f"2026-11-14T12:00:0{calls['n']}Z"

    rec = events.EventRecorder(path, RUN_ID, now=clock)
    first = rec.record("run_started", team="nepse_rules")
    second = rec.record("run_finished", status="ok")
    assert first["ts"] != second["ts"]
    assert events.verify_events(path) == []
    with pytest.raises(ValueError, match="clock returned a bad timestamp"):
        events.EventRecorder(tmp_path / "bad.jsonl", RUN_ID,
                             now=lambda: "not a timestamp").record(
                                 "run_started", team="x")


def test_recorder_rejects_reserved_keys_and_accepts_ts_override(tmp_path):
    path = tmp_path / "events.jsonl"
    rec = events.EventRecorder(path, RUN_ID, now=TS)
    with pytest.raises(ValueError, match="record\\(\\) sets"):
        rec.record("plan", steps=["technical"], seq=99)
    event = rec.record("run_started", team="nepse_rules",
                       ts="2026-11-14T13:00:00Z")
    assert event["ts"] == "2026-11-14T13:00:00Z"
    assert events.verify_events(path) == []


def test_validate_rejects_wrong_optional_field_kind():
    with pytest.raises(ValueError, match="symbols must be"):
        events.validate_event(_good_event("run_started", symbols="DEMO"))
    with pytest.raises(ValueError, match="n_points must be"):
        events.validate_event(
            _good_event("tool_result", n_points="plenty"))
    with pytest.raises(ValueError, match="steps must be"):
        events.validate_event(_good_event("plan", steps=[]))


def test_validate_rejects_missing_common_field_and_accepts_offset_ts():
    event = _good_event("plan")
    del event["run_id"]
    with pytest.raises(ValueError, match="missing required field: run_id"):
        events.validate_event(event)
    offset = _good_event("plan", ts="2026-11-14T12:00:00+00:00")
    events.validate_event(offset)


def test_verify_reports_non_dict_row_and_missing_prev_hash(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('["not", "a", "dict"]\n', encoding="utf-8")
    problems = events.verify_events(path)
    assert problems
    assert any("invalid event" in p for p in problems)
    rec = events.EventRecorder(tmp_path / "chained.jsonl", RUN_ID, now=TS)
    rec.record("run_started", team="nepse_rules")
    rec.record("run_finished", status="ok")
    chained = tmp_path / "chained.jsonl"
    rows = events.load_events(chained)
    del rows[0]["prev_hash"]
    _write_rows(chained, rows)
    assert any("prev_hash mismatch" in p
               for p in events.verify_events(chained))


def test_hooks_record_validation_error_without_symbol(tmp_path):
    path = tmp_path / "events.jsonl"
    rec = events.EventRecorder(path, RUN_ID, now=TS)
    with pytest.raises(ValueError, match="needs a symbol"):
        rules_mode.run_rules_team("", "NEPSE", {}, recorder=rec)
    rows = events.load_events(path)
    assert len(rows) == 1
    assert rows[0]["type"] == "error"
    assert "symbol" not in rows[0]
    assert events.verify_events(path) == []


class _ErrorBreakingRecorder:
    """Records everything except ``error`` events, which raise."""

    def __init__(self):
        self.recorded = []

    def record(self, event_type, payload=None, **fields):
        if event_type == "error":
            raise RuntimeError("recorder broken")
        self.recorded.append(event_type)
        return {"type": event_type}


def test_broken_recorder_never_masks_the_run_error():
    # Rules team: the section error is re-raised, not the recorder's.
    rec = _ErrorBreakingRecorder()
    with pytest.raises(ValueError, match="at least 2 closes"):
        rules_mode.run_rules_team("DEMO", "NEPSE", {"closes": [100.0]},
                                  recorder=rec)
    assert "run_started" in rec.recorded


def test_autonomous_empty_symbols_records_error(tmp_path):
    path = tmp_path / "events.jsonl"
    rec = events.EventRecorder(path, RUN_ID, now=TS)
    with pytest.raises(ValueError, match="non-empty list"):
        autonomous.run_once({"symbols": []}, recorder=rec)
    rows = events.load_events(path)
    assert len(rows) == 1
    assert rows[0]["type"] == "error"
    assert events.verify_events(path) == []


def test_autonomous_broken_error_recorder_still_marks_symbol_stale(tmp_path):
    rec = _ErrorBreakingRecorder()
    summary = autonomous.run_once(
        _autonomous_config(tmp_path / "out"), recorder=rec)
    assert summary["symbols"]["BAD"]["status"] == "STALE_OR_FAILED"
    assert summary["symbols"]["GOOD"]["status"] == "fresh"
    assert "run_finished" in rec.recorded
