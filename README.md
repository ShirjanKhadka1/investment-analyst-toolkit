# Investment Analyst Toolkit

A small, tested Python toolkit for the calculations an investment / research
analyst actually does every day — financial ratios, DuPont decomposition,
valuation multiples, and a transparent DCF helper that forces you to show every
input. No black boxes, no hidden assumptions.

> **Educational use.** This is a learning and analysis toolkit, not investment
> advice, and not a recommendation to buy or sell any security. Every example
> uses clearly-labelled demo data unless a source is cited.

## Why this exists

Most finance tutorials give you a formula and a single answer. Analyst work is
the opposite: the answer is only as good as the inputs, the definitions, and
the checks around them. This toolkit is built around that discipline:

- Every function takes explicit inputs — no global state, no magic defaults.
- Every result can be recomputed from the inputs shown next to it.
- Edge cases are handled loudly: division by zero, negative equity, and
  missing inputs raise errors instead of silently returning a plausible number.

## Install

```bash
pip install -e .
```

Requires Python 3.10+ and nothing else — no pandas, no numpy, no network access.
That is deliberate: the core must run anywhere, including a fresh laptop.

## Quickstart (60 seconds)

```python
from toolkit import ratios, valuation

# Demo company, demo data — not a real company
r = ratios.profitability(
    revenue=1000.0, gross_profit=400.0,
    operating_profit=150.0, net_income=90.0,
)
print(r)
# {'gross_margin': 0.4, 'operating_margin': 0.15, 'net_margin': 0.09}

# A DCF that shows its work: cash flows, discount rate, terminal value
value = valuation.dcf_value(
    free_cash_flows=[100.0, 110.0, 121.0],
    discount_rate=0.10,
    terminal_growth=0.03,
    terminal_year_fcf=130.0,
)
print(round(value, 2))
```

Run the worked example:

```bash
python examples/demo_company.py
```

## What is inside

| Module | What it does |
|---|---|
| `toolkit/ratios.py` | Profitability, liquidity, leverage, efficiency ratios; DuPont ROE |
| `toolkit/valuation.py` | DCF (single-stage terminal value), EV/EBITDA and P/E multiples, Gordon growth |
| `toolkit/checks.py` | Input validation: non-negative where required, rates in range, growth < discount rate |

## Tests

```bash
python -m pytest tests/ -v
```

Every public function has at least one test, including an edge-case test
(zero revenue, negative equity, growth >= discount rate). If a test fails,
the toolkit is wrong — open an issue.

## What this is NOT

- Not a stock screener with live prices (no data vendor, no scraping).
- Not a completed valuation of any real company. Real-company case studies
  live in a separate repo, with sources cited, and are labelled work-in-progress
  until every input is verified.
- Not investment advice.

## Contributing

Good first contributions: a new ratio with a test and a docstring citing its
definition, a worked example in `examples/`, or a clearer error message.
See CONTRIBUTING.md. Security issues: do not open a public issue with a
reproduction that touches real credentials — there are none in this repo by design.

## License

MIT.

## v0.2.0 — Job 3: portfolio, performance, forecast, free API data
- `toolkit/portfolio.py` — returns, sample + shrunk covariance, equal / inverse-vol / min-variance / max-Sharpe weights, clean long-only weights, efficient frontier. Shrinkage + L2 by default; every portfolio compared to 1/N.
- `toolkit/performance.py` — Sharpe, Sortino, max drawdown, CVaR (5%), Calmar, annualised return/vol, train/test split, performance report. Metrics on train AND test, separately.
- `toolkit/forecast.py` — walk-forward only, naive benchmark every model must beat, moving-average + linear-trend models, 95% prediction intervals from training residuals, lookahead audit built into the evaluation.
- `toolkit/data.py` — free API calls, standard library only, no key in the core path: Yahoo Finance chart API, World Bank API, CSV loader (also the NEPSE annual-report adapter — fiscal years labelled by the user), bundled demo series, optional FRED with your own key passed explicitly.
- Agentic workflow: `finance-research-agents/agents/portfolio-forecast-analyst.md` + `runbooks/portfolio-forecast.md` — Data → Portfolio → Performance → Forecast → Reality Checker.
- Demo: `examples/demo_portfolio.py` (DEMO data only, no network, no key).
- No live trading, no buy/sell advice. Research and education only.
