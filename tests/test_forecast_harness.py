"""Tests for the forecast evaluation harness (PR 8.1).

Offline only: every evaluation here runs on synthetic fixture
series, never the network. The live run is
scripts/evaluate_forecast_harness.py.
"""
import math
import random
from datetime import date

import pytest

from toolkit import forecast_harness as fh
from toolkit.predictive import walk_forward_forecasts


def _as_horizon_fn(fn):
    """Wrap a train -> forecast fn as a (train, horizon) fn."""
    def _fn(train, horizon):
        return fn(train)
    return _fn


def _trend_series(n=200, slope=0.5, seed=7, noise=0.05):
    rng = random.Random(seed)
    return [100.0 + slope * i + rng.uniform(-noise, noise)
            for i in range(n)]


def _walk_series(n=200, seed=11):
    rng = random.Random(seed)
    out = [100.0]
    for _ in range(n - 1):
        out.append(out[-1] + rng.gauss(0, 1.0))
    return out


# --- log score ----------------------------------------------------------

def test_log_score_perfect_is_zero():
    assert fh.log_score([1.0, 0.0, 1.0], [1.0, 0.0, 1.0]) == pytest.approx(0.0)


def test_log_score_always_half_is_ln2():
    assert fh.log_score([0.5] * 10, [1.0, 0.0] * 5) == pytest.approx(math.log(2))


def test_log_score_worse_than_half_when_wrong():
    assert fh.log_score([0.9, 0.9], [0.0, 0.0]) > math.log(2)


def test_log_score_validates_inputs():
    with pytest.raises(ValueError):
        fh.log_score([1.2], [1.0])
    with pytest.raises(ValueError):
        fh.log_score([0.5, 0.5], [1.0])
    with pytest.raises(ValueError):
        fh.log_score([0.5], [0.5])


# --- calibration curve ---------------------------------------------------

def test_calibration_curve_counts_sum_to_n():
    probs = [0.05, 0.15, 0.45, 0.55, 0.85, 0.95]
    outs = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0]
    curve = fh.calibration_curve(probs, outs, n_bins=10)
    assert len(curve) == 10
    assert sum(b["n"] for b in curve) == 6
    assert curve[0]["n"] == 1
    assert curve[0]["mean_predicted"] == pytest.approx(0.05)
    assert curve[0]["observed_frequency"] == pytest.approx(0.0)
    assert curve[9]["n"] == 1
    assert curve[9]["observed_frequency"] == pytest.approx(1.0)


def test_calibration_curve_keeps_empty_bins():
    curve = fh.calibration_curve([0.5, 0.5], [1.0, 0.0], n_bins=4)
    assert sum(b["n"] for b in curve) == 2
    assert curve[0]["n"] == 0
    assert curve[0]["mean_predicted"] is None
    assert curve[0]["observed_frequency"] is None


def test_calibration_curve_validates():
    with pytest.raises(ValueError):
        fh.calibration_curve([0.5], [1.0, 0.0])
    with pytest.raises(ValueError):
        fh.calibration_curve([0.5], [1.0], n_bins=0)


# --- paired bootstrap CI -------------------------------------------------

def test_bootstrap_ci_deterministic_for_a_seed():
    diffs = [0.1, -0.2, 0.3, -0.1, 0.05, -0.05, 0.2, -0.15]
    first = fh.paired_bootstrap_ci(diffs, resamples=500, seed=42)
    second = fh.paired_bootstrap_ci(diffs, resamples=500, seed=42)
    assert first == second
    assert first[0] <= sum(diffs) / len(diffs) <= first[1]


def test_bootstrap_ci_constant_differences():
    assert fh.paired_bootstrap_ci([0.25] * 12, resamples=200, seed=1) == (0.25, 0.25)


def test_bootstrap_ci_validates():
    with pytest.raises(ValueError):
        fh.paired_bootstrap_ci([], resamples=200)
    with pytest.raises(ValueError):
        fh.paired_bootstrap_ci([0.1, -0.1], resamples=50)


# --- splits ---------------------------------------------------------------

def test_walk_forward_splits_plain_counts():
    out = fh.walk_forward_splits(30, train_size=20, horizon=1)
    assert out["n_splits"] == 10
    train, test = out["splits"][0]
    assert train == list(range(20))
    assert test == [20]
    train, test = out["splits"][-1]
    assert train == list(range(29))
    assert test == [29]


def test_purged_embargoed_splits_never_overlap_and_keep_gap():
    out = fh.walk_forward_splits(100, train_size=50, horizon=1,
                                 purge=5, embargo=5)
    for train_idx, test_idx in out["splits"]:
        assert not set(train_idx) & set(test_idx)
        assert min(test_idx) - max(train_idx) == 5 + 5 + 1
        assert train_idx == list(range(max(train_idx) + 1))
    assert out["n_splits"] == 100 - 1 - 5 - 50 + 1


