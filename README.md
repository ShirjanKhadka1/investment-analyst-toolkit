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

Tests: 124/124 passed (28 Oct 2026, fresh venv, Python 3.12, pytest 9.2.0).
Quality gates (Phase 7): `ruff check` clean, `mypy` clean under default
settings (see mypy.ini — untyped code is unchecked by default mypy, so
this is a guardrail, not full type safety), coverage 82% total with a
`fail_under=80` gate (data.py at 41% — its network fetchers cannot run
offline — is the drag; the gate sits at the measured level and rises
only as coverage genuinely grows). CI runs pytest on Linux/macOS/Windows
× Python 3.10/3.11/3.12; the macOS/Windows combinations are CI-only
until their first GitHub run is observed.

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

## Limitations

- The scheduled daily workflow is a plan until its first run is observed
  on GitHub with public output in `reports/daily/` — it is not claimed
  to have run until that run is seen.
- There is no live NEPSE feed: NEPSE data comes only from a user-supplied
  CSV, and any Nepal cost rate is UNKNOWN unless given with a source and
  an as-of date.
- All examples use demo data unless a source is cited.

> Research only, not investment advice; never a buy/sell recommendation

See `CONTRIBUTING.md` for how to contribute and `SECURITY.md` for how to
report a security issue privately.

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
- Tests at v0.3: 23/23 passed. (Historical count for that version only; current count is in the v0.7.0 section above and is verified by CI.)

## v0.4.0 — Quality data, autonomous updates, predictive models, zero-error guards
- **No stale data, ever:** `fetch_fresh_prices()` tries sources in order (Yahoo → Stooq → Alpha Vantage / Twelve Data / FMP with your key), skips any source that fails or is older than `max_age_days`, and raises listing every attempt if all are stale — it never returns stale data labelled fresh, and never invents a price.
- **API keys, properly:** keys come from the environment ONLY (`.env.example` lists FRED, Alpha Vantage, Twelve Data, FMP). No key is bundled, hard-coded, or invented; a missing key raises an error naming the provider, env var, and where to get a free key. No-key sources (Yahoo, Stooq, World Bank, SEC EDGAR, CSV) work with no key at all.
- **Predictive, walk-forward only:** naive, moving-average, linear trend, exponential smoothing; `auto_select_model()` picks by out-of-sample MAE; inverse-MAE ensemble; anomaly flags (z-score); volatility regime (high/low).
- **Autonomous auto-updating:** `toolkit/autonomous.py` — `run_once()` fetches fresh data, re-runs prediction + performance, writes timestamped + `latest` reports and a hash-chained audit log; one symbol failing never kills the run (its old report is kept, marked STALE). `run_loop()` repeats daily by default. Research only — no trades, no spend.
- **Zero-error guards:** every series is validated at the boundary — finite numbers only (NaN/inf/None/str/bool rejected), price pairs must be ISO dates, strictly increasing, unique, close > 0. Bad input fails loudly with what to fix, never as a wrong number downstream.
- CLI: `python -m toolkit.cli data --symbol AAPL`, `predict`, `autonomous-once --symbols AAPL,MSFT`.
- Tests at v0.4: 33 tests. (Historical count for that version only; current count is in the v0.7.0 section above and is verified by CI.)

### Autonomous scheduling (no human needed after setup)
```
# Daily 09:00 — run once, write reports, never trade, never spend
0 9 * * * cd /path/to/investment-analyst-toolkit && python -m toolkit.cli autonomous-once --symbols AAPL,MSFT --output-dir autonomous_reports
```
Or in Python: `autonomous.run_loop({"symbols": [...], "interval_seconds": 86400})`.

