"""Probabilistic direction models: P(up next day), walk-forward scored.

Stdlib only. Model-light by design: no LightGBM, no LSTM, no
Transformers, no GATs, no scikit-learn — the matrix row "Model zoo
(LightGBM / LSTM / Transformers / GATs)" stays NOT PLANNED.

Three base models, each outputting a probability of an up move
(next close strictly above today's close), never a hard call:

- logistic_gd: logistic regression fitted by plain batch gradient
  descent on standardised features (lr 0.10, 200 epochs, L2 0.01).
- gaussian_nb: Gaussian naive Bayes, per-class mean/variance per
  feature, prior = training base rate.
- score_platt: a FIXED-weight score on standardised features passed
  through a sigmoid, then Platt-calibrated (expanding, past folds
  only — see walk_forward_direction).

Plus one regime-conditioned variant (logistic_gd + a high-volatility
indicator feature) and two ensembles (equal-weight and
inverse-trailing-Brier-weighted) of the three base models.

Features (7, fixed in docs/direction-models-eval-protocol.md) come
from closes only, each feature at bar ``i`` using closes up to ``i``
only: mom5, mom20, sma20_gap, sma50_gap, rsi14 (scaled), vol20,
ret1. Warm-up is 50 bars (the sma50 window).

Regime detection builds on the existing
``toolkit/predictive.py`` ``volatility_regime(window=20)`` — it is
extended here, not duplicated — with CUSUM change-point detection
and an HMM-lite two-state (calm/volatile) counter that estimates
transition counts walk-forward. The HMM-lite is NOT a fitted HMM:
no EM, no likelihood maximisation, no library — just counted
transitions from per-bar volatility labels.

Evaluation discipline (MODEL RULES, protocol pre-registered in
docs/direction-models-eval-protocol.md): a model beats a baseline
only out of sample, with a seeded paired-bootstrap CI entirely on
the winning side; otherwise it is labelled "no edge". Baselines are
the 50% coin-flip, the train-window base rate of up-days, and the
pre-registered direction rules (``toolkit/direction_rules.py`` —
READ by this module, never modified) mapped to probabilities
(UP -> 0.75, DOWN -> 0.25, no call -> base rate).

Research only: a probability of an up move is not a buy/sell/hold
recommendation, and a past out-of-sample Brier score is not a
guarantee of future accuracy.
"""
from __future__ import annotations

import itertools
import math
import random

from .checks import validate_series
from .direction_rules import direction_for
from .indicators import rsi as _rsi
from .predictive import (
    brier_score,
    calibrated_probability,
    platt_calibrate,
    volatility_regime,
)

# Pre-registered protocol constants
# (docs/direction-models-eval-protocol.md).
FEATURE_NAMES = ("mom5", "mom20", "sma20_gap", "sma50_gap", "rsi14",
                 "vol20", "ret1")
WARMUP = 50
TRAIN_SIZE = 252
LOGISTIC_LR = 0.10
LOGISTIC_EPOCHS = 200
LOGISTIC_L2 = 0.01
SCORE_WEIGHTS = (0.3, 0.5, 0.4, 0.4, 0.2, -0.2, 0.1)
# SCORE_WEIGHTS order matches FEATURE_NAMES: mom5 0.3, mom20 0.5,
# sma20_gap 0.4, sma50_gap 0.4, rsi14 0.2, vol20 -0.2, ret1 0.1.
REGIME_WINDOW = 20
CUSUM_K_MULT = 0.5
CUSUM_H_MULT = 5.0
CALIBRATION_MIN_FOLDS = 30
ENSEMBLE_WEIGHT_WINDOW = 50
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261115
RULE_UP_PROB = 0.75
RULE_DOWN_PROB = 0.25
PROB_CLIP = 1e-6

BASE_MODELS = ("logistic_gd", "gaussian_nb", "score_platt")
EVALUATED_MODELS = ("logistic_gd", "gaussian_nb", "score_platt",
                    "logistic_regime", "ensemble_equal",
                    "ensemble_weighted")
BASELINES = ("coin_flip", "base_rate", "direction_rule")


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


