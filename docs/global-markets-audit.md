# Global-markets audit (Step 0) — 2026-11-08

Scope: what in each engine assumes NEPSE, so Step 1 profiles can replace
exactly those assumptions and nothing else. NEPSE behaviour must stay
byte-identical; existing tests prove it.

## (a) NEPSE-specific assumptions per engine (audit)

1. **Time Machine (`toolkit/timemachine.py`)** — `simulate_nepse()` hard-codes
   NEPSE: circuit ±15%, broker slabs, SEBON 0.015%, DP Rs 25, CGT 5% ≤365d /
   3.75% >365d, settlement T+2. Plain `simulate()`/`replay()` are
   market-agnostic (bar counts, no calendar, no fees).
2. **Risk gate (`toolkit/position_plan.py`)** — 16 checks are market-agnostic;
   config is USD-labelled (`max_order_usd`, `max_loss_per_trade_usd`) with no
   calendar, no price limits, no fees. Frictions are NOT applied here — they
   live in `timemachine._net_realised_r` only.
3. **Quant (`toolkit/quant.py`)** — DCF, VaR/CVaR, mean-variance, HRP, sector
   rotation are generic. Bank valuation takes inputs as given; the Nepal note
   is docstring-only (verify inputs vs the current NRB directive).
4. **Predictive (`toolkit/predictive.py`)** — walk-forward on bar counts vs a
   naive baseline; no calendar, currency or market assumption.
5. **Decision log (`toolkit/decision_log.py`)** — `exchange` defaults to
   `"NEPSE"`; alpha is measured vs the NEPSE index; board text says
   "Average alpha vs NEPSE index".
6. **Report (`toolkit/report.py`)** — `exchange` defaults to `"NEPSE"`;
   otherwise generic (symbol / sector / as-of).
7. **MCP (`toolkit/mcp_server.py`)** — 8 tools; only `nepse_frictions` is
   NEPSE-only. The rest (indicators, setups, timemachine, risk_gate, DCF,
   report) are generic.
8. **Agents / rules mode (`toolkit/rules_mode.py`)** — the 9-agent NEPSE team:
   sector checklists are NEPSE-named (hydropower PPA NPR/kWh, NRB policy
   rate, NEPSE index level); sentiment lexicons include Nepali words;
   broker-flow output is labelled indicative, never proven.
9. **Connectors / data (`toolkit/connectors.py`, `data.py`, `free_data.py`)** —
   `TRADING_WEEKDAYS`: stock Mon–Fri, NEPSE Sun–Thu, crypto/macro all days.
   NEPSE is CSV-only by design (no free public NEPSE OHLCV API). Yahoo /
   CoinGecko / ECB / World Bank are already global.
10. **Terminal / CLI (`toolkit/terminal.py`)** — `research --exchange` defaults
    to NEPSE; the dashboard itself is currency-agnostic (budget is a plain
    number, no currency label).
11. **`toolkit/nepal_frictions.py`** — the whole module IS the NEPSE profile
    (broker slabs, SEBON, DP, CGT, circuit, T+2). It is the reference
    implementation: generic profiles must mirror its `UnknownFrictionError`
    discipline — an un-sourced rate is UNKNOWN, never invented.

## (b) Live terminal test (Step 0b) — 2026-11-08, this VM

Method: `toolkit.connectors.fetch_prices` (live chain) → CSV →
`python -m toolkit.terminal <csv> --symbol … --entry … --stop … --target …`.
Egress IP 198.18.138.91 (shared/NAT). GitHub Actions runners are unaffected
(daily refresh green 8 Nov).

- **AAPL (US stock) — WORKS.** Terminal ran end-to-end on the real Yahoo
  series the connector retrieved 2026-11-07 (251 bars, 2025-11-07 →
  2026-11-06, last 342.37). Dashboard: RSI(14) 51.5, 0/5 setups, Time
  Machine target-first 16.1% vs break-even 31.6% ("Strongly toward stop"),
  risk gate PASS 16/16 at reward:risk 2.17 (entry 342.37 / stop 325.00 /
  target 380.00). (First run at target 377.00 was REFUSED: R:R 1.99 < 2.0
  minimum — my input, not a tool failure.)
- **SPY (index ETF) — FAILS (live fetch).** `RuntimeError: All data sources
  failed or stale for SPY (stock). Attempts: yahoo: fetch failed for SPY:
  The read operation timed out; stooq: fetch failed for SPY: The read
  operation timed out.`
- **7203.T (Toyota, Tokyo) — FAILS (live fetch).** Same error shape as SPY:
  yahoo + stooq both `The read operation timed out`.
- **EURUSD=X (FX) — FAILS (live fetch).** Same error shape as SPY.
- **BTC-USD (crypto, Yahoo) — FAILS (live fetch).** Same error shape as SPY.
- **BTC (CoinGecko connector) — FAILS, intermittent.** `TimeoutError: The
  read operation timed out` from `fetch_coingecko_history('bitcoin')`
  (days=365, 90 and 30). Raw endpoint works intermittently: `curl`
  market_chart 90d → 200 in 0.93s; Python urllib with 60s timeout → 200,
  74KB, in 0.8s on one attempt, then timeouts again.
- **Stooq — FAILS, third strike.** `curl` → 000 after 15s; Python →
  `TimeoutError`. It also returned HTML instead of data on 28 Oct and
  7 Nov 2026. Decision (Step 2): REMOVE Stooq.
- **SEC EDGAR companyfacts — WORKS.** `curl` data.sec.gov
  `CIK0000320193.json` (Apple) → 200, 3.9MB, 4.1s; Python urllib → 200
  in 2.6s. `fetch_sec_company_facts` already exists in `toolkit/data.py`.

Root cause (evidence, not guess): Yahoo tarpits this VM's egress IP by
User-Agent — `curl` with the toolkit UA (`investment-analyst-toolkit/0.2
(+github…)`) hangs (000 after 25s); the same URL with a browser UA returns
200 in ~1s; default curl UA gets a fast 429. CoinGecko tarpits the same IP
intermittently. Nothing in the toolkit code is broken here — the chain
correctly reports every failed attempt instead of inventing data. Yahoo is
therefore best-effort from this environment and must be labelled unofficial
(Step 2). The SPY/7203.T/EURUSD=X/BTC terminal runs are re-attempted in
Step 4, when the fixture CSV is built; a fixture fetched in a clear window
is still real, dated Yahoo data with its source stated.
