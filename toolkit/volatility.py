"""One-day-ahead volatility forecasting: EWMA + GARCH-lite, walk-forward scored.

Stdlib only. No new dependencies.

This module forecasts variance, not direction: at each origin it outputs
a one-day-ahead variance forecast for the next day's squared log
return. It reuses the realised-volatility idea already in the repo
(toolkit/predictive.py volatility_regime labels rolling return std;
toolkit/performance.py annualised_volatility annualises a return std)
but it does not duplicate them: those describe realised volatility
after the fact, this module forecasts it before the fact.

Differences from the existing helpers, stated so the numbers are never
mixed up:

- Returns here are LOG returns, ln(P_t / P_{t-1}); volatility_regime
  uses simple returns. The two are close for small moves and are not
  interchangeable as inputs.
- Realised variance here is the mean of SQUARED log returns over a
  trailing window (zero-mean assumption, the RiskMetrics convention),
  not the demeaned population std volatility_regime uses and not the
  sample std (n - 1) performance.annualised_volatility uses. A
  variance forecast must be comparable to a squared return, so the
  demeaned/sample forms are the wrong target here.
- GARCH-lite is NOT a fitted GARCH(1,1): alpha and beta are fixed
  (0.08 / 0.90, pre-registered in
  docs/volatility-forecast-eval-protocol.md) and only omega is
  anchored to the training-sample variance. No MLE, no optimiser.
  Never describe it as a fitted GARCH.

Evaluation discipline (MODEL RULES): the protocol in
docs/volatility-forecast-eval-protocol.md is pre-registered — models,
parameters, QLIKE scoring, walk-forward split and the decision rule
were fixed before any evaluation ran. A model counts as beating the
naive realised-vol baseline only if its pooled out-of-sample mean
QLIKE is lower AND the 95% paired-bootstrap CI on the mean QLIKE
difference lies entirely below zero; otherwise it is labelled
"no edge". The verdict strings in this module are exactly those two.
Interval coverage (80% / 95%) was added by the protocol amendment
of 2026-11-14 as a separate target check: pooled empirical coverage
of the +/-z * sigma forecast intervals against the nominal levels.
Coverage is reported, never used to rewrite the QLIKE verdict.
"""
from __future__ import annotations

import math
import random

from .checks import validate_series

# Pre-registered protocol constants (docs/volatility-forecast-eval-protocol.md).
EWMA_LAMBDA = 0.94
GARCH_LITE_ALPHA = 0.08
GARCH_LITE_BETA = 0.90
NAIVE_WINDOW = 20
INIT_WINDOW = 20
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261114

# Numerical guard for QLIKE's logarithm, documented in the protocol.
# On real return series the floor never binds.
VARIANCE_FLOOR = 1e-12

# Forecast-interval z-values (standard normal quantiles), fixed by the
# protocol amendment of 2026-11-14: the 80% interval is +/-z_80 * sigma
# with z_80 = Phi^-1(0.90), the 95% interval is +/-z_95 * sigma with
# z_95 = Phi^-1(0.975), under normal log returns. sigma is the square
# root of the model's one-day-ahead variance forecast.
Z_80 = 1.2815515655446004
Z_95 = 1.6448536269514722
COVERAGE_LEVELS = (0.80, 0.95)

MODELS = ("naive", "ewma", "garch_lite")


def log_returns(prices):
    """Log returns ln(P_t / P_{t-1}) for a price series.

    Prices must be positive: a zero or negative price raises instead
    of producing a meaningless return.
    """
    prices = validate_series("prices", prices, min_length=2)
    if any(p <= 0 for p in prices):
        raise ValueError("prices must all be positive for log_returns")
    return [math.log(prices[i] / prices[i - 1])
            for i in range(1, len(prices))]


def _sample_variance(values):
    """Sample variance (n - 1 denominator); needs at least 2 values."""
    n = len(values)
    if n < 2:
        raise ValueError("need at least 2 values for a sample variance")
    mean = sum(values) / n
    return sum((x - mean) ** 2 for x in values) / (n - 1)


def _floor(variance):
    """Variance floor so QLIKE's log is always defined."""
    return max(float(variance), VARIANCE_FLOOR)


def _validate_window(window):
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ValueError(f"window must be a positive integer, got {window!r}")
    return window


def realised_variance(returns, window=NAIVE_WINDOW):
    """Trailing realised variance: mean of squared log returns over
    the last ``window`` returns (zero-mean assumption).

    This is the naive baseline's forecast when carried forward: the
    variance of the recent past, assumed to persist one more day.
    """
    returns = validate_series("returns", returns, min_length=1)
    window = _validate_window(window)
    if len(returns) < window:
        raise ValueError(
            f"need at least {window} returns for realised_variance, "
            f"got {len(returns)}")
    tail = returns[-window:]
    return _floor(sum(r * r for r in tail) / window)


