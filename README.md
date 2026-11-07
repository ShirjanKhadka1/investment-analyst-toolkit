# Investment Analyst Toolkit

Start with the finding, not the promise: on the real-data and example-data
walk-forward runs recorded in this repo, the naive baseline wins. This
toolkit is tooling to test forecasts honestly — every model must beat the
naive benchmark ("tomorrow's close equals today's close") on the same
walk-forward split, and when it does not, that loss stays in the record.

Two single-symbol AAPL runs, both example periods — not general accuracy
claims, not statements about AAPL, and not statements about any NEPSE
security (AAPL is a US equity; there is no live NEPSE feed here):

| Run | Horizon | Naive MAE | Best fitted model MAE | Naive wins? |
| --- | ---: | ---: | ---: | ---: |
| Yahoo AAPL, 251 daily closes, 2025-10-29 to 2026-10-28 (docs/real-data-walk-forward-2026-10-29.md) | h = 1 | 3.7660 | 5.5880 (exp_smoothing_0.3) | Yes |
| Yahoo AAPL, 251 daily closes, 2025-10-29 to 2026-10-28 | h = 3 | 6.9771 | 8.2806 (exp_smoothing_0.3) | Yes |
| Yahoo AAPL, 251 daily closes, 2025-10-29 to 2026-10-28 | h = 5 | 9.2287 | 10.3993 (moving_average_5) | Yes |
| Labelled example file, AAPL daily closes, 2024-01-02 to 2025-12-31, 502 bars (docs/r6-example-data-walk-forward-2026-10-29.md) | h = 1 | 2.8474 | 4.5180 (exp_smoothing_0.3) | Yes |
| Labelled example file, AAPL daily closes, 2024-01-02 to 2025-12-31, 502 bars | h = 3 | 5.6394 | 6.5891 (exp_smoothing_0.3) | Yes |
| Labelled example file, AAPL daily closes, 2024-01-02 to 2025-12-31, 502 bars | h = 5 | 7.7088 | 7.9284 (exp_smoothing_0.3) | Yes |

MAE is in US dollars per share; `beats_naive` is False for every fitted
model at every horizon in both runs. MAE grows with horizon for every
model, including naive. One symbol, one period, one train/test split,
and the default four-model set only, in each run — that is the whole
claim, and it is not stretched further.

A small, tested Python toolkit for the calculations an investment / research
analyst actually does every day — financial ratios, DuPont decomposition,
valuation multiples, and a transparent DCF helper that forces you to show every
input. No black boxes, no hidden assumptions.

> **Educational use.** This is a learning and analysis toolkit, not investment
> advice, and not a recommendation to buy or sell any security. Every example
> uses clearly-labelled demo data unless a source is cited.

## Losses, kept in the record

None of these is softened, buried, or deleted. Each one is also in the
linked record named beside it.

- **Level-shift loss (synthetic fixture): naive wins, adaptive loses.**
  On the 100 to 150 level-shift fixture, naive wins at every horizon
  (MAE 3.333 at h = 1, 3.846 at h = 3, 4.545 at h = 5) and every
  non-naive model loses, including the adaptive ensemble (6.276, 7.690,
  10.382). Full table in the v0.8.0 section below and in
  `docs/post-mortem-prediction-failures.md`.
- **Noisy-flat drift loss (synthetic fixture): drift loses.** On the
  noisy-flat fixture, drift loses at every horizon (2.906 vs naive
  2.900 at h = 1; 2.164 vs 1.833 at h = 3; 3.668 vs 3.438 at h = 5):
  on a series with no real drift, the mean recent change is noise,
  and adding it hurts. Full table in the v0.8.0 section below and in
  `docs/post-mortem-prediction-failures.md`.
- **AAPL real-data naive win.** In the Yahoo AAPL walk-forward above,
  naive beat every fitted model at h = 1, 3, and 5 (naive MAE 3.7660,
  6.9771, 9.2287). Full record:
  `docs/real-data-walk-forward-2026-10-29.md`. The example-file run
  says the same thing on its own period (naive 2.8474, 5.6394, 7.7088):
  `docs/r6-example-data-walk-forward-2026-10-29.md`.
