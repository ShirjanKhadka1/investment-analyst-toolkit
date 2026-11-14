"""Tests for toolkit/connectors.py — Phase 2 data layer.

Offline throughout: every network call is replaced by a stub, a
fixture, or an injected fake. The only live-data assertions live in
tests/test_data_health.py, which parses the real-response fixtures
saved under tests/fixtures/ (source URL + fetch date in SOURCES.md).
"""
import datetime
from pathlib import Path

import pytest

from toolkit.connectors import (
    CACHE_TTL_SECONDS,
    FRESHNESS_SLA_SECONDS,
    Connector,
    ConnectorUnavailable,
    CsvConnector,
    DiskCache,
    default_price_chain,
    evaluate_freshness,
    fetch_fx_rates,
    fetch_macro,
    fetch_prices,
    fetch_with_retry,
    list_connectors,
)
from toolkit.data_layer import StaleDataError

TODAY = datetime.date(2026, 11, 7)
FIXTURES = Path(__file__).resolve().parent / "fixtures"

FRESH_PAIRS = [("2026-11-05", 100.0), ("2026-11-06", 101.0)]
STALE_PAIRS = [("2026-10-01", 100.0), ("2026-10-02", 101.0)]


def _no_sleep(_seconds):
    return None


# ---------------------------------------------------------------------------
# DiskCache
# ---------------------------------------------------------------------------
def test_cache_put_get_roundtrip_and_hit_counter(tmp_path):
    cache = DiskCache(tmp_path)
    payload = {"pairs": FRESH_PAIRS}
    cache.put("yahoo:AAPL", payload, ttl_seconds=3600, last_date="2026-11-06")
    entry = cache.get("yahoo:AAPL")
    assert entry is not None
    # JSON stores tuples as lists; the cache returns the stored form,
    # and the connector normalises it back to tuples on read.
    assert entry["payload"] == {"pairs": [list(p) for p in FRESH_PAIRS]}
    assert entry["last_date"] == "2026-11-06"
    assert entry["hits"] == 1
    assert cache.get("yahoo:AAPL")["hits"] == 2


def test_cache_expired_entry_is_a_miss(tmp_path):
    cache = DiskCache(tmp_path)
    cache.put("yahoo:AAPL", {"pairs": FRESH_PAIRS}, ttl_seconds=0)
    assert cache.get("yahoo:AAPL") is None


def test_cache_missing_key_is_a_miss(tmp_path):
    assert DiskCache(tmp_path).get("yahoo:MSFT") is None


def test_cache_corrupt_file_is_a_miss_not_a_crash(tmp_path):
    cache = DiskCache(tmp_path)
    path = next(iter([tmp_path / "x.json"]))
    path.write_text("{not json", encoding="utf-8")
    assert cache.get("anything") is None
    assert cache.stats() == {}


def test_cache_stats_reports_hits_and_last_date(tmp_path):
    cache = DiskCache(tmp_path)
    cache.put("ecb:latest", {"date": "2026-11-06"}, ttl_seconds=3600,
              last_date="2026-11-06")
    cache.get("ecb:latest")
    stats = cache.stats()
    assert stats["ecb:latest"]["hits"] == 1
    assert stats["ecb:latest"]["last_date"] == "2026-11-06"


def test_cache_put_never_raises_on_unwritable_dir(tmp_path):
    # put() creates parents; force failure by pointing at a file.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    cache2 = DiskCache(blocker / "child")
    cache2.put("k", {"v": 1}, ttl_seconds=60)  # must not raise


# ---------------------------------------------------------------------------
# Retries — transient only, never on 403/429
# ---------------------------------------------------------------------------
def test_retry_succeeds_first_try_without_sleep():
    calls = []
    result, attempts = fetch_with_retry(
        lambda: calls.append(1) or "ok", sleep=_no_sleep)
    assert (result, attempts) == ("ok", 1)
    assert len(calls) == 1


def test_retry_retries_transient_twice_then_succeeds():
    sleeps = []
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("API call failed (503) for https://example.test")
        return "ok"

    result, attempts = fetch_with_retry(
        flaky, sleep=sleeps.append)
    assert (result, attempts) == ("ok", 3)
    assert sleeps == [1.0, 2.0]


