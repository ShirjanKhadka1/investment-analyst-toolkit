"""Personas team run — run the five global personas on real inputs,
score them, and measure cross-persona agreement.

Round 10, PR 10.3. Deterministic and offline: inputs come only
from the committed fixtures (the real AAPL recording from 10.2 and
the market-context recording from this PR). No network, no LLM.
The personas are the deterministic rules functions in
``toolkit.rules_mode`` (``GLOBAL_TEAM``); this module builds their
inputs from the fixtures, scores each persona section, reduces
each section to a stance by the fixed rules in
``docs/round-10-protocol.md`` section 10.3, and measures how
often the personas agree.

The agreement rate is a MEASUREMENT, not a consensus and not a
recommendation: it says how often the five personas' coarse
stances match on this one company on this one date. It is not
evidence about the company, and it is never presented as a
signal, a rating, or investment advice. Forcing agreement would
be gaming; disagreement between a fundamentals read and a
risk-regime read is itself informative.
"""

from __future__ import annotations

import itertools
import math

from toolkit import dossier as dossier_mod
from toolkit import edgar as edgar_mod
from toolkit import rules_mode

__all__ = [
    "agreement_rate",
    "build_personas_team_inputs",
    "run_personas_team",
    "score_persona_section",
    "stance_for_persona",
]

# Persona keys, in the GLOBAL_TEAM order.
PERSONA_KEYS = (
    "us_large_cap_fundamentals",
    "etf_index",
    "fx_macro",
    "crypto",
    "earnings",
)

# Chance agreement between two personas with three stances.
CHANCE_BASELINE = 1.0 / 3.0

RUN_ID = "personas-team-aapl-2026-11-27"


# --- Input building -------------------------------------------------------
# Every value here comes from a committed fixture (the real AAPL
# recording from 10.2, the market-context recording from this PR).
# Nothing is typed, nothing is fetched, nothing is guessed. A
# metric that cannot be derived from the fixtures is omitted, so
# the persona's check reports it UNKNOWN — never filled in.

def _annual_value(facts, tags, as_of):
    """Freshest annual value for a concept, or None when absent."""
    try:
        value, _taxonomy, _tag = edgar_mod.freshest_annual_value_as_of(
            facts, tags, as_of)
    except (ValueError, KeyError, TypeError):
        return None
    return value


def _fiscal_ytd_value(facts, tag, quarter_end, as_of):
    """Year-to-date value for a flow concept through a quarter end.

    Among the 10-Q values for ``tag`` ending on ``quarter_end``
    and filed by ``as_of``, the year-to-date figure is the one
    covering the longest span — the earliest ``start`` date (the
    fiscal-year start). The single-quarter figure starts at the
    quarter start. Returns the value dict, or None.
    """
    entries = facts.get("facts", {}).get("us-gaap", {}).get(tag, {})
    candidates = []
    for rows in entries.get("units", {}).values():
        for row in rows:
            if (row.get("form") == "10-Q"
                    and row.get("end") == quarter_end
                    and row.get("filed", "") <= as_of
                    and row.get("start")):
                candidates.append(row)
    if not candidates:
        return None
    candidates.sort(key=lambda r: (r["start"], r["filed"]))
    return candidates[0]


def _window_return(pairs):
    """Return over a fetched closes window, in percent.

    ``pairs`` is [[date, close], ...] oldest first. The base is
    the earliest bar in the window (the fetched 1y window starts
    one trading day after the 1y-ago date for US equities, because
    the 1y-ago date was a market holiday). Returns
    (return_pct, base_date, last_date).
    """
    if len(pairs) < 2:
        raise ValueError("need at least 2 closes for a window return")
    base_date, base = pairs[0]
    last_date, last = pairs[-1]
    if base == 0:
        raise ValueError("base close is 0")
    return (last / base - 1.0) * 100.0, base_date, last_date


def _realised_vol_30d_daily_pct(closes):
    """Population std of the last 30 daily simple returns, in percent.

    Same population-variance convention as
    ``toolkit.rules_mode.technical_read``. Needs 31 closes.
    """
    if len(closes) < 31:
        return None
    rets = [closes[i] / closes[i - 1] - 1.0
            for i in range(len(closes) - 30, len(closes))
            if closes[i - 1] != 0]
    if len(rets) < 30:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * 100.0


