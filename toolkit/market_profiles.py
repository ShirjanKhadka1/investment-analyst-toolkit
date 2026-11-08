"""Market profiles: one sourced config per market.

A profile is the single place where a market's trading rules live:
weekdays, session hours, price limits, lot size, settlement lag, and
the fee/tax schedule. Every sourced number carries its source URL and
an as-of date in ``sources``; a number nobody has sourced is ``None``
(UNKNOWN), never a guess. This mirrors ``nepal_frictions.py`` — the
NEPSE profile IS the sourced Nepal schedule, imported, not copied.

Status labels: ``tested`` means the profile's numbers have been
exercised by a test or a live run in this repo; ``experimental``
means the numbers are sourced but the profile has not yet driven a
simulation end to end. NEPSE is tested; every other market is
experimental until Step 4 evidence lands.

``lot_size`` of 1 on the equity markets means the venue accepts
single-share orders (odd lots); a round lot of 100 shares is a
quoting convention, not a minimum.

NEPSE rules are effective-dated (verified 8 Nov 2026; see the
profile's ``*_rules`` fields and ``sources``):
- Trading week Sun-Thu until 2026-04-05, Mon-Fri from 2026-04-06
  (NEPSE board decision 22 Chaitra 2082). Hours 11:00-15:00 unchanged.
- Daily price limit 10% before 2026-04-20, 15% from (Securities
  Trading Operations (Fourth Amendment) Bylaws, 2082, SEBON-approved);
  pre-open band 2% -> 5% on the same date.
- Index circuit breaker: three-tier (4% -> 20-min halt, 5% -> 40-min
  halt, 6% -> rest of day) before 2026-04-20, two-tier (5% -> 15-min
  halt, 8% -> suspend rest of day) from. The first-tier window is
  reported as "first two hours" by most sources and "first hour" by
  one; the bylaws TEXT itself was not obtained, so the window is
  flagged as disputed in the profile.
- Settlement T+2 (transition date unverified): the CDSC Settlement
  Procedure (official) states T+2 as the standard, and a CDSC
  instruction of Asadh 21, 2074 (Jul 2017) put T+2 payment
  settlement in place — payment vs securities may be different
  legs, so no pre-2017 lag is published and no full-cycle transition
  date is published. The dated rule treats T+2 as in force from
  that instruction (2017-07-05); the value before it is UNKNOWN
  (None), not T+3: the T+3-before-Jan-2021 claim was withdrawn
  2026-11-08 for lack of an official source.
Freshness, the Time Machine and the risk gate must use the rule in
force on EACH BAR'S DATE (``*_on(day)``), never today's rule.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from .nepal_frictions import (
    BROKERAGE_MINIMUM_FLAT,
    BROKERAGE_SLABS,
    CGT_RATE_LONG,
    CGT_RATE_SHORT,
    CIRCUIT_LIMIT,
    DP_CHARGE_FLAT,
    SEBON_FEE_RATE,
)


def _parse_day(value):
    """A ``datetime.date`` or an ISO ``YYYY-MM-DD`` string -> date."""
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))


def _rule_on(rules, day):
    """Value of the dated rule in force on ``day``.

    ``rules`` is a tuple of ``(effective_from, value)`` pairs in
    ascending order, the first effective from ``datetime.date.min``
    (the rule before any change). Returns the value of the last
    rule whose effective date is on or before ``day``.
    """
    day = _parse_day(day)
    value = rules[0][1]
    for effective_from, candidate in rules:
        if day >= effective_from:
            value = candidate
        else:
            break
    return value

__all__ = [
    "KIND_PROFILES",
    "PROFILES",
    "MarketProfile",
    "UnknownMarketError",
    "default_profile",
    "get_profile",
    "list_profiles",
    "profile_config",
]

_AS_OF = "2026-11-08"


@dataclass(frozen=True)
class MarketProfile:
    """One market's trading rules, all sourced or explicitly UNKNOWN."""

    key: str
    name: str
    currency: str
    # Python weekday numbers, Monday=0 .. Sunday=6.
    trading_weekdays: tuple[int, ...]
    session_open: str  # local time "HH:MM"
    session_close: str  # local time "HH:MM"
    timezone: str
    # Where the holiday calendar lives. Dates are NOT hard-coded:
    # holidays change every year and belong to the exchange.
    holidays_source: str
    # Daily price limits as fractions (0.15 = 15%). None = no fixed
    # daily limit for a single stock (a market-wide breaker is not a
    # per-stock limit and is described in circuit_note instead).
    circuit_limit_up: float | None
    circuit_limit_down: float | None
    circuit_note: str
    # Minimum trade unit in shares. None = not sourced.
    lot_size: int | None
    # Settlement lag in business days. None = not sourced.
    settlement_lag_business_days: int | None
    settlement_note: str
    # Fees: name -> {"value": float | None, "unit": str, "note": str}.
    # A None value is UNKNOWN (broker- or venue-dependent), never zero.
    fees: dict
    # Taxes: same shape as fees.
    taxes: dict
    # field -> (source URL, as-of date). Every sourced number appears
    # here; a field with no entry is unsourced.
    sources: dict = field(default_factory=dict)
    # "tested" | "experimental" (see module docstring).
    status: str = "experimental"
    # Open disputes / caveats about this profile's numbers.
    disputes: tuple[str, ...] = ()
    # Free-form notes (e.g. related rule changes not modelled).
    notes: tuple[str, ...] = ()
    # Effective-dated rules. Each is a tuple of (effective_from,
    # value) pairs, ascending, first from datetime.date.min. When
    # None, the single plain field above is the rule for every date.
    trading_weekdays_rules: tuple | None = None
    circuit_limit_up_rules: tuple | None = None
    circuit_limit_down_rules: tuple | None = None
    preopen_limit: float | None = None
    preopen_limit_rules: tuple | None = None
    index_breaker: tuple | None = None
    index_breaker_rules: tuple | None = None
    settlement_lag_rules: tuple | None = None

    def trading_weekdays_on(self, day):
        """Trading weekdays (Mon=0..Sun=6) in force on ``day``."""
        if self.trading_weekdays_rules is not None:
            return _rule_on(self.trading_weekdays_rules, day)
        return self.trading_weekdays

    def circuit_limit_up_on(self, day):
        """Daily up-limit fraction in force on ``day``."""
        if self.circuit_limit_up_rules is not None:
            return _rule_on(self.circuit_limit_up_rules, day)
        return self.circuit_limit_up

    def circuit_limit_down_on(self, day):
        """Daily down-limit fraction in force on ``day``."""
        if self.circuit_limit_down_rules is not None:
            return _rule_on(self.circuit_limit_down_rules, day)
        return self.circuit_limit_down

    def preopen_limit_on(self, day):
        """Pre-open band fraction in force on ``day`` (None = unknown)."""
        if self.preopen_limit_rules is not None:
            return _rule_on(self.preopen_limit_rules, day)
        return self.preopen_limit

    def index_breaker_on(self, day):
        """Index circuit-breaker tiers in force on ``day``.

        Each tier is ``(trigger_fraction, action)``; None = unknown.
        """
        if self.index_breaker_rules is not None:
            return _rule_on(self.index_breaker_rules, day)
        return self.index_breaker

    def settlement_lag_on(self, day):
        """Settlement lag in business days in force on ``day``."""
        if self.settlement_lag_rules is not None:
            return _rule_on(self.settlement_lag_rules, day)
        return self.settlement_lag_business_days

