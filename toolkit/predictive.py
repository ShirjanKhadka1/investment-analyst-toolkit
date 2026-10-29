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


# ---------------------------------------------------------------------------
# Phase 10 — new forecasters, multi-horizon evaluation, intervals,
# adaptive weighting, drift flags. All stdlib, all walk-forward safe:
# every forecaster below is a pure function of its training window and
# the horizon requested. No test-period statistic ever enters training.
# ---------------------------------------------------------------------------

def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def ar1_forecast(train, horizon=1):
    """AR(1) by OLS on lagged values: y_t = c + phi * y_{t-1} + e_t.

    phi is estimated by ordinary least squares on (y_{t-1}, y_t) pairs
    inside the training window only. Stationarity guard: |phi| is
    clamped to 0.99, so iterated multi-step forecasts cannot explode
    on a near-unit-root window. If the lagged series has zero variance
    (denominator 0, e.g. a constant window), it falls back to naive
    (last value) — a stated fallback, not a silent repair.
    Multi-step forecasts iterate the recursion horizon times.
    """
    train = validate_series("train", train, min_length=3)
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    xs = train[:-1]
    ys = train[1:]
    mx, my = _mean(xs), _mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return train[-1]
    phi = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    phi = max(-0.99, min(0.99, phi))
    c = my - phi * mx
    value = train[-1]
    for _ in range(horizon):
        value = c + phi * value
    return value


def holt_forecast(train, horizon=1, alpha=0.3, beta=0.1, damping=1.0):
    """Holt double exponential smoothing: level + trend.

    level_t = alpha * y_t + (1 - alpha) * (level_{t-1} + trend_{t-1})
    trend_t = beta * (level_t - level_{t-1}) + (1 - beta) * trend_{t-1}
    Forecast h steps ahead: level + trend * sum_{i=1..h} damping^i.
    damping = 1.0 is plain Holt; damping < 1 (e.g. 0.9) damps the trend,
    which is the honest choice on a series whose trend may not persist.
    Initialised level = y_0, trend = y_1 - y_0 (documented, standard).
    """
    train = validate_series("train", train, min_length=2)
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    if not 0 < alpha <= 1 or not 0 <= beta <= 1 or not 0 < damping <= 1:
        raise ValueError("alpha in (0, 1], beta in [0, 1], damping in (0, 1]")
    level, trend = train[0], train[1] - train[0]
    for x in train[1:]:
        prev_level = level
        level = alpha * x + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
    if damping == 1.0:
        return level + horizon * trend
    return level + trend * damping * (1 - damping ** horizon) / (1 - damping)


def drift_forecast(train, horizon=1, window=None):
    """Drift method: last value plus horizon * mean recent change.

    mean change is taken over the last `window` differences (all
    differences when window is None). Unlike OLS trend it does not
    weight old observations; unlike naive it does not assume zero
    change. On a level shift it overshoots until the shift leaves
    the window — that failure mode is documented, not hidden.
    """
    train = validate_series("train", train, min_length=2)
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    diffs = [train[i] - train[i - 1] for i in range(1, len(train))]
    if window is not None:
        if window < 1:
            raise ValueError("window must be >= 1")
        diffs = diffs[-window:]
    return train[-1] + horizon * _mean(diffs)


def _linear_trend_h(train, horizon):
    """OLS trend forecast h steps ahead (x = n + h - 1)."""
    train = validate_series("train", train, min_length=2)
    n = len(train)
    xs = list(range(n))
    mx, my = _mean(xs), _mean(train)
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, train)) / den if den else 0.0
    return (my - slope * mx) + slope * (n + horizon - 1)


def _new_models():
    """Phase 10 forecasters as (train, horizon) functions."""
    return {
        "ar1": ar1_forecast,
        "holt": holt_forecast,
        "holt_damped": lambda t, h: holt_forecast(t, h, damping=0.9),
        "drift": drift_forecast,
    }


