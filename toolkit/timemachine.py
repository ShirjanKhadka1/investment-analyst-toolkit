"""Seeded Monte-Carlo + historical replay for a hypothetical exit plan.

Adapted from day-trader-skill's trader-timemachine (MIT licence,
https://github.com/Jakeschincariol/day-trader-skill, commit 3d0f4ef):
same discipline — 10,000 seeded replays, resampling from the asset's
own bars, stop checked before target, break-even verdict bands — but
rebuilt here for DAILY bars (not 5-minute bars) and for this toolkit's
zero-dependency style.

This is a SIMULATION, NOT A PREDICTION. It answers "if the next N days
behaved like past days at the same position in a window, how often
would this exit plan have hit its target before its stop?" — nothing
about what the price will actually do. Resampled history is not the
future; a high target-first share is not a probability of profit.

Method (stated, so it can be checked):
- The supplied series is past bars only. Each bar becomes one daily
  step: from OHLC dicts, the bar's own high/low/close moves relative
  to its open; from a plain closes list, the close-to-close move, with
  the bar's high/low set to max/min(open, close) — no intraday
  excursion is invented.
- A window is `horizon` consecutive steps. The pool for simulated
  step t is the set of steps that sat at position t in each past
  window — today's bars are never in the pool, and a step is never
  drawn from a different position. Fewer than 20 usable windows is
  refused (ValueError): a bootstrap on a handful of windows is noise.
- Each simulated path starts at `entry` and runs `horizon` steps. On
  a bar whose low touches the stop AND whose high touches the target,
  the stop is checked first (the worse fill is assumed).
- Break-even: break_even = 1 / (1 + R), R = (target - entry) /
  (entry - stop). Verdict compares the simulated target-first share
  with break-even: under 5 points apart is "Close to a coin flip",
  5-15 points "Leans toward ...", 15+ points "Strongly toward ...".
- replay_history runs the same rule, with no randomness, on every
  past window of the same length, using that window's actual steps.

Engine 1.2 additions (NEPSE Time Machine upgrade, 2026-11-07):
- ``detect_setups`` / ``detect_setup``: the five named scanner setups
  (breakout_20d, bull_flag, reclaim_ma20, oversold, oversold_bounce),
  reimplemented here from the same rules as ``scanner/setups.py``
  (nepali-capital-market-research, MIT) and ``toolkit/terminal.py`` —
  this module does not import the scanner repo. A setup function may
  also be supplied by the caller.
- ``replay_setup``: the deterministic replay restricted to past
  windows where the named setup had fired at the window's start.
  Every result carries an honest verdict — "edge", "coin flip" or
  "insufficient data" — from stated thresholds (see
  ``honest_verdict``), and every result states
  "simulation, not a prediction".
- ``simulate_nepse``: the seeded Monte-Carlo with NEPSE reality
  applied — daily price moves clipped to the circuit limit in force
  on each bar's date (10% before 2026-04-20, 15% from; Fourth
  Amendment Bylaws, 2082), an exit-liquidity guard that can
  BLOCK a position too large for typical volume, and real trading
  frictions from ``toolkit.nepal_frictions.sourced_nepal_frictions``
  (broker slabs, SEBON fee, DP charge, CGT split; settlement T+2).
  Realised R is reported net of those frictions.
"""
from __future__ import annotations

import datetime as _dt
import math
import random

from .checks import require_finite, require_positive, validate_series
from .market_profiles import get_profile
from .nepal_frictions import (
    UnknownFrictionError,
    realised_gain_after_fees,
    sourced_nepal_frictions,
    trade_cost_report,
)

__all__ = [
    "SETUP_DEFINITIONS",
    "SETUP_NAMES",
    "check_exit_liquidity",
    "detect_setup",
    "detect_setups",
    "honest_verdict",
    "replay_history",
    "replay_setup",
    "simulate",
    "simulate_nepse",
    "simulate_with_profile",
]

DEFAULT_SEED = 19
DEFAULT_N_SIMS = 10000
DEFAULT_HORIZON = 20
MIN_WINDOWS = 20

# Honest-verdict thresholds (stated, so they can be checked):
# - fewer than MIN_HONEST_WINDOWS setup windows: "insufficient data"
#   (a bootstrap on a handful of windows is noise, not evidence);
# - otherwise "edge" only when the simulated target-first share is at
#   least EDGE_MARGIN (10 percentage points) away from break-even;
# - anything in between is a "coin flip".
MIN_HONEST_WINDOWS = 20
EDGE_MARGIN = 0.10

# NEPSE circuit + settlement, from the user-supplied schedule
# (see toolkit/nepal_frictions.py): circuit ±15% in a day,
# settlement T+2 as standard. Re-check against a current
# NEPSE/SEBON notice before relying on either for a real trade.
NEPSE_CIRCUIT_SOURCE = (
    "Fourth Amendment Bylaws, 2082 (SEBON-approved): daily limits "
    "±10% before 2026-04-20, ±15% from; pre-open 2% -> 5% on the "
    "same date (fiscalnepal, 17 Apr 2026; newbusinessage, 20 Apr 2026)")
NEPSE_SETTLEMENT_NOTE = (
    "T+2 (transition date unverified). CDSC Settlement Procedure "
    "(official, cdsc.com.np/cmsettlementprocedure, 2026-11-08) states "
    "T+2 as the standard; the pre-2021 lag is unknown, not T+3. A T+1 "
    "override exists only where a specific broker offers and confirms "
    "it — T+1 is NOT the market standard")
DEFAULT_MAX_PARTICIPATION = 0.10


def _quantile(sorted_values, q):
    """Linear-interpolation quantile (numpy 'linear' method)."""
    if not sorted_values:
        raise ValueError("no values to summarise")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[int(pos)]
    return sorted_values[lo] * (hi - pos) + sorted_values[hi] * (pos - lo)