def naive_forecast_variance(returns, window=NAIVE_WINDOW):
    """Baseline forecast: trailing realised variance carried forward."""
    return realised_variance(returns, window=window)


def ewma_forecast_variance(returns, lam=EWMA_LAMBDA, init_window=INIT_WINDOW):
    """EWMA variance forecast for the day after the last return.

    Recursion: sigma^2_t = lam * sigma^2_{t-1} + (1 - lam) * r^2_{t-1},
    initialised with the sample variance of the first ``init_window``
    returns. Each return enters the recursion exactly once, in order,
    so the returned value uses only returns available at the origin.
    """
    returns = validate_series("returns", returns, min_length=2)
    if not 0 < lam < 1:
        raise ValueError(f"lam must be between 0 and 1, got {lam}")
    init_window = _validate_window(init_window)
    if len(returns) < init_window:
        raise ValueError(
            f"need at least {init_window} returns for ewma_forecast_variance, "
            f"got {len(returns)}")
    variance = _sample_variance(returns[:init_window])
    for r in returns[init_window:]:
        variance = lam * variance + (1.0 - lam) * r * r
    return _floor(variance)


def garch_lite_forecast_variance(returns, alpha=GARCH_LITE_ALPHA,
                                 beta=GARCH_LITE_BETA,
                                 init_window=INIT_WINDOW):
    """GARCH-lite variance forecast for the day after the last return.

    Recursion: sigma^2_t = omega + alpha * r^2_{t-1} + beta * sigma^2_{t-1}
    with alpha and beta FIXED (not fitted) and omega anchored to the
    expanding training-sample variance: omega = sigma_bar^2 * (1 - alpha
    - beta), where sigma_bar^2 is the sample variance of ALL returns
    given. The recursion is initialised with the sample variance of
    the first ``init_window`` returns, exactly like EWMA.
    """
    returns = validate_series("returns", returns, min_length=2)
    if alpha < 0 or beta < 0 or alpha + beta >= 1:
        raise ValueError(
            f"need alpha >= 0, beta >= 0 and alpha + beta < 1, "
            f"got alpha={alpha}, beta={beta}")
    init_window = _validate_window(init_window)
    if len(returns) < init_window:
        raise ValueError(
            f"need at least {init_window} returns for "
            f"garch_lite_forecast_variance, got {len(returns)}")
    long_run = _sample_variance(returns)
    omega = long_run * (1.0 - alpha - beta)
    variance = _sample_variance(returns[:init_window])
    for r in returns[init_window:]:
        variance = omega + alpha * r * r + beta * variance
    return _floor(variance)


def forecast_variance(model, returns, **params):
    """One-day-ahead variance forecast from one named model.

    model is "naive", "ewma" or "garch_lite"; params override the
    pre-registered defaults (lam / alpha / beta / window /
    init_window). Callers that want the protocol numbers pass no
    params.
    """
    if model == "naive":
        return naive_forecast_variance(
            returns, window=params.get("window", NAIVE_WINDOW))
    if model == "ewma":
        return ewma_forecast_variance(
            returns, lam=params.get("lam", EWMA_LAMBDA),
            init_window=params.get("init_window", INIT_WINDOW))
    if model == "garch_lite":
        return garch_lite_forecast_variance(
            returns, alpha=params.get("alpha", GARCH_LITE_ALPHA),
            beta=params.get("beta", GARCH_LITE_BETA),
            init_window=params.get("init_window", INIT_WINDOW))
    raise ValueError(f"unknown volatility model {model!r}; "
                     f"expected one of {MODELS}")


def z_value(level):
    """Standard-normal z for a two-sided forecast interval level.

    Only the protocol's two levels are defined: 0.80 -> z ~= 1.28
    (Phi^-1(0.90)) and 0.95 -> z ~= 1.645 (Phi^-1(0.975)). Any other
    level raises instead of silently interpolating a quantile.
    """
    if level == 0.80:
        return Z_80
    if level == 0.95:
        return Z_95
    raise ValueError(
        f"unsupported coverage level {level!r}; expected 0.80 or 0.95")


def inside_interval(realised_return, forecast_variance, level):
    """True if the realised return falls inside the model's forecast
    interval for ``level``: |r| <= z * sqrt(forecast variance).

    The interval is centred at zero (log returns are treated as
    zero-mean for a one-day horizon, the same convention as the
    realised-variance target). A return exactly on the boundary
    counts as inside.
    """
    if forecast_variance <= 0:
        raise ValueError(
            f"forecast_variance must be > 0, got {forecast_variance}")
    return abs(realised_return) <= z_value(level) * math.sqrt(
        forecast_variance)


