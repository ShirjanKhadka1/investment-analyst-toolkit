# U.2 — Static workspace UI rendered from events.jsonl: pre-registered protocol

Round U: Agent Workspace UI, PR U.2. Branch `u2-workspace-ui`, based on
`u1-events` at `b1e3930`.

This file is the protocol for this PR, written BEFORE any implementation.
Nothing in this file is a result: the field-to-panel map, the
determinism rule, the accessibility rules, the styling constraints, the
export command and the acceptance criteria below are fixed here, and
the implementation commits that follow must match them. If the
implementation needs a different contract, this protocol is amended in
its own commit first — the contract is never silently changed to fit
the code.

No screenshots or UI notes were supplied by the user for this round:
this protocol is built from the brief's stated requirements only. No
panel, label, colour or layout below is claimed to come from a user
screenshot, and none is invented from an assumed one — where the brief
does not fix a detail, the protocol fixes the simplest deterministic
choice and says so.

## Scope (what this PR is and is not)

- This PR adds a static renderer that turns an `events.jsonl` (the U.1
  log) into a SINGLE self-contained HTML file. It is the read-only
  workspace view for Round U. It adds no live behaviour: U.3 (live
  mode + replay) is a later PR, and nothing here opens a socket,
  polls, or fetches.
- The renderer reads the U.1 schema only (`toolkit/events.py`,
  `SCHEMA_VERSION "1.0.0"`). It renders exactly what the events
  carry and invents nothing: no claim text, no number, no verdict and
  no timestamp is added that is not in the input log. Anything the
  log does not say is rendered as an explicit empty state or as
  UNKNOWN (see below), never as a plausible-looking value.
- No track-record file is touched: not the daily-track-record workflow
  YAML, not `scripts/track_record_daily.py`, not
  `toolkit/direction_rules.py`, not `docs/direction-rules.md`, not any
  `calls*.jsonl`, not the `data/track-record` branch, not
  `reports/daily/*`. The workspace UI is a separate stream from the
  pre-registered track record.
- No new core dependencies — stdlib only. No network in tests, and no
  network in the renderer at all: the output file contains no
  third-party runtime code, no external stylesheet, script, font or
  image reference, and no build step.
- The matrix gains ONE new implemented row; no existing row is flipped.

## What the U.1 fixture actually contains (read before rendering)

The committed fixture `examples/agent-run-events.jsonl` has 23 events
but only SEVEN of the eleven U.1 event types: `run_started`, `plan`,
`step_started`, `tool_call`, `tool_result`, `evidence_ref`,
`run_finished`. It contains NO `debate_turn`, NO `gate_result`, NO
`memo_section` and NO `error` event. The renderer must therefore
render every panel driven by an absent type as an explicit empty
state ("No gate results recorded yet", and so on) — never as a blank
hole and never by borrowing a value from another panel. The BLOCK /
PASS banner behaviour and the memo/debate panels are verified in
tests with synthetic events built through the U.1 recorder and
validator, not from the fixture.

Two further schema facts constrain the render contract:

- `memo_section` carries NO claim text in the U.1 schema — only
  `section` and the optional `symbol` / `status`. The memo panel
  therefore renders each section's name and status plus its linked
  citations; it cannot render claim-level prose, and it must not
  invent any.
- `debate_turn` carries `speaker` and `text` (required) and an
  optional free-form `stance` string. The schema does not constrain
  `stance` values, so the bull/bear split is a renderer rule (below),
  not a schema guarantee.

## Render contract: which fields map to which panels

The renderer is a pure function of `(events, reference_time)`. Input
events are read in file order with `toolkit.events.load_events`. Every
event-derived string in the output is HTML-escaped (`html.escape`);
no event value is ever interpolated raw.

