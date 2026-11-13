"""Portfolio strategy backtester — walk-forward, costs from market profiles.

Builds on what already exists instead of duplicating it: weights come
from ``toolkit.portfolio`` (equal weight, inverse volatility, minimum
variance, max Sharpe), metrics from ``toolkit.performance``, costs from
``toolkit.market_profiles`` (via ``toolkit.nepal_frictions`` for NEPSE),
and the single-asset crossover engine in ``toolkit.backtest`` is left
alone — this module is the portfolio-level companion to it.

Rules, stated so they can be checked:
- Caller-supplied daily bars only. No new data source, no live feed,
  no redistributed raw price series: published outputs are derived
  metrics (equity curves normalised to 100, returns, ratios) — never
  the input closes.
- No look-ahead, ever. At a rebalance on date t the strategy sees
  closes through t only and trades at close t; in walk-forward, fold
  weights come from bars strictly before the fold's first test bar.
- Costs are never silently zero. A market with no sourced fee schedule
  (US, crypto, FX, generic) raises ``UnknownCostError`` unless the
  caller supplies an explicit ``cost_fraction``. What each profile's
  cost includes — and what it leaves out — is carried in every result
  as ``cost_basis`` and printed on the tear sheet.
- NEPSE costs are the sourced schedule (broker slab + SEBON 0.015% +
  DP Rs 25 per scrip per trade). Capital gains tax is NOT charged in
  this backtester: CGT depends on the realised gain of each lot and
  its holding period, which this portfolio-level engine does not
  track — the tear sheet says so instead of inventing a tax number.
- Research only. No order execution, no live trading, no buy/sell
  signal. A strategy that cannot beat the buy-and-hold benchmark and
  the naive 1/N baseline out of sample has not earned its complexity.
- Reproducible: the core engine is deterministic (no randomness at
  all); the bootstrap takes a seed, and the same seed + same inputs
  give byte-identical results (the tear sheet formats every float
  with a fixed format, so the HTML is deterministic too).

Optional numpy extra: the core is standard-library only and keeps the
60-second install. ``requirements-numpy.txt`` adds numpy, used only
for vectorised gathering / elementwise work in the bootstrap and in
``period_returns``. Reductions deliberately use the same
``math.fsum`` order as the pure-Python path, so the numpy path
produces bit-identical results by construction — the test suite
proves it on the same fixture both ways. The pure-Python path is
the default (``use_numpy`` defaults to False everywhere); passing
``use_numpy=True`` without numpy installed raises
``NumpyUnavailableError`` instead of silently falling back.
"""
from __future__ import annotations

import html
import math
import random

from .checks import (
    require_non_negative,
    require_positive,
    validate_price_pairs,
    validate_series,
)
from .market_profiles import get_profile
from .nepal_frictions import (
    DP_CHARGE_FLAT,
    SEBON_FEE_RATE,
    brokerage_cost_for,
    sourced_nepal_frictions,
)
from .performance import performance_report
from .portfolio import (
    equal_weights,
    inverse_volatility_weights,
    max_sharpe_weights,
    min_variance_weights,
    returns_from_prices,
)


def _load_numpy():  # pragma: no cover - import outcome is env-dependent
    """Optional numpy import, isolated so mypy stays clean with numpy
    installed and without it (see [mypy-numpy] in mypy.ini). Returns
    the numpy module, or None when it is not installed. The whole
    function is excluded from coverage measurement: its two branches
    are taken in different environments (numpy present vs absent),
    so no single-env coverage run can cover both — the behaviour is
    instead proven by the HAS_NUMPY / NumpyUnavailableError tests,
    which pass in both envs."""
    try:
        import numpy as np

        return np
    except ImportError:  # pragma: no cover - import outcome is env-dependent
        return None


_np = _load_numpy()
HAS_NUMPY = _np is not None

__all__ = [
    "HAS_NUMPY",
    "STRATEGIES",
    "NumpyUnavailableError",
    "UnknownCostError",
    "bootstrap_ci",
    "buy_and_hold_strategy",
    "equal_weight_strategy",
    "inverse_volatility_strategy",
    "max_sharpe_strategy",
    "min_variance_strategy",
    "period_returns",
    "run_backtest",
    "tear_sheet",
    "trade_cost",
    "validate_bars",
    "walk_forward_backtest",
]


class UnknownCostError(ValueError):
    """Raised when a market has no sourced fee schedule and the caller
    supplied no explicit cost_fraction — costs are never assumed zero."""


class NumpyUnavailableError(ValueError):
    """Raised when use_numpy=True but numpy is not installed."""


# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------

