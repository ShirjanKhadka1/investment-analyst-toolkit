"""Forward volatility ledger (Round E, E.2): module + runner + publish.

The ledger is append-only and hash-chained (same chain as the
track record), forecasts exist only for complete bars, a forecast
is scored at most once, and a second run APPENDS — it never
overwrites. These tests run three consecutive simulated runs
plus publishing, against a local bare origin — never the real
data branch. No network: every price series is deterministic
fixture data.
"""
import importlib.util
import json
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from toolkit import volatility_ledger as vl
from toolkit.decision_log import GENESIS_PREV_HASH, row_hash
from toolkit.volatility import qlike

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_daily = _load_module("volatility_ledger_daily",
                      REPO_ROOT / "scripts" / "volatility_ledger_daily.py")
_verify = _load_module(
    "verify_volatility_ledger",
    REPO_ROOT / "scripts" / "verify_volatility_ledger.py")
run_day = _daily.run_day
RefusedRunError = _daily.RefusedRunError


def _forecast(symbol="SPY", origin_date="2026-11-13", model="ewma",
              variance=0.0004):
    return {
        "symbol": symbol,
        "forecast_date": "2026-11-14",
        "origin_date": origin_date,
        "origin_close": 100.0,
        "model": model,
        "forecast_variance": variance,
        "annualised_vol": vl.annualised_vol(variance),
        "bar_complete": True,
        "source": "yahoo",
    }


# --- module: append / score / verify --------------------------------------

def test_append_forecast_chains_from_genesis(tmp_path):
    path = tmp_path / "ledger.jsonl"
    first = vl.append_forecast(path, _forecast())
    assert first["prev_hash"] == GENESIS_PREV_HASH
    assert first["forecast_id"] == vl.forecast_id(first)
    assert first["schema_version"] == "1.0.0"
    second = vl.append_forecast(path, _forecast(model="naive"))
    assert second["prev_hash"] == row_hash(first)
    assert second["forecast_id"] != first["forecast_id"]
    assert vl.verify_ledger(path) == []


def test_append_forecast_refuses_incomplete_bar(tmp_path):
    path = tmp_path / "ledger.jsonl"
    bad = _forecast()
    bad["bar_complete"] = False
    with pytest.raises(ValueError, match="bar_complete must be True"):
        vl.append_forecast(path, bad)
    assert not path.exists()


def test_append_forecast_refuses_off_watchlist_and_bad_model(tmp_path):
    path = tmp_path / "ledger.jsonl"
    with pytest.raises(ValueError, match="not on the volatility"):
        vl.append_forecast(path, _forecast(symbol="QQQ"))
    with pytest.raises(ValueError, match="unknown volatility model"):
        vl.append_forecast(path, _forecast(model="garch"))
    with pytest.raises(ValueError, match="forecast_variance must be"):
        vl.append_forecast(path, _forecast(variance=0.0))
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        vl.append_forecast(path, _forecast(origin_date="13 Nov 2026"))


def test_append_score_once_and_unknown_id(tmp_path):
    path = tmp_path / "ledger.jsonl"
    stored = vl.append_forecast(path, _forecast())
    score = vl.score_forecast(stored, 0.0005, "2026-11-16")
    assert score["status"] == "scored"
    assert score["qlike"] == qlike(0.0005, 0.0004)
    assert score["actual_date"] == "2026-11-16"
    vl.append_score(path, stored["forecast_id"], score)
    with pytest.raises(ValueError, match="already has a score"):
        vl.append_score(path, stored["forecast_id"], score)
    with pytest.raises(ValueError, match="unknown forecast_id"):
        vl.append_score(path, "deadbeefdeadbeef", score)
    with pytest.raises(ValueError, match="status must be one of"):
        vl.append_score(path, stored["forecast_id"],
                        {**score, "status": "maybe"})
    assert vl.verify_ledger(path) == []


