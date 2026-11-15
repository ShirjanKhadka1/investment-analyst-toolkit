# Cross-sectional ranking — real-data model card (Round E, E.1)

Research only. Never a buy/sell/hold recommendation. A
past decile spread is not a guarantee of future returns.

## Protocol

Pre-registered in `docs/real-data-eval-protocol.md` **before**
this evaluation ran. Universe: 30 liquid US large caps, chosen
from today's liquid names — **survivorship bias: past losers
that delisted are absent, so every result here is
survivorship-conditioned, not unbiased history.** Prices from
Yahoo (5y, 1d); fundamentals from EDGAR point-in-time (the
latest 10-K filed on or before each ranking date, no
look-ahead). 12 monthly rebalances, last trading day of each
month Dec 2024–Nov 2025. Momentum is 6-minus-1 (126-bar
formation, 21-bar skip); value is earnings yield + book to
price; quality is ROE + gross margin; composite is the mean of
the available component ranks. Forward window 21 bars.
Costs: generic 0.20% round trip (0.10% each way). Spreads are
top-decile minus bottom-decile net forward returns, and a rule
is "edge" only if its mean spread is positive AND the 95%
paired-bootstrap CI (10,000 resamples, seed 20261114) lies
entirely above zero. Reproduce with:

```text
EDGAR_CONTACT_EMAIL=<contact> python3 scripts/evaluate_real_data.py --refresh --only ranking
```

The raw cache is NOT committed (data policy: derived metrics only). The committed record is `data/real-data-eval-2026-11-15/derived-metrics.json` plus `data/real-data-eval-2026-11-15/manifest.json` (source data with its SHA-256); re-fetch the raw data locally with `scripts/fetch_real_data_eval.py`, then re-run `scripts/evaluate_real_data.py`.

## Run

- **Run date:** 2026-11-15. **Universe:** AAPL, MSFT, NVDA,
  GOOGL, AMZN, AVGO, META, TSLA, JPM, BAC, GS, V, MA, UNH,
  JNJ, LLY, XOM, CVX, WMT, HD, PG, COST, KO, DIS, NFLX, CRM,
  ORCL, AMD, CAT, ADBE (30 caps).
- **Ranking dates:** 2024-12-31, 2025-01-31, 2025-02-28,
  2025-03-31, 2025-04-30, 2025-05-30, 2025-06-30, 2025-07-31,
  2025-08-29, 2025-09-30, 2025-10-31, 2025-11-28.
- **Mean baseline net return:** +1.16% per 21-bar window
  (equal-weighted, net of costs).
- **Implementation:** `toolkit/cross_sectional.py` (stdlib
  only); evaluation in `scripts/evaluate_real_data.py`.

## Results — decile spreads (net, per 21-bar window)

| Rule | Mean spread | 95% CI | Verdict |
| --- | --- | --- | --- |
| momentum | −0.83pp | [−5.40pp, +3.52pp] | no edge |
| value | −1.46pp | [−5.92pp, +2.89pp] | no edge |
| quality | −3.01pp | [−6.88pp, +0.71pp] | no edge |
| composite | −3.30pp | [−8.04pp, +1.65pp] | no edge |

No rule shows an edge: every mean spread is negative and
every 95% CI includes zero. The monthly spreads are noisy
(momentum ranges −12.36pp to +16.20pp across the 12
rebalances), which is what 12 observations look like — the
CIs are wide because n = 12.

### Monthly spreads

| Date | Momentum | Value | Quality | Composite |
| --- | --- | --- | --- | --- |
| 2024-12-31 | +5.80pp | +12.23pp | −4.03pp | +6.49pp |
| 2025-01-31 | −8.27pp | +3.81pp | +4.13pp | −9.17pp |
| 2025-02-28 | −2.29pp | +5.44pp | −1.97pp | −4.33pp |
| 2025-03-31 | +16.20pp | −2.43pp | −2.85pp | +16.04pp |
| 2025-04-30 | −11.89pp | −1.54pp | −17.15pp | −12.07pp |
| 2025-05-30 | −1.24pp | +7.68pp | +0.18pp | −4.12pp |
| 2025-06-30 | +0.05pp | −7.68pp | −4.04pp | −5.26pp |
| 2025-07-31 | −12.36pp | +2.93pp | +10.66pp | −13.15pp |
| 2025-08-29 | +6.77pp | −15.64pp | −9.67pp | −12.31pp |
| 2025-09-30 | +6.51pp | −12.28pp | −10.85pp | +5.80pp |
| 2025-10-31 | −7.49pp | −7.54pp | +0.21pp | −9.91pp |
| 2025-11-28 | −1.72pp | −2.51pp | −0.76pp | +2.41pp |

## Concept coverage (what was missing, per symbol)

A concept missing as of a ranking date excludes that
component for that symbol at that rebalance — no value is
ever invented or defaulted.

- **GrossProfit missing at all 12 dates** (no GrossProfit
  concept in their filings — banks, payment networks,
  energy, and several others report no gross profit):
  BAC, CVX, DIS, GOOGL, GS, JPM, LLY, MA, META, PG, UNH, V,
  WMT, XOM. Their quality score uses ROE only.
- **No shares outstanding in EDGAR at all** (no
  `dei:EntityCommonStockSharesOutstanding` concept), so no
  market cap and no value/quality components at any
  rebalance — momentum only: GOOGL, META.
- **XBRL tag fallbacks actually used** (the freshest
  candidate wins; the card records the tag): Microsoft's
  `Revenues` ends 2010-12-31, so its revenue comes from
  `RevenueFromContractWithCustomerExcludingAssessedTax`;
  Amazon files no `Revenues` at all; Goldman Sachs files no
  top-line revenue tag — its revenue is
  `RevenuesNetOfInterestExpense`; Visa's
  `StockholdersEquity` stops in 2011, so its equity comes
  from
  `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`.
- **XOM ticker mapping overridden:** the SEC's own
  company_tickers.json maps XOM to CIK 2115436 ("ExxonMobil
  Holdings Corp"), whose submissions endpoint 404s; the real
  filer EXXON MOBIL CORP (CIK 34088) has an empty ticker list
  and is absent from that table entirely. XOM is mapped to
  34088 by an explicit override in the eval script.
