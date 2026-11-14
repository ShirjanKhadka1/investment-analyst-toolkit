"""Round 7.1 — data foundation: Yahoo volume, Stooq re-test +
fallback chain.

All offline: every network call is replaced by a fixture or an
injected fake. The only live-network touchpoint is the Stooq runner
probe (scripts/probe_stooq.py, staged at
ops/ready-for-browser/stooq-ci-probe.yml) — it is NOT deployed, and
the live chain stays Yahoo-only until that probe passes.

Additive-only (binding constraint update): toolkit/connectors.py and
toolkit/data.py change by ADDITION only — no existing function is
modified. Volumes arrive via NEW functions (data.parse_yahoo_chart /
fetch_yahoo_bars / median_volume) and a NEW BarsConnector subclass;
Connector.fetch, fetch_yahoo_prices, default_price_chain and
fetch_prices are byte-identical to main. The FRED provider built in
the first 7.1 pass was DROPPED by the same update: no FRED provider
code remains in this PR (see the FRED-drop regression tests below),
and FRED is listed as a PLANNED item in the capability-matrix build
plan instead.
"""
import datetime
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from toolkit import connectors, data, free_data, position_plan
from toolkit.connectors import (
    BarsConnector,
    Connector,
    DiskCache,
    default_price_chain,
    default_price_chain_with_bars,
    fetch_prices,
    fetch_prices_with_volumes,
    list_connectors_with_opt_ins,
)
from toolkit.data_layer import list_data_sources

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
TODAY = datetime.date(2026, 11, 7)

YAHOO_FIXTURE = json.loads(
    (FIXTURES / "yahoo_aapl_chart_2026-11-07.json").read_text(
        encoding="utf-8"))
STOOQ_FIXTURE_TEXT = (FIXTURES / "stooq_aapl_daily_2026-11-07.csv").read_text(
    encoding="utf-8")

EXPECTED_PAIRS = [
    ("2026-11-02", 338.32000732421875),
    ("2026-11-03", 331.8999938964844),
    ("2026-11-04", 349.510009765625),
    ("2026-11-05", 344.1700134277344),
    ("2026-11-06", 342.3699951171875),
]
EXPECTED_VOLUMES = {
    "2026-11-02": 64757700.0,
    "2026-11-03": 63160000.0,
    "2026-11-04": 89637700.0,
    "2026-11-05": 75678900.0,
    "2026-11-06": 44676300.0,
}
# Median of the five fixture volumes: sorted, the middle value.
EXPECTED_MEDIAN_VOLUME = 64757700.0


def _no_sleep(_seconds):
    return None


def _pass_plan(**overrides):
    plan = {
        "entry": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "risk_per_share": 5.0,
        "reward_risk": 2.0,
        "shares": 20,
        "max_loss": 100.0,
        "cash_available": 10000.0,
        "asset_type": "equity",
        "symbol": "DEMO",
        "short": False,
        "daily_loss_usd": 0.0,
        "trades_today": 0,
        "orders_today": 0,
        "entry_window_ok": True,
        "is_duplicate": False,
        "date": "2026-11-07",
    }
    plan.update(overrides)
    return plan


# ---------------------------------------------------------------------------
# (a) Yahoo volume — additive only, pairs shape untouched
# ---------------------------------------------------------------------------
def test_parse_yahoo_chart_fixture_returns_pairs_and_volumes():
    pairs, volumes = data.parse_yahoo_chart(YAHOO_FIXTURE)
    assert pairs == EXPECTED_PAIRS
    assert volumes == EXPECTED_VOLUMES
    assert len(volumes) == 5  # volume on 5/5 bars


def test_fetch_yahoo_prices_still_pairs_only(monkeypatch):
    monkeypatch.setattr(data, "_get_json", lambda url: YAHOO_FIXTURE)
    pairs = data.fetch_yahoo_prices("AAPL")
    assert pairs == EXPECTED_PAIRS
    assert isinstance(pairs[0], tuple)


