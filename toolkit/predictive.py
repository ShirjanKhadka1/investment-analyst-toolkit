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
from .forecast import (
    linear_trend_forecast,
    moving_average_forecast,
    naive_forecast,
    walk_forward_evaluate,
)


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


def _ensemble_equal(train):
    """Equal-weight mean of the member models on the training window only.

    This is the walk-forward-safe ensemble candidate: at each walk-forward
    step it uses only series[:t], never the test point. It differs from
    ensemble_forecast(), whose inverse-MAE weighting is a point-forecast
    helper and is NOT walk-forward evaluated."""
    train = validate_series("train", train, min_length=1)
    points = [fn(train) for fn in _models().values()]
    return sum(points) / len(points)


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


def compare_models_walk_forward(series, train_size, include_ensemble=False):
    """Walk-forward model-comparison report.

    Runs every available forecaster (naive + the models in _models())
    through the SAME walk-forward split, ranks them by out-of-sample
    MAE, and says plainly whether any model beat naive.

    include_ensemble (default False, preserving the original four-model
    ranking and agreement with auto_select_model): when True, adds
    'ensemble_equal' — the equal-weight mean of the member models,
    recomputed from the training window only at each walk-forward step,
    so it is judged with no lookahead. The ensemble is a candidate like
    any other: if it loses to naive, the report says so.

    Returns a dict with:
      train_size, n_test, best_model, best_mae, naive_mae,
      beats_naive (True only if the best MAE is strictly below naive's
        MAE — the same strict rule as auto_select_model),
      naive_wins (True when no model strictly beats naive, i.e. naive
        is best or tied — a reported result, never hidden),
      ranking (one entry per model, sorted by MAE ascending, each with
        mae, rmse, interval_coverage, n_test and its own beats_naive),
      results (the raw per-model dict from walk_forward_evaluate),
      note (a plain-language statement of the outcome).

    Research only: a lower past out-of-sample MAE is not a guarantee
    of future accuracy, and this report is not a buy/sell/hold signal.
    Raises on a too-short series or a train_size that leaves no test
    set, instead of guessing."""
    series = validate_series("series", series, min_length=10)
    if not 0 < train_size < len(series):
        raise ValueError("train_size must leave a non-empty test set")
    models = dict(_models())
    if include_ensemble:
        models["ensemble_equal"] = _ensemble_equal
    results = walk_forward_evaluate(series, models, train_size)
    ranking = [{"model": name, "mae": r["mae"], "rmse": r["rmse"],
                "interval_coverage": r["interval_coverage"], "n_test": r["n_test"],
                "beats_naive": r.get("beats_naive_mae", False)}
               for name, r in results.items()]
    ranking.sort(key=lambda r: r["mae"])
    best = ranking[0]
    naive_mae = results["naive"]["mae"]
    beats_naive = best["mae"] < naive_mae
    naive_wins = not beats_naive
    if naive_wins:
        note = ("Naive wins: no model beat the naive forecast out of sample "
                "on this split; that result is reported, not hidden.")
    else:
        note = ("Best model beat naive out of sample on this split; past "
                "out-of-sample MAE is not a guarantee of future accuracy.")
    return {"train_size": train_size, "n_test": len(series) - train_size,
            "best_model": best["model"], "best_mae": best["mae"],
            "naive_mae": naive_mae, "beats_naive": beats_naive,
            "naive_wins": naive_wins, "ranking": ranking,
            "results": results, "note": note}


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
    mean return. Returns indices into the series, with the return value.

    Prices must be positive: a zero or negative price previously
    produced a division-by-zero or a meaningless return.
    """
    series = validate_series("series", series, min_length=5)
    if any(p <= 0 for p in series):
        raise ValueError("series prices must all be positive for anomaly_flags")
    rets = [series[i] / series[i - 1] - 1 for i in range(1, len(series))]
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
    if sd == 0:
        return []
    return [{"index": i + 1, "return": rets[i], "z": (rets[i] - mean) / sd}
            for i in range(len(rets)) if abs((rets[i] - mean) / sd) > z]


def volatility_regime(series, window=20):
    """High/low volatility regime: rolling return std vs its own median.
    'high' if the latest window std is above the median window std.

    Convention, stated: rolling stds use the population denominator
    (len(window)), unlike performance._stdev's sample denominator
    (n - 1). The regime is a within-series comparison, so the choice
    moves the median and the latest std together; do not compare
    these std values directly against performance volatilities.
    Prices must be positive (returns are price ratios).
    Tie rule (Phase 4): 'high' only when the latest std exceeds the
    median by more than a 1e-9 relative tolerance. Without it, a
    perfectly flat-volatility series flipped to 'high' on floating-
    point noise (~1e-14 relative) in the window stds — proven by a
    failing test before this fix. Real volatility gaps are orders
    of magnitude larger than 1e-9, so this changes no real reading.
    """
    series = validate_series("series", series, min_length=window + 2)
    if any(p <= 0 for p in series):
        raise ValueError("series prices must all be positive for volatility_regime")
    rets = [series[i] / series[i - 1] - 1 for i in range(1, len(series))]
    windows = []
    for i in range(len(rets) - window + 1):
        w = rets[i:i + window]
        m = sum(w) / len(w)
        windows.append(math.sqrt(sum((x - m) ** 2 for x in w) / len(w)))
    if not windows:
        raise ValueError("not enough data for a volatility window")
    med = sorted(windows)[len(windows) // 2]
    return {"regime": "high" if windows[-1] > med * (1.0 + 1e-9) else "low",
            "latest_std": windows[-1], "median_std": med}
