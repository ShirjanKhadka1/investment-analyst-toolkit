# Forecast evaluation harness — evaluation protocol (PR 8.1) — PRE-REGISTERED

Written 2026-11-14, **before any evaluation was run**. This protocol
fixes the universe, models, parameters, splits, scoring and decision
rule in advance. No parameter is tuned after seeing results; if the
results are bad, the bad result is published as "no edge", not
re-tuned away. Model rules (MASTER-ROADMAP.md): a model must beat its
baseline out of sample or be labelled "no edge".

## Pre-registered summary (everything fixed before the first run)

- **Price models in the run: 10** — the naive baseline plus **9
  tried models**: moving_average_5, linear_trend,
  exp_smoothing_0.3, ar1, holt, holt_damped, drift,
  ensemble_equal, ensemble_adaptive. The baseline is not a tried
  model.
- **Volatility models tried: 2** (EWMA, GARCH-lite) plus the naive
  baseline, re-run through the PR 7.2 rule (QLIKE, same protocol).
- **Total models tried: 11** (9 price + 2 volatility), plus 2
  baselines. No parameter search was run and none will be run.
- **Loss functions (price):** MAE primary, RMSE secondary.
  Direction accuracy vs 50% and vs the up-day base rate.
- **Proper scoring rules (probabilistic):** Brier score and log
  score, with a 10-bin calibration curve.
- **Baselines:** price — naive no-change forecast (next value = last
  value); volatility — naive trailing-20-day realised variance.
- **Splits:** expanding walk-forward from 252 closes (price) /
  252 returns (volatility); plus a purged/embargoed variant
  (purge 5, embargo 5) as a second split check on the same folds.
- **Bootstrap:** paired, 10,000 resamples, seed 20261114, 95%
  percentile CI on the mean paired difference.
- **Decision rule:** see below. A win on RMSE alone, on one symbol
  alone, or on direction alone never counts.

## Universe and data

- Symbols: **SPY, AAPL, VT** — the same liquid US symbols as the
  PR 7.2 volatility protocol, with long Yahoo history. No crypto or
  FX (no daily-close concept for a 24-hour market) and no NEPSE
  (no keyless daily feed exists for it).
- Source: Yahoo Finance chart API via the toolkit's own
  `toolkit.data.fetch_yahoo_prices(symbol, range_="2y", interval="1d")`,
  fetched on the run date. Daily closes only, as returned by Yahoo;
  no adjustment, cleaning or filling. A fetch failure aborts the
  run — no invented prices.
- Price series: closes as returned. Returns: log returns,
  `r_t = ln(P_t / P_{t-1})`, for the volatility leg only.

## Task

One-day-ahead (horizon 1) **price** forecast and one-day-ahead
**variance** forecast, evaluated walk-forward. At each origin the
model sees only data available at that origin and is judged on the
next observation.

## Price models (parameters fixed here, not fitted to the outcome)

All models are functions of the training window only, refit /
re-anchored at every origin. Nothing peeks ahead.

1. **Baseline — naive**: next value = last value.
2. **moving_average_5**: mean of the last 5 closes.
3. **linear_trend**: OLS trend on the time index, forecast 1 step
   ahead.
4. **exp_smoothing_0.3**: exponential smoothing, alpha = 0.3.
5. **ar1**: AR(1) forecast from the training window.
6. **holt**: Holt linear trend, alpha = 0.3, beta = 0.1, undamped.
7. **holt_damped**: Holt with damping 0.9.
8. **drift**: last value plus the training-window mean change.
9. **ensemble_equal**: equal-weight mean of naive, moving_average_5,
   linear_trend and exp_smoothing_0.3.
10. **ensemble_adaptive**: inverse-recent-MAE-weighted mean of the
    same members, weights from the training window only.

## Volatility models

Re-run of PR 7.2, unchanged: naive trailing-20-day realised
variance, EWMA (lambda = 0.94), GARCH-lite (alpha = 0.08,
beta = 0.90, omega anchored to the training-sample variance).
Scored by QLIKE (primary) and MSE on variance (secondary), verdict
by the PR 7.2 decision rule (pooled mean QLIKE lower AND the 95%
paired-bootstrap CI on the mean QLIKE difference entirely below
zero, else "no edge"). The 7.2 verdict is reproduced, not
re-decided: if the re-run disagrees with the 7.2 card, the
disagreement is published and investigated, not silently resolved.

## Splits

- **Expanding walk-forward (primary).** First origin after 252
  closes (price) / 252 returns (volatility). At origin `t` the
  model sees `series[:t]` only and is judged on the next
  observation. Origins run to the last observation.