def _validate_plan(entry, stop, target):
    require_positive("entry", entry)
    require_positive("stop", stop)
    require_positive("target", target)
    if stop >= entry:
        raise ValueError(f"stop ({stop}) must be below entry ({entry})")
    if target <= entry:
        raise ValueError(f"target ({target}) must be above entry ({entry})")
    return float(entry), float(stop), float(target)


def _steps_from_prices(prices):
    """Normalise the input into a list of daily steps.

    Each step is a dict of relative moves for one day:
    {"high": high/open - 1, "low": low/open - 1, "close": close/open - 1}.
    Accepts a list of daily closes (floats) or a list of OHLC dicts
    with keys open/high/low/close. For closes, the first close has no
    preceding bar, so it contributes no step. An OHLC bar may also
    carry a "date" (ISO string or date); when present it is carried
    on the step so the caller can clip by the rule in force on that
    bar's date (NEPSE's circuit limit changed on 2026-04-20).
    """
    if prices is None:
        raise ValueError("prices is required")
    rows = list(prices)
    if not rows:
        raise ValueError("prices is empty")
    if isinstance(rows[0], dict):
        steps = []
        for i, bar in enumerate(rows):
            for key in ("open", "high", "low", "close"):
                if key not in bar:
                    raise ValueError(f"prices[{i}] is missing key {key!r}")
                require_finite(f"prices[{i}][{key}]", bar[key])
            op = float(bar["open"])
            if op <= 0:
                raise ValueError(f"prices[{i}]['open'] must be positive, got {op}")
            step = {
                "high": float(bar["high"]) / op - 1.0,
                "low": float(bar["low"]) / op - 1.0,
                "close": float(bar["close"]) / op - 1.0,
            }
            if "date" in bar:
                raw = bar["date"]
                step["date"] = (raw if isinstance(raw, _dt.date)
                                else _dt.date.fromisoformat(str(raw)))
            steps.append(step)
        return steps
    closes = validate_series("prices", rows, min_length=2)
    if any(c <= 0 for c in closes):
        raise ValueError("prices must all be positive")
    steps = []
    for i in range(1, len(closes)):
        ret = closes[i] / closes[i - 1] - 1.0
        steps.append({"high": max(0.0, ret), "low": min(0.0, ret), "close": ret})
    return steps


def _check_windows(n_steps, horizon):
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise ValueError(f"horizon must be a positive integer, got {horizon!r}")
    n_windows = n_steps - horizon + 1
    if n_windows < MIN_WINDOWS:
        raise ValueError(
            f"need at least {MIN_WINDOWS} usable windows of {horizon} steps, "
            f"got {max(0, n_windows)} from {n_steps} steps — supply a longer "
            "history or a shorter horizon; a bootstrap on a handful of "
            "windows is noise, not evidence")
    return n_windows


def _run_steps(steps, entry, stop, target):
    """Run the exit rule over one sequence of steps starting at `entry`.

    Returns (outcome, exit_price, exit_day, final_close): outcome is
    "target", "stop" or "neither"; exit_day is 1-based, or None when
    neither level was touched. Stop is checked before target on a bar
    touching both.
    """
    price = entry
    for day, step in enumerate(steps, start=1):
        high = price * (1.0 + step["high"])
        low = price * (1.0 + step["low"])
        close = price * (1.0 + step["close"])
        if low <= stop:
            return "stop", stop, day, close
        if high >= target:
            return "target", target, day, close
        price = close
    return "neither", price, None, price


def _verdict(target_first_share, break_even):
    """Verdict bands from the gap between the simulated target-first
    share and break-even, in percentage points."""
    diff_pts = round((target_first_share - break_even) * 100.0, 9)
    if abs(diff_pts) < 5.0:
        return "Close to a coin flip"
    if abs(diff_pts) < 15.0:
        return "Leans toward target" if diff_pts > 0 else "Leans toward stop"
    return "Strongly toward target" if diff_pts > 0 else "Strongly toward stop"


def _summarise(outcomes, final_closes, realised_r, *, seed, n_sims, horizon,
               n_windows, entry, stop, target, extra=None):
    n = len(outcomes)
    target_first = outcomes.count("target") / n
    stop_first = outcomes.count("stop") / n
    neither = outcomes.count("neither") / n
    risk = entry - stop
    reward_risk = (target - entry) / risk
    break_even = 1.0 / (1.0 + reward_risk)
    ordered = sorted(final_closes)
    result = {
        "method": "simulation, not a prediction",
        "seed": seed,
        "horizon": horizon,
        "n_windows": n_windows,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_per_share": risk,
        "reward_risk": reward_risk,
        "break_even": break_even,
        "target_first": target_first,
        "stop_first": stop_first,
        "neither": neither,
        "median_close": _quantile(ordered, 0.5),
        "close_p5": _quantile(ordered, 0.05),
        "close_p95": _quantile(ordered, 0.95),
        "average_R": sum(realised_r) / len(realised_r),
        "verdict": _verdict(target_first, break_even),
    }
    if n_sims is not None:
        result["n_sims"] = n_sims
    if extra:
        result.update(extra)
    return result


def _simulate_from_steps(steps, entry, stop, target, n_sims, seed, horizon):
    """Run the seeded Monte-Carlo over a pre-built step list.

    Returns a dict with outcomes, final_closes, realised_R (gross, in
    units of risk), exit_prices, exit_days and n_windows. The pool
    for simulated step t is the set of steps that sat at position t
    in each past window — never mixed across positions.
    """
    n_windows = _check_windows(len(steps), horizon)
    pools = [[steps[i + t] for i in range(n_windows)] for t in range(horizon)]
    rng = random.Random(seed)
    risk = entry - stop
    outcomes = []
    final_closes = []
    realised_r = []
    exit_prices = []
    exit_days = []
    for _ in range(n_sims):
        path = [rng.choice(pools[t]) for t in range(horizon)]
        outcome, exit_price, exit_day, final_close = _run_steps(
            path, entry, stop, target)
        outcomes.append(outcome)
        final_closes.append(final_close)
        realised_r.append((exit_price - entry) / risk)
        exit_prices.append(exit_price)
        exit_days.append(exit_day)
    return {"outcomes": outcomes, "final_closes": final_closes,
            "realised_r": realised_r, "exit_prices": exit_prices,
            "exit_days": exit_days, "n_windows": n_windows}


