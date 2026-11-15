"""Tests for the EDGAR point-in-time path (Round E, E.1).

No network: _get_json is stubbed, and the facts documents are
small hand-built dicts. The XBRL-tag cases mirror real filings
seen 2026-11-15 (Microsoft's stale "Revenues", Goldman Sachs'
"RevenuesNetOfInterestExpense", Visa's stale
"StockholdersEquity").
"""
import gzip
import json

import pytest

from toolkit import edgar


def _facts(*entries):
    """A minimal companyfacts doc from (taxonomy, tag, value,
    end, filed, form, fy) tuples."""
    facts = {"cik": 1, "entityName": "TEST", "facts": {}}
    for taxonomy, tag, value, end, filed, form, fy in entries:
        unit = facts["facts"].setdefault(taxonomy, {}).setdefault(
            tag, {"label": tag, "units": {"USD": []}})
        unit["units"]["USD"].append(
            {"end": end, "val": value, "accn": "0000000000-00-000000",
             "fy": fy, "fp": "FY" if form == "10-K" else "Q1",
             "form": form, "filed": filed})
    return facts


def test_declared_user_agent_requires_email():
    with pytest.raises(ValueError, match="contact email"):
        edgar.declared_user_agent("")
    with pytest.raises(ValueError, match="contact email"):
        edgar.declared_user_agent("not-an-email")
    ua = edgar.declared_user_agent("researcher@example.com")
    assert "researcher@example.com" in ua
    assert ua.startswith("investment-analyst-toolkit/")


def test_get_json_decompresses_gzip(monkeypatch):
    payload = {"a": 1}
    raw = gzip.compress(json.dumps(payload).encode("utf-8"))

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return raw

    monkeypatch.setattr(edgar._urlrequest, "urlopen",
                        lambda *args, **kwargs: FakeResponse())
    assert edgar._get_json("https://data.sec.gov/x",
                           "test-agent") == payload


def test_annual_value_as_of_prefers_10k():
    facts = _facts(
        ("us-gaap", "NetIncomeLoss", 100, "2023-12-31",
         "2024-02-20", "10-K", 2023),
        ("us-gaap", "NetIncomeLoss", 30, "2024-09-30",
         "2024-11-01", "10-Q", 2024))
    value = edgar.annual_value_as_of(facts, "us-gaap",
                                     "NetIncomeLoss", "2024-12-31")
    # 10-K preferred even though the 10-Q is more recent.
    assert value["value"] == 100
    assert value["form"] == "10-K"


def test_annual_value_as_of_ignores_later_filings():
    facts = _facts(
        ("us-gaap", "NetIncomeLoss", 100, "2023-12-31",
         "2024-02-20", "10-K", 2023),
        ("us-gaap", "NetIncomeLoss", 999, "2024-12-31",
         "2025-02-20", "10-K", 2024))
    value = edgar.annual_value_as_of(facts, "us-gaap",
                                     "NetIncomeLoss", "2024-12-31")
    assert value["value"] == 100
    with pytest.raises(ValueError, match="filed by 2023-12-31"):
        edgar.annual_value_as_of(facts, "us-gaap", "NetIncomeLoss",
                                 "2023-12-31")


def test_freshest_candidate_beats_stale_tag():
    # Microsoft-style: "Revenues" ends 2010, the contract-revenue
    # tag is current. A single-tag lookup would return the stale one.
    facts = _facts(
        ("us-gaap", "Revenues", 62_484_000_000, "2010-12-31",
         "2011-01-27", "10-Q", 2011),
        ("us-gaap",
         "RevenueFromContractWithCustomerExcludingAssessedTax",
         245_122_000_000, "2023-06-30", "2023-07-27", "10-K", 2023))
    value, _tax, tag = edgar.freshest_annual_value_as_of(
        facts, [("us-gaap", "Revenues"),
                ("us-gaap",
                 "RevenueFromContractWithCustomerExcludingAssessedTax")],
        "2024-12-31")
    assert tag == ("RevenueFromContractWithCustomerExcludingAssessedTax")
    assert value["value"] == 245_122_000_000


def test_freshest_candidate_gs_revenue_tag():
    # Goldman Sachs-style: no "Revenues" at all; the net-of-interest
    # tag is the revenue concept.
    facts = _facts(
        ("us-gaap", "RevenuesNetOfInterestExpense",
         53_512_000_000, "2023-12-31", "2024-01-30", "10-K", 2023))
    value, _tax, tag = edgar.freshest_annual_value_as_of(
        facts, [("us-gaap", "Revenues"),
                ("us-gaap", "RevenuesNetOfInterestExpense")],
        "2024-12-31")
    assert tag == "RevenuesNetOfInterestExpense"
    assert value["value"] == 53_512_000_000


def test_freshest_candidate_raises_when_all_missing():
    # A bank-style filer: no GrossProfit concept at all. The
    # caller records the concept as missing; no value is invented.
    facts = _facts(
        ("us-gaap", "NetIncomeLoss", 100, "2023-12-31",
         "2024-02-20", "10-K", 2023))
    with pytest.raises(ValueError, match="none of us-gaap:GrossProfit"):
        edgar.freshest_annual_value_as_of(
            facts, [("us-gaap", "GrossProfit")], "2024-12-31")