def validate_bars(bars):
    """Normalise caller-supplied daily bars.

    ``bars`` maps symbol -> [(ISO date, close), ...]. Every symbol must
    share the same date list (aligned bars), dates strictly increasing,
    closes positive. Returns ``{symbol: [(date, close), ...]}`` with
    ISO dates and float closes. Any violation raises — a broken series
    is never silently repaired, because repair would invent data.
    """
    if not isinstance(bars, dict) or not bars:
        raise ValueError("bars must be a non-empty dict of symbol -> price pairs")
    normalised = {}
    dates = None
    for symbol, pairs in bars.items():
        if not isinstance(symbol, str) or not symbol:
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
        cleaned = validate_price_pairs(f"bars[{symbol!r}]", pairs)
        symbol_dates = [d for d, _ in cleaned]
        if dates is None:
            dates = symbol_dates
        elif symbol_dates != dates:
            raise ValueError(
                f"bars[{symbol!r}] dates differ from the first symbol's "
                "dates — all symbols must share the same (aligned) dates")
        normalised[symbol] = cleaned
    return normalised


def _closes_of(bars, symbol):
    return [close for _, close in bars[symbol]]


def period_returns(bars, use_numpy=False):
    """Per-asset simple period returns from validated bars.

    Returns ``{symbol: [r_1, ..., r_{n-1}]}`` where
    ``r_t = close_t / close_{t-1} - 1``. The numpy path does the same
    elementwise IEEE operations as the pure path (division and
    subtraction are correctly rounded per element), so the two paths
    return bit-identical lists; the pure path is the default.
    """
    bars = validate_bars(bars)
    # Numpy path (excluded from core coverage — see the pragma on the
    # `if` line): exercised by the identical-results tests in numpy
    # envs (CI `full` job).
    if use_numpy:  # pragma: no cover
        _require_numpy()
        out = {}
        for symbol, pairs in bars.items():
            closes = _np.array([c for _, c in pairs], dtype=float)
            out[symbol] = (closes[1:] / closes[:-1] - 1.0).tolist()
        return out
    return {s: returns_from_prices(_closes_of(bars, s)) for s in bars}


# ---------------------------------------------------------------------------
# Costs from market profiles
# ---------------------------------------------------------------------------

def trade_cost(market, trade_value, side="buy"):
    """Cost of one trade, in the market's currency, from its profile.

    Returns ``(cost, basis)`` where ``basis`` is a plain-language note
    of what the cost includes. ``side`` is "buy" or "sell".
    Raises ``UnknownCostError`` when the profile carries no sourced fee
    schedule (US, crypto, FX, generic) — the caller must then pass an
    explicit ``cost_fraction`` to the backtest instead of getting a
    silent zero. Raises ``ValueError`` for an unknown market key (via
    ``get_profile``) or a non-positive trade value.
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    require_positive("trade_value", trade_value)
    profile = get_profile(market)
    if market == "nepse":
        frictions = sourced_nepal_frictions()
        cost = (brokerage_cost_for(trade_value, frictions)
                + trade_value * SEBON_FEE_RATE + DP_CHARGE_FLAT)
        basis = ("NEPSE sourced schedule 2026-10-28: broker slab + SEBON "
                 "0.015% + DP Rs 25 per scrip per trade; CGT excluded "
                 "(lot gain / holding period not tracked)")
        return cost, basis
    if market == "india":
        # STT 0.1% on buy AND sell (delivery) + NSE txn charge ~0.003%.
        cost = trade_value * (0.001 + 0.00003)
        basis = ("India: STT 0.1% (delivery, both sides) + NSE txn charge "
                 "~0.003%; brokerage broker-dependent, excluded")
        return cost, basis
    if market == "uk":
        # SDRT 0.5% on electronic Main Market purchases only.
        cost = trade_value * 0.005 if side == "buy" else 0.0
        basis = ("UK: SDRT 0.5% on buys only; brokerage excluded "
                 "(broker-dependent)")
        return cost, basis
    raise UnknownCostError(
        f"no sourced fee schedule for market {market!r} "
        f"({profile.name}): pass an explicit cost_fraction to the "
        "backtest instead of assuming zero cost")


def _trade_cost_for(market, trade_value, side, cost_fraction):
    """One trade's cost: explicit caller fraction wins, else profile."""
    if cost_fraction is not None:
        require_non_negative("cost_fraction", cost_fraction)
        return trade_value * cost_fraction, (
            f"caller-supplied flat cost_fraction={cost_fraction} of "
            f"traded value ({side})")
    return trade_cost(market, trade_value, side)