def _fundamentals_metrics(facts, as_of):
    """The six us_fundamentals_check metrics, from EDGAR facts.

    Only metrics derivable from the committed recording are
    returned. Interest coverage and return on invested capital
    need interest expense and income-tax expense, which the 10.2
    recording does not carry (it subsets to the dossier's concept
    map) — those two are omitted, so the check reports them
    UNKNOWN. Revenue growth compares the FY2026 filing with the
    FY2025 filing (read at the FY2026 year-end, before the FY2026
    10-K was filed).
    """
    concepts = dossier_mod.DOSSIER_EDGAR_CONCEPTS
    revenue = _annual_value(facts, concepts["revenue"], as_of)
    net_income = _annual_value(facts, concepts["net_income"], as_of)
    ocf = _annual_value(facts, concepts["operating_cash_flow"], as_of)
    capex = _annual_value(facts, concepts["capex"], as_of)
    liabilities = _annual_value(
        facts, concepts["total_liabilities"], as_of)
    equity = _annual_value(facts, concepts["equity"], as_of)
    metrics = {}
    if revenue is not None and net_income is not None:
        metrics["net_margin_pct"] = (
            net_income["value"] / revenue["value"] * 100.0
            if revenue["value"] else None)
    if (revenue is not None and ocf is not None
            and capex is not None):
        fcf = ocf["value"] - capex["value"]
        metrics["free_cash_flow_margin_pct"] = (
            fcf / revenue["value"] * 100.0 if revenue["value"] else None)
    if liabilities is not None and equity is not None and equity["value"]:
        metrics["debt_to_equity"] = liabilities["value"] / equity["value"]
    prior_revenue = _annual_value(facts, concepts["revenue"], "2026-09-26")
    if (revenue is not None and prior_revenue is not None
            and prior_revenue["value"]):
        metrics["revenue_growth_pct"] = (
            (revenue["value"] - prior_revenue["value"])
            / prior_revenue["value"] * 100.0)
    return {k: v for k, v in metrics.items() if v is not None}


def _earnings_events(facts, as_of):
    """The most recent earnings event, from the filer's filings.

    Q4 FY2026 is reported only inside the FY2026 10-K (annual
    total), so the quarter is DERIVED: the annual filing minus
    the 9-month year-to-date figure from the Q3 10-Q. EPS uses
    the dossier's convention (net income / shares outstanding).
    No consensus estimates exist in any public source this
    programme uses, so expected EPS/revenue are absent and the
    surprises are UNKNOWN — the event is recorded with its
    reported values, never with an invented expectation. The
    event date is the 10-K filed date: the sourced date the Q4
    results entered the filed record.
    """
    concepts = dossier_mod.DOSSIER_EDGAR_CONCEPTS
    revenue = _annual_value(facts, concepts["revenue"], as_of)
    net_income = _annual_value(facts, concepts["net_income"], as_of)
    shares = _annual_value(
        facts, concepts["shares_outstanding"], as_of)
    if revenue is None or net_income is None or shares is None:
        return []
    revenue_ytd = _fiscal_ytd_value(
        facts, "RevenueFromContractWithCustomerExcludingAssessedTax",
        "2026-06-27", as_of)
    income_ytd = _fiscal_ytd_value(
        facts, "NetIncomeLoss", "2026-06-27", as_of)
    if revenue_ytd is None or income_ytd is None:
        return []
    q4_revenue = revenue["value"] - revenue_ytd["val"]
    q4_income = net_income["value"] - income_ytd["val"]
    if not shares["value"]:
        return []
    q4_eps = round(q4_income / shares["value"], 2)
    return [{
        "date": revenue.get("filed", as_of),
        "reported_revenue": q4_revenue,
        "reported_eps": q4_eps,
        "expected_eps": None,
        "expected_revenue": None,
        "guidance": "UNKNOWN",
        "derivation": "Q4 FY2026 = FY2026 annual filing minus "
                      "9-month year-to-date (Q3 10-Q); EPS = Q4 net "
                      "income / shares outstanding (dossier convention)",
    }]


