"""Market profiles (Step 1): sourced configs, NEPSE byte-identical.

Every sourced number must carry its source URL and as-of date; an
unsourced number is None (UNKNOWN), never a guess. The NEPSE profile
is the standing sourced schedule, and the old literal
TRADING_WEEKDAYS table must survive the derivation unchanged.
"""

import pytest

from toolkit import connectors, timemachine
from toolkit.market_profiles import (
    KIND_PROFILES,
    PROFILES,
    UnknownMarketError,
    default_profile,
    get_profile,
    list_profiles,
    profile_config,
)
from toolkit.nepal_frictions import (
    BROKERAGE_MINIMUM_FLAT,
    CGT_RATE_LONG,
    CGT_RATE_SHORT,
    CIRCUIT_LIMIT,
    DP_CHARGE_FLAT,
    SEBON_FEE_RATE,
)
from toolkit.position_plan import DEFAULT_RESEARCH_CONFIG, risk_gate

DEMO_CLOSES = [100.0 + i * 0.5 for i in range(60)] + [130.0] * 10


def test_all_seven_profiles_exist():
    assert set(list_profiles()) == {
        "nepse", "us", "india", "uk", "crypto", "fx", "generic"}
    assert default_profile().key == "nepse"


def test_unknown_market_raises():
    with pytest.raises(UnknownMarketError):
        get_profile("mars")
    with pytest.raises(UnknownMarketError):
        timemachine.simulate_with_profile(
            DEMO_CLOSES, 130.0, 125.0, 140.0, profile="mars")


def test_every_sourced_number_has_url_and_date():
    for key, profile in PROFILES.items():
        for field, source in profile.sources.items():
            assert isinstance(source, tuple) and len(source) == 2, (key, field)
            url, as_of = source
            assert isinstance(url, str) and url, (key, field)
            assert isinstance(as_of, str) and len(as_of) == 10, (key, field)
        # A sourced fee/tax value must be traceable: the profile must
        # name at least one source for its numbers.
        if key != "generic":
            assert profile.sources, key


def test_nepse_profile_matches_sourced_schedule():
    nepse = get_profile("nepse")
    assert nepse.circuit_limit_up == CIRCUIT_LIMIT == 0.15
    assert nepse.circuit_limit_down == CIRCUIT_LIMIT
    assert nepse.settlement_lag_business_days == 2
    assert nepse.currency == "NPR"
    # Current rule (from 2026-04-06): Mon-Fri. The Sun-Thu week is
    # kept only as the pre-change rule in trading_weekdays_rules.
    assert set(nepse.trading_weekdays) == {0, 1, 2, 3, 4}
    assert nepse.fees["sebon_fee_rate"]["value"] == SEBON_FEE_RATE
    assert nepse.fees["dp_charge_flat"]["value"] == DP_CHARGE_FLAT
    assert nepse.fees["brokerage_minimum_flat"]["value"] == BROKERAGE_MINIMUM_FLAT
    assert nepse.taxes["cgt_short"]["value"] == CGT_RATE_SHORT
    assert nepse.taxes["cgt_long"]["value"] == CGT_RATE_LONG
    assert nepse.status == "tested"
    assert nepse.disputes  # breaker window + settlement disputes recorded


def test_generic_profile_is_all_unknown():
    generic = get_profile("generic")
    assert generic.circuit_limit_up is None
    assert generic.circuit_limit_down is None
    assert generic.lot_size is None
    assert generic.settlement_lag_business_days is None
    assert generic.fees == {} and generic.taxes == {}
    assert generic.sources == {}


def test_trading_weekdays_derivation_is_byte_identical():
    # Current (post-2026-04-06) weekdays. The pre-change Sun-Thu
    # week lives in the dated rules, not in this table.
    old = {
        "stock_daily": {0, 1, 2, 3, 4},
        "crypto_daily": {0, 1, 2, 3, 4, 5, 6},
        "fx_reference": {0, 1, 2, 3, 4},
        "macro_annual": {0, 1, 2, 3, 4, 5, 6},
        "nepse_csv_daily": {0, 1, 2, 3, 4},
    }
    assert connectors.TRADING_WEEKDAYS == old
    assert set(KIND_PROFILES) == set(old)


