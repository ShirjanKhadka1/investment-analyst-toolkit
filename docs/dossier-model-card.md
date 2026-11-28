# Dossier model card — Round 9, PR 9.3; real filer Round 10, PR 10.2

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
| Real AAPL recording (`examples/real-aapl-2026-11-27.json`) | Apple Inc., CIK 320193, as of 2026-11-27: companyfacts subset (exactly the tags the dossier reads), full submissions, 251 Yahoo closes to 2026-11-27, SIC peer discovery and per-peer P/E multiples | The first real filer (Round 10, PR 10.2): the eval re-runs from this recording, never the network |
| SIC 3571 page recordings (`tests/fixtures/sic-3571-browse-edgar-page1.html`, `...-page2.html`) | The browse-edgar CGI table for SIC 3571, both pages (100 + 14 rows), recorded 2026-11-28 | The peer parser's fixture: `parse_sic_browse_page` is tested against these pages, not the live CGI |

## Per-fixture results (eval 2026-11-28, `scripts/evaluate_dossier.py`)

| Fixture | Status | Reality Checker | Checker | Unsourced | Unparseable | Numbers | Sourced dossier vs rules | Sections | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| synthetic-edgar | complete | Pass | PASS | 0 | 0 | 58/58 | 23 vs 4 | 10/10 | **win** |
| demo-csvs | complete | Pass | PASS | 0 | 0 | 22/22 | 9 vs 1 | 10/10 | **win** |
| real-aapl | complete | Pass | PASS | 0 | 0 | 61/61 | 23 vs 4 | 10/10 | **win** |

## Real filer: Apple Inc. (AAPL), as of 2026-11-27

**Filer.** Apple Inc. (AAPL, Nasdaq), CIK 320193, SIC 3571
Electronic Computers. Last close 358.54 on 2026-11-27 (Yahoo,
251 bars from 2025-11-28). Next event watched: Q1 FY 2027 10-Q
filing, due 2027-02-04 (the 40-day deadline after the quarter
ending 2026-12-26 — a derived due date, not a filed fact).

**Per-concept filed values.** All eleven concepts are filed,
all from the FY 2026 10-K (period end 2026-09-26, filed
2026-11-03, accession 0000320193-26-000026). No concept is
UNKNOWN on this filer.

| Concept | Value | Tag used |
| --- | --- | --- |
| revenue | 478,834,000,000 | RevenueFromContractWithCustomerExcludingAssessedTax |
| net_income | 132,175,000,000 | NetIncomeLoss |
| gross_profit | 233,602,000,000 | GrossProfit |
| operating_income | 158,063,000,000 | OperatingIncomeLoss |
| total_assets | 404,548,000,000 | Assets |
| total_liabilities | 292,455,000,000 | Liabilities |
| equity | 112,093,000,000 | StockholdersEquity |
| cash | 36,305,000,000 | CashAndCashEquivalentsAtCarryingValue |
| operating_cash_flow | 145,097,000,000 | NetCashProvidedByUsedInOperatingActivities |
| capex | 9,041,000,000 | PaymentsToAcquirePropertyPlantAndEquipment |
| shares_outstanding | 14,532,259,000 | CommonStockSharesOutstanding |

Derived: net margin 27.60%; free cash flow 136,056,000,000
(operating cash flow minus capex); EPS 9.10; net debt
256,150,000,000; liabilities 72.29% of assets. Forensic: no
cash-vs-profit flag (operating cash flow covers net income).

**The live-fetch fix.** `fetch_dossier_inputs` was broken in two
ways and had never run. (1) It flattened
`DOSSIER_EDGAR_CONCEPTS` into one tag list and passed it to
`edgar.get_fundamentals_as_of`, which raises `ValueError` on the
first tag the filer does not carry — Apple has no
`StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`,
so the fetch crashed. It now calls `edgar.ticker_to_cik` then
`edgar.fetch_company_facts` and returns the raw companyfacts
dict as `edgar_facts`; `build_dossier` reads each concept with
its per-concept fallback, so a missing tag is UNKNOWN, never a
crash. (2) It returned `fundamentals.get("facts")`, a key
`get_fundamentals_as_of` never sets (it returns `"values"`), so
the dossier would have received `edgar_facts=None`. The wrapper
now also returns the filer's submissions (name, SIC, SIC
description, exchange), Yahoo prices, and the SIC peer
discovery result. Peer discovery is best-effort: when it fails
the wrapper still returns the base inputs and the dossier's
peers section is UNKNOWN with the failure reason.

**An XBRL quirk the real filer exposed.** Apple files operating
cash flow under `NetCashProvidedByUsedInOperatingActivities`
and carries no `NetCashProvidedByOperatingActivities` tag at
all. The concept map now carries both tags for
`operating_cash_flow` (freshest wins) — without it, free cash
flow and the DCF would have been UNKNOWN on this filer. The
same label-variant pattern already existed for revenue (three
tags) and equity (two tags).

