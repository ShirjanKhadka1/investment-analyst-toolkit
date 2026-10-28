"""Data layer: source registry, payload validation, and the publish gate.

Research only. Never investment advice, never a buy/sell recommendation.

This module is the boundary between "data arrived" and "data may be
published". Three rules, enforced here and nowhere else by convention:

1. A corrupt payload is REJECTED, never repaired. Repair would invent
   data. See ``validate_series_payload``.
2. A stale series is REJECTED as fresh, never relabelled. See
   ``publish_check`` and ``StaleDataError``. The previous good report
   is kept (see ``autonomous.run_once``) and the failed run is labelled
   STALE — it is never published as if it were fresh.
3. Every source carries its licence/terms note in the registry below.
   A source with no licence note may not be added to the registry.
   "TO VERIFY" is a valid, honest value — it blocks redistribution
   claims, it does not block a clearly-labelled local research use.

Sources verified (or explicitly NOT verified) on 28 Oct 2026 — see
DATA_SOURCES.md for the full audit trail. Free, public, legitimate
sources only: no paywalled or login-locked source, no undocumented
endpoint, no reverse-engineered token flow, no retry after a 403/429.
"""
from __future__ import annotations

import datetime as _dt

from .checks import validate_price_pairs
from .data import check_freshness

__all__ = [
    "CorruptDataError",
    "StaleDataError",
    "list_data_sources",
    "publish_check",
    "validate_series_payload",
]


class CorruptDataError(ValueError):
    """A payload that failed schema validation. Never publish it."""


class StaleDataError(ValueError):
    """A series whose last date is older than max_age_days. Never
    publish it as fresh; keep the last-known-good report instead."""


# Every entry: name, kind, needs_key, in_no_key_chain, licence_terms,
# verification (dated, honest). A missing licence_terms is a bug, not
# a gap to fill later — the registry test enforces it.
DATA_SOURCES = [
    {
        "name": "Yahoo Finance chart API",
        "kind": "stock",
        "needs_key": False,
        "in_no_key_chain": True,
        "licence_terms": (
            "Public endpoint, no key. No licence is published on the "
            "endpoint itself; use is under Yahoo's terms. Redistribution "
            "terms are TO VERIFY — do not redistribute Yahoo data as "
            "your own dataset."
        ),
        "verification": "Verified working 28 Oct 2026 (AAPL, 251 points, fresh).",
    },
    {
        "name": "Stooq daily CSV",
        "kind": "stock",
        "needs_key": False,
        "in_no_key_chain": True,
        "licence_terms": (
            "Free CSV download, no key. Fallback only; Stooq's own terms "
            "apply to reuse. Redistribution terms TO VERIFY."
        ),
        "verification": (
            "NOT relied on alone: a 28 Oct 2026 smoke check from this "
            "environment returned an HTML page, not CSV."
        ),
    },
    {
        "name": "CoinGecko keyless public API",
        "kind": "crypto",
        "needs_key": False,
        "in_no_key_chain": True,
        "licence_terms": (
            "Documented keyless public API (docs.coingecko.com). Shared "
            "IP pool, roughly 10-30 calls/min. Respect the rate limit; "
            "no key, no workaround if rate-limited."
        ),
        "verification": "Verified working 28 Oct 2026 (BTC price + 91 daily points, fresh).",
    },
    {
        "name": "ECB euro reference rates",
        "kind": "fx-reference",
        "needs_key": False,
        "in_no_key_chain": False,
        "licence_terms": (
            "Published by the European Central Bank as official daily "
            "reference rates. Attribute the ECB as the source. A single "
            "daily fixing per currency — latest rate only, never a "
            "series, never a tradable quote."
        ),
        "verification": "Verified working 28 Oct 2026 (2026-10-27 fixing).",
    },
    {
        "name": "World Bank API v2",
        "kind": "macro",
        "needs_key": False,
        "in_no_key_chain": False,
        "licence_terms": (
            "World Bank Open Data terms apply. Exact redistribution "
            "licence is TO VERIFY before any dataset is republished."
        ),
        "verification": "Verified working 28 Oct 2026 (Nepal GDP 2025).",
    },
    {
        "name": "SEC EDGAR",
        "kind": "filings",
        "needs_key": False,
        "in_no_key_chain": False,
        "licence_terms": (
            "US public filings; EDGAR full-text and company-concept APIs "
            "are public, with a declared User-Agent required. No key."
        ),
        "verification": (
            "NOT VERIFIED from this environment: 28 Oct 2026 fetch "
            "returned HTTP 403 despite a declared User-Agent. Existing "
            "fetcher left in place, not claimed verified, not retried "
            "aggressively. TO VERIFY from a non-VM network."
        ),
    },
    {
        "name": "FRED / Alpha Vantage / Twelve Data / FMP",
        "kind": "keyed",
        "needs_key": True,
        "in_no_key_chain": False,
        "licence_terms": (
            "Each provider's own free-tier terms, under the user's own "
            "key only. Keys come from the environment, never from this "
            "repo, and no key is bundled or invented."
        ),
        "verification": "Not in the no-key chain; no key supplied, none invented.",
    },
    {
        "name": "NEPSE (nepalstock.com.np)",
        "kind": "nepse",
        "needs_key": False,
        "in_no_key_chain": False,
        "licence_terms": (
            "NO documented free public OHLCV API verified as of "
            "28 Oct 2026. The only legitimate paths are user-supplied "
            "CSV (validated by the scanner loaders) or the paid, "
            "licensed NEPSE Market Data Provider — the latter is the "
            "owner's funding decision only and is NOT started."
        ),
        "verification": (
            "Data finding, not an access blocker: public homepage "
            "renders only 'Loading...' with no prices; data sits behind "
            "an internal token flow that is deliberately NOT wired in."
        ),
    },
]


