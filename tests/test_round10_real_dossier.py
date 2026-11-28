"""Tests for Round 10, PR 10.2: real-company dossier + SIC peer lookup.

All tests run offline: the live AAPL fetch is a committed
recording (``examples/real-aapl-2026-11-27.json``) plus the SIC
browse-edgar page recordings
(``tests/fixtures/sic-3571-browse-edgar-page*.html``), and every
network call in the wrapper / peer-lookup tests is monkeypatched.
No test calls the network. Protocol: docs/round-10-protocol.md
section 10.2.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import evaluate_dossier as eval_suite

from toolkit import connectors as connectors_mod
from toolkit import dossier, edgar
from toolkit.dossier import run_dossier
from toolkit.groundedness import check_memo

ROOT = REPO_ROOT
REAL_FIXTURE = ROOT / "examples" / "real-aapl-2026-11-27.json"
SIC_PAGE1 = ROOT / "tests" / "fixtures" / "sic-3571-browse-edgar-page1.html"
SIC_PAGE2 = ROOT / "tests" / "fixtures" / "sic-3571-browse-edgar-page2.html"

AS_OF = "2026-11-27"
FIXED_TS = "2026-11-27T00:00:00Z"

# The nine CIKs the SIC 3571 pages map to a ticker (live
# company_tickers_exchange.json, 2026-11-28), as a small
# monkeypatched exchange table for the peer-lookup tests.
_EXCHANGE_ROWS = [
    [320193, "Apple Inc.", "AAPL", "Nasdaq"],
    [1571996, "Dell Technologies Inc.", "DELL", "NYSE"],
    [926326, "OMNICELL, INC.", "OMCL", "Nasdaq"],
    [1394056, "ONE STOP SYSTEMS, INC.", "OSS", "Nasdaq"],
    [2133022, "Oura Inc.", "OURA", None],
    [944075, "SOCKET MOBILE, INC.", "SCKT", "OTC"],
    [1375365, "Super Micro Computer, Inc.", "SMCI", "Nasdaq"],
    [1375365, "Super Micro Computer, Inc.", "SMCIP", "Nasdaq"],
    [2097163, "Xanadu Quantum Technologies Ltd", "XNDU", "Nasdaq"],
    [1720446, "Zepp Health Corp", "ZEPP", "NYSE"],
]

# Filed FY2026 values (10-K filed 2026-11-03) pinned from the
# recording — every number below is read from the fixture by the
# test, never typed into the dossier itself.
EXPECTED_FILED = {
    "revenue": 478834000000,
    "net_income": 132175000000,
    "gross_profit": 233602000000,
    "operating_income": 158063000000,
    "total_assets": 404548000000,
    "total_liabilities": 292455000000,
    "equity": 112093000000,
    "cash": 36305000000,
    "operating_cash_flow": 145097000000,
    "capex": 9041000000,
    "shares_outstanding": 14532259000,
}


def _real_fixture():
    return json.loads(REAL_FIXTURE.read_text(encoding="utf-8"))


def _value(result, field):
    for record in result["dossier"]["values"]:
        if record["field"] == field:
            return record
    raise AssertionError(f"no value record for {field}")


def _sections(memo):
    return [line[3:].strip() for line in memo.splitlines()
            if line.startswith("## ")]


# --- Protocol test 1: the live-fetch wrapper, fixed ---

def _fake_edgar_json(fixture):
    def fake(url, user_agent=None):
        if "company_tickers_exchange.json" in url:
            return {"fields": ["cik", "name", "ticker", "exchange"],
                    "data": _EXCHANGE_ROWS}
        if "company_tickers.json" in url:
            return {"0": {"cik_str": 320193, "ticker": "AAPL",
                          "title": "Apple Inc."}}
        if "companyfacts" in url:
            return fixture["edgar_facts"]
        if "submissions" in url:
            return fixture["submissions"]
        raise AssertionError(f"unexpected EDGAR URL {url}")
    return fake


def _fake_edgar_text():
    page1 = SIC_PAGE1.read_text(encoding="utf-8")
    page2 = SIC_PAGE2.read_text(encoding="utf-8")

    def fake(url, user_agent=None):
        if "start=100" in url:
            return page2
        if "SIC=3571" in url:
            return page1
        raise AssertionError(f"unexpected EDGAR URL {url}")
    return fake


def test_fetch_dossier_inputs_returns_raw_facts_not_none(monkeypatch):
    """Both wrapper bugs, fixed: the raw companyfacts dict is
    returned as edgar_facts (never None), and the fetch does not
    crash on Apple's missing noncontrolling-interest equity tag."""
    fixture = _real_fixture()
    monkeypatch.setattr(edgar, "_get_json", _fake_edgar_json(fixture))
    monkeypatch.setattr(edgar, "_get_text", _fake_edgar_text())
    monkeypatch.setattr(
        edgar, "get_fundamentals_as_of",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("wrapper must not call get_fundamentals_as_of")))
    monkeypatch.setattr(
        connectors_mod, "fetch_prices_with_volumes",
        lambda ticker, kind="stock", include_stooq=False: {
            "source": "yahoo",
            "pairs": [[date, close] for date, close
                      in fixture["prices"]["pairs"]]})
    inputs = dossier.fetch_dossier_inputs("AAPL", AS_OF)
    assert inputs["ticker"] == "AAPL"
    assert inputs["as_of"] == AS_OF
    # Bug 2 fixed: edgar_facts is the raw companyfacts dict.
    assert inputs["edgar_facts"] is fixture["edgar_facts"]
    assert inputs["edgar_facts"] is not None
    assert inputs["edgar_facts"]["entityName"] == "Apple Inc."
    # Bug 1 fixed: no crash although Apple carries no
    # StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest.
    assert ("StockholdersEquityIncludingPortionAttributableToNoncontrolling"
            "Interest" not in
            inputs["edgar_facts"]["facts"]["us-gaap"])
    assert inputs["submissions"]["sic"] == "3571"
    assert inputs["submissions"]["sicDescription"] == \
        "Electronic Computers"
    assert inputs["company_name"] == "Apple Inc."
    assert inputs["exchange"] == "Nasdaq"
    assert inputs["sic"] == "3571"
    assert inputs["prices"]["pairs"][-1][0] == AS_OF
    assert inputs["prices"]["pairs"][-1][1] == pytest.approx(358.54)
    # SIC discovery rode along, excluding the filer itself.
    assert inputs["sic_peers"]["rows_parsed"] == 114
    assert {peer["ticker"] for peer in inputs["sic_peers"]["peers"]} == {
        "DELL", "OMCL", "OSS", "OURA", "SCKT", "SMCI", "XNDU", "ZEPP"}
    assert "AAPL" not in {peer["ticker"]
                          for peer in inputs["sic_peers"]["peers"]}


