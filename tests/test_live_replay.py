"""Tests for toolkit/live_server.py + toolkit/live_replay.py — Round U, PR U.3.

No network: the SSE server is started in-process on an ephemeral
localhost port and fetched with urllib; every other test runs
offline against local files and synthetic event logs built with the
U.1 validator. The CLI tests invoke the scripts as subprocesses
(offline only). The fixture tests read the committed
examples/agent-run-events.jsonl and examples/agent-run-events-2.jsonl.
"""

import json
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest

from toolkit import events, live_replay, live_server, workspace_ui

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "examples" / "agent-run-events.jsonl"
FIXTURE_2 = REPO_ROOT / "examples" / "agent-run-events-2.jsonl"
REPLAY_HTML = REPO_ROOT / "docs" / "workspace-replay.html"
EXAMPLE_HTML = REPO_ROOT / "examples" / "agent-run-workspace.html"
FIXTURE_SCRIPT = REPO_ROOT / "scripts" / "run_fake_agent_events.py"
FIXTURE_2_SCRIPT = REPO_ROOT / "scripts" / "run_fake_agent_events_2.py"
REPLAY_SCRIPT = REPO_ROOT / "scripts" / "render_replay.py"
SERVE_SCRIPT = REPO_ROOT / "scripts" / "serve_workspace.py"

TS = "2026-11-14T12:00:00Z"
RUN_ID = "test-run"


def _ev(seq, event_type, ts, **fields):
    """One valid event dict (prev_hash is not schema-checked)."""
    return {"schema_version": "1.0.0", "run_id": RUN_ID, "seq": seq,
            "ts": ts, "type": event_type, **fields}


def _staggered_events():
    """Two tools with hand-computed latencies: 3 s and 2 s."""
    return [
        _ev(0, "run_started", "2026-11-14T12:00:00Z",
            team="nepse_rules"),
        _ev(1, "tool_call", "2026-11-14T12:00:00Z", tool="alpha",
            step="technical"),
        _ev(2, "tool_result", "2026-11-14T12:00:03Z", tool="alpha",
            step="technical", status="ok"),
        _ev(3, "tool_call", "2026-11-14T12:00:10Z", tool="beta",
            step="fundamentals"),
        _ev(4, "tool_result", "2026-11-14T12:00:12Z", tool="beta",
            step="fundamentals", status="ok"),
        _ev(5, "run_finished", "2026-11-14T12:00:12Z", status="ok"),
    ]


def _parse_sse(body):
    """``(data_payloads, end_payload)`` from a raw SSE body."""
    data = []
    end = None
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        if block.startswith("event: end\n"):
            end = json.loads(block.split("data: ", 1)[1])
        elif block.startswith("data: "):
            data.append(json.loads(block[len("data: "):]))
        else:  # pragma: no cover — a frame we do not emit.
            raise AssertionError(f"unexpected SSE frame: {block!r}")
    return data, end


def _http_get(port, path, timeout=10):
    with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
        return resp.status, resp.headers, resp.read()


@contextmanager
def _running_server(events_path, mode, poll_interval=0.05):
    server = live_server.build_server("127.0.0.1", 0, events_path, mode,
                                      poll_interval=poll_interval)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# --- SSE server ------------------------------------------------------


def test_sse_recorded_mode_frames_in_order_with_end_frame():
    with _running_server(FIXTURE, "recorded") as port:
        status, headers, body = _http_get(port, "/events")
    assert status == 200
    assert headers.get_content_type() == "text/event-stream"
    data, end = _parse_sse(body.decode("utf-8"))
    expected = events.load_events(FIXTURE)
    assert data == expected
    assert end == {"run_id": "u1-fixture-run", "events": 23,
                   "status": "done"}


def test_sse_tail_mode_streams_appended_events(tmp_path):
    path = tmp_path / "events.jsonl"
    recorder = events.EventRecorder(path, RUN_ID, now=TS)
    recorder.record("run_started", team="nepse_rules")
    result = {}

    def reader(port):
        _status, _headers, body = _http_get(port, "/events", timeout=15)
        result["body"] = body.decode("utf-8")

    with _running_server(path, "tail") as port:
        thread = threading.Thread(target=reader, args=(port,),
                                  daemon=True)
        thread.start()
        time.sleep(0.3)  # let the tail stream the run_started row
        recorder.record("tool_call", tool="alpha", step="technical",
                        ts="2026-11-14T12:00:02Z")
        recorder.record("tool_result", tool="alpha", step="technical",
                        status="ok", ts="2026-11-14T12:00:04Z")
        recorder.record("run_finished", status="ok",
                        ts="2026-11-14T12:00:06Z")
        thread.join(timeout=10)
    assert not thread.is_alive(), "tail stream did not end"
    data, end = _parse_sse(result["body"])
    assert [row["type"] for row in data] == [
        "run_started", "tool_call", "tool_result", "run_finished"]
    assert end == {"run_id": RUN_ID, "events": 4, "status": "done"}


