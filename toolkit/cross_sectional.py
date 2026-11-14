"""Cross-sectional ranking — momentum / value / quality proxies,
decile tests net of costs.

Stdlib only. The ranking functions take plain caller-supplied
series and numbers and never fetch live data themselves: momentum
comes from price closes the caller passes in, value and quality
come from fundamentals the caller passes in (or from SEC EDGAR via
``proxies_from_edgar``, which is the one function here that touches
the network — through the existing ``toolkit/edgar.py``, never a
second EDGAR client). Tests use fixtures only, never live EDGAR or
Yahoo, following the existing ``tests/test_edgar.py`` pattern.

Costs are charged, never assumed away. ``round_trip_cost_fraction``
uses the existing cost model — ``toolkit.strategy_backtester.
trade_cost`` (NEPSE: the sourced broker-slab + SEBON 0.015% + DP
Rs 25 schedule; India/UK: their sourced schedules) — or an explicit
caller-supplied ``cost_fraction``. A market with no sourced schedule
raises ``UnknownCostError`` unless the caller supplies one, exactly
as the portfolio backtester does. Capital gains tax is NOT charged
(lot gain / holding period not tracked — same exclusion as the
backtester, stated wherever a result is reported, not hidden).

Verdict rule (pre-registered in
``docs/cross-sectional-eval-protocol.md``): a ranking rule "beats
zero" only if its mean net top-minus-bottom decile spread is
greater than zero AND the 95% seeded-bootstrap CI on the mean
spread lies entirely above zero. Otherwise the verdict is
"no edge". A win before costs never counts.

Research only. Never investment advice. A past decile spread is
not a promise about any future spread.
"""
from __future__ import annotations

import math
import random

from .checks import require_finite, require_positive, validate_series
from .edgar import get_fundamentals
from .strategy_backtester import trade_cost

__all__ = [
    "BOOTSTRAP_RESAMPLES",
    "BOOTSTRAP_SEED",
    "MOMENTUM_FORMATION",
    "MOMENTUM_SKIP",
    "assign_deciles",
    "book_to_price",
    "earnings_yield",
    "gross_margin",
    "momentum_score",
    "net_forward_return",
    "proxies_from_edgar",
    "proxies_from_facts",
    "rank_universe",
    "return_on_equity",
    "round_trip_cost_fraction",
    "spread_verdict",
]

# Protocol-fixed defaults (docs/cross-sectional-eval-protocol.md):
# 6-minus-1 momentum (126-bar formation, most recent 21 skipped),
# 2000 bootstrap resamples, seed 20261114.
MOMENTUM_FORMATION = 126
MOMENTUM_SKIP = 21
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261114
N_DECILES = 10

# XBRL concepts the value/quality proxies need, as (taxonomy, tag).
PROXY_CONCEPTS = (
    ("us-gaap", "NetIncomeLoss"),
    ("us-gaap", "StockholdersEquity"),
    ("us-gaap", "Revenues"),
    ("us-gaap", "GrossProfit"),
    ("us-gaap", "Assets"),
)


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------

def momentum_score(closes, formation=MOMENTUM_FORMATION,
                   skip=MOMENTUM_SKIP):
    """6-minus-1 momentum: total return from ``formation`` bars ago
    to ``skip`` bars ago, ``P[-1-skip] / P[-1-formation] - 1``.

    The most recent ``skip`` bars are excluded (the standard 12-1
    skip, shortened to the 6-month formation this protocol uses).
    ``closes`` must hold more than ``formation`` closes; every close
    must be positive. Raises on a shorter series instead of
    computing momentum over a truncated window.
    """
    if not isinstance(formation, int) or isinstance(formation, bool) \
            or formation <= 0:
        raise ValueError(f"formation must be a positive int, got {formation!r}")
    if not isinstance(skip, int) or isinstance(skip, bool) or skip < 0 \
            or skip >= formation:
        raise ValueError(
            f"skip must be an int in [0, formation), got {skip!r}")
    values = validate_series("closes", closes, min_length=formation + 1)
    if any(v <= 0 for v in values):
        raise ValueError("closes must all be positive")
    return values[-1 - skip] / values[-1 - formation] - 1.0


# ---------------------------------------------------------------------------
# Value / quality proxies from fundamentals
# ---------------------------------------------------------------------------

def earnings_yield(net_income, market_cap):
    """Net income / market capitalisation. Market cap must be > 0."""
    require_finite("net_income", net_income)
    require_positive("market_cap", market_cap)
    return net_income / market_cap


