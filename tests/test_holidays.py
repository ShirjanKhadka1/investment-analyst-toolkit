"""Holiday calendars (8 Nov 2026): seeded only from official notices.

A date outside every covered range is UNKNOWN, never assumed a
trading day and never assumed a holiday.
"""

import datetime

import pytest

from toolkit.holidays import HolidayCalendar, load_holidays


def test_load_nepse_dashain_2083():
    calendar = load_holidays("nepse")
    assert calendar.market == "nepse"
    assert len(calendar.entries) == 9
    assert all(e.name == "Dashain 2083 closure" for e in calendar.entries)
    assert all(e.source_url and len(e.as_of) == 10 for e in calendar.entries)
    # The closure takes out the five weekday sessions Mon 19-Fri 23 Oct.
    assert calendar.is_holiday(datetime.date(2026, 10, 19))
    assert calendar.is_holiday(datetime.date(2026, 10, 23))
    assert calendar.is_holiday(datetime.date(2026, 10, 25))
    assert not calendar.is_holiday(datetime.date(2026, 10, 26))
    assert not calendar.is_holiday(datetime.date(2026, 10, 16))


def test_calendar_status_on_distinguishes_unknown():
    calendar = load_holidays("nepse")
    assert calendar.status_on(datetime.date(2026, 10, 19)) == "holiday"
    assert calendar.status_on(datetime.date(2026, 10, 26)) == "unknown"
    assert calendar.status_on(datetime.date(2026, 11, 8)) == "unknown"
    # Tihar 2083 is deliberately NOT seeded: no official NEPSE notice
    # text was obtained, so its dates stay unknown, not invented.
    assert calendar.status_on(datetime.date(2026, 11, 9)) == "unknown"


def test_load_unknown_market_raises():
    with pytest.raises(FileNotFoundError):
        load_holidays("no-such-market")


def test_empty_calendar_is_all_unknown():
    calendar = HolidayCalendar(market="test", entries=(), covered_ranges=())
    assert calendar.status_on(datetime.date(2026, 11, 8)) == "unknown"
    assert not calendar.is_holiday(datetime.date(2026, 11, 8))
