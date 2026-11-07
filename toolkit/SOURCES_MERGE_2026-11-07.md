# Sources & attribution — merge of 2026-11-07

Three new modules landed in this round (`toolkit/indicators.py`,
`toolkit/timemachine.py`, `toolkit/position_plan.py`), drawing on two
outside repos. This file records, for each source, its licence, the
exact commit inspected, and what was adopted, adapted, or rejected.
Nothing here is a claim about the source repos' current state — only
about the commits named below.

## 1. FinceptTerminal — ideas only, NO code copied

- Repo: https://github.com/Fincept-Corporation/FinceptTerminal
- Commit inspected: `7716ee8` (2026-10-26)
- Licence: **GNU AGPL-3.0-or-later** (dual-licensed with a commercial
  licence; the open edition is AGPL-3.0)
- What it is: an open-source financial-intelligence terminal (C++/Qt6
  + embedded Python), ~534,600 lines C++ and ~337,300 lines Python at
  inspection, 201 connectors, no NEPSE/Nepal coverage.

### Licence rule applied (binding)

Because FinceptTerminal is AGPL-3.0, **no Fincept code, text, layouts,
command syntax, branding, or trade dress was copied into this MIT
repo**. Only ideas and standard formulas were taken, and every formula
was reimplemented from scratch in this toolkit's own zero-dependency
style. Each affected module carries an attribution comment naming the
source repo, the commit, and "ideas only, no code copied".

### Adopted / adapted

| Idea | Where it landed | How |
| --- | --- | --- |
| Technical-indicator library + canonical defaults table (RSI 14, MACD 12/26/9, Bollinger 20/2, ATR 14, Stochastic 14/3, ADX 14, Ichimoku 9/26/52, Keltner, VWAP) | `toolkit/indicators.py` | ADAPT — formulas reimplemented from the standard published definitions; every indicator documents what it cannot prove; `INDICATOR_DEFAULTS` records the defaults table |
| DCF pipeline checklist, risk sheet, screener grammar, connector-catalogue contract, TTL/cache discipline, sentiment-pipeline design, check-numbers pattern | Elsewhere / deferred | See the merge plan in `~/workspace/github-repos/ops/fincept-daytrader-merge-plan.md` (F2–F8). Not part of this round's toolkit code. |

### Rejected (with reason)

- Live brokerage / order execution (22 broker adapters, order
  matching, smart order engine) — real-money trading; this programme
  is research-only.
- Algo-trading / RL / auto-execution — signals + auto-execution are
  out of scope.
- Paid-data dependence (Fincept Data API credits, cloud endpoints,
  Pro/Enterprise, key-required connectors) — the toolkit runs on
  keyless sources + user-supplied CSVs.
- Branding, layouts, command syntax, trade dress — AGPL + trademark
  assertions; ideas only.
- Any implication that an indicator output is a prediction — every
  indicator in `toolkit/indicators.py` carries a "cannot prove" line.

## 2. day-trader-skill — arithmetic adapted, with attribution

- Repo: https://github.com/Jakeschincariol/day-trader-skill
- Commit inspected: `3d0f4ef` (2026-10-20), v2.0.0
- Licence: **MIT** — arithmetic and discipline may be adapted with
  attribution (repo + commit + "adapted from"), which is what was done.
- What it is: 11 skills, each one SKILL.md + one stdlib-only Python
  tool; 315 tests, all passing at inspection.

### Adapted

| Source skill | Where it landed | What changed in adaptation |
| --- | --- | --- |
| trader-timemachine (seeded Monte-Carlo + historical replay) | `toolkit/timemachine.py` | Daily bars instead of 5-minute bars; position-matched resampling from the asset's own past steps; stop checked before target; break-even verdict bands kept; output states "simulation, not a prediction" |
| trader-exit (exit-plan-before-entry, R arithmetic, refusals) | `toolkit/position_plan.py` (`plan_exit`) | Same R arithmetic and rounding rules (stop away from entry to the cent, target half-up); same refusals (stop >= entry, target <= entry, risk < 0.05% of price, reward:risk below minimum) |
| trader-budget (sizing as min-of-limits + one config file) | `toolkit/position_plan.py` (`size_position`, `DEFAULT_RESEARCH_CONFIG`) | Four limits (max loss, position %, max order, cash), whole shares, the deciding limit named; example config values kept as examples, labelled as such |
| trader-risk (PASS/BLOCK checklist + fingerprinting) | `toolkit/position_plan.py` (`risk_gate`) | 16 named checks adapted to research; fingerprint = sha256 of the exact plan dict + date, valid only for that date; a PASS is not a yes |

### Rejected (with reason)

- trader-broker (Alpaca MCP order placement, IBKR connector, ticket
  approve/fill/flatten) — the only execution skill; excluded in full.
