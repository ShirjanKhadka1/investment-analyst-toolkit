"""Research report pipeline (Phase 1 engine 1.9).

``research <SYMBOL>`` produces a one-page HTML report — thesis, bull/bear,
peers and sector comps, risks, cited sources — from data the caller
supplies (price bars, fundamentals rows, peer rows, all as CSVs or plain
Python values). Standard library only; no network, no keys, no live
trading, no orders.

Research and education only: not investment advice, never a buy/sell/hold
recommendation. Every number in the report traces to an input row or is
labelled UNKNOWN. If no data exists for a symbol, the report says so
plainly instead of inventing figures.

Honesty rules, enforced by construction:
- Bars, fundamentals, and peers are all caller-supplied. A figure with
  no source is rendered as UNKNOWN, never filled in.
- Peers/comps are labelled with their source per row; a peer row with no
  source is UNKNOWN.
- Derived figures (SMA, RSI, 20-session change, premium/discount) are
  computed from the supplied bars only, and the report names the input
  they came from (row count + date range).
- A symbol with no bars, no fundamentals, and no peers produces a
  ``no_data`` report whose sections say so in plain language.

CSV formats (headers case-insensitive, ``#`` comment lines skipped):
- bars: ``date,open,high,low,close,volume`` (only ``date`` and ``close``
  are required; same tolerance as ``toolkit.terminal.load_bars``).
- fundamentals: ``field,value,source,period`` — one row per figure.
- peers: ``symbol,metric,value,source`` — one row per peer metric.
  A peer row with an empty ``source`` is UNKNOWN.
"""

from __future__ import annotations

import csv
import datetime
import html
import math
from pathlib import Path

from . import indicators

__all__ = [
    "bars_from_csv",
    "build_report",
    "fundamentals_from_csv",
    "peers_from_csv",
    "render_html",
    "write_report_html",
]

_LABEL_NOT_ADVICE = (
    "Research and education only — not investment advice, "
    "never a buy/sell/hold recommendation."
)

_MISSING = "UNKNOWN"


def _read_csv_rows(path):
    """Read a CSV file, skipping blank lines and ``#`` comment lines."""
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            lines = [ln for ln in handle
                     if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc.strerror or exc}") from None
    if not lines:
        raise ValueError(f"{path}: file is empty (no header, no data rows)")
    reader = csv.DictReader(lines)
    fields = {(name or "").strip().lower(): name
              for name in (reader.fieldnames or [])}
    return reader, fields


def _number(row_no, field, raw):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"row {row_no}: {field} {raw!r} is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"row {row_no}: {field} {raw!r} is not finite")
    return value


def bars_from_csv(path):
    """Load daily bars from a CSV file.

    Returns a list of dicts with keys date/open/high/low/close/volume
    (volume is None when the column is absent). Raises ValueError with a
    plain message — never a traceback — on malformed input.
    """
    reader, fields = _read_csv_rows(path)
    if "date" not in fields or "close" not in fields:
        raise ValueError(
            f"{path}: bars CSV must have 'date' and 'close' columns, "
            f"found {sorted(fields)}")
    bars = []
    for row_no, row in enumerate(reader, start=2):
        raw_date = (row.get(fields["date"]) or "").strip()
        try:
            date = datetime.date.fromisoformat(raw_date)
        except ValueError:
            raise ValueError(
                f"row {row_no}: date {raw_date!r} is not ISO YYYY-MM-DD"
            ) from None
        close = _number(row_no, "close", row.get(fields["close"]))
        if close <= 0:
            raise ValueError(f"row {row_no}: close must be positive")
        bar = {"date": date, "close": close, "open": None,
               "high": None, "low": None, "volume": None}
        for key in ("open", "high", "low"):
            if key in fields and (row.get(fields[key]) or "").strip():
                value = _number(row_no, key, row.get(fields[key]))
                if value <= 0:
                    raise ValueError(f"row {row_no}: {key} must be positive")
                bar[key] = value
        if "volume" in fields and (row.get(fields["volume"]) or "").strip():
            volume = _number(row_no, "volume", row.get(fields["volume"]))
            if volume < 0:
                raise ValueError(f"row {row_no}: volume must not be negative")
            bar["volume"] = volume
        bar["open"] = bar["open"] if bar["open"] is not None else close
        bar["high"] = bar["high"] if bar["high"] is not None else close
        bar["low"] = bar["low"] if bar["low"] is not None else close
        bars.append(bar)
    if not bars:
        raise ValueError(f"{path}: no data rows")
    return bars


