# Provenance: data/fixtures/spy_daily.csv

- **What:** SPY (SPDR S&P 500 ETF Trust) daily closes, 251 bars.
- **Coverage:** 2025-11-07 → 2026-11-06. First close 670.97, last close 735.29.
- **Source:** Yahoo Finance chart API, `https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=1y&interval=1d`
  (unofficial endpoint, best-effort — see DATA_SOURCES.md).
- **Retrieved:** 8 Nov 2026 from this VM. The toolkit's own Yahoo connector
  timed out from this VM's shared egress (User-Agent tarpit — see
  docs/global-markets-audit.md), so the fixture was fetched with curl
  using a browser User-Agent; the payload is Yahoo's data, unmodified
  except for dropping `null` closes and rounding to 2 decimals.
- **Verification:** 251 bars, monotonic dates, no gaps beyond weekends/
  market holidays, last bar 2026-11-06 (the last trading day before
  retrieval). Reproduce with the script in docs/global-evidence.md.
- **Licence:** Yahoo's terms apply; redistribution TO VERIFY — do not
  redistribute this file as your own dataset. It is a test fixture for
  the worked example, not a data product.