def _population_std(xs):
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) if xs else 0.0


def _simple_returns(closes):
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

def direction_features(closes):
    """Feature dict per bar (None for warm-up bars), closes only.

    Each feature at index ``i`` uses closes ``[:i+1]`` only — no
    look-ahead by construction. A row exists (non-None) only for
    ``i >= 50`` (the sma50 window). RSI comes from
    ``toolkit/indicators.py`` (Wilder, window 14), scaled to
    ``(RSI - 50) / 50``.
    """
    closes = validate_series("closes", closes, min_length=1)
    if any(p <= 0 for p in closes):
        raise ValueError("closes must all be positive for direction_features")
    rsi_values = _rsi(closes, window=14)
    out = []
    for i, price in enumerate(closes):
        if i < WARMUP or rsi_values[i] is None:
            out.append(None)
            continue
        rets = _simple_returns(closes[:i + 1])
        out.append({
            "mom5": price / closes[i - 5] - 1.0,
            "mom20": price / closes[i - 20] - 1.0,
            "sma20_gap": price / _mean(closes[i - 19:i + 1]) - 1.0,
            "sma50_gap": price / _mean(closes[i - 49:i + 1]) - 1.0,
            "rsi14": (rsi_values[i] - 50.0) / 50.0,
            "vol20": _population_std(rets[-20:]),
            "ret1": price / closes[i - 1] - 1.0,
        })
    return out


def feature_rows(closes):
    """(X, y, indices): standardised-ready rows + next-day outcome.

    X rows are feature vectors in FEATURE_NAMES order; y is 1 when
    the next close is strictly above today's close, else 0 (a flat
    close counts as down — the tie rule fixed in the protocol).
    A row exists for every index ``i`` with a complete feature row
    and a next close. Features use closes up to ``i`` only; the
    label uses close ``i + 1`` (the outcome being forecast).
    """
    closes = validate_series("closes", closes, min_length=WARMUP + 2)
    if any(p <= 0 for p in closes):
        raise ValueError("closes must all be positive for feature_rows")
    feats = direction_features(closes)
    X, y, indices = [], [], []
    for i in range(len(closes) - 1):
        if feats[i] is None:
            continue
        X.append([feats[i][name] for name in FEATURE_NAMES])
        y.append(1 if closes[i + 1] > closes[i] else 0)
        indices.append(i)
    return X, y, indices


def _generic_standardisation(X):
    """(means, stds) per column over the given rows, any width."""
    n_features = len(X[0])
    means, stds = [], []
    for j in range(n_features):
        col = [row[j] for row in X]
        means.append(_mean(col))
        stds.append(_population_std(col))
    return means, stds


def _standardisation(X):
    """(means, stds) per feature column over the given rows.

    A zero std maps the feature to 0 on standardisation (the
    feature carries no signal in that window) instead of dividing
    by zero.
    """
    if not X:
        raise ValueError("_standardisation needs at least one row")
    if len(X[0]) != len(FEATURE_NAMES):
        raise ValueError("standardisation expects 7-feature rows")
    return _generic_standardisation(X)


def _standardise(row, means, stds):
    return [0.0 if stds[j] == 0 else (row[j] - means[j]) / stds[j]
            for j in range(len(FEATURE_NAMES))]


# ---------------------------------------------------------------------------
# Base models: fit -> params dict, proba -> P(up)
# ---------------------------------------------------------------------------

