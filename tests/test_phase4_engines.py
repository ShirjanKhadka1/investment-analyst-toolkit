"""Phase 4 slice 1 tests (NEW FILE ONLY — no existing test file modified).

Covers: put-call parity still green, DCF sanity, validation
rejections added in this slice, Nepal frictions UNKNOWN handling,
and volatility-regime flag behaviour. All fixtures are offline and
deterministic. Research only: no test here is buy/sell/hold advice,
and no Nepal rate below is a real schedule — the "known" friction
fixture is explicitly a TEST FIXTURE, not a sourced NEPSE fee.
"""
import math

import pytest

from toolkit import (backtest, forecast, montecarlo, nepal_frictions,
                     options, performance, portfolio, predictive, ratios,
                     valuation)
from toolkit.nepal_frictions import NepalFrictions, UnknownFrictionError


# ---------------------------------------------------------------- options
def test_put_call_parity_phase4():
    """Put-call parity (Stoll 1969; Black-Scholes-Merton 1973):
    C - P = S - K*exp(-rT). Must hold to floating-point tolerance."""
    S, K, T, r, sigma = 120.0, 100.0, 0.5, 0.04, 0.25
    c = options.black_scholes(S, K, T, r, sigma, "call")
    p = options.black_scholes(S, K, T, r, sigma, "put")
    assert (c - p) == pytest.approx(S - K * math.exp(-r * T), abs=1e-9)
    assert c > 0 and p > 0


def test_greeks_bogus_kind_raises():
    """Pre-Phase-4 bug, proven before the fix: an unknown kind fell
    through to the put branch and returned put Greeks silently."""
    with pytest.raises(ValueError):
        options.greeks(100, 100, 1.0, 0.05, 0.2, kind="bogus")


def test_monte_carlo_bogus_kind_raises():
    """Same fall-through bug in Monte Carlo pricing: unknown kind
    previously priced a put silently."""
    with pytest.raises(ValueError):
        montecarlo.monte_carlo_option_price(100, 100, 1.0, 0.05, 0.2,
                                            kind="bogus", n_paths=50, steps=5)


def test_greeks_call_put_delta_bracket():
    """Call delta in (0, 1), put delta in (-1, 0), gamma/vega positive
    for both — standard Black-Scholes Greek signs."""
    call = options.greeks(100, 100, 1.0, 0.05, 0.2, "call")
    put = options.greeks(100, 100, 1.0, 0.05, 0.2, "put")
    assert 0 < call["delta"] < 1
    assert -1 < put["delta"] < 0
    assert call["gamma"] > 0 and put["gamma"] > 0
    assert call["vega"] > 0 and put["vega"] > 0


# ------------------------------------------------------------------- DCF
def test_dcf_rises_with_cash_flow():
    """Scaling every cash flow (explicit + terminal) scales value."""
    base = valuation.dcf_value([100.0, 100.0], 0.10, 0.02, 102.0)
    doubled = valuation.dcf_value([200.0, 200.0], 0.10, 0.02, 204.0)
    assert doubled == pytest.approx(2 * base)
    assert doubled > base > 0


def test_dcf_falls_with_discount_rate():
    """Higher discount rate -> lower present value (same cash flows)."""
    low = valuation.dcf_value([100.0, 100.0], 0.08, 0.02, 102.0)
    high = valuation.dcf_value([100.0, 100.0], 0.15, 0.02, 102.0)
    assert high < low


def test_dcf_rejects_nonfinite_and_empty():
    with pytest.raises(ValueError):
        valuation.dcf_value([], 0.10, 0.02, 102.0)
    with pytest.raises(ValueError):
        valuation.dcf_value([100.0, float("nan")], 0.10, 0.02, 102.0)
    with pytest.raises(ValueError):
        valuation.dcf_value([100.0], 0.10, 0.02, float("inf"))


def test_gordon_direction():
    """Gordon (1959): P = D1/(k-g) rises with D1, falls as k rises."""
    assert valuation.gordon_value(10.0, 0.10, 0.03) > valuation.gordon_value(5.0, 0.10, 0.03)
    assert valuation.gordon_value(5.0, 0.15, 0.03) < valuation.gordon_value(5.0, 0.10, 0.03)
    with pytest.raises(ValueError):
        valuation.gordon_value(float("nan"), 0.10, 0.03)


