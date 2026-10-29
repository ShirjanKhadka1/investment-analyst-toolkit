# Real-data walk-forward record — 2026-10-29

Research only. Never a buy/sell/hold recommendation. A lower past
out-of-sample MAE is not a guarantee of future accuracy.

## What was fetched — one polite fetch, one symbol, one period

- **Fetch date:** 2026-10-29 (Nepal Time, morning; the run was made
  before the US 2026-10-29 close, so the latest available close is
  2026-10-28).
- **Source:** Yahoo Finance chart API, via the toolkit's own
  `toolkit.data.fetch_yahoo_prices("AAPL")` (default 1-year daily range).
  A single request; no retries, no parallel hammering.
- **Symbol:** AAPL (Apple Inc., NASDAQ). This is a US equity, not a
  NEPSE security. No NEPSE data is used or invented here, and there is
  no live NEPSE feed in this toolkit.
- **Series:** daily closes, **n = 251**, from **2025-10-29** (close
  269.70) to **2026-10-28** (close 343.83). Closes are as returned by
  Yahoo; no adjustment, cleaning, or filling was applied.
- **Method:** the existing `toolkit.predictive.compare_models_walk_forward`
  with `train_size=125` and `horizons=(1, 3, 5)`, default model set only
  (naive, moving_average_5, linear_trend, exp_smoothing_0.3 — the Phase 10
  models were NOT included, so this says nothing about them). At each
  origin the model sees only data up to that origin; it is judged on the
  close `horizon` steps ahead. MAE/RMSE are in US dollars per share.

## Results — naive wins at every horizon; every other model loses

`beats_naive` is False for every non-naive model at every horizon.
The losses are part of the record, not edited out.

### Horizon h = 1 (n_test = 126)

| Model | MAE | RMSE | Beats naive |
| --- | --- | --- | --- |
| naive | 3.7660 | 5.1844 | — (baseline) |
| exp_smoothing_0.3 | 5.5880 | 7.3246 | No |
| moving_average_5 | 5.9042 | 7.9255 | No |
| linear_trend | 17.6933 | 21.1098 | No |

Best model: naive. `beats_naive: False`, `naive_wins: True`.

### Horizon h = 3 (n_test = 124)

| Model | MAE | RMSE | Beats naive |
| --- | --- | --- | --- |
| naive | 6.9771 | 9.4570 | — (baseline) |
| exp_smoothing_0.3 | 8.2806 | 10.4331 | No |
| moving_average_5 | 8.3444 | 10.6089 | No |
| linear_trend | 18.4991 | 22.1609 | No |

Best model: naive. `beats_naive: False`, `naive_wins: True`.

### Horizon h = 5 (n_test = 122)

| Model | MAE | RMSE | Beats naive |
| --- | --- | --- | --- |
| naive | 9.2287 | 11.8878 | — (baseline) |
| moving_average_5 | 10.3993 | 12.5576 | No |
| exp_smoothing_0.3 | 10.4690 | 12.5416 | No |
| linear_trend | 19.2241 | 23.1244 | No |

Best model: naive. `beats_naive: False`, `naive_wins: True`.

## Reading (and not over-reading) this

- Naive — "tomorrow's close equals today's close" — beat all three
  fitted baselines at h = 1, 3, and 5 on this split. On a rising but
  noisy series, the fitted models' extrapolation hurt more than it
  helped; linear_trend was by far the worst at every horizon
  (MAE 17.69–19.22 vs naive 3.77–9.23), consistent with a trend fitted
  on the training window overshooting when the slope does not persist.
- MAE grows with horizon for every model, including naive
  (3.77 → 6.98 → 9.23): longer horizons are harder, as expected.
  Nothing here shows a model "working" — it shows the baseline winning.
- **Explicit limits:** one symbol (AAPL), one 12-month period
  (2025-10-29 to 2026-10-28), one train/test split (125/126 at h = 1),
  closes only, no transaction costs, no dividends/splits adjustment
  beyond what Yahoo returns, default four-model set only. This is NOT
  a statement about general model accuracy, about AAPL, or about any
  other symbol, period, or market — and it is not investment advice.
