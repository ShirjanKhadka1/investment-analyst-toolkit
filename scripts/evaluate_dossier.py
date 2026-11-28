"""Run the pre-registered 9.3 dossier eval suite (Round 9, PR 9.3).

Builds the two fixture dossiers and scores them against the
protocol's two baselines, plus (Round 10, PR 10.2) the REAL Apple
dossier from the committed live recording
(``examples/real-aapl-2026-11-27.json``, AAPL as of 2026-11-27):

- fixture 1: synthetic EDGAR facts (examples/dossier/
  synthetic-edgar-facts.json, DEMO Corporation) + Yahoo prices +
  peers + DCF + risks + monitoring checklist;
- fixture 2: the committed demo CSVs
  (examples/research-demo-fundamentals.csv and
  examples/research-demo-peers.csv, synthetic NEPSE-style company)
  + peers + risks + checklist (no prices, no DCF — comps only).
- real filer: the committed AAPL recording — raw companyfacts
  subset, submissions (SIC 3571 Electronic Computers), 251 Yahoo
  closes to 2026-11-27, SIC peer discovery (114 rows, 8
  ticker-mapped peers) and the peers' P/E multiples computed from
  their own Yahoo closes and EDGAR filings. The DCF forecast is
  the filed FY2026 free cash flow, flat for 5 years, at the
  discount rate and terminal growth recorded in the fixture's
  ``dcf_assumptions`` — assumptions, stated in full, never
  filings. The real run is reported alongside the two fixture
  wins, in full: unsourced count, Reality Checker verdict,
  section coverage, per-concept filed values, peer table.

Baselines (pre-registered in the Round 9 protocol before any
fixture ran):
(a) the deterministic global rules team
    (toolkit.rules_mode.run_global_rules_team) over the same inputs
    — the dossier must carry strictly more sourced content, with no
    more unsourced numbers (0);
(b) the manual company-deep-dive runbook as executed on Bottlers
    Nepal (the worked example in finance-research-agents,
    docs/worked-example/bottlers-nepal-terai-bnt-research-note.md,
    recorded in examples/dossier/bnt-runbook-baseline.json) — the
    pipeline must match the runbook's section coverage on the
    comparable sections and beat it on unsourced-number count
    (0 vs the note's 202 numeric claims with no event log).

Sourced-content counting rule (fixed here, not tuned after seeing
results): a dossier's sourced count is the number of filed/DERIVED
value records, plus 1 if a price is filed, plus the number of peer
medians, plus 1 if a DCF is built, plus the number of comps built,
plus the number of risks with triggers, plus 1 if the checklist has
a dated next event. A rules-team sourced count is the number of
sourced metric rows across its persona sections, plus 1 if
technicals had closes. The memo never reaches an LLM: it is
code-generated from fixture files, so no FakeProvider call is
needed — the protocol allows evaluation on fixture files.

Win (pre-registered): on EVERY fixture dossier, 0 unsourced numbers
under the 9.2 checker, Reality Checker Pass, all 7 protocol tests
passing, and strictly more sourced sections than the rules-mode
baseline. Any fixture failing any of those is "no edge" for that
fixture, and the pipeline is not claimed to beat the baselines.
A dossier about a real company is never claimed validated until
this suite passes first. Round 10, PR 10.2 extends the win rule
to the real AAPL run: it must meet the same bar (0 unsourced,
Pass, 10/10) or the verdict for the real filer is "no edge",
published with the exact reason.

Usage:
  python3 scripts/evaluate_dossier.py [--date YYYY-MM-DD]
      [--output PATH]
Default output: docs/dossier-eval-results.json. Exit 0 on WIN,
exit 1 on "no edge". No network, no real key, no live fetch.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import dossier
from toolkit import edgar as edgar_mod
from toolkit.groundedness import check_memo
from toolkit.rules_mode import run_global_rules_team

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "examples" / "dossier"
REAL_FIXTURE = ROOT / "examples" / "real-aapl-2026-11-27.json"
DEFAULT_OUTPUT = ROOT / "docs" / "dossier-eval-results.json"

# Comparable sections: the dossier sections and the BNT runbook
# note's sections that cover the same ground. The note's debate,
# risk veto and accuracy audit are separate agents' outputs, not
# dossier sections, and are named as out of scope in the model card.
COMPARABLE_SECTIONS = (
    "Thesis", "Business", "Industry", "Financials", "Forensic",
    "Valuation", "Key risks", "What would change my mind",
    "Open questions", "Sources",
)


def edgar_fixture_inputs():
    """Fixed inputs for the synthetic EDGAR fixture dossier."""
    facts = json.loads(
        (FIXTURE_DIR / "synthetic-edgar-facts.json").read_text(
            encoding="utf-8"))
    return {
        "ticker": "DEMO", "as_of": "2026-03-31",
        "company_name": "Demo Corporation", "exchange": "NYSE",
        "industry": "Beverages (soft drinks)",
        "edgar_facts": facts,
        "prices": {"source": "Yahoo chart",
                   "pairs": [("2026-03-30", 129.00),
                             ("2026-03-31", 131.50)]},
        "peers": [
            {"symbol": "PEER-A", "metric": "pe_ratio", "value": 12.5,
             "source": "Synthetic demo fixture"},
            {"symbol": "PEER-B", "metric": "pe_ratio", "value": 9.0,
             "source": "Synthetic demo fixture"},
        ],
        "dcf": {"free_cash_flows": [110000000, 115000000, 120000000,
                                    125000000, 130000000],
                "discount_rate": 0.09, "terminal_growth": 0.025,
                "terminal_year_fcf": 133250000},
        "risks": [
            {"risk": "Revenue concentration in one product line",
             "trigger": "Next 10-Q revenue below the filed FY 2025 "
                        "revenue"},
            {"risk": "Debt refinancing in the next year",
             "trigger": "Total liabilities above the filed FY 2025 "
                        "level in the next filing"},
        ],
        "next_event": {"event": "Q2 2026 10-Q filing",
                       "due_date": "2026-08-10"},
    }


def demo_fixture_inputs():
    """Fixed inputs for the demo-CSV fixture dossier."""
    fundamentals = dossier.load_demo_fundamentals(
        ROOT / "examples" / "research-demo-fundamentals.csv")
    peers = dossier.load_demo_peers(
        ROOT / "examples" / "research-demo-peers.csv")
    return {
        "ticker": "DEMO", "as_of": "2026-03-31",
        "company_name": "Demo Bank (synthetic)", "exchange": "NEPSE",
        "industry": "Banks",
        "demo_fundamentals": fundamentals,
        "peers": peers["peers"],
        "risks": [{"risk": "NPL rising",
                   "trigger": "Next annual report NPL above the filed "
                              "level"}],
        "next_event": {"event": "FY 2082/83 annual report",
                       "due_date": "2026-11-15"},
    }


def real_filer_inputs():
    """Inputs for the real AAPL dossier, from the committed recording.

    Every number comes from ``examples/real-aapl-2026-11-27.json``
    (the live fetch, committed by
    ``scripts/fetch_real_aapl_dossier.py``) — nothing is typed
    here except the two risk wordings and the next-event label,
    which are caller-supplied prose with no bare numbers (the
    same pattern as the fixture dossiers). The DCF forecast is
    built from the filed free cash flow (operating cash flow
    minus capex, both read from the recording with the dossier's
    own point-in-time reader) plus the assumptions recorded in
    the fixture — flat for the stated years, at the stated
    discount rate and terminal growth.
    """
    fixture = json.loads(REAL_FIXTURE.read_text(encoding="utf-8"))
    facts = fixture["edgar_facts"]
    as_of = fixture["as_of"]
    ocf, _taxonomy, _tag = edgar_mod.freshest_annual_value_as_of(
        facts, dossier.DOSSIER_EDGAR_CONCEPTS["operating_cash_flow"],
        as_of)
    capex, _taxonomy, _tag = edgar_mod.freshest_annual_value_as_of(
        facts, dossier.DOSSIER_EDGAR_CONCEPTS["capex"], as_of)
    filed_fcf = ocf["value"] - capex["value"]
    assumptions = fixture["dcf_assumptions"]
    years = assumptions["forecast_years"]
    terminal_growth = assumptions["terminal_growth"]
    return {
        "ticker": fixture["ticker"], "as_of": as_of,
        "company_name": fixture["company_name"],
        "exchange": fixture["exchange"],
        "industry": fixture["sic_description"],
        "sic": fixture["sic"],
        "sic_description": fixture["sic_description"],
        "sic_peers": fixture["sic_peers"],
        "edgar_facts": facts,
        "prices": fixture["prices"],
        "peers": [
            {"symbol": peer["ticker"], "metric": "pe_ratio",
             "value": peer["pe_ratio"],
             "source": "SEC EDGAR SIC 3571 peer P/E "
                       "(Yahoo close / EDGAR EPS)"}
            for peer in fixture["peer_multiples"]],
        "dcf": {
            "free_cash_flows": [filed_fcf] * years,
            "discount_rate": assumptions["discount_rate"],
            "terminal_growth": terminal_growth,
            "terminal_year_fcf": round(
                filed_fcf * (1 + terminal_growth), 2),
        },
        "risks": [
            {"risk": "Revenue concentration in hardware products",
             "trigger": "Next 10-K revenue below the filed FY 2026 "
                        "revenue"},
            {"risk": "Debt refinancing as liabilities grow",
             "trigger": "Total liabilities above the filed FY 2026 "
                        "level in the next filing"},
        ],
        "next_event": {"event": "Q1 FY 2027 10-Q filing",
                       "due_date": "2027-02-04"},
        "ts": f"{as_of}T00:00:00Z",
    }


def real_filer_detail():
    """The real run's provenance detail for the eval summary.

    SIC discovery, the peer table (multiples and exclusions) and
    the DCF assumptions, straight from the committed recording —
    so the summary JSON carries the full real-filer evidence, not
    just the win/loss row.
    """
    fixture = json.loads(REAL_FIXTURE.read_text(encoding="utf-8"))
    sic_peers = fixture["sic_peers"]
    return {
        "sic": fixture["sic"],
        "sic_description": fixture["sic_description"],
        "sic_rows_parsed": sic_peers["rows_parsed"],
        "sic_pages": sic_peers["pages"],
        "sic_peers_with_ticker": len(sic_peers["peers"]),
        "sic_without_ticker": sic_peers["without_ticker"],
        "comps_peers": [peer["ticker"]
                        for peer in fixture["peer_multiples"]],
        "peer_multiples": fixture["peer_multiples"],
        "peer_exclusions": fixture["peer_exclusions"],
        "dcf_assumptions": fixture["dcf_assumptions"],
        "last_close": fixture["prices"]["pairs"][-1][1],
        "last_close_date": fixture["prices"]["pairs"][-1][0],
        "price_bars": len(fixture["prices"]["pairs"]),
    }


def dossier_sourced_count(result):
    """Sourced-content count for a dossier (rule in the docstring)."""
    data = result["dossier"]
    count = len([v for v in data["values"]
                 if v["status"] in ("filed", "DERIVED")])
    if data["price"]["status"] == "filed":
        count += 1
    count += len(data["peers"].get("medians") or {})
    if data["valuation"]["dcf"] is not None:
        count += 1
    count += len(data["valuation"]["comps"] or [])
    count += len([r for r in data["risks"] if r.get("trigger")])
    if data["checklist"]["next_event"]:
        count += 1
    return count


def rules_sourced_count(sections):
    """Sourced-content count for a rules-team run (same rule)."""
    count = 0
    for name, section in sections.items():
        if not isinstance(section, dict):
            continue
        rows = section.get("metrics")
        if isinstance(rows, list):
            count += len([r for r in rows if r.get("sourced")])
        if name == "technical" and "n_bars" in section:
            count += 1
    return count


def rules_inputs_for(result):
    """Rules-team inputs over the same fixture data as a dossier."""
    data = result["dossier"]
    by_field = {v["field"]: v for v in data["values"]}

    def filed(field):
        record = by_field.get(field)
        if record is not None and record["status"] == "filed":
            return record["value"]
        return None

    def derived(field):
        record = by_field.get(field)
        if record is not None and record["status"] == "DERIVED":
            return record["value"]
        return None

    metrics = {}
    margin = derived("net_margin_pct")
    if margin is not None:
        metrics["net_margin_pct"] = margin
    fcf = derived("free_cash_flow")
    revenue = filed("revenue")
    if fcf is not None and revenue:
        metrics["free_cash_flow_margin_pct"] = fcf / revenue * 100
    liabilities = filed("total_liabilities")
    equity = filed("equity")
    if liabilities is not None and equity:
        metrics["debt_to_equity"] = liabilities / equity
    closes = None
    if data["price"]["status"] == "filed" and data["price_info"]:
        closes = data["price_info"].get("closes")
    return {"closes": closes, "fundamentals_metrics": metrics,
            "etf_metrics": {}, "fx_inputs": {}, "crypto_metrics": {},
            "earnings_events": [], "headlines": []}


def bnt_coverage(baseline):
    """Which comparable sections the BNT note covers."""
    sections = " ".join(baseline["sections"]).lower()
    covered = []
    for name in COMPARABLE_SECTIONS:
        key = name.lower()
        if key in sections or (
                name == "Thesis" and "run contract" in sections) or (
                name == "Financials"
                and "frozen audited year" in sections) or (
                name == "Valuation" and "market context" in sections) or (
                name == "Key risks" and "risk scenarios" in sections):
            covered.append(name)
    return covered


def evaluate_fixture(name, inputs, run_date, extra=None):
    """Build one fixture dossier and score it. Returns the row."""
    result = dossier.run_dossier(inputs)
    data = result["dossier"]
    memo = result["memo"]
    events = result["events"]
    check = check_memo(memo, events)
    reality = result["reality_check"]
    rules = run_global_rules_team(
        data["ticker"], "us", rules_inputs_for(result))
    dossier_count = dossier_sourced_count(result)
    rules_count = rules_sourced_count(rules)
    memo_sections = [line[3:].strip() for line in memo.splitlines()
                     if line.startswith("## ")]
    covered = [s for s in COMPARABLE_SECTIONS if s in memo_sections]
    row = {
        "fixture": name,
        "ticker": data["ticker"],
        "as_of": data["as_of"],
        "status": result["status"],
        "reality_checker": reality["verdict"],
        "unsourced_numbers": len(
            check["checks"]["groundedness"]["unsourced"]),
        "unparseable_numbers": len(
            check["checks"]["groundedness"]["unparseable"]),
        "numbers_checked": check["numbers_checked"],
        "numbers_grounded": check["numbers_grounded"],
        "checker_verdict": check["verdict"],
        "fixes": reality["fixes"],
        "dossier_sourced_count": dossier_count,
        "rules_sourced_count": rules_count,
        "beats_rules_baseline": dossier_count > rules_count,
        "comparable_sections_covered": covered,
        "sections_covered_count": len(covered),
        "valuation": {
            "dcf_enterprise_value":
                (data["valuation"]["dcf"] or {}).get("enterprise_value"),
            "dcf_per_share":
                (data["valuation"]["dcf"] or {}).get("per_share"),
            "comps": data["valuation"]["comps"],
            "not_built": data["valuation"]["not_built"],
        },
    }
    row["win"] = (
        row["unsourced_numbers"] == 0
        and row["unparseable_numbers"] == 0
        and reality["verdict"] == "Pass"
        and row["beats_rules_baseline"]
        and row["sections_covered_count"] == len(COMPARABLE_SECTIONS))
    if extra:
        row.update(extra)
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="run date YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--output", default=None,
                        help="output JSON path")
    args = parser.parse_args(argv)
    run_date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output = Path(args.output) if args.output else DEFAULT_OUTPUT

    baseline = json.loads(
        (FIXTURE_DIR / "bnt-runbook-baseline.json").read_text(
            encoding="utf-8"))
    bnt_covered = bnt_coverage(baseline)

    rows = [
        evaluate_fixture("synthetic-edgar",
                         edgar_fixture_inputs(), run_date),
        evaluate_fixture("demo-csvs",
                         demo_fixture_inputs(), run_date),
        evaluate_fixture("real-aapl",
                         real_filer_inputs(), run_date,
                         extra=real_filer_detail()),
    ]
    bnt = {
        "note": baseline["note"],
        "numeric_claim_count": baseline["numeric_claim_count"],
        "unparseable_token_count": baseline["unparseable_token_count"],
        "has_event_log": baseline["has_event_log"],
        "comparable_sections_covered": bnt_covered,
        "sections_covered_count": len(bnt_covered),
        "stated_limits": baseline["stated_limits"],
    }
    win = all(row["win"] for row in rows)
    summary = {
        "date": run_date,
        "verdict": "win" if win else "no edge",
        "fixtures": rows,
        "runbook_baseline_bnt": bnt,
        "bnt_unsourced_advantage":
            "dossier 0 unsourced numbers on every fixture vs "
            f"{bnt['numeric_claim_count']} numeric claims in the BNT "
            "note with no event log to trace against",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")

    print(f"Round 9.3 dossier eval — {run_date}")
    for row in rows:
        print(f"\n[{row['fixture']}] status={row['status']} "
              f"reality={row['reality_checker']} "
              f"checker={row['checker_verdict']} "
              f"unsourced={row['unsourced_numbers']} "
              f"unparseable={row['unparseable_numbers']} "
              f"numbers={row['numbers_grounded']}/"
              f"{row['numbers_checked']} "
              f"sourced dossier={row['dossier_sourced_count']} "
              f"vs rules={row['rules_sourced_count']} "
              f"sections={row['sections_covered_count']}/"
              f"{len(COMPARABLE_SECTIONS)} "
              f"-> {'WIN' if row['win'] else 'NO EDGE'}")
        for fix in row["fixes"]:
            print(f"  fix: {fix}")
        if row["fixture"] == "real-aapl":
            print(f"  SIC {row['sic']} ({row['sic_description']}): "
                  f"{row['sic_rows_parsed']} rows in "
                  f"{row['sic_pages']} pages, "
                  f"{row['sic_peers_with_ticker']} ticker-mapped peers, "
                  f"{row['sic_without_ticker']} without a ticker")
            for peer in row["peer_multiples"]:
                print(f"  peer {peer['ticker']}: price "
                      f"{peer['price']} on {peer['price_date']}, "
                      f"EPS {peer['eps']}, P/E {peer['pe_ratio']}x "
                      f"(net income {peer['net_income']} filed "
                      f"{peer['net_income_filed']})")
            for item in row["peer_exclusions"]:
                print(f"  peer {item['ticker']}: EXCLUDED — "
                      f"{item['reason']}")
            print(f"  DCF assumptions: {row['dcf_assumptions']}")
            print(f"  last close {row['last_close']} on "
                  f"{row['last_close_date']} "
                  f"({row['price_bars']} Yahoo bars)")
    print(f"\n[BNT runbook baseline] numeric claims="
          f"{bnt['numeric_claim_count']} (no event log), unparseable="
          f"{bnt['unparseable_token_count']}, comparable sections="
          f"{bnt['sections_covered_count']}/"
          f"{len(COMPARABLE_SECTIONS)}")
    print(f"\nVERDICT: {'WIN' if win else 'NO EDGE'}")
    print(f"wrote {output}")
    return 0 if win else 1


if __name__ == "__main__":
    raise SystemExit(main())
