# Watchlist proposal — public track record (Step 6)

**PROPOSAL — NOT ACTIVE.** This file proposes a fixed watchlist only.
No daily run has been set up, nothing is being tracked yet, and no
workflow file has been deployed. The daily run happens only after the
user approves this watchlist.

## Proposed watchlist (7 tickers)

| # | Ticker | What it is | Market | Profile | Exchange | Persona | Data source | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | SPY | SPDR S&P 500 ETF | US | us | NYSE Arca | etf_index | Yahoo (fixture exists: `data/fixtures/spy_daily.csv`) | **tested** |
| 2 | AAPL | Apple Inc. (US large-cap) | US | us | Nasdaq | us_large_cap_fundamentals | Yahoo (cached series 2026-11-07) + SEC EDGAR | **tested** |
| 3 | VT | Vanguard Total World Stock ETF | US-listed, global holdings | us | NYSE Arca | etf_index | Yahoo | experimental |
| 4 | NIFTYBEES.NS | Nippon India ETF Nifty BeES (NIFTY 50) | India | india | NSE | etf_index | Yahoo | experimental |
| 5 | ISF.L | iShares Core FTSE 100 UCITS ETF | UK | uk | LSE | etf_index | Yahoo | experimental |
| 6 | EURUSD=X | Euro / US dollar | FX | fx | FX (OTC) | fx_macro | Yahoo | experimental |
| 7 | BTC-USD | Bitcoin / USD | Crypto | crypto | crypto (venue-agnostic) | crypto | Yahoo / CoinGecko | experimental |

Profile is the venue's rules, not the holdings' geography: VT holds
stocks worldwide but trades on NYSE Arca under US rules, so the
`us` profile judges it. The mapping is enforced in code —
`toolkit/market_profiles.py` (`WATCHLIST_PROFILES`) — and
`tests/test_watchlist.py` fails if this table and that dict disagree,
if a ticker maps to a profile that does not exist, or if a removed
ticker (QQQ, 7203.T) comes back.

Removed in Round 3 (8 Nov 2026): **QQQ** (redundant with SPY — a
second US large-cap ETF adds no engine surface) and **7203.T**
(no Japan profile exists and no Japanese fundamentals source is
wired, so the fundamentals persona could not judge it honestly).

## Why these seven

- **One per engine surface:** two US subjects with different
  personas (SPY for etf_index, AAPL for us_large_cap_fundamentals),
  one world ETF, one India ETF, one UK ETF, one FX pair, one crypto
  — every Step 3 persona gets a live subject, and three markets
  (US, India, UK) get a sourced profile exercised.
- **Liquid and Yahoo-available:** all seven resolve on the Yahoo
  chart API, the only stock source left after Stooq's removal.
  Yahoo was verified from GitHub runners, not just this VM:
  probe #1, 8 Nov 2026, 3/3 OK (SPY n=251, 7203.T n=243,
  EURUSD=X n=260, all last 2026-11-06);
  probe #2, 8 Nov 2026, 2/2 OK (NIFTYBEES.NS n=250, last
  2026-11-08; ISF.L n=254, last 2026-11-06).
  Runs: https://github.com/ShirjanKhadka1/investment-analyst-toolkit/actions/runs/42487106087
  and https://github.com/ShirjanKhadka1/investment-analyst-toolkit/actions/runs/42488973219.
  The "tarpitted from this VM" problem is a property of this
  machine's shared egress IP, not of Yahoo: the runners answer.
- **SPY and AAPL are already tested:** SPY has a dated fixture and a
  published "no edge" walk-forward (docs/global-evidence.md); AAPL
  ran end-to-end through the terminal in Step 0. The other five are
  honestly experimental until their first verified run.

## Deliberate gaps (not hidden)

- **No Japan ticker.** 7203.T was removed because no Japan profile
  exists and no Japanese fundamentals source is wired. Add a Japan
  name only with a sourced Japan profile and a verified Yahoo run.
- **No NEPSE ticker.** NEPSE stays CSV-only by design — no live feed
  exists, and the track record must not pretend otherwise. NEPSE
  coverage continues through the scanner repo's CSV pipeline.
- **One stock-price source.** Yahoo is the only verified global
  equities source. If Yahoo fails from runners, global price data
  has no fallback. Fallback is PROPOSED only (opt-in free-key
  provider via repo secret, or CSV snapshots) — not implemented.

## Track-record spec (Round 3)

This spec defines the public track record. It is a spec, not a run:
nothing is being tracked yet, and no daily workflow is deployed.

### One call

A call is a directional prediction for one watchlist ticker over a
**fixed horizon of 5 trading days** (one trading week, counted on
the ticker's own profile calendar — `trading_weekdays` of its
mapped profile). Fields, logged **before** the outcome is known:

- `date` — the call date (YYYY-MM-DD).
- `symbol` — the watchlist ticker.
- `exchange` — the venue label from the table above (required;
  `toolkit/decision_log.py` refuses a write without it).
- `profile` — the mapped profile key from the table above.
- `persona` — the rules-mode persona that produced the direction.
- `expected` — `up` or `down` only. No `range` calls in the public
  track record: direction is what is scored.
- `horizon_days` — 5, meaning 5 **trading** days. Note the unit:
  `decision_log.settle_call` checks the horizon in calendar days
  as a minimum, and 5 trading days always span at least 5 calendar
  days, so the check holds — but the recorded horizon is trading
  days, and the settlement date is the call date + 5 trading days
  on the ticker's profile calendar.
- `entry_close` — the first close **after** the call date
  (point-in-time reference price; same-day prices never leak in).
- `confidence` — the stated probability the direction is right,
  in [0, 1].
- `sources` — the data sources used (e.g. `yahoo`, `sec-edgar`).
- `thesis` — one paragraph: what the persona saw.

At most one call per ticker per day, from that ticker's persona. If
a persona gives no direction on a day (neutral), no call is logged.
A call is never backfilled, never edited after logging, and never
deleted. A failed fetch is recorded as a missed day, never filled in.

**Not yet defined — activation blocker.** The rules team returns
structured sections with discussion flags, not a direction verdict
(`toolkit/rules_mode.py`: "flags are discussion prompts, not
verdicts"). The rule that maps a persona's sections to UP / DOWN /
neutral does not exist yet anywhere in the codebase. It must be
specified, implemented in `scripts/track_record_daily.py`, and
tested — with its exact thresholds written here — before the daily
workflow is deployed. No direction may be invented by the workflow
to fill the gap.

### Settlement

After the 5-trading-day horizon, the call settles against realised
prices: `realised_return = close_at_horizon / entry_close − 1`.

- **Hit:** `expected == "up"` and realised return > 0, or
  `expected == "down"` and realised return < 0. A zero return is a
  **miss** (the predicted direction did not happen).
- Settlement uses the ticker's own price series. The realised
  return of the ticker's market index over the same window is
  recorded alongside where an index exists (S&P 500 for SPY/AAPL,
  NIFTY 50 for NIFTYBEES.NS, FTSE 100 for ISF.L, FTSE Global All
  Cap for VT; none for FX/crypto), with the index source labelled.
  Settlement against an unnamed index series is refused.
- A call settles only after its horizon has passed; settling early
  is refused by `decision_log.settle_call`.

### Scoring — two baselines, one interval, one minimum

Every settled call is scored against **two baselines** computed on
the **same** settled calls (same n, so the comparison is
apples-to-apples):

- **Naive baseline:** predicts the direction of the previous
  5-trading-day window continues — the sign of the return over the
  5 trading days before the call date. A zero previous return
  predicts UP (documented tie-break).
- **Buy-and-hold baseline:** always predicts UP (holding the
  ticker). This is the "did the model beat just owning it?" check.

Report, for the model and for each baseline: **n** (settled calls)
and the **95% Wilson score interval** on the hit rate. With
p̂ = hits / n and z = 1.96:

- centre = (p̂ + z² / (2n)) / (1 + z² / n)
- half-width = z · √(p̂(1 − p̂) / n + z² / (4n²)) / (1 + z² / n)
- interval = [centre − half-width, centre + half-width]

**No headline hit rate until at least 30 settled calls.** Below
n = 30 the page shows n, the Wilson interval and the baselines'
intervals — but no "hit rate X%" headline figure. The interval is
shown because it honestly displays the uncertainty; the headline
percentage is what is withheld. At n ≥ 30 the headline hit rate
is shown with its interval, for the model and both baselines.
Losses stay in the record alongside wins; the badge counts settled
calls only; open calls never count as wins.

### Where results live

Results (the calls log and the rendered track-record page) live on
a **data branch** (`data/track-record`), updated by the staged
daily workflow — **never on main**. Main carries the code and the
spec; the data branch carries the outcomes. The workflow file is
staged at `ops/ready-for-browser/daily-track-record.yml` (NOT
deployed): daily schedule + manual dispatch, it fetches the seven
tickers, runs the rules team, logs new calls, settles due calls,
and commits the results to the data branch. It never writes to
main and never merges anything.

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

The staged daily workflow (`ops/ready-for-browser/daily-track-record.yml`)
is deployed via the browser route (the API token lacks workflow
scope) and the first daily run starts. Deploying it needs the user's
approval of this watchlist first.
