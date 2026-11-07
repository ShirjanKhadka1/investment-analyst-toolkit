"""Quant library — DCF, bank valuation, VaR/CVaR, factors, optimisation,
sector rotation. Zero dependencies, pure Python.

Textbook implementations written from scratch for this toolkit
(Markowitz mean-variance, Lopez de Prado hierarchical risk parity,
Gordon growth, Sharpe/Sortino, historical + parametric VaR).
No code was copied from any outside repo; where an idea came from a
named source it is recorded in ``toolkit/SOURCES_MERGE_2026-11-07.md``.
The Phase 0a teardown of skfolio / FinQuant / microsoft/qlib /
QuantInvestStrats had not reported back when this module was written,
so nothing here depends on it; the coordinator can reconcile later
where one of those libraries would have offered a better-tested
implementation (notably HRP clustering and shrinkage estimation).

No look-ahead, stated once here and repeated in the docstrings:
every function takes only data available at the decision date —
cash-flow forecasts, returns, prices and covariance inputs as given.
Nothing in this module fetches, infers or invents market data.

Fold-ins from the Phase 0a-expanded teardowns (2026-11-07, licences
in ~/workspace/github-repos/ops/LICENSES.md): skfolio (BSD-3-Clause)
risk-measure library and max-diversification formulation; QuantInvestStrats
(MIT) perf-stats — Sharpe inference, multiple-testing correction,
turnover and transaction costs; microsoft/qlib (MIT) Alpha158 factor
set as a citable canonical baseline, reimplemented; EigenLedger
(Apache-2.0) one friendly portfolio_analysis() entry point. Everything
below is reimplemented from the textbook definitions, not copied code;
the sklearn-estimator interface (skfolio) is deliberately NOT copied —
this toolkit's functions are stateless by design — and FinQuant's
Portfolio container shape is already covered by toolkit/portfolio.py.

Research only: not investment advice. Every number is computed from
the inputs shown next to it; worked examples in the tests use
hand-computed fixtures or clearly-labelled synthetic data.
"""
from __future__ import annotations

import itertools
import math

from .checks import (
    require_finite,
    require_non_negative,
    require_positive,
    require_rate,
    validate_series,
)
from .performance import cvar as _historical_cvar
from .performance import max_drawdown, sharpe_ratio, sortino_ratio
from .portfolio import efficient_frontier, min_variance_weights