def _horizon_models(include_new_models=False, include_ensemble=False,
                    include_adaptive=False):
    """All candidate models as (train, horizon) -> point forecast."""
    models = {
        "naive": lambda t, h: t[-1],
        "moving_average_5": lambda t, h: _mean(t[-5:]),
        "linear_trend": _linear_trend_h,
        "exp_smoothing_0.3": lambda t, h: exponential_smoothing_forecast(t, alpha=0.3),
    }
    if include_new_models:
        models.update(_new_models())
    if include_ensemble:
        _members = list(models.values())  # snapshot BEFORE insertion: the
        # ensemble must average its members, never itself (recursion).
        def _ens(t, h, _members=_members):
            return _mean([fn(t, h) for fn in _members])
        models["ensemble_equal"] = _ens
    if include_adaptive:
        models["ensemble_adaptive"] = lambda t, h: adaptive_ensemble_forecast(t, h)["forecast"]
    return models


def _walk_forward_horizon(series, models_h, train_size, horizon):
    """Walk-forward MAE/RMSE per model at one fixed horizon.

    Origins t run from train_size to len(series) - horizon inclusive.
    At each origin the model receives ONLY series[:t] and is judged on
    series[t + horizon - 1]. Returns per-model dicts with mae, rmse,
    n_test, horizon, and beats_naive_mae vs the naive MAE at the same
    horizon (naive at horizon h is still the last value, so its MAE
    grows with h on trending series and stays small on flat ones).
    """
    series = validate_series("series", series, min_length=10)
    if not 0 < train_size < len(series):
        raise ValueError("train_size must leave a non-empty test set")
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    if train_size > len(series) - horizon:
        raise ValueError("train_size leaves no complete horizon window in the test set")
    results = {}
    for name, fn in models_h.items():
        actual, predicted = [], []
        for t in range(train_size, len(series) - horizon + 1):
            actual.append(series[t + horizon - 1])
            predicted.append(fn(series[:t], horizon))
        mae = _mean([abs(a - p) for a, p in zip(actual, predicted)])
        rmse = math.sqrt(_mean([(a - p) ** 2 for a, p in zip(actual, predicted)])) if actual else 0.0
        results[name] = {"mae": mae, "rmse": rmse, "n_test": len(actual), "horizon": horizon}
    if "naive" in results:
        naive_mae = results["naive"]["mae"]
        for r in results.values():
            r["beats_naive_mae"] = r["mae"] < naive_mae
    return results


def walk_forward_forecasts(series, models_h, train_size, horizon):
    """Per-model (origin, forecast, actual) triples at one horizon.

    This is the lookahead-audit surface: a forecast at origin t is
    computed from series[:t] only, so perturbing series after
    t + horizon - 1 cannot change it. Tests use exactly that property.
    """
    series = validate_series("series", series, min_length=10)
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    out = {}
    for name, fn in models_h.items():
        rows = []
        for t in range(train_size, len(series) - horizon + 1):
            rows.append((t, fn(series[:t], horizon), series[t + horizon - 1]))
        out[name] = rows
    return out


def empirical_interval(residuals, alpha=0.10):
    """Empirical (lower, upper) residual quantiles at alpha/2, 1 - alpha/2.

    Linear-interpolation quantiles over the given residuals. A prediction
    interval is then point + (lo, hi). Coverage on the residual sample
    itself is >= 1 - alpha by construction up to interpolation; coverage
    on new data is NOT guaranteed and is never claimed as such.
    """
    if not residuals:
        raise ValueError("residuals must be non-empty")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)")
    ordered = sorted(residuals)

    def _quantile(q):
        pos = q * (len(ordered) - 1)
        lo_i = math.floor(pos)
        hi_i = math.ceil(pos)
        if lo_i == hi_i:
            return ordered[lo_i]
        frac = pos - lo_i
        return ordered[lo_i] * (1 - frac) + ordered[hi_i] * frac

    return _quantile(alpha / 2), _quantile(1 - alpha / 2)


