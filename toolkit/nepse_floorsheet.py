"""NEPSE floorsheet CSV importer + broker-flow analytics.

Stdlib only. A floorsheet — the broker-by-broker record of who
bought and sold what, at what price — arrives here ONLY as a
user-supplied CSV. There is no live floorsheet feed: no documented,
free, public NEPSE floorsheet API exists, and none is claimed or
wired in. The loader follows the same loud-error discipline as
``toolkit/nepse_loader.py``: a bad row raises ``FloorsheetError``
naming the line, never silently skipped, and a source must be named
— unsourced floorsheets are refused, not loaded.

Analytics are descriptive counts of the supplied rows (top buyers
and sellers per symbol, net flow per broker, concentration), not
signals: a broker buying heavily is a fact about the supplied
rows, not a recommendation about the symbol.

Research only. Never investment advice.
"""
from __future__ import annotations

import csv
import datetime
import math

__all__ = [
    "FloorsheetError",
    "broker_flow",
    "concentration",
    "load_floorsheet_csv",
    "symbol_flow",
    "top_flow",
]

REQUIRED_COLUMNS = {"date", "broker", "symbol", "side", "quantity",
                    "price"}


class FloorsheetError(ValueError):
    """Raised for any floorsheet problem: missing file, bad row,
    no source."""


def load_floorsheet_csv(path, source):
    """Load a user-supplied NEPSE floorsheet CSV.

    Required columns: ``date,broker,symbol,side,quantity,price``.
    ``side`` must be ``buy`` or ``sell``; ``quantity`` a positive
    integer; ``price`` a positive finite number; ``date`` ISO
    ``YYYY-MM-DD``. ``#`` comment lines are skipped (the bundled
    demo file carries a SYNTHETIC header comment). Returns
    ``(rows, source)`` where rows is a list of
    ``{"date", "broker", "symbol", "side", "quantity", "price",
    "value"}`` with ``value = quantity * price``. Any bad row raises
    ``FloorsheetError`` naming the line. A source must be named.
    """
    if not isinstance(source, str) or not source.strip():
        raise FloorsheetError(
            "nepse_floorsheet: a source must be named (e.g. 'NEPSE "
            "floorsheet for 2026-11-13, transcribed by hand'). "
            "Unsourced floorsheets are refused, not loaded.")
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
    except OSError as exc:
        raise FloorsheetError(f"cannot open {path}: {exc}") from exc
    reader = csv.DictReader(lines)
    if reader.fieldnames is None or not REQUIRED_COLUMNS.issubset(
            {f.strip() for f in reader.fieldnames}):
        raise FloorsheetError(
            f"{path}: expected columns "
            f"{', '.join(sorted(REQUIRED_COLUMNS))}")
    rows = []
    for n, raw in enumerate(reader, start=2):
        row = {(k.strip() if k else k):
               (v.strip() if isinstance(v, str) else v)
               for k, v in raw.items()}
        where = f"{path} line {n}"
        date = row.get("date")
        broker = row.get("broker")
        symbol = row.get("symbol")
        side = row.get("side")
        if not date or not broker or not symbol or not side:
            raise FloorsheetError(
                f"{where}: missing date/broker/symbol/side")
        try:
            parsed = datetime.date.fromisoformat(date)
        except ValueError as exc:
            raise FloorsheetError(
                f"{where}: date must be ISO YYYY-MM-DD, got {date!r}"
                ) from exc
        if side not in ("buy", "sell"):
            raise FloorsheetError(
                f"{where}: side must be 'buy' or 'sell', got {side!r}")
        try:
            quantity = int(row.get("quantity", ""))
        except (TypeError, ValueError) as exc:
            raise FloorsheetError(
                f"{where}: quantity must be a positive integer, got "
                f"{row.get('quantity')!r}") from exc
        if str(row.get("quantity", "")).strip() != str(quantity) \
                or quantity <= 0:
            raise FloorsheetError(
                f"{where}: quantity must be a positive integer, got "
                f"{row.get('quantity')!r}")
        try:
            price = float(row.get("price", ""))
        except (TypeError, ValueError) as exc:
            raise FloorsheetError(
                f"{where}: price must be a positive number, got "
                f"{row.get('price')!r}") from exc
        if not math.isfinite(price):
            raise FloorsheetError(f"{where}: price must be finite")
        if price <= 0:
            raise FloorsheetError(f"{where}: price must be > 0")
        rows.append({
            "date": parsed.isoformat(),
            "broker": broker,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "value": quantity * price,
        })
    if not rows:
        raise FloorsheetError(f"{path}: no data rows")
    return rows, source.strip()