- **Purged / embargoed walk-forward (second split check).** Same
  origins, but the training window drops its last **purge = 5**
  observations, and the test observation is delayed by
  **embargo = 5** observations after the origin. So the last
  training index and the test index are always at least
  purge + embargo + 1 apart, and no observation is ever both
  training and test. The purged/embargoed run is a robustness
  check on the same decision rule: a model that beats the
  baseline only on the unpurged split has its verdict reported
  with that fact attached, and the primary verdict is the
  expanding walk-forward one. The split code is tested by a
  no-look-ahead test: perturbing any observation after an origin
  cannot change that origin's forecast.
- **No other split** is used. In particular there is no random
  shuffle split and no in-sample fit report.

## Scoring

### Price forecasts (per symbol, horizon 1)

- **MAE** (primary) and **RMSE** (secondary) of (actual −
  forecast), out of sample, per model per symbol.
- **Baseline comparison:** MAE difference (model − naive) per
  fold, paired. The 95% paired-bootstrap CI (10,000 resamples,
  seed 20261114) on the mean MAE difference is reported per
  symbol.
- **Direction:** a forecast implies a direction — up when the
  forecast is above the last close, down when below (a forecast
  exactly equal to the last close predicts no move and counts as
  a wrong direction call when the actual moves, and as correct
  only when the actual is exactly unchanged). Direction accuracy
  is the fraction of folds called right, reported per symbol and
  pooled across symbols, against **50%** (coin flip) and against
  the **up-day base rate** (fraction of folds where the actual
  close is above the last close). Pooled accuracy carries a
  bootstrap CI (same resamples and seed) on the pooled accuracy.

### Probabilistic forecasts (Brier, log score, calibration)

Point forecasts carry no probability, so probabilities are built
one documented way: the out-of-sample **score** for a fold is the
signed distance (forecast − last close); the **outcome** is 1
when the actual close is above the last close, 0 otherwise. The
out-of-sample folds are split by time at their midpoint: Platt
scaling is fitted on the **first half** only, and Brier score,
log score and the calibration curve are computed on the **second
half** only. Fitting the calibrator on the evaluated half would
overstate calibration, so it is never done.

- **Brier score** (lower is better; 0.25 is always forecasting
  0.5). Baselines: always-0.5 (Brier 0.25) and the base-rate
  constant (Brier = base rate × (1 − base rate)).
- **Log score** (lower is better; ln 2 ≈ 0.6931 is always
  forecasting 0.5). Probabilities are clipped to [1e-15,
  1 − 1e-15] so the logarithm is always defined; the clip is a
  numerical guard, not a tuning choice.
- **Calibration curve:** 10 equal-width bins over [0, 1], each
  bin reporting its count, mean predicted probability and
  observed frequency. Empty bins are shown with count 0, never
  dropped — a bin with no forecasts is a fact about the model.

### Volatility forecasts

QLIKE primary, MSE on variance secondary, per the PR 7.2
protocol (see "Volatility models").

## Decision rule (the verdict, fixed before running)

Per symbol, a price model **beats the baseline** only if BOTH hold:

1. its out-of-sample mean MAE is lower than the naive baseline's
   mean MAE on that symbol, and
2. the 95% paired-bootstrap CI on the mean MAE difference
   (model − naive) lies **entirely below zero**.

Otherwise the model is labelled **"no edge"** on that symbol. A
model is labelled **"beats baseline" overall** only if it beats
the baseline on **all three symbols**; otherwise its overall
label is "no edge". A win on RMSE alone, on direction alone, or
on one symbol alone never counts. Direction is reported
alongside, never as the verdict: a model can beat the baseline
on MAE while its direction accuracy sits at 50%, and both facts
are published.

Volatility verdicts follow the PR 7.2 rule (pooled QLIKE, CI
entirely below zero). The probabilistic scores (Brier, log score,
calibration) are reported against their baselines and never
rewrite a MAE or QLIKE verdict.

## What counts as "models tried"

9 price models tried (moving_average_5, linear_trend,
exp_smoothing_0.3, ar1, holt, holt_damped, drift,
ensemble_equal, ensemble_adaptive) + 2 volatility models tried
(EWMA, GARCH-lite) = **11 models tried**, plus 2 baselines
(price naive, volatility naive). No parameter search was run
and none will be run for this PR: every parameter above is
fixed in this protocol. The count is printed in the model card
and in every harness experiment record.

## Reproducibility

The evaluation is run by `scripts/evaluate_forecast_harness.py`
(stdlib only; network only for the Yahoo fetch). Its output is
the input to `docs/forecast-harness-model-card.md`. The offline
test suite (`tests/test_forecast_harness.py`) never touches the
network: it runs the same scoring, split and decision-rule code
on synthetic fixture series.
