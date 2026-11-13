"""Tests for the track-record engine (Round 4).

Covered on fixtures: Wilson intervals, per-ticker + pooled scoring,
the 100-call headline rule, the look-ahead rule (a future bar never
leaks into a direction), idempotency, settlement correctness, the
missed-day rule, and the special session (NIFTYBEES.NS 2026-11-08).
"""
import importlib.util
import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from toolkit.decision_log import load_calls, update_call, write_call
from toolkit.track_record import (
    headline_allowed,
    render_track_record_md,
    score_settled,
    wilson_interval,
)
from toolkit.trading_calendar import settlement_date

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_DOC = REPO_ROOT / "docs" / "watchlist-proposal.md"


def _load_daily_script():
    """Load scripts/track_record_daily.py by path (not on sys.path)."""
    spec = importlib.util.spec_from_file_location(
        "track_record_daily",
        REPO_ROOT / "scripts" / "track_record_daily.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_daily = _load_daily_script()
EXCHANGE_LABELS = _daily.EXCHANGE_LABELS
PERSONAS = _daily.PERSONAS
run_day = _daily.run_day


# --- fixtures ------------------------------------------------------------

def _dates_through(end, skip=(), extra=()):
    """Weekday dates from 2026-08-10 through ``end``.

    ``skip`` dates are omitted (holidays); ``extra`` dates are added
    (special sessions) and the result is sorted.
    """
    out = []
    day = date(2026, 8, 10)
    while day <= _parse(end):
        if day.weekday() < 5 and day.isoformat() not in skip:
            out.append(day)
        day += timedelta(days=1)
    out.extend(_parse(d) for d in extra)
    return sorted(set(out))


def _parse(value):
    return date.fromisoformat(str(value))


def _pairs(dates, start_close=100.0, step=0.5):
    return [(d.isoformat(), start_close + i * step)
            for i, d in enumerate(dates)]


def _settled(symbol, hit, naive_hit=True, bnh_hit=True):
    return {"symbol": symbol, "status": "settled", "hit": hit,
            "naive_hit": naive_hit, "bnh_hit": bnh_hit}


# --- Wilson + scoring ------------------------------------------------------

def test_wilson_interval_known_values():
    low, high = wilson_interval(60, 100)
    assert abs(low - 0.5020) < 0.001
    assert abs(high - 0.6906) < 0.001
    assert wilson_interval(0, 0) == (None, None)
    assert abs(wilson_interval(0, 10)[0]) < 1e-9
    assert abs(wilson_interval(10, 10)[1] - 1.0) < 1e-9
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_score_settled_per_ticker_and_pooled():
    calls = [_settled("SPY", True)] * 6 + [_settled("SPY", False)] * 4 \
        + [_settled("AAPL", True)] * 8 + [_settled("AAPL", False)] * 2
    scores = score_settled(calls)
    assert scores["pooled"]["model"]["n"] == 20
    assert scores["pooled"]["model"]["hits"] == 14
    assert scores["per_ticker"]["SPY"]["model"]["n"] == 10
    assert scores["per_ticker"]["AAPL"]["model"]["hits"] == 8
    assert scores["pooled"]["naive"]["n"] == 20


def test_headline_withheld_below_100_and_shown_at_100():
    assert headline_allowed(99) is False
    assert headline_allowed(100) is True
    calls = [_settled("SPY", True)] * 99
    page = render_track_record_md(calls)
    assert "withheld until 100 settled calls" in page
    assert "Pooled hit rate (headline)" not in page
    calls = [_settled("SPY", True)] * 100
    page = render_track_record_md(calls)
    assert "Pooled hit rate (headline)" in page
    assert "100.0%" in page


def test_render_states_the_correlation_caveat():
    page = render_track_record_md([_settled("SPY", True)])
    assert "Tickers are correlated" in page
    assert "understate" in page


# --- update_call -----------------------------------------------------------

def test_update_call_baselines_only_on_settled(tmp_path):
    path = tmp_path / "calls.jsonl"
    call = write_call(path, {
        "date": "2026-11-06", "symbol": "SPY", "exchange": "NYSE Arca",
        "thesis": "t", "horizon_days": 5, "expected": "up",
        "sources": ["yahoo"], "confidence": 0.5})
    with pytest.raises(ValueError):
        update_call(path, call["call_id"], naive_hit=True)
    with pytest.raises(ValueError):
        update_call(path, call["call_id"], expected="down")
    with pytest.raises(ValueError):
        update_call(path, "no-such-id", naive_hit=True)


# --- run_day on fixtures ---------------------------------------------------

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


def _all_ticker_series(end, bars=60):
    """Rising weekday series for all 7 tickers, ending ``end``."""
    dates = _dates_through(end)
    return {symbol: _pairs(dates) for symbol in
            ("SPY", "AAPL", "VT", "NIFTYBEES.NS", "ISF.L",
             "EURUSD=X", "BTC-USD")}


def _index_series(end):
    dates = _dates_through(end)
    return {"^GSPC": _pairs(dates, 5000.0, 5.0),
            "^NSEI": _pairs(dates, 25000.0, 25.0),
            "^FTSE": _pairs(dates, 9000.0, 9.0)}


def _run(tmp_path, as_of, series, fail_for=()):
    calls = tmp_path / "calls.jsonl"
    out = tmp_path / "track-record.md"
    missed = tmp_path / "missed_days.jsonl"
    bars_dir = tmp_path / "bars"
    fetch = _fake_fetch_factory({**series, **_index_series(as_of)},
                                fail_for)
    summary = run_day(calls, out, bars_dir, missed, as_of, fetch=fetch)
    return summary, calls, out, missed


def test_look_ahead_future_bar_never_leaks(tmp_path):
    """A bar dated after the decision date cannot change a direction.

    The fixture's SPY series runs to 2026-11-09 with a crash on the
    9th (close 1.0). Decided on 2026-11-06, the rule must see only
    bars to the 6th — the crash must not flip the call.
    """
    dates = _dates_through("2026-11-06")
    spy = _pairs(dates, 100.0, 0.5)
    spy.append(("2026-11-09", 1.0))  # future crash bar
    series = _all_ticker_series("2026-11-06")
    series["SPY"] = spy
    _summary, calls, _, _ = _run(tmp_path, "2026-11-06", series)
    logged = [c for c in load_calls(calls) if c["symbol"] == "SPY"]
    assert len(logged) == 1
    assert logged[0]["expected"] == "up"  # rising to the 6th, crash unseen
    assert logged[0]["entry_close"] == spy[-2][1]


def test_recorded_source_follows_the_actual_fetch(tmp_path):
    """A CoinGecko fetch must not be recorded as Yahoo."""
    series = _all_ticker_series("2026-11-06")

    def fetch(symbol, kind="stock"):
        source = "coingecko" if symbol == "BTC-USD" else "yahoo"
        return {"pairs": list(series[symbol]),
                "source": source, "symbol": symbol}

    calls = tmp_path / "calls.jsonl"
    run_day(calls, tmp_path / "track-record.md", tmp_path / "bars",
            tmp_path / "missed_days.jsonl", "2026-11-06", fetch=fetch)
    logged = {c["symbol"]: c for c in load_calls(calls)}
    assert logged["BTC-USD"]["sources"] == ["coingecko"]
    assert logged["SPY"]["sources"] == ["yahoo"]


def test_idempotent_rerun_never_duplicates(tmp_path):
    series = _all_ticker_series("2026-11-06")
    summary, calls, _, _ = _run(tmp_path, "2026-11-06", series)
    assert summary["calls_logged"] == 7
    summary2, _, _, _ = _run(tmp_path, "2026-11-06", series)
    assert summary2["calls_logged"] == 0
    assert len(load_calls(calls)) == 7


def test_settlement_correctness_and_chained_call(tmp_path):
    """Nov 6 call settles Nov 13; a new call is due the same day."""
    series = _all_ticker_series("2026-11-13")
    _run(tmp_path, "2026-11-06", series)
    summary, calls, _, _ = _run(tmp_path, "2026-11-13", series)
    assert summary["calls_settled"] >= 1
    spy = [c for c in load_calls(calls) if c["symbol"] == "SPY"]
    settled = [c for c in spy if c["status"] == "settled"]
    assert len(settled) == 1
    assert settled[0]["date"] == "2026-11-06"
    assert settled[0]["settled_date"] == "2026-11-13"
    assert settled[0]["hit"] is True  # rising series, UP call
    assert settled[0]["naive_hit"] is True
    assert settled[0]["bnh_hit"] is True
    assert len(spy) == 2  # settled Nov 6 call + new Nov 13 call


def test_missed_day_recorded_and_never_backfilled(tmp_path):
    series = _all_ticker_series("2026-11-06")
    summary, calls, _, missed = _run(
        tmp_path, "2026-11-06", series, fail_for=("ISF.L",))
    assert summary["missed_days"] == 1
    assert [c for c in load_calls(calls)
            if c["symbol"] == "ISF.L"] == []
    records = [json.loads(line) for line in
               missed.read_text(encoding="utf-8").splitlines()]
    assert records[0]["symbol"] == "ISF.L"
    assert records[0]["date"] == "2026-11-06"
    # Next day the fetch works: the call is dated the 9th, not the 6th.
    _summary2, _, _, _ = _run(tmp_path, "2026-11-09", series)
    isf = [c for c in load_calls(calls) if c["symbol"] == "ISF.L"]
    assert len(isf) == 1 and isf[0]["date"] == "2026-11-09"


def test_run_day_writes_empty_missed_file_when_zero_missed(tmp_path):
    """Zero missed days must still leave a missed_days.jsonl behind.

    Regression: the deployed first run (2026-11-13) logged 7 calls
    with 0 missed days, but no missed_days.jsonl was ever written —
    and publish() then died copying a file that did not exist.
    """
    series = _all_ticker_series("2026-11-06")
    summary, _calls, _out, missed = _run(tmp_path, "2026-11-06", series)
    assert summary["calls_logged"] == 7
    assert summary["missed_days"] == 0
    assert missed.exists()
    assert missed.read_text(encoding="utf-8") == ""


def _make_git_repo(tmp_path):
    """A temp repo with a local bare origin, ready for publish()."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True,
                   capture_output=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True,
                   capture_output=True)
    for key, value in (("user.name", "test"),
                       ("user.email", "test@example.invalid")):
        subprocess.run(["git", "-C", str(repo), "config", key, value],
                       check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    str(origin)], check=True, capture_output=True)
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "push", "origin", "main"],
                   check=True, capture_output=True)
    return repo, origin


def _origin_file(origin, branch, name):
    proc = subprocess.run(
        ["git", "--git-dir", str(origin), "show", f"{branch}:{name}"],
        capture_output=True, text=True, check=True)
    return proc.stdout


def test_publish_first_run_with_no_missed_days_file(tmp_path):
    """First run, zero missed days, no missed_days.jsonl: publish
    succeeds, creates data/track-record, commits all three files.

    Reproduces the 2026-11-13 deployed failure (run
    43413814636): publish() copied missed_days.jsonl unconditionally
    and `cp` failed because the file was never written. The file is
    deleted here after run_day to simulate the pre-fix output.
    """
    series = _all_ticker_series("2026-11-06")
    summary, calls, out, missed = _run(tmp_path, "2026-11-06", series)
    assert summary["calls_logged"] == 7 and summary["missed_days"] == 0
    missed.unlink()  # pre-fix output: file never written
    repo, origin = _make_git_repo(tmp_path)
    message = _daily.publish([calls, out, missed], "data/track-record",
                             repo_root=repo)
    assert message is not None
    assert message.startswith("Daily track record: ")
    listing = subprocess.run(
        ["git", "--git-dir", str(origin), "ls-tree", "--name-only",
         "data/track-record"], capture_output=True, text=True,
        check=True).stdout.splitlines()
    # The branch is created from HEAD, so it also carries the repo
    # tree; the three published outputs must all be present in it.
    assert {"calls.jsonl", "missed_days.jsonl",
            "track-record.md"} <= set(listing)
    assert _origin_file(origin, "data/track-record",
                        "missed_days.jsonl") == ""
    assert len(_origin_file(origin, "data/track-record",
                            "calls.jsonl").splitlines()) == 7


def test_publish_refuses_a_missing_calls_log(tmp_path):
    """Precision guard: only missed_days.jsonl may be absent.

    A genuinely missing calls.jsonl must still fail loudly instead
    of being papered over with an empty file.
    """
    repo, _origin = _make_git_repo(tmp_path)
    out = tmp_path / "track-record.md"
    out.write_text("# track record\n", encoding="utf-8")
    missed = tmp_path / "missed_days.jsonl"
    missed.write_text("", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="calls.jsonl"):
        _daily.publish([tmp_path / "calls.jsonl", out, missed],
                       "data/track-record", repo_root=repo)


def test_special_session_settles_on_13_november(tmp_path):
    """NIFTYBEES.NS: the 2026-11-08 Muhurat bar counts (see spec)."""
    dates = _dates_through("2026-11-13", skip=("2026-11-10",),
                           extra=("2026-11-08",))
    assert date(2026, 11, 8) in dates and date(2026, 11, 10) not in dates
    series = _all_ticker_series("2026-11-06")
    series["NIFTYBEES.NS"] = _pairs(dates)
    _run(tmp_path, "2026-11-06", series)
    nifty = [c for c in load_calls(tmp_path / "calls.jsonl")
             if c["symbol"] == "NIFTYBEES.NS"]
    assert len(nifty) == 1
    # The settlement date the engine computes for this call:
    observed = frozenset(d.isoformat() for d in dates)
    assert settlement_date("india", "2026-11-06", 5,
                           observed) == date(2026, 11, 13)
    _summary, calls, _, _ = _run(tmp_path, "2026-11-13", series)
    settled = [c for c in load_calls(calls)
               if c["symbol"] == "NIFTYBEES.NS"
               and c["status"] == "settled"]
    assert len(settled) == 1
    assert settled[0]["settled_date"] == "2026-11-13"


def test_script_tables_match_the_watchlist_doc():
    """EXCHANGE_LABELS/PERSONAS agree with the doc table."""
    lines = WATCHLIST_DOC.read_text(encoding="utf-8").splitlines()
    header_idx = next(
        i for i, line in enumerate(lines)
        if line.startswith("|") and "Ticker" in line
        and "Exchange" in line and "Persona" in line)
    header = [c.strip() for c in lines[header_idx].strip(" |").split("|")]
    ticker_col = header.index("Ticker")
    exchange_col = header.index("Exchange")
    persona_col = header.index("Persona")
    table = {}
    for line in lines[header_idx + 2:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        table[cells[ticker_col]] = (cells[exchange_col], cells[persona_col])
    assert set(table) == set(EXCHANGE_LABELS) == set(PERSONAS)
    for symbol, (exchange, persona) in table.items():
        assert EXCHANGE_LABELS[symbol] == exchange
        assert PERSONAS[symbol] == persona
