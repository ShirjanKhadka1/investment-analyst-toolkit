"""Round 7.1 — data foundation: Yahoo volume, Stooq re-test +
fallback chain, opt-in FRED provider.

All offline: every network call is replaced by a fixture or an
injected fake. The only live-network touchpoint is the Stooq runner
probe (scripts/probe_stooq.py, staged at
ops/ready-for-browser/stooq-ci-probe.yml) — it is NOT deployed, and
the live chain stays Yahoo-only until that probe passes.
"""
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from toolkit import connectors, data, free_data, position_plan
from toolkit.connectors import (
    Connector,
    DiskCache,
    default_price_chain,
    fetch_prices,
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

    conn = Connector("yahoo", lambda s: EXPECTED_PAIRS,
                     fetch_bars=fake_bars)
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


def test_connector_volumes_survive_cache_round_trip(tmp_path):
    def fake_bars(symbol):
        return EXPECTED_PAIRS, EXPECTED_VOLUMES

    conn = Connector("yahoo", lambda s: EXPECTED_PAIRS,
                     fetch_bars=fake_bars)
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

    def stooq_bars(symbol):
        return EXPECTED_PAIRS, EXPECTED_VOLUMES

    chain = [
        Connector("yahoo", failing),
        Connector("stooq", lambda s: EXPECTED_PAIRS,
                  fetch_bars=stooq_bars),
    ]
    model = fetch_prices("AAPL", kind="stock", chain=chain, today=TODAY,
                         sleep=_no_sleep)
    assert model["source"] == "stooq"
    assert model["volumes"] == EXPECTED_VOLUMES
    assert model["tried"][0]["source"] == "yahoo"


def test_live_chain_stays_yahoo_only():
    assert [c.name for c in default_price_chain("stock")] == ["yahoo"]
    assert [c.name for c in default_price_chain(
        "stock", include_stooq=True)] == ["yahoo", "stooq"]


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
# (c) FRED provider — env key only, registered in DATA_SOURCES
# ---------------------------------------------------------------------------
def test_fred_no_key_refuses_cleanly(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(ValueError, match="FRED_API_KEY"):
        data.fetch_fred_observations("GDP")


def test_fred_fake_key_offline(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "FAKE-KEY-FOR-TESTS")
    monkeypatch.setattr(data, "_get_json", lambda url: {"observations": [
        {"date": "2024-01-01", "value": "."},
        {"date": "2024-01-02", "value": "123.5"},
        {"date": "2024-01-03", "value": ""},
        {"date": "2024-01-04", "value": "124.0"},
    ]})
    model = data.fetch_fred_observations("GDP")
    assert model["source"] == "FRED"
    assert model["series_id"] == "GDP"
    assert model["values"] == [("2024-01-02", 123.5),
                               ("2024-01-04", 124.0)]
    assert model["provenance"]["n_points"] == 2
    assert model["provenance"]["last_date"] == "2024-01-04"


def test_fred_explicit_key_and_empty_payload(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(data, "_get_json", lambda url: {"observations": [
        {"date": "2024-01-02", "value": "1.0"}]})
    model = data.fetch_fred_observations("GDP", api_key="FAKE-KEY-EXPLICIT")
    assert model["values"] == [("2024-01-02", 1.0)]
    monkeypatch.setattr(data, "_get_json",
                        lambda url: {"observations": []})
    with pytest.raises(RuntimeError, match="No FRED observations"):
        data.fetch_fred_observations("GDP", api_key="FAKE-KEY-EXPLICIT")


def test_fred_registered_in_data_sources_with_terms():
    sources = list_data_sources()
    fred = [e for e in sources if e["name"] == "FRED"]
    assert len(fred) == 1
    assert fred[0]["needs_key"] is True
    assert fred[0]["in_no_key_chain"] is False
    assert fred[0]["licence_terms"].strip()
    assert "FRED_API_KEY" in fred[0]["verification"]
    assert not [e for e in sources if "Alpha Vantage" in e["name"]
                and "FRED" in e["name"]]


def test_stooq_connector_registered_with_terms():
    rows = connectors.list_connectors()
    stooq = [r for r in rows if r["name"] == "stooq"]
    assert len(stooq) == 1
    assert stooq[0]["licence_terms"].strip()
    assert "NOT in the live chain" in stooq[0]["licence_terms"]
