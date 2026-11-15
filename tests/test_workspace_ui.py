"""Tests for toolkit/workspace_ui.py — Round U, PR U.2 (static workspace UI).

No network: every test runs offline against local files and synthetic
event logs built with the U.1 validator. The CLI test invokes
scripts/render_workspace.py as a subprocess (offline only). The
fixture tests read the committed examples/agent-run-events.jsonl.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from toolkit import events, workspace_ui

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "examples" / "agent-run-events.jsonl"
EXAMPLE_HTML = REPO_ROOT / "examples" / "agent-run-workspace.html"
RENDER_SCRIPT = REPO_ROOT / "scripts" / "render_workspace.py"

TS = "2026-11-14T12:00:00Z"
REF = "2026-11-14T12:00:12Z"
RUN_ID = "test-run"

MINIMAL = {
    "run_started": {"team": "nepse_rules", "symbol": "DEMO"},
    "plan": {"steps": ["technical", "fundamentals"]},
    "step_started": {"step": "technical"},
    "tool_call": {"tool": "technical_read"},
    "tool_result": {"tool": "technical_read", "status": "ok"},
    "evidence_ref": {"source": "Paper A"},
    "debate_turn": {"speaker": "bull", "text": "growth is strong"},
    "gate_result": {"gate": "freshness", "verdict": "PASS"},
    "memo_section": {"section": "verdict"},
    "run_finished": {"status": "ok"},
    "error": {"error": "boom"},
}


def _good_event(event_type, **overrides):
    event = {"schema_version": events.SCHEMA_VERSION, "run_id": RUN_ID,
             "seq": 0, "ts": TS, "type": event_type,
             **MINIMAL[event_type]}
    event.update(overrides)
    return event


def _log(specs):
    """Build a valid event list from [(type, overrides), ...]."""
    log = []
    for seq, (event_type, overrides) in enumerate(specs):
        event = _good_event(event_type, **overrides)
        event["seq"] = seq
        log.append(event)
    return log


def _basic_log():
    return _log([
        ("run_started", {}),
        ("plan", {}),
        ("step_started", {}),
        ("tool_call", {}),
        ("tool_result", {}),
        ("run_finished", {}),
    ])


# --- Determinism -------------------------------------------------------

def test_render_is_deterministic():
    first = workspace_ui.render_events_html(_basic_log(),
                                           reference_time=REF)
    second = workspace_ui.render_events_html(_basic_log(),
                                            reference_time=REF)
    assert first == second


def test_fixture_render_is_deterministic(tmp_path):
    events_list = events.load_events(FIXTURE)
    first = workspace_ui.render_events_html(events_list,
                                           reference_time=REF)
    second = workspace_ui.render_events_html(events_list,
                                            reference_time=REF)
    assert first == second
    out_a = tmp_path / "a.html"
    out_b = tmp_path / "b.html"
    workspace_ui.render_events_file(FIXTURE, out_a, reference_time=REF)
    workspace_ui.render_events_file(FIXTURE, out_b, reference_time=REF)
    assert out_a.read_bytes() == out_b.read_bytes()


def test_fixture_renders_without_error():
    html_text = workspace_ui.render_events_html(
        events.load_events(FIXTURE), reference_time=REF)
    assert "u1-fixture-run" in html_text


# --- Required panels (fixture) -----------------------------------------

REQUIRED_IDS = ["plan-rail", "agent-lanes", "live-feed",
                "evidence-panel", "debate", "risk-gates", "memo"]


def test_fixture_has_all_required_panels():
    html_text = workspace_ui.render_events_html(
        events.load_events(FIXTURE), reference_time=REF)
    for panel_id in REQUIRED_IDS:
        assert f'id="{panel_id}"' in html_text


def test_fixture_empty_states_for_absent_types():
    html_text = workspace_ui.render_events_html(
        events.load_events(FIXTURE), reference_time=REF)
    # The fixture has no debate_turn, gate_result or memo_section.
    assert "No debate turns recorded yet." in html_text
    assert "No gate results recorded yet." in html_text
    assert "No memo sections recorded yet." in html_text
    assert "Risk gates — 0 passed, 0 blocked" in html_text


def test_fixture_plan_rail_steps_and_status():
    html_text = workspace_ui.render_events_html(
        events.load_events(FIXTURE), reference_time=REF)
    for step in ["technical", "fundamentals", "sentiment",
                 "broker_flow", "macro", "corporate_actions"]:
        assert step in html_text
    # Every planned step finished ok in the fixture.
    assert html_text.count("done</span>") >= 6


def test_fixture_agent_lanes():
    html_text = workspace_ui.render_events_html(
        events.load_events(FIXTURE), reference_time=REF)
    assert html_text.count('class="lane"') == 6
    for tool in ["technical_read", "sector_fundamentals_check",
                 "sentiment_score", "broker_flow_read",
                 "macro_checklist", "corporate_action_flags"]:
        assert tool in html_text


def test_fixture_evidence_panel():
    html_text = workspace_ui.render_events_html(
        events.load_events(FIXTURE), reference_time=REF)
    assert 'id="evidence-0"' in html_text
    assert 'id="evidence-1"' in html_text
    assert "Paper A" in html_text
    assert "Paper B" in html_text


def test_fixture_tool_calls_are_collapsible_details():
    html_text = workspace_ui.render_events_html(
        events.load_events(FIXTURE), reference_time=REF)
    assert html_text.count("<details>") == 6
    assert html_text.count("<summary>") == 6


# --- Freshness ---------------------------------------------------------

@pytest.mark.parametrize("ts,ref,expected", [
    ("2026-11-14T12:00:00Z", "2026-11-14T12:00:12Z", "12s ago"),
    ("2026-11-14T12:00:00Z", "2026-11-14T12:01:30Z", "1m ago"),
    ("2026-11-14T12:00:00Z", "2026-11-14T13:00:00Z", "1h ago"),
    ("2026-11-14T12:00:00Z", "2026-11-15T13:00:00Z", "1d ago"),
    ("2026-11-14T12:00:00Z", "2026-11-14T12:00:00Z", "0s ago"),
    ("2026-11-14T12:00:12Z", "2026-11-14T12:00:00Z", "0s ago"),
])
def test_freshness_known_answers(ts, ref, expected):
    assert workspace_ui.freshness(ts, ref) == expected


def test_freshness_rejects_bad_input():
    with pytest.raises(ValueError, match="reference_time must be"):
        workspace_ui.freshness(TS, "not-a-time")
    with pytest.raises(ValueError, match="ts must be"):
        workspace_ui.freshness("not-a-time", REF)


def test_freshness_stamps_deterministic_and_absolute_time_present():
    html_text = workspace_ui.render_events_html(
        events.load_events(FIXTURE), reference_time=REF)
    # 17 stamps: 11 feed rows + 6 collapsible tool-call summaries.
    # (The 6 tool_result rows are consumed into the <details> pairs
    # and render no separate stamp.)
    assert html_text.count("12s ago") == 17
    assert '<time datetime="2026-11-14T12:00:00Z">' in html_text


# --- UNKNOWN numbers and evidence links --------------------------------

def test_unsourced_number_renders_unknown():
    log = _log([
        ("run_started", {}),
        ("plan", {}),
        ("step_started", {"step": "technical"}),
        ("tool_call", {"tool": "technical_read", "step": "technical"}),
        ("tool_result", {"tool": "technical_read", "step": "technical",
                         "status": "ok", "n_points": 30,
                         "symbol": "DEMO"}),
        ("run_finished", {}),
    ])
    html_text = workspace_ui.render_events_html(log, reference_time=REF)
    assert "30 points" in html_text
    assert "UNKNOWN" in html_text
    assert 'href="#evidence-' not in html_text


def test_sourced_number_links_to_evidence():
    log = _log([
        ("run_started", {}),
        ("plan", {}),
        ("step_started", {"step": "technical"}),
        ("tool_call", {"tool": "technical_read", "step": "technical"}),
        ("tool_result", {"tool": "technical_read", "step": "technical",
                         "status": "ok", "n_points": 30,
                         "symbol": "DEMO"}),
        ("evidence_ref", {"source": "Paper A", "step": "technical",
                          "symbol": "DEMO", "n_points": 30}),
        ("run_finished", {}),
    ])
    html_text = workspace_ui.render_events_html(log, reference_time=REF)
    assert "30 points" in html_text
    assert "UNKNOWN" not in html_text
    assert 'href="#evidence-0"' in html_text


# --- Risk gates and the veto banner ------------------------------------

def test_veto_banner_present_on_block():
    log = _log([
        ("run_started", {}),
        ("gate_result", {"gate": "freshness", "verdict": "BLOCK",
                         "reason": "data is stale"}),
        ("gate_result", {"gate": "validate_series", "verdict": "PASS"}),
        ("run_finished", {}),
    ])
    html_text = workspace_ui.render_events_html(log, reference_time=REF)
    assert 'id="risk-veto-banner"' in html_text
    assert 'role="alert"' in html_text
    assert "RISK VETO" in html_text
    assert "freshness" in html_text
    assert "BLOCKED" in html_text
    assert "data is stale" in html_text
    # First element inside main, before every other panel.
    main_pos = html_text.index("<main")
    banner_pos = html_text.index('id="risk-veto-banner"')
    feed_pos = html_text.index('id="live-feed"')
    assert main_pos < banner_pos < feed_pos


def test_veto_banner_absent_on_pass():
    log = _log([
        ("run_started", {}),
        ("gate_result", {"gate": "freshness", "verdict": "PASS"}),
        ("run_finished", {}),
    ])
    html_text = workspace_ui.render_events_html(log, reference_time=REF)
    assert 'id="risk-veto-banner"' not in html_text
    assert "RISK VETO" not in html_text


def test_veto_banner_absent_when_no_gates():
    html_text = workspace_ui.render_events_html(
        events.load_events(FIXTURE), reference_time=REF)
    assert 'id="risk-veto-banner"' not in html_text


def test_risk_gate_counts():
    log = _log([
        ("run_started", {}),
        ("gate_result", {"gate": "freshness", "verdict": "PASS"}),
        ("gate_result", {"gate": "validate_series", "verdict": "PASS"}),
        ("gate_result", {"gate": "liquidity", "verdict": "BLOCK",
                         "reason": "too thin"}),
        ("run_finished", {}),
    ])
    html_text = workspace_ui.render_events_html(log, reference_time=REF)
    assert "Risk gates — 2 passed, 1 blocked" in html_text
    assert "too thin" in html_text


# --- Debate and memo ---------------------------------------------------

def test_debate_bull_bear_neutral_columns():
    log = _log([
        ("run_started", {}),
        ("debate_turn", {"speaker": "bull", "stance": "bull",
                         "text": "growth is strong"}),
        ("debate_turn", {"speaker": "bear", "stance": "bearish",
                         "text": "valuation is stretched"}),
        ("debate_turn", {"speaker": "risk", "text": "watch liquidity"}),
        ("run_finished", {}),
    ])
    html_text = workspace_ui.render_events_html(log, reference_time=REF)
    assert "<h3>Bull</h3>" in html_text
    assert "<h3>Bear</h3>" in html_text
    assert "growth is strong" in html_text
    assert "valuation is stretched" in html_text
    assert "Neutral / unclassified" in html_text
    assert "watch liquidity" in html_text


def test_memo_citations_link_to_evidence():
    log = _log([
        ("run_started", {}),
        ("evidence_ref", {"source": "Paper A", "symbol": "DEMO"}),
        ("memo_section", {"section": "verdict", "symbol": "DEMO",
                         "status": "bullish"}),
        ("run_finished", {}),
    ])
    html_text = workspace_ui.render_events_html(log, reference_time=REF)
    assert "verdict" in html_text
    assert "bullish" in html_text
    assert 'href="#evidence-0"' in html_text


def test_memo_without_evidence_is_unknown():
    log = _log([
        ("run_started", {}),
        ("memo_section", {"section": "verdict", "symbol": "DEMO"}),
        ("run_finished", {}),
    ])
    html_text = workspace_ui.render_events_html(log, reference_time=REF)
    assert "No evidence linked" in html_text
    assert "UNKNOWN" in html_text


# --- Accessibility, escaping, empty and invalid logs -------------------

def test_no_tabindex_no_script_no_external_refs():
    html_text = workspace_ui.render_events_html(
        events.load_events(FIXTURE), reference_time=REF)
    assert "tabindex" not in html_text
    assert "<script" not in html_text
    assert "<link" not in html_text
    assert "<img" not in html_text
    assert "src=" not in html_text
    assert 'href="http' not in html_text


def test_landmarks_and_skip_link():
    html_text = workspace_ui.render_events_html(
        events.load_events(FIXTURE), reference_time=REF)
    for landmark in ["<header>", "<nav", "<main", "<aside",
                     "<footer>", 'class="skip-link"']:
        assert landmark in html_text
    assert 'aria-label="Plan"' in html_text
    assert 'aria-label="Evidence"' in html_text


def test_empty_log_renders_with_empty_states():
    html_text = workspace_ui.render_events_html([], reference_time=REF)
    assert "No plan recorded yet." in html_text
    assert "No events recorded yet." in html_text
    assert "No evidence recorded yet." in html_text
    assert 'id="risk-veto-banner"' not in html_text


def test_failed_step_status_shows_failed():
    log = _log([
        ("run_started", {}),
        ("plan", {}),
        ("step_started", {"step": "technical"}),
        ("tool_call", {"tool": "technical_read", "step": "technical"}),
        ("tool_result", {"tool": "technical_read", "step": "technical",
                         "status": "failed"}),
        ("run_finished", {"status": "failed"}),
    ])
    html_text = workspace_ui.render_events_html(log, reference_time=REF)
    assert html_text.count("failed</span>") >= 2


def test_unmatched_tool_call_renders_without_result():
    log = _log([
        ("run_started", {}),
        ("tool_call", {"tool": "technical_read", "step": "technical"}),
        ("run_finished", {}),
    ])
    html_text = workspace_ui.render_events_html(log, reference_time=REF)
    assert "<details>" in html_text
    assert "No result recorded yet." in html_text


def test_event_values_are_escaped():
    log = _log([
        ("run_started", {}),
        ("debate_turn", {"speaker": "bull", "stance": "bull",
                         "text": "<script>alert(1)</script>"}),
        ("run_finished", {}),
    ])
    html_text = workspace_ui.render_events_html(log, reference_time=REF)
    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_text


def test_render_refuses_invalid_event():
    event = _good_event("plan")
    del event["steps"]
    with pytest.raises(ValueError, match="missing required field: steps"):
        workspace_ui.render_events_html([event], reference_time=REF)


# --- CLI and committed example -----------------------------------------

def test_cli_renders_fixture_to_temp_dir(tmp_path):
    output = tmp_path / "workspace.html"
    proc = subprocess.run(
        [sys.executable, str(RENDER_SCRIPT), "--events", str(FIXTURE),
         "--output", str(output), "--reference-time", REF],
        capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "23 events rendered" in proc.stdout
    assert output.exists()
    assert output.read_text(encoding="utf-8") == (
        workspace_ui.render_events_html(events.load_events(FIXTURE),
                                       reference_time=REF))


def test_cli_exits_1_on_invalid_log(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"type": "plan"}\n', encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(RENDER_SCRIPT), "--events", str(bad),
         "--output", str(tmp_path / "out.html")],
        capture_output=True, text=True, check=False)
    assert proc.returncode == 1
    assert "does not validate" in proc.stderr


def test_cli_exits_1_on_missing_input(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(RENDER_SCRIPT), "--events",
         str(tmp_path / "nope.jsonl"),
         "--output", str(tmp_path / "out.html")],
        capture_output=True, text=True, check=False)
    assert proc.returncode == 1
    assert "not found" in proc.stderr


def test_committed_example_matches_fresh_render():
    assert EXAMPLE_HTML.exists()
    fresh = workspace_ui.render_events_html(
        events.load_events(FIXTURE),
        reference_time=workspace_ui.EXAMPLE_REFERENCE_TIME)
    assert EXAMPLE_HTML.read_text(encoding="utf-8") == fresh
