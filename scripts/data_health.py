"""Data-health report: per-source freshness SLA status as JSON.

Usage:
  python3 scripts/data_health.py [--cache-dir DIR] [--output PATH]
                                 [--probe] [--repo-root DIR]

Writes ``data_health.json`` (default: repo root) with one entry per
source: last-fetch time, last data date, age, SLA status
(FRESH / STALE / UNAVAILABLE / UNKNOWN) and cache hits. If data is
stale, it is FLAGGED in the output — never published silently.

Modes:
- Cache-only (default): reads only the on-disk cache and the
  user-supplied CSVs under ``data/``. Never touches the network, so
  it is safe to run offline and in tests. Exit 0 unless the report
  cannot be written.
- ``--probe``: attempts a live fetch per network source (through
  ``toolkit/connectors.py``, cache-first, bounded retries, never
  retried on 403/429). A probe that fails marks its source
  UNAVAILABLE with the error text. Exit 2 when the overall status is
  DEGRADED (any source STALE or UNAVAILABLE) so a scheduled workflow
  can alert on it; the report file is still written first, so the
  evidence is never lost. The actual email alert is a separate,
  parent-owned step — this script's job is the exit code and the
  clear error text.

Overall status: FRESH (every source FRESH), DEGRADED (any STALE or
UNAVAILABLE), PARTIAL (some FRESH, the rest UNKNOWN — e.g. no
user-supplied NEPSE CSV is in the repo yet), UNKNOWN (nothing known).

Research only. No raw vendor data is written here — only fetch times,
dates, ages and counts. NEPSE stays CSV-only: the nepse_csv entry
reads the newest user-supplied CSV under ``data/`` and reports
UNKNOWN when there is none.
"""
import argparse
import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit.connectors import (
    FRESHNESS_SLA_SECONDS,
    DiskCache,
    evaluate_freshness,
)

SOURCE_ORDER = ("yahoo", "coingecko", "ecb", "worldbank", "nepse_csv")

SOURCE_LABELS = {
    "yahoo": "Yahoo Finance chart API",
    "coingecko": "CoinGecko keyless public API",
    "ecb": "ECB euro reference rates",
    "worldbank": "World Bank API v2",
    "nepse_csv": "User-supplied NEPSE CSV (data/*.csv)",
}

SOURCE_KINDS = {
    "yahoo": "stock_daily",
    "coingecko": "crypto_daily",
    "ecb": "fx_reference",
    "worldbank": "macro_annual",
    "nepse_csv": "nepse_csv_daily",
}

# Cache keys are "<connector>:<symbol>"; the health script only needs
# the latest entry per connector, whatever the symbol was.
_CACHE_PREFIX = {
    "yahoo": "yahoo:",
    "coingecko": "coingecko:",
    "ecb": "ecb:",
    "worldbank": "worldbank:",
}


def _now_utc():
    return datetime.now(timezone.utc)


def _sla_label(seconds):
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} day" if days == 1 else f"{days} days"
    hours = seconds // 3600
    return f"{hours} hours"


