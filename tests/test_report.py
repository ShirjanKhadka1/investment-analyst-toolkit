"""Tests for toolkit/report.py (engine 1.9) and the terminal `research`
subcommand. All fixtures are synthetic and offline. The honesty rule
under test: every number in a report traces to an input row or is
labelled UNKNOWN — nothing is invented.
"""

import datetime
import subprocess
import sys
from pathlib import Path

import pytest

from toolkit import report

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_CSV = REPO_ROOT / "demo.csv"


def _bars(n=30, start=100.0, step=1.0):
    base = datetime.date(2026, 1, 1)
    return [{"date": base + datetime.timedelta(days=i),
             "close": start + step * i} for i in range(n)]


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _run(*args, cwd=REPO_ROOT):
    return subprocess.run(
        [sys.executable, "-m", "toolkit.terminal", *args],
        cwd=cwd, capture_output=True, text=True, timeout=120, check=False)


# --- build_report ----------------------------------------------------------

def test_no_data_report_says_so_plainly():
    built = report.build_report("NABIL")
    assert built["status"] == "no_data"
    assert "No data was supplied for NABIL" in built["thesis"]
    assert built["figures"] == {}
    assert built["peers"] == []
    assert built["fundamentals"] == []
    html_text = report.render_html(built)
    assert "No data was supplied for NABIL" in html_text
    assert "no comparison is shown, and none was invented" in html_text


def test_empty_symbol_refused():
    with pytest.raises(ValueError):
        report.build_report("   ")


def test_synthetic_bars_produce_traced_figures():
    bars = _bars(30, start=100.0, step=1.0)
    built = report.build_report("DEMO", bars=bars, as_of="2026-02-01")
    assert built["status"] == "ok"
    assert built["figures"]["latest_close"]["value"] == 129.0
    assert built["figures"]["bars_n"]["value"] == 30
    # SMA-20 of 110..129 is 119.5; 20-session change is +19/110.
    assert built["figures"]["sma20"]["value"] == pytest.approx(119.5)
    assert built["figures"]["change_20d_pct"]["value"] == pytest.approx(
        (129.0 / 110.0 - 1.0) * 100.0)
    assert "supplied bars: 30 rows" in built["figures"]["sma20"]["source"]
    assert "129.00" in built["thesis"]
    assert any("above its 20-day moving average" in b for b in built["bull"])
    assert "not investment advice" in report.render_html(built)


def test_short_history_has_no_sma_and_flags_the_risk():
    built = report.build_report("DEMO", bars=_bars(10), as_of="2026-02-01")
    assert "sma20" not in built["figures"]
    assert any("Short price history" in r for r in built["risks"])


def test_fundamentals_keep_source_or_unknown():
    built = report.build_report(
        "DEMO",
        fundamentals=[
            {"field": "revenue", "value": 1000.0,
             "source": "Annual report FY 2081/82", "period": "FY 2081/82"},
            {"field": "net_income", "value": 50.0, "source": "", "period": ""},
        ],
        as_of="2026-02-01")
    assert built["fundamentals"][0]["source"] == "Annual report FY 2081/82"
    assert built["fundamentals"][1]["source"] == "UNKNOWN"
    assert built["fundamentals"][1]["period"] == "UNKNOWN"
    html_text = report.render_html(built)
    assert "Annual report FY 2081/82" in html_text
    assert html_text.count("UNKNOWN") >= 2


def test_peers_labelled_with_source_or_unknown():
    built = report.build_report(
        "DEMO",
        peers=[
            {"symbol": "PEER1", "metric": "pe_ratio", "value": 12.5,
             "source": "Annual report FY 2081/82"},
            {"symbol": "PEER2", "metric": "pe_ratio", "value": 9.0,
             "source": ""},
        ],
        as_of="2026-02-01")
    assert built["peers"][0]["source"] == "Annual report FY 2081/82"
    assert built["peers"][1]["source"] == "UNKNOWN"
    html_text = report.render_html(built)
    assert "PEER1" in html_text and "PEER2" in html_text
    assert "UNKNOWN" in html_text