def prediction_interval(train, forecast_fn_h, horizon=1, alpha=0.10):
    """Point + interval from walk-forward residuals inside the training window.

    Residuals come from internal walk-forward origins inside train only
    (never from the point being forecast). With >= 5 residuals the
    interval uses empirical_interval(); with fewer it falls back to a
    normal approximation (point +/- 1.96 * residual std for alpha=0.10
    the multiplier is 1.645 — the fallback uses the alpha-matched
    normal quantile approximated as 1.645 at 0.10, 1.96 at 0.05, else
    1.645) and the method label says so. Intervals are stated with
    their method and n_residuals, never as guaranteed ranges.
    """
    train = validate_series("train", train, min_length=10)
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    point = forecast_fn_h(train, horizon)
    inner_start = max(5, len(train) // 2)
    residuals = []
    for t in range(inner_start, len(train) - horizon + 1):
        try:
            residuals.append(train[t + horizon - 1] - forecast_fn_h(train[:t], horizon))
        except ValueError:
            continue
    if len(residuals) >= 5:
        lo_q, hi_q = empirical_interval(residuals, alpha=alpha)
        return {"point": point, "lower": point + lo_q, "upper": point + hi_q,
                "method": "empirical", "n_residuals": len(residuals), "alpha": alpha}
    sd = math.sqrt(sum((r - _mean(residuals)) ** 2 for r in residuals) / (len(residuals) - 1)) if len(residuals) > 1 else 0.0
    z = 1.96 if alpha <= 0.05 else 1.645
    return {"point": point, "lower": point - z * sd, "upper": point + z * sd,
            "method": "normal-approx (few residuals)", "n_residuals": len(residuals), "alpha": alpha}


def _adaptive_weights(train, horizon=1, window=5):
    """Inverse-recent-MAE weights from training-window data only.

    Internal walk-forward origins inside train (capped at the last 20
    origins) score each member model; weights use each model's MAE over
    its most recent `window` origins, w = 1 / (mae + 1e-9), normalised
    to sum to 1. If train is too short (< 12 points) or no internal
    origin exists, weights fall back to equal and the label says so.
    A zero-MAE member gets (numerically) almost all the weight — that
    is the rule, and it is visible in the returned weights, not hidden.
    """
    train = validate_series("train", train, min_length=2)
    members = dict(_models())
    members.update(_new_models())
    member_h = {
        "naive": lambda t, h: t[-1],
        "moving_average_5": lambda t, h: _mean(t[-5:]),
        "linear_trend": _linear_trend_h,
        "exp_smoothing_0.3": lambda t, h: exponential_smoothing_forecast(t, alpha=0.3),
        "ar1": ar1_forecast,
        "holt": holt_forecast,
        "holt_damped": lambda t, h: holt_forecast(t, h, damping=0.9),
        "drift": drift_forecast,
    }
    if len(train) < 12:
        n = len(member_h)
        return {k: 1.0 / n for k in member_h}, "fallback: equal (train too short)"
    origins = list(range(max(10, len(train) // 2), len(train) - horizon + 1))[-20:]
    if not origins:
        n = len(member_h)
        return {k: 1.0 / n for k in member_h}, "fallback: equal (no internal origin)"
    recent_mae = {}
    for name, fn in member_h.items():
        errs = []
        for t in origins[-window:]:
            try:
                errs.append(abs(train[t + horizon - 1] - fn(train[:t], horizon)))
            except ValueError:
                continue
        recent_mae[name] = _mean(errs) if errs else float("inf")
    raw = {k: (1.0 / (v + 1e-9) if v != float("inf") else 0.0) for k, v in recent_mae.items()}
    total = sum(raw.values())
    if total <= 0:
        n = len(member_h)
        return {k: 1.0 / n for k in member_h}, "fallback: equal (no finite MAE)"
    return {k: v / total for k, v in raw.items()}, "inverse-recent-MAE"


def adaptive_ensemble_forecast(train, horizon=1, window=5):
    """Adaptive ensemble point forecast + its weights and weighting label.

    Weights come from _adaptive_weights() — recent internal
    walk-forward MAE on the training window only. The forecast is the
    weighted mean of the member models' horizon forecasts. Like every
    ensemble here it is a candidate, judged out of sample, never a
    claim of superiority on its own.
    """
    train = validate_series("train", train, min_length=2)
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    weights, weighting = _adaptive_weights(train, horizon, window=window)
    member_h = {
        "naive": lambda t, h: t[-1],
        "moving_average_5": lambda t, h: _mean(t[-5:]),
        "linear_trend": _linear_trend_h,
        "exp_smoothing_0.3": lambda t, h: exponential_smoothing_forecast(t, alpha=0.3),
        "ar1": ar1_forecast,
        "holt": holt_forecast,
        "holt_damped": lambda t, h: holt_forecast(t, h, damping=0.9),
        "drift": drift_forecast,
    }
    points = {k: fn(train, horizon) for k, fn in member_h.items()}
    value = sum(points[k] * weights[k] for k in points)
    return {"forecast": value, "weights": weights, "weighting": weighting, "points": points}


def drift_flag(early_errors, recent_errors):
    """Drift flag from two error samples (absolute errors, same model).

    drift = True only when both samples have >= 3 errors AND the recent
    mean absolute error is >= 1.5x the early mean. Otherwise drift is
    False with a reason (insufficient data, or no growth). This flags
    drift in words; it never silently switches models — a flagged model
    stays flagged until a human or a new evaluation clears it.
    """
    early = [abs(float(e)) for e in early_errors]
    recent = [abs(float(e)) for e in recent_errors]
    if len(early) < 3 or len(recent) < 3:
        return {"drift": False, "reason": "insufficient data (need >= 3 errors per period)",
                "early_mae": _mean(early) if early else None,
                "recent_mae": _mean(recent) if recent else None, "ratio": None}
    early_mae, recent_mae = _mean(early), _mean(recent)
    ratio = (recent_mae / early_mae) if early_mae > 0 else (float("inf") if recent_mae > 0 else 1.0)
    if ratio >= 1.5:
        return {"drift": True, "reason": "recent MAE >= 1.5x early MAE",
                "early_mae": early_mae, "recent_mae": recent_mae, "ratio": ratio}
    return {"drift": False, "reason": "recent MAE below 1.5x early MAE",
            "early_mae": early_mae, "recent_mae": recent_mae, "ratio": ratio}


def walk_forward_drift(series, model_fn_h, train_size, horizon=1):
    """Split walk-forward absolute errors into first/second half and flag drift.

    model_fn_h is a (train, horizon) forecast function. Errors come from
    walk_forward_forecasts() at the given horizon, so the flag is
    out-of-sample by construction.
    """
    rows = walk_forward_forecasts(series, {"model": model_fn_h}, train_size, horizon)["model"]
    errors = [abs(actual - forecast) for _, forecast, actual in rows]
    half = len(errors) // 2
    flag = drift_flag(errors[:half], errors[half:])
    flag["n_early"], flag["n_recent"] = half, len(errors) - half
    flag["horizon"] = horizon
    return flag


def compare_models_walk_forward(series, train_size, include_ensemble=False,
                                 horizons=(1,), include_new_models=False,
                                 include_adaptive=False):
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
    horizons = tuple(horizons)
    if not horizons or any(not isinstance(h, int) or h < 1 for h in horizons):
        raise ValueError("horizons must be a non-empty tuple of positive ints")
    if len(set(horizons)) != len(horizons):
        raise ValueError("horizons must not repeat")
    models = dict(_models())
    if include_ensemble:
        models["ensemble_equal"] = _ensemble_equal
    results = walk_forward_evaluate(series, models, train_size)
    # Multi-horizon evaluation (Phase 10). The h=1 ranking above is kept
    # byte-for-byte compatible: by_horizon is additional, never a
    # replacement, so existing callers and tests see identical output
    # when horizons == (1,) and no new models are included.
    models_h = _horizon_models(include_new_models=include_new_models,
                               include_ensemble=include_ensemble,
                               include_adaptive=include_adaptive)
    by_horizon = {}
    for h in horizons:
        res_h = _walk_forward_horizon(series, models_h, train_size, h)
        ranking_h = [{"model": name, "mae": r["mae"], "rmse": r["rmse"],
                      "n_test": r["n_test"], "horizon": h,
                      "beats_naive": r.get("beats_naive_mae", False)}
                     for name, r in res_h.items()]
        ranking_h.sort(key=lambda r: r["mae"])
        best_h = ranking_h[0]
        by_horizon[h] = {"best_model": best_h["model"], "best_mae": best_h["mae"],
                         "naive_mae": res_h["naive"]["mae"],
                         "beats_naive": best_h["mae"] < res_h["naive"]["mae"],
                         "naive_wins": not best_h["mae"] < res_h["naive"]["mae"],
                         "ranking": ranking_h, "results": res_h}
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
    out = {"train_size": train_size, "n_test": len(series) - train_size,
           "best_model": best["model"], "best_mae": best["mae"],
           "naive_mae": naive_mae, "beats_naive": beats_naive,
           "naive_wins": naive_wins, "ranking": ranking,
           "results": results, "note": note,
           "horizons": list(horizons), "by_horizon": by_horizon}
    return out


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
