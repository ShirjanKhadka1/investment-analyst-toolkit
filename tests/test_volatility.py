"""Tests for volatility forecasting (PR 7.2, Round 7).

Offline only: every series here is synthetic or hand-built. No
network, no Yahoo, no real prices. The real-data evaluation lives in
docs/volatility-forecast-model-card.md and is reproduced by
scripts/evaluate_volatility_forecast.py, never by this file.

What this file proves, and how:

- Unit: log returns, realised variance, EWMA and GARCH-lite
  recursions (hand-computed values), QLIKE (exact values, asymmetry),
  validation errors.
- Walk-forward: expanding origins, no lookahead (perturbing future
  returns cannot change earlier folds), fold counts.
- Decision rule: the protocol's verdict ("beats baseline" vs
  "no edge") is tested on HAND-BUILT fold dicts, where the forecasts
  are controlled exactly — this is the deterministic test of the rule,
  both branches, plus CI determinism and pooling.
- Fixture outcome: the documented real-data outcome (both models beat
  the naive baseline out of sample) is reproduced on a synthetic
  fixture — six pooled constant-volatility series. The fixture's
  ground truth is constant volatility, so the win there is the
  ESTIMATION-EFFICIENCY edge (EWMA/GARCH-lite average more
  observations than the 20-day window, and QLIKE penalises forecast
  noise), not volatility timing. That is stated here and in the model
  card on purpose: the fixture asserts the scoring machinery and the
  verdict pattern, not timing skill.
"""
import math

import pytest

from toolkit import volatility as vol

# ---------------------------------------------------------------------------
# Unit: returns and realised variance
# ---------------------------------------------------------------------------


def test_log_returns_hand_computed():
    # ln(110/100), ln(99/110)
    out = vol.log_returns([100.0, 110.0, 99.0])
    assert out == pytest.approx([math.log(1.1), math.log(99.0 / 110.0)])


def test_log_returns_rejects_non_positive_price():
    with pytest.raises(ValueError):
        vol.log_returns([100.0, 0.0, 101.0])
    with pytest.raises(ValueError):
        vol.log_returns([100.0, -5.0])


def test_log_returns_needs_two_prices():
    with pytest.raises(ValueError):
        vol.log_returns([100.0])


def test_realised_variance_hand_computed():
    # mean of squares of [0.01, -0.02, 0.01, 0.0] over window 4
    assert vol.realised_variance([0.01, -0.02, 0.01, 0.0], window=4) == \
        pytest.approx(0.00015)
    # trailing window: only the last 2 count
    assert vol.realised_variance([0.5, 0.01, -0.02], window=2) == \
        pytest.approx((0.0001 + 0.0004) / 2)


def test_realised_variance_needs_full_window():
    with pytest.raises(ValueError):
        vol.realised_variance([0.01, 0.02], window=20)


# ---------------------------------------------------------------------------
# Unit: EWMA and GARCH-lite recursions
# ---------------------------------------------------------------------------


def test_ewma_hand_computed():
    # 20 returns alternating +0.01/-0.01: sample variance = 0.002/19.
    # One more return 0.02: var = 0.94 * (0.002/19) + 0.06 * 0.0004.
    returns = [0.01 if i % 2 == 0 else -0.01 for i in range(20)] + [0.02]
    expected = 0.94 * (0.002 / 19) + 0.06 * 0.0004
    assert vol.ewma_forecast_variance(returns) == pytest.approx(expected)


def test_ewma_adapts_after_a_large_return():
    calm = [0.01 if i % 2 == 0 else -0.01 for i in range(40)]
    shocked = calm + [0.10]
    assert vol.ewma_forecast_variance(shocked) > \
        vol.ewma_forecast_variance(calm)


def test_ewma_rejects_bad_lambda():
    with pytest.raises(ValueError):
        vol.ewma_forecast_variance([0.01] * 25, lam=1.0)
    with pytest.raises(ValueError):
        vol.ewma_forecast_variance([0.01] * 25, lam=0.0)