def test_walk_forward_splits_validates():
    with pytest.raises(ValueError):
        fh.walk_forward_splits(30, train_size=25, purge=20)
    with pytest.raises(ValueError):
        fh.walk_forward_splits(30, train_size=20, embargo=-1)
    with pytest.raises(ValueError):
        fh.walk_forward_splits(30, train_size=30)


# --- price evaluation -----------------------------------------------------

def test_price_models_registry_has_ten_with_naive_baseline():
    models = fh.price_models()
    assert len(models) == 10
    assert "naive" in models
    assert set(models) == {"naive", "moving_average_5", "linear_trend",
                           "exp_smoothing_0.3", "ar1", "holt",
                           "holt_damped", "drift", "ensemble_equal",
                           "ensemble_adaptive"}


def test_ensemble_equal_is_mean_of_base_members():
    train = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    models = fh.price_models()
    members = [models["naive"](train), models["moving_average_5"](train),
               models["linear_trend"](train),
               models["exp_smoothing_0.3"](train)]
    assert models["ensemble_equal"](train) == pytest.approx(
        sum(members) / len(members))


def test_evaluate_price_models_matches_walk_forward_forecasts_unpurged():
    series = _trend_series(n=80, seed=3)
    result = fh.evaluate_price_models(series, train_size=40, resamples=200,
                                      seed=5)
    triples = walk_forward_forecasts(
        series,
        {name: _as_horizon_fn(fn)
         for name, fn in fh.price_models().items()},
        40, horizon=1)
    for name, rows in triples.items():
        got = [(f["origin"], f["forecast"], f["actual"])
               for f in result["models"][name]["folds"]]
        assert got == rows


def test_purged_forecast_never_sees_the_purged_tail():
    series = _trend_series(n=80, seed=3)
    probe_seen = []

    def probe(train):
        probe_seen.append(train[-1])
        return train[-1]

    models = {"naive": fh.price_models()["naive"], "probe": probe}
    fh.evaluate_price_models(series, train_size=40, purge=5, embargo=5,
                             models=models, resamples=200, seed=5)
    # At origin t the probe's last training value is series[t-6]:
    # the 5 purged observations (t-5..t-1) are never seen.
    assert probe_seen[0] == pytest.approx(series[40 - 6])
    assert probe_seen[-1] == pytest.approx(series[80 - 1 - 5 - 6])


def test_no_lookahead_perturbing_the_future_changes_nothing_earlier():
    series = _trend_series(n=80, seed=3)
    first = fh.evaluate_price_models(series, train_size=40, resamples=200,
                                     seed=5)
    later = list(series)
    later[-1] += 50.0
    later[-2] -= 50.0
    second = fh.evaluate_price_models(later, train_size=40, resamples=200,
                                      seed=5)
    for name in fh.price_models():
        assert first["models"][name]["folds"][:-2] == \
            second["models"][name]["folds"][:-2]


def test_linear_trend_beats_naive_on_a_clean_trend():
    series = _trend_series(n=200, slope=0.5, noise=0.05, seed=7)
    result = fh.evaluate_price_models(series, train_size=60, resamples=500,
                                      seed=5)
    trend = result["models"]["linear_trend"]
    assert trend["verdict"] == "beats baseline"
    assert trend["mae"] < result["models"]["naive"]["mae"]
    assert trend["ci_95"][1] < 0
    assert result["models"]["naive"]["verdict"] == "baseline"
    assert result["models_tried"] == 9


def test_constant_series_is_no_edge_for_everyone():
    series = [100.0] * 120
    result = fh.evaluate_price_models(series, train_size=60, resamples=200,
                                      seed=5)
    for name, model in result["models"].items():
        if name != "naive":
            assert model["verdict"] == "no edge"
            assert model["mae"] == pytest.approx(0.0)


def test_direction_accuracy_and_base_rate_on_a_trend():
    series = _trend_series(n=200, slope=0.5, noise=0.01, seed=7)
    result = fh.evaluate_price_models(series, train_size=60, resamples=200,
                                      seed=5)
    trend = result["models"]["linear_trend"]
    assert trend["up_day_base_rate"] == pytest.approx(1.0)
    assert trend["direction_accuracy"] == pytest.approx(1.0)
    assert trend["direction_n_correct"] == trend["n"]


def test_evaluate_price_models_validates():
    with pytest.raises(ValueError):
        fh.evaluate_price_models([1.0] * 20, train_size=15,
                                 models={"moving_average_5": lambda t: t[-1]})


# --- probabilistic evaluation ---------------------------------------------

