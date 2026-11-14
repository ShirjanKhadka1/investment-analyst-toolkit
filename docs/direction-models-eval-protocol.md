# Direction-models evaluation protocol (PR 8.2) — PRE-REGISTERED

Written 2026-11-14, **before any evaluation was run**. This protocol
fixes the universe, models, parameters, features, baselines, splits,
scoring and decision rule in advance. No parameter is tuned after
seeing results; if the results are bad, the bad result is published
as "no edge", not re-tuned away. Model rules (MASTER-ROADMAP.md): a
model must beat its baseline out of sample or be labelled "no edge".

## Pre-registered summary (everything fixed before the first run)

- **Models tried: 3 base models** (logistic gradient-descent,
  Gaussian naive Bayes, fixed-weight score) **+ 1
  regime-conditioned variant** (logistic + high-volatility indicator)
  **+ 2 ensembles** (equal-weight, performance-weighted) = **6
  evaluated entries**. No parameter search was run and none will be
  run for this PR.
- **Features (7, fixed):** mom5, mom20, sma20_gap, sma50_gap,
  rsi14-scaled, vol20, ret1 — all computed from closes only, each
  feature at bar `i` using closes up to `i` only.
- **Primary score: Brier score** on the probability of an up move.
  Secondary: log score (probabilities clipped to [1e-6, 1-1e-6]).
- **Baselines (fixed):** 50% coin-flip, train-window base rate of
  up-days, and the pre-registered direction rules
  (`sma50_trend` for SPY/AAPL/VT) mapped to probabilities.
- **Splits:** expanding walk-forward from 252 closes, ~248 folds
  per symbol, symbols SPY / AAPL / VT, Yahoo 2y closes.
- **Decision rule:** pooled across symbols; a model beats a baseline
  only if its pooled mean Brier is lower AND the 95%
  paired-bootstrap CI on the mean Brier difference (model −
  baseline), from **2000 resamples, seed 20261115**, lies entirely
  below zero. "Edge" only when a model beats ALL THREE baselines.
- **Calibration:** reliability bins (10 equal-width bins with counts)
  and expanding Platt calibration fitted on past folds only
  (once >= 30 past folds exist). Calibration is reported, never
  used to rewrite the Brier verdict.

## Universe and data

- Symbols: **SPY, AAPL, VT** — liquid US equities from the
  programme's approved watchlist, with long Yahoo history. No crypto
  or FX (no daily-close concept for a 24-hour market) and no NEPSE
  (no keyless daily feed exists for it).
- Source: Yahoo Finance chart API via the toolkit's own
  `toolkit.data.fetch_yahoo_prices(symbol, range_="2y", interval="1d")`,
  fetched 2026-11-14. Daily closes only, as returned by Yahoo; no
  adjustment, cleaning or filling. A fetch failure aborts the run —
  no invented prices.
- Prices must be positive; a non-positive price raises instead of
  producing a return.
- Outcome: `y = 1` when the next day's close is strictly above
  today's close, else `0`. A flat close (exactly equal) counts as
  down (`0`) — the tie rule is fixed here, not chosen after seeing
  how many ties there are.

## Features (fixed here, computed from closes only)

At bar index `i` (closes `[:i+1]` only — no look-ahead), with
`P` the close at `i`:

| Feature | Definition |
|---|---|
| `mom5` | `P / P[i-5] - 1` (5-trading-day simple return) |
| `mom20` | `P / P[i-20] - 1` (20-trading-day simple return) |
| `sma20_gap` | `P / mean(P[i-19..i]) - 1` |
| `sma50_gap` | `P / mean(P[i-49..i]) - 1` |
| `rsi14` | `(RSI(14) - 50) / 50`, RSI from `toolkit/indicators.py` (Wilder) |
| `vol20` | population std of the last 20 one-day simple returns |
| `ret1` | `P / P[i-1] - 1` (last one-day return) |

Warm-up: a feature row exists only for `i >= 50` (the sma50 window).
Standardisation (logistic and score models): each feature is
standardised with the mean and population std of that feature over
the training rows only; a zero training std maps the feature to 0.
Standardisation statistics are recomputed at every origin from the
training rows visible at that origin — never from the test row.

## Models (parameters fixed here, not fitted to the outcome)

