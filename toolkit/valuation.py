"""Valuation helpers. Every helper shows its inputs; none hides an assumption.

DCF convention used here (stated, so it can be challenged):
- free_cash_flows: explicit forecast-period FCFs, in order, undiscounted.
- terminal_year_fcf: the FCF in the first year AFTER the forecast period,
  already grown at terminal_growth. It is an input, not derived silently.
- Terminal value = terminal_year_fcf / (discount_rate - terminal_growth).
- All cash flows are discounted at the end of each year (no mid-year adjustment
  unless the caller adjusts the cash flows first).
"""

from .checks import require_finite, require_rate, validate_series


def dcf_value(free_cash_flows, discount_rate, terminal_growth, terminal_year_fcf):
    """Enterprise value from explicit FCFs + a single-stage terminal value."""
    require_rate("discount_rate", discount_rate)
    require_rate("terminal_growth", terminal_growth)
    free_cash_flows = validate_series("free_cash_flows", free_cash_flows, min_length=1)
    require_finite("terminal_year_fcf", terminal_year_fcf)
    if discount_rate <= terminal_growth:
        raise ValueError(
            f"discount_rate ({discount_rate}) must exceed "
            f"terminal_growth ({terminal_growth}), otherwise terminal value is undefined"
        )
    if not free_cash_flows:
        raise ValueError("free_cash_flows must not be empty")
    pv = sum(
        fcf / (1 + discount_rate) ** (i + 1)
        for i, fcf in enumerate(free_cash_flows)
    )
    terminal_value = terminal_year_fcf / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / (1 + discount_rate) ** len(free_cash_flows)
    return pv + pv_terminal


def gordon_value(next_dividend, required_return, growth):
    """Gordon growth model: P = D1 / (k - g). k must exceed g."""
    require_finite("next_dividend", next_dividend)
    require_rate("required_return", required_return)
    require_rate("growth", growth)
    if required_return <= growth:
        raise ValueError("required_return must exceed growth in the Gordon model")
    return next_dividend / (required_return - growth)


def implied_price_from_multiple(metric_value, multiple):
    """Implied price/value = metric x multiple (e.g. EPS x P/E, EBITDA x EV/EBITDA).

    Trivial arithmetic, kept as a function so the multiple and the metric are
    named in code and in tests, instead of buried in a spreadsheet cell.
    """
    require_finite("metric_value", metric_value)
    require_finite("multiple", multiple)
    return metric_value * multiple
