# Forecast-harness model card (PR 8.1)

Research only. Never a buy/sell/hold recommendation. A lower past out-of-sample MAE or QLIKE is not a guarantee of future accuracy.

## Protocol

Pre-registered in `forecast-harness-eval-protocol.md` **before** this evaluation ran: SPY / AAPL / VT daily closes from Yahoo (range 2y), expanding walk-forward from 252 closes (price) / 252 returns (volatility), horizon 1, 9 tried price models + the naive baseline, EWMA / GARCH-lite + the naive volatility baseline re-run through the PR 7.2 QLIKE rule, paired bootstrap CIs (10,000 resamples, seed 20261114). A price model beats the baseline on a symbol only if its out-of-sample mean MAE is lower than naive AND the 95% CI on the mean MAE difference lies entirely below zero; overall "beats baseline" needs a win on all three symbols, otherwise "no edge". Direction accuracy is reported against 50% and the up-day base rate and never decides a verdict. Reproduce with:

```text
python3 scripts/evaluate_forecast_harness.py
```

## Run

- **Run date:** 2026-11-14. **Data:** Yahoo chart API via `toolkit.data.fetch_yahoo_prices`, range 2y, interval 1d.
- **Models tried:** 11 (9 price + 2 volatility) plus 2 baselines.
- **Implementation:** `toolkit/forecast_harness.py` (stdlib only).
- **SPY:** 501 closes, 2024-11-14 to 2026-11-13 (500 log returns, 249 out-of-sample price folds, 248 volatility folds).
- **AAPL:** 501 closes, 2024-11-14 to 2026-11-13 (500 log returns, 249 out-of-sample price folds, 248 volatility folds).
- **VT:** 501 closes, 2024-11-14 to 2026-11-13 (500 log returns, 249 out-of-sample price folds, 248 volatility folds).

## Results — price models (per symbol)

Pooled verdict rule: overall "beats baseline" only on a win on all three symbols.

### SPY (n = 249 folds)

| Model | MAE | RMSE | MAE diff vs naive | 95% CI on diff | Direction acc. | Up-day base rate | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| naive | 4.6103 | 6.1825 | 0.0000 | [0.0000, 0.0000] | 0.000 (0/249) | 0.514 | baseline |
| drift | 4.6127 | 6.1806 | 0.0024 | [-0.0399, 0.0452] | 0.514 (128/249) | 0.514 | no edge |
| ar1 | 4.6368 | 6.2135 | 0.0265 | [-0.0409, 0.0944] | 0.486 (121/249) | 0.514 | no edge |
| ensemble_adaptive | 5.0116 | 6.5728 | 0.4014 | [0.0806, 0.7242] | 0.558 (139/249) | 0.514 | no edge |
| holt_damped | 6.1080 | 8.0276 | 1.4977 | [0.9374, 2.0718] | 0.562 (140/249) | 0.514 | no edge |
| holt | 6.1165 | 8.0385 | 1.5062 | [0.9440, 2.0781] | 0.562 (140/249) | 0.514 | no edge |
| exp_smoothing_0.3 | 6.2045 | 7.9734 | 1.5943 | [1.0421, 2.1501] | 0.554 (138/249) | 0.514 | no edge |
| moving_average_5 | 6.5828 | 8.4096 | 1.9726 | [1.3642, 2.5820] | 0.538 (134/249) | 0.514 | no edge |
| ensemble_equal | 6.7614 | 8.5855 | 2.1511 | [1.5194, 2.8035] | 0.550 (137/249) | 0.514 | no edge |
| linear_trend | 15.7624 | 19.9602 | 11.1521 | [9.6480, 12.7173] | 0.562 (140/249) | 0.514 | no edge |

### AAPL (n = 249 folds)

