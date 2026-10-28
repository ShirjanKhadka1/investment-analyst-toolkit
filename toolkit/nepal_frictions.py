"""Nepal (NEPSE) trading frictions — parameters only, no invented rates.

RESEARCH ONLY. This module is not buy/sell/hold advice, promises no
return, and uses no live NEPSE feed. Exchange stated wherever relevant:
all parameters below are for trades on NEPSE (Nepal Stock Exchange),
not for any other exchange sharing a ticker or a name.

SOURCING — READ BEFORE USING ANY NUMBER FROM THIS MODULE:
Every rate defaults to None, meaning UNKNOWN. That is deliberate, and
it is the honest result of the source check done for Phase 4 slice 1
(2026-10-28): the local programme files were checked first, as required
— ~/workspace/github-repos/nepali-capital-market-research/guides/
market-structure.md names the participants (SEBON as regulator, NEPSE
as trading platform, brokers as access point, clearing/settlement
institutions, depository/registrar services) but states NO brokerage
rate, NO SEBON fee, NO DP charge, NO capital-gains-tax rate, NO
circuit-limit percentage, and NO settlement lag. Its companion guide
(nepal-data-handling.md) likewise states no fee schedule and warns
that current terms must be checked against the current NEPSE/SEBON
notice for the period in question. No rate from those files exists to
copy, so no rate is asserted here.

To use a real rate, a caller must construct NepalFrictions with that
rate AND a source AND an as_of date (enforced in __post_init__):
a supplied rate without a citation is rejected, not stored. Until
then, every cost function below either raises UnknownFrictionError
or returns a status="UNKNOWN" dict with total_cost=None — it NEVER
silently assumes zero, because zero fees would flatter a backtest.

SOURCED SCHEDULE (Phase 4 patch, 28 Oct 2026 — replaces UNKNOWN for
the rates it actually states; see sourced_nepal_frictions()):
  Broker commission (buy & sell, by trade value, NEPSE):
    up to Rs 50,000: 0.36% (minimum Rs 10); Rs 50,001–5 lakh: 0.33%;
    Rs 5 lakh–20 lakh: 0.31%; Rs 20 lakh–1 crore: 0.27%;
    above Rs 1 crore: 0.24%.
  SEBON transaction fee: 0.015%, buy & sell.
  DP charge (CDSC): Rs 25 per scrip, buy & sell.
  Capital gains tax (sell only, resident individuals, listed
  shares): held 365 days or less: 5% of net gain; held more than
  365 days: 3.75% of net gain. The gain taxed is the gain AFTER
  fees: sell proceeds after charges minus the fee-inclusive cost
  of buying. CGT halving source: Ministry of Finance notice in
  Nepal Gazette vol. 76, Additional 40, Part 5, dated 31 Bhadra
  2083 (16 September 2026), replacing Finance Act 2083 rates of
  10% / 7.5%. Dividends are taxed separately at a flat 5% — NOTE
  ONLY: this module has no dividend handling and does not
  implement dividend tax.
  Circuit limits: +15% / -15% in a day, user-supplied, current
  as of 28 Oct 2026. NOTE: circuit limits can change — re-check
  against a current NEPSE/SEBON notice before relying on them
  for a real trade.
  Settlement lag: T+2 days as standard (user-supplied, 28 Oct
  2026), with a broker-specific override to T+1 where a broker
  offers it — the example supplied is broker number 58
  (user-supplied example, as of 28 Oct 2026; no broker name
  beyond "broker 58" is asserted). The T+1 override must be
  confirmed with the specific broker before use, and T+1 is
  NOT the market standard.
Source citation for the schedule: user-supplied rate-schedule
screenshot, 28 Oct 2026 (the page name/URL the screenshot came
from is NOT established, so it is cited only as the screenshot),
plus the Gazette notice above for CGT. No rate beyond those
listed here has been added.

DOWNSIDE: frictions are a certain drag — a trade that is profitable
before costs can be a net loss after brokerage, SEBON fee, DP charge
and tax, and the drag is proportionally worst on small trades (a flat
DP charge) and on frequent trading (costs paid on every round trip).
FALSIFIER: if a broker contract note / broker statement / current
SEBON or NEPSE notice shows a different fee breakdown from the rates
a caller supplied, the statement/notice wins and the supplied inputs
must be corrected — this module's arithmetic never overrides a
primary document. Past fee schedules do not prove current fees.

Cost arithmetic (when — and only when — all used rates are known):
  brokerage_cost = trade_value * brokerage_rate
  sebon_fee      = trade_value * sebon_fee_rate
  dp_charge      = dp_charge_flat (flat, per transaction)
  capital_gains_tax = realised_gain * capital_gains_tax_rate
                      (sell side only, gain > 0 only; a loss pays
                      no gains tax in this arithmetic — if the tax
                      rules for the period say otherwise, the caller
                      must model that explicitly, not assume it)
  total_friction_cost = brokerage_cost + sebon_fee + dp_charge
                        + capital_gains_tax
  net_cash (buy)  = -(trade_value + total_friction_cost)
  net_cash (sell) = trade_value - total_friction_cost
The formula structure above is plain arithmetic on caller-supplied
inputs; it is NOT a claim about what any rate is.
"""
from __future__ import annotations

