# Event study — real-data model card (Round E, E.1)

Research only. Never a buy/sell/hold recommendation. A past
abnormal return around an earnings filing is not a guarantee
of future returns.

## Protocol

Pre-registered in `docs/real-data-eval-protocol.md` **before**
this evaluation ran. Universe: 30 liquid US large caps, chosen
from today's liquid names — **survivorship bias: past losers
that delisted are absent, so every result here is
survivorship-conditioned, not unbiased history.** Events are
10-Q filing dates in 2025 (SEC EDGAR submissions), one per
symbol: the latest 2025 10-Q with at least 280 prior price
bars, first 10 alphabetical symbols that qualify. Market model
vs SPY (estimation window [−250, −31], event window [−5, +5]),
mean-adjusted fallback when dates differ. Significance is the
bootstrap p (10,000 resamples, seed 20261114) on the CAAR.
Reproduce with:

```text
EDGAR_CONTACT_EMAIL=<contact> python3 scripts/evaluate_real_data.py --refresh --only events
```

The cache is `data/real-data-eval-2026-11-15.json`.

## Run

- **Run date:** 2026-11-15. **Events:** 10 (AAPL, AMZN, AVGO,
  GOOGL, LLY, META, MSFT, NVDA, TSLA, V).
- **Implementation:** `toolkit/event_study.py` (stdlib only);
  evaluation in `scripts/evaluate_real_data.py`.

## Results — aggregate

| CAAR | t-stat | Bootstrap p | Significant | Small-n caveat |
| --- | --- | --- | --- | --- |
| +1.45% | 0.44 | 0.6425 | No | Yes (n = 10) |

No significant aggregate abnormal return around 10-Q filing
dates: CAAR +1.45% with a bootstrap p of 0.6425. With n = 10
the small-n caveat applies — the aggregate is a weak test,
and the two individually significant events below are
single-event results, not findings.

## Results — per event

| Symbol | Event date | Model | CAR | t-stat | Bootstrap p | Significant |
| --- | --- | --- | --- | --- | --- | --- |
| AAPL | 2025-08-01 | market | +7.79% | 1.78 | 0.077 | No |
| AMZN | 2025-10-31 | market | +10.61% | 2.28 | 0.020 | Yes |
| AVGO | 2025-09-10 | market | +7.83% | 0.82 | 0.335 | No |
| GOOGL | 2025-10-30 | market | +10.70% | 1.93 | 0.063 | No |
| LLY | 2025-10-30 | market | +15.21% | 1.83 | 0.082 | No |
| META | 2025-10-30 | market | −17.38% | −3.25 | 0.005 | Yes |
| MSFT | 2025-10-29 | market | −3.19% | −0.87 | 0.319 | No |
| NVDA | 2025-11-19 | market | −6.43% | −0.88 | 0.338 | No |
| TSLA | 2025-10-23 | market | −5.57% | −0.48 | 0.600 | No |
| V | 2025-07-30 | market | −5.13% | −1.46 | 0.133 | No |

All 10 events used the market model (SPY dates align with
every event symbol's). No event needed the mean-adjusted
fallback.
