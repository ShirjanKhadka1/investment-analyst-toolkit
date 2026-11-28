# Round 10 evaluation protocol — PRE-REGISTERED

Written 2026-11-28, **before any Round 10 implementation exists**.
This protocol fixes what will be tested, the baselines, what counts as
a win versus "no edge", the fixtures, and what each model card will
contain — in advance, for 10.1, 10.2 and 10.3. No parameter, threshold
or verdict below is tuned after seeing results; if the results are bad,
the bad result is published, not re-tuned away. Model rules
(MASTER-ROADMAP.md): a model must beat its baseline out of sample or
be labelled "no edge". **LLM output never counts as a validated
prediction.** A memo, a debate verdict or a dossier is text until its
calls settle against a baseline — and no headline is published before
100 settled calls in the series being scored.

Foundation branch: `round-10-foundation`, based on main
`add11dc9187cb544d785e3b1113b186f92c075b3` (2026-11-28, PR #67
merge). This protocol and the counts-docs fix and the regenerated
capability matrix are the foundation; no 10.2/10.3 implementation
is in this branch.

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
  (`toolkit/llm_providers.py`, `FakeProvider`) or on fixture files,
  or on a recorded live response committed as a fixture. A live
  fetch is run by a script (`scripts/`) and its output is committed
  as a fixture before any test reads it — tests never call the
  network.
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
  Round 10 PR unless the user writes "merge #N" naming it.
- Maximum 3 PRs per round in this repo.
- No release, no tag, no workflow deployment. Any workflow YAML is
  staged under `ops/ready-for-browser/` with a CI test running its
  exact commands locally, including a SECOND-RUN and multi-run test.
  The user deploys it.
- The settled-calls discrepancy is carried over, unresolved: the
  live track record shows 0 settled calls; programme memory claims
  5/7. The 5/7 figure is never cited anywhere in Round 10 work.
  Investigating it is out of scope for this round unless it blocks
  a 10.2/10.3 deliverable, in which case it is reported, not fixed
  silently.

## Shared ground (read before building)

- **Dossier pipeline (on main, 9.3):** `toolkit/dossier.py`
  (`build_dossier`, `render_dossier_memo`, `reality_check`,
  `run_dossier`, demo-CSV loaders, EDGAR concept map — 11 concepts,
  template map 15 sections → 8 templates). The live-fetch wrapper
  `fetch_dossier_inputs` is **broken on main and has never run**:
  it flattens `DOSSIER_EDGAR_CONCEPTS` into a single tag list and
  passes it to `edgar.get_fundamentals_as_of`, which raises
  `ValueError` on the first tag the filer does not carry (Apple has
  no `StockholdersEquityIncludingPortionAttributableToNoncontrolling
  Interest`, so the fetch crashes), and even if it did not crash it
  returns `fundamentals.get("facts")`, a key `get_fundamentals_as_of`
  never sets (it returns `"values"`), so the dossier would receive
  `edgar_facts=None` and no fundamentals at all. `build_dossier`
  itself is sound: `_edgar_values` uses
  `edgar.freshest_annual_value_as_of` per concept and marks a
  concept with no filing by `as_of` as UNKNOWN, never crashing.
  Fixing `fetch_dossier_inputs` is 10.2 work, not a protocol
  amendment.