def test_garch_lite_degenerate_case_is_the_sample_variance():
    # alpha = beta = 0: omega = long-run sample variance, and the
    # recursion returns omega at every step — so the forecast is
    # exactly the sample variance of the returns given.
    returns = [0.01, -0.02, 0.015, -0.005, 0.02, -0.01] * 5
    mean = sum(returns) / len(returns)
    expected = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    assert vol.garch_lite_forecast_variance(
        returns, alpha=0.0, beta=0.0) == pytest.approx(expected)


def test_garch_lite_rejects_nonstationary_params():
    with pytest.raises(ValueError):
        vol.garch_lite_forecast_variance([0.01] * 25, alpha=0.5, beta=0.5)
    with pytest.raises(ValueError):
        vol.garch_lite_forecast_variance([0.01] * 25, alpha=-0.1, beta=0.5)


def test_garch_lite_is_not_a_fitted_garch():
    # Same returns, different fixed alpha/beta give different
    # forecasts — the parameters are inputs, not estimates. And the
    # forecast is positive and finite on a real-shaped series.
    returns = [0.01, -0.015, 0.02, -0.01, 0.005] * 10
    a = vol.garch_lite_forecast_variance(returns, alpha=0.08, beta=0.90)
    b = vol.garch_lite_forecast_variance(returns, alpha=0.20, beta=0.70)
    assert a > 0 and b > 0 and a != b


def test_forecast_variance_dispatch_and_unknown_model():
    returns = [0.01, -0.02, 0.015, -0.005] * 10
    for model in vol.MODELS:
        assert vol.forecast_variance(model, returns) > 0
    with pytest.raises(ValueError):
        vol.forecast_variance("garch", returns)


def test_all_zero_returns_hit_the_variance_floor():
    # A flat series has zero variance; the floor keeps QLIKE defined.
    assert vol.ewma_forecast_variance([0.0] * 25) == vol.VARIANCE_FLOOR
    assert vol.naive_forecast_variance([0.0] * 25) == vol.VARIANCE_FLOOR


# ---------------------------------------------------------------------------
# Unit: scoring
# ---------------------------------------------------------------------------


def test_qlike_exact_and_asymmetric():
    # Perfect forecast: log(0.0004) + 1.
    assert vol.qlike(0.0004, 0.0004) == pytest.approx(math.log(0.0004) + 1)
    # Under-forecasting is penalised more than over-forecasting by
    # the same factor: qlike(r, r/4) > qlike(r, 4r).
    assert vol.qlike(0.0004, 0.0001) > vol.qlike(0.0004, 0.0016)


def test_qlike_rejects_bad_inputs():
    with pytest.raises(ValueError):
        vol.qlike(0.0004, 0.0)
    with pytest.raises(ValueError):
        vol.qlike(-0.0001, 0.0004)


def test_mse_variance_is_zero_for_a_perfect_forecast():
    assert vol.mse_variance(0.0004, 0.0004) == 0.0
    assert vol.mse_variance(0.0004, 0.0005) == pytest.approx(1e-08)


def test_annualised_volatility_from_variance():
    assert vol.annualised_volatility_from_variance(0.0004) == \
        pytest.approx(math.sqrt(0.0004 * 252))


# ---------------------------------------------------------------------------
# Walk-forward: structure and no lookahead
# ---------------------------------------------------------------------------


def _synthetic_returns(n=120, seed=1):
    import random
    rng = random.Random(seed)
    return [rng.gauss(0, 0.015) for _ in range(n)]


def test_walk_forward_fold_count_and_models():
    wf = vol.walk_forward_volatility(_synthetic_returns(120), train_size=50)
    assert wf["n_test"] == 70
    assert len(wf["folds"]) == 70
    assert wf["models"] == list(vol.MODELS)
    assert wf["folds"][0]["origin"] == 50
    assert wf["folds"][-1]["origin"] == 119


def test_walk_forward_needs_a_nonempty_test_set():
    with pytest.raises(ValueError):
        vol.walk_forward_volatility(_synthetic_returns(50), train_size=50)


