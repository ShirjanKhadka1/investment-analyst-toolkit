"""Phase 11 coverage tests — every test asserts behaviour, not just execution.

Targets the lines coverage showed as missing at 80% total (28-29 Oct 2026):
free_data adapters/wrappers, portfolio guards + efficient_frontier,
Monte Carlo happy paths, ratios guards, predictive validation/fallback
branches, checks guards, the data-layer fetchers (transport stubbed, so
parsing/validation behaviour is what is asserted — no live network), and
the CLI commands (network/autonomous calls stubbed the same way).

Nothing here touches the network, a private repo, or Nepse Decode.
"""
import datetime
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from toolkit import (
    checks,
    cli,
    data,
    free_data,
    montecarlo,
    options,
    portfolio,
    predictive,
    ratios,
)

# ---------------------------------------------------------------- free_data
_ECB_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
 <Cube><Cube time="2026-10-27">
  <Cube currency="USD" rate="1.1576"/>
  <Cube currency="NPR" rate="154.32"/>
 </Cube></Cube></gesmes:Envelope>"""


def test_list_free_data_sources_all_no_key():
    sources = free_data.list_free_data_sources()
    assert len(sources) == 5
    assert all(s["needs_key"] is False for s in sources)
    kinds = {s["kind"] for s in sources}
    assert {"stock", "crypto", "fx-reference", "macro"} <= kinds
    assert any("Yahoo" in s["source"] for s in sources)


def test_coingecko_history_maps_symbol_and_keeps_points(monkeypatch):
    seen = {}

    def fake_get_json(url):
        seen["url"] = url
        return {"prices": [[1761523200000, 80000.0], [1761609600000, 81000.0]]}

    monkeypatch.setattr(free_data, "_get_json", fake_get_json)
    pts = free_data.fetch_coingecko_history("BTC")
    assert "bitcoin" in seen["url"]
    assert [p[1] for p in pts] == [80000.0, 81000.0]
    assert pts[0][0] < pts[1][0]
    pts2 = free_data.fetch_coingecko_history("SOL")
    assert "solana" in seen["url"]
    assert len(pts2) == 2


def test_coingecko_history_empty_payload_raises(monkeypatch):
    monkeypatch.setattr(free_data, "_get_json", lambda url: {"prices": []})
    with pytest.raises(RuntimeError):
        free_data.fetch_coingecko_history("BTC")


def test_crypto_and_stooq_adapters_rewrite_symbols(monkeypatch):
    captured = []
    monkeypatch.setattr(free_data, "fetch_yahoo_prices", lambda s: captured.append(s))
    monkeypatch.setattr(free_data, "fetch_stooq_prices", lambda s: captured.append(s))
    free_data._yahoo_crypto_adapter("BTC")
    free_data._yahoo_crypto_adapter("ETH-USD")
    free_data._stooq_adapter("AAPL")
    free_data._stooq_adapter("aapl.us")
    assert captured == ["BTC-USD", "ETH-USD", "aapl.us", "aapl.us"]


def test_ecb_reference_rates_from_fixture(monkeypatch):
    monkeypatch.setattr(free_data, "_get_text", lambda url: _ECB_FIXTURE)
    out = free_data.fetch_ecb_reference_rates()
    assert out["date"] == "2026-10-27"
    assert out["rates"]["USD"] == 1.1576
    assert out["source"] == "ECB euro reference rates"
    assert out["retrieved_at"]  # timestamp present, not relabelled as today


def test_free_macro_wrapper_carries_provenance(monkeypatch):
    monkeypatch.setattr(free_data, "fetch_worldbank_indicator",
                        lambda c, i, per_page=100: [("2024", 42.0)])
    out = free_data.fetch_free_macro("NPL", "NY.GDP.MKTP.CD")
    assert out["source"] == "World Bank API v2"
    assert out["country"] == "NPL" and out["values"] == [("2024", 42.0)]
    assert out["retrieved_at"]


# ---------------------------------------------------------------- portfolio
def test_returns_from_prices_single_price_raises_and_happy_path():
    with pytest.raises(ValueError):
        portfolio.returns_from_prices([100.0])
    assert portfolio.returns_from_prices([100.0, 110.0, 121.0]) == pytest.approx([0.1, 0.1])


def test_sample_covariance_no_assets_and_mismatched_lengths():
    with pytest.raises(ValueError):
        portfolio.sample_covariance([])
    # Both series long enough that the length check itself must fire
    # (a too-short series would fail validation first).
    with pytest.raises(ValueError, match="same length"):
        portfolio.sample_covariance([[0.01, 0.02, 0.03], [0.01, 0.02]])


def test_shrunk_covariance_alpha_bounds_and_diagonal_only():
    with pytest.raises(ValueError):
        portfolio.shrunk_covariance([[0.01, 0.02], [0.01, 0.03]], alpha=-0.1)
    with pytest.raises(ValueError):
        portfolio.shrunk_covariance([[0.01, 0.02], [0.01, 0.03]], alpha=1.1)
    diag = portfolio.shrunk_covariance([[0.01, 0.02, 0.03], [0.02, 0.01, 0.02]], alpha=1.0)
    assert diag[0][1] == 0.0 and diag[1][0] == 0.0
    assert diag[0][0] > 0 and diag[1][1] > 0


def test_solve_singular_matrix_raises():
    with pytest.raises(ValueError, match="singular"):
        portfolio._solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 1.0])
    # Non-singular control: the solver itself works.
    assert portfolio._solve([[2.0, 0.0], [0.0, 2.0]], [2.0, 4.0]) == pytest.approx([1.0, 2.0])


def test_inverse_volatility_zero_vol_asset_raises():
    with pytest.raises(ValueError, match="zero volatility"):
        portfolio.inverse_volatility_weights([[0.0, 0.0, 0.0], [0.01, 0.02, 0.01]])


def test_min_variance_degenerate_guard(monkeypatch):
    # A positive-definite covariance can never give a zero-sum inverse-ones
    # vector, so the guard is exercised with a stubbed solver: the guard,
    # not the solver, is what this test asserts.
    monkeypatch.setattr(portfolio, "_solve", lambda a, b: [1.0, -1.0])
    with pytest.raises(ValueError, match="degenerate"):
        portfolio.min_variance_weights([[0.01, 0.02], [0.01, 0.03]])


def test_max_sharpe_degenerate_total_raises():
    # Constant +0.02 and constant -0.02 assets: zero sample covariance,
    # ridge gives a diagonal matrix, so raw weights are [2, -2], sum 0.
    with pytest.raises(ValueError, match="degenerate"):
        portfolio.max_sharpe_weights([[0.02, 0.02], [-0.02, -0.02]])


def test_clean_weights_none_survive_raises():
    with pytest.raises(ValueError, match="no weights survive"):
        portfolio.clean_weights([0.001, 0.002], threshold=0.01)
    cleaned = portfolio.clean_weights([0.5, 0.4, 0.001], threshold=0.01)
    assert cleaned == pytest.approx([0.5 / 0.9, 0.4 / 0.9, 0.0])
    assert sum(cleaned) == pytest.approx(1.0)


def test_portfolio_volatility_negative_variance_raises():
    with pytest.raises(ValueError, match="negative portfolio variance"):
        portfolio.portfolio_volatility([1.0], [[-1.0]])
    assert portfolio.portfolio_volatility([1.0, 0.0], [[0.04, 0.0], [0.0, 0.09]]) == pytest.approx(0.2)


def test_efficient_frontier_hits_target_and_sums_to_one():
    rets = [[0.01, 0.02, 0.015, 0.01, 0.02], [0.005, 0.01, 0.0, 0.012, 0.008]]
    means = portfolio.mean_returns(rets)
    target = sum(means) / 2
    rows = portfolio.efficient_frontier(rets, [target, means[0]])
    assert len(rows) == 2
    for tgt, vol, w in rows:
        assert sum(w) == pytest.approx(1.0, abs=1e-9)
        assert sum(wi * mi for wi, mi in zip(w, means)) == pytest.approx(tgt, abs=1e-6)
        assert vol >= 0.0


# ---------------------------------------------------------------- montecarlo
def test_gbm_rejects_negative_sigma_and_bad_counts():
    with pytest.raises(ValueError):
        montecarlo.gbm_paths(100, 0.05, -0.2, 1.0, 5, 2)
    with pytest.raises(ValueError):
        montecarlo.gbm_paths(100, 0.05, 0.2, 1.0, 0, 2)
    with pytest.raises(ValueError):
        montecarlo.gbm_paths(100, 0.05, 0.2, 1.0, 5, 0)


def test_gbm_seed_reproducible_and_shape():
    a = montecarlo.gbm_paths(100, 0.05, 0.2, 1.0, 5, 3, seed=7)
    b = montecarlo.gbm_paths(100, 0.05, 0.2, 1.0, 5, 3, seed=7)
    assert a == b
    assert len(a) == 3 and all(len(p) == 6 and p[0] == 100 for p in a)
    assert montecarlo.gbm_paths(100, 0.05, 0.2, 1.0, 5, 1, seed=8) != a[:1]


def test_simulated_var_quantile_values():
    # alpha=0.05 on 3 values: k=floor(0.15)=0 -> worst return, as a loss.
    assert montecarlo.simulated_var([0.01, -0.02, 0.03], alpha=0.05) == pytest.approx(0.02)
    # alpha=0.5: k=floor(1.5)=1 -> middle return 0.01, loss -0.01.
    assert montecarlo.simulated_var([0.01, -0.02, 0.03], alpha=0.5) == pytest.approx(-0.01)


def test_monte_carlo_option_price_near_black_scholes():
    call = montecarlo.monte_carlo_option_price(100, 100, 1.0, 0.05, 0.2,
                                               "call", n_paths=20000, seed=42, steps=50)
    put = montecarlo.monte_carlo_option_price(100, 100, 1.0, 0.05, 0.2,
                                              "put", n_paths=20000, seed=42, steps=50)
    bs = options.black_scholes(100, 100, 1.0, 0.05, 0.2, "call")
    assert abs(call - bs) < 1.0  # MC noise, not exact agreement
    assert 0 < put < call < 100  # ATM with r > 0: call worth more than put


# ---------------------------------------------------------------- ratios
def test_leverage_zero_equity_raises_and_assets_branch():
    with pytest.raises(ValueError, match="must not be zero"):
        ratios.leverage(500.0, 0.0)
    out = ratios.leverage(500.0, 1000.0, total_assets=2000.0)
    assert out == {"debt_to_equity": 0.5, "debt_to_assets": 0.25}
    assert "debt_to_assets" not in ratios.leverage(500.0, 1000.0)
    # Negative equity is a real distressed state, not an error: ratio is negative.
    assert ratios.leverage(500.0, -100.0)["debt_to_equity"] == pytest.approx(-5.0)


def test_dupont_zero_equity_raises():
    with pytest.raises(ValueError, match="must not be zero"):
        ratios.dupont_roe(90.0, 1000.0, 2000.0, 0.0)


# ---------------------------------------------------------------- predictive
def test_exp_smoothing_alpha_bounds_and_values():
    with pytest.raises(ValueError):
        predictive.exponential_smoothing_forecast([10.0, 20.0, 30.0], alpha=0.0)
    with pytest.raises(ValueError):
        predictive.exponential_smoothing_forecast([10.0, 20.0, 30.0], alpha=1.5)
    assert predictive.exponential_smoothing_forecast([10.0, 20.0, 30.0], alpha=1.0) == 30.0
    # level: 10 -> 0.5*20+0.5*10=15 -> 0.5*30+0.5*15=22.5
    assert predictive.exponential_smoothing_forecast([10.0, 20.0, 30.0], alpha=0.5) == pytest.approx(22.5)


def test_predictive_horizon_and_train_size_guards():
    series = [100.0 + i for i in range(20)]
    with pytest.raises(ValueError):
        predictive.auto_select_model(series, train_size=0)
    with pytest.raises(ValueError):
        predictive.auto_select_model(series, train_size=20)
    with pytest.raises(ValueError):
        predictive.ar1_forecast(series, horizon=0)
    with pytest.raises(ValueError):
        predictive.holt_forecast(series, horizon=0)
    with pytest.raises(ValueError):
        predictive.drift_forecast(series, horizon=0)
    with pytest.raises(ValueError):
        predictive.drift_forecast(series, horizon=1, window=0)
    # Drift with an explicit window uses only the last `window` changes:
    # last 2 changes of [100..119] are both 1.0, so forecast = 119 + 1.0.
    assert predictive.drift_forecast(series, horizon=1, window=2) == pytest.approx(120.0)
    with pytest.raises(ValueError):
        predictive.walk_forward_forecasts(series, {"naive": lambda t, h: t[-1]},
                                          train_size=10, horizon=0)
    with pytest.raises(ValueError):
        predictive.prediction_interval(series, lambda t, h: t[-1], horizon=0)
    with pytest.raises(ValueError):
        predictive.adaptive_ensemble_forecast(series, horizon=0)


def test_walk_forward_horizon_guards_and_values():
    series = [100.0 + i * 0.5 for i in range(20)]
    models_h = predictive._horizon_models()
    res = predictive._walk_forward_horizon(series, models_h, train_size=10, horizon=1)
    assert res["naive"]["n_test"] == 10
    assert res["naive"]["mae"] == pytest.approx(0.5)  # last value lags a 0.5 step
    with pytest.raises(ValueError):
        predictive._walk_forward_horizon(series, models_h, train_size=20, horizon=1)
    with pytest.raises(ValueError):
        predictive._walk_forward_horizon(series, models_h, train_size=10, horizon=0)
    with pytest.raises(ValueError, match="no complete horizon window"):
        predictive._walk_forward_horizon(series, models_h, train_size=19, horizon=5)


def test_empirical_interval_alpha_guard_and_single_residual():
    with pytest.raises(ValueError):
        predictive.empirical_interval([1.0, -1.0], alpha=1.5)
    with pytest.raises(ValueError):
        predictive.empirical_interval([], alpha=0.1)
    # One residual: both quantile positions coincide, so lo == hi == the value.
    assert predictive.empirical_interval([5.0], alpha=0.10) == (5.0, 5.0)


def test_prediction_interval_falls_back_when_forecaster_fails_early():
    def flaky(train, horizon):
        if len(train) < 8:
            raise ValueError("too short for this forecaster")
        return train[-1]

    train = [100.0 + i for i in range(12)]
    res = predictive.prediction_interval(train, flaky, horizon=1, alpha=0.10)
    # Inner origins 6,7 fail and are skipped; 8..11 give 4 residuals, all 1.0,
    # so the normal-approx fallback applies with sd 0 and point 111.0.
    assert res["method"] == "normal-approx (few residuals)"
    assert res["n_residuals"] == 4
    assert res["point"] == pytest.approx(111.0)
    assert res["lower"] == pytest.approx(111.0) and res["upper"] == pytest.approx(111.0)


def test_adaptive_weights_fallback_labels():
    short = [100.0 + i for i in range(10)]
    res = predictive.adaptive_ensemble_forecast(short, horizon=1)
    assert res["weighting"] == "fallback: equal (train too short)"
    assert sum(res["weights"].values()) == pytest.approx(1.0)
    assert len(set(res["weights"].values())) == 1
    # 12 points, horizon 5: no internal origin exists (range(10, 8) is empty).
    res2 = predictive.adaptive_ensemble_forecast([100.0 + i * 0.5 for i in range(12)],
                                                 horizon=5)
    assert res2["weighting"] == "fallback: equal (no internal origin)"
    assert sum(res2["weights"].values()) == pytest.approx(1.0)


def test_ensemble_forecast_equal_fallback_is_the_mean_of_points():
    train = [100.0, 102.0, 104.0]
    res = predictive.ensemble_forecast(train)
    assert res["weighting"] == "equal (no walk-forward weights supplied)"
    assert res["forecast"] == pytest.approx(sum(res["points"].values()) / len(res["points"]))
    assert set(res["points"]) == {"naive", "moving_average_5", "linear_trend", "exp_smoothing_0.3"}


def test_anomaly_flags_flat_series_returns_empty():
    assert predictive.anomaly_flags([100.0] * 10) == []


# ---------------------------------------------------------------- checks
def test_checks_require_guards():
    with pytest.raises(ValueError, match="is required"):
        checks.require_positive("x", None)
    with pytest.raises(ValueError, match="finite number"):
        checks.require_positive("x", "bad")
    with pytest.raises(ValueError, match="must be positive"):
        checks.require_positive("x", 0)
    with pytest.raises(ValueError, match="is required"):
        checks.require_non_negative("x", None)
    with pytest.raises(ValueError, match="finite number"):
        checks.require_non_negative("x", "bad")
    with pytest.raises(ValueError, match="must not be negative"):
        checks.require_non_negative("x", -1)
    with pytest.raises(ValueError, match="is required"):
        checks.require_finite("x", None)
    with pytest.raises(ValueError, match="finite number"):
        checks.require_finite("x", float("nan"))
    with pytest.raises(ValueError, match="is required"):
        checks.require_rate("x", None)
    with pytest.raises(ValueError, match="finite number"):
        checks.require_rate("x", "bad")
    with pytest.raises(ValueError, match="between -1 and 1"):
        checks.require_rate("x", 1.5)
    with pytest.raises(ValueError, match="is required"):
        checks.validate_series("x", None)
    with pytest.raises(ValueError, match="must be a list of numbers"):
        checks.validate_series("x", 5)
    with pytest.raises(ValueError, match="is empty"):
        checks.validate_price_pairs("x", [])
    with pytest.raises(ValueError, match="must be a \\(date, close\\) pair"):
        checks.validate_price_pairs("x", [["2026-01-01"]])


# ---------------------------------------------------------------- data layer
class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload if isinstance(self._payload, bytes) else self._payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(monkeypatch, payload=None, exc=None):
    def fake(req, timeout=20):
        if exc is not None:
            raise exc
        return _FakeResp(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake)


def test_get_json_and_text_error_mapping(monkeypatch):
    url = "https://example.invalid/x"
    _patch_urlopen(monkeypatch, exc=urllib.error.HTTPError(url, 403, "Forbidden", {}, None))
    with pytest.raises(RuntimeError, match="API call failed \\(403\\)"):
        data._get_json(url)
    _patch_urlopen(monkeypatch, exc=urllib.error.URLError("boom"))
    with pytest.raises(RuntimeError, match="Network call failed"):
        data._get_json(url)
    _patch_urlopen(monkeypatch, exc=urllib.error.HTTPError(url, 429, "Too Many", {}, None))
    with pytest.raises(RuntimeError, match="API call failed \\(429\\)"):
        data._get_text(url)
    _patch_urlopen(monkeypatch, exc=urllib.error.URLError("boom"))
    with pytest.raises(RuntimeError, match="Network call failed"):
        data._get_text(url)
    _patch_urlopen(monkeypatch, payload=json.dumps({"ok": True}))
    assert data._get_json(url) == {"ok": True}
    _patch_urlopen(monkeypatch, payload="hello")
    assert data._get_text(url) == "hello"


def test_fetch_yahoo_prices_skips_none_and_rejects_bad_payloads(monkeypatch):
    payload = {"chart": {"result": [{"timestamp": [1700000000, 1700086400, 1700172800],
                                     "indicators": {"quote": [{"close": [100.0, None, 102.0]}]}}]}}
    monkeypatch.setattr(data, "_get_json", lambda url: payload)
    pairs = data.fetch_yahoo_prices("AAPL")
    assert [c for _, c in pairs] == [100.0, 102.0]
    assert pairs[0][0] < pairs[1][0]
    monkeypatch.setattr(data, "_get_json", lambda url: {})
    with pytest.raises(RuntimeError, match="Unexpected Yahoo"):
        data.fetch_yahoo_prices("AAPL")
    monkeypatch.setattr(data, "_get_json",
                        lambda url: {"chart": {"result": [{"timestamp": [1700000000],
                                                           "indicators": {"quote": [{"close": [None]}]}}]}})
    with pytest.raises(RuntimeError, match="No prices"):
        data.fetch_yahoo_prices("AAPL")


def test_fetch_worldbank_indicator_sorts_and_skips_none(monkeypatch):
    rows = [{"date": "2023", "value": 5.0}, {"date": "2024", "value": None},
            {"date": "2022", "value": 3.0}]
    monkeypatch.setattr(data, "_get_json", lambda url: [{}, rows])
    assert data.fetch_worldbank_indicator("NPL", "X") == [("2022", 3.0), ("2023", 5.0)]
    monkeypatch.setattr(data, "_get_json", lambda url: [{}, []])
    with pytest.raises(RuntimeError, match="No World Bank"):
        data.fetch_worldbank_indicator("NPL", "X")
    monkeypatch.setattr(data, "_get_json", lambda url: {"not": "a list"})
    with pytest.raises(RuntimeError, match="No World Bank"):
        data.fetch_worldbank_indicator("NPL", "X")


def test_fetch_fred_series_key_guard_and_parsing(monkeypatch):
    with pytest.raises(ValueError, match="api_key"):
        data.fetch_fred_series("GDP", "")
    monkeypatch.setattr(data, "_get_json", lambda url: {"observations": [
        {"date": "2024-01-01", "value": "."},
        {"date": "2024-01-02", "value": "123.5"},
        {"date": "2024-01-03", "value": ""}]})
    assert data.fetch_fred_series("GDP", "KEY") == [("2024-01-02", 123.5)]


def test_fetch_stooq_prices_parsing_and_rejections(monkeypatch):
    monkeypatch.setattr(data, "_get_text",
                        lambda url: "Date,Close\n2024-01-02,100.5\n2024-01-03,101.5\n")
    assert data.fetch_stooq_prices("aapl.us") == [("2024-01-02", 100.5), ("2024-01-03", 101.5)]
    monkeypatch.setattr(data, "_get_text", lambda url: "Date,Open\n2024-01-02,100.5\n")
    with pytest.raises(RuntimeError, match="No Stooq"):
        data.fetch_stooq_prices("aapl.us")
    monkeypatch.setattr(data, "_get_text", lambda url: "")
    with pytest.raises(RuntimeError, match="No Stooq"):
        data.fetch_stooq_prices("aapl.us")
    # Rows present with a Close column but no usable close -> "no closes".
    monkeypatch.setattr(data, "_get_text", lambda url: "Date,Close\n2024-01-02,\n")
    with pytest.raises(RuntimeError, match="No Stooq closes"):
        data.fetch_stooq_prices("aapl.us")


def test_alpha_vantage_parses_sorted_and_rejects_empty(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ALPHA_VANTAGE_API_KEY"):
        data.fetch_alpha_vantage_prices("AAPL")
    monkeypatch.setattr(data, "_get_json", lambda url: {"Time Series (Daily)": {
        "2024-01-03": {"4. close": "102.0"}, "2024-01-02": {"4. close": "101.0"}}})
    assert data.fetch_alpha_vantage_prices("AAPL", api_key="K") == [
        ("2024-01-02", 101.0), ("2024-01-03", 102.0)]
    monkeypatch.setattr(data, "_get_json", lambda url: {"Time Series (Daily)": {}})
    with pytest.raises(RuntimeError, match="No Alpha Vantage"):
        data.fetch_alpha_vantage_prices("AAPL", api_key="K")


def test_twelvedata_and_fmp_parse_and_reject_empty(monkeypatch):
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TWELVEDATA_API_KEY"):
        data.fetch_twelvedata_prices("AAPL")
    with pytest.raises(ValueError, match="FMP_API_KEY"):
        data.fetch_fmp_prices("AAPL")
    monkeypatch.setattr(data, "_get_json", lambda url: {"values": [
        {"datetime": "2024-01-03 00:00:00", "close": "102.0"},
        {"datetime": "2024-01-02 00:00:00", "close": "101.0"}]})
    assert data.fetch_twelvedata_prices("AAPL", api_key="K") == [
        ("2024-01-02", 101.0), ("2024-01-03", 102.0)]
    monkeypatch.setattr(data, "_get_json", lambda url: {"values": []})
    with pytest.raises(RuntimeError, match="No Twelve Data"):
        data.fetch_twelvedata_prices("AAPL", api_key="K")
    monkeypatch.setattr(data, "_get_json", lambda url: {"historical": [
        {"date": "2024-01-03", "close": 102.0}, {"date": "2024-01-02", "close": 101.0}]})
    assert data.fetch_fmp_prices("AAPL", api_key="K") == [
        ("2024-01-02", 101.0), ("2024-01-03", 102.0)]
    monkeypatch.setattr(data, "_get_json", lambda url: {"historical": []})
    with pytest.raises(RuntimeError, match="No FMP"):
        data.fetch_fmp_prices("AAPL", api_key="K")


def test_sec_cik_guard_and_passthrough(monkeypatch):
    with pytest.raises(ValueError, match="CIK must be digits"):
        data.fetch_sec_company_facts("abc")
    monkeypatch.setattr(data, "_get_json", lambda url: {"cik": "0000320193"})
    assert data.fetch_sec_company_facts(320193) == {"cik": "0000320193"}


def test_get_api_key_env_and_freshness_guards(monkeypatch):
    monkeypatch.setenv("TEST_PHASE11_KEY", "SECRET123")
    assert data.get_api_key("TEST_PHASE11_KEY", "TestProv", "https://example.invalid") == "SECRET123"
    monkeypatch.delenv("TEST_PHASE11_KEY", raising=False)
    with pytest.raises(ValueError, match="TEST_PHASE11_KEY"):
        data.get_api_key("TEST_PHASE11_KEY", "TestProv", "https://example.invalid")
    today = datetime.date(2026, 10, 28)
    assert data.check_freshness("2026-10-27", today=today)["stale"] is False
    assert data.check_freshness("2026-10-01", max_age_days=7, today=today)["stale"] is True
    with pytest.raises(ValueError, match="must be ISO"):
        data.check_freshness("not-a-date", today=today)
    with pytest.raises(ValueError, match="in the future"):
        data.check_freshness("2026-10-29", today=today)


def test_load_csv_prices_columns_and_empty(tmp_path):
    good = tmp_path / "prices.csv"
    good.write_text("date,close\n2024-01-02,100.0\n2024-01-03,101.0\n")
    assert data.load_csv_prices(str(good)) == [("2024-01-02", 100.0), ("2024-01-03", 101.0)]
    bad = tmp_path / "bad.csv"
    bad.write_text("date,open\n2024-01-02,100.0\n")
    with pytest.raises(ValueError, match="must have columns"):
        data.load_csv_prices(str(bad))
    empty = tmp_path / "empty.csv"
    empty.write_text("date,close\n")
    with pytest.raises(ValueError, match="no price rows"):
        data.load_csv_prices(str(empty))


def test_list_free_sources_flags_keyed_source():
    sources = data.list_free_sources()
    assert len(sources) == 5
    fred = next((s for s in sources if "FRED" in s["source"]), None)
    assert fred is not None
    assert fred["needs_key"] is True
    assert all(s["needs_key"] is False for s in sources if "FRED" not in s["source"])


# ---------------------------------------------------------------- cli
def test_cli_forecast_demo_and_option_return_zero(capsys):
    assert cli.main(["forecast-demo"]) == 0
    assert cli.main(["option", "--S", "100", "--K", "100", "--T", "1",
                     "--r", "0.05", "--sigma", "0.2", "--kind", "call"]) == 0
    out = capsys.readouterr().out
    assert "price:" in out and "greeks:" in out


def test_cli_predict_demo_and_csv(tmp_path):
    assert cli.main(["predict"]) == 0
    csv_path = tmp_path / "prices.csv"
    rows = ["date,close"] + [f"2024-01-{i + 1:02d},{100.0 + i}" for i in range(20)]
    csv_path.write_text("\n".join(rows) + "\n")
    assert cli.main(["predict", "--csv", str(csv_path)]) == 0


def test_cli_data_yahoo_and_stooq_stubbed(monkeypatch, capsys):
    today = datetime.date.today().isoformat()
    monkeypatch.setattr(cli.data, "fetch_yahoo_prices", lambda s: [(today, 100.0)])
    assert cli.main(["data", "--symbol", "AAPL", "--source", "yahoo"]) == 0
    assert "AAPL" in capsys.readouterr().out
    captured = []
    monkeypatch.setattr(cli.data, "fetch_stooq_prices", lambda s: captured.append(s) or [(today, 100.0)])
    assert cli.main(["data", "--symbol", "aapl", "--source", "stooq"]) == 0
    assert captured == ["aapl.us"]


def test_cli_autonomous_once_stubbed(monkeypatch):
    monkeypatch.setattr("toolkit.autonomous.run_once", lambda cfg: {"runs": 1})
    assert cli.main(["autonomous-once", "--symbols", "AAPL,MSFT"]) == 0


def test_cli_no_args_exits():
    with pytest.raises(SystemExit):
        cli.main([])
