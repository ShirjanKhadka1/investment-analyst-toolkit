# Forecast ledger migration note (PR 8.1)

## Why a new ledger

`reports/daily/predictions.jsonl` scores forecasts by updating
rows in place: a row gains `status`, `actual`, `actual_date`,
`error` and `direction_correct` after its outcome is known. The
strict append-only check therefore fails on it **by design** —
the file is a ledger that rewrites its own history, so no hash
chain over it can hold. The append-only forecast ledger
(`toolkit/forecast_ledger.py`) fixes the shape, not the data:
forecast rows are written once and never change; scores are
separate appended rows that reference the forecast id.

## What migrates

The 36 rows in `reports/daily/predictions.jsonl` (34 scored, 2
pending, at the time this PR is written) migrate as a **labelled
development period**:

- One forecast row per old row, with `development_period: true`
  and `source: "reports/daily/predictions.jsonl"`. Forecast fields
  carry over unchanged: `symbol`, `predicted_at`,
  `predicted_for`, `forecast`, `last_close`, `model`.
- One score row per old row that already has an `actual` (status
  `scored` / `failed` — failures stay in), with `actual`,
  `actual_date`, `error`, `direction_correct`, `status`.
- Pending rows migrate as unscored forecasts (no score row).

The 34 scored forecasts are the development period defined in
`docs/forecast-scoring-protocol.md`: produced by the autonomous
pipeline before any pre-registered protocol existed, they are
scored so the record is complete, and they are **not** a
validation set for any model.

## How to run it (read-only on the source)

```text
python3 -c "
from toolkit.forecast_ledger import (
    migrate_development_period, verify_ledger)
out = migrate_development_period(
    'reports/daily/predictions.jsonl',
    'reports/daily/predictions-append-only.jsonl')
print(out)
print('problems:', verify_ledger(
    'reports/daily/predictions-append-only.jsonl'))
"
```

Expected output for the file as it stands when this PR is
written: 36 forecasts written, 34 scores written, 2 pending,
zero verification problems. The counts are checked against the
file at migration time, not hardcoded — if the daily workflow
has appended rows since, the numbers will be larger.

Verify after migrating: `verify_ledger` returns an empty list
(chain links hold, every forecast id matches its content, every
score references a forecast that exists, no duplicate ids, no
forecast scored twice). Count check: forecasts written equals
the old file's row count, scores written equals its scored-row
count, pending equals the rest.

## What does NOT change in this PR

- `reports/daily/predictions.jsonl` is not modified, and no row
  in it is rewritten.
- The autonomous workflow (`.github/workflows/scheduled-update.yml`
  / `toolkit.cli autonomous-once`) keeps writing the old format.
- No track-record file is touched (daily-track-record workflow,
  `track_record_daily.py`, `direction_rules.py`,
  `docs/direction-rules.md`, calls files, the
  `data/track-record` branch).

## Proposal: switching the workflow to the new ledger (NOT applied)

A future PR — not this one — could switch the daily writer to
the ledger: `autonomous-once` appends a forecast row per
prediction via `append_forecast`, and the next run appends a
score row via `append_score` once the outcome is known, instead
of updating the old row. The old `predictions.jsonl` would then
be frozen as the development period (migrated once, as above)
and the new ledger would become the live one. That switch needs
its own review because it changes a scheduled workflow's
behaviour; this PR deliberately does not make it.