| Model | MAE | RMSE | MAE diff vs naive | 95% CI on diff | Direction acc. | Up-day base rate | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| drift | 3.3536 | 4.6848 | -0.0047 | [-0.0267, 0.0173] | 0.526 (131/249) | 0.526 | no edge |
| naive | 3.3584 | 4.6902 | 0.0000 | [0.0000, 0.0000] | 0.000 (0/249) | 0.526 | baseline |
| ar1 | 3.3799 | 4.7252 | 0.0216 | [-0.0270, 0.0698] | 0.474 (118/249) | 0.526 | no edge |
| ensemble_adaptive | 3.8429 | 5.3002 | 0.4845 | [0.2351, 0.7505] | 0.490 (122/249) | 0.526 | no edge |
| holt | 4.7724 | 6.5370 | 1.4140 | [0.9833, 1.8667] | 0.506 (126/249) | 0.526 | no edge |
| holt_damped | 4.7730 | 6.5284 | 1.4146 | [0.9844, 1.8653] | 0.506 (126/249) | 0.526 | no edge |
| exp_smoothing_0.3 | 4.8881 | 6.3826 | 1.5297 | [1.1224, 1.9722] | 0.490 (122/249) | 0.526 | no edge |
| moving_average_5 | 5.1957 | 6.8775 | 1.8374 | [1.3754, 2.3389] | 0.498 (124/249) | 0.526 | no edge |
| ensemble_equal | 6.7868 | 8.2298 | 3.4285 | [2.8511, 4.0304] | 0.510 (127/249) | 0.526 | no edge |
| linear_trend | 19.6571 | 23.0894 | 16.2987 | [14.7026, 17.9110] | 0.506 (126/249) | 0.526 | no edge |

### VT (n = 249 folds)

| Model | MAE | RMSE | MAE diff vs naive | 95% CI on diff | Direction acc. | Up-day base rate | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| drift | 1.0466 | 1.3988 | -0.0000 | [-0.0103, 0.0105] | 0.514 (128/249) | 0.514 | no edge |
| naive | 1.0466 | 1.3988 | 0.0000 | [0.0000, 0.0000] | 0.008 (2/249) | 0.514 | baseline |
| ar1 | 1.0507 | 1.4031 | 0.0041 | [-0.0099, 0.0179] | 0.482 (120/249) | 0.514 | no edge |
| ensemble_adaptive | 1.1334 | 1.4457 | 0.0867 | [0.0179, 0.1548] | 0.510 (127/249) | 0.514 | no edge |
| holt_damped | 1.3411 | 1.7306 | 0.2945 | [0.1710, 0.4210] | 0.502 (125/249) | 0.514 | no edge |
| holt | 1.3420 | 1.7330 | 0.2954 | [0.1718, 0.4212] | 0.502 (125/249) | 0.514 | no edge |
| exp_smoothing_0.3 | 1.3583 | 1.7118 | 0.3116 | [0.1887, 0.4343] | 0.522 (130/249) | 0.514 | no edge |
| ensemble_equal | 1.4238 | 1.8007 | 0.3772 | [0.2418, 0.5164] | 0.498 (124/249) | 0.514 | no edge |
| moving_average_5 | 1.4355 | 1.8051 | 0.3888 | [0.2551, 0.5232] | 0.514 (128/249) | 0.514 | no edge |
| linear_trend | 3.1180 | 3.9795 | 2.0714 | [1.7731, 2.3837] | 0.510 (127/249) | 0.514 | no edge |

## Overall price verdicts

| Model | SPY | AAPL | VT | Overall |
| --- | --- | --- | --- | --- |
| naive | baseline | baseline | baseline | baseline |
| moving_average_5 | no edge | no edge | no edge | no edge |
| linear_trend | no edge | no edge | no edge | no edge |
| exp_smoothing_0.3 | no edge | no edge | no edge | no edge |
| ar1 | no edge | no edge | no edge | no edge |
| holt | no edge | no edge | no edge | no edge |
| holt_damped | no edge | no edge | no edge | no edge |
| drift | no edge | no edge | no edge | no edge |
| ensemble_equal | no edge | no edge | no edge | no edge |
| ensemble_adaptive | no edge | no edge | no edge | no edge |

## Pooled direction accuracy

