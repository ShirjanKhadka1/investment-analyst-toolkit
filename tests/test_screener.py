"""Evidence tests for the universe screener (Round 7, PR 7.3).

Offline fixtures only: hand-built (date, close) pairs, no network,
no live data. The fixture universe is small on purpose — one symbol
per named setup, plus a flat symbol that matches nothing — so the
expected match set is exact, not "at least these".

Expected matches (all five setups, as_of 2026-11-03):
- BREAK   (100..120 rising, 21 bars): breakout_20d only — the last
  close is the 21-bar high; the 10-session run gained only 9.5%
  (bull_flag needs 10%) and the close was already above MA20 on the
  prior bar (reclaim needs a cross).
- FLAT    (100 x 30): nothing.
- FALL    (200 down by 2, 30 bars): oversold only — RSI-14 collapses;
  the last close is below the prior close, so no oversold bounce.
- BULL    (100 -> 110 over 10 sessions, then five 112s): bull_flag
  only — exactly +10% on the run, last-5 range 0%.
- RECLAIM (100 x 20, then 90, then 105): breakout_20d (105 above the
  100 high), reclaim_ma20 (105 crossed above MA20 99.75 after 90 sat
  below 99.50), and oversold_bounce (RSI-14 hit 0.0 on the 90 bar,
  inside the last 5, and 105 is above 90).

Base rate (defined in toolkit/screener.py): a setup's firing rate on
the same universe with NO criteria filters — same as-of cut, full
bars, no min_price / min_volume / lookback. The gap between a
setup's firing rate and its base rate is exactly what the criteria
did.
"""

import datetime

import pytest

from toolkit.screener import (
    append_audit_log,
    parse_criteria,
    read_audit_log,
    scan_universe,
)
from toolkit.terminal import SETUP_ORDER, evaluate_setups

START = datetime.date(2026, 10, 5)


def _pairs(closes, start=START):
    return [((start + datetime.timedelta(days=i)).isoformat(), float(c))
            for i, c in enumerate(closes)]


def _universe():
    return {
        "BREAK": _pairs([100.0 + i for i in range(21)]),
        "FLAT": _pairs([100.0] * 30),
        "FALL": _pairs([200.0 - 2.0 * i for i in range(30)]),
        "BULL": _pairs([100, 101, 102, 103, 104, 105, 106, 107, 108,
                        109, 110, 112, 112, 112, 112, 112]),
        "RECLAIM": _pairs([100.0] * 20 + [90.0, 105.0]),
    }


EXPECTED = {
    "BREAK": {"breakout_20d"},
    "FLAT": set(),
    "FALL": {"oversold"},
    "BULL": {"bull_flag"},
    "RECLAIM": {"breakout_20d", "reclaim_ma20", "oversold_bounce"},
}

AS_OF = "2026-11-03"


def _matched(result, symbols=None):
    out = {}
    for row in result.matches:
        out.setdefault(row["symbol"], set()).add(row["setup"])
    for symbol in (symbols if symbols is not None else _universe()):
        out.setdefault(symbol, set())
    return out


def test_scan_matches_expected_setups_for_the_fixture_universe():
    result = scan_universe(_universe(), {"as_of": AS_OF})
    assert _matched(result) == EXPECTED
    assert result.as_of == AS_OF
    assert result.criteria["setups"] == list(SETUP_ORDER)
    # The screener reuses terminal.evaluate_setups: the detail strings
    # must be identical, not a reimplementation.
    closes = [c for _d, c in _universe()["BREAK"]]
    expected_detail = evaluate_setups(closes)["breakout_20d"][1]
    breakout = [m for m in result.matches
                if m["symbol"] == "BREAK"
                and m["setup"] == "breakout_20d"]
    assert len(breakout) == 1
    assert breakout[0]["detail"] == expected_detail
    assert breakout[0]["as_of"] == AS_OF


