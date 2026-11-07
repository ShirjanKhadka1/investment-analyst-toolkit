"""Tests for toolkit/terminal.py — the one-command research terminal.

The demo CSV is the shipped synthetic example (see demo.csv header):
80 weekday bars, clearly labelled SYNTHETIC, never real market data.
End-to-end tests run the CLI as a subprocess, exactly as a user
would: ``python -m toolkit.terminal demo.csv``.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from toolkit.terminal import build_dashboard, evaluate_setups, load_bars

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_CSV = REPO_ROOT / "demo.csv"
SECTIONS = (
    "1. Indicator snapshot",
    "2. Screening",
    "3. Time-Machine odds",
    "4. Risk gate",
)
HONESTY_LABELS = (
    "not investment advice",
    "Simulation, not a prediction",
    "not a recommendation",
    "not a buy/sell/hold signal",
    "A PASS is not a yes",
)


def _run(*args, cwd=REPO_ROOT):
    return subprocess.run(
        [sys.executable, "-m", "toolkit.terminal", *args],
        cwd=cwd, capture_output=True, text=True, timeout=120, check=False)


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- end-to-end on the shipped demo ----------------------------------------

def test_demo_csv_runs_end_to_end_and_exits_zero():
    assert DEMO_CSV.exists(), "shipped demo.csv is missing"
    proc = _run(str(DEMO_CSV))
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stdout + proc.stderr
    for section in SECTIONS:
        assert section in proc.stdout, f"missing section: {section}"
    assert "Bars: 80" in proc.stdout
    assert "[MATCH] breakout_20d" in proc.stdout
    assert "[MATCH] bull_flag" in proc.stdout
    assert "Matches: 2 of 5 setups" in proc.stdout
    assert "Target first" in proc.stdout
    assert "Historical replay" in proc.stdout
    assert "Verdict: BLOCK" in proc.stdout  # entry window not confirmed


def test_demo_output_carries_every_honesty_label():
    proc = _run(str(DEMO_CSV))
    assert proc.returncode == 0, proc.stderr
    for label in HONESTY_LABELS:
        assert label in proc.stdout, f"missing honesty label: {label}"


def test_demo_output_is_deterministic():
    first = _run(str(DEMO_CSV), )
    second = _run(str(DEMO_CSV))
    assert first.returncode == 0 and second.returncode == 0
    assert first.stdout == second.stdout
    assert first.stderr == second.stderr


def test_help_exits_zero():
    proc = _run("--help")
    assert proc.returncode == 0
    assert "demo.csv" in proc.stdout or "CSV" in proc.stdout


def test_entry_window_ok_passes_the_gate():
    proc = _run(str(DEMO_CSV), "--entry-window-ok")
    assert proc.returncode == 0, proc.stderr
    assert "Verdict: PASS (16/16 checks passed)" in proc.stdout


# --- bad input: non-zero exit, one-line error, never a traceback -----------

def test_malformed_csv_exits_nonzero_without_traceback(tmp_path):
    bad = _write(tmp_path, "bad.csv",
                 "date,open,high,low,close,volume\n"
                 "2026-01-01,100,101,99,abc,1000\n")
    proc = _run(str(bad))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stdout + proc.stderr
    assert "error:" in proc.stderr


def test_empty_csv_is_handled(tmp_path):
    empty = _write(tmp_path, "empty.csv",
                   "date,open,high,low,close,volume\n")
    proc = _run(str(empty))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stdout + proc.stderr
    assert "error:" in proc.stderr


def test_header_only_comment_file_is_handled(tmp_path):
    only_comments = _write(tmp_path, "comments.csv",
                           "# nothing but a comment\n")
    proc = _run(str(only_comments))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stdout + proc.stderr


def test_missing_columns_is_handled(tmp_path):
    nocols = _write(tmp_path, "nocols.csv", "not,a,real,file\n1,2,3,4\n")
    proc = _run(str(nocols))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stdout + proc.stderr
    assert "error:" in proc.stderr


def test_missing_file_is_handled(tmp_path):
    proc = _run(str(tmp_path / "does-not-exist.csv"))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stdout + proc.stderr
    assert "error:" in proc.stderr


def test_close_outside_high_low_is_handled(tmp_path):
    bad = _write(tmp_path, "range.csv",
                 "date,open,high,low,close,volume\n"
                 "2026-01-01,100,101,99,150,1000\n")
    proc = _run(str(bad))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stdout + proc.stderr


def test_bad_stop_flag_is_handled(tmp_path):
    proc = _run(str(DEMO_CSV), "--stop", "99999", "--target", "999999")
    assert proc.returncode != 0
    assert "Traceback" not in proc.stdout + proc.stderr
    assert "error:" in proc.stderr


def test_stop_without_target_is_handled(tmp_path):
    proc = _run(str(DEMO_CSV), "--stop", "1200")
    assert proc.returncode != 0
    assert "Traceback" not in proc.stdout + proc.stderr


# --- short history degrades gracefully, still exits zero -------------------

def test_short_csv_degrades_gracefully(tmp_path):
    short = _write(tmp_path, "short.csv",
                   "date,close\n"
                   "2026-01-01,100\n2026-01-02,101\n2026-01-05,102\n")
    proc = _run(str(short))
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stdout + proc.stderr
    for section in SECTIONS:
        assert section in proc.stdout
    assert "n/a (needs 20 bars)" in proc.stdout
    assert "No odds summary" in proc.stdout  # too few windows: a note
    assert "Risk gate" in proc.stdout
    assert "Verdict: BLOCK" in proc.stdout


# --- unit tests: loader and setups ------------------------------------------

def test_load_bars_skips_comment_lines_and_loads_80_bars():
    bars = load_bars(str(DEMO_CSV))
    assert len(bars) == 80
    assert bars[0]["date"] == "2026-07-20"
    assert bars[-1]["date"] == "2026-11-06"
    assert bars[-1]["close"] == pytest.approx(1284.91)
    assert all(b["high"] >= b["close"] >= b["low"] for b in bars)
    assert all(b["volume"] is not None and b["volume"] >= 0 for b in bars)


def test_load_bars_close_only_csv_defaults_open_high_low(tmp_path):
    path = _write(tmp_path, "closes.csv",
                  "date,close\n2026-01-01,100\n2026-01-02,102\n")
    bars = load_bars(str(path))
    assert bars[0] == {"date": "2026-01-01", "close": 100.0,
                       "open": 100.0, "high": 100.0, "low": 100.0,
                       "volume": None}
    assert bars[1]["open"] == pytest.approx(100.0)  # previous close


def test_load_bars_rejects_empty_file(tmp_path):
    path = _write(tmp_path, "blank.csv", "")
    with pytest.raises(ValueError, match="empty"):
        load_bars(str(path))


def test_evaluate_setups_breakout_matches_and_flat_does_not():
    rising = [100.0 + i for i in range(21)]  # last close is the high
    assert evaluate_setups(rising)["breakout_20d"][0] is True
    flat = [100.0] * 30
    results = evaluate_setups(flat)
    assert results["breakout_20d"][0] is False
    assert results["bull_flag"][0] is False
    assert results["reclaim_ma20"][0] is False


def test_evaluate_setups_oversold_on_a_falling_series():
    falling = [200.0 - 2.0 * i for i in range(30)]
    results = evaluate_setups(falling)
    assert results["oversold"][0] is True
    assert "RSI-14" in results["oversold"][1]


def test_evaluate_setups_reports_too_few_bars():
    results = evaluate_setups([100.0, 101.0, 102.0])
    assert results["breakout_20d"][0] is False
    assert "too few closes" in results["breakout_20d"][1]


# --- data-health section (Phase 2) -----------------------------------------

def _health(overall="FRESH", sources=None):
    return {
        "generated_at": "2026-11-07T12:00:00+00:00",
        "generated_by": "scripts/data_health.py",
        "overall": overall,
        "sources": sources if sources is not None else [
            {"name": "yahoo", "status": "FRESH", "last_data_date": "2026-11-06",
             "age_days": 1, "sla_label": "1 day", "error": None},
            {"name": "coingecko", "status": "FRESH", "last_data_date": "2026-11-07",
             "age_days": 0, "sla_label": "1 day", "error": None},
            {"name": "nepse_csv", "status": "UNKNOWN", "last_data_date": None,
             "age_days": None, "sla_label": "1 day", "error": None},
        ],
    }


def test_dashboard_without_health_says_not_checked():
    bars = load_bars(str(DEMO_CSV))
    text = build_dashboard(bars)
    assert "5. Data health" in text
    assert "Not checked in this run" in text
    assert "scripts/data_health.py" in text


def test_dashboard_with_health_shows_per_source_status():
    bars = load_bars(str(DEMO_CSV))
    text = build_dashboard(bars, data_health=_health())
    assert "5. Data health" in text
    assert "Overall: FRESH" in text
    assert "FRESH       yahoo" in text
    assert "last data 2026-11-06" in text
    assert "UNKNOWN     nepse_csv" in text


def test_dashboard_with_stale_source_warns_plainly():
    bars = load_bars(str(DEMO_CSV))
    stale = _health(overall="DEGRADED", sources=[
        {"name": "yahoo", "status": "STALE", "last_data_date": "2026-10-01",
         "age_days": 37, "sla_label": "1 day", "error": None},
    ])
    text = build_dashboard(bars, data_health=stale)
    assert "STALE       yahoo" in text
    assert "WARNING: a source above is STALE or UNAVAILABLE" in text
    assert "Do not treat this dashboard as fresh" in text


def test_cli_data_health_flag_shows_section(tmp_path):
    health_path = tmp_path / "health.json"
    health_path.write_text(json.dumps(_health()), encoding="utf-8")
    proc = _run(str(DEMO_CSV), "--data-health", str(health_path))
    assert proc.returncode == 0, proc.stderr
    assert "5. Data health" in proc.stdout
    assert "Overall: FRESH" in proc.stdout


def test_cli_missing_data_health_file_exits_nonzero(tmp_path):
    proc = _run(str(DEMO_CSV), "--data-health", str(tmp_path / "nope.json"))
    assert proc.returncode == 2
    assert "error: cannot read data-health file" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_cli_malformed_data_health_file_exits_nonzero(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    proc = _run(str(DEMO_CSV), "--data-health", str(bad))
    assert proc.returncode == 2
    assert "error: cannot read data-health file" in proc.stderr
