"""Phase 4 patch tests: sourced NEPSE friction schedule.

All rates here come from the user-supplied inputs of 28 Oct 2026
(rate-schedule screenshot; circuit limits +15%/-15%; settlement
T+2 standard with a broker T+1 override, example broker 58).
No rate beyond those inputs is asserted, and no website is
claimed as the screenshot's origin. Settlement T+2 is the
standard; T+1 is a broker-confirmed override only.
"""
import pytest

from toolkit import nepal_frictions
from toolkit.nepal_frictions import (
    NepalFrictions,
    UnknownFrictionError,
    sourced_nepal_frictions,
)


def test_factory_carries_sourced_rates():
    f = sourced_nepal_frictions()
    assert f.sebon_fee_rate == pytest.approx(0.00015)
    assert f.dp_charge_flat == pytest.approx(25.0)
    assert f.cgt_rate_short == pytest.approx(0.05)
    assert f.cgt_rate_long == pytest.approx(0.0375)
    assert f.circuit_limit_up == pytest.approx(0.15)
    assert f.circuit_limit_down == pytest.approx(0.15)
    assert f.settlement_lag_business_days == 2
    assert f.as_of == "2026-10-28"
    # Only settlement is supplied by the factory itself via the
    # standard lag, so every other parameter is known; the
    # unknown list is empty once the lag is set.
    assert f.unknown_fields() == []
    assert f.is_cost_known() is True


def test_brokerage_slab_boundaries():
    f = sourced_nepal_frictions()
    cases = [
        (50000.0, 0.0036), (50001.0, 0.0033), (500000.0, 0.0033),
        (500001.0, 0.0031), (2000000.0, 0.0031), (2000001.0, 0.0027),
        (10000000.0, 0.0027), (10000001.0, 0.0024),
    ]
    for value, rate in cases:
        assert nepal_frictions.brokerage_rate_for(value, f) == pytest.approx(rate)


def test_brokerage_minimum_flat_applies():
    f = sourced_nepal_frictions()
    # Rs 1,000 at 0.36% would be Rs 3.60 — the Rs 10 minimum wins.
    assert nepal_frictions.brokerage_cost_for(1000.0, f) == pytest.approx(10.0)
    # Rs 100,000 at 0.33% = Rs 330, above the minimum.
    assert nepal_frictions.brokerage_cost_for(100000.0, f) == pytest.approx(330.0)


def test_buy_cost_with_sourced_schedule():
    f = sourced_nepal_frictions()
    rep = nepal_frictions.trade_cost_report(100000.0, "buy", f)
    assert rep["status"] == "KNOWN"
    assert rep["brokerage_cost"] == pytest.approx(330.0)
    assert rep["sebon_fee"] == pytest.approx(15.0)
    assert rep["dp_charge"] == pytest.approx(25.0)
    assert rep["total_cost"] == pytest.approx(370.0)
    assert rep["net_cash"] == pytest.approx(-100370.0)


def test_cgt_holding_period_split():
    f = sourced_nepal_frictions()
    assert nepal_frictions.cgt_rate_for(365, f) == pytest.approx(0.05)
    assert nepal_frictions.cgt_rate_for(0, f) == pytest.approx(0.05)
    assert nepal_frictions.cgt_rate_for(366, f) == pytest.approx(0.0375)
    short = nepal_frictions.trade_cost_report(
        100000.0, "sell", f, realised_gain=10000.0, holding_days=100)
    assert short["capital_gains_tax"] == pytest.approx(500.0)
    long = nepal_frictions.trade_cost_report(
        100000.0, "sell", f, realised_gain=10000.0, holding_days=400)
    assert long["capital_gains_tax"] == pytest.approx(375.0)


def test_cgt_split_needs_holding_days():
    f = sourced_nepal_frictions()
    rep = nepal_frictions.trade_cost_report(
        100000.0, "sell", f, realised_gain=10000.0)
    assert rep["status"] == "UNKNOWN"
    assert "holding_days" in rep["unknown_fields"]
    with pytest.raises(UnknownFrictionError):
        nepal_frictions.apply_trade_frictions(
            100000.0, "sell", f, realised_gain=10000.0)


def test_cgt_loss_pays_no_tax():
    f = sourced_nepal_frictions()
    rep = nepal_frictions.trade_cost_report(
        100000.0, "sell", f, realised_gain=-5000.0, holding_days=100)
    assert rep["capital_gains_tax"] == pytest.approx(0.0)


def test_gain_after_fees_round_trip():
    f = sourced_nepal_frictions()
    out = nepal_frictions.realised_gain_after_fees(
        100000.0, 120000.0, f, holding_days=400)
    # Buy charges: 330 + 15 + 25 = 370; buy total 100,370.
    assert out["buy_charges"] == pytest.approx(370.0)
    assert out["buy_total_cost"] == pytest.approx(100370.0)
    # Sell charges: 396 + 18 + 25 = 439; net before tax 119,561.
    assert out["sell_charges"] == pytest.approx(439.0)
    assert out["sell_net_before_tax"] == pytest.approx(119561.0)
    # Gain after fees 19,191; long CGT 3.75% = 719.6625.
    assert out["realised_gain"] == pytest.approx(19191.0)
    assert out["capital_gains_tax"] == pytest.approx(719.6625)
    assert out["net_proceeds"] == pytest.approx(118841.3375)


def test_circuit_band_plus_minus_15():
    f = sourced_nepal_frictions()
    assert nepal_frictions.circuit_status(115.0, 100.0, f)["within_limits"] is True
    assert nepal_frictions.circuit_status(116.0, 100.0, f)["within_limits"] is False
    assert nepal_frictions.circuit_status(85.0, 100.0, f)["within_limits"] is True
    assert nepal_frictions.circuit_status(84.0, 100.0, f)["within_limits"] is False


def test_settlement_default_t2_and_t1_override():
    f = sourced_nepal_frictions()
    assert nepal_frictions.settlement_status(f)["settlement_cycle"] == "T+2"
    over = sourced_nepal_frictions(
        settlement_lag_business_days=1, broker_override="broker 58")
    status = nepal_frictions.settlement_status(over)
    assert status["settlement_cycle"] == "T+1"
    assert over.settlement_broker_note == "broker 58"
    # Default-unknown frictions still report settlement UNKNOWN.
    unknown = nepal_frictions.settlement_status(
        nepal_frictions.default_unknown_frictions())
    assert unknown["status"] == "UNKNOWN"


def test_t1_override_requires_named_broker():
    with pytest.raises(ValueError):
        sourced_nepal_frictions(settlement_lag_business_days=1)


def test_unsourced_slab_and_invalid_slab_rejected():
    with pytest.raises(ValueError):
        NepalFrictions(brokerage_slabs=((50000.0, 0.0036),))
    with pytest.raises(ValueError):
        NepalFrictions(
            brokerage_slabs=((500000.0, 0.0033), (50000.0, 0.0036)),
            source="test", as_of="2026-10-28")
    with pytest.raises(ValueError):
        NepalFrictions(
            brokerage_slabs=((50000.0, 1.5),),
            source="test", as_of="2026-10-28")