def test_fetch_dossier_inputs_end_to_end_from_wrapper(monkeypatch):
    """The wrapper's inputs build a complete dossier: every one
    of the 11 concepts is filed from the raw facts."""
    fixture = _real_fixture()
    monkeypatch.setattr(edgar, "_get_json", _fake_edgar_json(fixture))
    monkeypatch.setattr(edgar, "_get_text", _fake_edgar_text())
    monkeypatch.setattr(
        connectors_mod, "fetch_prices_with_volumes",
        lambda ticker, kind="stock", include_stooq=False: {
            "source": "yahoo",
            "pairs": [[date, close] for date, close
                      in fixture["prices"]["pairs"]]})
    inputs = dossier.fetch_dossier_inputs("AAPL", AS_OF)
    inputs["ts"] = FIXED_TS
    result = run_dossier(inputs)
    assert result["status"] == "complete"
    for field, value in EXPECTED_FILED.items():
        record = _value(result, field)
        assert record["status"] == "filed", field
        assert record["value"] == value, field
        assert record["period"] == "FY 2026", field
        assert record["filed"] == "2026-11-03", field


def test_fetch_dossier_inputs_peer_failure_does_not_sink_fetch(
        monkeypatch):
    """No peer source (SIC page down) leaves the peers section
    UNKNOWN with the reason — the base fetch still returns."""
    fixture = _real_fixture()
    monkeypatch.setattr(edgar, "_get_json", _fake_edgar_json(fixture))

    def failing_text(url, user_agent=None):
        raise RuntimeError("EDGAR network call failed")
    monkeypatch.setattr(edgar, "_get_text", failing_text)
    monkeypatch.setattr(
        connectors_mod, "fetch_prices_with_volumes",
        lambda ticker, kind="stock", include_stooq=False: {
            "source": "yahoo",
            "pairs": [[date, close] for date, close
                      in fixture["prices"]["pairs"]]})
    inputs = dossier.fetch_dossier_inputs("AAPL", AS_OF)
    assert inputs["edgar_facts"] is not None
    assert inputs["sic_peers"] is None
    assert "SIC peer lookup failed" in inputs["peers_reason"]
    inputs["ts"] = FIXED_TS
    result = run_dossier(inputs)
    peers = result["dossier"]["peers"]
    assert peers["status"] == "UNKNOWN"
    assert "SIC peer lookup failed" in peers["reason"]


