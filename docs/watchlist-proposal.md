# Watchlist proposal — public track record (Step 6)

**PROPOSAL — NOT ACTIVE.** This file proposes a fixed watchlist only.
No daily run has been set up, nothing is being tracked yet, and no
workflow file has been created. The daily run happens only after the
user approves this watchlist.

## Proposed watchlist (7 tickers)

| # | Ticker | What it is | Market | Persona | Data source | Status |
|---|---|---|---|---|---|---|
| 1 | SPY | SPDR S&P 500 ETF | US | etf_index | Yahoo (fixture exists: `data/fixtures/spy_daily.csv`) | **tested** |
| 2 | QQQ | Invesco Nasdaq-100 ETF | US | etf_index | Yahoo | experimental |
| 3 | AAPL | Apple Inc. (US large-cap) | US | us_large_cap_fundamentals | Yahoo (cached series 2026-11-07) + SEC EDGAR | **tested** |
| 4 | VT | Vanguard Total World Stock ETF | Global | etf_index | Yahoo | experimental |
| 5 | 7203.T | Toyota Motor (Japan) | Japan | us_large_cap_fundamentals* | Yahoo | experimental |
| 6 | EURUSD=X | Euro / US dollar | FX | fx_macro | Yahoo | experimental |
| 7 | BTC-USD | Bitcoin / USD | Crypto | crypto | Yahoo / CoinGecko | experimental |

\* The `us_large_cap_fundamentals` persona is a rules checklist over
supplied metrics, not a US-only model — it applies to any large-cap
with sourced fundamentals. Toyota's fundamentals would come from its
filings, not EDGAR (EDGAR covers US-listed issuers; Toyota files
6-K/20-F there as an ADR, but that is out of scope for the first run).

## Why these seven

- **One per engine surface:** two US ETFs, one US stock with EDGAR
  fundamentals, one world ETF, one non-US stock, one FX pair, one
  crypto — every Step 3 persona gets a live subject.
- **Liquid and Yahoo-available:** all seven resolve on the Yahoo
  chart API, the only stock source left after Stooq's removal.
- **SPY and AAPL are already tested:** SPY has a dated fixture and a
  published "no edge" walk-forward (docs/global-evidence.md); AAPL
  ran end-to-end through the terminal in Step 0. The other five are
  honestly experimental until their first verified run.

## Deliberate gaps (not hidden)

- **No India or UK ticker yet.** India and UK have sourced profiles
  (Step 1) but no watchlist slot: Yahoo is the only stock source and
  it is tarpitted from this VM, so adding NIFTY/FTSE names before a
  second source exists would promise coverage we cannot verify.
  Add one India and one UK name once a second stock source is wired.
- **No NEPSE ticker.** NEPSE stays CSV-only by design — no live feed
  exists, and the track record must not pretend otherwise. NEPSE
  coverage continues through the scanner repo's CSV pipeline.

## Rules for the track record (fixed when the watchlist is approved)

1. The list is fixed: no ticker is added, removed or swapped without
   a dated note in this file.
2. Every call is logged **before** its outcome, with the model, the
   inputs and the date — never backfilled.
3. Failures stay in the record alongside wins; the badge counts
   settled calls only.
4. Research only, never investment advice; no orders, no execution.
5. Data is Yahoo best-effort (unofficial) + CoinGecko for crypto;
   a failed fetch is recorded as a missed day, never filled in.

## What approval unlocks (separate step, not this file)

A daily workflow that fetches the seven tickers, runs the terminal +
rules team + walk-forward, and appends the dated result to
`docs/track-record.md`. The workflow file itself needs the browser
route (the API token lacks workflow scope) and will be staged under
`ops/ready-for-browser/` when approved.
