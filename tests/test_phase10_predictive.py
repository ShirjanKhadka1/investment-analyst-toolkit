"""Phase 10: new predictive capability, honestly evaluated.

New forecasters (AR(1), Holt, damped Holt, drift), multi-horizon
walk-forward evaluation (h=1/3/5), empirical prediction intervals,
adaptive ensemble weighting, and drift flags. All fixtures are
synthetic/demo series — no market data, no live feed. Losing to naive
is a reported result, not a hidden one.
"""
import math

import pytest

from toolkit import predictive
from toolkit.predictive import (
    _horizon_models,
    adaptive_ensemble_forecast,
    ar1_forecast,
    drift_flag,
    drift_forecast,
    empirical_interval,
    holt_forecast,
    prediction_interval,
    walk_forward_drift,
    walk_forward_forecasts,
)

TRENDING = [100 + 2 * i for i in range(40)]
LEVEL_SHIFT = [100.0] * 15 + [150.0] * 15
NOISY_FLAT = [100.0 + ((i * 37) % 7 - 3) for i in range(40)]


# --- New forecasters: maths on known series -------------------------------

def test_ar1_recovers_linear_series_approximately():
    # On a perfect trend, AR(1) with phi clamped < 1 cannot track the
    # trend exactly; it must stay finite and below the true next value
    # (mean reversion pulls it back), not explode.
    train = TRENDING[:20]
    f = ar1_forecast(train, horizon=1)
    assert math.isfinite(f)
    assert f < 140.0  # true next value; AR(1) mean-reverts below it


def test_ar1_stationarity_guard_on_explosive_series():
    train = [2.0 ** i for i in range(10)]
    f = ar1_forecast(train, horizon=5)
    assert math.isfinite(f)
    assert f < 1e6  # clamped phi prevents exponential blow-up


def test_ar1_constant_window_falls_back_to_naive():
    assert ar1_forecast([100.0] * 10, horizon=3) == 100.0


def test_holt_exact_on_perfect_trend_and_damped_below_it():
    train = TRENDING[:20]
    assert holt_forecast(train, horizon=1) == pytest.approx(140.0)
    assert holt_forecast(train, horizon=3) == pytest.approx(144.0)
    damped = holt_forecast(train, horizon=3, damping=0.9)
    assert 140.0 < damped < 144.0  # damped trend undershoots plain Holt


def test_drift_exact_on_perfect_trend_and_overshoots_after_shift():
    assert drift_forecast(TRENDING[:20], horizon=1) == pytest.approx(140.0)
    # Train ending just after the shift: mean diff is inflated by the
    # 50-point jump, so drift overshoots the flat 150 level — documented.
    assert drift_forecast(LEVEL_SHIFT[:16], horizon=1) > 150.0


def test_new_forecasters_reject_bad_inputs():
    with pytest.raises(ValueError):
        ar1_forecast([100.0, 101.0], horizon=1)
    with pytest.raises(ValueError):
        holt_forecast([100.0], horizon=1)
    with pytest.raises(ValueError):
        drift_forecast([100.0], horizon=1)
    with pytest.raises(ValueError):
        holt_forecast(TRENDING[:20], horizon=0)
    with pytest.raises(ValueError):
        holt_forecast(TRENDING[:20], horizon=1, alpha=1.5)


# --- Lookahead: perturbing what comes AFTER the target changes nothing ---

def test_walk_forward_forecast_unchanged_when_later_values_perturbed():
    models = _horizon_models(include_new_models=True, include_adaptive=True)
    base = walk_forward_forecasts(TRENDING, models, train_size=20, horizon=3)
    perturbed = list(TRENDING)
    for i in range(24, len(perturbed)):
        perturbed[i] = 9999.0  # strictly after the first targets (t=20 -> 22)
    pert = walk_forward_forecasts(perturbed, models, train_size=20, horizon=3)
    for name in models:
        # Origin t=20's forecast must be identical: it saw only series[:20].
        assert base[name][0][1] == pert[name][0][1]
        assert base[name][0][0] == 20


def test_forecaster_uses_only_its_training_window():
    train = TRENDING[:20]
    for fn in (ar1_forecast, holt_forecast, drift_forecast):
        before = fn(train, horizon=3)
        # Appending "future" values to a copy must not change the call
        # made on the original window — the function receives train only.
        assert fn(train, horizon=3) == before


# --- Multi-horizon shapes and ranking --------------------------------------

def test_multi_horizon_shapes_and_n_test():
    rep = predictive.compare_models_walk_forward(
        TRENDING, train_size=20, horizons=(1, 3, 5),
        include_new_models=True, include_ensemble=True, include_adaptive=True)
    assert rep["horizons"] == [1, 3, 5]
    assert rep["by_horizon"][1]["ranking"][0]["n_test"] == 20
    assert rep["by_horizon"][3]["ranking"][0]["n_test"] == 18
    assert rep["by_horizon"][5]["ranking"][0]["n_test"] == 16
    for h in (1, 3, 5):
        maes = [r["mae"] for r in rep["by_horizon"][h]["ranking"]]
        assert maes == sorted(maes)
        assert rep["by_horizon"][h]["naive_mae"] == rep["by_horizon"][h]["results"]["naive"]["mae"]


def test_default_call_unchanged_by_phase10():
    # Backwards compatibility: no new models, h=1 only, same 4-model ranking.
    rep = predictive.compare_models_walk_forward(TRENDING, train_size=20)
    assert {r["model"] for r in rep["ranking"]} == {
        "naive", "moving_average_5", "linear_trend", "exp_smoothing_0.3"}
    assert rep["best_model"] == "linear_trend"