- **Scanner bullish crossover loss (example data, NOT NEPSE).** On the
  same labelled AAPL example file, the bullish EMA-crossover signal
  hit 33.3% at 5 bars (1 win, 2 losses, 3 events) against a naive rate
  of 56.5% — a lift of −23.1 points. At 20 bars it won 2 of 3 yet still
  averaged −1.87%. The bearish 20-bar lift (+18.6 points) rests on
  n = 2 with a mean forward return of −6.05% and is noise, not a win
  to quote alone. Full table, with every event and its baseline, in
  the scanner repo's backtest section:
  https://github.com/ShirjanKhadka1/nepali-capital-market-research/blob/main/scanner/README.md

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
| `toolkit/pit_store.py` | Point-in-time store: bars + fundamentals with `as_of` / `published_at`; reads return only what was published by the query date (Engine 1.1) |
| `toolkit/timemachine.py` | Seeded Monte-Carlo + historical replay, named-setup replay, honest verdict, NEPSE upgrade with circuit limits, exit-liquidity guard and sourced frictions (Engine 1.2) |
| `toolkit/terminal.py` | One-command research terminal: `python -m toolkit.terminal demo.csv` |
| `toolkit/quant.py` | Quant library: FCFF/FCFE DCF with sensitivity, bank valuation (PBV/ROE/NPL/CAR inputs as given), historical + parametric VaR/CVaR, entropic VaR, Sharpe inference, multiple-testing correction, turnover/costs, momentum + mean-reversion factors, mean-variance + HRP + max-diversification optimisation, sector rotation, Alpha158 baseline factors (Engine 1.6) |
| `toolkit/predictive.py` (Engine 1.7) | Walk-forward validation only (expanding/rolling, refit per fold), model vs naive / buy-and-hold / index report with "edge" / "no edge" verdicts, Platt calibration + Brier score, model cards, heuristic regime detection, retraining schedule, walk-forward reads through the point-in-time store; results in `MODELS.md` (generated by `scripts/generate_models_table.py`) |
| `toolkit/report.py` (Engine 1.9) | Research report pipeline: `research <SYMBOL>` builds a one-page HTML report (thesis, bull/bear, peers and sector comps, risks, cited sources) from caller-supplied bars, fundamentals, and peer rows; every number traces to an input row or is UNKNOWN; no data means the report says so plainly |
| `toolkit/mcp_server.py` (Engine 1.10) | MCP server (stdlib-only JSON-RPC over stdio: initialize, tools/list, tools/call) exposing read-only analytics as tools — indicators, screening setups, Time-Machine simulation, risk gate, quant functions, NEPSE frictions, research report. No execution, no network, no keys. NEPSE MCP servers already exist (see the module docstring); this one fetches nothing and executes nothing |

## One-command terminal

```bash
python -m toolkit.terminal demo.csv
```

Reads a CSV of daily OHLCV bars and prints a research dashboard to
stdout — no network, no keys, no live trading, no orders. The shipped
`demo.csv` is a **synthetic** example (80 weekday bars, 2026-07-20 to
2026-11-06, NEPSE-style prices in NPR, symbol DEMO; generated
2026-11-07 from a seeded random walk — see the `#` provenance lines
at the top of the file, which the CLI skips when reading). Bring your
own file the same way: header `date,open,high,low,close,volume`
(only `date` and `close` are required; `open`/`high`/`low` default
to the close, `volume` is optional and VWAP is skipped without it).

What it prints, in four sections:

1. **Indicator snapshot** — RSI(14), MACD 12/26/9, Bollinger 20/2
   (with %B), ATR(14), SMA20/EMA20 trend, ADX(14), Stochastic,
   VWAP. Values that need more history than the file holds print as
   `n/a (needs N bars)` instead of being invented.
