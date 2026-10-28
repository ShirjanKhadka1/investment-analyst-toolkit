"""Performance and risk — zero dependencies, pure Python.

Every metric is reported with its train/test split when it is used for
a claim. An in-sample Sharpe is a description, not a prediction.
"""
from __future__ import annotations

import math


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def sharpe_ratio(returns, risk_free=0.0, periods_per_year=252):
    """Annualised Sharpe: mean(excess) / stdev(excess) * sqrt(periods)."""
    excess = [r - risk_free for r in returns]
    sd = _stdev(excess)
    if sd == 0.0:
        raise ValueError("zero return volatility — Sharpe undefined")
    return _mean(excess) / sd * math.sqrt(periods_per_year)


def sortino_ratio(returns, risk_free=0.0, periods_per_year=252):
    """Annualised Sortino: downside deviation only in the denominator."""
    excess = [r - risk_free for r in returns]
    downside = [min(0.0, e) for e in excess]
    dd = math.sqrt(sum(d * d for d in downside) / len(downside)) if downside else 0.0
    if dd == 0.0:
        raise ValueError("no downside deviation — Sortino undefined")
    return _mean(excess) / dd * math.sqrt(periods_per_year)


def max_drawdown(prices):
    """Maximum peak-to-trough fall, as a positive fraction (0.30 = 30%)."""
    if len(prices) < 2:
        raise ValueError("need at least 2 prices")
    peak = prices[0]
    worst = 0.0
    for p in prices:
        peak = max(peak, p)
        worst = max(worst, (peak - p) / peak if peak else 0.0)
    return worst


def cvar(returns, alpha=0.05):
    """Historical CVaR (expected shortfall): mean of the worst alpha fraction.

    alpha=0.05 means the mean of the worst 5% of returns. Returns a
    negative number for a loss tail (e.g. -0.04 = -4% average worst-day).
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    if not returns:
        raise ValueError("no returns")
    ordered = sorted(returns)
    k = max(1, int(math.ceil(alpha * len(ordered))))
    return _mean(ordered[:k])


def calmar_ratio(returns, prices, periods_per_year=252):
    """Annualised return / max drawdown. Raises if drawdown is zero."""
    dd = max_drawdown(prices)
    if dd == 0.0:
        raise ValueError("zero drawdown — Calmar undefined")
    if len(returns) < 2:
        raise ValueError("need at least 2 returns")
    ann = _mean(returns) * periods_per_year
    return ann / dd


def annualised_return(returns, periods_per_year=252):
    return _mean(returns) * periods_per_year


def annualised_volatility(returns, periods_per_year=252):
    return _stdev(returns) * math.sqrt(periods_per_year)


def train_test_split(series, train_size):
    """Split a series into train (first train_size) and test (rest).

    This split is the honesty device: fit on train, judge on test, never
    the other way round. Raises if the split leaves an empty side.
    """
    if not 0 < train_size < len(series):
        raise ValueError("train_size must leave a non-empty test set")
    return series[:train_size], series[train_size:]


def performance_report(returns, prices, risk_free=0.0, periods_per_year=252, label=""):
    """One dict of metrics, with the label of the split it was computed on."""
    return {
        "label": label,
        "annualised_return": annualised_return(returns, periods_per_year),
        "annualised_volatility": annualised_volatility(returns, periods_per_year),
        "sharpe": sharpe_ratio(returns, risk_free, periods_per_year),
        "sortino": sortino_ratio(returns, risk_free, periods_per_year),
        "max_drawdown": max_drawdown(prices),
        "cvar_5pct": cvar(returns, 0.05),
    }
