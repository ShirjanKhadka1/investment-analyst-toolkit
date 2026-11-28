# Round 9 evaluation protocol (Agents) — PRE-REGISTERED

Written 2026-11-28, **before any Round 9 implementation exists**.
This protocol fixes what will be tested, the baselines, what counts as
a win versus "no edge", the fixtures, and what each model card will
contain — in advance, for 9.1, 9.2 and 9.3. No parameter, threshold or
verdict below is tuned after seeing results; if the results are bad,
the bad result is published, not re-tuned away. Model rules
(MASTER-ROADMAP.md): a model must beat its baseline out of sample or
be labelled "no edge". **LLM output never counts as a validated
prediction.** A memo, a debate verdict or a dossier is text until its
calls settle against a baseline — and no headline is published before
100 settled calls in the series being scored.

Foundation branch: `round-9-foundation`, based on main
`94a6db6b3bae773fbcc6fe9f9397476c7dd790e0` (2026-11-27). This protocol
and the regenerated capability matrix are the foundation; no 9.1/9.2/
9.3 implementation is in this branch.

## Amendment rule

If an implementation needs a different test, threshold, fixture or
verdict rule, this protocol is amended in its own commit first — the
protocol is never silently changed to fit the code. An amendment names
the date, the reason, and exactly which sections change, in the style
of the 2026-11-14 amendment note in
`docs/volatility-forecast-eval-protocol.md`.

## Programme rules that bind all three items

- No new core dependencies: stdlib only. Optional extras only, and
  only if a builder justifies one in the PR.
- No network in tests. Every evaluation runs on the fake provider
  (`toolkit/llm_providers.py`, `FakeProvider`: scripted responses,
  fixed token counts, scripted errors) or on fixture files.
- No fake numbers: every number in a result comes from a rerunnable
  script, never typed. Every number in a memo or dossier must trace
  to a tool output or an input row, or the memo is blocked (9.2).
- No track-record file is touched: not the daily-track-record
  workflow YAML, not `scripts/track_record_daily.py`, not
  `toolkit/direction_rules.py`, not `docs/direction-rules.md`, not
  any `calls*.jsonl`, not the `data/track-record` branch, not
  `reports/daily/*`. The `llm-team` series is a separate labelled
  series in the decision log, never mixed into the pre-registered
  rules' series.
- PR #29 is not modified by the foundation and is not merged by any
  Round 9 PR unless the user writes "merge #N" naming it. 9.1 works
  from a rebase of its branch (see below).
- Maximum 3 PRs per round in this repo. 9.3's persona/skill files are
  a separate PR in `finance-research-agents`, as the roadmap orders.
- No release, no tag, no workflow deployment. Any workflow YAML is
  staged under `ops/ready-for-browser/` with a CI test running its
  exact commands locally, including a SECOND-RUN and multi-run test.
  The user deploys it.

## Shared ground (read before building)

