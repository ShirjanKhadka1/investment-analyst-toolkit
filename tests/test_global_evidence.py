"""Step 4 evidence: the SPY worked example reproduces from its fixture.

Every number pinned here was produced by running the toolkit over
data/fixtures/spy_daily.csv (real Yahoo data, 2025-11-07 -> 2026-11-06;
provenance in spy_daily.PROVENANCE.md). If any number changes, the
fixture or the engine changed — investigate, do not just update.
"""

import pytest

from toolkit import data as data_mod
from toolkit import predictive, rules_mode, timemachine
from toolkit.position_plan import risk_gate

FIXTURE = "data/fixtures/spy_daily.csv"


@pytest.fixture(scope="module")
def spy():
    pairs = data_mod.load_csv_prices(FIXTURE)
    return pairs, [c for _, c in pairs]


def test_fixture_is_real_dated_spy(spy):
    pairs, closes = spy
    assert len(pairs) == 251
    assert pairs[0] == ("2025-11-07", 670.97)
    assert pairs[-1] == ("2026-11-06", 735.29)
    assert closes[-1] / closes[0] - 1.0 == pytest.approx(0.0959, abs=0.0001)


def test_technical_read_on_spy(spy):
    _, closes = spy
    tech = rules_mode.technical_read(closes)
    assert tech["momentum_20d_pct"] == pytest.approx(-5.56, abs=0.01)
    assert tech["price_vs_mean_20d_pct"] == pytest.approx(-3.68, abs=0.01)
    assert tech["rsi_14"] == pytest.approx(57.84, abs=0.01)


def test_time_machine_us_profile_on_spy(spy):
    _, closes = spy
    tm = timemachine.simulate_with_profile(
        closes, 735.29, 700.00, 810.00, profile="us",
        n_sims=1000, seed=19, horizon=20)
    assert tm["market"] == "us"
    assert tm["currency"] == "USD"
    assert tm["settlement_lag_business_days"] == 1
    assert tm["target_first"] == pytest.approx(0.007, abs=0.001)
    assert tm["stop_first"] == pytest.approx(0.082, abs=0.001)
    assert tm["break_even"] == pytest.approx(0.3208, abs=0.0001)
    assert "brokerage" in tm["unmodelled_fees"]
    assert "capital_gains" in tm["unmodelled_fees"]


def test_risk_gate_us_sizes_on_spy(spy):
    pairs, _ = spy
    for shares, verdict in ((10, "BLOCK"), (3, "BLOCK"), (2, "PASS")):
        plan = {"entry": 735.29, "stop": 700.00, "target": 810.00,
                "risk_per_share": 35.29, "shares": shares,
                "max_loss": 35.29 * shares,
                "entry_window_ok": True, "date": pairs[-1][0]}
        gate = risk_gate(plan, profile="us")
        assert gate["verdict"] == verdict
        assert gate["market"] == "us"
    assert risk_gate({"entry": 735.29, "stop": 700.00, "target": 810.00,
                      "risk_per_share": 35.29, "shares": 2,
                      "max_loss": 70.58, "entry_window_ok": True,
                      "date": "2026-11-06"}, profile="us")["passed"] == 16


def test_walk_forward_on_spy_is_no_edge(spy):
    _, closes = spy
    wf = predictive.compare_models_walk_forward(closes, train_size=200)
    assert wf["n_test"] == 51
    assert wf["best_model"] == "naive"
    assert wf["beats_naive"] is False
    assert wf["naive_wins"] is True
    assert wf["best_mae"] == pytest.approx(4.9124, abs=0.0001)
    assert wf["naive_mae"] == pytest.approx(4.9124, abs=0.0001)