2. **Screening** — the five named scanner setups (`breakout_20d`,
   `bull_flag`, `reclaim_ma20`, `oversold`, `oversold_bounce`),
   reimplemented here from `scanner/setups.py` so the toolkit stays
   self-contained. Each setup prints its rule and whether the past
   closes satisfy it. On the demo file, 2 of 5 match
   (`breakout_20d`, `bull_flag`).
3. **Time-Machine odds** — a seeded Monte-Carlo replay (seed 19,
   10,000 replays, 20-day horizon) of a hypothetical exit plan plus
   a deterministic historical replay of the same rule. Default plan:
   entry = last close, stop = entry − 1.5 × ATR(14), target at
   reward:risk 2 (override with `--stop`/`--target`,
   `--risk-reward`, `--horizon`, `--n-sims`, `--seed`).
4. **Risk gate** — the 16-check PASS/BLOCK checklist on that
   hypothetical plan (sized from the example research budget).
   It BLOCKs by default because the entry window is not confirmed;
   pass `--entry-window-ok` only when the plan's entry window is
   actually open.

### What it will NOT do

- It is **research and education only — not investment advice,
  never a buy/sell/hold recommendation**. A setup match means the
  past closes satisfy a stated rule, not that anything should be
  bought. A PASS is not a yes.
- The Time-Machine output is a **simulation, not a prediction**:
  resampled history is not the future, and a target-first share
  above break-even is a property of the past sample, not a
  probability of profit.
- It places no orders, talks to no broker, fetches nothing, and
  stores nothing. A bad CSV (missing file, missing columns,
  non-numeric prices, no data rows) exits non-zero with a one-line
  `error: ...` message and never a traceback; a short-but-valid
  file still prints, with the sections it cannot run replaced by a
  short note.

See `tests/test_terminal.py` for the coverage (end-to-end run,
malformed / empty / missing files, determinism, honesty labels,
short-file degradation).

## Point-in-time store (Engine 1.1)

`toolkit/pit_store.py` — every record carries `as_of` (the date the
data describes) and `published_at` (when it became available).
`bars_as_of(date)` and `fundamentals_as_of(date)` return only
records with `published_at <= date`, one per `as_of` (the latest
published revision). A backtest that reads through this store cannot
see data that was not yet published on its decision date, so
look-ahead is impossible by construction. Revisions are kept, never
overwritten; a query before anything was published returns an empty
list, never an invented row. `bars_for_timemachine(date)` feeds the
visible bars straight into `timemachine.simulate` /
`replay_history`. Zero dependencies, offline tests with synthetic
fixtures only.

## NEPSE Time Machine upgrade (Engine 1.2)

`toolkit/timemachine.py` keeps its existing `simulate` /
`replay_history` API and adds:

- **Named-setup replay** — `replay_setup(prices, setup, entry, stop,
  target)`: the deterministic replay restricted to past windows
  where one of the five named setups (`breakout_20d`, `bull_flag`,
  `reclaim_ma20`, `oversold`, `oversold_bounce`) had fired at the
  window's start, evaluated on closes visible then — never on later
  bars. The rules are reimplemented locally; this module does not
  import the scanner repo. A caller-supplied setup function is also
  accepted (`setup_fn=`).
- **Honest verdict** — every setup replay and NEPSE simulation
  carries `honest_verdict`: `"edge"`, `"coin flip"` or
  `"insufficient data"`. Thresholds, stated: fewer than 20 windows
  is `"insufficient data"`; otherwise `"edge"` only when the
  target-first share is at least 10 percentage points from
  break-even; anything in between is `"coin flip"`. "Edge"
  describes the past sample only — every result still states
  "simulation, not a prediction".
- **NEPSE simulation** — `simulate_nepse(...)`: the seeded
  Monte-Carlo with daily moves clipped to the ±15% circuit limit
  (user-supplied, 28 Oct 2026), an exit-liquidity guard that
  BLOCKs a position above 10% of typical daily volume instead of
  simulating it, and real trading frictions from
  `sourced_nepal_frictions()` (broker slabs 0.36%/0.33%/0.31%/
  0.27%/0.24%, SEBON 0.015%, DP Rs 25, CGT 5% ≤365d / 3.75%
  >365d — user-supplied rate-schedule screenshot, 28 Oct 2026;
  settlement T+2 as standard). `average_R` is reported net of
  those frictions, with `average_R_gross` kept for comparison.

