"""Engine 1.7 tests — predictive layer (walk-forward only).

Fixtures: a trending series (models should beat naive), a flat
random-walk-like series (naive should win — the "no edge" case is a
test, not an embarrassment), and a fixed-seed synthetic series for
the store/leak tests. Nothing here is real market data.
"""
import math
import random

import pytest

from toolkit import predictive
from toolkit.pit_store import PointInTimeStore

# Trending series: 100 + 0.5*t + small wiggle. Linear trend / drift
# should beat naive out of sample; naive should lose.
TREND = [100.0 + 0.5 * t + (1.0 if t % 2 else -1.0) for t in range(60)]
# Driftless random walk (fixed seed 1, 200 points): naive is the
# optimal one-step forecast, so no model should beat it out of sample
# by more than noise — the "no edge" case. (A deterministic
# alternating series was tried first and REJECTED: any smoothing
# model beats naive on it, so it proves nothing.)
_rng = random.Random(1)
FLAT = [100.0]
for _ in range(199):
    FLAT.append(FLAT[-1] + _rng.gauss(0.0, 1.0))


def _synthetic_prices(n=60, seed=11):
    rng = random.Random(seed)
    out, price = [], 100.0
    for _ in range(n):
        price += rng.gauss(0.3, 1.0)
        out.append(round(price, 4))
    return out


# --- walk_forward_validate ---------------------------------------------------

def test_walk_forward_validate_expanding_trend():
    res = predictive.walk_forward_validate(
        TREND, lambda t, h: predictive._linear_trend_h(t, h), 40, horizon=1)
    naive = predictive.walk_forward_validate(
        TREND, lambda t, h: t[-1], 40, horizon=1)
    assert res["n_test"] == 20
    assert res["mae"] < naive["mae"]
    assert len(res["folds"]) == 20
    # Every fold's forecast used only data before its origin.
    for origin, forecast, actual in res["folds"]:
        assert actual == TREND[origin]


def test_walk_forward_validate_rolling_window():
    res = predictive.walk_forward_validate(
        TREND, lambda t, h: predictive.drift_forecast(t, h), 40,
        horizon=1, window=10)
    assert res["window"] == 10
    assert res["n_test"] == 20
    # Rolling window sees only the last 10 points: prove it by
    # perturbing an early point — the forecast must not change.
    perturbed = TREND[:]
    perturbed[0] = 9999.0
    res2 = predictive.walk_forward_validate(
        perturbed, lambda t, h: predictive.drift_forecast(t, h), 40,
        horizon=1, window=10)
    assert res2["folds"] == res["folds"]


def test_walk_forward_validate_rejects_bad_inputs():
    with pytest.raises(ValueError):
        predictive.walk_forward_validate(TREND, lambda t, h: t[-1], 60)
    with pytest.raises(ValueError):
        predictive.walk_forward_validate(TREND, lambda t, h: t[-1], 40, horizon=0)
    with pytest.raises(ValueError):
        predictive.walk_forward_validate(TREND, lambda t, h: t[-1], 40, window=0)


def test_lookahead_audit_perturb_future():
    # Perturbing series AFTER an origin must not change that origin's
    # forecast. This is the audit surface for the whole layer.
    rows = predictive.walk_forward_forecasts(
        TREND, {"drift": lambda t, h: predictive.drift_forecast(t, h)}, 40, 1)
    perturbed = TREND[:]
    perturbed[55] = 5000.0
    rows2 = predictive.walk_forward_forecasts(
        perturbed, {"drift": lambda t, h: predictive.drift_forecast(t, h)}, 40, 1)
    for (o1, f1, _), (o2, f2, _) in zip(rows["drift"], rows2["drift"]):
        if o1 < 55:
            assert f1 == f2


# --- strategy_backtest ---------------------------------------------------------

def test_strategy_backtest_trend_beats_buy_and_hold():
    res = predictive.strategy_backtest(
        TREND, lambda t, h: predictive._linear_trend_h(t, h), 40)
    assert res["n_steps"] == 20
    assert res["beats_buy_and_hold"] is True
    # Buy-and-hold over the test window: last / first-test-price - 1.
    assert res["buy_and_hold_return"] == pytest.approx(
        TREND[-1] / TREND[39] - 1.0)


def test_strategy_backtest_flat_series_loses():
    # On the flat alternating series the trend model trades noise and
    # should not beat buy-and-hold — the honest losing case.
    res = predictive.strategy_backtest(
        FLAT, lambda t, h: predictive._linear_trend_h(t, h), 40)
    assert res["beats_buy_and_hold"] is False