def test_fetch_yahoo_bars_returns_same_pairs_plus_volumes(monkeypatch):
    monkeypatch.setattr(data, "_get_json", lambda url: YAHOO_FIXTURE)
    pairs, volumes = data.fetch_yahoo_bars("AAPL")
    assert pairs == EXPECTED_PAIRS
    assert volumes == EXPECTED_VOLUMES


def test_parse_yahoo_chart_skips_none_close_and_none_volume():
    payload = {"chart": {"result": [{
        "timestamp": [1793318400, 1793404800, 1793491200],
        "indicators": {"quote": [{"close": [100.0, None, 102.0],
                                  "volume": [1000, 2000, None]}]},
    }]}}
    pairs, volumes = data.parse_yahoo_chart(payload)
    assert pairs == [("2026-10-30", 100.0), ("2026-11-01", 102.0)]
    # Bar 2 skipped (no close); bar 3 kept in pairs, absent from volumes.
    assert volumes == {"2026-10-30": 1000.0}


def test_median_volume_and_empty():
    assert data.median_volume(EXPECTED_VOLUMES) == EXPECTED_MEDIAN_VOLUME
    assert data.median_volume({}) is None
    assert data.median_volume(None) is None
    assert data.median_volume({"2026-11-02": 10.0,
                               "2026-11-03": 20.0}) == 15.0


def test_yahoo_connector_model_carries_volumes_additively():
    def fake_bars(symbol):
        return EXPECTED_PAIRS, EXPECTED_VOLUMES

    conn = BarsConnector("yahoo", lambda s: EXPECTED_PAIRS, fake_bars)
    model = conn.fetch("AAPL", today=TODAY, sleep=_no_sleep)
    assert model["pairs"] == EXPECTED_PAIRS
    assert model["volumes"] == EXPECTED_VOLUMES
    assert model["provenance"]["n_volumes"] == 5
    assert model["source"] == "yahoo"


def test_connector_without_bars_has_no_volumes_key():
    conn = Connector("yahoo", lambda s: EXPECTED_PAIRS)
    model = conn.fetch("AAPL", today=TODAY, sleep=_no_sleep)
    assert "volumes" not in model
    assert "n_volumes" not in model["provenance"]


def test_bars_connector_without_bars_fetch_is_volume_less():
    # A BarsConnector whose bars fetch fails still returns the pairs
    # model with no volumes key — a volume-less series is not an error.
    def failing_bars(symbol):
        raise RuntimeError("simulated bars outage")

    conn = BarsConnector("yahoo", lambda s: EXPECTED_PAIRS, failing_bars)
    model = conn.fetch("AAPL", today=TODAY, sleep=_no_sleep)
    assert model["pairs"] == EXPECTED_PAIRS
    assert "volumes" not in model


def test_connector_volumes_survive_cache_round_trip(tmp_path):
    def fake_bars(symbol):
        return EXPECTED_PAIRS, EXPECTED_VOLUMES

    conn = BarsConnector("yahoo", lambda s: EXPECTED_PAIRS, fake_bars)
    cache = DiskCache(tmp_path)
    first = conn.fetch("AAPL", cache=cache, today=TODAY, sleep=_no_sleep)
    assert first["provenance"]["cache_hit"] is False
    second = conn.fetch("AAPL", cache=cache, today=TODAY, sleep=_no_sleep)
    assert second["provenance"]["cache_hit"] is True
    assert second["volumes"] == EXPECTED_VOLUMES
    assert second["pairs"] == EXPECTED_PAIRS


def test_median_volume_from_fetched_series_passes_exit_liquidity():
    context = {
        "portfolio_value": 100000.0,
        "sector_exposure_usd": 0.0,
        "sector": "technology",
        "typical_daily_volume": data.median_volume(EXPECTED_VOLUMES),
    }
    res = position_plan.extended_risk_gate(_pass_plan(), context=context)
    assert res["checks"]["exit_liquidity"]["pass"] is True
    assert res["verdict"] == "PASS"


def test_volume_less_series_still_fail_closes_exit_liquidity():
    res = position_plan.extended_risk_gate(_pass_plan(), context={
        "portfolio_value": 100000.0,
        "sector_exposure_usd": 0.0,
        "sector": "technology",
    })
    assert res["checks"]["exit_liquidity"]["pass"] is False
    assert "missing" in res["checks"]["exit_liquidity"]["reason"]