class UnknownMarketError(ValueError):
    """Raised when a market key has no profile."""


def _src(url, as_of=_AS_OF):
    return (url, as_of)


_NEPSE_SOURCES = {
    "trading_weekdays": _src(
        "https://sharesansar.com/newsdetail/nepal-stock-exchange-opens-monday-to-friday-under-new-government-timings-2026-04-08",
        "2026-04-08"),
    "trading_weekdays_2": _src(
        "https://fiscalnepal.com/2026/04/06/25407", "2026-04-06"),
    "trading_weekdays_3": _src(
        "https://wilson.com.np/nepse-online-trading/", "2026-10-22"),
    "session": _src(
        "https://wilson.com.np/nepse-online-trading/", "2026-10-22"),
    "fees": _src("user-supplied rate-schedule screenshot, 28 Oct 2026"),
    "circuit": _src(
        "https://www.fiscalnepal.com/2026/04/17/25634/nepse-expands-daily-price-movement-limit-to-15-introduces-new-circuit-breaker-rules/",
        "2026-04-17"),
    "circuit_2": _src(
        "https://www.newbusinessage.com/news/48267/nepse-raises-daily-price-band-to-15--revises-circuit-breaker-rules/",
        "2026-04-20"),
    "circuit_3": _src(
        "https://kharchapatra.com/blog/nepse-settlement-t2-circuit-breaker-nepal",
        "2026-11-08"),
    "breaker_old_tiers": _src(
        "https://nepalytix.com/blog/nepse-should-not-close-for-nine-days-every-dashain",
        "2026-11-08"),
    "settlement": _src(
        "https://cdsc.com.np/cmsettlementprocedure", "2026-11-08"),
    "weekday_dispute_mon_fri": _src(
        "https://punji.app/learn/nepse-trading-hours", "2026-09-25"),
}

