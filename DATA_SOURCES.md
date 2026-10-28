# DATA_SOURCES.md — every source this toolkit may call, with licence/terms

> Research only. Not investment advice. Nothing here is a recommendation
> to buy, sell, or invest in any security.

This file is the toolkit's data-source record, required by the programme's
integrity rules: every number's source is named, with its period, or the
number is labelled UNKNOWN/TO VERIFY. The machine-readable twin of this
table is `toolkit/data_layer.py` (`DATA_SOURCES`); if the two disagree,
the code registry is the one the tests enforce, and this file must be
corrected — never the other way round.

Verification dates below are 28 Oct 2026 unless stated otherwise. A
source marked TO VERIFY or NOT VERIFIED is not a usable claim: it may
not be cited as working, and its data may not be republished as a
dataset until the note is resolved.

## No-key chain (used by `toolkit/free_data.py`)

| Source | Provides | Licence / terms | Verification (28 Oct 2026) |
|---|---|---|---|
| Yahoo Finance chart API | Stock/ETF daily price history | Public endpoint, no key. No licence published on the endpoint; use under Yahoo's terms. Redistribution TO VERIFY. | Working: AAPL, 251 points, last date 2026-10-27, fresh. |
| Stooq daily CSV | Stock daily closes (fallback only) | Free CSV, no key; Stooq's terms apply to reuse. Redistribution TO VERIFY. | NOT relied on alone: smoke check returned an HTML page, not CSV. |
| CoinGecko keyless public API | Crypto daily prices (market chart) | Documented keyless API; shared IP pool, roughly 10–30 calls/min. Respect the limit; never work around a rate limit. | Working: BTC price + 91 daily points, fresh. |
| ECB euro reference rates | Daily EUR reference fixings (latest only) | Published by the ECB as official reference rates; attribute the ECB. A fixing is not a tradable quote and not a series. | Working: 2026-10-27 fixing, 29 currencies. |
| World Bank API v2 | Macro indicators by country/year | World Bank Open Data terms apply. Exact redistribution licence TO VERIFY before republishing. | Working: Nepal GDP 2025. |

## Keyed or unverified (NOT in the no-key chain)

| Source | Provides | Licence / terms | Verification |
|---|---|---|---|
| SEC EDGAR | US company filings | Public APIs, declared User-Agent required, no key. | NOT VERIFIED from this environment (HTTP 403 despite declared User-Agent). Fetcher left in place, not claimed verified, not retried aggressively. TO VERIFY from a non-VM network. |
| FRED, Alpha Vantage, Twelve Data, FMP | Macro / prices (free tiers) | Each provider's own free-tier terms, under the user's own key only. | No key supplied, none invented. Keys come from the environment only (`toolkit/data.py`), never from this repo. |

## NEPSE — no live feed (data finding, not an access blocker)

No documented, free, public NEPSE OHLCV API was verified as of 28 Oct 2026.
NEPSE's public homepage renders only "Loading..." with no prices; its data
sits behind an internal token flow that third-party libraries reverse-
engineer. That flow is deliberately NOT wired in: it is undocumented
access that can be blocked or changed, and depending on it is exactly
the prominent-failure mode this programme refuses to build on.

Legitimate NEPSE data paths, in order:
1. **User-supplied CSV**, validated by the scanner loaders in
   `nepali-capital-market-research/scanner/data.py` (see that repo's
   `guides/nepal-data-handling.md` for the exact format).
2. **Paid, licensed NEPSE Market Data Provider** (data.smartwealthpro.com)
   — the owner's funding decision only. It is NOT started, and no
   pricing is quoted here.

## Rules every fetcher follows

- Sources are tried one at a time, in order, with **no retries and no
  parallel hammering**. A 403 or 429 is recorded as a failure and the
  chain moves on — it is never worked around, and the chain must not
  be re-run in a tight loop.
- **Stale is never fresh.** A series older than `max_age_days`
  (default 7) is skipped by the fallback chain and rejected by the
  publish gate (`toolkit/data_layer.py: publish_check`). On a failed
  scheduled run the previous good report is kept and the run is
  labelled STALE — stale output is never published as if it were fresh.
- **Corrupt is never repaired.** A payload that fails schema validation
  (non-ISO dates, non-increasing dates, non-positive closes) is
  rejected whole; a partial series is never published.
- **One symbol failing never kills a run.** Its error is recorded, its
  previous report is kept, and the other symbols still publish.
- **No credentials anywhere.** No key, token, or secret is stored in
  this repo, in any workflow, or in any generated report.
