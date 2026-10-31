"""Round 3 (R6) tests: NEPSE user-supplied-CSV loader only.

No live feed, no real NEPSE price, no fabricated series. The
multi-symbol fixture is synthetic and labelled as such; the
single-symbol check reuses the scanner repo's labelled AAPL
example file path only when it exists next to this repo tree,
and otherwise is skipped by the caller (see the guard below).
"""

import os

import pytest

from toolkit.nepse_loader import (
    NepseLoaderError,
    load_nepse_user_csv,
    load_nepse_user_csv_single,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SYNTHETIC = os.path.join(
    HERE, "..", "..", "nepali-capital-market-research", "scanner",
    "examples", "nepse_user_supplied_SYNTHETIC.csv")


def _synthetic_path():
    return os.path.normpath(SYNTHETIC)


def test_synthetic_multi_symbol_loads(tmp_path):
    # Self-contained: use the scanner repo example when it is adjacent,
    # otherwise write the same synthetic fixture locally. A test that
    # skips in a single-repo CI checkout made the suite count differ
    # between local (198) and CI (197) runs, failing the number check.
    if os.path.exists(_synthetic_path()):
        path = _synthetic_path()
    else:
        path = str(tmp_path / "nepse_user_supplied_SYNTHETIC.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("symbol,sector,date,close,high,low,volume\n")
            for i, close in enumerate(
                    [100.00, 101.00, 102.50, 101.50, 103.00], start=1):
                f.write(
                    f"SYNTHETIC-A,S,2026-01-0{i},{close},{close + 1},"
                    f"{close - 1},100\n")
            for i, close in enumerate(
                    [50.00, 51.00, 52.00, 51.50, 53.00], start=1):
                f.write(
                    f"SYNTHETIC-B,S,2026-01-0{i},{close},{close + 1},"
                    f"{close - 1},100\n")
    groups, source = load_nepse_user_csv(
        path, "SYNTHETIC example file, invented for testing")
    assert set(groups) == {"SYNTHETIC-A", "SYNTHETIC-B"}
    assert groups["SYNTHETIC-A"]["closes"] == [
        100.00, 101.00, 102.50, 101.50, 103.00]
    assert source.startswith("SYNTHETIC")


def test_source_is_mandatory():
    with pytest.raises(NepseLoaderError):
        load_nepse_user_csv(_synthetic_path(), "")
    with pytest.raises(NepseLoaderError):
        load_nepse_user_csv_single(_synthetic_path(), "  ")


def test_bad_row_raises_not_skipped(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "symbol,sector,date,close,high,low,volume\n"
        "X,S,2026-01-01,10,11,9,100\n"
        "X,S,2026-01-02,-5,11,9,100\n",
        encoding="utf-8")
    with pytest.raises(NepseLoaderError):
        load_nepse_user_csv(str(bad), "hand-written bad-row fixture")
