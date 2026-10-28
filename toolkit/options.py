"""Options pricing — Black-Scholes, Greeks, implied volatility. Zero dependencies.

This is the advanced layer: a library that prices an option without
hand-waving, and shows every Greek, so a user can see what moves the price.
"""
from __future__ import annotations

import math

_SQRT2 = math.sqrt(2.0)


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes(S, K, T, r, sigma, kind="call"):
    """Black-Scholes price. S>0, K>0, T>0, sigma>0. kind='call' or 'put'."""
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        raise ValueError("S, K, T, sigma must all be positive")
    if kind not in ("call", "put"):
        raise ValueError("kind must be 'call' or 'put'")
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if kind == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def greeks(S, K, T, r, sigma, kind="call"):
    """Delta, gamma, theta (per year), vega (per 1.0 vol), rho (per 1.0 rate).

    kind must be 'call' or 'put'; anything else raises instead of
    silently returning put Greeks (pre-Phase-4 bug: an unknown kind
    fell through to the put branch).
    """
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        raise ValueError("S, K, T, sigma must all be positive")
    if kind not in ("call", "put"):
        raise ValueError("kind must be 'call' or 'put'")
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    pdf = _norm_pdf(d1)
    gamma = pdf / (S * sigma * math.sqrt(T))
    vega = S * pdf * math.sqrt(T)
    if kind == "call":
        delta = _norm_cdf(d1)
        theta = (-S * pdf * sigma / (2 * math.sqrt(T))
                 - r * K * math.exp(-r * T) * _norm_cdf(d2))
        rho = K * T * math.exp(-r * T) * _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-S * pdf * sigma / (2 * math.sqrt(T))
                 + r * K * math.exp(-r * T) * _norm_cdf(-d2))
        rho = -K * T * math.exp(-r * T) * _norm_cdf(-d2)
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho}


def implied_volatility(price, S, K, T, r, kind="call", tol=1e-4):
    """Implied vol by bisection. Raises if price is outside BS bounds."""
    lo, hi = 0.001, 5.0
    if not (black_scholes(S, K, T, r, lo, kind) <= price <= black_scholes(S, K, T, r, hi, kind)):
        raise ValueError("price is outside Black-Scholes bounds for this vol range")
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if black_scholes(S, K, T, r, mid, kind) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)
