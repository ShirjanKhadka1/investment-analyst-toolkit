"""Tests for toolkit/timemachine.py — synthetic example data only."""

import random

import pytest

from toolkit import timemachine


def _closes(n=60, seed=7):
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1.0 + rng.gauss(0.001, 0.01)))
    return closes


def _both_touch_bars(n=40):
    # Every bar touches both levels: low 90 <= stop 95, high 110 >= target 105.
    return [{"open": 100.0, "high": 110.0, "low": 90.0, "close": 100.0}
            for _ in range(n)]


def _target_bars(n=60):
    # Every bar runs straight to the target: high 112 >= target 102.
    return [{"open": 100.0, "high": 112.0, "low": 100.0, "close": 110.0}
            for _ in range(n)]


def test_simulate_shares_sum_to_one_and_records_seed():
    res = timemachine.simulate(_closes(), 100, 95, 110, n_sims=500, seed=19)
    assert res["seed"] == 19
    assert res["n_sims"] == 500
    assert res["horizon"] == 20
    assert res["n_windows"] == 40  # 59 steps - 20 + 1
    assert res["method"] == "simulation, not a prediction"
    assert res["target_first"] + res["stop_first"] + res["neither"] == pytest.approx(1.0)
    assert res["close_p5"] <= res["median_close"] <= res["close_p95"]
    assert res["break_even"] == pytest.approx(1.0 / 3.0)  # R = 2
    assert res["reward_risk"] == pytest.approx(2.0)
    assert res["verdict"] in ("Close to a coin flip", "Leans toward target",
                              "Leans toward stop", "Strongly toward target",
                              "Strongly toward stop")


def test_simulate_is_deterministic_for_a_fixed_seed():
    prices = _closes()
    a = timemachine.simulate(prices, 100, 95, 110, n_sims=200, seed=19)
    b = timemachine.simulate(prices, 100, 95, 110, n_sims=200, seed=19)
    assert a == b
    c = timemachine.simulate(prices, 100, 95, 110, n_sims=200, seed=20)
    assert c["seed"] == 20
    assert c != a


def test_simulate_refuses_fewer_than_20_windows():
    with pytest.raises(ValueError):
        timemachine.simulate(_closes(30), 100, 95, 110, n_sims=10)


def test_simulate_refuses_bad_plan_levels():
    prices = _closes()
    with pytest.raises(ValueError):
        timemachine.simulate(prices, 100, 100, 110, n_sims=10)
    with pytest.raises(ValueError):
        timemachine.simulate(prices, 100, 105, 110, n_sims=10)
    with pytest.raises(ValueError):
        timemachine.simulate(prices, 100, 95, 100, n_sims=10)
    with pytest.raises(ValueError):
        timemachine.simulate(prices, 100, 95, 90, n_sims=10)


def test_replay_stop_checked_before_target_on_a_bar_touching_both():
    res = timemachine.replay_history(_both_touch_bars(), 100, 95, 105)
    assert res["n_windows"] == 21
    assert res["stop_first"] == 1.0
    assert res["target_first"] == 0.0
    assert res["neither"] == 0.0
    assert res["average_R"] == pytest.approx(-1.0)
    assert res["windows"][0]["outcome"] == "stop"
    assert res["windows"][0]["exit_day"] == 1
    assert res["windows"][0]["exit_price"] == 95.0
    assert res["seed"] is None  # deterministic replay carries no seed


def test_replay_strongly_toward_target_when_every_bar_hits_target():
    res = timemachine.replay_history(_target_bars(), 100, 90, 102)
    assert res["target_first"] == 1.0
    # R = 2/10 = 0.2, break-even = 1/1.2 = 0.8333; gap 16.7pts.
    assert res["break_even"] == pytest.approx(1.0 / 1.2)
    assert res["verdict"] == "Strongly toward target"
    assert res["average_R"] == pytest.approx(0.2)


def test_verdict_bands():
    verdict = timemachine._verdict
    assert verdict(0.50, 0.50) == "Close to a coin flip"
    assert verdict(0.549, 0.50) == "Close to a coin flip"
    assert verdict(0.55, 0.50) == "Leans toward target"
    assert verdict(0.60, 0.50) == "Leans toward target"
    assert verdict(0.65, 0.50) == "Strongly toward target"
    assert verdict(0.45, 0.50) == "Leans toward stop"
    assert verdict(0.30, 0.50) == "Strongly toward stop"


def test_replay_window_count_and_shares():
    res = timemachine.replay_history(_closes(), 100, 95, 110)
    assert res["n_windows"] == 40
    assert len(res["windows"]) == 40
    assert res["target_first"] + res["stop_first"] + res["neither"] == pytest.approx(1.0)
    assert res["windows"][0]["start_index"] == 0
    assert res["windows"][-1]["start_index"] == 39


def test_replay_refuses_fewer_than_20_windows():
    with pytest.raises(ValueError):
        timemachine.replay_history(_closes(30), 100, 95, 110)


def test_simulate_accepts_ohlc_dicts_and_rejects_a_missing_key():
    bars = [{"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0}
            for _ in range(60)]
    res = timemachine.simulate(bars, 100, 95, 110, n_sims=50, seed=19)
    assert res["n_windows"] == 41  # 60 bars - 20 + 1
    bad = [{"open": 100.0, "high": 102.0, "low": 99.0} for _ in range(60)]
    with pytest.raises(ValueError):
        timemachine.simulate(bad, 100, 95, 110, n_sims=10)


def test_simulate_neither_paths_report_final_close_as_exit():
    # Flat closes: no bar ever touches stop 95 or target 110.
    res = timemachine.simulate([100.0] * 60, 100, 95, 110, n_sims=50, seed=19)
    assert res["neither"] == 1.0
    assert res["median_close"] == pytest.approx(100.0)
    assert res["average_R"] == pytest.approx(0.0)
    assert res["verdict"] == "Strongly toward stop"  # 0.0 vs break-even 1/3
