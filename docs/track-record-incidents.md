# Track-record incidents — 13–14 November 2026

Dated 2026-11-14. Factual record of what happened to the public
track-record log (`data/track-record/calls.jsonl` on the
`data/track-record` branch). No blame language: this file records
what the code did, with commit SHAs, so the fix can be judged
against it. The Daily track record workflow was disabled by the
owner on 2026-11-14 and stays disabled until the fix in PR 7.0 is
deployed and verified by dry run.

## Incident 1 — manual dispatch logged in-progress bars (2026-11-13)

- Run: workflow_dispatch 43425018720, created 2026-11-13T12:25:26Z,
  head commit 78067b0.
- Commit on `data/track-record`:
  `b4ab061b69d27bd408c8b7b508e99c6bd2051ecf` ("Daily track record:
  2026-11-13", 2026-11-13T12:25:36Z).
- Logged 7 calls dated 2026-11-13, one per approved watchlist
  ticker (SPY, AAPL, VT, NIFTYBEES.NS, ISF.L, EURUSD=X, BTC-USD).
- 4 calls used completed bars: SPY, AAPL, VT on the 2026-11-12
  close; NIFTYBEES.NS on the 2026-11-13 close (NSE had closed
  before the fetch).
- 3 calls used in-progress bars at the 12:25 UTC fetch: ISF.L
  (London session open), EURUSD=X and BTC-USD (entries match no
  daily close). The script took the last bar Yahoo returned and
  assumed a post-close run; that assumption holds for the scheduled
  22:00 UTC run but not for a 12:25 UTC manual dispatch. The log
  records no bar date, so the bar behind each entry had to be
  reconstructed by comparing entry closes to Yahoo daily closes.

## Incident 2 — scheduled run overwrote the log (2026-11-14)

- Run: schedule 43543725407, created 2026-11-14T01:59:09Z (the
  22:00 UTC cron fired late), head commit addad95 ("Daily derived
  refresh + data health", 2026-11-13T17:15:03Z — only
  `data/derived/demo.json` and `data_health.json`).
- Commit on `data/track-record`:
  `c35ce63f6dd013f9e89ebec3cf24d54b20f0b242` ("Daily track record:
  2026-11-14", 2026-11-14T01:59:17Z).
- Logged 7 fresh calls dated 2026-11-14 and OVERWROTE
  `calls.jsonl`: the 13 Nov rows are gone from the live file.
  The 14 Nov rows:

  | Date | Symbol | Direction | Entry close | call_id |
  |---|---|---|---|---|
  | 2026-11-14 | SPY | down | 754.37 | 68b15f2b2681acd7 |
  | 2026-11-14 | AAPL | up | 356.27 | 89f5e2f478fec8e4 |
  | 2026-11-14 | VT | down | 155.30 | b235dd9c5281de9d |
  | 2026-11-14 | NIFTYBEES.NS | up | 288.22 | 5c3bbc9db1092f86 |
  | 2026-11-14 | ISF.L | up | 1059.60 | 962f687a72013b27 |
  | 2026-11-14 | EURUSD=X | down | 1.10502 | 27792fcf979fb585 |
  | 2026-11-14 | BTC-USD | down | 68211.43 | 5da8690e9193959b |

- The 14 Nov entries do not all match a daily close recorded in
  the Incident 1 audit (12 Nov: BTC-USD 71342.97, EURUSD=X
  1.10952; 13 Nov: BTC-USD 68792.32, EURUSD=X 1.10314):
  BTC-USD 68211.43 and EURUSD=X 1.10502 match neither; ISF.L
  1059.60 equals the 12 Nov close, not the 13 Nov close (1063.60);
  SPY 754.37 matches neither the 12 Nov close (757.12) nor the
  13 Nov close (751.19) as recorded in that audit. This file states
  those comparisons only; it does not speculate about why Yahoo
  served those values.
- `missed_days.jsonl` was empty in both runs. `track-record.md`
  says 0 settled calls in both runs.

## Root cause

The workflow checks out `main`. `main` has no
`data/track-record/calls.jsonl` (verified 404), so the script
loads zero prior calls, logs a fresh call per ticker on every
run, and `publish()` copies the run's outputs over the
data-branch file. The design said one call per ticker per 5
trading days, non-overlapping, with settlement after the
horizon — but that design only works when the script can see
the prior calls, and it never could. Settlement can never
happen while this stands: every run wipes the open calls
before their 5-day horizon passes.

## What is recoverable

Nothing was deleted from git history. The file was overwritten
in a new commit, so both versions are recoverable from the
branch history:

- 13 Nov rows: `git show b4ab061:calls.jsonl`
- 14 Nov rows: `git show c35ce63:calls.jsonl` (also the live file
  until the clean restart replaces it)

No call was ever settled, so no scored outcome was lost: the
record stands at 0 settled calls.

## Resolution

- The Daily track record workflow was disabled by the owner on
  2026-11-14 (Step 0 of Round 7). It stays disabled until the
  fix is deployed and verified by dry run.
- PR 7.0 fixes the script (append-only log with a hash chain, a
  publish invariant that refuses to push unless the remote rows
  are untouched, bar-completeness checks, and a `--provisional`
  flag) and adds `scripts/verify_track_record.py`, which walks
  the branch history and fails if any commit removed or changed
  an earlier row.
- The owner's decision on what counts is in
  `docs/track-record-annotations.md`: clean restart. Both runs
  above are commissioning and are not scored; the official
  series starts at the first clean run after the fix, in a new
  file (`data/track-record/calls-v2.jsonl`).