## Phase 7 — quality / security / supply chain (local, deploy after Phase 6 verifies)
- **Lint/type/coverage, measured not claimed:** `ruff check` clean (ruff 0.17.3, with documented ignores only in scanner/agent configs — see their ruff.toml files), `mypy` clean under default settings (see mypy.ini — untyped code is unchecked by default mypy, so this is a guardrail, not full type safety), coverage **82%** total with `fail_under=80` set at the measured level.
- **Real bugs found by lint, then fixed:** keyed fetchers built URLs from the unresolved `api_key` argument, so an env-supplied key never reached the request (now resolved-key URLs, proven by tests); a test used `_dt` imported inside a different test, so its loop test passed for the wrong reason (now uses module-level `datetime`); dead variables and redundant int casts removed.
- **Property tests (6 new):** put-call parity across a full parameter grid, discounted-intrinsic bounds (a European put may trade below undiscounted intrinsic when rates are positive — the test was corrected, not the engine), DCF/Gordon monotonicity, portfolio volatility bounds.
- **CI:** OS × Python matrix (Linux/macOS/Windows × 3.10/3.11/3.12) with pinned pytest 9.2.0; quality job (ruff, mypy, coverage) and smoke job on ubuntu. macOS/Windows are CI-only until their first GitHub run is observed. CodeQL + Dependabot configs added; their GitHub-side enablement, and secret scanning / push protection, are TO VERIFY after deployment. SBOM.md: runtime dependencies none (stdlib-only); CI deps pinned (pytest 9.2.0, ruff 0.17.3, mypy 2.4.0, coverage 7.17.0).
- Tests: 124/124 (118 at Phase 5 + 6 property tests), verified 28 Oct 2026 in a fresh venv.

## v0.7.0 — Phase 4: engines + prediction accuracy
- **Engine validation:** every toolkit formula audited against a named reference (Black–Scholes–Merton 1973 + put-call parity, Gordon 1959, Markowitz portfolio variance, Sharpe 1966/1994, Sortino, historical CVaR, DuPont identity, exact GBM discretisation, walk-forward evaluation). Correct formulas left unchanged; genuine bugs proven by failing cases first, then fixed: `options.greeks` and Monte Carlo silently returned put values for an unknown kind (now raise `ValueError`), negative trade costs added money in backtests (now rejected), non-positive prices and bad alphas now rejected, flat-volatility regime no longer flips on float noise.
- **Nepal frictions (`toolkit/nepal_frictions.py`):** brokerage, SEBON fee, DP charge, capital gains tax, circuit limits, settlement lag as parameters — every rate UNKNOWN by default, because no rate is stated in the local Nepal guides; a rate is accepted only with a source + as-of date. Cost reports return UNKNOWN (never zero) when any needed rate is unknown, so a backtest can never run cost-free by accident. NEPSE stated; scanner stays CSV-only; no live feed.
- **Nepal frictions, sourced schedule (Phase 4 patch, 28 Oct 2026 — deploy after Phase 4 verifies):** `sourced_nepal_frictions()` carries the user-supplied schedule — broker slabs 0.36%/0.33%/0.31%/0.27%/0.24% (min Rs 10), SEBON 0.015%, DP Rs 25, CGT 5% at ≤365 days / 3.75% above (gain after fees, sell only; Gazette vol. 76, Additional 40, Part 5, 31 Bhadra 2083), circuit limits +15%/−15% (re-check against a current NEPSE/SEBON notice before relying on them), settlement T+2 standard with a broker-confirmed T+1 override (example: broker 58 — T+1 is not the standard). Dividends at flat 5%: note only, not implemented. Tests: 118/118 (106 at v0.7.0 + 12 patch tests).
- **Prediction accuracy (`toolkit/accuracy_report.py`, `docs/post-mortem-prediction-failures.md`):** ledger-driven accuracy per model/version with n stated, pending never counted as wins, failures listed with reason + action, zero-scored models render "no scored predictions". Post-mortem written first for the one locally evidenced failure (fixture, n=1, direction wrong, abs error 15.0 — not smoothed). Before/after OOS (walk-forward, existing models, `predictive.py` unchanged): level-shift fixture naive wins (MAE 3.33 vs 10.0/11.06/14.66, `beats_naive: False`); trending fixture linear_trend genuinely wins (MAE 0.0 vs naive 2.0). Two scratch candidates lost to naive and were NOT adopted. No model change shipped that fails the naive test.
- Tests: 106/106 passed (66 at v0.6.0 + 5 accuracy-report + 35 engine-validation tests), verified 28 Oct 2026 in a fresh venv.

## v0.6.0 — Phase 3: data layer + scheduled auto-updating (design live, first run not yet observed)

