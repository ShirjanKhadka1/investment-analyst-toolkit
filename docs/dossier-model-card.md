# Dossier model card — Round 9, PR 9.3

Company dossier pipeline: one ticker in, one dossier out (EDGAR
fundamentals, prices, peers, valuation, risks, monitoring
checklist). Every number sourced or UNKNOWN. The pipeline executes
the `company-deep-dive` runbook order (Investment Researcher ->
Forensic -> Financial -> Valuation -> Reality Checker) from
injected data. No live fetch in tests; no LLM anywhere in the
pipeline — the memo is code-generated from the evidence table.

## Fixture inventory

| Fixture | What it is | Why it exists |
| --- | --- | --- |
| Synthetic EDGAR facts (`examples/dossier/synthetic-edgar-facts.json`) | DEMO Corporation, fixed CIK, 10-K FY2025 filed 2026-02-14 + 10-Q Q1 2026 filed 2026-05-10, known concept values | Point-in-time discipline: the 10-Q must never leak into a dossier as of 2026-03-31; the stale `Revenues` 2024 tag tests freshest-tag choice |
| Demo CSVs (`examples/research-demo-fundamentals.csv`, `examples/research-demo-peers.csv`) | Synthetic NEPSE-style company, NPR millions, FY 2081 | No prices, no DCF inputs, one sourceless value (NPL), one sourceless peer row — the partial-data path |
| BNT runbook baseline (`examples/dossier/bnt-runbook-baseline.json`) | Bottlers Nepal worked example, counted by `toolkit.groundedness.extract_numbers` | The manual runbook's section coverage and unsourced-number count to beat |

## Per-fixture results (eval 2026-11-28, `scripts/evaluate_dossier.py`)

| Fixture | Status | Reality Checker | Checker | Unsourced | Unparseable | Numbers | Sourced dossier vs rules | Sections | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| synthetic-edgar | complete | Pass | PASS | 0 | 0 | 58/58 | 23 vs 4 | 10/10 | **win** |
| demo-csvs | complete | Pass | PASS | 0 | 0 | 22/22 | 9 vs 1 | 10/10 | **win** |

## Valuation inputs, stated in full

**Synthetic EDGAR (DEMO, NYSE, as of 2026-03-31).** DCF:
free cash flows 110,000,000; 115,000,000; 120,000,000; 125,000,000;
130,000,000; discount rate 9.00%; terminal growth 2.50%;
terminal-year free cash flow 133,250,000. Enterprise value
1,795,776,221.54; per-share value 29.52 (equity value
1,475,776,221.54 less net debt 320,000,000, divided by 50,000,000
shares). Downside case (terminal growth 0.00%): enterprise value
1,402,206,659.84. Comps: P/E implied price 20.86 (EPS 1.94 x
peer-median multiple 10.75x, median of 2 peer pe_ratio values).
Last close 131.50 (Yahoo chart, 2026-03-31).

**Demo CSVs (DEMO, NEPSE, as of 2026-03-31).** DCF not built: no
free-cash-flow inputs supplied. Comps: P/B implied price 336
(book value per share 240 NPR x peer-median multiple 1.40x,
median of 1 peer price_to_book value). No prices supplied, so no
last close. NPL is UNKNOWN (no source in the fixture).

## Baselines

- **Rules-mode team** (`run_global_rules_team` over the same
  inputs): dossier sourced counts 23 vs 4 (EDGAR) and 9 vs 1
  (demo CSVs) — strictly more sourced content on both fixtures,
  with 0 unsourced numbers either way.
- **BNT runbook baseline**: the worked example carries 202 numeric
  claims with no event log to trace against (170 further tokens
  unparseable under the checker grammar — the note predates the
  grammar) and names 7 of the 10 comparable sections as headings
  (Business, Industry and What-would-change-my-mind are embedded in
  its thesis and risk text, not separate sections). The dossier
  covers all 10 comparable sections and carries 0 unsourced
  numbers. The note's debate, risk veto and accuracy audit are
  separate agents' outputs, not dossier sections, and are out of
  scope here.

## Verdict per fixture

Both fixtures: **win** under the pre-registered rule (0 unsourced
numbers, Reality Checker Pass, all 7 protocol tests passing,
strictly more sourced content than the rules-mode baseline). No
fixture is "no edge".

## Known gaps

- **Fixtures only.** The win is on two synthetic fixtures. No real
  company's dossier has been built or validated; a real-company
  dossier is never claimed validated until its fixture suite passes
  first — and the fixtures do not prove the pipeline handles a real
  filer's XBRL quirks (missing tags, restatements, non-USD units).
- **Peer sources.** The peer set is caller-supplied; there is no
  EDGAR SIC peer lookup wired in yet, so a dossier with no peer
  source has an UNKNOWN peers section and no comps.
- **Value-matching, not truth.** The groundedness checker verifies
  a number appears in the run's tool outputs, not that the tool
  output was right, the label is right, or the reasoning is sound
  — a dossier can pass with every number sourced and still be
  wrong. The Reality Checker's claim and logic audits re-check
  arithmetic and cut-offs, not judgement.
- **Caller-supplied prose.** Risk text, triggers, industry and
  company name are the caller's words. A bare number in them
  blocks the dossier (fail-closed, by design) — triggers must be
  written against filed values, not numeric thresholds.
- **Earnings.** The earnings-review template maps only when
  earnings events are supplied; neither fixture supplies them, so
  no Earnings section is exercised end-to-end beyond a unit test.
- **No live run.** The live-fetch wrapper
  (`fetch_dossier_inputs`) has never run against EDGAR or Yahoo;
  no real-company dossier exists yet.
