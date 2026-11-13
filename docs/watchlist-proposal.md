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
  on the ticker's profile calendar (see
  `toolkit/trading_calendar.py`).
- `entry_close` — the close of the **decision day**. The daily run
  happens after the close (`scripts/track_record_daily.py` runs at
  22:00 UTC), so the decision-day close is known at decision time
  and is point-in-time safe: the call uses only bars up to and
  including its decision date. (The generic `decision_log` field
  is caller-supplied; the track-record engine always supplies the
  decision-day close.)
- `rule_id`, `rule_version`, `rule_code_hash` — the pre-registered
  direction rule that produced the direction
  (`docs/direction-rules.md`): its id, its version, and the git
  blob hash of `toolkit/direction_rules.py` at logging time.
- `confidence` — 0.5, a neutral prior (see
  `docs/direction-rules.md`): the rules state no probability.
- `prev_5d_return` — the return over the 5 trading days before the
  call date, for the naive baseline (None when fewer than 6 closes
  are visible).
- `confidence` — the stated probability the direction is right,
  in [0, 1].
- `sources` — the data sources used (e.g. `yahoo`, `sec-edgar`).
- `thesis` — one paragraph: what the persona saw.

At most one call per ticker per 5 trading days, non-overlapping:
a new call is due only when the previous call's horizon has passed
(previous decision date + 5 trading days ≤ today). If a rule gives
no direction on a due day (an exact tie, or too few visible bars),
no call is logged. A call is never backfilled, never edited after
logging, and never deleted. A failed fetch is recorded as a missed
day, never filled in.