def test_audit_rows_cover_every_symbol_setup_pair_and_reproduce():
    universe = _universe()
    result = scan_universe(universe, {"as_of": AS_OF})
    assert len(result.audit_rows) == len(universe) * len(SETUP_ORDER)
    canonical = parse_criteria({"as_of": AS_OF}).to_dict()
    bars_used = {"BREAK": 21, "FLAT": 30, "FALL": 30, "BULL": 16,
                 "RECLAIM": 22}
    for row in result.audit_rows:
        assert row["criteria"] == canonical
        assert row["as_of"] == AS_OF
        assert row["bars_used"] == bars_used[row["symbol"]]
        assert row["volume_applied"] is False
        assert row["matched"] == (row["setup"] in EXPECTED[row["symbol"]])
        assert isinstance(row["detail"], str) and row["detail"]
    # Deterministic: the same inputs give byte-identical rows, and a
    # different criteria spec shows up in the rows, not silently.
    again = scan_universe(universe, {"as_of": AS_OF})
    assert again.audit_rows == result.audit_rows
    subset = scan_universe(universe, {"as_of": AS_OF,
                                      "setups": ["oversold"]})
    assert len(subset.audit_rows) == len(universe)
    assert all(r["setup"] == "oversold" for r in subset.audit_rows)
    assert subset.audit_rows[0]["criteria"]["setups"] == ["oversold"]


def test_unfiltered_scan_firing_rate_equals_base_rate():
    # No filters, so firing == base for every setup on the 5-symbol
    # fixture universe: breakout 2/5, every other setup 1/5.
    result = scan_universe(_universe(), {"as_of": AS_OF})
    assert result.rates["breakout_20d"] == {
        "symbols": 5, "base_matches": 2, "firing_matches": 2,
        "base_rate": 2 / 5, "firing_rate": 2 / 5}
    for name in ("bull_flag", "reclaim_ma20", "oversold",
                 "oversold_bounce"):
        assert result.rates[name] == {
            "symbols": 5, "base_matches": 1, "firing_matches": 1,
            "base_rate": 1 / 5, "firing_rate": 1 / 5}
    for row in result.audit_rows:
        assert row["firing_rate"] == row["base_rate"]
        assert row["firing_rate"] == result.rates[row["setup"]][
            "firing_rate"]


def test_firing_rate_vs_base_rate_shows_what_the_filter_did():
    # CHEAP would break out unfiltered, but min_price=10 filters it
    # out — so breakout's firing rate (2/6) drops below its base
    # rate (3/6) while every other setup's rates are unchanged.
    universe = dict(_universe())
    universe["CHEAP"] = _pairs([5.0 + 0.1 * i for i in range(21)])
    result = scan_universe(universe, {"as_of": AS_OF, "min_price": 10})
    assert result.rates["breakout_20d"] == {
        "symbols": 6, "base_matches": 3, "firing_matches": 2,
        "base_rate": 3 / 6, "firing_rate": 2 / 6}
    for name in ("bull_flag", "reclaim_ma20", "oversold",
                 "oversold_bounce"):
        assert result.rates[name]["base_rate"] == 1 / 6
        assert result.rates[name]["firing_rate"] == 1 / 6
    breakout_rows = [r for r in result.audit_rows
                     if r["setup"] == "breakout_20d"]
    assert all(r["firing_rate"] == 2 / 6 for r in breakout_rows)
    assert all(r["base_rate"] == 3 / 6 for r in breakout_rows)
    # The same filter applied to a subset scan still compares
    # against the base rate on the same universe.
    subset = scan_universe(universe, {"as_of": AS_OF, "min_price": 10,
                                      "setups": ["breakout_20d"]})
    assert subset.rates == {"breakout_20d": {
        "symbols": 6, "base_matches": 3, "firing_matches": 2,
        "base_rate": 3 / 6, "firing_rate": 2 / 6}}


def test_min_price_filter_keeps_cheap_symbols_out_and_says_so():
    universe = dict(_universe())
    universe["CHEAP"] = _pairs([5.0 + 0.1 * i for i in range(21)])
    filtered = scan_universe(universe, {"as_of": AS_OF, "min_price": 10})
    assert _matched(filtered, universe)["CHEAP"] == set()
    cheap_rows = [r for r in filtered.audit_rows
                  if r["symbol"] == "CHEAP"]
    assert len(cheap_rows) == len(SETUP_ORDER)
    assert all(not r["matched"] for r in cheap_rows)
    assert all("below min_price 10.00" in r["detail"]
               for r in cheap_rows)
    assert all(r["bars_used"] == 21 for r in cheap_rows)
    # Without the filter the same series does match breakout_20d.
    unfiltered = scan_universe(universe, {"as_of": AS_OF})
    assert _matched(unfiltered, universe)["CHEAP"] == {"breakout_20d"}


