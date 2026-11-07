"""Point-in-time store: every record carries ``as_of`` and ``published_at``.

A record describes a date (``as_of`` — the date the data is about) and
becomes available on a (usually later) date (``published_at`` — when it
was released). A query ``as_of=DATE`` returns only records with
``published_at <= DATE``. That is the whole point: a backtest that
reads through this store can never see data that was not yet published
on its decision date, so look-ahead is impossible by construction
rather than by discipline.

Original code for this toolkit (no outside source). Research only.

Rules, stated so they can be checked:
- Dates are ISO ``YYYY-MM-DD``. ``published_at`` must not precede
  ``as_of``: a value cannot be published before the date it describes.
- Revisions are kept, not overwritten: two records may share an
  ``as_of`` with different ``published_at`` values. A query returns
  the latest revision published on or before the query date.
- Bars carry open/high/low/close (all positive, high >= low,
  high >= close >= low) and optional volume (non-negative).
- Fundamentals are free-form numeric fields (e.g. revenue,
  net_income, shares_outstanding): every value must be a finite
  number, and at least one field is required.
- A query for a date before anything was published returns an empty
  list — never a partial or invented row.
"""
from __future__ import annotations

import datetime

from .checks import require_finite, require_non_negative, require_positive

__all__ = ["PointInTimeStore"]


def _parse_date(name, value):
    """Accept a ``datetime.date`` or an ISO ``YYYY-MM-DD`` string."""
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            raise ValueError(
                f"{name} must be an ISO YYYY-MM-DD date, got {value!r}"
            ) from None
    raise ValueError(f"{name} must be an ISO YYYY-MM-DD date, got {value!r}")


def _check_publication(as_of, published_at):
    if published_at < as_of:
        raise ValueError(
            f"published_at ({published_at.isoformat()}) must not precede "
            f"as_of ({as_of.isoformat()}): a value cannot be published "
            "before the date it describes"
        )


class PointInTimeStore:
    """Daily bars + a small fundamentals table, read point-in-time.

    Usage: add bars and fundamentals as they are published, then read
    with ``bars_as_of(date)`` / ``fundamentals_as_of(date)``. Every
    read is point-in-time correct: only records with
    ``published_at <= date`` are visible.
    """

    def __init__(self):
        self._bars = []  # dicts: as_of/published_at/open/high/low/close/volume
        self._fundamentals = []  # dicts: as_of/published_at/fields

    # --- writes -----------------------------------------------------------

    def add_bar(self, as_of, published_at, open, high, low, close,
                volume=None):
        """Add one daily bar. Returns the stored record (dict)."""
        as_of_d = _parse_date("as_of", as_of)
        pub_d = _parse_date("published_at", published_at)
        _check_publication(as_of_d, pub_d)
        open_v = require_positive("open", open)
        high_v = require_positive("high", high)
        low_v = require_positive("low", low)
        close_v = require_positive("close", close)
        if high_v < low_v:
            raise ValueError(f"high ({high_v}) must not be below low ({low_v})")
        if close_v > high_v:
            raise ValueError(
                f"close ({close_v}) must not be above high ({high_v})")
        if close_v < low_v:
            raise ValueError(
                f"close ({close_v}) must not be below low ({low_v})")
        volume_v = None
        if volume is not None:
            volume_v = require_non_negative("volume", volume)
        record = {"as_of": as_of_d, "published_at": pub_d,
                  "open": float(open_v), "high": float(high_v),
                  "low": float(low_v), "close": float(close_v),
                  "volume": (None if volume_v is None else float(volume_v))}
        self._bars.append(record)
        return dict(record)

    def add_fundamental(self, as_of, published_at, **fields):
        """Add one fundamentals row (e.g. revenue=..., net_income=...).

        Returns the stored record (dict). At least one numeric field
        is required; every value must be a finite number.
        """
        as_of_d = _parse_date("as_of", as_of)
        pub_d = _parse_date("published_at", published_at)
        _check_publication(as_of_d, pub_d)
        if not fields:
            raise ValueError(
                "add_fundamental needs at least one numeric field "
                "(e.g. revenue=...); got none")
        clean = {}
        for name, value in fields.items():
            clean[name] = float(require_finite(name, value))
        record = {"as_of": as_of_d, "published_at": pub_d, "fields": clean}
        self._fundamentals.append(record)
        return {"as_of": as_of_d, "published_at": pub_d, **clean}

    # --- point-in-time reads ----------------------------------------------

    def _latest_visible(self, records, query_date):
        """One record per as_of: the latest published on/before the query."""
        best = {}
        for record in records:
            if record["published_at"] > query_date:
                continue  # not yet published: invisible to this query
            key = record["as_of"]
            current = best.get(key)
            if current is None or record["published_at"] >= current["published_at"]:
                best[key] = record
        return [best[key] for key in sorted(best)]

    def bars_as_of(self, query_date):
        """Daily bars visible on ``query_date``, sorted by as_of.

        Each bar is a dict with ISO ``date`` plus open/high/low/close
        and volume (None when the bar was stored without one). Only
        bars with ``published_at <= query_date`` appear, one per
        as_of date (the latest published revision).
        """
        query = _parse_date("query_date", query_date)
        return [{"date": r["as_of"].isoformat(), "open": r["open"],
                 "high": r["high"], "low": r["low"], "close": r["close"],
                 "volume": r["volume"]}
                for r in self._latest_visible(self._bars, query)]

    def closes_as_of(self, query_date):
        """``(date, close)`` pairs visible on ``query_date``."""
        return [(b["date"], b["close"]) for b in self.bars_as_of(query_date)]

    def fundamentals_as_of(self, query_date):
        """Fundamentals rows visible on ``query_date``, sorted by as_of.

        Each row is a dict with ISO ``date`` plus the stored fields,
        one per as_of date (the latest published revision).
        """
        query = _parse_date("query_date", query_date)
        out = []
        for r in self._latest_visible(self._fundamentals, query):
            row = {"date": r["as_of"].isoformat()}
            row.update(r["fields"])
            out.append(row)
        return out

    def latest_fundamental(self, query_date, field):
        """The latest visible value of one fundamental field.

        Returns ``(as_of_date, value)`` or None when no visible row
        carries that field.
        """
        rows = [r for r in self.fundamentals_as_of(query_date)
                if field in r]
        if not rows:
            return None
        last = rows[-1]
        return last["date"], last[field]

    def bars_for_timemachine(self, query_date):
        """Bars visible on ``query_date`` as OHLC dicts for
        ``toolkit.timemachine.simulate`` / ``replay_history``."""
        return [{"open": b["open"], "high": b["high"], "low": b["low"],
                 "close": b["close"]}
                for b in self.bars_as_of(query_date)]

    # --- inventory ----------------------------------------------------------

    def __len__(self):
        """Total records stored (bars + fundamentals), visible or not."""
        return len(self._bars) + len(self._fundamentals)

    def as_of_dates(self):
        """Sorted as_of dates held, bars and fundamentals combined."""
        dates = {r["as_of"] for r in self._bars}
        dates.update(r["as_of"] for r in self._fundamentals)
        return [d.isoformat() for d in sorted(dates)]