def fit_logistic(X, y, lr=LOGISTIC_LR, epochs=LOGISTIC_EPOCHS,
                 l2=LOGISTIC_L2):
    """Logistic regression by plain batch gradient descent.

    Weights start at 0; the intercept is unpenalised. Returns the
    fitted params (weights, bias, standardisation stats) — a plain
    dict, no fitted-object machinery.
    """
    if not X:
        raise ValueError("fit_logistic needs at least one training row")
    if len(X) != len(y):
        raise ValueError("X and y must have the same length")
    n_features = len(X[0])
    means, stds = _generic_standardisation(X)
    Xs = [[0.0 if stds[j] == 0 else (row[j] - means[j]) / stds[j]
           for j in range(n_features)] for row in X]
    weights = [0.0] * n_features
    bias = 0.0
    n = len(Xs)
    for _ in range(epochs):
        grad_w = [0.0] * n_features
        grad_b = 0.0
        for row, target in zip(Xs, y):
            p = _sigmoid(sum(w * v for w, v in zip(weights, row)) + bias)
            err = p - target
            for j in range(n_features):
                grad_w[j] += err * row[j]
            grad_b += err
        for j in range(n_features):
            weights[j] -= lr * (grad_w[j] / n + l2 * weights[j])
        bias -= lr * grad_b / n
    return {"weights": weights, "bias": bias, "means": means, "stds": stds}


def logistic_proba(params, row):
    """P(up) for one raw feature row under fitted logistic params."""
    xs = _standardise(row, params["means"], params["stds"])
    return _sigmoid(sum(w * v for w, v in zip(params["weights"], xs))
                    + params["bias"])


def fit_gaussian_nb(X, y):
    """Gaussian naive Bayes: per-class mean/variance per feature.

    Variances are floored at 1e-6. The prior is the training base
    rate. A class with no training rows is recorded as absent and
    ``gaussian_nb_proba`` falls back to the base rate for it.
    """
    if not X:
        raise ValueError("fit_gaussian_nb needs at least one training row")
    if len(X) != len(y):
        raise ValueError("X and y must have the same length")
    base_rate = _mean(y)
    params = {"base_rate": base_rate, "classes": {}}
    for cls in (0, 1):
        rows = [row for row, target in zip(X, y) if target == cls]
        if not rows:
            params["classes"][cls] = None
            continue
        stats = []
        for j in range(len(FEATURE_NAMES)):
            col = [row[j] for row in rows]
            var = _population_std(col) ** 2
            stats.append((_mean(col), max(var, 1e-6)))
        params["classes"][cls] = {"prior": len(rows) / len(X), "stats": stats}
    return params


def gaussian_nb_proba(params, row):
    """P(up) for one raw feature row under fitted NB params."""
    up = params["classes"][1]
    down = params["classes"][0]
    if up is None or down is None:
        # One class never seen in training: the model has no
        # evidence to move off the base rate.
        return params["base_rate"]

    def _log_likelihood(cls_params):
        total = math.log(cls_params["prior"])
        for value, (mu, var) in zip(row, cls_params["stats"]):
            total += (-0.5 * math.log(2.0 * math.pi * var)
                      - (value - mu) ** 2 / (2.0 * var))
        return total

    log_up = _log_likelihood(up)
    log_down = _log_likelihood(down)
    top = max(log_up, log_down)
    p_up = math.exp(log_up - top)
    p_down = math.exp(log_down - top)
    return p_up / (p_up + p_down)


def score_proba(row, means, stds):
    """P(up) from the FIXED-weight score: sigmoid of the weighted sum.

    The weights are fixed in the protocol, not fitted. ``means`` /
    ``stds`` standardise the row (training-window stats, supplied by
    the caller); the score itself has no fitted parameters.
    """
    xs = _standardise(row, means, stds)
    score = sum(w * v for w, v in zip(SCORE_WEIGHTS, xs))
    return _sigmoid(score)


# ---------------------------------------------------------------------------
# Regime detection: volatility_regime + CUSUM + HMM-lite
# ---------------------------------------------------------------------------

def regime_label(closes, window=REGIME_WINDOW):
    """High/low volatility regime at the last visible close.

    Thin wrapper over the existing ``toolkit/predictive.py``
    ``volatility_regime(window=20)``: the latest rolling return std
    vs the median rolling std of the same (visible) series. The
    label describes the recent window; it is not a prediction.
    """
    return volatility_regime(closes, window=window)["regime"]