def build_personas_team_inputs(aapl_fixture, market_fixture):
    """Inputs for run_global_rules_team, from the two fixtures.

    ``aapl_fixture`` is the 10.2 recording
    (examples/real-aapl-2026-11-27.json); ``market_fixture`` is
    this PR's recording
    (examples/personas-team-inputs-2026-11-27.json). Both share
    as_of 2026-11-27.
    """
    as_of = aapl_fixture["as_of"]
    if market_fixture["as_of"] != as_of:
        raise ValueError(
            "market fixture as_of "
            f"{market_fixture['as_of']} != AAPL as_of {as_of}")
    facts = aapl_fixture["edgar_facts"]
    closes = [float(close) for _date, close
              in aapl_fixture["prices"]["pairs"]]

    spy = market_fixture["series"]["SPY"]["pairs"]
    spy_return, spy_base, _spy_last = _window_return(spy)
    eurusd = market_fixture["series"]["EURUSD=X"]["pairs"]
    fx_change, fx_base, _fx_last = _window_return(eurusd)
    btc_pairs = market_fixture["series"]["BTC-USD"]["pairs"]
    btc_closes = [float(close) for _date, close in btc_pairs]
    crypto = {
        "price_usd": btc_closes[-1],
        "all_time_high_usd": max(btc_closes),
    }
    if len(btc_closes) >= 200:
        crypto["mean_200d_usd"] = (
            sum(btc_closes[-200:]) / 200.0)
    vol_30d = _realised_vol_30d_daily_pct(btc_closes)
    if vol_30d is not None:
        crypto["realised_vol_30d_daily_pct"] = vol_30d

    return {
        "closes": closes,
        "fundamentals_metrics": _fundamentals_metrics(facts, as_of),
        "etf_metrics": {"index_return_1y_pct": spy_return},
        "fx_inputs": {"base_currency": "EUR", "quote_currency": "USD",
                      "pair_change_1y_pct": fx_change},
        "crypto_metrics": crypto,
        "earnings_events": _earnings_events(facts, as_of),
        "headlines": [],
        "_provenance": {
            "as_of": as_of,
            "spy_window": {"base_date": spy_base, "last_date": as_of,
                           "n_bars": len(spy)},
            "eurusd_window": {"base_date": fx_base, "last_date": as_of,
                              "n_bars": len(eurusd)},
            "btc_window": {"base_date": btc_pairs[0][0],
                           "last_date": as_of,
                           "n_bars": len(btc_pairs)},
        },
    }


# --- Scoring ---------------------------------------------------------------
# A persona section is "sourced" when it carries at least one
# concrete value traced to an input row or tool output; otherwise
# "UNKNOWN" (no evaluable input). The concrete values are named,
# so the score is auditable, not a bare count.

def _sourced_metric_rows(section, key="metrics"):
    rows = section.get(key)
    if not isinstance(rows, list):
        return []
    return [row["metric"] for row in rows if row.get("sourced")]


def score_persona_section(persona_key, section):
    """Score one persona section: sourced vs UNKNOWN."""
    if persona_key == "us_large_cap_fundamentals":
        sourced = _sourced_metric_rows(section)
    elif persona_key == "etf_index":
        sourced = _sourced_metric_rows(section)
        if section.get("etf_minus_index_1y_pct") is not None:
            sourced = sourced + ["etf_minus_index_1y_pct"]
    elif persona_key == "fx_macro":
        sourced = _sourced_metric_rows(section, key="rows")
        if section.get("rate_differential_bps") is not None:
            sourced = sourced + ["rate_differential_bps"]
    elif persona_key == "crypto":
        sourced = _sourced_metric_rows(section)
        sourced = sourced + sorted(
            k for k, v in (section.get("computed") or {}).items()
            if v is not None)
    elif persona_key == "earnings":
        sourced = []
        for event in section.get("events") or []:
            for name in ("reported_eps", "reported_revenue",
                         "eps_surprise_pct", "revenue_surprise_pct"):
                if isinstance(event.get(name), (int, float)):
                    sourced.append(f"{event.get('date')}:{name}")
    else:
        raise ValueError(f"unknown persona {persona_key!r}")
    if sourced:
        return {"persona": persona_key, "score": "sourced",
                "sourced_values": sorted(set(sourced)),
                "reason": f"{len(set(sourced))} concrete value(s) "
                          "traced to an input"}
    return {"persona": persona_key, "score": "UNKNOWN",
            "sourced_values": [],
            "reason": "no concrete value traced to an input"}


