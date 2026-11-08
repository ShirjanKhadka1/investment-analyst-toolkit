"""Investment Analyst Toolkit: explicit, tested finance calculations.

Note: toolkit.mcp_server is intentionally NOT imported here — it is a
``python -m`` entry point, and eagerly importing it would make
``python -m toolkit.mcp_server`` emit a RuntimeWarning. Import it
directly (``from toolkit import mcp_server``) when needed.
"""

from . import (
    archive,
    autonomous,
    backtest,
    checks,
    connectors,
    data,
    decision_log,
    edgar,
    forecast,
    indicators,
    market_profiles,
    montecarlo,
    nepal_frictions,
    options,
    paper_ledger,
    performance,
    pit_store,
    portfolio,
    position_plan,
    predictive,
    quant,
    ratios,
    report,
    rules_mode,
    timemachine,
    tracker,
    valuation,
)

__all__ = ["archive", "autonomous", "backtest", "checks", "connectors", "data", "decision_log", "edgar", "forecast", "indicators", "market_profiles", "montecarlo", "nepal_frictions", "options", "paper_ledger", "performance", "pit_store", "portfolio", "position_plan", "predictive", "quant", "ratios", "report", "rules_mode", "timemachine", "tracker", "valuation"]