def cusum_alarms(returns, k_mult=CUSUM_K_MULT, h_mult=CUSUM_H_MULT):
    """CUSUM change-point alarms on a return series.

    Reference mean 0; ``k = k_mult * sigma`` and alarm threshold
    ``h = h_mult * sigma`` where sigma is the population std of the
    given returns. Cumulative sums S+ / S- reset to 0 on an alarm.
    Returns {"n_alarms", "alarm_indices", "k", "h"}. A constant
    series (sigma 0) raises instead of alarming on noise.
    """
    returns = validate_series("returns", returns, min_length=2)
    sigma = _population_std(returns)
    if sigma == 0:
        raise ValueError("cusum_alarms needs non-constant returns")
    k = k_mult * sigma
    h = h_mult * sigma
    s_pos = s_neg = 0.0
    alarms = []
    for i, r in enumerate(returns):
        s_pos = max(0.0, s_pos + r - k)
        s_neg = min(0.0, s_neg + r + k)
        if s_pos > h or s_neg < -h:
            alarms.append(i)
            s_pos = s_neg = 0.0
    return {"n_alarms": len(alarms), "alarm_indices": alarms, "k": k, "h": h}


def hmm_lite_transitions(closes, window=REGIME_WINDOW):
    """Two-state (calm/volatile) labels + counted transitions.

    Each bar from ``window`` onward is labelled by its trailing
    ``window``-day return std vs the expanding median std up to that
    bar — the same comparison ``volatility_regime`` makes, applied
    per bar. Transition counts are estimated from the visible bars
    only. This is NOT a fitted HMM: no EM, no likelihood, no
    library — just counted transitions from the per-bar labels.
    """
    closes = validate_series("closes", closes, min_length=window + 2)
    if any(p <= 0 for p in closes):
        raise ValueError("closes must all be positive for hmm_lite_transitions")
    rets = _simple_returns(closes)
    stds = []
    for i in range(len(rets) - window + 1):
        stds.append(_population_std(rets[i:i + window]))
    states = []
    for i, std in enumerate(stds):
        median = sorted(stds[:i + 1])[len(stds[:i + 1]) // 2]
        states.append("volatile" if std > median * (1.0 + 1e-9) else "calm")
    transitions = {"calm->calm": 0, "calm->volatile": 0,
                   "volatile->calm": 0, "volatile->volatile": 0}
    for a, b in itertools.pairwise(states):
        transitions[f"{a}->{b}"] += 1
    return {"states": states, "transitions": transitions,
            "n_volatile": states.count("volatile"), "n_calm": states.count("calm")}


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def log_score(probabilities, outcomes):
    """Log score: mean negative log-likelihood of the outcomes.

    Probabilities are clipped to [1e-6, 1-1e-6] so one confident
    wrong call cannot produce an infinite score. Lower is better.
    """
    probs = validate_series("probabilities", probabilities, min_length=1)
    outs = validate_series("outcomes", outcomes, min_length=1)
    if len(probs) != len(outs):
        raise ValueError("probabilities and outcomes must have the same length")
    if any(p < 0.0 or p > 1.0 for p in probs):
        raise ValueError("probabilities must be in [0, 1]")
    if any(o not in (0.0, 1.0) for o in outs):
        raise ValueError("outcomes must be 0 or 1")
    total = 0.0
    for p, o in zip(probs, outs):
        pc = min(1.0 - PROB_CLIP, max(PROB_CLIP, p))
        total += -(math.log(pc) if o == 1 else math.log(1.0 - pc))
    return total / len(probs)


def reliability_bins(probabilities, outcomes, n_bins=10):
    """Reliability (calibration) bins: count, mean prob, observed rate.

    Equal-width bins [0, 0.1), ..., [0.9, 1.0]; a probability of
    exactly 1.0 lands in the last bin. Empty bins are reported with
    count 0 and None rates, not hidden.
    """
    probs = validate_series("probabilities", probabilities, min_length=1)
    outs = validate_series("outcomes", outcomes, min_length=1)
    if len(probs) != len(outs):
        raise ValueError("probabilities and outcomes must have the same length")
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")
    bins = []
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        idx = [i for i, p in enumerate(probs)
               if (lo <= p < hi) or (b == n_bins - 1 and p == 1.0)]
        if idx:
            bins.append({"bin": (lo, hi), "count": len(idx),
                         "mean_probability": _mean([probs[i] for i in idx]),
                         "observed_up_rate": _mean([outs[i] for i in idx])})
        else:
            bins.append({"bin": (lo, hi), "count": 0,
                         "mean_probability": None, "observed_up_rate": None})
    return bins


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
    return (means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1])


