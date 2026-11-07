"""Tests for scripts/data_health.py — Phase 2 freshness SLA report.

Offline throughout, except that the fixture tests parse the real
public responses saved under tests/fixtures/ (source URL + fetch
date in SOURCES.md). No test touches the live network.
"""
import datetime
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import data_health as dh
from toolkit.free_data import _coingecko_daily_points, _parse_ecb_xml

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
NOW = datetime.datetime(2026, 11, 7, 12, 0, 0,
                        tzinfo=datetime.timezone.utc)


def _fresh_probes():
    return {
        "yahoo": lambda: "2026-11-06",
        "coingecko": lambda: "2026-11-07",
        "ecb": lambda: "2026-11-06",
        "worldbank": lambda: "2025",
    }


def _report(**kwargs):
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("repo_root", REPO_ROOT)
    return dh.build_health_report(**kwargs)


def _by_name(report):
    return {s["name"]: s for s in report["sources"]}


# ---------------------------------------------------------------------------
# Report building
# ---------------------------------------------------------------------------
def test_all_fresh_probes_give_fresh_overall_except_unknown_csv(tmp_path):
    # No data/ dir in the tmp root -> nepse_csv UNKNOWN, rest FRESH.
    report = _report(probes=_fresh_probes(), repo_root=tmp_path,
                       cache_dir=tmp_path / "cache")
    assert report["overall"] == "PARTIAL"
    by_name = _by_name(report)
    assert by_name["yahoo"]["status"] == "FRESH"
    assert by_name["yahoo"]["age_days"] == 1
    assert by_name["worldbank"]["status"] == "FRESH"
    assert by_name["nepse_csv"]["status"] == "UNKNOWN"
    assert "CSV-only" in by_name["nepse_csv"]["note"]


def test_stale_probe_makes_overall_degraded(tmp_path):
    probes = _fresh_probes()
    probes["yahoo"] = lambda: "2026-10-01"
    report = _report(probes=probes, repo_root=tmp_path,
                       cache_dir=tmp_path / "cache")
    assert report["overall"] == "DEGRADED"
    assert _by_name(report)["yahoo"]["status"] == "STALE"
    assert _by_name(report)["yahoo"]["age_days"] == 37


def test_failed_probe_marks_unavailable_and_keeps_previous(tmp_path):
    previous = {"sources": [
        {"name": "yahoo", "last_fetch": "2026-11-06T00:00:00+00:00",
         "last_data_date": "2026-11-06"}]}
    probes = _fresh_probes()
    probes["yahoo"] = lambda: (_ for _ in ()).throw(
        RuntimeError("API call failed (403) for https://example.test"))
    report = _report(probes=probes, previous=previous, repo_root=tmp_path,
                       cache_dir=tmp_path / "cache")
    by_name = _by_name(report)
    assert by_name["yahoo"]["status"] == "UNAVAILABLE"
    assert "403" in by_name["yahoo"]["error"]
    assert by_name["yahoo"]["last_fetch"] == "2026-11-06T00:00:00+00:00"
    assert by_name["yahoo"]["last_data_date"] == "2026-11-06"
    assert report["overall"] == "DEGRADED"


def test_cache_only_mode_reads_cache_and_never_probes(tmp_path):
    from toolkit.connectors import DiskCache
    cache = DiskCache(tmp_path / "cache")
    cache.put("yahoo:AAPL", {
        "symbol": "AAPL", "kind": "stock", "source": "yahoo",
        "pairs": [["2026-11-05", 344.17], ["2026-11-06", 342.37]],
        "provenance": {"source": "yahoo",
                       "retrieved_at": "2026-11-07T00:00:00+00:00",
                       "last_date": "2026-11-06", "age_days": 1,
                       "n_points": 2, "stale": False, "cache_hit": False},
        "tried": []}, ttl_seconds=3600, last_date="2026-11-06",
        now=datetime.datetime(2026, 11, 7, tzinfo=datetime.timezone.utc))
    report = _report(cache_dir=tmp_path / "cache", repo_root=tmp_path)
    by_name = _by_name(report)
    assert by_name["yahoo"]["status"] == "FRESH"
    assert by_name["yahoo"]["last_fetch"] == "2026-11-07T00:00:00+00:00"
    assert by_name["yahoo"]["cache_hits"] == 0
    assert by_name["coingecko"]["status"] == "UNKNOWN"
    assert "never fetched" in by_name["coingecko"]["note"]


def test_nothing_known_gives_unknown_overall(tmp_path):
    report = _report(repo_root=tmp_path, cache_dir=tmp_path / "cache")
    assert report["overall"] == "UNKNOWN"
    assert all(s["status"] == "UNKNOWN" for s in report["sources"])


def test_nepse_csv_reads_newest_user_csv(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "old.csv").write_text(
        "date,close\n2026-10-01,100.0\n", encoding="utf-8")
    (data_dir / "new.csv").write_text(
        "date,close\n2026-11-06,200.0\n2026-11-05,199.0\n", encoding="utf-8")
    report = _report(repo_root=tmp_path, cache_dir=tmp_path / "cache")
    by_name = _by_name(report)
    assert by_name["nepse_csv"]["status"] == "FRESH"
    assert by_name["nepse_csv"]["last_data_date"] == "2026-11-06"
    assert "new.csv" in by_name["nepse_csv"]["note"]


