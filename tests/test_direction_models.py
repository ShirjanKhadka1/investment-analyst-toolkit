"""Tests for probabilistic direction models (PR 8.2).

Offline only: synthetic fixture series, never the network. The
live-data evaluation is scripts/evaluate_direction_models.py;
these tests pin the same scoring, calibration and decision-rule
code on fixtures, including the no-lookahead property (perturbing
closes after an origin cannot change that origin's forecast) and
both branches of the decision rule ("edge" and "no edge").
"""
import math

import pytest

from toolkit import direction_models as dm


def _rising_closes(n=130, start=100.0, step=0.4, wiggle=0.15):
    """Mostly-rising closes with a small alternating wiggle."""
    closes = [start]
    for i in range(1, n):
        closes.append(closes[-1] + step + (wiggle if i % 2 else -wiggle))
    return closes


def _random_walk_closes(n=130, seed=7, start=100.0):
    import random
    rng = random.Random(seed)
    closes = [start]
    for _ in range(1, n):
        closes.append(closes[-1] * (1.0 + rng.gauss(0.0, 0.01)))
    return closes


# --- Features -----------------------------------------------------------

def test_features_warmup_none_before_50():
    closes = _rising_closes(60)
    feats = dm.direction_features(closes)
    assert feats[:50] == [None] * 50
    assert all(f is not None for f in feats[50:])


def test_features_values_on_flat_growth():
    # Constant +1% daily growth: mom5/mom20/ret1 exact, gaps > 0.
    closes = [100.0 * 1.01 ** i for i in range(60)]
    feats = dm.direction_features(closes)
    f = feats[59]
    assert f["ret1"] == pytest.approx(0.01)
    assert f["mom5"] == pytest.approx(1.01 ** 5 - 1)
    assert f["mom20"] == pytest.approx(1.01 ** 20 - 1)
    assert f["sma20_gap"] > 0 and f["sma50_gap"] > 0
    assert f["vol20"] == pytest.approx(0.0, abs=1e-12)
    assert f["rsi14"] == pytest.approx(1.0)  # all gains -> RSI 100


def test_features_reject_non_positive():
    with pytest.raises(ValueError):
        dm.direction_features([100.0] * 10 + [0.0])


def test_features_no_lookahead():
    closes = _random_walk_closes(120)
    before = dm.direction_features(closes)
    changed = closes[:]
    changed[100] = changed[100] * 1.5  # perturb a late close
    after = dm.direction_features(changed)
    assert before[:90] == after[:90]


def test_feature_rows_labels_and_flat_tie():
    closes = [100.0, 101.0, 101.0, 102.0] + [102.0 + i for i in range(60)]
    X, y, indices = dm.feature_rows(closes)
    # Row at index 50: next close 102+1 > 102 -> 1; find the flat pair.
    assert len(X) == len(y) == len(indices)
    assert all(len(row) == 7 for row in X)
    assert set(y) <= {0, 1}
    # The flat close at index 1 -> row index 1 is warm-up (None), so
    # build the tie case directly: index 51 has next close equal.
    closes2 = [100.0 + i * 0.5 for i in range(60)]
    closes2[52] = closes2[51]  # flat next day for row 51
    _X2, y2, idx2 = dm.feature_rows(closes2)
    assert y2[idx2.index(51)] == 0


# --- Logistic -----------------------------------------------------------

def test_logistic_proba_in_range_and_deterministic():
    X, y, _ = dm.feature_rows(_random_walk_closes(120))
    params = dm.fit_logistic(X[:60], y[:60])
    again = dm.fit_logistic(X[:60], y[:60])
    assert params == again
    for row in X[60:65]:
        p = dm.logistic_proba(params, row)
        assert 0.0 <= p <= 1.0


def test_logistic_separates_trivial_signal():
    # Rows where mom20 > 0 are always up, mom20 < 0 always down.
    X = [[0.0, v, 0.0, 0.0, 0.0, 0.01, 0.0] for v in
         [0.05] * 20 + [-0.05] * 20]
    y = [1] * 20 + [0] * 20
    params = dm.fit_logistic(X, y)
    assert dm.logistic_proba(params, X[0]) > 0.9
    assert dm.logistic_proba(params, X[-1]) < 0.1


