> **SUPERSEDED (Round 3) — do not use the numbers below as current.**
> The canonical programme map is
> [`finance-research-agents/ops/SYSTEM-MAP.md`](https://github.com/ShirjanKhadka1/finance-research-agents/blob/main/ops/SYSTEM-MAP.md),
> and current counts live in each repo's generated `status.json`
> (produced by `scripts/generate_status.py`, checked by `scripts/check_numbers.py`).
> This file is kept in place, unchanged except for this banner and for
> count claims replaced by pointers below, so its history is preserved.
> Any number still visible below is historical, not current.

# Integration — investment-analyst-toolkit

## Role in the system
The calculating layer. Agents think; this toolkit computes — and proves it
with tests, so a number is not just an agent's claim.

## How to use
```python
from toolkit import ratios, valuation
ratios.profitability(revenue=1000, gross_profit=400, operating_profit=150, net_income=90)
valuation.dcf_value(fcfs=[100, 110, 121], discount_rate=0.10, terminal_year_fcf=130, terminal_growth=0.03)
```
Zero dependencies, Python 3.10+. Tests: `pytest` — current generated count in `status.json` (verified by GitHub Actions CI).
Demo: `examples/demo_company.py` (demo data only, never a real company).

## Connections
- Called by: financial-analyst and valuation-analyst agents, for ratios,
  DuPont, DCF, Gordon growth, and multiples.
- Feeds: playbook templates (ratios table, valuation build) and
  nepal-market-research case studies (once inputs are verified).
- Rules shared with agents: ratios as decimals, zero denominators raise,
  discount rate must exceed growth, terminal FCF is the first year AFTER
  the forecast. If code and agent rules ever disagree, fix the code —
  the rule is the contract.
## Extending
Add a function + a test + a demo line. No new dependency without a reason
written in the PR — zero dependencies is what keeps this free for millions.