All models are refit at every origin using only training rows
visible at that origin. Each outputs a probability of an up move,
never a hard call. All stdlib only; no LightGBM, no LSTM, no
Transformers, no GATs, no scikit-learn — the matrix row "Model zoo
(LightGBM / LSTM / Transformers / GATs)" stays NOT PLANNED.

1. **logistic_gd** — logistic regression fitted by plain batch
   gradient descent on the standardised features: learning rate
   0.10, 200 epochs, L2 penalty λ = 0.01, intercept unpenalised,
   weights initialised to 0. No optimiser, no library.
2. **gaussian_nb** — Gaussian naive Bayes: per-class (up/down)
   mean and variance per feature, variance floored at 1e-6, prior
   = class base rate in the training rows. Log-probabilities are
   combined in log space (no underflow-prone raw products).
3. **score_platt** — fixed-weight score on standardised features,
   `score = 0.5·mom20 + 0.3·mom5 + 0.4·sma20_gap + 0.4·sma50_gap
   + 0.2·rsi14 − 0.2·vol20 + 0.1·ret1`, passed through a sigmoid.
   The weights are FIXED here, not fitted. The raw sigmoid output
   is then Platt-calibrated (see Calibration).
4. **logistic_regime** — logistic_gd with one extra binary feature:
   `1` when the origin's volatility regime (see Regime detection)
   is "high", else `0`. Same learning rate, epochs and λ as
   logistic_gd. This is the regime-conditioning test: it is judged
   against plain logistic_gd, not only against the baselines.
5. **ensemble_equal** — the equal-weight mean of the three base
   models' (calibrated, where applicable) probabilities at the
   origin. No fitting.
6. **ensemble_weighted** — inverse-trailing-Brier-weighted mean of
   the three base models' probabilities. Weights at an origin come
   from each model's Brier score over its most recent 50
   out-of-sample folds strictly before that origin
   (`w = 1 / (brier + 1e-9)`, normalised to sum to 1). Before 50
   past folds exist, weights use all past folds; before any past
   fold exists, weights are equal. No look-ahead: a fold's outcome
   enters a weight only after that fold is scored.

## Baselines (fixed here)

1. **coin_flip** — probability 0.5 at every fold. Its Brier score
   is exactly 0.25 on any outcome set.
2. **base_rate** — probability = the fraction of up-days among the
   training rows at that origin (recomputed per origin, expanding).
3. **direction_rule** — the pre-registered rule for the symbol from
   `toolkit/direction_rules.py` (`sma50_trend` for SPY, AAPL, VT),
   evaluated on closes visible at the origin only, mapped to a
   probability: UP → 0.75, DOWN → 0.25, no call → the base-rate
   probability at that origin. The 0.75 / 0.25 mapping is fixed
   here: a hard rule carries no calibrated probability of its own,
   so it is scored as a moderately confident call, and the mapping
   is stated rather than tuned.

## Regime detection (fixed here)

Regime detection builds on the existing
`toolkit/predictive.py` `volatility_regime(window=20)` — it is
extended, not duplicated — plus two new detectors in
`toolkit/direction_models.py`:

- **volatility_regime (existing):** rolling 20-day return std vs
  its own median over the visible series; "high" when the latest
  std exceeds the median. The regime label at an origin uses
  closes visible at that origin only.
- **CUSUM change-point detection:** on simple returns, with
  reference mean 0 and `k = 0.5 · σ_train`, alarm threshold
  `h = 5 · σ_train`, where `σ_train` is the population std of the
  training returns at that origin. Cumulative sums `S+`/`S-` reset
  to 0 on an alarm. Only the count of alarms up to the origin is
  reported — CUSUM is a diagnostic here, not a model input.
- **HMM-lite (two-state):** each bar is labelled calm/volatile by
  its trailing 20-day return std vs the expanding median std up to
  that bar (the same comparison `volatility_regime` makes, applied
  per bar). Transition counts (calm→calm, calm→volatile,
  volatile→calm, volatile→volatile) are estimated from the visible
  bars only, walk-forward. This is NOT a fitted HMM: there is no
  EM, no likelihood maximisation, no library — just counted
  transitions from the per-bar labels.

The regime-conditioning question is answered by the head-to-head
comparison of `logistic_regime` vs `logistic_gd` under the same
decision rule (Section "Decision rule"). If the conditioned model
does not beat the plain model, the card says regime conditioning
shows "no edge" on this window.