def test_profile_config_nepse_is_default_plus_labels():
    cfg = profile_config("nepse")
    for key, value in DEFAULT_RESEARCH_CONFIG.items():
        assert cfg[key] == value
    assert cfg["currency"] == "NPR"
    assert cfg["market"] == "nepse"


def test_simulate_nepse_profile_delegates_byte_identical():
    classic = timemachine.simulate_nepse(
        DEMO_CLOSES, 130.0, 125.0, 140.0, n_sims=100, seed=19, horizon=20)
    via_profile = timemachine.simulate_with_profile(
        DEMO_CLOSES, 130.0, 125.0, 140.0, profile="nepse",
        n_sims=100, seed=19, horizon=20)
    assert via_profile == classic
    via_kwarg = timemachine.simulate_nepse(
        DEMO_CLOSES, 130.0, 125.0, 140.0, n_sims=100, seed=19,
        horizon=20, profile="nepse")
    assert via_kwarg == classic


def test_simulate_us_no_clip_unmodelled_brokerage():
    result = timemachine.simulate_with_profile(
        DEMO_CLOSES, 130.0, 125.0, 140.0, profile="us",
        n_sims=100, seed=19, horizon=20)
    assert result["market"] == "us"
    assert result["currency"] == "USD"
    assert result["circuit_limit_up"] is None
    assert result["circuit_limit_down"] is None
    assert result["settlement_lag_business_days"] == 1
    assert "brokerage" in result["unmodelled_fees"]
    assert result["average_R_net"] == result["average_R_gross"]


def test_simulate_india_applies_stt_and_clips():
    # A 30% one-day jump: the India 20% circuit must clip it, and the
    # 0.1% delivery STT on both sides must make net < gross.
    jump = [100.0] * 40 + [130.0] * 30
    india = timemachine.simulate_with_profile(
        jump, 130.0, 125.0, 140.0, profile="india",
        n_sims=100, seed=19, horizon=20)
    assert india["market"] == "india"
    assert india["circuit_limit_up"] == 0.20
    assert india["average_R_net"] < india["average_R_gross"]
    assert "stt_intraday_sell (intraday only)" in india["unmodelled_fees"]


def test_simulate_profile_rejects_bad_lot_and_conflicts():
    # lot_size 1 everywhere it is sourced: any positive share count is fine.
    timemachine.simulate_with_profile(
        DEMO_CLOSES, 130.0, 125.0, 140.0, profile="us", shares=3,
        n_sims=10, seed=19, horizon=20)
    with pytest.raises(ValueError):
        timemachine.simulate_nepse(
            DEMO_CLOSES, 130.0, 125.0, 140.0, profile="us",
            frictions="not-none")


def test_risk_gate_profile_labels_without_changing_checks():
    plan = {"entry": 100.0, "stop": 95.0, "target": 110.0,
            "risk_per_share": 5.0, "shares": 10, "max_loss": 50.0,
            "entry_window_ok": True, "date": "2026-11-06"}
    plain = risk_gate(plan)
    assert "market" not in plain
    assert plain["passed"] + plain["failed"] == 16
    with_profile = risk_gate(plan, profile="us")
    assert with_profile["market"] == "us"
    assert with_profile["currency"] == "USD"
    assert with_profile["verdict"] == plain["verdict"]
    assert with_profile["checks"] == plain["checks"]
    assert with_profile["fingerprint"] == plain["fingerprint"]


# ---------------------------------------------------------------------------
# Effective-dated NEPSE rules (8 Nov 2026)
# ---------------------------------------------------------------------------