# ---------------------------------------------------------------------------
# (b) Stooq — parser, fetcher, fallback chain (NOT live)
# ---------------------------------------------------------------------------
def test_stooq_symbol_mapping():
    assert free_data.stooq_symbol("SPY") == "spy.us"
    assert free_data.stooq_symbol("AAPL.US") == "aapl.us"
    assert free_data.stooq_symbol("7203.T") == "7203.t"
    with pytest.raises(ValueError, match="must not be empty"):
        free_data.stooq_symbol("  ")


def test_parse_stooq_csv_fixture_returns_pairs_and_volumes():
    pairs, volumes = free_data.parse_stooq_csv(STOOQ_FIXTURE_TEXT)
    assert pairs == EXPECTED_PAIRS
    assert volumes == EXPECTED_VOLUMES


def test_parse_stooq_csv_refuses_html_and_empty():
    with pytest.raises(ValueError, match="no CSV"):
        free_data.parse_stooq_csv(
            "<!DOCTYPE html><html><body>challenge</body></html>")
    with pytest.raises(ValueError, match="no CSV"):
        free_data.parse_stooq_csv("")
    with pytest.raises(ValueError, match="Date and Close"):
        free_data.parse_stooq_csv("foo,bar\n1,2\n")
    with pytest.raises(ValueError, match="no usable price rows"):
        free_data.parse_stooq_csv("Date,Open,High,Low,Close,Volume\n")


def test_fetch_stooq_prices_with_fake_text(monkeypatch):
    monkeypatch.setattr(free_data, "_get_text",
                        lambda url: STOOQ_FIXTURE_TEXT)
    assert free_data.fetch_stooq_prices("AAPL") == EXPECTED_PAIRS
    pairs, volumes = free_data.fetch_stooq_bars("SPY")
    assert pairs == EXPECTED_PAIRS
    assert volumes == EXPECTED_VOLUMES


def test_fetch_stooq_prices_html_payload_is_a_failure(monkeypatch):
    monkeypatch.setattr(
        free_data, "_get_text",
        lambda url: "<!DOCTYPE html><html>challenge</html>")
    with pytest.raises(RuntimeError, match="no usable CSV"):
        free_data.fetch_stooq_prices("AAPL")


def test_default_fetchers_stooq_is_opt_in_only():
    assert [name for name, _ in free_data.default_fetchers("stock")] == [
        "yahoo"]
    assert [name for name, _ in
            free_data.default_fetchers("stock", include_stooq=True)] == [
        "yahoo", "stooq"]


def test_fallback_yahoo_down_stooq_serves_the_series():
    def failing(symbol):
        raise RuntimeError("simulated Yahoo outage")

    def stooq_fake(symbol):
        return EXPECTED_PAIRS

    result = free_data.fetch_free_prices(
        "AAPL", fetchers=[("yahoo", failing), ("stooq", stooq_fake)],
        today=TODAY)
    assert result["source"] == "stooq"
    assert result["pairs"] == EXPECTED_PAIRS
    assert result["tried"][0]["source"] == "yahoo"
    assert result["tried"][0]["status"] == "failed"


def test_connector_chain_falls_back_to_stooq_with_volumes():
    def failing(symbol):
        raise RuntimeError("simulated Yahoo outage")

    def failing_bars(symbol):
        raise RuntimeError("simulated Yahoo outage")

    def stooq_bars(symbol):
        return EXPECTED_PAIRS, EXPECTED_VOLUMES

    chain = [
        BarsConnector("yahoo", failing, failing_bars),
        BarsConnector("stooq", lambda s: EXPECTED_PAIRS, stooq_bars),
    ]
    model = fetch_prices("AAPL", kind="stock", chain=chain, today=TODAY,
                         sleep=_no_sleep)
    assert model["source"] == "stooq"
    assert model["volumes"] == EXPECTED_VOLUMES
    assert model["tried"][0]["source"] == "yahoo"