from dataclasses import dataclass

from .checks import require_finite, require_non_negative


class UnknownFrictionError(ValueError):
    """Raised when a friction calculation needs a rate that is UNKNOWN."""


_RATE_FIELDS = ("brokerage_rate", "sebon_fee_rate", "capital_gains_tax_rate",
                "circuit_limit_up", "circuit_limit_down",
                "cgt_rate_short", "cgt_rate_long")
_UNKNOWN_SOURCE = "UNKNOWN — not verified in local programme files as of 2026-10-28"

# Sourced NEPSE broker-commission slabs (Phase 4 patch, 28 Oct 2026):
# (trade-value upper bound inclusive in NPR, rate as a fraction).
# The last slab has no upper bound. Minimum commission Rs 10 applies
# on top of the slab rate (see brokerage_cost_for).
BROKERAGE_SLABS = (
    (50000.0, 0.0036),
    (500000.0, 0.0033),
    (2000000.0, 0.0031),
    (10000000.0, 0.0027),
    (float("inf"), 0.0024),
)
BROKERAGE_MINIMUM_FLAT = 10.0
SEBON_FEE_RATE = 0.00015
DP_CHARGE_FLAT = 25.0
CGT_RATE_SHORT = 0.05    # held 365 days or less
CGT_RATE_LONG = 0.0375   # held more than 365 days
CIRCUIT_LIMIT = 0.15     # +15% / -15% in a day, user-supplied 28 Oct 2026
_SOURCED_SOURCE = (
    "User-supplied NEPSE rate-schedule screenshot, 28 Oct 2026 "
    "(broker commission slabs, SEBON fee, DP charge, CGT rates); "
    "CGT halving per Ministry of Finance notice, Nepal Gazette "
    "vol. 76, Additional 40, Part 5, 31 Bhadra 2083 (16 Sep 2026), "
    "replacing Finance Act 2083 rates of 10% / 7.5%; circuit limits "
    "user-supplied, current as of 28 Oct 2026 — re-check against a "
    "current NEPSE/SEBON notice before relying on them for a real trade")