def book_to_price(book_equity, market_cap):
    """Book equity / market capitalisation. Market cap must be > 0."""
    require_finite("book_equity", book_equity)
    require_positive("market_cap", market_cap)
    return book_equity / market_cap


def return_on_equity(net_income, book_equity):
    """Net income / book equity. Book equity must be non-zero."""
    require_finite("net_income", net_income)
    require_finite("book_equity", book_equity)
    if book_equity == 0:
        raise ValueError("book_equity must be non-zero for ROE")
    return net_income / book_equity


def gross_margin(gross_profit, revenues):
    """Gross profit / revenues. Revenues must be non-zero."""
    require_finite("gross_profit", gross_profit)
    require_finite("revenues", revenues)
    if revenues == 0:
        raise ValueError("revenues must be non-zero for gross margin")
    return gross_profit / revenues


def _fact_value(facts, tag):
    """One concept's numeric value from a facts mapping.

    Accepts either the ``get_fundamentals`` shape
    (``{"values": {tag: {"value": ...}}}``) or a plain
    ``{tag: number}`` mapping. A missing concept raises ValueError
    naming it — no value is ever invented or defaulted.
    """
    if not isinstance(facts, dict):
        raise TypeError(
            f"facts must be a mapping, got {type(facts).__name__}")
    pool = facts.get("values", facts) if isinstance(facts, dict) else facts
    if not isinstance(pool, dict) or tag not in pool:
        raise ValueError(f"concept {tag} not in facts")
    entry = pool[tag]
    value = entry.get("value") if isinstance(entry, dict) else entry
    require_finite(tag, value)
    return float(value)


def proxies_from_facts(facts, market_cap):
    """Value/quality proxies from one company's annual fundamentals.

    ``facts`` is a ``get_fundamentals``-style dict or a plain
    ``{tag: number}`` mapping with the five ``PROXY_CONCEPTS`` tags
    (NetIncomeLoss, StockholdersEquity, Revenues, GrossProfit,
    Assets). Returns the four raw proxies plus provenance when the
    ``get_fundamentals`` shape is given (each concept's form, filing
    date and period end). ``Assets`` is returned as a level for the
    caller (return on assets) and is required to be present even
    though no proxy here divides by it — a half-supplied snapshot
    is refused, not half-used. Raises on any missing concept.
    """
    require_positive("market_cap", market_cap)
    values = {tag: _fact_value(facts, tag) for _, tag in PROXY_CONCEPTS}
    provenance = {}
    pool = facts.get("values", {}) if isinstance(facts, dict) else {}
    if isinstance(pool, dict):
        for tag in values:
            entry = pool.get(tag)
            if isinstance(entry, dict):
                provenance[tag] = {
                    "form": entry.get("form"),
                    "filed": entry.get("filed"),
                    "end": entry.get("end"),
                }
    return {
        "earnings_yield": earnings_yield(
            values["NetIncomeLoss"], market_cap),
        "book_to_price": book_to_price(
            values["StockholdersEquity"], market_cap),
        "roe": return_on_equity(
            values["NetIncomeLoss"], values["StockholdersEquity"]),
        "gross_margin": gross_margin(
            values["GrossProfit"], values["Revenues"]),
        "assets": values["Assets"],
        "market_cap": float(market_cap),
        "provenance": provenance,
    }


def proxies_from_edgar(ticker, market_cap, user_agent=None):
    """Value/quality proxies for a US ticker from SEC EDGAR.

    Fetches the five proxy concepts' latest audited annual (10-K)
    values through the existing ``toolkit.edgar.get_fundamentals``
    (throttled, declared User-Agent) and computes the same proxies
    as ``proxies_from_facts``. Network only: this is the one
    function in this module that fetches. ``market_cap`` is
    caller-supplied (price x shares outstanding at the ranking
    date) — EDGAR facts carry no market cap. Returns the proxies
    plus ``ticker``, ``cik`` and ``source``.
    """
    require_positive("market_cap", market_cap)
    fundamentals = get_fundamentals(ticker, list(PROXY_CONCEPTS),
                                    user_agent=user_agent)
    proxies = proxies_from_facts(fundamentals, market_cap)
    proxies["ticker"] = fundamentals["ticker"]
    proxies["cik"] = fundamentals["cik"]
    proxies["source"] = fundamentals["source"]
    return proxies


