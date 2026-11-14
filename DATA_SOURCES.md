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

Re-verified 7 Nov 2026 (Phase 2, from this environment via the
toolkit's own fetchers): Yahoo AAPL fresh through 2026-11-06;
CoinGecko BTC through 2026-11-07; ECB fixing dated 2026-11-06
(29 currencies); World Bank Nepal GDP through 2025
($45,489,810,283.11). Stooq REMOVED 8 Nov 2026 (third failed verification — see docs/global-markets-audit.md). Re-implemented 14 Nov 2026 (Round 7.1): parser + fetcher tested offline — NOT in the live chain; the live chain stays Yahoo-only until the runner probe (staged at ops/ready-for-browser/stooq-ci-probe.yml) passes. From this VM Stooq still answers with a JavaScript-challenge HTML page, not CSV (checked 14 Nov 2026).

## No-key chain (used by `toolkit/free_data.py` and `toolkit/connectors.py`)

| Source | Provides | Licence / terms | Verification (28 Oct 2026) |
|---|---|---|---|
| Yahoo Finance chart API | Stock/ETF daily price history — UNOFFICIAL, best-effort | Public endpoint, no key. No licence published on the endpoint; use under Yahoo's terms. Redistribution TO VERIFY. Yahoo publishes no public API for this endpoint; it can be rate-limited/tarpitted by User-Agent/IP. | Working: AAPL, 251 points, last date 2026-10-27, fresh. Re-verified 7 Nov 2026: AAPL fresh through 2026-11-06. 8 Nov 2026: SPY/7203.T/EURUSD/BTC-USD all timed out from this VM's shared egress (tarpit by User-Agent — browser UA works); AAPL cached series still usable. Works from GitHub Actions runners. |
| Stooq daily CSV | NOT LIVE (Round 7.1) — provider + parser re-implemented, tested offline with a Stooq-format fixture; live chain stays Yahoo-only until the runner probe passes | Public CSV endpoint, no key. Redistribution TO VERIFY — derived analytics only. | REMOVED 8 Nov 2026 (third failed verification: HTML instead of CSV on 28 Oct and 7 Nov 2026; connection timeout 8 Nov 2026). Re-implemented 14 Nov 2026 (Round 7.1); probe staged at ops/ready-for-browser/stooq-ci-probe.yml, UNVERIFIED. From this VM Stooq still answers with a JavaScript-challenge HTML page, not CSV (checked 14 Nov 2026). |
| CoinGecko keyless public API | Crypto daily prices (market chart) | Documented keyless API; shared IP pool, roughly 10–30 calls/min. Respect the limit; never work around a rate limit. | Working: BTC price + 91 daily points, fresh. Re-verified 7 Nov 2026: BTC through 2026-11-07 (last price $75,912). |
| ECB euro reference rates | Daily EUR reference fixings (latest only) | Published by the ECB as official reference rates; attribute the ECB. A fixing is not a tradable quote and not a series. | Working: 2026-10-27 fixing, 29 currencies. Re-verified 7 Nov 2026: 2026-11-06 fixing, 29 currencies (USD 1.1165). |
| World Bank API v2 | Macro indicators by country/year | World Bank Open Data terms apply. Exact redistribution licence TO VERIFY before republishing. | Working: Nepal GDP 2025. Re-verified 7 Nov 2026: Nepal GDP 2025, $45,489,810,283.11. |
| SEC EDGAR (toolkit/edgar.py) | US fundamentals (XBRL company facts, submissions, ticker->CIK) | Public APIs, declared User-Agent required, no key. Fair access: max 10 req/s; module throttles to 5/s. | Verified 8 Nov 2026: data.sec.gov submissions for Apple (CIK 320193) returned 200, most recent filing 2026-11-06. Ticker table (www.sec.gov) intermittently tarpitted from this VM — pass the CIK directly if it times out. |

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

- Sources are tried one at a time, in order. Retries are bounded
  and transient-only (`toolkit/connectors.py`: timeouts, connection
  errors and HTTP 5xx, max 3 attempts with backoff). A 403 or 429 is
  recorded as a failure and the chain moves on — it is never worked
  around, and the chain must not be re-run in a tight loop. (The
  older no-key chain in `toolkit/free_data.py` still does no retries
  at all; the retry policy lives only in the connector chain.)
- **Freshness SLAs (Phase 2, `toolkit/connectors.py`):** daily bars
  and FX fixings 1 day; World Bank annual indicators 1 year. A
  series older than its SLA is STALE: flagged in `data_health.json`
  and in the terminal dashboard, never published as fresh.
  `scripts/data_health.py` writes the per-source report;
  `scripts/refresh_derived.py` refreshes `data/derived/*.json` with
  an `as_of` date and a stale flag. Both scripts exit non-zero with
  a clear error on failure.
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
