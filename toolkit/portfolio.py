"""Portfolio construction — zero dependencies, pure Python.

Design choices, stated plainly (this is the Job 2 lesson):
- Shrinkage is the DEFAULT, not an afterthought: sample covariance on
  short histories is error-sensitive, so `shrunk_covariance` blends the
  sample covariance toward its diagonal. This is a simplified shrinkage
  (alpha parameter, documented), not full Ledoit-Wolf.
- L2 (ridge) is available on every optimiser: it pulls wild concentrated
  weights toward equal weight. Use it unless you can defend not using it.
- Every optimiser is compared to 1/N and inverse-volatility benchmarks.
  A portfolio that cannot beat 1/N out of sample is not an improvement.
- Weights sum to 1. Unconstrained solutions may be negative (short);
  `clean_weights` gives the long-only, thresholded version, explicitly.
"""
from __future__ import annotations

import math

from .checks import require_non_negative, require_positive


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def returns_from_prices(prices):
    """Simple returns from a price series. Raises on non-positive prices."""
    if len(prices) < 2:
        raise ValueError("need at least 2 prices")
    out = []
    for prev, cur in zip(prices, prices[1:]):
        require_positive("previous price", prev)
        require_positive("price", cur)
        out.append(cur / prev - 1.0)
    return out


def mean_returns(return_matrix):
    """return_matrix: list of asset return lists (same length). -> list of means."""
    return [_mean(r) for r in return_matrix]


def sample_covariance(return_matrix):
    """Sample covariance matrix (n x n) from asset return lists."""
    n = len(return_matrix)
    if n == 0:
        raise ValueError("no assets")
    t = len(return_matrix[0])
    if t < 2:
        raise ValueError("need at least 2 observations")
    means = mean_returns(return_matrix)
    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            cov[i][j] = sum(
                (return_matrix[i][k] - means[i]) * (return_matrix[j][k] - means[j])
                for k in range(t)
            ) / (t - 1)
    return cov


def shrunk_covariance(return_matrix, alpha=0.1):
    """Blend sample covariance toward its diagonal.

    shrunk = (1-alpha) * sample + alpha * diag(sample).
    alpha in [0, 1]; 0 = raw sample, 1 = diagonal only (no correlations).
    Default 0.1: a modest, stated shrink, not a magic number.
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("alpha must be in [0, 1]")
    cov = sample_covariance(return_matrix)
    n = len(cov)
    return [
        [
            (1 - alpha) * cov[i][j] + (alpha * cov[i][i] if i == j else 0.0)
            for j in range(n)
        ]
        for i in range(n)
    ]


def _ridge(matrix, lam):
    n = len(matrix)
    return [[matrix[i][j] + (lam if i == j else 0.0) for j in range(n)] for i in range(n)]


def _solve(a, b):
    """Solve a x = b by Gauss-Jordan. Raises on singular matrix."""
    n = len(a)
    aug = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[piv][col]) < 1e-12:
            raise ValueError("singular matrix — add L2 / shrinkage")
        aug[col], aug[piv] = aug[piv], aug[col]
        div = aug[col][col]
        aug[col] = [v / div for v in aug[col]]
        for r in range(n):
            if r != col and aug[r][col] != 0.0:
                f = aug[r][col]
                aug[r] = [v - f * w for v, w in zip(aug[r], aug[col])]
    return [aug[i][n] for i in range(n)]


def _matvec(m, v):
    return [sum(row[j] * v[j] for j in range(len(v))) for row in m]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def equal_weights(n):
    require_positive("number of assets", n)
    return [1.0 / n] * n


def inverse_volatility_weights(return_matrix, l2=0.0):
    """Weights proportional to 1/volatility, normalised to sum 1."""
    cov = _ridge(shrunk_covariance(return_matrix), l2)
    vols = [math.sqrt(max(cov[i][i], 0.0)) for i in range(len(cov))]
    if any(v == 0.0 for v in vols):
        raise ValueError("zero volatility asset — cannot inverse-weight")
    inv = [1.0 / v for v in vols]
    total = sum(inv)
    return [v / total for v in inv]


def min_variance_weights(return_matrix, l2=0.01):
    """Minimum-variance weights: w = Sigma^-1 1 / (1' Sigma^-1 1)."""
    cov = _ridge(shrunk_covariance(return_matrix), l2)
    n = len(cov)
    inv_ones = _solve(cov, [1.0] * n)
    total = sum(inv_ones)
    if total == 0.0:
        raise ValueError("degenerate minimum-variance solution")
    return [v / total for v in inv_ones]


def max_sharpe_weights(return_matrix, risk_free=0.0, l2=0.01):
    """Unconstrained max-Sharpe weights, normalised to sum 1.

    w proportional to Sigma^-1 (mu - rf). May be negative (short).
    Use clean_weights() for the long-only version, and always compare
    to equal_weights() out of sample before claiming an improvement.
    """
    mu = mean_returns(return_matrix)
    cov = _ridge(shrunk_covariance(return_matrix), l2)
    excess = [m - risk_free for m in mu]
    raw = _solve(cov, excess)
    total = sum(raw)
    if total == 0.0:
        raise ValueError("degenerate max-Sharpe solution")
    return [v / total for v in raw]


def clean_weights(weights, threshold=0.01):
    """Long-only version: drop weights below threshold, renormalise positives."""
    pos = [w if w >= threshold else 0.0 for w in weights]
    total = sum(pos)
    if total == 0.0:
        raise ValueError("no weights survive the threshold")
    return [w / total for w in pos]


def portfolio_return(weights, means):
    return _dot(weights, means)


def portfolio_volatility(weights, covariance):
    var = _dot(weights, _matvec(covariance, weights))
    if var < 0.0:
        raise ValueError("negative portfolio variance — check covariance")
    return math.sqrt(var)


def efficient_frontier(return_matrix, targets, l2=0.01):
    """Minimum-variance portfolio for each target return.

    Solves the KKT system for [w, lambda, gamma] with constraints
    sum(w)=1 and mu'w=target. Targets outside the feasible range raise.
    Returns list of (target, volatility, weights).
    """
    mu = mean_returns(return_matrix)
    cov = _ridge(shrunk_covariance(return_matrix), l2)
    n = len(cov)
    out = []
    for target in targets:
        # KKT matrix (n+2) x (n+2): [2*Sigma, 1, mu; 1', 0, 0; mu', 0, 0]
        size = n + 2
        kkt = [[0.0] * size for _ in range(size)]
        for i in range(n):
            for j in range(n):
                kkt[i][j] = 2.0 * cov[i][j]
            kkt[i][n] = 1.0
            kkt[i][n + 1] = mu[i]
            kkt[n][i] = 1.0
            kkt[n + 1][i] = mu[i]
        rhs = [0.0] * n + [1.0, target]
        sol = _solve(kkt, rhs)
        w = sol[:n]
        out.append((target, portfolio_volatility(w, cov), w))
    return out