# ---------------------------------------------------------------------------
# Strategies: (train_closes, symbols) -> {symbol: weight}
# ---------------------------------------------------------------------------

def _long_only(weights):
    """Long-only version of a weight list: negatives to zero, renormalise.

    The optimisers in portfolio.py are unconstrained and may short;
    this backtester is long-only, so shorts are removed explicitly
    (and the tear sheet / strategy name says long-only) rather than
    silently carried as a short book. Raises if nothing survives.
    """
    pos = [max(0.0, w) for w in weights]
    total = sum(pos)
    if total <= 0.0:
        raise ValueError("no positive weights — long-only portfolio is empty")
    return [w / total for w in pos]


def _return_matrix(train_closes, symbols):
    """{symbol: [closes]} -> [[returns per asset]] (same order as symbols)."""
    return [returns_from_prices(train_closes[s]) for s in symbols]


def _fallback(train_closes, symbols):
    """Equal weights: the stated fallback when history is too short
    (< 2 returns per asset) or an optimiser fails on the window."""
    return {s: w for s, w in zip(symbols, equal_weights(len(symbols)))}


def equal_weight_strategy(train_closes, symbols):
    """1/N: the naive baseline every optimiser must beat out of sample."""
    return _fallback(train_closes, symbols)


def buy_and_hold_strategy(train_closes, symbols):
    """Buy-and-hold benchmark: equal weights, bought once, never traded.

    Implemented as a strategy function so the benchmark runs through
    the same engine; ``run_backtest`` buys it at the first bar and
    never rebalances it, whatever ``rebalance_every`` says.
    """
    return _fallback(train_closes, symbols)


def inverse_volatility_strategy(train_closes, symbols):
    """Weights proportional to 1/volatility (portfolio.py), long-only."""
    try:
        matrix = _return_matrix(train_closes, symbols)
        if any(len(r) < 2 for r in matrix):
            return _fallback(train_closes, symbols)
        weights = _long_only(inverse_volatility_weights(matrix))
    except ValueError:
        return _fallback(train_closes, symbols)
    return {s: w for s, w in zip(symbols, weights)}


def min_variance_strategy(train_closes, symbols):
    """Minimum-variance weights (portfolio.py), long-only."""
    try:
        matrix = _return_matrix(train_closes, symbols)
        if any(len(r) < 2 for r in matrix):
            return _fallback(train_closes, symbols)
        weights = _long_only(min_variance_weights(matrix))
    except ValueError:
        return _fallback(train_closes, symbols)
    return {s: w for s, w in zip(symbols, weights)}


def max_sharpe_strategy(train_closes, symbols):
    """Max-Sharpe weights (portfolio.py), long-only."""
    try:
        matrix = _return_matrix(train_closes, symbols)
        if any(len(r) < 2 for r in matrix):
            return _fallback(train_closes, symbols)
        weights = _long_only(max_sharpe_weights(matrix))
    except ValueError:
        return _fallback(train_closes, symbols)
    return {s: w for s, w in zip(symbols, weights)}


STRATEGIES = {
    "equal_weight": equal_weight_strategy,
    "buy_and_hold": buy_and_hold_strategy,
    "inverse_volatility": inverse_volatility_strategy,
    "min_variance": min_variance_strategy,
    "max_sharpe": max_sharpe_strategy,
}


def _resolve_strategy(strategy):
    """A registry name or a callable -> (name, callable)."""
    if isinstance(strategy, str):
        try:
            return strategy, STRATEGIES[strategy]
        except KeyError:
            raise ValueError(
                f"unknown strategy {strategy!r}; known: "
                f"{', '.join(sorted(STRATEGIES))}") from None
    if not callable(strategy):
        raise TypeError("strategy must be a registry name or a callable")
    return "custom", strategy


def _weights_at(strategy_fn, bars, symbols, end_index):
    """Target weights at bar ``end_index``: the strategy sees closes
    through ``end_index`` only (bars[:end_index + 1]) — never later."""
    train = {s: _closes_of(bars, s)[:end_index + 1] for s in symbols}
    weights = strategy_fn(train, symbols)
    if set(weights) != set(symbols):
        raise ValueError("strategy must return one weight per symbol")
    total = sum(weights.values())
    if total <= 0.0:
        raise ValueError("strategy weights must sum to a positive total")
    return {s: float(weights[s]) / total for s in symbols}


# ---------------------------------------------------------------------------
# Portfolio-level backtest
# ---------------------------------------------------------------------------

def _equity_returns(equity):
    return [equity[t] / equity[t - 1] - 1.0 for t in range(1, len(equity))]


