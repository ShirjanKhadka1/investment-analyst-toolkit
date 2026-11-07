"""No-LLM rules mode for the NEPSE agent team — deterministic, offline.

Engine 1.3 (Phase 1): every NEPSE agent in finance-research-agents
works with any LLM, including a free local Ollama, and this module is
the fallback that lets the core run with zero API keys and zero LLM
at all. Every function here is a pure, deterministic rule over inputs
the caller supplies; nothing is fetched, nothing is guessed, and
nothing in this module is a signal, a rating, or investment advice.
Outputs are structured dicts the agent files' output schemas mirror,
so an LLM run and a rules run produce the same shape.

Rules are deliberately conservative: a rule that cannot be evaluated
from the inputs returns ``"UNKNOWN"`` with a reason, never a filled-in
value. Broker-flow output is always labelled indicative, never proven.
"""

from __future__ import annotations

import math

__all__ = [
    "RULES_MODE_VERSION",
    "broker_flow_read",
    "corporate_action_flags",
    "macro_checklist",
    "run_rules_team",
    "sector_fundamentals_check",
    "sentiment_score",
    "technical_read",
]

RULES_MODE_VERSION = "1.3.0"

# --- Sentiment lexicons (keyword fallback, design only) -----------------
# English words and Nepali words (Devanagari + common Romanised forms).
# A headline matching only these words is a keyword count, not proof
# of market sentiment; the score is a count with a label, nothing more.
_POSITIVE_EN = ("growth", "profit", "record", "surge", "gain", "rise",
                "dividend", "bonus", "expansion", "recovery", "strong")
_NEGATIVE_EN = ("loss", "fall", "drop", "decline", "fraud", "default",
                "penalty", "probe", "crash", "slump", "weak", "lawsuit")
_POSITIVE_NE = ("वृद्धि", "नाफा", "लाभ", "बढ्यो", "बढेको", "सुधार",
                "badhyo", "nafa", "labh", "badheko")
_NEGATIVE_NE = ("घाटा", "गिरावट", "घट्यो", "हानि", "ठगी", "संकट",
                "ghata", "ghirawat", "ghatyo", "sankat")


def _rsi(closes, period=14):
    """Classic Wilder RSI over closes; None when not enough data."""
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains += change
        else:
            losses -= change
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))


def technical_read(closes):
    """Deterministic technical read over a list of closes (oldest first).

    Returns momentum vs 20 bars ago, price vs 20-bar mean, 20-bar
    volatility (std of daily returns), and RSI(14). Any figure that
    cannot be computed from the supplied bars is None with a note.
    This describes the past bars only; it predicts nothing.
    """
    closes = [float(c) for c in closes]
    if len(closes) < 2:
        raise ValueError("technical_read needs at least 2 closes")
    out = {"n_bars": len(closes), "last_close": closes[-1]}
    if len(closes) >= 21:
        out["momentum_20d_pct"] = (closes[-1] / closes[-21] - 1.0) * 100.0
        window = closes[-20:]
        out["mean_20d"] = sum(window) / len(window)
        out["price_vs_mean_20d_pct"] = (closes[-1] / out["mean_20d"] - 1.0) * 100.0
    else:
        out["momentum_20d_pct"] = None
        out["mean_20d"] = None
        out["price_vs_mean_20d_pct"] = None
        out["note_20d"] = "fewer than 21 bars: 20-day figures UNKNOWN"
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))
            if closes[i - 1] != 0]
    if rets:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        out["volatility_daily_pct"] = math.sqrt(var) * 100.0
    else:
        out["volatility_daily_pct"] = None
    out["rsi_14"] = _rsi(closes)
    if out["rsi_14"] is None:
        out["note_rsi"] = "fewer than 15 bars: RSI(14) UNKNOWN"
    out["reading"] = ("past bars only — momentum, mean-reversion and "
                      "volatility describe history, not the next move")
    return out


