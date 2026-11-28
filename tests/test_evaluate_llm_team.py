"""Tests for scripts/evaluate_llm_team.py (Round 9.1).

The evaluator scores the llm-team series only: Brier primary,
hit rate secondary, seeded paired-bootstrap CI, verdict withheld
before 100 scored calls. All fixtures are synthetic decision-log
rows built with the real write/settle helpers — no network, no
LLM, no real prices.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.evaluate_llm_team import evaluate_llm_team
from toolkit.agent_team import settle_llm_call, write_llm_call
from toolkit.decision_log import load_calls, write_call


def _settled_log(path, n, *, model_right=True, naive_right=False,
                 confidence=0.9, symbol="VT"):
    """Write n settled llm-team calls: model expected "up" with the
    given confidence; realised up when model_right, down otherwise.
    Naive expects up when naive_right, down otherwise."""
    for i in range(n):
        record = write_llm_call(
            path, {"date": "2026-11-06", "symbol": symbol,
                   "exchange": "NYSE Arca", "thesis": "team",
                   "horizon_days": 5, "expected": "up",
                   "sources": ["test"], "confidence": confidence,
                   "entry_close": 50.0, "prev_5d_return": 0.01})
        realised = 51.0 if model_right else 49.0
        prev = 0.01 if naive_right else -0.01
        settle_llm_call(path, record["call_id"], realised, 50.0, 0.0,
                        "test:index", "2026-11-12", prev_5d_return=prev)
    return load_calls(path)


def test_withheld_before_100(tmp_path):
    calls = _settled_log(tmp_path / "calls.jsonl", 3)
    result = evaluate_llm_team(calls)
    assert result["n_scored"] == 3
    assert result["n_settled"] == 3
    assert result["verdict"] == "withheld"
    assert result["brier_diff_ci_95"] is None
    assert result["brier_model"] == pytest.approx(0.01)
    assert result["brier_naive"] == pytest.approx(1.0)


def test_beats_naive_at_100(tmp_path):
    calls = _settled_log(tmp_path / "calls.jsonl", 100,
                         model_right=True, naive_right=False)
    result = evaluate_llm_team(calls)
    assert result["n_scored"] == 100
    assert result["verdict"] == "beats naive"
    assert result["brier_model"] == pytest.approx(0.01)
    assert result["brier_naive"] == pytest.approx(1.0)
    lo, hi = result["brier_diff_ci_95"]
    assert lo > 0  # CI entirely above zero: model Brier lower
    assert hi >= lo  # degenerate CI when all paired diffs are equal


def test_no_edge_when_model_worse_than_naive(tmp_path):
    # Model wrong at 0.9 confidence (Brier 0.81); naive expects down
    # and is right (Brier 0.0) — the model loses on Brier.
    calls = _settled_log(tmp_path / "calls.jsonl", 100,
                         model_right=False, naive_right=False)
    result = evaluate_llm_team(calls)
    assert result["verdict"] == "no edge"
    assert result["brier_model"] > result["brier_naive"]


def test_empty_series_is_withheld_with_counts_only(tmp_path):
    result = evaluate_llm_team([])
    assert result["n_scored"] == 0
    assert result["n_settled"] == 0
    assert result["n_open"] == 0
    assert result["verdict"] == "withheld"
    assert result["brier_model"] is None


def test_range_calls_excluded_from_brier(tmp_path):
    path = tmp_path / "calls.jsonl"
    record = write_llm_call(
        path, {"date": "2026-11-06", "symbol": "VT",
               "exchange": "NYSE Arca", "thesis": "team",
               "horizon_days": 5, "expected": "range",
               "expected_low": 48.0, "expected_high": 52.0,
               "sources": ["test"], "confidence": 0.6,
               "entry_close": 50.0, "prev_5d_return": 0.01})
    settle_llm_call(path, record["call_id"], 51.0, 50.0, 0.0,
                    "test:index", "2026-11-12", prev_5d_return=0.01)
    result = evaluate_llm_team(load_calls(path))
    assert result["n_settled"] == 1
    assert result["n_scored"] == 0
    assert result["n_range_excluded"] == 1
    assert result["verdict"] == "withheld"


def test_other_series_never_scored(tmp_path):
    path = tmp_path / "calls.jsonl"
    write_call(path, {"date": "2026-11-06", "symbol": "SPY",
                      "exchange": "NYSE Arca", "thesis": "rule",
                      "horizon_days": 5, "expected": "up",
                      "sources": ["yahoo"], "confidence": 0.5,
                      "entry_close": 100.0})
    result = evaluate_llm_team(load_calls(path))
    assert result["n_llm_team_total"] == 0
    assert result["n_scored"] == 0
    assert result["verdict"] == "withheld"


def test_cli_writes_json(tmp_path):
    calls_path = tmp_path / "calls.jsonl"
    _settled_log(calls_path, 2)
    out = tmp_path / "eval.json"
    proc = subprocess.run(
        [sys.executable, "scripts/evaluate_llm_team.py",
         "--calls", str(calls_path), "--out", str(out)],
        capture_output=True, text=True, cwd=Path(__file__).parent.parent,
        check=False)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["verdict"] == "withheld"
    assert result["n_scored"] == 2
    assert json.loads(proc.stdout)["n_scored"] == 2