def test_root_serves_workspace_ui_byte_identical():
    with _running_server(FIXTURE, "recorded") as port:
        status, headers, body = _http_get(port, "/")
    assert status == 200
    assert headers.get_content_type() == "text/html"
    expected = workspace_ui.render_events_html(
        events.load_events(FIXTURE))
    assert body.decode("utf-8") == expected


def test_root_response_has_no_animation():
    with _running_server(FIXTURE, "recorded") as port:
        _status, _headers, body = _http_get(port, "/")
    lowered = body.decode("utf-8").lower()
    assert "animation" not in lowered
    assert "transition" not in lowered
    assert "@keyframes" not in lowered


def test_missing_file_recorded_mode_streams_empty_then_end(tmp_path):
    missing = tmp_path / "no-such-events.jsonl"
    with _running_server(missing, "recorded") as port:
        status, headers, body = _http_get(port, "/events")
        assert status == 200
        assert headers.get_content_type() == "text/event-stream"
        data, end = _parse_sse(body.decode("utf-8"))
        assert data == []
        assert end == {"run_id": None, "events": 0, "status": "done"}
        status, _headers, page = _http_get(port, "/")
        assert status == 200
        assert "No events recorded yet." in page.decode("utf-8")


def test_empty_file_recorded_mode_streams_empty_then_end(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("", encoding="utf-8")
    with _running_server(path, "recorded") as port:
        _status, _headers, body = _http_get(port, "/events")
    data, end = _parse_sse(body.decode("utf-8"))
    assert data == []
    assert end["events"] == 0


def test_unknown_path_is_404():
    with (_running_server(FIXTURE, "recorded") as port,
          pytest.raises(urllib.error.HTTPError) as excinfo):
        _http_get(port, "/nope")
    assert excinfo.value.code == 404


def test_serve_cli_bad_mode_exits_1():
    proc = subprocess.run(
        [sys.executable, str(SERVE_SCRIPT), "--mode", "bogus"],
        check=False, cwd=REPO_ROOT, capture_output=True, text=True,
        timeout=60)
    assert proc.returncode == 1
    assert "--mode must be one of" in proc.stderr


def test_make_handler_rejects_bad_mode():
    with pytest.raises(ValueError, match="mode must be one of"):
        live_server.make_handler(FIXTURE, "bogus")


# --- Replay page -----------------------------------------------------


def test_replay_page_labelling():
    assert REPLAY_HTML.exists()
    text = REPLAY_HTML.read_text(encoding="utf-8")
    assert "recorded demo" in text  # exact lowercase phrase
    assert "2026-11-14" in text  # run date, derived from the log
    assert "FAKE" in text  # provider, labelled honestly
    assert 'id="recorded-demo-banner"' in text
    assert "u1-fixture-run" in text


def test_replay_page_contains_meter_and_diff_sections():
    text = REPLAY_HTML.read_text(encoding="utf-8")
    assert 'id="cost-latency-meter"' in text
    assert 'id="run-diff"' in text
    assert "4 added, 1 removed, 0 changed; 0 per-step status changes" in text
    assert "Cost: $0.00" in text


def test_replay_page_has_no_animation():
    lowered = REPLAY_HTML.read_text(encoding="utf-8").lower()
    assert "animation" not in lowered
    assert "transition" not in lowered
    assert "@keyframes" not in lowered


def test_workspace_example_has_no_animation():
    lowered = EXAMPLE_HTML.read_text(encoding="utf-8").lower()
    assert "animation" not in lowered
    assert "transition" not in lowered
    assert "@keyframes" not in lowered


def test_replay_rerender_is_byte_identical(tmp_path):
    out = tmp_path / "replay.html"
    proc = subprocess.run(
        [sys.executable, str(REPLAY_SCRIPT), "--output", str(out)],
        check=False, cwd=REPO_ROOT, capture_output=True, text=True,
        timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "23 events rendered" in proc.stdout
    assert out.read_bytes() == REPLAY_HTML.read_bytes()


def test_render_replay_cli_missing_file_exits_1(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(REPLAY_SCRIPT), "--events",
         str(tmp_path / "no-such.jsonl")],
        check=False, cwd=REPO_ROOT, capture_output=True, text=True,
        timeout=60)
    assert proc.returncode == 1
    assert "not found" in proc.stderr


def test_replay_without_diff_omits_diff_section():
    events_list = events.load_events(FIXTURE)
    text = live_replay.render_replay_html(events_list)
    assert 'id="run-diff"' not in text
    assert 'id="cost-latency-meter"' in text
    assert "recorded demo" in text


def test_replay_rejects_empty_provider():
    with pytest.raises(ValueError, match="provider must be"):
        live_replay.render_replay_html(events.load_events(FIXTURE),
                                       provider="  ")


def test_replay_html_is_deterministic():
    first = live_replay.render_replay_html(
        events.load_events(FIXTURE),
        diff_events=events.load_events(FIXTURE_2),
        reference_time="2026-11-14T12:00:12Z")
    second = live_replay.render_replay_html(
        events.load_events(FIXTURE),
        diff_events=events.load_events(FIXTURE_2),
        reference_time="2026-11-14T12:00:12Z")
    assert first == second


# --- Run diff --------------------------------------------------------


def test_diff_fixture_pair_counts_and_identities():
    diff = live_replay.diff_runs(events.load_events(FIXTURE),
                                 events.load_events(FIXTURE_2))
    assert diff["run_a"] == "u1-fixture-run"
    assert diff["run_b"] == "u3-fixture-run-2"
    assert (len(diff["added"]), len(diff["removed"]),
            len(diff["changed"]),
            len(diff["step_status_changes"])) == (4, 1, 0, 0)
    added_types = sorted(e["type"] for e in diff["added"])
    assert added_types == ["debate_turn", "evidence_ref", "gate_result",
                           "memo_section"]
    assert "Paper C" in diff["added"][0]["key"] or any(
        "Paper C" in e["key"] for e in diff["added"])
    assert len(diff["removed"]) == 1
    assert diff["removed"][0]["type"] == "evidence_ref"
    assert "Paper B" in diff["removed"][0]["key"]


def test_diff_same_run_is_empty():
    rows = events.load_events(FIXTURE)
    diff = live_replay.diff_runs(rows, list(rows))
    assert diff["added"] == []
    assert diff["removed"] == []
    assert diff["changed"] == []
    assert diff["step_status_changes"] == []


def test_diff_changed_result_and_step_status():
    run_a = [
        _ev(0, "run_started", TS, team="nepse_rules"),
        _ev(1, "step_started", TS, step="technical"),
        _ev(2, "tool_call", TS, tool="alpha", step="technical"),
        _ev(3, "tool_result", TS, tool="alpha", step="technical",
            status="ok"),
        _ev(4, "run_finished", TS, status="ok"),
    ]
    run_b = [
        _ev(0, "run_started", TS, team="nepse_rules"),
        _ev(1, "step_started", TS, step="technical"),
        _ev(2, "tool_call", TS, tool="alpha", step="technical"),
        _ev(3, "tool_result", TS, tool="alpha", step="technical",
            status="failed"),
        _ev(4, "run_finished", TS, status="ok"),
    ]
    diff = live_replay.diff_runs(run_a, run_b)
    assert len(diff["added"]) == 0
    assert len(diff["removed"]) == 0
    assert len(diff["changed"]) == 1
    assert diff["changed"][0]["type"] == "tool_result"
    assert diff["changed"][0]["before"] == {"status": "ok"}
    assert diff["changed"][0]["after"] == {"status": "failed"}
    assert diff["step_status_changes"] == [
        {"step": "technical", "before": "done", "after": "failed"}]


def test_diff_render_is_deterministic():
    diff = live_replay.diff_runs(events.load_events(FIXTURE),
                                 events.load_events(FIXTURE_2))
    assert (live_replay.render_diff_html(diff)
            == live_replay.render_diff_html(diff))


def test_step_status_agrees_with_renderer():
    for fixture in (FIXTURE, FIXTURE_2):
        rows = events.load_events(fixture)
        steps = {e.get("step") for e in rows if e.get("step")}
        for step in steps:
            assert (live_replay.step_status(rows, step)
                    == workspace_ui._step_status(rows, step))


# --- Cost/latency meter ----------------------------------------------


def test_meter_fixture_1_exact_pinned_values():
    meter = live_replay.compute_cost_latency(
        events.load_events(FIXTURE))
    assert meter["n_calls"] == 6
    assert meter["n_results"] == 6
    assert meter["n_unmatched_calls"] == 0
    assert meter["total_latency_s"] == 0.0
    assert meter["mean_latency_s"] == 0.0
    assert meter["slowest"] == {"tool": "technical_read",
                                "step": "technical", "latency_s": 0.0}
    assert all(p["latency_s"] == 0.0 for p in meter["per_tool"])
    assert meter["cost_usd"] == 0.0


def test_meter_fixture_2_exact_pinned_values():
    meter = live_replay.compute_cost_latency(
        events.load_events(FIXTURE_2))
    assert meter["n_calls"] == 6
    assert meter["n_results"] == 6
    assert meter["n_unmatched_calls"] == 0
    assert meter["total_latency_s"] == 12.0
    assert meter["mean_latency_s"] == 2.0
    assert meter["slowest"] == {"tool": "technical_read",
                                "step": "technical", "latency_s": 2.0}
    assert meter["cost_usd"] == 0.0


def test_meter_staggered_synthetic_hand_computed():
    meter = live_replay.compute_cost_latency(_staggered_events())
    assert meter["n_calls"] == 2
    assert meter["n_results"] == 2
    assert meter["n_unmatched_calls"] == 0
    assert meter["total_latency_s"] == 5.0  # 3 s + 2 s
    assert meter["mean_latency_s"] == 2.5
    assert meter["slowest"] == {"tool": "alpha",
                                "step": "technical", "latency_s": 3.0}
    assert meter["cost_usd"] == 0.0


def test_meter_unmatched_call_contributes_no_latency():
    rows = [_ev(0, "run_started", TS, team="nepse_rules"),
            _ev(1, "tool_call", TS, tool="alpha", step="technical"),
            _ev(2, "run_finished", TS, status="ok")]
    meter = live_replay.compute_cost_latency(rows)
    assert meter["n_calls"] == 1
    assert meter["n_results"] == 0
    assert meter["n_unmatched_calls"] == 1
    assert meter["total_latency_s"] == 0.0
    assert meter["mean_latency_s"] == 0.0
    assert meter["slowest"] is None


def test_meter_cost_needs_tokens_and_rate():
    rows = _staggered_events()
    default = live_replay.compute_cost_latency(rows)
    assert default["cost_usd"] == 0.0
    tokens_only = live_replay.compute_cost_latency(rows, tokens=1000)
    assert tokens_only["cost_usd"] == 0.0  # rate defaults to 0
    priced = live_replay.compute_cost_latency(
        rows, rate_per_token_usd=0.01, tokens=1000)
    assert priced["cost_usd"] == 10.0  # 1000 * 0.01, exact
    assert "1000 tokens at $0.01 per token" in priced["cost_basis"]


def test_meter_rejects_bad_rate_and_tokens():
    rows = _staggered_events()
    with pytest.raises(ValueError, match="must not be negative"):
        live_replay.compute_cost_latency(rows, rate_per_token_usd=-1.0)
    with pytest.raises(ValueError, match="non-negative integer"):
        live_replay.compute_cost_latency(rows, tokens=-5)
    with pytest.raises(ValueError, match="non-negative integer"):
        live_replay.compute_cost_latency(rows, tokens=1.5)


def test_meter_render_shows_cost_and_slowest():
    meter = live_replay.compute_cost_latency(_staggered_events())
    text = live_replay.render_meter_html(meter)
    assert 'id="cost-latency-meter"' in text
    assert "Total latency: 5.00 s" in text
    assert "Mean latency: 2.50 s" in text
    assert "alpha" in text and "3.00 s" in text
    assert "Cost: $0.00" in text


# --- Second fixture --------------------------------------------------


def test_second_fixture_script_is_deterministic(tmp_path):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    for target in (first, second):
        proc = subprocess.run(
            [sys.executable, str(FIXTURE_2_SCRIPT), "--output",
             str(target)],
            check=False, cwd=REPO_ROOT, capture_output=True, text=True,
            timeout=60)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "chain OK" in proc.stdout
    assert first.read_bytes() == second.read_bytes()  # deterministic
    rows = events.load_events(first)
    assert len(rows) == 26
    assert rows[0]["run_id"] == "u3-fixture-run-2"
    assert rows[0]["type"] == "run_started"
    assert rows[-1]["type"] == "run_finished"
    assert events.verify_events(first) == []
    types = [row["type"] for row in rows]
    for expected in ("debate_turn", "gate_result", "memo_section"):
        assert expected in types, expected
    assert first.read_bytes() == FIXTURE_2.read_bytes()


def test_committed_u1_fixture_is_untouched(tmp_path):
    target = tmp_path / "fresh.jsonl"
    proc = subprocess.run(
        [sys.executable, str(FIXTURE_SCRIPT), "--output", str(target)],
        check=False, cwd=REPO_ROOT, capture_output=True, text=True,
        timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert target.read_bytes() == FIXTURE.read_bytes()