def test_fetch_prices_with_volumes_serves_yahoo_bars(monkeypatch):
    monkeypatch.setattr(data, "_get_json", lambda url: YAHOO_FIXTURE)
    model = fetch_prices_with_volumes("AAPL", kind="stock", today=TODAY,
                                      sleep=_no_sleep)
    assert model["source"] == "yahoo"
    assert model["pairs"] == EXPECTED_PAIRS
    assert model["volumes"] == EXPECTED_VOLUMES


def test_live_chain_stays_yahoo_only():
    assert [c.name for c in default_price_chain("stock")] == ["yahoo"]
    assert [c.name for c in default_price_chain_with_bars("stock")] == [
        "yahoo"]
    assert [c.name for c in default_price_chain_with_bars(
        "stock", include_stooq=True)] == ["yahoo", "stooq"]
    # The live chain takes no opt-in: default_price_chain has no
    # include_stooq parameter at all (additive-only: its signature
    # is byte-identical to main).
    assert "include_stooq" not in inspect.signature(
        default_price_chain).parameters
    assert "include_stooq" in inspect.signature(
        default_price_chain_with_bars).parameters


# ---------------------------------------------------------------------------
# Stooq runner probe — exact commands, second-run + multi-run stability
# ---------------------------------------------------------------------------
def test_probe_run_probe_reports_ok_and_fail():
    from scripts import probe_stooq

    def fake_ok(symbol):
        return EXPECTED_PAIRS

    lines, ok = probe_stooq.run_probe(fake_ok, ["SPY", "AAPL"])
    assert ok == 2
    assert lines[-1] == "PROBE 2/2 OK"
    assert lines[0].startswith("SPY OK n=5 last=2026-11-06 close=342.369")

    def fake_mixed(symbol):
        if symbol == "SPY":
            return EXPECTED_PAIRS
        raise RuntimeError("simulated Stooq outage")

    lines, ok = probe_stooq.run_probe(fake_mixed, ["SPY", "AAPL"])
    assert ok == 1
    assert lines[-1] == "PROBE 1/2 OK"
    assert lines[1].startswith("AAPL FAIL RuntimeError: simulated Stooq outage")