def test_html_escapes_symbol_and_sources():
    built = report.build_report(
        "<script>alert(1)</script>", bars=_bars(30),
        sources=["<b>not bold</b>"], as_of="2026-02-01")
    html_text = report.render_html(built)
    assert "<script>" not in html_text
    # build_report uppercases the symbol, so the escaped form is uppercase.
    assert "&lt;SCRIPT&gt;" in html_text
    assert "<b>not bold</b>" not in html_text


def test_write_report_html_writes_file(tmp_path):
    built = report.build_report("DEMO", bars=_bars(30), as_of="2026-02-01")
    path = report.write_report_html(built, tmp_path / "sub" / "DEMO.html")
    assert path.is_file()
    assert "Research report" in path.read_text(encoding="utf-8")


# --- CSV loaders ------------------------------------------------------------

def test_bars_from_csv_round_trip(tmp_path):
    path = _write(tmp_path, "bars.csv",
                  "# synthetic fixture\nDate,Close\n2026-01-01,100\n2026-01-02,101\n")
    bars = report.bars_from_csv(path)
    assert len(bars) == 2
    assert bars[0]["close"] == 100.0
    assert bars[0]["date"] == datetime.date(2026, 1, 1)


def test_bars_from_csv_rejects_bad_input(tmp_path):
    path = _write(tmp_path, "bad.csv", "date,close\n2026-01-01,abc\n")
    with pytest.raises(ValueError):
        report.bars_from_csv(path)
    missing = _write(tmp_path, "empty.csv", "date,close\n")
    with pytest.raises(ValueError):
        report.bars_from_csv(missing)
    with pytest.raises(ValueError):
        report.bars_from_csv(tmp_path / "does-not-exist.csv")


def test_fundamentals_and_peers_from_csv(tmp_path):
    fund = _write(tmp_path, "fund.csv",
                  "field,value,source,period\nrevenue,1000,Annual report,2081/82\n"
                  "net_income,50,,\n")
    rows = report.fundamentals_from_csv(fund)
    assert rows[0]["source"] == "Annual report"
    assert rows[1]["source"] == ""
    peers = _write(tmp_path, "peers.csv",
                   "symbol,metric,value,source\nPEER1,pe_ratio,12.5,Annual report\n")
    prow = report.peers_from_csv(peers)
    assert prow[0]["symbol"] == "PEER1" and prow[0]["value"] == 12.5


# --- terminal research subcommand -------------------------------------------

def test_research_subcommand_writes_html(tmp_path):
    out = tmp_path / "DEMO-research.html"
    proc = _run("research", "DEMO", "--bars", str(DEMO_CSV),
                "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    assert "Research report for DEMO" in proc.stdout
    assert "not investment advice" in proc.stdout
    assert out.is_file()
    html_text = out.read_text(encoding="utf-8")
    assert "Thesis" in html_text and "Peers and sector comps" in html_text


def test_research_subcommand_no_data(tmp_path):
    out = tmp_path / "NABIL-research.html"
    proc = _run("research", "NABIL", "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    assert "Status: no_data" in proc.stdout
    assert "No data was supplied for NABIL" in out.read_text(encoding="utf-8")


def test_research_subcommand_stdout(tmp_path):
    proc = _run("research", "DEMO", "--bars", str(DEMO_CSV), "--stdout")
    assert proc.returncode == 0, proc.stderr
    assert "<html" in proc.stdout


def test_research_subcommand_bad_csv(tmp_path):
    bad = _write(tmp_path, "bad.csv", "date,close\n2026-01-01,abc\n")
    proc = _run("research", "DEMO", "--bars", str(bad))
    assert proc.returncode == 2
    assert "Traceback" not in proc.stdout + proc.stderr
    assert proc.stderr.startswith("error: ")


def test_demo_command_still_works():
    proc = _run(str(DEMO_CSV))
    assert proc.returncode == 0, proc.stderr
    assert "1. Indicator snapshot" in proc.stdout
    assert "Bars: 80" in proc.stdout