__all__ = [
    "alpha158_baseline_factors",
    "bank_valuation",
    "benjamini_hochberg",
    "bonferroni_adjust",
    "cross_sectional_ranks",
    "dcf_fcfe",
    "dcf_fcff",
    "dcf_sensitivity",
    "entropic_var",
    "historical_var",
    "hrp_weights",
    "max_diversification_weights",
    "mean_reversion_factor",
    "mean_variance_weights",
    "momentum_factor",
    "net_return_after_costs",
    "parametric_cvar",
    "parametric_var",
    "portfolio_analysis",
    "risk_summary",
    "sector_relative_strength",
    "sector_rotation_allocation",
    "sharpe_confidence_interval",
    "sharpe_standard_error",
    "turnover",
]


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _norm_ppf(p):
    """Inverse standard-normal CDF (Acklam's rational approximation).

    Accurate to ~2e-9 absolute (Acklam's rational approximation).
    Used by the parametric VaR/CVaR so the module stays
    dependency-free (no scipy). Checked in tests against the known
    values Phi^-1(0.05) = -1.6448536269514722 and
    Phi^-1(0.975) = 1.959963984540054.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0, 1), got {p}")
    a = [-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


# ---------------------------------------------------------------------------
# DCF — FCFF / FCFE with explicit assumptions, terminal value, sensitivity
# ---------------------------------------------------------------------------

def dcf_fcff(free_cash_flows, discount_rate, terminal_growth,
             terminal_year_fcf=None, net_debt=0.0, shares_outstanding=None):
    """FCFF DCF: enterprise value, then equity value and value per share.

    free_cash_flows: explicit forecast-period FCFs, undiscounted, in
    order. terminal_year_fcf: FCF in the first year AFTER the forecast
    period, already grown at terminal_growth; when omitted it is
    derived as last FCF * (1 + terminal_growth) and the derivation is
    stated in the result. Terminal value =
    terminal_year_fcf / (discount_rate - terminal_growth), discounted
    at the end of the forecast period (same convention as
    valuation.dcf_value — this function extends it with the equity
    bridge and the assumptions echo, it does not replace it).
    net_debt: debt minus cash, subtracted to reach equity value.
    shares_outstanding: when given, value_per_share is returned.
    No look-ahead: every input is a forecast or a stated assumption
    made at the valuation date.
    """
    require_rate("discount_rate", discount_rate)
    require_rate("terminal_growth", terminal_growth)
    flows = validate_series("free_cash_flows", free_cash_flows, min_length=1)
    require_finite("net_debt", net_debt)
    if discount_rate <= terminal_growth:
        raise ValueError(
            f"discount_rate ({discount_rate}) must exceed terminal_growth "
            f"({terminal_growth}), otherwise terminal value is undefined")
    if terminal_year_fcf is None:
        terminal_year_fcf = flows[-1] * (1 + terminal_growth)
        terminal_derived = True
    else:
        terminal_year_fcf = require_finite("terminal_year_fcf", terminal_year_fcf)
        terminal_derived = False
    if shares_outstanding is not None:
        shares_outstanding = require_positive("shares_outstanding", shares_outstanding)
    n = len(flows)
    pv_explicit = sum(f / (1 + discount_rate) ** (i + 1) for i, f in enumerate(flows))
    terminal_value = terminal_year_fcf / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / (1 + discount_rate) ** n
    enterprise_value = pv_explicit + pv_terminal
    equity_value = enterprise_value - net_debt
    return {
        "pv_explicit_fcfs": pv_explicit,
        "terminal_value": terminal_value,
        "pv_terminal_value": pv_terminal,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "value_per_share": (equity_value / shares_outstanding
                            if shares_outstanding is not None else None),
        "terminal_year_fcf": terminal_year_fcf,
        "terminal_year_fcf_derived": terminal_derived,
        "assumptions": {"discount_rate": discount_rate,
                        "terminal_growth": terminal_growth,
                        "n_forecast_years": n, "net_debt": net_debt,
                        "shares_outstanding": shares_outstanding},
    }


def dcf_fcfe(free_cash_flows_to_equity, cost_of_equity, terminal_growth,
             terminal_year_fcfe=None, shares_outstanding=None):
    """FCFE DCF: equity value directly (discount FCFE at cost of equity).

    Same conventions as dcf_fcff, except the discount rate is the cost
    of equity and no net-debt bridge is needed — FCFE is already after
    debt service. terminal_year_fcfe defaults to last FCFE grown at
    terminal_growth, stated in the result when derived.
    """
    require_rate("cost_of_equity", cost_of_equity)
    require_rate("terminal_growth", terminal_growth)
    flows = validate_series("free_cash_flows_to_equity",
                            free_cash_flows_to_equity, min_length=1)
    if cost_of_equity <= terminal_growth:
        raise ValueError(
            f"cost_of_equity ({cost_of_equity}) must exceed terminal_growth "
            f"({terminal_growth}), otherwise terminal value is undefined")
    if terminal_year_fcfe is None:
        terminal_year_fcfe = flows[-1] * (1 + terminal_growth)
        terminal_derived = True
    else:
        terminal_year_fcfe = require_finite("terminal_year_fcfe", terminal_year_fcfe)
        terminal_derived = False
    if shares_outstanding is not None:
        shares_outstanding = require_positive("shares_outstanding", shares_outstanding)
    n = len(flows)
    pv_explicit = sum(f / (1 + cost_of_equity) ** (i + 1) for i, f in enumerate(flows))
    terminal_value = terminal_year_fcfe / (cost_of_equity - terminal_growth)
    pv_terminal = terminal_value / (1 + cost_of_equity) ** n
    equity_value = pv_explicit + pv_terminal
    return {
        "pv_explicit_fcfes": pv_explicit,
        "terminal_value": terminal_value,
        "pv_terminal_value": pv_terminal,
        "equity_value": equity_value,
        "value_per_share": (equity_value / shares_outstanding
                            if shares_outstanding is not None else None),
        "terminal_year_fcfe": terminal_year_fcfe,
        "terminal_year_fcfe_derived": terminal_derived,
        "assumptions": {"cost_of_equity": cost_of_equity,
                        "terminal_growth": terminal_growth,
                        "n_forecast_years": n,
                        "shares_outstanding": shares_outstanding},
    }


def dcf_sensitivity(free_cash_flows, discount_rates, terminal_growths,
                    terminal_year_fcf=None):
    """Sensitivity table: enterprise value for each (discount, growth) pair.

    discount_rates / terminal_growths: non-empty lists of rates. Pairs
    with discount_rate <= terminal_growth are reported as None (terminal
    value undefined) instead of raising, so one bad cell does not kill
    the table — the None is the answer for that cell. Returns
    {"rows": [{"discount_rate", "terminal_growth", "enterprise_value"}]}.
    """
    flows = validate_series("free_cash_flows", free_cash_flows, min_length=1)
    discount_rates = validate_series("discount_rates", discount_rates, min_length=1)
    terminal_growths = validate_series("terminal_growths", terminal_growths, min_length=1)
    for r in discount_rates + terminal_growths:
        require_rate("rate", r)
    rows = []
    for r in discount_rates:
        for g in terminal_growths:
            if r <= g:
                rows.append({"discount_rate": r, "terminal_growth": g,
                             "enterprise_value": None})
                continue
            res = dcf_fcff(flows, r, g, terminal_year_fcf=terminal_year_fcf)
            rows.append({"discount_rate": r, "terminal_growth": g,
                         "enterprise_value": res["enterprise_value"]})
    return {"rows": rows}


# ---------------------------------------------------------------------------
# Bank valuation (Nepal note in the docstring)
# ---------------------------------------------------------------------------

def bank_valuation(price, book_value_per_share, roe, cost_of_equity,
                   terminal_growth, npl_ratio, cost_to_income,
                   capital_adequacy, min_capital_adequacy=None):
    """Bank valuation from caller-supplied inputs — nothing is fetched.

    Nepal note: NPL, ROE, PBV and the other inputs are taken exactly as
    given; this function holds NO NEPSE bank data and invents none.
    Any worked example must label its data source, or "synthetic".
    Justified P/BV (Gordon): (ROE - g) / (k - g). Implied price =
    justified P/BV * book value per share.
    Flags are stated rules, not judgements: npl_ratio > 0.05 is
    flagged "high NPL"; cost_to_income > 0.60 is flagged "high cost";
    when min_capital_adequacy is supplied (e.g. the regulator's
    minimum — verify against the current Nepal Rastra Bank directive
    before relying on it), capital_adequacy below it is flagged
    "below minimum CAR". When min_capital_adequacy is None the CAR
    check is skipped and reported as "not checked".
    """
    price = require_positive("price", price)
    book_value_per_share = require_positive("book_value_per_share", book_value_per_share)
    roe = require_finite("roe", roe)
    cost_of_equity = require_rate("cost_of_equity", cost_of_equity)
    terminal_growth = require_rate("terminal_growth", terminal_growth)
    npl_ratio = require_non_negative("npl_ratio", npl_ratio)
    cost_to_income = require_non_negative("cost_to_income", cost_to_income)
    capital_adequacy = require_non_negative("capital_adequacy", capital_adequacy)
    if cost_of_equity <= terminal_growth:
        raise ValueError("cost_of_equity must exceed terminal_growth")
    if min_capital_adequacy is not None:
        min_capital_adequacy = require_non_negative(
            "min_capital_adequacy", min_capital_adequacy)
    pbv = price / book_value_per_share
    justified_pbv = (roe - terminal_growth) / (cost_of_equity - terminal_growth)
    implied_price = justified_pbv * book_value_per_share
    flags = []
    if npl_ratio > 0.05:
        flags.append("high NPL (npl_ratio > 5%)")
    if cost_to_income > 0.60:
        flags.append("high cost-to-income (> 60%)")
    if min_capital_adequacy is None:
        car_check = "not checked (no minimum supplied)"
    elif capital_adequacy < min_capital_adequacy:
        flags.append("below minimum CAR")
        car_check = "below minimum"
    else:
        car_check = "at or above minimum"
    return {
        "pbv": pbv,
        "justified_pbv": justified_pbv,
        "implied_price": implied_price,
        "upside_to_implied": (implied_price / price - 1.0),
        "inputs": {"price": price, "book_value_per_share": book_value_per_share,
                   "roe": roe, "cost_of_equity": cost_of_equity,
                   "terminal_growth": terminal_growth, "npl_ratio": npl_ratio,
                   "cost_to_income": cost_to_income,
                   "capital_adequacy": capital_adequacy,
                   "min_capital_adequacy": min_capital_adequacy},
        "car_check": car_check,
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# VaR / CVaR — historical + parametric (performance.cvar is reused, not
# duplicated, for historical CVaR; montecarlo.simulated_var already
# covers the simulated case and is left alone)
# ---------------------------------------------------------------------------

def historical_var(returns, alpha=0.05):
    """Historical VaR: the alpha-quantile loss, as a positive fraction.

    alpha=0.05: the loss that is exceeded on the worst 5% of days.
    k = ceil(alpha * n); VaR = -sorted[k-1]. Returns a positive number
    for a loss tail (0.05 = a 5% one-period loss at the 95% level).
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    returns = validate_series("returns", returns, min_length=1)
    ordered = sorted(returns)
    k = max(1, math.ceil(alpha * len(ordered)))
    return -ordered[k - 1]