# --- Protocol test 2: SIC peer lookup ---

def test_parse_sic_browse_page_from_committed_recordings():
    page1 = edgar.parse_sic_browse_page(
        SIC_PAGE1.read_text(encoding="utf-8"))
    page2 = edgar.parse_sic_browse_page(
        SIC_PAGE2.read_text(encoding="utf-8"))
    assert len(page1) == 100
    assert len(page2) == 14
    assert {"cik": 320193, "name": "Apple Inc."} in page1
    assert {"cik": 1375365,
            "name": "Super Micro Computer, Inc."} in page2
    assert all(isinstance(row["cik"], int) for row in page1 + page2)
    assert len({row["cik"] for row in page1 + page2}) == 114


def test_parse_sic_browse_page_rejects_non_text_and_empty():
    with pytest.raises(TypeError, match="must be text"):
        edgar.parse_sic_browse_page(None)
    assert edgar.parse_sic_browse_page("<html></html>") == []


def test_parse_company_tickers_exchange_picks_common_ticker():
    parsed = edgar.parse_company_tickers_exchange(
        {"fields": ["cik", "name", "ticker", "exchange"],
         "data": _EXCHANGE_ROWS})
    smci = parsed[1375365]
    assert smci["ticker"] == "SMCI"
    assert smci["tickers"] == ["SMCI", "SMCIP"]
    assert parsed[320193]["ticker"] == "AAPL"
    assert parsed[2133022]["ticker"] == "OURA"
    assert parsed[2133022]["exchange"] is None


def test_parse_company_tickers_exchange_no_ticker_is_not_a_peer():
    parsed = edgar.parse_company_tickers_exchange(
        {"fields": ["cik", "name", "ticker", "exchange"],
         "data": [[123, "Defunct Corp", None, None]]})
    assert parsed[123]["ticker"] is None
    assert parsed[123]["tickers"] == []


def test_get_sic_peers_discovers_real_peers_mocked(monkeypatch):
    monkeypatch.setattr(edgar, "_get_json", _fake_edgar_json(
        _real_fixture()))
    monkeypatch.setattr(edgar, "_get_text", _fake_edgar_text())
    out = edgar.get_sic_peers("3571", exclude_cik=320193)
    assert out["sic"] == "3571"
    assert out["rows_parsed"] == 114
    assert out["pages"] == 2
    assert out["without_ticker"] == 105
    tickers = {peer["ticker"] for peer in out["peers"]}
    assert tickers == {"DELL", "OMCL", "OSS", "OURA", "SCKT", "SMCI",
                      "XNDU", "ZEPP"}
    dell = next(peer for peer in out["peers"]
                if peer["ticker"] == "DELL")
    assert dell == {"cik": "0001571996",
                    "name": "Dell Technologies Inc.", "ticker": "DELL",
                    "tickers": ["DELL"], "exchange": "NYSE"}
    smci = next(peer for peer in out["peers"]
                if peer["ticker"] == "SMCI")
    assert smci["tickers"] == ["SMCI", "SMCIP"]


def test_get_sic_peers_validates_sic_and_reports_network_failure(
        monkeypatch):
    with pytest.raises(ValueError, match="SIC must be 4 digits"):
        edgar.get_sic_peers("357")
    monkeypatch.setattr(
        edgar, "_get_text",
        lambda url, user_agent=None: (_ for _ in ()).throw(
            RuntimeError("EDGAR network call failed")))
    with pytest.raises(RuntimeError, match="EDGAR network call failed"):
        edgar.get_sic_peers("3571")


# --- Protocol test 3: the real Apple dossier ---

def test_real_aapl_dossier_from_fixture_passes_checker():
    inputs = eval_suite.real_filer_inputs()
    result = run_dossier(inputs)
    assert result["status"] == "complete"
    assert result["reality_check"]["verdict"] == "Pass"
    assert result["reality_check"]["fixes"] == []
    check = check_memo(result["memo"], result["events"])
    assert check["verdict"] == "PASS"
    assert check["numbers_checked"] == 61
    assert check["numbers_grounded"] == 61
    assert check["checks"]["groundedness"]["unsourced"] == []
    assert check["checks"]["groundedness"]["unparseable"] == []
    sections = _sections(result["memo"])
    for name in eval_suite.COMPARABLE_SECTIONS:
        assert name in sections
    assert len([s for s in eval_suite.COMPARABLE_SECTIONS
                if s in sections]) == 10


