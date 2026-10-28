"""Free data adapters — standard library only, no API key in the core path.

Sources (all free, no account required for the calls used here):
- Yahoo Finance chart API: public price history, no key.
- World Bank API: public indicators (GDP, inflation, etc.), no key.
- CSV files: your own data, including NEPSE company data from a verified
  annual report — the user labels the fiscal year; this module never
  invents a feed, a price, or a fiscal year.
- Demo data: bundled synthetic series, labelled DEMO, for offline use.

If a network call fails (offline, blocked, rate-limited), the function
raises a clear RuntimeError — it never returns invented prices.
Paid sources (FRED with a key, Polygon, etc.) are OPTIONAL: pass your
own key explicitly, never hard-code one, and the core examples never
need them.
"""
from __future__ import annotations

import csv
import json
import urllib.error
import urllib.request

_USER_AGENT = "investment-analyst-toolkit/0.2 (+https://github.com/ShirjanKhadka1/investment-analyst-toolkit)"


def list_free_sources():
    """Table of free sources this module can call, with key requirements."""
    return [
        {"source": "Yahoo Finance chart API", "needs_key": False, "function": "fetch_yahoo_prices"},
        {"source": "World Bank API v2", "needs_key": False, "function": "fetch_worldbank_indicator"},
        {"source": "CSV file (your own data / NEPSE annual-report data)", "needs_key": False, "function": "load_csv_prices"},
        {"source": "Bundled demo series", "needs_key": False, "function": "demo_series"},
        {"source": "FRED (optional, your own key)", "needs_key": True, "function": "fetch_fred_series (pass api_key=...)"},
    ]


def _get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API call failed ({e.code}) for {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network call failed for {url}: {e.reason}") from e


def fetch_yahoo_prices(symbol, range_="1y", interval="1d"):
    """Daily closes from Yahoo Finance chart API. Returns [(date_str, close)].

    No API key. Dates are ISO strings from Yahoo timestamps.
    """
    import datetime

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={range_}&interval={interval}"
    )
    data = _get_json(url)
    try:
        result = data["chart"]["result"][0]
        stamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected Yahoo response for {symbol}") from e
    out = []
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        date = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date().isoformat()
        out.append((date, float(close)))
    if not out:
        raise RuntimeError(f"No prices returned for {symbol}")
    return out


def fetch_worldbank_indicator(country, indicator, per_page=100):
    """World Bank indicator values. Returns [(year, value)].

    Example: country="NPL", indicator="NY.GDP.MKTP.CD" (GDP, current US$).
    No API key.
    """
    url = (
        f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
        f"?format=json&per_page={per_page}"
    )
    data = _get_json(url)
    if not isinstance(data, list) or len(data) < 2 or not data[1]:
        raise RuntimeError(f"No World Bank data for {country}/{indicator}")
    out = []
    for row in data[1]:
        if row.get("value") is None:
            continue
        out.append((str(row["date"]), float(row["value"])))
    return sorted(out)


def fetch_fred_series(series_id, api_key):
    """FRED series, optional — requires YOUR OWN key, passed explicitly.

    Never hard-code a key. Core examples never call this function.
    """
    if not api_key:
        raise ValueError("FRED needs an api_key passed explicitly — none was given")
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={api_key}&file_type=json"
    )
    data = _get_json(url)
    out = []
    for row in data.get("observations", []):
        if row["value"] in (".", ""):
            continue
        out.append((row["date"], float(row["value"])))
    return out


def load_csv_prices(path, date_col="date", close_col="close"):
    """Load (date, close) pairs from a CSV file.

    This is the NEPSE adapter too: if your CSV came from a verified
    annual report or filing, label its fiscal year in your own notes —
    this function reads the file you give it, nothing else.
    """
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if date_col not in (reader.fieldnames or []) or close_col not in (reader.fieldnames or []):
            raise ValueError(f"CSV must have columns {date_col!r} and {close_col!r}")
        for row in reader:
            out.append((row[date_col], float(row[close_col])))
    if not out:
        raise ValueError("CSV contains no price rows")
    return out


def demo_series():
    """Bundled DEMO price series (synthetic, labelled DEMO in every use)."""
    return [100.0, 102.0, 101.0, 105.0, 107.0, 106.0, 110.0, 112.0, 111.0, 115.0,
            118.0, 117.0, 121.0, 124.0, 123.0, 127.0, 130.0, 129.0, 133.0, 136.0]


# ---------------------------------------------------------------------------
# v0.4 — quality data, freshness, keys, fallback (no stale data, no invented data)
# ---------------------------------------------------------------------------
import datetime as _dt
import os as _os


def get_api_key(env_name, provider, signup_url):
    """Read a key from the environment ONLY. Never hard-code, never invent.
    Raises a clear error telling the user where to get a free key."""
    key = _os.environ.get(env_name)
    if not key:
        raise ValueError(
            f"{provider} needs {env_name} set in the environment — no key is "
            f"bundled, and none is invented. Get a free key at {signup_url}, "
            f"then run: export {env_name}=<your-key>  (or put it in .env, never in code)"
        )
    return key


