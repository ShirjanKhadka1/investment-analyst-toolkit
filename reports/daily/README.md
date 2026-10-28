# Daily autonomous research reports — generated, not hand-written

> Research only. Not investment advice. Nothing here is a recommendation
> to buy, sell, or invest in any security. No trade is made by this system.

This directory holds the output of the scheduled workflow
`.github/workflows/scheduled-update.yml`, which runs daily at
**01:00 UTC (06:45 NPT)** and can also be run on demand (manual dispatch,
with a dry-run mode that commits nothing).

- Each symbol has a folder with timestamped reports (`YYYYMMDDTHHMMSSZ.md`)
  and a `latest.md` — the last report from a **fresh** run.
- A run that fails for a symbol writes a timestamped report labelled
  `STALE_OR_FAILED` and **keeps the previous `latest.md` unchanged**
  (last-known-good). A stale report is never presented as fresh.
- `predictions.jsonl` is the prediction ledger: every prediction is
  written **before** its outcome is known and scored on the next run.
  Pending predictions never count as wins; failures stay in the record
  with a reason and an action.
- `audit.jsonl` is the hash-chained audit log for the runs.
- `summary-*.json` summarises each run (fresh vs stale per symbol).

Until the first scheduled run is observed on GitHub, the schedule above
is a plan, not a proven fact — this directory will be empty or will
contain only this note. No autonomy is claimed until a scheduled run
with public output actually exists and is seen.

Failure visibility: there are no email alerts (email access is a
recorded blocker). Failures are visible in the public workflow run log
and in the `STALE_OR_FAILED` labels in this directory.

Data sources for these reports: the free, public sources recorded in
`../DATA_SOURCES.md` (Yahoo → Stooq fallback for stocks). No live NEPSE
feed exists in this layer — see `DATA_SOURCES.md` for why.
