"""Tests for toolkit/strategy_backtester.py — portfolio-level backtester.

Known-answer fixtures are hand-computed (see the arithmetic in each
test's comment); the look-ahead tests prove future bars cannot leak
into weights by mutation; the numpy tests run the same fixture both
ways and require bit-identical results.
"""
import json

import pytest

from toolkit import strategy_backtester as sb
from toolkit.strategy_backtester import (
    NumpyUnavailableError,
    UnknownCostError,
    bootstrap_ci,
    equal_weight_strategy,
    inverse_volatility_strategy,
    max_sharpe_strategy,
    min_variance_strategy,
    period_returns,
    run_backtest,
    tear_sheet,
    trade_cost,
    validate_bars,
    walk_forward_backtest,
)

DATES = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
A = [100.0, 102.0, 101.0, 103.0, 105.0]
B = [50.0, 50.0, 51.0, 51.0, 52.0]


def bars(a=None, b=None):
    return {"AAA": list(zip(DATES, a if a is not None else A)),
            "BBB": list(zip(DATES, b if b is not None else B))}


# --- bars ---------------------------------------------------------------

def test_validate_bars_accepts_aligned_pairs():
    out = validate_bars(bars())
    assert out["AAA"][0] == ("2026-01-05", 100.0)
    assert out["BBB"][-1] == ("2026-01-09", 52.0)


def test_validate_bars_rejects_misaligned_dates():
    bad = bars()
    bad["BBB"] = list(zip(
        ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-10"],
        B))
    with pytest.raises(ValueError, match="same.*dates"):
        validate_bars(bad)


def test_validate_bars_rejects_non_positive_close():
    with pytest.raises(ValueError):
        validate_bars(bars(a=[100.0, 0.0, 101.0, 103.0, 105.0]))


def test_validate_bars_rejects_empty():
    with pytest.raises(ValueError):
        validate_bars({})


def test_period_returns_known_answer():
    out = period_returns(bars())
    assert out["AAA"] == pytest.approx(
        [0.02, -0.00980392156862745, 0.0198019801980198, 0.01941747572815533])
    assert out["BBB"] == pytest.approx([0.0, 0.02, 0.0, 0.0196078431372549])


# --- costs from market profiles ------------------------------------------

def test_trade_cost_nepse_known_answer():
    # Rs 10,000: broker 0.36% = 36 (above Rs 10 min) + SEBON 1.5 + DP 25.
    cost, basis = trade_cost("nepse", 10000.0, "buy")
    assert cost == pytest.approx(62.5)
    assert "NEPSE" in basis and "CGT excluded" in basis
    # Rs 40,000: broker 144 + SEBON 6 + DP 25 = 175.
    assert trade_cost("nepse", 40000.0, "buy")[0] == pytest.approx(175.0)


def test_trade_cost_india_and_uk_known_answer():
    # India: STT 0.1% + NSE charge 0.003% = 10.3 on 10,000, both sides.
    assert trade_cost("india", 10000.0, "buy")[0] == pytest.approx(10.3)
    assert trade_cost("india", 10000.0, "sell")[0] == pytest.approx(10.3)
    # UK: SDRT 0.5% on buys only.
    assert trade_cost("uk", 10000.0, "buy")[0] == pytest.approx(50.0)
    assert trade_cost("uk", 10000.0, "sell")[0] == pytest.approx(0.0)


def test_trade_cost_unknown_schedule_raises():
    with pytest.raises(UnknownCostError, match="no sourced fee schedule"):
        trade_cost("us", 10000.0, "buy")
    with pytest.raises(UnknownCostError):
        trade_cost("generic", 10000.0, "buy")


def test_trade_cost_rejects_bad_side_and_value():
    with pytest.raises(ValueError):
        trade_cost("nepse", 10000.0, "hold")
    with pytest.raises(ValueError):
        trade_cost("nepse", -5.0, "buy")


# --- run_backtest: known answers ------------------------------------------

def test_backtest_equal_weight_no_cost_known_answer():
    # rebalance_every=10: only the t=0 trade. 50 shares A @100, 100 B @50.
    # Equity: 10000, 10100, 10150, 10250, 10450 (hand-computed).
    result = run_backtest(bars(), "equal_weight", market="generic",
                          cost_fraction=0.0, rebalance_every=10)
    assert result["equity"] == [10000.0, 10100.0, 10150.0, 10250.0, 10450.0]
    assert result["total_return"] == pytest.approx(0.045)
    assert result["total_cost"] == pytest.approx(0.0)
    assert result["n_trades"] == 2
    assert result["total_traded"] == pytest.approx(10000.0)
    assert result["benchmark_equity"] == result["equity"]
    assert result["beats_benchmark"] is False  # equal, not greater
    assert result["beats_naive"] is False
    assert result["cost_basis"].startswith("caller-supplied flat")


