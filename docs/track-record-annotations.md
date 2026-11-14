# Track-record annotations — what counts

## Owner's decision, 2026-11-14: clean restart

Both runs so far are **commissioning** and are **not scored**.
The official series starts at the first clean run after the
PR 7.0 fix is deployed and verified. This decision is recorded
before any outcome exists: the record stands at **0 settled
calls**, so no scored outcome is discarded by it.

Why a restart, not a repair: the 13 Nov run logged 3 of 7 calls
from in-progress bars, and the 14 Nov run overwrote the log, so
the live file no longer holds the 13 Nov rows at all. Repairing
either run into a scored series would mean scoring calls whose
entries are not all completed bars, on a log that no longer
contains one of the runs. A restart keeps the evidence intact
and the scored series honest.

Nothing is deleted or moved on the `data/track-record` branch.
Both commissioning versions stay in git history (see
`docs/track-record-incidents.md`). The new series uses a new
file path: `data/track-record/calls-v2.jsonl`. The old
`calls.jsonl` is left exactly as the 14 Nov run left it — a
frozen commissioning record, never scored, never appended to.

## The 13 Nov commissioning calls — counted / excluded

The facts below stand even though these calls are not scored.
They are the Round 6 audit's findings, kept so the commissioning
record can be read without re-deriving it. Entry bars were
reconstructed by comparing each entry close to Yahoo daily
closes. Settle dates are the 5th trading day after the entry
bar per the calendar spec, computed with
`toolkit.trading_calendar.settlement_date` on the entry-bar date
— not by calendar arithmetic.

### Counted (completed bars at fetch)

| Symbol | Direction | Entry close | Entry bar | Settle date |
|---|---|---|---|---|
| SPY | down | 757.12 | 2026-11-12 close | 2026-11-19 |
| AAPL | up | 354.88 | 2026-11-12 close | 2026-11-19 |
| VT | down | 155.75 | 2026-11-12 close | 2026-11-19 |
| NIFTYBEES.NS | up | 286.64 | 2026-11-13 close | 2026-11-20 |

Call IDs (commit `b4ab061`): SPY `bfdd237aaf0f932d`, AAPL
`a9a5ada19f92c5f2`, VT `addcae16a4923f2d`, NIFTYBEES.NS
`230bcbcec6652077`. All four are `sma50_trend` v1.0.0.

### Excluded and labelled (in-progress bars at fetch)

| Symbol | Direction | Entry close | Reason |
|---|---|---|---|
| ISF.L | up | 1061.00 | London session still open at the 12:25 UTC fetch; entry matches no daily close |
| EURUSD=X | down | 1.10522 | FX bar dated the current UTC day; matches no daily close |
| BTC-USD | down | 69169.08 | Crypto bar dated the current UTC day; matches no daily close |

Call IDs (commit `b4ab061`): ISF.L `bd7406d29fcf9627`,
EURUSD=X `6cee5e144b08aa6c`, BTC-USD `34ab734c376f2776`.
ISF.L is `sma50_trend` v1.0.0; EURUSD=X and BTC-USD are
`mom20_sign` v1.0.0.

## The 14 Nov commissioning run — not scored

The scheduled run of 2026-11-14 (commit `c35ce63`) logged 7
calls dated 2026-11-14 and overwrote the log. It is also
commissioning and is not scored. Its rows are listed in
`docs/track-record-incidents.md`.

## Rules for the new series (PR 7.0)

- Only calls with `bar_complete` count toward scoring. For
  equities/ETFs a run that would log from an in-progress bar is
  refused outright; with `--provisional` it is labelled
  provisional and is never scored, and it never blocks a
  replacement call. For crypto/FX the in-progress bar is dropped
  and the call uses the last complete bar (no close to wait for).
- The log is append-only with a hash chain (`prev_hash` on every
  row). The publish step refuses to push unless the remote rows
  are byte-identical and the diff has only additions.
- `scripts/verify_track_record.py` walks the branch history and
  fails if any commit removed or changed an earlier row.