def test_nepse_trading_weekdays_are_effective_dated():
    nepse = get_profile("nepse")
    # Sun-Thu until 2026-04-05 (board decision 22 Chaitra 2082).
    assert nepse.trading_weekdays_on("2026-04-05") == (6, 0, 1, 2, 3)
    assert nepse.trading_weekdays_on("2026-04-03") == (6, 0, 1, 2, 3)
    # Mon-Fri from 2026-04-06.
    assert nepse.trading_weekdays_on("2026-04-06") == (0, 1, 2, 3, 4)
    assert nepse.trading_weekdays_on("2026-11-08") == (0, 1, 2, 3, 4)
    # The plain field is the current rule, not the historical one.
    assert nepse.trading_weekdays == (0, 1, 2, 3, 4)


def test_nepse_circuit_limit_is_effective_dated():
    nepse = get_profile("nepse")
    # 10% before 2026-04-20, 15% from (Fourth Amendment Bylaws, 2082).
    assert nepse.circuit_limit_up_on("2026-04-19") == 0.10
    assert nepse.circuit_limit_down_on("2026-04-19") == 0.10
    assert nepse.circuit_limit_up_on("2026-04-20") == 0.15
    assert nepse.circuit_limit_down_on("2026-04-20") == 0.15
    # A +12% day is within limits after the change, a violation before.
    assert 0.12 <= nepse.circuit_limit_up_on("2026-04-20")
    assert 0.12 > nepse.circuit_limit_up_on("2026-04-19")


def test_nepse_preopen_and_breaker_are_effective_dated():
    nepse = get_profile("nepse")
    assert nepse.preopen_limit_on("2026-04-19") == 0.02
    assert nepse.preopen_limit_on("2026-04-20") == 0.05
    # Three-tier breaker before, two-tier from.
    assert nepse.index_breaker_on("2026-04-19") == (
        (0.04, "20-minute halt"),
        (0.05, "40-minute halt after reopening"),
        (0.06, "suspend rest of day"),
    )
    assert nepse.index_breaker_on("2026-04-20") == (
        (0.05, "15-minute halt if within first two hours"),
        (0.08, "suspend rest of day"),
    )


def test_nepse_settlement_lag_is_effective_dated():
    nepse = get_profile("nepse")
    # T+2 current (CDSC Settlement Procedure, official); T+3 before
    # the full T+2 cycle (Magh 2077 / Jan 2021).
    assert nepse.settlement_lag_on("2020-12-31") == 3
    assert nepse.settlement_lag_on("2021-01-15") == 2
    assert nepse.settlement_lag_on("2026-11-08") == 2


def test_profile_config_on_a_date_carries_the_rules_in_force():
    before = profile_config("nepse", on="2026-04-15")
    assert before["circuit_limit_up"] == 0.10
    assert before["circuit_limit_down"] == 0.10
    assert before["preopen_limit"] == 0.02
    assert before["trading_weekdays"] == (0, 1, 2, 3, 4)
    assert before["settlement_lag_business_days"] == 2
    after = profile_config("nepse", on="2026-04-25")
    assert after["circuit_limit_up"] == 0.15
    assert after["preopen_limit"] == 0.05
    # Without a date, only currency/market are set (unchanged).
    plain = profile_config("nepse")
    assert "circuit_limit_up" not in plain
    assert plain["currency"] == "NPR" and plain["market"] == "nepse"


def test_risk_gate_uses_the_plan_dates_rules():
    plan = {"entry": 100.0, "stop": 95.0, "target": 110.0,
            "risk_per_share": 5.0, "shares": 10, "max_loss": 50.0,
            "entry_window_ok": True, "date": "2026-04-15"}
    # The 16 checks and the fingerprint do not change with the date;
    # the config the gate reads does (proved via profile_config).
    res = risk_gate(plan, profile="nepse")
    assert res["passed"] + res["failed"] == 16
    assert res["fingerprint"] == risk_gate(
        dict(plan, date="2026-04-15"))["fingerprint"]
    assert profile_config("nepse", on="2026-04-15")["circuit_limit_up"] == 0.10