- **Data layer (`toolkit/data_layer.py`):** a source registry where every
  source carries its licence/terms note (a missing note is a bug, caught
  by test), a payload validator that rejects corrupt series whole
  (never repairs, never partially publishes), and a publish gate
  (`publish_check`) that rejects stale series as fresh. Full record:
  `DATA_SOURCES.md` — free public sources only (Yahoo → Stooq, CoinGecko,
  ECB, World Bank); SEC EDGAR is NOT verified from this environment and
  is not claimed verified; keyed providers run on the user's own key
  only; NEPSE has no live feed (no documented free public API verified
  — user-supplied CSV only, paid licensed feed is the owner's funding
  decision and is not started).
- **Last-known-good fix:** `autonomous.run_once` previously overwrote
  `latest.md` with its own failure report. Now `latest.md` is updated
  only on a fresh run; a failed run writes its timestamped
  `STALE_OR_FAILED` report and keeps the previous good report.
- **Scheduled run (`.github/workflows/scheduled-update.yml`):** daily at
  01:00 UTC = 06:45 NPT — after the previous US trading day's data has
  landed, before the NEPSE day — plus a manual-dispatch trigger with a
  dry-run mode that commits nothing. Generated reports commit to
  `reports/daily/` on main (generated files only, no credentials, no
  keys). **Honest limit, stated plainly:** until the first scheduled
  run is observed on GitHub with public output in `reports/daily/`,
  this schedule is a plan, not a proven fact — no autonomy is claimed
  until that run is seen. There are no email alerts (email access is a
  recorded blocker) and no Cloudflare cron backup (Cloudflare access
  unverified, recorded blocker); failures are visible in the public
  workflow log and in the `STALE_OR_FAILED` labels themselves.
- Tests: 66/66 passed (52 at v0.5.1 + 14 new data-layer/GATE 3 tests),
  verified 28 Oct 2026 in a fresh venv. GitHub Actions CI runs the same
  suite on Python 3.10/3.11/3.12.

## v0.5.1 — Walk-forward model comparison (patch)
- **Walk-forward model comparison:** `predictive.compare_models_walk_forward()` runs naive + all existing models through the same walk-forward split, ranks them by MAE, and reports `beats_naive: False` when naive wins — that result is kept in the record, not hidden.
- Tests at v0.5.1: 52/52 passed. Historical count for that version only; the current count is in the v0.7.0 section above.

## v0.5.0 — Proven record, failure learning, archiving (live on GitHub)
- **Proven record, not claims:** every prediction is written to `predictions.jsonl` BEFORE its outcome is known, scored on the next run (direction + abs error), and reported as direction accuracy + mean abs error over n scored predictions. Pending predictions never count as wins.
- **Recording failure and improving from it:** every failure gets a reason (direction wrong / error above threshold) and an action — the failing model is demoted in the next auto-selection by inverse-MAE re-weighting. Failures are never deleted from the record.
- **Archiving:** every run is timestamped and indexed in `archive/index.json`; `latest` is only a pointer. Pruning is opt-in and never deletes a symbol's latest run.
- **Agentic accuracy loop:** new Accuracy Auditor agent + prediction-accuracy-loop runbook in finance-research-agents: audit the ledger, log failures, demote failing models, check archive completeness and data freshness before any report goes out.
- **All API calls:** Yahoo, Stooq, World Bank, SEC EDGAR (no key) + FRED, Alpha Vantage, Twelve Data, FMP (your env key only), all through the fresh-data fallback chain.
- **Honest limit, stated plainly:** no system is failure-free. This one is built so failures are measured, logged, and acted on — that is the closest to "no failure" that is honest.
- Tests at v0.5.0: 52/52 passed (47 at v0.5 + free-data, + 5 model-comparison tests). Historical count for that version only; the current count is in the v0.7.0 section above.
- **Free-data layer (post-v0.5):** `toolkit/free_data.py` — unified no-key fetcher (Yahoo → Stooq for stocks; CoinGecko for crypto; ECB reference rates; World Bank macro), every series carrying source, retrieval time, last date and age, stale data never presented as fresh. No live NEPSE feed exists in this layer: no documented free public NEPSE OHLCV API was verified (see the NEPSE scanner's teardown).
- **Walk-forward model comparison (post-v0.5):** `predictive.compare_models_walk_forward()` runs naive + all existing models through the same walk-forward split, ranks them by MAE, and reports `beats_naive: False` when naive wins — that result is kept in the record, not hidden.
