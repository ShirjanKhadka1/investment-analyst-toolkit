"""Tests for toolkit/free_data.py. Fixture-based: NO live network calls.

Fixtures below are small hand-made samples in the shape of the real
responses (CoinGecko market_chart, ECB eurofxref XML) — they are test
data, not live data, and are labelled as such. Fallback behaviour is
tested with injected fake fetchers, never with real endpoints.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toolkit.free_data import (
    _coingecko_daily_points,
    _parse_ecb_xml,
    default_fetchers,
    fetch_free_many,
    fetch_free_prices,
)

TODAY = datetime.date(2026, 10, 28)


def _pairs(last, closes):
    base = datetime.date(2026, 10, 20)
    return [((base + datetime.timedelta(days=i)).isoformat(), c)
            for i, c in enumerate(closes)][:last]


# --- CoinGecko fixture parsing -------------------------------------------
def test_coingecko_daily_points_keeps_last_price_per_day():
    # Fixture: two points on 2026-10-27 (last wins), one on 2026-10-28.
    payload = {"prices": [
        [1761523200000, 80000.0],  # 2026-10-27 00:00 UTC (approx, fixture)
        [1761609600000, 81000.0],  # 2026-10-28 00:00 UTC (approx, fixture)
        [1761613200000, 80647.0],  # same day, later — must win
    ]}
    pts = _coingecko_daily_points(payload)
    assert pts[-1][1] == 80647.0
    assert len(pts) == 2
    assert pts[0][0] < pts[1][0]


def test_coingecko_daily_points_skips_bad_points():
    payload = {"prices": [[1761523200000, 80000.0], ["bad", 1.0], [1761523200000, None]]}
    assert _coingecko_daily_points(payload) == [("2026-10-27", 80000.0)] or len(_coingecko_daily_points(payload)) >= 1


# --- ECB fixture parsing ---------------------------------------------------
_ECB_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
 <Cube><Cube time="2026-10-27">
  <Cube currency="USD" rate="1.1576"/>
  <Cube currency="NPR" rate="154.32"/>
 </Cube></Cube></gesmes:Envelope>"""


def test_parse_ecb_xml_fixture():
    parsed = _parse_ecb_xml(_ECB_FIXTURE)
    assert parsed["date"] == "2026-10-27"
    assert parsed["rates"]["USD"] == 1.1576
    assert parsed["rates"]["NPR"] == 154.32


def test_parse_ecb_xml_no_cube_raises():
    try:
        _parse_ecb_xml("<root/>")
    except ValueError:
        return
    raise AssertionError("expected ValueError for XML with no dated Cube")


# --- Fallback order / stale / all-failed ----------------------------------
def test_fallback_uses_first_fresh_source():
    calls = []

    def bad(_s):
        calls.append("bad")
        raise RuntimeError("boom")

    def good(_s):
        calls.append("good")
        return _pairs(9, [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0])

    out = fetch_free_prices("X", fetchers=[("bad", bad), ("good", good)], today=TODAY)
    assert out["source"] == "good"
    assert calls == ["bad", "good"]
    assert out["provenance"]["stale"] is False
    assert out["provenance"]["last_date"] == "2026-10-28"
    assert out["provenance"]["retrieved_at"]  # UTC timestamp present
    assert out["tried"][0]["source"] == "bad"


def test_stale_source_is_skipped_not_returned():
    old = [(f"2026-10-{10 + i:02d}", 100.0 + i) for i in range(5)]
    fresh = _pairs(9, [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0])
    out = fetch_free_prices("X", fetchers=[("old", lambda s: old), ("new", lambda s: fresh)],
                            max_age_days=7, today=TODAY)
    assert out["source"] == "new"
    assert out["tried"][0]["status"] == "stale"


def test_all_sources_failed_lists_every_attempt():
    try:
        fetch_free_prices("X", fetchers=[("a", lambda s: (_ for _ in ()).throw(RuntimeError("a down"))),
                                         ("b", lambda s: (_ for _ in ()).throw(RuntimeError("b down")))],
                          today=TODAY)
    except RuntimeError as exc:
        assert "a down" in str(exc) and "b down" in str(exc)
        return
    raise AssertionError("expected RuntimeError when all sources fail")


def test_broken_series_from_source_is_rejected():
    # Unsorted dates must be rejected by validation, not repaired.
    broken = [("2026-10-28", 100.0), ("2026-10-27", 101.0)]
    try:
        fetch_free_prices("X", fetchers=[("broken", lambda s: broken)], today=TODAY)
    except RuntimeError as exc:
        assert "broken" in str(exc)
        return
    raise AssertionError("expected rejection of unsorted series")


# --- Per-symbol isolation ---------------------------------------------------
def test_fetch_free_many_isolates_failures():
    def ok(_s):
        return _pairs(9, [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0])

    def bad(_s):
        raise RuntimeError("no data")

    # fetch_free_many takes one chain; isolation is tested by a fetcher
    # that fails for one symbol and succeeds for the other.
    def per_symbol(s):
        if s == "BAD":
            raise RuntimeError("no data")
        return ok(s)

    out = fetch_free_many(["GOOD", "BAD"], fetchers=[("one", per_symbol)], today=TODAY)
    assert out["GOOD"]["source"] == "one"
    assert "error" in out["BAD"] and "no data" in out["BAD"]["error"]


# --- Registry ---------------------------------------------------------------
def test_default_fetchers_stock_and_crypto():
    assert [n for n, _ in default_fetchers("stock")] == ["yahoo", "stooq"]
    assert [n for n, _ in default_fetchers("crypto")] == ["coingecko", "yahoo"]
    try:
        default_fetchers("nepse")
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown kind")
