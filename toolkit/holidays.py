"""Per-market holiday calendars.

A calendar is a JSON file under ``data/holidays/<market>.json``:

    {
      "market": "nepse",
      "entries": [
        {"date": "2026-10-19", "name": "Dashain 2083 closure",
         "source_url": "https://...", "as_of": "2026-11-08",
         "notice": "NEPSE Dashain closure notice (nepalstock.com notices)"}
      ],
      "covered_ranges": [
        {"from": "2026-10-17", "to": "2026-10-25",
         "note": "Dashain 2083 closure, complete for this range"}
      ],
      "coverage_note": "Coverage is NOT complete: only the ranges "
                       "listed are covered. Dates outside every "
                       "covered range are UNKNOWN."
    }

Rules for seeding: an entry comes only from an official exchange
notice (or a republication of one that names the notice). A
secondary calendar alone never seeds an entry.

Dates outside every covered range are UNKNOWN — the calendar
cannot say whether the market was open. Freshness must degrade to
UNKNOWN on that ground, never to a false STALE (see
``connectors.evaluate_freshness``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path

__all__ = ["HolidayCalendar", "HolidayEntry", "load_holidays"]


@dataclass(frozen=True)
class HolidayEntry:
    """One closed date: its name, source URL and as-of date."""

    date: _date
    name: str
    source_url: str
    as_of: str
    notice: str = ""


@dataclass(frozen=True)
class HolidayCalendar:
    """A market's holiday calendar with declared coverage.

    ``covered_ranges`` are (start, end) date pairs the file claims
    to cover COMPLETELY: every holiday inside a covered range is
    listed, so an unlisted date inside a covered range was a
    trading day (if a trading weekday). Outside every covered
    range, a date's holiday status is UNKNOWN.
    """

    market: str
    entries: tuple = ()
    covered_ranges: tuple = ()
    coverage_note: str = ""

    def covers(self, day):
        """True when ``day`` is inside a covered range."""
        day = _as_date(day)
        return any(start <= day <= end for start, end in self.covered_ranges)

    def is_holiday(self, day):
        """True when ``day`` is a listed holiday."""
        day = _as_date(day)
        return any(entry.date == day for entry in self.entries)

    def status_on(self, day):
        """``holiday`` | ``not-listed`` | ``unknown`` for ``day``.

        ``not-listed`` means the day is inside a covered range and
        not listed, so it was not a holiday. ``unknown`` means the
        day is outside every covered range: the calendar cannot say.
        """
        day = _as_date(day)
        if self.is_holiday(day):
            return "holiday"
        if self.covers(day):
            return "not-listed"
        return "unknown"


def _as_date(value):
    if isinstance(value, _date):
        return value
    return _date.fromisoformat(str(value))


def load_holidays(market, repo_root=None):
    """Load ``data/holidays/<market>.json`` into a HolidayCalendar.

    Raises FileNotFoundError when no file exists for the market
    (callers treat that as "no calendar", not as "no holidays").
    """
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parent.parent
    path = root / "data" / "holidays" / f"{market}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = tuple(
        HolidayEntry(
            date=_date.fromisoformat(item["date"]),
            name=item["name"],
            source_url=item["source_url"],
            as_of=item["as_of"],
            notice=item.get("notice", ""),
        )
        for item in raw.get("entries", [])
    )
    covered = tuple(
        (_date.fromisoformat(item["from"]), _date.fromisoformat(item["to"]))
        for item in raw.get("covered_ranges", [])
    )
    return HolidayCalendar(
        market=raw.get("market", market),
        entries=entries,
        covered_ranges=covered,
        coverage_note=raw.get("coverage_note", ""),
    )
