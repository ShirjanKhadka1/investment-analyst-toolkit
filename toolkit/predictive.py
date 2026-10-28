"""Predictive analysis — walk-forward only, model-selected, never point-only.

Models here are baselines, not magic: naive, moving average, linear trend,
and exponential smoothing. auto_select_model() picks the winner by
out-of-sample MAE (never in-sample fit), and ensemble_forecast() averages
models weighted by inverse out-of-sample MAE. If no model beats naive,
the report says so — that is a result, not a failure.
"""
from __future__ import annotations

import math

from .checks import validate_series
from .forecast import (linear_trend_forecast, moving_average_forecast,
                       naive_forecast, walk_forward_evaluate)


def exponential_smoothing_forecast(train, alpha=0.3):
    train = validate_series("train", train, min_length=1)
    if not 0 < alpha <= 1:
        raise ValueError(f"alpha must be in (0, 1], got {alpha}")
    level = train[0]
    for x in train[1:]:
        level = alpha * x + (1 - alpha) * level
    return level


def _models():
    return {
        "naive": naive_forecast,
        "moving_average_5": lambda t: moving_average_forecast(t, window=5),
        "linear_trend": linear_trend_forecast,
        "exp_smoothing_0.3": lambda t: exponential_smoothing_forecast(t, alpha=0.3),
    }


def auto_select_model(series, train_size):
    """Walk-forward MAE for every model; returns best model + full table.
    Raises on a too-short series instead of guessing."""
    series = validate_series("series", series, min_length=10)
    if not 0 < train_size < len(series):
        raise ValueError("train_size must leave a non-empty test set")
    results = walk_forward_evaluate(series, _models(), train_size)
    best = min(results, key=lambda k: results[k]["mae"])
    return {"best_model": best, "best_mae": results[best]["mae"],
            "beats_naive": results[best]["mae"] < results["naive"]["mae"],
            "results": results}


def ensemble_forecast(train, series_for_weights=None, train_size=None):
    """Inverse-MAE-weighted average of the models. If no weights series is
    given, falls back to a plain mean — and says so in the return value."""
    train = validate_series("train", train, min_length=2)
    fns = _models()
    points = {name: fn(train) for name, fn in fns.items()}
    if series_for_weights is not None and train_size:
        res = walk_forward_evaluate(series_for_weights, fns, train_size)
        weights = {k: 1.0 / v["mae"] if v["mae"] > 0 else 0.0 for k, v in res.items()}
        total = sum(weights.values())
        if total > 0:
            value = sum(points[k] * weights[k] / total for k in points)
            return {"forecast": value, "weighting": "inverse_walk_forward_mae", "weights": weights, "points": points}
    value = sum(points.values()) / len(points)
    return {"forecast": value, "weighting": "equal (no walk-forward weights supplied)", "points": points}


def anomaly_flags(series, z=3.0):
    """Indices where a one-step return is |z| standard deviations from the
    mean return. Returns indices into the series, with the return value."""
    series = validate_series("series", series, min_length=5)
    rets = [series[i] / series[i - 1] - 1 for i in range(1, len(series))]
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
    if sd == 0:
        return []
    return [{"index": i + 1, "return": rets[i], "z": (rets[i] - mean) / sd}
            for i in range(len(rets)) if abs((rets[i] - mean) / sd) > z]


def volatility_regime(series, window=20):
    """High/low volatility regime: rolling return std vs its own median.
    'high' if the latest window std is above the median window std."""
    series = validate_series("series", series, min_length=window + 2)
    rets = [series[i] / series[i - 1] - 1 for i in range(1, len(series))]
    windows = []
    for i in range(len(rets) - window + 1):
        w = rets[i:i + window]
        m = sum(w) / len(w)
        windows.append(math.sqrt(sum((x - m) ** 2 for x in w) / len(w)))
    if not windows:
        raise ValueError("not enough data for a volatility window")
    med = sorted(windows)[len(windows) // 2]
    return {"regime": "high" if windows[-1] > med else "low",
            "latest_std": windows[-1], "median_std": med}