def _newest_csv_date(data_dir):
    """Last (max) date across user-supplied CSVs under data/.

    Returns (date_str, path) or (None, None). A CSV that cannot be
    read is skipped with its path recorded — it is not data.
    """
    newest = None
    newest_path = None
    skipped = []
    try:
        files = sorted(Path(data_dir).glob("*.csv"))
    except OSError:
        return None, None, []
    for path in files:
        try:
            with open(path, newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if "date" not in (reader.fieldnames or []):
                    raise ValueError(f"{path}: no 'date' column")
                rows = [r for r in reader if r.get("date")]
            if not rows:
                raise ValueError(f"{path}: no dated rows")
            latest = max(r["date"].strip() for r in rows)
            date.fromisoformat(latest)
        except (OSError, ValueError, KeyError):
            skipped.append(str(path))
            continue
        if newest is None or latest > newest:
            newest, newest_path = latest, str(path)
    return newest, newest_path, skipped


def _real_probes(cache_dir=None):
    """Live probe callables: name -> fn() returning last data date.

    Each probe is one cheap call through the toolkit's own connectors
    (cache-first, bounded retries, never retried on 403/429), so a
    probe run also populates the on-disk cache for later cache-only
    runs. A probe that raises marks its source UNAVAILABLE.
    """
    from toolkit.connectors import (  # deferred: keeps this module importable offline
        fetch_fx_rates,
        fetch_macro,
        fetch_prices,
    )

    cache = DiskCache(cache_dir)
    return {
        "yahoo": lambda: fetch_prices(
            "AAPL", kind="stock", cache=cache)["provenance"]["last_date"],
        "coingecko": lambda: fetch_prices(
            "BTC", kind="crypto", cache=cache)["provenance"]["last_date"],
        "ecb": lambda: fetch_fx_rates(cache=cache)["date"],
        "worldbank": lambda: fetch_macro(
            "NPL", "NY.GDP.MKTP.CD", cache=cache)["provenance"]["last_date"],
    }


def _source_from_cache(name, stats):
    """Latest cache entry for a connector, or None."""
    prefix = _CACHE_PREFIX.get(name)
    if prefix is None:
        return None
    candidates = [(k, v) for k, v in stats.items() if k.startswith(prefix)]
    if not candidates:
        return None
    key, entry = max(candidates, key=lambda kv: kv[1].get("fetched_at") or "")
    return {"cache_key": key, **entry}


def build_health_report(*, cache_dir=None, probes=None, previous=None,
                        repo_root=None, now=None):
    """Build the health report dict. Pure except for the probe
    callables and the data/ scan; ``now`` is injectable for tests.

    ``probes``: dict name -> callable returning the last data date,
    or None for cache-only mode. ``previous``: a prior report dict
    (carries forward last_fetch / last_error when a probe fails).
    """
    now = now or _now_utc()
    today = now.date()
    repo_root = Path(repo_root) if repo_root else Path.cwd()
    stats = DiskCache(cache_dir).stats()
    prev_by_name = {}
    if previous:
        for entry in previous.get("sources", []) or []:
            prev_by_name[entry.get("name")] = entry
    probes = probes or {}

    sources = []
    for name in SOURCE_ORDER:
        kind = SOURCE_KINDS[name]
        sla = FRESHNESS_SLA_SECONDS[kind]
        prev = prev_by_name.get(name, {})
        entry = {
            "name": name,
            "label": SOURCE_LABELS[name],
            "kind": kind,
            "sla_seconds": sla,
            "sla_label": _sla_label(sla),
            "status": "UNKNOWN",
            "last_fetch": None,
            "last_data_date": None,
            "age_days": None,
            "age_trading_days": None,
            "cache_hits": 0,
            "error": None,
            "note": "",
        }
        cached = _source_from_cache(name, stats)
        if cached is not None:
            entry["last_fetch"] = cached.get("fetched_at")
            entry["last_data_date"] = cached.get("last_date")
            entry["cache_hits"] = int(cached.get("hits", 0))
        if name == "nepse_csv":
            last_date, path, skipped = _newest_csv_date(repo_root / "data")
            if last_date is not None:
                entry["last_data_date"] = last_date
                entry["note"] = f"newest user-supplied CSV: {path}"
                try:
                    entry["last_fetch"] = datetime.fromtimestamp(
                        Path(path).stat().st_mtime,
                        tz=timezone.utc).isoformat(timespec="seconds")
                except OSError:
                    entry["last_fetch"] = None
            else:
                entry["note"] = (
                    "no user-supplied NEPSE CSV under data/ yet; "
                    "NEPSE stays CSV-only")
                if skipped:
                    entry["note"] += f"; skipped unreadable: {skipped}"
            if entry["last_data_date"] is not None:
                judged = evaluate_freshness(
                    entry["last_data_date"], sla, today=today,
                    kind="nepse_csv_daily")
                entry["status"] = judged["status"]
                entry["age_days"] = judged["age_days"]
                entry["age_trading_days"] = judged["age_trading_days"]
            sources.append(entry)
            continue
        if name in probes:
            try:
                last_date = probes[name]()
                entry["last_data_date"] = str(last_date)
                entry["last_fetch"] = now.isoformat(timespec="seconds")
                entry["error"] = None
                judged = evaluate_freshness(
                    entry["last_data_date"], sla, today=today, kind=kind)
                entry["status"] = judged["status"]
                entry["age_days"] = judged["age_days"]
                entry["age_trading_days"] = judged["age_trading_days"]
            except Exception as exc:  # noqa: BLE001 — probe failure is data
                entry["status"] = "UNAVAILABLE"
                entry["error"] = str(exc)
                entry["last_fetch"] = prev.get("last_fetch")
                entry["last_data_date"] = prev.get("last_data_date")
                entry["note"] = "live probe failed; last known values kept"
        elif entry["last_data_date"] is not None:
            judged = evaluate_freshness(
                entry["last_data_date"], sla, today=today, kind=kind)
            entry["status"] = judged["status"]
            entry["age_days"] = judged["age_days"]
            entry["age_trading_days"] = judged["age_trading_days"]
            entry["note"] = "from on-disk cache only (no probe this run)"
        else:
            entry["note"] = "never fetched (no cache entry, no probe)"
        sources.append(entry)

    statuses = [s["status"] for s in sources]
    if all(s == "FRESH" for s in statuses):
        overall = "FRESH"
    elif any(s in ("STALE", "UNAVAILABLE") for s in statuses):
        overall = "DEGRADED"
    elif any(s == "FRESH" for s in statuses):
        overall = "PARTIAL"
    else:
        overall = "UNKNOWN"
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "generated_by": "scripts/data_health.py",
        "overall": overall,
        "sources": sources,
    }