def test_verify_ledger_catches_tamper_orphan_and_duplicate(tmp_path):
    path = tmp_path / "ledger.jsonl"
    first = vl.append_forecast(path, _forecast())
    second = vl.append_forecast(path, _forecast(model="garch_lite"))
    assert second["prev_hash"] == row_hash(first)
    vl.append_score(path, first["forecast_id"],
                    vl.score_forecast(first, 0.0005, "2026-11-16"))
    assert vl.verify_ledger(path) == []
    # Tamper: edit the first row's variance in place.
    rows = vl.load_rows(path)
    rows[0]["forecast_variance"] = 0.0009
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n"
                            for r in rows), encoding="utf-8")
    problems = vl.verify_ledger(path)
    assert any("forecast_id mismatch" in p for p in problems)
    assert any("prev_hash mismatch" in p for p in problems)
    # Orphan score: a score row for an unknown id, appended
    # properly so the chain stays linked.
    orphan = {"schema_version": "1.0.0", "record_type": "score",
              "forecast_id": "0123456789abcdef",
              "actual_date": "2026-11-16", "realised_variance": 0.0005,
              "qlike": -8.0, "mse_variance": 0.0, "status": "scored"}
    vl._append_row(path, orphan)
    problems = vl.verify_ledger(path)
    assert any("unknown forecast_id" in p for p in problems)


def test_verify_ledger_catches_off_watchlist_and_incomplete(tmp_path):
    path = tmp_path / "ledger.jsonl"
    row = {"schema_version": "1.0.0", "record_type": "forecast",
           "symbol": "QQQ", "forecast_date": "2026-11-14",
           "origin_date": "2026-11-13", "origin_close": 100.0,
           "model": "ewma", "forecast_variance": 0.0004,
           "annualised_vol": 0.317, "bar_complete": False,
           "source": "yahoo"}
    vl._append_row(path, row)
    problems = vl.verify_ledger(path)
    assert any("not on the volatility ledger watchlist" in p
               for p in problems)
    assert any("bar_complete is not True" in p for p in problems)