| Panel (HTML id) | Source events / fields | Rule |
|---|---|---|
| Page header (`header`) | `run_started`: `run_id`, `team`, `symbol`/`symbols`, `exchange`/`market`; `run_finished`: `status` | Run id, team, symbol(s) and run status (`ok` / `failed` / `in progress` when no `run_finished` exists). |
| Plan rail (`nav#plan-rail`) | `plan`: `steps` (ordered list) | One entry per planned step, in plan order. Per-step status chip derived from later events: `done` when a `tool_result` with that `step` and `status "ok"` exists, `failed` when one has `status "failed"`, `in progress` when a `step_started` exists but no result yet, `not started` otherwise. |
| Agent lanes (`section#agent-lanes`) | `step_started`, `tool_call`, `tool_result`, grouped by `step` | One lane per `step` that appears in any of those events, in first-appearance order. A lane is an analyst role: its status chip is the same derived status as the plan rail. Each lane lists its tool calls in file order. |
| Live feed (`section#live-feed`) | ALL events, in file order | One feed row per event: event type, step/tool where present, and a freshness stamp (see below). Each `tool_call` row and its matching `tool_result` row (same `tool`, same `step`, the next `tool_result` after the call) form ONE collapsible pair rendered as a native `<details><summary>` element; unmatched calls/results render as single rows, never silently dropped. `error` events render as feed rows with an error chip. |
| Evidence panel (`aside#evidence-panel`) | `evidence_ref`: `source`, `symbol`, `step`, `last_date`, `n_points` | One entry per `evidence_ref`, in file order: source name, symbol/step, last date and point count where present. Each entry is an anchor target (`id="evidence-N"`). |
| Debate / bull–bear (`section#debate`) | `debate_turn`: `speaker`, `text`, `stance`, `step` | Two columns: bull and bear. `stance` matched case-insensitively — `"bull"` / `"bullish"` → bull column, `"bear"` / `"bearish"` → bear column; any other value, or a missing `stance`, goes to a "Neutral / unclassified" list below the columns. Speaker and text rendered verbatim (escaped). Empty state when no `debate_turn` exists. |
| Risk-veto banner (`div#risk-veto-banner`) | `gate_result`: `gate`, `verdict`, `reason` | Rendered ONLY when at least one `gate_result` has verdict `"BLOCK"`. It is the first element inside `main`, before every other panel, with `role="alert"`, the text `RISK VETO — <gate> BLOCKED` plus the gate's `reason` where present, and a class that makes it unmissable (full-width, high-contrast, not colour-only: the word BLOCK is in the text). It MUST NOT appear when every gate passes, and MUST NOT appear when no gate exists. |
| Risk-gate card (`section#risk-gates`) | `gate_result`: `gate`, `verdict`, `reason`, `symbol` | One row per gate check with its verdict chip (`PASS` / `BLOCK`) and reason where present, plus pass/fail counts in the card heading (e.g. "Risk gates — 2 passed, 1 blocked"). Empty state when no `gate_result` exists. |
| Memo (`section#memo`) | `memo_section`: `section`, `status`, `symbol`; `evidence_ref` for citations | One block per `memo_section`, in file order: section name and status. Citations are the `evidence_ref` events whose `symbol` matches the section's `symbol` (or, when the section has no `symbol`, whose `step` matches — memo sections carry no `step`, so in practice the symbol match); each citation is a link to its evidence-panel anchor (`#evidence-N`). A section with no matching evidence renders the citation line "No evidence linked — UNKNOWN". Empty state when no `memo_section` exists. |

## Numbers and UNKNOWN

A "number" in the UI is an `n_points` value (from `tool_result` /
`evidence_ref`) or a numeric value inside a `tool_result` `detail`
string. The renderer NEVER computes, rounds or re-derives a number:
it prints the value exactly as recorded.

- A number shown in the evidence panel or in a tool-result feed row
  is sourced when an `evidence_ref` exists for the same (`step`,
  `symbol`) pair; it is rendered with a link to that evidence entry.
- A number whose (`step`, `symbol`) pair has NO matching
  `evidence_ref` is rendered with an `UNKNOWN` badge next to it
  (text, not colour-only) and no source link. This is the only
  permitted rendering for an unsourced number.
- The fixture's `n_points` values: the rules-mode hooks record no
  `n_points` on `tool_result`, so the fixture renders no numbers in
  the feed; its two `evidence_ref` rows carry no `n_points` either,
  so the evidence panel shows sources only. The UNKNOWN rule is
  therefore tested with synthetic events, not the fixture.

## Determinism rule

Same `events.jsonl` + same `reference_time` → byte-identical HTML.
Enforced by construction:

- No wall-clock read anywhere in the renderer: freshness stamps are
  computed against an explicit `reference_time` parameter. When the
  caller omits it, the reference time is the LATEST `ts` in the log
  (not "now"). A log with no events uses no reference time and
  renders no stamps.
