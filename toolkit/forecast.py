"""Walk-forward forecasting — zero dependencies, pure Python.

Rules that make this honest (the Job 2 lesson):
1. WALK-FORWARD ONLY. A model is fit on data up to time t and judged on
   t+1. Fitting on the whole series and reporting in-sample fit is not
   a forecast and is never presented as one.
2. NAIVE BENCHMARK ALWAYS. Every model must beat the naive forecast
   (next value = last value) out of sample, or it is not an improvement.
3. INTERVALS, NOT POINTS. A point forecast without a prediction interval
   (from training residuals) is incomplete.
4. LOOKAHEAD AUDIT. walk_forward_evaluate() asserts the training window
   ends before the test point on every step. If that assertion ever
   fails, the evaluation is invalid, not the data.
"""
from __future__ import annotations

import math

from .checks import validate_series


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


def naive_forecast(train):
    """Next value = last observed value. The benchmark every model must beat."""
    train = validate_series("train", train, min_length=1)
    if not train:
        raise ValueError("empty training series")
    return train[-1]


def moving_average_forecast(train, window=5):
    train = validate_series("train", train, min_length=1)
    if not train:
        raise ValueError("empty training series")
    if window < 1:
        raise ValueError("window must be >= 1")
    return _mean(train[-window:])


def linear_trend_forecast(train):
    """Least-squares trend on the time index, forecast one step ahead.

    OLS slope/intercept on x = 0..n-1; forecast at x = n.
    """
    train = validate_series("train", train, min_length=2)
    if len(train) < 2:
        raise ValueError("need at least 2 points for a trend")
    n = len(train)
    xs = list(range(n))
    mx, my = _mean(xs), _mean(train)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, train))
    den = sum((x - mx) ** 2 for x in xs)
    slope = num / den if den else 0.0
    intercept = my - slope * mx
    return intercept + slope * n


def _residual_std(train, forecast_fn):
    """Residual std from one-step-ahead fits inside the training window."""
    if len(train) < 3:
        return 0.0
    residuals = []
    for t in range(2, len(train)):
        try:
            residuals.append(train[t] - forecast_fn(train[:t]))
        except ValueError:
            continue
    return _stdev(residuals)


def forecast_with_interval(train, forecast_fn):
    """(point, lower, upper) with a 95% interval from training residuals."""
    point = forecast_fn(train)
    sd = _residual_std(train, forecast_fn)
    return point, point - 1.96 * sd, point + 1.96 * sd


def _mae(actual, predicted):
    return _mean([abs(a - p) for a, p in zip(actual, predicted)])


def _rmse(actual, predicted):
    return math.sqrt(_mean([(a - p) ** 2 for a, p in zip(actual, predicted)]))


def walk_forward_evaluate(series, models, train_size, horizon=1, volatility_regime=None):
    """Walk-forward evaluation. Returns per-model MAE/RMSE + interval coverage.

    models: dict name -> forecast function taking a training list.
    On every step, the model sees ONLY series[:t] and is judged on
    series[t:t+horizon]. The lookahead audit is structural: the slice
    passed to the model ends at t, by construction.

    volatility_regime: optional documented label, 'high' or 'low'
    (or None). It is recorded verbatim on every model's result dict
    and changes no metric — the intended source is
    predictive.volatility_regime()['regime'], computed by the caller
    from the same series. A regime label is a description of the
    training window, not a forecast: if high-regime windows do not
    show larger out-of-sample errors, the label must not be read as
    a risk warning with predictive content (falsifier).
    """
    _validate_regime(volatility_regime)
    if horizon != 1:
        raise ValueError("this version evaluates one-step-ahead only (horizon=1)")
    if not 0 < train_size < len(series):
        raise ValueError("train_size must leave a non-empty test set")
    results = {}
    for name, fn in models.items():
        actual, predicted = [], []
        covered, total = 0, 0
        for t in range(train_size, len(series)):
            train = series[:t]  # lookahead audit: train ends at t, test is series[t]
            assert len(train) == t
            point, lo, hi = forecast_with_interval(train, fn)
            actual.append(series[t])
            predicted.append(point)
            total += 1
            if lo <= series[t] <= hi:
                covered += 1
        results[name] = {
            "mae": _mae(actual, predicted),
            "rmse": _rmse(actual, predicted),
            "interval_coverage": covered / total if total else 0.0,
            "n_test": total,
            "volatility_regime": volatility_regime,
        }
    # Naive benchmark check, in the results, not in prose.
    if "naive" in results:
        naive_mae = results["naive"]["mae"]
        for name, r in results.items():
            r["beats_naive_mae"] = r["mae"] < naive_mae
    return results
