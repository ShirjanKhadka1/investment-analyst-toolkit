"""Tests for the 9.3 company dossier pipeline (Round 9, PR 9.3).

The pipeline is a pure function over injected data: no network, no
LLM, no live fetch in any test. Fixture data is the committed
synthetic EDGAR facts (examples/dossier/synthetic-edgar-facts.json)
and the demo CSVs (examples/research-demo-*.csv). Every test pins
one pre-registered protocol behaviour (docs/round-9-protocol.md,
section 9.3, tests 1-7).
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import evaluate_dossier as eval_suite

from toolkit import dossier
from toolkit.dossier import (
    build_dossier,
    load_demo_fundamentals,
    load_demo_peers,
    reality_check,
    render_dossier_memo,
    run_dossier,
)
from toolkit.groundedness import check_memo, extract_numbers
from toolkit.valuation import dcf_value

ROOT = Path(__file__).resolve().parent.parent
EDGAR_FIXTURE = ROOT / "examples" / "dossier" / "synthetic-edgar-facts.json"
DEMO_FUND = ROOT / "examples" / "research-demo-fundamentals.csv"
DEMO_PEERS = ROOT / "examples" / "research-demo-peers.csv"

FIXED_TS = "2026-03-31T00:00:00Z"


def _edgar_facts():
    return json.loads(EDGAR_FIXTURE.read_text(encoding="utf-8"))


def _edgar_inputs(as_of="2026-03-31", **overrides):
    inputs = {
        "ticker": "DEMO", "as_of": as_of,
        "company_name": "Demo Corporation", "exchange": "NYSE",
        "industry": "Beverages (soft drinks)",
        "edgar_facts": _edgar_facts(),
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
        "ts": FIXED_TS,
    }
    inputs.update(overrides)
    return inputs


def _demo_inputs(**overrides):
    inputs = {
        "ticker": "DEMO", "as_of": "2026-03-31",
        "company_name": "Demo Bank (synthetic)", "exchange": "NEPSE",
        "industry": "Banks",
        "demo_fundamentals": load_demo_fundamentals(DEMO_FUND),
        "peers": load_demo_peers(DEMO_PEERS)["peers"],
        "risks": [{"risk": "NPL rising",
                   "trigger": "Next annual report NPL above the filed "
                              "level"}],
        "next_event": {"event": "FY 2082/83 annual report",
                       "due_date": "2026-11-15"},
        "ts": FIXED_TS,
    }
    inputs.update(overrides)
    return inputs


def _value(result, field):
    for record in result["dossier"]["values"]:
        if record["field"] == field:
            return record
    raise AssertionError(f"no value record for {field}")


# --- Protocol test 1: ticker -> fundamentals, point-in-time ---

def test_edgar_revenue_is_the_10k_value_not_the_later_10q():
    result = run_dossier(_edgar_inputs())
    record = _value(result, "revenue")
    assert record["status"] == "filed"
    assert record["value"] == 385000000
    assert record["period"] == "FY 2025"


def test_no_filing_by_as_of_is_unknown_not_filled_from_later():
    # As of 2026-01-31 the only revenue filing visible is the 2024
    # 10-K (filed 2025-02-14): the freshest value filed by as_of is
    # the 2024 revenue, and the 2025 10-K (filed 2026-02-14) must
    # not leak in. Concepts with no filing by then are UNKNOWN.
    result = run_dossier(_edgar_inputs(as_of="2026-01-31"))
    revenue = _value(result, "revenue")
    assert revenue["status"] == "filed"
    assert revenue["value"] == 350000000
    assert revenue["period"] == "FY 2024"
    for field in ("net_income", "total_assets"):
        record = _value(result, field)
        assert record["status"] == "UNKNOWN"
        assert record["value"] is None
        assert "no filing by 2026-01-31" in record["reason"]
    # Before any filing at all, revenue is UNKNOWN too.
    early = run_dossier(_edgar_inputs(as_of="2025-01-31"))
    assert _value(early, "revenue")["status"] == "UNKNOWN"


def test_missing_concept_is_unknown():
    facts = _edgar_facts()
    # Keep only the revenue tags: every other concept is absent.
    facts["facts"]["us-gaap"] = {
        "RevenueFromContractWithCustomerExcludingAssessedTax":
            facts["facts"]["us-gaap"]
            ["RevenueFromContractWithCustomerExcludingAssessedTax"],
    }
    result = run_dossier(_edgar_inputs(edgar_facts=facts))
    assert _value(result, "revenue")["status"] == "filed"
    assert _value(result, "net_income")["status"] == "UNKNOWN"
    assert _value(result, "shares_outstanding")["status"] == "UNKNOWN"


def test_derived_values_carry_formulas():
    result = run_dossier(_edgar_inputs())
    margin = _value(result, "net_margin_pct")
    assert margin["status"] == "DERIVED"
    assert margin["value"] == pytest.approx(25.19)
    assert "net_income / revenue" in margin["source"]
    fcf = _value(result, "free_cash_flow")
    assert fcf["value"] == 110000000
    eps = _value(result, "eps")
    assert eps["value"] == pytest.approx(1.94)


# --- Protocol test 2: prices ---

def test_prices_carry_provenance():
    result = run_dossier(_edgar_inputs())
    price = result["dossier"]["price"]
    assert price["status"] == "filed"
    assert price["value"] == 131.50
    assert price["source"] == "Yahoo chart"
    assert price["period"] == "2026-03-31"


def test_no_prices_is_unknown_not_invented():
    inputs = _edgar_inputs()
    del inputs["prices"]
    result = run_dossier(inputs)
    assert result["dossier"]["price"]["status"] == "UNKNOWN"
    assert "no prices supplied" in result["dossier"]["price"]["reason"]
    assert result["status"] == "complete"


def test_price_after_as_of_is_unknown_look_ahead_guard():
    inputs = _edgar_inputs()
    inputs["prices"] = {"source": "Yahoo chart",
                        "pairs": [("2026-03-31", 131.50),
                                  ("2026-04-01", 132.00)]}
    result = run_dossier(inputs)
    assert result["dossier"]["price"]["status"] == "UNKNOWN"
    assert "after as_of" in result["dossier"]["price"]["reason"]


# --- Protocol test 3: peers ---

def test_peers_from_caller_and_median_is_derived():
    result = run_dossier(_edgar_inputs())
    peers = result["dossier"]["peers"]
    assert peers["status"] == "filed"
    assert peers["medians"]["pe_ratio"]["value"] == 10.75
    assert peers["medians"]["pe_ratio"]["n"] == 2


def test_no_peers_is_unknown_with_reason():
    inputs = _edgar_inputs()
    del inputs["peers"]
    result = run_dossier(inputs)
    assert result["dossier"]["peers"]["status"] == "UNKNOWN"
    assert result["dossier"]["peers"]["reason"] == \
        "no peer source supplied"
    assert result["status"] == "complete"


def test_digit_peer_symbols_never_reach_the_memo():
    result = run_dossier(_demo_inputs())
    assert "PEER1" not in result["memo"]
    assert "PEER2" not in result["memo"]
    assert "Peer A" in result["memo"]
    # The mapping is recorded in the dossier data: both demo rows
    # are the same symbol (PEER1), so both carry the one label.
    labels = [p["label"] for p in result["dossier"]["peers"]["peers"]]
    assert labels == ["Peer A", "Peer A"]


# --- Protocol test 4: valuation ---

def test_dcf_inputs_stated_and_value_recomputes():
    result = run_dossier(_edgar_inputs())
    dcf = result["dossier"]["valuation"]["dcf"]
    assert dcf is not None
    assert dcf["free_cash_flows"] == [110000000, 115000000, 120000000,
                                      125000000, 130000000]
    assert dcf["discount_rate"] == 0.09
    assert dcf["terminal_growth"] == 0.025
    assert dcf["terminal_year_fcf"] == 133250000
    assert dcf["enterprise_value"] == pytest.approx(
        dcf_value(dcf["free_cash_flows"], 0.09, 0.025, 133250000))
    assert dcf["per_share"] == pytest.approx(29.52)


def test_comps_use_peer_median_and_recompute():
    result = run_dossier(_edgar_inputs())
    comps = result["dossier"]["valuation"]["comps"]
    assert len(comps) == 1
    assert comps[0]["method"] == "P/E"
    assert comps[0]["multiple"] == 10.75
    assert comps[0]["implied_price"] == round(1.94 * 10.75, 2)


def test_demo_csv_dossier_builds_comps_not_dcf():
    result = run_dossier(_demo_inputs())
    valuation = result["dossier"]["valuation"]
    assert valuation["dcf"] is None
    assert any("no free-cash-flow inputs" in reason
               for reason in valuation["not_built"])
    comps = valuation["comps"]
    assert len(comps) == 1
    assert comps[0]["method"] == "P/B"
    assert comps[0]["implied_price"] == pytest.approx(240.0 * 1.4)
    assert result["status"] == "complete"


def test_dcf_not_built_when_rate_does_not_exceed_growth():
    inputs = _edgar_inputs()
    inputs["dcf"] = {"free_cash_flows": [100000000],
                     "discount_rate": 0.02, "terminal_growth": 0.025,
                     "terminal_year_fcf": 102500000}
    result = run_dossier(inputs)
    assert result["dossier"]["valuation"]["dcf"] is None
    assert any("does not exceed" in reason
               for reason in
               result["dossier"]["valuation"]["not_built"])
    assert result["status"] == "complete"


def test_sanity_check_is_recorded():
    result = run_dossier(_edgar_inputs())
    sanity = result["dossier"]["valuation"]["sanity"]
    assert sanity["method_fit"] == "DCF, comps"
    assert "10.75x" in sanity["peer_check"]
    assert "0.00%" in sanity["downside"]
    assert sanity["one_sentence_test"]


# --- Protocol test 5: risks and monitoring checklist ---

def test_risks_carry_triggers_and_checklist_is_dated():
    result = run_dossier(_edgar_inputs())
    assert len(result["dossier"]["risks"]) == 2
    assert all(r["trigger"] for r in result["dossier"]["risks"])
    checklist = result["dossier"]["checklist"]
    assert checklist["next_event"]["event"] == "Q2 2026 10-Q filing"
    assert checklist["next_event"]["due_date"] == "2026-08-10"


def test_risk_without_trigger_is_a_named_fix():
    inputs = _edgar_inputs()
    inputs["risks"] = [{"risk": "Something bad", "trigger": ""}]
    result = run_dossier(inputs)
    assert result["status"] == "In progress"
    assert any("risk without an observable trigger" in fix
               for fix in result["reality_check"]["fixes"])


def test_no_next_event_is_unknown_with_reason():
    inputs = _edgar_inputs()
    del inputs["next_event"]
    result = run_dossier(inputs)
    assert result["dossier"]["checklist"]["next_event"] is None
    assert result["dossier"]["checklist"]["reason"]
    assert result["status"] == "complete"


# --- Protocol test 6: sourced-or-UNKNOWN (9.2 checker) ---

def test_edgar_fixture_dossier_passes_checker_with_zero_unsourced():
    result = run_dossier(_edgar_inputs())
    check = check_memo(result["memo"], result["events"])
    assert check["verdict"] == "PASS"
    assert check["checks"]["groundedness"]["unsourced"] == []
    assert check["checks"]["groundedness"]["unparseable"] == []
    assert check["numbers_checked"] == check["numbers_grounded"]
    assert result["reality_check"]["verdict"] == "Pass"
    assert result["status"] == "complete"


def test_demo_fixture_dossier_passes_checker_with_zero_unsourced():
    result = run_dossier(_demo_inputs())
    check = check_memo(result["memo"], result["events"])
    assert check["verdict"] == "PASS"
    assert check["checks"]["groundedness"]["unsourced"] == []
    assert check["checks"]["groundedness"]["unparseable"] == []
    assert result["reality_check"]["verdict"] == "Pass"


def test_planted_number_in_memo_is_blocked():
    result = run_dossier(_edgar_inputs())
    planted = result["memo"] + "\nAnd also 999,999 extra.\n"
    check = check_memo(planted, result["events"])
    assert check["verdict"] == "BLOCK"
    assert any(item["token"] == "999,999" for item in
               check["checks"]["groundedness"]["unsourced"])


def test_number_in_risk_trigger_is_blocked_not_laundered():
    inputs = _edgar_inputs()
    inputs["risks"] = [
        {"risk": "Revenue falls",
         "trigger": "Next 10-Q revenue below 90,000,000"}]
    result = run_dossier(inputs)
    assert result["status"] == "In progress"
    assert any("90,000,000" in fix
               for fix in result["reality_check"]["fixes"])


def test_unknown_values_are_never_written_as_numbers():
    result = run_dossier(_demo_inputs())
    # npl_pct is UNKNOWN (no source in the CSV): its value 1.8 must
    # not appear anywhere in the memo.
    assert "1.8" not in result["memo"]
    assert "npl_pct: UNKNOWN" in result["memo"]


# --- Protocol test 7: template reuse ---

def test_every_memo_section_maps_to_a_template():
    result = run_dossier(_edgar_inputs())
    headings = [line[3:].strip() for line in result["memo"].splitlines()
                if line.startswith("## ")]
    assert headings
    for heading in headings:
        assert heading in dossier.TEMPLATE_MAP, heading
    # The two sections with no template are named, not silent.
    assert dossier.TEMPLATE_MAP["Gate verdict"].startswith("NO TEMPLATE")
    assert dossier.TEMPLATE_MAP["Sources"].startswith("NO TEMPLATE")


def test_earnings_section_only_when_events_supplied():
    result = run_dossier(_edgar_inputs())
    assert "## Earnings" not in result["memo"]
    inputs = _edgar_inputs()
    inputs["earnings_events"] = [
        {"period": "Q1 2026", "reported": 1.05, "expected": 1.00,
         "source": "Synthetic demo fixture"}]
    result = run_dossier(inputs)
    assert "## Earnings" in result["memo"]
    assert result["status"] == "complete"


# --- Renderer safety ---

def test_memo_never_contains_fiscal_year_slash():
    result = run_dossier(_demo_inputs())
    assert "2081/82" not in result["memo"]
    assert "2082/83" not in result["memo"]
    assert "FY 2081" in result["memo"]
    assert "FY 2082" in result["memo"]


def test_memo_never_contains_compact_money_suffix():
    for inputs in (_edgar_inputs(), _demo_inputs()):
        result = run_dossier(inputs)
        claims, unparseable = extract_numbers(result["memo"])
        assert unparseable == []
        for claim in claims:
            assert claim["token"][-1] not in ("M", "B")


def test_memo_never_contains_the_cik():
    result = run_dossier(_edgar_inputs())
    assert "0001234567" not in result["memo"]
    assert "1234567" not in result["memo"]


def test_forensic_flags_are_grounded():
    result = run_dossier(_edgar_inputs())
    assert "44.44% of assets" in result["memo"]
    check = check_memo(result["memo"], result["events"])
    assert check["verdict"] == "PASS"


# --- Loaders ---

def test_demo_loaders():
    fund = load_demo_fundamentals(DEMO_FUND)
    by_field = {r["field"]: r for r in fund}
    assert by_field["revenue"]["value"] == 1250.0
    assert by_field["revenue"]["unit"] == "NPR millions"
    assert by_field["revenue"]["csv_field"] == "revenue_npr_m"
    assert by_field["npl_pct"]["status"] == "UNKNOWN"
    peers = load_demo_peers(DEMO_PEERS)
    assert len(peers["peers"]) == 2
    assert len(peers["dropped"]) == 1
    assert "PEER2" in peers["dropped"][0]


# --- Baseline: strictly more sourced content than rules mode ---

def test_render_dossier_memo_renders_from_evidence():
    result = run_dossier(_edgar_inputs())
    # run_dossier re-renders with the PASS gate after the check.
    memo = render_dossier_memo(result["dossier"], "PASS")
    assert memo == result["memo"]
    assert "## Thesis" in memo
    assert "## Gate verdict" in memo


def test_reality_check_direct():
    result = run_dossier(_edgar_inputs())
    outcome = reality_check(
        result["dossier"], result["memo"], result["events"])
    assert outcome["verdict"] == "Pass"
    grounded = outcome["number_audit"]["checks"]["groundedness"]
    assert grounded["unsourced"] == []
    assert grounded["unparseable"] == []


def test_dossier_beats_rules_mode_on_sourced_content():
    from toolkit.rules_mode import run_global_rules_team
    for inputs in (_edgar_inputs(), _demo_inputs()):
        result = run_dossier(inputs)
        rules = run_global_rules_team(
            result["dossier"]["ticker"], "us",
            eval_suite.rules_inputs_for(result))
        assert eval_suite.dossier_sourced_count(result) > \
            eval_suite.rules_sourced_count(rules)


# --- Runner behaviour ---

def test_run_dossier_is_deterministic():
    first = run_dossier(_edgar_inputs())
    second = run_dossier(_edgar_inputs())
    assert first["memo"] == second["memo"]
    assert first["events"] == second["events"]
    assert first["status"] == second["status"]


def test_build_dossier_rejects_missing_ticker_and_date():
    with pytest.raises(ValueError):
        build_dossier({"as_of": "2026-03-31"})
    with pytest.raises(ValueError):
        build_dossier({"ticker": "DEMO"})