| Model | Pooled accuracy | 95% CI | Up-day base rate | n |
| --- | --- | --- | --- | --- |
| naive | 0.003 | [0.0000, 0.0067] | 0.518 | 747 |
| moving_average_5 | 0.517 | [0.4806, 0.5529] | 0.518 | 747 |
| linear_trend | 0.526 | [0.4913, 0.5609] | 0.518 | 747 |
| exp_smoothing_0.3 | 0.522 | [0.4873, 0.5582] | 0.518 | 747 |
| ar1 | 0.481 | [0.4444, 0.5167] | 0.518 | 747 |
| holt | 0.523 | [0.4873, 0.5596] | 0.518 | 747 |
| holt_damped | 0.523 | [0.4873, 0.5596] | 0.518 | 747 |
| drift | 0.518 | [0.4819, 0.5529] | 0.518 | 747 |
| ensemble_equal | 0.519 | [0.4833, 0.5542] | 0.518 | 747 |
| ensemble_adaptive | 0.519 | [0.4833, 0.5556] | 0.518 | 747 |

Direction accuracy is reported against 50% (coin flip) and the up-day base rate. It never decides a verdict.

## Probabilistic scores (Brier / log score)

Platt scaling fitted on the first half of each model's out-of-sample folds, scored on the second half only. Lower is better; always forecasting 0.5 scores Brier 0.25 and log score ln 2 ≈ 0.6931.

### SPY

| Model | Brier (eval half) | Brier always-0.5 | Log score (eval half) | Log score always-0.5 |
| --- | --- | --- | --- | --- |
| ar1 | 0.2452 | 0.2500 | 0.6834 | 0.6931 |
| drift | 0.2453 | 0.2500 | 0.6838 | 0.6931 |
| ensemble_adaptive | 0.2540 | 0.2500 | 0.7011 | 0.6931 |
| ensemble_equal | 0.2491 | 0.2500 | 0.6913 | 0.6931 |
| exp_smoothing_0.3 | 0.2561 | 0.2500 | 0.7053 | 0.6931 |
| holt | 0.2612 | 0.2500 | 0.7158 | 0.6931 |
| holt_damped | 0.2612 | 0.2500 | 0.7157 | 0.6931 |
| linear_trend | 0.2490 | 0.2500 | 0.6912 | 0.6931 |
| moving_average_5 | 0.2550 | 0.2500 | 0.7032 | 0.6931 |
| naive | 0.2526 | 0.2500 | 0.6983 | 0.6931 |

### AAPL

| Model | Brier (eval half) | Brier always-0.5 | Log score (eval half) | Log score always-0.5 |
| --- | --- | --- | --- | --- |
| ar1 | 0.2762 | 0.2500 | 0.7486 | 0.6931 |
| drift | 0.2623 | 0.2500 | 0.7181 | 0.6931 |
| ensemble_adaptive | 0.2489 | 0.2500 | 0.6910 | 0.6931 |
| ensemble_equal | 0.2475 | 0.2500 | 0.6881 | 0.6931 |
| exp_smoothing_0.3 | 0.2476 | 0.2500 | 0.6883 | 0.6931 |
| holt | 0.2490 | 0.2500 | 0.6912 | 0.6931 |
| holt_damped | 0.2489 | 0.2500 | 0.6910 | 0.6931 |
| linear_trend | 0.2483 | 0.2500 | 0.6898 | 0.6931 |
| moving_average_5 | 0.2488 | 0.2500 | 0.6907 | 0.6931 |
| naive | 0.2491 | 0.2500 | 0.6913 | 0.6931 |

### VT

| Model | Brier (eval half) | Brier always-0.5 | Log score (eval half) | Log score always-0.5 |
| --- | --- | --- | --- | --- |
| ar1 | 0.2446 | 0.2500 | 0.6822 | 0.6931 |
| drift | 0.2474 | 0.2500 | 0.6879 | 0.6931 |
| ensemble_adaptive | 0.2537 | 0.2500 | 0.7005 | 0.6931 |
| ensemble_equal | 0.2533 | 0.2500 | 0.6998 | 0.6931 |
| exp_smoothing_0.3 | 0.2580 | 0.2500 | 0.7092 | 0.6931 |
| holt | 0.2592 | 0.2500 | 0.7117 | 0.6931 |
| holt_damped | 0.2593 | 0.2500 | 0.7119 | 0.6931 |
| linear_trend | 0.2528 | 0.2500 | 0.6988 | 0.6931 |
| moving_average_5 | 0.2563 | 0.2500 | 0.7059 | 0.6931 |
| naive | 0.2543 | 0.2500 | 0.7017 | 0.6931 |

