# Track record — public calls, scored after the fact

> Research only, not investment advice. Every call below was logged before its outcome was known and settled after its 5-trading-day horizon. Losses stay in the record.

- Calls settled (pooled): **2**
- Pooled hit rate: **withheld until 100 settled calls** (currently n=2). Wilson interval: [34.2%, 100.0%].
- Pooled naive baseline: n=2, 2 hits, 95% Wilson [34.2%, 100.0%]
- Pooled buy-and-hold baseline: n=2, 0 hits, 95% Wilson [0.0%, 65.8%]

**Tickers are correlated, so the pooled interval understates uncertainty:** seven tickers moving together are not seven independent trials. Read the pooled interval as a lower bound on the uncertainty, not as if the calls were independent.

## Per ticker

| Ticker | Model | Naive | Buy-and-hold |
|---|---|---|---|
| BTC-USD | n=1, 1 hits, 95% Wilson [20.7%, 100.0%] | n=1, 1 hits, 95% Wilson [20.7%, 100.0%] | n=1, 0 hits, 95% Wilson [0.0%, 79.3%] |
| EURUSD=X | n=1, 1 hits, 95% Wilson [20.7%, 100.0%] | n=1, 1 hits, 95% Wilson [20.7%, 100.0%] | n=1, 0 hits, 95% Wilson [0.0%, 79.3%] |

Per-ticker rates are reported with n and Wilson intervals; a point-estimate percentage appears only in the pooled headline, once the 100-call minimum is met.

Scored from the decision log on the data branch (`data/track-record`) — never edited by hand.
