"""Engine 1.6 tests — quant library.

Every numeric expectation below was hand-computed (independently of the
implementation) before the tests were written; the derivation is in the
comment above each test. Synthetic data is labelled synthetic.
"""
import math

import pytest

from toolkit import quant
from toolkit.portfolio import sample_covariance

# Fixed 20-observation return series used by the VaR fixtures.
RETS_20 = [-0.05, -0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05,
           -0.04, -0.025, 0.015, -0.015, 0.005, 0.025, -0.035, 0.045,
           -0.045, 0.035]


# --- DCF -------------------------------------------------------------------

def test_dcf_fcff_hand_computed():
    # FCFs [100, 110, 121], r=10%, g=3%, terminal FCF 130 (given).
    # PV flows = 100/1.1 + 110/1.21 + 121/1.331 = 272.72727272727275
    # TV = 130 / 0.07 = 1857.1428571428569; PV(TV) = TV / 1.331
    #   = 1395.298915960073; EV = 1668.0261886873457.
    res = quant.dcf_fcff([100.0, 110.0, 121.0], 0.10, 0.03,
                         terminal_year_fcf=130.0)
    assert res["pv_explicit_fcfs"] == pytest.approx(272.72727272727275)
    assert res["terminal_value"] == pytest.approx(1857.1428571428569)
    assert res["pv_terminal_value"] == pytest.approx(1395.298915960073)
    assert res["enterprise_value"] == pytest.approx(1668.0261886873457)
    assert res["equity_value"] == pytest.approx(1668.0261886873457)  # no debt
    assert res["value_per_share"] is None
    assert res["terminal_year_fcf_derived"] is False


def test_dcf_fcff_equity_bridge_and_per_share():
    # Same EV 1668.0261886873457; net debt 68.0261886873457 leaves
    # equity exactly 1600; 100 shares -> 16.00 per share.
    res = quant.dcf_fcff([100.0, 110.0, 121.0], 0.10, 0.03,
                         terminal_year_fcf=130.0, net_debt=68.0261886873457,
                         shares_outstanding=100)
    assert res["equity_value"] == pytest.approx(1600.0)
    assert res["value_per_share"] == pytest.approx(16.0)


def test_dcf_fcff_derives_terminal_fcf_when_omitted():
    # Terminal FCF = 121 * 1.03 = 124.63; TV = 124.63 / 0.07 = 1780.4286.
    res = quant.dcf_fcff([100.0, 110.0, 121.0], 0.10, 0.03)
    assert res["terminal_year_fcf"] == pytest.approx(124.63)
    assert res["terminal_year_fcf_derived"] is True
    assert res["terminal_value"] == pytest.approx(124.63 / 0.07)


def test_dcf_fcfe_hand_computed():
    # FCFE [50, 55, 60], ke=12%, g=2%, terminal FCFE 61.2 (given).
    # PV = 50/1.12 + 55/1.2544 + 60/1.404928 = 131.1953352769679
    # TV = 61.2 / 0.10 = 612; PV(TV) = 612 / 1.404928 = 435.60951166180746
    # Equity value = 566.8048469387754.
    res = quant.dcf_fcfe([50.0, 55.0, 60.0], 0.12, 0.02,
                         terminal_year_fcfe=61.2, shares_outstanding=100)
    assert res["pv_explicit_fcfes"] == pytest.approx(131.1953352769679)
    assert res["terminal_value"] == pytest.approx(612.0)
    assert res["pv_terminal_value"] == pytest.approx(435.60951166180746)
    assert res["equity_value"] == pytest.approx(566.8048469387754)
    assert res["value_per_share"] == pytest.approx(5.668048469387754)


def test_dcf_rejects_growth_at_or_above_discount():
    with pytest.raises(ValueError):
        quant.dcf_fcff([100.0], 0.05, 0.05, terminal_year_fcf=100.0)
    with pytest.raises(ValueError):
        quant.dcf_fcfe([100.0], 0.03, 0.05, terminal_year_fcfe=100.0)
    with pytest.raises(ValueError):
        quant.dcf_fcff([], 0.10, 0.03)