# ------------------------------------------------------------- validation
def test_negative_prices_rejected():
    with pytest.raises(ValueError):
        portfolio.returns_from_prices([100, -110])
    with pytest.raises(ValueError):
        portfolio.returns_from_prices([0, 100])


def test_max_drawdown_rejects_negative_price():
    """Pre-Phase-4: [100, -50, 80] returned a 1.5 (150%) drawdown."""
    with pytest.raises(ValueError):
        performance.max_drawdown([100, -50, 80])
    assert performance.max_drawdown([100, 110, 99]) == pytest.approx(0.10, rel=1e-9)


def test_backtest_rejects_negative_cost_and_bad_signal():
    """Pre-Phase-4: cost_per_trade=-5 ADDED money on every trade,
    and signal=2 was silently accepted."""
    prices = [100, 101, 102, 103]
    with pytest.raises(ValueError):
        backtest.backtest(prices, [0, 1, 1, 1], cost_per_trade=-5.0)
    with pytest.raises(ValueError):
        backtest.backtest([100, 101, 102], [0, 2, 2])
    with pytest.raises(ValueError):
        backtest.backtest([100, -101, 102], [0, 1, 1])


def test_gbm_rejects_zero_or_negative_time():
    """Pre-Phase-4: T=0 returned flat paths; T<0 ran time backwards."""
    with pytest.raises(ValueError):
        montecarlo.gbm_paths(100, 0.05, 0.2, 0.0, 5, 2)
    with pytest.raises(ValueError):
        montecarlo.gbm_paths(100, 0.05, 0.2, -1.0, 5, 2)


def test_simulated_var_rejects_bad_alpha():
    """Pre-Phase-4: alpha=1.0 raised IndexError, not ValueError."""
    with pytest.raises(ValueError):
        montecarlo.simulated_var([0.01, -0.02, 0.03], alpha=1.0)
    with pytest.raises(ValueError):
        montecarlo.simulated_var([], alpha=0.05)


def test_sample_covariance_rejects_mismatched_and_nan():
    with pytest.raises(ValueError):
        portfolio.sample_covariance([[0.01, 0.02], [0.01]])
    with pytest.raises(ValueError):
        portfolio.sample_covariance([[0.01, float("nan")], [0.01, 0.02]])


def test_sharpe_rejects_nonfinite_and_bad_periods():
    with pytest.raises(ValueError):
        performance.sharpe_ratio([0.01, float("nan")])
    with pytest.raises(ValueError):
        performance.sharpe_ratio([0.01, 0.02], periods_per_year=0)
    with pytest.raises(ValueError):
        performance.annualised_return([], periods_per_year=252)


def test_ratios_reject_nan_inputs():
    with pytest.raises(ValueError):
        ratios.profitability(1000.0, float("nan"), 150.0, 90.0)
    with pytest.raises(ValueError):
        ratios.dupont_roe(float("inf"), 1000.0, 2000.0, 800.0)
    with pytest.raises(ValueError):
        ratios.leverage(500.0, float("nan"))


def test_predictive_rejects_nonpositive_prices():
    calm = [100 + i * 0.1 for i in range(30)]
    with pytest.raises(ValueError):
        predictive.volatility_regime([100, 0, 101] + calm, window=5)
    with pytest.raises(ValueError):
        predictive.anomaly_flags([100, 101, -5, 102, 103, 104], z=1.0)


def test_forecast_rejects_nan_train():
    with pytest.raises(ValueError):
        forecast.naive_forecast([1.0, float("nan")])
    with pytest.raises(ValueError):
        forecast.linear_trend_forecast([1.0, float("inf")])


def test_portfolio_variance_formula():
    """Portfolio variance w' Sigma w (Markowitz): hand-computed case."""
    cov = [[0.04, 0.01], [0.01, 0.09]]
    var = 0.25 * 0.04 + 0.25 * 0.09 + 2 * 0.25 * 0.01
    assert portfolio.portfolio_volatility([0.5, 0.5], cov) == pytest.approx(math.sqrt(var))
    assert portfolio.portfolio_return([0.5, 0.5], [0.02, 0.04]) == pytest.approx(0.03)


