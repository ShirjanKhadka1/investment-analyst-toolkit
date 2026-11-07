"""Tests for scripts/refresh_derived.py — Phase 2 derived datasets.

Offline throughout: the demo CSV is synthetic and shipped in the
repo; every other CSV here is written by the test itself.
"""
import datetime
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.refresh_derived import refresh_one

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_CSV = REPO_ROOT / "demo.csv"
TODAY = datetime.date(2026, 11, 7)
NOW = datetime.datetime(2026, 11, 7, 12, 0, 0,
                        tzinfo=datetime.timezone.utc)


def test_refresh_demo_csv_is_fresh_and_complete(tmp_path):
    out = tmp_path / "demo.json"
    dataset = refresh_one(DEMO_CSV, out, now=NOW, today=TODAY)
    assert dataset["as_of"] == "2026-11-06"
    assert dataset["n_bars"] == 80
    assert dataset["stale"] is False
    assert dataset["age_days"] == 1
    assert dataset["last_close"] == pytest.approx(1284.91)
    assert dataset["dataset"] == "demo"
    # Repo-relative when run from the repo root, absolute otherwise.
    assert dataset["source_csv"] in (str(DEMO_CSV), "demo.csv")
    assert dataset["generated_at"] == "2026-11-07T12:00:00+00:00"
    for key in ("rsi_14", "macd", "bollinger_upper", "atr_14", "sma_20",
                "adx_14", "stochastic_k", "vwap"):
        assert key in dataset["indicators_last"]
    assert isinstance(dataset["setups_matched"], list)
    from toolkit.terminal import SETUP_ORDER
    assert set(dataset["setups_detail"]) == set(SETUP_ORDER)
    assert all(name in SETUP_ORDER for name in dataset["setups_matched"])
    tm = dataset["timemachine"]
    assert tm["n_sims"] == 1000
    assert tm["seed"] == 19
    assert tm["horizon"] == 20
    assert tm["n_windows"] > 0
    assert tm["target_first"] + tm["stop_first"] + tm["neither"] == pytest.approx(1.0)
    assert dataset["hypothetical_plan"]["stop"] < \
        dataset["hypothetical_plan"]["entry"] < \
        dataset["hypothetical_plan"]["target"]
    written = json.loads(out.read_text())
    assert written == dataset


def _write_stale_csv(tmp_path, name="old.csv", n_bars=45):
    csv_path = tmp_path / name
    rows = ["date,open,high,low,close,volume"]
    price = 100.0
    day = datetime.date(2020, 1, 6)
    for _ in range(n_bars):
        rows.append(f"{day.isoformat()},{price:.2f},{price + 1:.2f},"
                    f"{price - 1:.2f},{price:.2f},1000")
        price += 1.0
        day += datetime.timedelta(days=1)
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return csv_path


def test_refresh_stale_csv_is_flagged_not_silent(tmp_path):
    csv_path = _write_stale_csv(tmp_path)
    out = tmp_path / "old.json"
    dataset = refresh_one(csv_path, out, now=NOW, today=TODAY)
    assert dataset["stale"] is True
    assert "STALE" in dataset["stale_note"]
    assert dataset["age_days"] > 2000
    written = json.loads(out.read_text())
    assert written["stale"] is True


def test_refresh_missing_csv_raises_clear_error(tmp_path):
    with pytest.raises(ValueError, match="cannot read"):
        refresh_one(tmp_path / "nope.csv", tmp_path / "nope.json",
                    now=NOW, today=TODAY)


def test_refresh_too_few_bars_raises(tmp_path):
    csv_path = tmp_path / "tiny.csv"
    csv_path.write_text("date,close\n2026-11-06,100.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="at least 20 bars"):
        refresh_one(csv_path, tmp_path / "tiny.json", now=NOW, today=TODAY)


def _run_refresh(*args, cwd=REPO_ROOT):
    return subprocess.run(
        [sys.executable, "-m", "scripts.refresh_derived", *args],
        cwd=cwd, capture_output=True, text=True, timeout=120, check=False)


def test_main_refreshes_demo_and_exits_zero(tmp_path):
    proc = _run_refresh("--csv", str(DEMO_CSV),
                        "--out-dir", str(tmp_path / "derived"), cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "refreshed" in proc.stdout
    assert "(as of 2026-11-06, 80 bars, fresh)" in proc.stdout
    dataset = json.loads((tmp_path / "derived" / "demo.json").read_text())
    assert dataset["stale"] is False


def test_main_missing_csv_exits_two_and_names_the_file(tmp_path):
    proc = _run_refresh("--csv", str(tmp_path / "nope.csv"),
                        "--out-dir", str(tmp_path / "derived"), cwd=REPO_ROOT)
    assert proc.returncode == 2
    assert "error: refresh failed for" in proc.stderr
    assert "nope.csv" in proc.stderr


def test_main_stale_csv_warns_but_exits_zero(tmp_path):
    csv_path = _write_stale_csv(tmp_path)
    proc = _run_refresh("--csv", str(csv_path),
                        "--out-dir", str(tmp_path / "derived"), cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "warning" in proc.stderr
    assert "STALE" in proc.stderr
    dataset = json.loads((tmp_path / "derived" / "old.json").read_text())
    assert dataset["stale"] is True


def test_main_continues_after_a_failure(tmp_path):
    proc = _run_refresh("--csv", str(tmp_path / "nope.csv"),
                        "--csv", str(DEMO_CSV),
                        "--out-dir", str(tmp_path / "derived"), cwd=REPO_ROOT)
    assert proc.returncode == 2
    assert (tmp_path / "derived" / "demo.json").exists()
