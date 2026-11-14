# Direction-models model card (PR 8.2)

Research only. Never a buy/sell/hold recommendation. A past
out-of-sample Brier score is not a guarantee of future accuracy.

## Protocol

Pre-registered in `direction-models-eval-protocol.md` **before**
this evaluation ran: SPY / AAPL / VT daily closes from Yahoo
(range 2y), expanding walk-forward from 252 closes, 1-day-ahead
P(up next day), Brier primary, log score secondary, verdict by the
pooled mean-Brier difference with a 95% paired-bootstrap CI (2000
resamples, seed 20261115). A model is "edge" only if it beats ALL
THREE baselines (coin-flip, base rate, direction rule) — lower
pooled mean Brier AND the CI entirely below zero for each; otherwise
"no edge". No parameter was tuned after seeing results. Reproduce
with:

```text
python3 scripts/evaluate_direction_models.py
```

Determinism check: the evaluation was run twice on 2026-11-14 and
the pooled numbers reproduced exactly on the second run.

## Run

- **Run date:** 2026-11-14. **Data:** Yahoo chart API via
  `toolkit.data.fetch_yahoo_prices`, range 2y, interval 1d.
- **Series:** 501 closes per symbol, 2024-11-14 to 2026-11-13
  (248 out-of-sample folds per symbol, 744 pooled). Closes as
  returned by Yahoo; no adjustment, cleaning or filling. Up-day
  rates in the test folds: SPY 0.516, AAPL 0.528, VT 0.516.
- **Models tried:** 3 base models (logistic gradient-descent,
  lr 0.10 / 200 epochs / L2 0.01; Gaussian naive Bayes; fixed-weight
  score) + 1 regime-conditioned variant (logistic + high-volatility
  indicator) + 2 ensembles (equal-weight, inverse-trailing-Brier
  weighted) = 6 evaluated entries, plus the 3 baselines.
- **Implementation:** `toolkit/direction_models.py` (stdlib only).

## Results — no edge: every model loses to a coin flip

Pooled across SPY + AAPL + VT (744 folds). Lower Brier is better;
0.25 is the score of always forecasting 0.5.

| Model | Mean Brier | Raw Brier | Mean log score | Accuracy @0.5 | Verdict |
| --- | --- | --- | --- | --- | --- |
| coin_flip (baseline) | 0.2500 | — | 0.6931 | 0.480 | baseline |
| base_rate (baseline) | 0.2511 | — | — | 0.520 | baseline |
| direction_rule (baseline) | 0.3239 | — | — | — | baseline |
| logistic_gd | 0.2559 | 0.2512 | 0.7056 | 0.499 | **no edge** |
| gaussian_nb | 0.2599 | 0.2719 | 0.7139 | 0.496 | **no edge** |
| score_platt | 0.2636 | 0.3348 | 0.7263 | 0.496 | **no edge** |
| logistic_regime | 0.2549 | 0.2524 | 0.7034 | 0.495 | **no edge** |
| ensemble_equal | 0.2562 | — | 0.7058 | 0.495 | **no edge** |
| ensemble_weighted | 0.2561 | — | 0.7055 | 0.497 | **no edge** |

Mean Brier difference vs each baseline (model − baseline) with the
95% paired-bootstrap CI; "beats" only when the CI lies entirely
below zero:

| Model | vs coin_flip | vs base_rate | vs direction_rule |
| --- | --- | --- | --- |
| logistic_gd | +0.0059 [+0.0009, +0.0108] | +0.0048 [+0.0013, +0.0085] | −0.0680 [−0.0843, −0.0520] ✓ |
| gaussian_nb | +0.0099 [+0.0016, +0.0175] | +0.0088 [+0.0026, +0.0148] | −0.0640 [−0.0812, −0.0467] ✓ |
| score_platt | +0.0136 [+0.0065, +0.0215] | +0.0125 [+0.0060, +0.0199] | −0.0603 [−0.0789, −0.0419] ✓ |
| logistic_regime | +0.0049 [+0.0006, +0.0091] | +0.0039 [+0.0012, +0.0065] | −0.0690 [−0.0855, −0.0528] ✓ |
| ensemble_equal | +0.0062 [+0.0009, +0.0113] | +0.0052 [+0.0015, +0.0086] | −0.0677 [−0.0842, −0.0506] ✓ |
| ensemble_weighted | +0.0061 [+0.0007, +0.0112] | +0.0050 [+0.0015, +0.0083] | −0.0678 [−0.0845, −0.0508] ✓ |

Every model beats the direction-rule baseline by a wide margin —
but that baseline is a straw man in probability terms: the fixed
0.75 / 0.25 mapping is far too confident for a rule whose
thresholded accuracy is only 0.496, so its Brier (0.3239) is much
worse than guessing 0.5. Against the two honest baselines every
model loses, and the CIs lie entirely ABOVE zero: on this window
the models are significantly *worse* than a coin flip, not merely
indistinguishable from one. The best entry, logistic_regime
(Brier 0.2549), still trails the coin flip by 0.0049 with a CI of
[+0.0006, +0.0091].

Per symbol (mean Brier; lower is better):