def test_min_volume_applies_only_where_volume_data_exists():
    universe = dict(_universe())
    universe["VOL_LOW"] = _pairs([100.0 + i for i in range(21)])
    volumes = {
        "BREAK": [(d, 1000.0) for d, _c in universe["BREAK"]],
        "VOL_LOW": [(d, 100.0) for d, _c in universe["VOL_LOW"]],
    }
    result = scan_universe(universe, {"as_of": AS_OF, "min_volume": 500},
                           volumes=volumes)
    matched = _matched(result, universe)
    assert matched["BREAK"] == {"breakout_20d"}
    assert matched["VOL_LOW"] == set()
    low_rows = [r for r in result.audit_rows
                if r["symbol"] == "VOL_LOW"]
    assert all(r["volume_applied"] is True for r in low_rows)
    assert all("below min_volume 500" in r["detail"] for r in low_rows)
    # FALL has no volume data: the filter is skipped, not invented.
    assert matched["FALL"] == {"oversold"}
    fall_rows = [r for r in result.audit_rows if r["symbol"] == "FALL"]
    assert all(r["volume_applied"] is False for r in fall_rows)


def test_lookback_limits_the_bars_the_setups_see():
    result = scan_universe(_universe(), {"as_of": AS_OF, "lookback": 10})
    assert _matched(result) == {s: set() for s in _universe()}
    assert all(r["bars_used"] == 10 for r in result.audit_rows)
    breakout = next(r for r in result.audit_rows
                    if r["symbol"] == "BREAK"
                    and r["setup"] == "breakout_20d")
    assert breakout["matched"] is False
    assert "too few closes" in breakout["detail"]
    # The lookback also moves the firing rate: with 10 bars nothing
    # can fire, but the base rate (full bars) is unchanged.
    assert result.rates["breakout_20d"]["firing_rate"] == 0
    assert result.rates["breakout_20d"]["base_rate"] == 2 / 5


@pytest.mark.parametrize("spec", [
    {"as_of": AS_OF, "setups": ["breakout_20d", "no_such_setup"]},
    {"as_of": AS_OF, "setups": []},
    {"as_of": AS_OF, "setups": ["oversold", "oversold"]},
    {"as_of": AS_OF, "setups": "oversold"},
    {"as_of": AS_OF, "setups": [42]},
    {"as_of": AS_OF, "min_price": 0},
    {"as_of": AS_OF, "min_price": -5},
    {"as_of": AS_OF, "min_price": "10"},
    {"as_of": AS_OF, "min_volume": -1},
    {"as_of": AS_OF, "lookback": 0},
    {"as_of": AS_OF, "lookback": -3},
    {"as_of": AS_OF, "lookback": 21.5},
    {"as_of": AS_OF, "lookback": True},
    {"as_of": AS_OF, "surprise_key": 1},
    {"as_of": "2026-13-01"},
    {"as_of": "03 Nov 2026"},
    {"as_of": 20261103},
    {"setups": ["oversold"]},
    {},
    None,
    "oversold",
])
def test_invalid_criteria_are_refused_loudly(spec):
    with pytest.raises(ValueError):
        parse_criteria(spec)
    with pytest.raises(ValueError):
        scan_universe(_universe(), spec)


def test_point_in_time_a_future_bar_cannot_create_a_match():
    # 20 flat bars, then 99, then a later 120 that WOULD break out.
    future = _pairs([100.0] * 20 + [99.0, 120.0])
    universe = {"FUTURE": future}
    # As of the 99 bar, the 120 bar does not exist yet: no breakout,
    # and only 21 bars were used. (oversold DOES match here — RSI-14
    # is 0.0 on the 99 dip, computed from past bars only. That is the
    # point: the early scan sees the dip, not the future rally.)
    early = scan_universe(universe, {"as_of": "2026-10-25"})
    assert _matched(early, universe) == {"FUTURE": {"oversold"}}
    assert "breakout_20d" not in _matched(early, universe)["FUTURE"]
    assert all(r["bars_used"] == 21 for r in early.audit_rows)
    # As of the 120 bar, the same universe DOES break out — the
    # difference is the as-of date, never a peek ahead. (reclaim and
    # the oversold bounce also match now, both from bars on or before
    # 2026-10-26.)
    late = scan_universe(universe, {"as_of": "2026-10-26"})
    assert _matched(late, universe) == {
        "FUTURE": {"breakout_20d", "reclaim_ma20", "oversold_bounce"}}
    assert all(r["bars_used"] == 22 for r in late.audit_rows)


