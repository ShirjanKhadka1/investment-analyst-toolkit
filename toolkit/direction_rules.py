"""Pre-registered direction rules for the public track record.

A rule turns a point-in-time close series into a direction — UP,
DOWN, or no call (None). The rules are registered HERE, with fixed
parameters, and never refit: changing a parameter is a new rule
version, not a silent edit. No parameter was tuned on live data:
the windows (50, 20) are the standard textbook windows, fixed at
registration on 2026-11-08.

How a rule is identified in every logged call:

- ``rule_id`` — e.g. ``sma50_trend``.
- ``rule_version`` — e.g. ``1.0.0``; a parameter change bumps it.
- ``rule_code_hash`` — the git blob hash (``git hash-object``) of
  THIS file at the time the call was logged, so any later edit to a
  rule is detectable from the call record alone.

Direction comes from the rule, not from a persona's discussion
flags: ``toolkit/rules_mode.py`` returns sections with flags, not
verdicts, and no code here invents a verdict from them. The
``persona`` recorded on a call is attribution — which watchlist
persona owns the ticker — not the source of the direction.

Original code for this toolkit. Research only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

__all__ = [
    "RULES",
    "TICKER_RULES",
    "direction_for",
    "evaluate_rule",
    "rule_code_hash",
]

# rule_id -> registration. "params" are FIXED at registration
# (2026-11-08); a change to any of them is a new version.
RULES = {
    "sma50_trend": {
        "version": "1.0.0",
        "params": {"window": 50},
        "definition": ("UP when the decision-day close is above the "
                       "50-day simple moving average of closes "
                       "(decision day included); DOWN when below; no "
                       "call when equal or when fewer than 50 closes "
                       "are visible."),
        "min_closes": 50,
    },
    "mom20_sign": {
        "version": "1.0.0",
        "params": {"window": 20},
        "definition": ("UP when the 20-trading-day return "
                       "(decision-day close / close 20 trading days "
                       "earlier - 1) is positive; DOWN when negative; "
                       "no call when zero or when fewer than 21 closes "
                       "are visible."),
        "min_closes": 21,
    },
}

# One rule per watchlist ticker, registered 2026-11-08. Equity
# subjects use the trend rule; FX and crypto — where a moving-average
# level has no venue meaning — use the momentum-sign rule. This
# assignment is a registration choice, not a fitted result.
TICKER_RULES = {
    "SPY": "sma50_trend",
    "AAPL": "sma50_trend",
    "VT": "sma50_trend",
    "NIFTYBEES.NS": "sma50_trend",
    "ISF.L": "sma50_trend",
    "EURUSD=X": "mom20_sign",
    "BTC-USD": "mom20_sign",
}


def rule_code_hash():
    """Git blob hash of this file (``git hash-object`` format).

    Computed with hashlib, no git binary needed: sha1 of
    ``b"blob <len>\\0" + file bytes``. Any edit to a rule changes
    the hash, so a logged call pins the exact rule code it ran.
    """
    data = Path(__file__).read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def _sma(closes, window):
    """Mean of the last ``window`` closes (None when too few)."""
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def evaluate_rule(rule_id, closes):
    """Direction for ``rule_id`` from a PIT close list (oldest first).

    ``closes`` is a list of floats, decision-day close last, visible
    point-in-time only — the caller must have cut it at the decision
    date (see ``toolkit/pit_store.py``). Returns ``"up"``, ``"down"``
    or None (no call: insufficient data, or an exact tie).
    """
    if rule_id not in RULES:
        raise ValueError(f"unknown direction rule {rule_id!r} — "
                         f"known: {', '.join(sorted(RULES))}")
    if not closes or any(c <= 0 for c in closes):
        raise ValueError("closes must be a non-empty list of positive "
                         "numbers")
    rule = RULES[rule_id]
    if len(closes) < rule["min_closes"]:
        return None
    if rule_id == "sma50_trend":
        average = _sma(closes, rule["params"]["window"])
        if closes[-1] > average:
            return "up"
        if closes[-1] < average:
            return "down"
        return None
    if rule_id == "mom20_sign":
        window = rule["params"]["window"]
        ret = closes[-1] / closes[-window - 1] - 1.0
        if ret > 0:
            return "up"
        if ret < 0:
            return "down"
        return None
    raise AssertionError(f"rule {rule_id!r} has no evaluator")


def direction_for(symbol, closes):
    """``(rule_id, direction)`` for a watchlist ticker's PIT closes."""
    if symbol not in TICKER_RULES:
        raise ValueError(f"no direction rule registered for {symbol!r} — "
                         f"registered: {', '.join(sorted(TICKER_RULES))}")
    rule_id = TICKER_RULES[symbol]
    return rule_id, evaluate_rule(rule_id, closes)