def sector_fundamentals_check(sector, metrics):
    """Sector-specific checklist for a NEPSE sector.

    ``sector`` is one of banks, hydropower, insurance, microfinance
    (anything else returns UNKNOWN guidance). ``metrics`` maps the
    sector's key ratios to numbers the caller sourced; a missing
    metric is reported UNKNOWN, never filled in. Thresholds below
    are rules-of-thumb flags for discussion, not pass/fail verdicts
    and not advice.
    """
    sector = str(sector).lower()
    guidance = {
        "banks": {"metrics": ("capital_adequacy_pct", "npl_pct",
                              "net_interest_margin_pct", "cost_to_income_pct",
                              "credit_deposit_ratio_pct"),
                  "flags": (("npl_pct", 5.0, "above", "NPL above 5% — ask why"),
                            ("capital_adequacy_pct", 11.0, "below",
                             "capital adequacy below 11% — ask why"))},
        "hydropower": {"metrics": ("ppa_rate_npr_per_kwh", "plant_load_factor_pct",
                                   "debt_to_equity", "interest_coverage",
                                   "years_to_ppa_expiry"),
                       "flags": (("interest_coverage", 1.5, "below",
                                  "interest coverage below 1.5x — ask why"),)},
        "insurance": {"metrics": ("solvency_margin_ratio", "combined_ratio_pct",
                                  "premium_growth_pct", "claim_ratio_pct"),
                      "flags": (("combined_ratio_pct", 100.0, "above",
                                 "combined ratio above 100% — underwriting loss"),)},
        "microfinance": {"metrics": ("npl_pct", "cost_of_funds_pct",
                                     "operating_expense_ratio_pct",
                                     "capital_adequacy_pct"),
                         "flags": (("npl_pct", 5.0, "above",
                                    "NPL above 5% — ask why"),)},
    }
    if sector not in guidance:
        return {"sector": sector, "status": "UNKNOWN",
                "reason": "no sector guidance for this sector — "
                          "use the generic Fundamentals agent only"}
    spec = guidance[sector]
    rows = []
    for name in spec["metrics"]:
        value = metrics.get(name)
        rows.append({"metric": name,
                     "value": value if value is not None else "UNKNOWN",
                     "sourced": value is not None})
    flags = []
    for name, threshold, direction, text in spec["flags"]:
        value = metrics.get(name)
        if value is None:
            continue
        triggered = (value > threshold if direction == "above"
                     else value < threshold)
        if triggered:
            flags.append({"metric": name, "value": value,
                          "threshold": threshold, "flag": text})
    return {"sector": sector, "status": "checked", "metrics": rows,
            "flags": flags,
            "note": "flags are discussion prompts, not verdicts"}


def sentiment_score(headlines):
    """Lexicon sentiment count over headlines.

    ``headlines`` is a list of dicts with ``text`` and optional
    ``lang`` (``"ne"`` or ``"en"``) and ``source``. Returns counts by
    stance and a label (BULLISH / BEARISH / NEUTRAL) that is a
    keyword count only. A headline with no lexicon hit is unclassified,
    never forced into a stance.
    """
    counts = {"positive": 0, "negative": 0, "unclassified": 0, "total": 0}
    items = []
    for head in headlines:
        text = str(head.get("text", ""))
        counts["total"] += 1
        lowered = text.lower()
        pos = sum(1 for w in _POSITIVE_EN if w in lowered)
        pos += sum(1 for w in _POSITIVE_NE if w in text or w in lowered)
        neg = sum(1 for w in _NEGATIVE_EN if w in lowered)
        neg += sum(1 for w in _NEGATIVE_NE if w in text or w in lowered)
        if pos > neg:
            stance, counts["positive"] = "positive", counts["positive"] + 1
        elif neg > pos:
            stance, counts["negative"] = "negative", counts["negative"] + 1
        else:
            stance, counts["unclassified"] = "unclassified", counts["unclassified"] + 1
        items.append({"text": text, "source": head.get("source", "UNKNOWN"),
                      "stance": stance, "pos_hits": pos, "neg_hits": neg})
    if counts["total"] == 0:
        label = "NO HEADLINES"
    elif counts["positive"] > counts["negative"]:
        label = "BULLISH"
    elif counts["negative"] > counts["positive"]:
        label = "BEARISH"
    else:
        label = "NEUTRAL"
    return {"label": label, "counts": counts, "items": items,
            "note": "lexicon keyword count only — not evidence about "
                    "fundamentals and not a signal"}


