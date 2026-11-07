"""Tests for Engine 1.2 (toolkit/timemachine.py NEPSE upgrade).

Synthetic example data only. Every result asserted here must state
"simulation, not a prediction", and no test asserts profitability.
"""

from pathlib import Path

import pytest

from toolkit import nepal_frictions, timemachine
from toolkit.terminal import load_bars

REPO_ROOT = Path(__file__).resolve().parent.parent


def _rising_closes(n=60, start=100.0, step=1.0):
    return [start + step * i for i in range(n)]


def _falling_closes(n=60, start=200.0, step=-1.0):
    return [start + step * i for i in range(n)]


# --- named setups -----------------------------------------------------------

def test_detect_setups_returns_all_five_in_order():
    result = timemachine.detect_setups(_rising_closes())
    assert list(result) == list(timemachine.SETUP_NAMES)
    assert set(result) == {
        "breakout_20d", "bull_flag", "reclaim_ma20",
        "oversold", "oversold_bounce"}


def test_detect_setup_breakout_fires_on_rising_closes():
    matched, detail = timemachine.detect_setup(
        "breakout_20d", _rising_closes())
    assert matched is True
    assert "above 20-day high" in detail


def test_detect_setup_oversold_fires_on_falling_closes():
    matched, detail = timemachine.detect_setup(
        "oversold", _falling_closes())
    assert matched is True
    assert "below 30" in detail
    matched, _ = timemachine.detect_setup("oversold", _rising_closes())
    assert matched is False


def test_detect_setup_unknown_name_raises():
    with pytest.raises(ValueError, match="unknown setup"):
        timemachine.detect_setup("moon_shot", _rising_closes())


def test_detect_setup_reports_too_few_closes():
    matched, detail = timemachine.detect_setup("breakout_20d", [100.0] * 10)
    assert matched is False
    assert detail.startswith("too few closes")


def test_detect_setup_does_not_import_the_scanner_repo():
    import toolkit.timemachine as tm
    assert "scanner" not in getattr(tm, "__file__", "")


# --- honest verdict ----------------------------------------------------------

def test_honest_verdict_thresholds_are_stated_and_applied():
    # Fewer than 20 windows: insufficient data, however large the gap.
    assert timemachine.honest_verdict(1.0, 1.0 / 3.0, 19) == "insufficient data"
    assert timemachine.honest_verdict(None, 1.0 / 3.0, 100) == "insufficient data"
    # Exactly 10 points from break-even with enough windows: edge.
    assert timemachine.honest_verdict(0.4334, 1.0 / 3.0, 20) == "edge"
    assert timemachine.honest_verdict(0.2333, 1.0 / 3.0, 30) == "edge"
    # Inside 10 points: coin flip.
    assert timemachine.honest_verdict(0.40, 1.0 / 3.0, 50) == "coin flip"
    assert timemachine.honest_verdict(1.0 / 3.0, 1.0 / 3.0, 50) == "coin flip"


# --- exit liquidity -----------------------------------------------------------

def test_exit_liquidity_passes_a_small_position():
    check = timemachine.check_exit_liquidity(100, 10_000)
    assert check["blocked"] is False
    assert check["participation"] == pytest.approx(0.01)


def test_exit_liquidity_blocks_a_position_above_ten_percent_of_volume():
    check = timemachine.check_exit_liquidity(2_000, 10_000)
    assert check["blocked"] is True
    assert check["participation"] == pytest.approx(0.20)
    assert "BLOCKED" in check["reason"]


def test_exit_liquidity_at_exactly_ten_percent_is_not_blocked():
    check = timemachine.check_exit_liquidity(1_000, 10_000)
    assert check["blocked"] is False


def test_exit_liquidity_rejects_bad_inputs():
    with pytest.raises(ValueError):
        timemachine.check_exit_liquidity(0, 10_000)
    with pytest.raises(ValueError):
        timemachine.check_exit_liquidity(100, 0)
    with pytest.raises(ValueError):
        timemachine.check_exit_liquidity(100, 10_000, max_participation=1.5)


# --- replay_setup -------------------------------------------------------------

def test_replay_setup_only_replays_windows_where_the_setup_fired():
    # Rising closes: breakout fires at every window start once 21
    # closes exist (windows 20..39 = 20 setup windows of 40 total).
    res = timemachine.replay_setup(
        _rising_closes(), "breakout_20d", 160, 155, 170)
    assert res["method"] == "simulation, not a prediction"
    assert res["setup"] == "breakout_20d"
    assert res["n_windows"] == 40
    assert res["n_setup_windows"] == 20
    assert res["n_skipped_insufficient_history"] == 20
    assert res["target_first"] == pytest.approx(1.0)
    assert res["honest_verdict"] == "edge"
    assert len(res["windows"]) == 20
    assert all(w["outcome"] == "target" for w in res["windows"])


