# Volatility-forecast evaluation protocol (PR 7.2) — PRE-REGISTERED

Written 2026-11-14, **before any evaluation was run**. This protocol
fixes the universe, models, parameters, scoring and decision rule in
advance. No parameter is tuned after seeing results; if the results
are bad, the bad result is published, not re-tuned away. Model rules
(MASTER-ROADMAP.md): a model must beat its baseline out of sample or
be labelled "no edge".

## Universe and data

- Symbols: **SPY, AAPL, VT** — liquid US equities from the programme's
  approved watchlist, with long Yahoo history. No crypto or FX (no
  daily-close concept for a 24-hour market) and no NEPSE (no keyless
  daily feed exists for it).
- Source: Yahoo Finance chart API via the toolkit's own
  `toolkit.data.fetch_yahoo_prices(symbol, range_="2y", interval="1d")`,
  fetched 2026-11-14. Daily closes only, as returned by Yahoo; no
  adjustment, cleaning or filling. A fetch failure aborts the run —
  no invented prices.
- Returns: log returns, `r_t = ln(P_t / P_{t-1})`. Prices must be
  positive; a non-positive price raises instead of producing a return.

## Task

One-day-ahead **variance** forecast: at each origin the model outputs
a forecast `σ̂²` for the next day's realised variance, proxied by the
next day's squared log return, `σ² = r²`. (A single squared return is
a noisy proxy — that noise is exactly what QLIKE is built for; see
Scoring.)

## Models (parameters fixed here, not fitted to the outcome)

All three models are refit/re-anchored at every origin using only
returns available at that origin. Nothing peeks ahead.

1. **Baseline — naive realised-vol**: trailing 20-trading-day
   realised variance, `mean(r²)` over the last 20 returns, carried
   forward as the next-day forecast. No parameters.
2. **EWMA**: `σ²_t = λ·σ²_{t-1} + (1 − λ)·r²_{t-1}` with **λ = 0.94**
   (RiskMetrics daily value), initialised at each origin with the
   sample variance of the first 20 returns of the training slice.
   The forecast for the next day is the recursion's current value.
3. **GARCH-lite**: `σ²_t = ω + α·r²_{t-1} + β·σ²_{t-1}` with
   **α = 0.08, β = 0.90 fixed**; `ω = σ̄²·(1 − α − β)` where `σ̄²` is
   the expanding training-sample variance of returns up to the origin
   (re-anchored every fold). This is "lite" on purpose: no MLE fit,
   no optimiser, stdlib only. It must not be described as a fitted
   GARCH(1,1).

Numerical guard (documented, not tuned): every variance forecast is
floored at `1e-12` so QLIKE's logarithm is always defined. On real
return series the floor never binds.

## Walk-forward split

- Expanding window. The first origin sits after **252 returns**
  (~1 trading year of training). At origin `t` (return index) the
  model sees returns `[:t]` only and forecasts the variance of
  return `t` (the next day). Origins run to the last return.
- Out-of-sample folds: all returns after the first 252, per symbol.
  With ~2 years of daily data that is roughly 250 folds per symbol.

## Scoring

- **Primary: QLIKE** — `QLIKE = ln(σ̂²) + σ²/σ̂²`, averaged over
  out-of-sample folds. Lower is better. QLIKE is the proper scoring
  rule for variance forecasts: it penalises under-forecasting
  (the `σ²/σ̂²` term explodes when the forecast is too small) more
  than over-forecasting, which is the asymmetry a risk user cares
  about.
- **Secondary: MSE on variance** — `mean((σ² − σ̂²)²)`. Reported for
  completeness; it does **not** decide the verdict, because MSE on
  squared single-day returns is dominated by a few large moves.
- Scores are reported per symbol and pooled across symbols.

## Decision rule (the verdict, fixed before running)

Pooled across symbols (SPY + AAPL + VT). A model **beats the
baseline** only if BOTH hold:

1. its pooled mean QLIKE is lower than the baseline's pooled mean
   QLIKE, and
2. the 95% paired-bootstrap confidence interval on the mean QLIKE
   difference (model − baseline), from **2000 resamples, seed
   20261114**, resampling per-fold QLIKE differences pooled across
   symbols, lies **entirely below zero**.

Otherwise the model is labelled **"no edge"** — in the model card and
in the code's verdict strings. A win on MSE alone never counts. A win
on one symbol alone never counts.

## What counts as "models tried"

Two models tried (EWMA, GARCH-lite) plus the baseline. No parameter
search was run and none will be run for this PR: λ, α, β and the
20-day window are fixed above. If neither model beats the baseline,
the PR still ships with the "no edge" label — the evaluation is the
deliverable either way.

## Reproducibility

The evaluation is run by `scripts/evaluate_volatility_forecast.py`
(stdlib only; network only for the Yahoo fetch). Its output is the
input to `docs/volatility-forecast-model-card.md`. The offline test
suite (`tests/test_volatility.py`) never touches the network: it runs
the same scoring and decision-rule code on synthetic fixture series.
