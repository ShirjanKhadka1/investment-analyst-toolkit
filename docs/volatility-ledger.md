# Forward volatility ledger (Round E, E.2)

Research only. Never a buy/sell/hold recommendation. A forecast
in this ledger is a logged prediction, not a result — and no
result is claimed here until the protocol's threshold is met.

## Protocol

Pre-registered in `volatility-ledger-protocol.md` **before any
ledger row existed**: the exact 7-ticker watchlist (SPY, AAPL,
VT, NIFTYBEES.NS, ISF.L, EURUSD=X, BTC-USD), the backtest's
three models unchanged (naive trailing-20-day realised variance;
EWMA λ = 0.94; GARCH-lite α = 0.08, β = 0.90), one forecast row
per (symbol, forecast date, model) from the last COMPLETE bar
(the track record's `bar_complete` rule), scores as separate
appended rows, QLIKE primary and MSE on variance secondary, and
the claim threshold of **100 scored observations per symbol**.
Each forecast uses the trailing 253 closes (252 log returns) up
to and including the origin bar. Implementation:
`toolkit/volatility_ledger.py`; runner:
`scripts/volatility_ledger_daily.py`; verifier:
`scripts/verify_volatility_ledger.py`.

Reproduce the runner (offline except for the price fetch):

```text
python3 scripts/volatility_ledger_daily.py --ledger <path> --missed <path>
```

## Ledger state — no claims yet

- **Branch:** `data/volatility-ledger` (its own data branch —
  never `data/track-record`). Files: `volatility-ledger.jsonl`
  plus `missed_days.jsonl`.
- **Forecasts logged:** 0. **Scores settled:** 0.
- **Threshold met:** no — 0 of 100 scored observations per
  symbol. No verdict ("beats baseline" / "no edge") is
  published before every symbol reaches 100 scored
  observations. Before that, this doc reports counts only.
- **Backtest (for context, not a ledger result):** the 2026-11-14
  backtest on SPY/AAPL/VT found EWMA −8.0264 and GARCH-lite
  −8.0643 pooled mean QLIKE vs naive −7.9652
  (`docs/volatility-forecast-model-card.md`). A lower past
  out-of-sample QLIKE is not a guarantee of future accuracy,
  and the backtest's numbers do not count toward this ledger's
  threshold.

## Workflow

File `.github/workflows/daily-volatility-ledger.yml` is **staged,
not deployed**: it lives at
`ops/ready-for-browser/daily-volatility-ledger.yml` in the ops
repo, and the owner deploys it himself (copy into
`.github/workflows/` and enable in Actions). Schedule: daily,
22:30 UTC — after the NYSE close (21:00 UTC) and after the daily
track record (22:00 UTC). Steps: checkout, setup Python 3.12,
seed the local ledger and missed-days files from the
`data/volatility-ledger` branch (genesis when the branch or the
files do not exist yet), run the daily ledger step with
`--publish`, verify the branch history is append-only. No
provisional mode: a manual dispatch before the close records
missed days, it never logs a forecast from a partial bar.

### Dispatching during a session

The scheduled run is 22:30 UTC — after the NYSE close (21:00
UTC), so every session has closed and all seven tickers log a
forecast. A manual dispatch earlier in the day does NOT refuse
the run, and it never logs a forecast from a partial bar. What
happens per ticker depends on whether its session has closed at
dispatch time (session closes: NSE 10:00 UTC, LSE 16:30 UTC,
NYSE 21:00 UTC):

- Before 10:00 UTC: NIFTYBEES.NS, ISF.L, SPY, AAPL and VT each
  record a missed day (their bars are incomplete); EURUSD=X and
  BTC-USD still log a forecast, from the previous complete bar
  (their in-progress bar is dropped).
- 10:00–16:30 UTC: NIFTYBEES.NS logs (NSE closed); ISF.L, SPY,
  AAPL and VT record missed days; EURUSD=X and BTC-USD log from
  the previous complete bar.
- 16:30–21:00 UTC: NIFTYBEES.NS and ISF.L log; SPY, AAPL and VT
  record missed days; EURUSD=X and BTC-USD log from the previous
  complete bar.
- After 21:00 UTC: all seven log.

A missed day is never filled in and never forecast from. The
only dispatch that refuses the whole run is an UNKNOWN session
state (no time-zone database on the runner) — never guessed.

## Notes on the ledger's conventions

- `annualised_vol` uses the same 252-day convention for every
  symbol, including BTC-USD and EURUSD=X. A 24-hour market has
  no 252-day session year; the figure is a comparability
  convention, not a claim about sessions.
- `forecast_date` is the nominal next calendar day after the
  origin bar. The forecast is defined as the variance of the
  first bar after the origin bar; when that bar lands later
  (weekend / holiday), the score row's `actual_date` is that
  bar's real date.
- A missed day is never filled in and never forecast from. A
  fetch failure is a missed day, not a crash. A forecast is
  scored at most once; a correction is a new forecast row,
  never an edit or a second score.