def test_retry_gives_up_after_three_transient_failures():
    sleeps = []
    with pytest.raises(RuntimeError, match="503"):
        fetch_with_retry(
            lambda: (_ for _ in ()).throw(
                RuntimeError("API call failed (503) for https://example.test")),
            sleep=sleeps.append)
    assert sleeps == [1.0, 2.0]


def test_retry_never_retries_403():
    sleeps = []
    calls = {"n": 0}

    def blocked():
        calls["n"] += 1
        raise RuntimeError("API call failed (403) for https://example.test")

    with pytest.raises(RuntimeError, match="403"):
        fetch_with_retry(blocked, sleep=sleeps.append)
    assert calls["n"] == 1
    assert sleeps == []


def test_retry_never_retries_429():
    sleeps = []
    calls = {"n": 0}

    def limited():
        calls["n"] += 1
        raise RuntimeError("API call failed (429) for https://example.test")

    with pytest.raises(RuntimeError, match="429"):
        fetch_with_retry(limited, sleep=sleeps.append)
    assert calls["n"] == 1
    assert sleeps == []


def test_retry_retries_timeout_errors():
    state = {"n": 0}

    def flaky_timeout():
        state["n"] += 1
        if state["n"] == 1:
            raise TimeoutError("timed out")
        return "ok"

    result, attempts = fetch_with_retry(flaky_timeout, sleep=_no_sleep)
    assert (result, attempts) == ("ok", 2)


def test_retry_rejects_zero_attempts():
    with pytest.raises(ValueError, match="attempts"):
        fetch_with_retry(lambda: "ok", attempts=0, sleep=_no_sleep)


# ---------------------------------------------------------------------------
# Freshness SLA evaluation
# ---------------------------------------------------------------------------
def test_sla_table_values():
    assert FRESHNESS_SLA_SECONDS["stock_daily"] == 86400
    assert FRESHNESS_SLA_SECONDS["crypto_daily"] == 86400
    assert FRESHNESS_SLA_SECONDS["fx_reference"] == 86400
    assert FRESHNESS_SLA_SECONDS["macro_annual"] == 365 * 86400
    assert FRESHNESS_SLA_SECONDS["nepse_csv_daily"] == 86400
    assert CACHE_TTL_SECONDS["coingecko"] < FRESHNESS_SLA_SECONDS["crypto_daily"]
    assert CACHE_TTL_SECONDS["worldbank"] < FRESHNESS_SLA_SECONDS["macro_annual"]


def test_evaluate_freshness_fresh_and_stale_boundary():
    # TODAY is Sat 2026-11-07. A Friday series is 1 calendar day old
    # but 0 trading days old — FRESH. Thursday is 1 trading day old —
    # still FRESH; Wednesday is 2 trading days old — STALE.
    fresh = evaluate_freshness("2026-11-06", 86400, today=TODAY)
    assert fresh == {"last_date": "2026-11-06", "age_days": 1,
                     "age_trading_days": 0,
                     "sla_seconds": 86400, "status": "FRESH"}
    one_trading_day = evaluate_freshness("2026-11-05", 86400, today=TODAY)
    assert one_trading_day["status"] == "FRESH"
    assert one_trading_day["age_trading_days"] == 1
    stale = evaluate_freshness("2026-11-04", 86400, today=TODAY)
    assert stale["status"] == "STALE"
    assert stale["age_days"] == 3
    assert stale["age_trading_days"] == 2


def test_evaluate_freshness_weekend_does_not_go_stale():
    # Friday data checked on Sunday and on Monday morning is fresh:
    # no trading day has passed without new data.
    sunday = datetime.date(2026, 11, 8)
    monday = datetime.date(2026, 11, 9)
    assert evaluate_freshness(
        "2026-11-06", 86400, today=sunday,
        kind="stock_daily")["status"] == "FRESH"
    assert evaluate_freshness(
        "2026-11-06", 86400, today=monday,
        kind="stock_daily")["status"] == "FRESH"
    # Thursday data checked Monday: Friday and Monday both passed
    # with no new data — genuinely stale.
    assert evaluate_freshness(
        "2026-11-05", 86400, today=monday,
        kind="stock_daily")["status"] == "STALE"