def test_walk_forward_has_no_lookahead():
    # Perturbing returns AFTER origin 60 must not change any fold
    # with origin <= 60: those forecasts saw only returns[:origin].
    base = _synthetic_returns(120, seed=3)
    perturbed = base[:80] + [0.5] * 40
    wf_base = vol.walk_forward_volatility(base, train_size=50)
    wf_pert = vol.walk_forward_volatility(perturbed, train_size=50)
    for fold_base, fold_pert in zip(wf_base["folds"], wf_pert["folds"]):
        if fold_base["origin"] <= 60:
            assert fold_base["forecasts"] == fold_pert["forecasts"]
            assert fold_base["realised_variance"] == \
                fold_pert["realised_variance"]
        else:
            break


def test_score_volatility_forecasts_counts_folds():
    wf = vol.walk_forward_volatility(_synthetic_returns(120), train_size=50)
    scores = vol.score_volatility_forecasts(wf)
    assert set(scores) == set(vol.MODELS)
    for score in scores.values():
        assert score["n"] == 70
        assert math.isfinite(score["mean_qlike"])
        assert score["mean_mse_variance"] >= 0


# ---------------------------------------------------------------------------
# Decision rule on hand-built folds (deterministic, both branches)
# ---------------------------------------------------------------------------


def _folds_from_forecasts(pairs):
    """Build a walk-forward result dict from (realised, forecasts)
    pairs. forecasts maps model -> variance forecast."""
    folds = [{"origin": i, "realised_variance": realised,
              "forecasts": dict(forecasts)}
             for i, (realised, forecasts) in enumerate(pairs)]
    return {"folds": folds, "n_test": len(folds), "train_size": 0,
            "models": list(vol.MODELS)}


def test_decision_rule_beats_baseline_when_clearly_better():
    # EWMA forecasts the realised variance exactly; naive is 4x too
    # high every fold. Mean QLIKE difference is large and negative,
    # and every resampled mean is the same — CI entirely below zero.
    pairs = [(0.0004, {"naive": 0.0016, "ewma": 0.0004,
                       "garch_lite": 0.0016}) for _ in range(50)]
    out = vol.compare_volatility_models(_folds_from_forecasts(pairs),
                                        resamples=200)
    assert out["models"]["ewma"]["verdict"] == "beats baseline"
    assert out["models"]["ewma"]["mean_qlike_diff_vs_baseline"] < 0
    assert out["models"]["ewma"]["ci_95"][1] < 0
    # GARCH-lite equals the baseline here: no edge.
    assert out["models"]["garch_lite"]["verdict"] == "no edge"
    assert out["models"]["garch_lite"]["mean_qlike_diff_vs_baseline"] == \
        pytest.approx(0.0)


def test_decision_rule_no_edge_when_identical_to_baseline():
    pairs = [(0.0004, {"naive": 0.0004, "ewma": 0.0004,
                       "garch_lite": 0.0004}) for _ in range(50)]
    out = vol.compare_volatility_models(_folds_from_forecasts(pairs),
                                        resamples=200)
    for model in ("ewma", "garch_lite"):
        assert out["models"][model]["verdict"] == "no edge"


def test_decision_rule_no_edge_when_worse_than_baseline():
    pairs = [(0.0004, {"naive": 0.0004, "ewma": 0.0016,
                       "garch_lite": 0.0016}) for _ in range(50)]
    out = vol.compare_volatility_models(_folds_from_forecasts(pairs),
                                        resamples=200)
    for model in ("ewma", "garch_lite"):
        assert out["models"][model]["verdict"] == "no edge"
        assert out["models"][model]["mean_qlike_diff_vs_baseline"] > 0