def test_logistic_rejects_empty_and_mismatched():
    with pytest.raises(ValueError):
        dm.fit_logistic([], [])
    with pytest.raises(ValueError):
        dm.fit_logistic([[0.0] * 7], [1, 0])


# --- Naive Bayes / score ------------------------------------------------

def test_gaussian_nb_proba_in_range():
    X, y, _ = dm.feature_rows(_random_walk_closes(120))
    params = dm.fit_gaussian_nb(X[:60], y[:60])
    for row in X[60:65]:
        p = dm.gaussian_nb_proba(params, row)
        assert 0.0 <= p <= 1.0


def test_gaussian_nb_single_class_falls_back_to_base_rate():
    X = [[0.0] * 7 for _ in range(10)]
    params = dm.fit_gaussian_nb(X, [1] * 10)
    assert dm.gaussian_nb_proba(params, X[0]) == pytest.approx(1.0)


def test_score_proba_fixed_weights():
    # Standardised row of all ones: score = sum(weights) = 1.5.
    means = [0.0] * 7
    stds = [1.0] * 7
    row = [1.0] * 7
    expected = 1.0 / (1.0 + math.exp(-sum(dm.SCORE_WEIGHTS)))
    assert dm.score_proba(row, means, stds) == pytest.approx(expected)
    # A zero row scores 0 -> probability 0.5.
    assert dm.score_proba([0.0] * 7, means, stds) == pytest.approx(0.5)


def test_score_weights_are_the_protocol_weights():
    assert dm.SCORE_WEIGHTS == (0.3, 0.5, 0.4, 0.4, 0.2, -0.2, 0.1)


# --- Regime detection ---------------------------------------------------

def test_regime_label_high_on_volatility_spike():
    calm = [100.0 * 1.001 ** i for i in range(60)]
    assert dm.regime_label(calm) == "low"
    spiky = calm[:]
    for i in range(50, 60):
        spiky[i] = spiky[i - 1] * (1.05 if i % 2 else 0.95)
    assert dm.regime_label(spiky) == "high"


def test_cusum_alarms_on_level_shift():
    import random
    rng = random.Random(3)
    rets = [rng.gauss(0.0, 0.01) for _ in range(100)]
    rets += [rng.gauss(0.05, 0.01) for _ in range(100)]
    result = dm.cusum_alarms(rets)
    assert result["n_alarms"] >= 1
    assert result["alarm_indices"][-1] >= 100


def test_cusum_raises_on_constant_returns():
    with pytest.raises(ValueError):
        dm.cusum_alarms([0.01] * 50)


def test_hmm_lite_transitions_sum_and_labels():
    closes = _random_walk_closes(120)
    result = dm.hmm_lite_transitions(closes)
    total = sum(result["transitions"].values())
    assert total == len(result["states"]) - 1
    assert result["n_volatile"] + result["n_calm"] == len(result["states"])
    assert set(result["states"]) <= {"calm", "volatile"}


# --- Scoring ------------------------------------------------------------

def test_brier_and_log_score_known_answers():
    from toolkit.predictive import brier_score
    assert brier_score([0.5, 0.5], [0, 1]) == pytest.approx(0.25)
    assert brier_score([1.0, 0.0], [1, 0]) == pytest.approx(0.0)
    assert dm.log_score([0.5, 0.5], [0, 1]) == pytest.approx(math.log(2))
    assert dm.log_score([1.0, 0.0], [1, 0]) == pytest.approx(0.0, abs=1e-5)


def test_log_score_rejects_bad_inputs():
    with pytest.raises(ValueError):
        dm.log_score([0.5], [0, 1])
    with pytest.raises(ValueError):
        dm.log_score([1.5], [1])