def test_evaluate_freshness_nepse_week_is_effective_dated():
    # NEPSE traded Sun-Thu until 2026-04-05, Mon-Fri from 2026-04-06
    # (board decision 22 Chaitra 2082). A Friday bar after the change
    # IS a trading day; a Sunday bar after it is NOT.
    sunday = datetime.date(2026, 4, 12)
    monday = datetime.date(2026, 4, 13)
    # Friday data checked Sunday: no trading day passed (Sat+Sun
    # are not trading days now) — FRESH with age 0.
    judged = evaluate_freshness(
        "2026-04-10", 86400, today=sunday, kind="nepse_csv_daily")
    assert judged["status"] == "FRESH"
    assert judged["age_trading_days"] == 0
    # The same Friday data checked Monday: Monday is 1 trading day.
    judged = evaluate_freshness(
        "2026-04-10", 86400, today=monday, kind="nepse_csv_daily")
    assert judged["status"] == "FRESH"
    assert judged["age_trading_days"] == 1
    # Thursday data checked Monday: Friday AND Monday passed — STALE.
    assert evaluate_freshness(
        "2026-04-09", 86400, today=monday,
        kind="nepse_csv_daily")["status"] == "STALE"
    # Across the boundary: Thursday 2 Apr checked Sunday 5 Apr —
    # Sunday WAS a trading day then, so 1 trading day old, FRESH.
    assert evaluate_freshness(
        "2026-04-02", 86400, today=datetime.date(2026, 4, 5),
        kind="nepse_csv_daily")["age_trading_days"] == 1
    # And the old week is gone for current dates: Thursday 5 Nov
    # 2026 checked Sunday 8 Nov is 1 trading day old (Friday only),
    # Wednesday 4 Nov checked Sunday is 2 (Thu + Fri) — STALE.
    assert evaluate_freshness(
        "2026-11-05", 86400, today=datetime.date(2026, 11, 8),
        kind="nepse_csv_daily")["status"] == "FRESH"
    assert evaluate_freshness(
        "2026-11-04", 86400, today=datetime.date(2026, 11, 8),
        kind="nepse_csv_daily")["status"] == "STALE"


def test_evaluate_freshness_crypto_counts_weekends():
    # Crypto trades every day: Friday data checked Sunday is 2 days
    # old with no new data — STALE, not a weekend pass.
    sunday = datetime.date(2026, 11, 8)
    assert evaluate_freshness(
        "2026-11-06", 86400, today=sunday,
        kind="crypto_daily")["status"] == "STALE"
    assert evaluate_freshness(
        "2026-11-06", 86400, today=sunday)["status"] == "FRESH"


def test_evaluate_freshness_year_only_read_as_31_dec():
    # World Bank annual: the 2025 value is the latest published year.
    judged = evaluate_freshness("2025", 365 * 86400, today=TODAY)
    assert judged["status"] == "FRESH"
    assert judged["age_days"] == (TODAY - datetime.date(2025, 12, 31)).days
    old = evaluate_freshness("2024", 365 * 86400, today=TODAY)
    assert old["status"] == "STALE"


def test_evaluate_freshness_rejects_future_and_garbage():
    with pytest.raises(ValueError, match="future"):
        evaluate_freshness("2026-11-08", 86400, today=TODAY)
    with pytest.raises(ValueError, match="last_date"):
        evaluate_freshness("not-a-date", 86400, today=TODAY)


# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------
def test_connector_rejects_unknown_name():
    with pytest.raises(ValueError, match="unknown connector"):
        Connector("bloomberg", lambda s: [])


def test_connector_fetch_validates_and_adds_provenance():
    conn = Connector("yahoo", lambda s: FRESH_PAIRS)
    model = conn.fetch("AAPL", today=TODAY, sleep=_no_sleep)
    assert model["source"] == "yahoo"
    assert model["pairs"] == FRESH_PAIRS
    prov = model["provenance"]
    assert prov["last_date"] == "2026-11-06"
    assert prov["age_days"] == 1
    assert prov["n_points"] == 2
    assert prov["stale"] is False
    assert prov["cache_hit"] is False
    assert prov["attempts"] == 1


