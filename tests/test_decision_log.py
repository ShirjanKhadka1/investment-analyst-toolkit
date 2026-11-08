"""Tests for toolkit/decision_log.py + paper_ledger.py (Engines 1.4, 1.5).

All fixtures are synthetic; nothing here reaches the published
scoreboard, which starts at n=0.
"""

import pytest

from toolkit import decision_log, paper_ledger, position_plan

CALL = {"date": "2026-11-01", "symbol": "BNT", "exchange": "NEPSE",
        "thesis": "synthetic fixture",
        "horizon_days": 5, "expected": "up",
        "sources": ["synthetic demo series"], "confidence": 0.7,
        "entry_close": 100.0}


def _write(tmp_path, **overrides):
    call = dict(CALL, **overrides)
    return decision_log.write_call(tmp_path / "calls.jsonl", call)


def test_write_call_validates(tmp_path):
    path = tmp_path / "calls.jsonl"
    with pytest.raises(ValueError):
        decision_log.write_call(path, {**CALL, "expected": "sideways"})
    with pytest.raises(ValueError):
        decision_log.write_call(path, {**CALL, "confidence": 1.5})
    with pytest.raises(ValueError):
        decision_log.write_call(path, {**CALL, "sources": []})
    with pytest.raises(ValueError):
        decision_log.write_call(path, {**CALL, "horizon_days": 0})
    with pytest.raises(ValueError):
        decision_log.write_call(path, {**CALL, "expected": "range"})
    rec = _write(tmp_path)
    assert rec["status"] == "open" and len(rec["call_id"]) == 16
    assert decision_log.load_calls(path)[0]["symbol"] == "BNT"
    assert decision_log.load_calls(tmp_path / "missing.jsonl") == []


def test_settle_hit_and_alpha(tmp_path):
    path = tmp_path / "calls.jsonl"
    rec = _write(tmp_path)
    settled = decision_log.settle_call(
        path, rec["call_id"], realised_close=110.0, entry_close=100.0,
        index_return=0.05, index_source="synthetic demo index series",
        as_of="2026-11-07")
    assert settled["status"] == "settled"
    assert settled["realised_return"] == pytest.approx(0.10)
    assert settled["alpha"] == pytest.approx(0.05)
    assert settled["hit"] is True
    assert settled["brier"] == pytest.approx((0.7 - 1.0) ** 2)


def test_settle_refusals(tmp_path):
    path = tmp_path / "calls.jsonl"
    rec = _write(tmp_path)
    with pytest.raises(ValueError):  # before horizon ends (Nov 6)
        decision_log.settle_call(path, rec["call_id"], 110.0, 100.0,
                                 0.05, "synthetic demo", "2026-11-03")
    with pytest.raises(ValueError):  # unnamed index source
        decision_log.settle_call(path, rec["call_id"], 110.0, 100.0,
                                 0.05, "", "2026-11-07")
    with pytest.raises(ValueError):  # unknown call
        decision_log.settle_call(path, "nope", 110.0, 100.0,
                                 0.05, "synthetic demo", "2026-11-07")
    decision_log.settle_call(path, rec["call_id"], 90.0, 100.0, 0.0,
                             "synthetic demo", "2026-11-07")
    with pytest.raises(ValueError):  # already settled
        decision_log.settle_call(path, rec["call_id"], 90.0, 100.0, 0.0,
                                 "synthetic demo", "2026-11-08")


def test_settle_down_call_and_range_call(tmp_path):
    path = tmp_path / "calls.jsonl"
    down = _write(tmp_path, expected="down", confidence=0.6)
    settled = decision_log.settle_call(path, down["call_id"], 95.0, 100.0,
                                       0.0, "synthetic demo", "2026-11-07")
    assert settled["hit"] is True
    assert settled["brier"] == pytest.approx((0.4 - 0.0) ** 2)
    rng = _write(tmp_path, expected="range", confidence=0.8,
                 expected_low=95.0, expected_high=105.0)
    settled = decision_log.settle_call(path, rng["call_id"], 102.0, 100.0,
                                       0.01, "synthetic demo", "2026-11-07")
    assert settled["hit"] is True and settled["brier"] is None


