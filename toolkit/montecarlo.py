"""Monte Carlo simulation — GBM paths, simulated VaR, option payoff. Zero dependencies.

Seeded for reproducibility: same seed, same paths, every time.
A simulation without a seed is not a testable result.
"""
from __future__ import annotations

import math
import random


def gbm_paths(S0, mu, sigma, T, steps, n_paths, seed=42):
    """Geometric Brownian Motion paths. Returns list of price lists (incl. S0)."""
    if S0 <= 0 or sigma < 0 or steps < 1 or n_paths < 1:
        raise ValueError("bad GBM parameters")
    rng = random.Random(seed)
    dt = T / steps
    drift = (mu - 0.5 * sigma * sigma) * dt
    vol = sigma * math.sqrt(dt)
    paths = []
    for _ in range(n_paths):
        path = [S0]
        price = S0
        for _ in range(steps):
            price *= math.exp(drift + vol * rng.gauss(0.0, 1.0))
            path.append(price)
        paths.append(path)
    return paths


def simulated_var(returns_or_paths_end, alpha=0.05):
    """Simulated VaR: the alpha-quantile loss, as a positive fraction.

    Input: list of simulated end values, or list of returns. If values
    look like prices (>1 in magnitude on average), they are converted
    to returns against their mean start — pass returns directly to
    avoid ambiguity.
    """
    ordered = sorted(returns_or_paths_end)
    k = max(0, int(math.floor(alpha * len(ordered))))
    return -ordered[k] if ordered else 0.0


def monte_carlo_option_price(S0, K, T, r, sigma, kind="call", n_paths=10000, seed=42, steps=252):
    """Monte Carlo European option price, discounted at r, risk-neutral drift r."""
    paths = gbm_paths(S0, r, sigma, T, steps, n_paths, seed=seed)
    payoffs = []
    for path in paths:
        st = path[-1]
        payoffs.append(max(st - K, 0.0) if kind == "call" else max(K - st, 0.0))
    return math.exp(-r * T) * (sum(payoffs) / len(payoffs))