def broker_flow_read(rows):
    """Broker-flow read — indicative only, never proven.

    ``rows`` is a list of dicts with ``broker`` (number or name),
    ``buy_value`` and ``sell_value`` (same currency). Returns the
    concentration of the top broker by buy value and the net flow.
    Every output carries the label that broker flow is indicative:
    broker identity does not prove who the ultimate buyer is, and
    a flow pattern is not evidence of intent.
    """
    if not rows:
        return {"status": "UNKNOWN", "reason": "no broker-flow rows supplied",
                "label": "indicative only — never proven"}
    total_buy = sum(float(r.get("buy_value", 0.0)) for r in rows)
    total_sell = sum(float(r.get("sell_value", 0.0)) for r in rows)
    ranked = sorted(rows, key=lambda r: float(r.get("buy_value", 0.0)),
                    reverse=True)
    top = ranked[0]
    top_share = (float(top.get("buy_value", 0.0)) / total_buy
                 if total_buy else 0.0)
    return {"status": "checked", "total_buy_value": total_buy,
            "total_sell_value": total_sell,
            "net_flow": total_buy - total_sell,
            "top_broker_by_buy": top.get("broker", "UNKNOWN"),
            "top_broker_buy_share_pct": top_share * 100.0,
            "n_brokers": len(rows),
            "label": "INDICATIVE ONLY — broker flow never proves who "
                     "bought, why, or what happens next"}


def macro_checklist(inputs):
    """NRB / macro checklist. Missing inputs are UNKNOWN, never filled."""
    fields = ("nrb_policy_rate_pct", "liquidity_surplus_npr_bn",
              "remittance_growth_pct", "inflation_pct",
              "nepse_index_level", "interest_rate_trend")
    rows = []
    for name in fields:
        value = inputs.get(name)
        rows.append({"field": name,
                     "value": value if value is not None else "UNKNOWN",
                     "sourced": value is not None})
    return {"rows": rows,
            "note": "each field needs its source + as-of date; "
                    "an UNKNOWN field blocks any claim that depends on it"}


def corporate_action_flags(actions):
    """Flag corporate actions and what each one changes for a holder.

    ``actions`` is a list of dicts with ``type`` (bonus, right,
    cash_dividend, stock_split, merger, other), ``detail`` and
    ``date``. Unknown types are kept as-is and flagged for review.
    """
    known = {"bonus": "new shares issued free — share count rises, "
                      "per-share figures change",
             "right": "existing holders offered new shares, usually "
                      "at a discount — dilution unless taken up",
             "cash_dividend": "cash paid out — price typically adjusts "
                              "by the dividend on ex-date",
             "stock_split": "share count rises, price falls in "
                            "proportion — no value change by itself",
             "merger": "company structure changes — read the swap ratio "
                       "and the filing, not the headline"}
    out = []
    for action in actions:
        kind = str(action.get("type", "other")).lower()
        out.append({"type": kind,
                    "date": action.get("date", "UNKNOWN"),
                    "detail": action.get("detail", ""),
                    "effect": known.get(kind, "UNKNOWN type — read the "
                                              "filing before assuming "
                                              "any effect")})
    return {"actions": out, "n_actions": len(out)}


def run_rules_team(symbol, exchange, inputs):
    """Run every rules-mode agent over supplied inputs, one dict out.

    ``inputs`` may carry ``closes``, ``sector``, ``metrics``,
    ``headlines``, ``broker_rows``, ``macro`` and ``actions``; any
    missing key yields that section as UNKNOWN. The returned dict
    mirrors the NEPSE agent team's structured output schema.
    """
    if not symbol or not exchange:
        raise ValueError("run_rules_team needs a symbol and an exchange")
    sections = {"symbol": symbol, "exchange": exchange,
                "rules_mode_version": RULES_MODE_VERSION,
                "llm": "none — deterministic rules mode, zero API keys"}
    closes = inputs.get("closes")
    sections["technical"] = (technical_read(closes) if closes else
                             {"status": "UNKNOWN",
                              "reason": "no closes supplied"})
    sections["fundamentals"] = sector_fundamentals_check(
        inputs.get("sector", ""), inputs.get("metrics") or {})
    sections["sentiment"] = sentiment_score(inputs.get("headlines") or [])
    sections["broker_flow"] = broker_flow_read(
        inputs.get("broker_rows") or [])
    sections["macro"] = macro_checklist(inputs.get("macro") or {})
    sections["corporate_actions"] = corporate_action_flags(
        inputs.get("actions") or [])
    sections["note"] = ("rules-mode output: a structured read of supplied "
                        "inputs, not a recommendation, not a prediction")
    return sections