def test_direction_probabilities_split_fit_eval():
    # One label flipped per block: perfectly separable scores would
    # saturate Platt's Newton fit and trigger its documented
    # base-rate fallback, so the fixture carries realistic noise.
    scores = [-3.0, -2.0, -1.0, 1.0, 2.0, 3.0] * 4
    outs = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0] * 4
    out = fh.evaluate_direction_probabilities(scores, outs)
    assert out["n_fit"] == 12
    assert out["n_eval"] == 12
    assert out["brier"] < out["brier_always_half"]
    assert out["brier_always_half"] == pytest.approx(0.25)
    assert out["log_score"] < out["log_score_always_half"]
    assert sum(b["n"] for b in out["calibration"]) == 12


def test_direction_probabilities_too_few_folds_reports_instead():
    out = fh.evaluate_direction_probabilities([1.0, -1.0, 0.5, -0.5],
                                              [1.0, 0.0, 1.0, 0.0])
    assert "too few folds" in out["note"]


def test_direction_probabilities_validates():
    with pytest.raises(ValueError):
        fh.evaluate_direction_probabilities([1.0, 2.0], [1.0])


# --- volatility evaluation -------------------------------------------------

def test_evaluate_volatility_models_reuses_qlike_verdicts():
    rng = random.Random(4)
    returns = [rng.gauss(0, 0.01) for _ in range(300)]
    out = fh.evaluate_volatility_models(returns, train_size=252)
    assert out["models_tried"] == 2
    assert set(out["comparison"]["models"]) == {"naive", "ewma",
                                                "garch_lite"}
    for model in out["comparison"]["models"].values():
        assert model["verdict"] in ("beats baseline", "no edge", "baseline")
    assert out["comparison"]["models"]["naive"]["verdict"] == "baseline"


# --- full run ---------------------------------------------------------------

def test_run_full_evaluation_counts_eleven_models_tried():
    series = {"SPY": _trend_series(n=120, slope=0.3, seed=1),
              "AAPL": _trend_series(n=120, slope=0.4, seed=2)}
    out = fh.run_full_evaluation(series, train_size=60, resamples=200,
                                 seed=5)
    assert out["models_tried"] == 11
    assert out["models_tried_price"] == 9
    assert out["models_tried_volatility"] == 2
    assert out["symbols"] == ["SPY", "AAPL"]
    assert out["overall_price_verdicts"]["naive"] == "baseline"
    for verdict in out["overall_price_verdicts"].values():
        assert verdict in ("beats baseline", "no edge", "baseline")
    pooled = out["pooled_direction"]["linear_trend"]
    assert 0.0 <= pooled["accuracy"] <= 1.0
    assert pooled["n"] == 2 * (120 - 60)


def test_run_full_evaluation_overall_needs_every_symbol():
    # linear_trend beats naive on the trending symbol only; the
    # flat symbol keeps its overall verdict at "no edge".
    series = {"UP": _trend_series(n=120, slope=0.5, noise=0.01, seed=1),
              "FLAT": [100.0] * 120}
    out = fh.run_full_evaluation(series, train_size=60, resamples=200,
                                 seed=5)
    assert out["per_symbol"]["UP"]["price"]["models"]["linear_trend"][
        "verdict"] == "beats baseline"
    assert out["overall_price_verdicts"]["linear_trend"] == "no edge"


# --- daily-stream scoring ----------------------------------------------------

def _daily_row(symbol, forecast, last_close, actual=None, correct=None,
               predicted_at="20261114T000000Z"):
    row = {"symbol": symbol, "forecast": forecast, "last_close": last_close,
           "model": "naive", "predicted_at": predicted_at,
           "predicted_for": "next_step"}
    if actual is not None:
        row.update({"actual": actual, "actual_date": "2026-11-14",
                    "status": "scored" if correct else "failed",
                    "direction_correct": bool(correct)})
    else:
        row["status"] = "pending"
    return row


def test_score_daily_predictions_counts_and_failed_stay_in():
    rows = [
        _daily_row("AAPL", 100.0, 99.0, actual=101.0, correct=True),
        _daily_row("AAPL", 100.0, 99.0, actual=98.0, correct=False),
        _daily_row("MSFT", 200.0, 199.0, actual=201.0, correct=True),
        _daily_row("MSFT", 200.0, 199.0),
    ]
    out = fh.score_daily_predictions(rows, resamples=200, seed=5)
    assert out["counts"] == {"made": 4, "scored": 3, "pending": 1}
    assert out["claim"] == "insufficient n — no claim"
    assert out["verdict"] is None
    assert out["pooled"]["n_scored"] == 3
    assert out["per_symbol"]["AAPL"]["n_scored"] == 2
    assert out["per_symbol"]["MSFT"]["n_scored"] == 1
    assert out["pooled"]["mae"] == pytest.approx((1.0 + 2.0 + 1.0) / 3)


