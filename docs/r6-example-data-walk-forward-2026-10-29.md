# R6 walk-forward on existing labelled example data — 2026-10-29

Research only. Never a buy/sell/hold recommendation. A lower past
out-of-sample MAE is not a guarantee of future accuracy.

## Why example data, not NEPSE

**No real-NEPSE walk-forward re-run was possible.** No permitted
real NEPSE price dataset exists: shirjankhadka.com.np's Data Use &
Terms (effective 1 Oct 2026) forbid bulk re-scraping of its data
files to republish a competing feed, no bulk-access permission is
on file, and no documented free public NEPSE OHLCV API exists (see
the scanner repo's `scanner/NEPSE_USER_CSV_TERMS.md` for the full
audit). This run therefore uses the EXISTING labelled example file
shipped with the scanner — AAPL daily closes, a US equity, NOT
NEPSE. It says nothing about any NEPSE security.

## What was run

- File: `../nepali-capital-market-research/scanner/examples/aapl-daily-closes-2024-2025.csv`
  (existing file, untouched by this round).
- Series: AAPL (NASDAQ) daily closes, **2024-01-02 → 2025-12-31,
  n = 502**, first close 185.64, last close 271.86.
- Method: `toolkit.predictive.compare_models_walk_forward` with
  `train_size = 251` and `horizons = (1, 3, 5)`, default four-model
  set only (naive, moving_average_5, linear_trend,
  exp_smoothing_0.3). At each origin the model sees only data up
  to that origin. MAE/RMSE in US dollars per share.

## Results — naive wins at every horizon

| Horizon | n_test | Naive MAE / RMSE | exp_smoothing_0.3 | moving_average_5 | linear_trend | Beats naive? |
| --- | --- | --- | --- | --- | --- | --- |
| h = 1 | 251 | 2.8474 / 4.3117 | 4.5180 / 6.2247 | 4.9345 / 6.9123 | 22.7787 / 26.2165 | No |
| h = 3 | 249 | 5.6394 / 8.1204 | 6.5891 / 8.8699 | 7.0780 / 9.4096 | 23.4342 / 26.8982 | No |
| h = 5 | 247 | 7.7088 / 10.3697 | 7.9284 / 10.5098 | 8.4038 / 10.9391 | 24.1026 / 27.5718 | No |

`beats_naive` is False for every non-naive model at every horizon;
`naive_wins` is True throughout. The losses are part of the record,
not edited out.

## Reading (and not over-reading)

- Same conclusion as the previously recorded real-data walk-forward
  (AAPL 251 closes, 2025-10-29 → 2026-10-28, naive MAE 3.766 /
  6.977 / 9.229 at h = 1 / 3 / 5) — but a different series and
  period, so the numbers must not be mixed with that run's.
- MAE grows with horizon for every model, including naive
  (2.85 → 5.64 → 7.71): longer horizons are harder, as expected.
- Limits: one symbol (AAPL), one 24-month period, one train/test
  split (251/251 at h = 1), closes only, no transaction costs, no
  dividends/splits adjustment beyond what the file contains,
  default four-model set only. This is NOT a statement about
  general model accuracy, about AAPL, or about any NEPSE security.
- The companion scanner crossover backtest on the same file is in
  the scanner repo's `scanner/R6-EXAMPLE-DATA-RESULTS.md`.
