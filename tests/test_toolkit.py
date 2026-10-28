import pytest

from toolkit import ratios, valuation


def test_profitability_margins():
    r = ratios.profitability(1000.0, 400.0, 150.0, 90.0)
    assert r == {"gross_margin": 0.4, "operating_margin": 0.15, "net_margin": 0.09}


def test_profitability_zero_revenue_raises():
    with pytest.raises(ValueError):
        ratios.profitability(0.0, 400.0, 150.0, 90.0)


def test_liquidity():
    r = ratios.liquidity(500.0, 250.0, inventory=100.0)
    assert r["current_ratio"] == 2.0
    assert r["quick_ratio"] == 1.6


def test_dupont_multiplies_to_roe():
    r = ratios.dupont_roe(net_income=90.0, revenue=1000.0, total_assets=2000.0, total_equity=800.0)
    assert r["roe"] == pytest.approx(r["net_margin"] * r["asset_turnover"] * r["equity_multiplier"])
    assert r["roe"] == pytest.approx(0.1125)


def test_dcf_single_year_no_growth():
    # One FCF of 100, discount 10%, terminal FCF 100, growth 0:
    # PV = 100/1.1 + (100/0.10)/1.1 = 90.909 + 909.091 = 1000
    assert valuation.dcf_value([100.0], 0.10, 0.0, 100.0) == pytest.approx(1000.0)


def test_dcf_growth_must_be_below_discount_rate():
    with pytest.raises(ValueError):
        valuation.dcf_value([100.0], 0.05, 0.05, 100.0)


def test_gordon():
    assert valuation.gordon_value(5.0, 0.10, 0.03) == pytest.approx(71.428571, rel=1e-6)


def test_multiple():
    assert valuation.implied_price_from_multiple(10.0, 15.0) == 150.0
