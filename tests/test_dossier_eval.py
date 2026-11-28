"""Tests for the 9.3 dossier eval suite (Round 9, PR 9.3).

These tests pin the suite the protocol pre-registered: the
committed fixture inventory (synthetic EDGAR facts + demo CSVs +
BNT runbook baseline), the exact summary numbers the eval script
reports (both fixtures WIN, 0 unsourced, Reality Checker Pass,
dossier sourced counts 23 and 9 vs rules 4 and 1, BNT baseline 202
numeric claims with no event log), and byte-identical fixture
regeneration. No network, no live LLM, no live fetch: the suite
runs on fixture files only.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import evaluate_dossier as eval_suite
import generate_dossier_fixtures as fixture_gen

FIXTURE_DIR = REPO_ROOT / "examples" / "dossier"
RESULTS_PATH = REPO_ROOT / "docs" / "dossier-eval-results.json"

EXPECTED = {
    "synthetic-edgar": {
        "status": "complete", "reality_checker": "Pass",
        "unsourced_numbers": 0, "unparseable_numbers": 0,
        "numbers_checked": 58, "numbers_grounded": 58,
        "dossier_sourced_count": 23, "rules_sourced_count": 4,
        "sections_covered_count": 10, "win": True,
    },
    "demo-csvs": {
        "status": "complete", "reality_checker": "Pass",
        "unsourced_numbers": 0, "unparseable_numbers": 0,
        "numbers_checked": 22, "numbers_grounded": 22,
        "dossier_sourced_count": 9, "rules_sourced_count": 1,
        "sections_covered_count": 10, "win": True,
    },
    "real-aapl": {
        "status": "complete", "reality_checker": "Pass",
        "unsourced_numbers": 0, "unparseable_numbers": 0,
        "numbers_checked": 61, "numbers_grounded": 61,
        "dossier_sourced_count": 23, "rules_sourced_count": 4,
        "sections_covered_count": 10, "win": True,
    },
}


def test_eval_fixtures_exist():
    assert (FIXTURE_DIR / "synthetic-edgar-facts.json").exists()
    assert (FIXTURE_DIR / "bnt-runbook-baseline.json").exists()
    assert (REPO_ROOT / "examples" / "real-aapl-2026-11-27.json").exists()


def test_real_filer_row_carries_peer_and_sic_detail(tmp_path):
    output = tmp_path / "results.json"
    assert eval_suite.main(["--date", "2026-11-28",
                            "--output", str(output)]) == 0
    summary = json.loads(output.read_text(encoding="utf-8"))
    row = {r["fixture"]: r for r in summary["fixtures"]}["real-aapl"]
    assert row["sic"] == "3571"
    assert row["sic_description"] == "Electronic Computers"
    assert row["sic_rows_parsed"] == 114
    assert row["sic_pages"] == 2
    assert row["sic_peers_with_ticker"] == 8
    assert row["sic_without_ticker"] == 105
    assert row["comps_peers"] == ["DELL", "OMCL", "OSS", "SMCI"]
    assert row["peer_multiples"][0]["ticker"] == "DELL"
    assert row["peer_multiples"][0]["pe_ratio"] == 34.81
    assert [item["ticker"] for item in row["peer_exclusions"]] == [
        "OURA", "SCKT", "XNDU", "ZEPP"]
    assert row["valuation"]["dcf_enterprise_value"] == 1923637181695.24
    assert row["valuation"]["dcf_per_share"] == 114.74
    assert row["valuation"]["comps"][0]["implied_price"] == 256.89
    assert row["valuation"]["comps"][0]["multiple"] == 28.23


def test_eval_returns_win_with_pinned_numbers(tmp_path):
    output = tmp_path / "results.json"
    code = eval_suite.main(["--date", "2026-11-28",
                            "--output", str(output)])
    assert code == 0
    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["verdict"] == "win"
    assert summary["date"] == "2026-11-28"
    rows = {row["fixture"]: row for row in summary["fixtures"]}
    assert set(rows) == {"synthetic-edgar", "demo-csvs", "real-aapl"}
    for name, expected in EXPECTED.items():
        row = rows[name]
        for key, value in expected.items():
            assert row[key] == value, (name, key, row[key], value)
    bnt = summary["runbook_baseline_bnt"]
    assert bnt["numeric_claim_count"] == 202
    assert bnt["unparseable_token_count"] == 170
    assert bnt["has_event_log"] is False


def test_eval_is_deterministic(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    assert eval_suite.main(["--date", "2026-11-28",
                            "--output", str(first)]) == 0
    assert eval_suite.main(["--date", "2026-11-28",
                            "--output", str(second)]) == 0
    assert first.read_text(encoding="utf-8") == \
        second.read_text(encoding="utf-8")


def _agents_repo_with_bnt_note():
    """A finance-research-agents checkout holding the BNT note, or None."""
    import os
    candidates = []
    env = os.environ.get("DOSSIER_AGENTS_REPO")
    if env:
        candidates.append(Path(env))
    candidates.append(REPO_ROOT.parent / "finance-research-agents")
    for candidate in candidates:
        note = (candidate / "docs" / "worked-example" /
                "bottlers-nepal-terai-bnt-research-note.md")
        if note.exists():
            return candidate
    return None


def test_fixture_regeneration_is_byte_identical():
    agents_repo = _agents_repo_with_bnt_note()
    if agents_repo is None:
        pytest.skip("no finance-research-agents checkout with the BNT "
                    "note next to this repo (set DOSSIER_AGENTS_REPO)")
    assert fixture_gen.main(
        ["--agents-repo", str(agents_repo), "--check"]) == 0


def test_committed_results_match_pinned_numbers():
    assert RESULTS_PATH.exists()
    summary = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    assert summary["verdict"] == "win"
    rows = {row["fixture"]: row for row in summary["fixtures"]}
    for name, expected in EXPECTED.items():
        for key, value in expected.items():
            assert rows[name][key] == value, (name, key)


def test_bnt_baseline_sections_are_recorded():
    baseline = json.loads(
        (FIXTURE_DIR / "bnt-runbook-baseline.json").read_text(
            encoding="utf-8"))
    assert baseline["company"].startswith("Bottlers Nepal")
    assert len(baseline["sections"]) == 13
    assert baseline["stated_limits"]
    assert "no DCF claimed" in baseline["stated_limits"][0]
