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
    "GLOBAL_TEAM",
    "RULES_MODE_VERSION",
    "broker_flow_read",
    "corporate_action_flags",
    "crypto_check",
    "earnings_check",
    "etf_index_check",
    "fx_macro_check",
    "list_global_personas",
    "macro_checklist",
    "run_global_rules_team",
    "run_rules_team",
    "sector_fundamentals_check",
    "sentiment_score",
    "technical_read",
    "us_fundamentals_check",
]

RULES_MODE_VERSION = "1.4.0"  # 1.4.0 adds the market-agnostic global team.

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


# --- Market-agnostic global team (1.4.0) ----------------------------------
# Same structure as the NEPSE team above: pure, deterministic rules over
# caller-supplied inputs; nothing fetched, nothing guessed, UNKNOWN for
# anything not supplied. These five personas cover the markets the
# NEPSE team does not: US large-cap fundamentals (EDGAR XBRL metrics),
# ETF/index, FX/macro, crypto, and earnings. The NEPSE functions above
# are untouched. Thresholds are rules-of-thumb discussion prompts, not
# pass/fail verdicts and not advice.

def _metric_rows(names, metrics):
    """One row per named metric: value or UNKNOWN, never filled in."""
    rows = []
    for name in names:
        value = metrics.get(name)
        rows.append({"metric": name,
                     "value": value if value is not None else "UNKNOWN",
                     "sourced": value is not None})
    return rows


def _flag_rows(metrics, flags):
    """Discussion-prompt flags: (metric, threshold, direction, text)."""
    out = []
    for name, threshold, direction, text in flags:
        value = metrics.get(name)
        if value is None:
            continue
        triggered = (value > threshold if direction == "above"
                     else value < threshold)
        if triggered:
            out.append({"metric": name, "value": value,
                        "threshold": threshold, "flag": text})
    return out


def us_fundamentals_check(metrics):
    """US large-cap fundamentals over caller-supplied XBRL metrics.

    ``metrics`` maps EDGAR-sourced figures to numbers — e.g. from
    ``toolkit.edgar.get_fundamentals`` (Revenues, NetIncomeLoss) plus
    derived ratios the caller computed (revenue_growth_pct,
    net_margin_pct, free_cash_flow_margin_pct, debt_to_equity,
    interest_coverage, return_on_invested_capital_pct). A missing
    metric is UNKNOWN, never filled in. Flags are discussion prompts.
    """
    names = ("revenue_growth_pct", "net_margin_pct",
             "free_cash_flow_margin_pct", "debt_to_equity",
             "interest_coverage", "return_on_invested_capital_pct")
    flags = (("debt_to_equity", 2.0, "above",
              "debt/equity above 2.0x — ask why"),
             ("interest_coverage", 1.5, "below",
              "interest coverage below 1.5x — ask why"),
             ("revenue_growth_pct", 0.0, "below",
              "revenue declining year on year — ask why"),
             ("net_margin_pct", 0.0, "below",
              "net margin negative — ask why"))
    return {"persona": "us_large_cap_fundamentals", "status": "checked",
            "metrics": _metric_rows(names, metrics),
            "flags": _flag_rows(metrics, flags),
            "note": "flags are discussion prompts, not verdicts; "
                    "every metric needs its EDGAR source + period"}


def etf_index_check(metrics):
    """ETF/index read over caller-supplied fund metrics.

    ``metrics``: expense_ratio_pct, tracking_error_pct,
    assets_under_management_usd, premium_discount_pct (ETF price vs
    NAV), index_return_1y_pct, etf_return_1y_pct. Missing is UNKNOWN.
    """
    names = ("expense_ratio_pct", "tracking_error_pct",
             "assets_under_management_usd", "premium_discount_pct",
             "index_return_1y_pct", "etf_return_1y_pct")
    flags = (("expense_ratio_pct", 0.50, "above",
              "expense ratio above 0.50% — high for a broad index ETF, ask why"),
             ("tracking_error_pct", 1.0, "above",
              "tracking error above 1% — ask why the fund trails its index"))
    rows = _metric_rows(names, metrics)
    premium = metrics.get("premium_discount_pct")
    if premium is not None and abs(premium) > 0.5:
        extra = {"metric": "premium_discount_pct", "value": premium,
                 "threshold": 0.5,
                 "flag": "premium/discount beyond ±0.5% — ask why"}
    else:
        extra = None
    out_flags = _flag_rows(metrics, flags)
    if extra is not None:
        out_flags.append(extra)
    gap = None
    if (metrics.get("index_return_1y_pct") is not None
            and metrics.get("etf_return_1y_pct") is not None):
        gap = (metrics["etf_return_1y_pct"]
               - metrics["index_return_1y_pct"])
    return {"persona": "etf_index", "status": "checked",
            "metrics": rows, "flags": out_flags,
            "etf_minus_index_1y_pct": gap,
            "note": "flags are discussion prompts, not verdicts; "
                    "tracking error and premium/discount describe the "
                    "past year only"}