def parametric_var(returns, alpha=0.05):
    """Parametric (normal) VaR: -(mean + z_alpha * sd), positive = loss.

    z_alpha is the alpha-quantile of the standard normal (negative for
    alpha < 0.5). Assumes normally distributed returns — a stated
    assumption that fails on fat-tailed series, which is why the
    historical VaR sits next to it and the two should be compared.
    Sample stdev (n - 1).
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    returns = validate_series("returns", returns, min_length=2)
    return -(_mean(returns) + _norm_ppf(alpha) * _stdev(returns))


def parametric_cvar(returns, alpha=0.05):
    """Parametric (normal) CVaR / expected shortfall, positive = loss.

    For a normal with mean mu and sd sigma:
    ES = -(mu - sigma * phi(z_alpha) / alpha), where phi is the
    standard-normal density and z_alpha its alpha-quantile.
    Same normality caveat as parametric_var.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    returns = validate_series("returns", returns, min_length=2)
    mu, sd = _mean(returns), _stdev(returns)
    z = _norm_ppf(alpha)
    phi = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    return -(mu - sd * phi / alpha)


def risk_summary(returns, prices, risk_free=0.0, periods_per_year=252, alpha=0.05):
    """One dict of risk metrics, reusing performance.py (no duplication).

    Sharpe / Sortino / max drawdown come from performance.py; VaR/CVaR
    are computed here (historical via performance.cvar for the CVaR
    leg). Undefined ratios (zero volatility, no downside) are None
    with the reason recorded — never a made-up number.
    """
    returns = validate_series("returns", returns, min_length=2)
    prices = validate_series("prices", prices, min_length=2)
    out = {"sharpe": None, "sortino": None, "sharpe_undefined_reason": None,
           "sortino_undefined_reason": None}
    try:
        out["sharpe"] = sharpe_ratio(returns, risk_free, periods_per_year)
    except ValueError as exc:
        out["sharpe_undefined_reason"] = str(exc)
    try:
        out["sortino"] = sortino_ratio(returns, risk_free, periods_per_year)
    except ValueError as exc:
        out["sortino_undefined_reason"] = str(exc)
    out["max_drawdown"] = max_drawdown(prices)
    out["historical_var"] = historical_var(returns, alpha)
    out["historical_cvar"] = -_historical_cvar(returns, alpha)
    out["parametric_var"] = parametric_var(returns, alpha)
    out["parametric_cvar"] = parametric_cvar(returns, alpha)
    out["alpha"] = alpha
    return out