# ---------------------------------------------------------------------------
# Walk-forward evaluation
# ---------------------------------------------------------------------------

def _fit_all(X_train, y_train):
    """Fit the base models on training rows.

    Returns (logistic_params, nb_params, (score means, stds)).
    The regime variant is fitted separately by
    _fit_logistic_regime (it needs the per-row regime labels).
    """
    means, stds = _standardisation(X_train)
    logistic = fit_logistic(X_train, y_train)
    nb = fit_gaussian_nb(X_train, y_train)
    return logistic, nb, (means, stds)


def _fit_logistic_regime(X_train, y_train, regimes_train):
    """Logistic fit on rows extended with a high-vol indicator.

    Same learner as fit_logistic (lr 0.10, 200 epochs, L2 0.01),
    on 8-wide rows: the 7 features plus 1.0 when the row's regime
    is "high", else 0.0.
    """
    X_ext = [row + [1.0 if r == "high" else 0.0]
             for row, r in zip(X_train, regimes_train)]
    return fit_logistic(X_ext, y_train)


def _logistic_regime_proba(params, row, regime):
    ext = row + [1.0 if regime == "high" else 0.0]
    xs = [0.0 if params["stds"][j] == 0
          else (v - params["means"][j]) / params["stds"][j]
          for j, v in enumerate(ext)]
    return _sigmoid(sum(w * v for w, v in zip(params["weights"], xs))
                    + params["bias"])


