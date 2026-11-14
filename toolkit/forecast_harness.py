"""Forecast evaluation harness — walk-forward + purged/embargoed splits,
proper scoring rules, calibration curves, bootstrap-CI model comparison,
and a count of models tried (PR 8.1).

Stdlib only. The harness evaluates; it never invents data. Every
verdict comes from the pre-registered protocol in
docs/forecast-harness-eval-protocol.md, fixed before the first run.

Reuse, not duplication: price scoring builds on
forecast.walk_forward_evaluate's MAE/RMSE convention; Brier score
and Platt calibration come from predictive.brier_score /
predictive.platt_calibrate / predictive.calibrated_probability;
volatility scoring and the QLIKE verdict come from
volatility.walk_forward_volatility /
volatility.score_volatility_forecasts /
volatility.compare_volatility_models. The paired bootstrap CI here
is the generic public form of the seeded percentile algorithm in
volatility._bootstrap_ci (which is private to that module). Folds
are built from walk_forward_splits (the single audit surface);
with purge = embargo = 0 they equal
predictive.walk_forward_forecasts' triples, which the test suite
checks.

Rules that make this honest (the protocol):
- A price model "beats baseline" on a symbol only if its
  out-of-sample mean MAE is lower than naive AND the 95%
  paired-bootstrap CI on the mean MAE difference lies entirely
  below zero; otherwise "no edge". Overall "beats baseline" needs
  a win on every symbol in the universe.
- Direction accuracy is reported against 50% and the up-day base
  rate; it never decides a verdict.
- Probabilities are built one documented way (signed distance
  forecast - last close, Platt-fitted on the first half of the
  out-of-sample folds, scored on the second half only).
- No claim from the daily stream until 100 scored forecasts.
- Failures stay in. Every count is shown.
"""
from __future__ import annotations

import json
import math
import random
from datetime import date
from pathlib import Path

from .checks import validate_series
from .forecast import (
    linear_trend_forecast,
    moving_average_forecast,
    naive_forecast,
)
from .predictive import (
    adaptive_ensemble_forecast,
    ar1_forecast,
    brier_score,
    calibrated_probability,
    drift_forecast,
    exponential_smoothing_forecast,
    holt_forecast,
    platt_calibrate,
)
from .volatility import (
    compare_volatility_models,
    log_returns,
    score_volatility_forecasts,
    walk_forward_volatility,
)

__all__ = [
    "BOOTSTRAP_RESAMPLES",
    "BOOTSTRAP_SEED",
    "CALIBRATION_BINS",
    "EMBARGO",
    "MIN_SCORED_FOR_CLAIM",
    "PRICE_BASELINE",
    "PURGE",
    "calibration_curve",
    "evaluate_direction_probabilities",
    "evaluate_price_models",
    "evaluate_volatility_models",
    "load_experiments",
    "log_score",
    "paired_bootstrap_ci",
    "price_models",
    "read_daily_predictions",
    "record_harness_experiment",
    "run_full_evaluation",
    "score_daily_predictions",
    "walk_forward_splits",
]

# Pre-registered protocol constants
# (docs/forecast-harness-eval-protocol.md).
BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 20261114
PURGE = 5
EMBARGO = 5
CALIBRATION_BINS = 10
MIN_SCORED_FOR_CLAIM = 100
PRICE_BASELINE = "naive"
# Log-score clip: a numerical guard so log(0) is never taken, not a
# tuning choice (protocol-fixed).
_LOG_CLIP = 1e-15


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


# --- Proper scoring rules + calibration -------------------------------

def log_score(probabilities, outcomes):
    """Log score: mean negative log-likelihood of the realised outcome.

    Lower is better; ln 2 (~0.6931) is always forecasting 0.5.
    Probabilities are clipped to [1e-15, 1 - 1e-15] so the logarithm
    is always defined (a numerical guard, protocol-fixed).
    """
    probs = validate_series("probabilities", probabilities, min_length=1)
    outs = validate_series("outcomes", outcomes, min_length=1)
    if len(probs) != len(outs):
        raise ValueError("probabilities and outcomes must have the same length")
    if any(p < 0.0 or p > 1.0 for p in probs):
        raise ValueError("probabilities must be in [0, 1]")
    if any(o not in (0.0, 1.0) for o in outs):
        raise ValueError("outcomes must be 0 or 1")
    terms = []
    for p, o in zip(probs, outs):
        p = min(1.0 - _LOG_CLIP, max(_LOG_CLIP, p))
        terms.append(-math.log(p) if o == 1.0 else -math.log(1.0 - p))
    return _mean(terms)