NEPSE = MarketProfile(
    key="nepse",
    name="NEPSE (Nepal Stock Exchange)",
    currency="NPR",
    trading_weekdays=(0, 1, 2, 3, 4),  # Mon-Fri, current (from 2026-04-06).
    trading_weekdays_rules=(
        (datetime.date.min, (6, 0, 1, 2, 3)),  # Sun-Thu until 2026-04-05.
        (datetime.date(2026, 4, 6), (0, 1, 2, 3, 4)),
    ),
    session_open="11:00",
    session_close="15:00",
    timezone="Asia/Kathmandu",
    holidays_source="https://nepalstock.com.np (NEPSE notices); repo copy: "
                    "data/holidays/nepse.json — Dashain 2083 only, "
                    "coverage incomplete",
    circuit_limit_up=CIRCUIT_LIMIT,
    circuit_limit_down=CIRCUIT_LIMIT,
    circuit_limit_up_rules=(
        (datetime.date.min, 0.10),  # 10% until 2026-04-19.
        (datetime.date(2026, 4, 20), 0.15),
    ),
    circuit_limit_down_rules=(
        (datetime.date.min, 0.10),
        (datetime.date(2026, 4, 20), 0.15),
    ),
    circuit_note="±15% of the previous close in a day from 2026-04-20; "
                 "±10% before (Fourth Amendment Bylaws, 2082, "
                 "SEBON-approved).",
    preopen_limit=0.05,
    preopen_limit_rules=(
        (datetime.date.min, 0.02),  # 2% until 2026-04-19.
        (datetime.date(2026, 4, 20), 0.05),
    ),
    index_breaker=(
        (0.05, "15-minute halt if within first two hours"),
        (0.08, "suspend rest of day"),
    ),
    index_breaker_rules=(
        (datetime.date.min, (
            (0.04, "20-minute halt"),
            (0.05, "40-minute halt after reopening"),
            (0.06, "suspend rest of day"),
        )),
        (datetime.date(2026, 4, 20), (
            (0.05, "15-minute halt if within first two hours"),
            (0.08, "suspend rest of day"),
        )),
    ),
    lot_size=None,  # not in the sourced schedule; do not guess.
    settlement_lag_business_days=2,
    settlement_lag_rules=(
        # T+2 payment settlement in place from the CDSC instruction
        # of Asadh 21, 2074 (2017-07-05); earlier lag is UNKNOWN
        # (None), not T+3: the T+3 claim was withdrawn 2026-11-08 —
        # no official source supports it. Payment vs securities may
        # be different legs, so no full-cycle transition date is
        # published — the boundary below is the payment-instruction
        # date, not a claimed transition date.
        (datetime.date.min, None),
        (datetime.date(2017, 7, 5), 2),
    ),
    settlement_note="T+2 (transition date unverified). CDSC Settlement "
                    "Procedure (official) states T+2 as the standard; a "
                    "CDSC instruction of Asadh 21, 2074 put T+2 payment "
                    "settlement in place — payment vs securities may be "
                    "different legs, so no pre-2017 lag is published. "
                    "Broker 58 offers T+1.",
    fees={
        "brokerage_slabs": {
            "value": BROKERAGE_SLABS,
            "unit": "(upper bound NPR, rate) per slab; see nepal_frictions",
            "note": "0.36% to Rs 50k, 0.33% to Rs 5 lakh, 0.31% to Rs 20 lakh, "
                    "0.27% to Rs 1 crore, 0.24% above; minimum Rs 10.",
        },
        "brokerage_minimum_flat": {"value": BROKERAGE_MINIMUM_FLAT,
                                   "unit": "NPR", "note": "minimum per trade."},
        "sebon_fee_rate": {"value": SEBON_FEE_RATE, "unit": "fraction",
                           "note": "0.015% of trade value."},
        "dp_charge_flat": {"value": DP_CHARGE_FLAT, "unit": "NPR per scrip",
                           "note": "Rs 25 per scrip."},
    },
    taxes={
        "cgt_short": {"value": CGT_RATE_SHORT, "unit": "fraction",
                      "note": "5% on gains, held 365 days or less."},
        "cgt_long": {"value": CGT_RATE_LONG, "unit": "fraction",
                     "note": "3.75% on gains, held more than 365 days."},
    },
    sources=_NEPSE_SOURCES,
    status="tested",
    notes=(
        ("Same amendment (Fourth Amendment Bylaws, 2082, from 2026-04-20) "
         "also widened the order-placement range from 2% to 3% (Merolagani "
         "infographic of the amendment, 2026-04-20); not modelled — the "
         "exact base the 3% applies to is not stated in the reports seen."),
    ),
    disputes=(
        (
            "Index breaker, first-tier window: most reports say the 5% "
            "halt applies 'within the first two hours' (fiscalnepal, "
            "newbusinessage, kharchapatra, wilson); one report says "
            "'first hour'. The bylaws TEXT itself was not obtained, so "
            "the two-tier numbers are from corroborated reports, not "
            "from the instrument. Do not state the window as settled."
        ),
        (
            "Settlement transition: the pre-2021 lag was previously "
            "published as T+3 (biznessvue citing ShareSansar); no "
            "official source supports it, so the claim was withdrawn "
            "2026-11-08 and the pre-2017 lag is recorded as unknown, "
            "not T+3. The rule treats T+2 as in force from the CDSC "
            "instruction of Asadh 21, 2074 / Jul 2017, which required "
            "payment within T+2; it is the payment leg, not "
            "necessarily the full-cycle date, so no full-cycle "
            "transition date is published."
        ),
    ),
)