def fundamentals_from_csv(path):
    """Load fundamentals rows: ``field,value,source,period``.

    Returns a list of dicts with keys field/value/source/period. The
    source may be empty — such a row is UNKNOWN downstream, never
    dropped and never filled in.
    """
    reader, fields = _read_csv_rows(path)
    if "field" not in fields or "value" not in fields:
        raise ValueError(
            f"{path}: fundamentals CSV must have 'field' and 'value' "
            f"columns, found {sorted(fields)}")
    rows = []
    for row_no, row in enumerate(reader, start=2):
        field = (row.get(fields["field"]) or "").strip()
        if not field:
            raise ValueError(f"row {row_no}: field must not be empty")
        value = _number(row_no, "value", row.get(fields["value"]))
        rows.append({
            "field": field,
            "value": value,
            "source": (row.get(fields["source"]) or "").strip()
            if "source" in fields else "",
            "period": (row.get(fields["period"]) or "").strip()
            if "period" in fields else "",
        })
    if not rows:
        raise ValueError(f"{path}: no data rows")
    return rows


def peers_from_csv(path):
    """Load peer rows: ``symbol,metric,value,source``.

    Returns a list of dicts with keys symbol/metric/value/source. The
    source may be empty — such a row is UNKNOWN downstream.
    """
    reader, fields = _read_csv_rows(path)
    if "symbol" not in fields or "metric" not in fields \
            or "value" not in fields:
        raise ValueError(
            f"{path}: peers CSV must have 'symbol', 'metric' and 'value' "
            f"columns, found {sorted(fields)}")
    rows = []
    for row_no, row in enumerate(reader, start=2):
        symbol = (row.get(fields["symbol"]) or "").strip()
        metric = (row.get(fields["metric"]) or "").strip()
        if not symbol or not metric:
            raise ValueError(
                f"row {row_no}: symbol and metric must not be empty")
        value = _number(row_no, "value", row.get(fields["value"]))
        rows.append({
            "symbol": symbol,
            "metric": metric,
            "value": value,
            "source": (row.get(fields["source"]) or "").strip()
            if "source" in fields else "",
        })
    if not rows:
        raise ValueError(f"{path}: no data rows")
    return rows


def _fmt(value):
    """Format a number for display, or UNKNOWN for None."""
    if value is None:
        return _MISSING
    if isinstance(value, float) and value == int(value):
        return f"{value:,.0f}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def _source_label(source):
    return source if source else _MISSING