**SIC peer lookup.** `edgar.get_sic_peers` pages the
browse-edgar CGI for SIC 3571 (the protocol's "100 rows" was
the first page only — the full SIC is 114 rows in 2 pages) and
maps CIK to ticker via the SEC bulk
`company_tickers_exchange.json`. 105 of the 114 rows have no
current ticker (defunct companies, and individuals who filed as
owners) and are not peers. 9 rows map to a ticker; excluding
Apple itself, 8 peers: DELL, OMCL, OSS, OURA, SCKT, SMCI,
XNDU, ZEPP. One CIK carries two tickers (Super Micro: SMCI +
SMCIP); the lookup picks the shortest ticker (the common stock
— preferred classes carry a suffix), so the peer is SMCI, and
the full ticker list is recorded per peer. EDGAR's full-text
search (efts.sec.gov) was not used: it has no structured SIC
filter.

**Peer multiples (comps input).** Each discovered peer's P/E is
its Yahoo last close on 2026-11-27 divided by its EPS (latest
annual net income / shares outstanding filed by 2026-11-27).

| Peer | Price | EPS | P/E | Net income (filed) |
| --- | --- | --- | --- | --- |
| DELL | 316.96 | 9.1043 | 34.81x | 5,936,000,000 (2026-03-16) |
| OMCL | 35.57 | 0.0456 | 780.51x | 2,052,000 (2026-02-26) |
| OSS | 4.48 | 0.2070 | 21.65x | 5,087,694 (2026-03-18) |
| SMCI | 23.76 | 3.3955 | 7.00x | 2,230,453,000 (2026-08-31) |

Median P/E 28.23x (median of 4 peer values). Excluded, with
reasons, never given a made-up multiple: OURA (no Yahoo price —
404, unlisted), SCKT (EPS −1.8025, no meaningful P/E), XNDU
(EPS −0.0691), ZEPP (shares outstanding is 0 in the latest
filing). OMCL's 780.51x is real (price 35.57 on EPS 0.0456)
and is kept — the median is robust to it, and dropping a valid
peer for having a high multiple would be cherry-picking.

## Valuation inputs, stated in full

**Real AAPL (as of 2026-11-27).** DCF: free cash flows
136,056,000,000 flat for 5 years (the filed FY 2026 free cash
flow, no growth assumed in the forecast period — an assumption,
not a filing); discount rate 9.00%; terminal growth 2.50%;
terminal-year free cash flow 139,457,400,000 (filed FCF x 1.025).
Enterprise value 1,923,637,181,695.24; per-share value 114.74
(equity value 1,667,487,181,695.24 less net debt 256,150,000,000,
divided by 14,532,259,000 shares). Downside case (terminal
growth 0.00%): enterprise value 1,511,733,333,333.33. Comps: P/E
implied price 256.89 (EPS 9.10 x peer-median multiple 28.23x,
median of 4 peer pe_ratio values). Last close 358.54 — above
both the DCF per-share value and the comps implied price; the
dossier reports that, it does not recommend anything.

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
  inputs): dossier sourced counts 23 vs 4 (EDGAR), 9 vs 1
  (demo CSVs) and 23 vs 4 (real AAPL) — strictly more sourced
  content on all three runs, with 0 unsourced numbers either way.
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

All three runs: **win** under the pre-registered rule (0 unsourced
numbers, Reality Checker Pass, all protocol tests passing,
strictly more sourced content than the rules-mode baseline). The
real-filer win is for AAPL as of 2026-11-27 only (Round 10
protocol §10.2): a single real filer is a first proof, not a
general claim. No fixture is "no edge".

## Known gaps

- **One real filer only.** The win is on two synthetic fixtures
  plus one real filer (AAPL as of 2026-11-27). No other real
  filer's dossier has been built or validated; a filer with
  restatements, non-USD units, a missing concept the map does
  not cover, or a SIC whose peers are mostly unlisted has not
  been tried.
- **Peer sources.** The peer set is discovered from EDGAR's SIC
  search, but the comps multiple still needs a per-peer price
  and filing: 4 of the 8 discovered peers produced a P/E and 4
  were excluded with reasons. A SIC whose ticker-mapped peers
  are all loss-making or unpriced yields no comps at all, and
  the dossier then says so.
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
- **DCF assumptions are assumptions.** The real AAPL DCF grows
  nothing: the forecast is the filed FY2026 free cash flow held
  flat for 5 years at a stated 9.00% discount rate and 2.50%
  terminal growth. Those rates are caller-supplied, not filings;
  a different discount rate or growth assumption moves the
  per-share value materially, and the dossier states the inputs
  so the reader can re-run it.
- **The live-fetch wrapper does not compute peer multiples.**
  `fetch_dossier_inputs` returns the discovered peers (CIK /
  name / ticker) but no multiples; the per-peer price + filing
  fetch lives in `scripts/fetch_real_aapl_dossier.py`. A caller
  using the wrapper directly gets an UNKNOWN peers section until
  that step runs.
