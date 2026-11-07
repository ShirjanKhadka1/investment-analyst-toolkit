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
"""
from __future__ import annotations

import math
import random

from .checks import require_finite, require_positive, validate_series

__all__ = ["replay_history", "simulate"]

DEFAULT_SEED = 19
DEFAULT_N_SIMS = 10000
DEFAULT_HORIZON = 20
MIN_WINDOWS = 20


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
    preceding bar, so it contributes no step.
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
            steps.append({
                "high": float(bar["high"]) / op - 1.0,
                "low": float(bar["low"]) / op - 1.0,
                "close": float(bar["close"]) / op - 1.0,
            })
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
    n_windows = _check_windows(len(steps), horizon)
    # Position-matched pools: pool[t] holds the steps that sat at
    # position t in each past window — never mixed across positions.
    pools = [[steps[i + t] for i in range(n_windows)] for t in range(horizon)]
    rng = random.Random(seed)
    outcomes = []
    final_closes = []
    realised_r = []
    risk = entry - stop
    for _ in range(n_sims):
        path = [rng.choice(pools[t]) for t in range(horizon)]
        outcome, exit_price, _, final_close = _run_steps(path, entry, stop, target)
        outcomes.append(outcome)
        final_closes.append(final_close)
        realised_r.append((exit_price - entry) / risk)
    return _summarise(outcomes, final_closes, realised_r, seed=seed,
                      n_sims=n_sims, horizon=horizon, n_windows=n_windows,
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
