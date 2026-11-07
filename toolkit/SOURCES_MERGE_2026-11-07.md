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