def walk_forward_direction(closes, symbol, train_size=TRAIN_SIZE):
    """Walk-forward direction probabilities for one symbol.

    Origins run from ``train_size`` to the second-last close. At
    each origin the models are refit on feature rows with index
    ``< origin`` only, and forecast P(up) for the next close. The
    issued probability for each base model is its Platt-calibrated
    probability once >= 30 past out-of-sample folds exist for that
    model (the Platt fit uses past folds only); before that, the
    raw probability is issued and flagged ``calibrated: False``.
    Ensembles combine the three base models' issued probabilities.
    Baselines are computed per origin from visible data only.

    Returns {"symbol", "n_closes", "train_size", "n_test", "folds"}
    where each fold carries origin, outcome, regime, CUSUM alarm
    count up to the origin, issued + raw probabilities per model,
    and the three baseline probabilities.
    """
    closes = validate_series("closes", closes, min_length=train_size + 2)
    if any(p <= 0 for p in closes):
        raise ValueError("closes must all be positive for walk_forward_direction")
    if symbol not in ("SPY", "AAPL", "VT"):
        raise ValueError(f"walk_forward_direction covers SPY/AAPL/VT, got {symbol!r}")
    if not 0 < train_size < len(closes) - 1:
        raise ValueError("train_size must leave a non-empty test set")
    X, y, indices = feature_rows(closes)
    if not X:
        raise ValueError("no complete feature rows in the series")
    index_pos = {idx: k for k, idx in enumerate(indices)}
    # Regime label per feature row, computed ONCE for the whole
    # series: each label uses closes up to that row's index only,
    # so precomputing it cannot leak (and recomputing it inside the
    # origin loop would be O(n^3)). The origin loop reads, never
    # recomputes, these labels.
    regimes = [regime_label(closes[:idx + 1]) for idx in indices]
    folds = []
    # Past-fold history per model, for expanding Platt fits and for
    # the performance-weighted ensemble's trailing Brier.
    past = {m: {"raw": [], "outcome": []}
            for m in BASE_MODELS + ("logistic_regime",)}
    past_brier = {m: [] for m in BASE_MODELS}
    for origin in range(train_size, len(closes) - 1):
        if origin not in index_pos:
            continue
        k = index_pos[origin]
        X_train, y_train = X[:k], y[:k]
        if len(X_train) < 10:
            continue
        logistic, nb, (means, stds) = _fit_all(X_train, y_train)
        regime_params = _fit_logistic_regime(X_train, y_train, regimes[:k])
        row = X[k]
        raw = {
            "logistic_gd": logistic_proba(logistic, row),
            "gaussian_nb": gaussian_nb_proba(nb, row),
            "score_platt": score_proba(row, means, stds),
        }
        origin_regime = regimes[k]
        raw["logistic_regime"] = _logistic_regime_proba(
            regime_params, row, origin_regime)
        # Expanding Platt calibration on past folds only.
        issued, calibrated_flags = {}, {}
        for model in BASE_MODELS + ("logistic_regime",):
            if len(past[model]["raw"]) >= CALIBRATION_MIN_FOLDS:
                fit = platt_calibrate(past[model]["raw"], past[model]["outcome"])
                issued[model] = calibrated_probability(raw[model], fit["a"], fit["b"])
                calibrated_flags[model] = True
            else:
                issued[model] = raw[model]
                calibrated_flags[model] = False
        # Ensembles from the three base models' issued probabilities.
        base_issued = [issued[m] for m in BASE_MODELS]
        issued["ensemble_equal"] = _mean(base_issued)
        if past_brier["logistic_gd"]:
            recent = {m: past_brier[m][-ENSEMBLE_WEIGHT_WINDOW:]
                      for m in BASE_MODELS}
            weights = {m: 1.0 / (_mean(recent[m]) + 1e-9) for m in BASE_MODELS}
            total = sum(weights.values())
            issued["ensemble_weighted"] = sum(
                issued[m] * weights[m] / total for m in BASE_MODELS)
        else:
            issued["ensemble_weighted"] = issued["ensemble_equal"]
        # Baselines from visible data only.
        base_rate = _mean(y_train)
        _rule_id, rule_dir = direction_for(symbol, closes[:origin + 1])
        rule_prob = (RULE_UP_PROB if rule_dir == "up" else
                     RULE_DOWN_PROB if rule_dir == "down" else base_rate)
        outcome = y[k]
        cusum = cusum_alarms(_simple_returns(closes[:origin + 1]))
        folds.append({
            "origin": origin, "outcome": outcome,
            "regime": origin_regime,
            "cusum_alarms": cusum["n_alarms"],
            "probs": {m: issued[m] for m in EVALUATED_MODELS},
            "raw_probs": {m: raw[m] for m in
                          ("logistic_gd", "gaussian_nb", "score_platt",
                           "logistic_regime")},
            "calibrated": calibrated_flags,
            "baselines": {"coin_flip": 0.5, "base_rate": base_rate,
                          "direction_rule": rule_prob},
        })
        # Record this fold for future origins only (never the current).
        for model in BASE_MODELS + ("logistic_regime",):
            past.setdefault(model, {"raw": [], "outcome": []})
            past[model]["raw"].append(raw[model])
            past[model]["outcome"].append(outcome)
        for model in BASE_MODELS:
            past_brier[model].append((issued[model] - outcome) ** 2)
    return {"symbol": symbol, "n_closes": len(closes),
            "train_size": train_size, "n_test": len(folds), "folds": folds}


# ---------------------------------------------------------------------------
# Pooled comparison + decision rule
# ---------------------------------------------------------------------------