def test_zero_match_scan_still_returns_and_writes_audit_rows(tmp_path):
    # A scan with no matches is still a scan: full audit rows, zero
    # firing rates, and the rows are written to the log file too.
    universe = {"FLAT": _pairs([100.0] * 30)}
    path = tmp_path / "zero-match.jsonl"
    result = scan_universe(universe, {"as_of": AS_OF}, audit_path=path)
    assert result.matches == []
    assert len(result.audit_rows) == len(SETUP_ORDER)
    assert all(not r["matched"] for r in result.audit_rows)
    assert all(r["firing_rate"] == 0 for r in result.audit_rows)
    assert all(r["base_rate"] == 0 for r in result.audit_rows)
    assert path.exists()
    assert read_audit_log(path) == result.audit_rows


def test_scan_with_audit_path_persists_rows(tmp_path):
    path = tmp_path / "scan-audit.jsonl"
    result = scan_universe(_universe(), {"as_of": AS_OF},
                           audit_path=path)
    assert read_audit_log(path) == result.audit_rows
    # A second scan appends, never rewrites the first scan's rows.
    again = scan_universe(_universe(), {"as_of": AS_OF,
                                        "setups": ["oversold"]},
                          audit_path=path)
    rows = read_audit_log(path)
    assert len(rows) == len(result.audit_rows) + len(again.audit_rows)
    assert rows[:len(result.audit_rows)] == result.audit_rows
    assert rows[len(result.audit_rows):] == again.audit_rows


def test_audit_log_jsonl_roundtrip_is_append_only(tmp_path):
    result = scan_universe(_universe(), {"as_of": AS_OF})
    path = tmp_path / "scan-audit.jsonl"
    assert append_audit_log(path, result.audit_rows) == len(
        result.audit_rows)
    assert append_audit_log(path, result.audit_rows) == len(
        result.audit_rows)
    rows = read_audit_log(path)
    assert len(rows) == 2 * len(result.audit_rows)
    assert rows[:len(result.audit_rows)] == result.audit_rows
    assert rows[len(result.audit_rows):] == result.audit_rows
    # The stored rows reproduce the scan exactly: same criteria,
    # same matches.
    stored_matches = {(r["symbol"], r["setup"]) for r in rows
                      if r["matched"]}
    assert stored_matches == {(m["symbol"], m["setup"])
                              for m in result.matches}
    with pytest.raises(FileNotFoundError):
        read_audit_log(tmp_path / "does-not-exist.jsonl")
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"symbol": "BREAK"}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match="malformed audit-log line"):
        read_audit_log(bad)
    with pytest.raises(ValueError):
        append_audit_log(path, ["not a dict"])


def test_broken_series_and_volumes_are_refused_loudly():
    universe = _universe()
    with pytest.raises(ValueError):
        scan_universe({}, {"as_of": AS_OF})
    with pytest.raises(ValueError):
        scan_universe({"EMPTY": []}, {"as_of": AS_OF})
    with pytest.raises(ValueError):
        scan_universe({"BAD": [("2026-10-06", 100.0),
                               ("2026-10-05", 101.0)]},
                      {"as_of": AS_OF})
    # No bar on or before as_of: the symbol cannot be scanned as of
    # that date, and the scan says so instead of scanning nothing.
    with pytest.raises(ValueError, match="no bar on or before"):
        scan_universe({"LATE": _pairs([100.0, 101.0],
                                      start=datetime.date(2026, 11, 10))},
                      {"as_of": AS_OF})
    # Volumes for an unknown symbol, and volumes that do not align
    # one-to-one with the symbol's price bars.
    with pytest.raises(ValueError, match="unknown symbol"):
        scan_universe(universe, {"as_of": AS_OF},
                      volumes={"GHOST": [("2026-10-05", 1.0)]})
    with pytest.raises(ValueError, match="must match"):
        scan_universe(universe, {"as_of": AS_OF},
                      volumes={"BREAK": [("2026-10-05", 1.0)]})
    with pytest.raises(ValueError):
        scan_universe(universe, {"as_of": AS_OF},
                      volumes={"BREAK": [(d, -5.0)
                                         for d, _c in universe["BREAK"]]})


def test_parse_criteria_defaults_and_canonical_form():
    criteria = parse_criteria({"as_of": AS_OF})
    assert criteria.setups == tuple(SETUP_ORDER)
    assert criteria.min_price is None
    assert criteria.lookback is None
    assert criteria.min_volume is None
    assert criteria.to_dict() == {
        "as_of": AS_OF,
        "setups": list(SETUP_ORDER),
        "min_price": None,
        "lookback": None,
        "min_volume": None,
    }
    assert parse_criteria({"as_of": AS_OF, "min_price": 10}).min_price \
        == 10.0
    # A ScreenerCriteria passes straight through scan_universe.
    result = scan_universe(_universe(), criteria)
    assert _matched(result) == EXPECTED
