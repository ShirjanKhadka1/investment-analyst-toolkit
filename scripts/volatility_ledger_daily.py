"""Daily forward-volatility-ledger step (Round E, E.2).

Runs the pre-registered protocol in
docs/volatility-ledger-protocol.md and nothing else: once a day,
fetch the seven watchlist tickers, take the last COMPLETE bar per
symbol, compute the three one-day-ahead variance forecasts
(naive / EWMA / GARCH-lite) from the trailing 253 closes, append
the forecast rows, then score any forecast rows whose first bar
after the origin bar has now arrived. Results live on the data
branch (data/volatility-ledger) — NEVER on main.

Safety (same discipline as PR 7.0 for the track record): the
ledger is append-only with a hash chain
(volatility-ledger.jsonl). The runner seeds its local ledger from
the data branch before running, so prior forecasts load; publish()
refuses to push unless the remote rows are byte-identical and
the diff has only additions; verify_volatility_ledger.py walks
the branch history after publishing. A second run must APPEND,
never overwrite — see tests/test_volatility_ledger.py, which
proves it against a local bare remote.

bar_complete is the track record's rule, not a copy of it: this
script loads scripts/track_record_daily.py (the same importlib
pattern tests/test_track_record_append_only.py uses) and calls
its _bar_complete, so the two runners can never drift apart. A
forecast is logged ONLY from a complete bar. An incomplete or
missing bar is recorded as a missed day in missed_days.jsonl
and never produces a forecast row. There is no provisional mode:
a run before the close records missed days, it never logs a
forecast from a partial bar. A session state of UNKNOWN (no
time-zone database) refuses the whole run — never guessed.

Stdlib only. Network is used ONLY for the price fetch, through
the toolkit's own connector chain (toolkit.connectors.
fetch_prices — Yahoo first, with the repo's fallbacks). A fetch
failure is a missed day, not a crash. This script is NOT run by
CI or by scripts/qa.py — it is the reproducibility record for
docs/volatility-ledger.md, and its workflow is deployed by the
owner from ops/ready-for-browser/daily-volatility-ledger.yml.

Usage:
  python3 scripts/volatility_ledger_daily.py
      [--ledger data/volatility-ledger/volatility-ledger.jsonl]
      [--missed data/volatility-ledger/missed_days.jsonl]
      [--as-of YYYY-MM-DD] [--publish-branch data/volatility-ledger]
      [--publish]
"""
import argparse
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from toolkit import volatility as vol
from toolkit import volatility_ledger as vl
from toolkit.connectors import fetch_prices
from toolkit.decision_log import row_hash
from toolkit.market_profiles import WATCHLIST_PROFILES

LEDGER_FILE = vl.LEDGER_FILE
MISSED_FILE = vl.MISSED_FILE
DATA_BRANCH = vl.DATA_BRANCH
# Trailing closes per forecast (252 log returns), per the protocol.
HISTORY_CLOSES = 253
KINDS = {
    "SPY": "stock",
    "AAPL": "stock",
    "VT": "stock",
    "NIFTYBEES.NS": "stock",
    "ISF.L": "stock",
    "EURUSD=X": "stock",
    "BTC-USD": "crypto",
}


class RefusedRunError(RuntimeError):
    """The run is refused (unknown session state): nothing is logged."""