US = MarketProfile(
    key="us",
    name="US equities (NYSE / Nasdaq)",
    currency="USD",
    trading_weekdays=(0, 1, 2, 3, 4),  # Mon-Fri
    session_open="09:30",
    session_close="16:00",
    timezone="America/New_York",
    holidays_source="https://www.nyse.com/markets/nyse/trading-info (NYSE calendar)",
    circuit_limit_up=None,
    circuit_limit_down=None,
    circuit_note="No fixed daily limit per stock. Market-wide breakers on the "
                 "S&P 500: -7% (Level 1) and -13% (Level 2) halt all US stock "
                 "trading 15 min if before 3:25 p.m. ET; -20% (Level 3) ends "
                 "trading for the day. Single stocks: Limit Up-Limit Down "
                 "bands (Tier 1: 5% around the 5-min reference price).",
    lot_size=1,
    settlement_lag_business_days=1,
    settlement_note="T+1 since 28 May 2024 (SEC Rule 15c6-1).",
    fees={
        "brokerage": {"value": None, "unit": "broker-dependent",
                      "note": "No exchange brokerage schedule; the broker sets it."},
        "sec_section_31_fee": {
            "value": None, "unit": "rate set by the SEC, revised periodically",
            "note": "Charged on sales; verify the current rate at sec.gov "
                    "before modelling it — do not hard-code a stale rate."},
    },
    taxes={
        "capital_gains": {"value": None, "unit": "jurisdiction-dependent",
                          "note": "US federal + state rates depend on the "
                                  "holder; not modelled."},
    },
    sources={
        "session": _src("https://www.sonicwallet.com/learn/stocks/stock-market-hours-and-holidays",
                        "2026-11-02"),
        "circuit": _src("https://www.sonicwallet.com/learn/stocks/stock-market-hours-and-holidays",
                        "2026-11-02"),
        "settlement": _src("https://www.stockeducation.com/research-data/t-1-settlement/",
                           "2026-10-17"),
        "lot_size": _src("https://www.sonicwallet.com/learn/stocks/stock-market-hours-and-holidays",
                         "2026-11-02"),
    },
)