def test_real_aapl_filed_values_are_the_fy2026_10k():
    inputs = eval_suite.real_filer_inputs()
    result = run_dossier(inputs)
    for field, value in EXPECTED_FILED.items():
        record = _value(result, field)
        assert record["status"] == "filed", field
        assert record["value"] == value, field
        assert record["period"] == "FY 2026", field
        assert record["filed"] == "2026-11-03", field
    price = result["dossier"]["price"]
    assert price["status"] == "filed"
    assert price["value"] == 358.54
    assert price["period"] == AS_OF
    assert result["dossier"]["sic"] == "3571"
    assert result["dossier"]["sic_peers"]["rows_parsed"] == 114


def test_real_aapl_operating_cash_flow_uses_apple_tag_variant():
    """Apple files operating cash flow under
    NetCashProvidedByUsedInOperatingActivities, not
    NetCashProvidedByOperatingActivities — the concept map must
    carry the variant or free cash flow (and the DCF) is UNKNOWN."""
    inputs = eval_suite.real_filer_inputs()
    result = run_dossier(inputs)
    ocf = _value(result, "operating_cash_flow")
    assert ocf["status"] == "filed"
    assert ocf["value"] == 145097000000
    fcf = _value(result, "free_cash_flow")
    assert fcf["status"] == "DERIVED"
    assert fcf["value"] == 136056000000


def test_real_aapl_valuation_from_real_inputs():
    inputs = eval_suite.real_filer_inputs()
    result = run_dossier(inputs)
    valuation = result["dossier"]["valuation"]
    assert valuation["not_built"] == []
    dcf = valuation["dcf"]
    assert dcf["free_cash_flows"] == [136056000000] * 5
    assert dcf["discount_rate"] == 0.09
    assert dcf["terminal_growth"] == 0.025
    assert dcf["terminal_year_fcf"] == 139457400000
    assert dcf["enterprise_value"] == 1923637181695.24
    assert dcf["per_share"] == 114.74
    assert dcf["downside_value"] == 1511733333333.33
    assert result["dossier"]["peers"]["medians"]["pe_ratio"] == {
        "value": 28.23, "n": 4,
        "formula": "median of 4 peer pe_ratio values"}
    assert valuation["comps"] == [{
        "method": "P/E", "metric_value": 9.1, "metric": "eps",
        "multiple": 28.23,
        "multiple_source": "median of 4 peer pe_ratio values",
        "implied_price": 256.89}]


def test_real_aapl_dossier_beats_rules_baseline():
    inputs = eval_suite.real_filer_inputs()
    result = run_dossier(inputs)
    rules = eval_suite.run_global_rules_team(
        "AAPL", "us", eval_suite.rules_inputs_for(result))
    assert (eval_suite.dossier_sourced_count(result)
            > eval_suite.rules_sourced_count(rules))


# --- Protocol test 5: no look-ahead ---

def test_real_aapl_look_ahead_moves_as_of_earlier():
    """As of 2026-10-01 (before the FY2026 10-K was filed
    2026-11-03) the dossier shows the FY2025 filing instead, and
    the price series ending 2026-11-27 is UNKNOWN — the later
    filing never leaks in."""
    inputs = eval_suite.real_filer_inputs()
    inputs["as_of"] = "2026-10-01"
    inputs["ts"] = "2026-10-01T00:00:00Z"
    del inputs["dcf"]    # built from the FY2026 filed FCF
    del inputs["peers"]  # built from filings by 2026-11-27
    result = run_dossier(inputs)
    revenue = _value(result, "revenue")
    assert revenue["status"] == "filed"
    assert revenue["value"] == 416161000000
    assert revenue["period"] == "FY 2025"
    assert revenue["filed"] == "2025-10-31"
    assert revenue["value"] != EXPECTED_FILED["revenue"]
    ocf = _value(result, "operating_cash_flow")
    assert ocf["value"] == 111482000000
    assert ocf["filed"] == "2025-10-31"
    price = result["dossier"]["price"]
    assert price["status"] == "UNKNOWN"
    assert "after as_of" in price["reason"]
    assert result["reality_check"]["verdict"] == "Pass"
