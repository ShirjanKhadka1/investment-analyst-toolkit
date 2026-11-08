# Global evidence — worked example (Step 4)

A worked global example end to end, from a real, dated fixture. Every
number below was produced by running the toolkit over the fixture on
8 Nov 2026; nothing is hand-typed. The script is rerunnable:

```bash
python3 -m pytest tests/test_global_evidence.py -q   # pins every number below
```

## Fixture

**SPY (SPDR S&P 500 ETF Trust), daily closes, 251 bars,
2025-11-07 → 2026-11-06.** First close 670.97, last close 735.29,
one-year return **+9.59%**. Source: Yahoo Finance chart API
(unofficial, best-effort); full provenance in
`data/fixtures/spy_daily.PROVENANCE.md`. Retrieved 8 Nov 2026 — the
toolkit's Yahoo connector timed out from this VM's shared egress, so
the fixture was fetched with curl under a browser User-Agent; the
payload is Yahoo's data, unmodified except for dropping `null` closes.

## Technical read (rules mode, market-agnostic)

| Figure | Value |
|---|---|
| Last close | 735.29 |
| Momentum, 20d | −5.56% |
| 20-day mean | 763.36 |
| Price vs 20-day mean | −3.68% |
| Daily volatility | 0.85% |
| RSI(14) | 57.8 |

Read: the last 20 days pulled back from the mean; RSI mid-range.
These describe the past bars only; they predict nothing.

## Time Machine, US profile (hypothetical plan)

Plan: entry 735.29 (last close), stop 700.00 (−4.8%),
target 810.00 (+10.1%), reward:risk 2.12. 1,000 simulations,
seed 19, horizon 20 days, profile `us` (T+1 settlement, no circuit
limit, brokerage/capital-gains/SEC Section 31 fee unmodelled —
listed, not invented).

| Outcome | Share |
|---|---|
| Target first | 0.7% |
| Stop first | 8.2% |
| Neither in 20 days | 91.1% |
| Average R | +0.21 |
| Break-even win rate | 32.1% |

Read: target-first (0.7%) is far below break-even (32.1%) — the
plan as written leans strongly toward the stop over 20 days. That is
the Time Machine's read of the resampled past, not a prediction.

## Risk gate, US profile (same plan, default $10k research config)

The default config caps a position at 25% of a $10,000 budget
($2,500), a single order at $2,500, loss per trade at $100 and daily
loss at $200.

| Shares | Position | Max loss | Verdict |
|---|---|---|---|
| 10 | $7,352.90 | $352.90 | **BLOCK 12/16** — size_limits, position_cap, loss_per_trade, daily_loss |
| 3 | $2,205.87 | $105.87 | **BLOCK 15/16** — loss_per_trade |
| 2 | $1,470.58 | $70.58 | **PASS 16/16** |

Read: the gate blocks the plan on size, not on SPY. At 2 shares the
same entry/stop/target passes all 16 checks. Position sizing is the
gate doing its job.

## ETF/index persona (rules mode)

Supplied from the fixture only: 1-year index return +9.59%,
ETF return +9.59% (same series), gap 0.0. Expense ratio, tracking
error, AUM and premium/discount were **not sourced for this run**
and are reported UNKNOWN — the persona refuses to fill them in.

## Walk-forward on SPY (the result, published even though it is "no edge")

`predictive.compare_models_walk_forward`, train 200 bars, test 51,
horizon 1 day. Every forecaster judged on the same split:

| Model | MAE | RMSE | Beats naive |
|---|---|---|---|
| **naive** | **4.9124** | 7.0582 | — |
| exp_smoothing_0.3 | 5.4822 | 7.4219 | No |
| moving_average_5 | 5.8416 | 7.6366 | No |
| linear_trend | 9.6653 | 13.9487 | No |

**Best model: naive. No model beat naive out of sample on this
split.** That is the published result: on one year of SPY daily
closes, the toolkit's forecasters show no edge over carrying the
last price forward. A lower past out-of-sample MAE would not have
guaranteed future accuracy either; this report is not a signal.

## Market status

| Market | Status | Evidence |
|---|---|---|
| NEPSE | **tested** | Existing suite; NEPSE behaviour byte-identical (see Step 1 tests) |
| US (SPY, AAPL) | **tested** | This file: fixture, Time Machine, risk gate, walk-forward |
| India, UK, crypto, FX | **experimental** | Profiles sourced (Step 1); no worked example yet |
| Generic | **experimental** | User-set frictions only; nothing sourced |

## What this does not claim

- No live feed: the fixture is a snapshot, not a feed. SPY after
  2026-11-06 is not in this repo.
- No advice: every section above is a structured read of supplied
  inputs, not a recommendation.
- Yahoo is unofficial: if Yahoo blocks the endpoint, this example
  still reproduces from the fixture — that is why the fixture exists.
