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

## v0.3.0 — Advanced layer (most-advanced version, in progress to deploy)
- `toolkit/options.py` — Black-Scholes call/put, Greeks (delta, gamma, theta, vega, rho), implied volatility by bisection. Put-call parity tested.
- `toolkit/montecarlo.py` — seeded GBM paths (reproducible), Monte Carlo option pricing, simulated VaR.
- `toolkit/backtest.py` — moving-average crossover signals (no lookahead: position uses yesterday's signal), backtest with trade costs, buy-and-hold benchmark on the same data.
- `toolkit/cli.py` — command line: `python -m toolkit.cli option ...` and `forecast-demo`, no network, no key.
- Agent pack: `agents/debate-analyst.md` — bull vs bear debate + risk veto, toolkit numbers for every argument.
- Tests at v0.3: 23/23 passed. (Historical count for that version only; current count is in the v0.5.0 section below and is verified by CI.)

## v0.4.0 — Quality data, autonomous updates, predictive models, zero-error guards
- **No stale data, ever:** `fetch_fresh_prices()` tries sources in order (Yahoo → Stooq → Alpha Vantage / Twelve Data / FMP with your key), skips any source that fails or is older than `max_age_days`, and raises listing every attempt if all are stale — it never returns stale data labelled fresh, and never invents a price.
- **API keys, properly:** keys come from the environment ONLY (`.env.example` lists FRED, Alpha Vantage, Twelve Data, FMP). No key is bundled, hard-coded, or invented; a missing key raises an error naming the provider, env var, and where to get a free key. No-key sources (Yahoo, Stooq, World Bank, SEC EDGAR, CSV) work with no key at all.
- **Predictive, walk-forward only:** naive, moving-average, linear trend, exponential smoothing; `auto_select_model()` picks by out-of-sample MAE; inverse-MAE ensemble; anomaly flags (z-score); volatility regime (high/low).
- **Autonomous auto-updating:** `toolkit/autonomous.py` — `run_once()` fetches fresh data, re-runs prediction + performance, writes timestamped + `latest` reports and a hash-chained audit log; one symbol failing never kills the run (its old report is kept, marked STALE). `run_loop()` repeats daily by default. Research only — no trades, no spend.
- **Zero-error guards:** every series is validated at the boundary — finite numbers only (NaN/inf/None/str/bool rejected), price pairs must be ISO dates, strictly increasing, unique, close > 0. Bad input fails loudly with what to fix, never as a wrong number downstream.
- CLI: `python -m toolkit.cli data --symbol AAPL`, `predict`, `autonomous-once --symbols AAPL,MSFT`.
- Tests at v0.4: 33 tests. (Historical count for that version only; current count is in the v0.5.0 section below and is verified by CI.)

### Autonomous scheduling (no human needed after setup)
```
# Daily 09:00 — run once, write reports, never trade, never spend
0 9 * * * cd /path/to/investment-analyst-toolkit && python -m toolkit.cli autonomous-once --symbols AAPL,MSFT --output-dir autonomous_reports
```
Or in Python: `autonomous.run_loop({"symbols": [...], "interval_seconds": 86400})`.

## v0.5.0 — Proven record, failure learning, archiving (live on GitHub)
- **Proven record, not claims:** every prediction is written to `predictions.jsonl` BEFORE its outcome is known, scored on the next run (direction + abs error), and reported as direction accuracy + mean abs error over n scored predictions. Pending predictions never count as wins.
- **Recording failure and improving from it:** every failure gets a reason (direction wrong / error above threshold) and an action — the failing model is demoted in the next auto-selection by inverse-MAE re-weighting. Failures are never deleted from the record.
- **Archiving:** every run is timestamped and indexed in `archive/index.json`; `latest` is only a pointer. Pruning is opt-in and never deletes a symbol's latest run.
- **Agentic accuracy loop:** new Accuracy Auditor agent + prediction-accuracy-loop runbook in finance-research-agents (now 10 agents): audit the ledger, log failures, demote failing models, check archive completeness and data freshness before any report goes out.
- **All API calls:** Yahoo, Stooq, World Bank, SEC EDGAR (no key) + FRED, Alpha Vantage, Twelve Data, FMP (your env key only), all through the fresh-data fallback chain.
- **Honest limit, stated plainly:** no system is failure-free. This one is built so failures are measured, logged, and acted on — that is the closest to "no failure" that is honest.
- Tests: 47/47 passed (37 at v0.5 + 10 free-data tests), verified 28 Oct 2026 and by GitHub Actions CI (pytest on Python 3.10/3.11/3.12). Historical version counts above are as-at their version; the current count is this one, verified by CI.
- **Free-data layer (post-v0.5):** `toolkit/free_data.py` — unified no-key fetcher (Yahoo → Stooq for stocks; CoinGecko for crypto; ECB reference rates; World Bank macro), every series carrying source, retrieval time, last date and age, stale data never presented as fresh. No live NEPSE feed exists in this layer: no documented free public NEPSE OHLCV API was verified (see the NEPSE scanner's teardown).