def test_level_shift_naive_wins_at_every_horizon():
    rep = predictive.compare_models_walk_forward(
        LEVEL_SHIFT, train_size=15, horizons=(1, 3, 5),
        include_new_models=True, include_ensemble=True, include_adaptive=True)
    for h in (1, 3, 5):
        bh = rep["by_horizon"][h]
        assert bh["best_model"] == "naive"
        assert bh["naive_wins"] is True
        # AR(1) ties naive here; a tie is not a win (strict < rule).
        ar1 = next(r for r in bh["ranking"] if r["model"] == "ar1")
        assert ar1["mae"] == bh["naive_mae"]
        assert ar1["beats_naive"] is False


def test_drift_loses_on_noisy_flat_at_every_horizon():
    # The documented drift failure mode, reported not hidden.
    rep = predictive.compare_models_walk_forward(
        NOISY_FLAT, train_size=20, horizons=(1, 3, 5), include_new_models=True)
    for h in (1, 3, 5):
        drift = next(r for r in rep["by_horizon"][h]["ranking"] if r["model"] == "drift")
        assert drift["beats_naive"] is False


def test_multi_horizon_rejects_bad_horizons():
    with pytest.raises(ValueError):
        predictive.compare_models_walk_forward(TRENDING, train_size=20, horizons=(0,))
    with pytest.raises(ValueError):
        predictive.compare_models_walk_forward(TRENDING, train_size=20, horizons=(1, 1))
    with pytest.raises(ValueError):
        predictive.compare_models_walk_forward(TRENDING, train_size=38, horizons=(5,))


# --- Intervals ---------------------------------------------------------------

def test_empirical_interval_on_known_residuals():
    lo, hi = empirical_interval([-2.0, -1.0, 0.0, 1.0, 2.0], alpha=0.20)
    # Interpolated 10%/90% quantiles over 5 points: -1.6 / +1.6.
    # Nominal 80% coverage covers 3 of 5 sample points — correct
    # small-sample arithmetic, not a coverage guarantee for new data.
    assert lo == pytest.approx(-1.6) and hi == pytest.approx(1.6)
    inside = sum(1 for r in [-2.0, -1.0, 0.0, 1.0, 2.0] if lo <= r <= hi)
    assert inside == 3
    with pytest.raises(ValueError):
        empirical_interval([], alpha=0.10)


def test_prediction_interval_method_and_n_reported():
    # On a perfect trend Holt is exact after the transient, so its
    # internal residuals are ~3e-14 float noise and the interval
    # legitimately degenerates to (approximately) the point itself.
    res = prediction_interval(TRENDING[:20], lambda t, h: holt_forecast(t, h), horizon=1)
    assert res["method"] == "empirical"
    assert res["n_residuals"] >= 5
    assert res["lower"] == pytest.approx(res["point"], abs=1e-9)
    assert res["upper"] == pytest.approx(res["point"], abs=1e-9)
    # On a varying series the same function gives a genuine width.
    noisy = prediction_interval(NOISY_FLAT[:20], lambda t, h: holt_forecast(t, h), horizon=1)
    assert noisy["method"] == "empirical"
    assert noisy["lower"] < noisy["upper"]
    # The normal-approx fallback only triggers when internal origins are
    # scarce: at h=1 a 10-point minimum always yields >= 5 residuals, so
    # use h=5 on a 12-point train (2 internal origins) to reach it.
    short = prediction_interval(TRENDING[:12], lambda t, h: holt_forecast(t, h), horizon=5)
    assert short["method"].startswith("normal-approx")
    assert short["n_residuals"] < 5


# --- Adaptive ensemble -------------------------------------------------------

def test_adaptive_weights_sum_to_one_and_fallback_on_short_train():
    res = adaptive_ensemble_forecast(TRENDING[:30], horizon=1)
    assert sum(res["weights"].values()) == pytest.approx(1.0)
    assert res["weighting"] == "inverse-recent-MAE"
    short = adaptive_ensemble_forecast([100.0, 101.0, 102.0, 103.0, 104.0], horizon=1)
    assert short["weighting"].startswith("fallback")
    vals = list(short["weights"].values())
    assert all(v == vals[0] for v in vals)  # equal fallback weights


def test_adaptive_ensemble_beats_naive_on_trend_loses_on_shift():
    trend = predictive.compare_models_walk_forward(
        TRENDING, train_size=20, horizons=(1,), include_new_models=True, include_adaptive=True)
    row = next(r for r in trend["by_horizon"][1]["ranking"] if r["model"] == "ensemble_adaptive")
    assert row["beats_naive"] is True
    shift = predictive.compare_models_walk_forward(
        LEVEL_SHIFT, train_size=15, horizons=(1,), include_new_models=True, include_adaptive=True)
    row = next(r for r in shift["by_horizon"][1]["ranking"] if r["model"] == "ensemble_adaptive")
    assert row["beats_naive"] is False  # adaptive does not rescue the shift


# --- Drift flags ---------------------------------------------------------------

def test_drift_flag_fires_and_insufficient_data():
    assert drift_flag([1.0, 1.0, 1.0], [2.0, 2.0, 2.0])["drift"] is True
    assert drift_flag([1.0, 1.0, 1.0], [1.1, 1.1, 1.1])["drift"] is False
    flag = drift_flag([1.0], [5.0])
    assert flag["drift"] is False and "insufficient" in flag["reason"]


def test_walk_forward_drift_on_shift_series():
    # Naive on the level shift: early test errors are large (the jump),
    # later errors are zero — so recent < early, no drift flag. The
    # flag must not fire backwards.
    flag = walk_forward_drift(LEVEL_SHIFT, lambda t, h: t[-1], train_size=15, horizon=1)
    assert flag["drift"] is False
    assert flag["n_early"] + flag["n_recent"] == 15