def test_connector_fetch_rejects_corrupt_series():
    conn = Connector("yahoo", lambda s: [("2026-11-06", -5.0)])
    with pytest.raises(ConnectorUnavailable, match="invalid series"):
        conn.fetch("AAPL", today=TODAY, sleep=_no_sleep)


def test_connector_fetch_raises_stale_data_error_when_old():
    conn = Connector("yahoo", lambda s: STALE_PAIRS)
    with pytest.raises(StaleDataError, match="days old"):
        conn.fetch("AAPL", today=TODAY, sleep=_no_sleep)


def test_connector_cache_hit_avoids_second_fetch(tmp_path):
    calls = {"n": 0}

    def fetch_pairs(symbol):
        calls["n"] += 1
        return FRESH_PAIRS

    cache = DiskCache(tmp_path)
    conn = Connector("yahoo", fetch_pairs)
    first = conn.fetch("AAPL", cache=cache, today=TODAY, sleep=_no_sleep)
    assert first["provenance"]["cache_hit"] is False
    second = conn.fetch("AAPL", cache=cache, today=TODAY, sleep=_no_sleep)
    assert second["provenance"]["cache_hit"] is True
    assert second["pairs"] == FRESH_PAIRS
    assert calls["n"] == 1


def test_connector_cache_hit_with_stale_data_refetches(tmp_path):
    calls = {"n": 0}

    def fetch_pairs(symbol):
        calls["n"] += 1
        return FRESH_PAIRS

    cache = DiskCache(tmp_path)
    cache.put("yahoo:AAPL", {
        "symbol": "AAPL", "kind": "stock", "source": "yahoo",
        "pairs": STALE_PAIRS,
        "provenance": {"source": "yahoo", "retrieved_at": "2026-10-02T00:00:00+00:00",
                       "last_date": "2026-10-02", "age_days": 36,
                       "n_points": 2, "stale": False, "cache_hit": False},
        "tried": []}, ttl_seconds=3600, last_date="2026-10-02")
    conn = Connector("yahoo", fetch_pairs)
    model = conn.fetch("AAPL", cache=cache, today=TODAY, sleep=_no_sleep)
    assert model["provenance"]["cache_hit"] is False
    assert calls["n"] == 1


def test_csv_connector_reads_synthetic_nepse_fixture():
    path = FIXTURES / "nepse_demo_bars_synthetic.csv"
    conn = CsvConnector(str(path), "synthetic fixture for tests")
    model = conn.fetch(today=TODAY, sleep=_no_sleep)
    assert model["source"] == "csv"
    assert model["pairs"][0] == ("2026-10-05", 1005.0)
    assert model["pairs"][-1] == ("2026-11-06", 1097.0)
    assert model["provenance"]["last_date"] == "2026-11-06"


def test_csv_connector_accepts_scanner_style_loader():
    def scanner_style_loader(path):
        # Same shape as nepali-capital-market-research/scanner/data.py
        # load_csv: (path) -> [(date, close)].
        return [("2026-11-06", 2500.0)]

    conn = CsvConnector("unused.csv", "scanner loader test",
                        loader=scanner_style_loader)
    model = conn.fetch("NEPSE", today=TODAY, sleep=_no_sleep)
    assert model["pairs"] == [("2026-11-06", 2500.0)]


def test_csv_connector_refuses_missing_source():
    with pytest.raises(ConnectorUnavailable, match="source must be named"):
        CsvConnector("x.csv", "   ")


def test_csv_connector_missing_file_is_unavailable(tmp_path):
    conn = CsvConnector(str(tmp_path / "nope.csv"), "test source")
    with pytest.raises(ConnectorUnavailable, match="nope.csv"):
        conn.fetch(today=TODAY, sleep=_no_sleep)


def test_csv_connector_stale_file_raises_stale(tmp_path):
    path = tmp_path / "old.csv"
    path.write_text("date,close\n2026-01-05,100.0\n2026-01-06,101.0\n",
                    encoding="utf-8")
    conn = CsvConnector(str(path), "old test file")
    with pytest.raises(StaleDataError, match="days old"):
        conn.fetch(today=TODAY, sleep=_no_sleep)