def check_freshness(last_date_str, max_age_days=7, today=None):
    """Freshness gate: returns {age_days, stale, last_date}.
    Raises on an unparsable date instead of treating it as fresh."""
    try:
        last = _dt.date.fromisoformat(str(last_date_str))
    except ValueError as e:
        raise ValueError(f"last_date must be ISO YYYY-MM-DD, got {last_date_str!r}") from e
    today = today or _dt.date.today()
    age = (today - last).days
    if age < 0:
        raise ValueError(f"last_date {last_date_str} is in the future vs {today.isoformat()}")
    return {"last_date": last.isoformat(), "age_days": age, "stale": age > max_age_days}


def _get_text(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API call failed ({e.code}) for {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network call failed for {url}: {e.reason}") from e


def fetch_stooq_prices(symbol):
    """Stooq daily closes, CSV, no key. Symbol like 'aapl.us'. Returns [(date, close)]."""
    text = _get_text(f"https://stooq.com/q/d/l/?s={symbol}&i=d")
    rows = list(csv.DictReader(text.splitlines()))
    if not rows or "Close" not in (rows[0] or {}):
        raise RuntimeError(f"No Stooq data for {symbol}")
    out = [(r["Date"], float(r["Close"])) for r in rows if r.get("Close")]
    if not out:
        raise RuntimeError(f"No Stooq closes for {symbol}")
    return out


def fetch_alpha_vantage_prices(symbol, api_key=None):
    """Alpha Vantage daily prices. Key from env ALPHA_VANTAGE_API_KEY or argument."""
    key = api_key or get_api_key("ALPHA_VANTAGE_API_KEY", "Alpha Vantage", "https://www.alphavantage.co/support/#api-key")
    data = _get_json(f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={symbol}&apikey={key}&outputsize=compact")
    series = data.get("Time Series (Daily)") or {}
    if not series:
        raise RuntimeError(f"No Alpha Vantage data for {symbol} (check symbol/key/rate limit)")
    return sorted((d, float(v["4. close"])) for d, v in series.items())


def fetch_twelvedata_prices(symbol, api_key=None):
    """Twelve Data daily prices. Key from env TWELVEDATA_API_KEY or argument."""
    key = api_key or get_api_key("TWELVEDATA_API_KEY", "Twelve Data", "https://twelvedata.com/")
    data = _get_json(f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=1day&apikey={key}")
    values = data.get("values") or []
    if not values:
        raise RuntimeError(f"No Twelve Data data for {symbol} (check symbol/key/rate limit)")
    return sorted((v["datetime"][:10], float(v["close"])) for v in values)


def fetch_fmp_prices(symbol, api_key=None):
    """Financial Modeling Prep daily prices. Key from env FMP_API_KEY or argument."""
    key = api_key or get_api_key("FMP_API_KEY", "Financial Modeling Prep", "https://site.financialmodelingprep.com/developer/docs")
    data = _get_json(f"https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}?apikey={key}")
    hist = data.get("historical") or []
    if not hist:
        raise RuntimeError(f"No FMP data for {symbol} (check symbol/key/rate limit)")
    return sorted((h["date"], float(h["close"])) for h in hist)


def fetch_sec_company_facts(cik):
    """SEC EDGAR company facts, no key. CIK must be digits (e.g. Apple 320193).
    SEC requires a declared User-Agent — this module sends one. Returns the
    raw facts dict; callers pick a concept (e.g. Revenues) and must state it."""
    if not str(cik).isdigit():
        raise ValueError(f"CIK must be digits, got {cik!r}")
    return _get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{str(cik).zfill(10)}.json")


def fetch_fresh_prices(symbol, fetchers, max_age_days=7, today=None):
    """Fallback chain with a freshness gate — the anti-stale-data core.

    fetchers: list of (source_name, callable(symbol) -> [(date, close)]).
    Tries each in order; a source that fails, returns an empty/broken
    series, or is older than max_age_days is skipped, with the reason
    recorded. Returns {symbol, source, pairs, provenance, tried}.
    Raises RuntimeError listing EVERY attempt if all sources fail or
    are stale — never returns stale data labelled as fresh, never
    invents a price to fill the gap.
    """
    from .checks import validate_price_pairs
    tried = []
    for name, fn in fetchers:
        try:
            pairs = validate_price_pairs(f"prices from {name}", fn(symbol))
            fresh = check_freshness(pairs[-1][0], max_age_days=max_age_days, today=today)
            if fresh["stale"]:
                tried.append({"source": name, "status": "stale", "last_date": pairs[-1][0], "age_days": fresh["age_days"]})
                continue
            return {"symbol": symbol, "source": name, "pairs": pairs,
                    "provenance": {"source": name, "last_date": pairs[-1][0], "age_days": fresh["age_days"], "n_points": len(pairs), "stale": False},
                    "tried": tried}
        except Exception as e:  # noqa: BLE001 — fallback must catch any source failure
            tried.append({"source": name, "status": "failed", "error": str(e)})
    raise RuntimeError(f"All data sources failed or stale for {symbol}. Attempts: {tried}. No fresh data was returned.")
