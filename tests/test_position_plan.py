"""Tests for toolkit/position_plan.py — synthetic example data only."""

import pytest

from toolkit import position_plan

CFG = position_plan.DEFAULT_RESEARCH_CONFIG


def _pass_plan(**overrides):
    plan = {
        "entry": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "risk_per_share": 5.0,
        "reward_risk": 2.0,
        "shares": 20,
        "max_loss": 100.0,
        "cash_available": 10000.0,
        "asset_type": "equity",
        "symbol": "DEMO",
        "short": False,
        "daily_loss_usd": 0.0,
        "trades_today": 0,
        "orders_today": 0,
        "entry_window_ok": True,
        "is_duplicate": False,
        "date": "2026-11-07",
    }
    plan.update(overrides)
    return plan


def test_plan_exit_computed_target_and_r():
    plan = position_plan.plan_exit(100, 95)
    assert plan["risk_per_share"] == 5.0
    assert plan["target"] == 110.0
    assert plan["reward_risk"] == 2.0
    assert plan["reward_per_share"] == 10.0
    assert plan["risk_pct_of_entry"] == pytest.approx(0.05)


def test_plan_exit_supplied_target():
    plan = position_plan.plan_exit(100, 95, target=120)
    assert plan["target"] == 120.0
    assert plan["reward_risk"] == 4.0


def test_plan_exit_stop_rounds_down_target_rounds_half_up():
    # Stop 95.009 -> 95.00 (away from entry); target 110.005 -> 110.01.
    plan = position_plan.plan_exit(100, 95.009, target=110.005)
    assert plan["stop"] == 95.0
    assert plan["target"] == 110.01
    assert plan["risk_per_share"] == 5.0


def test_plan_exit_refuses_stop_at_or_above_entry():
    with pytest.raises(ValueError):
        position_plan.plan_exit(100, 100)
    with pytest.raises(ValueError):
        position_plan.plan_exit(100, 101)


def test_plan_exit_refuses_target_at_or_below_entry():
    with pytest.raises(ValueError):
        position_plan.plan_exit(100, 95, target=100)
    with pytest.raises(ValueError):
        position_plan.plan_exit(100, 95, target=90)


def test_plan_exit_refuses_risk_below_0_05pct_and_allows_the_boundary():
    with pytest.raises(ValueError):
        position_plan.plan_exit(100, 99.96)  # R 0.04 = 0.04% of entry
    plan = position_plan.plan_exit(100, 99.95)  # R 0.05 = exactly 0.05%
    assert plan["risk_per_share"] == 0.05


def test_plan_exit_refuses_reward_risk_below_minimum():
    with pytest.raises(ValueError):
        position_plan.plan_exit(100, 95, target=108)  # RR 1.6 < 2.0
    plan = position_plan.plan_exit(100, 95, target=108, risk_reward=1.5)
    assert plan["reward_risk"] == 1.6


def test_size_position_loss_limit_decides():
    res = position_plan.size_position(10000, 100, 0.25, 2500, 100, 5)
    assert res["shares"] == 20
    assert res["limiting_factor"] == "max_loss_per_trade"
    assert res["limits"] == {"max_loss_per_trade": 20, "max_position_pct": 25,
                             "max_order_usd": 25, "cash_available": 100}
    assert res["position_cost"] == 2000.0
    assert res["max_loss"] == 100.0


def test_size_position_each_limit_can_decide():
    # Position-% limit: 25 shares ties with the order limit; % wins the tie.
    res = position_plan.size_position(10000, 10000, 0.25, 2500, 100, 5)
    assert res["shares"] == 25
    assert res["limiting_factor"] == "max_position_pct"
    # Order limit: 15 shares is the smallest.
    res = position_plan.size_position(10000, 10000, 0.25, 1500, 100, 5)
    assert res["shares"] == 15
    assert res["limiting_factor"] == "max_order_usd"
    # Cash limit: 1500 cash buys 15 shares, below every other limit.
    res = position_plan.size_position(10000, 100, 0.25, 2500, 100, 5,
                                      cash_available=1500)
    assert res["shares"] == 15
    assert res["limiting_factor"] == "cash_available"
    assert res["cash_remaining"] == 0.0


def test_size_position_refuses_zero_shares_and_tiny_orders():
    with pytest.raises(ValueError):
        position_plan.size_position(10000, 1, 0.25, 2500, 100, 5)
    with pytest.raises(ValueError):
        position_plan.size_position(10000, 100, 0.25, 2500, 100, 5,
                                    min_order_usd=5000)