def calibration_curve(probabilities, outcomes, n_bins=CALIBRATION_BINS):
    """Reliability bins: count, mean predicted, observed frequency.

    10 equal-width bins over [0, 1] by default. Empty bins are kept
    with n = 0 and None for mean/observed — a bin with no forecasts
    is a fact about the model, never dropped.
    """
    probs = validate_series("probabilities", probabilities, min_length=1)
    outs = validate_series("outcomes", outcomes, min_length=1)
    if len(probs) != len(outs):
        raise ValueError("probabilities and outcomes must have the same length")
    if any(p < 0.0 or p > 1.0 for p in probs):
        raise ValueError("probabilities must be in [0, 1]")
    if any(o not in (0.0, 1.0) for o in outs):
        raise ValueError("outcomes must be 0 or 1")
    if not isinstance(n_bins, int) or n_bins < 1:
        raise ValueError(f"n_bins must be a positive int, got {n_bins!r}")
    bins = [{"bin": b, "bin_low": b / n_bins, "bin_high": (b + 1) / n_bins,
             "n": 0, "mean_predicted": None, "observed_frequency": None}
            for b in range(n_bins)]
    sums = [0.0] * n_bins
    hits = [0.0] * n_bins
    for p, o in zip(probs, outs):
        b = min(int(p * n_bins), n_bins - 1)
        bins[b]["n"] += 1
        sums[b] += p
        hits[b] += o
    for b in range(n_bins):
        if bins[b]["n"]:
            bins[b]["mean_predicted"] = sums[b] / bins[b]["n"]
            bins[b]["observed_frequency"] = hits[b] / bins[b]["n"]
    return bins