def fx_macro_check(inputs):
    """FX/macro read over caller-supplied central-bank and price data.

    ``inputs``: base_currency, quote_currency,
    policy_rate_base_pct, policy_rate_quote_pct,
    inflation_base_pct, inflation_quote_pct,
    pair_change_1y_pct, pair_volatility_annualised_pct. Missing is
    UNKNOWN. The rate differential is computed only when both policy
    rates are supplied.
    """
    names = ("policy_rate_base_pct", "policy_rate_quote_pct",
             "inflation_base_pct", "inflation_quote_pct",
             "pair_change_1y_pct", "pair_volatility_annualised_pct")
    differential = None
    if (inputs.get("policy_rate_base_pct") is not None
            and inputs.get("policy_rate_quote_pct") is not None):
        differential = (inputs["policy_rate_base_pct"]
                        - inputs["policy_rate_quote_pct"])
    flags = []
    move = inputs.get("pair_change_1y_pct")
    if move is not None and abs(move) > 20.0:
        flags.append({"metric": "pair_change_1y_pct", "value": move,
                      "threshold": 20.0,
                      "flag": "pair moved more than 20% in a year — ask why"})
    return {"persona": "fx_macro", "status": "checked",
            "pair": (f"{inputs.get('base_currency', 'UNKNOWN')}/"
                     f"{inputs.get('quote_currency', 'UNKNOWN')}"),
            "rows": _metric_rows(names, inputs),
            "rate_differential_bps": (differential * 100.0
                                      if differential is not None else None),
            "flags": flags,
            "note": "a rate differential is one input among many — "
                    "it predicts nothing about the next move"}


def crypto_check(metrics):
    """Crypto read over caller-supplied market metrics.

    ``metrics``: price_usd, market_cap_usd, volume_24h_usd,
    all_time_high_usd, mean_200d_usd, realised_vol_30d_daily_pct,
    circulating_supply, max_supply. Missing is UNKNOWN. Drawdown,
    volume/market-cap and price-vs-200d mean are computed only when
    their inputs are supplied.
    """
    names = ("price_usd", "market_cap_usd", "volume_24h_usd",
             "all_time_high_usd", "mean_200d_usd",
             "realised_vol_30d_daily_pct", "circulating_supply",
             "max_supply")
    computed = {}
    if (metrics.get("price_usd") is not None
            and metrics.get("all_time_high_usd")):
        computed["drawdown_from_ath_pct"] = (
            metrics["price_usd"] / metrics["all_time_high_usd"] - 1.0) * 100.0
    if (metrics.get("volume_24h_usd") is not None
            and metrics.get("market_cap_usd")):
        computed["volume_to_mcap_pct"] = (
            metrics["volume_24h_usd"] / metrics["market_cap_usd"]) * 100.0
    if (metrics.get("price_usd") is not None
            and metrics.get("mean_200d_usd")):
        computed["price_vs_mean_200d_pct"] = (
            metrics["price_usd"] / metrics["mean_200d_usd"] - 1.0) * 100.0
    flags = []
    if computed.get("drawdown_from_ath_pct") is not None \
            and computed["drawdown_from_ath_pct"] < -50.0:
        flags.append({"metric": "drawdown_from_ath_pct",
                      "value": computed["drawdown_from_ath_pct"],
                      "threshold": -50.0,
                      "flag": "more than 50% below all-time high — ask why"})
    if metrics.get("realised_vol_30d_daily_pct") is not None \
            and metrics["realised_vol_30d_daily_pct"] > 5.0:
        flags.append({"metric": "realised_vol_30d_daily_pct",
                      "value": metrics["realised_vol_30d_daily_pct"],
                      "threshold": 5.0,
                      "flag": "average daily move above 5% — very high volatility"})
    if (metrics.get("circulating_supply") is not None
            and metrics.get("max_supply")):
        share = metrics["circulating_supply"] / metrics["max_supply"]
        computed["circulating_share_of_max_supply"] = share
        if share < 0.5:
            flags.append({"metric": "circulating_supply",
                          "value": share, "threshold": 0.5,
                          "flag": "less than half of max supply circulating "
                                  "— future issuance dilutes holders, ask why"})
    return {"persona": "crypto", "status": "checked",
            "metrics": _metric_rows(names, metrics),
            "computed": computed, "flags": flags,
            "note": "flags are discussion prompts, not verdicts; "
                    "crypto volatility cuts both ways and this read "
                    "predicts nothing"}


