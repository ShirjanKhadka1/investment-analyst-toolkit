"""Financial ratios and DuPont decomposition.

All ratios are returned as decimals (0.40 = 40%), never as percentages,
so callers cannot mix up the two. Every denominator is checked: a zero
denominator raises, it never returns 0 or infinity silently.
"""

from .checks import require_finite, require_non_negative, require_positive


def profitability(revenue, gross_profit, operating_profit, net_income):
    """Gross, operating, and net margins. Revenue must be positive.

    Profit inputs are checked finite only: a negative profit (a loss)
    is real and must flow through as a negative margin.
    """
    require_positive("revenue", revenue)
    require_finite("gross_profit", gross_profit)
    require_finite("operating_profit", operating_profit)
    require_finite("net_income", net_income)
    return {
        "gross_margin": gross_profit / revenue,
        "operating_margin": operating_profit / revenue,
        "net_margin": net_income / revenue,
    }


def liquidity(current_assets, current_liabilities, inventory=0.0):
    """Current ratio and quick ratio (current assets less inventory)."""
    require_finite("current_assets", current_assets)
    require_positive("current_liabilities", current_liabilities)
    require_non_negative("inventory", inventory)
    return {
        "current_ratio": current_assets / current_liabilities,
        "quick_ratio": (current_assets - inventory) / current_liabilities,
    }


def leverage(total_debt, total_equity, total_assets=None):
    """Debt-to-equity. If total_assets is given, debt-to-assets is added.

    Negative equity is not an error here — it is a real, distressed state —
    but it makes the ratio negative, which callers must interpret, not ignore.
    """
    require_finite("total_debt", total_debt)
    require_finite("total_equity", total_equity)
    if total_equity == 0:
        raise ValueError("total_equity must not be zero for debt-to-equity")
    out = {"debt_to_equity": total_debt / total_equity}
    if total_assets is not None:
        require_positive("total_assets", total_assets)
        out["debt_to_assets"] = total_debt / total_assets
    return out


def dupont_roe(net_income, revenue, total_assets, total_equity):
    """DuPont decomposition: ROE = net margin x asset turnover x equity multiplier.

    Returns the three components and their product, so the decomposition can
    be checked: components must multiply to ROE (within floating-point error).
    """
    require_positive("revenue", revenue)
    require_positive("total_assets", total_assets)
    require_finite("net_income", net_income)
    require_finite("total_equity", total_equity)
    if total_equity == 0:
        raise ValueError("total_equity must not be zero for DuPont ROE")
    net_margin = net_income / revenue
    asset_turnover = revenue / total_assets
    equity_multiplier = total_assets / total_equity
    return {
        "net_margin": net_margin,
        "asset_turnover": asset_turnover,
        "equity_multiplier": equity_multiplier,
        "roe": net_margin * asset_turnover * equity_multiplier,
    }