def test_reliability_bins_counts_sum_and_empty_bins_reported():
    probs = [0.05, 0.15, 0.85, 0.95]
    outs = [0, 0, 1, 1]
    bins = dm.reliability_bins(probs, outs, n_bins=10)
    assert len(bins) == 10
    assert sum(b["count"] for b in bins) == 4
    assert bins[0]["count"] == 1
    assert bins[0]["observed_up_rate"] == pytest.approx(0.0)
    assert bins[5]["count"] == 0
    assert bins[5]["mean_probability"] is None
    assert bins[9]["count"] == 1
    assert bins[9]["observed_up_rate"] == pytest.approx(1.0)


def test_reliability_bins_probability_one_lands_in_last_bin():
    bins = dm.reliability_bins([1.0], [1], n_bins=10)
    assert bins[-1]["count"] == 1


# --- Walk-forward --------------------------------------------------------

def test_walk_forward_direction_structure():
    closes = _random_walk_closes(130)
    wf = dm.walk_forward_direction(closes, "SPY", train_size=60)
    assert wf["symbol"] == "SPY"
    assert wf["n_test"] == len(wf["folds"]) > 0
    for fold in wf["folds"]:
        assert set(fold["probs"]) == set(dm.EVALUATED_MODELS)
        assert all(0.0 <= p <= 1.0 for p in fold["probs"].values())
        assert fold["baselines"]["coin_flip"] == 0.5
        assert 0.0 <= fold["baselines"]["base_rate"] <= 1.0
        assert fold["baselines"]["direction_rule"] in (0.25, 0.75) or \
            fold["baselines"]["direction_rule"] == fold["baselines"]["base_rate"]
        assert fold["outcome"] in (0, 1)
        assert fold["regime"] in ("high", "low")
        assert fold["cusum_alarms"] >= 0


def test_walk_forward_direction_rule_baseline_up_on_rising_series():
    closes = _rising_closes(130)
    wf = dm.walk_forward_direction(closes, "SPY", train_size=60)
    # Steadily rising closes: sma50_trend is UP at every origin.
    assert all(f["baselines"]["direction_rule"] == 0.75
               for f in wf["folds"])


def test_walk_forward_ensemble_equal_is_mean_of_base():
    closes = _random_walk_closes(130)
    wf = dm.walk_forward_direction(closes, "AAPL", train_size=60)
    for fold in wf["folds"]:
        expected = sum(fold["probs"][m] for m in dm.BASE_MODELS) / 3
        assert fold["probs"]["ensemble_equal"] == pytest.approx(expected)


def test_walk_forward_calibration_flags_flip_after_30_folds():
    closes = _random_walk_closes(130)
    wf = dm.walk_forward_direction(closes, "VT", train_size=60)
    flags = [f["calibrated"]["logistic_gd"] for f in wf["folds"]]
    assert flags[0] is False
    assert flags[-1] is True
    # The flip happens exactly once the 30-fold threshold is reached.
    assert flags.count(False) == 30


def test_walk_forward_no_lookahead():
    closes = _random_walk_closes(130)
    wf = dm.walk_forward_direction(closes, "SPY", train_size=60)
    changed = closes[:]
    changed[120] = changed[120] * 1.5  # perturb a late close
    wf2 = dm.walk_forward_direction(changed, "SPY", train_size=60)
    # Folds whose outcome is decided before index 120 are identical.
    for f1, f2 in zip(wf["folds"], wf2["folds"]):
        if f1["origin"] < 119:
            assert f1["probs"] == f2["probs"]
            assert f1["baselines"] == f2["baselines"]


def test_walk_forward_rejects_unknown_symbol_and_short_series():
    with pytest.raises(ValueError):
        dm.walk_forward_direction(_random_walk_closes(130), "BTC-USD",
                                  train_size=60)
    with pytest.raises(ValueError):
        dm.walk_forward_direction([100.0] * 40, "SPY", train_size=60)


# --- Decision rule ------------------------------------------------------