def earnings_check(events):
    """Earnings read over caller-supplied reported/expected figures.

    ``events`` is a list of dicts with ``date`` and optionally
    ``reported_eps``, ``expected_eps``, ``reported_revenue``,
    ``expected_revenue``, ``guidance`` ("raised"/"maintained"/
    "lowered"). Surprise is computed only when both reported and
    expected are supplied; otherwise it is UNKNOWN, never assumed.
    """
    out = []
    for event in events:
        row = {"date": event.get("date", "UNKNOWN"),
               "guidance": event.get("guidance", "UNKNOWN")}
        for reported_key, expected_key, name in (
                ("reported_eps", "expected_eps", "eps_surprise_pct"),
                ("reported_revenue", "expected_revenue",
                 "revenue_surprise_pct")):
            reported = event.get(reported_key)
            expected = event.get(expected_key)
            if reported is not None and expected not in (None, 0):
                row[name] = (reported - expected) / abs(expected) * 100.0
            else:
                row[name] = "UNKNOWN"
        flags = []
        for name in ("eps_surprise_pct", "revenue_surprise_pct"):
            value = row[name]
            if value != "UNKNOWN" and abs(value) > 10.0:
                flags.append({"metric": name, "value": value,
                              "threshold": 10.0,
                              "flag": f"{name} beyond ±10% — large surprise, ask why"})
        if event.get("guidance") == "lowered":
            flags.append({"metric": "guidance", "value": "lowered",
                          "threshold": None,
                          "flag": "guidance lowered — ask why"})
        row["flags"] = flags
        out.append(row)
    return {"persona": "earnings", "events": out, "n_events": len(out),
            "note": "surprises describe reported quarters only; "
                    "a beat says nothing about the next quarter"}


# Registry: persona key -> rules function, mirroring the NEPSE team's
# shape so the same runner and the same tests cover both teams.
GLOBAL_TEAM = {
    "us_large_cap_fundamentals": us_fundamentals_check,
    "etf_index": etf_index_check,
    "fx_macro": fx_macro_check,
    "crypto": crypto_check,
    "earnings": earnings_check,
}


def list_global_personas():
    """Persona keys of the market-agnostic global team, in order."""
    return list(GLOBAL_TEAM)


def run_global_rules_team(symbol, market, inputs):
    """Run the global rules-mode team over supplied inputs, one dict out.

    ``market`` is a market-profile key (e.g. "us", "crypto", "fx").
    ``inputs`` may carry ``closes``, ``fundamentals_metrics``,
    ``etf_metrics``, ``fx_inputs``, ``crypto_metrics`` and
    ``earnings_events``; any missing key yields that section as
    UNKNOWN. Technicals and sentiment reuse the market-agnostic
    functions shared with the NEPSE team.
    """
    if not symbol or not market:
        raise ValueError("run_global_rules_team needs a symbol and a market")
    sections = {"symbol": symbol, "market": market,
                "rules_mode_version": RULES_MODE_VERSION,
                "llm": "none — deterministic rules mode, zero API keys"}
    closes = inputs.get("closes")
    sections["technical"] = (technical_read(closes) if closes else
                             {"status": "UNKNOWN",
                              "reason": "no closes supplied"})
    sections["us_fundamentals"] = us_fundamentals_check(
        inputs.get("fundamentals_metrics") or {})
    sections["etf_index"] = etf_index_check(
        inputs.get("etf_metrics") or {})
    sections["fx_macro"] = fx_macro_check(inputs.get("fx_inputs") or {})
    sections["crypto"] = crypto_check(inputs.get("crypto_metrics") or {})
    sections["earnings"] = earnings_check(
        inputs.get("earnings_events") or [])
    sections["sentiment"] = sentiment_score(inputs.get("headlines") or [])
    sections["note"] = ("global rules-mode output: a structured read of "
                        "supplied inputs, not a recommendation, not a "
                        "prediction")
    return sections
