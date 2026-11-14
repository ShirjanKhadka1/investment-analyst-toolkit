# Volatility-forecast model card (PR 7.2)

Research only. Never a buy/sell/hold recommendation. A lower past
out-of-sample QLIKE is not a guarantee of future accuracy.

## Protocol

Pre-registered in `volatility-forecast-eval-protocol.md` **before**
this evaluation ran: SPY / AAPL / VT daily closes from Yahoo
(range 2y), log returns, expanding walk-forward from 252 returns,
1-day-ahead variance forecasts, QLIKE primary, MSE on variance
secondary, verdict by the pooled mean-QLIKE difference with a 95%
paired-bootstrap CI (2000 resamples, seed 20261114). A model beats
the baseline only if its pooled mean QLIKE is lower AND the CI lies
entirely below zero; otherwise "no edge". No parameter was tuned
after seeing results. Reproduce with:

```text
python3 scripts/evaluate_volatility_forecast.py
```

## Run

- **Run date:** 2026-11-14. **Data:** Yahoo chart API via
  `toolkit.data.fetch_yahoo_prices`, range 2y, interval 1d.
- **Series:** 501 closes per symbol, 2024-11-14 to 2026-11-13
  (500 log returns, 248 out-of-sample folds per symbol, 744 pooled).
  Closes as returned by Yahoo; no adjustment, cleaning or filling.
- **Models tried:** 2 (EWMA λ = 0.94; GARCH-lite α = 0.08, β = 0.90,
  ω anchored to training-sample variance) plus the naive baseline
  (trailing 20-day realised variance carried forward).
- **Implementation:** `toolkit/volatility.py` (stdlib only).

## Results — both models beat the baseline out of sample

Pooled across SPY + AAPL + VT (744 folds). Lower QLIKE is better.

| Model | Mean QLIKE | Diff vs naive | 95% CI on diff | Mean MSE on variance | Verdict |
| --- | --- | --- | --- | --- | --- |
| naive (baseline) | −7.9652 | — | — | 1.330e-07 | baseline |
| EWMA (λ = 0.94) | −8.0264 | −0.0612 | [−0.1032, −0.0236] | 1.317e-07 | **beats baseline** |
| GARCH-lite (α = 0.08, β = 0.90) | −8.0643 | −0.0991 | [−0.1718, −0.0385] | 1.317e-07 | **beats baseline** |

Per symbol (mean QLIKE; lower is better):

| Symbol | naive | EWMA | GARCH-lite |
| --- | --- | --- | --- |
| SPY (n = 248) | −8.4041 | −8.4799 | −8.5240 |
| AAPL (n = 248) | −7.1959 | −7.2564 | −7.2957 |
| VT (n = 248) | −8.2955 | −8.3430 | −8.3731 |

Both models beat the baseline on every symbol individually as well
as pooled, and both also have lower pooled MSE on variance — but MSE
did not decide the verdict; QLIKE did, per the protocol.

## Reading (and not over-reading) this

- The wins are **modest**: QLIKE differences of −0.06 and −0.10
  against a mean QLIKE near −8. The CIs exclude zero, so the
  direction is supported on this window, but the size of the edge is
  small — a variance forecast that is slightly less wrong, not a
  crystal ball.
- **Part of the edge may be estimation efficiency, not timing:**
  EWMA's effective memory is longer than the baseline's 20-day
  window, so its forecast is a less noisy estimate of the same
  variance — and QLIKE penalises forecast noise. On synthetic
  constant-volatility series (no volatility dynamics at all) both
  models still beat the 20-day baseline on QLIKE
  (`tests/test_volatility.py`). The win does not by itself prove
  volatility-timing skill. The edge that would prove timing is a win
  over an equal-memory baseline, which this PR does not test.
- **The bootstrap assumes independent folds:** the paired bootstrap
  resamples folds as if independent, but walk-forward forecast errors
  are serially dependent, so the CI is likely too narrow. Treat the
  verdict as supported by the per-symbol consistency (both models win
  on all three symbols individually) rather than as a formal
  significance proof.
- GARCH-lite's pooled QLIKE is lower than EWMA's (−8.0643 vs
  −8.0264), but no head-to-head decision rule was pre-registered, so
  this card makes **no claim** that GARCH-lite beats EWMA. Both beat
  the baseline; the ranking between them is untested.
- **Explicit limits:** one 2-year window (2024-11-14 to 2026-11-13),
  three US equities, one train/test split (252/248), closes only, no
  transaction costs, no dividends/splits adjustment beyond what
  Yahoo returns. The realised-variance proxy is a single squared
  daily return — noisy by construction, which is why QLIKE (not MSE)
  is the primary score. GARCH-lite is not a fitted GARCH(1,1):
  α and β are fixed and only ω is anchored to the training variance.
  This is NOT a statement about general model accuracy, about any
  symbol, or about any other period, horizon or universe.
- **No failure to publish:** neither model failed the protocol, so
  there is no "no edge" label in this card. Had either failed, the
  failure would have been printed here in the same table.
