"""Connector chain: one standard interface per data source, with
fallbacks, on-disk caching and bounded retries.

Research only. Never investment advice, never a buy/sell recommendation.
Every series returned carries its source, retrieval time, last date and
age, so stale data is never presented as fresh. A source that fails is
recorded in the ``tried`` list with its reason; if every source fails
the error lists EVERY attempt. Missing data is an abstention with a
reason, never a silent zero-fill.

Architecture (design only — no code copied from any of them):
- OpenBB (Apache-2.0): the provider contract — one standardised model
  per endpoint, one connector per source, and the core never touches a
  vendor API. Adding a source means adding one connector, not touching
  the chain. Vendor payloads are untrusted input: they are validated
  before they may be published (see ``toolkit/data_layer.py``).
- qf-lib (Apache-2.0): the prefetching/caching wrapper — a cache sits
  between the chain and the network, and CSV is a first-class source.
- augur (MIT): evidence time-semantics — every item carries its
  business date (``last_date``), its fetch time (``retrieved_at``) and
  its age, and a stale series is flagged, never relabelled.

Sources, in priority order (see DATA_SOURCES.md for the audit trail):
1. Caller-supplied CSVs — the ONLY NEPSE path. There is no
   documented free public NEPSE OHLCV API; NEPSE stays CSV-only and
   no live feed is invented. The scanner repo owns NEPSE CSV loading
   (``nepali-capital-market-research/scanner/data.py: load_csv``); this
   module's CSV connector accepts any loader with the same
   ``(path) -> [(date, close)]`` shape, so the scanner loader plugs
   in without a copy.
2. Yahoo Finance chart API (stock/ETF daily closes, no key).
3. Stooq daily CSV (stock fallback only; it returned no data on
   28 Oct 2026 and again on 7 Nov 2026 — it is never relied on alone).
4. CoinGecko keyless public API (crypto daily prices, no key).
5. ECB euro reference rates (daily FX fixings, latest only).
6. World Bank API v2 (macro indicators by country/year, no key).

Retry rules (bounded, polite — the chain must never hammer a source):
- Transient failures only (timeouts, connection errors, HTTP 5xx)
  are retried, at most 3 attempts total, with exponential backoff.
- HTTP 403/429/404 and validation failures are NEVER retried: a 403
  or 429 is a hard stop for that source in this run, recorded as a
  failure, and the chain moves on. Callers must not re-run the chain
  in a tight loop.
- No paywalled, login-gated, or undocumented endpoint is called.
  Redistribution terms that are TO VERIFY mean derived analytics
  only — never republish raw vendor data as a dataset.

Caching: network fetches go through :class:`DiskCache` (JSON files
under ``~/.cache/investment-analyst-toolkit``, overridable with the
``TOOLKIT_CACHE_DIR`` environment variable). Each source has its own
TTL; a cached entry older than its TTL is refetched, and a cached
series is still checked against its freshness SLA on its last date.
A corrupt cache file is treated as a miss, never as data. The CSV
connector does not cache payloads — the caller's file IS the store —
but its loads are recorded for the health report.
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import json
import os
import re
import time
from pathlib import Path

from .checks import validate_price_pairs
from .data import (
    check_freshness,
    fetch_stooq_prices,
    fetch_yahoo_prices,
)
from .data_layer import StaleDataError
from .free_data import (
    fetch_coingecko_history,
    fetch_ecb_reference_rates,
    fetch_free_macro,
)

__all__ = [
    "CACHE_TTL_SECONDS",
    "FRESHNESS_SLA_SECONDS",
    "TRADING_WEEKDAYS",
    "Connector",
    "ConnectorUnavailable",
    "DiskCache",
    "default_price_chain",
    "evaluate_freshness",
    "fetch_fx_rates",
    "fetch_macro",
    "fetch_prices",
    "fetch_with_retry",
    "list_connectors",
]

_DAY = 86400

# Freshness SLA per series kind, in seconds. Daily bars and FX fixings
# must be at most 1 trading day old; World Bank annual indicators at most
# 1 year old (a year-only date is read as 31 Dec of that year — see
# evaluate_freshness). A series older than its SLA is STALE: flagged,
# never published as fresh.
#
# The SLA is judged in TRADING days, not calendar days: markets publish
# no new data on their non-trading days, so a calendar-day SLA goes red
# every weekend (Friday data is 2 calendar days old on Sunday, 3 on
# Monday morning) even when nothing is wrong. Trading days per kind:
# US stocks and ECB fixings Mon–Fri; NEPSE Sun–Thu (Nepal's trading
# week); crypto and annual macro trade/publish every day, so their age
# stays calendar days. Exchange holidays are not modelled — a long
# holiday weekend can still read STALE, which is a false alarm to
# triage, not a silent pass.
FRESHNESS_SLA_SECONDS = {
    "stock_daily": 1 * _DAY,
    "crypto_daily": 1 * _DAY,
    "fx_reference": 1 * _DAY,
    "macro_annual": 365 * _DAY,
    "nepse_csv_daily": 1 * _DAY,
}

# Trading weekdays per series kind (Monday=0 … Sunday=6), used by
# evaluate_freshness to count age in trading days. Derived from the
# market profiles (toolkit/market_profiles.py KIND_PROFILES) so the
# calendar has exactly one source of truth; the values are identical
# to the literal table this replaced.
from .market_profiles import KIND_PROFILES as _KIND_PROFILES
from .market_profiles import get_profile as _get_profile

TRADING_WEEKDAYS = {
    kind: set(_get_profile(market).trading_weekdays)
    for kind, market in _KIND_PROFILES.items()
}

# On-disk cache TTL per source, in seconds. A TTL is shorter than the
# matching SLA on purpose: a cached fetch is never old enough to be
# stale on its fetch time alone, and the data's last date is still
# checked against the SLA on every read.
CACHE_TTL_SECONDS = {
    "csv": 12 * 3600,
    "yahoo": 12 * 3600,
    "stooq": 12 * 3600,
    "coingecko": 6 * 3600,
    "ecb": 12 * 3600,
    "worldbank": 30 * _DAY,
}

# Licence/terms note per connector. A connector with no note may not
# be registered — the registry test enforces it.
_CONNECTOR_TERMS = {
    "csv": (
        "Caller-supplied file only. The caller names the source of the "
        "CSV (e.g. a verified annual report); unsourced prices are "
        "refused by the NEPSE loader. NEPSE stays CSV-only: no live "
        "feed is invented and no undocumented endpoint is called."
    ),
    "yahoo": (
        "Public endpoint, no key. No licence is published on the "
        "endpoint itself; use is under Yahoo's terms. Redistribution "
        "terms are TO VERIFY — do not redistribute Yahoo data as your "
        "own dataset; publish derived analytics only."
    ),
    "stooq": (
        "Free CSV download, no key. Fallback only; Stooq's own terms "
        "apply to reuse. Redistribution terms TO VERIFY."
    ),
    "coingecko": (
        "Documented keyless public API; shared IP pool, roughly "
        "10-30 calls/min. Respect the limit; never work around a rate "
        "limit. Redistribution terms TO VERIFY."
    ),
    "ecb": (
        "Published by the ECB as official reference rates; attribute "
        "the ECB. A fixing is not a tradable quote and not a price "
        "series. Redistribution of the published rates with "
        "attribution is the ECB's own publication practice."
    ),
    "worldbank": (
        "World Bank Open Data terms apply. Exact redistribution "
        "licence TO VERIFY before republishing raw values; publish "
        "derived analytics only."
    ),
}

_SOURCE_KINDS = {
    "csv": "nepse_csv_daily",
    "yahoo": "stock_daily",
    "stooq": "stock_daily",
    "coingecko": "crypto_daily",
    "ecb": "fx_reference",
    "worldbank": "macro_annual",
}


def _normalise_model(model):
    """Restore tuple pairs/values after a JSON round-trip through the
    cache: JSON stores tuples as lists, but the live path returns
    list-of-tuples, and the standard model must be one shape."""
    model = dict(model)
    for key in ("pairs", "values"):
        rows = model.get(key)
        if isinstance(rows, list):
            model[key] = [tuple(row) for row in rows]
    return model


def _load_csv_prices(path, date_col="date", close_col="close"):
    """``toolkit.data.load_csv_prices`` that also skips ``#`` comment
    lines, as ``toolkit.terminal.load_bars`` does — so files like the
    shipped ``demo.csv`` (comment header, synthetic) load here too.
    Same validation otherwise: the named columns must exist and
    every close must parse; at least one row is required."""
    with open(path, newline="", encoding="utf-8") as handle:
        lines = [ln for ln in handle
                 if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise ValueError(f"{path}: file is empty (no header, no data rows)")
    reader = csv.DictReader(lines)
    if date_col not in (reader.fieldnames or []) \
            or close_col not in (reader.fieldnames or []):
        raise ValueError(
            f"CSV must have columns {date_col!r} and {close_col!r}")
    out = []
    for row in reader:
        out.append((row[date_col], float(row[close_col])))
    if not out:
        raise ValueError("CSV contains no price rows")
    return out


class ConnectorUnavailable(RuntimeError):
    """A connector could not produce a usable series (network failure,
    empty payload, or a payload that failed validation). It is an
    abstention with a reason: the chain records it and moves on."""


def _now_utc():
    return _dt.datetime.now(_dt.timezone.utc)


def _now_utc_iso():
    return _now_utc().isoformat(timespec="seconds")


def _default_cache_dir():
    override = os.environ.get("TOOLKIT_CACHE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "investment-analyst-toolkit"


# ---------------------------------------------------------------------------
# On-disk cache
# ---------------------------------------------------------------------------
class DiskCache:
    """JSON-file cache, one file per key, with TTL and a hit counter.

    A cache entry records ``fetched_at`` (UTC), ``ttl_seconds``,
    ``hits`` (incremented on every hit, so the health report can
    count cache use), ``last_date`` (the data's business date, when
    known) and ``payload`` (the standard model dict). A missing file,
    a corrupt file, or an entry older than its TTL is a miss — never
    data. ``put`` never raises on a write failure: a cache that cannot
    be written is a performance loss, not a data error, and the
    failure is reported on stderr instead of breaking the fetch.
    """

    def __init__(self, cache_dir=None):
        self.cache_dir = Path(cache_dir) if cache_dir else _default_cache_dir()

    def _path_for(self, key):
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(key))
        digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:12]
        return self.cache_dir / f"{safe[:80]}-{digest}.json"

    def get(self, key, now=None):
        """Return the cached entry dict, or None on any miss. A hit
        increments the stored hit counter."""
        now = now or _now_utc()
        path = self._path_for(key)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            fetched_at = _dt.datetime.fromisoformat(entry["fetched_at"])
            ttl = float(entry["ttl_seconds"])
        except (KeyError, TypeError, ValueError):
            return None
        if (now - fetched_at).total_seconds() > ttl:
            return None
        entry["hits"] = int(entry.get("hits", 0)) + 1
        try:
            path.write_text(json.dumps(entry), encoding="utf-8")
        except OSError:
            pass  # a hit counter that cannot be persisted is not an error
        return entry

    def put(self, key, payload, ttl_seconds, last_date=None, now=None):
        """Store ``payload`` under ``key``. Never raises on failure."""
        now = now or _now_utc()
        path = self._path_for(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            previous = None
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = None
            entry = {
                "key": str(key),
                "fetched_at": now.isoformat(timespec="seconds"),
                "ttl_seconds": ttl_seconds,
                "hits": int((previous or {}).get("hits", 0)),
                "last_date": last_date,
                "payload": payload,
            }
            path.write_text(json.dumps(entry), encoding="utf-8")
        except OSError as exc:
            print(f"warning: cache write failed for {key}: {exc}",
                  flush=True)

    def stats(self):
        """Per-key cache metadata for the health report: {key:
        {fetched_at, ttl_seconds, hits, last_date}}. Corrupt files
        are skipped, never reported as data."""
        out = {}
        try:
            files = sorted(self.cache_dir.glob("*.json"))
        except OSError:
            return out
        for path in files:
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
                out[str(entry["key"])] = {
                    "fetched_at": entry["fetched_at"],
                    "ttl_seconds": entry["ttl_seconds"],
                    "hits": int(entry.get("hits", 0)),
                    "last_date": entry.get("last_date"),
                }
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return out


# ---------------------------------------------------------------------------
# Bounded retries — transient failures only
# ---------------------------------------------------------------------------
_TRANSIENT_HTTP = re.compile(r"API call failed \((5\d\d)\)")
_HARD_HTTP = re.compile(r"API call failed \((403|429|400|401|404)\)")


def _is_transient_error(exc):
    """True only for failures worth one more attempt: timeouts,
    connection errors, HTTP 5xx, and network errors. A 403/429/404,
    a validation failure, or any other error is NOT transient — a
    403 or 429 is a hard stop for that source in this run."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    text = str(exc)
    if _HARD_HTTP.search(text):
        return False
    if _TRANSIENT_HTTP.search(text):
        return True
    return "Network call failed" in text or "timed out" in text.lower()