INDIA = MarketProfile(
    key="india",
    name="India equities (NSE / BSE)",
    currency="INR",
    trading_weekdays=(0, 1, 2, 3, 4),  # Mon-Fri
    session_open="09:15",
    session_close="15:30",
    timezone="Asia/Kolkata",
    holidays_source="https://www.nseindia.com (NSE holiday calendar; circulars)",
    circuit_limit_up=0.20,
    circuit_limit_down=0.20,
    circuit_note="Per-stock daily price bands of 2%, 5%, 10% or 20% "
                 "depending on the scrip. Index-wide breakers on NIFTY / "
                 "SENSEX at 10%, 15%, 20%. The 20% band is the widest "
                 "per-stock band; a scrip's own band may be tighter.",
    lot_size=1,
    settlement_lag_business_days=1,
    settlement_note="T+1 standard for equity delivery (T+0 optional for "
                    "eligible securities).",
    fees={
        "stt_delivery": {"value": 0.001, "unit": "fraction of trade value",
                         "note": "Securities Transaction Tax 0.1% on buy AND sell (delivery)."},
        "stt_intraday_sell": {"value": 0.00025, "unit": "fraction of trade value",
                              "note": "STT 0.025% on the sell side only (intraday)."},
        "exchange_txn_charge": {"value": 0.00003, "unit": "fraction (approx.)",
                                "note": "NSE transaction charge, about 0.003%; "
                                        "exchange-set and revised periodically."},
        "brokerage": {"value": None, "unit": "broker-dependent",
                      "note": "No exchange brokerage schedule; the broker sets it."},
    },
    taxes={
        "stcg_listed_equity": {"value": 0.20, "unit": "fraction",
                               "note": "Short-term capital gains on listed equity "
                                       "held up to 12 months (STT conditions met)."},
    },
    sources={
        "session": _src("https://www.t7wealth.com/stock-exchanges", "2026-10-13"),
        "circuit": _src("https://www.t7wealth.com/stock-exchanges", "2026-10-13"),
        "settlement": _src("https://www.angelone.in/knowledge-center/online-share-trading/what-is-equity-trading",
                           "2026-10-19"),
        "stt": _src("https://www.thebeststockbroker.com/intraday-trading/", "2026-11-08"),
        "stcg": _src("https://www.angelone.in/knowledge-center/online-share-trading/what-is-equity-trading",
                     "2026-10-19"),
    },
)