# ---------------------------------------------------------------------------
# Factors — momentum and mean reversion (time-series scores)
# ---------------------------------------------------------------------------

def momentum_factor(prices, lookback=126):
    """Momentum: trailing return over `lookback` periods.

    (price[-1] / price[-1 - lookback] - 1). Positive = price above
    where it was `lookback` periods ago. Uses only prices available
    at the decision date (the last price is the decision-date price).
    Prices must be positive.
    """
    prices = validate_series("prices", prices, min_length=2)
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    if len(prices) <= lookback:
        raise ValueError(
            f"need at least lookback + 1 prices ({lookback + 1}), got {len(prices)}")
    if any(p <= 0 for p in prices):
        raise ValueError("prices must all be positive for momentum_factor")
    return prices[-1] / prices[-1 - lookback] - 1.0


def mean_reversion_factor(prices, window=20):
    """Mean reversion: z-score of the last price vs its rolling window.

    z = (last - mean(window)) / sample sd(window). A high positive z
    means the price sits above its recent mean (reversion would be
    down); the signal is -z if you want "positive = expect up".
    Returns {"z_score", "signal" (-z), "mean", "std", "last"}.
    Uses only the last `window` prices available at the decision date.
    """
    prices = validate_series("prices", prices, min_length=2)
    if window < 2:
        raise ValueError("window must be >= 2")
    if len(prices) < window:
        raise ValueError(f"need at least window ({window}) prices, got {len(prices)}")
    if any(p <= 0 for p in prices):
        raise ValueError("prices must all be positive for mean_reversion_factor")
    w = prices[-window:]
    mean, sd = _mean(w), _stdev(w)
    if sd == 0.0:
        raise ValueError("zero volatility in window — z-score undefined")
    z = (prices[-1] - mean) / sd
    return {"z_score": z, "signal": -z, "mean": mean, "std": sd, "last": prices[-1]}


def cross_sectional_ranks(scores):
    """Percentile ranks (0..1) of a {name: score} dict, highest score = 1.

    Ties share the mean of their rank positions. Pure ranking of
    scores supplied by the caller — it does not look at prices.
    """
    if not scores:
        raise ValueError("scores must not be empty")
    for name, value in scores.items():
        require_finite(f"scores[{name!r}]", value)
    ordered = sorted(scores.items(), key=lambda kv: kv[1])
    n = len(ordered)
    ranks = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        # positions i..j (0-based); percentile = mean position / (n - 1)
        pct = (sum(range(i, j + 1)) / (j - i + 1)) / (n - 1) if n > 1 else 1.0
        for k in range(i, j + 1):
            ranks[ordered[k][0]] = pct
        i = j + 1
    return ranks


# ---------------------------------------------------------------------------
# Portfolio optimisation — mean-variance (wraps portfolio.py) and HRP
# ---------------------------------------------------------------------------

def mean_variance_weights(return_matrix, target_return=None, risk_free=0.0, l2=0.0):
    """Mean-variance weights from an asset return matrix.

    target_return=None: minimum-variance portfolio (portfolio.py).
    Otherwise the minimum-variance portfolio for that target return
    (efficient frontier, first point). risk_free is accepted for
    API symmetry with the max-Sharpe case and is currently unused —
    the max-Sharpe weights live in portfolio.max_sharpe_weights;
    passing a non-zero risk_free here raises instead of silently
    ignoring it. Weights sum to 1; they may be negative (short)
    unless cleaned with portfolio.clean_weights.
    """
    if risk_free != 0.0:
        raise ValueError(
            "risk_free != 0 is not supported here — use "
            "portfolio.max_sharpe_weights for the max-Sharpe portfolio")
    if target_return is None:
        return min_variance_weights(return_matrix, l2=l2)
    points = efficient_frontier(return_matrix, [target_return], l2=l2)
    return points[0][2]