def score_interval_coverage(walk_forward_results,
                            levels=COVERAGE_LEVELS):
    """Pooled empirical interval coverage per model.

    For each fold the model's forecast interval is built from its
    one-day-ahead variance forecast (sigma = sqrt(forecast)) and the
    realised return's magnitude |r| = sqrt(realised_variance) — the
    folds store realised variance r^2, whose square root is exactly
    |r|, so no extra field is needed and no sign information is lost
    (coverage only depends on |r|). Coverage for a level is the
    fraction of folds whose realised return falls inside the
    interval. Reported pooled across symbols and per symbol; the
    targets are the nominal levels themselves (80% / 95%).

    Accepts one walk-forward result dict or a list of them (one per
    symbol). Returns {"n_folds", "n_symbols", "levels", "models":
    {model: {"coverage_80", "coverage_95", "n"}}, "per_symbol":
    [{...per-result coverage...}]}. Coverage is a separate target
    check: it never rewrites the QLIKE verdict produced by
    compare_volatility_models.
    """
    if isinstance(walk_forward_results, dict):
        results = [walk_forward_results]
    else:
        results = list(walk_forward_results)
    if not results:
        raise ValueError("no walk-forward results to score")
    for level in levels:
        z_value(level)  # raises on an unsupported level
    models = results[0]["models"]
    for result in results:
        if result["models"] != models:
            raise ValueError("all results must cover the same models")
        if not result["folds"]:
            raise ValueError("no folds to score")
    n_folds = sum(len(r["folds"]) for r in results)
    out_models = {}
    for model in models:
        entry = {"n": n_folds}
        for level in levels:
            hits = 0
            for result in results:
                for fold in result["folds"]:
                    realised_return = math.sqrt(fold["realised_variance"])
                    if inside_interval(realised_return,
                                       fold["forecasts"][model], level):
                        hits += 1
            entry[f"coverage_{int(level * 100)}"] = hits / n_folds
        out_models[model] = entry
    per_symbol = []
    for result in results:
        per = {"n": len(result["folds"])}
        for model in models:
            for level in levels:
                hits = sum(
                    1 for fold in result["folds"]
                    if inside_interval(math.sqrt(fold["realised_variance"]),
                                       fold["forecasts"][model], level))
                per.setdefault(model, {})[f"coverage_{int(level * 100)}"] = (
                    hits / len(result["folds"]))
        per_symbol.append(per)
    return {"n_folds": n_folds, "n_symbols": len(results),
            "levels": list(levels), "models": out_models,
            "per_symbol": per_symbol}


def qlike(realised_variance, forecast_variance):
    """QLIKE proper scoring rule for a variance forecast.

    QLIKE = ln(forecast) + realised / forecast. Lower is better.
    Under-forecasting is penalised more than over-forecasting: when
    the forecast is far too small, realised / forecast explodes.
    realised_variance must be >= 0, forecast_variance must be > 0.
    """
    if realised_variance < 0:
        raise ValueError(
            f"realised_variance must be >= 0, got {realised_variance}")
    if forecast_variance <= 0:
        raise ValueError(
            f"forecast_variance must be > 0 for QLIKE, got {forecast_variance}")
    return math.log(forecast_variance) + realised_variance / forecast_variance


def mse_variance(realised_variance, forecast_variance):
    """Squared error on variance (secondary score; never decides)."""
    if realised_variance < 0:
        raise ValueError(
            f"realised_variance must be >= 0, got {realised_variance}")
    if forecast_variance <= 0:
        raise ValueError(
            f"forecast_variance must be > 0, got {forecast_variance}")
    return (realised_variance - forecast_variance) ** 2


def annualised_volatility_from_variance(variance, periods_per_year=252):
    """Annualised volatility implied by a daily variance forecast."""
    if variance < 0:
        raise ValueError(f"variance must be >= 0, got {variance}")
    if periods_per_year <= 0:
        raise ValueError(
            f"periods_per_year must be positive, got {periods_per_year}")
    return math.sqrt(variance * periods_per_year)


def walk_forward_volatility(returns, models=MODELS, train_size=252):
    """Walk-forward 1-day-ahead variance forecasts, expanding window.

    Origins t run from train_size to len(returns) - 1 inclusive. At
    each origin the model receives ONLY returns[:t] (it is refit /
    re-anchored on that slice) and forecasts the variance of return
    t, whose realised variance is r_t^2. Returns {"folds": [(origin,
    realised_variance, {model: forecast})...], "n_test", "train_size",
    "models"}.
    """
    returns = validate_series("returns", returns, min_length=10)
    if not 0 < train_size < len(returns):
        raise ValueError(
            f"train_size must leave a non-empty test set, got {train_size} "
            f"for {len(returns)} returns")
    for model in models:
        if model not in MODELS:
            raise ValueError(f"unknown volatility model {model!r}")
    folds = []
    for t in range(train_size, len(returns)):
        training = returns[:t]
        realised = returns[t] ** 2
        forecasts = {model: forecast_variance(model, training)
                     for model in models}
        folds.append({"origin": t, "realised_variance": realised,
                      "forecasts": forecasts})
    return {"folds": folds, "n_test": len(folds), "train_size": train_size,
            "models": list(models)}


