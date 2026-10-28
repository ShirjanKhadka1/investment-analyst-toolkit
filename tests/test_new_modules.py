"""Tests for Job 3 modules: portfolio, performance, forecast, data (offline parts)."""
import math

from toolkit import data, forecast, performance, portfolio


def _demo_returns():
    # 3 assets, 12 periods, deterministic demo data only.
    return [
        [0.02, -0.01, 0.03, 0.01, 0.02, -0.02, 0.04, 0.01, 0.00, 0.03, -0.01, 0.02],
        [0.01, 0.02, -0.01, 0.02, 0.01, 0.01, -0.01, 0.02, 0.01, 0.00, 0.02, 0.01],
        [-0.01, 0.01, 0.02, -0.02, 0.03, 0.01, 0.02, -0.01, 0.03, 0.01, 0.00, 0.02],
    ]


def test_equal_and_inverse_weights_sum_to_one():
    assert abs(sum(portfolio.equal_weights(3)) - 1.0) < 1e-9
    w = portfolio.inverse_volatility_weights(_demo_returns())
    assert abs(sum(w) - 1.0) < 1e-9
    assert all(v > 0 for v in w)


def test_min_variance_and_max_sharpe_sum_to_one():
    for fn in (portfolio.min_variance_weights, portfolio.max_sharpe_weights):
        w = fn(_demo_returns())
        assert abs(sum(w) - 1.0) < 1e-9


def test_clean_weights_long_only():
    w = portfolio.clean_weights([0.5, -0.2, 0.3, 0.005])
    assert abs(sum(w) - 1.0) < 1e-9
    assert all(v >= 0 for v in w)


def test_shrunk_covariance_diagonal_only_at_alpha_one():
    cov = portfolio.shrunk_covariance(_demo_returns(), alpha=1.0)
    assert cov[0][1] == 0.0
    assert cov[0][0] > 0


def test_performance_metrics():
    rets = [0.01, -0.02, 0.03, 0.01, -0.01, 0.02]
    prices = [100, 101, 99, 102, 103, 102, 104]
    assert performance.max_drawdown(prices) > 0
    assert performance.cvar(rets, 0.05) <= 0
    rep = performance.performance_report(rets, prices, label="test")
    assert rep["label"] == "test"
    assert "sharpe" in rep


def test_train_test_split_rejects_bad_split():
    try:
        performance.train_test_split([1, 2, 3], 3)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_walk_forward_beats_naive_flag_present_and_lookahead():
    series = data.demo_series()
    results = forecast.walk_forward_evaluate(
        series,
        {"naive": forecast.naive_forecast,
         "moving_average": lambda t: forecast.moving_average_forecast(t, 3),
         "trend": forecast.linear_trend_forecast},
        train_size=10,
    )
    assert results["naive"]["n_test"] == 10
    assert "beats_naive_mae" in results["moving_average"]
    assert 0.0 <= results["naive"]["interval_coverage"] <= 1.0


def test_forecast_interval_contains_point():
    point, lo, hi = forecast.forecast_with_interval(data.demo_series(), forecast.naive_forecast)
    assert lo <= point <= hi


def test_data_list_and_demo():
    assert len(data.demo_series()) == 20
    assert any(s["needs_key"] is False for s in data.list_free_sources())


def test_returns_from_prices():
    assert abs(portfolio.returns_from_prices([100, 110, 99])[0] - 0.1) < 1e-9
    assert abs(portfolio.returns_from_prices([100, 110, 99])[1] + 0.1) < 1e-9