def test_cvar_is_mean_of_worst_tail():
    """Historical CVaR (expected shortfall): mean of worst alpha fraction."""
    rets = [0.01 * i for i in range(-10, 10)]  # -0.10 .. 0.09, 20 values
    assert performance.cvar(rets, 0.05) == pytest.approx(-0.10)


# ------------------------------------------------------- regime awareness
def _prices_from_returns(returns, start=100.0):
    prices = [start]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    return prices


def test_volatility_regime_high_and_low():
    calm = [0.001, -0.001] * 15
    assert predictive.volatility_regime(_prices_from_returns(calm), window=5)["regime"] == "low"
    tail = calm[:20] + [0.05, -0.05] * 4
    assert predictive.volatility_regime(_prices_from_returns(tail), window=5)["regime"] == "high"


def test_performance_report_regime_passthrough_and_default():
    rets = [0.01, -0.02, 0.03, 0.01, -0.01, 0.02]
    prices = [100, 101, 99, 102, 103, 102, 104]
    default = performance.performance_report(rets, prices, label="t")
    assert default["volatility_regime"] is None
    high = performance.performance_report(rets, prices, label="t", volatility_regime="high")
    assert high["volatility_regime"] == "high"
    # The flag changes no metric.
    for key in ("annualised_return", "annualised_volatility", "sharpe",
                "sortino", "max_drawdown", "cvar_5pct"):
        assert high[key] == default[key]


def test_performance_report_regime_wiring_from_predictive():
    series = _prices_from_returns([0.001, -0.001] * 15)
    regime = predictive.volatility_regime(series, window=5)["regime"]
    rets = [series[i] / series[i - 1] - 1 for i in range(1, len(series))]
    rep = performance.performance_report(rets, series, volatility_regime=regime)
    assert rep["volatility_regime"] == "low"


def test_performance_report_rejects_bad_regime():
    with pytest.raises(ValueError):
        performance.performance_report([0.01, 0.02], [100, 101, 102],
                                       volatility_regime="medium")


def test_walk_forward_regime_recorded_metrics_unchanged():
    series = [100 + i + (0.5 if i % 3 == 0 else 0.0) for i in range(20)]
    models = {"naive": forecast.naive_forecast}
    plain = forecast.walk_forward_evaluate(series, models, train_size=10)
    flagged = forecast.walk_forward_evaluate(series, models, train_size=10,
                                             volatility_regime="high")
    assert flagged["naive"]["volatility_regime"] == "high"
    assert plain["naive"]["volatility_regime"] is None
    assert flagged["naive"]["mae"] == plain["naive"]["mae"]
    assert flagged["naive"]["rmse"] == plain["naive"]["rmse"]
    with pytest.raises(ValueError):
        forecast.walk_forward_evaluate(series, models, train_size=10,
                                       volatility_regime="wild")


# --------------------------------------------------------- Nepal frictions
def test_nepal_default_all_unknown():
    f = nepal_frictions.default_unknown_frictions()
    assert f.unknown_fields() == [
        "brokerage_rate", "sebon_fee_rate", "dp_charge_flat",
        "capital_gains_tax_rate", "circuit_limit_up",
        "circuit_limit_down", "settlement_lag_business_days"]
    assert f.brokerage_rate is None and f.sebon_fee_rate is None
    assert f.dp_charge_flat is None and f.capital_gains_tax_rate is None
    assert f.is_cost_known() is False


def test_nepal_report_unknown_never_assumes_zero():
    f = nepal_frictions.default_unknown_frictions()
    rep = nepal_frictions.trade_cost_report(100000.0, "buy", f)
    assert rep["status"] == "UNKNOWN"
    assert rep["total_cost"] is None and rep["net_cash"] is None
    assert "brokerage_rate" in rep["unknown_fields"]
    # A zero-cost assumption would have returned total_cost == 0.0.
    assert rep["total_cost"] != 0.0


def test_nepal_apply_raises_on_unknown():
    f = nepal_frictions.default_unknown_frictions()
    with pytest.raises(UnknownFrictionError):
        nepal_frictions.apply_trade_frictions(100000.0, "buy", f)
    with pytest.raises(UnknownFrictionError):
        nepal_frictions.backtest_trade_cost(100000.0, "sell", f, realised_gain=5000.0)


