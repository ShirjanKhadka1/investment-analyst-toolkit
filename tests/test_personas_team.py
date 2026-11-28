"""Tests for the personas team run (Round 10, PR 10.3).

The five global personas (toolkit/rules_mode.py GLOBAL_TEAM)
run on the committed real AAPL fixture (10.2) plus the
market-context fixture (this PR). Scoring, stance rules and
agreement are the fixed rules in docs/round-10-protocol.md
section 10.3 — no rule is tuned after seeing the real run.
"""

import json
from pathlib import Path

import pytest

from toolkit import personas_team, rules_mode

ROOT = Path(__file__).resolve().parent.parent
AAPL_FIXTURE = ROOT / "examples" / "real-aapl-2026-11-27.json"
MARKET_FIXTURE = ROOT / "examples" / "personas-team-inputs-2026-11-27.json"
SYNTHETIC = ROOT / "tests" / "fixtures" / "personas-team-synthetic.json"


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _synthetic():
    return _load(SYNTHETIC)


# --- Input building: real fixtures -----------------------------------------

def test_build_inputs_from_real_fixtures():
    aapl = _load(AAPL_FIXTURE)
    market = _load(MARKET_FIXTURE)
    inputs = personas_team.build_personas_team_inputs(aapl, market)
    assert len(inputs["closes"]) == 251
    assert inputs["closes"][-1] == pytest.approx(358.54)
    metrics = inputs["fundamentals_metrics"]
    assert metrics["revenue_growth_pct"] == pytest.approx(15.06, abs=0.01)
    assert metrics["net_margin_pct"] == pytest.approx(27.60, abs=0.01)
    assert metrics["free_cash_flow_margin_pct"] == pytest.approx(
        28.41, abs=0.01)
    assert metrics["debt_to_equity"] == pytest.approx(2.61, abs=0.01)
    # Interest coverage and ROIC need interest / tax expense, which
    # the 10.2 recording does not carry — they stay UNKNOWN.
    assert "interest_coverage" not in metrics
    assert "return_on_invested_capital_pct" not in metrics
    assert inputs["etf_metrics"]["index_return_1y_pct"] == pytest.approx(
        6.57, abs=0.01)
    fx = inputs["fx_inputs"]
    assert fx["base_currency"] == "EUR"
    assert fx["quote_currency"] == "USD"
    assert fx["pair_change_1y_pct"] == pytest.approx(-5.43, abs=0.01)
    crypto = inputs["crypto_metrics"]
    assert crypto["price_usd"] == pytest.approx(59866.37, abs=0.01)
    assert crypto["all_time_high_usd"] == pytest.approx(96929.33, abs=0.01)
    assert crypto["mean_200d_usd"] == pytest.approx(71810.93, abs=0.01)
    assert crypto["realised_vol_30d_daily_pct"] == pytest.approx(
        2.41, abs=0.01)
    events = inputs["earnings_events"]
    assert len(events) == 1
    event = events[0]
    assert event["date"] == "2026-11-03"
    assert event["reported_revenue"] == 114477000000
    assert event["reported_eps"] == pytest.approx(2.11)
    assert event["expected_eps"] is None
    assert event["expected_revenue"] is None
    assert inputs["headlines"] == []


def test_build_inputs_rejects_mismatched_as_of():
    aapl = _load(AAPL_FIXTURE)
    market = _load(MARKET_FIXTURE)
    market = dict(market, as_of="2026-11-26")
    with pytest.raises(ValueError, match="as_of"):
        personas_team.build_personas_team_inputs(aapl, market)


# --- Scoring ---------------------------------------------------------------

def test_score_sourced_vs_unknown():
    # Fundamentals with values -> sourced; empty -> UNKNOWN.
    sourced = rules_mode.us_fundamentals_check(
        {"net_margin_pct": 25.0})
    assert personas_team.score_persona_section(
        "us_large_cap_fundamentals", sourced)["score"] == "sourced"
    empty = rules_mode.us_fundamentals_check({})
    assert personas_team.score_persona_section(
        "us_large_cap_fundamentals", empty)["score"] == "UNKNOWN"
    # Earnings with a computable surprise -> sourced; reported
    # only -> UNKNOWN (the section output carries no concrete
    # value without an expectation to compare against).
    beat = rules_mode.earnings_check([
        {"date": "2026-10-30", "reported_eps": 1.2, "expected_eps": 1.0}])
    assert personas_team.score_persona_section(
        "earnings", beat)["score"] == "sourced"
    reported_only = rules_mode.earnings_check([
        {"date": "2026-10-30", "reported_eps": 1.2,
         "reported_revenue": 100.0}])
    assert personas_team.score_persona_section(
        "earnings", reported_only)["score"] == "UNKNOWN"
    with pytest.raises(ValueError):
        personas_team.score_persona_section("no_such_persona", {})


# --- Stance rules: every stance value, from the synthetic fixture ---------

_CHECK = {
    "us_large_cap_fundamentals": rules_mode.us_fundamentals_check,
    "etf_index": rules_mode.etf_index_check,
    "fx_macro": rules_mode.fx_macro_check,
    "crypto": rules_mode.crypto_check,
    "earnings": rules_mode.earnings_check,
}


