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

NEPSE disputes recorded here (not silently resolved):
- Trading week: this repo has used Sun-Thu since the programme began
  (user-supplied schedule, 28 Oct 2026). Multiple sources say NEPSE
  moved to Mon-Fri in April 2026 (board decision 2082 Chaitra 23,
  after Sunday became a public holiday). The profile keeps Sun-Thu
  because existing tests pin that behaviour byte-identical; the
  dispute is flagged and the Mon-Fri evidence is cited. Verify
  against NEPSE's own calendar before changing it — it changes
  freshness judgements.
- Settlement: the user-supplied schedule says T+2; at least one guide
  says T+3. The profile keeps T+2 (the sourced schedule) and flags
  the dispute.
"""

from __future__ import annotations

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

__all__ = [
    "KIND_PROFILES",
    "MarketProfile",
    "PROFILES",
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


class UnknownMarketError(ValueError):
    """Raised when a market key has no profile."""


def _src(url, as_of=_AS_OF):
    return (url, as_of)


_NEPSE_SOURCES = {
    "trading_weekdays": _src(
        "https://wilson.com.np/nepse-online-trading/", "2026-10-22"),
    "session": _src(
        "https://wilson.com.np/nepse-online-trading/", "2026-10-22"),
    "fees": _src("user-supplied rate-schedule screenshot, 28 Oct 2026"),
    "circuit": _src("user-supplied rate-schedule screenshot, 28 Oct 2026"),
    "settlement": _src("user-supplied rate-schedule screenshot, 28 Oct 2026"),
    "weekday_dispute_mon_fri": _src(
        "https://punji.app/learn/nepse-trading-hours", "2026-09-25"),
    "weekday_dispute_mon_fri_2": _src(
        "https://markethours.io/market-holidays/nepse", "2026-11-08"),
    "settlement_dispute_t3": _src(
        "https://www.arthneeti.org/nepse-investing-guide.pdf", "2026-11-08"),
}

NEPSE = MarketProfile(
    key="nepse",
    name="NEPSE (Nepal Stock Exchange)",
    currency="NPR",
    trading_weekdays=(6, 0, 1, 2, 3),  # Sun-Thu; DISPUTED, see below.
    session_open="11:00",
    session_close="15:00",
    timezone="Asia/Kathmandu",
    holidays_source="https://nepalstock.com.np (NEPSE notices; not hard-coded)",
    circuit_limit_up=CIRCUIT_LIMIT,
    circuit_limit_down=CIRCUIT_LIMIT,
    circuit_note="±15% of the previous close in a day (user-supplied, 28 Oct 2026).",
    lot_size=None,  # not in the sourced schedule; do not guess.
    settlement_lag_business_days=2,
    settlement_note="T+2 as standard (user-supplied, 28 Oct 2026); broker 58 offers T+1.",
    fees={
        "brokerage_slabs": {
            "value": None,
            "unit": "slabs (see nepal_frictions.BROKERAGE_SLABS)",
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
    disputes=(
        "Trading week: Sun-Thu here (user-supplied schedule, 28 Oct 2026), "
        "but several sources say NEPSE moved to Mon-Fri in April 2026 "
        "(board decision 2082 Chaitra 23; punji.app 2026-09-25, "
        "markethours.io 2026-11-08). Verify against NEPSE's own calendar "
        "before changing — it changes freshness judgements.",
        "Settlement: T+2 here (user-supplied schedule); at least one guide "
        "says T+3 (arthneeti.org NEPSE guide). Verify before relying on it.",
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


def profile_config(profile, base=None):
    """Derive a risk-gate config from a profile.

    The gate's 16 checks never change; only the config values they
    read can come from a profile. Amounts stay in the profile's
    currency (the gate's ``*_usd`` field names are historical — the
    values are whatever currency the config is in). NEPSE derives
    exactly DEFAULT_RESEARCH_CONFIG, so the default path is
    byte-identical.
    """
    from .position_plan import DEFAULT_RESEARCH_CONFIG

    if isinstance(profile, str):
        profile = get_profile(profile)
    cfg = dict(DEFAULT_RESEARCH_CONFIG if base is None else base)
    cfg["currency"] = profile.currency
    cfg["market"] = profile.key
    return cfg
