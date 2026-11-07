"""Refresh derived datasets from the CSVs already in the repo.

Usage:
  python3 scripts/refresh_derived.py [--csv PATH ...]
                                     [--out-dir DIR] [--n-sims N]

For each OHLCV CSV (default: ``demo.csv``), computes the indicator
snapshot, the named-setup screening matches and the Time-Machine odds
summary, and writes ``data/derived/<stem>.json`` with provenance:
source file, ``as_of`` (last bar date), ``generated_at`` (UTC) and a
``stale`` flag against the 1-day daily-bars SLA. Derived analytics
only — no raw vendor data is republished, and the demo CSV is
synthetic (see its header).

Stale input is FLAGGED, never published silently: a stale CSV still
produces a dataset (its previous good output is not silently kept
either — the flag travels in the file), a warning goes to stderr, and
the run still exits 0. A real failure — a missing or corrupt CSV, or
an unwritable output — prints ``error: ...`` to stderr and exits 2
after the remaining CSVs have been attempted, so a scheduled run can
alert on it. The actual email alert is a separate, parent-owned step.

Research only. Never investment advice. Time-Machine odds describe
the past sample; they are not a probability of future profit. All
time-sensitive reads go through the bars' own dates (``as_of``); no
look-ahead: nothing here reads a price dated after ``as_of``.
"""
import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import indicators, terminal, timemachine
from toolkit.connectors import evaluate_freshness
from toolkit.position_plan import plan_exit

INDICATOR_KEYS = (
    "rsi_14", "macd", "macd_signal", "macd_histogram",
    "bollinger_upper", "bollinger_middle", "bollinger_lower",
    "atr_14", "sma_20", "ema_20", "adx_14",
    "stochastic_k", "stochastic_d",
)


def _last_value(series):
    if not series:
        return None
    return series[-1]


def _display_path(path):
    """Repo-relative path when the CSV sits under the working
    directory (so committed datasets never carry a local absolute
    path); the given path otherwise."""
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def refresh_one(csv_path, out_path, *, n_sims=1000, seed=19, horizon=20,
                now=None, today=None):
    """Build one derived dataset from ``csv_path`` and write it to
    ``out_path``. Returns the dataset dict. Raises ValueError (bad
    CSV / too few bars / unplannable) or OSError (unreadable /
    unwritable) with a plain message — the caller turns it into an
    exit code, never a traceback."""
    now = now or datetime.now(timezone.utc)
    today = today or date.today()
    bars = terminal.load_bars(str(csv_path))
    if len(bars) < 20:
        raise ValueError(
            f"{csv_path}: need at least 20 bars for a derived dataset, "
            f"got {len(bars)}")
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b["volume"] for b in bars] \
        if all(b["volume"] is not None for b in bars) else None
    last = bars[-1]
    as_of = last["date"]
    judged = evaluate_freshness(as_of, 86400, today=today)

    all_ind = indicators.compute_all(closes, highs, lows, volumes)
    snapshot = {key: _last_value(all_ind[key]) for key in INDICATOR_KEYS}
    if volumes is not None:
        snapshot["vwap"] = _last_value(all_ind["vwap"])

    setups = terminal.evaluate_setups(closes)
    matches = sorted(name for name, (hit, _d) in setups.items() if hit)
    setup_detail = {name: detail for name, (_h, detail) in setups.items()}

    entry = last["close"]
    atr_value = snapshot["atr_14"]
    stop, _target, _basis = terminal._default_plan(entry, atr_value)
    # Let plan_exit derive the target from the stop (as the terminal
    # does for its default plan): passing the pre-rounded target back
    # in can sit a hair below R=2 after cent-rounding and be refused.
    exit_plan = plan_exit(entry, stop, risk_reward=2.0)
    ohlc = [{"open": b["open"], "high": b["high"], "low": b["low"],
             "close": b["close"]} for b in bars]
    sim = timemachine.simulate(ohlc, exit_plan["entry"], exit_plan["stop"],
                               exit_plan["target"], n_sims=n_sims,
                               seed=seed, horizon=horizon)
    replay = timemachine.replay_history(
        ohlc, exit_plan["entry"], exit_plan["stop"], exit_plan["target"],
        horizon=horizon)

    dataset = {
        "dataset": Path(csv_path).stem,
        "source_csv": _display_path(csv_path),
        "generated_at": now.isoformat(timespec="seconds"),
        "generated_by": "scripts/refresh_derived.py",
        "as_of": as_of,
        "n_bars": len(bars),
        "last_close": last["close"],
        "stale": judged["status"] == "STALE",
        "sla_label": "1 day",
        "age_days": judged["age_days"],
        "stale_note": (
            "STALE: last bar is older than the 1-day SLA — do not "
            "treat this dataset as fresh."
            if judged["status"] == "STALE" else ""),
        "indicators_last": snapshot,
        "setups_matched": matches,
        "setups_detail": setup_detail,
        "hypothetical_plan": {
            "entry": exit_plan["entry"],
            "stop": exit_plan["stop"],
            "target": exit_plan["target"],
            "reward_risk": exit_plan["reward_risk"],
        },
        "timemachine": {
            "n_sims": sim["n_sims"],
            "seed": sim["seed"],
            "horizon": sim["horizon"],
            "n_windows": sim["n_windows"],
            "target_first": sim["target_first"],
            "stop_first": sim["stop_first"],
            "neither": sim["neither"],
            "break_even": sim["break_even"],
            "verdict": sim["verdict"],
            "median_close": sim["median_close"],
            "close_p5": sim["close_p5"],
            "close_p95": sim["close_p95"],
            "average_R": sim["average_R"],
            "replay_target_first": replay["target_first"],
            "replay_stop_first": replay["stop_first"],
            "replay_neither": replay["neither"],
            "replay_n_windows": replay["n_windows"],
        },
    }
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(dataset, indent=2) + "\n",
                        encoding="utf-8")
    return dataset


