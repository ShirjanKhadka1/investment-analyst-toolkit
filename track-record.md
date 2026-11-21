# Track record — public calls, scored after the fact

> Research only, not investment advice. Every call below was logged before its outcome was known and settled after its 5-trading-day horizon. Losses stay in the record.

- Calls settled (pooled): **7**
- Pooled hit rate: **withheld until 100 settled calls** (currently n=7). Wilson interval: [35.9%, 91.8%].
- Pooled naive baseline: n=7, 3 hits, 95% Wilson [15.8%, 75.0%]
- Pooled buy-and-hold baseline: n=7, 1 hits, 95% Wilson [2.6%, 51.3%]

**Tickers are correlated, so the pooled interval understates uncertainty:** seven tickers moving together are not seven independent trials. Read the pooled interval as a lower bound on the uncertainty, not as if the calls were independent.

## Per ticker

| Ticker | Model | Naive | Buy-and-hold |
|---|---|---|---|
| AAPL | n=1, 0 hits, 95% Wilson [0.0%, 79.3%] | n=1, 0 hits, 95% Wilson [0.0%, 79.3%] | n=1, 0 hits, 95% Wilson [0.0%, 79.3%] |
| BTC-USD | n=1, 1 hits, 95% Wilson [20.7%, 100.0%] | n=1, 1 hits, 95% Wilson [20.7%, 100.0%] | n=1, 0 hits, 95% Wilson [0.0%, 79.3%] |
| EURUSD=X | n=1, 1 hits, 95% Wilson [20.7%, 100.0%] | n=1, 1 hits, 95% Wilson [20.7%, 100.0%] | n=1, 0 hits, 95% Wilson [0.0%, 79.3%] |
| ISF.L | n=1, 0 hits, 95% Wilson [0.0%, 79.3%] | n=1, 0 hits, 95% Wilson [0.0%, 79.3%] | n=1, 0 hits, 95% Wilson [0.0%, 79.3%] |
| NIFTYBEES.NS | n=1, 1 hits, 95% Wilson [20.7%, 100.0%] | n=1, 1 hits, 95% Wilson [20.7%, 100.0%] | n=1, 1 hits, 95% Wilson [20.7%, 100.0%] |
| SPY | n=1, 1 hits, 95% Wilson [20.7%, 100.0%] | n=1, 0 hits, 95% Wilson [0.0%, 79.3%] | n=1, 0 hits, 95% Wilson [0.0%, 79.3%] |
| VT | n=1, 1 hits, 95% Wilson [20.7%, 100.0%] | n=1, 0 hits, 95% Wilson [0.0%, 79.3%] | n=1, 0 hits, 95% Wilson [0.0%, 79.3%] |

Per-ticker rates are reported with n and Wilson intervals; a point-estimate percentage appears only in the pooled headline, once the 100-call minimum is met.

Scored from the decision log on the data branch (`data/track-record`) — never edited by hand.
