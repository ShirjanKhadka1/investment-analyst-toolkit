"""Tests for the personas team eval script (Round 10, PR 10.3)."""

import json
from pathlib import Path
from unittest import mock

import pytest

from scripts import evaluate_personas_team
from toolkit import personas_team

ROOT = Path(__file__).resolve().parent.parent


def _run_eval(tmp_path, date="2026-11-28"):
    output = tmp_path / "personas-team-eval-results.json"
    code = evaluate_personas_team.main(
        ["--date", date, "--output", str(output)])
    return code, output


def test_eval_exits_zero_and_writes_summary(tmp_path):
    code, output = _run_eval(tmp_path)
    assert code == 0
    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["verdict"] == "win"
    assert summary["run_id"] == personas_team.RUN_ID
    assert summary["symbol"] == "AAPL"
    assert summary["as_of"] == "2026-11-27"
    assert summary["last_close"] == pytest.approx(358.54)
    scores = summary["scores"]
    assert sum(1 for s in scores.values()
               if s["score"] == "sourced") == 4
    assert scores["earnings"]["score"] == "UNKNOWN"
    stances = summary["stances"]
    assert stances["us_large_cap_fundamentals"]["stance"] == "constructive"
    assert stances["etf_index"]["stance"] == "constructive"
    assert stances["fx_macro"]["stance"] == "cautious"
    assert stances["crypto"]["stance"] == "cautious"
    assert stances["earnings"]["stance"] == "UNKNOWN"
    agreement = summary["agreement"]
    assert agreement["rate"] == pytest.approx(1.0 / 3.0)
    assert agreement["matching_pairs"] == 2
    assert agreement["n_pairs"] == 6
    assert agreement["chance_baseline"] == pytest.approx(1.0 / 3.0)


def test_eval_summary_reproducible(tmp_path):
    _, first = _run_eval(tmp_path / "a")
    _, second = _run_eval(tmp_path / "b")
    assert first.read_text(encoding="utf-8") == second.read_text(
        encoding="utf-8")


def test_eval_exits_nonzero_when_no_agreement_rate(tmp_path):
    """Fewer than 2 non-UNKNOWN stances -> exit 1 (no edge)."""
    aapl = json.loads(
        (ROOT / "examples" / "real-aapl-2026-11-27.json").read_text(
            encoding="utf-8"))
    market = json.loads(
        (ROOT / "examples" / "personas-team-inputs-2026-11-27.json")
        .read_text(encoding="utf-8"))
    result = personas_team.run_personas_team(aapl, market)
    for key in personas_team.PERSONA_KEYS:
        result["stances"][key] = {"persona": key, "stance": "UNKNOWN",
                                  "reason": "patched"}
        result["scores"][key] = {"persona": key, "score": "UNKNOWN",
                                 "sourced_values": [],
                                 "reason": "patched"}
    result["agreement"] = personas_team.agreement_rate(
        {k: "UNKNOWN" for k in personas_team.PERSONA_KEYS})
    with mock.patch.object(
            evaluate_personas_team.personas_team,
            "run_personas_team", return_value=result):
        code, output = _run_eval(tmp_path)
    assert code == 1
    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["verdict"] == "no edge"
    assert summary["agreement"]["rate"] is None