@dataclass
class NepalFrictions:
    """NEPSE trading-friction parameters. All rates default to UNKNOWN (None).

    Fields (all Optional; None = UNKNOWN, never zero):
      brokerage_rate: fraction of trade value charged by the broker.
      sebon_fee_rate: fraction of trade value charged as SEBON fee.
      dp_charge_flat: flat DP (depository participant) charge per
        transaction, in the trade currency (NPR for NEPSE trades).
      capital_gains_tax_rate: fraction of realised gain charged as
        capital gains tax (sell side, gain > 0).
      circuit_limit_up / circuit_limit_down: fractions bounding the
        allowed price move from the previous close (NEPSE circuit
        limits). Both must be known before any limit check.
      settlement_lag_business_days: settlement lag in business days
        (the "T+N" N in a T+N cycle). UNKNOWN until verified against
        a current NEPSE/SEBON notice — the task brief names T+2 as
        the parameter to model, but the local guides do not state
        any lag, so this module does NOT assert 2.
      source: where each supplied rate came from (e.g. a SEBON/NEPSE
        notice or broker schedule, with document name).
      as_of: period/date the supplied rates apply to (e.g. "2026-10").
    """

    brokerage_rate: float | None = None
    sebon_fee_rate: float | None = None
    dp_charge_flat: float | None = None
    capital_gains_tax_rate: float | None = None
    circuit_limit_up: float | None = None
    circuit_limit_down: float | None = None
    settlement_lag_business_days: int | None = None
    # Sourced-schedule forms (Phase 4 patch): a slab schedule
    # replaces the single brokerage_rate, and a holding-period
    # CGT split replaces the single capital_gains_tax_rate. A
    # parameter counts as known if EITHER form is supplied.
    brokerage_slabs: tuple | None = None
    brokerage_minimum_flat: float | None = None
    cgt_rate_short: float | None = None
    cgt_rate_long: float | None = None
    # Broker-specific settlement note, e.g. "broker 58" for a
    # confirmed T+1 override. A note alone never sets the lag.
    settlement_broker_note: str | None = None
    source: str = _UNKNOWN_SOURCE
    as_of: str | None = None

    def __post_init__(self):
        for name in _RATE_FIELDS:
            value = getattr(self, name)
            if value is not None:
                require_finite(name, value)
                if not 0.0 <= value < 1.0:
                    raise ValueError(f"{name} must be in [0, 1), got {value}")
        if self.dp_charge_flat is not None:
            require_non_negative("dp_charge_flat", self.dp_charge_flat)
        if self.brokerage_minimum_flat is not None:
            require_non_negative("brokerage_minimum_flat",
                                 self.brokerage_minimum_flat)
        if self.brokerage_slabs is not None:
            prev = None
            for bound, rate in self.brokerage_slabs:
                require_finite("brokerage slab rate", rate)
                if not 0.0 <= rate < 1.0:
                    raise ValueError(
                        f"brokerage slab rate must be in [0, 1), got {rate}")
                if prev is not None and not bound > prev:
                    raise ValueError(
                        "brokerage slab upper bounds must be strictly "
                        f"increasing, got {self.brokerage_slabs!r}")
                prev = bound
        if self.settlement_lag_business_days is not None:
            lag = self.settlement_lag_business_days
            if isinstance(lag, bool) or not isinstance(lag, int) or lag < 0:
                raise ValueError(
                    f"settlement_lag_business_days must be a non-negative int, got {lag!r}")
        supplied = [n for n in (
            "brokerage_rate", "sebon_fee_rate", "dp_charge_flat",
            "capital_gains_tax_rate", "circuit_limit_up",
            "circuit_limit_down", "settlement_lag_business_days",
            "brokerage_slabs", "brokerage_minimum_flat",
            "cgt_rate_short", "cgt_rate_long")
            if getattr(self, n) is not None]
        if supplied and (not self.source or self.source == _UNKNOWN_SOURCE
                         or self.as_of is None or not str(self.as_of).strip()):
            raise ValueError(
                "supplied friction rate(s) "
                f"{supplied} require a source and an as_of date; "
                "unsourced rates are rejected, not stored")

    def brokerage_known(self):
        """True when a single rate OR a slab schedule is supplied."""
        return self.brokerage_rate is not None or self.brokerage_slabs is not None

    def cgt_known(self):
        """True when a single CGT rate OR both split rates are supplied."""
        return (self.capital_gains_tax_rate is not None
                or (self.cgt_rate_short is not None
                    and self.cgt_rate_long is not None))

    def unknown_fields(self):
        """Names of friction parameters still UNKNOWN (None).

        The seven base names are kept for backward compatibility:
        brokerage counts as known if a slab schedule is supplied,
        and CGT counts as known if both split rates are supplied.
        """
        unknown = []
        if not self.brokerage_known():
            unknown.append("brokerage_rate")
        if self.sebon_fee_rate is None:
            unknown.append("sebon_fee_rate")
        if self.dp_charge_flat is None:
            unknown.append("dp_charge_flat")
        if not self.cgt_known():
            unknown.append("capital_gains_tax_rate")
        if self.circuit_limit_up is None:
            unknown.append("circuit_limit_up")
        if self.circuit_limit_down is None:
            unknown.append("circuit_limit_down")
        if self.settlement_lag_business_days is None:
            unknown.append("settlement_lag_business_days")
        return unknown

    def is_cost_known(self):
        """True only when brokerage, SEBON fee and DP charge are all known."""
        return (self.brokerage_known()
                and self.sebon_fee_rate is not None
                and self.dp_charge_flat is not None)


def default_unknown_frictions():
    """The default: every rate UNKNOWN. Use this until real, cited
    rates are supplied — never replace it with a remembered rate."""
    return NepalFrictions()