UK = MarketProfile(
    key="uk",
    name="UK equities (London Stock Exchange)",
    currency="GBP",
    trading_weekdays=(0, 1, 2, 3, 4),  # Mon-Fri
    session_open="08:00",
    session_close="16:30",
    timezone="Europe/London",
    holidays_source="https://www.londonstockexchange.com (LSE calendar)",
    circuit_limit_up=None,
    circuit_limit_down=None,
    circuit_note="No fixed daily limit per stock. Volatility auctions pause "
                 "a stock when it moves sharply; the auction, not a fixed "
                 "band, is the brake.",
    lot_size=1,
    settlement_lag_business_days=2,
    settlement_note="T+2 today; T+1 confirmed for 11 October 2027 (UK cash equities).",
    fees={
        "sdrt_buy": {"value": 0.005, "unit": "fraction of purchase price",
                     "note": "Stamp Duty Reserve Tax 0.5% on electronic Main Market "
                             "purchases (buy side only). AIM shares exempt since 2014; "
                             "most ETFs exempt."},
        "brokerage": {"value": None, "unit": "broker-dependent",
                      "note": "No exchange brokerage schedule; the broker sets it."},
    },
    taxes={
        "capital_gains": {"value": None, "unit": "jurisdiction-dependent",
                          "note": "UK CGT rates/allowances depend on the holder; "
                                  "not modelled."},
    },
    sources={
        "session": _src("https://www.tiingo.com/blog/london-stock-exchange/", "2026-07-01"),
        "settlement": _src("https://www.tiingo.com/blog/london-stock-exchange/", "2026-07-01"),
        "sdrt": _src("https://www.gov.uk/hmrc-internal-manuals/stamp-taxes-shares-manual/stsm131090",
                     "2026-11-08"),
    },
)

CRYPTO = MarketProfile(
    key="crypto",
    name="Crypto spot (venue-agnostic)",
    currency="USD",
    trading_weekdays=(0, 1, 2, 3, 4, 5, 6),  # every day
    session_open="00:00",
    session_close="23:59",
    timezone="UTC",
    holidays_source="none — spot crypto has no holidays; individual venues "
                    "may schedule maintenance windows.",
    circuit_limit_up=None,
    circuit_limit_down=None,
    circuit_note="No price limits and no market-wide breaker on spot crypto. "
                 "Individual venues may halt a single asset.",
    lot_size=None,  # fractional; venue minimums vary.
    settlement_lag_business_days=0,
    settlement_note="Spot settlement is immediate on-venue; on-chain "
                    "settlement depends on the chain, not modelled.",
    fees={
        "venue_fee": {"value": None, "unit": "venue-dependent",
                      "note": "Maker/taker fees differ by venue and tier; no "
                              "single schedule exists."},
    },
    taxes={
        "capital_gains": {"value": None, "unit": "jurisdiction-dependent",
                          "note": "Depends on the holder's jurisdiction; not modelled."},
    },
    sources={
        "session": _src("https://www.trade500.com/compare/crypto-vs-forex/", "2026-11-08"),
        "circuit": _src("https://globalmarkets24hours.com/", "2026-11-08"),
    },
)

FX = MarketProfile(
    key="fx",
    name="Spot FX (OTC, venue-agnostic)",
    currency="USD",  # quote currency of the majors; a pair's quote varies.
    trading_weekdays=(0, 1, 2, 3, 4),  # Mon-Fri (opens Sunday evening).
    # A 24/5 market has no daily open/close: it opens Sunday 17:00 ET
    # and closes Friday 17:00 ET. The fields below carry those two
    # times; the weekday list is what the calendar uses.
    session_open="17:00",
    session_close="17:00",
    timezone="America/New_York",
    holidays_source="none — spot FX has no exchange holiday calendar; "
                    "liquidity thins on major holidays.",
    circuit_limit_up=None,
    circuit_limit_down=None,
    circuit_note="No price limits on spot FX.",
    lot_size=None,  # lot sizes are venue-defined (standard/mini/micro).
    settlement_lag_business_days=None,
    settlement_note="Spot FX settlement varies by pair (commonly T+2, T+1 "
                    "for some pairs) — not modelled; verify per pair.",
    fees={
        "spread": {"value": None, "unit": "venue-dependent",
                   "note": "Cost is the bid/ask spread, set by the venue; "
                           "no commission schedule exists."},
    },
    taxes={
        "capital_gains": {"value": None, "unit": "jurisdiction-dependent",
                          "note": "Depends on the holder's jurisdiction; not modelled."},
    },
    sources={
        "session": _src("https://www.monerohub.io/blog/crypto-trading-vs-forex-trading-hours-leverage-and-liquidity/",
                        "2026-11-08"),
    },
)