def test_default_research_config_shape():
    assert CFG["paper"] is True
    assert CFG["allow_options"] is False
    assert CFG["allow_margin"] is False
    assert CFG["allow_short"] is False
    assert CFG["min_reward_risk"] == 2.0
    assert CFG["require_stop"] is True
    for key in ("budget", "cash_buffer_pct", "max_position_pct",
                "max_order_usd", "min_order_usd", "max_loss_per_trade_usd",
                "max_daily_loss_usd", "max_trades_per_day"):
        assert key in CFG


def test_risk_gate_passes_a_clean_plan_with_16_checks():
    res = position_plan.risk_gate(_pass_plan(), CFG)
    assert res["verdict"] == "PASS"
    assert res["passed"] == 16
    assert res["failed"] == 0
    assert res["failed_checks"] == []
    assert list(res["checks"]) == list(position_plan.GATE_CHECKS)
    assert all(c["pass"] for c in res["checks"].values())
    assert res["date"] == "2026-11-07"


def test_risk_gate_fingerprint_is_stable_per_plan_and_date_only():
    plan = _pass_plan()
    a = position_plan.risk_gate(plan, CFG)["fingerprint"]
    b = position_plan.risk_gate(_pass_plan(), CFG)["fingerprint"]
    assert a == b
    assert len(a) == 64  # sha256 hex
    other_date = position_plan.risk_gate(_pass_plan(date="2026-11-08"), CFG)
    assert other_date["fingerprint"] != a
    assert other_date["date"] == "2026-11-08"
    changed = position_plan.risk_gate(_pass_plan(shares=19), CFG)
    assert changed["fingerprint"] != a


def test_risk_gate_blocks_short_blocked_symbol_and_paper_off():
    assert position_plan.risk_gate(_pass_plan(short=True), CFG)["verdict"] == "BLOCK"
    assert "short_blocked" in position_plan.risk_gate(
        _pass_plan(short=True), CFG)["failed_checks"]
    cfg = dict(CFG, blocked_symbols=["DEMO"])
    res = position_plan.risk_gate(_pass_plan(), cfg)
    assert res["verdict"] == "BLOCK"
    assert "blocked_list" in res["failed_checks"]
    cfg = dict(CFG, paper=False)
    res = position_plan.risk_gate(_pass_plan(), cfg)
    assert res["verdict"] == "BLOCK"
    assert "paper_mode" in res["failed_checks"]


def test_risk_gate_blocks_low_reward_risk_missing_window_and_duplicate():
    res = position_plan.risk_gate(_pass_plan(reward_risk=1.0), CFG)
    assert "reward_risk" in res["failed_checks"]
    plan = _pass_plan()
    del plan["entry_window_ok"]
    assert "entry_window" in position_plan.risk_gate(plan, CFG)["failed_checks"]
    res = position_plan.risk_gate(_pass_plan(is_duplicate=True), CFG)
    assert "duplicate" in res["failed_checks"]


def test_risk_gate_blocks_daily_limits_and_missing_stop():
    res = position_plan.risk_gate(_pass_plan(trades_today=3), CFG)
    assert "trades_per_day" in res["failed_checks"]
    res = position_plan.risk_gate(_pass_plan(orders_today=3), CFG)
    assert "daily_order_limit" in res["failed_checks"]
    res = position_plan.risk_gate(_pass_plan(daily_loss_usd=150.0), CFG)
    assert "daily_loss" in res["failed_checks"]  # 150 + 100 > 200
    plan = _pass_plan()
    del plan["stop"]
    res = position_plan.risk_gate(plan, CFG)
    assert res["verdict"] == "BLOCK"
    assert "format" in res["failed_checks"]
    assert "stop_required" in res["failed_checks"]


def test_risk_gate_blocks_oversize_and_over_loss_plans():
    res = position_plan.risk_gate(_pass_plan(shares=30), CFG)
    assert "position_cap" in res["failed_checks"]  # cost 3000 > 2500 cap
    assert "size_limits" in res["failed_checks"]  # cost 3000 > max order 2500
    res = position_plan.risk_gate(_pass_plan(shares=25, max_loss=125.0), CFG)
    assert "loss_per_trade" in res["failed_checks"]  # 125 > 100