def simulate(prices, entry, stop, target, n_sims=10000, seed=19, horizon=20):
    """Seeded Monte-Carlo replay of a hypothetical exit plan (daily bars).

    Inputs: prices — list of daily closes, or list of OHLC dicts
    (open/high/low/close) from the asset's own past; entry/stop/target
    — positive prices with stop < entry < target; n_sims (default
    10000); seed (default 19 — stated in every result); horizon —
    simulated days per path (default 20).
    Output: dict with target_first/stop_first/neither shares, median
    and 5-95 final closes, average realised R, break-even
    (1 / (1 + R)), verdict, seed, n_sims, horizon and n_windows.
    Raises ValueError on stop >= entry, target <= entry, or fewer
    than 20 usable windows.
    Limit: resampled history is not the future. A target-first share
    above break-even is a property of the past sample, not a
    probability that this plan will profit.
    """
    entry, stop, target = _validate_plan(entry, stop, target)
    if isinstance(n_sims, bool) or not isinstance(n_sims, int) or n_sims < 1:
        raise ValueError(f"n_sims must be a positive integer, got {n_sims!r}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        # Toolkit convention is ValueError for all input validation
        # (see toolkit/checks.py); a TypeError here would be the only
        # one in the toolkit.
        raise ValueError(f"seed must be an integer, got {seed!r}")  # noqa: TRY004
    steps = _steps_from_prices(prices)
    run = _simulate_from_steps(steps, entry, stop, target,
                               n_sims, seed, horizon)
    return _summarise(run["outcomes"], run["final_closes"],
                      run["realised_r"], seed=seed,
                      n_sims=n_sims, horizon=horizon,
                      n_windows=run["n_windows"],
                      entry=entry, stop=stop, target=target)


def replay_history(prices, entry, stop, target, horizon=20):
    """Run the same exit rule on every past window of `horizon` steps.

    No randomness: each window replays its own actual steps starting
    from `entry`. Deterministic, so the result carries seed None.
    Inputs/outputs mirror simulate(), plus a "windows" list with one
    entry per past window (start_index, outcome, exit_price, exit_day,
    realised_R, final_close). Raises ValueError on stop >= entry,
    target <= entry, or fewer than 20 usable windows.
    Limit: past windows are one sample of history, overlapping each
    other; a good replay record is not a prediction either.
    """
    entry, stop, target = _validate_plan(entry, stop, target)
    steps = _steps_from_prices(prices)
    n_windows = _check_windows(len(steps), horizon)
    risk = entry - stop
    outcomes = []
    final_closes = []
    realised_r = []
    windows = []
    for i in range(n_windows):
        outcome, exit_price, exit_day, final_close = _run_steps(
            steps[i:i + horizon], entry, stop, target)
        outcomes.append(outcome)
        final_closes.append(final_close)
        realised_r.append((exit_price - entry) / risk)
        windows.append({
            "start_index": i,
            "outcome": outcome,
            "exit_price": exit_price,
            "exit_day": exit_day,
            "realised_R": (exit_price - entry) / risk,
            "final_close": final_close,
        })
    return _summarise(outcomes, final_closes, realised_r, seed=None,
                      n_sims=None, horizon=horizon,
                      n_windows=n_windows, entry=entry, stop=stop,
                      target=target,
                      extra={"windows": windows,
                             "note": "deterministic replay — no seed, no randomness"})


# ---------------------------------------------------------------------------
# Engine 1.2 — named setups, honest verdict, NEPSE upgrade
# ---------------------------------------------------------------------------

# The five named setups, in scanner order. Rules are worded to match
# scanner/setups.py (nepali-capital-market-research, MIT — which
# adapted day-trader-skill's scanner rule table, MIT, commit 3d0f4ef)
# and toolkit/terminal.py; only the code is rewritten here against
# this module's own indicators, so this module stays self-contained
# and never imports the scanner repo.
SETUP_NAMES = (
    "breakout_20d",
    "bull_flag",
    "reclaim_ma20",
    "oversold",
    "oversold_bounce",
)

SETUP_DEFINITIONS = {
    "breakout_20d": "Latest close above the highest close of the prior "
                    "20 closes (needs 21 closes).",
    "bull_flag": "Close rose at least 10% over the 10 sessions ending "
                 "6 bars ago, then the last 5 closes span at most 5% "
                 "(needs 16 closes).",
    "reclaim_ma20": "Latest close above its 20-day moving average "
                    "while the prior close was at or below its 20-day "
                    "moving average (needs 21 closes).",
    "oversold": "Wilder RSI-14 below 30 on the latest close "
                "(needs 15 closes).",
    "oversold_bounce": "Wilder RSI-14 below 30 at least once in the "
                       "last 5 sessions and the latest close above the "
                       "prior close (needs 15 closes).",
}


def _setup_breakout_20d(closes):
    if len(closes) < 21:
        return False, f"too few closes (need 21, have {len(closes)})"
    level = max(closes[-21:-1])
    close = closes[-1]
    if close <= level:
        return False, (f"close {close:.2f} not above 20-day high "
                       f"{level:.2f}")
    return True, (f"close {close:.2f} above 20-day high {level:.2f} "
                  f"(+{100.0 * (close / level - 1):.2f}%)")


def _setup_bull_flag(closes):
    if len(closes) < 16:
        return False, f"too few closes (need 16, have {len(closes)})"
    run_start, run_end = closes[-16], closes[-6]
    if run_start <= 0:
        return False, "run start price is not positive"
    run_gain = run_end / run_start - 1
    flag = closes[-5:]
    flag_range = (max(flag) - min(flag)) / min(flag)
    if run_gain < 0.10:
        return False, (f"10-session run only {100.0 * run_gain:+.2f}% "
                       "(needs at least +10%)")
    if flag_range > 0.05:
        return False, (f"run +{100.0 * run_gain:.2f}% but last-5 range "
                       f"{100.0 * flag_range:.2f}% exceeds 5%")
    return True, (f"run +{100.0 * run_gain:.2f}% "
                  f"({run_start:.2f} -> {run_end:.2f}), last-5 range "
                  f"{100.0 * flag_range:.2f}%")


def _setup_reclaim_ma20(closes):
    if len(closes) < 21:
        return False, f"too few closes (need 21, have {len(closes)})"
    from .indicators import sma
    ma = sma(closes, 20)
    if ma[-1] is None or ma[-2] is None:
        return False, "20-day average not defined yet"
    if closes[-1] <= ma[-1]:
        return False, (f"close {closes[-1]:.2f} not above MA20 "
                       f"{ma[-1]:.2f}")
    if closes[-2] > ma[-2]:
        return False, (f"close already above MA20 on the prior bar "
                       f"({closes[-2]:.2f} vs {ma[-2]:.2f})")
    return True, (f"close {closes[-1]:.2f} crossed above MA20 "
                  f"{ma[-1]:.2f} (prior {closes[-2]:.2f} vs "
                  f"{ma[-2]:.2f})")


def _setup_oversold(closes):
    if len(closes) < 15:
        return False, f"too few closes (need 15, have {len(closes)})"
    from .indicators import rsi
    value = rsi(closes)[-1]
    if value is None:
        return False, "RSI-14 not defined yet"
    if value >= 30:
        return False, f"RSI-14 {value:.1f} (not below 30)"
    return True, f"RSI-14 {value:.1f} (below 30)"


def _setup_oversold_bounce(closes):
    if len(closes) < 15:
        return False, f"too few closes (need 15, have {len(closes)})"
    if closes[-1] <= closes[-2]:
        return False, (f"latest close {closes[-1]:.2f} not above prior "
                       f"close {closes[-2]:.2f}")
    from .indicators import rsi
    series = rsi(closes)
    recent = [v for v in series[-5:] if v is not None]
    if not recent or min(recent) >= 30:
        return False, "RSI-14 not below 30 in the last 5 sessions"
    return True, (f"RSI-14 {series[-1]:.1f} (low {min(recent):.1f} in "
                  f"last 5), close {closes[-1]:.2f} above prior "
                  f"{closes[-2]:.2f}")


_SETUP_FUNCTIONS = {
    "breakout_20d": _setup_breakout_20d,
    "bull_flag": _setup_bull_flag,
    "reclaim_ma20": _setup_reclaim_ma20,
    "oversold": _setup_oversold,
    "oversold_bounce": _setup_oversold_bounce,
}


def detect_setup(name, closes):
    """Evaluate one named setup on a closes list.

    Returns (matched, detail). A match is a statement about past
    closes only — never a signal. Raises ValueError on an unknown
    setup name.
    """
    if name not in _SETUP_FUNCTIONS:
        raise ValueError(
            f"unknown setup {name!r}; known setups: {list(SETUP_NAMES)}")
    return _SETUP_FUNCTIONS[name](validate_series("closes", closes))


def detect_setups(closes):
    """Evaluate all five named setups on a closes list.

    Returns {name: (matched, detail)} in SETUP_NAMES order.
    """
    closes = validate_series("closes", closes)
    return {name: _SETUP_FUNCTIONS[name](list(closes))
            for name in SETUP_NAMES}


def honest_verdict(target_first, break_even, n_windows, *,
                   min_windows=MIN_HONEST_WINDOWS, edge_margin=EDGE_MARGIN):
    """The honest verdict for a target-first share against break-even.

    Thresholds (stated, so they can be checked):
    - "insufficient data" when fewer than ``min_windows`` windows
      (default 20) support the figure, or when ``target_first`` is
      None (no windows at all);
    - "edge" when the target-first share is at least ``edge_margin``
      (default 0.10 = 10 percentage points) away from break-even;
    - "coin flip" for anything in between.
    "Edge" describes the past sample only. Every result that carries
    this verdict also carries "simulation, not a prediction".
    """
    if target_first is None:
        return "insufficient data"
    require_finite("target_first", target_first)
    require_finite("break_even", break_even)
    if (isinstance(n_windows, bool) or not isinstance(n_windows, int)
            or n_windows < 0):
        raise ValueError(
            f"n_windows must be a non-negative integer, got {n_windows!r}")
    if n_windows < min_windows:
        return "insufficient data"
    if abs(target_first - break_even) >= edge_margin:
        return "edge"
    return "coin flip"


def check_exit_liquidity(shares, typical_daily_volume,
                         max_participation=DEFAULT_MAX_PARTICIPATION):
    """Exit-liquidity guard: can this position plausibly exit?

    ``shares`` against ``typical_daily_volume`` (same units, e.g.
    shares per day): the position is BLOCKED when it exceeds
    ``max_participation`` (default 10%) of a typical day's volume —
    exiting would itself move the price, and on NEPSE a thin
    counterparty book may not absorb it at all. Returns a dict with
    ``blocked``, the participation fraction, and a plain-language
    reason. This is a guard, not a forecast: it says nothing about
    whether the price will move.
    """
    if isinstance(shares, bool) or not isinstance(shares, int) or shares < 1:
        raise ValueError(f"shares must be a positive integer, got {shares!r}")
    require_positive("typical_daily_volume", typical_daily_volume)
    require_finite("max_participation", max_participation)
    if not 0.0 < max_participation <= 1.0:
        raise ValueError(
            f"max_participation must be in (0, 1], got {max_participation}")
    participation = shares / float(typical_daily_volume)
    blocked = participation > max_participation
    return {
        "blocked": blocked,
        "shares": shares,
        "typical_daily_volume": float(typical_daily_volume),
        "participation": participation,
        "max_participation": max_participation,
        "reason": (
            f"{shares} shares is {100.0 * participation:.1f}% of typical "
            f"daily volume ({typical_daily_volume:.0f}); "
            f"{'exceeds' if blocked else 'within'} the "
            f"{100.0 * max_participation:.0f}% exit-liquidity limit"
            + (" — BLOCKED: exiting a position this large against "
               "typical volume may not be absorbable at the quoted "
               "price" if blocked else "")),
        "note": ("Exit-liquidity guard only: a pass says nothing about "
                 "whether the price will move."),
    }


def _closes_from_prices(prices):
    """Return (closes, is_ohlc) for setup evaluation."""
    rows = list(prices)
    if not rows:
        raise ValueError("prices is empty")
    if isinstance(rows[0], dict):
        return [float(bar["close"]) for bar in rows], True
    return validate_series("prices", rows), False


def _net_realised_r(entry, exit_price, risk, shares, frictions, holding_days):
    """Realised R net of NEPSE frictions, for one round trip.

    Gain is computed AFTER fees (sell proceeds after sell-side
    charges minus the fee-inclusive cost of buying), CGT applies
    only on a positive gain, and the net cash profit is divided by
    the risk taken (risk_per_share * shares). Raises
    UnknownFrictionError when a needed rate is UNKNOWN.
    """
    report = realised_gain_after_fees(
        entry * shares, exit_price * shares, frictions,
        holding_days=holding_days)
    net_profit = report["net_proceeds"] - report["buy_total_cost"]
    return net_profit / (risk * shares)


def _clip_steps_to_circuit(steps, limit_up, limit_down, limit_on=None):
    """Clip each step's daily moves to the circuit limits.

    A NEPSE price cannot move beyond the daily limit of the previous
    close in a day, so a simulated step that would carry it further
    is clipped at the limit instead of being allowed through.
    ``limit_on`` is an optional callable ``date -> (up, down)``:
    when given, a step carrying a "date" is clipped by the limit in
    force on that bar's date (NEPSE: 10% before 2026-04-20, 15%
    from); a step without a date falls back to ``limit_up`` /
    ``limit_down``. The returned steps carry no "date" key.
    """
    clipped = []
    for step in steps:
        up, down = limit_up, limit_down
        if limit_on is not None and "date" in step:
            up, down = limit_on(step["date"])
        clipped.append({
            "high": min(step["high"], up),
            "low": max(step["low"], -down),
            "close": max(-down, min(step["close"], up)),
        })
    return clipped


def _dated_limits_for(profile, steps):
    """(limit_on, as_of) for per-bar-date clipping, or (None, None).

    ``limit_on`` resolves a step's date to the (up, down) circuit
    limits in force on that date; ``as_of`` is the latest dated
    step's date, for reporting which rule the reported limits are.
    """
    dated = [s["date"] for s in steps if "date" in s]
    if not dated:
        return None, None
    return (lambda d: (profile.circuit_limit_up_on(d),
                       profile.circuit_limit_down_on(d))), max(dated)


def replay_setup(prices, setup, entry, stop, target, horizon=20, *,
                 setup_fn=None, frictions=None, shares=None):
    """Deterministic replay restricted to windows where a setup fired.

    ``setup`` is a name from SETUP_NAMES, or ``setup_fn`` is a
    callable taking a closes list and returning True (or
    (matched, detail)) when the setup fired. The setup is evaluated
    on the closes visible at the START of each window — the closes
    through the window's first bar — never on later bars, so the
    replay is point-in-time correct.

    Returns the replay_history shape plus ``setup``,
    ``n_setup_windows``, ``n_windows`` (total windows considered),
    ``n_skipped_insufficient_history`` (windows too early for the
    setup to be evaluated), ``honest_verdict`` ("edge" / "coin
    flip" / "insufficient data"), and a ``windows`` list for the
    setup windows only. With zero setup windows the shares are
    None and the verdict is "insufficient data" — no number is
    invented from no sample. With ``frictions`` and ``shares``,
    ``average_R_net`` is also reported (realised R after NEPSE
    frictions; see ``_net_realised_r``).

    Raises ValueError on a bad plan, an unknown setup name, or
    fewer than 20 total windows — the same floor as replay_history.
    """
    entry, stop, target = _validate_plan(entry, stop, target)
    if setup_fn is not None and not callable(setup_fn):
        raise ValueError(f"setup_fn must be callable, got {setup_fn!r}")
    if setup_fn is None:
        if not isinstance(setup, str) or setup not in _SETUP_FUNCTIONS:
            raise ValueError(
                f"unknown setup {setup!r}; known setups: "
                f"{list(SETUP_NAMES)} (or pass setup_fn=...)")
        setup_name = setup
        fn = _SETUP_FUNCTIONS[setup]
    else:
        setup_name = getattr(setup_fn, "__name__", "custom")
        fn = setup_fn
    if frictions is not None and shares is None:
        raise ValueError(
            "shares is required when frictions are supplied: the flat "
            "DP charge (Rs 25 per transaction) is per trade, so the "
            "net R depends on the position size")
    if shares is not None and (isinstance(shares, bool)
                               or not isinstance(shares, int)
                               or shares < 1):
        raise ValueError(f"shares must be a positive integer, got {shares!r}")
    steps = _steps_from_prices(prices)
    n_windows = _check_windows(len(steps), horizon)
    closes, is_ohlc = _closes_from_prices(prices)
    risk = entry - stop
    outcomes = []
    final_closes = []
    realised_r = []
    realised_r_net = []
    windows = []
    n_skipped = 0
    for i in range(n_windows):
        # Closes visible at the window's start: through the start
        # close for a closes series; through the prior bar's close
        # for OHLC bars (the window starts at bar i's open).
        prefix = closes[:i] if is_ohlc else closes[:i + 1]
        outcome_fn = fn(list(prefix))
        if isinstance(outcome_fn, tuple):
            matched, _detail = outcome_fn
        else:
            matched, _detail = bool(outcome_fn), ""
        if not matched and isinstance(outcome_fn, tuple) and str(
                _detail).startswith("too few closes"):
            n_skipped += 1
            continue
        if not matched:
            continue
        outcome, exit_price, exit_day, final_close = _run_steps(
            steps[i:i + horizon], entry, stop, target)
        outcomes.append(outcome)
        final_closes.append(final_close)
        realised_r.append((exit_price - entry) / risk)
        if frictions is not None:
            holding = exit_day if exit_day is not None else horizon
            realised_r_net.append(_net_realised_r(
                entry, exit_price, risk, shares, frictions, holding))
        windows.append({
            "start_index": i,
            "outcome": outcome,
            "exit_price": exit_price,
            "exit_day": exit_day,
            "realised_R": (exit_price - entry) / risk,
            "final_close": final_close,
        })
    base = {
        "method": "simulation, not a prediction",
        "setup": setup_name,
        "horizon": horizon,
        "n_windows": n_windows,
        "n_setup_windows": len(outcomes),
        "n_skipped_insufficient_history": n_skipped,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_per_share": risk,
        "reward_risk": (target - entry) / risk,
        "break_even": 1.0 / (1.0 + (target - entry) / risk),
    }
    if not outcomes:
        base.update({
            "target_first": None,
            "stop_first": None,
            "neither": None,
            "median_close": None,
            "close_p5": None,
            "close_p95": None,
            "average_R": None,
            "verdict": "insufficient data",
            "honest_verdict": "insufficient data",
            "windows": [],
            "note": ("no past window fired this setup — nothing to "
                     "replay, so no shares are reported"),
        })
        if frictions is not None:
            base["average_R_net"] = None
        return base
    result = _summarise(outcomes, final_closes, realised_r, seed=None,
                        n_sims=None, horizon=horizon,
                        n_windows=n_windows, entry=entry, stop=stop,
                        target=target,
                        extra={"windows": windows,
                               "setup": setup_name,
                               "n_setup_windows": len(outcomes),
                               "n_skipped_insufficient_history": n_skipped,
                               "note": ("deterministic replay of setup "
                                        "windows only — no seed, no "
                                        "randomness")})
    result["honest_verdict"] = honest_verdict(
        result["target_first"], result["break_even"], len(outcomes))
    if frictions is not None:
        result["average_R_net"] = (
            sum(realised_r_net) / len(realised_r_net))
    return result


def simulate_nepse(prices, entry, stop, target, *, shares=100,
                   typical_daily_volume=None, frictions=None,
                   n_sims=10000, seed=19, horizon=20, profile=None):
    """Seeded Monte-Carlo for a NEPSE hypothetical exit plan.

    The plain ``simulate`` plus NEPSE reality, all labelled:
    - daily moves clipped to the circuit limit in force on EACH
      bar's date (10% before 2026-04-20, 15% from — see
      ``NEPSE_CIRCUIT_SOURCE``); undated bars use the current 15%;
    - exit-liquidity guard: when ``typical_daily_volume`` is given
      and ``shares`` exceeds 10% of it, the run is BLOCKED and no
      simulation is produced (``blocked: True``);
    - frictions from ``sourced_nepal_frictions()`` by default
      (broker slabs, SEBON 0.015%, DP Rs 25, CGT 5% <=365d /
      3.75% >365d — user-supplied rate-schedule screenshot,
      28 Oct 2026): ``average_R`` is reported NET of those costs,
      with ``average_R_gross`` kept for comparison;
    - settlement T+2 as standard (user-supplied, 28 Oct 2026).
    ``shares`` (default 100) is the example position size the flat
    DP charge is computed on — it is stated in every result, change
    it with ``shares=``. ``holding_days`` for CGT is the simulated
    exit day (or the horizon when neither level is touched).

    Returns the ``simulate`` shape plus ``honest_verdict`` ("edge" /
    "coin flip" / "insufficient data"), circuit/friction/settlement
    labels, ``shares`` and the liquidity check. Raises ValueError
    on a bad plan and UnknownFrictionError when a needed rate is
    UNKNOWN. Every result states "simulation, not a prediction".

    ``profile`` (a market key or MarketProfile, default None) routes
    the run through ``simulate_with_profile`` instead: with the
    NEPSE profile the result is identical to this function; with any
    other profile the market's own limits, fees and settlement apply.
    Pass ``profile`` or ``frictions``, not both.
    """
    if profile is not None:
        if frictions is not None:
            raise ValueError("pass profile or frictions, not both")
        return simulate_with_profile(
            prices, entry, stop, target, profile=profile, shares=shares,
            typical_daily_volume=typical_daily_volume, n_sims=n_sims,
            seed=seed, horizon=horizon)
    entry, stop, target = _validate_plan(entry, stop, target)
    if isinstance(n_sims, bool) or not isinstance(n_sims, int) or n_sims < 1:
        raise ValueError(f"n_sims must be a positive integer, got {n_sims!r}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        # Toolkit convention is ValueError for all input validation
        # (see toolkit/checks.py); a TypeError here would be the only
        # one in the toolkit.
        raise ValueError(f"seed must be an integer, got {seed!r}")  # noqa: TRY004
    if isinstance(shares, bool) or not isinstance(shares, int) or shares < 1:
        raise ValueError(f"shares must be a positive integer, got {shares!r}")
    if frictions is None:
        frictions = sourced_nepal_frictions()
    if frictions.circuit_limit_up is None or frictions.circuit_limit_down is None:
        raise UnknownFrictionError(
            "circuit limits are UNKNOWN: no limit is asserted — supply "
            "sourced, dated limits before running a NEPSE simulation")
    liquidity = None
    if typical_daily_volume is not None:
        liquidity = check_exit_liquidity(shares, typical_daily_volume)
        if liquidity["blocked"]:
            return {
                "method": "simulation, not a prediction",
                "blocked": True,
                "block_reason": liquidity["reason"],
                "liquidity": liquidity,
                "entry": entry,
                "stop": stop,
                "target": target,
                "shares": shares,
                "honest_verdict": None,
                "verdict": "BLOCKED — exit liquidity",
                "note": ("No simulation was run: the position is too "
                         "large for typical volume. Reduce shares or "
                         "supply a larger typical_daily_volume."),
            }
    steps = _steps_from_prices(prices)
    # Dated bars are clipped by the limit in force on EACH bar's
    # date (NEPSE: 10% before 2026-04-20, 15% from); undated bars
    # keep the single frictions limits, byte-identical to before.
    limit_on, limit_as_of = _dated_limits_for(get_profile("nepse"), steps)
    clipped = _clip_steps_to_circuit(
        steps, frictions.circuit_limit_up, frictions.circuit_limit_down,
        limit_on=limit_on)
    run = _simulate_from_steps(clipped, entry, stop, target,
                               n_sims, seed, horizon)
    risk = entry - stop
    realised_r_net = []
    for exit_price, exit_day in zip(run["exit_prices"], run["exit_days"]):
        holding = exit_day if exit_day is not None else horizon
        realised_r_net.append(_net_realised_r(
            entry, exit_price, risk, shares, frictions, holding))
    average_r_gross = sum(run["realised_r"]) / len(run["realised_r"])
    average_r_net = sum(realised_r_net) / len(realised_r_net)
    result = _summarise(run["outcomes"], run["final_closes"],
                        realised_r_net, seed=seed,
                        n_sims=n_sims, horizon=horizon,
                        n_windows=run["n_windows"],
                        entry=entry, stop=stop, target=target)
    buy_report = trade_cost_report(entry * shares, "buy", frictions)
    result.update({
        "blocked": False,
        "shares": shares,
        "average_R_gross": average_r_gross,
        "average_R_net": average_r_net,
        "honest_verdict": honest_verdict(
            result["target_first"], result["break_even"],
            run["n_windows"]),
        "circuit_limit_up": (
            get_profile("nepse").circuit_limit_up_on(limit_as_of)
            if limit_as_of is not None else frictions.circuit_limit_up),
        "circuit_limit_down": (
            get_profile("nepse").circuit_limit_down_on(limit_as_of)
            if limit_as_of is not None else frictions.circuit_limit_down),
        "circuit_source": NEPSE_CIRCUIT_SOURCE,
        "frictions_source": frictions.source,
        "frictions_as_of": frictions.as_of,
        "buy_charges_at_entry": buy_report["total_cost"],
        "settlement_lag_business_days":
            frictions.settlement_lag_business_days,
        "settlement_note": NEPSE_SETTLEMENT_NOTE,
        "liquidity": liquidity,
        "note": ("average_R is net of NEPSE frictions (broker slabs, "
                 "SEBON fee, DP charge, CGT on gains after fees); "
                 "average_R_gross is the same simulation before costs"),
    })
    if limit_as_of is not None:
        # Dated bars: the reported limits are the ones in force on
        # the latest bar's date, not a single flat limit.
        result["circuit_limit_as_of"] = limit_as_of.isoformat()
        result["circuit_limits_dated"] = True
    return result


# --- Market-profile simulation (Step 1, global-markets) -------------------
# The NEPSE path above is untouched: simulate_with_profile with the
# NEPSE profile delegates straight back to simulate_nepse, so the
# existing behaviour (and its tests) is byte-identical. Every other
# profile runs the generic path below, which applies ONLY the fees
# and taxes the profile actually sources; anything UNKNOWN is listed
# in ``unmodelled_fees`` and never silently set to zero.

def _profile_side_cost(trade_value, side, profile):
    """(cost, unmodelled) for one side of a trade under a profile.

    Fee entries with a numeric value are applied: a unit containing
    "fraction" is a rate on the trade value, any other numeric unit
    is a flat charge in the profile's currency. Entries with a None
    value are unmodelled (broker- or venue-dependent), never zero.
    Side rules, by fee name: "intraday" fees apply to same-day trades
    only, so a multi-day simulation skips them (listed unmodelled);
    a name containing "sell" applies on the sell side only; a name
    ending in "_buy" applies on the buy side only.
    """
    cost = 0.0
    unmodelled = []
    for name, spec in profile.fees.items():
        value = spec.get("value")
        if value is None:
            unmodelled.append(name)
            continue
        if "intraday" in name:
            unmodelled.append(name + " (intraday only)")
            continue
        if "sell" in name and side != "sell":
            continue
        if name.endswith("_buy") and side != "buy":
            continue
        unit = str(spec.get("unit", ""))
        if "fraction" in unit:
            cost += float(value) * trade_value
        else:
            cost += float(value)
    return cost, unmodelled


def _profile_tax(gain_after_fees, profile):
    """(tax, unmodelled) on a gain under a profile.

    Only taxes whose key names a gains tax ("cgt" / "stcg") and whose
    value is sourced are applied, and only on a positive gain. Any
    other tax entry (or a None value) is unmodelled.
    """
    tax = 0.0
    unmodelled = []
    if gain_after_fees <= 0:
        return 0.0, [k for k, v in profile.taxes.items()
                     if v.get("value") is None]
    for name, spec in profile.taxes.items():
        value = spec.get("value")
        if value is None:
            unmodelled.append(name)
            continue
        if "cgt" in name or "stcg" in name:
            tax += float(value) * gain_after_fees
        else:
            unmodelled.append(name)
    return tax, unmodelled


def _net_realised_r_generic(entry, exit_price, risk, shares, profile,
                           holding_days):
    """Realised R net of a profile's sourced fees/taxes, one round trip.

    Returns (net_R, unmodelled): gain after fees, tax on a positive
    gain only, divided by the risk taken. A fee or tax the profile
    does not source is listed in ``unmodelled`` — the net figure is
    explicitly partial, never a silent zero-cost run.
    """
    buy_value = entry * shares
    sell_value = exit_price * shares
    buy_cost, un_buy = _profile_side_cost(buy_value, "buy", profile)
    sell_cost, un_sell = _profile_side_cost(sell_value, "sell", profile)
    gain_after_fees = (sell_value - sell_cost) - (buy_value + buy_cost)
    tax, un_tax = _profile_tax(gain_after_fees, profile)
    net_profit = gain_after_fees - tax
    unmodelled = sorted(set(un_buy + un_sell + un_tax))
    return net_profit / (risk * shares), unmodelled


def simulate_with_profile(prices, entry, stop, target, *, profile,
                          shares=100, typical_daily_volume=None,
                          n_sims=10000, seed=19, horizon=20):
    """Seeded Monte-Carlo for a hypothetical exit plan under a market profile.

    ``profile`` is a market key (``"us"``, ``"india"``, ``"uk"``,
    ``"crypto"``, ``"fx"``, ``"generic"``, ``"nepse"``) or a
    MarketProfile. With ``"nepse"`` this delegates to
    ``simulate_nepse`` unchanged — byte-identical output. For any
    other market:
    - daily moves are clipped to the profile's circuit limits when it
      has them; a market with no per-stock limit runs unclipped;
    - ``shares`` must be a multiple of the profile's lot size when
      one is sourced (ValueError otherwise);
    - ``average_R_net`` applies only the profile's sourced fees and
      gains taxes; anything unsourced is listed in
      ``unmodelled_fees`` and the net figure is labelled partial;
    - settlement lag and note come from the profile.

    Returns the ``simulate`` shape plus ``honest_verdict``,
    ``market`` / ``market_name``, circuit / settlement labels,
    ``shares`` and the liquidity check. Raises ValueError on a bad
    plan or an unknown market, and UnknownFrictionError is never
    raised here — an unsourced rate is unmodelled, not fatal, because
    the generic path never asserts a rate it does not have.
    """
    if isinstance(profile, str):
        profile = get_profile(profile)
    if profile.key == "nepse":
        return simulate_nepse(
            prices, entry, stop, target, shares=shares,
            typical_daily_volume=typical_daily_volume, n_sims=n_sims,
            seed=seed, horizon=horizon)
    entry, stop, target = _validate_plan(entry, stop, target)
    if isinstance(n_sims, bool) or not isinstance(n_sims, int) or n_sims < 1:
        raise ValueError(f"n_sims must be a positive integer, got {n_sims!r}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError(f"seed must be an integer, got {seed!r}")  # noqa: TRY004
    if isinstance(shares, bool) or not isinstance(shares, int) or shares < 1:
        raise ValueError(f"shares must be a positive integer, got {shares!r}")
    if profile.lot_size and shares % profile.lot_size != 0:
        raise ValueError(
            f"shares ({shares}) must be a multiple of the {profile.key} "
            f"lot size ({profile.lot_size})")
    liquidity = None
    if typical_daily_volume is not None:
        liquidity = check_exit_liquidity(shares, typical_daily_volume)
        if liquidity["blocked"]:
            return {
                "method": "simulation, not a prediction",
                "blocked": True,
                "block_reason": liquidity["reason"],
                "liquidity": liquidity,
                "entry": entry,
                "stop": stop,
                "target": target,
                "shares": shares,
                "market": profile.key,
                "market_name": profile.name,
                "honest_verdict": None,
                "verdict": "BLOCKED — exit liquidity",
                "note": ("No simulation was run: the position is too "
                         "large for typical volume. Reduce shares or "
                         "supply a larger typical_daily_volume."),
            }
    steps = _steps_from_prices(prices)
    # Dated bars are clipped by the limit in force on EACH bar's
    # date; undated bars keep the single profile limits.
    limit_on, limit_as_of = _dated_limits_for(profile, steps)
    if profile.circuit_limit_up is not None and profile.circuit_limit_down is not None:
        steps = _clip_steps_to_circuit(
            steps, profile.circuit_limit_up, profile.circuit_limit_down,
            limit_on=limit_on)
    run = _simulate_from_steps(steps, entry, stop, target,
                               n_sims, seed, horizon)
    risk = entry - stop
    realised_r_net = []
    unmodelled_all = set()
    for exit_price, exit_day in zip(run["exit_prices"], run["exit_days"]):
        holding = exit_day if exit_day is not None else horizon
        net_r, unmodelled = _net_realised_r_generic(
            entry, exit_price, risk, shares, profile, holding)
        realised_r_net.append(net_r)
        unmodelled_all.update(unmodelled)
    average_r_gross = sum(run["realised_r"]) / len(run["realised_r"])
    average_r_net = sum(realised_r_net) / len(realised_r_net)
    result = _summarise(run["outcomes"], run["final_closes"],
                        realised_r_net, seed=seed,
                        n_sims=n_sims, horizon=horizon,
                        n_windows=run["n_windows"],
                        entry=entry, stop=stop, target=target)
    buy_cost, _ = _profile_side_cost(entry * shares, "buy", profile)
    result.update({
        "blocked": False,
        "shares": shares,
        "market": profile.key,
        "market_name": profile.name,
        "currency": profile.currency,
        "average_R_gross": average_r_gross,
        "average_R_net": average_r_net,
        "honest_verdict": honest_verdict(
            result["target_first"], result["break_even"],
            run["n_windows"]),
        "circuit_limit_up": (
            profile.circuit_limit_up_on(limit_as_of)
            if limit_as_of is not None else profile.circuit_limit_up),
        "circuit_limit_down": (
            profile.circuit_limit_down_on(limit_as_of)
            if limit_as_of is not None else profile.circuit_limit_down),
        "circuit_note": profile.circuit_note,
        "unmodelled_fees": sorted(unmodelled_all),
        "buy_charges_at_entry": buy_cost,
        "settlement_lag_business_days":
            profile.settlement_lag_business_days,
        "settlement_note": profile.settlement_note,
        "liquidity": liquidity,
        "note": ("average_R is net of the sourced fees/taxes of the "
                 f"{profile.key} profile only; anything in "
                 "unmodelled_fees was NOT charged, so the net figure "
                 "is partial. average_R_gross is the same simulation "
                 "before costs."),
    })
    if limit_as_of is not None:
        result["circuit_limit_as_of"] = limit_as_of.isoformat()
        result["circuit_limits_dated"] = True
    return result