def test_replay_setup_reports_insufficient_data_when_nothing_fires():
    res = timemachine.replay_setup(
        _rising_closes(), "oversold", 160, 155, 170)
    assert res["n_setup_windows"] == 0
    assert res["target_first"] is None
    assert res["average_R"] is None
    assert res["honest_verdict"] == "insufficient data"
    assert res["windows"] == []


def test_replay_setup_is_point_in_time_correct():
    # The jump to 120 happens at the END of the series. Windows
    # starting earlier must not see it: no window's setup check may
    # use closes after the window's start.
    closes = [100.0] * 40 + [120.0] * 20
    res = timemachine.replay_setup(closes, "breakout_20d", 160, 155, 170)
    assert res["n_setup_windows"] == 0
    assert res["honest_verdict"] == "insufficient data"


def test_replay_setup_small_sample_is_insufficient_data_not_an_edge():
    # Custom setup fires on only 16 windows: even a 100% target-first
    # record is "insufficient data", not an edge.
    res = timemachine.replay_setup(
        _rising_closes(), "custom", 160, 155, 170,
        setup_fn=lambda closes: len(closes) >= 25)
    assert res["n_setup_windows"] == 16
    assert res["target_first"] == pytest.approx(1.0)
    assert res["honest_verdict"] == "insufficient data"


def test_replay_setup_accepts_a_callable_setup():
    res = timemachine.replay_setup(
        _rising_closes(), "custom", 160, 155, 170,
        setup_fn=lambda closes: len(closes) >= 21)
    assert res["n_setup_windows"] == 20


def test_replay_setup_rejects_unknown_setup_and_bad_plan():
    with pytest.raises(ValueError, match="unknown setup"):
        timemachine.replay_setup(_rising_closes(), "nope", 160, 155, 170)
    with pytest.raises(ValueError):
        timemachine.replay_setup(_rising_closes(), "breakout_20d",
                                 160, 160, 170)
    with pytest.raises(ValueError):
        timemachine.replay_setup(_rising_closes(30), "breakout_20d",
                                 160, 155, 170)


def test_replay_setup_net_r_is_below_gross_r_after_frictions():
    frictions = nepal_frictions.sourced_nepal_frictions()
    res = timemachine.replay_setup(
        _rising_closes(), "breakout_20d", 160, 155, 170,
        frictions=frictions, shares=100)
    assert res["average_R_net"] < res["average_R"]
    with pytest.raises(ValueError, match="shares is required"):
        timemachine.replay_setup(
            _rising_closes(), "breakout_20d", 160, 155, 170,
            frictions=frictions)


# --- simulate_nepse -----------------------------------------------------------

def test_simulate_nepse_labels_everything_and_reports_net_r():
    res = timemachine.simulate_nepse(
        _rising_closes(), 160, 155, 170, n_sims=500, seed=19)
    assert res["method"] == "simulation, not a prediction"
    assert res["blocked"] is False
    assert res["shares"] == 100
    assert res["circuit_limit_up"] == pytest.approx(0.15)
    assert res["circuit_limit_down"] == pytest.approx(0.15)
    assert "rate-schedule screenshot, 28 Oct 2026" in res["circuit_source"]
    assert "rate-schedule screenshot, 28 Oct 2026" in res["frictions_source"]
    assert res["frictions_as_of"] == "2026-10-28"
    assert res["settlement_lag_business_days"] == 2
    assert res["buy_charges_at_entry"] > 0
    assert res["honest_verdict"] in ("edge", "coin flip", "insufficient data")
    # Frictions are a certain drag: net R is always below gross R.
    assert res["average_R_net"] < res["average_R_gross"]
    assert res["average_R"] == pytest.approx(res["average_R_net"])
    assert res["target_first"] + res["stop_first"] + res["neither"] == (
        pytest.approx(1.0))


def test_simulate_nepse_is_deterministic_for_a_fixed_seed():
    prices = _rising_closes()
    a = timemachine.simulate_nepse(prices, 160, 155, 170, n_sims=200, seed=19)
    b = timemachine.simulate_nepse(prices, 160, 155, 170, n_sims=200, seed=19)
    assert a == b