def _run_one(bars, symbols, strategy_fn, market, cost_fraction,
             initial_capital, rebalance_every, rebalance=True):
    """One portfolio run through the engine.

    Fully invested, no cash buffer: at each rebalance date t the
    portfolio is re-set to the target weights at close t (weights
    from closes through t only), costs are deducted from the portfolio
    value first, and the remainder is invested. Between rebalances
    shares are constant. ``rebalance=False`` buys once at t=0 and
    never trades again (the buy-and-hold benchmark).

    Returns (equity, dates, n_trades, total_cost, total_traded,
    weights_history) where equity[0] is the starting capital (the
    first trade's cost is embedded in equity[1] via the shares set
    at close 0 — see module docstring).
    """
    dates = [d for d, _ in bars[symbols[0]]]
    n = len(dates)
    closes = {s: _closes_of(bars, s) for s in symbols}
    equity = [initial_capital]
    shares = {s: 0.0 for s in symbols}
    n_trades = 0
    total_cost = 0.0
    total_traded = 0.0
    weights_history = []
    cost_basis = None
    for t in range(n):
        is_rebalance = (t == 0) or (rebalance and t % rebalance_every == 0)
        if not is_rebalance:
            equity.append(sum(shares[s] * closes[s][t] for s in symbols))
            continue
        value = initial_capital if t == 0 else sum(
            shares[s] * closes[s][t] for s in symbols)
        if t > 0:
            equity.append(value)  # pre-trade value at this rebalance date
        target = _weights_at(strategy_fn, bars, symbols, t)
        weights_history.append({"date": dates[t], "weights": target})
        traded = 0.0
        cost = 0.0
        for s in symbols:
            current_value = shares[s] * closes[s][t] if t > 0 else 0.0
            target_value = target[s] * value
            leg = abs(target_value - current_value)
            if leg <= 0.0:
                continue
            traded += leg
            side = "buy" if target_value >= current_value else "sell"
            leg_cost, basis = _trade_cost_for(
                market, leg, side, cost_fraction)
            cost += leg_cost
            cost_basis = basis
            n_trades += 1
        total_cost += cost
        total_traded += traded
        investable = value - cost
        if investable <= 0.0:
            raise ValueError(
                "trade costs consumed the whole portfolio at "
                f"{dates[t]} — lower the cost or raise the capital")
        for s in symbols:
            shares[s] = target[s] * investable / closes[s][t]
        if t == 0:
            equity[0] = initial_capital
        else:
            equity[-1] = investable
    return {
        "equity": equity, "dates": dates, "n_trades": n_trades,
        "total_cost": total_cost, "total_traded": total_traded,
        "weights_history": weights_history, "cost_basis": cost_basis,
    }