def test_stance_rules_synthetic_fixture():
    """Each persona, each stance value, through the real checks."""
    synthetic = _synthetic()
    for persona in personas_team.PERSONA_KEYS:
        cases = synthetic[persona]
        for expected in ("constructive", "cautious", "neutral",
                         "unknown"):
            section = _CHECK[persona](cases[expected])
            got = personas_team.stance_for_persona(persona, section)
            want = "UNKNOWN" if expected == "unknown" else expected
            assert got["stance"] == want, (
                f"{persona} case {expected}: got {got['stance']}")
    # Earnings with reported values but no expectation is UNKNOWN.
    section = _CHECK["earnings"](
        synthetic["earnings"]["unknown_reported_only"])
    assert personas_team.stance_for_persona(
        "earnings", section)["stance"] == "UNKNOWN"
    with pytest.raises(ValueError):
        personas_team.stance_for_persona("no_such_persona", {})


def test_stance_fundamentals_counts_healthy_vs_flagged():
    """The fundamentals stance is a majority of sourced checks."""
    section = rules_mode.us_fundamentals_check(
        {"debt_to_equity": 2.5, "interest_coverage": 1.2,
         "revenue_growth_pct": 10.0, "net_margin_pct": 20.0})
    stance = personas_team.stance_for_persona(
        "us_large_cap_fundamentals", section)
    assert stance["stance"] == "neutral"
    assert stance["detail"] == {"healthy": ["net_margin_pct",
                                            "revenue_growth_pct"],
                                "weak": ["debt_to_equity",
                                         "interest_coverage"]}


# --- Agreement -------------------------------------------------------------

def test_agreement_rate_pairs_and_table():
    stances = {"us_large_cap_fundamentals": "constructive",
               "etf_index": "constructive",
               "fx_macro": "cautious",
               "crypto": "cautious",
               "earnings": "UNKNOWN"}
    result = personas_team.agreement_rate(stances)
    assert result["n_personas"] == 4
    assert result["n_pairs"] == 6
    assert result["matching_pairs"] == 2
    assert result["rate"] == pytest.approx(1.0 / 3.0)
    assert result["chance_baseline"] == pytest.approx(1.0 / 3.0)
    assert len(result["pairs"]) == 6
    matches = {(p["a"], p["b"]) for p in result["pairs"] if p["match"]}
    assert matches == {("etf_index", "us_large_cap_fundamentals"),
                       ("crypto", "fx_macro")}


def test_agreement_rate_all_match_and_none():
    all_same = {k: "constructive" for k in personas_team.PERSONA_KEYS}
    assert personas_team.agreement_rate(all_same)["rate"] == 1.0
    one = {"us_large_cap_fundamentals": "constructive",
           "etf_index": "UNKNOWN", "fx_macro": "UNKNOWN",
           "crypto": "UNKNOWN", "earnings": "UNKNOWN"}
    result = personas_team.agreement_rate(one)
    assert result["rate"] is None
    assert result["n_personas"] == 1
    none = {k: "UNKNOWN" for k in personas_team.PERSONA_KEYS}
    assert personas_team.agreement_rate(none)["rate"] is None


# --- Team run: real AAPL fixture -------------------------------------------

def test_run_personas_team_real_aapl():
    aapl = _load(AAPL_FIXTURE)
    market = _load(MARKET_FIXTURE)
    result = personas_team.run_personas_team(aapl, market)
    assert result["run_id"] == personas_team.RUN_ID
    assert result["symbol"] == "AAPL"
    assert result["as_of"] == "2026-11-27"
    assert result["last_close"] == pytest.approx(358.54)
    scores = result["scores"]
    assert [scores[k]["score"] for k in personas_team.PERSONA_KEYS] == [
        "sourced", "sourced", "sourced", "sourced", "UNKNOWN"]
    stances = result["stances"]
    assert stances["us_large_cap_fundamentals"]["stance"] == "constructive"
    assert stances["etf_index"]["stance"] == "constructive"
    assert stances["fx_macro"]["stance"] == "cautious"
    assert stances["crypto"]["stance"] == "cautious"
    assert stances["earnings"]["stance"] == "UNKNOWN"
    agreement = result["agreement"]
    assert agreement["n_personas"] == 4
    assert agreement["n_pairs"] == 6
    assert agreement["matching_pairs"] == 2
    assert agreement["rate"] == pytest.approx(1.0 / 3.0)
    # Technical is reported alongside, not a persona stance.
    assert result["technical"]["n_bars"] == 251
    assert result["technical"]["rsi_14"] is not None
    assert result["sentiment"]["label"] == "NO HEADLINES"
    # All five persona sections plus technical and sentiment ran.
    assert set(result["sections"]) >= {
        "us_fundamentals", "etf_index", "fx_macro", "crypto",
        "earnings", "technical", "sentiment"}


def test_run_personas_team_reproducible():
    """Same fixtures re-run byte-identically (fixed run id, clock)."""
    aapl = _load(AAPL_FIXTURE)
    market = _load(MARKET_FIXTURE)
    first = personas_team.run_personas_team(aapl, market)
    second = personas_team.run_personas_team(aapl, market)
    assert (json.dumps(first, sort_keys=True)
            == json.dumps(second, sort_keys=True))