def fetch_with_retry(fn, *, attempts=3, backoff_seconds=(1.0, 2.0, 4.0),
                     sleep=time.sleep):
    """Call ``fn()`` with bounded retries. Returns ``(result,
    attempts_used)``. Only transient errors are retried (see
    :func:`_is_transient_error`); any other error is raised at once,
    and a persistent transient error is raised after ``attempts``
    tries. ``sleep`` is injectable so tests never wait."""
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts!r}")
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn(), attempt
        except Exception as exc:
            last_exc = exc
            if not _is_transient_error(exc) or attempt == attempts:
                raise
            delay = backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)]
            sleep(delay)
    raise last_exc  # pragma: no cover — the loop always raises first


# ---------------------------------------------------------------------------
# Freshness evaluation (augur-style: business date vs SLA)
# ---------------------------------------------------------------------------
def _parse_business_date(value):
    """Parse a business date: full ISO date, or a bare year (World
    Bank annual values), read as 31 Dec of that year — the annual
    value for a year is not superseded until the next year's value
    is published."""
    text = str(value).strip()
    if re.fullmatch(r"\d{4}", text):
        return _dt.date(int(text), 12, 31)
    return _dt.date.fromisoformat(text)


def _trading_days_between(last, today, weekdays):
    """Trading days strictly after ``last`` up to and including
    ``today`` — the number of days on which new data was expected
    but has not arrived. A Friday last-date checked on Sunday is 0
    (no trading day passed); checked on Monday it is 1."""
    count = 0
    day = last + _dt.timedelta(days=1)
    while day <= today:
        if day.weekday() in weekdays:
            count += 1
        day += _dt.timedelta(days=1)
    return count