def _cluster_order(covariance):
    """Quasi-diagonal asset order by single-linkage clustering.

    Distance d = sqrt((1 - rho) / 2) from the correlation matrix.
    Clusters merge by their closest cross-pair (single linkage); the
    order of a merged cluster is the concatenation of its parts, so
    similar assets end up adjacent. Returns a permutation of
    range(n). Textbook HRP seriation (Lopez de Prado 2016), written
    from scratch — no scipy.
    """
    n = len(covariance)
    corr = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            vi, vj = covariance[i][i], covariance[j][j]
            if vi <= 0 or vj <= 0:
                raise ValueError("covariance diagonal must be positive")
            corr[i][j] = covariance[i][j] / math.sqrt(vi * vj)
    dist = [[math.sqrt(max(0.0, (1.0 - corr[i][j]) / 2.0)) for j in range(n)]
            for i in range(n)]
    clusters = [[i] for i in range(n)]  # each cluster: ordered member list
    while len(clusters) > 1:
        best, best_d = None, None
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                d = min(dist[i][j] for i in clusters[a] for j in clusters[b])
                if best_d is None or d < best_d:
                    best, best_d = (a, b), d
        a, b = best
        clusters[a] = clusters[a] + clusters[b]
        del clusters[b]
    return clusters[0]


def _cluster_variance(covariance, members):
    """Variance of the inverse-variance-weighted sub-portfolio."""
    inv = [1.0 / covariance[i][i] for i in members]
    total = sum(inv)
    w = [v / total for v in inv]
    var = 0.0
    for a, i in enumerate(members):
        for b, j in enumerate(members):
            var += w[a] * w[b] * covariance[i][j]
    return var


def _hrp_allocate(covariance, order, weights):
    """Recursive bisection over the quasi-diagonal order."""
    if len(order) == 1:
        return
    half = len(order) // 2
    left, right = order[:half], order[half:]
    var_left = _cluster_variance(covariance, left)
    var_right = _cluster_variance(covariance, right)
    if var_left + var_right == 0.0:
        raise ValueError("zero cluster variance — HRP undefined")
    alloc_left = 1.0 - var_left / (var_left + var_right)
    for i in left:
        weights[i] *= alloc_left
    for i in right:
        weights[i] *= (1.0 - alloc_left)
    _hrp_allocate(covariance, left, weights)
    _hrp_allocate(covariance, right, weights)


def hrp_weights(covariance):
    """Hierarchical Risk Parity weights (Lopez de Prado 2016).

    covariance: n x n symmetric matrix with a positive diagonal.
    Steps: (1) quasi-diagonal order by single-linkage clustering on
    correlation distance; (2) recursive bisection, splitting each
    cluster in the order and allocating between halves in inverse
    proportion to their inverse-variance cluster variances.
    Weights are long-only and sum to 1. Unlike mean-variance, HRP
    needs no matrix inversion, so it does not blow up on a
    near-singular covariance — that robustness is the point, and the
    price is that expected returns are ignored entirely (HRP is a
    risk-only allocation; say so when you use it).
    No look-ahead: the covariance is an input as of the decision date.
    """
    if not covariance:
        raise ValueError("covariance must not be empty")
    n = len(covariance)
    for i, row in enumerate(covariance):
        if len(row) != n:
            raise ValueError("covariance must be square")
        for j, value in enumerate(row):
            require_finite(f"covariance[{i}][{j}]", value)
    for i in range(n):
        for j in range(n):
            if abs(covariance[i][j] - covariance[j][i]) > 1e-9:
                raise ValueError("covariance must be symmetric")
    if any(covariance[i][i] <= 0 for i in range(n)):
        raise ValueError("covariance diagonal must be positive")
    order = _cluster_order(covariance)
    weights = [1.0] * n
    _hrp_allocate(covariance, order, weights)
    total = sum(weights)
    return [w / total for w in weights]


# ---------------------------------------------------------------------------
# Sector rotation — relative-strength rotation with stated rules
# ---------------------------------------------------------------------------

def sector_relative_strength(returns_by_sector, lookback):
    """Relative strength per sector over the trailing `lookback` returns.

    For each sector: trailing compounded return over the last
    `lookback` periods, minus the equal-weighted mean trailing return
    across sectors (the relative-strength score). Returns
    {"scores": {sector: score}, "ranking": [sectors best first],
     "trailing_returns": {sector: trailing return}}.
    Rules, stated: the score is trailing return only — no momentum
    smoothing, no volatility adjustment; the caller decides the
    rebalance frequency. Uses only returns available at the decision
    date (the last return is the decision-date return).
    """
    if not returns_by_sector:
        raise ValueError("returns_by_sector must not be empty")
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    trailing = {}
    for sector, rets in returns_by_sector.items():
        rets = validate_series(f"returns_by_sector[{sector!r}]", rets, min_length=1)
        if len(rets) < lookback:
            raise ValueError(
                f"sector {sector!r} needs at least lookback ({lookback}) "
                f"returns, got {len(rets)}")
        value = 1.0
        for r in rets[-lookback:]:
            value *= (1.0 + r)
        trailing[sector] = value - 1.0
    mean_trailing = _mean(list(trailing.values()))
    scores = {s: trailing[s] - mean_trailing for s in trailing}
    ranking = sorted(scores, key=lambda s: scores[s], reverse=True)
    return {"scores": scores, "ranking": ranking, "trailing_returns": trailing}


