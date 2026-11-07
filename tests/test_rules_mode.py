"""Tests for toolkit/rules_mode.py — deterministic no-LLM rules mode (1.3)."""

import pytest

from toolkit import rules_mode


def test_technical_read_full_series():
    closes = [100.0 + i for i in range(30)]  # steady rise
    out = rules_mode.technical_read(closes)
    assert out["n_bars"] == 30
    assert out["momentum_20d_pct"] == pytest.approx(18.35, abs=0.1)
    assert out["rsi_14"] == 100.0  # all gains
    assert out["volatility_daily_pct"] > 0


def test_technical_read_short_series_unknowns():
    out = rules_mode.technical_read([100.0, 101.0, 102.0])
    assert out["momentum_20d_pct"] is None
    assert out["rsi_14"] is None
    assert "UNKNOWN" in out["note_20d"]


def test_technical_read_needs_two_closes():
    with pytest.raises(ValueError):
        rules_mode.technical_read([100.0])


def test_sector_banks_flags_high_npl():
    out = rules_mode.sector_fundamentals_check(
        "banks", {"npl_pct": 6.0, "capital_adequacy_pct": 12.0})
    assert out["status"] == "checked"
    assert any(f["metric"] == "npl_pct" for f in out["flags"])
    missing = next(r for r in out["metrics"] if r["metric"] == "net_interest_margin_pct")
    assert missing["value"] == "UNKNOWN"


def test_sector_unknown_returns_unknown():
    out = rules_mode.sector_fundamentals_check("airlines", {})
    assert out["status"] == "UNKNOWN"


@pytest.mark.parametrize("sector", ["hydropower", "insurance", "microfinance"])
def test_sector_guides_exist(sector):
    out = rules_mode.sector_fundamentals_check(sector, {})
    assert out["status"] == "checked"
    assert len(out["metrics"]) >= 3


def test_sentiment_english_and_nepali():
    heads = [
        {"text": "Bank reports record profit growth", "lang": "en", "source": "Paper A"},
        {"text": "कम्पनीलाई घाटा", "lang": "ne", "source": "Paper B"},
        {"text": "Market opens on Monday", "lang": "en", "source": "Paper C"},
    ]
    out = rules_mode.sentiment_score(heads)
    assert out["counts"] == {"positive": 1, "negative": 1, "unclassified": 1, "total": 3}
    assert out["label"] == "NEUTRAL"
    assert "not a signal" in out["note"]


def test_sentiment_empty():
    out = rules_mode.sentiment_score([])
    assert out["label"] == "NO HEADLINES"


def test_broker_flow_indicative_label():
    rows = [{"broker": 58, "buy_value": 900.0, "sell_value": 100.0},
            {"broker": 12, "buy_value": 100.0, "sell_value": 800.0}]
    out = rules_mode.broker_flow_read(rows)
    assert out["top_broker_by_buy"] == 58
    assert out["top_broker_buy_share_pct"] == pytest.approx(90.0)
    assert "INDICATIVE ONLY" in out["label"]
    assert rules_mode.broker_flow_read([])["status"] == "UNKNOWN"


def test_macro_checklist_unknowns():
    out = rules_mode.macro_checklist({"nrb_policy_rate_pct": 5.5})
    by_field = {r["field"]: r for r in out["rows"]}
    assert by_field["nrb_policy_rate_pct"]["value"] == 5.5
    assert by_field["inflation_pct"]["value"] == "UNKNOWN"


def test_corporate_actions_known_and_unknown():
    out = rules_mode.corporate_action_flags([
        {"type": "bonus", "date": "2026-01-15", "detail": "10% bonus"},
        {"type": "mystery", "date": "2026-02-01", "detail": "?"},
    ])
    assert out["n_actions"] == 2
    assert "share count rises" in out["actions"][0]["effect"]
    assert "UNKNOWN type" in out["actions"][1]["effect"]


def test_run_rules_team_full_and_empty():
    full = rules_mode.run_rules_team("BNT", "NEPSE", {
        "closes": [100.0 + i for i in range(30)],
        "sector": "banks", "metrics": {"npl_pct": 2.0},
        "headlines": [{"text": "record profit", "source": "X"}],
        "broker_rows": [{"broker": 1, "buy_value": 50.0, "sell_value": 40.0}],
        "macro": {"inflation_pct": 4.0}, "actions": []})
    assert full["technical"]["rsi_14"] == 100.0
    assert full["fundamentals"]["status"] == "checked"
    assert full["sentiment"]["label"] == "BULLISH"
    assert "zero API keys" in full["llm"]
    empty = rules_mode.run_rules_team("BNT", "NEPSE", {})
    assert empty["technical"]["status"] == "UNKNOWN"
    assert empty["broker_flow"]["status"] == "UNKNOWN"
    with pytest.raises(ValueError):
        rules_mode.run_rules_team("", "NEPSE", {})