def main(argv=None):
    """CLI entry point. Returns 0 on success, 2 if any CSV failed
    (after attempting the rest). Stale input warns but exits 0."""
    parser = argparse.ArgumentParser(
        description="Refresh derived datasets (indicators, screening, "
                    "Time-Machine) from repo CSVs.")
    parser.add_argument("--csv", dest="csvs", action="append", default=None,
                        help="OHLCV CSV to refresh (repeatable; "
                             "default: demo.csv)")
    parser.add_argument("--out-dir", default=None,
                        help="output directory "
                             "(default: <repo-root>/data/derived)")
    parser.add_argument("--n-sims", type=int, default=1000,
                        help="Time-Machine replays per dataset "
                             "(default: 1000)")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    csvs = args.csvs or [str(root / "demo.csv")]
    out_dir = Path(args.out_dir) if args.out_dir else root / "data" / "derived"
    failures = []
    for csv_path in csvs:
        out_path = out_dir / (Path(csv_path).stem + ".json")
        try:
            dataset = refresh_one(csv_path, out_path, n_sims=args.n_sims)
        except (ValueError, OSError) as exc:
            print(f"error: refresh failed for {csv_path}: {exc}",
                  file=sys.stderr)
            failures.append(csv_path)
            continue
        flag = "STALE" if dataset["stale"] else "fresh"
        print(f"refreshed {csv_path} -> {out_path} "
              f"(as of {dataset['as_of']}, {dataset['n_bars']} bars, {flag})")
        if dataset["stale"]:
            print(f"warning: {csv_path} is STALE (last bar "
                  f"{dataset['as_of']}, age {dataset['age_days']}d > SLA "
                  f"1 day) — flagged in {out_path}, not published as fresh",
                  file=sys.stderr)
    if failures:
        print(f"error: {len(failures)} dataset(s) failed to refresh: "
              f"{failures}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