def sector_rotation_allocation(returns_by_sector, lookback, top_n=2):
    """Rotation allocation: equal weight in the top_n sectors by score.

    Rules, stated in full: rank sectors by relative strength
    (sector_relative_strength); hold the top_n equal-weighted; every
    other sector gets 0. Rebalance is the caller's decision — this
    function computes one allocation as of the decision date and says
    nothing about when to trade it. top_n must be >= 1 and <= the
    number of sectors.
    """
    if top_n < 1:
        raise ValueError("top_n must be >= 1")
    if top_n > len(returns_by_sector):
        raise ValueError(
            f"top_n ({top_n}) exceeds the number of sectors "
            f"({len(returns_by_sector)})")
    result = sector_relative_strength(returns_by_sector, lookback)
    allocation = {s: (1.0 / top_n if s in result["ranking"][:top_n] else 0.0)
                  for s in returns_by_sector}
    return {"allocation": allocation, "ranking": result["ranking"],
            "scores": result["scores"],
            "trailing_returns": result["trailing_returns"]}


# ---------------------------------------------------------------------------
# Fold-ins from the Phase 0a-expanded teardowns (see module docstring).
# All reimplemented from textbook definitions; no outside code copied.
# ---------------------------------------------------------------------------

def _solve_linear(a, b):
    """Solve a x = b by Gauss-Jordan with partial pivoting.

    Textbook elimination, written for this module (portfolio.py has
    its own copy for return-matrix optimisation; this one serves the
    covariance-input optimisers below). Raises on a singular matrix.
    """
    n = len(a)
    aug = [list(row) + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[piv][col]) < 1e-12:
            raise ValueError("singular matrix — covariance is degenerate")
        aug[col], aug[piv] = aug[piv], aug[col]
        div = aug[col][col]
        aug[col] = [v / div for v in aug[col]]
        for r in range(n):
            if r != col and aug[r][col] != 0.0:
                f = aug[r][col]
                aug[r] = [v - f * w for v, w in zip(aug[r], aug[col])]
    return [aug[i][n] for i in range(n)]


def max_diversification_weights(covariance):
    """Maximum-diversification weights (skfolio formulation, textbook).

    Maximises the diversification ratio (w' sigma) / sqrt(w' Sigma w);
    the unconstrained solution is w proportional to Sigma^-1 sigma,
    where sigma is the vector of asset volatilities. Long-only by
    construction of the fallback: if the unconstrained solution has
    any negative weight (or a non-positive sum), the function falls
    back to inverse-volatility weights and says so in the result —
    it never silently returns a short position from a "long-only"
    optimiser. Weights sum to 1.
    """
    if not covariance:
        raise ValueError("covariance must not be empty")
    n = len(covariance)
    for i, row in enumerate(covariance):
        if len(row) != n:
            raise ValueError("covariance must be square")
        for j, value in enumerate(row):
            require_finite(f"covariance[{i}][{j}]", value)
    if any(covariance[i][i] <= 0 for i in range(n)):
        raise ValueError("covariance diagonal must be positive")
    vols = [math.sqrt(covariance[i][i]) for i in range(n)]
    try:
        raw = _solve_linear([list(row) for row in covariance], vols)
    except ValueError:
        raw = None
    if raw is None or sum(raw) <= 0 or any(v < 0 for v in raw):
        inv = [1.0 / v for v in vols]
        total = sum(inv)
        return {"weights": [v / total for v in inv],
                "method": "fallback: inverse-volatility "
                          "(unconstrained solution was not long-only)"}
    total = sum(raw)
    return {"weights": [v / total for v in raw],
            "method": "max-diversification (Sigma^-1 sigma, normalised)"}