def test_backtest_flat_cost_known_answer():
    # cost_fraction 0.01 on the t=0 trade only: cost 100, investable 9900.
    # 49.5 A, 99 B -> equity 10000, 9999, 10048.5, 10147.5, 10345.5.
    result = run_backtest(bars(), "equal_weight", market="generic",
                          cost_fraction=0.01, rebalance_every=10)
    assert result["equity"] == [10000.0, 9999.0, 10048.5, 10147.5, 10345.5]
    assert result["total_cost"] == pytest.approx(100.0)
    assert result["n_trades"] == 2


def test_backtest_nepse_costs_from_profile():
    result = run_backtest(bars(), "equal_weight", market="nepse",
                          rebalance_every=10)
    # t=0: two 5,000 legs -> 2 * (18 + 0.75 + 25) = 87.5.
    assert result["total_cost"] == pytest.approx(87.5)
    assert "NEPSE sourced schedule" in result["cost_basis"]
    assert result["equity"][0] == 10000.0
    assert result["equity"][-1] < 10450.0  # costs drag vs the cost-free run


def test_backtest_us_without_cost_fraction_raises():
    with pytest.raises(UnknownCostError):
        run_backtest(bars(), "equal_weight", market="us")


def test_backtest_rebalanced_every_bar_trades():
    result = run_backtest(bars(), "equal_weight", market="generic",
                          cost_fraction=0.0, rebalance_every=1)
    assert result["n_rebalances"] == 5
    assert result["n_trades"] > 2
    # Hand-computed final equity for the daily-rebalanced 1/N run.
    assert result["equity"][-1] == pytest.approx(10452.043784504092)


def test_backtest_metrics_are_present_and_sane():
    result = run_backtest(bars(), "equal_weight", market="generic",
                          cost_fraction=0.0, rebalance_every=10)
    metrics = result["metrics"]
    assert metrics["label"] == "strategy"
    assert metrics["annualised_return"] == pytest.approx(
        sum(result["returns"]) / len(result["returns"]) * 252)
    assert metrics["max_drawdown"] == pytest.approx(0.0)
    assert result["benchmark_metrics"]["label"] == "buy_and_hold"
    assert result["naive_metrics"]["label"] == "naive_equal_weight"


def test_backtest_rejects_bad_inputs():
    with pytest.raises(ValueError, match="at least 3 bars"):
        run_backtest({"AAA": [("2026-01-05", 100.0),
                              ("2026-01-06", 101.0)]},
                     "equal_weight", cost_fraction=0.0)
    with pytest.raises(ValueError, match="unknown strategy"):
        run_backtest(bars(), "nope", cost_fraction=0.0)
    with pytest.raises(ValueError, match="unknown market"):
        run_backtest(bars(), "equal_weight", market="atlantis",
                     cost_fraction=0.0)
    with pytest.raises(ValueError):
        run_backtest(bars(), "equal_weight", cost_fraction=-0.01)
    with pytest.raises(ValueError):
        run_backtest(bars(), "equal_weight", cost_fraction=0.0,
                     rebalance_every=0)
    with pytest.raises(ValueError):
        run_backtest(bars(), "equal_weight", cost_fraction=0.0,
                     initial_capital=0.0)


def test_backtest_cost_consuming_portfolio_raises():
    with pytest.raises(ValueError, match="consumed the whole portfolio"):
        run_backtest(bars(), "equal_weight", market="generic",
                     cost_fraction=1.0, rebalance_every=10)


def test_backtest_is_deterministic():
    first = run_backtest(bars(), "min_variance", market="generic",
                         cost_fraction=0.001, rebalance_every=1)
    second = run_backtest(bars(), "min_variance", market="generic",
                          cost_fraction=0.001, rebalance_every=1)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# --- strategies ------------------------------------------------------------

def test_strategy_wrappers_sum_to_one_and_long_only():
    train = {"AAA": A, "BBB": B}
    for fn in (equal_weight_strategy, inverse_volatility_strategy,
               min_variance_strategy, max_sharpe_strategy):
        weights = fn(train, ["AAA", "BBB"])
        assert set(weights) == {"AAA", "BBB"}
        assert sum(weights.values()) == pytest.approx(1.0)
        assert all(w >= 0.0 for w in weights.values())


def test_strategy_fallback_on_short_history():
    train = {"AAA": [100.0, 102.0], "BBB": [50.0, 50.0]}
    assert inverse_volatility_strategy(train, ["AAA", "BBB"]) == {
        "AAA": 0.5, "BBB": 0.5}
    assert min_variance_strategy(train, ["AAA", "BBB"]) == {
        "AAA": 0.5, "BBB": 0.5}
    assert max_sharpe_strategy(train, ["AAA", "BBB"]) == {
        "AAA": 0.5, "BBB": 0.5}