def run_backtest(bars, strategy, market="generic", cost_fraction=None,
                 initial_capital=10000.0, rebalance_every=1,
                 risk_free=0.0, periods_per_year=252):
    """Backtest one strategy on caller-supplied daily bars.

    ``bars``: {symbol: [(ISO date, close), ...]}, aligned dates.
    ``strategy``: a registry name (``equal_weight``,
    ``inverse_volatility``, ``min_variance``, ``max_sharpe``) or a
    callable ``(train_closes, symbols) -> {symbol: weight}``.
    ``market``: a market-profiles key whose fee schedule prices
    trades; ``cost_fraction`` (flat fraction of traded value) wins
    over the profile when given. ``rebalance_every`` is in bars
    (1 = every bar). Returns a JSON-serialisable dict: equity curve,
    period returns, metrics for the strategy, the buy-and-hold
    benchmark and the naive 1/N rebalanced baseline on the same bars
    and costs, trade counts, total cost and turnover, and the
    ``cost_basis`` note. No randomness: same inputs give identical
    outputs.
    """
    bars = validate_bars(bars)
    symbols = sorted(bars)
    n = len(bars[symbols[0]])
    if n < 3:
        raise ValueError(f"need at least 3 bars, got {n}")
    require_positive("initial_capital", initial_capital)
    if not isinstance(rebalance_every, int) or isinstance(rebalance_every, bool) \
            or rebalance_every < 1:
        raise ValueError(
            f"rebalance_every must be a positive int, got {rebalance_every!r}")
    require_non_negative("risk_free", risk_free)
    if cost_fraction is not None:
        require_non_negative("cost_fraction", cost_fraction)
    get_profile(market)  # unknown market key raises here, before trading
    name, strategy_fn = _resolve_strategy(strategy)
    run = _run_one(bars, symbols, strategy_fn, market, cost_fraction,
                   initial_capital, rebalance_every, rebalance=True)
    bench = _run_one(bars, symbols, buy_and_hold_strategy, market,
                     cost_fraction, initial_capital, rebalance_every,
                     rebalance=False)
    naive = _run_one(bars, symbols, equal_weight_strategy, market,
                     cost_fraction, initial_capital, rebalance_every,
                     rebalance=True)
    equity = run["equity"]
    returns = _equity_returns(equity)
    bench_returns = _equity_returns(bench["equity"])
    naive_returns = _equity_returns(naive["equity"])
    metrics = performance_report(returns, equity, risk_free,
                                 periods_per_year, label="strategy")
    bench_metrics = performance_report(bench_returns, bench["equity"],
                                       risk_free, periods_per_year,
                                       label="buy_and_hold")
    naive_metrics = performance_report(naive_returns, naive["equity"],
                                       risk_free, periods_per_year,
                                       label="naive_equal_weight")
    mean_equity = sum(equity) / len(equity)
    return {
        "strategy": name,
        "market": market,
        "cost_basis": run["cost_basis"],
        "symbols": symbols,
        "dates": run["dates"],
        "n_bars": n,
        "initial_capital": initial_capital,
        "rebalance_every": rebalance_every,
        "equity": equity,
        "returns": returns,
        "benchmark_equity": bench["equity"],
        "benchmark_returns": bench_returns,
        "naive_equity": naive["equity"],
        "naive_returns": naive_returns,
        "metrics": metrics,
        "benchmark_metrics": bench_metrics,
        "naive_metrics": naive_metrics,
        "total_return": equity[-1] / equity[0] - 1.0,
        "benchmark_total_return": bench["equity"][-1] / bench["equity"][0] - 1.0,
        "naive_total_return": naive["equity"][-1] / naive["equity"][0] - 1.0,
        "n_trades": run["n_trades"],
        "n_rebalances": len(run["weights_history"]),
        "total_cost": run["total_cost"],
        "total_traded": run["total_traded"],
        "turnover": (run["total_traded"] / mean_equity
                     if mean_equity > 0 else 0.0),
        "beats_benchmark": equity[-1] > bench["equity"][-1],
        "beats_naive": equity[-1] > naive["equity"][-1],
        "weights_history": run["weights_history"],
        "note": ("Research only — not investment advice. Fully invested, "
                 "long-only; costs per cost_basis; past performance is "
                 "not a guarantee of future results."),
    }


# ---------------------------------------------------------------------------
# Walk-forward evaluation (no look-ahead by construction)
# ---------------------------------------------------------------------------

def walk_forward_backtest(bars, strategy, train_size, test_step=20,
                          market="generic", cost_fraction=None,
                          risk_free=0.0, periods_per_year=252):
    """Walk-forward evaluation of one strategy.

    Origins advance by ``test_step`` from ``train_size``. At each
    origin t the strategy sees bars[:t] only (closes before the
    fold's first test bar) and the resulting weights are held over
    the next ``test_step`` bars (the final fold may be shorter).
    Each fold is scored as a growth factor from a normalised starting
    value of 1.0, with the fold's entry trade priced by the same
    cost model, against the buy-and-hold equal-weight benchmark
    over the identical window. A fold's weights can never depend on
    its own test bars — the look-ahead test proves it by mutation.
    """
    bars = validate_bars(bars)
    symbols = sorted(bars)
    n = len(bars[symbols[0]])
    if not 0 < train_size < n:
        raise ValueError("train_size must leave a non-empty test set")
    if not isinstance(test_step, int) or isinstance(test_step, bool) \
            or test_step < 1:
        raise ValueError(f"test_step must be a positive int, got {test_step!r}")
    if cost_fraction is not None:
        require_non_negative("cost_fraction", cost_fraction)
    get_profile(market)
    name, strategy_fn = _resolve_strategy(strategy)
    closes = {s: _closes_of(bars, s) for s in symbols}
    dates = [d for d, _ in bars[symbols[0]]]
    folds = []
    cost_basis = None
    for origin in range(train_size, n, test_step):
        end = min(origin + test_step, n)
        if end <= origin:
            break
        # Weights from bars[:origin] only — the fold's test bars
        # (origin..end-1) are never visible to the strategy.
        weights = _weights_at(strategy_fn, bars, symbols, origin - 1)
        fold_return, bench_return, basis = _fold_returns(
            closes, symbols, weights, origin - 1, end,
            market, cost_fraction)
        cost_basis = basis
        folds.append({
            "origin": dates[origin - 1],
            "origin_index": origin - 1,
            "test_start": dates[origin],
            "test_end": dates[end - 1],
            "n_test_bars": end - origin,
            "weights": weights,
            "fold_return": fold_return,
            "benchmark_return": bench_return,
            "beats_benchmark": fold_return > bench_return,
        })
    if not folds:
        raise ValueError("train_size leaves no test fold")
    fold_returns = [f["fold_return"] for f in folds]
    bench_returns = [f["benchmark_return"] for f in folds]
    return {
        "strategy": name,
        "market": market,
        "cost_basis": cost_basis,
        "symbols": symbols,
        "train_size": train_size,
        "test_step": test_step,
        "n_folds": len(folds),
        "folds": folds,
        "mean_fold_return": sum(fold_returns) / len(fold_returns),
        "mean_benchmark_return": sum(bench_returns) / len(bench_returns),
        "folds_beating_benchmark": sum(
            1 for f in folds if f["beats_benchmark"]),
        "note": ("Out-of-sample only: every fold's weights come from "
                 "bars before the fold. Past walk-forward returns are "
                 "not a guarantee of future results."),
    }