def list_data_sources():
    """Return a copy of the source registry (never the live list)."""
    return [dict(entry) for entry in DATA_SOURCES]


def validate_series_payload(payload):
    """Validate a {symbol, source, pairs} payload at the boundary.

    ``pairs`` must pass ``validate_price_pairs`` (ISO dates, strictly
    increasing, unique, close > 0). Anything else raises
    CorruptDataError — a broken series is never silently repaired and
    never partially published. Returns the normalised pairs.
    """
    if not isinstance(payload, dict):
        raise CorruptDataError(f"payload must be a dict, got {type(payload).__name__}")
    symbol = payload.get("symbol")
    source = payload.get("source")
    pairs = payload.get("pairs")
    if not symbol or not isinstance(symbol, str):
        raise CorruptDataError("payload missing a string 'symbol'")
    if not source or not isinstance(source, str):
        raise CorruptDataError("payload missing a string 'source'")
    if pairs is None:
        raise CorruptDataError(f"payload for {symbol} missing 'pairs'")
    try:
        return validate_price_pairs(f"prices for {symbol} from {source}", pairs)
    except ValueError as exc:
        raise CorruptDataError(str(exc)) from exc


def publish_check(pairs, max_age_days=7, today=None):
    """The publish gate: validate, then check freshness.

    Returns a provenance dict {last_date, age_days, n_points, stale:
    False} for a series that may be published. Raises CorruptDataError
    for a broken series and StaleDataError for a series older than
    max_age_days — in both cases the caller must keep the
    last-known-good report and label the run STALE, never publish the
    rejected series as fresh.
    """
    try:
        clean = validate_price_pairs("series for publish_check", pairs)
    except ValueError as exc:
        raise CorruptDataError(str(exc)) from exc
    fresh = check_freshness(clean[-1][0], max_age_days=max_age_days, today=today)
    if fresh["stale"]:
        raise StaleDataError(
            f"last date {clean[-1][0]} is {fresh['age_days']} days old "
            f"(max {max_age_days}); stale data must not be published as fresh"
        )
    return {
        "last_date": clean[-1][0],
        "age_days": fresh["age_days"],
        "n_points": len(clean),
        "stale": False,
        "checked_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