def build_report(symbol, *, bars=None, fundamentals=None, peers=None,
                 sector=None, exchange="NEPSE", sources=None, as_of=None):
    """Build a one-page research report dict for ``symbol``.

    ``bars`` is a list of ``{date, close}`` dicts (as returned by
    :func:`bars_from_csv`); ``fundamentals`` a list of
    ``{field, value, source, period}`` dicts; ``peers`` a list of
    ``{symbol, metric, value, source}`` dicts. ``sources`` is an extra
    list of free-text source notes. ``as_of`` is an ISO date string
    (defaults to today). Nothing is fetched: every figure comes from
    these inputs or is UNKNOWN.
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol must not be empty")
    bars = list(bars or [])
    fundamentals = list(fundamentals or [])
    peers = list(peers or [])
    if as_of is None:
        as_of = datetime.date.today().isoformat()
    has_data = bool(bars or fundamentals or peers)

    figures = {}
    thesis_lines = []
    bull = []
    bear = []
    risks = []
    if bars:
        closes = [b["close"] for b in bars]
        dates = [b["date"] for b in bars]
        first, last = dates[0], dates[-1]
        bar_source = (f"supplied bars: {len(bars)} rows, "
                      f"{first.isoformat()} to {last.isoformat()}")
        figures["latest_close"] = {"value": closes[-1], "source": bar_source}
        figures["bars_n"] = {"value": len(bars), "source": bar_source}
        if len(closes) >= 20:
            sma20 = indicators.sma(closes, 20)[-1]
            figures["sma20"] = {"value": sma20, "source": bar_source}
            change_20 = (closes[-1] / closes[-20] - 1.0) * 100.0
            figures["change_20d_pct"] = {"value": change_20,
                                         "source": bar_source}
        if len(closes) >= 15:
            rsi14 = indicators.rsi(closes, 14)[-1]
            figures["rsi14"] = {"value": rsi14, "source": bar_source}
        latest = closes[-1]
        thesis_lines.append(
            f"Latest close {latest:,.2f} on {last.isoformat()} "
            f"({len(bars)} supplied bars, {first.isoformat()} to "
            f"{last.isoformat()}).")
        if "sma20" in figures:
            sma20 = figures["sma20"]["value"]
            change = figures["change_20d_pct"]["value"]
            thesis_lines.append(
                f"20-day moving average {sma20:,.2f}; latest close is "
                f"{'above' if latest > sma20 else 'at or below'} it, "
                f"{change:+.1f}% over the last 20 sessions.")
            if latest > sma20:
                bull.append(
                    f"Latest close above its 20-day moving average "
                    f"({latest:,.2f} vs {sma20:,.2f}, supplied bars).")
            else:
                bear.append(
                    f"Latest close at or below its 20-day moving average "
                    f"({latest:,.2f} vs {sma20:,.2f}, supplied bars).")
            if change > 0:
                bull.append(f"Close up {change:.1f}% over the last "
                            f"20 sessions (supplied bars).")
            elif change < 0:
                bear.append(f"Close down {change:.1f}% over the last "
                            f"20 sessions (supplied bars).")
        if "rsi14" in figures:
            rsi14 = figures["rsi14"]["value"]
            thesis_lines.append(f"Wilder RSI-14 {rsi14:.1f} (past bars only).")
            if rsi14 < 30:
                bull.append(f"RSI-14 {rsi14:.1f} is below 30 (oversold "
                            f"read on past bars only — not a signal).")
            elif rsi14 > 70:
                bear.append(f"RSI-14 {rsi14:.1f} is above 70 (overbought "
                            f"read on past bars only — not a signal).")
        if len(bars) < 20:
            risks.append(
                f"Short price history: only {len(bars)} bars supplied, so "
                f"no 20-day moving average or 20-session change could be "
                f"computed.")
    else:
        thesis_lines.append("No price bars were supplied for this symbol.")

    fund_rows = []
    for row in fundamentals:
        fund_rows.append({
            "field": row["field"],
            "value": row["value"],
            "source": _source_label(row.get("source")),
            "period": row.get("period") or _MISSING,
        })
    if fund_rows:
        thesis_lines.append(
            f"{len(fund_rows)} fundamentals figure(s) supplied "
            f"(each with its source or UNKNOWN).")
    else:
        thesis_lines.append("No fundamentals figures were supplied.")

    peer_rows = []
    for row in peers:
        peer_rows.append({
            "symbol": row["symbol"],
            "metric": row["metric"],
            "value": row["value"],
            "source": _source_label(row.get("source")),
        })

    if not has_data:
        thesis = (
            f"No data was supplied for {symbol}. This report contains no "
            f"figures for the symbol — nothing has been estimated or "
            f"filled in. Supply bars, fundamentals, or peer rows and "
            f"re-run."
        )
        risks = [
            ("No data at all was supplied, so no thesis, peer comparison, "
             "or risk read is possible."),
        ]
    else:
        thesis = " ".join(thesis_lines)
        risks.append(
            "Every figure above traces to a supplied input row or is "
            "labelled UNKNOWN; figures labelled UNKNOWN must be sourced "
            "before any claim is built on them.")
        risks.append(
            "Past bars describe the past. A moving average, RSI, or "
            "20-session change is not a forecast.")

    return {
        "symbol": symbol,
        "exchange": exchange,
        "sector": sector or _MISSING,
        "as_of": as_of,
        "status": "ok" if has_data else "no_data",
        "thesis": thesis,
        "bull": bull,
        "bear": bear,
        "figures": figures,
        "fundamentals": fund_rows,
        "peers": peer_rows,
        "risks": risks,
        "sources": list(sources or []),
    }


def render_html(report):
    """Render a report dict from :func:`build_report` as one HTML page."""
    esc = html.escape
    symbol = esc(report["symbol"])
    rows = []
    rows.append("<!DOCTYPE html>")
    rows.append('<html lang="en"><head><meta charset="utf-8">')
    rows.append(f"<title>Research report — {symbol}</title>")
    rows.append("<style>body{font-family:sans-serif;max-width:760px;margin:2em auto;"
                "padding:0 1em;color:#111}h1{font-size:1.4em}h2{font-size:1.1em;"
                "margin-top:1.5em}table{border-collapse:collapse;width:100%}"
                "th,td{border:1px solid #ccc;padding:4px 8px;text-align:left;"
                "font-size:.9em}.unknown{color:#888}.note{color:#555;"
                "font-size:.9em}</style></head><body>")
    rows.append(f"<h1>Research report — {symbol} ({esc(report['exchange'])})</h1>")
    rows.append(f"<p class=\"note\">{esc(_LABEL_NOT_ADVICE)} "
                f"As of {esc(report['as_of'])}. Sector: {esc(report['sector'])}.</p>")
    rows.append("<h2>Thesis</h2>")
    rows.append(f"<p>{esc(report['thesis'])}</p>")
    rows.append("<h2>Key figures (from supplied inputs)</h2>")
    if report["figures"]:
        rows.append("<table><tr><th>Figure</th><th>Value</th><th>Source</th></tr>")
        for name, fig in report["figures"].items():
            rows.append(f"<tr><td>{esc(name)}</td><td>{esc(_fmt(fig['value']))}"
                        f"</td><td>{esc(fig['source'])}</td></tr>")
        rows.append("</table>")
    else:
        rows.append("<p>No figures could be computed — no price bars were supplied.</p>")
    rows.append("<h2>Bull case (from the supplied data)</h2>")
    if report["bull"]:
        rows.append("<ul>" + "".join(f"<li>{esc(b)}</li>" for b in report["bull"]) + "</ul>")
    else:
        rows.append("<p>Nothing in the supplied data supports a bull read.</p>")
    rows.append("<h2>Bear case (from the supplied data)</h2>")
    if report["bear"]:
        rows.append("<ul>" + "".join(f"<li>{esc(b)}</li>" for b in report["bear"]) + "</ul>")
    else:
        rows.append("<p>Nothing in the supplied data supports a bear read.</p>")
    rows.append("<h2>Fundamentals</h2>")
    if report["fundamentals"]:
        rows.append("<table><tr><th>Field</th><th>Value</th><th>Source</th><th>Period</th></tr>")
        for row in report["fundamentals"]:
            cls = " class=\"unknown\"" if row["source"] == _MISSING else ""
            rows.append(f"<tr><td>{esc(row['field'])}</td><td>{esc(_fmt(row['value']))}</td>"
                        f"<td{cls}>{esc(row['source'])}</td><td>{esc(row['period'])}</td></tr>")
        rows.append("</table>")
    else:
        rows.append("<p>No fundamentals figures were supplied.</p>")
    rows.append("<h2>Peers and sector comps</h2>")
    if report["peers"]:
        rows.append("<table><tr><th>Peer</th><th>Metric</th><th>Value</th><th>Source</th></tr>")
        for row in report["peers"]:
            cls = " class=\"unknown\"" if row["source"] == _MISSING else ""
            rows.append(f"<tr><td>{esc(row['symbol'])}</td><td>{esc(row['metric'])}</td>"
                        f"<td>{esc(_fmt(row['value']))}</td><td{cls}>{esc(row['source'])}</td></tr>")
        rows.append("</table>")
    else:
        rows.append("<p>No peer rows were supplied — no comparison is shown, and none was invented.</p>")
    rows.append("<h2>Risks</h2>")
    rows.append("<ul>" + "".join(f"<li>{esc(r)}</li>" for r in report["risks"]) + "</ul>")
    if report["sources"]:
        rows.append("<h2>Sources</h2>")
        rows.append("<ul>" + "".join(f"<li>{esc(s)}</li>" for s in report["sources"]) + "</ul>")
    rows.append("<p class=\"note\">Generated offline by investment-analyst-toolkit "
                "(engine 1.9). No live data, no forecast, no recommendation.</p>")
    rows.append("</body></html>")
    return "\n".join(rows) + "\n"


def write_report_html(report, path):
    """Write :func:`render_html` output to ``path`` and return the path."""
    path = Path(path)
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(report), encoding="utf-8")
    return path