def paired_bootstrap_ci(differences, resamples=BOOTSTRAP_RESAMPLES,
                        seed=BOOTSTRAP_SEED):
    """95% percentile bootstrap CI on the mean of paired differences.

    Seeded, so the CI is deterministic for a given input. Returns
    (lo, hi) of the resampled means. This is the generic public form
    of the seeded percentile algorithm in volatility._bootstrap_ci.
    """
    diffs = validate_series("differences", differences, min_length=1)
    if resamples < 100:
        raise ValueError(f"need at least 100 resamples, got {resamples}")
    rng = random.Random(seed)
    n = len(diffs)
    means = []
    for _ in range(resamples):
        sample = [diffs[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    return (means[int(0.025 * resamples)],
            means[int(0.975 * resamples) - 1])


# --- Splits ------------------------------------------------------------

def walk_forward_splits(n, train_size, horizon=1, purge=0, embargo=0):
    """Expanding walk-forward (train, test) index splits.

    Origins t run from train_size to n - horizon - embargo
    inclusive. At origin t the training indices are range(0,
    t - purge) and the test indices are range(t + embargo,
    t + embargo + horizon): the last training index and the first
    test index are always at least purge + embargo + 1 apart, and
    no index is ever both training and test. With purge = embargo
    = 0 this is the plain expanding walk-forward split.
    """
    if not isinstance(n, int) or n < 10:
        raise ValueError(f"n must be an int >= 10, got {n!r}")
    if not isinstance(train_size, int) or not 0 < train_size < n:
        raise ValueError(
            f"train_size must leave a non-empty test set, got {train_size} "
            f"for {n} observations")
    if not isinstance(horizon, int) or horizon < 1:
        raise ValueError(f"horizon must be a positive int, got {horizon!r}")
    if not isinstance(purge, int) or purge < 0:
        raise ValueError(f"purge must be a non-negative int, got {purge!r}")
    if not isinstance(embargo, int) or embargo < 0:
        raise ValueError(f"embargo must be a non-negative int, got {embargo!r}")
    if train_size - purge < 10:
        raise ValueError(
            f"train_size - purge must leave at least 10 training "
            f"observations, got {train_size - purge}")
    splits = []
    for t in range(train_size, n - horizon - embargo + 1):
        train_idx = list(range(t - purge))
        test_idx = list(range(t + embargo, t + embargo + horizon))
        splits.append((train_idx, test_idx))
    if not splits:
        raise ValueError("splits are empty: train_size leaves no test window")
    return {"splits": splits, "n": n, "train_size": train_size,
            "horizon": horizon, "purge": purge, "embargo": embargo,
            "n_splits": len(splits)}


# --- Price models (protocol-fixed registry, horizon 1) -----------------

def price_models():
    """All price models as train -> 1-step-ahead point forecast.

    The 9 tried models plus the naive baseline (10 models in the
    run). Every parameter is fixed by the protocol; nothing here is
    fitted to an outcome.
    """
    base = {
        "naive": naive_forecast,
        "moving_average_5": lambda t: moving_average_forecast(t, window=5),
        "linear_trend": linear_trend_forecast,
        "exp_smoothing_0.3":
            lambda t: exponential_smoothing_forecast(t, alpha=0.3),
    }

    def _ensemble_equal(train):
        points = [fn(train) for fn in base.values()]
        return sum(points) / len(points)

    return {
        **base,
        "ar1": lambda t: ar1_forecast(t, horizon=1),
        "holt": lambda t: holt_forecast(t, horizon=1),
        "holt_damped": lambda t: holt_forecast(t, horizon=1, damping=0.9),
        "drift": lambda t: drift_forecast(t, horizon=1),
        "ensemble_equal": _ensemble_equal,
        "ensemble_adaptive":
            lambda t: adaptive_ensemble_forecast(t, horizon=1)["forecast"],
    }


def _direction_correct(forecast, last_close, actual):
    """One direction call: up iff forecast > last close, down iff <.

    A forecast exactly equal to the last close predicts no move:
    correct only when the actual is exactly unchanged.
    """
    if forecast > last_close:
        return actual > last_close
    if forecast < last_close:
        return actual < last_close
    return actual == last_close


# --- Price evaluation ---------------------------------------------------

def evaluate_price_models(series, train_size, purge=0, embargo=0,
                          models=None, resamples=BOOTSTRAP_RESAMPLES,
                          seed=BOOTSTRAP_SEED):
    """Walk-forward price evaluation vs the naive baseline.

    Horizon 1. Folds are built directly from walk_forward_splits:
    at each split the model receives ONLY the training indices of
    that split — [series[i] for i in train_idx] — and is judged on
    the test index. That is the look-ahead audit surface: in the
    purged/embargoed variant the model never sees the purged tail,
    by construction. (With purge = embargo = 0 the folds equal
    predictive.walk_forward_forecasts' triples; the test suite
    checks that equivalence.) Per model: MAE/RMSE, direction
    accuracy vs 50% and the up-day base rate, paired-bootstrap CI
    on the mean MAE difference vs naive, and the protocol verdict.
    The probabilistic block (Brier / log score / calibration) fits
    Platt scaling on the first half of the folds and scores the
    second half only; with fewer than 8 folds it reports too-few
    instead of a score.
    """
    series = validate_series("series", series, min_length=10)
    splits = walk_forward_splits(len(series), train_size, horizon=1,
                                 purge=purge, embargo=embargo)
    models = dict(price_models() if models is None else models)
    if PRICE_BASELINE not in models:
        raise ValueError(
            f"models must include the {PRICE_BASELINE!r} baseline")
    per_model = {}
    for name, fn in models.items():
        folds = []
        for train_idx, test_idx in splits["splits"]:
            train = [series[i] for i in train_idx]
            test_i = test_idx[0]
            folds.append({"origin": train_idx[-1] + 1,
                          "forecast": fn(train),
                          "actual": series[test_i],
                          "last_close": series[test_i - 1]})
        if not folds:
            raise ValueError(f"no folds for model {name!r}")
        errors = [f["actual"] - f["forecast"] for f in folds]
        mae = _mean([abs(e) for e in errors])
        rmse = math.sqrt(_mean([e ** 2 for e in errors]))
        correct = [_direction_correct(f["forecast"], f["last_close"],
                                      f["actual"]) for f in folds]
        accuracy = _mean([1.0 if c else 0.0 for c in correct])
        up_days = [f["actual"] > f["last_close"] for f in folds]
        base_rate = _mean([1.0 if u else 0.0 for u in up_days])
        per_model[name] = {
            "mae": mae, "rmse": rmse, "n": len(folds),
            "direction_accuracy": accuracy,
            "direction_n_correct": sum(1 for c in correct if c),
            "up_day_base_rate": base_rate,
            "folds": folds,
        }
    naive_mae = per_model[PRICE_BASELINE]["mae"]
    for name, result in per_model.items():
        if name == PRICE_BASELINE:
            result["mae_diff_vs_baseline"] = 0.0
            result["ci_95"] = (0.0, 0.0)
            result["verdict"] = "baseline"
            continue
        diffs = [abs(a["actual"] - a["forecast"])
                 - abs(b["actual"] - b["forecast"])
                 for a, b in zip(result["folds"],
                                 per_model[PRICE_BASELINE]["folds"])]
        mean_diff = _mean(diffs)
        lo, hi = paired_bootstrap_ci(diffs, resamples=resamples, seed=seed)
        result["mae_diff_vs_baseline"] = mean_diff
        result["ci_95"] = (lo, hi)
        result["verdict"] = ("beats baseline"
                             if result["mae"] < naive_mae and hi < 0
                             else "no edge")
    for name, result in per_model.items():
        scores = [f["forecast"] - f["last_close"] for f in result["folds"]]
        outcomes = [1.0 if f["actual"] > f["last_close"] else 0.0
                    for f in result["folds"]]
        result["probabilistic"] = evaluate_direction_probabilities(
            scores, outcomes)
    return {"series_length": len(series), "train_size": train_size,
            "horizon": 1, "purge": purge, "embargo": embargo,
            "n_folds": len(next(iter(per_model.values()))["folds"]),
            "baseline": PRICE_BASELINE,
            "models_tried": len(models) - 1,
            "models": per_model}


def evaluate_direction_probabilities(scores, outcomes, fit_fraction=0.5):
    """Brier / log score / calibration on out-of-sample direction.

    scores are signed out-of-sample distances (forecast - last
    close); outcomes are 1 when the actual close is above the last
    close, 0 otherwise. Platt scaling is fitted on the first
    fit_fraction of the folds (by time) and scored on the rest
    only — fitting on the evaluated half would overstate
    calibration. With fewer than 8 folds there is too little to
    split, so the result says so instead of scoring.
    """
    scores = validate_series("scores", scores, min_length=1)
    outs = validate_series("outcomes", outcomes, min_length=1)
    if len(scores) != len(outs):
        raise ValueError("scores and outcomes must have the same length")
    if any(o not in (0.0, 1.0) for o in outs):
        raise ValueError("outcomes must be 0 or 1")
    if not 0.0 < fit_fraction < 1.0:
        raise ValueError(f"fit_fraction must be in (0, 1), got {fit_fraction}")
    if len(scores) < 8:
        return {"note": "too few folds to split fit/eval "
                        "(need at least 8)", "n": len(scores)}
    cut = max(4, int(len(scores) * fit_fraction))
    if len(scores) - cut < 1:
        return {"note": "too few folds to split fit/eval "
                        "(need at least 8)", "n": len(scores)}
    cal = platt_calibrate(scores[:cut], outs[:cut])
    eval_probs = [calibrated_probability(s, cal["a"], cal["b"])
                  for s in scores[cut:]]
    eval_outs = outs[cut:]
    base = _mean(eval_outs)
    return {
        "n_fit": cut, "n_eval": len(eval_outs),
        "brier": brier_score(eval_probs, eval_outs),
        "brier_always_half": 0.25,
        "brier_base_rate": base * (1.0 - base),
        "log_score": log_score(eval_probs, eval_outs),
        "log_score_always_half": math.log(2),
        "calibration": calibration_curve(eval_probs, eval_outs),
        "platt": {"a": cal["a"], "b": cal["b"],
                  "brier_before": cal["brier_before"],
                  "brier_after": cal["brier_after"]},
    }


# --- Volatility evaluation (re-run of PR 7.2, unchanged rule) ----------

def evaluate_volatility_models(returns, train_size=252):
    """Re-run the PR 7.2 volatility evaluation on one return series.

    walk_forward_volatility + score_volatility_forecasts +
    compare_volatility_models from toolkit/volatility.py: QLIKE
    primary, MSE on variance secondary, verdict by the pooled
    mean-QLIKE difference with a 95% paired-bootstrap CI
    (2000 resamples, seed 20261114 — the volatility module's own
    constants). The harness adds nothing to the rule; it only
    records the re-run.
    """
    returns = validate_series("returns", returns, min_length=10)
    wf = walk_forward_volatility(returns, train_size=train_size)
    return {"walk_forward": wf,
            "scores": score_volatility_forecasts(wf),
            "comparison": compare_volatility_models(wf),
            "models_tried": 2, "baseline": "naive"}


# --- Full run ------------------------------------------------------------

def run_full_evaluation(price_series_by_symbol, train_size=252,
                        purge=0, embargo=0,
                        resamples=BOOTSTRAP_RESAMPLES,
                        seed=BOOTSTRAP_SEED):
    """Run the whole protocol: price + volatility, all symbols.

    price_series_by_symbol: {symbol: closes}. Overall price verdict
    per model is "beats baseline" only if it beats the baseline on
    every symbol; otherwise "no edge". Pooled direction accuracy
    pools per-fold correctness across symbols, with a bootstrap CI
    on the pooled accuracy. models_tried counts tried models only
    (9 price + 2 volatility = 11); baselines are not tried models.
    """
    if not isinstance(price_series_by_symbol, dict) \
            or not price_series_by_symbol:
        raise ValueError("price_series_by_symbol must be a non-empty dict")
    per_symbol = {}
    for symbol, closes in price_series_by_symbol.items():
        closes = validate_series(f"closes for {symbol}", closes,
                                 min_length=10)
        price = evaluate_price_models(closes, train_size, purge=purge,
                                      embargo=embargo, resamples=resamples,
                                      seed=seed)
        vol = evaluate_volatility_models(log_returns(closes),
                                         train_size=train_size)
        per_symbol[symbol] = {"price": price, "volatility": vol}
    symbols = list(price_series_by_symbol)
    pooled_volatility = compare_volatility_models(
        [per_symbol[s]["volatility"]["walk_forward"] for s in symbols])
    overall = {}
    for name in price_models():
        if name == PRICE_BASELINE:
            overall[name] = "baseline"
            continue
        verdicts = [per_symbol[s]["price"]["models"][name]["verdict"]
                    for s in symbols]
        overall[name] = ("beats baseline"
                         if all(v == "beats baseline" for v in verdicts)
                         else "no edge")
    pooled_direction = {}
    for name in price_models():
        correct = []
        ups = []
        for symbol in symbols:
            for fold in per_symbol[symbol]["price"]["models"][name]["folds"]:
                correct.append(1.0 if _direction_correct(
                    fold["forecast"], fold["last_close"], fold["actual"])
                    else 0.0)
                ups.append(1.0 if fold["actual"] > fold["last_close"]
                           else 0.0)
        lo, hi = paired_bootstrap_ci(correct, resamples=resamples, seed=seed)
        pooled_direction[name] = {
            "accuracy": _mean(correct),
            "ci_95": (lo, hi), "n": len(correct),
            "up_day_base_rate": _mean(ups),
        }
    return {"protocol": "docs/forecast-harness-eval-protocol.md",
            "train_size": train_size, "horizon": 1,
            "purge": purge, "embargo": embargo,
            "resamples": resamples, "seed": seed,
            "symbols": symbols, "per_symbol": per_symbol,
            "overall_price_verdicts": overall,
            "pooled_direction": pooled_direction,
            "pooled_volatility": pooled_volatility,
            "models_tried_price": 9, "models_tried_volatility": 2,
            "models_tried": 11}


# --- Daily-stream scoring (docs/forecast-scoring-protocol.md) -----------

def read_daily_predictions(path):
    """Read reports/daily predictions.jsonl rows, in file order.

    Missing file -> empty list. Rows are returned as stored; this
    function never edits the ledger.
    """
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def score_daily_predictions(rows, resamples=BOOTSTRAP_RESAMPLES,
                            seed=BOOTSTRAP_SEED):
    """Score the reports/daily next-step forecasts (H2 protocol).

    rows: prediction dicts with forecast, last_close, actual (when
    scored), direction_correct and symbol. Scored = rows with an
    actual (status scored/failed — failures stay in); pending =
    the rest. Per symbol and pooled: MAE/RMSE vs the no-change
    baseline (last_close), paired-bootstrap CI on the mean
    absolute-error difference, direction accuracy vs 50% and the
    up-day base rate. Claim is "insufficient n — no claim" until
    100 forecasts are scored; after that the verdict rule is the
    pooled MAE lower than baseline AND the CI entirely below zero.
    """
    if not isinstance(rows, list):
        raise ValueError(  # noqa: TRY004 — toolkit convention is
            # ValueError for all input validation (decision_log.py).
            "rows must be a list of prediction dicts")
    made = len(rows)
    scored = [r for r in rows if r.get("actual") is not None]
    pending = made - len(scored)

    def _score(subset):
        if not subset:
            return {"n_scored": 0, "mae": None, "rmse": None,
                    "baseline_mae": None, "baseline_rmse": None,
                    "mae_diff_vs_baseline": None, "ci_95": None,
                    "direction_accuracy": None, "direction_ci_95": None,
                    "up_day_base_rate": None}
        f_err = [abs(r["actual"] - r["forecast"]) for r in subset]
        b_err = [abs(r["actual"] - r["last_close"]) for r in subset]
        diffs = [f - b for f, b in zip(f_err, b_err)]
        lo, hi = paired_bootstrap_ci(diffs, resamples=resamples, seed=seed)
        correct = [1.0 if r.get("direction_correct") else 0.0
                   for r in subset]
        d_lo, d_hi = paired_bootstrap_ci(correct, resamples=resamples,
                                         seed=seed)
        ups = [1.0 if r["actual"] > r["last_close"] else 0.0
               for r in subset]
        return {"n_scored": len(subset),
                "mae": _mean(f_err),
                "rmse": math.sqrt(_mean(
                    [(r["actual"] - r["forecast"]) ** 2 for r in subset])),
                "baseline_mae": _mean(b_err),
                "baseline_rmse": math.sqrt(_mean(
                    [(r["actual"] - r["last_close"]) ** 2 for r in subset])),
                "mae_diff_vs_baseline": _mean(diffs),
                "ci_95": (lo, hi),
                "direction_accuracy": _mean(correct),
                "direction_ci_95": (d_lo, d_hi),
                "up_day_base_rate": _mean(ups)}

    pooled = _score(scored)
    per_symbol = {}
    for symbol in sorted({r.get("symbol") for r in scored}):
        per_symbol[symbol] = _score(
            [r for r in scored if r.get("symbol") == symbol])
    if len(scored) < MIN_SCORED_FOR_CLAIM:
        claim = "insufficient n — no claim"
        verdict = None
    else:
        verdict = ("beats baseline"
                   if pooled["mae"] < pooled["baseline_mae"]
                   and pooled["ci_95"][1] < 0 else "no edge")
        claim = verdict
    return {"counts": {"made": made, "scored": len(scored),
                       "pending": pending},
            "min_scored_for_claim": MIN_SCORED_FOR_CLAIM,
            "claim": claim, "verdict": verdict,
            "resamples": resamples, "seed": seed,
            "pooled": pooled, "per_symbol": per_symbol}


# --- Experiment tracking (qlib workflow/recorder) -----------------------

def record_harness_experiment(name, evaluation, path):
    """Append one JSON line recording a harness run.

    The qlib experiment workflow/recorder idea as a plain JSONL
    append (no mlflow, no server): one line per run with the
    config (splits, seed, resamples, models, train_size, horizon,
    purge, embargo), the model count, and the per-model verdicts
    and headline metrics. Re-running adds a line; it never
    rewrites the past. Returns the record dict that was written.
    """
    if not isinstance(evaluation, dict) \
            or "overall_price_verdicts" not in evaluation:
        raise ValueError(
            "evaluation must be a run_full_evaluation() result dict")
    record = {
        "name": name, "date": date.today().isoformat(),
        "protocol": evaluation.get("protocol"),
        "config": {"train_size": evaluation["train_size"],
                   "horizon": evaluation["horizon"],
                   "purge": evaluation["purge"],
                   "embargo": evaluation["embargo"],
                   "resamples": evaluation["resamples"],
                   "seed": evaluation["seed"],
                   "symbols": evaluation["symbols"]},
        "models_tried": evaluation["models_tried"],
        "overall_price_verdicts": evaluation["overall_price_verdicts"],
        "pooled_volatility_verdicts": {
            model: m["verdict"] for model, m in
            evaluation["pooled_volatility"]["models"].items()},
        "volatility_verdicts": {
            symbol: {model: m["verdict"] for model, m in
                     block["volatility"]["comparison"]["models"].items()}
            for symbol, block in evaluation["per_symbol"].items()},
        "per_symbol_price_mae": {
            symbol: {model: m["mae"] for model, m in
                     block["price"]["models"].items()}
            for symbol, block in evaluation["per_symbol"].items()},
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    return record


def load_experiments(path):
    """Read harness experiment records, in file order."""
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