def evaluate_freshness(last_date, sla_seconds, today=None, kind=None):
    """Judge a series by its last business date against an SLA.

    Returns {last_date, age_days, age_trading_days, sla_seconds,
    status} where status is FRESH (age within the SLA) or STALE.
    ``age_days`` is the calendar age (reported as-is); the status
    is judged on ``age_trading_days`` — trading days after the last
    date on which new data was expected (see TRADING_WEEKDAYS) —
    so a Friday series is not STALE on a Sunday. ``kind`` selects
    the trading calendar; with no kind, Mon–Fri is assumed. A
    future date or an unparsable date raises ValueError instead of
    being called fresh.
    """
    today = today or _dt.date.today()
    try:
        last = _parse_business_date(last_date)
    except ValueError as exc:
        raise ValueError(
            f"last_date must be ISO YYYY-MM-DD or a year, got {last_date!r}"
        ) from exc
    age_days = (today - last).days
    if age_days < 0:
        raise ValueError(
            f"last_date {last_date} is in the future vs {today.isoformat()}")
    weekdays = TRADING_WEEKDAYS.get(kind, {0, 1, 2, 3, 4}) if kind else {0, 1, 2, 3, 4}
    age_trading_days = _trading_days_between(last, today, weekdays)
    age_seconds = age_trading_days * _DAY
    return {
        "last_date": str(last_date),
        "age_days": age_days,
        "age_trading_days": age_trading_days,
        "sla_seconds": sla_seconds,
        "status": "FRESH" if age_seconds <= sla_seconds else "STALE",
    }


