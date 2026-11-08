"""Tests for the market-agnostic global rules team (rules_mode 1.4).

Same structure as tests/test_rules_mode.py: pure deterministic rules
over caller-supplied inputs; a missing input is UNKNOWN, never filled.
"""

import pytest

from toolkit import rules_mode


def test_us_fundamentals_flags_and_unknowns():
    out = rules_mode.us_fundamentals_check(
        {"debt_to_equity": 2.5, "interest_coverage": 1.2,
         "revenue_growth_pct": 8.0, "net_margin_pct": 25.0})
    assert out["persona"] == "us_large_cap_fundamentals"
    assert any(f["metric"] == "debt_to_equity" for f in out["flags"])
    assert any(f["metric"] == "interest_coverage" for f in out["flags"])
    missing = next(r for r in out["metrics"]
                   if r["metric"] == "return_on_invested_capital_pct")
    assert missing["value"] == "UNKNOWN"
    assert "not verdicts" in out["note"]


def test_us_fundamentals_clean_no_flags():
    out = rules_mode.us_fundamentals_check(
        {"debt_to_equity": 0.5, "interest_coverage": 8.0,
         "revenue_growth_pct": 10.0, "net_margin_pct": 20.0,
         "free_cash_flow_margin_pct": 25.0,
         "return_on_invested_capital_pct": 18.0})
    assert out["flags"] == []


def test_etf_index_gap_and_flags():
    out = rules_mode.etf_index_check(
        {"expense_ratio_pct": 0.03, "tracking_error_pct": 0.4,
         "premium_discount_pct": 0.8,
         "index_return_1y_pct": 12.0, "etf_return_1y_pct": 11.5})
    assert out["etf_minus_index_1y_pct"] == pytest.approx(-0.5)
    assert any(f["metric"] == "premium_discount_pct" for f in out["flags"])
    missing = next(r for r in out["metrics"]
                   if r["metric"] == "assets_under_management_usd")
    assert missing["value"] == "UNKNOWN"


def test_etf_index_tracking_error_flag():
    out = rules_mode.etf_index_check({"tracking_error_pct": 1.5})
    assert any(f["metric"] == "tracking_error_pct" for f in out["flags"])
    assert out["etf_minus_index_1y_pct"] is None


def test_fx_macro_differential_and_unknowns():
    out = rules_mode.fx_macro_check({
        "base_currency": "USD", "quote_currency": "EUR",
        "policy_rate_base_pct": 4.5, "policy_rate_quote_pct": 2.0,
        "pair_change_1y_pct": 3.0})
    assert out["pair"] == "USD/EUR"
    assert out["rate_differential_bps"] == pytest.approx(250.0)
    assert out["flags"] == []
    by_field = {r["metric"]: r for r in out["rows"]}
    assert by_field["inflation_base_pct"]["value"] == "UNKNOWN"


def test_fx_macro_large_move_flagged():
    out = rules_mode.fx_macro_check(
        {"base_currency": "USD", "quote_currency": "JPY",
         "pair_change_1y_pct": -25.0})
    assert out["rate_differential_bps"] is None
    assert any(f["metric"] == "pair_change_1y_pct" for f in out["flags"])


def test_crypto_computed_and_flags():
    out = rules_mode.crypto_check({
        "price_usd": 40000.0, "all_time_high_usd": 69000.0,
        "market_cap_usd": 800_000_000_000, "volume_24h_usd": 20_000_000_000,
        "mean_200d_usd": 35000.0, "realised_vol_30d_daily_pct": 6.0,
        "circulating_supply": 19_000_000, "max_supply": 21_000_000})
    assert out["computed"]["drawdown_from_ath_pct"] == pytest.approx(-42.03, abs=0.01)
    assert out["computed"]["volume_to_mcap_pct"] == pytest.approx(2.5)
    assert out["computed"]["price_vs_mean_200d_pct"] == pytest.approx(14.29, abs=0.01)
    assert out["computed"]["circulating_share_of_max_supply"] == pytest.approx(0.905, abs=0.001)
    assert any(f["metric"] == "realised_vol_30d_daily_pct" for f in out["flags"])
    assert not any(f["metric"] == "drawdown_from_ath_pct" for f in out["flags"])


def test_crypto_deep_drawdown_and_dilution_flags():
    out = rules_mode.crypto_check({
        "price_usd": 100.0, "all_time_high_usd": 1000.0,
        "circulating_supply": 1_000_000, "max_supply": 10_000_000})
    assert out["computed"]["drawdown_from_ath_pct"] == pytest.approx(-90.0)
    assert any(f["metric"] == "drawdown_from_ath_pct" for f in out["flags"])
    assert any("dilutes holders" in f["flag"] for f in out["flags"])
    assert out["computed"].get("volume_to_mcap_pct") is None


def test_earnings_surprise_and_guidance():
    out = rules_mode.earnings_check([
        {"date": "2026-10-30", "reported_eps": 1.20, "expected_eps": 1.00,
         "reported_revenue": 100.0, "expected_revenue": 95.0,
         "guidance": "raised"},
        {"date": "2026-07-30", "reported_eps": 0.80, "expected_eps": 1.00,
         "guidance": "lowered"},
        {"date": "2026-04-30", "reported_eps": 1.10},
    ])
    assert out["n_events"] == 3
    assert out["events"][0]["eps_surprise_pct"] == pytest.approx(20.0)
    assert out["events"][0]["revenue_surprise_pct"] == pytest.approx(5.26, abs=0.01)
    assert out["events"][1]["eps_surprise_pct"] == pytest.approx(-20.0)
    assert any(f["metric"] == "guidance" for f in out["events"][1]["flags"])
    assert out["events"][2]["eps_surprise_pct"] == "UNKNOWN"


def test_global_team_registry():
    assert rules_mode.list_global_personas() == [
        "us_large_cap_fundamentals", "etf_index", "fx_macro", "crypto",
        "earnings"]
    for fn in rules_mode.GLOBAL_TEAM.values():
        assert callable(fn)
    assert rules_mode.RULES_MODE_VERSION == "1.4.0"


def test_run_global_rules_team_full_and_empty():
    full = rules_mode.run_global_rules_team("AAPL", "us", {
        "closes": [100.0 + i for i in range(30)],
        "fundamentals_metrics": {"net_margin_pct": 25.0},
        "etf_metrics": {"expense_ratio_pct": 0.03},
        "fx_inputs": {"base_currency": "USD", "quote_currency": "EUR"},
        "crypto_metrics": {"price_usd": 50000.0},
        "earnings_events": [{"date": "2026-10-30", "reported_eps": 1.2,
                             "expected_eps": 1.0}],
        "headlines": [{"text": "record profit growth", "source": "X"}]})
    assert full["market"] == "us"
    assert full["technical"]["rsi_14"] == 100.0
    assert full["us_fundamentals"]["status"] == "checked"
    assert full["earnings"]["events"][0]["eps_surprise_pct"] == pytest.approx(20.0)
    assert full["sentiment"]["label"] == "BULLISH"
    assert "zero API keys" in full["llm"]
    empty = rules_mode.run_global_rules_team("BTC-USD", "crypto", {})
    assert empty["technical"]["status"] == "UNKNOWN"
    assert empty["crypto"]["metrics"][0]["value"] == "UNKNOWN"
    assert empty["earnings"]["n_events"] == 0
    with pytest.raises(ValueError):
        rules_mode.run_global_rules_team("", "us", {})
