"""Tests for the NEPSE bring-your-own-data kit (Round E, E.3).

No network: every test runs on the bundled synthetic fixtures
or on small CSVs written to tmp_path. The fixtures are
SYNTHETIC (invented prices, invented brokers) — never real
market data.
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from toolkit.nepse_byod import (
    NepseByodError,
    run_byod,
    validate_nepse_floorsheet_csv,
    validate_nepse_price_csv,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
PRICES = FIXTURES / "nepse_byod_prices_SYNTHETIC.csv"
FLOORSHEET = FIXTURES / "nepse_byod_floorsheet_SYNTHETIC.csv"
EVENTS = FIXTURES / "nepse_byod_events_SYNTHETIC.csv"
SOURCE = "synthetic fixture, generated from seed 20261115"


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def test_validate_price_fixture():
    groups, source = validate_nepse_price_csv(PRICES, SOURCE)
    assert source == SOURCE
    assert len(groups) == 12
    for group in groups.values():
        assert len(group["closes"]) == 280
        assert group["dates"][0] == "2025-01-01"
        assert group["dates"] == sorted(group["dates"])


def test_validate_floorsheet_fixture():
    rows, source = validate_nepse_floorsheet_csv(FLOORSHEET, SOURCE)
    assert source == SOURCE
    assert len(rows) == 120
    assert {row["broker"] for row in rows} == {"58", "34", "21", "45"}


def test_source_required():
    with pytest.raises(NepseByodError, match="source must be named"):
        validate_nepse_price_csv(PRICES, "")
    with pytest.raises(NepseByodError, match="source must be named"):
        validate_nepse_floorsheet_csv(FLOORSHEET, "   ")


def test_missing_column_refused(tmp_path):
    path = tmp_path / "prices.csv"
    _write_csv(path, ["symbol", "sector", "date", "close", "high",
                      "low"],
               [["NABIL", "Banks", "2025-01-01", "100", "101", "99"]])
    with pytest.raises(NepseByodError, match="volume"):
        validate_nepse_price_csv(path, SOURCE)


def test_bad_date_refused(tmp_path):
    path = tmp_path / "prices.csv"
    _write_csv(path, ["symbol", "sector", "date", "close", "high",
                      "low", "volume"],
               [["NABIL", "Banks", "01/01/2025", "100", "101", "99",
                 "1000"]])
    with pytest.raises(NepseByodError, match="line 2"):
        validate_nepse_price_csv(path, SOURCE)


def test_non_monotonic_dates_refused(tmp_path):
    path = tmp_path / "prices.csv"
    _write_csv(path, ["symbol", "sector", "date", "close", "high",
                      "low", "volume"],
               [["NABIL", "Banks", "2025-01-02", "100", "101", "99",
                 "1000"],
                ["NABIL", "Banks", "2025-01-01", "101", "102", "100",
                 "1000"]])
    with pytest.raises(NepseByodError,
                       match="not strictly increasing"):
        validate_nepse_price_csv(path, SOURCE)


def test_empty_cell_refused(tmp_path):
    path = tmp_path / "prices.csv"
    _write_csv(path, ["symbol", "sector", "date", "close", "high",
                      "low", "volume"],
               [["NABIL", "Banks", "2025-01-01", "", "101", "99",
                 "1000"]])
    with pytest.raises(NepseByodError, match="missing close"):
        validate_nepse_price_csv(path, SOURCE)


def test_non_positive_close_refused(tmp_path):
    path = tmp_path / "prices.csv"
    _write_csv(path, ["symbol", "sector", "date", "close", "high",
                      "low", "volume"],
               [["NABIL", "Banks", "2025-01-01", "0", "101", "99",
                 "1000"]])
    with pytest.raises(NepseByodError):
        validate_nepse_price_csv(path, SOURCE)


def test_high_below_low_refused(tmp_path):
    path = tmp_path / "prices.csv"
    _write_csv(path, ["symbol", "sector", "date", "close", "high",
                      "low", "volume"],
               [["NABIL", "Banks", "2025-01-01", "100", "99", "101",
                 "1000"]])
    with pytest.raises(NepseByodError):
        validate_nepse_price_csv(path, SOURCE)


def test_bad_floorsheet_side_refused(tmp_path):
    path = tmp_path / "floorsheet.csv"
    _write_csv(path, ["date", "broker", "symbol", "side", "quantity",
                      "price"],
               [["2025-01-02", "58", "NABIL", "hold", "100",
                 "500.00"]])
    with pytest.raises(NepseByodError, match="side"):
        validate_nepse_floorsheet_csv(path, SOURCE)


def test_single_symbol_form(tmp_path):
    path = tmp_path / "single.csv"
    rows = [["2025-01-01", "100", "101", "99", "1000"],
            ["2025-01-02", "101", "102", "100", "1100"]]
    _write_csv(path, ["date", "close", "high", "low", "volume"], rows)
    closes, dates, source = validate_nepse_price_csv(
        path, SOURCE, single_symbol=True)
    assert closes == [100.0, 101.0]
    assert dates == ["2025-01-01", "2025-01-02"]
    assert source == SOURCE


def test_too_short_series_refuses_run(tmp_path):
    path = tmp_path / "short.csv"
    rows = []
    for i in range(100):
        day = f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
        rows.append(["NABIL", "Banks", day, "100", "101", "99",
                     "1000"])
    _write_csv(path, ["symbol", "sector", "date", "close", "high",
                      "low", "volume"], rows)
    with pytest.raises(NepseByodError, match="260\\+ closes"):
        run_byod(path, FLOORSHEET, SOURCE)


def test_full_run_on_fixtures():
    report = run_byod(PRICES, FLOORSHEET, SOURCE, events_path=EVENTS)
    assert report["source"] == SOURCE
    assert len(report["per_symbol"]) == 12
    assert report["floorsheet"]["n_rows"] == 120
    assert set(report["floorsheet"]["broker_flow"]) == {
        "58", "34", "21", "45"}
    # Floorsheet flow is descriptive: buys minus sells across rows.
    total_net = sum(flow["net_flow"] for flow in
                    report["floorsheet"]["broker_flow"].values())
    assert total_net == pytest.approx(
        sum(row["value"] if row["side"] == "buy" else -row["value"]
            for row in validate_nepse_floorsheet_csv(
                FLOORSHEET, SOURCE)[0]))
    harness = report["harness"]
    assert sorted(harness["symbols"]) == sorted(report["per_symbol"])
    assert harness["models_tried"] == 11
    assert set(harness["overall_price_verdicts"]) == {
        "naive", "moving_average_5", "linear_trend",
        "exp_smoothing_0.3", "ar1", "holt", "holt_damped",
        "drift", "ensemble_equal", "ensemble_adaptive"}
    assert report["direction"]["symbols"] == sorted(
        report["per_symbol"])
    ranking = report["ranking"]
    assert ranking["n_symbols"] == 12
    assert ranking["forward_bars"] == 21
    assert ranking["rules"]["momentum"]["n"] == len(
        ranking["ranking_bars"])
    assert ranking["rules"]["momentum"]["verdict"] in {
        "edge", "no edge"}
    study = report["event_study"]
    assert study["n_events"] == 4
    assert study["market_proxy"] == "API"
    assert set(study["aggregate"]) >= {
        "caar", "t_stat", "bootstrap_p", "significant",
        "small_n_caveat"}
    assert study["aggregate"]["small_n_caveat"] is True


def test_cli_writes_reports(tmp_path):
    # The CLI run uses no events file, covering the no-events path.
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "nepse_byod_run.py"),
         "--prices", str(PRICES), "--floorsheet", str(FLOORSHEET),
         "--source", SOURCE,
         "--out", str(out_dir)],
        capture_output=True, text=True, cwd=ROOT, timeout=600,
         check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(
        (out_dir / "nepse-byod-report.json").read_text(
            encoding="utf-8"))
    assert report["floorsheet"]["n_rows"] == 120
    assert "event_study" not in report
    markdown = (out_dir / "nepse-byod-report.md").read_text(
        encoding="utf-8")
    assert "NEPSE bring-your-own-data run" in markdown
    assert "descriptive only" in markdown


def test_cli_refusal_exit_code(tmp_path):
    bad = tmp_path / "bad.csv"
    _write_csv(bad, ["symbol", "sector", "date", "close", "high",
                     "low", "volume"],
               [["NABIL", "Banks", "2025-01-01", "100", "101", "99",
                 "1000"]])
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "nepse_byod_run.py"),
         "--prices", str(bad), "--floorsheet", str(FLOORSHEET),
         "--source", SOURCE, "--out", str(tmp_path / "out")],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
         check=False)
    assert result.returncode == 2
    assert "error:" in result.stderr
