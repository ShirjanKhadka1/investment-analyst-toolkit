"""Append-only track record (PR 7.0): consecutive simulated runs
against a throwaway remote, settlement across the 5-day horizon,
idempotency, provisional/excluded non-blocking, tamper detection.

The old single-run test was insufficient: it published once and
never proved a second run could load the first run's calls, nor
that a later run could not silently replace them. These tests run
three consecutive simulated runs plus settlement, against a local
bare origin — never the real data branch.
"""
import importlib.util
import json
import os
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from toolkit.decision_log import (
    GENESIS_PREV_HASH,
    _append_row,
    _call_id,
    append_reflection,
    load_calls,
    load_rows,
    row_hash,
    verify_chain,
    write_call,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_daily = _load_module("track_record_daily",
                      REPO_ROOT / "scripts" / "track_record_daily.py")
_verify = _load_module("verify_track_record",
                       REPO_ROOT / "scripts" / "verify_track_record.py")
run_day = _daily.run_day
RefusedRunError = _daily.RefusedRunError


# --- fixtures ------------------------------------------------------------

def _dates_through(end):
    """Weekday dates from 2026-08-10 through ``end``."""
    out = []
    day = date(2026, 8, 10)
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
    """Rising weekday series for all 7 tickers + 3 index series."""
    out = {}
    for symbol in ("SPY", "AAPL", "VT", "NIFTYBEES.NS", "ISF.L",
                   "EURUSD=X", "BTC-USD"):
        out[symbol] = _pairs(_dates_through(end))
    for symbol, base, step in (("^GSPC", 5000.0, 5.0),
                               ("^NSEI", 25000.0, 25.0),
                               ("^FTSE", 9000.0, 9.0)):
        out[symbol] = _pairs(_dates_through(end), base, step)
    return out


# Session start times (UTC) — what a time-aware fetch serves: a bar
# for today exists only once its session has opened. This is what
# makes the midday scenario realistic: at 12:25 UTC the NYSE has
# not opened (no SPY bar for today), the NSE has closed (complete
# NIFTYBEES.NS bar), and London is open (partial ISF.L bar).
PROFILE_OF = {"SPY": "us", "AAPL": "us", "VT": "us",
              "NIFTYBEES.NS": "india", "ISF.L": "uk",
              "EURUSD=X": "fx", "BTC-USD": "crypto",
              "^GSPC": "us", "^NSEI": "india", "^FTSE": "uk"}
SESSION_START_UTC = {"us": (14, 30), "india": (3, 45), "uk": (8, 0),
                     "crypto": (0, 0), "fx": (0, 0)}


def _fake_fetch(series, now):
    """``fetch(symbol, kind)`` serving only bars whose session has
    started by ``now`` — like Yahoo, not like a static file."""
    def fetch(symbol, kind="stock"):
        if symbol not in series:
            raise RuntimeError(f"no fixture series for {symbol}")
        hour, minute = SESSION_START_UTC[PROFILE_OF[symbol]]
        start = now.replace(hour=hour, minute=minute, second=0,
                            microsecond=0)
        pairs = [(d, c) for d, c in series[symbol]
                 if date.fromisoformat(d) < now.date()
                 or (date.fromisoformat(d) == now.date() and now >= start)]
        return {"pairs": pairs, "source": "yahoo", "symbol": symbol}
    return fetch


def _at(hour, minute=0, day="2026-11-13"):
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00+00:00")


def _paths(tmp_path):
    return {
        "calls": tmp_path / "calls-v2.jsonl",
        "out": tmp_path / "track-record.md",
        "missed": tmp_path / "missed_days.jsonl",
        "bars": tmp_path / "bars",
    }


def _bash_exe():
    """The bash that ships with Git (deterministic on Windows).

    Plain ``bash`` on the PATH is unreliable on the Windows
    runner: the seed step intermittently exits 1 with no stderr
    (seen on Windows 3.11 and 3.12 across runs — the same test
    passes on 3.12 in one run and fails on 3.12 in another —
    while Linux and macOS are always green). The failure is in
    the shell-invocation layer, not the script: every git call in
    the script is guarded or would print an error, so a silent
    exit 1 cannot come from the script itself. Git's own bash is
    locatable from the git executable the test already shells out
    to, so resolve it from there on Windows; plain ``bash``
    elsewhere.
    """
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            candidate = (Path(git).resolve().parent.parent / "bin"
                         / "bash.exe")
            if candidate.is_file():
                return str(candidate)
    return "bash"


def _run(paths, as_of, series, now, provisional=False):
    return run_day(paths["calls"], paths["out"], paths["bars"],
                   paths["missed"], as_of, fetch=_fake_fetch(series, now),
                   now=now, provisional=provisional)


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
    return _daily.publish([paths["calls"], paths["out"],
                           paths["missed"]], "data/track-record",
                          repo_root=repo)


def _chain_row(prev_hash, n):
    """A minimal valid chained call row (valid call_id + link)."""
    row = {"record_type": "call", "symbol": "SPY",
           "date": "2026-11-06", "prev_hash": prev_hash, "n": n}
    row["call_id"] = _call_id(
        {k: v for k, v in row.items() if k != "call_id"})
    return row


# --- three consecutive runs + settlement ---------------------------------

def test_three_consecutive_runs_settle_and_never_replace(tmp_path):
    """Run 1 logs 7 calls; run 2 loads them and changes nothing;
    run 3 settles all 7 and logs 7 new ones; a same-day rerun is
    idempotent. Every step publishes to a throwaway remote, and
    run 1's rows are byte-identical after run 3.
    """
    paths = _paths(tmp_path)
    series = _series("2026-11-20")
    repo, origin = _make_git_repo(tmp_path)

    # Run 1 — Friday 2026-11-06, 22:00 UTC (after every close).
    summary = _run(paths, "2026-11-06", series, _at(22, day="2026-11-06"))
    assert summary["calls_logged"] == 7
    assert summary["calls_settled"] == 0
    assert summary["head_hash"] is not None
    rows_run1 = load_rows(paths["calls"])
    assert len(rows_run1) == 7
    assert summary["head_hash"] == row_hash(rows_run1[-1])
    assert verify_chain(paths["calls"]) == []
    snapshot = paths["calls"].read_bytes()
    by_symbol = {c["symbol"]: c for c in load_calls(paths["calls"])}
    # Equities decided on the complete 6 Nov bar; crypto/FX dropped
    # the in-progress 6 Nov bar and used the complete 5 Nov bar.
    for symbol in ("SPY", "AAPL", "VT", "NIFTYBEES.NS", "ISF.L"):
        assert by_symbol[symbol]["bar_date"] == "2026-11-06"
        assert by_symbol[symbol]["bar_complete"] is True
        assert by_symbol[symbol]["provisional"] is False
    for symbol in ("EURUSD=X", "BTC-USD"):
        assert by_symbol[symbol]["bar_date"] == "2026-11-05"
        assert by_symbol[symbol]["bar_complete"] is True
        assert by_symbol[symbol]["provisional"] is False
    assert _publish(paths, repo) is not None
    assert len(_origin_file(origin, "data/track-record",
                            "calls-v2.jsonl").splitlines()) == 7

    # Run 2 — Monday 2026-11-09: no call due, nothing settles.
    # It must load run 1's calls and leave the file untouched.
    summary = _run(paths, "2026-11-09", series, _at(22, day="2026-11-09"))
    assert summary["calls_logged"] == 0
    assert summary["calls_settled"] == 0
    assert paths["calls"].read_bytes() == snapshot
    assert _publish(paths, repo) is None  # no changes to publish

    # Run 3 — Friday 2026-11-13: all 7 settle, 7 new calls log.
    summary = _run(paths, "2026-11-13", series, _at(22, day="2026-11-13"))
    assert summary["calls_settled"] == 7
    assert summary["calls_logged"] == 7
    rows = load_rows(paths["calls"])
    assert len(rows) == 21  # 7 calls + 7 settlements + 7 calls
    assert paths["calls"].read_bytes().startswith(snapshot)
    assert verify_chain(paths["calls"]) == []
    calls = load_calls(paths["calls"])
    settled = [c for c in calls if c.get("status") == "settled"]
    open_calls = [c for c in calls if c.get("status") == "open"]
    assert len(settled) == 7 and len(open_calls) == 7
    assert _publish(paths, repo) is not None
    remote = _origin_file(origin, "data/track-record", "calls-v2.jsonl")
    assert len(remote.splitlines()) == 21
    assert remote.encode("utf-8").startswith(snapshot)

    # Same-day rerun of run 3: idempotent — no duplicates, no commit.
    summary = _run(paths, "2026-11-13", series, _at(22, day="2026-11-13"))
    assert summary["calls_logged"] == 0
    assert summary["calls_settled"] == 0
    assert len(load_rows(paths["calls"])) == 21
    assert _publish(paths, repo) is None


# --- pre-close refusal + provisional --------------------------------------

def test_pre_close_dispatch_refuses_and_provisional_never_scores(tmp_path):
    """A manual dispatch at 12:25 UTC on 2026-11-13 (ISF.L's session
    open, partial bar served) refuses and writes nothing; with
    --provisional it logs the ISF.L call labelled provisional, and
    that call never settles and never blocks a replacement.
    """
    paths = _paths(tmp_path)
    series = _series("2026-11-20")
    midday = _at(12, 25, day="2026-11-13")

    with pytest.raises(RefusedRunError):
        _run(paths, "2026-11-13", series, midday)
    assert not paths["calls"].exists()  # refused run writes nothing

    summary = _run(paths, "2026-11-13", series, midday, provisional=True)
    assert summary["calls_logged"] == 7
    assert summary["provisional_logged"] == 1
    calls = load_calls(paths["calls"])
    by_symbol = {c["symbol"]: c for c in calls}
    isf = by_symbol["ISF.L"]
    assert isf["provisional"] is True
    assert isf["bar_complete"] is False
    assert isf["bar_date"] == "2026-11-13"
    # US equities had no 13 Nov bar yet (session not open): normal
    # calls on the complete 12 Nov bar. NIFTYBEES.NS closed at
    # 10:00 UTC: normal call on the complete 13 Nov bar. Crypto/FX
    # dropped the partial 13 Nov bar: normal calls on 12 Nov.
    for symbol in ("SPY", "AAPL", "VT"):
        assert by_symbol[symbol]["bar_date"] == "2026-11-12"
        assert by_symbol[symbol]["provisional"] is False
    assert by_symbol["NIFTYBEES.NS"]["bar_date"] == "2026-11-13"
    assert by_symbol["NIFTYBEES.NS"]["provisional"] is False
    for symbol in ("EURUSD=X", "BTC-USD"):
        assert by_symbol[symbol]["bar_date"] == "2026-11-12"
        assert by_symbol[symbol]["provisional"] is False

    # A post-close run logs a normal ISF.L call: the provisional
    # one never blocks a replacement.
    _run(paths, "2026-11-16", series, _at(22, day="2026-11-16"))
    isf_calls = [c for c in load_calls(paths["calls"])
                 if c["symbol"] == "ISF.L"]
    assert len(isf_calls) == 2
    assert sum(1 for c in isf_calls if not c["provisional"]) == 1

    # At the provisional call's horizon (20 Nov) it stays open and
    # un-settled, while the normal 13 Nov calls settle.
    summary = _run(paths, "2026-11-20", series, _at(22, day="2026-11-20"))
    assert summary["calls_settled"] >= 1
    calls = load_calls(paths["calls"])
    provisional = next(c for c in calls
                       if c["symbol"] == "ISF.L" and c["provisional"])
    assert provisional["status"] == "open"
    settled_ids = {r.get("call_id") for r in load_rows(paths["calls"])
                   if r.get("record_type") == "settlement"}
    assert provisional["call_id"] not in settled_ids
    spy = next(c for c in calls if c["symbol"] == "SPY"
               and c["date"] == "2026-11-13")
    assert spy["status"] == "settled"


# --- excluded calls never block -------------------------------------------

def test_excluded_call_never_blocks_a_replacement(tmp_path):
    """A call marked excluded in the log does not make the next
    call not-due: the run logs a fresh call for the same ticker.
    """
    paths = _paths(tmp_path)
    series = _series("2026-11-13")
    excluded = {
        "record_type": "call",
        "date": "2026-11-06", "symbol": "SPY", "exchange": "NYSE Arca",
        "profile": "us", "persona": "etf_index",
        "rule_id": "sma50_trend", "rule_version": "1.0.0",
        "rule_code_hash": "0" * 64, "expected": "up",
        "horizon_days": 5, "entry_close": 100.0,
        "bar_date": "2026-11-06", "bar_complete": True,
        "provisional": False, "confidence": 0.5,
        "confidence_basis": "excluded fixture call",
        "sources": ["yahoo"], "thesis": "excluded fixture call",
        "prev_5d_return": 0.01, "status": "excluded",
        "prev_hash": GENESIS_PREV_HASH,
    }
    excluded["call_id"] = _call_id(
        {k: v for k, v in excluded.items() if k != "call_id"})
    _append_row(paths["calls"], excluded)
    assert verify_chain(paths["calls"]) == []

    summary = _run(paths, "2026-11-13", series, _at(22, day="2026-11-13"))
    assert summary["calls_logged"] == 7
    spy = [c for c in load_calls(paths["calls"])
           if c["symbol"] == "SPY"]
    assert len(spy) == 2
    assert spy[0]["status"] == "excluded"
    assert spy[1]["status"] == "open"
    assert spy[1]["provisional"] is False


# --- workflow seed, end to end -------------------------------------------

def test_workflow_seed_loads_prior_calls_end_to_end(tmp_path):
    """The workflow's own steps, executed locally against a
    throwaway remote: run 1 publishes; a fresh checkout seeds
    prior calls from the data branch (the step whose absence
    caused the November 2026 overwrite); run 2 loads them and
    changes nothing; run 3 settles and appends; the history
    verifier passes on the published branch.
    """
    series = _series("2026-11-20")
    run1 = _paths(tmp_path / "run1")
    repo, origin = _make_git_repo(tmp_path)

    _run(run1, "2026-11-06", series, _at(22, day="2026-11-06"))
    snapshot = run1["calls"].read_bytes()
    assert _publish(run1, repo) is not None
    assert len(_origin_file(origin, "data/track-record",
                            "calls-v2.jsonl").splitlines()) == 7

    # Fresh runner checkout (main only — no calls file on main).
    runner = tmp_path / "runner"
    subprocess.run(["git", "clone", str(origin), str(runner)],
                   check=True, capture_output=True)
    assert not (runner / "data" / "track-record"
                / "calls-v2.jsonl").exists()

    # The workflow's seed step, verbatim (paths as in the YAML).
    seed = (
        'if git ls-remote --exit-code --heads origin data/track-record '
        '>/dev/null 2>&1; then '
        'git fetch origin data/track-record; '
        'mkdir -p data/track-record; '
        'for f in calls-v2.jsonl missed_days.jsonl; do '
        'if git cat-file -e "origin/data/track-record:$f" 2>/dev/null; '
        'then git show "origin/data/track-record:$f" '
        '> "data/track-record/$f"; fi; done; fi')
    # Run the step from a file, not `bash -c`: passing the script as
    # a -c argument goes through Windows CreateProcess quoting, and
    # on the Windows runner that invocation exits 1 with no stderr.
    # The script itself cannot exit 1 — every git call is guarded
    # by an if, so a git failure still exits 0 — which puts the
    # failure in the invocation layer, not the script. The script
    # text below is unchanged; only how it is handed to bash.
    # The bash is Git's own (see _bash_exe): plain `bash` on the
    # PATH resolves unreliably on the Windows runner. The failure
    # is surfaced with both stdout and stderr now, so any residual
    # failure is diagnosable instead of silent.
    seed_file = tmp_path / "seed.sh"
    with seed_file.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(seed + "\n")
    proc = subprocess.run([_bash_exe(), str(seed_file)], cwd=runner,
                          capture_output=True, check=False)
    assert proc.returncode == 0, (
        "seed step failed: "
        + proc.stderr.decode(errors="replace")
        + proc.stdout.decode(errors="replace"))
    seeded = runner / "data" / "track-record" / "calls-v2.jsonl"
    assert seeded.read_bytes() == snapshot

    runner_paths = {
        "calls": seeded,
        "out": runner / "data" / "track-record" / "track-record.md",
        "missed": runner / "data" / "track-record" / "missed_days.jsonl",
        "bars": runner / "data" / "track-record" / "bars",
    }
    # Run 2 loads the seeded calls and changes nothing.
    summary = _run(runner_paths, "2026-11-09", series,
                   _at(22, day="2026-11-09"))
    assert summary["calls_logged"] == 0
    assert summary["calls_settled"] == 0
    assert seeded.read_bytes() == snapshot
    assert _publish(runner_paths, runner) is None
    # Run 3 settles all 7 and appends 7 new calls.
    summary = _run(runner_paths, "2026-11-13", series,
                   _at(22, day="2026-11-13"))
    assert summary["calls_settled"] == 7
    assert summary["calls_logged"] == 7
    assert seeded.read_bytes().startswith(snapshot)
    assert verify_chain(seeded) == []
    assert _publish(runner_paths, runner) is not None
    remote = _origin_file(origin, "data/track-record", "calls-v2.jsonl")
    assert len(remote.splitlines()) == 21
    assert remote.encode("utf-8").startswith(snapshot)
    assert _verify.check_history(runner, "data/track-record",
                                 "calls-v2.jsonl") == []


# --- tamper detection + history verifier -----------------------------------

def test_tampered_old_row_fails_the_chain_and_the_publish(tmp_path):
    """Editing any earlier row breaks the chain, and the publish
    invariant refuses to push a log that is not an append-only
    extension of the remote's.
    """
    paths = _paths(tmp_path)
    series = _series("2026-11-06")
    _run(paths, "2026-11-06", series, _at(22, day="2026-11-06"))
    assert verify_chain(paths["calls"]) == []

    lines = paths["calls"].read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["entry_close"] = float(tampered["entry_close"]) + 1.0
    lines[0] = json.dumps(tampered, sort_keys=True)
    paths["calls"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert verify_chain(paths["calls"]) != []

    # Publish invariant: a local log that drops or changes a remote
    # row is refused; a strict extension is accepted.
    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()
    good = tmp_path / "good.jsonl"
    _run({"calls": good, "out": tmp_path / "o.md",
          "missed": tmp_path / "m.md", "bars": tmp_path / "b"},
         "2026-11-06", series, _at(22, day="2026-11-06"))
    (remote_dir / "calls-v2.jsonl").write_bytes(good.read_bytes())
    with pytest.raises(RuntimeError):
        _daily._check_publish_invariant([paths["calls"]], remote_dir)
    # Strict extension: local = remote rows + one more valid row.
    extended = tmp_path / "extended.jsonl"
    extended.write_bytes(good.read_bytes())
    extra = json.loads(good.read_text().splitlines()[0])
    extra.pop("call_id", None)
    extra["prev_hash"] = row_hash(load_rows(good)[-1])
    extra["call_id"] = _call_id(
        {k: v for k, v in extra.items() if k != "call_id"})
    with extended.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(extra, sort_keys=True) + "\n")
    _daily._check_publish_invariant([extended], remote_dir)  # no raise


def test_verify_script_fails_on_a_history_that_rewrites_a_row(tmp_path):
    """scripts/verify_track_record.py: an append-only history
    passes; a commit that rewrites an earlier row fails.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True,
                   capture_output=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", str(origin), str(repo)], check=True,
                   capture_output=True)
    for key, value in (("user.name", "test"),
                       ("user.email", "test@example.invalid")):
        subprocess.run(["git", "-C", str(repo), "config", key, value],
                       check=True, capture_output=True)
    log = repo / "calls-v2.jsonl"

    def commit(message):
        subprocess.run(["git", "-C", str(repo), "add", "calls-v2.jsonl"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", message],
                       check=True, capture_output=True)
        # The clone of an empty repo has no local branch name yet;
        # push the current HEAD as main.
        subprocess.run(["git", "-C", str(repo), "push", "origin",
                        "HEAD:main"],
                       check=True, capture_output=True)

    rows = [_chain_row(GENESIS_PREV_HASH, 1)]
    rows.append(_chain_row(row_hash(rows[0]), 2))
    log.write_text("".join(json.dumps(r, sort_keys=True) + "\n"
                           for r in rows), encoding="utf-8")
    commit("v1: two rows")
    rows.append(_chain_row(row_hash(rows[1]), 3))
    log.write_text("".join(json.dumps(r, sort_keys=True) + "\n"
                           for r in rows), encoding="utf-8")
    commit("v2: append one row")
    assert _verify.check_history(repo, "main", "calls-v2.jsonl",
                                 fetch=False) == []

    # v3 rewrites row 1 (same position, different content).
    rows[0] = _chain_row(GENESIS_PREV_HASH, 99)
    log.write_text("".join(json.dumps(r, sort_keys=True) + "\n"
                           for r in rows), encoding="utf-8")
    commit("v3: rewrite an earlier row")
    problems = _verify.check_history(repo, "main", "calls-v2.jsonl",
                                     fetch=False)
    assert problems != []
    assert "removed or changed" in problems[0]


def test_missing_tzdata_refuses_run(monkeypatch, tmp_path):
    """A system with no IANA time-zone database: the session state
    is UNKNOWN and the run refuses — it never guesses.

    Windows ships no tz database, so ZoneInfo finds nothing there
    (16 Windows CI failures on PR 7.0). The run must refuse with
    RefusedRunError, naming the optional tzdata extra — even with
    --provisional, since a bar whose completeness cannot be
    determined cannot be logged, provisionally or otherwise.
    """
    from zoneinfo import ZoneInfoNotFoundError

    def _no_tzdata(name):
        raise ZoneInfoNotFoundError(
            f"No time zone found with key {name}")

    monkeypatch.setattr(_daily, "ZoneInfo", _no_tzdata)
    paths = _paths(tmp_path)
    series = _series("2026-11-06")
    with pytest.raises(RefusedRunError, match="tzdata"):
        _run(paths, "2026-11-06", series, _at(22, day="2026-11-06"))
    assert not paths["calls"].exists()  # refused run writes nothing
    with pytest.raises(RefusedRunError, match="tzdata"):
        _run(paths, "2026-11-06", series, _at(22, day="2026-11-06"),
             provisional=True)
    assert not paths["calls"].exists()


def test_verifier_passes_on_crlf_copy_of_valid_log(tmp_path):
    """The history verifier compares canonical rows, not raw bytes.

    A valid log whose line endings were converted to CRLF (e.g. a
    Windows checkout) still passes — the rows are identical, only
    the line endings differ. Rewriting a row in the CRLF history
    still fails: the canonical comparison does not weaken the
    "no row removed or changed" guarantee.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True,
                   capture_output=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", str(origin), str(repo)], check=True,
                   capture_output=True)
    for key, value in (("user.name", "test"),
                       ("user.email", "test@example.invalid"),
                       ("core.autocrlf", "false")):
        subprocess.run(["git", "-C", str(repo), "config", key, value],
                       check=True, capture_output=True)
    log = repo / "calls-v2.jsonl"

    def write_crlf(rows):
        log.write_bytes(b"".join(
            (json.dumps(r, sort_keys=True) + "\r\n").encode("utf-8")
            for r in rows))

    def commit(message):
        subprocess.run(["git", "-C", str(repo), "add", "calls-v2.jsonl"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", message],
                       check=True, capture_output=True)
        # The clone of an empty repo has no local branch name yet;
        # push the current HEAD as main.
        subprocess.run(["git", "-C", str(repo), "push", "origin",
                        "HEAD:main"],
                       check=True, capture_output=True)

    rows = [_chain_row(GENESIS_PREV_HASH, 1)]
    rows.append(_chain_row(row_hash(rows[0]), 2))
    write_crlf(rows)
    commit("v1: two rows, CRLF")
    assert verify_chain(log) == []
    rows.append(_chain_row(row_hash(rows[1]), 3))
    write_crlf(rows)
    commit("v2: append one row, CRLF")
    assert verify_chain(log) == []
    assert _verify.check_history(repo, "main", "calls-v2.jsonl",
                                 fetch=False) == []

    # v3 rewrites row 1 (same position, different content, still
    # CRLF): the verifier must still fail.
    rows[0] = _chain_row(GENESIS_PREV_HASH, 99)
    write_crlf(rows)
    commit("v3: rewrite an earlier row, CRLF")
    problems = _verify.check_history(repo, "main", "calls-v2.jsonl",
                                     fetch=False)
    assert problems != []
    assert "removed or changed" in problems[0]


def test_jsonl_writers_use_lf_line_endings(tmp_path):
    """JSONL logs are LF on every OS (PR 7.0 Windows fix).

    The publish invariant compares bytes and git stores LF — but
    text-mode appends translate "\\n" to CRLF on Windows, so the
    local log no longer matched the published blob and the
    byte-prefix check failed (Windows CI failure on PR #30).
    Every JSONL writer must force LF.
    """
    calls = tmp_path / "calls.jsonl"
    _append_row(calls, {"record_type": "note", "x": 1})
    write_call(calls, {
        "date": "2026-11-06", "symbol": "SPY", "exchange": "NYSE",
        "thesis": "test", "horizon_days": 5, "expected": "up",
        "sources": ["test"], "confidence": 0.5,
        "entry_close": 100.0})
    assert b"\r" not in calls.read_bytes()
    assert calls.read_bytes().endswith(b"\n")

    missed = tmp_path / "missed_days.jsonl"
    _daily._record_missed(missed, date(2026, 11, 6), "SPY", "no data")
    assert b"\r" not in missed.read_bytes()

    reflections = tmp_path / "reflections.jsonl"
    append_reflection(reflections, "some-call-id", "test reflection")
    assert b"\r" not in reflections.read_bytes()