def test_custom_callable_strategy():
    def all_in_a(train_closes, symbols):
        return {"AAA": 1.0, "BBB": 0.0}

    result = run_backtest(bars(), all_in_a, market="generic",
                          cost_fraction=0.0, rebalance_every=10)
    assert result["strategy"] == "custom"
    # All in A from 100 -> 105: final equity 10500.
    assert result["equity"][-1] == pytest.approx(10500.0)


# --- walk-forward ------------------------------------------------------------

def test_walk_forward_known_answer():
    # train_size=3, test_step=1, equal weight, no cost.
    # Fold 1 (origin idx 3): 0.5*(103/101) + 0.5*(51/51) - 1 = 0.00990099.
    # Fold 2 (origin idx 4): 0.5*(105/103) + 0.5*(52/51) - 1 = 0.01951266.
    wf = walk_forward_backtest(bars(), "equal_weight", train_size=3,
                               test_step=1, market="generic",
                               cost_fraction=0.0)
    assert wf["n_folds"] == 2
    assert wf["folds"][0]["fold_return"] == pytest.approx(0.0099009900990099)
    assert wf["folds"][1]["fold_return"] == pytest.approx(0.019512659432705)
    assert wf["mean_fold_return"] == pytest.approx(0.014706824765857)
    assert wf["folds_beating_benchmark"] == 0
    assert wf["folds"][0]["n_test_bars"] == 1


def test_walk_forward_rejects_bad_splits():
    with pytest.raises(ValueError):
        walk_forward_backtest(bars(), "equal_weight", train_size=0,
                              cost_fraction=0.0)
    with pytest.raises(ValueError):
        walk_forward_backtest(bars(), "equal_weight", train_size=5,
                              cost_fraction=0.0)
    with pytest.raises(ValueError):
        walk_forward_backtest(bars(), "equal_weight", train_size=3,
                              test_step=0, cost_fraction=0.0)


def test_walk_forward_lookahead_mutation():
    """Mutating a FUTURE bar must not change earlier folds' weights.

    The last close (index 4) is in fold 2's test window and in no
    training window before fold 2's origin. Changing it must leave
    fold 1 untouched and fold 2's weights untouched (only its
    realised return moves) — if any weight changed, future data
    leaked into training.
    """
    before = walk_forward_backtest(bars(), "min_variance", train_size=3,
                                   test_step=1, market="generic",
                                   cost_fraction=0.0)
    mutated = bars(b=[50.0, 50.0, 51.0, 51.0, 60.0])
    after = walk_forward_backtest(mutated, "min_variance", train_size=3,
                                  test_step=1, market="generic",
                                  cost_fraction=0.0)
    assert after["folds"][0] == before["folds"][0]
    assert after["folds"][1]["weights"] == before["folds"][1]["weights"]
    assert after["folds"][1]["fold_return"] != before["folds"][1]["fold_return"]


def test_walk_forward_strategy_never_sees_future_bars():
    """A spy strategy records how many bars it was shown at each fold;
    it must never see a bar at or after the fold's first test bar."""
    seen = []

    def spy(train_closes, symbols):
        seen.append({s: len(v) for s, v in train_closes.items()})
        return {"AAA": 0.5, "BBB": 0.5}

    walk_forward_backtest(bars(), spy, train_size=3, test_step=1,
                          market="generic", cost_fraction=0.0)
    assert seen == [{"AAA": 3, "BBB": 3}, {"AAA": 4, "BBB": 4}]


def test_backtest_prefix_invariance_under_future_mutation():
    """Changing a bar after index k cannot change equity[:k+1]."""
    before = run_backtest(bars(), "equal_weight", market="generic",
                          cost_fraction=0.0, rebalance_every=10)
    mutated = bars(b=[50.0, 50.0, 51.0, 51.0, 60.0])
    after = run_backtest(mutated, "equal_weight", market="generic",
                         cost_fraction=0.0, rebalance_every=10)
    assert after["equity"][:4] == before["equity"][:4]
    assert after["equity"][4] != before["equity"][4]


# --- bootstrap ---------------------------------------------------------------

RETURNS = [0.01, -0.01, 0.02, -0.02, 0.0, 0.01]


def test_bootstrap_ci_reproducible_and_ordered():
    first = bootstrap_ci(RETURNS, n_bootstrap=200, seed=7)
    second = bootstrap_ci(RETURNS, n_bootstrap=200, seed=7)
    assert first == second
    assert first["lower"] <= first["estimate"] <= first["upper"]
    assert first["estimate"] == pytest.approx(sum(RETURNS) / len(RETURNS))
    assert first["n_usable"] == 200
    assert first["seed"] == 7


def test_bootstrap_ci_sharpe_undefined_on_flat_returns():
    with pytest.raises(ValueError, match="undefined"):
        bootstrap_ci([0.01, 0.01, 0.01, 0.01, 0.01], statistic="sharpe")