def test_nepse_csv_stale_file_is_flagged(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "old.csv").write_text(
        "date,close\n2026-01-06,100.0\n", encoding="utf-8")
    report = _report(repo_root=tmp_path, cache_dir=tmp_path / "cache")
    by_name = _by_name(report)
    assert by_name["nepse_csv"]["status"] == "STALE"
    assert report["overall"] == "DEGRADED"


def test_unreadable_csv_is_skipped_not_fatal(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "bad.csv").write_text(
        "nonsense\nwithout,dates\n", encoding="utf-8")
    report = _report(repo_root=tmp_path, cache_dir=tmp_path / "cache")
    by_name = _by_name(report)
    assert by_name["nepse_csv"]["status"] == "UNKNOWN"
    assert "skipped unreadable" in by_name["nepse_csv"]["note"]


def test_sla_labels():
    assert dh._sla_label(86400) == "1 day"
    assert dh._sla_label(365 * 86400) == "365 days"


# ---------------------------------------------------------------------------
# Script entry point (subprocess, as the scheduler would run it)
# ---------------------------------------------------------------------------
def _run_health(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "scripts.data_health", *args],
        cwd=cwd, capture_output=True, text=True, timeout=120, check=False)


def test_main_cache_only_writes_report_and_exits_zero(tmp_path):
    proc = _run_health("--repo-root", str(tmp_path),
                       "--output", str(tmp_path / "data_health.json"),
                       "--cache-dir", str(tmp_path / "cache"), cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "data health: UNKNOWN" in proc.stdout
    report = json.loads((tmp_path / "data_health.json").read_text())
    assert report["generated_by"] == "scripts/data_health.py"
    assert len(report["sources"]) == 5


def test_main_creates_output_parent_dirs(tmp_path):
    out = tmp_path / "nested" / "deep" / "h.json"
    proc = _run_health("--repo-root", str(tmp_path),
                       "--output", str(out),
                       "--cache-dir", str(tmp_path / "cache"), cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stderr
    assert out.exists()


def test_main_output_under_a_file_exits_two(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    proc = _run_health("--repo-root", str(tmp_path),
                       "--output", str(blocker / "h.json"),
                       "--cache-dir", str(tmp_path / "cache"), cwd=REPO_ROOT)
    assert proc.returncode == 2
    assert "error: cannot write" in proc.stderr


def test_main_probe_degraded_exits_two_but_still_writes(monkeypatch, tmp_path):
    def failing_probes(cache_dir=None):
        def boom():
            raise RuntimeError("API call failed (503) for https://example.test")
        return {"yahoo": boom, "coingecko": boom, "ecb": boom,
                "worldbank": boom}

    monkeypatch.setattr(dh, "_real_probes", failing_probes)
    out = tmp_path / "h.json"
    code = dh.main(["--probe", "--repo-root", str(tmp_path),
                    "--output", str(out),
                    "--cache-dir", str(tmp_path / "cache")])
    assert code == 2
    report = json.loads(out.read_text())
    assert report["overall"] == "DEGRADED"
    assert _by_name(report)["yahoo"]["status"] == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# Real-response fixtures (cut from live responses 2026-11-07)
# ---------------------------------------------------------------------------
def test_yahoo_fixture_is_real_aapl_response():
    payload = json.loads(
        (FIXTURES / "yahoo_aapl_chart_2026-11-07.json").read_text())
    result = payload["chart"]["result"][0]
    assert result["meta"]["currency"] == "USD"
    closes = result["indicators"]["quote"][0]["close"]
    assert closes[-1] == pytest.approx(342.3699951171875)
    assert closes[-2] == pytest.approx(344.1700134277344)
    assert len(closes) == 5
    stamps = result["timestamp"]
    days = [datetime.datetime.fromtimestamp(
        t, datetime.timezone.utc).date().isoformat() for t in stamps]
    assert days == ["2026-11-02", "2026-11-03", "2026-11-04",
                    "2026-11-05", "2026-11-06"]


def test_ecb_fixture_parses_to_real_2026_11_06_fixing():
    text = (FIXTURES / "ecb_eurofxref_2026-11-06.xml").read_text()
    parsed = _parse_ecb_xml(text)
    assert parsed["date"] == "2026-11-06"
    assert len(parsed["rates"]) == 29
    assert parsed["rates"]["USD"] == pytest.approx(1.1165)
    assert parsed["rates"]["GBP"] == pytest.approx(0.83415)
    assert parsed["rates"]["INR"] == pytest.approx(104.8645)


def test_coingecko_fixture_collapses_to_real_daily_points():
    payload = json.loads(
        (FIXTURES / "coingecko_btc_market_chart_2026-11-07.json").read_text())
    assert len(payload["prices"]) == 288
    points = _coingecko_daily_points(payload)
    assert [d for d, _p in points] == ["2026-11-06", "2026-11-07"]
    assert all(price > 70000 for _d, price in points)


def test_fixture_provenance_file_covers_every_fixture():
    doc = (FIXTURES / "SOURCES.md").read_text()
    for name in ("yahoo_aapl_chart_2026-11-07.json",
                 "ecb_eurofxref_2026-11-06.xml",
                 "coingecko_btc_market_chart_2026-11-07.json",
                 "nepse_demo_bars_synthetic.csv"):
        assert name in doc
    assert "2026-11-07" in doc
    assert "SYNTHETIC" in doc