def _fabricated_results(model_probs, outcomes, n_symbols=1):
    """Walk-forward-shaped results with caller-chosen probabilities."""
    results = []
    for _ in range(n_symbols):
        folds = []
        for i, (probs, outcome) in enumerate(zip(model_probs, outcomes)):
            folds.append({
                "origin": 60 + i, "outcome": outcome, "regime": "low",
                "cusum_alarms": 0,
                "probs": probs,
                "raw_probs": {m: probs[m] for m in
                              ("logistic_gd", "gaussian_nb", "score_platt",
                               "logistic_regime")},
                "calibrated": {m: True for m in dm.EVALUATED_MODELS},
                "baselines": {"coin_flip": 0.5, "base_rate": 0.5,
                              "direction_rule": 0.5},
            })
        results.append({"symbol": "SPY", "n_closes": 200, "train_size": 60,
                        "n_test": len(folds), "folds": folds})
    return results


def test_compare_edge_branch_perfect_model_beats_all_baselines():
    n = 60
    outcomes = [i % 2 for i in range(n)]
    model_probs = []
    for o in outcomes:
        probs = {m: 0.5 for m in dm.EVALUATED_MODELS}
        probs["logistic_gd"] = 0.99 if o == 1 else 0.01
        probs["logistic_regime"] = probs["logistic_gd"]
        model_probs.append(probs)
    comparison = dm.compare_direction_models(
        _fabricated_results(model_probs, outcomes))
    assert comparison["n_folds"] == n
    assert comparison["models"]["logistic_gd"]["verdict"] == "edge"
    assert comparison["models"]["gaussian_nb"]["verdict"] == "no edge"
    ci = comparison["models"]["logistic_gd"]["per_baseline"]["coin_flip"]["ci_95"]
    assert ci[1] < 0


def test_compare_no_edge_branch_coin_flip_model():
    n = 60
    outcomes = [i % 2 for i in range(n)]
    model_probs = [{m: 0.5 for m in dm.EVALUATED_MODELS}
                   for _ in range(n)]
    comparison = dm.compare_direction_models(
        _fabricated_results(model_probs, outcomes))
    for model in dm.EVALUATED_MODELS:
        assert comparison["models"][model]["verdict"] == "no edge"
        assert comparison["models"][model]["mean_brier"] == pytest.approx(0.25)


def test_compare_regime_head_to_head():
    n = 60
    outcomes = [i % 2 for i in range(n)]
    model_probs = []
    for o in outcomes:
        probs = {m: 0.5 for m in dm.EVALUATED_MODELS}
        probs["logistic_gd"] = 0.5
        probs["logistic_regime"] = 0.99 if o == 1 else 0.01
        model_probs.append(probs)
    comparison = dm.compare_direction_models(
        _fabricated_results(model_probs, outcomes))
    assert comparison["regime_conditioning"]["verdict"] == "improves"
    assert comparison["regime_conditioning"]["ci_95"][1] < 0


def test_compare_rejects_empty_and_unknown_baseline():
    with pytest.raises(ValueError):
        dm.compare_direction_models([])
    with pytest.raises(ValueError):
        dm.compare_direction_models(
            _fabricated_results([{m: 0.5 for m in dm.EVALUATED_MODELS}],
                                [1]),
            baseline="not_a_baseline")


def test_compare_pools_across_symbols():
    n = 40
    outcomes = [i % 2 for i in range(n)]
    model_probs = [{m: 0.5 for m in dm.EVALUATED_MODELS}
                   for _ in range(n)]
    comparison = dm.compare_direction_models(
        _fabricated_results(model_probs, outcomes, n_symbols=3))
    assert comparison["n_folds"] == 120
    assert comparison["n_symbols"] == 3


def test_bootstrap_ci_deterministic():
    diffs = [0.01 * (i % 5 - 2) for i in range(50)]
    assert dm._bootstrap_ci(diffs) == dm._bootstrap_ci(diffs)
    with pytest.raises(ValueError):
        dm._bootstrap_ci([])
    with pytest.raises(ValueError):
        dm._bootstrap_ci(diffs, resamples=50)