- Order-shaped files (`orders.json` bracket orders, flatten/close-all,
  options/crypto/margin/short) — execution artefacts; not kept even as
  "paper".
- Day-trading-specific / US-specific material as if general (TSLA
  example as evidence, NYSE holidays, PDT rules, Yahoo as NEPSE
  coverage) — wrong market, wrong jurisdiction.
- Any implication that Time-Machine odds or an exit plan is a
  prediction — both source repos say so themselves, and this
  toolkit's walk-forward honesty rule says so too.

## Standing rules for this merge

- Research/education only: never investment advice, never a
  buy/sell/hold recommendation.
- Zero-dependency stdlib only; every new public function has tests.
- No invented data: all examples use synthetic or clearly-labelled
  example data.
- Predictions stay probabilistic and walk-forward honest: seed stated,
  in-sample/out-of-sample stated, naive baseline compared where a
  forecast is made.

## 3. Phase 1 engines 1.1–1.2 (2026-11-07)

### Engine 1.1 — point-in-time store (`toolkit/pit_store.py`)

- Original code for this toolkit; no outside source. Every record
  carries `as_of` / `published_at`; reads return only records
  published on or before the query date. Tests:
  `tests/test_pit_store.py` (17 tests, synthetic fixtures only),
  including a leak test that a naive store ignoring `published_at`
  fails.

### Engine 1.2 — NEPSE Time Machine upgrade (`toolkit/timemachine.py`)

- Named-setup detection (`detect_setups`, `detect_setup`) and the
  five setup rules: reimplemented locally from the same rules as
  `scanner/setups.py` (nepali-capital-market-research, MIT — which
  adapted day-trader-skill's scanner rule table, MIT, commit
  3d0f4ef) and `toolkit/terminal.py`. No scanner code imported and
  no scanner code copied; this module never imports the scanner repo.
- `replay_setup` / `honest_verdict` / `check_exit_liquidity` /
  `simulate_nepse`: original code for this toolkit, built on the
  existing `simulate` / `replay_history` (day-trader-skill
  trader-timemachine, MIT, commit 3d0f4ef — see section 2) and on
  `toolkit/nepal_frictions.sourced_nepal_frictions()` (user-supplied
  rate-schedule screenshot, 28 Oct 2026). Circuit ±15% is the
  user-supplied figure from that schedule, labelled wherever it
  appears; no rate was taken from memory. Tests:
  `tests/test_timemachine_nepse.py` (27 tests, synthetic fixtures
  only), including the README worked-example numbers pinned to the
  shipped synthetic `demo.csv`.

### One-command terminal (`toolkit/terminal.py`, `tests/test_terminal.py`, `demo.csv`)

- From the Round 2 CLI draft (`~/workspace/github-repos/
  round2-drafts/terminal/`, written for this programme, 2026-11-07):
  the draft was re-verified from a fresh clone of live `main`
  (its 20 tests pass, including end-to-end subprocess runs) before
  being deployed. Three minimal lint fixes were applied on deploy
  so the repo stays ruff-clean (ruff 0.17.3): `subprocess.run`
  gained an explicit `check=False` in the test helper,
  `load_bars` now opens the CSV with a context manager, and the
  CLI's broad `except Exception` carries an inline `noqa: BLE001`
  with its reason recorded in `ruff.toml`. No behaviour changed.
  Attribution inside the module header is unchanged (indicators:
  FinceptTerminal defaults table, ideas only, AGPL-3.0, commit
  7716ee8; Time Machine / risk gate: day-trader-skill, MIT, commit
  3d0f4ef; setups: reimplemented from `scanner/setups.py`, MIT).
- `toolkit/terminal.py` is intentionally NOT imported by
  `toolkit/__init__.py`: importing it there makes
  `python -m toolkit.terminal` emit a `RuntimeWarning`
  (`'toolkit.terminal' found in sys.modules...`). It is imported
  directly (`from toolkit.terminal import ...`) wherever needed.

## Phase 1 engines 1.3–1.5 (2026-11-07)

### TradingAgents — patterns adapted, NO code copied

- Repo: https://github.com/TauricResearch/TradingAgents
- Licence: **Apache-2.0**
- What was taken, as patterns only, reimplemented from scratch in
  this toolkit's own zero-dependency style: the multi-agent role
  split (analysts → bull/bear debate → trader → three-way risk
  debate → portfolio manager), the decision-memory loop (log every
  call before its outcome, settle against realised return and alpha
  vs a regional benchmark, feed a one-paragraph reflection into the
  next run), and the point-in-time discipline (decisions scored from
  the first close *after* the analysis date). No TradingAgents code,
  text, branding, or model names were copied; its five-tier Buy /
  Overweight / Hold / Underweight / Sell rating was NOT adopted —
  this programme stays research-only and never issues a rating.