**Direction rules — defined (Round 4).** The direction comes from
the pre-registered rules in `docs/direction-rules.md`
(`sma50_trend`, `mom20_sign`), implemented in
`toolkit/direction_rules.py` and run by
`scripts/track_record_daily.py`. Each rule has a rule_id, a
version, and its code hash recorded in every logged call. The
Round 3 blocker ("no rule maps a persona's sections to a
direction") is closed by changing the mechanism: direction comes
from the price rules, not from persona discussion flags — the
persona recorded on a call is attribution, not the source of the
direction. The remaining activation blockers are the watchlist
approval itself and the deployment of the staged workflow; the
script exists and is tested.

### What "5 trading days" means, per calendar

"5 trading days" is counted on the ticker's own profile calendar
(`toolkit/trading_calendar.py`: `settlement_date` is the date 5
trading days after the decision day, decision day excluded). The
weekdays each profile counts are fixed in
`toolkit/market_profiles.py` (`trading_weekdays` /
`trading_weekdays_on`):

| Ticker | Profile | Trading weekdays | What 5 trading days means |
|---|---|---|---|
| SPY, AAPL, VT | `us` | Mon–Fri | 5 weekdays. No US holiday file exists yet (`data/holidays/` holds only `india.json` and `nepse.json`), so holiday status is UNKNOWN and treated as open — every weekday counts. E.g. a call decided Fri 2026-11-06 settles Fri 2026-11-13. |
| NIFTYBEES.NS | `india` | Mon–Fri | 5 weekdays minus listed holidays in `data/holidays/india.json` (coverage: Diwali week 2026 only — outside it, UNKNOWN = open). An observed bar on a non-standard day counts: the Sun 2026-11-08 Muhurat-session bar makes a Fri 2026-11-06 call settle Fri 2026-11-13; without that bar it settles Mon 2026-11-16 (Tue 10 Nov is the Diwali-Balipratipada holiday). |
| ISF.L | `uk` | Mon–Fri | 5 weekdays. No UK holiday file exists yet, so every weekday counts. E.g. Fri 2026-11-06 → Fri 2026-11-13. |
| EURUSD=X | `fx` | Mon–Fri | 5 weekdays. FX is a 24/5 market (opens Sunday evening) but the weekday list is what the calendar uses, so Saturday and Sunday never count, and there is no holiday file. E.g. Fri 2026-11-06 → Fri 2026-11-13. |
| BTC-USD | `crypto` | Every day (Mon–Sun) | 5 calendar days — weekends count, and spot crypto has no holidays. E.g. Fri 2026-11-06 → Wed 2026-11-11; a call decided Sat 2026-11-07 settles Thu 2026-11-12. |

Two rules apply on every calendar (see "Special-session rule"
below): an observed bar always counts as a trading day, even on
a non-standard day; a calendar trading day with no bar never
counts — it is a missed day and settlement waits.

NEPSE is not on this watchlist, so its calendar defines no
horizon here. (For the record: NEPSE traded Sun–Thu until
2026-04-05 and Mon–Fri since 2026-04-06 —
`toolkit/market_profiles.py`, tested in
`tests/test_market_profiles.py::test_nepse_trading_weekdays_are_effective_dated`.)

### Settlement

After the 5-trading-day horizon, the call settles against realised
prices: `realised_return = close_at_horizon / entry_close − 1`.

- **Hit:** `expected == "up"` and realised return > 0, or
  `expected == "down"` and realised return < 0. A zero return is a
  **miss** (the predicted direction did not happen).
- Settlement uses the ticker's own price series. The realised
  return of the ticker's market index over the same window is
  recorded alongside where an index exists (S&P 500 for SPY/AAPL,
  NIFTY 50 for NIFTYBEES.NS, FTSE 100 for ISF.L; none for FX/crypto,
  and none for VT — there is no Yahoo series for the FTSE Global
  All Cap). Where there is no index, the settlement records
  `index_return` 0.0 with a labelled `no-index` source — never an
  invented index return. Settlement against an unnamed index series
  is refused.
- A call settles only after its horizon has passed; settling early
  is refused by `decision_log.settle_call`.

### Special-session rule

A session counts as a trading day when the exchange traded and
published a close — that is, when a bar for that date exists in the
fetched series. A bar dated on a non-standard day therefore counts,
and the profile weekday calendar never removes a real bar. A
calendar trading day with no bar (a missed fetch) never counts: it
is recorded as a missed day and settlement waits instead of
silently using the next bar's close. Implemented in
`toolkit/trading_calendar.py` (`is_trading_day`, with
`observed_dates` = the fetched bar dates).

Worked example: NIFTYBEES.NS has a Yahoo bar dated 2026-11-08, a
Sunday — NSE's Diwali Muhurat session (Diwali 2026 fell on Sunday
8 November 2026; NSE circular NSE/CMTR/76941, 19 Oct 2026). For a
call decided Friday 2026-11-06, the trading days after are 8 Nov
(Muhurat session), 9 Nov, 11 Nov (10 Nov is the
Diwali-Balipratipada holiday — `data/holidays/india.json`), 12 Nov,
13 Nov — so the call settles **13 November 2026**. Without the 8
Nov bar it would settle 16 November. `tests/test_track_record.py`
proves the settlement code follows the rule for that bar.

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

Report, for the model and for each baseline, **per ticker and
pooled**: **n** (settled calls) and the **95% Wilson score
interval** on the hit rate. With p̂ = hits / n and z = 1.96:

- centre = (p̂ + z² / (2n)) / (1 + z² / n)
- half-width = z · √(p̂(1 − p̂) / n + z² / (4n²)) / (1 + z² / n)
- interval = [centre − half-width, centre + half-width]

**No headline hit rate until at least 100 settled calls** (pooled
n ≥ 100). Below that the page shows n and the Wilson intervals —
but no "hit rate X%" headline figure. A point-estimate percentage
appears only in the pooled headline, once the 100-call minimum is
met; per-ticker rates are reported with n and intervals only. The
interval is shown because it honestly displays the uncertainty;
the headline percentage is what is withheld. At n ≥ 100 the
headline hit rate is shown with its interval, for the model and
both baselines. Losses stay in the record alongside wins; the
badge counts settled calls only; open calls never count as wins.

**Tickers are correlated, so pooled intervals understate
uncertainty:** seven tickers moving together are not seven
independent trials. The pooled interval is a lower bound on the
uncertainty, not a statement about independent calls — the page
prints this caveat with the pooled numbers.

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
approval of this watchlist first. The runner script
(`scripts/track_record_daily.py`) and the direction rules
(`docs/direction-rules.md`) already exist and are tested — the
workflow deployment and the watchlist approval are the remaining
activation steps.