def _fold_returns(closes, symbols, weights, start, end, market, cost_fraction):
    """Growth over bars start+1..end-1 from a normalised value of 1.0.

    ``start`` is the execution close (weights trade here); the fold
    return is measured from the value after paying the entry cost.
    The benchmark is equal weights over the same window, same costs.
    Returns (fold_return, benchmark_return, cost_basis).
    """
    bench_weights = {s: 1.0 / len(symbols) for s in symbols}
    out = []
    basis = None
    for target in (weights, bench_weights):
        cost = 0.0
        for s in symbols:
            leg = target[s] * 1.0  # whole portfolio is traded at entry
            leg_cost, basis = _trade_cost_for(market, leg, "buy", cost_fraction)
            cost += leg_cost
        investable = 1.0 - cost
        if investable <= 0.0:
            raise ValueError("trade costs consumed the whole fold at entry")
        value = sum(target[s] * investable * closes[s][end - 1] / closes[s][start]
                    for s in symbols)
        out.append(value - 1.0)
    return out[0], out[1], basis


# ---------------------------------------------------------------------------
# Bootstrap confidence intervals
# ---------------------------------------------------------------------------

def _require_numpy():
    if not HAS_NUMPY:
        raise NumpyUnavailableError(
            "numpy is not installed — install the optional extra "
            "(requirements-numpy.txt) or run with use_numpy=False")


