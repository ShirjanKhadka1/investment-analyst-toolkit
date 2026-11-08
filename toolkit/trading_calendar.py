"""Trading-day calendar: profile weekdays + holidays + observed bars.

A trading day is counted from two sources, in this order:

1. **Observed bars win.** If a traded bar exists for a date, that date
   was a trading day — even when the profile's weekday calendar says
   otherwise. This is the **special-session rule**: an exchange that
   trades on a non-standard day (for example NSE's Diwali Muhurat
   session on Sunday 2026-11-08, for which Yahoo carries a real
   NIFTYBEES.NS bar) produces a real close, and the settlement count
   must not pretend the session did not happen.
2. **Calendar otherwise.** A date with no observed bar counts when its
   weekday is in the profile's ``trading_weekdays_on(day)`` and it is
   not a listed holiday in ``data/holidays/<market>.json`` (when that
   file exists). A date whose holiday status is UNKNOWN — outside every
   covered range, or a market with no holiday file at all — is treated
   as open: a holiday is never invented, and a missing calendar never
   turns a weekday into a false closure.

A calendar trading day with NO observed bar never counts as a trading
day for settlement: the bar is missing (a missed fetch), not a
non-trading day, and settlement waits instead of silently using the
next bar's close. See ``settlement_date`` and the missed-day rule in
``scripts/track_record_daily.py``.

Original code for this toolkit. Research only.
"""

from __future__ import annotations

import datetime
from functools import cache

from .holidays import load_holidays
from .market_profiles import get_profile

__all__ = [
    "add_trading_days",
    "is_trading_day",
    "settlement_date",
]


def _parse_day(value):
    """A ``datetime.date`` or an ISO ``YYYY-MM-DD`` string -> date."""
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))


@cache
def _calendar_for(market):
    """HolidayCalendar for ``market`` or None when no file exists."""
    try:
        return load_holidays(market)
    except FileNotFoundError:
        return None


def is_trading_day(profile_key, day, observed_dates=frozenset()):
    """True when ``day`` counts as a trading day for ``profile_key``.

    ``observed_dates`` is a set of ISO dates (or ``datetime.date``)
    for which a traded bar exists in the fetched series. An observed
    date always counts — the special-session rule. Otherwise the
    profile's weekday rule for that date decides, minus listed
    holidays. Unknown holiday status is treated as open.
    """
    day = _parse_day(day)
    observed = {_parse_day(d) for d in observed_dates}
    if day in observed:
        return True
    profile = get_profile(profile_key)
    if day.weekday() not in profile.trading_weekdays_on(day):
        return False
    calendar = _calendar_for(profile_key)
    return not (calendar is not None and calendar.is_holiday(day))


def add_trading_days(profile_key, start, n, observed_dates=frozenset()):
    """The date ``n`` trading days after ``start`` (``start`` excluded).

    ``n`` must be a positive integer. Days are counted with
    :func:`is_trading_day`, so an observed special session between
    ``start`` and the result counts toward ``n``.
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"n must be a positive integer, got {n!r}")
    day = _parse_day(start)
    remaining = n
    while remaining > 0:
        day += datetime.timedelta(days=1)
        if is_trading_day(profile_key, day, observed_dates):
            remaining -= 1
    return day


def settlement_date(profile_key, decision_date, horizon=5,
                    observed_dates=frozenset()):
    """The settlement date: ``horizon`` trading days after the call.

    ``decision_date`` itself is excluded: the horizon counts trading
    days *after* the decision day. The entry is the close of the
    decision day, so the first counted day is the next trading day.
    """
    return add_trading_days(profile_key, decision_date, horizon,
                            observed_dates)