def _run_probe_script():
    env = dict(os.environ)
    env["PROBE_SYMBOLS"] = "SPY,AAPL"
    env["STOOQ_PROBE_FIXTURE"] = str(
        FIXTURES / "stooq_aapl_daily_2026-11-07.csv")
    return subprocess.run(
        [sys.executable, "scripts/probe_stooq.py"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
        check=False)


def test_probe_script_second_run_and_multi_run_stable():
    # The probe's exact commands, run locally against the fixture:
    # second run + multi-run must produce byte-identical output.
    outputs = []
    for _ in range(3):
        proc = _run_probe_script()
        assert proc.returncode == 0
        outputs.append(proc.stdout)
    assert outputs[0] == outputs[1] == outputs[2]
    assert "SPY OK n=5" in outputs[0]
    assert "AAPL OK n=5" in outputs[0]
    assert "PROBE 2/2 OK" in outputs[0]


# ---------------------------------------------------------------------------
# FRED provider — DROPPED by the binding constraint update (rework).
# No FRED provider code remains in this PR; FRED is a PLANNED item in
# the capability-matrix build plan instead. These tests pin the drop:
# the provider function, its parser extraction and its DATA_SOURCES
# registration must all be gone, with the keyed row re-combined.
# (The pre-existing main function toolkit/data.py fetch_fred_series —
# explicit key, unregistered — is untouched; only the 7.1 provider
# is dropped.)
# ---------------------------------------------------------------------------
def test_fred_provider_functions_are_gone():
    assert not hasattr(data, "fetch_fred_observations")
    assert not hasattr(data, "parse_fred_observations")
    assert hasattr(data, "fetch_fred_series")  # main's, untouched


def test_fred_not_registered_separately_in_data_sources():
    sources = list_data_sources()
    assert not [e for e in sources if e["name"] == "FRED"]
    combined = [e for e in sources
                if e["name"] == "FRED / Alpha Vantage / Twelve Data / FMP"]
    assert len(combined) == 1
    assert combined[0]["needs_key"] is True
    assert combined[0]["in_no_key_chain"] is False


def test_fred_is_a_planned_build_plan_item_not_implemented():
    sys.path.insert(0, str(ROOT / "scripts"))
    import generate_capability_matrix as gen

    items = [item for item, _i, _e, _w in gen.BUILD_PLAN]
    assert "Opt-in macro providers (FRED/ECB/IMF)" in items
    doc = gen.render()
    assert "Opt-in macro providers (FRED/ECB/IMF)" in doc
    assert "PLANNED, not implemented" in doc


def test_stooq_connector_registered_with_terms():
    rows = list_connectors_with_opt_ins()
    stooq = [r for r in rows if r["name"] == "stooq"]
    assert len(stooq) == 1
    assert stooq[0]["licence_terms"].strip()
    assert "NOT in the live chain" in stooq[0]["licence_terms"]
    # The live listing is unchanged: Stooq appears only in the
    # opt-in listing, never in list_connectors().
    live = [r for r in connectors.list_connectors()
            if r["name"] == "stooq"]
    assert live == []


# ---------------------------------------------------------------------------
# Golden test — the track-record runner's price inputs, before vs after 7.1
#
# HOW THE GOLDEN VALUES WERE CAPTURED (read before editing them):
# On main (b703bfe), before any 7.1 change, a capture script drove the
# exact price-input code path the track-record runner uses and recorded
# the outputs below as literals:
#   run_day(fetch=<real connectors.fetch_prices with today pinned>)
#     -> connectors.fetch_prices -> default_price_chain -> Connector
#     -> data.fetch_yahoo_prices / free_data.fetch_coingecko_history
#     -> data._get_json (monkeypatched to the fixtures, offline)
#   then track_record_daily._merge_bars -> _pit_store -> _bar_complete,
#   and a full run_day end to end.
# Fixtures: tests/fixtures/yahoo_aapl_chart_2026-11-07.json (all six
# stock symbols fetch it — the network is stubbed, so every Yahoo call
# returns the AAPL chart) and
# tests/fixtures/coingecko_btc_market_chart_2026-11-07.json (BTC-USD).
# Pinned inputs: as_of 2026-11-06, today 2026-11-07 (so the 11-06 bars
# are fresh under the 7-day gate), now 2026-11-06 22:00 UTC (early)
# and 2026-11-07 22:00 UTC (late). To re-capture: check out b703bfe,
# run that path, and compare — the values below must come out
# identical, or the capture itself is suspect.
# This test re-runs that same path on the CURRENT code and asserts
# byte-equality with those literals. The values are literals on
# purpose: nothing here is re-derived from the current code, or the
# test could not catch a change. If 7.1 had altered the runner's
# price inputs — the (date, close) pairs, the merged bars, the PIT
# closes, or bar completeness — this test FAILS. It is offline
# (fixtures only, no network).
# ---------------------------------------------------------------------------
def _load_daily_script():
    """Load scripts/track_record_daily.py by path (not on sys.path)."""
    spec = importlib.util.spec_from_file_location(
        "track_record_daily",
        ROOT / "scripts" / "track_record_daily.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_daily = _load_daily_script()

_GOLDEN_AS_OF = datetime.date(2026, 11, 6)
_GOLDEN_TODAY = datetime.date(2026, 11, 7)
_GOLDEN_NOW_EARLY = datetime.datetime(
    2026, 11, 6, 22, 0, tzinfo=datetime.timezone.utc)
_GOLDEN_NOW_LATE = datetime.datetime(
    2026, 11, 7, 22, 0, tzinfo=datetime.timezone.utc)
# CoinGecko daily points from the BTC fixture, captured on main:
# one (date, last price) pair per UTC day, 2026-11-06 and 2026-11-07.
_GOLDEN_BTC_PAIRS = [
    ("2026-11-06", 75880.98147532696),
    ("2026-11-07", 75919.14991633192),
]
# Bar completeness of the last PIT bar at the early instant (Friday
# 22:00 UTC — the US session has closed, but a crypto/FX bar dated
# today is still in progress) — captured on main.
_GOLDEN_BAR_COMPLETE_EARLY = {
    "SPY": True,
    "AAPL": True,
    "VT": True,
    "NIFTYBEES.NS": True,
    "ISF.L": True,
    "EURUSD=X": False,
    "BTC-USD": False,
}


def test_track_record_runner_price_inputs_golden(monkeypatch, tmp_path):
    btc_fixture = json.loads(
        (FIXTURES / "coingecko_btc_market_chart_2026-11-07.json"
         ).read_text(encoding="utf-8"))
    monkeypatch.setattr(data, "_get_json", lambda url: YAHOO_FIXTURE)
    monkeypatch.setattr(free_data, "_get_json", lambda url: btc_fixture)

    def real_fetch(symbol, kind):
        # The runner's default fetch, with "today" pinned exactly as
        # in the main capture — the only difference from a live run.
        return connectors.fetch_prices(symbol, kind=kind,
                                       today=_GOLDEN_TODAY, sleep=_no_sleep)

    for symbol in _daily.WATCHLIST_PROFILES:
        expected_pairs = (_GOLDEN_BTC_PAIRS if symbol == "BTC-USD"
                          else EXPECTED_PAIRS)
        # 1. The runner's default fetch path returns the golden pairs
        #    — and NO volumes key: 7.1 must not change what the
        #    runner reads (volumes live on BarsConnector only).
        model = real_fetch(symbol, _daily.KINDS[symbol])
        assert model["pairs"] == expected_pairs, symbol
        assert "volumes" not in model, symbol
        if symbol == "BTC-USD":
            assert model["source"] == "coingecko"
        else:
            assert model["source"] == "yahoo"

        # 2. Merge + PIT + bar completeness, per symbol.
        bars, _new = _daily._merge_bars([], model["pairs"])
        assert bars == [
            {"date": d, "open": c, "high": c, "low": c, "close": c}
            for d, c in expected_pairs
        ], symbol
        pit = _daily._pit_store(bars).bars_as_of(_GOLDEN_AS_OF)
        pit_closes = [(b["date"], float(b["close"])) for b in pit]
        if symbol == "BTC-USD":
            # The 2026-11-07 CoinGecko bar is after as_of: the PIT
            # slice sees only the 2026-11-06 bar.
            assert pit_closes == [("2026-11-06", 75880.98147532696)]
        else:
            assert pit_closes == EXPECTED_PAIRS, symbol
        last_date = pit[-1]["date"]
        assert last_date == "2026-11-06", symbol
        profile = _daily.WATCHLIST_PROFILES[symbol]
        assert _daily._bar_complete(symbol, profile, last_date,
                                     _GOLDEN_NOW_EARLY) is \
            _GOLDEN_BAR_COMPLETE_EARLY[symbol], symbol
        assert _daily._bar_complete(symbol, profile, last_date,
                                     _GOLDEN_NOW_LATE) is True, symbol

    # 3. End-to-end run_day with the real fetch: the same summary,
    #    the same bars caches, no calls (5 closes are below every
    #    rule's minimum window, so no direction is produced), no
    #    missed days, an empty calls file.
    calls = tmp_path / "calls.jsonl"
    calls.write_text("", encoding="utf-8")
    summary = _daily.run_day(calls, tmp_path / "out.md",
                             tmp_path / "bars", tmp_path / "missed.jsonl",
                             _GOLDEN_AS_OF, fetch=real_fetch,
                             now=_GOLDEN_NOW_LATE)
    assert summary == {"as_of": "2026-11-06", "calls_logged": 0,
                       "calls_settled": 0, "missed_days": 0,
                       "provisional_logged": 0, "head_hash": None,
                       "n_settled": 0}
    assert calls.read_text(encoding="utf-8") == ""
    for symbol in _daily.WATCHLIST_PROFILES:
        cached = json.loads(
            (tmp_path / "bars" / f"{symbol}.json").read_text(
                encoding="utf-8"))
        assert cached["symbol"] == symbol
        expected_pairs = (_GOLDEN_BTC_PAIRS if symbol == "BTC-USD"
                          else EXPECTED_PAIRS)
        assert [(b["date"], b["close"]) for b in cached["bars"]] == \
            expected_pairs, symbol
