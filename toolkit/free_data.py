"""Unified FREE data layer — standard library only, no keys in the core path.

Research only. Never investment advice, never a buy/sell recommendation.
Every series returned carries its source, retrieval time, last date and
age, so stale data is never presented as fresh. Every failure is logged
with a reason in the ``tried`` list; one failed source never kills the
run, and if every source fails the error lists EVERY attempt.

What is wired in here (all verified or documented on 28 Oct 2026 — see
research/free-data-sources-2026-10-28.md for the full audit):
- Yahoo Finance chart API: stock/ETF price history, no key. Public
  endpoint, used as the toolkit's primary stock source since v0.2.
- Stooq daily CSV: stock history, no key. Kept as a fallback only —
  on 28 Oct 2026 a smoke check from this environment returned an HTML
  page instead of CSV, so Stooq is NOT relied on alone.
- CoinGecko keyless public API (docs.coingecko.com, "Keyless Public
  API"): crypto prices and market charts, no key, ~10-30 calls/min
  shared IP pool. Used for crypto series only.
- ECB euro reference rates (eurofxref-daily.xml): official daily
  reference rates vs EUR, no key. A single daily fixing per currency,
  NOT a tradable quote and NOT a price series — it is exposed as a
  latest-rate lookup, never fed into the price fallback chain.
- World Bank API v2: macro indicators, no key (macro wrapper only).

What is DELIBERATELY NOT wired in:
- Any paywalled, login-locked, or subscription source.
- Any undocumented website endpoint (Nasdaq/TradingView/Binance
  website APIs, NEPSE's internal token-protected endpoints). Those are
  not documented public APIs; depending on them is undocumented access
  that can be blocked or changed at any time, and NEPSE's token flow
  was explicitly rejected in the earlier teardown.
- Proprietary signals or data from Nepalytix, Chart Nepali, Chukul or
  Nepse Alpha. Nothing from those sites is copied or called here.
- Keyed sources (FRED, Alpha Vantage, Twelve Data, FMP): they exist in
  toolkit/data.py for users who supply their OWN free key. They are not
  in the no-key fallback chain, and no key is bundled or invented.

Politeness rules for every fetcher in this module: sources are tried
one at a time, in order, with no retries and no parallel hammering. A
403 or 429 is recorded as a failure and the chain moves on — it is
never worked around, and the caller must not re-run the chain in a
tight loop. If a source blocks this environment, that is a finding to
record, not a problem to bypass.
"""
from __future__ import annotations

import datetime as _dt
import xml.etree.ElementTree as _ET

from .checks import validate_price_pairs
from .data import (
    _get_json,
    _get_text,
    check_freshness,
    fetch_stooq_prices,
    fetch_worldbank_indicator,
    fetch_yahoo_prices,
)

__all__ = [
    "default_fetchers",
    "fetch_coingecko_history",
    "fetch_ecb_reference_rates",
    "fetch_free_macro",
    "fetch_free_many",
    "fetch_free_prices",
    "list_free_data_sources",
]

_COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "XRP": "ripple",
    "LTC": "litecoin",
}


def list_free_data_sources():
    """Documented no-key sources in the fallback chain, with key needs."""
    return [
        {"source": "Yahoo Finance chart API", "needs_key": False,
         "provides": "stock/ETF daily price history", "kind": "stock"},
        {"source": "Stooq daily CSV", "needs_key": False,
         "provides": "stock daily closes (fallback only; unverified 28 Oct 2026)",
         "kind": "stock"},
        {"source": "CoinGecko keyless public API", "needs_key": False,
         "provides": "crypto daily prices (market chart)", "kind": "crypto"},
        {"source": "ECB euro reference rates", "needs_key": False,
         "provides": "daily reference rates vs EUR (latest only, not a series)",
         "kind": "fx-reference"},
        {"source": "World Bank API v2", "needs_key": False,
         "provides": "macro indicators by country/year", "kind": "macro"},
    ]


def _now_utc_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# CoinGecko (documented keyless API)
# ---------------------------------------------------------------------------
def _coingecko_daily_points(payload):
    """Collapse a market_chart ``prices`` list [[ms, price], ...] to one
    (date, price) pair per UTC day, keeping the LAST price of each day.
    Pure function, so tests use fixtures and never touch the network."""
    per_day = {}
    for point in payload.get("prices", []) or []:
        try:
            ms, price = point
            day = _dt.datetime.fromtimestamp(ms / 1000.0, _dt.timezone.utc).date().isoformat()
        except (TypeError, ValueError, OverflowError):
            continue
        if price is None:
            continue
        per_day[day] = float(price)
    return sorted(per_day.items())


def fetch_coingecko_history(symbol, days=90):
    """Daily crypto prices from CoinGecko's documented keyless API.

    ``symbol`` is a ticker like BTC or a CoinGecko id like bitcoin.
    Returns [(date, price)] in USD, one point per UTC day."""
    coin_id = _COINGECKO_IDS.get(str(symbol).upper(), str(symbol).lower())
    payload = _get_json(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        f"?vs_currency=usd&days={int(days)}"
    )
    points = _coingecko_daily_points(payload)
    if not points:
        raise RuntimeError(f"No CoinGecko market-chart data for {symbol}")
    return points