# ---------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------
def test_chain_csv_first_then_yahoo(tmp_path):
    csv_path = FIXTURES / "nepse_demo_bars_synthetic.csv"
    calls = {"yahoo": 0}

    def yahoo_pairs(symbol):
        calls["yahoo"] += 1
        return FRESH_PAIRS

    chain = [CsvConnector(str(csv_path), "synthetic fixture"),
             Connector("yahoo", yahoo_pairs)]
    model = fetch_prices("DEMO", kind="stock", chain=chain, today=TODAY,
                         sleep=_no_sleep)
    assert model["source"] == "csv"
    assert calls["yahoo"] == 0  # CSV won; Yahoo never called


def test_chain_falls_back_when_csv_missing(tmp_path):
    calls = {"yahoo": 0}

    def yahoo_pairs(symbol):
        calls["yahoo"] += 1
        return FRESH_PAIRS

    chain = [CsvConnector(str(tmp_path / "nope.csv"), "missing file"),
             Connector("yahoo", yahoo_pairs)]
    model = fetch_prices("AAPL", kind="stock", chain=chain, today=TODAY,
                         sleep=_no_sleep)
    assert model["source"] == "yahoo"
    assert calls["yahoo"] == 1
    assert model["tried"][0]["source"] == "csv"
    assert model["tried"][0]["status"] == "failed"


def test_chain_records_stale_then_falls_back():
    chain = [Connector("yahoo", lambda s: STALE_PAIRS),
             Connector("coingecko", lambda s: FRESH_PAIRS)]
    model = fetch_prices("AAPL", kind="stock", chain=chain, today=TODAY,
                         sleep=_no_sleep)
    assert model["source"] == "coingecko"
    assert model["tried"][0] == {
        "source": "yahoo", "status": "stale",
        "error": model["tried"][0]["error"]}
    assert "days old" in model["tried"][0]["error"]


def test_chain_all_fail_lists_every_attempt():
    chain = [Connector("yahoo", lambda s: (_ for _ in ()).throw(
                RuntimeError("API call failed (403) for https://example.test"))),
             Connector("coingecko", lambda s: (_ for _ in ()).throw(
                RuntimeError("No coingecko data for aapl.us")))]
    with pytest.raises(RuntimeError) as excinfo:
        fetch_prices("AAPL", kind="stock", chain=chain, today=TODAY,
                     sleep=_no_sleep)
    text = str(excinfo.value)
    assert "yahoo" in text and "coingecko" in text
    assert "403" in text and "No coingecko data" in text
    assert "No fresh data was returned" in text


def test_default_price_chain_stock_and_crypto():
    stock = default_price_chain("stock")
    assert [c.name for c in stock] == ["yahoo"]
    crypto = default_price_chain("crypto")
    assert [c.name for c in crypto] == ["coingecko", "yahoo"]
    with_csv = default_price_chain(
        "stock", csv_path="x.csv", csv_source="test")
    assert [c.name for c in with_csv] == ["csv", "yahoo"]
    with pytest.raises(ValueError, match="unknown price kind"):
        default_price_chain("bond")


# ---------------------------------------------------------------------------
# FX and macro connectors (offline, injected fetchers)
# ---------------------------------------------------------------------------
def test_fetch_fx_rates_with_fake_and_cache(tmp_path):
    calls = {"n": 0}

    def fake_ecb():
        calls["n"] += 1
        return {"date": "2026-11-06",
                "rates": {"USD": 1.1165, "GBP": 0.83415},
                "source": "ECB euro reference rates",
                "retrieved_at": "2026-11-07T00:00:00+00:00"}

    cache = DiskCache(tmp_path)
    first = fetch_fx_rates(cache=cache, sleep=_no_sleep, fetch_fn=fake_ecb)
    assert first["date"] == "2026-11-06"
    assert first["rates"]["USD"] == 1.1165
    assert first["provenance"]["cache_hit"] is False
    second = fetch_fx_rates(cache=cache, sleep=_no_sleep, fetch_fn=fake_ecb)
    assert second["provenance"]["cache_hit"] is True
    assert calls["n"] == 1


def test_fetch_fx_rates_failure_is_unavailable():
    with pytest.raises(ConnectorUnavailable, match="ecb: fetch failed"):
        fetch_fx_rates(sleep=_no_sleep,
                       fetch_fn=lambda: (_ for _ in ()).throw(
                           RuntimeError("API call failed (403) for https://x")))