# ---------------------------------------------------------------------------
# Broker-flow analytics (descriptive only, over the supplied rows)
# ---------------------------------------------------------------------------

def broker_flow(rows):
    """Net flow per broker over the supplied rows.

    Returns ``{broker: {"buy_quantity", "sell_quantity",
    "buy_value", "sell_value", "net_flow", "net_quantity",
    "n_trades"}}`` where ``net_flow = buy_value - sell_value``
    (positive = net buyer in value terms) and ``net_quantity =
    buy_quantity - sell_quantity``. An empty row list raises —
    flow over no rows is not zero flow, it is no data.
    """
    if not rows:
        raise FloorsheetError("no floorsheet rows to analyse")
    out = {}
    for row in rows:
        entry = out.setdefault(row["broker"], {
            "buy_quantity": 0, "sell_quantity": 0,
            "buy_value": 0.0, "sell_value": 0.0, "n_trades": 0})
        if row["side"] == "buy":
            entry["buy_quantity"] += row["quantity"]
            entry["buy_value"] += row["value"]
        else:
            entry["sell_quantity"] += row["quantity"]
            entry["sell_value"] += row["value"]
        entry["n_trades"] += 1
    for entry in out.values():
        entry["net_flow"] = entry["buy_value"] - entry["sell_value"]
        entry["net_quantity"] = (
            entry["buy_quantity"] - entry["sell_quantity"])
    return out


def top_flow(rows, symbol, side="buy", top_n=3):
    """Top brokers by value on one side of one symbol.

    Returns a list of ``(broker, value)`` sorted by value
    descending, at most ``top_n`` entries. ``side`` must be ``buy``
    or ``sell``. A symbol with no rows on that side returns an
    empty list — that is a fact about the supplied rows, not an
    error.
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 1:
        raise ValueError(f"top_n must be a positive int, got {top_n!r}")
    totals = {}
    for row in rows:
        if row["symbol"] == symbol and row["side"] == side:
            totals[row["broker"]] = totals.get(row["broker"], 0.0) \
                + row["value"]
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]


def concentration(rows, symbol):
    """Broker concentration for one symbol's traded value.

    Returns ``{"n_brokers", "top3_share", "hhi"}`` where
    ``top3_share`` is the top three brokers' combined share of the
    symbol's total traded value (both sides) and ``hhi`` is the
    Herfindahl-Hirschman index of broker value shares (sum of
    squared shares, 1/n_brokers at perfect equality, 1.0 for a
    single broker). A symbol with no rows raises — concentration
    over no trades is undefined, not zero.
    """
    totals = {}
    for row in rows:
        if row["symbol"] == symbol:
            totals[row["broker"]] = totals.get(row["broker"], 0.0) \
                + row["value"]
    if not totals:
        raise FloorsheetError(f"no floorsheet rows for symbol {symbol!r}")
    total = math.fsum(totals.values())
    shares = sorted((v / total for v in totals.values()), reverse=True)
    top3 = math.fsum(shares[:3])
    hhi = math.fsum(s * s for s in shares)
    return {"n_brokers": len(totals), "top3_share": top3, "hhi": hhi}


def symbol_flow(rows, symbol):
    """One symbol's full broker-flow summary.

    Returns ``{"symbol", "total_quantity", "total_value",
    "top_buyers", "top_sellers", "top3_share", "hhi", "n_brokers"}``
    — top buyers/sellers from ``top_flow`` (top 3 by value),
    concentration from ``concentration``.
    """
    if not any(row["symbol"] == symbol for row in rows):
        raise FloorsheetError(f"no floorsheet rows for symbol {symbol!r}")
    total_quantity = sum(row["quantity"] for row in rows
                         if row["symbol"] == symbol)
    total_value = math.fsum(row["value"] for row in rows
                            if row["symbol"] == symbol)
    conc = concentration(rows, symbol)
    return {
        "symbol": symbol,
        "total_quantity": total_quantity,
        "total_value": total_value,
        "top_buyers": top_flow(rows, symbol, side="buy", top_n=3),
        "top_sellers": top_flow(rows, symbol, side="sell", top_n=3),
        "top3_share": conc["top3_share"],
        "hhi": conc["hhi"],
        "n_brokers": conc["n_brokers"],
    }
