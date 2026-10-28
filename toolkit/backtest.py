"""Strategy backtesting — with costs, so a backtest is not a fantasy.

Rules: signals are computed from data up to time t only (no lookahead),
trades pay a cost, and the equity curve is reported alongside a
buy-and-hold benchmark on the same data.
"""
from __future__ import annotations

from .checks import require_non_negative, require_positive, validate_series


def _validate_prices(prices):
    prices = validate_series("prices", prices, min_length=1)
    if any(p <= 0 for p in prices):
        raise ValueError("prices must all be positive")
    return prices


def moving_average_crossover_signals(prices, fast=10, slow=30):
    """1 = long, 0 = flat. Signal at t uses closes up to t only."""
    prices = _validate_prices(prices)
    if fast < 1 or slow <= fast:
        raise ValueError("need 0 < fast < slow")
    signals = []
    for t in range(len(prices)):
        if t < slow - 1:
            signals.append(0)
            continue
        fast_ma = sum(prices[t - fast + 1:t + 1]) / fast
        slow_ma = sum(prices[t - slow + 1:t + 1]) / slow
        signals.append(1 if fast_ma > slow_ma else 0)
    return signals


def backtest(prices, signals, initial_capital=10000.0, cost_per_trade=0.0):
    """Run a 1/0 signal series. Returns equity curve (incl. initial capital).

    Position for period t is signals[t-1] (yesterday's signal), so no
    trade is made on information from today. A change in position pays
    cost_per_trade once.

    Validation (pre-Phase-4 gaps): prices must be positive, signals
    must be exactly 0 or 1, and cost_per_trade must not be negative —
    a negative cost previously ADDED money on every trade.
    """
    prices = _validate_prices(prices)
    signals = list(signals)
    if len(prices) != len(signals):
        raise ValueError("prices and signals must be the same length")
    if any(s not in (0, 1) for s in signals):
        raise ValueError("signals must be 0 or 1 only")
    if initial_capital <= 0:
        raise ValueError("initial capital must be positive")
    require_non_negative("cost_per_trade", cost_per_trade)
    equity = [initial_capital]
    capital = initial_capital
    position = 0
    shares = 0.0
    for t in range(1, len(prices)):
        target = signals[t - 1]
        if target != position:
            if target == 1:
                shares = capital / prices[t - 1] if prices[t - 1] else 0.0
                capital = 0.0
            else:
                capital = shares * prices[t - 1] if shares else capital
                shares = 0.0
            capital -= cost_per_trade
            position = target
        value = capital + (shares * prices[t] if shares else 0.0)
        equity.append(value)
    return equity


def buy_and_hold_equity(prices, initial_capital=10000.0):
    """Buy-and-hold benchmark on the same prices."""
    prices = _validate_prices(prices)
    require_positive("initial_capital", initial_capital)
    return [initial_capital * p / prices[0] for p in prices]
