"""One-command research terminal: ``python -m toolkit.terminal demo.csv``.

Reads a CSV of daily OHLCV bars and prints a research dashboard to
stdout: an indicator snapshot, named-setup screening matches, a
Time-Machine odds summary, and a risk-gate verdict on a hypothetical
exit plan. Zero dependencies (standard library only), no network, no
keys, no live trading, no orders.

``python -m toolkit.terminal research <SYMBOL>`` (engine 1.9) writes a
one-page HTML research report instead: thesis, bull/bear, peers and
sector comps, risks, cited sources — from caller-supplied CSVs
(``--bars``, ``--fundamentals``, ``--peers``). See
``toolkit/report.py``; if no data exists for the symbol, the report
says so plainly instead of inventing figures.

Research and education only: not investment advice, never a
buy/sell/hold recommendation. Every section carries its honesty
label in the printed output.

Attribution
-----------
- Indicators come from ``toolkit/indicators.py`` (FinceptTerminal
  defaults table, ideas only, AGPL-3.0, commit 7716ee8 — no Fincept
  code copied; see that module).
- The Time-Machine summary comes from ``toolkit/timemachine.py``
  (adapted from day-trader-skill trader-timemachine, MIT,
  commit 3d0f4ef).
- The risk gate comes from ``toolkit/position_plan.py`` (adapted from
  day-trader-skill trader-exit / trader-budget / trader-risk, MIT,
  commit 3d0f4ef).
- The five named setups are reimplemented here (not imported) so the
  toolkit stays self-contained. The rules are adapted from
  ``scanner/setups.py`` in nepali-capital-market-research (MIT), which
  adapted day-trader-skill's scanner rule table (MIT, commit 3d0f4ef).
  Thresholds and definitions are identical to the scanner's; only the
  code is rewritten against this toolkit's own ``sma``/``rsi``.

CSV format
----------
Header row ``date,open,high,low,close,volume`` (case-insensitive).
Only ``date`` and ``close`` are required; ``open``/``high``/``low``
default to the close (open defaults to the previous close) and
``volume`` is optional (VWAP is skipped without it). Lines starting
with ``#`` are treated as comments and skipped, so a file can carry
a provenance note above its header. A bad file — missing file, no
header, missing columns, non-numeric or non-positive prices, no data
rows — exits non-zero with a one-line ``error: ...`` message on
stderr and never a traceback.

Default hypothetical plan
-------------------------
Unless ``--stop``/``--target`` are given, the plan is built from the
last close: entry = last close, stop = entry - 1.5 * ATR(14),
target = entry + R * (entry - stop) with R = ``--risk-reward``
(default 2). With fewer than
14 bars ATR is unavailable and the fallback is a 5% stop at the same
reward:risk. The plan is hypothetical research only; the risk gate
also BLOCKs by default because the entry window is not confirmed —
pass ``--entry-window-ok`` only when the plan's entry window is
actually open.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import math
import sys

from . import indicators, position_plan, report, timemachine

__all__ = [
    "SETUP_DEFINITIONS",
    "SETUP_ORDER",
    "build_dashboard",
    "evaluate_setups",
    "load_bars",
    "main",
]

# The five named setups, in scanner order. Definitions are worded to
# match scanner/setups.py (nepali-capital-market-research, MIT).
SETUP_ORDER = [
    "breakout_20d",
    "bull_flag",
    "reclaim_ma20",
    "oversold",
    "oversold_bounce",
]

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

_LABEL_NOT_ADVICE = (
    "Research and education only — not investment advice, "
    "never a buy/sell/hold recommendation."
)
_LABEL_SIMULATION = "Simulation, not a prediction."
_LABEL_MATCH = (
    "A match means the past closes satisfy the stated rule. "
    "It is not a buy/sell/hold signal."
)


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def _parse_float(row_no, field, raw):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"row {row_no}: {field} {raw!r} is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"row {row_no}: {field} must be finite, got {raw!r}")
    return value


def load_bars(path):
    """Load daily OHLCV bars from a CSV file.

    Returns a list of dicts with keys date/open/high/low/close/volume
    (volume is None when the column is absent). Raises ValueError with
    a plain message — never a traceback — on any malformed input, and
    lets OSError (e.g. file not found) propagate to the caller, which
    reports it the same way.
    """
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            lines = [ln for ln in handle
                     if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc.strerror or exc}") from None
    if not lines:
        raise ValueError(f"{path}: file is empty (no header, no data rows)")
    reader = csv.DictReader(lines)
    fields = {(name or "").strip().lower(): name
              for name in (reader.fieldnames or [])}
    if "date" not in fields or "close" not in fields:
        raise ValueError(
            f"{path}: CSV must have 'date' and 'close' columns, "
            f"found {sorted(fields)}")
    bars = []
    for row_no, row in enumerate(reader, start=2):
        date_raw = (row.get(fields["date"]) or "").strip()
        if not date_raw:
            raise ValueError(f"row {row_no}: date is empty")
        try:
            datetime.date.fromisoformat(date_raw)
        except ValueError:
            raise ValueError(
                f"row {row_no}: date {date_raw!r} is not YYYY-MM-DD") from None
        close = _parse_float(row_no, "close", row.get(fields["close"]))
        if close <= 0:
            raise ValueError(
                f"row {row_no}: close must be positive, got {close}")
        bar = {"date": date_raw, "close": close,
               "open": None, "high": None, "low": None, "volume": None}
        if "open" in fields and (row.get(fields["open"]) or "").strip():
            bar["open"] = _parse_float(row_no, "open", row.get(fields["open"]))
        if "high" in fields and (row.get(fields["high"]) or "").strip():
            bar["high"] = _parse_float(row_no, "high", row.get(fields["high"]))
        if "low" in fields and (row.get(fields["low"]) or "").strip():
            bar["low"] = _parse_float(row_no, "low", row.get(fields["low"]))
        if "volume" in fields and (row.get(fields["volume"]) or "").strip():
            bar["volume"] = _parse_float(
                row_no, "volume", row.get(fields["volume"]))
        for key in ("open", "high", "low"):
            if bar[key] is not None and bar[key] <= 0:
                raise ValueError(
                    f"row {row_no}: {key} must be positive, got {bar[key]}")
        if bar["volume"] is not None and bar["volume"] < 0:
            raise ValueError(
                f"row {row_no}: volume must not be negative, "
                f"got {bar['volume']}")
        if bar["high"] is not None and bar["low"] is not None \
                and bar["high"] < bar["low"]:
            raise ValueError(
                f"row {row_no}: high ({bar['high']}) is below low "
                f"({bar['low']})")
        if bar["high"] is not None and bar["close"] > bar["high"]:
            raise ValueError(
                f"row {row_no}: close ({bar['close']}) is above high "
                f"({bar['high']})")
        if bar["low"] is not None and bar["close"] < bar["low"]:
            raise ValueError(
                f"row {row_no}: close ({bar['close']}) is below low "
                f"({bar['low']})")
        bars.append(bar)
    if not bars:
        raise ValueError(f"{path}: CSV has a header but no data rows")
    for i, bar in enumerate(bars):
        if bar["open"] is None:
            bar["open"] = bars[i - 1]["close"] if i else bar["close"]
        if bar["high"] is None:
            bar["high"] = bar["close"]
        if bar["low"] is None:
            bar["low"] = bar["close"]
    return bars


# ---------------------------------------------------------------------------
# Named setups (reimplemented from scanner/setups.py — see module header)
# ---------------------------------------------------------------------------

def _breakout_20d(closes):
    if len(closes) < 21:
        return False, f"too few closes (need 21, have {len(closes)})"
    level = max(closes[-21:-1])
    close = closes[-1]
    if close <= level:
        return False, (f"close {close:.2f} not above 20-day high "
                       f"{level:.2f}")
    return True, (f"close {close:.2f} above 20-day high {level:.2f} "
                  f"(+{100.0 * (close / level - 1):.2f}%)")


def _bull_flag(closes):
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


def _reclaim_ma20(closes):
    if len(closes) < 21:
        return False, f"too few closes (need 21, have {len(closes)})"
    ma = indicators.sma(closes, 20)
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


def _oversold(closes):
    if len(closes) < 15:
        return False, f"too few closes (need 15, have {len(closes)})"
    value = indicators.rsi(closes)[-1]
    if value is None:
        return False, "RSI-14 not defined yet"
    if value >= 30:
        return False, f"RSI-14 {value:.1f} (not below 30)"
    return True, f"RSI-14 {value:.1f} (below 30)"


def _oversold_bounce(closes):
    if len(closes) < 15:
        return False, f"too few closes (need 15, have {len(closes)})"
    if closes[-1] <= closes[-2]:
        return False, (f"latest close {closes[-1]:.2f} not above prior "
                       f"close {closes[-2]:.2f}")
    series = indicators.rsi(closes)
    recent = [v for v in series[-5:] if v is not None]
    if not recent or min(recent) >= 30:
        return False, "RSI-14 not below 30 in the last 5 sessions"
    return True, (f"RSI-14 {series[-1]:.1f} (low {min(recent):.1f} in "
                  f"last 5), close {closes[-1]:.2f} above prior "
                  f"{closes[-2]:.2f}")


_SETUP_FUNCTIONS = {
    "breakout_20d": _breakout_20d,
    "bull_flag": _bull_flag,
    "reclaim_ma20": _reclaim_ma20,
    "oversold": _oversold,
    "oversold_bounce": _oversold_bounce,
}


def evaluate_setups(closes):
    """Evaluate the five named setups on a closes list.

    Returns {name: (matched, detail)} in SETUP_ORDER. A match is a
    statement about past closes only — never a signal.
    """
    return {name: _SETUP_FUNCTIONS[name](list(closes))
            for name in SETUP_ORDER}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def _fmt(value, places=2, need=None):
    """Format a possibly-None indicator value, or 'n/a (needs N bars)'."""
    if value is None:
        return f"n/a (needs {need} bars)" if need else "n/a"
    return f"{value:.{places}f}"


def _default_plan(entry, atr_value, risk_reward=2.0):
    """Default hypothetical plan: stop 1.5x ATR below entry (or a 5%
    stop when ATR is unavailable), target at the given reward:risk."""
    if atr_value is not None and atr_value > 0:
        stop = entry - 1.5 * atr_value
        basis = "stop = entry - 1.5 x ATR(14)"
    else:
        stop = entry * 0.95
        basis = "stop = entry - 5% (ATR(14) unavailable)"
    target = entry + risk_reward * (entry - stop)
    return stop, target, basis


def _format_health_section(data_health):
    """Render the data-health section. ``data_health`` is a dict as
    written by scripts/data_health.py, or None when no health check
    was run — in which case the dashboard says so plainly instead of
    implying the data was checked."""
    lines = []
    lines.append("")
    lines.append("5. Data health")
    if data_health is None:
        lines.append("   Not checked in this run (run "
                     "scripts/data_health.py). Stale data is never "
                     "published as fresh — but this dashboard cannot "
                     "prove its inputs are fresh either.")
        return lines
    generated_at = data_health.get("generated_at", "UNKNOWN")
    overall = data_health.get("overall", "UNKNOWN")
    lines.append(f"   Overall: {overall} (as of {generated_at})")
    bad = False
    for source in data_health.get("sources", []) or []:
        status = source.get("status", "UNKNOWN")
        if status in ("STALE", "UNAVAILABLE"):
            bad = True
        last = source.get("last_data_date")
        age = source.get("age_days")
        sla = source.get("sla_label", "?")
        line = (f"   {status:11s} {source.get('name', '?'):11s} "
                f"last data {last}  age {age}d (SLA {sla})")
        if source.get("error"):
            line += f" — {source['error']}"
        lines.append(line)
    if bad:
        lines.append("   WARNING: a source above is STALE or UNAVAILABLE. "
                     "Do not treat this dashboard as fresh until the "
                     "health check is FRESH again.")
    return lines


def build_dashboard(bars, symbol="DEMO", seed=19, n_sims=10000,
                    horizon=20, entry=None, stop=None, target=None,
                    risk_reward=2.0, budget=10000.0,
                    entry_window_ok=False, data_health=None):
    """Build the dashboard text for loaded bars. Pure function of its
    inputs (fixed seed, no clock, no network) — same input, same text.
    Raises ValueError on a bad user-supplied plan (bad --stop/--target);
    data-driven shortfalls are reported inside the sections instead.
    The dashboard assumes exchange NEPSE (the risk gate uses the
    default config, which is the NEPSE-derived config) and PRINTS
    that assumption in the header — the demo never detects the market
    from the CSV, so the assumption is stated, not hidden.
    """
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b["volume"] for b in bars] \
        if all(b["volume"] is not None for b in bars) else None
    n = len(bars)
    last = bars[-1]
    entry = last["close"] if entry is None else entry
    if entry <= 0:
        raise ValueError(f"--entry must be positive, got {entry}")
    if (stop is None) != (target is None):
        raise ValueError("give both --stop and --target, or neither")
    if stop is not None and stop >= entry:
        raise ValueError(f"--stop ({stop}) must be below entry ({entry})")
    if target is not None and target <= entry:
        raise ValueError(f"--target ({target}) must be above entry ({entry})")

    lines = []
    lines.append("investment-analyst-toolkit — terminal dashboard")
    lines.append(_LABEL_NOT_ADVICE)
    lines.append("")
    lines.append(f"Symbol: {symbol}   Bars: {n}   "
                 f"Range: {bars[0]['date']} to {last['date']}   "
                 f"Last close: {last['close']:.2f}")
    lines.append("Exchange (assumed): NEPSE — the demo does not detect "
                 "the market from the CSV; the risk gate below uses the "
                 "NEPSE default config.")

    # --- 1. Indicator snapshot -------------------------------------------
    lines.append("")
    lines.append("1. Indicator snapshot")
    lines.append("   Indicators describe the past; they cannot prove what "
                 "a price will do next.")
    all_ind = indicators.compute_all(closes, highs, lows, volumes)

    def last_of(key):
        return all_ind[key][-1]

    rsi_v = last_of("rsi_14")
    macd_v, sig_v, hist_v = (last_of("macd"), last_of("macd_signal"),
                             last_of("macd_histogram"))
    bb_u, bb_m, bb_l = (last_of("bollinger_upper"),
                        last_of("bollinger_middle"),
                        last_of("bollinger_lower"))
    atr_v = last_of("atr_14")
    sma_v, ema_v = last_of("sma_20"), last_of("ema_20")
    adx_v = last_of("adx_14")
    stoch_k, stoch_d = last_of("stochastic_k"), last_of("stochastic_d")
    if sma_v is not None:
        trend = ("above SMA20 and above EMA20" if last["close"] > sma_v
                 and ema_v is not None and last["close"] > ema_v else
                 "below SMA20" if last["close"] < sma_v else
                 "mixed vs SMA20/EMA20")
    else:
        trend = "n/a (needs 20 bars)"
    pct_b = ("n/a" if bb_u is None or bb_l is None or bb_u == bb_l else
             f"{(last['close'] - bb_l) / (bb_u - bb_l):.2f}")
    lines.append(f"   Close {last['close']:.2f} — trend vs SMA20/EMA20: "
                 f"{trend}")
    lines.append(f"   RSI(14)      {_fmt(rsi_v, 1, 15)}"
                 f"   (70/30 are conventions, not reversal proofs)")
    lines.append(f"   MACD 12/26/9 {_fmt(macd_v, 2, 34)}"
                 f"   signal {_fmt(sig_v, 2, 34)}"
                 f"   histogram {_fmt(hist_v, 2, 34)}")
    lines.append(f"   Bollinger 20/2  upper {_fmt(bb_u, 2, 20)}"
                 f"   middle {_fmt(bb_m, 2, 20)}"
                 f"   lower {_fmt(bb_l, 2, 20)}   %B {pct_b}")
    lines.append(f"   ATR(14)      {_fmt(atr_v, 2, 14)}"
                 f"   (how much prices moved, not which way next)")
    lines.append(f"   SMA20 {_fmt(sma_v, 2, 20)}   EMA20 {_fmt(ema_v, 2, 20)}"
                 f"   ADX(14) {_fmt(adx_v, 1, 27)}"
                 f"   Stoch %K {_fmt(stoch_k, 1, 14)}"
                 f"   %D {_fmt(stoch_d, 1, 16)}")
    if volumes is not None:
        lines.append(f"   VWAP (cumulative) {_fmt(last_of('vwap'), 2, 1)}")
    else:
        lines.append("   VWAP n/a (no volume column in this CSV)")

    # --- 2. Screening ------------------------------------------------------
    lines.append("")
    lines.append("2. Screening — named setups (scanner rules, "
                 "reimplemented here)")
    lines.append(f"   {_LABEL_MATCH}")
    results = evaluate_setups(closes)
    n_match = sum(1 for matched, _ in results.values() if matched)
    for name in SETUP_ORDER:
        matched, detail = results[name]
        lines.append(f"   [{'MATCH' if matched else '  --  '}] "
                     f"{name}: {detail}")
        lines.append(f"          rule: {SETUP_DEFINITIONS[name]}")
    lines.append(f"   Matches: {n_match} of {len(SETUP_ORDER)} setups. "
                 "A match is a description of past closes, not a "
                 "prediction.")

    # --- 3. Time-Machine odds ----------------------------------------------
    lines.append("")
    lines.append("3. Time-Machine odds")
    lines.append(f"   {_LABEL_SIMULATION} Resampled history is not the "
                 "future; a target-first share above break-even is a "
                 "property of the past sample, not a probability of profit.")
    if stop is None:
        stop, target, basis = _default_plan(entry, atr_v, risk_reward)
        user_plan = False
    else:
        basis = "stop/target supplied with --stop/--target"
        user_plan = True
    # Canonicalise the plan through plan_exit() once, so sections 3
    # and 4 use the same rounded stop/target. (plan_exit rounds the
    # stop down to the cent; a target computed from the unrounded
    # stop can sit a hair below R=2 afterwards and be refused.)
    exit_plan = None
    plan_error = None
    try:
        if user_plan:
            exit_plan = position_plan.plan_exit(
                entry, stop, target=target, risk_reward=risk_reward)
        else:
            exit_plan = position_plan.plan_exit(
                entry, stop, risk_reward=risk_reward)
        stop, target = exit_plan["stop"], exit_plan["target"]
    except ValueError as exc:
        plan_error = str(exc)
    lines.append(f"   Hypothetical plan: entry {entry:.2f}, stop "
                 f"{stop:.2f}, target {target:.2f} ({basis}; "
                 f"reward:risk {(target - entry) / (entry - stop):.2f})")
    ohlc = [{"open": b["open"], "high": b["high"], "low": b["low"],
             "close": b["close"]} for b in bars]
    try:
        sim = timemachine.simulate(ohlc, entry, stop, target,
                                   n_sims=n_sims, seed=seed,
                                   horizon=horizon)
    except ValueError as exc:
        lines.append(f"   No odds summary: {exc}")
        sim = None
    if sim is not None:
        lines.append(f"   Seed {sim['seed']}, {sim['n_sims']} replays, "
                     f"horizon {sim['horizon']} days, "
                     f"{sim['n_windows']} past windows")
        lines.append(f"   Target first {100.0 * sim['target_first']:.1f}%"
                     f"   Stop first {100.0 * sim['stop_first']:.1f}%"
                     f"   Neither {100.0 * sim['neither']:.1f}%")
        lines.append(f"   Break-even {100.0 * sim['break_even']:.1f}%"
                     f"   Verdict: {sim['verdict']}")
        lines.append(f"   Final close: median {sim['median_close']:.2f}, "
                     f"p5 {sim['close_p5']:.2f}, p95 "
                     f"{sim['close_p95']:.2f}, average realised R "
                     f"{sim['average_R']:.2f}")
        replay = timemachine.replay_history(ohlc, entry, stop, target,
                                            horizon=horizon)
        lines.append(f"   Historical replay (same rule, no randomness): "
                     f"target first {100.0 * replay['target_first']:.1f}%, "
                     f"stop first {100.0 * replay['stop_first']:.1f}%, "
                     f"neither {100.0 * replay['neither']:.1f}% over "
                     f"{replay['n_windows']} past windows")

    # --- 4. Risk gate --------------------------------------------------------
    lines.append("")
    lines.append("4. Risk gate — hypothetical plan only")
    lines.append("   A PASS is not a yes and not a recommendation: it only "
                 "means this exact plan passed these 16 checks on the "
                 "last bar's date.")
    config = dict(position_plan.DEFAULT_RESEARCH_CONFIG)
    config["budget"] = budget
    gate = None
    sized = None
    if exit_plan is None:
        lines.append(f"   No risk-gate verdict: {plan_error}")
    else:
        try:
            sized = position_plan.size_position(
                budget, config["max_loss_per_trade_usd"],
                config["max_position_pct"], config["max_order_usd"],
                entry, exit_plan["risk_per_share"],
                min_order_usd=config["min_order_usd"])
            full_plan = dict(exit_plan)
            full_plan.update(sized)
            full_plan.update({
                "symbol": symbol,
                "asset_type": "equity",
                "date": last["date"],
                "entry_window_ok": entry_window_ok,
                "cash_available": budget,
            })
            gate = position_plan.risk_gate(full_plan, config=config)
        except ValueError as exc:
            lines.append(f"   No risk-gate verdict: {exc}")
            gate = None
    if gate is not None:
        lines.append(f"   Plan: entry {exit_plan['entry']:.2f}, stop "
                     f"{exit_plan['stop']:.2f}, target "
                     f"{exit_plan['target']:.2f}, "
                     f"risk/share {exit_plan['risk_per_share']:.2f} "
                     f"({100.0 * exit_plan['risk_pct_of_entry']:.2f}% of "
                     f"entry), "
                     f"reward:risk {exit_plan['reward_risk']:.2f}")
        lines.append(f"   Size: {sized['shares']} "
                     f"{'share' if sized['shares'] == 1 else 'shares'}, "
                     f"cost "
                     f"{sized['position_cost']:.2f}, max loss "
                     f"{sized['max_loss']:.2f} "
                     f"(limiting factor: {sized['limiting_factor']})")
        lines.append(f"   Verdict: {gate['verdict']} "
                     f"({gate['passed']}/16 checks passed)")
        for name in position_plan.GATE_CHECKS:
            check = gate["checks"][name]
            if check["pass"]:
                lines.append(f"   PASS  {name}")
            else:
                lines.append(f"   BLOCK {name} — {check['reason']}")
        lines.append(f"   Fingerprint: {gate['fingerprint'][:16]}… "
                     "(valid only for this exact plan on "
                     f"{gate['date']})")
    lines.extend(_format_health_section(data_health))
    lines.append("")
    lines.append(_LABEL_NOT_ADVICE)
    lines.append("No live trading, no orders, no network, no keys. "
                 "All numbers come from the CSV you supplied.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m toolkit.terminal",
        description="One-command research dashboard for a CSV of daily "
                    "OHLCV bars. Research and education only — not "
                    "investment advice.")
    parser.add_argument("csv", help="CSV of daily bars: "
                        "date,open,high,low,close,volume "
                        "(only date and close are required)")
    parser.add_argument("--symbol", default="DEMO",
                        help="label printed in the header (default: DEMO)")
    parser.add_argument("--entry", type=float, default=None,
                        help="hypothetical entry price "
                             "(default: last close)")
    parser.add_argument("--stop", type=float, default=None,
                        help="hypothetical stop (needs --target too)")
    parser.add_argument("--target", type=float, default=None,
                        help="hypothetical target (needs --stop too)")
    parser.add_argument("--risk-reward", type=float, default=2.0,
                        help="reward:risk of the default plan "
                             "(default: 2.0)")
    parser.add_argument("--horizon", type=int, default=20,
                        help="Time-Machine horizon in days (default: 20)")
    parser.add_argument("--n-sims", type=int, default=10000,
                        help="Time-Machine replays (default: 10000)")
    parser.add_argument("--seed", type=int, default=19,
                        help="Time-Machine seed; same seed, same output "
                             "(default: 19)")
    parser.add_argument("--budget", type=float, default=10000.0,
                        help="hypothetical research budget for sizing "
                             "(default: 10000.0)")
    parser.add_argument("--entry-window-ok", action="store_true",
                        help="assert the plan's entry window is actually "
                             "open (default: not confirmed, so the risk "
                             "gate BLOCKs on entry_window)")
    parser.add_argument("--data-health", default=None,
                        help="data_health.json from "
                             "scripts/data_health.py, shown as section 5 "
                             "(default: not checked in this run)")
    return parser


def _build_research_parser():
    parser = argparse.ArgumentParser(
        prog="python -m toolkit.terminal research",
        description="One-page HTML research report for a symbol, from "
                    "caller-supplied CSVs. Research and education only — "
                    "not investment advice.")
    parser.add_argument("symbol", help="symbol the report is about "
                        "(e.g. a NEPSE ticker)")
    parser.add_argument("--bars", default=None,
                        help="CSV of daily bars: date,close "
                             "(see toolkit.report.bars_from_csv)")
    parser.add_argument("--fundamentals", default=None,
                        help="CSV of fundamentals rows: "
                             "field,value,source,period")
    parser.add_argument("--peers", default=None,
                        help="CSV of peer rows: symbol,metric,value,source")
    parser.add_argument("--sector", default=None,
                        help="sector label for the report header")
    parser.add_argument("--exchange", default="NEPSE",
                        help="exchange label (default: NEPSE)")
    parser.add_argument("--as-of", default=None,
                        help="as-of date YYYY-MM-DD (default: today)")
    parser.add_argument("--out", default=None,
                        help="where to write the HTML report "
                             "(default: reports/<SYMBOL>-research.html)")
    parser.add_argument("--stdout", action="store_true",
                        help="print the HTML to stdout instead of writing "
                             "a file")
    return parser


def _research_main(argv):
    """``research <SYMBOL>`` entry point. Returns an exit code."""
    args = _build_research_parser().parse_args(argv)
    try:
        bars = report.bars_from_csv(args.bars) if args.bars else None
        fundamentals = (report.fundamentals_from_csv(args.fundamentals)
                        if args.fundamentals else None)
        peers = (report.peers_from_csv(args.peers)
                 if args.peers else None)
        built = report.build_report(
            args.symbol, bars=bars, fundamentals=fundamentals,
            peers=peers, sector=args.sector, exchange=args.exchange,
            as_of=args.as_of)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.stdout:
        sys.stdout.write(report.render_html(built))
        return 0
    out = args.out or f"reports/{built['symbol']}-research.html"
    try:
        report.write_report_html(built, out)
    except OSError as exc:
        print(f"error: cannot write {out}: {exc.strerror or exc}",
              file=sys.stderr)
        return 2
    print(f"Research report for {built['symbol']}: {out}")
    print(f"Status: {built['status']} (as of {built['as_of']})")
    print(f"Thesis: {built['thesis']}")
    print(_LABEL_NOT_ADVICE)
    return 0


def main(argv=None):
    """CLI entry point. Returns an exit code; never raises on bad input
    (bad input prints ``error: ...`` to stderr and returns 2)."""
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "research":
        return _research_main(argv[1:])
    args = _build_parser().parse_args(argv)
    try:
        bars = load_bars(args.csv)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    data_health = None
    if args.data_health:
        try:
            with open(args.data_health, encoding="utf-8") as handle:
                data_health = json.load(handle)
        except (OSError, ValueError) as exc:
            print(f"error: cannot read data-health file "
                  f"{args.data_health}: {exc}", file=sys.stderr)
            return 2
    try:
        text = build_dashboard(
            bars, symbol=args.symbol, seed=args.seed, n_sims=args.n_sims,
            horizon=args.horizon, entry=args.entry, stop=args.stop,
            target=args.target, risk_reward=args.risk_reward,
            budget=args.budget, entry_window_ok=args.entry_window_ok,
            data_health=data_health)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 -- bad input must never surface
        # a traceback from the CLI; every failure is a one-line error.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
