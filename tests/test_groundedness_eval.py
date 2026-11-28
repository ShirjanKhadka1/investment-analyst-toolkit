"""Tests for the 9.2 agent eval suite (Round 9, PR 9.2).

These tests pin the suite the protocol pre-registered: the
committed fixture inventory (26 known-answer memo/log pairs), the
hash-chain validity of every fixture log, the FakeProvider-only
run, and the exact summary numbers the eval script reports
(checker 26/26, 0 false passes, grounded 11/11, planted 6/6,
gate agreement 24/26, baselines 11/26 and 12/26, verdict "win").
No network, no live LLM: the suite runs on the FakeProvider with
scripted memo texts and fixed token counts.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import evaluate_groundedness as eval_suite
import generate_groundedness_fixtures as fixture_gen

from toolkit import events

FIXTURE_DIR = REPO_ROOT / "examples" / "groundedness"

EXPECTED_KIND_COUNTS = {
    "grounded": 8,
    "rounded-in": 2,
    "rounded-out": 2,
    "planted": 6,
    "fabricated-source": 2,
    "missing-section": 2,
    "gate-mismatch": 2,
    "gate-blocked-honest": 1,
    "unparseable": 1,
}


def _manifest():
    return json.loads(
        (FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


def test_fixture_inventory_is_26_with_expected_kinds():
    manifest = _manifest()
    assert len(manifest) == 26
    kinds = {}
    for entry in manifest:
        kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1
        assert entry["expected"] in ("PASS", "BLOCK")
        assert entry["gate_verdict"] in ("PASS", "BLOCK")
    assert kinds == EXPECTED_KIND_COUNTS


def test_every_fixture_has_memo_and_log_files():
    for entry in _manifest():
        name = entry["name"]
        assert (FIXTURE_DIR / f"{name}.md").is_file()
        assert (FIXTURE_DIR / f"{name}.jsonl").is_file()


def test_every_fixture_log_verifies_clean():
    for entry in _manifest():
        path = FIXTURE_DIR / f"{entry['name']}.jsonl"
        assert events.verify_events(path) == []


def test_eval_suite_summary_is_pinned():
    results = eval_suite.run_eval_suite()
    summary = results["summary"]
    assert summary["n_fixtures"] == 26
    assert summary["checker_correct"] == 26
    assert summary["checker_accuracy"] == 1.0
    assert summary["false_passes"] == 0
    assert summary["false_blocks"] == 0
    assert summary["grounded_passed"] == 11
    assert summary["grounded_total"] == 11
    assert summary["grounded_pass_rate"] == 1.0
    assert summary["planted_blocked"] == 6
    assert summary["planted_total"] == 6
    assert summary["planted_block_rate"] == 1.0
    assert summary["unsourced_blocked"] == 15
    assert summary["unsourced_total"] == 15
    assert summary["unsourced_block_rate"] == 1.0
    assert summary["gate_agreements"] == 24
    assert summary["gate_total"] == 26
    assert summary["gate_agreement_rate"] == pytest.approx(24 / 26)
    assert summary["no_checker_accuracy"] == pytest.approx(11 / 26)
    assert summary["keyword_only_accuracy"] == pytest.approx(12 / 26)
    assert summary["verdict"] == "win"


def test_eval_suite_runs_on_the_fake_provider_only():
    results = eval_suite.run_eval_suite()
    assert results["provider"] == "fake"
    assert results["provider_calls"] == 26
    assert results["tokens_per_call"] == [10, 20]
    assert results["tokens_used"] == 26 * 30


def test_eval_suite_is_deterministic():
    first = eval_suite.run_eval_suite()
    second = eval_suite.run_eval_suite()
    assert first == second


def test_eval_suite_prints_every_fixture():
    results = eval_suite.run_eval_suite()
    names = [row["name"] for row in results["fixtures"]]
    assert names == sorted(entry["name"] for entry in _manifest())
    for row in results["fixtures"]:
        assert row["checker"] == row["expected"]


def test_eval_main_writes_results_json_and_exits_zero(tmp_path):
    output = tmp_path / "results.json"
    assert eval_suite.main(
        ["--date", "2026-11-28", "--output", str(output)]) == 0
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["run_date"] == "2026-11-28"
    assert written["summary"]["verdict"] == "win"
    assert written["thresholds"] == {
        "block_rate_min": 1.0,
        "grounded_pass_min": 0.95,
        "gate_agreement_min": 0.90,
    }
    assert len(written["fixtures"]) == 26


def test_fixture_generation_is_byte_identical(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    names_1, problems_1 = fixture_gen.generate(first)
    names_2, problems_2 = fixture_gen.generate(second)
    assert problems_1 == []
    assert problems_2 == []
    assert names_1 == names_2
    for name in names_1:
        for suffix in (".md", ".jsonl"):
            assert (first / f"{name}{suffix}").read_bytes() == (
                second / f"{name}{suffix}").read_bytes()
    assert (first / "manifest.json").read_bytes() == (
        second / "manifest.json").read_bytes()


def test_baselines_are_weaker_than_the_checker():
    results = eval_suite.run_eval_suite()
    summary = results["summary"]
    assert summary["checker_accuracy"] > summary["no_checker_accuracy"]
    assert summary["checker_accuracy"] > summary["keyword_only_accuracy"]


def test_keyword_baseline_passes_any_memo_with_a_sources_heading():
    assert eval_suite.keyword_checker_verdict(
        "## Sources\n- Yahoo chart\n", []) == "PASS"
    assert eval_suite.keyword_checker_verdict(
        "No citation here.\n", []) == "BLOCK"
    assert eval_suite.no_checker_verdict("anything", []) == "PASS"
