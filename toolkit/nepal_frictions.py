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
                "circuit_limit_up", "circuit_limit_down")
_UNKNOWN_SOURCE = "UNKNOWN — not verified in local programme files as of 2026-10-28"


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
        if self.settlement_lag_business_days is not None:
            lag = self.settlement_lag_business_days
            if isinstance(lag, bool) or not isinstance(lag, int) or lag < 0:
                raise ValueError(
                    f"settlement_lag_business_days must be a non-negative int, got {lag!r}")
        supplied = [n for n in (
            "brokerage_rate", "sebon_fee_rate", "dp_charge_flat",
            "capital_gains_tax_rate", "circuit_limit_up",
            "circuit_limit_down", "settlement_lag_business_days")
            if getattr(self, n) is not None]
        if supplied and (not self.source or self.source == _UNKNOWN_SOURCE
                         or self.as_of is None or not str(self.as_of).strip()):
            raise ValueError(
                "supplied friction rate(s) "
                f"{supplied} require a source and an as_of date; "
                "unsourced rates are rejected, not stored")

    def unknown_fields(self):
        """Names of friction parameters still UNKNOWN (None)."""
        return [n for n in (
            "brokerage_rate", "sebon_fee_rate", "dp_charge_flat",
            "capital_gains_tax_rate", "circuit_limit_up",
            "circuit_limit_down", "settlement_lag_business_days")
            if getattr(self, n) is None]

    def is_cost_known(self):
        """True only when brokerage, SEBON fee and DP charge are all known."""
        return (self.brokerage_rate is not None
                and self.sebon_fee_rate is not None
                and self.dp_charge_flat is not None)


def default_unknown_frictions():
    """The default: every rate UNKNOWN. Use this until real, cited
    rates are supplied — never replace it with a remembered rate."""
    return NepalFrictions()


def _validate_trade(trade_value, side):
    require_finite("trade_value", trade_value)
    if trade_value <= 0:
        raise ValueError(f"trade_value must be positive, got {trade_value}")
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")


def trade_cost_report(trade_value, side, frictions, realised_gain=None):
    """Cost breakdown for one NEPSE trade, or an UNKNOWN-marked dict.

    realised_gain is required on the sell side (capital gains tax
    needs a gain); on the buy side it is ignored. When any needed
    rate is UNKNOWN, returns status="UNKNOWN" with total_cost=None
    and net_cash=None — no cost is invented and no zero is assumed.
    When all needed rates are known, returns status="KNOWN" with the
    full breakdown (see module docstring for the arithmetic).
    """
    _validate_trade(trade_value, side)
    if not isinstance(frictions, NepalFrictions):
        raise ValueError("frictions must be a NepalFrictions instance")
    unknown = [n for n in ("brokerage_rate", "sebon_fee_rate", "dp_charge_flat")
               if getattr(frictions, n) is None]
    if side == "sell":
        if frictions.capital_gains_tax_rate is None:
            unknown.append("capital_gains_tax_rate")
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
    brokerage_cost = trade_value * frictions.brokerage_rate
    sebon_fee = trade_value * frictions.sebon_fee_rate
    dp_charge = frictions.dp_charge_flat
    cgt = 0.0
    if side == "sell" and realised_gain > 0:
        cgt = realised_gain * frictions.capital_gains_tax_rate
    total = brokerage_cost + sebon_fee + dp_charge + cgt
    net = -(trade_value + total) if side == "buy" else trade_value - total
    return {"status": "KNOWN", "exchange": "NEPSE",
            "trade_value": trade_value, "side": side,
            "brokerage_cost": brokerage_cost, "sebon_fee": sebon_fee,
            "dp_charge": dp_charge, "capital_gains_tax": cgt,
            "total_cost": total, "net_cash": net,
            "cost_as_fraction_of_trade_value": total / trade_value,
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
        raise ValueError("frictions must be a NepalFrictions instance")
    unknown = [n for n in ("circuit_limit_up", "circuit_limit_down")
               if getattr(frictions, n) is None]
    if unknown:
        return {"status": "UNKNOWN", "exchange": "NEPSE",
                "unknown_fields": unknown, "within_limits": None,
                "note": ("Circuit limits are UNKNOWN: no limit is "
                         "asserted. Check the current NEPSE/SEBON notice.")}
    lo = prev_close * (1.0 - frictions.circuit_limit_down)
    hi = prev_close * (1.0 + frictions.circuit_limit_up)
    return {"status": "KNOWN", "exchange": "NEPSE",
            "price": price, "prev_close": prev_close,
            "lower_limit": lo, "upper_limit": hi,
            "within_limits": lo <= price <= hi,
            "source": frictions.source, "as_of": frictions.as_of}


def settlement_status(frictions):
    """Settlement-cycle status. Returns the lag when known; UNKNOWN
    otherwise. No calendar date is computed: NEPSE trading-day /
    holiday calendars are not in the local programme files, so a
    settlement DATE would be invented precision."""
    if not isinstance(frictions, NepalFrictions):
        raise ValueError("frictions must be a NepalFrictions instance")
    if frictions.settlement_lag_business_days is None:
        return {"status": "UNKNOWN", "exchange": "NEPSE",
                "settlement_lag_business_days": None,
                "note": ("Settlement lag is UNKNOWN: check the current "
                         "NEPSE/SEBON notice for the period in question.")}
    return {"status": "KNOWN", "exchange": "NEPSE",
            "settlement_lag_business_days": frictions.settlement_lag_business_days,
            "settlement_cycle": f"T+{frictions.settlement_lag_business_days}",
            "source": frictions.source, "as_of": frictions.as_of}