def test_nepal_unsourced_rate_rejected():
    """A rate without source + date must not be storable."""
    with pytest.raises(ValueError):
        NepalFrictions(brokerage_rate=0.004)
    with pytest.raises(ValueError):
        NepalFrictions(brokerage_rate=0.004, source="some broker", as_of=None)


def test_nepal_invalid_rate_rejected():
    with pytest.raises(ValueError):
        NepalFrictions(brokerage_rate=1.5, source="test", as_of="2026-10-test")
    with pytest.raises(ValueError):
        NepalFrictions(dp_charge_flat=-25.0, source="test", as_of="2026-10-test")


def _test_fixture_frictions():
    """TEST FIXTURE ONLY — these numbers are invented for arithmetic
    tests and are NOT a real NEPSE/SEBON/broker schedule."""
    return NepalFrictions(
        brokerage_rate=0.004, sebon_fee_rate=0.00015, dp_charge_flat=25.0,
        capital_gains_tax_rate=0.05, circuit_limit_up=0.10,
        circuit_limit_down=0.10, settlement_lag_business_days=2,
        source="TEST FIXTURE ONLY — not a real schedule",
        as_of="2026-10-test")


def test_nepal_known_rates_compute_correctly():
    f = _test_fixture_frictions()
    buy = nepal_frictions.trade_cost_report(100000.0, "buy", f)
    assert buy["status"] == "KNOWN"
    assert buy["brokerage_cost"] == pytest.approx(400.0)
    assert buy["sebon_fee"] == pytest.approx(15.0)
    assert buy["dp_charge"] == pytest.approx(25.0)
    assert buy["capital_gains_tax"] == pytest.approx(0.0)
    assert buy["total_cost"] == pytest.approx(440.0)
    assert buy["net_cash"] == pytest.approx(-100440.0)
    sell = nepal_frictions.trade_cost_report(100000.0, "sell", f, realised_gain=10000.0)
    assert sell["capital_gains_tax"] == pytest.approx(500.0)
    assert sell["total_cost"] == pytest.approx(940.0)
    assert sell["net_cash"] == pytest.approx(99060.0)
    loss = nepal_frictions.trade_cost_report(100000.0, "sell", f, realised_gain=-5000.0)
    assert loss["capital_gains_tax"] == pytest.approx(0.0)


def test_nepal_sell_without_gain_is_unknown():
    """CGT needs a gain: a sell with no realised_gain cannot be priced."""
    f = _test_fixture_frictions()
    rep = nepal_frictions.trade_cost_report(100000.0, "sell", f)
    assert rep["status"] == "UNKNOWN"
    assert "realised_gain" in rep["unknown_fields"]
    with pytest.raises(UnknownFrictionError):
        nepal_frictions.apply_trade_frictions(100000.0, "sell", f)


def test_nepal_circuit_unknown_then_known():
    unknown = nepal_frictions.circuit_status(110.0, 100.0,
                                             nepal_frictions.default_unknown_frictions())
    assert unknown["status"] == "UNKNOWN" and unknown["within_limits"] is None
    f = _test_fixture_frictions()
    assert nepal_frictions.circuit_status(105.0, 100.0, f)["within_limits"] is True
    assert nepal_frictions.circuit_status(115.0, 100.0, f)["within_limits"] is False


def test_nepal_settlement_unknown_then_known():
    unknown = nepal_frictions.settlement_status(nepal_frictions.default_unknown_frictions())
    assert unknown["status"] == "UNKNOWN"
    assert unknown["settlement_lag_business_days"] is None
    # The "2" here is the test fixture's lag, NOT an asserted NEPSE rule.
    known = nepal_frictions.settlement_status(_test_fixture_frictions())
    assert known["status"] == "KNOWN" and known["settlement_cycle"] == "T+2"


def test_nepal_trade_input_validation():
    f = _test_fixture_frictions()
    with pytest.raises(ValueError):
        nepal_frictions.trade_cost_report(-100.0, "buy", f)
    with pytest.raises(ValueError):
        nepal_frictions.trade_cost_report(100.0, "hold", f)
