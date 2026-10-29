"""Walk-forward model-comparison report (Slice B).

Offline fixtures only: no network, no live prices. The comparison must
run every model through the SAME walk-forward split, rank by MAE, and
report — never hide — the case where naive wins.
"""
import pytest

from toolkit import predictive


def test_comparison_ranks_trend_first_on_trending_series():
    series = [100 + i * 2 for i in range(40)]
    rep = predictive.compare_models_walk_forward(series, train_size=20)
    assert rep["best_model"] == "linear_trend"
    assert rep["beats_naive"] is True
    assert rep["naive_wins"] is False
    assert rep["n_test"] == 20
    assert rep["train_size"] == 20
    maes = [row["mae"] for row in rep["ranking"]]
    assert maes == sorted(maes)
    assert rep["ranking"][0]["model"] == "linear_trend"
    assert rep["ranking"][0]["mae"] == rep["best_mae"]


def test_comparison_flags_naive_win_on_level_shift():
    # A sudden level shift that then persists: lagging models (moving
    # average, trend, smoothing) chase the old level, while naive adapts
    # immediately — so naive strictly wins out of sample.
    series = [100.0] * 15 + [150.0] * 15
    rep = predictive.compare_models_walk_forward(series, train_size=15)
    assert rep["best_model"] == "naive"
    assert rep["beats_naive"] is False
    assert rep["naive_wins"] is True
    assert rep["naive_mae"] < rep["ranking"][1]["mae"]
    assert "Naive wins" in rep["note"]
    assert all(row["beats_naive"] is False for row in rep["ranking"])


def test_comparison_covers_all_models_with_same_split():
    series = [100 + i * 2 for i in range(40)]
    rep = predictive.compare_models_walk_forward(series, train_size=20)
    assert {row["model"] for row in rep["ranking"]} == {
        "naive", "moving_average_5", "linear_trend", "exp_smoothing_0.3"}
    assert all(row["n_test"] == 20 for row in rep["ranking"])
    for row in rep["ranking"]:
        assert row["mae"] == rep["results"][row["model"]]["mae"]
        assert row["rmse"] == rep["results"][row["model"]]["rmse"]
    assert rep["naive_mae"] == rep["results"]["naive"]["mae"]


def test_comparison_agrees_with_auto_select():
    series = [100 + i * 2 for i in range(40)]
    rep = predictive.compare_models_walk_forward(series, train_size=20)
    sel = predictive.auto_select_model(series, train_size=20)
    assert rep["best_model"] == sel["best_model"]
    assert rep["best_mae"] == sel["best_mae"]
    assert rep["beats_naive"] == sel["beats_naive"]


def test_comparison_rejects_bad_inputs():
    with pytest.raises(ValueError):
        predictive.compare_models_walk_forward([100.0] * 9, train_size=5)
    with pytest.raises(ValueError):
        predictive.compare_models_walk_forward([100.0] * 20, train_size=20)
    with pytest.raises(ValueError):
        predictive.compare_models_walk_forward([100.0] * 20, train_size=0)
    with pytest.raises(ValueError):
        predictive.compare_models_walk_forward([100.0, float("nan")] * 10, train_size=10)