def sourced_nepal_frictions(settlement_lag_business_days=2,
                            broker_override=None):
    """The sourced NEPSE schedule (Phase 4 patch, 28 Oct 2026).

    Broker slabs, SEBON fee, DP charge and CGT split from the
    user-supplied rate-schedule screenshot of 28 Oct 2026; CGT
    halving per the Nepal Gazette notice cited in the module
    docstring; circuit limits +15%/-15% user-supplied, current
    as of 28 Oct 2026 (re-check against a current NEPSE/SEBON
    notice before relying on them for a real trade). Settlement
    is T+2 days as standard (user-supplied, 28 Oct 2026). A
    broker-specific T+1 override is available ONLY by passing
    settlement_lag_business_days=1 together with broker_override
    naming the broker (e.g. "broker 58", the user-supplied
    example) — the override must be confirmed with that broker
    before use, and T+1 is NOT the market standard. No broker
    name beyond what the caller supplies is asserted here.
    Dividends taxed separately at flat 5%: NOTE ONLY, not
    implemented (this module has no dividend handling).
    """
    if settlement_lag_business_days == 1 and not broker_override:
        raise ValueError(
            "a T+1 settlement override requires broker_override "
            "naming the broker (e.g. 'broker 58') and must be "
            "confirmed with that broker before use; T+1 is not "
            "the market standard")
    source = _SOURCED_SOURCE
    if settlement_lag_business_days == 1:
        source += (f" Settlement override: T+1 via {broker_override}, "
                   "user-supplied example as of 28 Oct 2026, to be "
                   "confirmed with that broker before use.")
    return NepalFrictions(
        brokerage_slabs=BROKERAGE_SLABS,
        brokerage_minimum_flat=BROKERAGE_MINIMUM_FLAT,
        sebon_fee_rate=SEBON_FEE_RATE,
        dp_charge_flat=DP_CHARGE_FLAT,
        cgt_rate_short=CGT_RATE_SHORT,
        cgt_rate_long=CGT_RATE_LONG,
        circuit_limit_up=CIRCUIT_LIMIT,
        circuit_limit_down=CIRCUIT_LIMIT,
        settlement_lag_business_days=settlement_lag_business_days,
        settlement_broker_note=broker_override,
        source=source,
        as_of="2026-10-28")


def brokerage_rate_for(trade_value, frictions):
    """The brokerage rate for one trade value: the single rate if
    set, else the slab whose upper bound first covers the value.
    Raises UnknownFrictionError when no brokerage form is known."""
    require_finite("trade_value", trade_value)
    if trade_value <= 0:
        raise ValueError(f"trade_value must be positive, got {trade_value}")
    if frictions.brokerage_rate is not None:
        return frictions.brokerage_rate
    if frictions.brokerage_slabs is not None:
        for bound, rate in frictions.brokerage_slabs:
            if trade_value <= bound:
                return rate
        return frictions.brokerage_slabs[-1][1]
    raise UnknownFrictionError("brokerage rate is UNKNOWN")


def brokerage_cost_for(trade_value, frictions):
    """Slab rate times value, subject to the minimum flat charge
    when one is set (Rs 10 in the sourced schedule)."""
    rate = brokerage_rate_for(trade_value, frictions)
    cost = trade_value * rate
    if frictions.brokerage_minimum_flat is not None:
        cost = max(cost, frictions.brokerage_minimum_flat)
    return cost


def cgt_rate_for(holding_days, frictions):
    """The CGT rate for a holding period: the single rate if set,
    else 5% for 365 days or less, 3.75% for more than 365 days.
    Raises UnknownFrictionError when no CGT form is known, and
    ValueError when the split form needs a holding period that
    was not supplied."""
    if frictions.capital_gains_tax_rate is not None:
        return frictions.capital_gains_tax_rate
    if frictions.cgt_rate_short is not None and frictions.cgt_rate_long is not None:
        if holding_days is None:
            raise ValueError(
                "holding_days is required: the sourced CGT split "
                "charges 5% at 365 days or less and 3.75% above")
        if isinstance(holding_days, bool) or not isinstance(holding_days, int) \
                or holding_days < 0:
            raise ValueError(
                f"holding_days must be a non-negative int, got {holding_days!r}")
        return (frictions.cgt_rate_short if holding_days <= 365
                else frictions.cgt_rate_long)
    raise UnknownFrictionError("capital gains tax rate is UNKNOWN")


