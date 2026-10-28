"""Monte Carlo simulation — GBM paths, simulated VaR, option payoff. Zero dependencies.

Seeded for reproducibility: same seed, same paths, every time.
A simulation without a seed is not a testable result.
"""
from __future__ import annotations

import math
import random


from .checks import require_finite, require_positive


def gbm_paths(S0, mu, sigma, T, steps, n_paths, seed=42):
    """Geometric Brownian Motion paths. Returns list of price lists (incl. S0).

    Exact discretisation: S(t+dt) = S(t) * exp((mu - 0.5*sigma^2)*dt
    + sigma*sqrt(dt)*Z). T must be positive: T = 0 previously returned
    flat paths silently, and T < 0 ran time backwards.
    """
    require_positive("S0", S0)
    require_finite("mu", mu)
    require_finite("sigma", sigma)
    if sigma < 0:
        raise ValueError("sigma must not be negative")
    require_positive("T", T)
    if steps < 1 or n_paths < 1:
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

    alpha must be in (0, 1): alpha = 1 previously raised IndexError
    instead of ValueError, and an empty input previously returned 0.0
    (a silent "no risk" answer).
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    if not returns_or_paths_end:
        raise ValueError("no simulated values")
    ordered = sorted(returns_or_paths_end)
    k = max(0, int(math.floor(alpha * len(ordered))))
    k = min(k, len(ordered) - 1)
    return -ordered[k] if ordered else 0.0


def monte_carlo_option_price(S0, K, T, r, sigma, kind="call", n_paths=10000, seed=42, steps=252):
    """Monte Carlo European option price, discounted at r, risk-neutral drift r.

    kind must be 'call' or 'put'; anything else raises instead of
    silently pricing a put (pre-Phase-4 bug, same fall-through as greeks).
    """
    if kind not in ("call", "put"):
        raise ValueError("kind must be 'call' or 'put'")
    paths = gbm_paths(S0, r, sigma, T, steps, n_paths, seed=seed)
    payoffs = []
    for path in paths:
        st = path[-1]
        payoffs.append(max(st - K, 0.0) if kind == "call" else max(K - st, 0.0))
    return math.exp(-r * T) * (sum(payoffs) / len(payoffs))