- Where it landed: `toolkit/rules_mode.py` (Engine 1.3, the
  deterministic no-LLM rules mode backing the nine NEPSE agents),
  `toolkit/decision_log.py` (Engine 1.4, decision log + settlement
  + scoreboard + reflections), and the Portfolio Manager agent's
  reflections section in finance-research-agents.
- Rejected from the same source: the paper's backtest return/Sharpe
  figures (not this programme's results, never quoted as such),
  any live-data vendor chain (NEPSE stays CSV-only), and any
  execution step (no orders anywhere in this programme).

## 3. Phase 1 engines 1.6–1.7 — quant library + predictive layer (2026-11-07)

- Modules: `toolkit/quant.py` (new), `toolkit/predictive.py` (extended),
  `MODELS.md` (generated by `scripts/generate_models_table.py`).
  Tests: `tests/test_quant.py`, `tests/test_predictive_phase1_17.py`.
  Every numeric fixture was hand-computed before the tests were written;
  the derivation is in the test comments. No outside code was copied
  anywhere in this round — all formulas are textbook reimplementations.
- The expanded Phase 0a teardown report (skfolio, FinQuant,
  microsoft/qlib, QuantInvestStrats and others) arrived DURING this
  round, so its ideas were folded in where they fit rather than waited
  for. Full licence log: `~/workspace/github-repos/ops/LICENSES.md`;
  teardowns: `~/workspace/github-repos/ops/teardown/`.

### Folded in (adapted as textbook reimplementations, with attribution)

| Source (licence, HEAD) | Idea | Where it landed |
| --- | --- | --- |
| skfolio (BSD-3-Clause, `90e3fb9`, v1.12.7) | Risk-measure library: EVaR; max-diversification formulation | `quant.entropic_var`, `quant.max_diversification_weights` |
| skfolio (BSD-3-Clause) | Shrinkage covariance | Already present (`portfolio.shrunk_covariance`); noted, not duplicated. The sklearn-estimator interface (fit/predict) was deliberately NOT copied — this toolkit's functions are stateless by design. BSD-3 non-endorsement: nothing here implies skfolio endorses this programme. |
| ArturSepp/QuantInvestStrats (MIT, `45de566`) | Perf-stats: Sharpe inference (Lo 2002 SE/CI), bootstrap significance, multiple-testing correction, tail risk, turnover, explicit transaction costs | `quant.sharpe_standard_error`, `quant.sharpe_confidence_interval`, `quant.bonferroni_adjust`, `quant.benjamini_hochberg`, `quant.turnover`, `quant.net_return_after_costs`. Bootstrap significance NOT implemented (deferred — the Lo asymptotic SE is the implemented inference); tail risk is covered by VaR/CVaR/EVaR in the same module. |
| microsoft/qlib (MIT, `5435523`) | Expression-engine feature layer; Alpha158 factor set as citable canonical baseline; workflow/recorder experiment tracking | `quant.alpha158_baseline_factors` (six price-based members, reimplemented from the published definitions, walk-forward safe); `predictive.record_experiment` (plain JSONL append). qlib's RL trading agents REJECTED (programme rule); its mlflow recorder REJECTED (fragility). |
| fmilthaler/FinQuant (MIT, `55112a9`, unmaintained, `build_portfolio` broken) | Portfolio container-object shape; efficient-frontier + Monte Carlo optimisers as textbook maths baselines | No new code: the container shape is already `toolkit/portfolio.py`, and the frontier/MC optimisers already exist there and in `toolkit/montecarlo.py`. Do not depend on FinQuant itself. |
| santoshlite/EigenLedger (Apache-2.0, `0f965f9`) | Concept only: one friendly `portfolio_analysis()` entry point | `quant.portfolio_analysis` (thin wrapper over this module's own functions). Its yfinance fetch path is broken — not copied. |
| OpenBB (Apache-2.0, NOT AGPL — corrected in LICENSES.md) | Provider contract pattern | No code taken; recorded here so the correction is on file. |
| maybe-finance/maybe (AGPL-3.0) | Balance-sheet / budget model shape | Ideas only, nothing taken into this repo. |

### Rejected / deferred from the teardowns

- skfolio risk-budgeting and distributionally-robust optimisers:
  deferred — HRP + max-diversification cover the hierarchical /
  diversification formulations for now; risk-budgeting needs a
  convex solver the zero-dependency toolkit does not have.
- QuantInvestStrats bootstrap significance: deferred (see above).
- qlib RL agents, mlflow recorder: REJECTED (see above).
- Any live-trading / execution path from any source: REJECTED by
  programme rule.