## Volatility re-run (PR 7.2 rule, same data window)

QLIKE primary (lower is better), MSE on variance secondary. Verdict by the PR 7.2 rule: pooled mean QLIKE lower than naive AND the 95% paired-bootstrap CI entirely below zero, else "no edge". The 7.2 card's numbers came from its own fetch window; this re-run fetches the 2y window fresh, so the values are whatever today's window gives. The rule is the same, and any verdict change would be published here, not resolved silently.

### SPY (n = 248 folds)
| Model | Mean QLIKE | Mean MSE on variance |
| --- | --- | --- |
| garch_lite | -8.5240 | 2.817e-08 |
| ewma | -8.4799 | 2.828e-08 |
| naive | -8.4041 | 2.882e-08 |

### AAPL (n = 248 folds)
| Model | Mean QLIKE | Mean MSE on variance |
| --- | --- | --- |
| garch_lite | -7.2957 | 3.357e-07 |
| ewma | -7.2564 | 3.353e-07 |
| naive | -7.1959 | 3.383e-07 |

### VT (n = 248 folds)
| Model | Mean QLIKE | Mean MSE on variance |
| --- | --- | --- |
| garch_lite | -8.3731 | 3.133e-08 |
| ewma | -8.3430 | 3.149e-08 |
| naive | -8.2955 | 3.195e-08 |

Pooled across symbols (n = 744 folds, 3 symbols):
| Model | Mean QLIKE | Diff vs naive | 95% CI on diff | Mean MSE on variance | Verdict |
| --- | --- | --- | --- | --- | --- |
| garch_lite | -8.0643 | -0.0991 | [-0.1718, -0.0385] | 1.317e-07 | beats baseline |
| ewma | -8.0264 | -0.0612 | [-0.1032, -0.0236] | 1.317e-07 | beats baseline |
| naive | -7.9652 | 0.0000 | [0.0000, 0.0000] | 1.330e-07 | baseline |

Reproduction check: the pooled mean QLIKE values reproduce the 7.2 card to 4 decimals — the 2y window is unchanged from the 7.2 fetch.

## Purged / embargoed second split check (purge 5, embargo 5)

Same origins, training window drops its last 5 observations and the test observation is delayed 5 observations after the origin. Verdicts below use the same decision rule. If a model's verdict differs between the two splits, both tables show it — a win on one split alone never changes the primary verdict.

| Model | SPY | AAPL | VT | Overall |
| --- | --- | --- | --- | --- |
| naive | baseline | baseline | baseline | baseline |
| moving_average_5 | no edge | no edge | no edge | no edge |
| linear_trend | no edge | no edge | no edge | no edge |
| exp_smoothing_0.3 | no edge | no edge | no edge | no edge |
| ar1 | no edge | no edge | no edge | no edge |
| holt | no edge | no edge | no edge | no edge |
| holt_damped | no edge | no edge | no edge | no edge |
| drift | no edge | beats baseline | no edge | no edge |
| ensemble_equal | no edge | no edge | no edge | no edge |
| ensemble_adaptive | no edge | no edge | no edge | no edge |

## Daily stream scoring (docs/forecast-scoring-protocol.md)

Forecasts made: **36**, scored: **34**, pending: **2**. Claim: **insufficient n — no claim** (no claim until 100 scored forecasts; the 34 scored forecasts in the file are the labelled development period, not a validation set).

| | MAE | RMSE | Baseline MAE | Baseline RMSE | MAE diff | 95% CI | Direction acc. | Base rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Pooled (n = 34) | 5.3916 | 8.9055 | 6.0509 | 9.7232 | -0.6593 | [-2.2258, 0.6225] | 0.500 | 0.294 |
| AAPL (n = 17) | 3.9141 | 5.5193 | 3.9176 | 5.9120 | -0.0035 | [-1.1417, 1.0628] | 0.471 | 0.294 |
| MSFT (n = 17) | 6.8690 | 11.3204 | 8.1841 | 12.4149 | -1.3151 | [-4.2520, 0.9531] | 0.529 | 0.294 |

## Experiment record

This run is recorded in `forecast-harness-experiments.jsonl` (config + model count + verdicts, one append-only line per run).
