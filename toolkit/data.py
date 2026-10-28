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