def _load_track_record_daily():
    """Load scripts/track_record_daily.py for its _bar_complete.

    The same importlib pattern tests/test_track_record_append_only.py
    uses. The module is read, never modified, and its rule is the
    ledger's rule — a copy here would drift.
    """
    path = ROOT / "scripts" / "track_record_daily.py"
    spec = importlib.util.spec_from_file_location(
        "track_record_daily", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_day(value):
    return date.fromisoformat(str(value))


def _record_missed(missed_path, as_of, symbol, reason):
    """Append one missed-day record. Never fills in, never overwrites."""
    record = {"date": as_of.isoformat(), "symbol": symbol,
              "reason": reason}
    path = Path(missed_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def _record_missed_once(missed_path, as_of, symbol, reason):
    """Append a missed-day record unless one already exists.

    A re-run (or a refused run retried) must not double-record the
    same missed day. Returns True when a new record was written.
    """
    path = Path(missed_path)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("date") == as_of.isoformat() \
                    and record.get("symbol") == symbol:
                return False
    _record_missed(missed_path, as_of, symbol, reason)
    return True


def _entry_bar(pairs, profile_key, bar_complete_fn, now):
    """The last COMPLETE bar of ``pairs`` [(date, close)], or None.

    Returns (bar_date, bar_close, reason) where reason is None on
    success and a missed-day reason otherwise. Equities/ETFs: the
    last bar must be complete, or there is no entry bar today.
    Crypto/FX: the in-progress last bar is dropped and the previous
    bar is the entry bar (the track record's rule). A session state
    of UNKNOWN raises RefusedRunError — never guessed.
    """
    if not pairs:
        return None, None, "no bars fetched"
    last_date, last_close = pairs[-1]
    complete = bar_complete_fn(last_date, profile_key, now)
    if complete is None:
        raise RefusedRunError(
            f"session state unknown for bar {last_date} — no "
            "time-zone database found for the session. Install the "
            "optional tzdata extra "
            "(`pip install -r requirements-tzdata.txt`) and re-run.")
    if complete:
        return last_date, last_close, None
    if profile_key in ("crypto", "fx"):
        if len(pairs) < 2:
            return None, None, ("no complete bar exists — the only "
                                "bar is in progress")
        prev_date, prev_close = pairs[-2]
        return prev_date, prev_close, None
    return None, None, (f"bar {last_date} is incomplete at fetch "
                        "time — the session has not closed")


def _score_due(ledger_path, symbol, pairs, as_of):
    """Score ``symbol``'s forecast rows whose first bar after the
    origin arrived.

    ``pairs`` is the symbol's fetched [(date, close)] series. For
    each unscored forecast, the realised variance is the squared
    log return of the first bar after the origin bar; a forecast
    with no later bar yet stays unscored, and a forecast whose
    origin bar is absent from the fetched series is left alone
    (a fetch gap is never scored against a wrong bar). Returns
    the number of scores appended.
    """
    if not pairs:
        return 0
    by_date = {d: c for d, c in pairs}
    dates = [d for d, _ in pairs]
    scored = 0
    for forecast in vl.load_ledger(ledger_path):
        if forecast["symbol"] != symbol or forecast["scores"]:
            continue
        origin = forecast["origin_date"]
        if origin not in by_date:
            continue
        later = [d for d in dates if d > origin]
        if not later:
            continue
        actual_date = later[0]
        realised = math.log(by_date[actual_date]
                            / by_date[origin]) ** 2
        score = vl.score_forecast(forecast, realised, actual_date)
        vl.append_score(ledger_path, forecast["forecast_id"], score)
        scored += 1
    return scored


def run_day(ledger_path, missed_path, as_of, fetch=fetch_prices,
            now=None):
    """Run one daily ledger step. Returns a summary dict.

    ``fetch`` is injectable ``(symbol, kind) -> result dict`` with
    a ``pairs`` list of (date, close) and a ``source``; the default
    is the real connector chain. ``now`` is the run instant (UTC,
    default: this moment) — it is what the bar-completeness check
    reads, not ``as_of``. Two phases, so a refusal is atomic:
    phase 1 fetches everything and writes nothing; phase 2 scores
    and logs. A refused run leaves the ledger untouched. All file
    writes stay under the given paths — this function never touches
    git or main.
    """
    as_of = _parse_day(as_of)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    ledger_path = Path(ledger_path)
    missed_path = Path(missed_path)
    track_record_daily = _load_track_record_daily()
    bar_complete = track_record_daily._bar_complete

    # Phase 1 — fetch everything, write nothing.
    fetched = {}
    missed_records = []
    for symbol in vl.WATCHLIST:
        profile_key = WATCHLIST_PROFILES[symbol]
        try:
            result = fetch(symbol, KINDS[symbol])
        except Exception as exc:  # noqa: BLE001 — a failed fetch is a
            # missed day, not a crash.
            missed_records.append((as_of, symbol, f"fetch failed: {exc}"))
            continue
        pairs = sorted((str(d), float(c))
                       for d, c in result["pairs"])
        fetched[symbol] = (pairs, str(result.get("source", "unknown")),
                           profile_key)

    # Session-unknown check, before anything is written: a bar
    # whose completeness cannot be determined refuses the run.
    for symbol, (pairs, _source, profile_key) in fetched.items():
        if not pairs:
            continue
        if bar_complete(symbol, profile_key, pairs[-1][0], now) is None:
            raise RefusedRunError(
                f"refusing run for {as_of}: session state unknown "
                f"for {symbol} — no time-zone database found for the "
                "session's time zone. Install the optional tzdata "
                "extra (`pip install -r requirements-tzdata.txt`) "
                "and re-dispatch.")

    # Phase 2 — score due forecasts, then log today's forecasts.
    logged = scored = missed = 0
    for symbol, (pairs, source, profile_key) in fetched.items():
        scored += _score_due(ledger_path, symbol, pairs, as_of)
        entry_date, entry_close, reason = _entry_bar(
            pairs, profile_key,
            lambda d, pk, n, _s=symbol: bar_complete(_s, pk, d, n),
            now)
        if reason is not None:
            if _record_missed_once(missed_path, as_of, symbol, reason):
                missed += 1
            continue
        entry_idx = [d for d, _ in pairs].index(entry_date)
        window = pairs[max(0, entry_idx - HISTORY_CLOSES + 1):
                       entry_idx + 1]
        if len(window) < HISTORY_CLOSES:
            if _record_missed_once(
                    missed_path, as_of, symbol,
                    f"only {len(window)} closes available, need "
                    f"{HISTORY_CLOSES} — no forecast from a shorter "
                    "window"):
                missed += 1
            continue
        closes = [c for _, c in window]
        returns = vol.log_returns(closes)
        existing = {(f["symbol"], f["origin_date"], f["model"])
                    for f in vl.load_forecasts(ledger_path)}
        for model in vl.MODELS:
            if (symbol, entry_date, model) in existing:
                continue  # idempotent re-run: already logged today
            variance = vol.forecast_variance(model, returns)
            vl.append_forecast(ledger_path, {
                "symbol": symbol,
                "forecast_date": (_parse_day(entry_date)
                                  + timedelta(days=1)).isoformat(),
                "origin_date": entry_date,
                "origin_close": entry_close,
                "model": model,
                "forecast_variance": variance,
                "annualised_vol": vl.annualised_vol(variance),
                "bar_complete": True,
                "source": source,
            })
            logged += 1
    for day, symbol, reason in missed_records:
        if _record_missed_once(missed_path, day, symbol, reason):
            missed += 1
    # A run with zero missed days writes no missed-day record, so
    # the file would not exist — and publish() copies all outputs.
    # Guarantee the file exists (empty when nothing was missed) so
    # a first run publishes cleanly instead of dying in `cp`.
    if not missed_path.exists():
        missed_path.parent.mkdir(parents=True, exist_ok=True)
        missed_path.touch()
    rows = vl.load_rows(ledger_path)
    head_hash = row_hash(rows[-1]) if rows else None
    counts = vl.ledger_counts(ledger_path)
    return {"as_of": as_of.isoformat(), "forecasts_logged": logged,
            "forecasts_scored": scored, "missed_days": missed,
            "head_hash": head_hash,
            "scored_total": counts["scored_total"],
            "threshold_met": counts["threshold_met"]}


def _check_publish_invariant(outputs, worktree):
    """Fail loudly unless the push is append-only (PR 7.0 discipline).

    For every ``*.jsonl`` output that already exists on the remote
    branch (``worktree`` holds the remote tip): the local file must
    start with the remote file's bytes — additions only, no row
    removed or changed. The ledger's hash chain must also verify.
    Raises RuntimeError on the first violation, before anything is
    copied or committed.
    """
    for src in outputs:
        src = Path(src)
        if src.suffix != ".jsonl" or not src.exists():
            continue
        remote = worktree / src.name
        if remote.exists():
            remote_bytes = remote.read_bytes()
            local_bytes = src.read_bytes()
            if not local_bytes.startswith(remote_bytes):
                raise RuntimeError(
                    f"publish refused: {src.name} on the data branch "
                    "has rows the local run does not have — the local "
                    "log is not an append-only extension of the "
                    "remote. Refusing to push (see "
                    "docs/track-record-incidents.md).")
        if src.name == LEDGER_FILE:
            problems = vl.verify_ledger(src)
            if problems:
                raise RuntimeError(
                    f"publish refused: hash chain broken in "
                    f"{src.name}: {problems[0]}")


def publish(outputs, branch, repo_root=None):
    """Commit ``outputs`` (paths) to ``branch`` via a git worktree.

    Refuses ``main`` outright. Creates the branch on first use.
    Commits only when the worktree differs; pushes to origin.
    Returns the commit message, or None when nothing changed.

    Only ``missed_days.jsonl`` may legitimately be absent (a run
    produced before the file was guaranteed, or a first run with
    zero missed days): it is published as an empty log. Any other
    missing output — above all the ledger — is an error, not
    something to paper over with an empty file. Mirrors
    scripts/track_record_daily.py::publish (same worktree pattern,
    same append-only invariant) without importing it: the ledger
    must not depend on the track-record runner's publish path.
    """
    outputs = [Path(o) for o in outputs]
    if branch == "main":
        raise ValueError("refusing to publish volatility-ledger "
                         "results to main — results live on a data "
                         "branch only")
    root = Path(repo_root) if repo_root else Path.cwd()
    worktree = Path(tempfile.mkdtemp(prefix="volatility-ledger-"))
    try:
        existing = subprocess.run(
            ["git", "ls-remote", "--exit-code", "--heads", "origin",
             branch], cwd=root, capture_output=True, check=False)
        if existing.returncode == 0:
            subprocess.run(["git", "fetch", "origin", branch], cwd=root,
                           check=True, capture_output=True)
            subprocess.run(["git", "-c", "core.autocrlf=false",
                            "worktree", "add", str(worktree),
                            branch], cwd=root, check=True,
                           capture_output=True)
        else:
            subprocess.run(["git", "-c", "core.autocrlf=false",
                            "worktree", "add", "-b", branch,
                            str(worktree)], cwd=root, check=True,
                           capture_output=True)
        _check_publish_invariant(outputs, worktree)
        for src in outputs:
            src = Path(src)
            dest = worktree / src.name
            if not src.exists():
                if src.name == MISSED_FILE:
                    dest.touch()
                    continue
                raise FileNotFoundError(
                    f"volatility-ledger output missing: {src}")
            subprocess.run(["cp", str(src), str(dest)], check=True)
        subprocess.run(["git", "config", "user.name",
                        "volatility-ledger"],
                       cwd=worktree, check=True)
        subprocess.run(["git", "config", "user.email",
                        "volatility-ledger@users.noreply.github.com"],
                       cwd=worktree, check=True)
        subprocess.run(["git", "add"] + [p.name for p in outputs],
                       cwd=worktree, check=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"],
                              cwd=worktree, check=False)
        if diff.returncode == 0:
            return None
        message = (f"Daily volatility ledger: "
                   f"{datetime.now(timezone.utc).date().isoformat()} "
                   "(data branch only, never main)")
        subprocess.run(["git", "commit", "-m", message], cwd=worktree,
                       check=True, capture_output=True)
        subprocess.run(["git", "push", "origin", branch], cwd=worktree,
                       check=True, capture_output=True)
        return message
    finally:
        subprocess.run(["git", "worktree", "remove", "--force",
                        str(worktree)], cwd=root, check=False,
                       capture_output=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger",
                        default=f"data/volatility-ledger/{LEDGER_FILE}")
    parser.add_argument("--missed",
                        default=f"data/volatility-ledger/{MISSED_FILE}")
    parser.add_argument("--as-of", default="",
                        help="decision date YYYY-MM-DD (default: "
                        "today, UTC)")
    parser.add_argument("--publish-branch", default=DATA_BRANCH)
    parser.add_argument("--publish", action="store_true",
                        help="publish the ledger + missed-days files "
                        "to the data branch")
    args = parser.parse_args(argv)
    as_of = (args.as_of
             or datetime.now(timezone.utc).date().isoformat())
    # Seed the local ledger from the data branch first (PR 7.0
    # lesson): the runner can only append to what it can see.
    # Genesis when the branch or the files do not exist yet. The
    # workflow does the seeding; a bare local run seeds here.
    if args.publish:
        root = Path.cwd()
        seed = subprocess.run(
            ["git", "ls-remote", "--exit-code", "--heads", "origin",
             args.publish_branch], cwd=root, capture_output=True,
            check=False)
        if seed.returncode == 0:
            subprocess.run(["git", "fetch", "origin",
                            args.publish_branch], cwd=root, check=True,
                           capture_output=True)
            for name, dest in ((LEDGER_FILE, args.ledger),
                               (MISSED_FILE, args.missed)):
                path = Path(dest)
                if path.exists():
                    continue
                show = subprocess.run(
                    ["git", "show",
                     f"origin/{args.publish_branch}:{name}"],
                    cwd=root, capture_output=True, check=False)
                if show.returncode == 0:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(show.stdout)
    try:
        summary = run_day(args.ledger, args.missed, as_of)
    except RefusedRunError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, default=str))
    if args.publish:
        message = publish([args.ledger, args.missed],
                          args.publish_branch)
        print(f"published: {message}" if message
              else "published: nothing changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
