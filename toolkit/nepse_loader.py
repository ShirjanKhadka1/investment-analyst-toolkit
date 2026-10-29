"""NEPSE user-supplied-CSV loader for the toolkit (Round 3, R6).

LOADER ONLY — the same verdict as the scanner's
``nepse_bridge`` module (see the scanner repo's
``scanner/NEPSE_USER_CSV_TERMS.md`` for the full terms audit):

- shirjankhadka.com.np's Data Use & Terms (effective 1 Oct 2026)
  allow quoting individual figures with attribution but forbid
  bulk re-scraping of the site's data files to republish a
  competing feed without emailed bulk-access permission. No such
  permission is on file, so no price series from that site is
  copied here.
- No documented, free, public NEPSE OHLCV API exists; NEPSE's
  internal token-protected flow is undocumented access and is NOT
  wired in. The licensed Market Data Provider route is paid and
  NOT started (owner's funding decision only).

This module therefore loads ONLY a CSV you supply, in one of two
documented formats, and refuses to run without a named source:

- Multi-symbol: ``symbol,sector,date,close,high,low,volume``
- Single-symbol: ``date,close,high,low,volume``

Rules: dates ISO, strictly increasing and unique per symbol;
``close > 0``, ``high >= low``, all numerics finite. A bad row
raises ``NepseLoaderError`` — never silently skipped. The returned
``source`` string must travel with any published result.

Research only. Never a buy/sell/hold recommendation. NEPSE
intraday trading is UPCOMING, not live.
"""

import csv

__all__ = ["NepseLoaderError", "load_nepse_user_csv",
           "load_nepse_user_csv_single"]


class NepseLoaderError(Exception):
    """Raised for any loader problem: missing file, bad row, no source."""


def _require_source(source):
    if not isinstance(source, str) or not source.strip():
        raise NepseLoaderError(
            "nepse_loader: a source must be named (e.g. 'NEPSE "
            "published data, session of 2026-10-29, transcribed by "
            "hand'). Unsourced prices are refused, not loaded.")
    return source.strip()


def _parse_float(row, field, where):
    try:
        value = float(row[field])
    except (KeyError, TypeError, ValueError):
        raise NepseLoaderError(f"{where}: bad or missing {field}")
    if value != value or value in (float("inf"), float("-inf")):
        raise NepseLoaderError(f"{where}: non-finite {field}")
    return value


def _read_rows(path, required):
    try:
        fh = open(path, newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise NepseLoaderError(f"cannot open {path}: {exc}") from exc
    with fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or not required.issubset(
                {f.strip() for f in reader.fieldnames}):
            raise NepseLoaderError(
                f"{path}: expected columns {', '.join(sorted(required))}")
        rows = []
        for n, raw in enumerate(reader, start=2):
            row = {(k.strip() if k else k):
                   (v.strip() if isinstance(v, str) else v)
                   for k, v in raw.items()}
            rows.append((n, row))
    return rows


def load_nepse_user_csv(path, source):
    """Load a user-supplied multi-symbol NEPSE CSV.

    Returns (groups, source) where groups maps symbol ->
    {"sector": str, "closes": [float, ...], "dates": [str, ...]}.
    """
    source = _require_source(source)
    groups = {}
    for n, row in _read_rows(
            path, {"symbol", "sector", "date", "close", "high", "low",
                   "volume"}):
        symbol = row.get("symbol")
        if not symbol or not row.get("date"):
            raise NepseLoaderError(f"{path} line {n}: missing symbol/date")
        close = _parse_float(row, "close", f"{path} line {n}")
        high = _parse_float(row, "high", f"{path} line {n}")
        low = _parse_float(row, "low", f"{path} line {n}")
        _parse_float(row, "volume", f"{path} line {n}")
        if close <= 0:
            raise NepseLoaderError(f"{path} line {n}: close must be > 0")
        if high < low:
            raise NepseLoaderError(f"{path} line {n}: high < low")
        entry = groups.setdefault(symbol, {"sector": row.get("sector", ""),
                                           "closes": [], "dates": []})
        if entry["sector"] == "" and row.get("sector"):
            entry["sector"] = row["sector"]
        entry["closes"].append(close)
        entry["dates"].append(row["date"])
    for symbol, entry in groups.items():
        dates = entry["dates"]
        if dates != sorted(dates) or len(set(dates)) != len(dates):
            raise NepseLoaderError(
                f"{path}: {symbol} dates must be strictly increasing "
                "and unique")
    if not groups:
        raise NepseLoaderError(f"{path}: no data rows")
    return groups, source


def load_nepse_user_csv_single(path, source):
    """Load a user-supplied single-symbol NEPSE CSV.

    Returns (closes, dates, source).
    """
    source = _require_source(source)
    closes, dates = [], []
    for n, row in _read_rows(
            path, {"date", "close", "high", "low", "volume"}):
        if not row.get("date"):
            raise NepseLoaderError(f"{path} line {n}: missing date")
        close = _parse_float(row, "close", f"{path} line {n}")
        high = _parse_float(row, "high", f"{path} line {n}")
        low = _parse_float(row, "low", f"{path} line {n}")
        _parse_float(row, "volume", f"{path} line {n}")
        if close <= 0:
            raise NepseLoaderError(f"{path} line {n}: close must be > 0")
        if high < low:
            raise NepseLoaderError(f"{path} line {n}: high < low")
        closes.append(close)
        dates.append(row["date"])
    if not closes:
        raise NepseLoaderError(f"{path}: no data rows")
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise NepseLoaderError(
            f"{path}: dates must be strictly increasing and unique")
    return closes, dates, source
