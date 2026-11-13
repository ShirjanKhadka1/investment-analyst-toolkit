"""Tests for the trading-day calendar and the special-session rule.

The rule under test: a bar dated on a non-standard day counts as a
trading day when the exchange traded and published a close. The
proving case is the NIFTYBEES.NS bar dated Sunday 2026-11-08 —
NSE's Diwali Muhurat session (NSE circular NSE/CMTR/76941, 19 Oct
2026). Tuesday 10 November 2026 is the Diwali-Balipratipada
holiday (data/holidays/india.json).
"""
from datetime import date

from toolkit.trading_calendar import (
    add_trading_days,
    is_trading_day,
    settlement_date,
)

# NIFTYBEES.NS bar dates for the Diwali week, as Yahoo carries them:
# a bar on Sunday 8 Nov (Muhurat session), none on Tuesday 10 Nov
# (holiday), bars on every other weekday.
DIWALI_WEEK_BARS = frozenset({
    "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05",
    "2026-11-06", "2026-11-08", "2026-11-09", "2026-11-11",
    "2026-11-12", "2026-11-13", "2026-11-16",
})


def test_weekday_and_weekend_without_a_bar():
    assert is_trading_day("india", "2026-11-06") is True  # Friday
    assert is_trading_day("india", "2026-11-07") is False  # Saturday
    assert is_trading_day("india", "2026-11-08") is False  # Sunday, no bar


def test_special_session_bar_counts_as_a_trading_day():
    """The 2026-11-08 NIFTYBEES.NS bar: a Sunday that traded."""
    assert is_trading_day("india", "2026-11-08",
                          DIWALI_WEEK_BARS) is True


def test_diwali_balipratipada_is_a_holiday():
    assert is_trading_day("india", "2026-11-10",
                          DIWALI_WEEK_BARS) is False


def test_settlement_date_counts_the_muhurat_session():
    """Call decided Fri 2026-11-06 settles Fri 13 Nov with the bar."""
    assert settlement_date("india", "2026-11-06", 5,
                           DIWALI_WEEK_BARS) == date(2026, 11, 13)


def test_settlement_date_without_the_special_bar():
    """Without the 8 Nov bar the same call settles Mon 16 Nov."""
    without = frozenset(d for d in DIWALI_WEEK_BARS if d != "2026-11-08")
    assert settlement_date("india", "2026-11-06", 5,
                           without) == date(2026, 11, 16)


def test_add_trading_days_rejects_bad_n():
    import pytest

    with pytest.raises(ValueError):
        add_trading_days("us", "2026-11-06", 0)
    with pytest.raises(ValueError):
        add_trading_days("us", "2026-11-06", -3)


def test_crypto_trades_every_day():
    assert settlement_date("crypto", "2026-11-06", 5) == date(2026, 11, 11)


# --- 5-trading-day windows per watchlist calendar (Round 5, Task 1) ---
# One "trading week" is 5 trading days on the ticker's own profile
# calendar (docs/watchlist-proposal.md). Pinned here from the same
# decision date, Friday 2026-11-06, so the calendars can be compared.


def test_five_trading_day_windows_per_calendar():
    """5 trading days from Fri 2026-11-06, per watchlist profile."""
    cases = [
        # (profile, observed bars, expected settlement date)
        ("us", frozenset(), date(2026, 11, 13)),      # Mon-Fri, no holiday file
        ("uk", frozenset(), date(2026, 11, 13)),      # Mon-Fri, no holiday file
        ("fx", frozenset(), date(2026, 11, 13)),      # Mon-Fri (24/5), no holiday file
        ("crypto", frozenset(), date(2026, 11, 11)),  # every day: 5 calendar days
        ("india", DIWALI_WEEK_BARS, date(2026, 11, 13)),  # Muhurat Sun bar counts
        ("india", frozenset(d for d in DIWALI_WEEK_BARS
                            if d != "2026-11-08"),
         date(2026, 11, 16)),  # no Muhurat bar: Tue 10 Nov is a holiday
    ]
    for profile, observed, expected in cases:
        assert settlement_date(profile, "2026-11-06", 5,
                               observed) == expected, profile


def test_weekend_days_per_calendar():
    """Crypto counts Sat/Sun; FX and US skip them (Round 5, Task 1)."""
    assert is_trading_day("crypto", "2026-11-07") is True   # Saturday
    assert is_trading_day("crypto", "2026-11-08") is True   # Sunday
    assert is_trading_day("fx", "2026-11-07") is False      # Saturday
    assert is_trading_day("fx", "2026-11-08") is False      # Sunday
    assert is_trading_day("us", "2026-11-07") is False      # Saturday
    assert is_trading_day("us", "2026-11-08") is False      # Sunday


def test_crypto_five_trading_days_from_a_saturday():
    """Crypto's 5 trading days from Sat 2026-11-07 land Thu 2026-11-12."""
    assert settlement_date("crypto", "2026-11-07", 5) == date(2026, 11, 12)