def test_decision_rule_requires_the_whole_ci_below_zero():
    # EWMA is better on average but the per-fold differences straddle
    # zero widely: mean negative, CI crossing zero -> "no edge".
    # (Half the folds EWMA is exact and naive 4x high; half the
    # reverse.) A mean-only rule would call this a win; the CI rule
    # must not.
    good = (0.0004, {"naive": 0.0016, "ewma": 0.0004,
                     "garch_lite": 0.0016})
    bad = (0.0004, {"naive": 0.0004, "ewma": 0.0016,
                    "garch_lite": 0.0016})
    pairs = [good] * 30 + [bad] * 20
    out = vol.compare_volatility_models(_folds_from_forecasts(pairs),
                                        resamples=200)
    assert out["models"]["ewma"]["mean_qlike_diff_vs_baseline"] < 0
    assert out["models"]["ewma"]["ci_95"][0] < 0 < \
        out["models"]["ewma"]["ci_95"][1]
    assert out["models"]["ewma"]["verdict"] == "no edge"


def test_bootstrap_ci_is_deterministic():
    pairs = [(0.0004, {"naive": 0.0016, "ewma": 0.0004,
                       "garch_lite": 0.0016}) for _ in range(50)]
    wf = _folds_from_forecasts(pairs)
    first = vol.compare_volatility_models(wf, resamples=200)
    second = vol.compare_volatility_models(wf, resamples=200)
    assert first == second


def test_compare_pools_across_symbols():
    # Two identical hand-built results pooled: n_folds doubles, the
    # verdict is unchanged.
    pairs = [(0.0004, {"naive": 0.0016, "ewma": 0.0004,
                       "garch_lite": 0.0016}) for _ in range(50)]
    wf = _folds_from_forecasts(pairs)
    out = vol.compare_volatility_models([wf, wf], resamples=200)
    assert out["n_folds"] == 100
    assert out["n_symbols"] == 2
    assert out["models"]["ewma"]["verdict"] == "beats baseline"


# ---------------------------------------------------------------------------
# Fixture outcome: documented real-data verdict reproduced on synthetic
# ---------------------------------------------------------------------------


def test_both_models_beat_baseline_on_pooled_constant_vol_fixture():
    """Six pooled constant-volatility series (sigma 0.015, seeds
    101-106, 400 returns each, train 100): both models beat the naive
    trailing-20-day baseline on pooled mean QLIKE with the 95% CI
    entirely below zero — the same verdict as the real-data model
    card. Ground truth here is constant volatility, so this is the
    estimation-efficiency edge (a longer effective memory is a less
    noisy variance estimate, and QLIKE penalises forecast noise),
    not volatility timing. The test pins the scoring machinery and
    the verdict; it does not claim timing skill.
    """
    import random
    results = []
    for seed in (101, 102, 103, 104, 105, 106):
        rng = random.Random(seed)
        returns = [rng.gauss(0, 0.015) for _ in range(400)]
        results.append(vol.walk_forward_volatility(returns, train_size=100))
    out = vol.compare_volatility_models(results, resamples=200)
    assert out["n_folds"] == 1800
    assert out["n_symbols"] == 6
    for model in ("ewma", "garch_lite"):
        assert out["models"][model]["verdict"] == "beats baseline"
        assert out["models"][model]["mean_qlike_diff_vs_baseline"] < 0
        assert out["models"][model]["ci_95"][1] < 0


def test_forecasts_adapt_after_a_volatility_shift():
    """Behavioural check on a single upward shift (sigma 0.01 -> 0.03
    at return 200): the EWMA forecast 30 returns after the shift is
    higher than before it, and higher than the naive forecast was
    just before the shift. No verdict asserted — the shift fixture
    is a behaviour check, not a verdict fixture."""
    import random
    rng = random.Random(7)
    returns = ([rng.gauss(0, 0.01) for _ in range(200)]
               + [rng.gauss(0, 0.03) for _ in range(200)])
    wf = vol.walk_forward_volatility(returns, train_size=100)
    before = wf["folds"][90]["forecasts"]["ewma"]  # origin 190, pre-shift
    after = wf["folds"][130]["forecasts"]["ewma"]  # origin 230, post-shift
    assert after > before
    assert after > wf["folds"][99]["forecasts"]["naive"]
