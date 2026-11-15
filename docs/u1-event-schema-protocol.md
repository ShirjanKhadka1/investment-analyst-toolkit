# U.1 — Agent run event schema and recorder: pre-registered protocol

Round U: Agent Workspace UI, PR U.1. Branch `u1-events`, based on main
`39a5ba7`.

This file is the protocol for this PR, written BEFORE any implementation.
Nothing in this file is a result: the schema, the event list, the
validation rules, the hash-chain rule, the fixture-run plan and the
acceptance criteria below are fixed here, and the implementation commits
that follow must match them. If the implementation needs a different
schema, this protocol is amended in its own commit first — the schema
is never silently changed to fit the code.

## Scope (what this PR is and is not)

- This PR adds an event log for agent-team runs: a schema-validated,
  versioned, hash-chained `events.jsonl`. It is the data layer Round U
  (workspace UI) will read in U.2/U.3. It adds no UI itself.
- There is NO `toolkit/agent_team.py` on main. The executable agent team
  is unmerged PR #29; this PR does NOT depend on it and does NOT import
  it. The "agent team" on main is `toolkit/rules_mode.py`
  (`run_rules_team` / `run_global_rules_team`) plus the live-run
  pipeline `toolkit/autonomous.py` (`run_once`). The additive hooks in
  this PR attach to those functions only.
- The hash-chain pattern follows `toolkit/decision_log.py` (PR 7.0):
  append-only rows, `prev_hash` = sha256 of the previous row's canonical
  JSON bytes, first row stores `"genesis"`, `verify_chain` fails loudly
  on a broken link.
- No track-record file is touched: not the daily-track-record workflow
  YAML, not `scripts/track_record_daily.py`, not
  `toolkit/direction_rules.py`, not `docs/direction-rules.md`, not any
  `calls*.jsonl`, not the `data/track-record` branch, not
  `reports/daily/*`. The event log is a separate stream from the
  pre-registered track record.
- No new core dependencies — stdlib only. No network in tests.
- The matrix gains ONE new implemented row; no existing row is flipped.

## Schema version

`SCHEMA_VERSION = "1.0.0"`. Every event carries `schema_version`
(`"1.0.0"`). A reader that sees any other version must refuse the event
rather than guess what it means.

## Event types (full list — exactly these eleven)

`run_started`, `plan`, `step_started`, `tool_call`, `tool_result`,
`evidence_ref`, `debate_turn`, `gate_result`, `memo_section`,
`run_finished`, `error`.

`debate_turn` is defined by the schema but emitted by NO hook in this
PR — there is no debate runner on main yet. It is reserved for a later
Round U PR (or a future debate runner) so the schema does not have to
change when debate arrives.

## Common fields (every event, every type)

Required:

| Field | Type | Rule |
|---|---|---|
| `schema_version` | string | Must equal `SCHEMA_VERSION` (`"1.0.0"`). |
| `run_id` | string | Non-empty. Identifies one run; all events of a run share it. |
| `seq` | integer | 0-based, consecutive within the file, starting at 0. `bool` is not an integer here. |
| `ts` | string | ISO 8601 timestamp, timezone-aware, UTC (`...Z` form). Naive or unparseable timestamps are rejected. |
| `type` | string | One of the eleven event types above. |

Recorder-added (not caller-supplied):

| Field | Type | Rule |
|---|---|---|
| `prev_hash` | string | sha256 hex of the previous row's canonical JSON bytes; `"genesis"` for the first row. |

## Per-type fields

Required fields are rejected when missing or mistyped; optional fields
may be omitted; extra fields are allowed (forward compatibility) but
must be JSON-serialisable.

| Type | Required | Optional |
|---|---|---|
| `run_started` | `team` (non-empty string) | `symbol` (non-empty string), `symbols` (non-empty list of strings), `exchange`, `market` |
| `plan` | `steps` (non-empty list of strings) | `symbols` (non-empty list of strings) |
| `step_started` | `step` (non-empty string) | `symbol` |
| `tool_call` | `tool` (non-empty string) | `step`, `symbol` |
| `tool_result` | `tool` (non-empty string), `status` (`"ok"` or `"failed"`) | `step`, `symbol`, `n_points`, `detail` |
| `evidence_ref` | `source` (non-empty string) | `symbol`, `step`, `last_date`, `n_points` |
| `debate_turn` | `speaker` (non-empty string), `text` (non-empty string) | `stance`, `step` |
| `gate_result` | `gate` (non-empty string), `verdict` (`"PASS"` or `"BLOCK"`) | `symbol`, `reason` |
| `memo_section` | `section` (non-empty string) | `symbol`, `status` |
| `run_finished` | `status` (`"ok"` or `"failed"`) | `symbol`, `symbols` |
| `error` | `error` (non-empty string) | `symbol`, `step` |