def _validate_trade(trade_value, side):
    require_finite("trade_value", trade_value)
    if trade_value <= 0:
        raise ValueError(f"trade_value must be positive, got {trade_value}")
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")


def trade_cost_report(trade_value, side, frictions, realised_gain=None,
                      holding_days=None):
    """Cost breakdown for one NEPSE trade, or an UNKNOWN-marked dict.

    realised_gain is required on the sell side (capital gains tax
    needs a gain); on the buy side it is ignored. When the CGT
    split form is in use, holding_days is also required on the
    sell side (5% at 365 days or less, 3.75% above). When any
    needed rate is UNKNOWN, returns status="UNKNOWN" with
    total_cost=None and net_cash=None — no cost is invented and
    no zero is assumed. When all needed rates are known, returns
    status="KNOWN" with the full breakdown (see module docstring
    for the arithmetic; brokerage uses the slab rate + minimum
    when a slab schedule is supplied).
    """
    _validate_trade(trade_value, side)
    if not isinstance(frictions, NepalFrictions):
        raise TypeError("frictions must be a NepalFrictions instance")
    unknown = []
    if not frictions.brokerage_known():
        unknown.append("brokerage_rate")
    if frictions.sebon_fee_rate is None:
        unknown.append("sebon_fee_rate")
    if frictions.dp_charge_flat is None:
        unknown.append("dp_charge_flat")
    if side == "sell":
        if not frictions.cgt_known():
            unknown.append("capital_gains_tax_rate")
        elif (frictions.capital_gains_tax_rate is None
                and holding_days is None):
            unknown.append("holding_days")
        if realised_gain is None:
            unknown.append("realised_gain")
        elif not isinstance(realised_gain, (int, float)) or isinstance(realised_gain, bool):
            raise ValueError(f"realised_gain must be a number, got {realised_gain!r}")
    if unknown:
        return {"status": "UNKNOWN", "exchange": "NEPSE",
                "trade_value": trade_value, "side": side,
                "unknown_fields": unknown, "total_cost": None,
                "net_cash": None,
                "note": ("Cannot compute trade cost: one or more friction "
                         "rates are UNKNOWN. Supply sourced, dated rates "
                         "in NepalFrictions; costs are never assumed zero.")}
    brokerage_cost = brokerage_cost_for(trade_value, frictions)
    sebon_fee = trade_value * frictions.sebon_fee_rate
    dp_charge = frictions.dp_charge_flat
    cgt = 0.0
    if side == "sell" and realised_gain > 0:
        cgt = realised_gain * cgt_rate_for(holding_days, frictions)
    total = brokerage_cost + sebon_fee + dp_charge + cgt
    net = -(trade_value + total) if side == "buy" else trade_value - total
    return {"status": "KNOWN", "exchange": "NEPSE",
            "trade_value": trade_value, "side": side,
            "brokerage_cost": brokerage_cost, "sebon_fee": sebon_fee,
            "dp_charge": dp_charge, "capital_gains_tax": cgt,
            "total_cost": total, "net_cash": net,
            "cost_as_fraction_of_trade_value": total / trade_value,
            "source": frictions.source, "as_of": frictions.as_of}


def realised_gain_after_fees(buy_value, sell_value, frictions,
                             holding_days=None):
    """The CGT-taxable gain on a round trip, computed AFTER fees,
    as the sourced schedule defines it: sell proceeds after
    sell-side charges, minus the fee-inclusive cost of buying.
    Returns the gain, the CGT on it (0 on a loss), and the net
    proceeds after all charges and tax. Raises
    UnknownFrictionError when a needed rate is UNKNOWN (or when
    the CGT split form needs a holding period not supplied)."""
    _validate_trade(buy_value, "buy")
    _validate_trade(sell_value, "sell")
    if not isinstance(frictions, NepalFrictions):
        raise TypeError("frictions must be a NepalFrictions instance")
    if not frictions.is_cost_known():
        raise UnknownFrictionError(
            f"cannot compute gain after fees: unknown fields "
            f"{frictions.unknown_fields()}")
    buy_charges = (brokerage_cost_for(buy_value, frictions)
                   + buy_value * frictions.sebon_fee_rate
                   + frictions.dp_charge_flat)
    sell_charges = (brokerage_cost_for(sell_value, frictions)
                    + sell_value * frictions.sebon_fee_rate
                    + frictions.dp_charge_flat)
    buy_total = buy_value + buy_charges
    sell_net_before_tax = sell_value - sell_charges
    gain = sell_net_before_tax - buy_total
    cgt = 0.0
    if gain > 0:
        cgt = gain * cgt_rate_for(holding_days, frictions)
    return {"exchange": "NEPSE",
            "buy_value": buy_value, "buy_charges": buy_charges,
            "buy_total_cost": buy_total,
            "sell_value": sell_value, "sell_charges": sell_charges,
            "sell_net_before_tax": sell_net_before_tax,
            "realised_gain": gain, "capital_gains_tax": cgt,
            "net_proceeds": sell_net_before_tax - cgt,
            "source": frictions.source, "as_of": frictions.as_of}


