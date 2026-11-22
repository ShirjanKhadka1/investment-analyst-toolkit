# Track-record incidents — 13–22 November 2026

Dated 2026-11-14, extended 2026-11-22. Factual record of what happened to the public
track-record log (`data/track-record/calls.jsonl` on the
`data/track-record` branch; the clean-restart series is
`calls-v2.jsonl` on the same branch). No blame language, except
where this file records the assistant's own wording failure
plainly (Incident 3): this file records what the code did, with
commit SHAs, so the fix can be judged against it. The Daily track record workflow was disabled by the
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

## Incident 3 — runs #9–#13: horizon-end failures, stale fetch, overlapping calls (2026-11-18 – 2026-11-22)

This is the incident the programme tracks as TRACK-RECORD
INCIDENT 2. The Daily track record workflow runs on a
`0 22 * * *` cron (22:00 UTC), but GitHub started these runs
hours late — created 01:55–03:21 UTC the following day
(#9 5h21m late, #10 4h57m, #11 4h52m, #12 4h59m, #13 3h55m).
The runs below are the scheduled runs of the clean-restart
series (`calls-v2.jsonl` on the `data/track-record` branch).
Data-branch commits, in order:

- `25b985d9952f8f8dee34c29396936ff116805b43` ("Daily track
  record: 2026-11-14", 2026-11-14T08:18:50Z) — the 7 first-series
  calls, dated 2026-11-14.
- `0c6195934e18b51b5ba85aaf80b8735bf13fe841` ("Daily track
  record: 2026-11-19", 2026-11-19T02:57:01Z) — logged 3 further
  calls, dated 2026-11-19 (see below).
- `940e7581702a0c81b51b1ee4ad417c058345ddcb` ("Daily track
  record: 2026-11-20", 2026-11-20T02:51:58Z).
- `15613e089467b00e8ae8c959ef56a9b18ff1ac86` ("Daily track
  record: 2026-11-21", 2026-11-21T02:59:29Z).

### Run #9 — failed, horizon-end ValueError on BTC (2026-11-18)

- Run: schedule 44229811256, created 2026-11-18T03:21:11Z,
  conclusion failure, head commit
  447cb3be35e68acef9252e7c156d00f22f44b6fb.
- The run tried to settle the 14 Nov BTC-USD call. Two gates
  govern settlement: the settle bar must be 5 trading days after
  the entry bar (settle bar 18 Nov), and the horizon must have
  ended (call date 2026-11-14 + 5d = 2026-11-19). The settle bar
  (18 Nov) fell before the horizon end (19 Nov), so
  `settle_call` raised `ValueError` ("cannot settle before the
  horizon ends"), uncaught by `main()`, and the run failed.
  Nothing was published; the data branch was untouched.

### Run #10 — success, settled nothing; stale fetch, missed bars; logged 3 overlapping calls (2026-11-19)

- Run: schedule 44447938985, created 2026-11-19T02:56:53Z,
  conclusion success, head commit
  0972636a9a63f4aafd43b889d09cd9f8a4703fc4.
- Settled nothing. Its fetch ended 17 Nov, so it recorded missed
  days instead of settling: NIFTYBEES.NS and ISF.L "no bar on
  settlement date 2026-11-19", BTC-USD "no bar on settlement
  date 2026-11-18".
- It logged 3 new calls dated 2026-11-19, one per ticker with no
  open call the script could see — NIFTYBEES.NS
  (`3f257a877c7df6d0`, up, 289.60), ISF.L (`5332effdbd924ff7`,
  up, 1043.40), BTC-USD (`429bd0aa0af7e9e2`, down,
  64893.9453125) — all from the 2026-11-17 entry bar. Those three
  overlap the still-unsettled 14 Nov first-series calls for the
  same tickers (same entry bar), and part of their horizon was
  already observable when logged. The owner excluded all three
  from scoring on 2026-11-22, before any outcome was known; see
  `docs/track-record-annotations.md` and
  `docs/track-record-exclusions.json`. They stay in the log,
  labelled, never deleted.

### Run #11 — success, settled BTC-USD and EURUSD=X (2026-11-20)

- Run: schedule 44663765938, created 2026-11-20T02:51:51Z,
  conclusion success, head commit
  d3cb24a8d03f3b1a2d4d0ff05ba2af4fc3dd3c65.
- Settled the 14 Nov BTC-USD and EURUSD=X calls; both hit.

### Run #12 — success, settled the remaining five first-series calls (2026-11-21)

- Run: schedule 44858766086, created 2026-11-21T02:59:20Z,
  conclusion success, head commit
  fde95f622fd470d94305c7a7c365ee1545f04104.
- Settled SPY, VT and NIFTYBEES.NS (hit) and AAPL and ISF.L
  (miss). All 7 first-series 14 Nov calls are now settled:
  5 hits (SPY, VT, NIFTYBEES.NS, BTC-USD, EURUSD=X), 2 misses
  (AAPL, ISF.L). The headline stays withheld: 7 settled calls
  is below the 100-call minimum.

### Run #13 — failed, same horizon-end ValueError on the 19 Nov calls (2026-11-22)

- Run: schedule 44973686669, created 2026-11-22T01:55:27Z,
  conclusion failure, head commit
  793e4d3637ec87751d4befab1e787a9cd0f74b22.
- The run tried to settle the 19 Nov calls: "cannot settle
  before the horizon ends: call date 2026-11-19 + 5d =
  2026-11-24, as_of is 2026-11-22". Same defect as run #9 —
  the settle-bar gate and the horizon-end gate disagree by a
  day, and the resulting `ValueError` is uncaught by `main()`.
  Nothing was published; the data branch was untouched.

## Root causes (Incident 3)

- **Horizon-end vs settle-bar mismatch (#9, #13):** settlement
  is governed by two gates — the settle bar is the 5th trading
  day after the entry bar, and settlement is refused before
  call date + 5 calendar days. For the 14 Nov series the settle
  bar (18 Nov) precedes the horizon end (19 Nov); for the
  19 Nov calls the settle bar precedes 24 Nov the same way.
  `settle_call` raises, `main()` does not catch it, the run
  fails. The fix belongs in the runner (a separate PR): do not
  attempt a settlement whose horizon has not ended, and treat
  the refusal as a skip, not a crash.
- **Stale fetch (#10):** the run's price fetch ended 17 Nov, so
  no settlement bar existed for 18/19 Nov; the run recorded
  missed days instead of settling. The missed-day path worked
  as designed — the failure was the fetch's freshness, not the
  log.
- **Overlapping calls (#10):** the script logs a fresh call per
  ticker when it sees no open call, but it could not see the
  14 Nov calls' overlap with the 19 Nov entry bar, so it logged
  three calls whose horizon partly overlapped the earlier
  series. Those three are now excluded by the owner's decision
  (see above), not deleted.

## "Failed exactly as predicted" — what the assistant got wrong about run #9

On 17 Nov the assistant predicted the 18 Nov run would fail,
from the two-gate settlement model: settle bar 18 Nov <
horizon end 19 Nov, `settle_call` ValueError uncaught by
`main()`. When run #9 failed, the assistant wrote that it had
"failed exactly as predicted" — and said nothing more. That
wording was wrong, and this file owns it plainly.

The prediction itself was correct, and the assistant had
flagged the failure to the user that morning as a known
expected failure needing his call, because the fix is a
track-record file change and track-record files are untouchable
without his explicit authorisation — so the assistant did not
fix it beforehand. But presenting a predicted failure as
routine understated it: a failure the team can predict to the
day is a known defect, not a weather event. It should have
been flagged as a defect to fix — with the fix (runner must
not attempt pre-horizon settlement) named, and the
authorisation question (may the runner be changed?) asked
directly — instead of being reported as if the prediction made
the failure acceptable. The same defect then failed run #13
on 22 Nov, four days later, still unfixed. The lesson is
recorded here so it is not repeated: a predicted failure is
still a failure, and "we knew it would fail" is a reason to
fix it, not a reason to shrug.

## Resolution (Incident 3)

- The three overlapping 19 Nov calls are excluded from scoring
  by the owner's decision of 2026-11-22
  (`docs/track-record-exclusions.json`); the scorer
  (`toolkit/track_record.py`) skips them and the settler
  (`toolkit/decision_log.py`) refuses them. The runner fix
  (separate PR) must skip excluded calls before attempting
  settlement, and must not attempt a settlement before its
  horizon ends — so that the next run (which would otherwise
  hit the same ValueError on the 19 Nov calls) completes.
- No ledger row was edited or deleted in this incident or in
  its fix: `calls-v2.jsonl`, `missed_days.jsonl` and
  `calls.jsonl` are untouched; the exclusion is a separate file
  the scorer and settler read, not a change to the log.
