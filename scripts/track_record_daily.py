"""Daily track-record step: fetch, decide, settle, render, publish.

One run (one ``--as-of`` date) does, per watchlist ticker:

1. Fetch the series (Yahoo via ``toolkit.connectors``; CoinGecko first
   for BTC-USD). A failed fetch is recorded as a **missed day** in
   ``missed_days.jsonl`` and never filled in — the ticker is skipped
   for this run, and no call is backfilled later.
2. Settle open calls whose 5-trading-day horizon has passed, against
   the close of the settlement date (see ``toolkit.trading_calendar``
   — the special-session rule: a bar on a non-standard day counts).
   Provisional and excluded calls are never settled.
3. Log one new call when one is due (one call per ticker per 5
   trading days, non-overlapping), from a point-in-time slice only:
   the direction rule sees bars up to and including the decision
   date, never later ones. Each call is logged BEFORE its outcome
   is known. The entry bar is the last COMPLETE bar: it records
   ``bar_date`` (the entry bar's date) and ``bar_complete``
   (whether that bar was complete at fetch time). A bar is complete
   when its exchange session had closed (equities/ETFs, per the
   market profile) or when it is dated before the current UTC day
   (crypto/FX, which have no close). An in-progress last bar is
   dropped for crypto/FX — the call uses the last complete bar —
   but for equities/ETFs a partial bar means the session is still
   open, so a run that would log from one REFUSES with a clear
   message unless ``--provisional`` is passed. A provisional call
   is labelled and never scored, and never blocks a replacement
   call. The horizon (settle date and next-call due date) runs
   from the entry bar, not the decision date.
4. Render the track-record page (per-ticker + pooled, Wilson 95%
   intervals, headline withheld below 100 settled calls).
5. With ``--publish``: commit the outputs to the data branch
   (``--publish-branch``, default ``data/track-record``) via a git
   worktree. Never main — ``--publish-branch main`` is refused.

Append-only (PR 7.0, after the November 2026 overwrite incident):
the log is a hash-chained JSONL file (see ``toolkit.decision_log``).
The workflow seeds the local calls file from the data branch
before running, so prior calls load; the script only ever appends.
``publish()`` verifies the remote rows are byte-identical and the
diff has only additions, and refuses to push otherwise.

Idempotent: a re-run on the same date logs no duplicate call (a
call for the same date/symbol/rule already in the log is skipped),
settles no call twice (``settle_call`` refuses settled calls), and
publishes no empty commit.

Stdlib only. Run from the repo root:
``python3 scripts/track_record_daily.py [--as-of YYYY-MM-DD]
[--calls PATH] [--out PATH] [--bars-dir DIR] [--publish]``.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import track_record as track_record_mod
from toolkit.connectors import fetch_prices
from toolkit.decision_log import (
    load_calls,
    load_rows,
    row_hash,
    settle_call,
    verify_chain,
    write_call,
)
from toolkit.direction_rules import RULES, direction_for, rule_code_hash
from toolkit.market_profiles import WATCHLIST_PROFILES, get_profile
from toolkit.pit_store import PointInTimeStore
from toolkit.trading_calendar import is_trading_day, settlement_date

HORIZON_TRADING_DAYS = 5

# File names in the data branch. The scored series lives in
# CALLS_FILE (v2 — the clean restart after the November 2026
# overwrite incident); CALLS_FILE_LEGACY is the frozen
# commissioning log, never appended to again.
CALLS_FILE = "calls-v2.jsonl"
CALLS_FILE_LEGACY = "calls.jsonl"


class RefusedRunError(ValueError):
    """The run would log a call from an incomplete bar."""

# Watchlist attribution, mirroring docs/watchlist-proposal.md (the
# doc table is authoritative — tests/test_track_record.py checks
# these dicts against it). Exchange is the venue label; persona is
# the rules-mode persona that owns the ticker. Direction comes from
# the registered price rule, not from the persona (see
# docs/direction-rules.md).
EXCHANGE_LABELS = {
    "SPY": "NYSE Arca",
    "AAPL": "Nasdaq",
    "VT": "NYSE Arca",
    "NIFTYBEES.NS": "NSE",
    "ISF.L": "LSE",
    "EURUSD=X": "FX (OTC)",
    "BTC-USD": "crypto (venue-agnostic)",
}
PERSONAS = {
    "SPY": "etf_index",
    "AAPL": "us_large_cap_fundamentals",
    "VT": "etf_index",
    "NIFTYBEES.NS": "etf_index",
    "ISF.L": "etf_index",
    "EURUSD=X": "fx_macro",
    "BTC-USD": "crypto",
}
KINDS = {
    "SPY": "stock",
    "AAPL": "stock",
    "VT": "stock",
    "NIFTYBEES.NS": "stock",
    "ISF.L": "stock",
    "EURUSD=X": "stock",
    "BTC-USD": "crypto",
}
# Benchmark index per ticker (Yahoo symbol), or None when the spec
# says there is no benchmark series: VT has no Yahoo series for the
# FTSE Global All Cap, and FX/crypto have no benchmark index by
# definition. A None entry settles with index_return 0.0 and a
# labelled "no-index" source — never an invented index return.
INDEX_SYMBOLS = {
    "SPY": "^GSPC",
    "AAPL": "^GSPC",
    "VT": None,
    "NIFTYBEES.NS": "^NSEI",
    "ISF.L": "^FTSE",
    "EURUSD=X": None,
    "BTC-USD": None,
}
NO_INDEX_SOURCE = {
    "VT": "no-index: no Yahoo series for FTSE Global All Cap",
    "EURUSD=X": "no-index: FX has no benchmark index in this spec",
    "BTC-USD": "no-index: crypto has no benchmark index in this spec",
}


def _parse_day(value):
    return date.fromisoformat(str(value))


def _load_json(path):
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _record_missed(missed_path, as_of, symbol, reason):
    """Append one missed-day record. Never fills in, never overwrites."""
    record = {"date": as_of.isoformat(), "symbol": symbol,
              "reason": reason}
    path = Path(missed_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
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


def _merge_bars(cached, pairs):
    """Merge fetched (date, close) pairs into a cached bar list.

    Returns ``(bars, new_count)``: bars sorted by date, one per date,
    newest close winning on a duplicate date. ``pairs`` are ISO date
    strings (or ``datetime.date``) with positive closes.
    """
    by_date = {b["date"]: b for b in (cached or [])}
    new_count = 0
    for raw_date, close in pairs:
        day = _parse_day(raw_date).isoformat()
        if day not in by_date:
            new_count += 1
        by_date[day] = {"date": day, "open": float(close),
                        "high": float(close), "low": float(close),
                        "close": float(close)}
    bars = [by_date[d] for d in sorted(by_date)]
    return bars, new_count


def _pit_store(bars):
    """Point-in-time store over cached bars.

    Yahoo daily series carry closes only, so each bar is stored with
    open = high = low = close — no intraday data is claimed. A bar
    is published on its own date: the daily run happens after the
    close, so the decision-day bar is visible to the decision.
    """
    store = PointInTimeStore()
    for bar in bars:
        store.add_bar(bar["date"], bar["date"], bar["open"], bar["high"],
                      bar["low"], bar["close"])
    return store


def _close_on(bars, day):
    """Close on ``day`` (ISO str or date) from a bar list, or None."""
    want = _parse_day(day).isoformat()
    for bar in bars:
        if bar["date"] == want:
            return float(bar["close"])
    return None


def _prev_5d_return(closes):
    """Return over the 5 trading days before the last close.

    ``closes`` is the PIT close list [(date, close)] oldest first,
    one entry per trading day — so the 6th-from-last close is 5
    trading days before the last. Returns None when fewer than 6
    closes are visible (no previous window to score the naive
    baseline against).
    """
    if len(closes) < 6:
        return None
    return closes[-1][1] / closes[-6][1] - 1.0


def _counts_toward(call):
    """True when a call blocks a replacement call for its ticker.

    A call counts when it is open or settled and is neither
    provisional nor excluded. A provisional call (logged from an
    incomplete bar with --provisional) and an excluded call never
    block a replacement — the next run may log a fresh call for
    the same ticker while the provisional/excluded one stays open.
    """
    if call.get("provisional"):
        return False
    return call.get("status") in ("open", "settled")


def _call_due(calls, symbol, as_of, profile_key, observed_dates):
    """True when a new call for ``symbol`` is due on ``as_of``.

    One call per ticker per 5 trading days, non-overlapping: due when
    there is no prior counting call, or the latest counting call's
    entry-bar date + 5 trading days is on or before ``as_of``.
    The horizon runs from the entry bar, not the decision date —
    the two differ when a call is dated a non-trading day or when
    a crypto/FX call uses the previous day's bar. Provisional and
    excluded calls never make a call due or not due — they are
    invisible to this check.
    """
    mine = [c for c in calls if c["symbol"] == symbol
            and _counts_toward(c)]
    if not mine:
        return True
    latest = max(_parse_day(c.get("bar_date") or c["date"]) for c in mine)
    next_due = latest
    remaining = HORIZON_TRADING_DAYS
    while remaining > 0:
        next_due += timedelta(days=1)
        if is_trading_day(profile_key, next_due, observed_dates):
            remaining -= 1
    return as_of >= next_due


def _session_close_utc(profile_key, bar_date):
    """UTC instant when the exchange session for ``bar_date`` closed.

    The profile's ``session_close`` is a local time and its
    ``timezone`` an IANA name, so the conversion is DST-aware for
    the bar's own date (a November bar closes at 21:00 UTC in New
    York; a July bar at 20:00 UTC).
    """
    profile = get_profile(profile_key)
    hour, minute = (int(part) for part in
                    profile.session_close.split(":"))
    local_close = datetime.combine(
        _parse_day(bar_date), time(hour, minute),
        tzinfo=ZoneInfo(profile.timezone))
    return local_close.astimezone(timezone.utc)


def _bar_complete(symbol, profile_key, bar_date, now):
    """True when the bar dated ``bar_date`` is complete at ``now``.

    Equities/ETFs: the exchange session for the bar's date must
    have closed by ``now`` (UTC). Crypto and FX have no daily
    session, so their bar is complete only when it is dated before
    the current UTC day — a bar dated today is still in progress.
    """
    if profile_key in ("crypto", "fx"):
        return _parse_day(bar_date) < now.date()
    return now >= _session_close_utc(profile_key, bar_date)


def _settle_due_calls(calls_path, calls, symbol, profile_key, bars,
                      observed_dates, as_of, fetch_index, missed_path):
    """Settle this ticker's open calls whose horizon has passed.

    Returns the number settled. A call whose settlement-date bar is
    missing stays open and is recorded as a missed day — never
    settled against the next bar's close.
    """
    settled = 0
    for call in [c for c in calls if c["symbol"] == symbol
                 and c.get("status") == "open"]:
        if call.get("provisional") or call.get("status") == "excluded":
            continue  # provisional/excluded calls are never settled
        # The horizon runs from the ENTRY BAR, not the decision date
        # — the two differ when a call is dated a non-trading day or
        # when a crypto/FX call uses the previous day's bar. Legacy
        # calls have no bar_date, so fall back to the decision date.
        call_date = _parse_day(call.get("bar_date") or call["date"])
        settle_day = settlement_date(profile_key, call_date,
                                     HORIZON_TRADING_DAYS, observed_dates)
        if as_of < settle_day:
            continue
        realised_close = _close_on(bars, settle_day)
        if realised_close is None:
            _record_missed_once(missed_path, as_of, symbol,
                           f"no bar on settlement date {settle_day}")
            continue
        index_symbol = INDEX_SYMBOLS[symbol]
        if index_symbol is None:
            index_return = 0.0
            index_source = NO_INDEX_SOURCE[symbol]
        else:
            try:
                index_bars = fetch_index(index_symbol)
            except Exception as exc:  # noqa: BLE001 — a failed index
                # fetch is a missed day, not a crash.
                _record_missed_once(missed_path, as_of, symbol,
                               f"index fetch failed for {index_symbol}: "
                               f"{exc}")
                continue
            index_entry = _close_on(index_bars, call_date)
            index_settle = _close_on(index_bars, settle_day)
            if index_entry is None or index_settle is None:
                _record_missed_once(
                    missed_path, as_of, symbol,
                    f"no index bar for {index_symbol} on {call_date} "
                    f"or {settle_day}")
                continue
            index_return = index_settle / index_entry - 1.0
            index_source = f"yahoo:{index_symbol}"
        entry_close = float(call["entry_close"])
        prev_ret = call.get("prev_5d_return")
        if prev_ret is None:
            naive_expected, naive_hit = None, None
        else:
            naive_expected = "up" if prev_ret >= 0 else "down"
            realised = float(realised_close) / entry_close - 1.0
            naive_hit = (realised > 0) if naive_expected == "up" \
                else (realised < 0)
        bnh_hit = (float(realised_close) / entry_close - 1.0) > 0
        # Baselines ride on the settlement row: one append, not a
        # settle-then-update rewrite (see toolkit/decision_log.py).
        settle_call(
            calls_path, call["call_id"], realised_close, entry_close,
            index_return, index_source, as_of.isoformat(),
            naive_expected=naive_expected, naive_hit=naive_hit,
            bnh_hit=bnh_hit)
        settled += 1
    return settled


def _decide_call(calls_path, calls, symbol, profile_key, bars, as_of,
                 source="unknown", now=None, provisional=False):
    """Log one new call for ``symbol`` when due. Returns True if logged.

    The direction comes from the registered rule evaluated on the
    point-in-time close slice only. Idempotent: a call for the same
    date/symbol/rule already in the log is skipped — provisional and
    excluded calls do not count for this check, so they never block
    a replacement call. ``source`` is the fetch result's actual
    source (e.g. "yahoo", "coingecko") — never assumed. The call
    records ``bar_date`` (the entry bar's date) and ``bar_complete``
    (whether that bar was complete at ``now``); a call logged from
    an incomplete bar is possible only with ``provisional=True``,
    and is labelled provisional.

    Entry bar = the last COMPLETE bar. An in-progress last bar is
    dropped for crypto/FX (no close to wait for — the daily bar
    only completes at midnight UTC, so the last complete bar is
    yesterday's); for equities/ETFs a partial bar means the session
    is still open, and the run must refuse (or log it as
    provisional) rather than decide on a half-formed bar. The rule
    is evaluated on the entry bars only — never on a partial bar.
    """
    observed = frozenset(b["date"] for b in bars)
    if not _call_due(calls, symbol, as_of, profile_key, observed):
        return False
    existing = [c for c in calls
                if c["symbol"] == symbol and c["date"] == as_of.isoformat()
                and not c.get("provisional")
                and c.get("status") != "excluded"]
    if existing:
        return False  # idempotent re-run: already logged today
    closes = [(b["date"], float(b["close"])) for b in bars]
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    last_date, _last_close = closes[-1]
    if _bar_complete(symbol, profile_key, last_date, now):
        entry_bars = closes
        bar_complete = True
        is_provisional = False
    elif profile_key in ("crypto", "fx"):
        if len(closes) < 2:
            raise RefusedRunError(
                f"refusing to log {symbol} for {as_of}: no complete "
                "bar exists — the only bar is in progress.")
        entry_bars = closes[:-1]  # drop the in-progress bar
        bar_complete = True
        is_provisional = False
    elif provisional:
        entry_bars = closes  # decide on the in-progress bar, labelled
        bar_complete = False
        is_provisional = True
    else:
        # Unreachable from run_day (it refuses first), but a direct
        # caller must get the same refusal, not a silent bad call.
        raise RefusedRunError(
            f"refusing to log {symbol} for {as_of}: bar {last_date} "
            "is incomplete at fetch time — the session has not "
            "closed. Re-dispatch after the close, or pass "
            "provisional=True to log it as a provisional call "
            "(never scored).")
    entry_date, entry_close = entry_bars[-1]
    rule_id, direction = direction_for(
        symbol, [c for _, c in entry_bars])
    if direction is None:
        return False
    prev_ret = _prev_5d_return(entry_bars)
    rule = RULES[rule_id]
    window = rule["params"]["window"]
    if rule_id == "sma50_trend":
        average = sum(c for _, c in entry_bars[-window:]) / window
        thesis = (f"{rule_id} v{rule['version']}: bar {entry_date} "
                  f"close {entry_close:.2f} vs 50-day SMA "
                  f"{average:.2f} -> {direction.upper()}.")
    else:
        ret = (entry_bars[-1][1] / entry_bars[-window - 1][1] - 1.0)
        thesis = (f"{rule_id} v{rule['version']}: 20-trading-day "
                  f"return {ret:.2%} -> {direction.upper()}.")
    call = {
        "date": as_of.isoformat(),
        "symbol": symbol,
        "exchange": EXCHANGE_LABELS[symbol],
        "profile": profile_key,
        "persona": PERSONAS[symbol],
        "rule_id": rule_id,
        "rule_version": rule["version"],
        "rule_code_hash": rule_code_hash(),
        "expected": direction,
        "horizon_days": HORIZON_TRADING_DAYS,
        "entry_close": entry_close,
        "bar_date": entry_date,
        "bar_complete": bar_complete,
        "provisional": is_provisional,
        "confidence": 0.5,
        "confidence_basis": ("neutral prior — the rule states no "
                             "probability; 0.5 is not a measured "
                             "probability"),
        "sources": [source],
        "thesis": thesis,
        "prev_5d_return": prev_ret,
    }
    write_call(calls_path, call)
    return True


def run_day(calls_path, out_path, bars_dir, missed_path, as_of,
            fetch=fetch_prices, now=None, provisional=False):
    """Run one daily step. Returns a summary dict.

    ``fetch`` is injectable ``(symbol, kind) -> result dict`` with a
    ``pairs`` list of (date, close); the default is the real Yahoo
    chain. ``now`` is the run instant (UTC, default: this moment) —
    it is what the bar-completeness check reads, not ``as_of``.
    ``provisional`` permits logging calls from incomplete bars as
    provisional calls (never scored); without it, such a run is
    refused before anything is logged. All file writes stay under
    the given paths — this function never touches git or main.

    Two phases, so a refusal is atomic: phase 1 fetches everything
    and writes nothing; phase 2 settles and logs. A refused run
    leaves the calls file untouched.
    """
    as_of = _parse_day(as_of)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    calls_path = Path(calls_path)
    out_path = Path(out_path)
    bars_dir = Path(bars_dir)
    bars_dir.mkdir(parents=True, exist_ok=True)
    calls = load_calls(calls_path)
    logged = settled = missed = 0
    provisional_logged = 0

    def fetch_index(index_symbol):
        result = fetch(index_symbol, "stock")
        bars, _ = _merge_bars([], result["pairs"])
        return bars

    # Phase 1 — fetch everything, write nothing.
    fetched = {}
    missed_records = []
    for symbol in WATCHLIST_PROFILES:
        profile_key = WATCHLIST_PROFILES[symbol]
        cache_path = bars_dir / f"{symbol}.json"
        cached = _load_json(cache_path)
        try:
            result = fetch(symbol, KINDS[symbol])
        except Exception as exc:  # noqa: BLE001 — a failed fetch is a
            # missed day, not a crash.
            missed_records.append((as_of, symbol, f"fetch failed: {exc}"))
            continue
        bars, _ = _merge_bars(
            cached["bars"] if cached else [], result["pairs"])
        store = _pit_store(bars)
        pit_bars = store.bars_as_of(as_of)
        fetched[symbol] = (bars, pit_bars,
                           str(result.get("source", "unknown")), profile_key)

    # Refusal check — a call due from an incomplete bar refuses the
    # whole run unless --provisional. Nothing has been logged yet.
    # Crypto/FX never refuse: their in-progress bar is dropped and
    # the call uses the last complete bar (no close to wait for).
    refused = []
    provisional_for = {}
    for symbol, (_bars, pit_bars, _source, profile_key) in fetched.items():
        if not pit_bars:
            continue
        # Observed dates for the special-session rule come from the
        # PIT slice only: a bar dated after the decision date must
        # never count toward settlement.
        observed = frozenset(b["date"] for b in pit_bars)
        if not _call_due(calls, symbol, as_of, profile_key, observed):
            continue
        bar_date = pit_bars[-1]["date"]
        if _bar_complete(symbol, profile_key, bar_date, now):
            continue
        if profile_key in ("crypto", "fx"):
            continue  # in-progress bar is dropped; last complete bar
        if provisional:
            provisional_for[symbol] = True
        else:
            refused.append((symbol, bar_date))
    if refused:
        detail = "; ".join(
            f"{symbol} (bar {bar_date} not complete at "
            f"{now.strftime('%H:%M')} UTC)"
            for symbol, bar_date in refused)
        raise RefusedRunError(
            f"refusing run for {as_of}: in-progress bars — {detail}. "
            "The session has not closed. Re-dispatch after the close, "
            "or pass --provisional to log provisional calls "
            "(labelled, never scored).")

    # Phase 2 — settle, then log.
    for symbol, (bars, pit_bars, source, profile_key) in fetched.items():
        cache_path = bars_dir / f"{symbol}.json"
        _save_json(cache_path, {"symbol": symbol, "bars": bars})
        observed = frozenset(b["date"] for b in pit_bars)
        settled += _settle_due_calls(
            calls_path, load_calls(calls_path), symbol, profile_key,
            pit_bars, observed, as_of, fetch_index, missed_path)
        calls = load_calls(calls_path)
        is_provisional = provisional_for.get(symbol, False)
        if _decide_call(calls_path, calls, symbol, profile_key,
                        pit_bars, as_of, source=source, now=now,
                        provisional=is_provisional):
            logged += 1
            if is_provisional:
                provisional_logged += 1
            calls = load_calls(calls_path)
    for day, symbol, reason in missed_records:
        if _record_missed_once(missed_path, day, symbol, reason):
            missed += 1
    all_calls = load_calls(calls_path)
    # A run with zero missed days writes no missed-day record, so the
    # file would not exist — and publish() copies all three outputs.
    # Guarantee the file exists (empty when nothing was missed) so a
    # first run publishes cleanly instead of dying in `cp`.
    missed_path = Path(missed_path)
    if not missed_path.exists():
        missed_path.parent.mkdir(parents=True, exist_ok=True)
        missed_path.touch()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        track_record_mod.render_track_record_md(all_calls),
        encoding="utf-8")
    rows = load_rows(calls_path)
    head_hash = row_hash(rows[-1]) if rows else None
    return {"as_of": as_of.isoformat(), "calls_logged": logged,
            "calls_settled": settled, "missed_days": missed,
            "provisional_logged": provisional_logged,
            "head_hash": head_hash,
            "n_settled": sum(1 for c in all_calls
                             if c.get("status") == "settled")}


def _check_publish_invariant(outputs, worktree):
    """Fail loudly unless the push is append-only (PR 7.0).

    For every ``*.jsonl`` output that already exists on the remote
    branch (``worktree`` holds the remote tip): the local file must
    start with the remote file's bytes — additions only, no row
    removed or changed. The calls log's hash chain must also
    verify. Raises RuntimeError on the first violation, before
    anything is copied or committed.
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
        if src.name in (CALLS_FILE, CALLS_FILE_LEGACY):
            problems = verify_chain(src)
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
    missing output — above all the calls log — is an error, not
    something to paper over with an empty file.

    Append-only invariant (PR 7.0, after the November 2026
    overwrite incident): before copying, every ``*.jsonl`` output
    is checked against the remote branch's version — the local
    file must start with the remote's bytes, so the diff has only
    additions — and the calls log's hash chain must verify. A
    mismatch fails the run loudly instead of pushing. The remote
    ref is fetched first, so the check reads the true tip.
    """
    # main() passes argparse strings; callers may also pass Paths.
    # Normalize once so every later use (cp loop, git add) sees
    # Path objects — a string has no .name and died in `git add`.
    outputs = [Path(o) for o in outputs]
    if branch == "main":
        raise ValueError("refusing to publish track-record results to "
                         "main — results live on a data branch only")
    root = Path(repo_root) if repo_root else Path.cwd()
    worktree = Path(tempfile.mkdtemp(prefix="track-record-"))
    try:
        existing = subprocess.run(
            ["git", "ls-remote", "--exit-code", "--heads", "origin",
             branch], cwd=root, capture_output=True, check=False)
        if existing.returncode == 0:
            # Fetch first: the local ref may be stale, and the
            # invariant below must read the true remote tip.
            subprocess.run(["git", "fetch", "origin", branch], cwd=root,
                           check=True, capture_output=True)
            subprocess.run(["git", "worktree", "add", str(worktree),
                            branch], cwd=root, check=True,
                           capture_output=True)
        else:
            subprocess.run(["git", "worktree", "add", "-b", branch,
                            str(worktree)], cwd=root, check=True,
                           capture_output=True)
        _check_publish_invariant(outputs, worktree)
        for src in outputs:
            src = Path(src)
            dest = worktree / src.name
            if not src.exists():
                if src.name == "missed_days.jsonl":
                    # First run with zero missed days: no record was
                    # ever written. Publish an empty log rather than
                    # failing the whole run in `cp`.
                    dest.touch()
                    continue
                raise FileNotFoundError(
                    f"track-record output missing: {src}")
            subprocess.run(["cp", str(src), str(dest)], check=True)
        subprocess.run(["git", "config", "user.name", "track-record"],
                       cwd=worktree, check=True)
        subprocess.run(["git", "config", "user.email",
                        "track-record@users.noreply.github.com"],
                       cwd=worktree, check=True)
        subprocess.run(["git", "add"] + [p.name for p in outputs],
                       cwd=worktree, check=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"],
                              cwd=worktree, check=False)
        if diff.returncode == 0:
            return None
        message = (f"Daily track record: "
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
    """CLI: ``python3 scripts/track_record_daily.py [options]``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls",
                        default="data/track-record/calls.jsonl")
    parser.add_argument("--out",
                        default="data/track-record/track-record.md")
    parser.add_argument("--bars-dir", default="data/track-record/bars")
    parser.add_argument("--missed",
                        default="data/track-record/missed_days.jsonl")
    parser.add_argument("--as-of", default=None,
                        help="YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--publish-branch", default="data/track-record")
    parser.add_argument("--publish", action="store_true",
                        help="commit outputs to --publish-branch "
                             "(never main)")
    parser.add_argument("--provisional", action="store_true",
                        help="allow calls from in-progress bars, "
                             "labelled provisional and never scored; "
                             "without it, such a run is refused")
    args = parser.parse_args(argv)
    as_of = (date.fromisoformat(args.as_of) if args.as_of
             else datetime.now(timezone.utc).date())
    try:
        summary = run_day(args.calls, args.out, args.bars_dir,
                          args.missed, as_of,
                          provisional=args.provisional)
    except RefusedRunError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    if args.publish:
        message = publish([args.calls, args.out, args.missed],
                          args.publish_branch)
        print(f"publish: {message or 'no changes'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