def test_dcf_sensitivity_table():
    res = quant.dcf_sensitivity([100.0, 110.0, 121.0], [0.08, 0.10],
                                [0.03, 0.10], terminal_year_fcf=130.0)
    assert len(res["rows"]) == 4
    by_cell = {(r["discount_rate"], r["terminal_growth"]): r["enterprise_value"]
               for r in res["rows"]}
    assert by_cell[(0.10, 0.03)] == pytest.approx(1668.0261886873457)
    # r <= g: terminal value undefined -> None, not a crash.
    assert by_cell[(0.08, 0.10)] is None
    assert by_cell[(0.10, 0.10)] is None
    # Higher discount rate lowers value at fixed growth.
    assert by_cell[(0.08, 0.03)] > by_cell[(0.10, 0.03)]


# --- Bank valuation ----------------------------------------------------------

def test_bank_valuation_hand_computed_synthetic():
    # Synthetic example (labelled): price 100, BVPS 50 -> P/BV 2.0.
    # Justified P/BV = (0.15 - 0.03) / (0.10 - 0.03) = 1.714285714285714.
    # Implied price = 1.714285714285714 * 50 = 85.71428571428571.
    res = quant.bank_valuation(price=100.0, book_value_per_share=50.0,
                               roe=0.15, cost_of_equity=0.10,
                               terminal_growth=0.03, npl_ratio=0.08,
                               cost_to_income=0.65, capital_adequacy=0.09,
                               min_capital_adequacy=0.11)
    assert res["pbv"] == pytest.approx(2.0)
    assert res["justified_pbv"] == pytest.approx(1.714285714285714)
    assert res["implied_price"] == pytest.approx(85.71428571428571)
    assert res["upside_to_implied"] == pytest.approx(85.71428571428571 / 100 - 1)
    assert "high NPL (npl_ratio > 5%)" in res["flags"]
    assert "high cost-to-income (> 60%)" in res["flags"]
    assert "below minimum CAR" in res["flags"]
    assert res["car_check"] == "below minimum"


def test_bank_valuation_clean_bank_no_flags():
    res = quant.bank_valuation(price=50.0, book_value_per_share=50.0,
                               roe=0.12, cost_of_equity=0.10,
                               terminal_growth=0.03, npl_ratio=0.02,
                               cost_to_income=0.45, capital_adequacy=0.13)
    assert res["flags"] == []
    assert res["car_check"] == "not checked (no minimum supplied)"
    assert res["justified_pbv"] == pytest.approx((0.12 - 0.03) / 0.07)


def test_bank_valuation_rejects_bad_inputs():
    with pytest.raises(ValueError):
        quant.bank_valuation(price=100.0, book_value_per_share=50.0, roe=0.15,
                             cost_of_equity=0.03, terminal_growth=0.03,
                             npl_ratio=0.02, cost_to_income=0.5,
                             capital_adequacy=0.12)


# --- VaR / CVaR --------------------------------------------------------------

def test_norm_ppf_known_values():
    assert quant._norm_ppf(0.05) == pytest.approx(-1.6448536269514722, abs=1e-8)
    assert quant._norm_ppf(0.975) == pytest.approx(1.959963984540054, abs=1e-8)
    assert quant._norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)
    with pytest.raises(ValueError):
        quant._norm_ppf(1.0)


def test_historical_var_hand_computed():
    # Sorted RETS_20: worst is -0.05, second worst -0.045.
    # alpha=0.05 -> k = ceil(1.0) = 1 -> VaR = 0.05.
    # alpha=0.10 -> k = ceil(2.0) = 2 -> VaR = 0.045.
    assert quant.historical_var(RETS_20, 0.05) == pytest.approx(0.05)
    assert quant.historical_var(RETS_20, 0.10) == pytest.approx(0.045)
    with pytest.raises(ValueError):
        quant.historical_var(RETS_20, 1.0)


