# LICENSES.md — reuse & licence log for investment-analyst-toolkit

This repo is MIT (see `LICENSE`). Every outside source whose ideas,
formulas, or arithmetic were used in this repo is recorded here with
its licence, the exact commit inspected, and what was taken. The
binding rule, from the programme mandate: **copyleft (AGPL/GPL)
sources contribute ideas/formulas ONLY**, reimplemented from scratch
with attribution — never code, text, layouts, command syntax,
branding, or trade dress. Permissive sources (MIT / Apache-2.0 /
BSD) may be adapted only with attribution; this repo prefers
reimplementation. Full inspection record (Phase 0 / Phase 0a):
`toolkit/SOURCES_MERGE_2026-11-07.md`.

## Outside code / idea sources

| Source | Licence | Commit inspected | What was taken |
|---|---|---|---|
| FinceptTerminal (Fincept-Corporation/FinceptTerminal) | GNU AGPL-3.0-or-later | `7716ee8` (2026-10-26) | Ideas only, no code copied: technical-indicator library + canonical defaults table (RSI 14, MACD 12/26/9, Bollinger 20/2, ATR 14, Stochastic 14/3, ADX 14, Ichimoku 9/26/52, Keltner, VWAP), reimplemented from the standard published definitions in `toolkit/indicators.py`. Live brokerage / order execution, algo/RL auto-execution, paid-data dependence, branding, layouts, command syntax, trade dress: REJECTED. |
| day-trader-skill (Jakeschincariol/day-trader-skill) | MIT | `3d0f4ef` (2026-10-20), v2.0.0 | Arithmetic adapted with attribution: trader-timemachine (seeded Monte-Carlo + historical replay) → `toolkit/timemachine.py`; trader-exit (R arithmetic, refusals) → `toolkit/position_plan.py` (`plan_exit`); trader-budget (sizing as min-of-limits) → `toolkit/position_plan.py` (`size_position`); trader-risk (PASS/BLOCK checklist) → `toolkit/position_plan.py` (`risk_gate`). trader-broker (order placement): REJECTED (execution). |
| TradingAgents (TauricResearch/TradingAgents) | Apache-2.0 | `9fa1196` (2026-11-04), v0.7.1 | Patterns only, reimplemented from scratch: multi-agent role split, decision-memory loop, point-in-time discipline → `toolkit/rules_mode.py`, `toolkit/decision_log.py`. No code, text, branding, or model names copied. Paper backtest figures NOT quoted. |
| skfolio (skfolio/skfolio) | BSD-3-Clause | `90e3fb9` (2026-11-06), v1.12.7 | Textbook reimplementations: EVaR, max-diversification formulation → `toolkit/quant.py`. BSD-3 non-endorsement: nothing here implies skfolio endorses this programme. |
| QuantInvestStrats (ArturSepp/QuantInvestStrats) | MIT | `45de566` (2026-11-06) | Perf-stats reimplemented: Sharpe inference (Lo 2002 SE/CI), multiple-testing correction, tail risk, turnover, transaction costs → `toolkit/quant.py`. Bootstrap significance NOT implemented (deferred). |
| qlib (microsoft/qlib) | MIT | `5435523` (2026-10-08) | Reimplemented from published definitions: Alpha158 factor set (six price-based members) → `toolkit/quant.py`; experiment tracking as plain JSONL append → `toolkit/predictive.py`. RL trading agents and mlflow recorder REJECTED. |
| FinQuant (fmilthaler/FinQuant) | MIT | `55112a9` (2023-09-03), v0.7.0 | No new code: portfolio container shape and frontier/MC optimisers already existed in `toolkit/portfolio.py` / `toolkit/montecarlo.py`. Do not depend on FinQuant itself (unmaintained, `build_portfolio` broken). |
| EigenLedger (santoshlite/EigenLedger) | Apache-2.0 | `0f965f9` (2025-09-13), v2.1.6 | Concept only: one friendly `portfolio_analysis()` entry point → `toolkit/quant.py`. Its yfinance fetch path is broken — not copied. (README badge says MIT; the LICENSE file governs: Apache-2.0.) |
| OpenBB (openbq-org/OpenBB) | Apache-2.0 | `8c69d8d` (2026-11-07) | Design only: provider contract pattern → `toolkit/connectors.py`. No code taken. NOT AGPL (root LICENSE verified first-hand). |
| maybe-finance/maybe (maybe-finance/maybe) | GNU AGPL-3.0 | `77b5469` (2025-07-24), v0.6.0 | Ideas only, nothing taken into this repo: balance-sheet / budget model shape. "Maybe" name/logo are trademarks — not used. |
| qf-lib (quarkfin/qf-lib) | Apache-2.0 | (HEAD not recorded in SOURCES_MERGE_2026-11-07.md) | Design only: data-provider abstraction with prefetching/caching wrapper, CSV as a first-class source → `toolkit/connectors.py` (`DiskCache` + CSV-first chain). No provider implementation used. |
| augur (BruceLanLan/augur) | MIT | (HEAD not recorded in SOURCES_MERGE_2026-11-07.md) | Conventions only: evidence time-semantics, missing-data abstention rule → connector provenance and health report. No code copied. |

## Data sources (licence / terms — see DATA_SOURCES.md for the full record)

| Source | Licence / terms |
|---|---|
| Yahoo Finance chart API | Public endpoint, no key. No licence published on the endpoint; use under Yahoo's terms. Redistribution TO VERIFY. |
| Stooq daily CSV | REMOVED 8 Nov 2026 (third failed verification; see docs/global-markets-audit.md). No Stooq code or data remains in the repo. |
| CoinGecko keyless public API | Documented keyless API; shared IP pool, roughly 10–30 calls/min. Respect the limit; never work around a rate limit. |
| ECB euro reference rates | Published by the ECB as official reference rates; attribute the ECB. A fixing is not a tradable quote and not a series. |
| World Bank API v2 | World Bank Open Data terms apply. Exact redistribution licence TO VERIFY before republishing. |
| SEC EDGAR | Public APIs, declared User-Agent required, no key. |
| FRED | Each provider's own free-tier terms, under the user's own key only. |
| Alpha Vantage | Each provider's own free-tier terms, under the user's own key only. |
| Twelve Data | Each provider's own free-tier terms, under the user's own key only. |
| FMP | Each provider's own free-tier terms, under the user's own key only. |

## Corrections on file

- OpenBB is Apache-2.0, NOT AGPL — root LICENSE verified first-hand.
- EigenLedger's README badge says MIT; its LICENSE file is Apache-2.0 — the file governs.
- FinceptTerminal's LICENSE file carries a stale dual-licensing notice superseded by its commercial-licence doc; the open edition is AGPL-3.0.