def test_fetch_macro_with_fake_and_cache(tmp_path):
    calls = {"n": 0}

    def fake_macro():
        calls["n"] += 1
        return [("2024", 43298911699.9284), ("2025", 45489810283.1132)]

    cache = DiskCache(tmp_path)
    first = fetch_macro("NPL", "NY.GDP.MKTP.CD", cache=cache,
                        sleep=_no_sleep, fetch_fn=fake_macro)
    assert first["values"][-1] == ("2025", 45489810283.1132)
    assert first["provenance"]["last_date"] == "2025"
    second = fetch_macro("NPL", "NY.GDP.MKTP.CD", cache=cache,
                         sleep=_no_sleep, fetch_fn=fake_macro)
    assert second["provenance"]["cache_hit"] is True
    assert calls["n"] == 1


def test_fetch_macro_empty_values_is_unavailable():
    with pytest.raises(ConnectorUnavailable, match="no values"):
        fetch_macro("NPL", "NOPE", sleep=_no_sleep, fetch_fn=list)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_list_connectors_covers_all_sources_with_terms():
    rows = list_connectors()
    names = [r["name"] for r in rows]
    assert names == ["csv", "yahoo", "coingecko", "ecb", "worldbank"]
    for row in rows:
        assert row["licence_terms"].strip(), f"{row['name']} has no terms note"
        assert row["sla_seconds"] > 0
        assert row["ttl_seconds"] > 0
    csv_row = rows[0]
    assert "CSV-only" in csv_row["licence_terms"]
    assert "TO VERIFY" in rows[1]["licence_terms"]


# ---------------------------------------------------------------------------
# Freshness with a holiday calendar (8 Nov 2026)
# ---------------------------------------------------------------------------

def test_evaluate_freshness_dashain_closure_is_not_stale():
    from toolkit.holidays import load_holidays

    calendar = load_holidays("nepse")
    # Last data Fri 16 Oct, checked Mon 26 Oct: without holidays this
    # is 6 trading days old (Mon 19-Fri 23 + Mon 26) — STALE. With the
    # Dashain calendar the 19-23 days are holidays, so it is FRESH.
    without = evaluate_freshness(
        "2026-10-16", 86400, today=datetime.date(2026, 10, 26),
        kind="nepse_csv_daily")
    assert without["status"] == "STALE"
    assert without["age_trading_days"] == 6
    with_cal = evaluate_freshness(
        "2026-10-16", 86400, today=datetime.date(2026, 10, 26),
        kind="nepse_csv_daily", holidays=calendar)
    assert with_cal["status"] == "FRESH"
    assert with_cal["age_trading_days"] == 0
    assert with_cal["unknown_days"] == 1  # Mon 26 Oct, outside coverage


def test_evaluate_freshness_unknown_days_degrade_to_unknown_not_stale():
    from toolkit.holidays import HolidayCalendar

    # A calendar covering 2-6 Nov 2026 with no holidays listed.
    calendar = HolidayCalendar(
        market="nepse", entries=(),
        covered_ranges=((datetime.date(2026, 11, 2),
                         datetime.date(2026, 11, 6)),))
    # Last data Fri 30 Oct, checked Wed 4 Nov: Mon-Wed are covered
    # and not holidays, so 3 known trading days — genuinely STALE.
    stale = evaluate_freshness(
        "2026-10-30", 86400, today=datetime.date(2026, 11, 4),
        kind="nepse_csv_daily", holidays=calendar)
    assert stale["status"] == "STALE"
    assert stale["age_trading_days"] == 3
    # The same gap over days the calendar does NOT cover: the
    # verdict depends on unknown days, so UNKNOWN, never STALE.
    unknown = evaluate_freshness(
        "2026-10-30", 86400, today=datetime.date(2026, 11, 4),
        kind="nepse_csv_daily",
        holidays=HolidayCalendar(market="nepse", entries=(),
                                 covered_ranges=()))
    assert unknown["status"] == "UNKNOWN"
    assert unknown["age_trading_days"] == 0
    assert unknown["age_trading_days_max"] == 3
    assert unknown["unknown_days"] == 3