def _quantile(sorted_values, q):
    """Linear-interpolation quantile (same convention as
    predictive.empirical_interval)."""
    n = len(sorted_values)
    pos = q * (n - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[lo]
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _statistic(sample, statistic, periods_per_year):
    """Mean period return, or annualised Sharpe (None when undefined)."""
    if statistic == "mean":
        return math.fsum(sample) / len(sample)
    if statistic == "sharpe":
        mean = math.fsum(sample) / len(sample)
        var = math.fsum((x - mean) ** 2 for x in sample) / (len(sample) - 1)
        sd = math.sqrt(var) if var > 0 else 0.0
        if sd == 0.0:
            return None
        return mean / sd * math.sqrt(periods_per_year)
    raise ValueError(f"unknown statistic {statistic!r}; use 'mean' or 'sharpe'")


def bootstrap_ci(returns, n_bootstrap=1000, alpha=0.05, seed=0,
                 statistic="mean", periods_per_year=252, use_numpy=False):
    """Percentile bootstrap confidence interval for a returns statistic.

    Resamples the given period returns with replacement (``n_bootstrap``
    resamples, ``seed`` drives a ``random.Random`` stream) and reports
    the percentile interval of the resampled statistic. The interval
    describes sampling variation of the statistic on THESE returns —
    it is not a prediction interval for future returns.

    ``use_numpy=True`` gathers resamples through numpy (the optional
    extra) but reduces with the same ``math.fsum`` order as the pure
    path, so both paths return bit-identical bounds. The pure path is
    the default. ``use_numpy=True`` without numpy installed raises
    ``NumpyUnavailableError``.
    """
    returns = validate_series("returns", returns, min_length=2)
    if not isinstance(n_bootstrap, int) or isinstance(n_bootstrap, bool) \
            or n_bootstrap < 10:
        raise ValueError(
            f"n_bootstrap must be an int >= 10, got {n_bootstrap!r}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha!r}")
    if statistic not in ("mean", "sharpe"):
        raise ValueError(
            f"unknown statistic {statistic!r}; use 'mean' or 'sharpe'")
    if use_numpy:
        _require_numpy()
    n = len(returns)
    # One shared index stream: both paths resample identically, so the
    # only difference between them is how values are gathered.
    rng = random.Random(seed)
    index_sets = [[rng.randrange(n) for _ in range(n)]
                  for _ in range(n_bootstrap)]
    estimate = _statistic(returns, statistic, periods_per_year)
    if estimate is None:
        raise ValueError("statistic is undefined on these returns")
    stats = []
    # Numpy path (excluded from core coverage, as in period_returns).
    if use_numpy:  # pragma: no cover
        arr = _np.array(returns, dtype=float)
        for idx in index_sets:
            value = _statistic(arr[idx].tolist(), statistic, periods_per_year)
            if value is not None:
                stats.append(value)
    else:
        for idx in index_sets:
            value = _statistic([returns[i] for i in idx], statistic,
                               periods_per_year)
            if value is not None:
                stats.append(value)
    if len(stats) < 10:
        raise ValueError(
            f"too few usable bootstrap resamples ({len(stats)}) — the "
            "statistic is undefined on most resamples")
    ordered = sorted(stats)
    return {
        "statistic": statistic,
        "estimate": estimate,
        "lower": _quantile(ordered, alpha / 2),
        "upper": _quantile(ordered, 1 - alpha / 2),
        "alpha": alpha,
        "n_bootstrap": n_bootstrap,
        "n_usable": len(stats),
        "n": n,
        "seed": seed,
        "use_numpy": bool(use_numpy),
        "method": ("percentile bootstrap: resample period returns with "
                   "replacement, same seed => same interval"),
    }


# ---------------------------------------------------------------------------
# Tear sheet (self-contained HTML, no external references)
# ---------------------------------------------------------------------------

def _fmt(value, places=4):
    if value is None:
        return "n/a"
    return f"{value:.{places}f}"


def _pct(value, places=2):
    if value is None:
        return "n/a"
    return f"{value * 100:.{places}f}%"


def _svg_curve(equity, bench_equity, width=640, height=200):
    """Normalised equity curves (start = 100) as an inline SVG polyline.

    Derived values only — the input closes never appear. No external
    references: the SVG is inline, no <img>, no links.
    """
    def _points(values):
        top = max(values) * 1.02
        bottom = min(values) * 0.98
        span = (top - bottom) or 1.0
        pts = []
        for i, v in enumerate(values):
            x = i * (width - 1) / max(len(values) - 1, 1)
            y = height - 1 - (v - bottom) / span * (height - 2)
            pts.append(f"{x:.1f},{y:.1f}")
        return " ".join(pts)

    norm = [v / equity[0] * 100.0 for v in equity]
    bench = [v / bench_equity[0] * 100.0 for v in bench_equity]
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} '
        f'{height}" role="img" '
        'aria-label="Normalised equity curve, strategy vs benchmark">'
        f'<polyline points="{_points(bench)}" fill="none" stroke="#888888" '
        'stroke-width="1.5" stroke-dasharray="5,3" />'
        f'<polyline points="{_points(norm)}" fill="none" stroke="#1a5fb4" '
        'stroke-width="2" /></svg>')