# ---------------------------------------------------------------------------
# Connectors — one standard interface per source
# ---------------------------------------------------------------------------
class Connector:
    """One source behind the standard price-series interface.

    ``fetch_pairs`` is a callable ``(symbol) -> [(date, close)]`` that
    does the vendor call and nothing else; this class adds cache,
    retry, validation, the freshness gate and provenance, so the core
    never touches a vendor API and never sees a raw payload.
    """

    def __init__(self, name, fetch_pairs):
        if name not in _SOURCE_KINDS:
            raise ValueError(f"unknown connector {name!r}")
        self.name = name
        self.kind = _SOURCE_KINDS[name]
        self.sla_seconds = FRESHNESS_SLA_SECONDS[self.kind]
        self.ttl_seconds = CACHE_TTL_SECONDS[name]
        self.terms = _CONNECTOR_TERMS[name]
        self._fetch_pairs = fetch_pairs

    def fetch(self, symbol, *, cache=None, today=None, max_age_days=7,
              sleep=time.sleep):
        """Return the standard price-series model for ``symbol``.

        The model is the same shape ``toolkit/free_data.py`` returns:
        {symbol, kind, source, pairs, provenance, tried}. Provenance
        carries source, retrieved_at, last_date, age_days, n_points,
        stale=False and cache_hit. Raises StaleDataError when the
        series is older than max_age_days, ConnectorUnavailable on
        any other failure. A 403/429 is never retried.
        """
        today = today or _dt.date.today()
        cache_key = f"{self.name}:{symbol}"
        if cache is not None:
            entry = cache.get(cache_key)
            if entry is not None:
                payload = entry.get("payload") or {}
                pairs = payload.get("pairs") or []
                if pairs:
                    try:
                        pairs = validate_price_pairs(
                            f"cached prices from {self.name}", pairs)
                        fresh = check_freshness(
                            pairs[-1][0], max_age_days=max_age_days,
                            today=today)
                    except ValueError:
                        fresh = {"stale": True, "age_days": None}
                    if not fresh["stale"]:
                        model = _normalise_model(payload)
                        model["pairs"] = pairs
                        model["provenance"] = dict(payload.get("provenance") or {})
                        model["provenance"]["cache_hit"] = True
                        model["provenance"]["retrieved_at"] = _now_utc_iso()
                        model["tried"] = []
                        return model
        try:
            pairs, attempts = fetch_with_retry(
                lambda: self._fetch_pairs(symbol), sleep=sleep)
        except Exception as exc:
            raise ConnectorUnavailable(
                f"{self.name}: fetch failed for {symbol}: {exc}") from exc
        try:
            pairs = validate_price_pairs(
                f"prices from {self.name}", pairs)
            fresh = check_freshness(
                pairs[-1][0], max_age_days=max_age_days, today=today)
        except ValueError as exc:
            raise ConnectorUnavailable(
                f"{self.name}: invalid series for {symbol}: {exc}") from exc
        if fresh["stale"]:
            raise StaleDataError(
                f"{self.name}: last date {pairs[-1][0]} is "
                f"{fresh['age_days']} days old (max {max_age_days})")
        model = {
            "symbol": symbol,
            "kind": "crypto" if self.name == "coingecko" else "stock",
            "source": self.name,
            "pairs": pairs,
            "provenance": {
                "source": self.name,
                "retrieved_at": _now_utc_iso(),
                "last_date": pairs[-1][0],
                "age_days": fresh["age_days"],
                "n_points": len(pairs),
                "stale": False,
                "cache_hit": False,
                "attempts": attempts,
            },
            "tried": [],
        }
        if cache is not None:
            cache.put(cache_key, model, self.ttl_seconds,
                      last_date=pairs[-1][0])
        return model