def test_parametric_var_and_cvar_hand_computed():
    # mean = 0.00025, sample sd = 0.03180843152042354.
    # Parametric VaR95 = -(0.00025 - 1.6448536269514722 * sd) = 0.0520702139540062.
    # Parametric CVaR95 = 0.06536165908390061 (normal ES formula).
    assert quant.parametric_var(RETS_20, 0.05) == pytest.approx(0.0520702139540062)
    assert quant.parametric_cvar(RETS_20, 0.05) == pytest.approx(0.06536165908390061)
    # Parametric exceeds historical here (fat left tail is mild) — the
    # comparison, not agreement, is the point.
    assert quant.parametric_var(RETS_20, 0.05) > quant.historical_var(RETS_20, 0.05)


def test_risk_summary_reuses_performance():
    # Prices [100, 110, 105, 120, 90, 100]: max drawdown = (120-90)/120 = 0.25.
    # Sharpe (annualised, rf=0) on RETS_20: mean 0.00025, sample sd
    # 0.03180843152042354 -> 0.12476650928382657. Sortino: downside
    # deviation sqrt(mean(min(0, r)^2)) -> 0.18114220932736796.
    res = quant.risk_summary(RETS_20, [100, 110, 105, 120, 90, 100])
    assert res["max_drawdown"] == pytest.approx(0.25)
    assert res["sharpe"] == pytest.approx(0.12476650928382657)
    assert res["sortino"] == pytest.approx(0.18114220932736796)
    assert res["historical_var"] == pytest.approx(0.05)
    assert res["historical_cvar"] == pytest.approx(0.05)  # worst 5% is just -0.05
    assert res["parametric_var"] == pytest.approx(0.0520702139540062)
    assert res["parametric_cvar"] == pytest.approx(0.06536165908390061)


def test_risk_summary_undefined_sharpe_is_none_not_crash():
    res = quant.risk_summary([0.01, 0.01, 0.01], [100, 101, 102])
    assert res["sharpe"] is None
    assert res["sharpe_undefined_reason"] is not None


# --- Factors -----------------------------------------------------------------

def test_momentum_factor_hand_computed():
    # Prices rise 100 -> 110 over 5 steps: momentum = 110/100 - 1 = 0.10.
    assert quant.momentum_factor([100, 101, 102, 103, 104, 105, 110], 6) \
        == pytest.approx(0.10)
    assert quant.momentum_factor([110, 105, 100], 2) == pytest.approx(-0.0909090909)
    with pytest.raises(ValueError):
        quant.momentum_factor([100, 101], 5)  # too short


def test_mean_reversion_factor_hand_computed():
    # Window [100..106]: mean 103, sample sd 1.8257418583505538,
    # z = (106 - 103) / sd = 1.6431676725154982; signal = -z.
    res = quant.mean_reversion_factor(
        [100, 102, 101, 103, 102, 104, 103, 105, 104, 106], window=10)
    assert res["z_score"] == pytest.approx(1.6431676725154982)
    assert res["signal"] == pytest.approx(-1.6431676725154982)
    assert res["mean"] == pytest.approx(103.0)
    assert res["last"] == pytest.approx(106.0)
    with pytest.raises(ValueError):
        quant.mean_reversion_factor([100, 100, 100, 100], window=4)  # zero sd


def test_cross_sectional_ranks():
    assert quant.cross_sectional_ranks({"a": 1.0, "b": 2.0, "c": 3.0}) == {
        "a": 0.0, "b": 0.5, "c": 1.0}
    # Ties share the mean of their positions: a,b tie for positions 0,1.
    ranks = quant.cross_sectional_ranks({"a": 1.0, "b": 1.0, "c": 3.0})
    assert ranks["a"] == pytest.approx(0.25)
    assert ranks["b"] == pytest.approx(0.25)
    assert ranks["c"] == pytest.approx(1.0)
    with pytest.raises(ValueError):
        quant.cross_sectional_ranks({})