def tear_sheet(result, walk_forward=None, bootstrap=None,
               title="Strategy backtest — tear sheet"):
    """Self-contained HTML tear sheet for a ``run_backtest`` result.

    Inline style only, no external references (no links, images, CSS
    or scripts from outside the document) and no raw price series:
    the chart is the normalised equity curve (start = 100), a derived
    metric. Deterministic: same result dict gives the same HTML
    byte for byte. ``walk_forward`` and ``bootstrap`` are the dicts
    from ``walk_forward_backtest`` / ``bootstrap_ci`` when available;
    their sections are omitted when not supplied.
    """
    if not isinstance(result, dict) or "equity" not in result:
        raise ValueError("result must be a run_backtest() result dict")
    esc = html.escape
    metrics = result["metrics"]
    bench = result["benchmark_metrics"]
    naive = result["naive_metrics"]
    rows = [
        ("Total return", _pct(result["total_return"]),
         _pct(result["benchmark_total_return"]),
         _pct(result["naive_total_return"])),
        ("Annualised return", _pct(metrics["annualised_return"]),
         _pct(bench["annualised_return"]), _pct(naive["annualised_return"])),
        ("Annualised volatility", _pct(metrics["annualised_volatility"]),
         _pct(bench["annualised_volatility"]),
         _pct(naive["annualised_volatility"])),
        ("Sharpe", _fmt(metrics["sharpe"]), _fmt(bench["sharpe"]),
         _fmt(naive["sharpe"])),
        ("Sortino", _fmt(metrics["sortino"]), _fmt(bench["sortino"]),
         _fmt(naive["sortino"])),
        ("Max drawdown", _pct(metrics["max_drawdown"]),
         _pct(bench["max_drawdown"]), _pct(naive["max_drawdown"])),
        ("CVaR (5%)", _pct(metrics["cvar_5pct"]), _pct(bench["cvar_5pct"]),
         _pct(naive["cvar_5pct"])),
    ]
    table_rows = "".join(
        f"<tr><td>{esc(name)}</td><td>{s}</td><td>{b}</td><td>{nv}</td></tr>"
        for name, s, b, nv in rows)
    wf_section = ""
    if walk_forward is not None:
        wf_rows = "".join(
            f"<tr><td>{esc(f['origin'])}</td>"
            f"<td>{esc(f['test_start'])} to {esc(f['test_end'])}</td>"
            f"<td>{_pct(f['fold_return'])}</td>"
            f"<td>{_pct(f['benchmark_return'])}</td>"
            f"<td>{'yes' if f['beats_benchmark'] else 'no'}</td></tr>"
            for f in walk_forward["folds"])
        wf_section = (
            "<h2>Walk-forward</h2>"
            f"<p>Train size {walk_forward['train_size']} bars, test step "
            f"{walk_forward['test_step']} bars, "
            f"{walk_forward['n_folds']} folds; mean fold return "
            f"{_pct(walk_forward['mean_fold_return'])} vs benchmark "
            f"{_pct(walk_forward['mean_benchmark_return'])}; "
            f"{walk_forward['folds_beating_benchmark']} of "
            f"{walk_forward['n_folds']} folds beat the benchmark.</p>"
            "<table><tr><th>Origin</th><th>Test window</th>"
            "<th>Fold return</th><th>Benchmark</th>"
            "<th>Beat benchmark</th></tr>"
            f"{wf_rows}</table>")
    boot_section = ""
    if bootstrap is not None:
        boot_section = (
            "<h2>Bootstrap confidence interval</h2>"
            f"<p>{esc(bootstrap['statistic'])} estimate "
            f"{_fmt(bootstrap['estimate'])}; "
            f"{int((1 - bootstrap['alpha']) * 100)}% interval "
            f"[{_fmt(bootstrap['lower'])}, {_fmt(bootstrap['upper'])}] "
            f"from {bootstrap['n_bootstrap']} resamples "
            f"(seed {bootstrap['seed']}, n={bootstrap['n']}). "
            "Sampling variation of the statistic on these returns — "
            "not a prediction interval for future returns.</p>")
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{esc(title)}</title>\n"
        "<style>body{font-family:sans-serif;max-width:760px;margin:2em auto;"
        "padding:0 1em;color:#222}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ccc;padding:4px 8px;text-align:left}"
        "th{background:#f0f0f0}h1{font-size:1.4em}h2{font-size:1.1em}"
        ".note{color:#555;font-size:0.9em}</style>\n</head>\n<body>\n"
        f"<h1>{esc(title)}</h1>\n"
        f"<p>Strategy: <b>{esc(str(result['strategy']))}</b> (long-only) | "
        f"Market: {esc(str(result['market']))} | Symbols: "
        f"{esc(', '.join(result['symbols']))} | Bars: {result['n_bars']} "
        f"({esc(result['dates'][0])} to {esc(result['dates'][-1])}) | "
        f"Initial capital: {result['initial_capital']} | Rebalance every "
        f"{result['rebalance_every']} bar(s) | Trades: "
        f"{result['n_trades']} | Total cost: "
        f"{_fmt(result['total_cost'])} | Turnover: "
        f"{_fmt(result['turnover'])}</p>\n"
        f"<p>Cost basis: {esc(str(result['cost_basis']))}</p>\n"
        f"{_svg_curve(result['equity'], result['benchmark_equity'])}\n"
        "<p class=\"note\">Chart: normalised equity (start = 100). Solid "
        "blue = strategy, dashed grey = buy-and-hold benchmark.</p>\n"
        "<h2>Metrics</h2>\n"
        "<table><tr><th>Metric</th><th>Strategy</th><th>Benchmark "
        "(buy-and-hold)</th><th>Naive (1/N rebalanced)</th></tr>"
        f"{table_rows}</table>\n"
        f"<p>Beats benchmark: "
        f"{'yes' if result['beats_benchmark'] else 'no'}; beats naive: "
        f"{'yes' if result['beats_naive'] else 'no'}.</p>\n"
        f"{wf_section}\n{boot_section}\n"
        "<p class=\"note\">Research only — not investment advice. Past "
        "performance is not a guarantee of future results. No order "
        "execution. Costs per the cost basis above; NEPSE capital "
        "gains tax is excluded (lot gain / holding period not "
        "tracked).</p>\n</body>\n</html>\n")