def test_strategy_backtest_rejects_non_positive_prices():
    with pytest.raises(ValueError):
        predictive.strategy_backtest([100, 0, 100, 101, 102, 103, 104, 105,
                                      106, 107, 108, 109], lambda t, h: t[-1], 5)


# --- walk_forward_report ---------------------------------------------------------

def test_walk_forward_report_trend_has_edge_models():
    rep = predictive.walk_forward_report(TREND, 40, horizons=(1, 5))
    assert rep["train_size"] == 40
    assert set(rep["by_horizon"]) == {1, 5}
    h1 = rep["by_horizon"][1]
    names = [r["model"] for r in h1["rows"]]
    assert "naive" in names and "linear_trend" in names
    trend_row = next(r for r in h1["rows"] if r["model"] == "linear_trend")
    assert trend_row["beats_naive"] is True
    assert trend_row["verdict"] == "edge"
    naive_row = next(r for r in h1["rows"] if r["model"] == "naive")
    assert naive_row["verdict"] == "no edge"  # naive cannot beat itself
    assert "no separate index supplied" in rep["index_label"]


def test_walk_forward_report_flat_series_no_edge():
    # On the flat series, no model should claim an edge at h=1:
    # the report must publish that, not hide it.
    rep = predictive.walk_forward_report(FLAT, 40, horizons=(1,))
    rows = rep["by_horizon"][1]["rows"]
    assert all(r["verdict"] == "no edge" for r in rows)
    assert rep["by_horizon"][1]["n_edge"] == 0


def test_walk_forward_report_with_index_series():
    index = [200.0 + 0.2 * t for t in range(60)]  # slow steady index
    rep = predictive.walk_forward_report(TREND, 40, horizons=(1,),
                                         index_series=index)
    assert "separate index series supplied" in rep["index_label"]
    row = rep["by_horizon"][1]["rows"][0]
    assert row["index_return"] == pytest.approx(index[-1] / index[39] - 1.0)


def test_walk_forward_report_rejects_mismatched_index():
    with pytest.raises(ValueError):
        predictive.walk_forward_report(TREND, 40, index_series=[100.0] * 10)


# --- Calibration -----------------------------------------------------------------

def test_brier_score_known_values():
    assert predictive.brier_score([0.5, 0.5], [0, 1]) == pytest.approx(0.25)
    assert predictive.brier_score([1.0, 0.0], [1, 0]) == pytest.approx(0.0)
    assert predictive.brier_score([0.0, 1.0], [1, 0]) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        predictive.brier_score([1.5], [1])
    with pytest.raises(ValueError):
        predictive.brier_score([0.5], [2])


def test_platt_calibrate_improves_or_matches_brier():
    # Raw scores are systematically overconfident (0.9/0.1 on a
    # 50/50 outcome stream); calibration must not make Brier worse.
    scores = [0.9, 0.1, 0.9, 0.1, 0.9, 0.1, 0.9, 0.1,
              0.9, 0.1, 0.9, 0.1, 0.9, 0.1, 0.9, 0.1]
    outcomes = [1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0]
    res = predictive.platt_calibrate(scores, outcomes)
    assert res["n"] == 16
    assert res["brier_after"] <= res["brier_before"] + 1e-9
    assert math.isfinite(res["a"]) and math.isfinite(res["b"])
    assert "out-of-sample" in res["method"]


def test_platt_calibrate_constant_scores_falls_back():
    res = predictive.platt_calibrate([0.5] * 10, [1, 0, 1, 0, 1, 0, 1, 0, 1, 0])
    assert "fallback" in res["method"]
    assert res["a"] == pytest.approx(0.0)


def test_calibrated_probability():
    assert predictive.calibrated_probability(0.5, 0.0, 0.0) == pytest.approx(0.5)
    assert predictive.calibrated_probability(10.0, 1.0, 0.0) > 0.99
    assert predictive.calibrated_probability(-10.0, 1.0, 0.0) < 0.01


# --- Model cards, regime, retraining ----------------------------------------------

def test_model_card_trend_edge():
    card = predictive.model_card("linear_trend", TREND, 40, horizon=1)
    assert card["model"] == "linear_trend"
    assert card["data_window"] == {"n_total": 60, "n_train": 40, "n_test": 20,
                                   "horizon": 1, "window": "expanding"}
    assert card["verdict"] == "edge"
    assert card["metrics"]["beats_naive"] is True
    assert "trend" in card["failure_modes"].lower()


def test_model_card_flat_no_edge():
    card = predictive.model_card("linear_trend", FLAT, 40, horizon=1)
    assert card["verdict"] == "no edge"
    assert card["failure_modes"]  # a card always states failure modes