## Walk-forward split

- Expanding window. The first origin sits after **252 closes**
  (~1 trading year of training, including the 50-bar feature
  warm-up). At origin `t` (close index) the model sees feature
  rows with index `< t` only and forecasts the probability that
  close `t+1` is above close `t`. Origins run to the second-last
  close.
- Out-of-sample folds: all closes after the first 252, per symbol.
  With ~2 years of daily data that is roughly 248 folds per symbol.

## Scoring

- **Primary: Brier score** — `mean((p − y)²)` over out-of-sample
  folds, per model and per baseline. Lower is better. 0 is
  perfect; 0.25 is the score of always forecasting 0.5.
- **Secondary: log score** — `mean(−(y·ln p + (1−y)·ln(1−p)))`
  with probabilities clipped to [1e-6, 1-1e-6]. Lower is better.
  Reported for completeness; it does **not** decide the verdict,
  because log score is dominated by a few very confident wrong
  calls.
- **Accuracy at 0.5** (call up when `p > 0.5`, down otherwise,
  `p = 0.5` counts as down) is reported per model for readability;
  it does **not** decide the verdict — a probability model is
  judged on its probabilities, not on a thresholded call.
- Scores are reported per symbol and pooled across symbols.

## Calibration

- **Expanding Platt calibration:** at each origin, once at least
  30 past out-of-sample folds exist for a model, its raw
  probabilities on those past folds are Platt-calibrated with the
  existing `toolkit/predictive.py` `platt_calibrate()` (fitted on
  past folds only), and the fitted `(a, b)` is applied to the
  current fold's raw probability via `calibrated_probability()`.
  Before 30 past folds exist, the raw probability is used
  uncalibrated and the fold is flagged as such. The Platt fit uses
  only outcomes already observed — no look-ahead.
- The Brier / log-score verdicts use the calibrated probabilities
  (the probabilities the model actually issues at each origin).
  Raw (uncalibrated) Brier is also reported per model so the
  calibration gain — or loss — is visible.
- **Reliability bins:** the issued probabilities are grouped into
  10 equal-width bins [0, 0.1), …, [0.9, 1.0]; per bin the card
  reports the count, the mean issued probability, and the observed
  up-rate. A bin with zero folds is reported as empty, not hidden.
  Reliability is a diagnostic, not a verdict rule.

## Decision rule (the verdict, fixed before running)

Pooled across symbols (SPY + AAPL + VT). A model **beats a
baseline** only if BOTH hold:

1. its pooled mean Brier score is lower than the baseline's pooled
   mean Brier score, and
2. the 95% paired-bootstrap confidence interval on the mean Brier
   difference (model − baseline), from **2000 resamples, seed
   20261115**, resampling per-fold Brier differences pooled across
   symbols, lies **entirely below zero**.

A model is labelled **"edge"** only if it beats ALL THREE baselines
under that rule. Otherwise it is labelled **"no edge"** — in the
model card and in the code's verdict strings. A win on log score
alone never counts. A win on accuracy alone never counts. A win on
one symbol alone never counts.

Regime conditioning is judged separately: `logistic_regime` is said
to improve on `logistic_gd` only if it beats it under the same
two-part rule (lower pooled mean Brier AND the 95% CI on the mean
Brier difference entirely below zero, same resamples and seed);
otherwise regime conditioning is labelled **"no edge"**.

## What counts as "models tried"

Three base models tried (logistic_gd, gaussian_nb, score_platt),
one regime-conditioned variant (logistic_regime) and two ensembles
(ensemble_equal, ensemble_weighted). No parameter search was run
and none will be run for this PR: the learning rate, epochs, λ,
fixed score weights, CUSUM `k`/`h`, regime window (20) and the
0.75 / 0.25 rule mapping are fixed above. If no model beats the
baselines, the PR still ships with the "no edge" labels — the
evaluation is the deliverable either way.

## Reproducibility

The evaluation is run by `scripts/evaluate_direction_models.py`
(stdlib only; network only for the Yahoo fetch). Its output is the
input to `docs/direction-models-model-card.md`. The offline test
suite (`tests/test_direction_models.py`) never touches the network:
it runs the same scoring, calibration and decision-rule code on
synthetic fixture series.
