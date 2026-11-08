"""Tests for the pre-registered direction rules.

The rules are registered, not fitted: parameters are fixed at
registration (2026-11-08) and the code hash pins the exact rule
code behind every logged call.
"""
import subprocess
from pathlib import Path

import pytest

from toolkit.direction_rules import (
    RULES,
    TICKER_RULES,
    direction_for,
    evaluate_rule,
    rule_code_hash,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_DOC = REPO_ROOT / "docs" / "direction-rules.md"


def _rising(n, start=100.0, step=0.5):
    return [start + i * step for i in range(n)]


def _falling(n, start=200.0, step=0.5):
    return [start - i * step for i in range(n)]


def test_sma50_trend_up_down_and_tie():
    rising = _rising(60)
    assert evaluate_rule("sma50_trend", rising) == "up"
    assert evaluate_rule("sma50_trend", _falling(60)) == "down"
    flat = [100.0] * 60
    assert evaluate_rule("sma50_trend", flat) is None


def test_sma50_trend_needs_fifty_closes():
    assert evaluate_rule("sma50_trend", _rising(49)) is None


def test_mom20_sign_up_down_and_zero():
    assert evaluate_rule("mom20_sign", _rising(21)) == "up"
    assert evaluate_rule("mom20_sign", _falling(21)) == "down"
    assert evaluate_rule("mom20_sign", [100.0] * 21) is None


def test_mom20_sign_needs_twenty_one_closes():
    assert evaluate_rule("mom20_sign", _rising(20)) is None


def test_unknown_rule_and_bad_closes_raise():
    with pytest.raises(ValueError):
        evaluate_rule("no_such_rule", _rising(60))
    with pytest.raises(ValueError):
        evaluate_rule("sma50_trend", [])
    with pytest.raises(ValueError):
        evaluate_rule("sma50_trend", [100.0] * 59 + [-5.0])


def test_every_watchlist_ticker_has_a_rule():
    from toolkit.market_profiles import WATCHLIST_PROFILES

    assert set(TICKER_RULES) == set(WATCHLIST_PROFILES)
    for symbol, rule_id in TICKER_RULES.items():
        assert rule_id in RULES
        used_rule, direction = direction_for(symbol, _rising(60))
        assert used_rule == rule_id
        assert direction in ("up", "down", None)


def test_rule_code_hash_matches_git_hash_object():
    """The recorded hash is the real git blob hash of the rule file."""
    expected = subprocess.run(
        ["git", "hash-object", "toolkit/direction_rules.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert rule_code_hash() == expected
    assert len(rule_code_hash()) == 40


def test_doc_tables_match_the_code():
    """The direction-rules doc table agrees with TICKER_RULES."""
    text = RULES_DOC.read_text(encoding="utf-8")
    for symbol, rule_id in TICKER_RULES.items():
        assert f"| {symbol} | `{rule_id}` |" in text
    for rule_id, meta in RULES.items():
        assert f"| `{rule_id}` | {meta['version']} |" in text