# --- Stances ---------------------------------------------------------------
# Fixed rules, stated in docs/round-10-protocol.md section 10.3
# BEFORE the run. No rule is chosen after seeing outputs.
# A stance is a coarse reduction of a section, not the section
# itself: a persona's full section always carries more than its
# stance.

def _metric_value(section, name, key="metrics"):
    rows = section.get(key)
    if not isinstance(rows, list):
        return None
    for row in rows:
        if row.get("metric") == name:
            value = row.get("value")
            return value if isinstance(value, (int, float)) else None
    return None


def _stance_from_sign(value):
    if value is None:
        return "UNKNOWN"
    if value > 0:
        return "constructive"
    if value < 0:
        return "cautious"
    return "neutral"


def stance_for_persona(persona_key, section):
    """Reduce one persona section to a stance, by the fixed rule."""
    if persona_key == "us_large_cap_fundamentals":
        sourced = _sourced_metric_rows(section)
        if not sourced:
            return {"persona": persona_key, "stance": "UNKNOWN",
                    "reason": "no sourced metric to assess"}
        flagged = {f["metric"] for f in section.get("flags") or []}
        weak = [m for m in sourced if m in flagged]
        healthy = [m for m in sourced if m not in flagged]
        if len(healthy) > len(weak):
            stance = "constructive"
        elif len(weak) > len(healthy):
            stance = "cautious"
        else:
            stance = "neutral"
        return {"persona": persona_key, "stance": stance,
                "reason": f"{len(healthy)} healthy vs {len(weak)} "
                          "flagged of the sourced checks",
                "detail": {"healthy": sorted(healthy),
                           "weak": sorted(weak)}}
    if persona_key == "etf_index":
        value = _metric_value(section, "index_return_1y_pct")
        return {"persona": persona_key,
                "stance": _stance_from_sign(value),
                "reason": ("SPY 1y return "
                           f"{value if value is not None else 'UNKNOWN'}% — "
                           "rising is constructive, falling cautious, "
                           "flat neutral"),
                "detail": {"index_return_1y_pct": value}}
    if persona_key == "fx_macro":
        value = _metric_value(section, "pair_change_1y_pct", key="rows")
        return {"persona": persona_key,
                "stance": _stance_from_sign(value),
                "reason": ("EUR/USD 1y change "
                           f"{value if value is not None else 'UNKNOWN'}% — "
                           "a rising EUR/USD is a weakening dollar, "
                           "constructive for AAPL's overseas earnings; "
                           "a falling EUR/USD is a strengthening dollar, "
                           "cautious"),
                "detail": {"pair_change_1y_pct": value}}
    if persona_key == "crypto":
        computed = section.get("computed") or {}
        value = computed.get("price_vs_mean_200d_pct")
        if not isinstance(value, (int, float)):
            value = None
        return {"persona": persona_key,
                "stance": _stance_from_sign(value),
                "reason": ("BTC price vs 200-day mean "
                           f"{value if value is not None else 'UNKNOWN'}% — "
                           "above the mean is a risk-on read, below it "
                           "risk-off, at it neutral"),
                "detail": {"price_vs_mean_200d_pct": value}}
    if persona_key == "earnings":
        events = section.get("events") or []
        if not events:
            return {"persona": persona_key, "stance": "UNKNOWN",
                    "reason": "no earnings event"}
        event = events[-1]
        surprises = [event[name] for name in
                     ("eps_surprise_pct", "revenue_surprise_pct")
                     if isinstance(event.get(name), (int, float))]
        if not surprises:
            return {"persona": persona_key, "stance": "UNKNOWN",
                    "reason": "most recent event carries no computable "
                              "surprise (no consensus expectation in any "
                              "public source this programme uses)",
                    "detail": {"event_date": event.get("date")}}
        if all(s > 0 for s in surprises):
            stance = "constructive"
        elif all(s < 0 for s in surprises):
            stance = "cautious"
        else:
            stance = "neutral"
        return {"persona": persona_key, "stance": stance,
                "reason": f"most recent event surprises {surprises}",
                "detail": {"event_date": event.get("date"),
                           "surprises": surprises}}
    raise ValueError(f"unknown persona {persona_key!r}")


