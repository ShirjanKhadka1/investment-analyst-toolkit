"""Phase 3 GATE 3 tests — all offline, fixture-based, no network.

GATE 3 requires, in CI: (1) kill a source and show fallback/rejection,
(2) corrupt a payload and show rejection (no partial/stale publish).
Both are proven here, plus the publish gate and the last-known-good
rule for scheduled runs. A failure in any of these blocks deployment.
"""
import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from toolkit import autonomous as auto_mod
from toolkit.data_layer import (
    CorruptDataError,
    StaleDataError,
    list_data_sources,
    publish_check,
    validate_series_payload,
)
from toolkit.free_data import fetch_free_prices

TODAY = dt.date(2026, 10, 28)


def _series(n=60, last="2026-10-27", start=100.0):
    last_d = dt.date.fromisoformat(last)
    return [((last_d - dt.timedelta(days=n - 1 - i)).isoformat(),
             start + i) for i in range(n)]


def _good(symbol):
    return _series()


def _failing(symbol):
    raise RuntimeError("simulated source outage (GATE 3 kill-a-source)")


def _corrupt(symbol):
    pairs = _series()
    pairs[10] = (pairs[9][0], 105.0)  # duplicate date -> corrupt
    return pairs


# --- Registry: every source carries a licence/terms note -------------------

def test_registry_every_source_has_licence_terms():
    sources = list_data_sources()
    assert len(sources) >= 8
    for entry in sources:
        assert entry["name"] and entry["verification"]
        assert entry["licence_terms"].strip(), entry["name"]


def test_registry_nepse_not_in_no_key_chain():
    nepse = [e for e in list_data_sources() if e["kind"] == "nepse"]
    assert len(nepse) == 1
    assert nepse[0]["in_no_key_chain"] is False


# --- GATE 3 (1): kill a source -> fallback, then all-dead -> rejection -----

def test_killed_source_falls_back_to_next_source():
    result = fetch_free_prices(
        "DEMO", fetchers=[("dead", _failing), ("good", _good)],
        today=TODAY)
    assert result["source"] == "good"
    assert result["provenance"]["stale"] is False
    assert result["tried"][0]["source"] == "dead"
    assert result["tried"][0]["status"] == "failed"


def test_all_sources_dead_raises_and_returns_no_data():
    with pytest.raises(RuntimeError, match="All free data sources failed"):
        fetch_free_prices("DEMO", fetchers=[("dead1", _failing),
                                            ("dead2", _failing)],
                          today=TODAY)


def test_corrupt_source_is_skipped_not_published():
    result = fetch_free_prices(
        "DEMO", fetchers=[("corrupt", _corrupt), ("good", _good)],
        today=TODAY)
    assert result["source"] == "good"
    assert result["tried"][0]["status"] == "failed"


# --- GATE 3 (2): corrupt payload -> rejection, never partial publish ------

def test_validate_series_payload_accepts_clean_series():
    pairs = validate_series_payload(
        {"symbol": "DEMO", "source": "fixture", "pairs": _series()})
    assert len(pairs) == 60


@pytest.mark.parametrize("bad", [
    {"source": "fixture", "pairs": _series()},               # missing symbol
    {"symbol": "DEMO", "pairs": _series()},               # missing source
    {"symbol": "DEMO", "source": "fixture"},              # missing pairs
    {"symbol": "DEMO", "source": "fixture",
     "pairs": [("2026-10-01", 100.0), ("2026-10-01", 101.0)]},  # dup date
    {"symbol": "DEMO", "source": "fixture",
     "pairs": [("01/10/2026", 100.0)]},                    # non-ISO date
    {"symbol": "DEMO", "source": "fixture",
     "pairs": [("2026-10-01", -5.0)]},                     # non-positive close
])
def test_validate_series_payload_rejects_corrupt(bad):
    with pytest.raises(CorruptDataError):
        validate_series_payload(bad)


def test_publish_check_rejects_stale_and_corrupt():
    fresh = publish_check(_series(), today=TODAY)
    assert fresh["stale"] is False and fresh["n_points"] == 60
    with pytest.raises(StaleDataError):
        publish_check(_series(last="2026-10-01"), today=TODAY)
    with pytest.raises(CorruptDataError):
        publish_check([("2026-10-01", 100.0), ("2026-10-01", 101.0)],
                      today=TODAY)


# --- Last-known-good: a failed run must not overwrite latest.md -----------

def test_failed_run_keeps_last_known_good_latest(tmp_path):
    out_dir = str(tmp_path / "reports")
    good_cfg = {"symbols": ["DEMO"], "output_dir": out_dir,
                "today": TODAY, "fetchers": [("good", _good)],
                "now": dt.datetime(2026, 10, 28, 1, 0, tzinfo=dt.timezone.utc)}
    auto_mod.run_once(good_cfg)
    latest_path = os.path.join(out_dir, "DEMO", "latest.md")
    with open(latest_path, encoding="utf-8") as f:
        good_latest = f.read()
    assert "FRESH" in good_latest

    bad_cfg = dict(good_cfg)
    bad_cfg["fetchers"] = [("dead", _failing)]
    bad_cfg["now"] = dt.datetime(2026, 10, 29, 1, 0, tzinfo=dt.timezone.utc)
    summary = auto_mod.run_once(bad_cfg)
    assert summary["symbols"]["DEMO"]["status"] == "STALE_OR_FAILED"
    with open(latest_path, encoding="utf-8") as f:
        assert f.read() == good_latest  # unchanged: last-known-good kept
    stale_path = os.path.join(out_dir, "DEMO", "20261029T010000Z.md")
    with open(stale_path, encoding="utf-8") as f:
        assert "STALE_OR_FAILED" in f.read()