def test_scoreboard_empty_and_settled(tmp_path):
    assert decision_log.scoreboard([])["n_settled"] == 0
    assert decision_log.scoreboard([])["hit_rate"] is None
    md = decision_log.render_scoreboard_md([])
    assert "n=0" in md and "no calls settled yet" in md.lower()
    path = tmp_path / "calls.jsonl"
    c1 = _write(tmp_path)
    c2 = _write(tmp_path, expected="down", confidence=0.6)
    decision_log.settle_call(path, c1["call_id"], 110.0, 100.0, 0.05,
                             "synthetic demo", "2026-11-07")
    decision_log.settle_call(path, c2["call_id"], 110.0, 100.0, 0.05,
                             "synthetic demo", "2026-11-07")
    board = decision_log.scoreboard(decision_log.load_calls(path))
    assert board["n_settled"] == 2 and board["wins"] == 1
    assert board["losses"] == 1 and board["hit_rate"] == pytest.approx(0.5)
    assert board["average_alpha"] == pytest.approx(0.05)
    md = decision_log.render_scoreboard_md(decision_log.load_calls(path))
    assert "| 2026-11-01 | BNT | up | 10.00% | 5.00% | yes |" in md


def test_reflections_roundtrip(tmp_path):
    path = tmp_path / "reflections.jsonl"
    assert decision_log.load_reflections(path) == []
    rec = decision_log.append_reflection(path, "abc123", "Missed the volume signal.")
    assert rec["call_id"] == "abc123"
    assert decision_log.load_reflections(path)[0]["text"] == "Missed the volume signal."
    with pytest.raises(ValueError):
        decision_log.append_reflection(path, "abc123", "  ")


def _passing_plan():
    plan = position_plan.plan_exit(100, 95, target=110)
    sized = position_plan.size_position(10000, 100, 0.25, 2500, 100, 5)
    return {**plan, **sized, "symbol": "BNT", "exchange": "NEPSE",
            "entry_window_ok": True, "date": "2026-11-07"}


def test_extended_gate_pass_and_base_unchanged(tmp_path):
    plan = _passing_plan()
    base = position_plan.risk_gate(plan)
    assert base["verdict"] == "PASS" and base["passed"] == 16
    ext = position_plan.extended_risk_gate(
        plan, context={"portfolio_value": 10000, "sector_exposure_usd": 0.0,
                       "sector": "manufacturing", "typical_daily_volume": 30000})
    assert ext["verdict"] == "PASS" and ext["passed"] == 19
    assert ext["base_gate"]["fingerprint"] == base["fingerprint"]
    assert ext["failed_checks"] == []


def test_extended_gate_blocks_each_extra_check():
    plan = _passing_plan()
    ctx = {"portfolio_value": 10000, "sector_exposure_usd": 0.0,
           "sector": "banks", "typical_daily_volume": 30000}
    # max loss per idea: plan max loss is 20 shares * 5 = 100; cap 50 blocks
    blocked = position_plan.extended_risk_gate(
        plan, config={"max_loss_per_idea_usd": 50.0}, context=ctx)
    assert blocked["verdict"] == "BLOCK"
    assert "max_loss_per_idea" in blocked["failed_checks"]
    # sector concentration: 9000 existing + 2000 cost > 30% of 10000
    blocked = position_plan.extended_risk_gate(
        plan, context={**ctx, "sector_exposure_usd": 9000.0})
    assert "sector_concentration" in blocked["failed_checks"]
    # exit liquidity: 20 shares vs volume 100 -> 20% > 10%
    blocked = position_plan.extended_risk_gate(
        plan, context={**ctx, "typical_daily_volume": 100})
    assert "exit_liquidity" in blocked["failed_checks"]
    # missing context fails closed, never passes silently
    blocked = position_plan.extended_risk_gate(plan, context={})
    assert blocked["verdict"] == "BLOCK"
    assert set(blocked["failed_checks"]) >= {"sector_concentration", "exit_liquidity"}


def test_extended_gate_base_block_propagates():
    plan = _passing_plan()
    plan["entry_window_ok"] = False
    ext = position_plan.extended_risk_gate(
        plan, context={"portfolio_value": 10000, "sector_exposure_usd": 0.0,
                       "typical_daily_volume": 30000})
    assert ext["verdict"] == "BLOCK" and "entry_window" in ext["failed_checks"]