# --- Agreement -------------------------------------------------------------

def agreement_rate(stances):
    """Pairwise agreement among the non-UNKNOWN stances.

    ``stances`` maps persona key -> stance string. Agreement is
    the fraction of unordered persona pairs (among personas with
    a non-UNKNOWN stance) whose stances match. Fewer than 2
    non-UNKNOWN stances means no rate exists. The chance baseline
    for three stances is 1/3; the rate is reported against it,
    never thresholded — a rate at chance is a measurement, not a
    failure.
    """
    present = {k: v for k, v in stances.items() if v != "UNKNOWN"}
    keys = sorted(present)
    pairs = []
    for a, b in itertools.combinations(keys, 2):
        pairs.append({"a": a, "b": b,
                      "stance_a": present[a], "stance_b": present[b],
                      "match": present[a] == present[b]})
    matching = sum(1 for p in pairs if p["match"])
    if len(keys) < 2:
        return {"rate": None, "pairs": pairs,
                "n_personas": len(keys), "n_pairs": len(pairs),
                "matching_pairs": matching,
                "chance_baseline": CHANCE_BASELINE,
                "reason": "fewer than 2 non-UNKNOWN stances — "
                          "no agreement rate exists"}
    return {"rate": matching / len(pairs), "pairs": pairs,
            "n_personas": len(keys), "n_pairs": len(pairs),
            "matching_pairs": matching,
            "chance_baseline": CHANCE_BASELINE,
            "reason": f"{matching} of {len(pairs)} persona pairs agree"}


# --- The team run ----------------------------------------------------------

_SECTION_KEY = {
    "us_large_cap_fundamentals": "us_fundamentals",
    "etf_index": "etf_index",
    "fx_macro": "fx_macro",
    "crypto": "crypto",
    "earnings": "earnings",
}


def run_personas_team(aapl_fixture, market_fixture,
                      run_id=RUN_ID):
    """Run the five personas on the AAPL fixture and score them.

    Returns a deterministic result dict (fixed run id, fixed
    as_of clock — no wall-clock time anywhere, so the same
    fixtures re-run byte-identically). Raises if any persona
    section crashes; a section a persona cannot evaluate is
    UNKNOWN with a reason, never a filled-in value.
    """
    inputs = build_personas_team_inputs(aapl_fixture, market_fixture)
    provenance = inputs.pop("_provenance")
    sections = rules_mode.run_global_rules_team(
        "AAPL", "us", inputs)
    scores = {}
    stances = {}
    for key in PERSONA_KEYS:
        section = sections[_SECTION_KEY[key]]
        scores[key] = score_persona_section(key, section)
        stances[key] = stance_for_persona(key, section)
    stance_values = {k: v["stance"] for k, v in stances.items()}
    agreement = agreement_rate(stance_values)
    technical = sections.get("technical") or {}
    return {
        "run_id": run_id,
        "symbol": "AAPL",
        "market": "us",
        "as_of": aapl_fixture["as_of"],
        "filer": aapl_fixture["company_name"],
        "sic": aapl_fixture["sic"],
        "sic_description": aapl_fixture["sic_description"],
        "last_close": aapl_fixture["prices"]["pairs"][-1][1],
        "scores": scores,
        "stances": stances,
        "agreement": agreement,
        "technical": {
            "momentum_20d_pct": technical.get("momentum_20d_pct"),
            "price_vs_mean_20d_pct":
                technical.get("price_vs_mean_20d_pct"),
            "rsi_14": technical.get("rsi_14"),
            "volatility_daily_pct":
                technical.get("volatility_daily_pct"),
            "n_bars": technical.get("n_bars"),
        },
        "sentiment": {
            "label": (sections.get("sentiment") or {}).get("label"),
        },
        "provenance": provenance,
        "sections": sections,
    }