- **Agent team code, unmerged:** `toolkit/agent_team.py` exists only
  on PR #29 (branch `llm-agent-team`, open, conflicting with main as
  of 2026-11-27 — `mergeable: false`, `mergeable_state: dirty`).
  Eight steps: gather → analysts → debate → trader → risk →
  portfolio_manager → log_call → memo. Checkpoint JSON after every
  step and after each LLM call inside the debate; resume without
  redoing completed steps or double-logging. Cost caps 4000 tokens /
  USD 0.50 per run. Architecture: `docs/llm-agent-team.md` (on the
  PR branch, not on main). Tests on that branch:
  `tests/test_agent_team.py` (PR #29 states 19 tests; not on main).
- **Provider layer (on main):** `toolkit/llm_providers.py` —
  Ollama, OpenAI-compatible, Anthropic, Gemini, fake. Caps enforced
  on every call; schema-validated JSON with a bounded repair loop;
  keys redacted from logs and exceptions. Factory:
  `get_provider(name)`.
- **Deterministic teams (on main):** `toolkit/rules_mode.py`
  (`run_rules_team` for NEPSE, `run_global_rules_team` for global;
  version 1.4.0). `GLOBAL_TEAM` has five personas:
  `us_large_cap_fundamentals`, `etf_index`, `fx_macro`, `crypto`,
  `earnings`. Any input a rule cannot evaluate returns `UNKNOWN`
  with a reason, never a filled-in value.
- **Event log (on main):** `toolkit/events.py` — schema-validated,
  versioned (`SCHEMA_VERSION = "1.0.0"`), hash-chained `events.jsonl`
  (`run_started`, `plan`, `step_started`, `tool_call`, `tool_result`,
  `evidence_ref`, `gate_result`, `memo_section`, `run_finished`,
  `error`). Opt-in recorder; without one, hooked functions return
  byte-identical output. Fixture runs:
  `scripts/run_fake_agent_events.py` and
  `scripts/run_fake_agent_events_2.py` write
  `examples/agent-run-events.jsonl` (and `-2.jsonl`) with no network
  and no LLM.
- **Workspace UI (on main, fixture-driven prototype):**
  `toolkit/workspace_ui.py` renders one self-contained HTML file
  from an event log; a number with no matching `evidence_ref`
  renders as UNKNOWN. `toolkit/live_server.py` streams recorded
  event files over SSE; `toolkit/live_replay.py` diffs two runs and
  meters cost/latency. None of these runs a live agent.
- **Risk gate (on main):** `toolkit/position_plan.py` —
  `build_plan` (stop 2x mean absolute daily move below entry,
  target 2x above, reward:risk 2.0) through the 19-check
  `extended_risk_gate`. A refused plan is a BLOCK with
  `plan_refused`.
- **Valuation and fundamentals (on main):**
  `toolkit/valuation.py` (`dcf_value`, `gordon_value`,
  `implied_price_from_multiple`); `toolkit/edgar.py`
  (`ticker_to_cik`, `fetch_company_facts`,
  `get_fundamentals_as_of` — point-in-time, prefer-10-K);
  `toolkit/connectors.py` (Yahoo prices with per-bar volume via
  `BarsConnector`, opt-in Stooq, ECB reference rates, World Bank
  macro); `toolkit/cross_sectional.py` (momentum/value/quality
  proxies, decile tests net of costs).
- **Research templates (finance-research-agents, 8 files in
  `templates/`):** `data-first-checklist.md`, `earnings-review.md`,
  `forensic-checklist.md`, `initiation-report.md`,
  `ledger-control-checklist.md`, `research-to-use-case.md`,
  `structured-debate.md`, `valuation-sanity-check.md`.
- **Personas (finance-research-agents, 37 agent files in `agents/`,
  `agents.json`):** global-side files on main are
  `agents/world-market-researcher.md` and the runbooks
  `runbooks/company-deep-dive.md` (Investment Researcher →
  Forensic → Financial → Valuation → Reality Checker),
  `runbooks/structured-debate.md` and
  `runbooks/nepse-agent-team.md` (parallel analysts, debate blocked
  until every analyst has reported). `skills/` holds 8 NEPSE-only
  skill files; there are **no global skill files yet**.
- **Live state at pre-registration (checked 2026-11-27/28):** 1142
  tests passing on a fresh clone of main `94a6db6`. Track record:
  23 rows in `calls-v2.jsonl` on `data/track-record`, 0 settled,
  scoreboard n=0 — no hit rate is published. Volatility ledger:
  built and tested, 0 forecast rows, 0 scores, workflow staged not
  deployed. PR #29's known volume gap is closed on main by Round
  7.1 (Yahoo per-bar volume), so a 9.1 rebase must adopt
  `BarsConnector` rather than re-implementing volume.

---

## 9.1 — Harden the executable team

**Scope.** Rebase PR #29 (`llm-agent-team`) onto current main,
resolve its conflicts, and harden the team without changing what it
is: debate, reflection, resume, risk-gate integration, separate
`llm-team` series. Paper only — it proposes no order and instructs
no execution. The team is not merged by this item; merging needs the
user's explicit "merge #N".

**What will be tested.**

1. **Debate:** bull then bear, each schema-validated JSON (side,
   1–5 claims, up to 5 key risks); the bull result is checkpointed
   before the bear call; a bear failure does not redo the bull.
2. **Reflection:** the latest reflections for the symbol feed the
   portfolio-manager prompt, and a run after a reflection lands
   produces a memo that cites it; a run with no reflections is
   unchanged.
3. **Resume:** a run interrupted at each step resumes from the
   failed step in a fresh process — no completed step is re-run, no
   LLM call is repeated, no call is double-logged; a completed
   re-run returns the stored memo and makes no provider calls.
4. **Risk-gate integration:** the 19-check extended gate is the
   veto. On BLOCK the portfolio manager makes no LLM call, no call
   is logged, and the memo direction is `no_call` with confidence
   and horizon `None`. On PASS the memo carries the gate's PASS.
   The volume-less exit-liquidity path still fail-closes (BLOCK)
   when no volume is supplied — and the rebase must use the
   Round 7.1 volume path (`BarsConnector`) so a normal Yahoo run
   supplies it.
5. **Separate `llm-team` series:** `write_llm_call` forces the
   series and refuses any other value; `score_series` and
   `render_series_md` count and render one series at a time; no
   `llm-team` row ever appears in the rules' series rendering.
6. **Caps and look-ahead:** per-run token/cost caps (4000 / USD
   0.50) checked after every LLM call; a future bar leaves the memo
   unchanged and mutating the last used bar changes it; excluded
   tool data never reaches the analysts.

**Baseline.** The deterministic rules-mode teams
(`run_rules_team` / `run_global_rules_team`) and the pre-registered
rules' track-record series, plus the naive and buy-and-hold
settlement baselines in `toolkit/track_record.py` (naive = previous
5-day direction continues; buy-and-hold always up). For the
engineering behaviours (debate, resume, gate, series separation)
the baseline is the PR #29 code as opened: the rebase must not
regress any of its 19 tests, and every new behaviour gets a test
that fails on the un-rebased code.

**Win vs "no edge".** Two separate verdicts, both pre-registered.
(a) Engineering: win = every test in 1–6 above passes on the
rebased main; any failure is a failed item, not a partial win.
(b) Prediction: the `llm-team` series direction calls are scored
only after they settle, Brier primary and hit rate secondary,
against the naive and buy-and-hold baselines with a seeded
bootstrap confidence interval. Win = the series beats the naive
baseline out of sample at 100 settled calls. Before 100 settled
calls there is no prediction verdict at all — the headline stays
withheld and the model card reports counts only. If at 100 settled
calls it does not beat naive, the verdict is "no edge" and the
series stays in the record.

**Fixtures.** Fake provider only, with scripted bull/bear/trader/PM
responses and scripted failures (schema failure, cost-cap breach,
transport error). Synthetic price fixtures: a rising series, a flat
series (plan refusal), a volume-less series (gate BLOCK), a series
with a future bar appended (look-ahead). The U.1/U.2 event fixtures
(`examples/agent-run-events.jsonl`, `examples/agent-run-events-2.jsonl`)
for the event-log shape. No real LLM key, no network, no real
prices in tests.

**Model card contents** (`docs/llm-team-model-card.md`, written
after the first settled evaluation — counts only before that):
series name and separation rule; rebase base commit and conflict
resolution note; debate/resume/gate test counts; caps; n settled /
n open; Brier and hit rate vs naive and buy-and-hold with bootstrap
CI; verdict (withheld / beats naive / no edge); known gaps
(including any gap the rebase could not close).

---

## 9.2 — Groundedness checker + agent eval suite

**Scope.** A checker that blocks any memo in which a number does
not trace to a tool output or an input row, plus an agent eval suite
with known-answer fixtures. The checker is a pure function over
(memo text, event log) — it takes the `evidence_ref` and
`tool_result` events as the only sources of truth. It does not
generate text, does not call an LLM, and does not edit the memo.

**What will be tested.**

1. **Structure:** a memo passes the structure check only when
   every required section is present (thesis, key risks,
   what-would-change-my-mind, gate verdict, sources). A memo
   missing any section is blocked, whatever its numbers say.
2. **Groundedness:** every numeric token in the memo (value plus
   its unit or label, as extracted by the checker's stated number
   grammar) must match an `evidence_ref` or `tool_result` value
   from the same run, within the checker's stated tolerance for
   rounding. Any untraced number blocks the memo. A number the
   checker cannot parse is treated as untraced (fail-closed), and
   the failure names the token.
3. **Risk-gate agreement:** on fixture runs, the checker's verdict
   (PASS/BLOCK) is compared with the risk gate's verdict from
   `gate_result` events. Agreement is reported per fixture; a
   disagreement is a named mismatch, not silently resolved.
4. **Eval suite, run on the fake provider:** the suite is a fixed
   set of known-answer fixtures — fully-grounded memos (must PASS),
   memos with one planted unsourced number (must BLOCK, naming the
   number), memos with a fabricated source label (must BLOCK),
   memos with a rounded value inside tolerance (must PASS) and
   outside tolerance (must BLOCK), memos missing a section (must
   BLOCK), and runs where the gate BLOCKed but the memo claims PASS
   (must BLOCK). The suite runs with no network and no real key,
   and its pass/fail list is printed in full — no summary-only
   reporting.

**Baseline.** Two baselines, both pre-registered. (a) No checker:
every memo passes — the suite measures what the checker adds.
(b) Keyword-only checker: a memo passes if it contains the word
"source" or a citation-looking string, with no value matching.
The groundedness checker must beat both baselines on the fixture
suite.

**Win vs "no edge".** Win = the checker blocks 100% of fixture
memos containing any unsourced number (0 false passes — a single
false pass fails the item) AND passes at least 95% of the
fully-grounded fixture memos AND agrees with the risk gate on at
least 90% of fixture runs AND beats both baselines on the suite.
Anything less is "no edge": the checker is recorded as not
better than the baselines, and no memo is claimed grounded on its
strength. The 100% / 95% / 90% thresholds are fixed here, before
any fixture is run.

**Fixtures.** All fixtures are committed files, not generated at
eval time: memo texts (grounded, planted-number, fabricated-source,
rounded in/out of tolerance, missing-section, gate-mismatch) paired
with their event logs (`examples/` fixtures, built from the U.1
fixture-run pattern in `scripts/run_fake_agent_events.py` — fixed
run id, fixed clock, byte-identical re-runs). The number grammar
and the rounding tolerance are fixed in the checker module's
docstring before the first fixture is written, and the fixtures
are written to that grammar — the grammar is never widened to make
a failing fixture pass.

**Model card contents** (`docs/groundedness-model-card.md`):
number grammar and rounding tolerance; fixture inventory with the
expected verdict of each; suite results in full (per-fixture
PASS/BLOCK, false-pass count, false-block count, gate-agreement
rate); comparison against both baselines; verdict (win / no edge);
known gaps (number forms the grammar cannot parse, tolerance
edge cases).

---

## 9.3 — Company dossier pipeline

**Scope.** One ticker in, one dossier out: EDGAR fundamentals,
prices, peers, valuation (DCF and comps), risks, and a monitoring
checklist. Every number sourced or UNKNOWN. The pipeline reuses the
8 research templates in `finance-research-agents/templates/` and
the `company-deep-dive` runbook order (Investment Researcher →
Forensic → Financial → Valuation → Reality Checker). The global
personas are added as agent and skill files in
`finance-research-agents` in a separate PR there. No new core
dependencies; no network in tests.

**What will be tested.**

1. **Ticker → fundamentals:** `ticker_to_cik` + point-in-time
   `get_fundamentals_as_of` (prefer 10-K) return the requested
   concepts as of the dossier date; a concept with no filing by
   that date is UNKNOWN, never filled from a later filing.
2. **Prices:** closes from the connector chain (Yahoo, Stooq
   opt-in) with provenance (source, last date, age); a stale or
   failed fetch marks the price section UNKNOWN or STALE, never
   invents a close.
3. **Peers:** the peer set is caller-supplied or taken from the
   same EDGAR SIC — never invented. With no peer source, the
   peers section is UNKNOWN with the reason stated.
4. **Valuation:** DCF via `toolkit/valuation.py::dcf_value` with
   every input (free cash flows, discount rate, terminal growth,
   terminal-year FCF) stated in the dossier, and comps via
   `implied_price_from_multiple` with the peer multiple and its
   source stated. If no method fits the inputs, the dossier says
   "Valuation not built, because…" and stops — it never forces a
   DCF. The `valuation-sanity-check.md` template is applied and
   its verdict recorded.
5. **Risks and monitoring checklist:** risks carry observable
   triggers (not feelings); the checklist names the next filing or
   event to watch and the date it is due, or UNKNOWN if undated.
6. **Sourced-or-UNKNOWN:** the finished dossier passes the 9.2
   groundedness checker with 0 unsourced numbers, and the Reality
   Checker step (number audit, claim audit, logic audit) returns
   Pass. Any failure is a named fix list, re-run until Pass or
   the dossier is labelled In progress.
7. **Template reuse:** every dossier section maps to one of the 8
   templates (`initiation-report.md` sections 1–5,
   `forensic-checklist.md`, `valuation-sanity-check.md`,
   `data-first-checklist.md`, `earnings-review.md`,
   `ledger-control-checklist.md`, `research-to-use-case.md`,
   `structured-debate.md`); a section with no template is named
   in the PR, not silently added.

**Baseline.** Two baselines. (a) The deterministic global rules
team (`run_global_rules_team`) over the same inputs — the dossier
must carry strictly more sourced content (fundamentals, valuation,
risks, checklist) than the rules output, with no more unsourced
numbers (0). (b) The manual `company-deep-dive` runbook as
executed on the same fixture company — the pipeline must match the
runbook's section coverage and beat it on unsourced-number count
(0 vs the runbook's count on that fixture).

**Win vs "no edge".** Win = on every fixture dossier: 0 unsourced
numbers under the 9.2 checker, Reality Checker Pass, all 7 tests
above passing, and strictly more sourced sections than the
rules-mode baseline. If any fixture dossier has an unsourced
number, a Reality Checker Fail, or no more sourced content than the
baseline, the verdict is "no edge" for that fixture, and the
pipeline is not claimed to beat the baselines. A dossier about a
real company is never claimed validated until its fixture suite
passes first.

**Fixtures.** Synthetic EDGAR facts fixture (fixed CIK, fixed
filing dates, known concept values) plus the committed example
files `examples/research-demo-fundamentals.csv` and
`examples/research-demo-peers.csv`; the Bottlers Nepal worked
example in `finance-research-agents`
(`docs/worked-example/bottlers-nepal-terai-bnt-research-note.md`)
as the runbook-baseline fixture — with its stated limits kept
(verified-inputs dataset plus model skeleton, no DCF claimed).
No live EDGAR fetch in tests; no real-company dossier is a
fixture.

**Global personas (separate PR in finance-research-agents).**
Agent files and matching skill files for the five `GLOBAL_TEAM`
personas — `us_large_cap_fundamentals`, `etf_index`, `fx_macro`,
`crypto`, `earnings` — plus the shared technical/sentiment
functions, each file mirroring the NEPSE agent/skill pattern
(`agents/<slug>.md`, `skills/<slug>/SKILL.md`). The PR also adds
the dossier pipeline's runbook step to `runbooks/company-deep-dive.md`
if the runbook needs it, in its own commit. Acceptance: the 8
NEPSE skills are untouched, every new file passes that repo's
checks (`scripts/qa.py`, `ops/check_roster.py`), and the roster
count in `agents.json` matches the files on disk.

**Model card contents** (`docs/dossier-model-card.md`): fixture
inventory (synthetic EDGAR facts, demo CSVs, Bottlers Nepal
worked example); per-fixture results (unsourced-number count,
Reality Checker verdict, section coverage vs both baselines);
valuation inputs for each fixture dossier, stated in full;
verdict per fixture (win / no edge); known gaps (peer sources,
filing coverage, template gaps).

---

## PR plan (max 3 in this repo)

1. **9.1** — rebase + harden the executable team (toolkit PR).
2. **9.2** — groundedness checker + agent eval suite (toolkit PR).
3. **9.3** — dossier pipeline (toolkit PR) + global personas as
   agent/skill files (separate PR in `finance-research-agents`).

Each PR updates `docs/CAPABILITY-MATRIX.md` through
`scripts/generate_capability_matrix.py` for exactly the rows it
converts (partial → implemented only with a linked test, per the
matrix contract), and ships its model card. The foundation branch
converts no rows: the matrix regeneration in `round-9-foundation`
corrects evidence only — 73 rows, 37 implemented, 22 partial,
14 not planned, unchanged.