def test_paper_ledger_roundtrip(tmp_path):
    path = tmp_path / "ledger.jsonl"
    assert paper_ledger.list_plans(path) == []
    plan = _passing_plan()
    gate = position_plan.extended_risk_gate(
        plan, context={"portfolio_value": 10000, "sector_exposure_usd": 0.0,
                       "typical_daily_volume": 30000})
    rec = paper_ledger.record_plan(path, plan, gate)
    assert rec["verdict"] == "PASS" and "not a recommendation" in rec["note"]
    assert rec["exchange"] == "NEPSE"
    assert paper_ledger.plans_for_symbol(path, "BNT")[0]["fingerprint"] == gate["fingerprint"]
    assert paper_ledger.plans_for_symbol(path, "NABIL") == []
    with pytest.raises(ValueError):
        paper_ledger.record_plan(path, plan, {"verdict": "MAYBE"})
    with pytest.raises(ValueError):
        paper_ledger.record_plan(path, {"entry": 1}, gate)


def test_write_call_requires_exchange(tmp_path):
    """Round 3: exchange is required for writes — a global call
    logged without one must never be silently labelled NEPSE."""
    path = tmp_path / "calls.jsonl"
    no_exchange = {k: v for k, v in CALL.items() if k != "exchange"}
    with pytest.raises(ValueError):
        decision_log.write_call(path, no_exchange)
    with pytest.raises(ValueError):
        decision_log.write_call(path, {**CALL, "exchange": ""})
    with pytest.raises(ValueError):
        decision_log.write_call(path, {**CALL, "exchange": 123})
    rec = _write(tmp_path, exchange="NYSE", symbol="AAPL")
    assert rec["exchange"] == "NYSE"
    assert decision_log.load_calls(path)[0]["exchange"] == "NYSE"


def test_load_calls_defaults_legacy_exchange(tmp_path):
    """Reads of logs written before the exchange requirement still
    default a missing exchange to NEPSE — the default lives only
    on the read path, never on writes."""
    import json

    path = tmp_path / "legacy.jsonl"
    legacy = {k: v for k, v in CALL.items() if k != "exchange"}
    legacy["call_id"] = "legacy0000000001"
    legacy["status"] = "open"
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    loaded = decision_log.load_calls(path)
    assert loaded[0]["exchange"] == "NEPSE"


def test_record_plan_requires_exchange(tmp_path):
    """Round 3: a plan without an exchange label cannot be recorded."""
    path = tmp_path / "ledger.jsonl"
    plan = _passing_plan()
    gate = position_plan.extended_risk_gate(
        plan, context={"portfolio_value": 10000, "sector_exposure_usd": 0.0,
                       "typical_daily_volume": 30000})
    no_exchange = {k: v for k, v in plan.items() if k != "exchange"}
    with pytest.raises(ValueError):
        paper_ledger.record_plan(path, no_exchange, gate)
    with pytest.raises(ValueError):
        paper_ledger.record_plan(path, {**plan, "exchange": "  "}, gate)
    rec = paper_ledger.record_plan(
        path, {**plan, "exchange": "NYSE", "symbol": "AAPL"}, gate)
    assert rec["exchange"] == "NYSE"


def test_list_plans_defaults_legacy_exchange(tmp_path):
    """Reads of ledgers written before the exchange requirement
    still default a missing exchange to NEPSE."""
    import json

    path = tmp_path / "legacy-ledger.jsonl"
    plan = _passing_plan()
    gate = position_plan.extended_risk_gate(
        plan, context={"portfolio_value": 10000, "sector_exposure_usd": 0.0,
                       "typical_daily_volume": 30000})
    legacy = {"schema_version": "1.5.0", "recorded_date": "2026-11-07",
              "symbol": "BNT",
              "plan": {k: v for k, v in plan.items() if k != "exchange"},
              "verdict": "PASS", "fingerprint": gate["fingerprint"],
              "failed_checks": [], "note": "legacy fixture"}
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    loaded = paper_ledger.list_plans(path)
    assert loaded[0]["exchange"] == "NEPSE"
    assert loaded[0]["plan"]["exchange"] == "NEPSE"