def test_score_daily_predictions_perfect_forecast_beats_baseline_at_n100():
    rows = [_daily_row("AAPL", 100.0 + i, 99.0 + i, actual=100.0 + i,
                       correct=True) for i in range(100)]
    out = fh.score_daily_predictions(rows, resamples=500, seed=5)
    assert out["counts"]["scored"] == 100
    assert out["pooled"]["mae"] == pytest.approx(0.0)
    assert out["claim"] == "beats baseline"
    assert out["verdict"] == "beats baseline"
    assert out["pooled"]["ci_95"][1] < 0


def test_score_daily_predictions_no_edge_when_forecast_is_baseline():
    rows = [_daily_row("AAPL", 99.0 + i, 99.0 + i, actual=100.0 + i,
                       correct=False) for i in range(100)]
    out = fh.score_daily_predictions(rows, resamples=200, seed=5)
    assert out["claim"] == "no edge"


def test_score_daily_predictions_cutoff_excludes_development_period():
    # Rows before the cutoff are the development period: scored or
    # not, they never count toward the 100-forecast threshold.
    # Rows on or after it count. Synthetic cutoff 2026-11-15.
    before = [
        _daily_row("AAPL", 100.0, 99.0, actual=101.0, correct=True,
                   predicted_at="20261114T000000Z"),
        _daily_row("AAPL", 100.0, 99.0, actual=98.0, correct=False,
                   predicted_at="20261113T000000Z"),
    ]
    after = [
        _daily_row("MSFT", 200.0, 199.0, actual=201.0, correct=True,
                   predicted_at="20261115T000000Z"),
        _daily_row("MSFT", 200.0, 199.0,
                   predicted_at="20261116T000000Z"),
    ]
    out = fh.score_daily_predictions(before + after, resamples=200,
                                     seed=5, cutoff="2026-11-15")
    assert out["cutoff"] == "2026-11-15"
    assert out["counts"] == {"made": 2, "scored": 1, "pending": 1}
    assert out["pooled"]["n_scored"] == 1
    assert set(out["per_symbol"]) == {"MSFT"}
    # The pre-cutoff scored rows do not leak into the metrics.
    assert out["pooled"]["mae"] == pytest.approx(1.0)
    # A date object works the same as the ISO string.
    out_date = fh.score_daily_predictions(
        before + after, resamples=200, seed=5, cutoff=date(2026, 11, 15))
    assert out_date["counts"] == {"made": 2, "scored": 1, "pending": 1}
    # No cutoff: the same rows count as the development record.
    dev = fh.score_daily_predictions(before + after, resamples=200, seed=5)
    assert dev["cutoff"] is None
    assert dev["counts"] == {"made": 4, "scored": 3, "pending": 1}


def test_score_daily_predictions_cutoff_refuses_undated_rows():
    rows = [_daily_row("AAPL", 100.0, 99.0, actual=101.0, correct=True)]
    rows[0].pop("predicted_at")
    with pytest.raises(ValueError):
        fh.score_daily_predictions(rows, resamples=200, seed=5,
                                   cutoff="2026-11-15")
    bad = [_daily_row("AAPL", 100.0, 99.0, actual=101.0, correct=True,
                      predicted_at="not-a-date")]
    with pytest.raises(ValueError):
        fh.score_daily_predictions(bad, resamples=200, seed=5,
                                   cutoff="2026-11-15")


def test_read_daily_predictions_round_trips(tmp_path):
    import json
    path = tmp_path / "predictions.jsonl"
    rows = [_daily_row("AAPL", 100.0, 99.0, actual=101.0, correct=True)]
    path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    assert fh.read_daily_predictions(path) == rows
    assert fh.read_daily_predictions(tmp_path / "missing.jsonl") == []


# --- experiment tracking ------------------------------------------------------

def test_record_harness_experiment_appends_and_loads(tmp_path):
    series = {"SPY": _trend_series(n=120, slope=0.3, seed=1)}
    evaluation = fh.run_full_evaluation(series, train_size=60,
                                        resamples=200, seed=5)
    path = tmp_path / "experiments.jsonl"
    first = fh.record_harness_experiment("test-run", evaluation, path)
    assert first["models_tried"] == 11
    assert first["config"]["train_size"] == 60
    assert first["config"]["symbols"] == ["SPY"]
    assert first["overall_price_verdicts"]["naive"] == "baseline"
    fh.record_harness_experiment("test-run-2", evaluation, path)
    loaded = fh.load_experiments(path)
    assert len(loaded) == 2
    assert loaded[0]["name"] == "test-run"
    assert loaded[1]["name"] == "test-run-2"


def test_record_harness_experiment_validates():
    with pytest.raises(ValueError):
        fh.record_harness_experiment("x", {"not": "an evaluation"}, "/tmp/x")
