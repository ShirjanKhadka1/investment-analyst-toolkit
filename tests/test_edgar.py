"""SEC EDGAR module: ticker->CIK, throttling, audited-annual extraction.

All network calls are monkeypatched — no live EDGAR request runs in
the test suite. Live verification (8 Nov 2026, this VM): data.sec.gov
companyfacts for Apple (CIK 320193) returned 200 in ~2.6s.
"""

import pytest

from toolkit import edgar

_TICKERS_FIXTURE = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}

_FACTS_FIXTURE = {
    "facts": {
        "us-gaap": {
            "Revenues": {"units": {"USD": [
                {"end": "2023-09-30", "val": 383285000000, "form": "10-K",
                 "filed": "2023-11-03", "accn": "0000320193-23-000106", "fy": 2023},
                {"end": "2024-09-28", "val": 391035000000, "form": "10-K",
                 "filed": "2024-11-01", "accn": "0000320193-24-000123", "fy": 2024},
                {"end": "2024-12-28", "val": 124300000000, "form": "10-Q",
                 "filed": "2025-01-30", "accn": "0000320193-25-000007", "fy": 2025},
            ]}},
            "NetIncomeLoss": {"units": {"USD": [
                {"end": "2024-09-28", "val": 93736000000, "form": "10-K",
                 "filed": "2024-11-01", "accn": "0000320193-24-000123", "fy": 2024},
            ]}},
        }
    }
}


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(edgar, "_ticker_cache", None)
    monkeypatch.setattr(edgar, "_last_call_monotonic", 0.0)


def test_ticker_to_cik_maps_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(
        edgar, "_get_json",
        lambda url, user_agent=None: calls.append(url) or _TICKERS_FIXTURE)
    assert edgar.ticker_to_cik("aapl") == 320193
    assert edgar.ticker_to_cik("MSFT") == 789019
    assert len(calls) == 1  # table fetched once, then cached
    assert "company_tickers.json" in calls[0]


def test_ticker_to_cik_unknown_and_empty(monkeypatch):
    monkeypatch.setattr(
        edgar, "_get_json",
        lambda url, user_agent=None: _TICKERS_FIXTURE)
    with pytest.raises(ValueError, match="unknown ticker"):
        edgar.ticker_to_cik("ZZZZ")
    with pytest.raises(ValueError, match="non-empty"):
        edgar.ticker_to_cik("  ")


def test_throttle_waits_half_sec_ceiling(monkeypatch):
    slept = []
    monkeypatch.setattr(edgar._time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(edgar, "_last_call_monotonic", 1000.0)
    monkeypatch.setattr(edgar._time, "monotonic", lambda: 1000.1)
    edgar._throttle()
    # 0.2s gap minus the 0.1s already elapsed (float tolerance 0.01s).
    assert slept and slept[0] > 0.09


def test_latest_annual_value_prefers_10k_over_newer_10q():
    out = edgar.latest_annual_value(_FACTS_FIXTURE, "us-gaap", "Revenues")
    assert out["value"] == 391035000000
    assert out["end"] == "2024-09-28"
    assert out["form"] == "10-K"
    assert out["fy"] == 2024


def test_latest_annual_value_missing_concept():
    with pytest.raises(ValueError, match="not in company facts"):
        edgar.latest_annual_value(_FACTS_FIXTURE, "us-gaap", "NoSuchTag")


def test_get_fundamentals_end_to_end(monkeypatch):
    def fake_get(url, user_agent=None):
        if "company_tickers.json" in url:
            return _TICKERS_FIXTURE
        return _FACTS_FIXTURE
    monkeypatch.setattr(edgar, "_get_json", fake_get)
    out = edgar.get_fundamentals(
        "AAPL", [("us-gaap", "Revenues"), ("us-gaap", "NetIncomeLoss")])
    assert out["ticker"] == "AAPL"
    assert out["cik"] == 320193
    assert out["source"] == "SEC EDGAR companyfacts"
    assert out["values"]["Revenues"]["value"] == 391035000000
    assert out["values"]["NetIncomeLoss"]["value"] == 93736000000


def test_get_fundamentals_missing_concept_raises(monkeypatch):
    monkeypatch.setattr(
        edgar, "_get_json",
        lambda url, user_agent=None: _TICKERS_FIXTURE
        if "company_tickers" in url else _FACTS_FIXTURE)
    with pytest.raises(ValueError, match="not in company facts"):
        edgar.get_fundamentals("AAPL", [("us-gaap", "NoSuchTag")])


def test_fetch_functions_validate_cik():
    with pytest.raises(ValueError, match="CIK must be digits"):
        edgar.fetch_submissions("ABC")
    with pytest.raises(ValueError, match="CIK must be digits"):
        edgar.fetch_company_facts("12x")
    with pytest.raises(ValueError, match="taxonomy and tag"):
        edgar.fetch_company_concept(320193, "", "Revenues")


def test_get_json_sends_declared_user_agent(monkeypatch):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        return _Resp()

    monkeypatch.setattr(edgar._urlrequest, "urlopen", fake_urlopen)
    edgar._get_json("https://data.sec.gov/submissions/CIK0000320193.json")
    assert seen["ua"] == edgar.EDGAR_USER_AGENT
    assert "investment-analyst-toolkit" in seen["ua"]


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"{}"


def test_get_json_allows_both_edgar_hosts(monkeypatch):
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append((req.full_url, req.get_header("Host")))
        return _Resp()

    monkeypatch.setattr(edgar._urlrequest, "urlopen", fake_urlopen)
    edgar._get_json("https://data.sec.gov/submissions/CIK0000320193.json")
    edgar._get_json("https://www.sec.gov/files/company_tickers.json")
    assert seen[0][0].startswith("https://data.sec.gov/")
    assert seen[1][0].startswith("https://www.sec.gov/")
    # No manual Host header: urllib sets it from the URL.
    assert seen[0][1] is None and seen[1][1] is None


@pytest.mark.parametrize("bad_url", [
    "https://data.sec.gov.evil.invalid/submissions/CIK0000320193.json",
    "https://www.sec.gov.evil.invalid/files/company_tickers.json",
    "https://evil.invalid/data.sec.gov/submissions/CIK0000320193.json",
    "https://evil.invalid/www.sec.gov/files/company_tickers.json",
    "https://data.sec.gov@evil.invalid/submissions/CIK0000320193.json",
    "https://www.sec.gov@evil.invalid/files/company_tickers.json",
    "http://data.sec.gov/submissions/CIK0000320193.json",
    "http://www.sec.gov/files/company_tickers.json",
    "https://sec.gov.evil.invalid/x.json",
    "ftp://data.sec.gov/x.json",
])
def test_get_json_rejects_lookalike_hosts(bad_url, monkeypatch):
    def boom(req, timeout=None):
        raise AssertionError("urlopen must not run for a rejected URL")

    monkeypatch.setattr(edgar._urlrequest, "urlopen", boom)
    with pytest.raises(ValueError, match="refusing non-EDGAR URL"):
        edgar._get_json(bad_url)


def test_check_edgar_url_accepts_exact_hosts_only():
    assert edgar._check_edgar_url(
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")
    assert edgar._check_edgar_url(
        "https://www.sec.gov/files/company_tickers.json")
    # Hostnames are case-insensitive; urlparse lowercases them.
    assert edgar._check_edgar_url("https://DATA.SEC.GOV/x.json")
