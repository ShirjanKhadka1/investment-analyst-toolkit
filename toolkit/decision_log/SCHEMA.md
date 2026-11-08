# Decision-log schema (Engine 1.4)

One JSON object per line in `calls.jsonl` (created by
`toolkit/decision_log.py:write_call`; the log starts empty — no calls
have been logged yet).

## Call record (written before the outcome is known)

| Field | Type | Required |
|---|---|---|
| `schema_version` | string, e.g. `"1.4.0"` | yes |
| `call_id` | string, first 16 hex chars of sha256 of the call content | yes |
| `date` | `YYYY-MM-DD` (call date) | yes |
| `symbol` | string, e.g. `"BNT"` | yes |
| `exchange` | string, e.g. `"NEPSE"` | yes — since Round 3; writes without it are refused. Reads of legacy logs (written before the requirement) default a missing value to `"NEPSE"` |
| `thesis` | string, one paragraph | yes |
| `horizon_days` | positive int | yes |
| `expected` | `"up"` / `"down"` / `"range"` | yes |
| `expected_low`, `expected_high` | numbers (range calls only) | range only |
| `entry_close` | number — first close *after* the call date (point-in-time reference) | no |
| `sources` | non-empty list of source labels | yes |
| `confidence` | number in [0, 1] — stated probability the expected direction/range is right | yes |
| `status` | `"open"` then `"settled"` | yes |

## Settlement fields (added by `settle_call`, after the horizon)

`settled_date`, `realised_close`, `realised_return`, `index_return`,
`index_source` (named — settlement refuses an unnamed index series),
`alpha` (realised return minus index return), `hit` (bool), `brier`
(Brier score on the stated P(up); `null` for range calls).

Settlement rules: only after `date + horizon_days`; realised prices
and the index return are supplied by the caller, never invented by
this module; a call settled once cannot be re-settled.

## Scoreboard

`docs/track-record.md` is regenerated from the log by
`render_scoreboard_md`. It starts at n=0 and stays there until a
real call is logged and settled. Losses are included; open calls
never count as wins.

## Reflections

`docs/reflections.jsonl` — one JSON object per line: `date`,
`call_id`, `author` (default `"portfolio-manager"`), `text`. Written
by `append_reflection` after a call settles; the Portfolio Manager
agent reads the reflections for a symbol before its next run on
that symbol. The file starts empty — no reflections exist yet.