- Iteration order is file order everywhere; no set iteration, no dict
  ordering dependence, no random ids. Evidence anchors are numbered
  by file position (`evidence-0`, `evidence-1`, …).
- Output is written with LF line endings on every OS
  (`newline="\n"`), UTF-8, and ends with exactly one trailing
  newline. The HTML template is a fixed string in the module; the
  only variable parts are the escaped event values and the counts
  derived from them.
- The committed example is rendered with the FIXED reference time
  `2026-11-14T12:00:12Z` (12 seconds after every fixture event, so
  every fixture stamp reads `12s ago`). That value is part of this
  protocol, not a choice left to the render command.

## Freshness stamps

`freshness(ts, reference_time)`: `reference_time - ts`, rendered as
`"{n}s ago"` under 60 s, `"{n}m ago"` under 60 min, `"{n}h ago"`
under 24 h, `"{n}d ago"` otherwise; `n` is the whole-unit floor. An
event at or after the reference time renders `"0s ago"`. The stamp is
always accompanied by the absolute UTC timestamp in a `<time
datetime="...">` element, so the relative text is never the only
record of when something happened.

## Accessibility rules

- Semantic landmarks, in order: a skip link (`Skip to main content`),
  `header`, `nav#plan-rail` (`aria-label="Plan"`), `main`, inside it
  `section#live-feed`, `section#agent-lanes`, `section#debate`,
  `section#risk-gates`, `section#memo`, and `aside#evidence-panel`
  (`aria-label="Evidence"`), then `footer`. Every section has an
  `h2` with a matching `id` and `aria-labelledby`.
- Collapsible tool calls are native `<details><summary>` — keyboard
  operable by default, no JavaScript, no `tabindex` attribute
  anywhere in the output (no tabindex hacks, positive or negative),
  no `role="button"` on a non-button.
- Status is never colour-only: every chip carries its status word as
  text (`done`, `failed`, `in progress`, `not started`, `PASS`,
  `BLOCK`, `UNKNOWN`). The risk-veto banner uses `role="alert"`.
- Focus order is DOM order; a visible `:focus-visible` outline is in
  the stylesheet. `prefers-reduced-motion` is respected (no
  animations in the stylesheet at all, so there is nothing to
  disable — stated here so a later animation is a protocol change,
  not a silent addition).
- All text meets the contrast of the chosen palette in both colour
  schemes (dark text on light background in light mode; light text on
  dark background in dark mode). No image carries information, so
  there are no `alt`-text decisions to get wrong.

## Styling constraints (mobile-first, no build step, no third-party code)

- ONE self-contained HTML file: all CSS is in a single inline
  `<style>` block in the `<head>`; there is NO JavaScript at all and
  NO external reference of any kind (no stylesheet, script, font,
  image or iframe URL — the file must render fully offline).
- Mobile-first: single-column layout by default; lanes, debate
  columns and the evidence panel stack vertically. At `min-width:
  900px` the layout becomes two columns (plan rail + lanes on the
  left, feed + evidence on the right) via a single media query.
- Dark mode is `prefers-color-scheme: dark` only — no toggle, no
  stored preference, no JavaScript. Light mode is the default.
- The stylesheet is deliberately small (well under 200 lines) and
  lives in the renderer module as a constant, so the rendered file
  is reproducible from the module alone.

## Renderer and export command