def main(argv=None):
    """CLI entry point. Returns an exit code; never raises on a
    failed probe (failures are data, reported in the JSON)."""
    parser = argparse.ArgumentParser(
        description="Write data_health.json with per-source SLA status.")
    parser.add_argument("--cache-dir", default=None,
                        help="on-disk cache dir (default: "
                             "TOOLKIT_CACHE_DIR or ~/.cache/...)")
    parser.add_argument("--output", default=None,
                        help="where to write the report "
                             "(default: <repo-root>/data_health.json)")
    parser.add_argument("--probe", action="store_true",
                        help="attempt a live fetch per network source")
    parser.add_argument("--repo-root", default=None,
                        help="repo root (default: this script's parent)")
    args = parser.parse_args(argv)
    root = Path(args.repo_root) if args.repo_root else \
        Path(__file__).resolve().parent.parent
    out_path = Path(args.output) if args.output else root / "data_health.json"
    previous = None
    if out_path.exists():
        try:
            previous = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
    cache = DiskCache(args.cache_dir)
    probes = _real_probes(cache.cache_dir) if args.probe else None
    report = build_health_report(
        cache_dir=cache.cache_dir, probes=probes, previous=previous,
        repo_root=root)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2) + "\n",
                            encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write {out_path}: {exc.strerror or exc}",
              file=sys.stderr)
        return 2
    bad = [s for s in report["sources"]
           if s["status"] in ("STALE", "UNAVAILABLE")]
    print(f"data health: {report['overall']} -> {out_path}")
    for source in report["sources"]:
        print(f"  {source['status']:11s} {source['name']:11s} "
              f"last data {source['last_data_date']} "
              f"age {source['age_days']}d (SLA {source['sla_label']})")
    if args.probe and bad:
        detail = "; ".join(
            f"{s['name']} {s['status']}"
            + (f" ({s['error']})" if s["error"] else "")
            for s in bad)
        print(f"error: data health DEGRADED: {detail}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