GENERIC = MarketProfile(
    key="generic",
    name="Generic (user-set frictions)",
    currency="USD",
    # Every day: a generic profile has no calendar, so age is counted
    # in calendar days. The caller overrides this for a real market.
    trading_weekdays=(0, 1, 2, 3, 4, 5, 6),
    session_open="09:30",
    session_close="16:00",
    timezone="UTC",
    holidays_source="none — the caller supplies the calendar.",
    circuit_limit_up=None,
    circuit_limit_down=None,
    circuit_note="No limits unless the caller sets them.",
    lot_size=None,
    settlement_lag_business_days=None,
    settlement_note="Not set — the caller supplies it.",
    fees={},
    taxes={},
    sources={},
)


PROFILES = {p.key: p for p in (NEPSE, US, INDIA, UK, CRYPTO, FX, GENERIC)}

# Public track-record watchlist (docs/watchlist-proposal.md): each
# ticker maps to the profile whose rules judge it. The mapping is
# the test's source of truth — a ticker with no entry here, or a
# profile key that does not exist, fails tests/test_watchlist.py,
# and the doc table must match this dict exactly. VT is US-listed
# (NYSE Arca) so the `us` profile judges it even though its
# holdings are global — the profile is the venue's rules, not the
# holdings' geography.
WATCHLIST_PROFILES = {
    "SPY": "us",
    "AAPL": "us",
    "VT": "us",
    "NIFTYBEES.NS": "india",
    "ISF.L": "uk",
    "EURUSD=X": "fx",
    "BTC-USD": "crypto",
}

# Connector freshness kinds (toolkit/connectors.py) and the profile
# whose trading week judges them. Values here must match the existing
# TRADING_WEEKDAYS behaviour exactly.
KIND_PROFILES = {
    "stock_daily": "us",
    "crypto_daily": "crypto",
    "fx_reference": "fx",
    "macro_annual": "generic",
    "nepse_csv_daily": "nepse",
}


def get_profile(key):
    """Return the profile for ``key`` or raise UnknownMarketError."""
    try:
        return PROFILES[key]
    except KeyError:
        raise UnknownMarketError(
            f"unknown market {key!r} — known: {', '.join(sorted(PROFILES))}"
        ) from None


def list_profiles():
    """All profiles, keyed by market key."""
    return dict(PROFILES)


def default_profile():
    """The default market is NEPSE — existing behaviour is unchanged."""
    return NEPSE


def profile_config(profile, base=None, on=None):
    """Derive a risk-gate config from a profile.

    The gate's 16 checks never change; only the config values they
    read can come from a profile. Amounts stay in the profile's
    currency (the gate's ``*_usd`` field names are historical — the
    values are whatever currency the config is in). NEPSE derives
    exactly DEFAULT_RESEARCH_CONFIG, so the default path is
    byte-identical.

    ``on`` is a date (or ISO string) the config applies to: when
    given, the config also carries the trading rules in force on
    that date — trading weekdays, circuit limits, pre-open band,
    index-breaker tiers and settlement lag — so a plan dated before
    a rule change is judged by the old rule, not today's. When
    ``on`` is None, only currency/market are set (unchanged).
    """
    from .position_plan import DEFAULT_RESEARCH_CONFIG

    if isinstance(profile, str):
        profile = get_profile(profile)
    cfg = dict(DEFAULT_RESEARCH_CONFIG if base is None else base)
    cfg["currency"] = profile.currency
    cfg["market"] = profile.key
    if on is not None:
        cfg["trading_weekdays"] = profile.trading_weekdays_on(on)
        cfg["circuit_limit_up"] = profile.circuit_limit_up_on(on)
        cfg["circuit_limit_down"] = profile.circuit_limit_down_on(on)
        cfg["preopen_limit"] = profile.preopen_limit_on(on)
        cfg["index_breaker"] = profile.index_breaker_on(on)
        cfg["settlement_lag_business_days"] = profile.settlement_lag_on(on)
    return cfg
