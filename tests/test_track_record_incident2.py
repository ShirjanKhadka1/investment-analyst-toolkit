"""Track-record incident 2 (November 2026): the runner, not the ledger.

Run #9 (18 Nov) and run #13 (22 Nov) FAILED on a horizon-end
ValueError from ``settle_call``; run #10 (19 Nov) logged fresh calls
for NIFTYBEES.NS, ISF.L and BTC-USD while their 14 Nov calls were
still unsettled — because a settlement attempt had recorded a missed
day and the runner then logged anyway — and logged them from bars
two days stale (entry bar 17 Nov). The live log on the data branch
shows the chain: 14 Nov calls with bar 12 Nov (NIFTYBEES.NS, ISF.L)
whose settlement-date bar was missing on 19 Nov, followed by new
19 Nov calls on the same tickers.

Covered here, all offline (canned fetch, no network):
(a) a not-yet-due horizon is a normal no-op — the run completes
    SUCCESS and the call settles on a later run;
(b) no new call is ever logged while a counting call is open, even
    after a missed-day settlement attempt;
(c) a call is logged only from the latest completed trading bar —
    stale bars log nothing and record a missed day;
(d) the summary reports the run start time and each symbol's last
    bar date.
"""
import importlib.util
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from toolkit.decision_log import load_calls, write_call

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_daily_script():
    """Load scripts/track_record_daily.py by path (not on sys.path)."""
    spec = importlib.util.spec_from_file_location(
        "track_record_daily",
        REPO_ROOT / "scripts" / "track_record_daily.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_daily = _load_daily_script()
run_day = _daily.run_day


# --- fixtures ------------------------------------------------------------

def _dates_through(end, daily=False):
    """Weekday dates (or every day for ``daily``) from 2026-08-10."""
    out = []
    day = date(2026, 8, 10)
    end = date.fromisoformat(str(end))
    while day <= end:
        if daily or day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _parse(value):
    return date.fromisoformat(str(value))


def _pairs(dates, start_close=100.0, step=0.5):
    return [(d.isoformat(), start_close + i * step)
            for i, d in enumerate(dates)]


def _all_ticker_series(end, daily_for=()):
    """Rising weekday series for all 7 tickers, ending ``end``.

    ``daily_for`` names tickers whose series runs every day
    (crypto/FX trade weekends) instead of weekdays only.
    """
    out = {}
    for symbol in ("SPY", "AAPL", "VT", "NIFTYBEES.NS", "ISF.L",
                   "EURUSD=X", "BTC-USD"):
        dates = _dates_through(end, daily=symbol in daily_for)
        out[symbol] = _pairs(dates)
    return out


def _index_series(end):
    dates = _dates_through(end)
    return {"^GSPC": _pairs(dates, 5000.0, 5.0),
            "^NSEI": _pairs(dates, 25000.0, 25.0),
            "^FTSE": _pairs(dates, 9000.0, 9.0)}


def _fake_fetch_factory(series_by_symbol, fail_for=()):
    """``fetch(symbol, kind)`` returning canned (date, close) pairs."""
    def fetch(symbol, kind="stock"):
        if symbol in fail_for:
            raise RuntimeError(f"simulated fetch failure for {symbol}")
        if symbol not in series_by_symbol:
            raise RuntimeError(f"no fixture series for {symbol}")
        return {"pairs": list(series_by_symbol[symbol]),
                "source": "yahoo", "symbol": symbol}
    return fetch


def _at(hour, minute=0, day="2026-11-06"):
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00+00:00")


def _paths(tmp_path):
    """One set of outputs per test: every run in a test loads the
    previous run's calls, exactly like the deployed daily runs."""
    return {
        "calls": tmp_path / "calls.jsonl",
        "out": tmp_path / "track-record.md",
        "missed": tmp_path / "missed_days.jsonl",
        "bars": tmp_path / "bars",
    }


def _run(paths, as_of, series, now, fail_for=()):
    """One simulated run; returns (summary, calls path, missed path)."""
    fetch = _fake_fetch_factory({**series, **_index_series(as_of)},
                                fail_for)
    summary = run_day(paths["calls"], paths["out"], paths["bars"],
                      paths["missed"], as_of, fetch=fetch, now=now)
    return summary, paths["calls"], paths["missed"]


def _missed_records(missed):
    if not missed.exists():
        return []
    return [json.loads(line) for line in
            missed.read_text(encoding="utf-8").splitlines()
            if line.strip()]


# --- (a) not-yet-due horizon is a no-op -----------------------------------

def test_not_yet_due_horizon_completes_success(tmp_path):
    """Run #13 replay: a BTC call dated 19 Nov (bar 17 Nov) whose
    trading-day settlement date (22 Nov) arrives before its
    calendar-day horizon end (24 Nov). The old runner raised the
    horizon-end ValueError and the whole run FAILED; the fixed
    runner treats the call as not due, completes SUCCESS, and the
    call stays open for a later run.
    """
    paths = _paths(tmp_path)
    calls = paths["calls"]
    # The 19 Nov call exactly as run #10 logged it (bar 17 Nov).
    seeded = write_call(calls, {
        "date": "2026-11-19", "symbol": "BTC-USD",
        "exchange": "crypto (venue-agnostic)", "profile": "crypto",
        "persona": "crypto", "rule_id": "sma50_trend",
        "rule_version": "1.0.0", "rule_code_hash": "0" * 64,
        "expected": "up", "horizon_days": 5, "entry_close": 90000.0,
        "bar_date": "2026-11-17", "bar_complete": True,
        "provisional": False, "confidence": 0.5,
        "confidence_basis": "incident 2 fixture call",
        "sources": ["yahoo"], "thesis": "incident 2 fixture call",
        "prev_5d_return": 0.01})
    # Daily BTC series (crypto trades weekends): the 22 Nov bar
    # exists, so the settlement is genuinely attempted — and the
    # horizon-end ValueError is what the old code raised.
    series = _all_ticker_series("2026-11-20", daily_for=("BTC-USD",))
    series["BTC-USD"] = _pairs(_dates_through("2026-11-22", daily=True),
                               90000.0, 50.0)
    summary, _, missed = _run(paths, "2026-11-22", series,
                              _at(22, day="2026-11-22"))
    # The run completed (run_day returned instead of raising).
    assert summary["calls_settled"] == 0
    # Six log: the five equities plus EURUSD=X (FX trades Mon-Fri,
    # so its Friday 20 Nov bar is the latest completed bar on a
    # Sunday run). BTC-USD logs nothing — its 19 Nov call is open.
    assert summary["calls_logged"] == 6
    btc = [c for c in load_calls(calls) if c["symbol"] == "BTC-USD"]
    assert len(btc) == 1
    assert btc[0]["call_id"] == seeded["call_id"]
    assert btc[0]["status"] == "open"  # not due, not settled, not lost
    assert "settled_date" not in btc[0]
    # Not due is not a missed day: nothing is recorded for BTC.
    assert [r for r in _missed_records(missed)
            if r["symbol"] == "BTC-USD"] == []


# --- (c) staleness gate: incident mirror ----------------------------------

def test_stale_bars_never_log_a_call(tmp_path):
    """Run #10 replay: on 13 Nov the fetch ends 11 Nov — two days
    early. The old runner logged fresh calls from the 11 Nov bars;
    the fixed runner logs nothing, records missed days, and the run
    succeeds. BTC-USD settles first (its settlement bar exists),
    then the gate blocks its replacement call; every other ticker
    stays open, blocked by the no-overlap rule.
    """
    paths = _paths(tmp_path)
    series = _all_ticker_series("2026-11-06")
    summary, calls, _ = _run(paths, "2026-11-06", series,
                             _at(22, day="2026-11-06"))
    assert summary["calls_logged"] == 7

    stale = _all_ticker_series("2026-11-11")
    summary, _, missed = _run(paths, "2026-11-13", stale,
                              _at(22, day="2026-11-13"))
    assert summary["calls_logged"] == 0
    # Only BTC-USD settles: crypto trades every day, so its
    # settlement bar (10 Nov) exists. EURUSD=X cannot settle yet —
    # FX trades Mon-Fri, so its settlement day is 12 Nov and there
    # is no 12 Nov bar — and the five equities cannot settle either
    # (no 13 Nov bar). All six stay open.
    assert summary["calls_settled"] == 1
    assert len(load_calls(calls)) == 7
    assert [c["date"] for c in load_calls(calls)] == \
        ["2026-11-06"] * 7  # no call dated 13 Nov anywhere
    records = [r for r in _missed_records(missed)
               if r["date"] == "2026-11-13"]
    assert len(records) == 7
    stale_reasons = {r["symbol"] for r in records
                     if "stale bars" in r["reason"]}
    assert stale_reasons == {"BTC-USD"}
    assert {r["symbol"] for r in records
            if "no bar on settlement date" in r["reason"]} == {
        "SPY", "AAPL", "VT", "NIFTYBEES.NS", "ISF.L", "EURUSD=X"}
    # (d) the summary names the stale bars it saw.
    assert summary["run_started_utc"] == "2026-11-13T22:00:00+00:00"
    assert summary["last_bar_date"]["SPY"] == "2026-11-11"
    assert summary["last_bar_date"]["BTC-USD"] == "2026-11-11"


def test_stale_bars_after_settlement_record_missed_day(tmp_path):
    """The gate's own missed-day record: prior calls settle
    cleanly, the replacement call is due, but the bars end two
    trading days early (20 Nov vs 23 Nov) — so nothing is logged
    from stale bars and a 'stale bars' missed day is recorded.
    (EURUSD=X is the exception that proves the rule: FX trades
    Mon-Fri, so its Friday 20 Nov bar IS the latest completed bar
    on Monday 23 Nov, and its replacement call logs normally.)
    """
    paths = _paths(tmp_path)
    series = _all_ticker_series("2026-11-06")
    _run(paths, "2026-11-06", series, _at(22, day="2026-11-06"))
    fresh = _all_ticker_series("2026-11-13")
    summary, _, _ = _run(paths, "2026-11-13", fresh,
                         _at(22, day="2026-11-13"))
    assert summary["calls_settled"] == 7
    assert summary["calls_logged"] == 7

    stale = _all_ticker_series("2026-11-20")
    summary, calls, missed = _run(
        paths, "2026-11-23", stale, _at(22, day="2026-11-23"))
    assert summary["calls_settled"] == 7  # the 13 Nov calls settle
    # One replacement logs (EURUSD=X, fresh Friday bar); the other
    # six are blocked by the staleness gate.
    assert summary["calls_logged"] == 1
    assert len(load_calls(calls)) == 15
    dated_23 = [c for c in load_calls(calls)
                if c["date"] == "2026-11-23"]
    assert [c["symbol"] for c in dated_23] == ["EURUSD=X"]
    records = [r for r in _missed_records(missed)
               if r["date"] == "2026-11-23"]
    assert len(records) == 6
    assert all("stale bars" in r["reason"] for r in records)
    assert {r["symbol"] for r in records} == {
        "SPY", "AAPL", "VT", "NIFTYBEES.NS", "ISF.L", "BTC-USD"}


# --- (2) settle after a missed day ----------------------------------------

def test_settle_after_missed_day(tmp_path):
    """A call whose settlement bar is missing on one run settles on
    a later run once the bar is available — the missed day is never
    backfilled, and the settlement uses the settlement-date bar.
    """
    paths = _paths(tmp_path)
    series = _all_ticker_series("2026-11-06")
    _run(paths, "2026-11-06", series, _at(22, day="2026-11-06"))
    # 13 Nov: no 13 Nov bar, so the 6 Nov SPY call cannot settle.
    # (BTC-USD and EURUSD=X settle here and log fresh calls — their
    # 12 Nov bar is the latest completed bar on 13 Nov — but SPY
    # logs nothing: its call is still open.)
    stale = _all_ticker_series("2026-11-12")
    summary, calls, missed = _run(paths, "2026-11-13", stale,
                                  _at(22, day="2026-11-13"))
    assert summary["calls_logged"] == 2
    spy = [c for c in load_calls(calls) if c["symbol"] == "SPY"]
    assert len(spy) == 1 and spy[0]["status"] == "open"
    assert {"date": "2026-11-13", "symbol": "SPY",
            "reason": "no bar on settlement date 2026-11-13"} \
        in _missed_records(missed)
    # 16 Nov: the 13 Nov bar is now available — the call settles.
    fresh = _all_ticker_series("2026-11-13")
    summary, calls, _ = _run(paths, "2026-11-16", fresh,
                             _at(22, day="2026-11-16"))
    spy = [c for c in load_calls(calls) if c["symbol"] == "SPY"]
    assert len(spy) == 1  # settled, and no replacement from stale bars
    assert spy[0]["status"] == "settled"
    assert spy[0]["settled_date"] == "2026-11-16"
    assert spy[0]["hit"] is True  # rising series, UP call
    assert summary["calls_settled"] == 5  # the five equity calls
    assert summary["calls_logged"] == 0  # replacements blocked: stale


# --- (b) no overlap, even after a missed-day settlement attempt -----------

def test_no_overlap_after_missed_day_settlement_attempt(tmp_path):
    """Run #10 replay, end to end: a settlement attempt records a
    missed day, and the runner must still log nothing for that
    ticker while its call is unsettled. A replacement is due only
    after the earlier call settles — and then only from fresh bars.
    """
    # _call_due, unit level: an open call blocks a replacement even
    # when its due date has passed; the same call settled does not.
    open_call = {"symbol": "SPY", "status": "open",
                 "bar_date": "2026-11-06", "date": "2026-11-06"}
    observed = frozenset(d.isoformat()
                         for d in _dates_through("2026-11-13"))
    assert _daily._call_due([open_call], "SPY", date(2026, 11, 13),
                            "us", observed) is False
    settled_call = dict(open_call, status="settled")
    assert _daily._call_due([settled_call], "SPY", date(2026, 11, 13),
                            "us", observed) is True

    paths = _paths(tmp_path)
    series = _all_ticker_series("2026-11-06")
    _run(paths, "2026-11-06", series, _at(22, day="2026-11-06"))
    # 13 Nov, bars through 11 Nov: settlement misses, no new calls.
    stale = _all_ticker_series("2026-11-11")
    summary, calls, missed = _run(paths, "2026-11-13", stale,
                                  _at(22, day="2026-11-13"))
    assert summary["calls_logged"] == 0
    assert _missed_records(missed) != []
    for symbol in ("SPY", "NIFTYBEES.NS", "ISF.L"):
        mine = [c for c in load_calls(calls) if c["symbol"] == symbol]
        assert len(mine) == 1 and mine[0]["status"] == "open"
    # 20 Nov, bars through 20 Nov: the 6 Nov calls settle and fresh
    # replacements log — exactly one open call per ticker, no more.
    fresh = _all_ticker_series("2026-11-20")
    summary, calls, _ = _run(paths, "2026-11-20", fresh,
                             _at(22, day="2026-11-20"))
    assert summary["calls_settled"] >= 5
    assert summary["calls_logged"] == 7
    for symbol in ("SPY", "AAPL", "VT", "NIFTYBEES.NS", "ISF.L",
                   "EURUSD=X", "BTC-USD"):
        mine = [c for c in load_calls(calls) if c["symbol"] == symbol]
        assert len(mine) == 2
        assert sum(1 for c in mine if c["status"] == "open") == 1
        assert sum(1 for c in mine if c["status"] == "settled") == 1


# --- (d) summary: run start + last bar date per symbol --------------------

def test_summary_reports_run_start_and_last_bar_dates(tmp_path):
    """The summary carries the run start time (UTC) and, per
    symbol, the last bar date the fetch returned — None when the
    fetch failed, so a stale-bars missed day is diagnosable from
    the summary alone.
    """
    paths = _paths(tmp_path)
    series = _all_ticker_series("2026-11-06")
    summary, _, _ = _run(paths, "2026-11-06", series,
                         _at(22, day="2026-11-06"))
    assert summary["run_started_utc"] == "2026-11-06T22:00:00+00:00"
    assert summary["last_bar_date"] == {
        symbol: "2026-11-06" for symbol in
        ("SPY", "AAPL", "VT", "NIFTYBEES.NS", "ISF.L",
         "EURUSD=X", "BTC-USD")}
    summary, _, _ = _run(paths, "2026-11-06", series,
                         _at(22, day="2026-11-06"),
                         fail_for=("ISF.L",))
    assert summary["last_bar_date"]["ISF.L"] is None
    assert summary["last_bar_date"]["SPY"] == "2026-11-06"


# --- exclusion skip: an excluded call is never attempted ------------------

def test_excluded_call_is_skipped_not_failed(tmp_path):
    """Incident 2 integration fix: the 19 Nov calls become due on
    24 Nov, and all three are excluded — ``settle_call`` refuses
    them. The runner must skip an excluded call outright (never
    attempt settlement, never a missed day) instead of failing
    the whole run on the refusal ValueError.
    """
    # Seed the 19 Nov BTC call exactly as run #10 logged it.
    excluded_paths = _paths(tmp_path / "excluded")
    control_paths = _paths(tmp_path / "control")
    seeded = {}
    for paths in (excluded_paths, control_paths):
        seeded[paths["calls"]] = write_call(paths["calls"], {
            "date": "2026-11-19", "symbol": "BTC-USD",
            "exchange": "crypto (venue-agnostic)", "profile": "crypto",
            "persona": "crypto", "rule_id": "mom20_sign",
            "rule_version": "1.0.0", "rule_code_hash": "0" * 64,
            "expected": "down", "horizon_days": 5,
            "entry_close": 64893.9453125,
            "bar_date": "2026-11-17", "bar_complete": True,
            "provisional": False, "confidence": 0.5,
            "confidence_basis": "incident 2 fixture call",
            "sources": ["yahoo"], "thesis": "incident 2 fixture call",
            "prev_5d_return": -0.01})
    # 24 Nov run: fresh bars through 24 Nov, so the 22 Nov
    # settlement bar exists and the horizon end (24 Nov) is reached.
    series = _all_ticker_series("2026-11-24", daily_for=("BTC-USD",))
    fetch = _fake_fetch_factory({**series, **_index_series("2026-11-24")})
    # Excluded: the run completes, the call stays open, nothing is
    # recorded for BTC — no settlement, no missed day.
    excluded_id = seeded[excluded_paths["calls"]]["call_id"]
    run_day(excluded_paths["calls"], excluded_paths["out"],
            excluded_paths["bars"], excluded_paths["missed"],
            "2026-11-24", fetch=fetch, now=_at(22, day="2026-11-24"),
            excluded_call_ids={excluded_id})
    btc = [c for c in load_calls(excluded_paths["calls"])
           if c["symbol"] == "BTC-USD"]
    assert len(btc) == 1
    assert btc[0]["call_id"] == excluded_id
    assert btc[0]["status"] == "open"
    assert "settled_date" not in btc[0]
    assert [r for r in _missed_records(excluded_paths["missed"])
            if r["symbol"] == "BTC-USD"] == []
    # Control: the same call, not excluded, settles on this run —
    # the skip (not the data) is what kept it open above.
    control_id = seeded[control_paths["calls"]]["call_id"]
    run_day(control_paths["calls"], control_paths["out"],
            control_paths["bars"], control_paths["missed"],
            "2026-11-24", fetch=fetch, now=_at(22, day="2026-11-24"))
    mine = [c for c in load_calls(control_paths["calls"])
            if c["call_id"] == control_id]
    assert len(mine) == 1
    assert mine[0]["status"] == "settled"
    assert mine[0]["settled_date"] == "2026-11-24"