def test_simulate_nepse_circuit_clips_extreme_daily_moves():
    # Every bar doubles (+100%): without the circuit the median final
    # close would be astronomic; clipped to +15%/day it cannot exceed
    # entry * 1.15 ** horizon.
    bars = [{"open": 100.0, "high": 200.0, "low": 100.0, "close": 200.0}
            for _ in range(60)]
    res = timemachine.simulate_nepse(bars, 100, 95, 200, n_sims=200, seed=19)
    assert res["median_close"] <= 100.0 * 1.15 ** 20 * (1.0 + 1e-9)
    assert res["close_p95"] <= 100.0 * 1.15 ** 20 * (1.0 + 1e-9)
    assert res["median_close"] > 100.0
    # Without the circuit the +100% bar hits the 200 target on day 1
    # (final close 200); clipped to +15%/day it takes 5 days to reach
    # it, so the exit bar's close is above 200.
    plain = timemachine.simulate(bars, 100, 95, 200, n_sims=200, seed=19)
    assert plain["median_close"] == pytest.approx(200.0)
    assert res["median_close"] > plain["median_close"]


def test_simulate_nepse_blocks_an_illiquid_position_and_runs_nothing():
    res = timemachine.simulate_nepse(
        _rising_closes(), 160, 155, 170, shares=5000,
        typical_daily_volume=10_000, n_sims=100, seed=19)
    assert res["blocked"] is True
    assert res["verdict"] == "BLOCKED — exit liquidity"
    assert res["honest_verdict"] is None
    assert "target_first" not in res
    assert res["liquidity"]["participation"] == pytest.approx(0.50)


def test_simulate_nepse_passes_a_liquid_position():
    res = timemachine.simulate_nepse(
        _rising_closes(), 160, 155, 170, shares=100,
        typical_daily_volume=10_000, n_sims=100, seed=19)
    assert res["blocked"] is False
    assert res["liquidity"]["blocked"] is False


def test_simulate_nepse_refuses_unknown_frictions():
    with pytest.raises(nepal_frictions.UnknownFrictionError):
        timemachine.simulate_nepse(
            _rising_closes(), 160, 155, 170,
            frictions=nepal_frictions.default_unknown_frictions(),
            n_sims=10, seed=19)


def test_simulate_nepse_rejects_bad_plan_and_shares():
    with pytest.raises(ValueError):
        timemachine.simulate_nepse(_rising_closes(), 160, 160, 170)
    with pytest.raises(ValueError):
        timemachine.simulate_nepse(_rising_closes(), 160, 155, 170, shares=0)


# --- worked example (README) ------------------------------------------------
# The numbers below are the worked example printed in the README.
# They are pinned here so the README can never drift from the code:
# same file (demo.csv — SYNTHETIC), same plan, same seed.

def _demo_ohlc():
    bars = load_bars(REPO_ROOT / "demo.csv")
    return [{"open": b["open"], "high": b["high"], "low": b["low"],
             "close": b["close"]} for b in bars]


def test_readme_worked_example_simulate_nepse():
    res = timemachine.simulate_nepse(
        _demo_ohlc(), 1284.91, 1259.72, 1335.29, shares=100,
        typical_daily_volume=30_000, n_sims=10_000, seed=19)
    assert res["n_windows"] == 61
    assert res["target_first"] == pytest.approx(0.5754)
    assert res["stop_first"] == pytest.approx(0.3900)
    assert res["neither"] == pytest.approx(0.0346)
    assert res["break_even"] == pytest.approx(1.0 / 3.0)
    assert res["average_R_gross"] == pytest.approx(0.79, abs=0.01)
    assert res["average_R_net"] == pytest.approx(0.37, abs=0.01)
    assert res["average_R_net"] < res["average_R_gross"]
    assert res["median_close"] == pytest.approx(1334.07, abs=0.01)
    assert res["buy_charges_at_entry"] == pytest.approx(468.29, abs=0.01)
    assert res["honest_verdict"] == "edge"


def test_readme_worked_example_replay_setup():
    # breakout_20d fired on only 11 of 61 windows: a 72.7%
    # target-first record that is still "insufficient data"
    # (fewer than 20 setup windows) — the honest verdict, not an edge.
    res = timemachine.replay_setup(
        _demo_ohlc(), "breakout_20d", 1284.91, 1259.72, 1335.29)
    assert res["n_windows"] == 61
    assert res["n_setup_windows"] == 11
    assert res["target_first"] == pytest.approx(0.7273, abs=0.001)
    assert res["honest_verdict"] == "insufficient data"
    # bull_flag never fired on this file: no shares are reported.
    res2 = timemachine.replay_setup(
        _demo_ohlc(), "bull_flag", 1284.91, 1259.72, 1335.29)
    assert res2["n_setup_windows"] == 0
    assert res2["target_first"] is None
    assert res2["honest_verdict"] == "insufficient data"