- **EDGAR (on main):** `toolkit/edgar.py` — `ticker_to_cik`,
  `fetch_submissions` (returns the filer's SIC and SIC description),
  `fetch_company_facts`, `fetch_company_concept`,
  `annual_value_as_of` / `freshest_annual_value_as_of`
  (point-in-time, prefer-10-K), `get_fundamentals_as_of`
  (raises on a missing concept — callers needing a per-concept
  fallback catch it per concept). Verified live 2026-11-28 from
  this VM: AAPL submissions return SIC 3571, "Electronic
  Computers". EDGAR throttles and requires a declared User-Agent
  (`declared_user_agent`).
- **SIC peer discovery (verified live 2026-11-28, not yet in the
  toolkit):** EDGAR's full-text search API (`efts.sec.gov`) is a
  text search over filing content — it has no structured SIC
  filter and is not a peer lookup. The working route is the
  browse-edgar CGI (`https://www.sec.gov/cgi-bin/browse-edgar?
  action=getcompany&SIC=<sic>&type=10-K&count=100`), which
  returns an HTML table with one CIK link per company in the SIC
  (100 rows for SIC 3571, including Apple CIK 0000320193). The
  CIK → ticker map comes from the SEC's bulk
  `company_tickers_exchange.json`. Both are public SEC endpoints;
  both worked from this VM on 2026-11-28.
- **Prices (on main):** `toolkit/connectors.py`
  (`fetch_prices_with_volumes`, Yahoo first, Stooq opt-in, ECB,
  World Bank). Verified live 2026-11-28: AAPL Yahoo, 251 pairs,
  last close 2026-11-27 $358.54, provenance recorded, not stale.
- **Groundedness checker (on main, 9.2):**
  `toolkit/groundedness.py` (`check_memo`) — every number must
  trace to a `tool_result` / `evidence_ref` value or the memo is
  blocked; unparseable numbers fail closed. The dossier's Reality
  Checker is the gate.
- **Global rules team (on main, 9.1/9.3):**
  `toolkit/rules_mode.py` — `GLOBAL_TEAM` has five personas:
  `us_large_cap_fundamentals`, `etf_index`, `fx_macro`, `crypto`,
  `earnings`; `run_global_rules_team(symbol, market, inputs)`
  runs them over supplied inputs (`closes`,
  `fundamentals_metrics`, `etf_metrics`, `fx_inputs`,
  `crypto_metrics`, `earnings_events`); any input a rule cannot
  evaluate returns `UNKNOWN` with a reason, never a filled-in
  value. The five personas also exist as agent + skill files in
  `finance-research-agents` (PR #6). The cross-check tests there
  only verify the roster matches the toolkit's dict and skip when
  no toolkit checkout is present — they do not run the team.
- **Real filer for this round (fixed here, before any run):**
  Apple Inc. (AAPL), SIC 3571 Electronic Computers, as_of
  2026-11-27 (the last trading day before this protocol date;
  Yahoo last close 2026-11-27 $358.54). AAPL is in the programme
  watchlist, is a large-cap with clean, well-tagged EDGAR data,
  and has no known restatement in its latest 10-K. If the live
  fetch for AAPL fails for a reason that cannot be fixed inside
  this round, the protocol is amended (own commit, naming the
  replacement filer and the reason) — the filer is never swapped
  silently.
- **Counts gate (failing on main at pre-registration):**
  `scripts/qa_counts.py` fails with 4 errors: README.md and
  `docs/positioning-draft.md` claim 36 agents (18 NEPSE / 18
  agnostic) but the live recount is 41 agents (18 NEPSE / 23
  agnostic), 12 runbooks, 8 templates, 42 `agents.json` entries.
  Cause: the personas PR #6 merged into finance-research-agents
  (36 → 41 agents) after the toolkit's docs were written. The
  fix is a docs-only change in 10.1; it is not part of 10.2 or
  10.3.
- **Live state at pre-registration (checked 2026-11-28):**
  main `add11dc9`, full fast suite 1266 passed / 1 skipped /
  4 deselected (1270 collected), coverage 91%. Track record:
  0 settled (see the discrepancy note above). Volatility ledger:
  live, 9 forecast rows (first run 2026-11-28).

---

## 10.1 — Foundation: protocol + counts-docs fix

**Scope.** Pre-register this protocol; fix the stale agent-count
claims so the `qa.py` counts gate passes on main again; regenerate
the capability matrix from the real code. No implementation beyond
the docs fix. No matrix row converts (the fix corrects evidence
only).

**What will be tested.**

1. **Protocol pre-registered:** this file exists on the foundation
   branch before any 10.2/10.3 implementation commit.
2. **Counts gate green:** `scripts/qa_counts.py` exits 0 on the
   foundation branch — README.md states "41 agents — 18
   NEPSE-side ... 23 market-agnostic" with 12 runbooks and 8
   templates, `docs/positioning-draft.md` states the same split
   ("41 agents, 12 runbooks", "18 NEPSE / 23 market-agnostic"),
   the removed claims ("36 analyst agents", "NEPSE-first") stay
   absent from README.md and `docs/index.html`, and the vendored
   snapshot `docs/agents-roster-counts.json` matches the live
   recount (as_of 2026-11-28, 41 / 18 / 23, 12 runbooks,
   8 templates, 42 `agents.json` entries).
3. **Matrix fresh:** `scripts/generate_capability_matrix.py
   --check` passes; the matrix is regenerated from the code, not
   hand-edited.

**Baseline.** Main `add11dc9`, where the counts gate fails with
exactly the 4 errors listed above. The foundation must turn those
4 errors to 0 without changing any other gate's outcome.

**Win vs "no edge".** Win = all three tests above pass. Any
failure is a failed item, not a partial win.

**Fixtures.** None — this item is a docs fix plus a protocol
file. The counts are produced by `scripts/count_agents.py`
(live roster fetch), never typed.

---

## 10.2 — Real-company dossier + SIC peer lookup

**Scope.** Fix the live-fetch wrapper, wire EDGAR SIC peer
lookup, and build + validate a REAL Apple dossier through the
groundedness checker. No new core dependencies; no network in
tests (the live fetch runs in a script and its output is
committed as a fixture before any test reads it).

**What will be tested.**

1. **Live-fetch wrapper fixed:** `fetch_dossier_inputs` returns
   a usable inputs dict for a real filer: the raw companyfacts
   dict as `edgar_facts` (not a `get_fundamentals_as_of`
   result), the filer's submissions (SIC + SIC description), and
   Yahoo prices. It does not crash when the filer lacks a
   candidate tag (Apple has no noncontrolling-interest equity
   tag) and it never returns `edgar_facts=None` for a filer
   whose facts were fetched. A concept with no filing by `as_of`
   is UNKNOWN in the dossier, never filled from a later filing.
2. **SIC peer lookup wired:** `edgar.get_sic_peers` (new) takes
   a SIC and returns real peers — companies in that SIC from
   EDGAR (browse-edgar CGI), each with CIK, name and ticker
   (via `company_tickers_exchange.json`), excluding the filer
   itself. The peer list is discovered, never caller-supplied
   and never invented. With no peer source (network down, SIC
   page unparseable) the peers section is UNKNOWN with the
   reason stated.
3. **Real Apple dossier:** `run_dossier` over the live-fetched
   AAPL inputs (as_of 2026-11-27) builds a dossier and renders a
   memo that passes the 9.2 checker with **0 unsourced numbers**,
   Reality Checker **Pass**, and **sections 10/10**. The live
   response is committed as a fixture
   (`examples/real-aapl-2026-11-27.json` or equivalent) and the
   eval re-runs from that fixture, so the result is
   reproducible offline.
4. **Valuation from real inputs:** DCF via
   `toolkit/valuation.py::dcf_value` and comps via
   `implied_price_from_multiple` use the real filed free cash
   flows and the discovered peers' multiples, with every input
   stated in the dossier. If no method fits the real inputs,
   the dossier says "Valuation not built, because…" and stops —
   it never forces a DCF and never substitutes a fixture value.
5. **No look-ahead:** no filed value dated after 2026-11-27 and
   no price dated after 2026-11-27 appears in the dossier; the
   point-in-time rule is checked by a test that moves `as_of`
   earlier and confirms the dossier changes (a later filing
   disappears).
6. **Eval extended:** `scripts/evaluate_dossier.py` gains a
   real-filer run (live fetch or the committed fixture) reported
   alongside the two fixture wins (synthetic-edgar 58/58,
   demo-csvs 22/22). The real-filer run's numbers are printed in
   full — unsourced count, Reality Checker verdict, section
   coverage — no summary-only reporting.

**Baseline.** Two baselines, both pre-registered. (a) The 9.3
fixture dossiers (synthetic-edgar, demo-csvs) — the real
dossier must meet the same bar (0 unsourced, Pass, 10/10).
(b) The deterministic global rules team
(`run_global_rules_team`) over the same AAPL inputs — the real
dossier must carry strictly more sourced content
(fundamentals, valuation, risks, checklist) than the rules
output, with no more unsourced numbers (0).

**Win vs "no edge".** Win = all 6 tests above pass on the real
Apple dossier. If the real dossier has any unsourced number, a
Reality Checker Fail, a section below 10/10, or no more sourced
content than the rules baseline, the verdict is "no edge" for
the real filer, and the failure is published with the exact
reason (which concept, which number, which section) — the
pipeline is not claimed to handle real filers on its strength.
A single real filer is a first proof, not a general claim: the
model card states that the win is for AAPL as of 2026-11-27
only.

**Fixtures.** The committed live response for AAPL as of
2026-11-27 (companyfacts subset, submissions, Yahoo closes)
plus the existing synthetic-edgar and demo-csv fixtures. The
live response is a recording, not a hand-written fixture: it is
fetched once by a script and committed byte-identical; tests
read the recording, never the network. The SIC peer-list
fixture is a recording of the browse-edgar CGI table for SIC
3571 (or a parsed subset), committed the same way.

**Model card contents** (update `docs/dossier-model-card.md`):
the real-filer section — filer, as_of, SIC, peer count and
peer source, per-concept filed values (with form/filed date),
the live-fetch fix (what was broken, what changed), the eval
results (real run in full, next to the two fixture wins),
valuation inputs in full, verdict (win / no edge), known gaps
(filers the pipeline has not been tried on, XBRL quirks not
yet handled).

---

## 10.3 — Personas as a running team

**Scope.** Run the five global personas against the SAME real
company (AAPL, as_of 2026-11-27), score their outputs, and
measure cross-persona agreement. The personas are deterministic
rules functions (`toolkit/rules_mode.py`, `GLOBAL_TEAM`) — no
LLM is involved, so no LLM output is ever presented as a
validated prediction. No new core dependencies; no network in
tests (the team run reads the committed AAPL fixture from
10.2).

**What will be tested.**

1. **Team run on real inputs:** `run_global_rules_team`
   ("AAPL", "us", inputs built from the committed AAPL fixture:
   Yahoo closes, EDGAR fundamentals metrics, earnings events
   from the filer's recent filings, plus market-context inputs
   for the etf/fx/crypto personas) returns all five persona
   sections plus technical and sentiment, with no section
   crashing. A section a persona cannot evaluate is UNKNOWN
   with a reason, never a filled-in value.
2. **Per-persona scoring:** each persona section is scored on
   a fixed rubric, stated here before the run: **sourced** (the
   section carries at least one concrete value traced to an
   input row or tool output) vs **UNKNOWN** (no evaluable
   input). The score is reported per persona (5/5, 4/5, …) in
   full — no summary-only reporting.
3. **Cross-persona agreement, measured:** each persona section
   is reduced to a stance by a fixed rule, stated here before
   the run (no rule is chosen after seeing outputs):
   - `us_large_cap_fundamentals`: stance from its health
     assessment — "constructive" when the assessment reports
     the filer as healthy on the majority of its checks,
     "cautious" when it reports the majority as weak,
     "neutral" on a tie, UNKNOWN when the section is UNKNOWN.
   - `earnings`: stance from the most recent earnings event —
     "constructive" on a beat (reported above expected on both
     EPS and revenue, or on the metric the event carries),
     "cautious" on a miss, "neutral" on a mixed/no-surprise
     event, UNKNOWN when no earnings event is evaluable.
   - `etf_index`: stance from the market-index read — the
     index (SPY, the market context for a US large-cap) trend
     is "constructive" when rising, "cautious" when falling,
     "neutral" when flat, UNKNOWN when no index input.
   - `fx_macro`: stance from the dollar/macro read —
     "constructive" for AAPL when the dollar is weakening
     (a weaker dollar lifts overseas earnings in USD),
     "cautious" when strengthening, "neutral" when flat,
     UNKNOWN when no FX input. The direction rule is fixed
     here, not chosen after the fact.
   - `crypto`: stance from the crypto read — "constructive"
     when the crypto market is in a risk-on read, "cautious"
     when risk-off, "neutral" when flat, UNKNOWN when no
     crypto input. Crypto is market context, not an AAPL
     input; the stance is about the risk regime, and the model
     card says so.
   - Agreement = the fraction of persona pairs (among personas
     with a non-UNKNOWN stance) whose stances match, reported
     as a rate plus the per-persona stances. The technical
     section's trend is reported alongside but is not a persona
     stance and does not enter the agreement rate.
4. **Reproducibility:** the same committed fixture re-run
   produces byte-identical stances and agreement rate (fixed
   run id, fixed clock). A test asserts this.
5. **Eval script:** `scripts/evaluate_personas_team.py` (new)
   runs the team on the committed AAPL fixture, prints the
   per-persona scores, the stances, and the agreement rate in
   full, and exits non-zero if any persona section crashes or
   the agreement rate cannot be computed (fewer than 2
   non-UNKNOWN stances).

**Baseline.** The cross-check tests in finance-research-agents
(roster match only, skip without a toolkit checkout) and the
9.1 `scripts/evaluate_llm_team.py` fixture runs. The baseline
for agreement is chance: with 3 stances (constructive /
cautious / neutral), random agreement between two personas is
1/3. The measured rate is reported against 1/3 — it is a
measurement, not a pass/fail threshold. The win condition is
that the team RUNS and agreement is MEASURED reproducibly, not
that the personas agree: forcing agreement would be gaming,
and disagreement between a fundamentals read and a risk-regime
read is itself informative.

**Win vs "no edge".** Win = all 5 tests above pass: the team
runs on the real AAPL fixture with no crashes, every persona
section is scored, the agreement rate is computed and
reproducible, and the eval script exits 0. If any persona
crashes, or fewer than 2 personas produce a stance (so no
agreement rate exists), the verdict is "no edge" and the
failure is published with the exact reason.

**Fixtures.** The committed AAPL fixture from 10.2 (closes,
fundamentals metrics, earnings events, market-context inputs)
plus a small synthetic team-run fixture for the stance rules
(each stance value exercised at least once, so the agreement
computation is tested independently of the real run).

**Model card contents** (new `docs/personas-team-model-card.md`):
the team-run inputs (filer, as_of, fixture); per-persona
scores and stances in full; the agreement rate with the
pairwise table; the chance baseline (1/3); verdict (win /
no edge); known gaps (the stance rules are a first, coarse
reduction — a persona's full section carries more than its
stance; the context personas assess the regime, not the
company; no live run is claimed until the eval is re-run on a
fresh live fetch).

---

## PR plan (max 3 in this repo)

1. **10.1** — Foundation: this protocol + counts-docs fix +
   regenerated capability matrix.
2. **10.2** — Real-company dossier + SIC peer lookup
   (live-fetch fix, `edgar.get_sic_peers`, real AAPL dossier,
   eval extension, dossier model-card update).
3. **10.3** — Personas as a running team (team run on the
   AAPL fixture, per-persona scoring, agreement measurement,
   eval script, personas-team model card).

Each PR updates `docs/CAPABILITY-MATRIX.md` through
`scripts/generate_capability_matrix.py` for exactly the rows it
converts (partial → implemented only with a linked test, per the
matrix contract), and ships its model card. The foundation
branch converts no rows: the matrix regeneration corrects
evidence only.