def test_bootstrap_ci_rejects_bad_inputs():
    with pytest.raises(ValueError):
        bootstrap_ci(RETURNS, n_bootstrap=5)
    with pytest.raises(ValueError):
        bootstrap_ci(RETURNS, alpha=1.5)
    with pytest.raises(ValueError):
        bootstrap_ci(RETURNS, statistic="median")


def test_bootstrap_numpy_path_matches_pure_path_exactly():
    """Same fixture both ways: bit-identical bounds when numpy is
    present; graceful NumpyUnavailableError (never a silent fallback)
    when it is absent.

    Deliberately NOT pytest.importorskip: a skip would make the test
    count differ between the core-only env (656) and the numpy env
    (658), and the repo's count gate (scripts/check_numbers.py,
    run by the CI quality job with no numpy installed) requires one
    stable count. So the test passes in both envs — proving the
    identical-results half when numpy is there, and the graceful
    degradation half when it is not.
    """
    # Pure-path comparisons run in BOTH envs (so the Sharpe path stays
    # covered without numpy); only the numpy half is skipped when it
    # is absent.
    pure = bootstrap_ci(RETURNS, n_bootstrap=200, seed=7, use_numpy=False)
    pure_sharpe = bootstrap_ci(RETURNS, n_bootstrap=200, seed=3,
                               statistic="sharpe", use_numpy=False)
    if not sb.HAS_NUMPY:
        with pytest.raises(NumpyUnavailableError, match="not installed"):
            bootstrap_ci(RETURNS, n_bootstrap=200, seed=7, use_numpy=True)
        return
    fast = bootstrap_ci(RETURNS, n_bootstrap=200, seed=7, use_numpy=True)
    assert fast["use_numpy"] is True
    for key in ("statistic", "estimate", "lower", "upper", "alpha",
                "n_bootstrap", "n_usable", "n", "seed"):
        assert fast[key] == pure[key], key
    fast_sharpe = bootstrap_ci(RETURNS, n_bootstrap=200, seed=3,
                               statistic="sharpe", use_numpy=True)
    assert fast_sharpe["estimate"] == pure_sharpe["estimate"]
    assert fast_sharpe["lower"] == pure_sharpe["lower"]
    assert fast_sharpe["upper"] == pure_sharpe["upper"]


def test_period_returns_numpy_path_matches_pure_path_exactly():
    """Bit-identical returns both ways when numpy is present;
    graceful error when absent (same no-skip reason as the
    bootstrap test above — the count gate needs one stable count)."""
    if not sb.HAS_NUMPY:
        with pytest.raises(NumpyUnavailableError, match="not installed"):
            period_returns(bars(), use_numpy=True)
        return
    assert period_returns(bars(), use_numpy=True) == period_returns(bars())


def test_numpy_unavailable_raises_instead_of_silently_falling_back(monkeypatch):
    monkeypatch.setattr(sb, "HAS_NUMPY", False)
    with pytest.raises(NumpyUnavailableError, match="not installed"):
        bootstrap_ci(RETURNS, use_numpy=True)
    with pytest.raises(NumpyUnavailableError, match="not installed"):
        period_returns(bars(), use_numpy=True)


# --- tear sheet ----------------------------------------------------------------

def test_tear_sheet_self_contained_and_deterministic():
    result = run_backtest(bars(), "equal_weight", market="generic",
                          cost_fraction=0.0, rebalance_every=10)
    wf = walk_forward_backtest(bars(), "equal_weight", train_size=3,
                               test_step=1, market="generic",
                               cost_fraction=0.0)
    boot = bootstrap_ci(result["returns"], n_bootstrap=100, seed=1)
    first = tear_sheet(result, walk_forward=wf, bootstrap=boot)
    second = tear_sheet(result, walk_forward=wf, bootstrap=boot)
    assert first == second  # byte-identical for the same inputs
    assert "<svg" in first and "<table" in first
    assert "equal_weight" in first
    assert "caller-supplied flat" in first
    assert "Walk-forward" in first
    assert "Bootstrap confidence interval" in first
    # No external references of any kind.
    assert "http://" not in first and "https://" not in first
    assert "src=" not in first and "href=" not in first
    # No raw price series: the normalised chart carries no closes.
    assert "Research only" in first


def test_tear_sheet_without_optional_sections():
    result = run_backtest(bars(), "equal_weight", market="generic",
                          cost_fraction=0.0, rebalance_every=10)
    sheet = tear_sheet(result)
    assert "Walk-forward" not in sheet
    assert "Bootstrap" not in sheet
    assert "http://" not in sheet and "https://" not in sheet


def test_tear_sheet_rejects_bad_result():
    with pytest.raises(ValueError):
        tear_sheet({"not": "a result"})