class CsvConnector(Connector):
    """Caller-supplied CSV connector — the NEPSE path (priority 1).

    ``path`` is the caller's file and ``source`` is the caller's
    source label (required: unsourced prices are refused). ``loader``
    defaults to a comment-tolerant CSV reader (same validation as
    ``toolkit.data.load_csv_prices``); pass the scanner's ``load_csv``
    (``nepali-capital-market-research/scanner/data.py``) to use the
    scanner's NEPSE loader behind this same interface.
    The file is read on every fetch — a local file needs no network
    cache — but each load is recorded for the health report.
    """

    def __init__(self, path, source, loader=None):
        if not isinstance(source, str) or not source.strip():
            raise ConnectorUnavailable(
                "csv: a source must be named (e.g. 'NEPSE published "
                "data, session of 2026-11-06, transcribed by hand'). "
                "Unsourced prices are refused, not loaded.")
        super().__init__("csv", lambda symbol: (loader or _load_csv_prices)(path))
        self.path = str(path)
        self.csv_source = source.strip()

    def fetch(self, symbol=None, *, cache=None, today=None, max_age_days=7,
              sleep=time.sleep):
        try:
            return super().fetch(symbol or Path(self.path).stem, cache=None,
                                 today=today, max_age_days=max_age_days,
                                 sleep=sleep)
        except ConnectorUnavailable as exc:
            raise ConnectorUnavailable(
                f"csv ({self.path}, source: {self.csv_source}): {exc}"
            ) from exc