def entropic_var(returns, alpha=0.05):
    """Entropic VaR (EVaR): inf_z (1/z) * ln(E[exp(z * L)] / alpha).

    L = -return is the loss. EVaR is the tightest coherent upper bound
    on VaR and CVaR at the same level that follows from the
    Chernoff bound, so EVaR >= VaR and EVaR >= CVaR on the same data —
    the tests assert both orderings. Minimised by golden-section
    search over log z (the objective is convex in z); the
    log-sum-exp trick keeps exp() from overflowing. Returns a
    positive number for a loss tail. skfolio's risk-measure library
    carries EVaR; this is a from-scratch implementation of the
    textbook definition (Ahmadi-Javid 2012).
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    returns = validate_series("returns", returns, min_length=2)
    losses = [-r for r in returns]

    def _objective(log_z):
        z = math.exp(log_z)
        scaled = [z * L for L in losses]
        m = max(scaled)
        log_mean_exp = m + math.log(
            sum(math.exp(x - m) for x in scaled) / len(scaled))
        return (log_mean_exp - math.log(alpha)) / z

    lo, hi = math.log(1e-4), math.log(1e4)
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = lo, hi
    c, d = b - gr * (b - a), a + gr * (b - a)
    fc, fd = _objective(c), _objective(d)
    for _ in range(200):
        if abs(b - a) < 1e-10:
            break
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - gr * (b - a)
            fc = _objective(c)
        else:
            a, c, fc = c, d, fd
            d = a + gr * (b - a)
            fd = _objective(d)
    return _objective((a + b) / 2.0)


# --- QuantInvestStrats perf-stats: Sharpe inference, multiple testing,
# turnover, transaction costs ------------------------------------------

def sharpe_standard_error(returns, periods_per_year=252):
    """Lo (2002) asymptotic standard error of the annualised Sharpe.

    SE = sqrt(periods) * sqrt((1 + 0.5 * SR_period^2) / T), with
    SR_period the per-period Sharpe (mean/sd) and T the number of
    returns. Assumes IID normal returns — a stated assumption: on
    heavy-tailed or autocorrelated returns this SE is too small, so
    the interval below is a lower bound on honest uncertainty, not a
    guarantee. Raises on zero volatility (Sharpe undefined).
    """
    returns = validate_series("returns", returns, min_length=2)
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    sd = _stdev(returns)
    if sd == 0.0:
        raise ValueError("zero return volatility — Sharpe undefined")
    sr_period = _mean(returns) / sd
    return math.sqrt(periods_per_year) * math.sqrt(
        (1.0 + 0.5 * sr_period * sr_period) / len(returns))


def sharpe_confidence_interval(returns, confidence=0.95, periods_per_year=252):
    """Annualised Sharpe with a normal-approximation confidence interval.

    Point estimate from performance.sharpe_ratio; half-width is
    z * SE with z the (1 + confidence) / 2 normal quantile and SE
    from sharpe_standard_error (Lo 2002, IID-normal assumption — see
    its docstring). A wide interval that covers 0 is the honest
    answer on a short history: it says the Sharpe is not significant.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    returns = validate_series("returns", returns, min_length=2)
    point = sharpe_ratio(returns, 0.0, periods_per_year)
    se = sharpe_standard_error(returns, periods_per_year)
    z = _norm_ppf((1.0 + confidence) / 2.0)
    return {"sharpe": point, "standard_error": se, "confidence": confidence,
            "lower": point - z * se, "upper": point + z * se,
            "significant": (point - z * se > 0) or (point + z * se < 0),
            "method": "Lo (2002) asymptotic SE, IID-normal assumption"}


def bonferroni_adjust(p_values, alpha=0.05):
    """Bonferroni correction: adjusted p = min(1, p * m).

    Family-wise error control for m simultaneous tests. Conservative:
    it rejects only p <= alpha / m. p_values must be in [0, 1].
    Returns {"adjusted", "reject", "method"}.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    p_values = validate_series("p_values", p_values, min_length=1)
    if any(p < 0.0 or p > 1.0 for p in p_values):
        raise ValueError("p_values must be in [0, 1]")
    m = len(p_values)
    adjusted = [min(1.0, p * m) for p in p_values]
    return {"adjusted": adjusted,
            "reject": [a <= alpha for a in adjusted],
            "method": f"Bonferroni (m={m})"}


def benjamini_hochberg(p_values, alpha=0.05):
    """Benjamini-Hochberg correction: false-discovery-rate control.

    Adjusted p-values are p * m / rank, enforced non-decreasing when
    read from the largest p downwards; rejection follows the step-up
    rule (reject the largest k with p_(k) <= k / m * alpha, and all
    smaller). Less conservative than Bonferroni when many tests are
    run — which is why the method is named in the result, so a reader
    can tell which error rate was controlled.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    p_values = validate_series("p_values", p_values, min_length=1)
    if any(p < 0.0 or p > 1.0 for p in p_values):
        raise ValueError("p_values must be in [0, 1]")
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    sorted_p = [p_values[i] for i in order]
    raw = [sorted_p[k] * m / (k + 1) for k in range(m)]
    adj_sorted = [0.0] * m
    running = 1.0
    for k in range(m - 1, -1, -1):
        running = min(running, raw[k])
        adj_sorted[k] = min(1.0, running)
    adjusted = [0.0] * m
    for k, i in enumerate(order):
        adjusted[i] = adj_sorted[k]
    # Step-up rejection: largest k with p_(k) <= k/m * alpha.
    k_max = -1
    for k in range(m):
        if sorted_p[k] <= (k + 1) / m * alpha:
            k_max = k
    reject_sorted = [k <= k_max for k in range(m)]
    reject = [False] * m
    for k, i in enumerate(order):
        reject[i] = reject_sorted[k]
    return {"adjusted": adjusted, "reject": reject,
            "method": f"Benjamini-Hochberg (m={m}, FDR)"}


