"""Phase 9: the equal-weight ensemble as a walk-forward candidate.

The ensemble was previously a point-forecast helper only; it was never
judged out of sample against naive. These tests put it through the same
walk-forward split as every other model, with no lookahead, and keep the
honest rule: losing to naive is a reported result, not a hidden one.
Offline fixtures only.
"""
import pytest

from toolkit import predictive


def test_ensemble_appears_in_ranking_when_enabled():
    series = [100 + i * 2 for i in range(40)]
    rep = predictive.compare_models_walk_forward(series, train_size=20,
                                                 include_ensemble=True)
    names = {row["model"] for row in rep["ranking"]}
    assert "ensemble_equal" in names
    assert len(names) == 5
    row = [r for r in rep["ranking"] if r["model"] == "ensemble_equal"][0]
    assert row["n_test"] == 20
    assert row["mae"] == rep["results"]["ensemble_equal"]["mae"]
    maes = [r["mae"] for r in rep["ranking"]]
    assert maes == sorted(maes)


def test_ensemble_point_is_mean_of_members():
    train = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]
    members = [fn(train) for fn in predictive._models().values()]
    assert predictive._ensemble_equal(train) == sum(members) / len(members)


def test_ensemble_does_not_save_naive_win_fixture():
    # Level shift: lagging members drag the ensemble, so naive still wins
    # even with the ensemble in the ranking — the ensemble is a candidate,
    # not a rescue.
    series = [100.0] * 15 + [150.0] * 15
    rep = predictive.compare_models_walk_forward(series, train_size=15,
                                                 include_ensemble=True)
    assert rep["best_model"] == "naive"
    assert rep["naive_wins"] is True
    assert "Naive wins" in rep["note"]


def test_ensemble_off_by_default_preserves_original_ranking():
    series = [100 + i * 2 for i in range(40)]
    rep = predictive.compare_models_walk_forward(series, train_size=20)
    assert {row["model"] for row in rep["ranking"]} == {
        "naive", "moving_average_5", "linear_trend", "exp_smoothing_0.3"}
    sel = predictive.auto_select_model(series, train_size=20)
    assert rep["best_model"] == sel["best_model"]


def test_ensemble_rejects_bad_inputs():
    with pytest.raises(ValueError):
        predictive.compare_models_walk_forward([100.0] * 9, train_size=5,
                                               include_ensemble=True)
