"""Phase 7 property-style tests on the core engines.

Deterministic, offline, no invented formulas. Each test states an
invariant that must hold for every parameter combination in the grid,
not just one worked example:
- put-call parity (Black-Scholes, 1973): C - P = S - K*exp(-rT)
- call price >= intrinsic value; put price >= intrinsic value
- DCF monotonicity: value rises with cash flows, falls with the
  discount rate, rises with terminal growth (discount > growth)
- Gordon value monotonicity in dividend and growth
- portfolio volatility: 0 <= vol <= max single-asset vol for
  long-only weights summing to 1 (diversification cannot exceed
  the riskiest asset, and variance cannot go negative)
- keyed fetchers use the RESOLVED key (env key or argument),
  never the literal argument when it is None (Phase 7 ruff fix:
  toolkit/data.py used to build keyed URLs from the unresolved
  argument, so an env-supplied key never reached the request).
"""

import math

import pytest

from toolkit import data, options, portfolio, valuation


def _parity_cases():
    for S in (80.0, 100.0, 120.0):
        for K in (90.0, 100.0, 110.0):
            for T in (0.25, 1.0, 2.0):
                for r in (0.0, 0.03, 0.08):
                    for sigma in (0.1, 0.3, 0.6):
                        yield S, K, T, r, sigma


def test_put_call_parity_grid():
    for S, K, T, r, sigma in _parity_cases():
        call = options.black_scholes(S, K, T, r, sigma, kind="call")
        put = options.black_scholes(S, K, T, r, sigma, kind="put")
        assert call - put == pytest.approx(S - K * math.exp(-r * T), abs=1e-9)


def test_option_prices_above_intrinsic_grid():
    # European no-arbitrage bounds use DISCOUNTED intrinsic value:
    # a European put may trade below undiscounted K - S when r > 0
    # (early exercise is not available), so the undiscounted bound
    # would be wrong for puts. Call >= S - K*exp(-rT), put >= K*exp(-rT) - S.
    for S, K, T, r, sigma in _parity_cases():
        call = options.black_scholes(S, K, T, r, sigma, kind="call")
        put = options.black_scholes(S, K, T, r, sigma, kind="put")
        assert call >= max(S - K * math.exp(-r * T), 0.0) - 1e-9
        assert put >= max(K * math.exp(-r * T) - S, 0.0) - 1e-9
        assert call >= 0.0 and put >= 0.0


def test_dcf_monotonicity():
    base = [100.0, 110.0, 120.0]
    v0 = valuation.dcf_value(base, 0.10, 0.03, 130.0)
    # More cash flow -> more value.
    v_more = valuation.dcf_value([200.0, 220.0, 240.0], 0.10, 0.03, 260.0)
    assert v_more > v0
    # Higher discount rate -> less value.
    v_high_r = valuation.dcf_value(base, 0.15, 0.03, 130.0)
    assert v_high_r < v0
    # Higher terminal growth (still below discount) -> more value.
    v_high_g = valuation.dcf_value(base, 0.10, 0.05, 130.0)
    assert v_high_g > v0
    # Gordon: same monotonicity in dividend and growth.
    assert valuation.gordon_value(5.0, 0.10, 0.03) < valuation.gordon_value(6.0, 0.10, 0.03)
    assert valuation.gordon_value(5.0, 0.10, 0.03) < valuation.gordon_value(5.0, 0.10, 0.05)


def test_portfolio_volatility_bounds():
    # Two assets, vols 0.20 and 0.10, correlation 0.3.
    cov = [[0.04, 0.006], [0.006, 0.01]]
    for w1 in (0.0, 0.25, 0.5, 0.75, 1.0):
        w = [w1, 1.0 - w1]
        vol = portfolio.portfolio_volatility(w, cov)
        assert 0.0 <= vol <= 0.20 + 1e-12
    # Perfectly correlated assets: vol is exactly the weighted vol.
    cov_perfect = [[0.04, 0.02], [0.02, 0.01]]
    assert portfolio.portfolio_volatility([0.5, 0.5], cov_perfect) == pytest.approx(0.15)


def test_keyed_fetchers_use_resolved_env_key(monkeypatch):
    """Env-supplied keys must reach the request URL (Phase 7 fix).

    Each keyed fetcher resolves its key (argument or env) and must
    build its URL from the RESOLVED key. We capture the URL via a
    stubbed _get_json and assert the env key appears and the
    literal string 'None' does not.
    """
    captured = {}

    def fake_get_json(url):
        captured["url"] = url
        return {"Time Series (Daily)": {"2026-10-27": {"4. close": "100.0"}}}

    monkeypatch.setattr(data, "_get_json", fake_get_json)
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "ENVKEY123")
    data.fetch_alpha_vantage_prices("AAPL", api_key=None)
    assert "ENVKEY123" in captured["url"]
    assert "None" not in captured["url"]


def test_keyed_fetcher_argument_key_used(monkeypatch):
    captured = {}

    def fake_get_json(url):
        captured["url"] = url
        return {"values": [{"datetime": "2026-10-27 00:00:00", "close": "100.0"}]}

    monkeypatch.setattr(data, "_get_json", fake_get_json)
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    data.fetch_twelvedata_prices("AAPL", api_key="ARGKEY456")
    assert "ARGKEY456" in captured["url"]