# --- Optimisation ------------------------------------------------------------

def _hadamard_returns():
    """5 assets x 8 obs with exactly diagonal sample covariance.

    Rows of the Sylvester Hadamard-8 matrix (excluding the all-ones
    row) are mutually orthogonal and sum to zero, so cross-covariances
    are exactly 0 and each asset's sample variance is 8*s^2/7 with
    s = sqrt(var * 7/8). Target variances: [0.04, 0.09, 0.01, 0.16,
    0.0625]; means [0.01, 0.005, 0.02, -0.005, 0.015].
    """
    rows = [
        [1, -1, 1, -1, 1, -1, 1, -1],
        [1, 1, -1, -1, 1, 1, -1, -1],
        [1, -1, -1, 1, 1, -1, -1, 1],
        [1, 1, 1, 1, -1, -1, -1, -1],
        [1, -1, 1, -1, -1, 1, -1, 1],
    ]
    variances = [0.04, 0.09, 0.01, 0.16, 0.0625]
    means = [0.01, 0.005, 0.02, -0.005, 0.015]
    out = []
    for row, var, mu in zip(rows, variances, means):
        s = math.sqrt(var * 7 / 8)
        out.append([mu + s * x for x in row])
    return out


def test_mean_variance_diagonal_hand_computed():
    # Inverse-variance weights for variances [0.04, 0.09, 0.01, 0.16,
    # 0.0625]: 1/v = [25, 11.111, 100, 6.25, 16], sum 158.361...,
    # weights [0.157867, 0.070163, 0.631468, 0.039467, 0.101035].
    # The fixture's sample covariance is exactly diagonal (asserted),
    # so the min-variance portfolio is exactly the inverse-variance one.
    rets = _hadamard_returns()
    cov = sample_covariance(rets)
    for i in range(5):
        for j in range(5):
            expected = [0.04, 0.09, 0.01, 0.16, 0.0625][i] if i == j else 0.0
            assert cov[i][j] == pytest.approx(expected, abs=1e-12)
    w = quant.mean_variance_weights(rets, l2=0.0)
    assert sum(w) == pytest.approx(1.0)
    assert w == pytest.approx(
        [0.157867, 0.070163, 0.631468, 0.039467, 0.101035], abs=1e-4)


def test_mean_variance_target_return():
    rets = _hadamard_returns()
    w = quant.mean_variance_weights(rets, target_return=0.01, l2=0.0)
    assert sum(w) == pytest.approx(1.0)
    mu = [sum(r) / len(r) for r in rets]
    assert sum(wi * mi for wi, mi in zip(w, mu)) == pytest.approx(0.01, abs=1e-6)


def test_mean_variance_rejects_nonzero_risk_free():
    with pytest.raises(ValueError):
        quant.mean_variance_weights(_hadamard_returns(), risk_free=0.01)


def test_hrp_two_asset_hand_computed():
    # cov [[0.04, 0.006], [0.006, 0.09]]: inverse-variance weights
    # w1 = 25 / (25 + 11.111) = 0.6923076923076923. HRP on two assets
    # reduces exactly to inverse-variance allocation.
    w = quant.hrp_weights([[0.04, 0.006], [0.006, 0.09]])
    assert w == pytest.approx([0.6923076923076923, 0.3076923076923077])


def test_hrp_diagonal_five_asset_hand_computed():
    # Diagonal covariance: HRP must equal inverse-variance weights
    # (same fixture as the mean-variance test).
    cov = [[0.0] * 5 for _ in range(5)]
    for i, v in enumerate([0.04, 0.09, 0.01, 0.16, 0.0625]):
        cov[i][i] = v
    w = quant.hrp_weights(cov)
    assert sum(w) == pytest.approx(1.0)
    assert all(wi > 0 for wi in w)
    assert w == pytest.approx(
        [0.157867, 0.070163, 0.631468, 0.039467, 0.101035], abs=1e-4)


