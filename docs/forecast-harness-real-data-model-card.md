# Forecast harness — real-data model card (Round E, E.1)

Research only. Never a buy/sell/hold recommendation. A lower
past out-of-sample MAE or QLIKE is not a guarantee of future
accuracy.

## Protocol

Pre-registered in `docs/real-data-eval-protocol.md` **before**
this evaluation ran. Universe: 30 liquid US large caps plus 4
ETFs (SPY, VT, QQQ, DIA), chosen from today's liquid names —
**survivorship bias: past losers that delisted are absent, so
every result here is survivorship-conditioned, not unbiased
history.** Prices from Yahoo (5y, 1d; the harness uses the
trailing 2y slice, 502 closes per symbol). Expanding
walk-forward from 252 closes (price) / 252 returns
(volatility), horizon 1, purge 5, embargo 5, 10 price models
plus the naive baseline, EWMA / GARCH-lite plus the naive
volatility baseline, paired bootstrap CIs (10,000 resamples,
seed 20261114). A price model beats the baseline on a symbol
only if its out-of-sample mean MAE is lower than naive AND
the 95% CI on the mean MAE difference lies entirely below
zero; overall "beats baseline" needs a win on all 34 symbols,
otherwise "no edge". Direction accuracy is reported against
50% and the up-day base rate and never decides a verdict.
Reproduce with:

```text
EDGAR_CONTACT_EMAIL=<contact> python3 scripts/evaluate_real_data.py --refresh --only harness
```

The cache is `data/real-data-eval-2026-11-15.json`.

## Run

- **Run date:** 2026-11-15. **Symbols:** 34 (30 caps + SPY,
  VT, QQQ, DIA), 502 closes each, 2024-11-13 to 2026-11-13.
- **Models tried:** 11 — 10 price models (including the naive
  baseline) + 2 volatility models (EWMA, GARCH-lite).
- **Implementation:** `toolkit/forecast_harness.py` (stdlib
  only); evaluation in `scripts/evaluate_real_data.py`.

## Results — price models (overall, 34 symbols)

Every price model is "no edge" overall: none beats the naive
baseline on all 34 symbols. Per-symbol wins exist (drift
beats the baseline on 4 symbols, AR(1) on 7, the equal
ensemble on 3) but the overall rule needs a win everywhere.

| Model | Overall verdict |
| --- | --- |
| naive | baseline |
| drift | no edge |
| ar1 | no edge |
| moving_average_5 | no edge |
| linear_trend | no edge |
| exp_smoothing_0.3 | no edge |
| holt | no edge |
| holt_damped | no edge |
| ensemble_equal | no edge |
| ensemble_adaptive | no edge |

## Results — pooled direction accuracy (n = 8,330 folds)

No model beats the up-day base rate (51.42%). Direction
accuracy is reported, never decisive.

| Model | Accuracy | 95% CI |
| --- | --- | --- |
| ensemble_equal | 50.91% | [49.84%, 51.99%] |
| exp_smoothing_0.3 | 50.90% | [49.83%, 51.99%] |
| moving_average_5 | 50.90% | [49.82%, 51.98%] |
| drift | 50.58% | [49.50%, 51.63%] |
| ensemble_adaptive | 50.64% | [49.58%, 51.72%] |
| holt | 50.48% | [49.42%, 51.58%] |
| holt_damped | 50.46% | [49.39%, 51.55%] |
| naive | 50.52% | [49.45%, 51.58%] |
| ar1 | 50.44% | [49.38%, 51.52%] |
| linear_trend | 49.60% | [48.52%, 50.66%] |

## Results — pooled volatility (n = 8,466 folds, 34 symbols)

Both volatility models **beat the naive baseline** on real
data, pooled: the 95% CI on the mean QLIKE difference lies
entirely below zero for both. This matches the 2026-11-14
backtest direction (EWMA −8.0264, GARCH-lite −8.0643 vs
naive −7.9652 on SPY/AAPL/VT) on a 34-symbol universe.

| Model | Mean QLIKE | Mean QLIKE diff vs naive | 95% CI on diff | Mean MSE on variance | Verdict |
| --- | --- | --- | --- | --- | --- |
| naive | −6.8533 | 0.0000 | [0.0000, 0.0000] | 2.134e-06 | baseline |
| ewma | −6.9128 | −0.0595 | [−0.0750, −0.0454] | 2.093e-06 | beats baseline |
| garch_lite | −6.9292 | −0.0759 | [−0.0990, −0.0561] | 2.092e-06 | beats baseline |