def _yahoo_pairs(symbol):
    return fetch_yahoo_prices(symbol)


def _stooq_pairs(symbol):
    sym = str(symbol).lower()
    if "." not in sym:
        sym = sym + ".us"
    return fetch_stooq_prices(sym)


def _coingecko_pairs(symbol):
    return fetch_coingecko_history(symbol)


def _yahoo_crypto_pairs(symbol):
    sym = str(symbol).upper()
    if "-" not in sym:
        sym = sym + "-USD"
    return fetch_yahoo_prices(sym)


def default_price_chain(kind="stock", csv_path=None, csv_source=None):
    """Ordered connectors for a series kind. kind='stock' -> CSV (if
    given), Yahoo, Stooq. kind='crypto' -> CSV (if given), CoinGecko,
    Yahoo (BTC-USD style). Anything else raises ValueError."""
    chain = []
    if csv_path is not None:
        chain.append(CsvConnector(csv_path, csv_source or "caller-supplied CSV"))
    if kind == "stock":
        chain += [Connector("yahoo", _yahoo_pairs),
                  Connector("stooq", _stooq_pairs)]
    elif kind == "crypto":
        chain += [Connector("coingecko", _coingecko_pairs),
                  Connector("yahoo", _yahoo_crypto_pairs)]
    else:
        raise ValueError(f"unknown price kind {kind!r} (use 'stock' or 'crypto')")
    return chain