def apply_trade_frictions(trade_value, side, frictions, realised_gain=None):
    """Same as trade_cost_report, but raises UnknownFrictionError
    instead of returning an UNKNOWN dict. Use this inside backtests:
    an UNKNOWN friction must stop the run, not silently pass as zero
    cost. The strict version is the safe default for backtests."""
    report = trade_cost_report(trade_value, side, frictions, realised_gain)
    if report["status"] == "UNKNOWN":
        raise UnknownFrictionError(
            f"cannot apply NEPSE frictions: unknown fields "
            f"{report['unknown_fields']}; supply sourced, dated rates")
    return report


def backtest_trade_cost(trade_value, side, frictions, realised_gain=None):
    """Backtest hook: the friction cost of one trade, for use as
    backtest.backtest(cost_per_trade=...)-style input. Raises
    UnknownFrictionError when any needed rate is UNKNOWN, so a
    backtest can never run cost-free by accident."""
    return apply_trade_frictions(trade_value, side, frictions, realised_gain)["total_cost"]


def circuit_status(price, prev_close, frictions):
    """Whether a price sits within the NEPSE circuit limits — or
    UNKNOWN when the limits are not known. Never guesses a limit."""
    require_finite("price", price)
    require_finite("prev_close", prev_close)
    if price <= 0 or prev_close <= 0:
        raise ValueError("price and prev_close must be positive")
    if not isinstance(frictions, NepalFrictions):
        raise TypeError("frictions must be a NepalFrictions instance")
    unknown = [n for n in ("circuit_limit_up", "circuit_limit_down")
               if getattr(frictions, n) is None]
    if unknown:
        return {"status": "UNKNOWN", "exchange": "NEPSE",
                "unknown_fields": unknown, "within_limits": None,
                "note": ("Circuit limits are UNKNOWN: no limit is "
                         "asserted. Check the current NEPSE/SEBON notice.")}
    lo = prev_close * (1.0 - frictions.circuit_limit_down)
    hi = prev_close * (1.0 + frictions.circuit_limit_up)
    # Boundary tolerance: 100 * 1.15 in binary floating point is
    # 114.99999999999999, so a price exactly at the +15% limit
    # would otherwise read as outside. The tolerance is 1e-9 of
    # the previous close — far below any real price tick.
    tol = 1e-9 * prev_close
    return {"status": "KNOWN", "exchange": "NEPSE",
            "price": price, "prev_close": prev_close,
            "lower_limit": lo, "upper_limit": hi,
            "within_limits": (lo - tol) <= price <= (hi + tol),
            "source": frictions.source, "as_of": frictions.as_of}


def settlement_status(frictions):
    """Settlement-cycle status. Returns the lag when known; UNKNOWN
    otherwise. No calendar date is computed: NEPSE trading-day /
    holiday calendars are not in the local programme files, so a
    settlement DATE would be invented precision."""
    if not isinstance(frictions, NepalFrictions):
        raise TypeError("frictions must be a NepalFrictions instance")
    if frictions.settlement_lag_business_days is None:
        return {"status": "UNKNOWN", "exchange": "NEPSE",
                "settlement_lag_business_days": None,
                "note": ("Settlement lag is UNKNOWN: check the current "
                         "NEPSE/SEBON notice for the period in question.")}
    return {"status": "KNOWN", "exchange": "NEPSE",
            "settlement_lag_business_days": frictions.settlement_lag_business_days,
            "settlement_cycle": f"T+{frictions.settlement_lag_business_days}",
            "source": frictions.source, "as_of": frictions.as_of}
