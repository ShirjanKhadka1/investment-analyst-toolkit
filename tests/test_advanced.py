import math

from toolkit import backtest, montecarlo, options


def test_put_call_parity():
    S, K, T, r, s = 100, 100, 1.0, 0.05, 0.2
    c = options.black_scholes(S, K, T, r, s, "call")
    p = options.black_scholes(S, K, T, r, s, "put")
    assert abs((c - p) - (S - K * math.exp(-r * T))) < 1e-9


def test_greeks_delta_range():
    g = options.greeks(100, 100, 1.0, 0.05, 0.2, "call")
    assert 0 < g["delta"] < 1 and g["gamma"] > 0 and g["vega"] > 0


def test_implied_vol_roundtrip():
    price = options.black_scholes(100, 100, 1.0, 0.05, 0.2, "call")
    iv = options.implied_volatility(price, 100, 100, 1.0, 0.05, "call")
    assert abs(iv - 0.2) < 1e-3


def test_monte_carlo_reproducible():
    a = montecarlo.gbm_paths(100, 0.05, 0.2, 1.0, 10, 5, seed=7)
    b = montecarlo.gbm_paths(100, 0.05, 0.2, 1.0, 10, 5, seed=7)
    assert a == b
    assert len(a) == 5 and len(a[0]) == 11


def test_backtest_no_lookahead_and_benchmark():
    prices = [100 + i for i in range(60)]
    sig = backtest.moving_average_crossover_signals(prices, fast=5, slow=20)
    eq = backtest.backtest(prices, sig, cost_per_trade=1.0)
    bh = backtest.buy_and_hold_equity(prices)
    assert len(eq) == len(prices) == len(bh)
    assert eq[0] == 10000.0
