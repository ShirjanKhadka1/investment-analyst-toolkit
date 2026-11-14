# Forecast scoring protocol for reports/daily (H2) — PRE-REGISTERED

Written 2026-11-14, **before the first scored run under this
protocol**. This protocol fixes the metric set, baseline, bootstrap
seed and replicate count, and the claim threshold in advance, for
the `reports/daily` next-step price forecasts. Changing any of
them afterwards requires a version note, not a silent edit. It
implements the design drafted in
`~/workspace/github-repos/ops/proposed-forecast-scoring.md`
(2026-11-14, proposal only) as part of PR 8.1, and the scoring is
done by the forecast evaluation harness
(`toolkit/forecast_harness.py`, `score_daily_predictions`).

Research only. Never investment advice. Nothing here is a
recommendation to buy, sell, or invest in any security.

## The ledger being scored

`reports/daily/predictions.jsonl` on main. Each row carries
`predicted_at`, `symbol`, `forecast`, `last_close`, `model`,
`predicted_for`, plus once scored: `status`, `actual`,
`actual_date`, `error`, `direction_correct`. Only rows with an
`actual` (status `scored` / `failed`) count as scored; `pending`
rows count as made-but-unscored. The denominator is always shown.

This ledger is scored **as it stands**. The strict append-only
check fails on it by design today: scoring updates rows in place.
The append-only forecast ledger (`toolkit/forecast_ledger.py`,
PR 8.1) is a separate, new ledger — the old file is not rewritten
and the autonomous workflow that writes it is not changed by this
protocol. See `docs/forecast-ledger-migration.md` for how the
existing scored forecasts carry over into the new ledger as a
labelled development period.

## The development period — what the existing 34 scored forecasts are

At the time this protocol is written, `predictions.jsonl` holds
**36 rows: 34 scored, 2 pending**. Those 34 scored forecasts are
a **separate development period**, labelled as such. They are
**not** the harness's validation set: they were produced by the
autonomous pipeline before this protocol existed, under no
pre-registered rule, and they must not be presented as validation
evidence for any model. They are scored here so the record is
complete and honest, and they count toward the 100-forecast
threshold below only as scored forecasts in the ledger — the
threshold is about the size of the scored record, not a claim
that the development period validates anything.

## Metrics (per symbol and pooled)

- **Forecast error:** MAE and RMSE of (actual − forecast).
- **Baseline:** the no-change forecast, i.e. `last_close`; MAE /
  RMSE of (actual − last_close). The difference (forecast minus
  baseline) is reported — the forecast must beat the baseline to
  claim anything.
- **Direction:** fraction `direction_correct` vs **50%** (coin
  flip), and vs the **base rate of up-days** in the scored window.
  A price forecast implies a direction (above or below the last
  close); the direction score is part of the result, not a
  separate claim.
- **Bootstrap CIs:** seeded resampling of the scored forecasts
  (**10,000 replicates, seed 20261114**, fixed and printed in the
  output) for the MAE difference and the direction-accuracy
  difference; 95% percentile CIs are reported.
- **Counts, always together:** forecasts made, forecasts scored,
  forecasts pending.

## Rules

- **No claim — no "beats baseline", no accuracy headline — until
  at least 100 scored forecasts.** Until then the output reports
  the numbers with the label **"insufficient n — no claim"**.
- **Failures stay in:** every scored row counts, including rows
  labelled `failed`; no row is dropped or re-scored.
- **Derived metrics only.** The scoring reads the ledger; it does
  not republish the raw price series.
- **Per-symbol and pooled** are both reported; a pooled win that
  comes from one symbol alone is stated as such.
- **The verdict rule, fixed here:** once 100 forecasts are
  scored, the daily stream "beats baseline" only if its pooled
  mean MAE is lower than the no-change baseline's AND the 95%
  bootstrap CI on the mean MAE difference lies entirely below
  zero. Otherwise "no edge".

## Reproducibility

Run from the repo root:

```text
python3 scripts/evaluate_forecast_harness.py
```

The script scores `reports/daily/predictions.jsonl` through the
harness and prints the counts, metrics and CIs. The offline test
suite (`tests/test_forecast_harness.py`) runs the same scoring
code on synthetic ledger rows.