def turnover(weights_history):
    """Mean one-way turnover across rebalances.

    weights_history: list of weight vectors (same length, each
    summing to 1). Turnover at one rebalance is 0.5 * sum|w_t - w_{t-1}|
    (one-way: buying half the change, selling half). The mean over
    rebalances is returned, plus the per-rebalance values. A
    buy-and-hold history has turnover 0.
    """
    if len(weights_history) < 2:
        raise ValueError("weights_history needs at least 2 weight vectors")
    n = len(weights_history[0])
    for i, w in enumerate(weights_history):
        w = validate_series(f"weights_history[{i}]", w, min_length=1)
        if len(w) != n:
            raise ValueError("all weight vectors must have the same length")
        if abs(sum(w) - 1.0) > 1e-6:
            raise ValueError(f"weights_history[{i}] must sum to 1")
    per_step = []
    for prev, cur in itertools.pairwise(weights_history):
        per_step.append(0.5 * sum(abs(c - p) for p, c in zip(prev, cur)))
    return {"mean_turnover": _mean(per_step), "per_step": per_step}


def net_return_after_costs(gross_return, turnover_value, cost_per_unit):
    """Net return = gross return - turnover * cost per unit traded.

    turnover_value is one-way turnover (see turnover()); cost_per_unit
    is the round-trip-ish cost per unit of turnover as a fraction
    (e.g. 0.0036 for a 0.36% broker slab). Explicit costs, stated in
    the open: a strategy whose edge is smaller than its costs is a
    losing strategy, and this function is where that is checked.
    """
    gross_return = require_finite("gross_return", gross_return)
    turnover_value = require_non_negative("turnover_value", turnover_value)
    cost_per_unit = require_non_negative("cost_per_unit", cost_per_unit)
    return gross_return - turnover_value * cost_per_unit


# --- qlib Alpha158: canonical factor baseline, reimplemented ----------

def alpha158_baseline_factors(prices):
    """A small canonical factor set, after qlib's Alpha158 (MIT).

    Alpha158 is the citable baseline factor set in microsoft/qlib
    (HEAD 5435523); these six factors reimplement its price-based
    members from their published definitions — no qlib code is used.
    Each factor at index t uses only prices up to t (walk-forward
    safe by construction): ROC over 5/10/20 bars, 20-bar return
    volatility (sample sd), price vs its 20-bar mean, and the 1-bar
    return. Rows start at index 20 (the longest lookback). Returns
    a list of {"index", "roc_5", "roc_10", "roc_20", "vol_20",
    "ma_ratio_20", "ret_1"} dicts. Volume-based Alpha158 members are
    NOT included (no volume input here) — the set is labelled a
    baseline subset, not the full 158.
    """
    prices = validate_series("prices", prices, min_length=21)
    if any(p <= 0 for p in prices):
        raise ValueError("prices must all be positive for alpha158_baseline_factors")
    rows = []
    for t in range(20, len(prices)):
        window_rets = [prices[i] / prices[i - 1] - 1.0
                       for i in range(t - 19, t + 1)]
        rows.append({
            "index": t,
            "roc_5": prices[t] / prices[t - 5] - 1.0,
            "roc_10": prices[t] / prices[t - 10] - 1.0,
            "roc_20": prices[t] / prices[t - 20] - 1.0,
            "vol_20": _stdev(window_rets),
            "ma_ratio_20": prices[t] / _mean(prices[t - 19:t + 1]) - 1.0,
            "ret_1": prices[t] / prices[t - 1] - 1.0,
        })
    return rows


# --- EigenLedger concept: one friendly entry point ----------------------

def portfolio_analysis(prices, risk_free=0.0, periods_per_year=252):
    """One friendly entry point over the specialist functions.

    Concept only, after EigenLedger (Apache-2.0): a single call that
    runs the standard analysis — returns from prices, the risk
    summary, momentum and mean-reversion — instead of making the
    caller assemble five calls. It adds no new maths: every number
    comes from a function in this module or portfolio.py, and the
    result says which. Momentum / mean-reversion are None when the
    series is too short for their lookbacks (20), rather than
    computed on too little data. Prices are caller-supplied.
    """
    from .portfolio import returns_from_prices

    prices = validate_series("prices", prices, min_length=2)
    if any(p <= 0 for p in prices):
        raise ValueError("prices must all be positive for portfolio_analysis")
    rets = returns_from_prices(prices)
    out = {"n_prices": len(prices), "returns": rets,
           "risk_summary": risk_summary(rets, prices, risk_free, periods_per_year),
           "momentum_20": (momentum_factor(prices, 20) if len(prices) > 20 else None),
           "mean_reversion_20": (mean_reversion_factor(prices, 20)
                                 if len(prices) >= 20 else None),
           "sources": {"risk_summary": "quant.risk_summary",
                       "momentum_20": "quant.momentum_factor",
                       "mean_reversion_20": "quant.mean_reversion_factor"}}
    return out