| Symbol | logistic_gd | gaussian_nb | score_platt | logistic_regime | ens_equal | ens_weighted | coin_flip | base_rate | direction_rule |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SPY (n = 248) | 0.2573 | 0.2618 | 0.2659 | 0.2541 | 0.2572 | 0.2569 | 0.2500 | 0.2514 | 0.3448 |
| AAPL (n = 248) | 0.2549 | 0.2578 | 0.2568 | 0.2556 | 0.2542 | 0.2544 | 0.2500 | 0.2500 | 0.3125 |
| VT (n = 248) | 0.2555 | 0.2602 | 0.2681 | 0.2551 | 0.2574 | 0.2570 | 0.2500 | 0.2518 | 0.3145 |

No model beats the coin flip on any single symbol either — the
failure is consistent, not one bad symbol.

## Calibration — Platt helped two models, hurt two

"Raw Brier" above is the uncalibrated probability; "Mean Brier" is
the issued (expanding-Platt-calibrated, once ≥ 30 past folds)
probability. Calibration is fitted on past folds only, so this is
a fair comparison — and it cuts both ways:

- score_platt: 0.3348 → 0.2636 (the fixed-weight sigmoid was wildly
  overconfident raw; Platt pulled it most of the way back).
- gaussian_nb: 0.2719 → 0.2599 (helped).
- logistic_gd: 0.2512 → 0.2559 (hurt — the Platt fit on past folds
  miscalibrated the present; past-fold calibration is no guarantee
  when the relationship drifts).
- logistic_regime: 0.2524 → 0.2549 (hurt, same reason).

Reliability bins for the best entry (logistic_regime, pooled 744
folds; the other models look the same in substance):

| Bin | n | Mean issued p | Observed up-rate |
| --- | --- | --- | --- |
| [0.3, 0.4) | 2 | 0.383 | 1.000 |
| [0.4, 0.5) | 67 | 0.483 | 0.627 |
| [0.5, 0.6) | 609 | 0.540 | 0.511 |
| [0.6, 0.7) | 62 | 0.632 | 0.484 |
| [0.7, 0.8) | 4 | 0.741 | 0.500 |
| other bins | 0 | — | — |

82% of folds sit in [0.5, 0.6) with an observed up-rate of 0.511 —
the model mostly says "slightly up", and slightly-up days do go up
slightly more often than not. But the moment it gets confident,
the signal vanishes: the [0.6, 0.7) bin observes 0.484, below the
base rate. The features carry a whisper of signal near 0.5 and
nothing beyond it — exactly what a Brier just above 0.25 says.

## Regime detection — regimes are sticky, conditioning is no edge

- **High-volatility folds:** SPY 107/248, AAPL 101/248, VT 153/248.
- **CUSUM alarms (full 2y series):** SPY 2, AAPL 4, VT 3 — genuine
  mean shifts in daily returns are rare on this window.
- **HMM-lite transitions (full 2y series):** SPY calm→calm 258,
  calm→volatile 15, volatile→calm 14, volatile→volatile 193;
  AAPL 239 / 12 / 11 / 218; VT 216 / 14 / 13 / 237. Volatility
  states are highly persistent: once volatile, the next bar stays
  volatile ~93% of the time; once calm, it stays calm ~95% of the
  time. The regime label describes the recent past well.
- **Regime conditioning head-to-head:** logistic_regime vs plain
  logistic_gd, pooled mean Brier difference −0.0010 with 95% CI
  [−0.0033, +0.0013]. The CI includes zero, so regime conditioning
  is labelled **"no edge"** on this window. The conditioned model
  is the best entry by a hair (0.2549 vs 0.2559), but a hair with a
  CI crossing zero is not an improvement — it is noise.

## Reading (and not over-reading) this

- **The failure is informative, not a bug hunt:** all six entries
  lose to a coin flip on all three symbols, with CIs entirely above
  zero. The honest reading is that 1-day direction from these
  seven price-only features is not predictable on this window — the
  standard result for daily equity direction, and the reason the
  programme's track record uses fixed rules rather than fitted
  models. Nothing here was re-tuned after seeing the result: the
  protocol fixed every parameter before the first run, and the
  "no edge" labels are the deliverable.
- **The bootstrap assumes independent folds:** the paired bootstrap
  resamples folds as if independent, but walk-forward forecast
  errors are serially dependent, so the CIs are likely too narrow.
  The verdict does not rest on the CIs alone: no model beats the
  coin flip on any single symbol, and accuracy at 0.5 sits at
  0.495–0.499 for every model — below the base-rate accuracy of
  0.520 (always predicting up). Three independent-looking failures
  of the same kind is the evidence, not one CI.
- **The direction-rule "win" is not an edge:** beating the
  direction-rule baseline only shows the 0.75 / 0.25 mapping is
  overconfident. The protocol requires beating ALL THREE baselines
  for "edge", precisely so a win against one weak baseline cannot
  be dressed up as skill.
- **Log score agrees with Brier against the coin flip:** every
  model's log score (0.7034–0.7263) is worse than the coin flip's
  −ln(0.5) = 0.6931, so the verdict does not depend on the choice
  of proper scoring rule. (Baseline log scores other than the coin
  flip's were not computed; the verdict rule uses Brier only.)
- **Explicit limits:** one 2-year window (2024-11-14 to
  2026-11-13), three US equities, one train/test split (252/248),
  closes only, seven price-only features, no transaction costs, no
  dividends/splits adjustment beyond what Yahoo returns. A
  different window, universe or feature set could give a different
  answer — this card makes no claim about any of them. This is NOT
  a statement about general model accuracy, about any symbol, or
  about any other period, horizon or universe.
- **No failure hidden:** all six evaluated entries failed the
  protocol, and all six failures are printed in the tables above
  with their CIs. Had any entry passed, it would have been printed
  in the same tables.