def score_volatility_forecasts(walk_forward_result):
    """Mean QLIKE and mean MSE-on-variance per model, out of sample."""
    folds = walk_forward_result["folds"]
    if not folds:
        raise ValueError("no folds to score")
    models = walk_forward_result["models"]
    out = {}
    for model in models:
        qlikes = [qlike(f["realised_variance"], f["forecasts"][model])
                  for f in folds]
        mses = [mse_variance(f["realised_variance"], f["forecasts"][model])
                for f in folds]
        out[model] = {"mean_qlike": sum(qlikes) / len(qlikes),
                      "mean_mse_variance": sum(mses) / len(mses),
                      "n": len(folds)}
    return out


def _bootstrap_ci(differences, resamples=BOOTSTRAP_RESAMPLES,
                  seed=BOOTSTRAP_SEED):
    """95% percentile bootstrap CI on the mean of paired differences.

    Seeded, so the CI is deterministic for a given input. Returns
    (lo, hi) of the resampled means.
    """
    if not differences:
        raise ValueError("no differences to bootstrap")
    if resamples < 100:
        raise ValueError(f"need at least 100 resamples, got {resamples}")
    rng = random.Random(seed)
    n = len(differences)
    means = []
    for _ in range(resamples):
        sample = [differences[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * resamples)]
    hi = means[int(0.975 * resamples) - 1]
    return (lo, hi)


def compare_volatility_models(walk_forward_results, baseline="naive",
                              resamples=BOOTSTRAP_RESAMPLES,
                              seed=BOOTSTRAP_SEED):
    """Apply the protocol decision rule to walk-forward results.

    walk_forward_results is one result dict, or a list of them (one
    per symbol) — the list case pools per-fold QLIKE differences
    across symbols, which is the protocol's primary verdict. A model
    "beats baseline" only if its pooled mean QLIKE is lower than the
    baseline's AND the 95% paired-bootstrap CI on the mean QLIKE
    difference (model - baseline) lies entirely below zero. Otherwise
    the verdict is "no edge". MSE is reported but never decides.

    Returns {"baseline", "n_folds", "n_symbols", "models": {model:
    {"mean_qlike", "mean_qlike_diff_vs_baseline", "ci_95",
    "mean_mse_variance", "verdict"}}}.
    """
    if isinstance(walk_forward_results, dict):
        results = [walk_forward_results]
    else:
        results = list(walk_forward_results)
    if not results:
        raise ValueError("no walk-forward results to compare")
    models = results[0]["models"]
    if baseline not in models:
        raise ValueError(f"baseline {baseline!r} not among models {models}")
    for result in results:
        if result["models"] != models:
            raise ValueError("all results must cover the same models")
    n_folds = sum(len(r["folds"]) for r in results)
    all_scores = [score_volatility_forecasts(r) for r in results]
    out_models = {}
    for model in models:
        if model == baseline:
            continue
        diffs = []
        for result in results:
            for fold in result["folds"]:
                diffs.append(qlike(fold["realised_variance"],
                                   fold["forecasts"][model])
                             - qlike(fold["realised_variance"],
                                     fold["forecasts"][baseline]))
        mean_diff = sum(diffs) / len(diffs)
        lo, hi = _bootstrap_ci(diffs, resamples=resamples, seed=seed)
        verdict = "beats baseline" if mean_diff < 0 and hi < 0 else "no edge"
        mean_qlike = (sum(s[model]["mean_qlike"] * s[model]["n"]
                          for s in all_scores) / n_folds)
        mean_mse = (sum(s[model]["mean_mse_variance"] * s[model]["n"]
                        for s in all_scores) / n_folds)
        out_models[model] = {
            "mean_qlike": mean_qlike,
            "mean_qlike_diff_vs_baseline": mean_diff,
            "ci_95": (lo, hi),
            "mean_mse_variance": mean_mse,
            "verdict": verdict,
        }
    base_qlike = (sum(s[baseline]["mean_qlike"] * s[baseline]["n"]
                      for s in all_scores) / n_folds)
    base_mse = (sum(s[baseline]["mean_mse_variance"] * s[baseline]["n"]
                    for s in all_scores) / n_folds)
    out_models[baseline] = {
        "mean_qlike": base_qlike,
        "mean_qlike_diff_vs_baseline": 0.0,
        "ci_95": (0.0, 0.0),
        "mean_mse_variance": base_mse,
        "verdict": "baseline",
    }
    return {"baseline": baseline, "n_folds": n_folds,
            "n_symbols": len(results), "models": out_models}
