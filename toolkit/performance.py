"""Performance and risk — zero dependencies, pure Python.

Every metric is reported with its train/test split when it is used for
a claim. An in-sample Sharpe is a description, not a prediction.
"""
from __future__ import annotations

import math

from .checks import require_finite, require_positive, validate_series


def _validate_periods(periods_per_year):
    require_positive("periods_per_year", periods_per_year)
    return periods_per_year


def _validate_regime(regime):
    if regime is not None and regime not in ("high", "low"):
        raise ValueError(f"volatility_regime must be 'high', 'low' or None, got {regime!r}")
    return regime


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def sharpe_ratio(returns, risk_free=0.0, periods_per_year=252):
    """Annualised Sharpe: mean(excess) / stdev(excess) * sqrt(periods).

    Sharpe (1966; 1994 revision): excess return per unit of total
    volatility. risk_free is per-period, on the same frequency as
    returns. Sample stdev (n - 1) in the denominator.
    """
    returns = validate_series("returns", returns, min_length=1)
    require_finite("risk_free", risk_free)
    _validate_periods(periods_per_year)
    excess = [r - risk_free for r in returns]
    sd = _stdev(excess)
    if sd == 0.0:
        raise ValueError("zero return volatility — Sharpe undefined")
    return _mean(excess) / sd * math.sqrt(periods_per_year)


def sortino_ratio(returns, risk_free=0.0, periods_per_year=252):
    """Annualised Sortino: downside deviation only in the denominator.

    Sortino & Price: downside deviation = sqrt(mean(min(0, excess)^2))
    over ALL observations (not just downside ones).
    """
    returns = validate_series("returns", returns, min_length=1)
    require_finite("risk_free", risk_free)
    _validate_periods(periods_per_year)
    excess = [r - risk_free for r in returns]
    downside = [min(0.0, e) for e in excess]
    dd = math.sqrt(sum(d * d for d in downside) / len(downside)) if downside else 0.0
    if dd == 0.0:
        raise ValueError("no downside deviation — Sortino undefined")
    return _mean(excess) / dd * math.sqrt(periods_per_year)


def max_drawdown(prices):
    """Maximum peak-to-trough fall, as a positive fraction (0.30 = 30%).

    Prices must be positive and finite: a negative price previously
    produced a drawdown above 1.0 (e.g. 1.5), which is not a fall
    any holder could experience.
    """
    prices = validate_series("prices", prices, min_length=2)
    if any(p <= 0 for p in prices):
        raise ValueError("prices must all be positive for max_drawdown")
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
    returns = validate_series("returns", returns, min_length=1)
    if not returns:
        raise ValueError("no returns")
    ordered = sorted(returns)
    k = max(1, int(math.ceil(alpha * len(ordered))))
    return _mean(ordered[:k])


def calmar_ratio(returns, prices, periods_per_year=252):
    """Annualised return / max drawdown. Raises if drawdown is zero.

    Note: annualised return here is the arithmetic mean x periods,
    not a geometrically compounded CAGR — a stated convention of
    this module, not a general Calmar definition.
    """
    _validate_periods(periods_per_year)
    returns = validate_series("returns", returns, min_length=2)
    dd = max_drawdown(prices)
    if dd == 0.0:
        raise ValueError("zero drawdown — Calmar undefined")
    if len(returns) < 2:
        raise ValueError("need at least 2 returns")
    ann = _mean(returns) * periods_per_year
    return ann / dd


def annualised_return(returns, periods_per_year=252):
    returns = validate_series("returns", returns, min_length=1)
    _validate_periods(periods_per_year)
    return _mean(returns) * periods_per_year


def annualised_volatility(returns, periods_per_year=252):
    returns = validate_series("returns", returns, min_length=1)
    _validate_periods(periods_per_year)
    return _stdev(returns) * math.sqrt(periods_per_year)


def train_test_split(series, train_size):
    """Split a series into train (first train_size) and test (rest).

    This split is the honesty device: fit on train, judge on test, never
    the other way round. Raises if the split leaves an empty side.
    """
    if not 0 < train_size < len(series):
        raise ValueError("train_size must leave a non-empty test set")
    return series[:train_size], series[train_size:]


def _safe(fn, *args):
    """Edge-case guard for reports: an undefined ratio (e.g. Sortino on a
    series with no downside, Sharpe on zero volatility) becomes None with
    a reason — it never crashes a whole autonomous run."""
    try:
        return fn(*args)
    except ValueError as e:
        return None


def performance_report(returns, prices, risk_free=0.0, periods_per_year=252, label="",
                       volatility_regime=None):
    """One dict of metrics, with the label of the split it was computed on.
    Undefined ratios are None (not a crash, not a made-up number).

    volatility_regime: optional documented label, 'high' or 'low' (or
    None when not supplied). It is a pass-through descriptor only —
    it changes no metric. The intended source is
    predictive.volatility_regime()['regime']; callers must compute
    and pass it explicitly so the report never invents a regime.
    Downside: a 'low' label is not a promise of future calm.
    Falsifier: if high-regime windows do not show larger realised
    moves than low-regime windows out of sample, the label carries
    no predictive content and must not be used as one.
    """
    _validate_regime(volatility_regime)
    return {
        "label": label,
        "volatility_regime": volatility_regime,
        "annualised_return": annualised_return(returns, periods_per_year),
        "annualised_volatility": annualised_volatility(returns, periods_per_year),
        "sharpe": _safe(sharpe_ratio, returns, risk_free, periods_per_year),
        "sortino": _safe(sortino_ratio, returns, risk_free, periods_per_year),
        "max_drawdown": max_drawdown(prices),
        "cvar_5pct": cvar(returns, 0.05),
    }
