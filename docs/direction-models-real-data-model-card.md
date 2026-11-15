# Direction models — real-data model card (Round E, E.1)

Research only. Never a buy/sell/hold recommendation. A past
Brier score is not a guarantee of future accuracy.

## Protocol

Pre-registered in `docs/real-data-eval-protocol.md` **before**
this evaluation ran. Universe: 30 liquid US large caps plus 4
ETFs (SPY, VT, QQQ, DIA), chosen from today's liquid names —
**survivorship bias: past losers that delisted are absent, so
every result here is survivorship-conditioned, not unbiased
history.** Prices from Yahoo (5y, 1d; walk-forward uses the
trailing 2y slice, 502 closes per symbol). Walk-forward from
train_size 252, origins run to the second-last close, Platt
calibration on past folds only once >= 30 past folds exist.
A model is "edge" only if its pooled mean Brier is lower than
ALL THREE baselines (coin flip, base rate, direction rule)
AND the 95% paired-bootstrap CI (10,000 resamples, seed
20261114) on each mean Brier difference lies entirely below
zero. Log score and accuracy are reported, never decisive.
Reproduce with:

```text
EDGAR_CONTACT_EMAIL=<contact> python3 scripts/evaluate_real_data.py --refresh --only direction
```

The cache is `data/real-data-eval-2026-11-15.json`.

## Run

- **Run date:** 2026-11-15. **Symbols:** 34, 249 test folds
  each (8,466 pooled). **Models tried:** 3 base models
  (logistic gradient descent, Gaussian naive Bayes, Platt
  score) + the regime-conditioned logistic + 2 ensembles.
- **Implementation:** `toolkit/direction_models.py` (stdlib
  only); evaluation in `scripts/evaluate_real_data.py`.
- **Direction-rule baseline caveat:** the rule baseline exists
  only for the 7 watchlist symbols with pre-registered rules
  (SPY, AAPL, VT, NIFTYBEES.NS, ISF.L, EURUSD=X, BTC-USD).
  For the other 27 symbols it falls back to the base rate
  (recorded per fold as `direction_rule_fallback`), so the
  pooled direction-rule baseline (Brier 0.2569) mixes 7 real
  rules with 27 base-rate fallbacks. No rule was invented for
  any new symbol.

## Results — pooled (n = 8,466 folds)

No model is "edge": none beats all three baselines. Every
model's pooled mean Brier is **worse than a coin flip**
(0.2500) and worse than the base rate (0.2505). The only
baseline any model beats is the direction rule — and that
baseline is itself worse than a coin flip (0.2569), because
the watchlist rules issue 0.9/0.1 probabilities that are
badly calibrated on this window.

| Model | Mean Brier | Mean log score | Accuracy at 0.5 | Verdict |
| --- | --- | --- | --- | --- |
| logistic_gd | 0.2540 | 0.7019 | 50.15% | no edge |
| logistic_regime | 0.2541 | 0.7020 | 50.31% | no edge |
| ensemble_equal | 0.2548 | 0.7033 | 50.33% | no edge |
| ensemble_weighted | 0.2551 | 0.7047 | 50.20% | no edge |
| gaussian_nb | 0.2569 | 0.7111 | 50.13% | no edge |
| score_platt | 0.2648 | 0.7345 | 49.89% | no edge |

| Model | vs coin flip (0.2500) | vs base rate (0.2505) | vs direction rule (0.2569) |
| --- | --- | --- | --- |
| logistic_gd | +0.0040 [0.0025, 0.0056] | +0.0035 [0.0022, 0.0049] | −0.0029 [−0.0047, −0.0007] beats |
| logistic_regime | +0.0041 [0.0026, 0.0056] | +0.0036 [0.0022, 0.0050] | −0.0028 [−0.0048, −0.0006] beats |
| ensemble_equal | +0.0048 [0.0032, 0.0063] | +0.0043 [0.0030, 0.0056] | −0.0021 [−0.0041, 0.0000] |
| ensemble_weighted | +0.0051 [0.0035, 0.0067] | +0.0046 [0.0032, 0.0059] | −0.0018 [−0.0038, 0.0003] |
| gaussian_nb | +0.0069 [0.0050, 0.0088] | +0.0064 [0.0047, 0.0082] | +0.0000 [−0.0022, 0.0024] |
| score_platt | +0.0148 [0.0123, 0.0175] | +0.0143 [0.0119, 0.0168] | +0.0080 [0.0050, 0.0109] |

(Differences are model minus baseline; a negative CI entirely
below zero is a win.)

## Regime conditioning

Logistic-with-regime vs plain logistic: mean Brier
difference +0.0001, 95% CI [−0.0007, +0.0008], "no edge".
Regime conditioning adds nothing on this universe and window.

## Reliability (logistic_gd, pooled)

Probabilities are reasonably calibrated in the middle bins
and overconfident at the extremes: the [0.4, 0.5) bin
predicts 0.47 and observes 0.52 (n = 2,896); the [0.5, 0.6)
bin predicts 0.54 and observes 0.51 (n = 4,485); the [0.7,
0.8) bin predicts 0.73 and observes 0.59 (n = 96); the
[0.8, 0.9) bin predicts 0.82 and observes 0.43 (n = 14).
Per-symbol up rates range 44.2% to 55.8%.