def fetch_prices(symbol, kind="stock", csv_path=None, csv_source=None,
                 cache=None, today=None, max_age_days=7, sleep=time.sleep,
                 chain=None):
    """Try the connector chain in order; return the first FRESH series.

    Returns the standard model {symbol, kind, source, pairs,
    provenance, tried}. Sources that fail or are stale are recorded
    in ``tried`` with their reason. Raises RuntimeError listing EVERY
    attempt if all fail or are stale — never returns stale data as
    fresh, never invents a price. Retries are bounded and transient
    only (see :func:`fetch_with_retry`); a 403/429 is a recorded
    failure, not a prompt to hammer the source again.
    """
    connectors = chain if chain is not None else default_price_chain(
        kind, csv_path=csv_path, csv_source=csv_source)
    tried = []
    for conn in connectors:
        try:
            model = conn.fetch(symbol, cache=cache, today=today,
                               max_age_days=max_age_days, sleep=sleep)
            model["tried"] = tried
            return model
        except StaleDataError as exc:
            tried.append({"source": conn.name, "status": "stale",
                          "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — fallback must catch any source failure
            tried.append({"source": conn.name, "status": "failed",
                          "error": str(exc)})
    raise RuntimeError(
        f"All data sources failed or stale for {symbol} ({kind}). "
        f"Attempts: {tried}. No fresh data was returned.")


# ---------------------------------------------------------------------------
# FX and macro connectors (latest-rate / annual-value models)
# ---------------------------------------------------------------------------
def fetch_fx_rates(cache=None, sleep=time.sleep, fetch_fn=None):
    """Latest ECB euro reference rates, cached. Returns {date, rates,
    source, retrieved_at, provenance}. A fixing is not a tradable
    quote and not a price series. Raises ConnectorUnavailable on
    failure (never retried on 403/429)."""
    cache_key = "ecb:latest"
    if cache is not None:
        entry = cache.get(cache_key)
        if entry is not None and entry.get("payload"):
            model = _normalise_model(entry["payload"])
            model["provenance"] = dict(model.get("provenance") or {})
            model["provenance"]["cache_hit"] = True
            model["provenance"]["retrieved_at"] = _now_utc_iso()
            return model
    fn = fetch_fn or fetch_ecb_reference_rates
    try:
        parsed, _attempts = fetch_with_retry(fn, sleep=sleep)
    except Exception as exc:
        raise ConnectorUnavailable(f"ecb: fetch failed: {exc}") from exc
    model = {"date": parsed["date"], "rates": parsed["rates"],
             "source": "ECB euro reference rates",
             "retrieved_at": _now_utc_iso(),
             "provenance": {"source": "ecb",
                            "retrieved_at": _now_utc_iso(),
                            "last_date": parsed["date"],
                            "n_points": len(parsed["rates"]),
                            "stale": False, "cache_hit": False}}
    if cache is not None:
        cache.put(cache_key, model, CACHE_TTL_SECONDS["ecb"],
                  last_date=parsed["date"])
    return model


def fetch_macro(country, indicator, cache=None, sleep=time.sleep,
                fetch_fn=None):
    """World Bank macro indicator, cached. Returns {source, country,
    indicator, values, retrieved_at, provenance} where values are
    [(year, value)] sorted by year. Raises ConnectorUnavailable on
    failure (never retried on 403/429)."""
    cache_key = f"worldbank:{country}:{indicator}"
    if cache is not None:
        entry = cache.get(cache_key)
        if entry is not None and entry.get("payload"):
            model = _normalise_model(entry["payload"])
            model["provenance"] = dict(model.get("provenance") or {})
            model["provenance"]["cache_hit"] = True
            model["provenance"]["retrieved_at"] = _now_utc_iso()
            return model
    fn = fetch_fn or (lambda: fetch_free_macro(country, indicator)["values"])
    try:
        values, _attempts = fetch_with_retry(fn, sleep=sleep)
    except Exception as exc:
        raise ConnectorUnavailable(
            f"worldbank: fetch failed for {country}/{indicator}: {exc}"
        ) from exc
    if not values:
        raise ConnectorUnavailable(
            f"worldbank: no values for {country}/{indicator}")
    ordered = sorted(values)
    model = {"source": "World Bank API v2", "country": country,
             "indicator": indicator, "values": ordered,
             "retrieved_at": _now_utc_iso(),
             "provenance": {"source": "worldbank",
                            "retrieved_at": _now_utc_iso(),
                            "last_date": str(ordered[-1][0]),
                            "n_points": len(ordered),
                            "stale": False, "cache_hit": False}}
    if cache is not None:
        cache.put(cache_key, model, CACHE_TTL_SECONDS["worldbank"],
                  last_date=str(ordered[-1][0]))
    return model


def list_connectors():
    """Registry table: every connector with its kind, SLA, TTL and
    licence/terms note. A connector with no terms note cannot exist
    — the registry test enforces it."""
    rows = []
    for name in ("csv", "yahoo", "stooq", "coingecko", "ecb", "worldbank"):
        kind = _SOURCE_KINDS[name]
        rows.append({
            "name": name,
            "kind": kind,
            "sla_seconds": FRESHNESS_SLA_SECONDS[kind],
            "ttl_seconds": CACHE_TTL_SECONDS[name],
            "licence_terms": _CONNECTOR_TERMS[name],
        })
    return rows