def _yahoo_crypto_adapter(symbol):
    """Yahoo fallback for crypto: BTC -> BTC-USD."""
    sym = str(symbol).upper()
    if "-" not in sym:
        sym = sym + "-USD"
    return fetch_yahoo_prices(sym)


def _stooq_adapter(symbol):
    """Stooq fallback for stocks: AAPL -> aapl.us (as in v0.4)."""
    sym = str(symbol).lower()
    if "." not in sym:
        sym = sym + ".us"
    return fetch_stooq_prices(sym)


# ---------------------------------------------------------------------------
# ECB euro reference rates (official daily XML, no key)
# ---------------------------------------------------------------------------
def _local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_ecb_xml(text):
    """Parse eurofxref XML into {date, rates: {currency: rate}}.
    Pure function; tests use a fixture document, never the network.
    Rates are EUR-base reference fixings, NOT tradable quotes."""
    root = _ET.fromstring(text)
    for node in root.iter():
        if _local(node.tag) == "Cube" and node.get("time"):
            rates = {}
            for child in node:
                if _local(child.tag) == "Cube" and child.get("currency"):
                    rates[child.get("currency")] = float(child.get("rate"))
            return {"date": node.get("time"), "rates": rates}
    raise ValueError("ECB eurofxref XML contains no dated Cube")


def fetch_ecb_reference_rates():
    """Latest ECB euro reference rates. Returns {date, rates, source}.
    One fixing per working day; if today is a weekend/holiday the date
    is the last published working day, and that date is returned — the
    rate is never relabelled with today's date."""
    text = _get_text("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml")
    parsed = _parse_ecb_xml(text)
    parsed["source"] = "ECB euro reference rates"
    parsed["retrieved_at"] = _now_utc_iso()
    return parsed


# ---------------------------------------------------------------------------
# Unified fallback chain
# ---------------------------------------------------------------------------
def default_fetchers(kind="stock"):
    """Ordered (name, callable) fallback chain for a series kind.
    kind='stock' -> Yahoo, then Stooq. kind='crypto' -> CoinGecko,
    then Yahoo (BTC-USD style). Anything else raises ValueError."""
    if kind == "stock":
        return [("yahoo", fetch_yahoo_prices), ("stooq", _stooq_adapter)]
    if kind == "crypto":
        return [("coingecko", fetch_coingecko_history), ("yahoo", _yahoo_crypto_adapter)]
    raise ValueError(f"unknown free-data kind {kind!r} (use 'stock' or 'crypto')")


def fetch_free_prices(symbol, kind="stock", max_age_days=7, today=None, fetchers=None):
    """Try the free sources in order; return the first FRESH series.

    Returns {symbol, kind, source, pairs, provenance, tried} where
    provenance carries source, retrieved_at (UTC), last_date, age_days,
    n_points and stale=False. Sources that fail, return a broken series,
    or are older than max_age_days are skipped with the reason recorded
    in ``tried``. Raises RuntimeError listing EVERY attempt if all fail
    or are stale — never returns stale data as fresh, never invents a
    price. No retries: a 403/429 is a recorded failure, not a prompt
    to hammer the source again.
    """
    chain = fetchers if fetchers is not None else default_fetchers(kind)
    tried = []
    for name, fn in chain:
        try:
            pairs = validate_price_pairs(f"prices from {name}", fn(symbol))
            fresh = check_freshness(pairs[-1][0], max_age_days=max_age_days, today=today)
            if fresh["stale"]:
                tried.append({"source": name, "status": "stale",
                              "last_date": pairs[-1][0], "age_days": fresh["age_days"]})
                continue
            return {
                "symbol": symbol, "kind": kind, "source": name, "pairs": pairs,
                "provenance": {"source": name, "retrieved_at": _now_utc_iso(),
                               "last_date": pairs[-1][0], "age_days": fresh["age_days"],
                               "n_points": len(pairs), "stale": False},
                "tried": tried,
            }
        except Exception as exc:  # noqa: BLE001 — fallback must catch any source failure
            tried.append({"source": name, "status": "failed", "error": str(exc)})
    raise RuntimeError(
        f"All free data sources failed or stale for {symbol} ({kind}). "
        f"Attempts: {tried}. No fresh data was returned.")


def fetch_free_many(symbols, kind="stock", max_age_days=7, today=None, fetchers=None):
    """Per-symbol isolation: fetch each symbol through fetch_free_prices;
    a failure for one symbol is recorded under 'error' and never kills
    the rest. Returns {symbol: result-dict or {'error': str}}."""
    out = {}
    for symbol in symbols:
        try:
            out[symbol] = fetch_free_prices(symbol, kind=kind,
                                            max_age_days=max_age_days,
                                            today=today, fetchers=fetchers)
        except Exception as exc:  # noqa: BLE001 — isolation requires catching per symbol
            out[symbol] = {"error": str(exc)}
    return out


def fetch_free_macro(country, indicator, per_page=100):
    """World Bank macro wrapper with provenance. Returns
    {source, country, indicator, values, retrieved_at}."""
    values = fetch_worldbank_indicator(country, indicator, per_page=per_page)
    return {"source": "World Bank API v2", "country": country,
            "indicator": indicator, "values": values,
            "retrieved_at": _now_utc_iso()}
