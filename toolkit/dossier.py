"""Company dossier pipeline — Round 9, PR 9.3.

One ticker in, one dossier out: EDGAR fundamentals, prices, peers,
valuation (DCF and comps), risks, and a monitoring checklist. Every
number sourced or UNKNOWN. The pipeline executes the
``company-deep-dive`` runbook order (Investment Researcher ->
Forensic -> Financial -> Valuation -> Reality Checker) from injected
data — it never fetches anything itself in the core path (the thin
live-fetch wrapper :func:`fetch_dossier_inputs` is separate and is
never used in tests).

Groundedness contract (Round 9.2): the finished dossier passes
``toolkit.groundedness.check_memo`` with 0 unsourced numbers, and
the Reality Checker step returns Pass. The memo is rendered ONLY
from the pipeline's evidence table, so every number it writes is
already in the run's event log. The renderer rules below are the
reason a dossier memo passes where a hand-written note does not:

- Headings use the checker's required section names exactly
  (``Thesis``, ``Key risks``, ``What would change my mind``,
  ``Gate verdict``, ``Sources``); the initiation-report template
  headings are mapped onto them (see :data:`TEMPLATE_MAP`).
- No compact money suffixes (``81M``, ``1.2B`` are unparseable under
  the checker grammar): money is written in full with thousands
  separators, e.g. ``385,000,000``.
- No peer symbol containing a digit (``PEER1`` is unparseable):
  peer symbols are rendered as ``Peer A``, ``Peer B``, ... with the
  mapping recorded in the dossier.
- No ``FY YYYY/YY`` period (``/82`` is unparseable): periods render
  as ``FY YYYY`` (the full period stays in the dossier data).
- Every count the memo states (forecast years, peer count, risk
  count) is carried in a ``tool_result`` detail string first.
- The ``Gate verdict`` section states that no position gate runs
  (a dossier proposes no position) and reports the Reality Checker
  verdict instead. No ``gate_result`` event is emitted: the dossier
  has no risk gate to agree with, and inventing one would mislabel
  the Reality Checker as the risk gate.
- Caller-supplied prose (risk text and triggers, industry, company
  name) must not contain bare numbers unless those numbers are
  already in the evidence table: a threshold such as "revenue below
  90,000,000" is a forward-looking number with no tool output behind
  it, so the Reality Checker blocks the dossier and names it. Write
  triggers against the filed values instead ("revenue below the
  filed FY 2025 revenue").

Template map (protocol test 7): every dossier section maps to one
of the 8 research templates in ``finance-research-agents/templates/``.
Two sections have no template and are named here, not silently
added: ``Gate verdict`` and ``Sources`` come from the 9.2 checker
contract. ``earnings-review`` maps only when earnings events are
supplied (none are in the fixtures, so no Earnings section is
rendered there).

Stdlib only, no network in this module's core path. A dossier about
a real company is never claimed validated until its fixture suite
passes first.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from . import edgar
from .groundedness import check_memo
from .valuation import dcf_value, implied_price_from_multiple

__all__ = [
    "DOSSIER_EDGAR_CONCEPTS",
    "TEMPLATE_MAP",
    "build_dossier",
    "fetch_dossier_inputs",
    "load_demo_fundamentals",
    "load_demo_peers",
    "reality_check",
    "render_dossier_memo",
    "run_dossier",
]

# EDGAR concepts the dossier reads, as candidate (taxonomy, tag)
# lists per economic concept (see edgar.freshest_annual_value_as_of:
# XBRL tags are not uniform across filers, so each concept tries
# several tags and takes the freshest value filed by as_of).
DOSSIER_EDGAR_CONCEPTS = {
    "revenue": [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "Revenues"),
        ("us-gaap", "SalesRevenueNet"),
    ],
    "net_income": [("us-gaap", "NetIncomeLoss")],
    "gross_profit": [("us-gaap", "GrossProfit")],
    "operating_income": [("us-gaap", "OperatingIncomeLoss")],
    "total_assets": [("us-gaap", "Assets")],
    "total_liabilities": [("us-gaap", "Liabilities")],
    "equity": [
        ("us-gaap", "StockholdersEquity"),
        ("us-gaap",
         "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    ],
    "cash": [("us-gaap", "CashAndCashEquivalentsAtCarryingValue")],
    "operating_cash_flow": [
        ("us-gaap", "NetCashProvidedByOperatingActivities")],
    "capex": [("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment")],
    "shares_outstanding": [("us-gaap", "CommonStockSharesOutstanding")],
}

# Dossier section -> research template it maps to (protocol test 7).
# "Gate verdict" and "Sources" have no template: they come from the
# 9.2 checker contract and are named as additions in the PR.
TEMPLATE_MAP = {
    "Thesis": "initiation-report.md §1",
    "Business": "initiation-report.md §2",
    "Industry": "initiation-report.md §3",
    "Financials": "initiation-report.md §4",
    "Forensic": "forensic-checklist.md",
    "Valuation": "initiation-report.md §5 + valuation-sanity-check.md",
    "Key risks": "initiation-report.md §6",
    "What would change my mind": "initiation-report.md §7",
    "Open questions": "initiation-report.md §8",
    "Earnings": "earnings-review.md (only when earnings events supplied)",
    "Ledger control": "ledger-control-checklist.md",
    "Research to use case": "research-to-use-case.md",
    "Debate": "structured-debate.md",
    "Gate verdict": "NO TEMPLATE — 9.2 checker contract (named addition)",
    "Sources": "NO TEMPLATE — 9.2 checker contract (named addition)",
}

# Sections the memo renders, in order.
_MEMO_SECTIONS = (
    "Thesis", "Business", "Industry", "Financials", "Forensic",
    "Valuation", "Key risks", "What would change my mind",
    "Open questions", "Earnings", "Ledger control",
    "Research to use case", "Debate", "Gate verdict", "Sources",
)

_EDGAR_SOURCE = "SEC EDGAR companyfacts"

# Unit for peer metrics (multiples carry "x"; anything else plain).
_PEER_METRIC_UNITS = {"pe_ratio": "x", "price_to_book": "x"}


def _round2(value):
    """Round a number to 2 decimals (the display precision)."""
    return round(float(value), 2)


def _fmt_number(value, unit=""):
    """Format a display value the way the memo writes it.

    Money / plain numbers: thousands separators, no decimals when the
    rounded value is integral, else 2 decimals. Percentages and
    multiples always carry 2 decimals plus their unit. A prose unit
    (e.g. "NPR millions") is appended as words after the number —
    it is not a checker unit, so it never affects matching. The
    evidence table stores these same display values, so memo and
    evidence match exactly.
    """
    value = _round2(value)
    if unit == "%":
        return f"{value:.2f}%"
    if unit == "x":
        return f"{value:.2f}x"
    text = f"{int(value):,}" if value == int(value) else f"{value:,.2f}"
    if unit:
        text += f" {unit}"
    return text


def _checker_unit(unit):
    """Map a dossier unit to a checker unit ("", "%" or "x").

    The checker only knows "", "%" and "x". A prose unit such as
    "NPR millions" is a scale label, not a checker unit, so it maps
    to "" for matching.
    """
    if unit in ("%", "x"):
        return unit
    return ""


def _safe_period(period):
    """Render a period string so the checker grammar accepts it.

    ``FY 2081/82`` leaves ``/82`` unparseable, so fiscal-year
    ranges render as ``FY YYYY`` (``FY 2082/83 annual report`` ->
    ``FY 2082 annual report``). The full period stays in the
    dossier data and in evidence; only the memo prose uses this.
    """
    if not period:
        return ""
    return re.sub(r"FY (\d{4})/\d{2}", r"FY \1", str(period).strip())


def _display_peer_label(symbol, index):
    """Digit-free display label for a peer symbol.

    Peer symbols containing a digit (``PEER1``) are unparseable
    under the checker grammar, so the memo renders peers as
    ``Peer A``, ``Peer B``, ... The mapping is recorded in the
    dossier; the original symbol is never written in the memo.
    """
    if any(ch.isdigit() for ch in str(symbol)):
        return f"Peer {chr(ord('A') + index)}"
    return str(symbol)


def _value(field, value, unit, source, period, status, reason=""):
    """One dossier value record."""
    return {
        "field": field,
        "value": value,
        "unit": unit,
        "source": source,
        "period": period,
        "status": status,
        "reason": reason,
    }


def _unknown(field, reason):
    """An UNKNOWN value record: no number, reason stated."""
    return _value(field, None, "", None, None, "UNKNOWN", reason)


def _median(values):
    """Median of a non-empty list (DERIVED, formula stated by callers)."""
    ordered = sorted(values)
    n = len(ordered)
    if n % 2 == 1:
        return ordered[n // 2]
    return (ordered[n // 2 - 1] + ordered[n // 2]) / 2


# ---------------------------------------------------------------------------
# Demo CSV loaders (committed example files, synthetic fixtures only)
# ---------------------------------------------------------------------------

def load_demo_fundamentals(path):
    """Load ``examples/research-demo-fundamentals.csv``.

    Returns a list of value records in dossier shape, with the
    CSV's field names normalised to the dossier's canonical names
    (``revenue_npr_m`` -> ``revenue`` with unit "NPR millions",
    ``net_income_npr_m`` -> ``net_income``,
    ``book_value_per_share_npr`` -> ``book_value_per_share`` with
    unit "NPR"; the original CSV field is kept as ``csv_field``).
    A row with no source (the ``npl_pct`` row) becomes UNKNOWN with
    the reason stated — a value with no source is never written as
    a number. The file is a SYNTHETIC fixture, not real market data.
    """
    field_map = {
        "revenue_npr_m": ("revenue", "NPR millions"),
        "net_income_npr_m": ("net_income", "NPR millions"),
        "book_value_per_share_npr": ("book_value_per_share", "NPR"),
        "npl_pct": ("npl_pct", "%"),
    }
    records = []
    with open(path, encoding="utf-8", newline="") as handle:
        lines = [line for line in handle
                 if not line.lstrip().startswith("#")]
    rows = list(csv.DictReader(lines))
    for row in rows:
        csv_field = (row.get("field") or "").strip()
        if not csv_field:
            continue
        field, unit = field_map.get(csv_field, (csv_field, ""))
        source = (row.get("source") or "").strip()
        period = (row.get("period") or "").strip()
        raw = (row.get("value") or "").strip()
        if not source:
            record = _unknown(
                field, f"no source in {Path(path).name} for {csv_field}")
            record["csv_field"] = csv_field
            records.append(record)
            continue
        try:
            number = float(raw)
        except ValueError:
            record = _unknown(
                field, f"non-numeric value {raw!r} in {Path(path).name}")
            record["csv_field"] = csv_field
            records.append(record)
            continue
        record = _value(field, _round2(number), unit, source, period,
                        "filed")
        record["csv_field"] = csv_field
        records.append(record)
    return records


def load_demo_peers(path):
    """Load ``examples/research-demo-peers.csv``.

    Returns {"peers": [...], "dropped": [...]}. A row with no source
    is dropped with its reason recorded — it never becomes a peer
    number. The file is a SYNTHETIC fixture, not real market data.
    """
    peers = []
    dropped = []
    with open(path, encoding="utf-8", newline="") as handle:
        lines = [line for line in handle
                 if not line.lstrip().startswith("#")]
    rows = list(csv.DictReader(lines))
    for row in rows:
        symbol = row["symbol"].strip()
        metric = (row.get("metric") or "").strip()
        source = (row.get("source") or "").strip()
        raw = (row.get("value") or "").strip()
        if not source:
            dropped.append(
                f"{symbol} {metric}: no source in {Path(path).name}")
            continue
        try:
            number = float(raw)
        except ValueError:
            dropped.append(f"{symbol} {metric}: non-numeric value {raw!r}")
            continue
        peers.append({"symbol": symbol, "metric": metric,
                      "value": _round2(number), "source": source})
    return {"peers": peers, "dropped": dropped}


# ---------------------------------------------------------------------------
# Pipeline: build the dossier data (no memo, no verdict yet)
# ---------------------------------------------------------------------------

def _edgar_values(edgar_facts, as_of):
    """Point-in-time EDGAR values, one record per concept.

    A concept with no filing by ``as_of`` is UNKNOWN, never filled
    from a later filing (protocol test 1). ``edgar_facts`` is the
    companyfacts dict from ``edgar.fetch_company_facts`` (or the
    synthetic fixture of the same shape).
    """
    records = []
    for field, candidates in DOSSIER_EDGAR_CONCEPTS.items():
        try:
            found, _taxonomy, _tag = edgar.freshest_annual_value_as_of(
                edgar_facts, candidates, as_of)
        except ValueError:
            records.append(_unknown(
                field, f"no filing by {as_of} for {field}"))
            continue
        period = f"FY {found.get('fy')}" if found.get("fy") else (
            found.get("end") or "")
        record = _value(
            field, _round2(found["value"]), "", _EDGAR_SOURCE, period,
            "filed",
            f"{found.get('form') or 'filing'} filed {found.get('filed')}")
        record["filed"] = found.get("filed")
        records.append(record)
    return records


def _prices_section(prices, as_of):
    """Price section from a fetch model (or an injected simple dict).

    Accepts the ``fetch_prices_with_volumes`` model shape
    ({symbol, source, pairs, provenance}) or a simple
    {source, last_close, last_date} dict. A last date after ``as_of``
    is look-ahead: the section is UNKNOWN with the reason stated.
    A stale or missing series is UNKNOWN, never an invented close
    (protocol test 2).
    """
    if not prices:
        return _unknown("last_close", "no prices supplied"), None
    source = prices.get("source") or "UNKNOWN source"
    if prices.get("pairs"):
        last_date, last_close = prices["pairs"][-1]
        last_date = str(last_date)
    else:
        last_close = prices.get("last_close")
        last_date = str(prices.get("last_date") or "")
    if last_close is None or not last_date:
        return _unknown("last_close", "price series has no last close"), None
    if last_date > str(as_of):
        return _unknown(
            "last_close",
            f"last price date {last_date} is after as_of {as_of}"), None
    record = _value("last_close", _round2(last_close), "", source,
                    last_date, "filed", f"last close {last_date}")
    closes = None
    if prices.get("pairs"):
        closes = [float(close) for _date, close in prices["pairs"]]
    return record, {"source": source, "last_close": _round2(last_close),
                    "last_date": last_date,
                    "n_bars": len(prices.get("pairs") or []) or None,
                    "closes": closes}


def _peers_section(peers):
    """Peer section: caller-supplied peers or an UNKNOWN section.

    The peer set is caller-supplied or taken from the same EDGAR SIC
    (not implemented: no SIC source is wired in yet) — never invented.
    With no peer source the section is UNKNOWN with the reason stated
    (protocol test 3).
    """
    if not peers:
        return {"status": "UNKNOWN",
                "reason": "no peer source supplied",
                "peers": [], "medians": {}}
    by_metric = {}
    labelled = []
    labels = {}
    for peer in peers:
        symbol = peer["symbol"]
        if symbol not in labels:
            labels[symbol] = _display_peer_label(
                symbol, len(labels))
        labelled.append({**peer, "label": labels[symbol]})
        by_metric.setdefault(peer["metric"], []).append(peer["value"])
    medians = {}
    for metric, values in by_metric.items():
        count = len(values)
        medians[metric] = {
            "value": _round2(_median(values)),
            "n": count,
            "formula": (f"median of {count} peer {metric} "
                        f"{'value' if count == 1 else 'values'}"),
        }
    return {"status": "filed", "reason": "", "peers": labelled,
            "medians": medians}


def _derived_values(values):
    """DERIVED values from filed fundamentals (formula stated each).

    Net margin, free cash flow (operating cash flow minus capex),
    earnings per share, net debt. A derived value needs every input
    filed; otherwise it is UNKNOWN with the missing input named.
    """
    by_field = {v["field"]: v for v in values}

    def filed(field):
        record = by_field.get(field)
        if record is not None and record["status"] == "filed":
            return record["value"]
        return None

    derived = []
    revenue = filed("revenue")
    net_income = filed("net_income")
    if revenue is not None and net_income is not None and revenue != 0:
        derived.append(_value(
            "net_margin_pct", _round2(net_income / revenue * 100), "%",
            "DERIVED: net_income / revenue x 100",
            by_field["net_income"]["period"], "DERIVED"))
    elif revenue is not None and net_income is not None:
        derived.append(_unknown("net_margin_pct", "revenue is 0"))
    else:
        derived.append(_unknown(
            "net_margin_pct",
            "needs filed revenue and net_income"))
    ocf = filed("operating_cash_flow")
    capex = filed("capex")
    if ocf is not None and capex is not None:
        derived.append(_value(
            "free_cash_flow", _round2(ocf - capex), "",
            "DERIVED: operating_cash_flow - capex",
            by_field["operating_cash_flow"]["period"], "DERIVED"))
    else:
        derived.append(_unknown(
            "free_cash_flow",
            "needs filed operating_cash_flow and capex"))
    shares = filed("shares_outstanding")
    if net_income is not None and shares is not None and shares != 0:
        derived.append(_value(
            "eps", _round2(net_income / shares), "",
            "DERIVED: net_income / shares_outstanding",
            by_field["net_income"]["period"], "DERIVED"))
    else:
        derived.append(_unknown(
            "eps", "needs filed net_income and shares_outstanding"))
    liabilities = filed("total_liabilities")
    cash = filed("cash")
    if liabilities is not None and cash is not None:
        derived.append(_value(
            "net_debt", _round2(liabilities - cash), "",
            "DERIVED: total_liabilities - cash",
            by_field["total_liabilities"]["period"], "DERIVED"))
    else:
        derived.append(_unknown(
            "net_debt", "needs filed total_liabilities and cash"))
    assets = filed("total_assets")
    if liabilities is not None and assets is not None and assets != 0:
        derived.append(_value(
            "liabilities_to_assets_pct",
            _round2(liabilities / assets * 100), "%",
            "DERIVED: total_liabilities / total_assets x 100",
            by_field["total_liabilities"]["period"], "DERIVED"))
    else:
        derived.append(_unknown(
            "liabilities_to_assets_pct",
            "needs filed total_liabilities and total_assets"))
    return derived


def _valuation_section(values, peers_section, dcf_inputs):
    """Valuation: DCF via dcf_value, comps via peer medians.

    Every input is stated in the dossier. If no method fits the
    inputs, the section says "Valuation not built, because..." and
    stops — it never forces a DCF (protocol test 4).
    """
    by_field = {v["field"]: v for v in values}
    section = {"dcf": None, "comps": None, "not_built": [],
               "sanity": None}

    def filed(field):
        record = by_field.get(field)
        if record is not None and record["status"] == "filed":
            return record["value"]
        return None

    def derived(field):
        for record in values:
            if record["field"] == field and record["status"] == "DERIVED":
                return record["value"]
        return None

    # --- DCF ---
    if dcf_inputs is None:
        section["not_built"].append(
            "DCF not built: no free-cash-flow inputs supplied")
    else:
        fcfs = dcf_inputs.get("free_cash_flows") or []
        rate = dcf_inputs.get("discount_rate")
        growth = dcf_inputs.get("terminal_growth")
        terminal_fcf = dcf_inputs.get("terminal_year_fcf")
        if not fcfs:
            section["not_built"].append(
                "DCF not built: free_cash_flows is empty")
        elif rate is None or growth is None or terminal_fcf is None:
            section["not_built"].append(
                "DCF not built: discount_rate, terminal_growth and "
                "terminal_year_fcf must all be supplied")
        elif rate <= growth:
            section["not_built"].append(
                f"DCF not built: discount_rate ({rate}) does not exceed "
                f"terminal_growth ({growth})")
        else:
            enterprise_value = _round2(dcf_value(
                fcfs, rate, growth, terminal_fcf))
            dcf = {
                "free_cash_flows": [_round2(f) for f in fcfs],
                "discount_rate": rate,
                "terminal_growth": growth,
                "terminal_year_fcf": _round2(terminal_fcf),
                "enterprise_value": enterprise_value,
                "per_share": None,
                "per_share_reason": "",
            }
            net_debt = derived("net_debt")
            shares = filed("shares_outstanding")
            if net_debt is not None and shares is not None and shares != 0:
                equity_value = _round2(enterprise_value - net_debt)
                dcf["per_share"] = _round2(equity_value / shares)
                dcf["equity_value"] = equity_value
                dcf["net_debt"] = _round2(net_debt)
            else:
                dcf["per_share_reason"] = (
                    "per-share DCF not built: needs DERIVED net_debt and "
                    "filed shares_outstanding")
            # Downside case (sanity check §3): terminal growth 0.
            downside_value = _round2(dcf_value(fcfs, rate, 0.0, fcfs[-1]))
            dcf["downside_value"] = downside_value
            section["dcf"] = dcf

    # --- Comps ---
    medians = peers_section.get("medians") or {}
    comps = []
    eps = derived("eps")
    if "pe_ratio" in medians and eps is not None:
        multiple = medians["pe_ratio"]["value"]
        n_pe = medians["pe_ratio"]["n"]
        comps.append({
            "method": "P/E",
            "metric_value": eps,
            "metric": "eps",
            "multiple": multiple,
            "multiple_source":
                f"median of {n_pe} peer pe_ratio "
                f"{'value' if n_pe == 1 else 'values'}",
            "implied_price": _round2(
                implied_price_from_multiple(eps, multiple)),
        })
    bvps = filed("book_value_per_share")
    if bvps is None:
        for record in values:
            if (record["field"] == "book_value_per_share"
                    and record["status"] == "filed"):
                bvps = record["value"]
    if "price_to_book" in medians and bvps is not None:
        multiple = medians["price_to_book"]["value"]
        n_pb = medians["price_to_book"]["n"]
        comps.append({
            "method": "P/B",
            "metric_value": bvps,
            "metric": "book_value_per_share",
            "multiple": multiple,
            "multiple_source": (
                f"median of {n_pb} peer price_to_book "
                f"{'value' if n_pb == 1 else 'values'}"),
            "implied_price": _round2(
                implied_price_from_multiple(bvps, multiple)),
        })
    if comps:
        section["comps"] = comps
    else:
        section["not_built"].append(
            "Comps not built: needs a peer median (pe_ratio with filed "
            "eps, or price_to_book with filed book_value_per_share)")

    # --- Sanity check (valuation-sanity-check.md) ---
    methods = []
    if section["dcf"] is not None:
        methods.append("DCF")
    if section["comps"]:
        methods.append("comps")
    peer_check = "no peer multiple available"
    if medians:
        peer_check = "; ".join(
            f"{metric} median {m['value']:.2f}x "
            f"({m['formula']})" for metric, m in sorted(medians.items()))
    downside = "no DCF built, so no downside case"
    if section["dcf"] is not None:
        downside = (
            f"DCF at terminal growth 0.00%: "
            f"{section['dcf']['downside_value']:,} enterprise value")
    section["sanity"] = {
        "method_fit": ", ".join(methods) if methods else "none",
        "peer_check": peer_check,
        "downside": downside,
        "one_sentence_test": (
            "This dossier is worth acting on only if the filed "
            "fundamentals and the stated valuation inputs hold."),
    }
    return section


def build_dossier(inputs):
    """Build the dossier data for one ticker as of one date.

    ``inputs`` (all data injected — no network, no LLM)::

        ticker (required), as_of (required, YYYY-MM-DD),
        company_name, exchange, industry (optional prose),
        edgar_facts (optional companyfacts dict),
        demo_fundamentals (optional list of value records, e.g. from
            load_demo_fundamentals),
        prices (optional fetch model or {source, last_close, last_date}),
        peers (optional list of {symbol, metric, value, source}),
        dcf (optional {free_cash_flows, discount_rate, terminal_growth,
            terminal_year_fcf}),
        risks (optional list of {risk, trigger}),
        next_event (optional {event, due_date}),
        earnings_events (optional list — see render_dossier_memo),
        run_id, ts (optional; ts defaults to f"{as_of}T00:00:00Z" so
            runs are deterministic).

    Returns the dossier dict: identity, sections (fundamentals,
    prices, peers, valuation, risks, checklist), the evidence table
    the memo renders from, and the event log. It does NOT render the
    memo or run the Reality Checker — see :func:`run_dossier` for
    the full run.
    """
    ticker = str(inputs.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("build_dossier needs a ticker")
    as_of = str(inputs.get("as_of") or "").strip()
    if not as_of:
        raise ValueError("build_dossier needs an as_of date")
    run_id = inputs.get("run_id") or f"dossier-{ticker}-{as_of}"
    ts = inputs.get("ts") or f"{as_of}T00:00:00Z"

    events = []
    seq = [0]

    def event(event_type, **fields):
        item = {"schema_version": "1.0.0", "run_id": run_id,
                "seq": seq[0], "ts": ts, "type": event_type}
        item.update(fields)
        seq[0] += 1
        events.append(item)
        return item

    steps = ["researcher", "forensic", "financial", "valuation",
             "reality_checker"]
    event("run_started", team="dossier", symbol=ticker, as_of=as_of)
    event("plan", steps=list(steps))

    # --- Step 1 (Researcher): identity + fundamentals ---
    event("step_started", step="researcher")
    values = []
    if inputs.get("edgar_facts") is not None:
        event("tool_call", tool="edgar.get_fundamentals_as_of",
              ticker=ticker, as_of=as_of)
        values = _edgar_values(inputs["edgar_facts"], as_of)
        event("tool_result", tool="edgar.get_fundamentals_as_of",
              status="ok", detail="edgar fundamentals fetched")
    elif inputs.get("demo_fundamentals") is not None:
        values = list(inputs["demo_fundamentals"])
        event("tool_call", tool="load_demo_fundamentals", ticker=ticker)
        event("tool_result", tool="load_demo_fundamentals", status="ok",
              detail="demo fundamentals loaded")
    else:
        values = [_unknown(
            field, "no fundamentals source supplied (no edgar_facts, "
                   "no demo_fundamentals)")
            for field in DOSSIER_EDGAR_CONCEPTS]
        event("tool_result", tool="fundamentals", status="ok",
              detail="no fundamentals source supplied")
    derived = _derived_values(values)
    values = values + derived
    # One tool_result carries every fundamentals number (filed and
    # DERIVED) — this is the evidence the memo's Financials section
    # traces to. One evidence_ref per distinct base source — the
    # memo's Sources section lists exactly these names.
    event("tool_result", tool="fundamentals", status="ok",
          detail="; ".join(
              f"{v['field']} {_fmt_number(v['value'], v['unit'])}"
              if v["status"] in ("filed", "DERIVED")
              else f"{v['field']} UNKNOWN"
              for v in values))
    seen_sources = []
    for record in values:
        if record["status"] in ("filed", "DERIVED") and record["source"]:
            base = record["source"].split(":")[0] if record["status"] == "filed" \
                else record["source"]
            if base not in seen_sources:
                seen_sources.append(base)
                event("evidence_ref", source=base)
    event("step_started", step="forensic")
    forensic = _forensic_section(values)
    event("tool_result", tool="forensic_check", status="ok",
          detail=forensic["detail"])

    # --- Step 2 (Financial is folded into values above; prices) ---
    event("step_started", step="financial")
    price_record, price_info = _prices_section(
        inputs.get("prices"), as_of)
    if price_record["status"] == "filed":
        event("tool_result", tool="prices", status="ok",
              detail=(f"last_close {price_record['value']} on "
                      f"{price_record['period']}"),
              last_close=price_record["value"])
        event("evidence_ref", source=price_record["source"])
    else:
        event("tool_result", tool="prices", status="ok",
              detail=f"last_close UNKNOWN: {price_record['reason']}")

    # --- Peers ---
    peers_section = _peers_section(inputs.get("peers"))
    if peers_section["status"] == "filed":
        for peer in peers_section["peers"]:
            event("evidence_ref", source=peer["source"])
        event("tool_result", tool="peers", status="ok",
              detail="; ".join(
                  f"{p['label']} {p['metric']} "
                  f"{_fmt_number(p['value'], _PEER_METRIC_UNITS.get(p['metric'], ''))}"
                  for p in peers_section["peers"]))
        for metric, median in peers_section["medians"].items():
            event("tool_result", tool="peer_median", status="ok",
                  detail=(f"{metric} median "
                          f"{_fmt_number(median['value'], _PEER_METRIC_UNITS.get(metric, ''))} "
                          f"from {median['n']} peers"),
                  **{f"{metric}_median": median["value"]})
    else:
        event("tool_result", tool="peers", status="ok",
              detail=f"peers UNKNOWN: {peers_section['reason']}")

    # --- Step 3 (Valuation) ---
    event("step_started", step="valuation")
    valuation = _valuation_section(values, peers_section,
                                   inputs.get("dcf"))
    if valuation["dcf"] is not None:
        dcf = valuation["dcf"]
        fcfs_text = ", ".join(_fmt_number(f) for f in dcf["free_cash_flows"])
        event("tool_result", tool="dcf_value", status="ok",
              detail=(
                  f"enterprise_value {dcf['enterprise_value']}; "
                  f"discount_rate {dcf['discount_rate'] * 100:.2f}%; "
                  f"terminal_growth {dcf['terminal_growth'] * 100:.2f}%; "
                  f"terminal_year_fcf {dcf['terminal_year_fcf']}; "
                  f"free_cash_flows {fcfs_text}; "
                  f"forecast_years {len(dcf['free_cash_flows'])}; "
                  f"downside_value {dcf['downside_value']}; "
                  f"downside terminal_growth 0.00%"),
              enterprise_value=dcf["enterprise_value"],
              downside_value=dcf["downside_value"])
        event("evidence_ref", source="DCF inputs (caller-supplied)")
        if dcf["per_share"] is not None:
            event("tool_result", tool="dcf_per_share", status="ok",
                  detail=(f"per_share {dcf['per_share']}; equity_value "
                          f"{dcf['equity_value']}; net_debt "
                          f"{dcf['net_debt']}"),
                  per_share=dcf["per_share"])
    for comp in valuation["comps"] or []:
        event("tool_result", tool="implied_price_from_multiple",
              status="ok",
              detail=(f"{comp['method']} implied_price "
                      f"{comp['implied_price']}; {comp['metric']} "
                      f"{comp['metric_value']}; multiple "
                      f"{_fmt_number(comp['multiple'], 'x')}"),
              implied_price=comp["implied_price"])
    if valuation["not_built"]:
        event("tool_result", tool="valuation", status="ok",
              detail="; ".join(valuation["not_built"]))

    # --- Earnings events (earnings-review template; only when supplied) ---
    for item in inputs.get("earnings_events") or []:
        event("tool_result", tool="earnings", status="ok",
              detail=(f"{item.get('period', '')}: reported "
                      f"{item.get('reported', 'UNKNOWN')}, expected "
                      f"{item.get('expected', 'UNKNOWN')}"))
        if item.get("source"):
            event("evidence_ref", source=item["source"])

    # --- Risks + checklist (protocol test 5) ---
    risks = []
    for item in inputs.get("risks") or []:
        risks.append({"risk": item.get("risk", ""),
                      "trigger": item.get("trigger", "")})
    next_event = inputs.get("next_event")
    checklist = {"next_event": next_event,
                 "reason": "" if next_event else
                 "no next filing or event supplied"}
    event("tool_result", tool="risks_checklist", status="ok",
          detail=(f"{len(risks)} risks; "
                  f"next_event {next_event['event'] if next_event else 'UNKNOWN'}"))

    # --- Evidence table: every number the memo may write ---
    evidence = _evidence_table(values, price_record, peers_section,
                               valuation, risks, checklist)
    event("tool_result", tool="dossier_counts", status="ok",
          detail=evidence["counts_detail"],
          forecast_years=evidence["counts"].get("forecast_years"),
          peer_count=evidence["counts"].get("peer_count"),
          risk_count=evidence["counts"].get("risk_count"))
    event("run_finished", status="ok", symbol=ticker)

    dossier = {
        "ticker": ticker,
        "as_of": as_of,
        "company_name": inputs.get("company_name") or ticker,
        "exchange": inputs.get("exchange") or "UNKNOWN",
        "industry": inputs.get("industry") or "UNKNOWN",
        "run_id": run_id,
        "values": values,
        "forensic": forensic,
        "price": price_record,
        "price_info": price_info,
        "peers": peers_section,
        "valuation": valuation,
        "risks": risks,
        "checklist": checklist,
        "earnings_events": inputs.get("earnings_events") or [],
        "evidence": evidence,
        "events": events,
        "template_map": dict(TEMPLATE_MAP),
    }
    return dossier


def _forensic_section(values):
    """Forensic checklist over the filed statements.

    Cash vs profit, profit quality, balance sheet. A flag is a
    question, not an accusation. With no filed statements the
    section is UNKNOWN with the reason stated.
    """
    by_field = {v["field"]: v for v in values}

    def filed(field):
        record = by_field.get(field)
        if record is not None and record["status"] == "filed":
            return record["value"]
        return None

    flags = []
    net_income = filed("net_income")
    ocf = filed("operating_cash_flow")
    if net_income is not None and ocf is not None:
        if ocf < net_income:
            flags.append(
                "operating cash flow below net income — ask why profit "
                "is not converting to cash")
        else:
            flags.append(
                "operating cash flow covers net income — no cash-vs-"
                "profit flag")
    else:
        flags.append(
            "cash vs profit not checked: needs filed net_income and "
            "operating_cash_flow")
    revenue = filed("revenue")
    if (revenue is not None and net_income is not None
            and net_income < 0):
        flags.append("net loss in the filed year — ask why")
    pct_record = by_field.get("liabilities_to_assets_pct")
    if pct_record is not None and pct_record["status"] == "DERIVED":
        flags.append(
            "liabilities are "
            f"{_fmt_number(pct_record['value'], '%')} of assets")
    if not flags:
        detail = "forensic: no filed statements to check"
    else:
        detail = "forensic: " + "; ".join(flags)
    return {"flags": flags, "detail": detail}


def _evidence_table(values, price_record, peers_section, valuation,
                    risks, checklist):
    """The numbers the memo may write, each with its evidence.

    The renderer writes numbers ONLY from this table (plus the
    counts below). A number not in this table must not appear in
    the memo — the Reality Checker enforces it.
    """
    entries = {}
    for record in values:
        if record["status"] in ("filed", "DERIVED") and \
                record["value"] is not None:
            entries[record["field"]] = {
                "value": record["value"], "unit": record["unit"],
                "source": record["source"]}
    if price_record["status"] == "filed":
        entries["last_close"] = {
            "value": price_record["value"], "unit": "",
            "source": price_record["source"]}
    for metric, median in (peers_section.get("medians") or {}).items():
        entries[f"{metric}_median"] = {
            "value": median["value"], "unit": "x",
            "source": median["formula"]}
    if valuation["dcf"] is not None:
        dcf = valuation["dcf"]
        entries["dcf_enterprise_value"] = {
            "value": dcf["enterprise_value"], "unit": "",
            "source": "DCF (caller-supplied inputs)"}
        entries["dcf_discount_rate"] = {
            "value": _round2(dcf["discount_rate"] * 100), "unit": "%",
            "source": "DCF input"}
        entries["dcf_terminal_growth"] = {
            "value": _round2(dcf["terminal_growth"] * 100), "unit": "%",
            "source": "DCF input"}
        entries["dcf_terminal_year_fcf"] = {
            "value": dcf["terminal_year_fcf"], "unit": "",
            "source": "DCF input"}
        entries["dcf_downside_value"] = {
            "value": dcf["downside_value"], "unit": "",
            "source": "DCF at terminal growth 0.0%"}
        if dcf["per_share"] is not None:
            entries["dcf_per_share"] = {
                "value": dcf["per_share"], "unit": "",
                "source": "DCF per-share (enterprise value less net debt, "
                          "divided by shares outstanding)"}
    for comp in valuation["comps"] or []:
        entries[f"comps_{comp['method']}_implied_price"] = {
            "value": comp["implied_price"], "unit": "",
            "source": comp["multiple_source"]}
        entries[f"comps_{comp['method']}_multiple"] = {
            "value": comp["multiple"], "unit": "x",
            "source": comp["multiple_source"]}
    counts = {}
    if valuation["dcf"] is not None:
        counts["forecast_years"] = len(
            valuation["dcf"]["free_cash_flows"])
    if peers_section.get("peers"):
        counts["peer_count"] = len(peers_section["peers"])
    counts["risk_count"] = len(risks)
    parts = []
    if "forecast_years" in counts:
        parts.append(f"{counts['forecast_years']} forecast years")
    if "peer_count" in counts:
        parts.append(f"{counts['peer_count']} peers")
    parts.append(f"{counts['risk_count']} risks")
    counts_detail = "dossier counts: " + ", ".join(parts)
    return {"entries": entries, "counts": counts,
            "counts_detail": counts_detail}


# ---------------------------------------------------------------------------
# Memo rendering (from the evidence table only)
# ---------------------------------------------------------------------------

def _evidence_sources(dossier):
    """Distinct base sources, in event-log order."""
    sources = []
    for event in dossier["events"]:
        if event.get("type") == "evidence_ref":
            source = event.get("source", "")
            if source and source not in sources:
                sources.append(source)
    return sources


def _num(dossier, key):
    """Formatted number for an evidence-table key, or None."""
    entry = dossier["evidence"]["entries"].get(key)
    if entry is None:
        return None
    return _fmt_number(entry["value"], entry["unit"])


def _field_value(dossier, field):
    """A dossier value record by field name, or None."""
    for record in dossier["values"]:
        if record["field"] == field:
            return record
    return None


def _field_line(record):
    """One Financials line for a value record."""
    if record["status"] == "UNKNOWN":
        return f"{record['field']}: UNKNOWN ({record['reason']})"
    period = _safe_period(record["period"])
    suffix = f", {period}" if period else ""
    return (f"{record['field']}: "
            f"{_fmt_number(record['value'], record['unit'])}"
            f" ({record['source']}{suffix})")


def render_dossier_memo(dossier, gate_verdict="BLOCK"):
    """Render the dossier memo (initiation-report template order).

    Every number in the memo comes from the dossier's evidence
    table, the forensic flags, or the counts detail — never from
    anywhere else. ``gate_verdict`` is the Reality Checker verdict
    for the Gate verdict section; :func:`run_dossier` renders first
    with the conservative ``BLOCK``, runs the check, and re-renders
    with ``PASS`` only when the check passes.
    """
    ticker = dossier["ticker"]
    as_of = dossier["as_of"]
    company = dossier["company_name"]
    exchange = dossier["exchange"]
    industry = dossier["industry"]
    lines = [f"# Dossier — {ticker} ({exchange}) — as of {as_of}", ""]

    def section(name, body):
        lines.append(f"## {name}")
        lines.extend(body)
        lines.append("")

    # --- Thesis (initiation §1) ---
    thesis = [f"{company} ({ticker}, {exchange}) is a {industry} company."]
    revenue = _num(dossier, "revenue")
    net_income = _num(dossier, "net_income")
    margin = _num(dossier, "net_margin_pct")
    period = ""
    record = _field_value(dossier, "revenue")
    if record is not None and record["period"]:
        period = _safe_period(record["period"])
    if revenue is not None and net_income is not None:
        thesis.append(
            f"Revenue {revenue} with net income {net_income}"
            + (f" in {period}" if period else "")
            + (f"; net margin {margin}." if margin else "."))
    elif revenue is not None:
        thesis.append(f"Revenue {revenue}"
                      + (f" in {period}." if period else ".")
                      + " Net income is UNKNOWN.")
    else:
        thesis.append("Revenue is UNKNOWN: no filed fundamentals.")
    close = _num(dossier, "last_close")
    per_share = _num(dossier, "dcf_per_share")
    if per_share is not None and close is not None:
        thesis.append(
            f"DCF per-share value {per_share} against a last close of "
            f"{close}.")
    elif per_share is not None:
        thesis.append(f"DCF per-share value {per_share}.")
    elif close is not None:
        thesis.append(f"Last close {close}; no DCF per-share value.")
    section("Thesis", thesis)

    # --- Business / Industry (initiation §2–3) ---
    section("Business", [(f"{company}, ticker {ticker}, exchange "
                         f"{exchange}.")])
    if industry == "UNKNOWN":
        section("Industry", ["Industry is UNKNOWN: no industry supplied."])
    else:
        section("Industry", [f"Industry: {industry}."])

    # --- Financials (initiation §4) ---
    section("Financials",
            [_field_line(record) for record in dossier["values"]])

    # --- Forensic (forensic-checklist) ---
    section("Forensic", list(dossier["forensic"]["flags"]))

    # --- Valuation (initiation §5 + valuation-sanity-check) ---
    valuation = dossier["valuation"]
    vlines = []
    dcf = valuation["dcf"]
    if dcf is not None:
        fcfs = "; ".join(_fmt_number(f) for f in dcf["free_cash_flows"])
        vlines.append(
            f"DCF enterprise value "
            f"{_num(dossier, 'dcf_enterprise_value')}. Inputs: free cash "
            f"flows {fcfs}; discount rate "
            f"{_num(dossier, 'dcf_discount_rate')}; terminal growth "
            f"{_num(dossier, 'dcf_terminal_growth')}; terminal-year free "
            f"cash flow {_num(dossier, 'dcf_terminal_year_fcf')}.")
        if dcf["per_share"] is not None:
            shares = _field_value(dossier, "shares_outstanding")
            vlines.append(
                f"DCF per-share value {_num(dossier, 'dcf_per_share')} "
                f"(equity value {_fmt_number(dcf['equity_value'])} less "
                f"net debt {_fmt_number(dcf['net_debt'])}, divided by "
                f"{_fmt_number(shares['value'])} shares).")
        else:
            vlines.append(
                "DCF per-share value not built: "
                f"{dcf['per_share_reason']}.")
        vlines.append(
            f"Downside case: DCF at terminal growth 0.00% gives "
            f"enterprise value {_num(dossier, 'dcf_downside_value')}.")
    for reason in valuation["not_built"]:
        vlines.append(f"Valuation not built: {reason}."
                      if not reason.startswith(("DCF", "Comps"))
                      else f"{reason}.")
    if dossier["peers"]["status"] == "filed":
        peer_lines = "; ".join(
            f"{p['label']} {p['metric']} "
            f"{_fmt_number(p['value'], _PEER_METRIC_UNITS.get(p['metric'], ''))}"
            for p in dossier["peers"]["peers"])
        vlines.append(f"Peers: {peer_lines}.")
    for comp in valuation["comps"] or []:
        key = f"comps_{comp['method']}_implied_price"
        multiple_key = f"comps_{comp['method']}_multiple"
        line = (f"{comp['method']} implied price {_num(dossier, key)} "
                f"({comp['metric']} {_fmt_number(comp['metric_value'])} x "
                f"multiple {_num(dossier, multiple_key)}, "
                f"{comp['multiple_source']}).")
        vlines.append(line)
    sanity = valuation["sanity"]
    vlines.append(f"Method fit: {sanity['method_fit']}.")
    vlines.append(f"Peer check: {sanity['peer_check']}.")
    vlines.append(f"Downside case: {sanity['downside']}.")
    vlines.append(f"One-sentence test: {sanity['one_sentence_test']}")
    section("Valuation", vlines)

    # --- Key risks (initiation §6) ---
    if dossier["risks"]:
        rlines = []
        for item in dossier["risks"]:
            trigger = item.get("trigger") or (
                "NOT STATED — a risk without an observable trigger is a "
                "Reality Checker fix")
            rlines.append(f"{item.get('risk', '')} — trigger: {trigger}")
        section("Key risks", rlines)
    else:
        section("Key risks", ["No risks supplied — the risk list is empty."])

    # --- What would change my mind (initiation §7) ---
    change = [
        "A new filing that restates any filed fundamental above.",
        "Any risk trigger in Key risks firing.",
    ]
    if per_share is not None and close is not None:
        change.append(
            f"A close below the DCF per-share value of {per_share} "
            "would change the valuation read.")
    section("What would change my mind", change)

    # --- Open questions (initiation §8) ---
    questions = []
    for record in dossier["values"]:
        if record["status"] == "UNKNOWN":
            questions.append(
                f"Why is {record['field']} UNKNOWN? {record['reason']}")
    if dossier["checklist"]["next_event"]:
        event_item = dossier["checklist"]["next_event"]
        questions.append(
            f"Next event to watch: {_safe_period(event_item['event'])} "
            f"(due {event_item['due_date']}).")
    else:
        questions.append(
            "No next filing or event supplied — the monitoring "
            "checklist has no dated item.")
    section("Open questions", questions)

    # --- Earnings (earnings-review; only when events supplied) ---
    if dossier["earnings_events"]:
        elines = []
        for item in dossier["earnings_events"]:
            elines.append(
                f"{item.get('period', '')}: reported "
                f"{item.get('reported', 'UNKNOWN')}, expected "
                f"{item.get('expected', 'UNKNOWN')} "
                f"({item.get('source', '')}).")
        section("Earnings", elines)

    # --- Ledger control (ledger-control-checklist) ---
    section("Ledger control", [
        (f"Frozen inputs: the values in Financials, each with its source "
         f"and period. Cut-off: {as_of} — no filing after this date is "
         "used. Restatement log: none supplied."),
    ])

    # --- Research to use case ---
    section("Research to use case", [
        ("Finding: the thesis above, with every number sourced or "
         "UNKNOWN. Proposed use: monitor the checklist in Open "
         "questions. Evidence gate: every number above traces to a "
         "source or is UNKNOWN. Kill criteria: any risk trigger firing, "
         "or a restatement of a filed fundamental."),
    ])

    # --- Debate (structured-debate) ---
    bull = []
    bear = []
    if revenue is not None and margin is not None:
        bull.append(f"Revenue {revenue} with net margin {margin}.")
    if per_share is not None:
        bull.append(f"DCF per-share value {per_share}.")
    if close is not None and per_share is not None:
        bear.append(
            f"Last close {close} against DCF per-share value {per_share}.")
    if dossier["risks"]:
        count = dossier["evidence"]["counts"]["risk_count"]
        bear.append(
            f"{count} {'risk carries' if count == 1 else 'risks carry'} "
            "triggers (see Key risks).")
    if not bull:
        bull.append("No sourced bull argument: fundamentals are UNKNOWN.")
    if not bear:
        bear.append("No sourced bear argument: no price or risk to cite.")
    section("Debate", [
        "Bull case: " + " ".join(bull),
        "Bear case: " + " ".join(bear),
        ("Risk scenarios: see Key risks. Falsifier: see What would "
         "change my mind."),
    ])

    # --- Gate verdict (no template — 9.2 checker contract) ---
    section("Gate verdict", [
        ("No position gate run — this dossier proposes no position, so "
         "there is no position-gate verdict to agree with. The "
         "dossier's gate is the Reality Checker (number audit, claim "
         f"audit, logic audit): {gate_verdict}."),
    ])

    # --- Sources (no template — 9.2 checker contract) ---
    section("Sources", [f"- {source}"
                        for source in _evidence_sources(dossier)])

    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------------
# Reality Checker (runbook step 5)
# ---------------------------------------------------------------------------

def _evidence_numbers(events):
    """Evidence (value, unit) pairs from an event log.

    Same tolerance rule as the checker: a memo number matches when
    the units agree and the values are within max(1% relative, 0.005
    absolute). This is the checker's own rule, restated here so the
    claim audit uses it too.
    """
    from .groundedness import collect_evidence
    return collect_evidence(events)


def _matches(value, unit, evidence):
    """True when (value, unit) matches any evidence item.

    ``evidence`` is the list of {"value", "unit", ...} dicts from
    ``groundedness.collect_evidence``. Same tolerance rule as the
    checker: units must agree and values within max(1% relative,
    0.005 absolute).
    """
    for item in evidence:
        if item["unit"] != unit:
            continue
        if abs(value - item["value"]) <= max(
                0.01 * abs(item["value"]), 0.005):
            return True
    return False


def reality_check(dossier, memo, events):
    """Reality Checker: number audit, claim audit, logic audit.

    Number audit is the 9.2 checker over (memo, events). Claim audit
    verifies every filed/DERIVED dossier value is in the evidence
    and every UNKNOWN has a reason. Logic audit re-checks the
    valuation arithmetic, the price cut-off (no look-ahead) and the
    filing cut-off. Returns {"verdict": "Pass"|"Fail", "fixes": [...],
    "number_audit": ..., "claim_audit": ..., "logic_audit": ...}.
    Any failure is a named fix list — the dossier is labelled
    In progress until the fixes are applied and the check re-run.
    """
    fixes = []

    # 1. Number audit (9.2 checker).
    number_audit = check_memo(memo, events)
    if number_audit["verdict"] != "PASS":
        fixes.extend(f"number audit: {reason}"
                     for reason in number_audit["reasons"])

    # 2. Claim audit.
    evidence = _evidence_numbers(events)
    claim_fixes = []
    for record in dossier["values"]:
        if record["status"] == "UNKNOWN":
            if not record["reason"]:
                claim_fixes.append(
                    f"UNKNOWN value {record['field']} has no reason")
            continue
        if record["value"] is None:
            claim_fixes.append(
                f"{record['field']} is {record['status']} with no value")
            continue
        if not _matches(record["value"],
                        _checker_unit(record["unit"]), evidence):
            claim_fixes.append(
                f"{record['field']} {record['value']} not in the "
                "event-log evidence")
        if not record["source"]:
            claim_fixes.append(f"{record['field']} has no source")
    for item in dossier["risks"]:
        if not item.get("trigger"):
            claim_fixes.append(
                f"risk without an observable trigger: {item.get('risk')}")
    if dossier["checklist"]["next_event"] is None and \
            not dossier["checklist"]["reason"]:
        claim_fixes.append("checklist has neither a next event nor a reason")
    fixes.extend(f"claim audit: {fix}" for fix in claim_fixes)

    # 3. Logic audit.
    logic_fixes = []
    valuation = dossier["valuation"]
    dcf = valuation["dcf"]
    if dcf is not None:
        if dcf["discount_rate"] <= dcf["terminal_growth"]:
            logic_fixes.append(
                "DCF built with discount_rate not above terminal_growth")
        recomputed = _round2(dcf_value(
            dcf["free_cash_flows"], dcf["discount_rate"],
            dcf["terminal_growth"], dcf["terminal_year_fcf"]))
        if abs(recomputed - dcf["enterprise_value"]) > 0.01:
            logic_fixes.append(
                "DCF enterprise value does not recompute from its inputs")
        if dcf["per_share"] is not None:
            equity = _round2(dcf["enterprise_value"] - dcf["net_debt"])
            shares = _field_value(dossier, "shares_outstanding")
            if shares is None or shares["value"] in (None, 0):
                logic_fixes.append("DCF per-share without shares")
            elif abs(_round2(equity / shares["value"])
                     - dcf["per_share"]) > 0.01:
                logic_fixes.append(
                    "DCF per-share does not recompute from equity value "
                    "and shares")
    for comp in valuation["comps"] or []:
        if comp["multiple"] <= 0:
            logic_fixes.append(
                f"{comp['method']} comps with a non-positive multiple")
        recomputed = _round2(implied_price_from_multiple(
            comp["metric_value"], comp["multiple"]))
        if abs(recomputed - comp["implied_price"]) > 0.01:
            logic_fixes.append(
                f"{comp['method']} implied price does not recompute")
    if not valuation["dcf"] and not valuation["comps"] \
            and not valuation["not_built"]:
        logic_fixes.append(
            "no valuation method built and no reason recorded")
    price = dossier["price"]
    if price["status"] == "filed" and price["period"] > dossier["as_of"]:
        logic_fixes.append("price last date is after as_of (look-ahead)")
    for record in dossier["values"]:
        filed_date = record.get("filed")
        if filed_date and filed_date > dossier["as_of"]:
            logic_fixes.append(
                f"{record['field']} filed {filed_date}, after as_of")
    fixes.extend(f"logic audit: {fix}" for fix in logic_fixes)

    verdict = "Pass" if not fixes else "Fail"
    return {
        "verdict": verdict,
        "fixes": fixes,
        "number_audit": number_audit,
        "claim_audit": {"pass": not claim_fixes, "fixes": claim_fixes},
        "logic_audit": {"pass": not logic_fixes, "fixes": logic_fixes},
    }


def run_dossier(inputs):
    """Full run: build, render, Reality-Check, label.

    Renders first with the conservative gate verdict ``BLOCK``,
    runs the Reality Checker, and re-renders with ``PASS`` only
    when the check passes — appending a ``gate_result`` event so
    the final memo and log agree. Returns {"dossier", "memo",
    "events", "reality_check", "status"} where status is "complete"
    on Pass and "In progress" on Fail (with the fix list).
    """
    dossier = build_dossier(inputs)
    events = dossier["events"]
    memo = render_dossier_memo(dossier, gate_verdict="BLOCK")
    check = reality_check(dossier, memo, events)
    verdict = check["verdict"]
    # The checker's gate vocabulary is PASS/BLOCK: a dossier whose
    # Reality Checker passes gets gate PASS; a failed dossier is
    # BLOCKed by its own gate.
    gate = "PASS" if verdict == "Pass" else "BLOCK"
    memo = render_dossier_memo(dossier, gate_verdict=gate)
    events.append({
        "schema_version": "1.0.0", "run_id": dossier["run_id"],
        "seq": len(events), "ts": dossier["events"][0]["ts"],
        "type": "gate_result", "verdict": gate,
        "gate": "reality_checker",
        "reason": ("all audits passed" if verdict == "Pass"
                   else "; ".join(check["fixes"])[:500]),
    })
    final = reality_check(dossier, memo, events)
    return {
        "dossier": dossier,
        "memo": memo,
        "events": events,
        "reality_check": final,
        "status": "complete" if final["verdict"] == "Pass"
                  else "In progress",
    }


# ---------------------------------------------------------------------------
# Live fetch wrapper (network; never used in tests)
# ---------------------------------------------------------------------------

def fetch_dossier_inputs(ticker, as_of, *, include_stooq=False,
                         user_agent=None):
    """Fetch live inputs for one ticker as of one date.

    Thin wrapper over ``edgar.get_fundamentals_as_of`` and
    ``connectors.fetch_prices_with_volumes``. Returns an inputs dict
    for :func:`build_dossier` (with ``edgar_facts`` and ``prices``).
    Network only — never call from tests.
    """
    from .connectors import fetch_prices_with_volumes
    concepts = [pair for pairs in DOSSIER_EDGAR_CONCEPTS.values()
                for pair in pairs]
    fundamentals = edgar.get_fundamentals_as_of(
        ticker, concepts, as_of, user_agent=user_agent)
    prices = fetch_prices_with_volumes(
        ticker, kind="stock", include_stooq=include_stooq)
    return {"ticker": ticker, "as_of": as_of,
            "edgar_facts": fundamentals.get("facts"),
            "prices": prices}