# ---------------------------------------------------------------------------
# Cross-sectional ranking
# ---------------------------------------------------------------------------

def _zscores(values):
    """Cross-sectional z-scores of {symbol: value}.

    Population standard deviation (the ranking is over the whole
    universe, not a sample of it). A zero-variance component scores
    0.0 for every symbol — it carries no cross-sectional signal and
    must not divide by zero.
    """
    if not values:
        raise ValueError("no values to z-score")
    mean = math.fsum(values.values()) / len(values)
    var = math.fsum((v - mean) ** 2 for v in values.values()) / len(values)
    sd = math.sqrt(var)
    if sd == 0:
        return {symbol: 0.0 for symbol in values}
    return {symbol: (v - mean) / sd for symbol, v in values.items()}


def assign_deciles(scores, n_deciles=N_DECILES):
    """Rank {symbol: score} into deciles 1..``n_deciles``.

    Decile ``n_deciles`` holds the highest scores. Assignment is by
    sorted rank (ties broken by symbol name, so it is
    deterministic): decile ``floor(rank * n / count) + 1``, capped
    at ``n``. Requires at least ``n_deciles`` symbols — deciles
    with empty buckets are meaningless, so a smaller universe
    raises instead of silently returning partial deciles.
    """
    if not isinstance(n_deciles, int) or isinstance(n_deciles, bool) \
            or n_deciles < 2:
        raise ValueError(f"n_deciles must be an int >= 2, got {n_deciles!r}")
    if not isinstance(scores, dict) or not scores:
        raise ValueError("scores must be a non-empty dict of symbol -> score")
    if len(scores) < n_deciles:
        raise ValueError(
            f"need at least {n_deciles} symbols for {n_deciles} deciles, "
            f"got {len(scores)}")
    for symbol, value in scores.items():
        require_finite(f"scores[{symbol!r}]", value)
    ordered = sorted(scores, key=lambda s: (scores[s], s))
    count = len(ordered)
    return {
        symbol: min(n_deciles, (rank * n_deciles) // count + 1)
        for rank, symbol in enumerate(ordered)
    }


def rank_universe(components):
    """Rank a universe into deciles from raw factor components.

    ``components`` maps symbol -> a dict with any subset of
    ``momentum``, ``earnings_yield``, ``book_to_price``, ``roe`` and
    ``gross_margin`` (raw values, not z-scores). Each component is
    z-scored cross-sectionally; the value score is the mean of the
    earnings-yield and book-to-price z-scores, the quality score the
    mean of the ROE and gross-margin z-scores, and the composite the
    mean of the available scores among (momentum z, value, quality).
    A symbol needs at least one component; a component needs at
    least two symbols carrying it. Returns per symbol
    ``{"momentum", "value", "quality", "composite", "decile"}`` —
    a missing score is None, never zero-filled. Deciles rank the
    composite.
    """
    if not isinstance(components, dict) or not components:
        raise ValueError("components must be a non-empty dict")
    if len(components) < N_DECILES:
        raise ValueError(
            f"need at least {N_DECILES} symbols to rank, "
            f"got {len(components)}")
    keys = ("momentum", "earnings_yield", "book_to_price", "roe",
            "gross_margin")
    for symbol, comp in components.items():
        if not isinstance(comp, dict) or not comp:
            raise ValueError(
                f"components[{symbol!r}] must be a non-empty dict")
        for key, value in comp.items():
            if key not in keys:
                raise ValueError(
                    f"unknown component {key!r} for {symbol!r}")
            require_finite(f"components[{symbol!r}][{key!r}]", value)
    z = {}
    for key in keys:
        present = {s: c[key] for s, c in components.items() if key in c}
        if len(present) >= 2:
            z[key] = _zscores(present)
    out = {}
    for symbol, comp in components.items():
        zs = {key: z[key][symbol] for key in z if symbol in z[key]}
        momentum = zs.get("momentum")
        value_parts = [zs[k] for k in ("earnings_yield", "book_to_price")
                       if k in zs]
        quality_parts = [zs[k] for k in ("roe", "gross_margin") if k in zs]
        value = (math.fsum(value_parts) / len(value_parts)
                 if value_parts else None)
        quality = (math.fsum(quality_parts) / len(quality_parts)
                   if quality_parts else None)
        available = [v for v in (momentum, value, quality)
                     if v is not None]
        if not available:
            raise ValueError(
                f"no usable component for {symbol!r}: every component "
                "it carries is held by fewer than two symbols")
        composite = math.fsum(available) / len(available)
        out[symbol] = {
            "momentum": momentum,
            "value": value,
            "quality": quality,
            "composite": composite,
        }
    deciles = assign_deciles(
        {symbol: row["composite"] for symbol, row in out.items()})
    for symbol, row in out.items():
        row["decile"] = deciles[symbol]
    return out


# ---------------------------------------------------------------------------
# Costs and decile spreads
# ---------------------------------------------------------------------------

def round_trip_cost_fraction(market, trade_value=None,
                             cost_fraction=None):
    """Round-trip (buy + sell) cost as a fraction of traded value.

    Explicit ``cost_fraction`` wins (per-leg fraction, doubled for
    the round trip). Otherwise the market's sourced schedule via
    ``trade_cost`` — NEPSE at the given ``trade_value`` (broker slab
    + SEBON + DP per leg). Raises ``UnknownCostError`` for a market
    with no sourced schedule and no explicit fraction: costs are
    never assumed zero. Capital gains tax is NOT included (lot gain
    / holding period not tracked). Returns
    ``(fraction, basis)`` where ``basis`` states what the cost
    includes, so it can be printed on any result.
    """
    if cost_fraction is not None:
        if not isinstance(cost_fraction, (int, float)) \
                or isinstance(cost_fraction, bool) \
                or cost_fraction < 0 or math.isnan(cost_fraction) \
                or math.isinf(cost_fraction):
            raise ValueError(
                f"cost_fraction must be a finite number >= 0, "
                f"got {cost_fraction!r}")
        return (2.0 * float(cost_fraction),
                (f"caller-supplied flat cost_fraction={cost_fraction} "
                 "per leg, round trip doubled"))
    if trade_value is None:
        raise ValueError(
            "trade_value is required when no cost_fraction is supplied")
    require_positive("trade_value", trade_value)
    buy_cost, buy_basis = trade_cost(market, trade_value, side="buy")
    sell_cost, sell_basis = trade_cost(market, trade_value, side="sell")
    total = buy_cost + sell_cost
    basis = (f"{buy_basis}; sell leg: {sell_basis}; CGT excluded "
             "(lot gain / holding period not tracked)")
    return total / trade_value, basis


def net_forward_return(gross_return, market, trade_value=None,
                       cost_fraction=None):
    """A forward return net of round-trip costs.

    ``net = gross - round_trip_cost_fraction``. Returns
    ``(net_return, cost_fraction_applied, basis)``.
    """
    require_finite("gross_return", gross_return)
    fraction, basis = round_trip_cost_fraction(
        market, trade_value=trade_value, cost_fraction=cost_fraction)
    return gross_return - fraction, fraction, basis


def _bootstrap_ci(values, resamples=BOOTSTRAP_RESAMPLES,
                  seed=BOOTSTRAP_SEED):
    """95% percentile bootstrap CI on the mean of ``values``.

    Seeded, so the CI is deterministic for a given input. Returns
    ``(lo, hi)`` of the resampled means.
    """
    if not values:
        raise ValueError("no values to bootstrap")
    if not isinstance(resamples, int) or isinstance(resamples, bool) \
            or resamples < 100:
        raise ValueError(f"need at least 100 resamples, got {resamples}")
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(math.fsum(sample) / n)
    means.sort()
    return (means[int(0.025 * resamples)],
            means[int(0.975 * resamples) - 1])


def spread_verdict(spreads, resamples=BOOTSTRAP_RESAMPLES,
                   seed=BOOTSTRAP_SEED):
    """Verdict on a list of per-rebalance top-minus-bottom spreads.

    The rule "beats zero" only if the mean net spread is greater
    than zero AND the 95% seeded-bootstrap CI on the mean spread
    lies entirely above zero. Otherwise the verdict is "no edge".
    Requires at least 2 spreads (a CI on one observation is not a
    CI). Returns ``{"mean_spread", "ci_95", "n", "verdict"}``.
    """
    values = validate_series("spreads", spreads, min_length=2)
    mean = math.fsum(values) / len(values)
    lo, hi = _bootstrap_ci(values, resamples=resamples, seed=seed)
    verdict = "beats zero" if mean > 0 and lo > 0 else "no edge"
    return {"mean_spread": mean, "ci_95": (lo, hi), "n": len(values),
            "verdict": verdict}