def test_model_card_unknown_model():
    with pytest.raises(ValueError):
        predictive.model_card("no_such_model", TREND, 40)


def test_detect_regime_is_labelled_heuristic():
    res = predictive.detect_regime(TREND, window=20)
    assert res["is_prediction"] is False
    assert "heuristic" in res["method"]
    assert res["label"].endswith("-trend")
    assert res["volatility_regime"] in ("high", "low")
    assert "falsifier" in res


def test_detect_regime_high_volatility_tail():
    # Quiet series, then a volatile tail: latest std must exceed the
    # median and the regime must read high.
    series = [100.0 + 0.01 * t for t in range(40)]
    series += [100.4 + (5.0 if t % 2 else -5.0) for t in range(20)]
    res = predictive.detect_regime(series, window=20)
    assert res["volatility_regime"] == "high"


def test_retraining_schedule_states_triggers():
    sched = predictive.retraining_schedule()
    assert sched["refit_per_fold"] is True
    assert any("drift" in t for t in sched["triggers"])
    assert any("regime" in t for t in sched["triggers"])
    assert "look-ahead" in sched["never"]


# --- walk_forward_from_store -------------------------------------------------------

def _build_store(n=60, seed=11, late_bar=False):
    import datetime
    store = PointInTimeStore()
    prices = _synthetic_prices(n, seed)
    base = datetime.date(2026, 1, 1)
    for i, price in enumerate(prices):
        date = (base + datetime.timedelta(days=i)).isoformat()
        store.add_bar(date, date, price, price, price, price)
    if late_bar:
        # A restatement for a LATER date, published a month after it,
        # with an extreme close: it must not leak into earlier origins.
        store.add_bar("2026-03-02", "2026-04-01", 201.0, 201.0, 201.0, 201.0)
    return store


def test_walk_forward_from_store_matches_plain_walk_forward():
    store = _build_store()
    res = predictive.walk_forward_from_store(
        store, lambda t, h: predictive.drift_forecast(t, h), 40, horizon=1)
    prices = _synthetic_prices(60, 11)
    plain = predictive.walk_forward_validate(
        prices, lambda t, h: predictive.drift_forecast(t, h), 40, horizon=1)
    assert res["n_test"] == plain["n_test"]
    assert res["mae"] == pytest.approx(plain["mae"])
    assert res["folds"][0][0] == store.as_of_dates()[39]


def test_walk_forward_from_store_late_bar_does_not_leak():
    # Mirror of the pit_store leak test: a bar published AFTER an
    # origin must not change that origin's forecast. A naive reader
    # that ignored published_at would see the 201 close and fail this.
    clean = predictive.walk_forward_from_store(
        _build_store(), lambda t, h: predictive.drift_forecast(t, h), 40)
    late = predictive.walk_forward_from_store(
        _build_store(late_bar=True),
        lambda t, h: predictive.drift_forecast(t, h), 40)
    # The late store has one extra origin date (the restated bar's
    # as_of); the first 20 folds — the origins both stores share —
    # must be identical.
    assert len(clean["folds"]) == 20
    assert [f for _, f, _ in clean["folds"]] == \
        [f for _, f, _ in late["folds"][:20]]
    assert clean["mae"] == pytest.approx(
        sum(abs(a - f) for _, f, a in late["folds"][:20]) / 20)


def test_walk_forward_from_store_rejects_short_store():
    store = PointInTimeStore()
    store.add_bar("2026-01-01", "2026-01-01", 100, 100, 100, 100)
    with pytest.raises(ValueError):
        predictive.walk_forward_from_store(
            store, lambda t, h: t[-1], 1)


def test_record_experiment_appends_jsonl(tmp_path):
    # qlib-style recorder, JSONL edition: one line per experiment,
    # verdicts preserved, re-runs append instead of rewriting.
    rep = predictive.walk_forward_report(TREND, 40, horizons=(1,))
    path = tmp_path / "experiments.jsonl"
    rec = predictive.record_experiment("trend-h1", rep, path)
    assert rec["name"] == "trend-h1"
    assert rec["train_size"] == 40
    assert rec["verdicts"]["1"]["linear_trend"] == "edge"
    assert rec["verdicts"]["1"]["naive"] == "no edge"
    predictive.record_experiment("trend-h1-rerun", rep, path)
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    import json
    assert json.loads(lines[1])["name"] == "trend-h1-rerun"
    with pytest.raises(ValueError):
        predictive.record_experiment("bad", {"not": "a report"}, path)