def test_hrp_correlated_five_asset_properties():
    # Correlated 5-asset covariance (synthetic): weights sum to 1,
    # all long-only, and the low-variance asset gets the largest weight.
    cov = [
        [0.04, 0.012, 0.006, 0.002, 0.004],
        [0.012, 0.09, 0.009, 0.003, 0.006],
        [0.006, 0.009, 0.01, 0.001, 0.002],
        [0.002, 0.003, 0.001, 0.16, 0.008],
        [0.004, 0.006, 0.002, 0.008, 0.0625],
    ]
    w = quant.hrp_weights(cov)
    assert sum(w) == pytest.approx(1.0)
    assert all(wi > 0 for wi in w)
    assert w[2] == max(w)  # lowest variance (0.01) dominates


def test_hrp_rejects_bad_covariance():
    with pytest.raises(ValueError):
        quant.hrp_weights([[0.04, 0.006], [0.007, 0.09]])  # asymmetric
    with pytest.raises(ValueError):
        quant.hrp_weights([[0.04, 0.0], [0.0, 0.0]])  # zero diagonal
    with pytest.raises(ValueError):
        quant.hrp_weights([[0.04, 0.006]])  # not square


# --- Sector rotation -----------------------------------------------------------

def test_sector_relative_strength_hand_computed():
    # Trailing 3-period returns: A 1.01^3-1 = 0.030301, B 1.02^3-1 =
    # 0.061208, C 0.99^3-1 = -0.029701. Mean = 0.0206027.
    # Scores: A 0.0096983, B 0.0406053, C -0.0503037; ranking B, A, C.
    res = quant.sector_relative_strength(
        {"A": [0.01, 0.01, 0.01, 0.01], "B": [0.02, 0.02, 0.02, 0.02],
         "C": [-0.01, -0.01, -0.01, -0.01]}, lookback=3)
    assert res["ranking"] == ["B", "A", "C"]
    assert res["trailing_returns"]["A"] == pytest.approx(0.030301)
    assert res["trailing_returns"]["B"] == pytest.approx(0.061208)
    assert res["trailing_returns"]["C"] == pytest.approx(-0.029701)
    assert res["scores"]["B"] == pytest.approx(0.0406053, abs=1e-6)
    assert res["scores"]["C"] == pytest.approx(-0.0503037, abs=1e-6)


def test_sector_rotation_allocation_top_n():
    res = quant.sector_rotation_allocation(
        {"A": [0.01] * 4, "B": [0.02] * 4, "C": [-0.01] * 4},
        lookback=3, top_n=2)
    assert res["allocation"] == {"A": 0.5, "B": 0.5, "C": 0.0}
    assert res["ranking"] == ["B", "A", "C"]
    with pytest.raises(ValueError):
        quant.sector_rotation_allocation({"A": [0.01] * 4}, lookback=3, top_n=2)
    with pytest.raises(ValueError):
        quant.sector_rotation_allocation(
            {"A": [0.01, 0.01]}, lookback=3, top_n=1)  # too short


# --- Fold-ins from the Phase 0a-expanded teardowns ---------------------------
# skfolio (BSD-3-Clause, HEAD 90e3fb9): EVaR, max-diversification.
# QuantInvestStrats (MIT, HEAD 45de566): Sharpe inference, multiple
# testing, turnover, transaction costs. qlib (MIT, HEAD 5435523):
# Alpha158 baseline factors. EigenLedger (Apache-2.0, HEAD 0f965f9):
# one friendly portfolio_analysis() entry point. All reimplemented
# from textbook definitions; no outside code copied.


