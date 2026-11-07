# Test fixtures — provenance

Every fixture in this directory is either cut from a real public
response (with its source URL and fetch date below) or labelled
SYNTHETIC. Never commit raw data whose source forbids redistribution
— derived analytics only. These fixtures are test inputs, not a
republished dataset.

## Cut from real public responses (fetched 2026-11-07, this environment)

| Fixture | Source URL | Fetched | What it is |
|---|---|---|---|
| `yahoo_aapl_chart_2026-11-07.json` | https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=5d&interval=1d | 2026-11-07 | Full Yahoo chart response for AAPL, 5 trading days 2026-11-02–2026-11-06 (closes 338.32, 331.90, 349.51, 344.17, 342.37). Saved unmodified. |
| `ecb_eurofxref_2026-11-06.xml` | https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml | 2026-11-07 | Full ECB eurofxref daily document, fixing dated 2026-11-06, 29 currencies. Saved unmodified. |
| `coingecko_btc_market_chart_2026-11-07.json` | https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=1 | 2026-11-07 | Full CoinGecko market_chart response for bitcoin, 288 points covering 2026-11-06–2026-11-07. Saved unmodified. |

## Synthetic (labelled, invented for tests — never real market data)

| Fixture | What it is |
|---|---|
| `nepse_demo_bars_synthetic.csv` | 25 synthetic NEPSE-style daily bars (date,open,high,low,close,volume), invented prices, symbol DEMO. For CSV-connector tests only. |

## Redistribution notes

- Yahoo: redistribution terms TO VERIFY — these fixtures are test
  inputs inside this repo, not a republished dataset; derived
  analytics only.
- ECB: official reference rates, published by the ECB; attribute the
  ECB when quoting them.
- CoinGecko / World Bank: keyless public APIs; redistribution terms
  TO VERIFY; derived analytics only.