## Validation rules (`validate_event`)

`validate_event(event)` raises `ValueError` on ANY of:

- the event is not a dict;
- `type` is missing or not one of the eleven types;
- any common field is missing, mistyped, or invalid (`schema_version`
  != `"1.0.0"`, empty `run_id`, `seq` negative / non-integer / `bool`,
  `ts` unparseable or timezone-naive);
- any required per-type field is missing or mistyped, or an enum field
  (`status`, `verdict`) holds a value outside its allowed set;
- the event is not JSON-serialisable (a `json.dumps` without a
  `default=` fallback must succeed).

Validation is pure and side-effect free: it never writes, never reads a
file, never calls the network.

## Hash-chain rule (following `toolkit/decision_log.py`)

- Canonical bytes of a row: `json.dumps(row, sort_keys=True,
  separators=(",", ":"), default=str)` encoded as UTF-8 — the same
  canonical form `decision_log.canonical_row_bytes` uses, so
  whitespace can never break a link.
- `row_hash(row)` = sha256 hex of the canonical bytes.
- Each appended row stores `prev_hash`: the hash of the previous row,
  or `"genesis"` for the first row. The row's own hash is computed over
  the row INCLUDING its `prev_hash` (as in `decision_log`), so moving
  a row changes the chain.
- The log is append-only: the recorder never rewrites or deletes a
  row. One JSON object per line, LF line endings on every OS
  (`newline="\n"` when writing, as in `decision_log`).
- `verify_events(path)` / `verify_chain(path)` return a list of
  problems; an empty list means the chain holds. Per row, in order, it
  checks: the row validates (`validate_event`); `seq` equals its
  0-based file position; `prev_hash` equals the hash of the previous
  row (`"genesis"` for the first). A tampered row — edited payload,
  inserted row, or reordered row — is reported, never silently passed.
- `load_events(path)` reads all events in file order; a missing file
  reads as an empty list.

## Recorder (`toolkit/events.py`, stdlib only)

- `EventRecorder(path, run_id, now=None)`: `path` is the `events.jsonl`
  to append to; `run_id` is the run id for every event it records.
  `now` is an injectable clock for determinism: `None` (UTC now),
  a fixed ISO string (every event gets it), or a zero-argument callable
  returning an ISO string. Appending to an existing file continues its
  `seq` and chain.
- `record(event_type, payload=None, **fields)`: builds the event
  (common fields + payload), validates it, appends one canonical JSON
  line, updates the chain state, and returns the recorded event dict.
- The recorder never fetches, never calls an LLM, never invents a
  number: every payload value comes from the caller or from the run
  it records.

## Additive hooks (opt-in only)

`recorder=None` is the default on every hooked function. When no
recorder is passed, behaviour must be byte-identical to before: same
return values, same files written, same exceptions. Existing tests
must pass unchanged.

- `toolkit/rules_mode.py::run_rules_team(symbol, exchange, inputs,
  recorder=None)` — team `"nepse_rules"`. Emits: `run_started`,
  `plan` (steps: `technical`, `fundamentals`, `sentiment`,
  `broker_flow`, `macro`, `corporate_actions`), then per section
  `step_started` + `tool_call` + `tool_result` (tool = the section
  function name, status `"ok"`), `evidence_ref` per headline that
  carries a source, and `run_finished` (status `"ok"`). On an
  exception: an `error` event, then the exception is re-raised —
  the return value and the exception are unchanged.
- `toolkit/rules_mode.py::run_global_rules_team(symbol, market, inputs,
  recorder=None)` — team `"global_rules"`. Same shape; steps:
  `technical`, `us_fundamentals`, `etf_index`, `fx_macro`, `crypto`,
  `earnings`, `sentiment`.