def test_entropic_var_constant_loss():
    # Constant loss 0.02: EVaR -> 0.02 as z -> infinity, so the
    # minimiser over the finite search range lands just above 0.02.
    assert quant.entropic_var([-0.02] * 20, 0.05) == pytest.approx(0.02, abs=1e-3)


def test_entropic_var_ordering():
    # EVaR >= VaR and EVaR >= CVaR at the same level (Chernoff bound),
    # and EVaR <= the worst observed loss.
    evar = quant.entropic_var(RETS_20, 0.10)
    assert evar >= quant.historical_var(RETS_20, 0.10)
    assert evar >= 0.0475  # historical CVaR at alpha=0.10
    assert evar <= 0.05  # worst observed loss
    with pytest.raises(ValueError):
        quant.entropic_var(RETS_20, 1.0)


def test_max_diversification_diagonal_hand_computed():
    # Diagonal cov, vols [0.2, 0.3, 0.1, 0.4, 0.25]: Sigma^-1 sigma
    # is proportional to 1/vol = [5, 3.333, 10, 2.5, 4], sum 24.8333.
    cov = [[0.0] * 5 for _ in range(5)]
    for i, v in enumerate([0.04, 0.09, 0.01, 0.16, 0.0625]):
        cov[i][i] = v
    res = quant.max_diversification_weights(cov)
    assert sum(res["weights"]) == pytest.approx(1.0)
    assert res["weights"] == pytest.approx(
        [0.201342, 0.134228, 0.402685, 0.100671, 0.161074], abs=1e-5)
    assert "max-diversification" in res["method"]


def test_max_diversification_fallback_is_labelled():
    # Perfectly correlated assets: Sigma^-1 sigma can go negative;
    # the fallback must be inverse-volatility AND labelled.
    cov = [[0.04, 0.06, 0.06], [0.06, 0.09, 0.09], [0.06, 0.09, 0.09]]
    res = quant.max_diversification_weights(cov)
    assert all(w >= 0 for w in res["weights"])
    assert sum(res["weights"]) == pytest.approx(1.0)
    assert "fallback" in res["method"]


def test_sharpe_standard_error_and_interval_hand_computed():
    # RETS_20: annualised Sharpe 0.12476650928382657 (see risk-summary
    # test); Lo (2002) SE = sqrt(252) * sqrt((1 + 0.5*sr_period^2)/20)
    # = 3.5497026871339483; 95% CI = 0.12477 +/- 1.959964 * SE.
    se = quant.sharpe_standard_error(RETS_20)
    assert se == pytest.approx(3.5497026871339483)
    ci = quant.sharpe_confidence_interval(RETS_20, confidence=0.95)
    assert ci["sharpe"] == pytest.approx(0.12476650928382657)
    assert ci["lower"] == pytest.approx(-6.832522913323763)
    assert ci["upper"] == pytest.approx(7.082055931891417)
    assert ci["significant"] is False  # the interval covers 0: honest
    with pytest.raises(ValueError):
        quant.sharpe_standard_error([0.01, 0.01, 0.01])


def test_bonferroni_adjust_hand_computed():
    # p = [0.01, 0.04, 0.03], m = 3: adjusted [0.03, 0.12, 0.09];
    # only p=0.01 survives at alpha=0.05.
    res = quant.bonferroni_adjust([0.01, 0.04, 0.03], alpha=0.05)
    assert res["adjusted"] == pytest.approx([0.03, 0.12, 0.09])
    assert res["reject"] == [True, False, False]
    with pytest.raises(ValueError):
        quant.bonferroni_adjust([1.5])


def test_benjamini_hochberg_hand_computed():
    # Same p-values: BH adjusted [0.03, 0.04, 0.04] (sorted raw
    # 0.03, 0.045, 0.04, monotone-enforced from the top); the step-up
    # rule rejects all three at alpha=0.05.
    res = quant.benjamini_hochberg([0.01, 0.04, 0.03], alpha=0.05)
    assert res["adjusted"] == pytest.approx([0.03, 0.04, 0.04])
    assert res["reject"] == [True, True, True]
    # A stricter alpha rejects nothing.
    res2 = quant.benjamini_hochberg([0.01, 0.04, 0.03], alpha=0.01)
    assert res2["reject"] == [False, False, False]


