# Direction rules — pre-registered (Round 4, 2026-11-08)

**PRE-REGISTERED.** These rules are fixed here before any call is
logged under them. A rule turns a point-in-time close series into a
direction — UP, DOWN, or no call. Nothing in this file was tuned on
live data: the windows (50, 20) are the standard textbook windows,
fixed at registration on 2026-11-08. Changing a parameter is a new
rule version, never a silent edit.

## Why rules, not personas

The Round 3 spec left an activation blocker: the rules team returns
structured sections with discussion flags, not a direction verdict,
so no rule mapped a persona's sections to UP / DOWN / neutral.
This file closes that blocker by changing the mechanism, not by
inventing a verdict: **direction comes from these pre-registered
price rules, not from a persona's discussion output.** The
`persona` recorded on a call is attribution — which watchlist
persona owns the ticker — not the source of the direction. Persona
sections remain context for the thesis; they never decide a call.

## The rules

| rule_id | version | Definition | Needs |
|---|---|---|---|
| `sma50_trend` | 1.0.0 | UP when the decision-day close is above the 50-day simple moving average of closes (decision day included); DOWN when below; no call when equal or when fewer than 50 closes are visible. | ≥ 50 closes |
| `mom20_sign` | 1.0.0 | UP when the 20-trading-day return (decision-day close / close 20 trading days earlier − 1) is positive; DOWN when negative; no call when zero or when fewer than 21 closes are visible. | ≥ 21 closes |

Parameters (fixed at registration, never refit):

- `sma50_trend`: `window = 50`.
- `mom20_sign`: `window = 20`.

## Per-ticker assignment (registered 2026-11-08)

| Ticker | Rule | Why this rule |
|---|---|---|
| SPY | `sma50_trend` | Equity trend |
| AAPL | `sma50_trend` | Equity trend |
| VT | `sma50_trend` | Equity trend |
| NIFTYBEES.NS | `sma50_trend` | Equity trend |
| ISF.L | `sma50_trend` | Equity trend |
| EURUSD=X | `mom20_sign` | FX has no venue-meaningful moving-average level |
| BTC-USD | `mom20_sign` | Crypto trades 24/7; momentum sign, not a level |

This assignment is a registration choice, not a fitted result. It
is enforced in code (`toolkit/direction_rules.py`, `TICKER_RULES`)
and mirrored here; the two must agree.

## What every logged call records

- `rule_id` — e.g. `sma50_trend`.
- `rule_version` — e.g. `1.0.0`.
- `rule_code_hash` — the git blob hash (`git hash-object`) of
  `toolkit/direction_rules.py` at the time the call was logged, so
  any later edit to a rule is detectable from the call record
  alone. Computed in code, never typed.
- `confidence` — **0.5, a neutral prior, not a measured
  probability.** The rules state no probability; the recorded 0.5
  says "no confidence claimed". Brier scores on it are not
  meaningful and must not be quoted as calibration.
- `prev_5d_return` — the return over the 5 trading days before the
  call date, for the naive baseline (None when fewer than 6 closes
  are visible; then the naive baseline has no outcome for that
  call).

## Call schedule

- **One call per ticker per 5 trading days, non-overlapping.** A new
  call is due only when the previous call's horizon has passed
  (previous decision date + 5 trading days ≤ today). A ticker with
  an open call inside its horizon gets no new call.
- **Point-in-time only.** A call uses only bars up to and including
  its decision date, read through `toolkit/pit_store.py`
  (`bars_as_of`). The daily run happens after the close, so the
  decision-day bar is visible to the decision — and no later bar
  is. A look-ahead test (`tests/test_track_record.py`) fails if any
  future bar leaks into a direction.
- **Entry = close of the decision day.** **Settle = close of the
  5th trading day after** the decision day, on the ticker's own
  profile calendar (see `toolkit/trading_calendar.py`). The
  settlement date counts a special session when a bar exists for
  it — see the special-session rule below.

## Special-session rule

A session counts as a trading day when the exchange traded and
published a close — that is, when a bar for that date exists in the
fetched series. A bar dated on a non-standard day therefore counts,
and the profile weekday calendar never removes a real bar. A
calendar trading day with no bar (a missed fetch) never counts: it
is recorded as a missed day and settlement waits instead of
silently using the next bar's close.

Worked example (the bar that forced the rule): NIFTYBEES.NS has a
Yahoo bar dated **2026-11-08, a Sunday** — NSE's Diwali Muhurat
session (Diwali 2026 fell on Sunday 8 November 2026; NSE circular
NSE/CMTR/76941, 19 Oct 2026; session 2:20–3:30 PM IST). India trades
Monday–Friday, so the profile calendar alone would skip it. Under
this rule the bar counts. For a call decided Friday 2026-11-06, the
trading days after are 8 Nov (Muhurat session), 9 Nov, 11 Nov
(10 Nov is the Diwali-Balipratipada holiday —
`data/holidays/india.json`), 12 Nov, 13 Nov — so the call settles
**13 November 2026**, not 16 November. Without the 8 Nov bar the
settlement date would be 16 November (9, 11, 12, 13, 16 Nov).
`tests/test_track_record.py` proves the settlement code follows
the rule for that bar.