- `toolkit/autonomous.py::run_once(config, recorder=None)` — team
  `"autonomous"`. Emits: `run_started` (with `symbols`), `plan`
  (steps: `fetch`, `validate`, `select_model`, `forecast`, `report`),
  then per symbol `step_started`, `tool_call` (tool
  `"fetch_fresh_prices"`), on a fresh fetch `tool_result` (status
  `"ok"`, `n_points`), `evidence_ref` (source / last date / point
  count from the fetch provenance), `gate_result` for the freshness
  gate and for `validate_series` (verdict `"PASS"`), and
  `memo_section` (section `"autonomous_report"`) after the report is
  written; on a per-symbol failure an `error` event (the run still
  continues to the next symbol, exactly as before), and finally
  `run_finished` (status `"ok"` — the run itself completed even when
  individual symbols failed, matching the existing summary contract).
  A recorder failure inside a per-symbol step is caught by the
  existing per-symbol handler and the symbol is marked
  `STALE_OR_FAILED`, like any other per-symbol failure; a recorder
  failure outside the per-symbol loop propagates, as in
  `decision_log`.

## Fixture-run plan (no network, no LLM, deterministic)

`scripts/run_fake_agent_events.py` (stdlib only, runnable offline):

- Uses the rules team only — rules mode needs no LLM and no provider,
  so the "fake provider" is a fixed, deterministic input bundle
  standing in for fetched data: 30 rising closes, a banks sector with
  two metrics, three headlines (two with sources), two broker-flow
  rows, a macro dict, one corporate action.
- Fixed `run_id` (`"u1-fixture-run"`) and a fixed clock
  (`"2026-11-14T12:00:00Z"` for every event), so the output is
  byte-identical on every run.
- Default output `examples/agent-run-events.jsonl` (committed fixture);
  `--output PATH` overrides it. Re-running deletes the previous output
  first, so the fixture never accumulates duplicate runs.
- Usage: `python3 scripts/run_fake_agent_events.py [--output PATH]`.
  Exit 0, prints the path and the event count.

## Tests (`tests/test_events.py`, no network)

- Schema validation accepts a good event of every one of the eleven
  types and rejects: a missing `type`, an unknown `type`, a missing
  required field, a bad timestamp (garbage; timezone-naive), a bad
  `seq` (negative, string, `bool`), a wrong `schema_version`, an
  empty `run_id`, a non-dict event.
- The recorder appends valid JSONL with a correct hash chain
  (`verify_events` returns `[]`); appending to an existing file
  continues `seq` and the chain; a fixed clock makes two runs
  byte-identical.
- `verify_events` fails on a tampered row (edited payload) and on a
  broken `prev_hash`.
- The hooks do not change existing behaviour: `run_rules_team` and
  `run_global_rules_team` return identical dicts with and without a
  recorder; `run_once` (fake fetchers, fixed `now`) returns an
  identical summary with and without a recorder.
- The fixture-run script produces a valid `events.jsonl` (run via
  subprocess with `--output` to a temp dir, offline only), and the
  committed `examples/agent-run-events.jsonl` verifies clean.

## Matrix and generated files

- ONE new implemented row in `scripts/generate_capability_matrix.py`
  (under `tradingagents`): "Agent run event log (events.jsonl,
  schema-validated, versioned, hash-chained)", evidence
  `toolkit/events.py`, test `tests/test_events.py`. No existing row
  changes.
- `docs/CAPABILITY-MATRIX.md` regenerated BY SCRIPT ONLY
  (`python3 scripts/generate_capability_matrix.py`), which also
  rewrites the README coverage table block.
- `status.json` regenerated ONLY via `scripts/generate_status.py`
  (with the same args the repo uses: `--root <root> --repo
  investment-analyst-toolkit`); the README `Tests: N/N` prose line is
  updated to the new measured total, following the existing
  append-a-clause style of that line, and the `coverage NN%` prose is
  updated only if the measured coverage actually changes.

## Acceptance criteria

1. The protocol commit is first in the branch history; implementation
   commits follow it (protocol-before-results, visible in commit order).
2. Schema validation rejects a bad event (test evidence, not a claim).
3. The chain verifies on a fresh log and fails on a tampered row.
4. The hooks do not change existing behaviour (output-equality tests
   with and without a recorder; the pre-existing suite passes
   unchanged).
5. The fixture run produces a valid `events.jsonl` offline, with no
   network and no LLM, deterministically.
6. The full test suite passes and `scripts/qa.py --offline` passes
   all 13 gates.
7. The matrix has one new implemented row (70 rows: 35 implemented,
   21 partial, 14 not planned) and `generate_capability_matrix.py
   --check` passes.
8. No track-record file is touched (verified by `git diff --stat`
   against main before pushing).