def test_ledger_counts_and_threshold(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    assert vl.MIN_SCORED_PER_SYMBOL == 100
    counts = vl.ledger_counts(path)
    assert counts["scored_total"] == 0
    assert counts["threshold_met"] is False
    monkeypatch.setattr(vl, "MIN_SCORED_PER_SYMBOL", 1)
    for symbol in vl.WATCHLIST:
        stored = vl.append_forecast(path, _forecast(symbol=symbol))
        vl.append_score(path, stored["forecast_id"],
                        vl.score_forecast(stored, 0.0005, "2026-11-16"))
    counts = vl.ledger_counts(path)
    assert counts["scored_total"] == len(vl.WATCHLIST)
    assert counts["threshold_met"] is True
    assert counts["per_symbol"]["SPY"] == {"forecasts": 1, "scored": 1}


def test_annualised_vol_convention():
    assert vl.annualised_vol(0.0004) == pytest.approx(0.317, abs=0.001)
    with pytest.raises(ValueError, match="must be >= 0"):
        vl.annualised_vol(-0.1)


# --- runner: fake deterministic series ------------------------------------

def _weekdays_through(end):
    out = []
    day = date(2025, 11, 3)
    end = date.fromisoformat(str(end))
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _pairs(dates, start_close=100.0, step=0.5):
    return [(d.isoformat(), start_close + i * step)
            for i, d in enumerate(dates)]


def _series(end):
    out = {}
    for symbol, base, step in (
            ("SPY", 100.0, 0.5), ("AAPL", 200.0, 1.0),
            ("VT", 100.0, 0.4), ("NIFTYBEES.NS", 300.0, 1.5),
            ("ISF.L", 800.0, 4.0), ("EURUSD=X", 1.1, 0.001),
            ("BTC-USD", 90000.0, 500.0)):
        out[symbol] = _pairs(_weekdays_through(end), base, step)
    return out


def _fake_fetch(series, end):
    """fetch(symbol, kind) serving bars through ``end`` only."""
    def fetch(symbol, kind="stock"):
        if symbol not in series:
            raise RuntimeError(f"no fixture series for {symbol}")
        pairs = [(d, c) for d, c in series[symbol]
                 if date.fromisoformat(d) <= date.fromisoformat(end)]
        return {"pairs": pairs, "source": "yahoo", "symbol": symbol}
    return fetch


def _at(hour, minute=0, day="2026-11-13"):
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00+00:00")


def _paths(tmp_path):
    return {"ledger": tmp_path / "volatility-ledger.jsonl",
            "missed": tmp_path / "missed_days.jsonl"}


def _run(paths, as_of, series, now):
    return run_day(paths["ledger"], paths["missed"], as_of,
                   fetch=_fake_fetch(series, as_of), now=now)


def test_two_runs_log_then_score_and_append(tmp_path):
    paths = _paths(tmp_path)
    series = _series("2026-11-17")
    # Run 1 — Friday 2026-11-13, 22:00 UTC (after every close).
    summary = _run(paths, "2026-11-13", series, _at(22))
    assert summary["forecasts_logged"] == 21  # 7 symbols x 3 models
    assert summary["forecasts_scored"] == 0
    assert summary["missed_days"] == 0
    assert summary["threshold_met"] is False
    rows_run1 = vl.load_rows(paths["ledger"])
    assert len(rows_run1) == 21
    assert vl.verify_ledger(paths["ledger"]) == []
    snapshot = paths["ledger"].read_bytes()
    by_symbol = {}
    for row in vl.load_forecasts(paths["ledger"]):
        by_symbol.setdefault(row["symbol"], row)
    for symbol in ("SPY", "AAPL", "VT", "NIFTYBEES.NS", "ISF.L"):
        assert by_symbol[symbol]["origin_date"] == "2026-11-13"
        assert by_symbol[symbol]["bar_complete"] is True
    # Crypto/FX dropped the in-progress 13 Nov bar: origin 12 Nov.
    for symbol in ("EURUSD=X", "BTC-USD"):
        assert by_symbol[symbol]["origin_date"] == "2026-11-12"
    # Same-day re-run is idempotent for forecasts: nothing new is
    # logged. It DOES score the 6 crypto/FX forecasts (origin
    # 12 Nov — the 13 Nov bar is their first bar after the origin).
    again = _run(paths, "2026-11-13", series, _at(22))
    assert again["forecasts_logged"] == 0
    assert again["forecasts_scored"] == 6
    assert len(vl.load_forecasts(paths["ledger"])) == 21
    assert paths["ledger"].read_bytes().startswith(snapshot)
    # Run 2 — Monday 2026-11-16: scores the remaining 15 forecasts
    # (the equities' origins are 13 Nov; the first bar after is
    # 16 Nov) and logs 21 new ones.
    summary2 = _run(paths, "2026-11-16", series, _at(22, day="2026-11-16"))
    assert summary2["forecasts_scored"] == 15  # 6 already scored
    assert summary2["forecasts_logged"] == 21
    rows = vl.load_rows(paths["ledger"])
    assert len(rows) == 63  # 21 forecasts + 21 scores + 21 forecasts
    assert rows[:len(rows_run1)] == rows_run1  # run 2 appended only
    assert vl.verify_ledger(paths["ledger"]) == []
    scores = vl.load_scores(paths["ledger"])
    assert len(scores) == 21
    actual_dates = sorted({s["actual_date"] for s in scores})
    assert actual_dates == ["2026-11-13", "2026-11-16"]
    assert all(s["status"] == "scored" for s in scores)
    counts = vl.ledger_counts(paths["ledger"])
    assert counts["scored_total"] == 21
    assert counts["threshold_met"] is False


def test_incomplete_equity_bar_is_a_missed_day_not_a_forecast(tmp_path):
    paths = _paths(tmp_path)
    series = _series("2026-11-13")
    # 12:00 UTC: the NYSE session has not closed — SPY/AAPL/VT
    # bars are incomplete; NSE/LSE have closed; crypto/FX use the
    # previous bar. Nothing is refused: the incomplete bars are
    # missed days, never forecasts.
    summary = _run(paths, "2026-11-13", series, _at(12))
    # NSE closed (10:00 UTC); NYSE (21:00 UTC) and LSE (16:30 UTC)
    # have not: only NIFTYBEES.NS plus crypto/FX log forecasts.
    assert summary["forecasts_logged"] == 9  # 3 symbols x 3 models
    missed = [json.loads(line) for line in
              paths["missed"].read_text(encoding="utf-8").splitlines()
              if line.strip()]
    missed_symbols = {r["symbol"] for r in missed}
    assert missed_symbols == {"SPY", "AAPL", "VT", "ISF.L"}
    assert vl.verify_ledger(paths["ledger"]) == []


def test_fetch_failure_is_a_missed_day_not_a_crash(tmp_path):
    paths = _paths(tmp_path)
    series = _series("2026-11-13")

    def failing_fetch(symbol, kind="stock"):
        if symbol == "AAPL":
            raise RuntimeError("yahoo 429")
        return _fake_fetch(series, "2026-11-13")(symbol, kind)

    summary = run_day(paths["ledger"], paths["missed"], "2026-11-13",
                      fetch=failing_fetch, now=_at(22))
    assert summary["forecasts_logged"] == 18  # 6 symbols x 3 models
    assert summary["missed_days"] == 1
    missed = [json.loads(line) for line in
              paths["missed"].read_text(encoding="utf-8").splitlines()
              if line.strip()]
    assert missed[0]["symbol"] == "AAPL"
    assert "fetch failed" in missed[0]["reason"]


def test_short_history_is_a_missed_day_not_a_short_forecast(tmp_path):
    paths = _paths(tmp_path)
    # Only 30 closes: fewer than the protocol's 253.
    short = {symbol: _pairs(_weekdays_through("2025-12-15"))
             for symbol in vl.WATCHLIST}
    summary = run_day(paths["ledger"], paths["missed"], "2025-12-15",
                      fetch=_fake_fetch(short, "2025-12-15"),
                      now=_at(22, day="2025-12-15"))
    assert summary["forecasts_logged"] == 0
    assert summary["missed_days"] == len(vl.WATCHLIST)
    assert not paths["ledger"].exists()
    assert vl.verify_ledger(paths["ledger"]) == []


# --- publish: three runs against a local bare origin ----------------------

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


def _publish(paths, repo):
    return _daily.publish([paths["ledger"], paths["missed"]],
                          "data/volatility-ledger", repo_root=repo)


def test_three_runs_publish_and_second_run_appends(tmp_path):
    """Run 1 publishes 21 forecasts; run 2 publishes 21 scores +
    21 forecasts on top of run 1's rows, byte-identical; run 3
    appends again; the branch history verifies append-only.
    """
    paths = _paths(tmp_path)
    series = _series("2026-11-17")
    repo, origin = _make_git_repo(tmp_path)
    _run(paths, "2026-11-13", series, _at(22))
    assert _publish(paths, repo) is not None
    assert len(_origin_file(origin, "data/volatility-ledger",
                            "volatility-ledger.jsonl").splitlines()) == 21
    snapshot = paths["ledger"].read_bytes()
    _run(paths, "2026-11-16", series, _at(22, day="2026-11-16"))
    assert paths["ledger"].read_bytes().startswith(snapshot)
    assert _publish(paths, repo) is not None
    assert len(_origin_file(origin, "data/volatility-ledger",
                            "volatility-ledger.jsonl").splitlines()) == 63
    _run(paths, "2026-11-17", series, _at(22, day="2026-11-17"))
    assert _publish(paths, repo) is not None
    # The branch history is append-only and the tip verifies.
    assert _verify.check_history(
        repo, branch="data/volatility-ledger",
        path="volatility-ledger.jsonl", fetch=True) == []
    # A tampered publish is refused: rewrite run 1's row locally
    # and publish must fail before pushing.
    rows = vl.load_rows(paths["ledger"])
    rows[0]["forecast_variance"] = 0.0099
    paths["ledger"].write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
        encoding="utf-8")
    with pytest.raises(RuntimeError, match="publish refused"):
        _publish(paths, repo)