def test_turnover_hand_computed():
    # [[1,0],[0.5,0.5],[0.5,0.5]]: steps 0.5*(1.0)=0.5 then 0;
    # mean one-way turnover = 0.25. Buy-and-hold has turnover 0.
    res = quant.turnover([[1.0, 0.0], [0.5, 0.5], [0.5, 0.5]])
    assert res["per_step"] == pytest.approx([0.5, 0.0])
    assert res["mean_turnover"] == pytest.approx(0.25)
    assert quant.turnover([[1.0, 0.0], [1.0, 0.0]])["mean_turnover"] == 0.0
    with pytest.raises(ValueError):
        quant.turnover([[0.5, 0.5]])  # needs >= 2 vectors


def test_net_return_after_costs():
    # Gross 5%, turnover 0.5 at 0.36% per unit (a broker slab):
    # net = 0.05 - 0.5 * 0.0036 = 0.0482.
    assert quant.net_return_after_costs(0.05, 0.5, 0.0036) == pytest.approx(0.0482)
    assert quant.net_return_after_costs(0.05, 0.0, 0.0036) == pytest.approx(0.05)


def test_alpha158_baseline_factors_hand_computed():
    # Flat 100 then a jump: prices [100]*21 + [110]*10. At index 20
    # (first row) roc_5 = 100/100 - 1 = 0; at index 21, roc_5 =
    # 110/100 - 1 = 0.10, ret_1 = 0.10, roc_20 = 110/100 - 1 = 0.10.
    prices = [100.0] * 21 + [110.0] * 10
    rows = quant.alpha158_baseline_factors(prices)
    assert len(rows) == 11  # indices 20..30
    assert rows[0]["index"] == 20
    assert rows[0]["roc_5"] == pytest.approx(0.0)
    assert rows[0]["vol_20"] == pytest.approx(0.0)
    assert rows[1]["roc_5"] == pytest.approx(0.10)
    assert rows[1]["ret_1"] == pytest.approx(0.10)
    assert rows[1]["roc_20"] == pytest.approx(0.10)
    assert rows[1]["ma_ratio_20"] == pytest.approx(110.0 / 100.5 - 1.0)


def test_alpha158_factors_are_walk_forward_safe():
    # Perturbing prices AFTER index t must not change row t.
    prices = [100.0 + 0.3 * i + (1.0 if i % 3 == 0 else 0.0) for i in range(40)]
    rows = quant.alpha158_baseline_factors(prices)
    perturbed = prices[:]
    perturbed[35] = 999.0
    rows2 = quant.alpha158_baseline_factors(perturbed)
    for r1, r2 in zip(rows, rows2):
        if r1["index"] < 35:
            assert r1 == r2
    with pytest.raises(ValueError):
        quant.alpha158_baseline_factors([100.0] * 10)  # too short


def test_portfolio_analysis_friendly_entry_point():
    prices = [100.0 + 0.5 * i for i in range(30)]
    res = quant.portfolio_analysis(prices)
    assert res["n_prices"] == 30
    assert len(res["returns"]) == 29
    assert res["risk_summary"]["max_drawdown"] == pytest.approx(0.0)
    assert res["momentum_20"] == pytest.approx(114.5 / 104.5 - 1.0)
    assert res["mean_reversion_20"]["last"] == pytest.approx(114.5)
    assert res["sources"]["risk_summary"] == "quant.risk_summary"
    short = quant.portfolio_analysis([100.0, 101.0, 102.0])
    assert short["momentum_20"] is None
    assert short["mean_reversion_20"] is None