- `toolkit/workspace_ui.py` (stdlib only): `render_events_html(events,
  reference_time=None) -> str` (pure: events are dicts in file order,
  `reference_time` an ISO 8601 string or `None` for "latest event
  ts"); `render_events_file(input_path, output_path,
  reference_time=None)` (reads with `toolkit.events.load_events`,
  writes the HTML with LF endings); `freshness(ts,
  reference_time) -> str`. Invalid events are refused with
  `ValueError` via `toolkit.events.validate_event` — the renderer
  never renders a log that fails U.1 validation.
- `scripts/render_workspace.py` (stdlib only, same CLI pattern as
  `scripts/run_fake_agent_events.py`): `python3
  scripts/render_workspace.py [--events PATH] [--output PATH]
  [--reference-time ISO]`. Defaults: `--events
  examples/agent-run-events.jsonl`, `--output
  examples/agent-run-workspace.html`, `--reference-time` the latest
  event ts. Exit 0 on success, printing the output path and the
  event count; exit 1 when the log fails validation.
- The committed example `examples/agent-run-workspace.html` is
  rendered from the committed fixture with
  `--reference-time 2026-11-14T12:00:12Z` and is byte-identical on
  re-render with the same arguments.

## Tests (`tests/test_workspace_ui.py`, no network)

- Determinism: rendering the same events list twice gives
  byte-identical HTML; rendering the fixture file twice gives
  byte-identical files.
- Every required panel is present in the fixture render (header,
  plan rail, agent lanes, live feed, evidence panel, debate,
  risk-gate card, memo) by HTML id, with the correct empty states
  for the absent types (no `risk-veto-banner` element in the
  fixture render — there is no BLOCK and no gate at all).
- UNKNOWN rendering: a synthetic log with a `tool_result` carrying
  `n_points` and no matching `evidence_ref` renders the number with
  an `UNKNOWN` badge and no source link; the same number WITH a
  matching `evidence_ref` renders with a link to its evidence anchor
  and no UNKNOWN badge.
- Risk-veto banner: present (first element in `main`,
  `role="alert"`, gate name and `BLOCK` in text) on a synthetic log
  with a BLOCK gate; absent on a synthetic log where every gate
  passes; absent on the fixture (no gates).
- Risk-gate card counts: pass/fail counts in the heading match the
  synthetic log's gate verdicts.
- Keyboard accessibility: every collapsible tool call is a real
  `<details><summary>`; the output contains no `tabindex` attribute
  and no `role="button"`; landmarks (`header`, `nav`, `main`,
  `aside`, `footer`) and a skip link are present.
- Freshness stamps are deterministic given a fixed reference time
  (known-answer cases: 12 s → `12s ago`, 90 s → `1m ago`, an event
  at the reference time → `0s ago`); the absolute timestamp is also
  present in a `<time datetime="...">` element.
- Evidence panel links numbers to sources: each evidence entry has
  a stable `evidence-N` anchor and the memo citation links resolve
  to an anchor that exists in the same document.
- The fixture `examples/agent-run-events.jsonl` renders without
  error (via the CLI script run as a subprocess with `--output` to
  a temp dir, offline only), and the committed
  `examples/agent-run-workspace.html` is byte-identical to a fresh
  render of the fixture with the protocol's fixed reference time.
- The renderer refuses an invalid log (a row failing
  `validate_event`) with `ValueError`, and the CLI exits 1 on it.

## Matrix and generated files

- ONE new implemented row in `scripts/generate_capability_matrix.py`
  (under `tradingagents`, directly after the U.1 event-log row):
  "Static agent workspace UI (single self-contained HTML rendered
  from events.jsonl)", evidence `toolkit/workspace_ui.py`, test
  `tests/test_workspace_ui.py`. No existing row changes.
- `docs/CAPABILITY-MATRIX.md` regenerated BY SCRIPT ONLY
  (`python3 scripts/generate_capability_matrix.py`), which also
  rewrites the README coverage table block.
- `status.json` regenerated ONLY via `scripts/generate_status.py`
  (with the same args the repo uses: `--root <root> --repo
  investment-analyst-toolkit`); the README `Tests: N/N` prose line
  is updated to the new measured total, following the existing
  append-a-clause style of that line, and the `coverage NN%` prose
  is updated only if the measured coverage actually changes.

## Acceptance criteria

1. The protocol commit is first in the branch history; implementation
   commits follow it (protocol-before-results, visible in commit order).
2. The renderer is a pure function of `(events, reference_time)`:
   same input renders byte-identical HTML (test evidence, not a claim).
3. Every required panel is present in the fixture render, with honest
   empty states for the event types the fixture does not contain.
4. A BLOCK gate produces an unmissable `role="alert"` banner as the
   first element in `main`; a PASS-only log and the fixture produce no
   banner element at all.
5. An unsourced number renders as UNKNOWN; a sourced number links to
   its evidence anchor.
6. Collapsible tool calls are native `<details><summary>`; the output
   contains no `tabindex` and no JavaScript.
7. The single HTML file contains no external reference and renders
   fully offline (no third-party runtime code, no build step).
8. The full test suite passes and `scripts/qa.py --offline` passes
   all 13 gates.
9. The matrix has one new implemented row (71 rows: 36 implemented,
   21 partial, 14 not planned) and `generate_capability_matrix.py
   --check` passes.
10. No track-record file is touched (verified by `git diff --stat`
    against the `u1-events` base before pushing).