def compare_direction_models(walk_forward_results, baseline="coin_flip",
                             resamples=BOOTSTRAP_RESAMPLES,
                             seed=BOOTSTRAP_SEED):
    """Apply the protocol decision rule to walk-forward results.

    ``walk_forward_results`` is one result dict, or a list of them
    (one per symbol) — the list case pools per-fold Brier
    differences across symbols, which is the protocol's primary
    verdict. A model "beats baseline" only if its pooled mean Brier
    is lower than the baseline's AND the 95% paired-bootstrap CI on
    the mean Brier difference (model − baseline) lies entirely below
    zero. A model is "edge" only if it beats ALL THREE baselines;
    otherwise "no edge". Log score and accuracy are reported, never
    decisive. The regime head-to-head (logistic_regime vs
    logistic_gd) uses the same two-part rule and is reported under
    "regime_conditioning".

    Returns {"baselines", "n_folds", "n_symbols", "models": {...},
    "regime_conditioning": {...}}.
    """
    results = ([walk_forward_results] if isinstance(walk_forward_results, dict)
               else list(walk_forward_results))
    if not results:
        raise ValueError("no walk-forward results to compare")
    if baseline not in BASELINES:
        raise ValueError(f"baseline {baseline!r} not among {BASELINES}")
    pooled = {m: {"probs": [], "outcomes": [], "raw_probs": []}
              for m in EVALUATED_MODELS}
    pooled_base = {b: {"probs": [], "outcomes": []} for b in BASELINES}
    n_folds = 0
    for result in results:
        for fold in result["folds"]:
            n_folds += 1
            for m in EVALUATED_MODELS:
                pooled[m]["probs"].append(fold["probs"][m])
                pooled[m]["outcomes"].append(fold["outcome"])
                if m in fold["raw_probs"]:
                    pooled[m]["raw_probs"].append(fold["raw_probs"][m])
            for b in BASELINES:
                pooled_base[b]["probs"].append(fold["baselines"][b])
                pooled_base[b]["outcomes"].append(fold["outcome"])
    if n_folds == 0:
        raise ValueError("no folds to compare")
    out_models = {}
    for model in EVALUATED_MODELS:
        probs = pooled[model]["probs"]
        outs = pooled[model]["outcomes"]
        mean_brier = brier_score(probs, outs)
        mean_log = log_score(probs, outs)
        accuracy = _mean([1.0 if (p > 0.5) == (o == 1) else 0.0
                          for p, o in zip(probs, outs)])
        raw_brier = (brier_score(pooled[model]["raw_probs"], outs)
                     if pooled[model]["raw_probs"] else None)
        per_baseline = {}
        for b in BASELINES:
            base_probs = pooled_base[b]["probs"]
            base_brier = brier_score(base_probs, outs)
            diffs = [(p - o) ** 2 - (bp - o) ** 2
                     for p, bp, o in zip(probs, base_probs, outs)]
            mean_diff = _mean(diffs)
            lo, hi = _bootstrap_ci(diffs, resamples=resamples, seed=seed)
            per_baseline[b] = {
                "baseline_brier": base_brier,
                "mean_brier_diff": mean_diff,
                "ci_95": (lo, hi),
                "beats_baseline": bool(mean_diff < 0 and hi < 0),
            }
        verdict = ("edge" if all(per_baseline[b]["beats_baseline"]
                                 for b in BASELINES) else "no edge")
        out_models[model] = {
            "mean_brier": mean_brier,
            "mean_brier_raw": raw_brier,
            "mean_log_score": mean_log,
            "accuracy_at_0.5": accuracy,
            "n_folds": n_folds,
            "per_baseline": per_baseline,
            "verdict": verdict,
        }
    # Regime head-to-head: logistic_regime vs logistic_gd.
    reg_probs = pooled["logistic_regime"]["probs"]
    plain_probs = pooled["logistic_gd"]["probs"]
    outs = pooled["logistic_gd"]["outcomes"]
    reg_diffs = [(p - o) ** 2 - (q - o) ** 2
                 for p, q, o in zip(reg_probs, plain_probs, outs)]
    reg_mean_diff = _mean(reg_diffs)
    reg_lo, reg_hi = _bootstrap_ci(reg_diffs, resamples=resamples, seed=seed)
    regime = {
        "mean_brier_diff_vs_plain_logistic": reg_mean_diff,
        "ci_95": (reg_lo, reg_hi),
        "improves": bool(reg_mean_diff < 0 and reg_hi < 0),
        "verdict": ("improves" if reg_mean_diff < 0 and reg_hi < 0
                    else "no edge"),
    }
    return {"baselines": list(BASELINES), "n_folds": n_folds,
            "n_symbols": len(results), "models": out_models,
            "regime_conditioning": regime}
