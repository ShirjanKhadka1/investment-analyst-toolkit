"""R-based hypothetical-position planning — research only, no orders.

Adapted arithmetic from day-trader-skill's trader-exit, trader-budget
and trader-risk (MIT licence,
https://github.com/Jakeschincariol/day-trader-skill, commit 3d0f4ef):
exit-plan-before-entry with R arithmetic and refusals, budget sizing
as the minimum of several limits (naming which limit decided it), and
a PASS/BLOCK risk-gate checklist with a fingerprint. Rebuilt here in
this toolkit's zero-dependency style, for hypothetical plans only.

Nothing in this module writes an order, places a trade, or talks to a
broker. A PASS from risk_gate() is not a yes — it only means the
hypothetical plan passed these 16 stated checks on this date, for
this exact plan dict (see the fingerprint). Research and education
only: not investment advice, never a buy/sell/hold recommendation.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from .checks import require_non_negative, require_positive

__all__ = ["DEFAULT_RESEARCH_CONFIG", "plan_exit", "risk_gate", "size_position"]

MIN_RISK_PCT = 0.0005  # 0.05% of entry price — below this, refuse.
_CENT = Decimal("0.01")

# Example research config, mirroring the day-trader-skill example
# config's shape and limits. These are EXAMPLE values for a
# hypothetical plan, not recommendations and not advice.
DEFAULT_RESEARCH_CONFIG = {
    "budget": 10000.0,
    "cash_buffer_pct": 0.10,
    "max_position_pct": 0.25,
    "max_order_usd": 2500.0,
    "min_order_usd": 100.0,
    "max_loss_per_trade_usd": 100.0,
    "max_daily_loss_usd": 200.0,
    "max_trades_per_day": 3,
    "max_orders_per_day": 3,
    "min_reward_risk": 2.0,
    "require_stop": True,
    "paper": True,
    "allow_options": False,
    "allow_margin": False,
    "allow_short": False,
    "blocked_symbols": [],
}

# The 16 risk-gate checks, in order. Names are stable: a fingerprint
# is only meaningful if the checklist it covers does not change.
GATE_CHECKS = (
    "format",
    "asset_type",
    "short_blocked",
    "blocked_list",
    "size_limits",
    "position_cap",
    "cash_buffer",
    "stop_required",
    "loss_per_trade",
    "reward_risk",
    "daily_loss",
    "trades_per_day",
    "entry_window",
    "duplicate",
    "daily_order_limit",
    "paper_mode",
)


def _round_down_cent(value):
    """Round to the cent, away from the entry for a long stop (down)."""
    return float(Decimal(str(value)).quantize(_CENT, rounding=ROUND_FLOOR))


def _round_half_up_cent(value):
    """Round to the cent, half-up (the target rule)."""
    return float(Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP))


def _clean(value):
    """Round a computed dollar amount to 10 decimal places so float
    dust (e.g. 5.000000000001) never leaks into a plan dict."""
    return round(float(value), 10)


def plan_exit(entry, stop, target=None, risk_reward=2.0, min_reward_risk=None):
    """Plan the exit of a hypothetical long position BEFORE entry.

    Inputs: entry, stop — positive prices, stop below entry;
    target — optional positive price above entry (default: computed
    from risk_reward); risk_reward — the reward:risk the plan aims
    for when the target is computed (default 2.0); min_reward_risk —
    the floor a supplied target must clear (default: risk_reward).
    Output: dict with entry, stop, target, risk_per_share (R, dollars
    per share), reward_per_share, reward_risk, risk_pct_of_entry, and
    the rounding rules applied.
    Rounding: the stop is rounded DOWN to the cent (away from entry,
    so the risk is never understated); the target is rounded HALF-UP
    to the cent.
    Refusals (ValueError, with the reason): stop >= entry; target
    <= entry; risk below 0.05% of the entry price; reward:risk of a
    supplied target below the minimum.
    Limit: R arithmetic describes the plan's shape; it cannot prove
    the stop or the target will ever be reached, or at what price a
    real exit would fill.
    """
    require_positive("entry", entry)
    require_positive("stop", stop)
    require_positive("risk_reward", risk_reward)
    if stop >= entry:
        raise ValueError(
            f"refusal: stop ({stop}) must be below entry ({entry}) — "
            "no exit plan without a stop below the entry")
    stop_r = _round_down_cent(stop)
    if stop_r >= entry:
        raise ValueError(
            f"refusal: stop ({stop}) rounds to {stop_r}, not below entry ({entry})")
    risk = _clean(entry - stop_r)
    risk_pct = risk / entry
    if risk_pct < MIN_RISK_PCT:
        raise ValueError(
            f"refusal: risk per share ({risk}) is {risk_pct:.4%} of entry "
            f"({entry}) — below the 0.05% minimum; the stop is too tight "
            "to plan around")
    floor_rr = risk_reward if min_reward_risk is None else min_reward_risk
    require_positive("min_reward_risk", floor_rr)
    if target is None:
        target_r = _round_half_up_cent(entry + risk_reward * risk)
    else:
        require_positive("target", target)
        target_r = _round_half_up_cent(target)
        if target_r <= entry:
            raise ValueError(
                f"refusal: target ({target}) must be above entry ({entry})")
        reward_risk = (target_r - entry) / risk
        if reward_risk < floor_rr:
            raise ValueError(
                f"refusal: reward:risk ({reward_risk:.2f}) is below the "
                f"minimum ({floor_rr}) — the target is too close for the risk")
    reward = _clean(target_r - entry)
    return {
        "entry": float(entry),
        "stop": stop_r,
        "target": target_r,
        "risk_per_share": risk,
        "reward_per_share": reward,
        "reward_risk": _clean(reward / risk),
        "risk_pct_of_entry": risk_pct,
        "stop_rounding": "down to the cent (away from entry)",
        "target_rounding": "half-up to the cent",
    }


def size_position(budget, max_loss_per_trade, max_position_pct, max_order_usd,
                  entry, risk_per_share, cash_available=None, min_order_usd=0.0):
    """Size a hypothetical position as the minimum of four limits.

    Inputs: budget, max_loss_per_trade, max_order_usd, entry,
    risk_per_share — positive numbers; max_position_pct in (0, 1];
    cash_available — optional cash on hand (default: the budget);
    min_order_usd — smallest order worth planning (default 0).
    Output: dict with whole shares, the four per-limit share counts,
    which limit decided it ("limiting_factor"), position_cost,
    max_loss, position_pct_of_budget and cash_remaining. Ties are
    broken in this order: max_loss_per_trade, max_position_pct,
    max_order_usd, cash_available.
    Refusals (ValueError, with the reason): zero shares under every
    limit; position cost below min_order_usd; any non-positive input.
    Limit: sizing caps the hypothetical loss; it cannot prove the
    loss will be capped — a gap through the stop loses more.
    """
    require_positive("budget", budget)
    require_positive("max_loss_per_trade", max_loss_per_trade)
    require_positive("max_order_usd", max_order_usd)
    require_positive("entry", entry)
    require_positive("risk_per_share", risk_per_share)
    require_non_negative("min_order_usd", min_order_usd)
    if not (0.0 < max_position_pct <= 1.0):
        raise ValueError(f"max_position_pct must be in (0, 1], got {max_position_pct}")
    cash = budget if cash_available is None else cash_available
    require_non_negative("cash_available", cash)
    limits = {
        "max_loss_per_trade": math.floor(max_loss_per_trade / risk_per_share),
        "max_position_pct": math.floor(budget * max_position_pct / entry),
        "max_order_usd": math.floor(max_order_usd / entry),
        "cash_available": math.floor(cash / entry),
    }
    shares = min(limits.values())
    limiting = next(name for name in limits if limits[name] == shares)
    if shares <= 0:
        raise ValueError(
            f"refusal: zero shares — the binding limit is {limiting} "
            f"(limits: {limits}); no position can be planned")
    cost = _clean(shares * entry)
    if cost < min_order_usd:
        raise ValueError(
            f"refusal: position cost ({cost}) is below min_order_usd "
            f"({min_order_usd}) — the plan is too small to size")
    return {
        "shares": shares,
        "limiting_factor": limiting,
        "limits": limits,
        "position_cost": cost,
        "max_loss": _clean(shares * risk_per_share),
        "position_pct_of_budget": _clean(cost / budget),
        "cash_available": float(cash),
        "cash_remaining": _clean(cash - cost),
    }


def _is_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _plan_number(plan, key):
    value = plan.get(key)
    return value if _is_number(value) else None


def risk_gate(plan, config=None):
    """PASS/BLOCK checklist over a hypothetical plan — research only.

    Inputs: plan — a dict with at least entry, stop, target,
    risk_per_share and shares (as returned by plan_exit() merged with
    size_position()), plus optional context keys: asset_type
    ("equity"/"etf"), symbol, short (bool), blocked (bool),
    daily_loss_usd, trades_today, orders_today, entry_window_ok
    (bool), is_duplicate (bool), date ("YYYY-MM-DD"); config — a dict
    like DEFAULT_RESEARCH_CONFIG (missing keys fall back to it).
    Output: dict with verdict ("PASS" only if all 16 checks pass,
    else "BLOCK"), per-check {"pass", "reason"}, passed/failed
    counts, failed_checks, date, and fingerprint — sha256 of the
    exact plan dict + date, valid only for that plan on that date.
    The 16 checks: format, asset_type, short_blocked, blocked_list,
    size_limits, position_cap, cash_buffer, stop_required,
    loss_per_trade, reward_risk, daily_loss, trades_per_day,
    entry_window, duplicate, daily_order_limit, paper_mode.
    Limit: a PASS is not a yes. It says the plan passed these checks
    as written, on this date — nothing about whether the trade is
    good, or will profit.
    """
    if not isinstance(plan, dict):
        # Toolkit convention is ValueError for all input validation
        # (see toolkit/checks.py); a TypeError here would be the only
        # one in the toolkit.
        raise ValueError("plan must be a dict")  # noqa: TRY004
    cfg = dict(DEFAULT_RESEARCH_CONFIG)
    if config is not None:
        if not isinstance(config, dict):
            raise ValueError("config must be a dict")
        cfg.update(config)
    date_str = plan.get("date")
    if date_str is None:
        date_str = datetime.date.today().isoformat()
    entry = _plan_number(plan, "entry")
    stop = _plan_number(plan, "stop")
    target = _plan_number(plan, "target")
    risk = _plan_number(plan, "risk_per_share")
    shares = _plan_number(plan, "shares")
    max_loss = _plan_number(plan, "max_loss")
    if max_loss is None and shares is not None and risk is not None:
        max_loss = shares * risk
    position_cost = shares * entry if shares is not None and entry is not None else None
    budget = cfg["budget"]
    cash = _plan_number(plan, "cash_available")
    if cash is None:
        cash = budget
    reward_risk = _plan_number(plan, "reward_risk")
    if reward_risk is None and target is not None and entry is not None and risk:
        reward_risk = (target - entry) / risk

    checks = {}

    def _check(name, passed, reason):
        checks[name] = {"pass": bool(passed), "reason": reason}

    _check("format",
           all(v is not None for v in (entry, stop, target, risk, shares))
           and entry > 0 and stop > 0 and target > 0 and risk > 0 and shares >= 0,
           "entry/stop/target/risk_per_share/shares present, finite and positive"
           if all(v is not None for v in (entry, stop, target, risk, shares))
           else "plan is missing a required numeric field (entry, stop, "
                "target, risk_per_share, shares) or one is not finite")
    _check("asset_type",
           plan.get("asset_type", "equity") in ("equity", "etf"),
           f"asset_type is {plan.get('asset_type', 'equity')!r} — research "
           "scope here is equity/ETF only")
    _check("short_blocked",
           not plan.get("short", False) and not cfg.get("allow_short", False),
           "short positions are blocked in this research config"
           if (plan.get("short", False) or cfg.get("allow_short", False))
           else "long only; shorts blocked")
    symbol = plan.get("symbol")
    _check("blocked_list",
           symbol not in cfg.get("blocked_symbols", []) and not plan.get("blocked", False),
           f"symbol {symbol!r} is on the blocked list"
           if symbol in cfg.get("blocked_symbols", []) or plan.get("blocked", False)
           else "symbol not on the blocked list")
    _check("size_limits",
           position_cost is not None and shares is not None and shares > 0
           and cfg["min_order_usd"] <= position_cost <= cfg["max_order_usd"],
           f"position cost {position_cost} must be within "
           f"[{cfg['min_order_usd']}, {cfg['max_order_usd']}] with shares > 0")
    _check("position_cap",
           position_cost is not None
           and position_cost <= budget * cfg["max_position_pct"],
           f"position cost {position_cost} must be at most "
           f"{budget * cfg['max_position_pct']} "
           f"({cfg['max_position_pct']:.0%} of budget)")
    _check("cash_buffer",
           cash - (position_cost or 0.0) >= budget * cfg["cash_buffer_pct"],
           f"cash after the trade must keep a {cfg['cash_buffer_pct']:.0%} "
           f"buffer of budget ({budget * cfg['cash_buffer_pct']})")
    _check("stop_required",
           (not cfg.get("require_stop", True))
           or (stop is not None and entry is not None and stop < entry),
           "a stop below the entry is required"
           if cfg.get("require_stop", True) else "no stop required by config")
    _check("loss_per_trade",
           max_loss is not None and max_loss <= cfg["max_loss_per_trade_usd"],
           f"max loss {max_loss} must be at most "
           f"{cfg['max_loss_per_trade_usd']} per trade")
    _check("reward_risk",
           reward_risk is not None and reward_risk >= cfg["min_reward_risk"],
           f"reward:risk {reward_risk} must be at least "
           f"{cfg['min_reward_risk']}")
    daily_loss = plan.get("daily_loss_usd", 0.0)
    _check("daily_loss",
           _is_number(daily_loss) and max_loss is not None
           and daily_loss + max_loss <= cfg["max_daily_loss_usd"],
           f"daily loss {daily_loss} + this trade's max loss {max_loss} "
           f"must stay within {cfg['max_daily_loss_usd']}")
    _check("trades_per_day",
           _is_number(plan.get("trades_today", 0))
           and plan.get("trades_today", 0) < cfg["max_trades_per_day"],
           f"trades today ({plan.get('trades_today', 0)}) must be below "
           f"{cfg['max_trades_per_day']}")
    _check("entry_window",
           plan.get("entry_window_ok") is True,
           "entry window not confirmed — set entry_window_ok=True only "
           "when the plan's entry window is actually open")
    _check("duplicate",
           not plan.get("is_duplicate", False),
           "duplicate of an existing planned position"
           if plan.get("is_duplicate", False) else "not a duplicate")
    _check("daily_order_limit",
           _is_number(plan.get("orders_today", 0))
           and plan.get("orders_today", 0) < cfg.get("max_orders_per_day",
                                                     cfg["max_trades_per_day"]),
           f"orders today ({plan.get('orders_today', 0)}) must be below "
           f"{cfg.get('max_orders_per_day', cfg['max_trades_per_day'])}")
    _check("paper_mode",
           cfg.get("paper", True) is True,
           "paper/research mode is off — live trading is not supported "
           "by this module"
           if cfg.get("paper", True) is not True
           else "research (paper) mode only — no live orders")

    failed = [name for name in GATE_CHECKS if not checks[name]["pass"]]
    fingerprint = hashlib.sha256(
        json.dumps({"plan": plan, "date": date_str},
                   sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {
        "verdict": "PASS" if not failed else "BLOCK",
        "date": date_str,
        "fingerprint": fingerprint,
        "fingerprint_note": "valid only for this exact plan on this date",
        "checks": {name: checks[name] for name in GATE_CHECKS},
        "passed": len(GATE_CHECKS) - len(failed),
        "failed": len(failed),
        "failed_checks": failed,
    }