### Worked example (synthetic demo data — reproduced by tests)

On the shipped synthetic `demo.csv`, hypothetical plan entry
1284.91, stop 1259.72, target 1335.29 (the terminal's default plan),
100 shares, typical daily volume 30,000, seed 19, 10,000 replays,
20-day horizon:

```python
from toolkit.terminal import load_bars
from toolkit import timemachine

bars = load_bars("demo.csv")
ohlc = [{"open": b["open"], "high": b["high"], "low": b["low"],
         "close": b["close"]} for b in bars]
res = timemachine.simulate_nepse(ohlc, 1284.91, 1259.72, 1335.29,
                                 shares=100,
                                 typical_daily_volume=30_000)
# target_first 57.5%, stop_first 39.0%, neither 3.5%
# break-even 33.3%, honest_verdict "edge", 61 windows
# average_R gross 0.79, net of frictions 0.37
# median final close 1334.07 (p5 1244.46, p95 1359.89)
# buy charges at entry Rs 468.29
rep = timemachine.replay_setup(ohlc, "breakout_20d",
                               1284.91, 1259.72, 1335.29)
# 11 setup windows of 61; target_first 72.7% — yet the honest
# verdict is "insufficient data" (fewer than 20 setup windows).
# bull_flag fired on 0 windows: no shares are reported at all.
```

Frictions cut the average realised R from 0.79 to 0.37 on this
example — that drag is the point of modelling them. And the
`breakout_20d` replay shows the verdict doing its job: a
72.7% target-first record on 11 windows is not an edge, it is too
little data to call. The numbers above are pinned by
`tests/test_timemachine_nepse.py`, so the README cannot drift
from the code.

## NEPSE agent team — no-LLM rules mode (Engine 1.3)

`toolkit/rules_mode.py` is the deterministic, zero-API-key fallback
for the nine NEPSE agents in
[finance-research-agents](https://github.com/ShirjanKhadka1/finance-research-agents)
(Fundamentals, Technical, News & Sentiment, Broker-flow, Macro,
Corporate Actions, Trader, Risk team, Portfolio Manager). Every
agent file states it works with any LLM, including a free local
Ollama — and this module lets the same structured outputs be
produced with no LLM at all: sector checklists (banks, hydropower,
insurance, microfinance — a missing metric is UNKNOWN, never
filled in), a technical read (momentum, 20-day mean, volatility,
RSI(14) — past bars only), a Nepali + English lexicon sentiment
count, a broker-flow read labelled INDICATIVE ONLY (broker flow
never proves who bought or why), an NRB/macro checklist, and
corporate-action flags. `run_rules_team()` runs the whole team over
supplied inputs in one structured dict. No keys, no endpoints, no
fetching; offline tests with synthetic fixtures only
(`tests/test_rules_mode.py`, 14 tests).

## Decision memory + public scoreboard (Engine 1.4)

`toolkit/decision_log.py` logs every research call *before* its
outcome is known (`write_call`: date, symbol, thesis, horizon,
expected direction/range, sources, confidence), settles it after
its horizon (`settle_call`: realised return and alpha vs the NEPSE
index — the index return and its source are supplied by the caller
and settlement refuses an unnamed index series, so no index history
is ever invented; settling before the horizon ends is refused),
and scores the settled calls (`scoreboard`, Brier score included).
Reflections (`append_reflection`) are read by the Portfolio Manager
agent before its next run on the same symbol; the format is
documented in `toolkit/decision_log/SCHEMA.md`.

> **Track record: n=0 settled — hit rate n=0, average alpha n=0,
> Brier score n=0, wins 0 / losses 0.** See
> [`docs/track-record.md`](docs/track-record.md), regenerated from
> the log by `render_scoreboard_md` and never edited by hand. The
> scoreboard starts empty and stays at n=0 until a real call is
> logged and settled; losses are included and open calls never
> count as wins.

## Extended risk gate + paper ledger (Engine 1.5)

`toolkit/position_plan.py` keeps its 16-check `risk_gate()`
unchanged and adds `extended_risk_gate()` — the same 16 checks
plus three per-idea limits: max loss per idea, sector
concentration (existing sector exposure + this plan, as a share of
the hypothetical portfolio), and exit liquidity (reusing
`timemachine.check_exit_liquidity`). Context the extra checks need
is supplied by the caller and every check fails closed when it is
missing. A PASS from either gate is not a recommendation.
`toolkit/paper_ledger.py` records hypothetical plans and their gate
verdicts (fingerprint included) — paper only, no orders, no
execution. Tests: `tests/test_decision_log.py` (10 tests, covering
1.4 and 1.5 on synthetic fixtures).

## Research report pipeline (Engine 1.9)

`python -m toolkit.terminal research <SYMBOL> --bars bars.csv
--fundamentals fund.csv --peers peers.csv` writes a one-page HTML
report (`reports/<SYMBOL>-research.html` by default): thesis, key
figures, bull/bear, fundamentals, peers and sector comps, risks,
cited sources. Data comes from the CSVs the caller supplies; peers
and comps are labelled with their source per row, or UNKNOWN; if no
data exists for the symbol, the report says so plainly instead of
inventing figures. HTML only — no PDF dependency, stdlib only.
Tests: `tests/test_report.py` (16 tests, synthetic fixtures, offline).

## MCP server (Engine 1.10)

`python -m toolkit.mcp_server` serves JSON-RPC 2.0 over stdio
(`initialize`, `tools/list`, `tools/call`, `ping`) with eight
read-only tools: indicators, screening setups, Time-Machine
simulation, risk gate, quant risk summary, quant DCF, NEPSE
frictions, research report. Every tool works on caller-supplied
arguments; nothing is fetched, nothing is executed, no keys. NEPSE
MCP servers already exist — `razaanstha/nepse-mcp-server` (live data
via the ShareBazaar API), `nlethetech/nepse-quant-terminal`
(FastMCP, including an order lifecycle), and Lipi's remote MCP
server (including `get_nepse_index`); see the module docstring for
the full prior-art note. This server deliberately does not compete
with them: it is the MCP front door to the toolkit's own tested
functions. Tests: `tests/test_mcp_server.py` (19 tests, protocol
level, offline).

## Tests

```bash
python -m pytest tests/ -v
```

Every public function has at least one test, including an edge-case test
(zero revenue, negative equity, growth >= discount rate). If a test fails,
the toolkit is wrong — open an issue.

Tests: 436/436 tests passing (Phase 1 engines 1.3–1.10, 2026-11-07, fresh clone, Python 3.12, pytest 9.2.0): 401 before + 35 new tests — research report pipeline (16) and MCP server (19).
Quality gates (Phase 7): `ruff check` clean, `mypy` clean under default
settings (see mypy.ini — untyped code is unchecked by default mypy, so
this is a guardrail, not full type safety), coverage 88% total with a
`fail_under=80` gate left unchanged (the remaining misses are mostly
defensive/unreachable branches, e.g. predictive.py 342-343, 348-349, 572
plus new-engine edge branches; quant.py 93%, predictive.py 95%;
free_data.py, portfolio.py, montecarlo.py, ratios.py and checks.py are
at 100%; `toolkit/terminal.py` measures 34% only because its tests run
it as a subprocess, which coverage.py does not attribute to this run).
CI runs pytest on Linux/macOS/Windows
× Python 3.10/3.11/3.12; the macOS/Windows combinations are CI-only
until their first GitHub run is observed.

Generated counts: see `status.json` (produced by `scripts/generate_status.py`,
checked by `scripts/check_numbers.py` — the check fails if any number above
disagrees with `status.json`).

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
- Tests at v0.3: 23/23 passed. (Historical count for that version only; the current count is in the Tests section above and in status.json, verified by CI.)

## v0.4.0 — Quality data, autonomous updates, predictive models, zero-error guards
- **No stale data, ever:** `fetch_fresh_prices()` tries sources in order (Yahoo → Stooq → Alpha Vantage / Twelve Data / FMP with your key), skips any source that fails or is older than `max_age_days`, and raises listing every attempt if all are stale — it never returns stale data labelled fresh, and never invents a price.
- **API keys, properly:** keys come from the environment ONLY (`.env.example` lists FRED, Alpha Vantage, Twelve Data, FMP). No key is bundled, hard-coded, or invented; a missing key raises an error naming the provider, env var, and where to get a free key. No-key sources (Yahoo, Stooq, World Bank, SEC EDGAR, CSV) work with no key at all.
- **Predictive, walk-forward only:** naive, moving-average, linear trend, exponential smoothing; `auto_select_model()` picks by out-of-sample MAE; inverse-MAE ensemble; anomaly flags (z-score); volatility regime (high/low).
- **Autonomous auto-updating:** `toolkit/autonomous.py` — `run_once()` fetches fresh data, re-runs prediction + performance, writes timestamped + `latest` reports and a hash-chained audit log; one symbol failing never kills the run (its old report is kept, marked STALE). `run_loop()` repeats daily by default. Research only — no trades, no spend.
- **Zero-error guards:** every series is validated at the boundary — finite numbers only (NaN/inf/None/str/bool rejected), price pairs must be ISO dates, strictly increasing, unique, close > 0. Bad input fails loudly with what to fix, never as a wrong number downstream.
- CLI: `python -m toolkit.cli data --symbol AAPL`, `predict`, `autonomous-once --symbols AAPL,MSFT`.
- Tests at v0.4: 33 tests. (Historical count for that version only; the current count is in the Tests section above and in status.json, verified by CI.)

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

## v0.8.0 — Phase 10: increased predictive capability (honest evaluation)

New forecasters in `toolkit/predictive.py` (all stdlib, all pure functions
of the training window only — no lookahead, no test-period statistics):

| Model | Maths (one line) |
| --- | --- |
| `ar1` | OLS y_t = c + phi·y_{t-1}; |phi| clamped to 0.99 (stationarity guard); iterated for multi-step |
| `holt` | Double exponential smoothing, level + trend (alpha 0.3, beta 0.1) |
| `holt_damped` | Holt with trend damping 0.9 — trend that may not persist |
| `drift` | Last value + horizon × mean recent change |
| `ensemble_adaptive` | Inverse-recent-MAE weights from internal walk-forward on the training window only; equal-weight fallback when train is too short |

Pre-existing models (`naive`, `moving_average_5`, `linear_trend`,
`exp_smoothing_0.3`, `ensemble_equal`) are unchanged, and the default
`compare_models_walk_forward()` call still returns exactly those four
models at h=1 — new models are opt-in via `include_new_models=True`,
with horizons via `horizons=(1, 3, 5)` and the adaptive ensemble via
`include_adaptive=True`, so no existing ranking changes silently.

Multi-horizon walk-forward results (MAE, synthetic fixtures only — not
market data; `*` = beats naive at that horizon; naive MAE in brackets):

| Fixture (train) | h | Winner(s) | Losers vs naive |
| --- | ---: | --- | --- |
| Trending, +2/step (20) | 1 | linear_trend, drift, holt, ensemble_adaptive = 0.000 (naive 2.000) | moving_average_5, exp_smoothing_0.3 |
| Trending, +2/step (20) | 3 | linear_trend, drift, holt, ensemble_adaptive = 0.000 (naive 6.000) | moving_average_5, exp_smoothing_0.3 |
| Trending, +2/step (20) | 5 | linear_trend, drift, holt, ensemble_adaptive = 0.000 (naive 10.000) | moving_average_5, exp_smoothing_0.3 |
| Level shift 100→150 (15) | 1 | naive = 3.333 (ar1 ties at 3.333, a tie is not a win) | all other models, incl. ensemble_adaptive (6.276) |
| Level shift 100→150 (15) | 3 | naive = 3.846 (ar1 ties) | all other models, incl. ensemble_adaptive (7.690) |
| Level shift 100→150 (15) | 5 | naive = 4.545 (ar1 ties) | all other models, incl. ensemble_adaptive (10.382) |
| Noisy flat, deterministic pattern (20) | 1 | ar1 = 1.855 (naive 2.900) | drift (2.906) only |
| Noisy flat, deterministic pattern (20) | 3 | moving_average_5 = 1.422 (naive 1.833) | drift (2.164) only |
| Noisy flat, deterministic pattern (20) | 5 | ar1 = 1.834 (naive 3.438) | drift (3.668) only |

Reading this table honestly: no model is "accurate in general". Naive
wins the level shift at every horizon; drift loses the noisy-flat
fixture at every horizon (its documented overshoot failure mode); the
adaptive ensemble does not rescue the shift. Wins above are wins on
that fixture, at that horizon, at that n — nothing more.

Also added: prediction intervals from walk-forward residuals inside
the training window (`prediction_interval()`: empirical quantiles with
>= 5 residuals, normal-approx fallback otherwise, method + n always
stated — never a guaranteed range), and drift flags
(`drift_flag()` / `walk_forward_drift()`: recent MAE >= 1.5× early MAE
over >= 3 errors each, flagged in words, never a silent model switch).

Version decision: bumped to **v0.8.0** because the new capability is
user-facing (new models, horizons, intervals, adaptive weighting) and
all 148 tests pass. Research only; not investment advice; no buy/sell/
hold recommendation; NEPSE stays CSV-only.

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
- Tests at v0.5.1: 52/52 passed. (Historical count for that version only; the current count is in the Tests section above and in status.json, verified by CI.)

## v0.5.0 — Proven record, failure learning, archiving (live on GitHub)
- **Proven record, not claims:** every prediction is written to `predictions.jsonl` BEFORE its outcome is known, scored on the next run (direction + abs error), and reported as direction accuracy + mean abs error over n scored predictions. Pending predictions never count as wins.
- **Recording failure and improving from it:** every failure gets a reason (direction wrong / error above threshold) and an action — the failing model is demoted in the next auto-selection by inverse-MAE re-weighting. Failures are never deleted from the record.
- **Archiving:** every run is timestamped and indexed in `archive/index.json`; `latest` is only a pointer. Pruning is opt-in and never deletes a symbol's latest run.
- **Agentic accuracy loop:** new Accuracy Auditor agent + prediction-accuracy-loop runbook in finance-research-agents: audit the ledger, log failures, demote failing models, check archive completeness and data freshness before any report goes out.
- **All API calls:** Yahoo, Stooq, World Bank, SEC EDGAR (no key) + FRED, Alpha Vantage, Twelve Data, FMP (your env key only), all through the fresh-data fallback chain.
- **Honest limit, stated plainly:** no system is failure-free. This one is built so failures are measured, logged, and acted on — that is the closest to "no failure" that is honest.
- Tests at v0.5.0: 52/52 passed (47 at v0.5 + free-data, + 5 model-comparison tests). (Historical count for that version only; the current count is in the Tests section above and in status.json, verified by CI.)
- **Free-data layer (post-v0.5):** `toolkit/free_data.py` — unified no-key fetcher (Yahoo → Stooq for stocks; CoinGecko for crypto; ECB reference rates; World Bank macro), every series carrying source, retrieval time, last date and age, stale data never presented as fresh. No live NEPSE feed exists in this layer: no documented free public NEPSE OHLCV API was verified (see the NEPSE scanner's teardown).
- **Walk-forward model comparison (post-v0.5):** `predictive.compare_models_walk_forward()` runs naive + all existing models through the same walk-forward split, ranks them by MAE, and reports `beats_naive: False` when naive wins — that result is kept in the record, not hidden.
