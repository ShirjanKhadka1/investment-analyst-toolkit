"""Tests for cross-sectional ranking, event studies and the NEPSE
floorsheet importer (PR 8.3, Round 8).

Offline only: every series here is synthetic or hand-built. No
network, no EDGAR, no Yahoo, no real prices. The EDGAR bridge is
tested with a monkeypatched ``get_fundamentals`` returning fixture
facts, never a live call. The evaluation on the seeded synthetic
universe lives in docs/cross-sectional-model-card.md and is
reproduced by scripts/evaluate_cross_sectional.py, never by this
file — except for one pinning test that runs that script and
asserts its deterministic verdicts, so the card's numbers cannot
drift from a rerun.

What this file proves, and how:

- Unit: momentum (hand-computed indexing), value/quality proxies
  (exact ratios, error cases), z-scores (exact values, zero
  variance), deciles (known assignment, ties, too-few raises).
- Ranking: composite ordering, unknown components, missing-data
  handling.
- Costs: NEPSE sourced schedule charged exactly (NPR 100,000 ->
  0.74% round trip), explicit cost_fraction path, UnknownCostError
  for unsourced markets, CGT exclusion stated in the basis.
- Decision rule: both branches — a planted constant spread says
  "beats zero", a zero-mean spread says "no edge" — plus CI
  determinism.
- Event study: market-model alpha/beta recovered from a
  hand-built linear series, a planted +5% jump detected as
  significant, mean-adjusted fallback labelled, loud errors for a
  non-trading-day event, a short series and misaligned market
  data. Aggregation: the centred-null bootstrap (a positive CAAR
  with tight CARs is significant; a zero-mean set is not).
- Loaders: the bundled SYNTHETIC demo CSVs load with exact
  broker-flow values; bad rows raise loudly (bad side, fractional
  quantity, bad date, unknown event type, missing column/source).
"""
import datetime
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from toolkit import cross_sectional as xs
from toolkit import event_study as es
from toolkit import nepse_floorsheet as nf
from toolkit.strategy_backtester import UnknownCostError

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------

def test_momentum_score_hand_computed():
    assert xs.momentum_score([100.0, 110.0, 121.0],
                             formation=2, skip=0) == pytest.approx(0.21)
    assert xs.momentum_score([100.0, 110.0, 121.0],
                             formation=2, skip=1) == pytest.approx(0.10)


def test_momentum_score_default_window_indexing():
    # 130 closes growing 1% a day: P[-22] / P[-127] - 1 = 1.01**105 - 1.
    closes = [100.0 * 1.01 ** i for i in range(130)]
    assert xs.momentum_score(closes) == pytest.approx(1.01 ** 105 - 1,
                                                      rel=1e-9)


def test_momentum_score_errors():
    with pytest.raises(ValueError):
        xs.momentum_score([100.0] * 10)  # shorter than formation + 1
    with pytest.raises(ValueError):
        xs.momentum_score([100.0] * 130, formation=0)
    with pytest.raises(ValueError):
        xs.momentum_score([100.0] * 130, skip=126)  # skip >= formation
    with pytest.raises(ValueError):
        xs.momentum_score([100.0] * 126 + [0.0])  # non-positive close


# ---------------------------------------------------------------------------
# Value / quality proxies
# ---------------------------------------------------------------------------

def test_proxy_ratios_exact():
    assert xs.earnings_yield(500.0, 5000.0) == pytest.approx(0.10)
    assert xs.book_to_price(2000.0, 5000.0) == pytest.approx(0.40)
    assert xs.return_on_equity(500.0, 2000.0) == pytest.approx(0.25)
    assert xs.gross_margin(3000.0, 10000.0) == pytest.approx(0.30)


def test_proxy_ratio_errors():
    with pytest.raises(ValueError):
        xs.earnings_yield(500.0, 0.0)
    with pytest.raises(ValueError):
        xs.book_to_price(2000.0, -5.0)
    with pytest.raises(ValueError):
        xs.return_on_equity(500.0, 0.0)
    with pytest.raises(ValueError):
        xs.gross_margin(3000.0, 0.0)


PLAIN_FACTS = {
    "NetIncomeLoss": 500.0,
    "StockholdersEquity": 2000.0,
    "Revenues": 10000.0,
    "GrossProfit": 3000.0,
    "Assets": 8000.0,
}


def test_proxies_from_plain_facts():
    out = xs.proxies_from_facts(PLAIN_FACTS, 5000.0)
    assert out["earnings_yield"] == pytest.approx(0.10)
    assert out["book_to_price"] == pytest.approx(0.40)
    assert out["roe"] == pytest.approx(0.25)
    assert out["gross_margin"] == pytest.approx(0.30)
    assert out["assets"] == pytest.approx(8000.0)
    assert out["provenance"] == {}


def test_proxies_from_fundamentals_shape_with_provenance():
    facts = {"values": {
        tag: {"value": value, "form": "10-K", "filed": "2026-02-01",
              "end": "2025-12-31"}
        for tag, value in PLAIN_FACTS.items()}}
    out = xs.proxies_from_facts(facts, 5000.0)
    assert out["earnings_yield"] == pytest.approx(0.10)
    assert out["provenance"]["NetIncomeLoss"] == {
        "form": "10-K", "filed": "2026-02-01", "end": "2025-12-31"}


def test_proxies_from_facts_missing_concept_raises():
    facts = dict(PLAIN_FACTS)
    del facts["GrossProfit"]
    with pytest.raises(ValueError, match="GrossProfit"):
        xs.proxies_from_facts(facts, 5000.0)


def test_proxies_from_edgar_uses_fixture_facts(monkeypatch):
    fixture = {
        "ticker": "FAKE", "cik": 123,
        "source": "SEC EDGAR companyfacts",
        "values": {tag: {"value": value, "form": "10-K",
                         "filed": "2026-02-01", "end": "2025-12-31"}
                   for tag, value in PLAIN_FACTS.items()},
    }
    captured = {}

    def fake_get_fundamentals(ticker, concepts, user_agent=None):
        captured["ticker"] = ticker
        captured["concepts"] = concepts
        return fixture

    monkeypatch.setattr(xs, "get_fundamentals", fake_get_fundamentals)
    out = xs.proxies_from_edgar("FAKE", 5000.0)
    assert captured["ticker"] == "FAKE"
    assert captured["concepts"] == list(xs.PROXY_CONCEPTS)
    assert out["earnings_yield"] == pytest.approx(0.10)
    assert out["ticker"] == "FAKE"
    assert out["cik"] == 123
    assert out["source"] == "SEC EDGAR companyfacts"


# ---------------------------------------------------------------------------
# Deciles and ranking
# ---------------------------------------------------------------------------

def test_assign_deciles_known_assignment():
    scores = {f"s{i}": float(i) for i in range(1, 11)}
    deciles = xs.assign_deciles(scores)
    assert deciles == {f"s{i}": i for i in range(1, 11)}


def test_assign_deciles_twenty_symbols_two_per_decile():
    scores = {f"s{i:02d}": float(i) for i in range(1, 21)}
    deciles = xs.assign_deciles(scores)
    assert deciles["s01"] == 1 and deciles["s02"] == 1
    assert deciles["s03"] == 2 and deciles["s19"] == 10
    assert deciles["s20"] == 10


def test_assign_deciles_ties_deterministic_by_symbol():
    scores = {s: 5.0 for s in ("b", "a", "c", "d", "e",
                               "f", "g", "h", "i", "j")}
    first = xs.assign_deciles(scores)
    assert xs.assign_deciles(scores) == first
    assert first["a"] == 1  # lowest rank goes to the first symbol name
    assert first["j"] == 10


def test_assign_deciles_errors():
    with pytest.raises(ValueError):
        xs.assign_deciles({f"s{i}": float(i) for i in range(9)})
    with pytest.raises(ValueError):
        xs.assign_deciles({"a": 1.0, "b": float("nan"),
                           "c": 3.0, "d": 4.0, "e": 5.0, "f": 6.0,
                           "g": 7.0, "h": 8.0, "i": 9.0, "j": 10.0})
    with pytest.raises(ValueError):
        xs.assign_deciles({f"s{i}": float(i) for i in range(1, 11)},
                          n_deciles=1)


def test_zscores_exact_and_zero_variance():
    out = xs._zscores({"a": 1.0, "b": 2.0, "c": 3.0})
    sd = math.sqrt(2.0 / 3.0)
    assert out["a"] == pytest.approx(-1.0 / sd)
    assert out["b"] == pytest.approx(0.0)
    assert out["c"] == pytest.approx(1.0 / sd)
    assert xs._zscores({"a": 5.0, "b": 5.0}) == {"a": 0.0, "b": 0.0}


def _ten_symbol_components():
    # Momentum 1..10, value components constant (zero variance ->
    # z = 0), quality monotone in the same order as momentum.
    return {
        f"s{i:02d}": {
            "momentum": float(i),
            "earnings_yield": 0.05,
            "book_to_price": 0.40,
            "roe": 0.01 * i,
            "gross_margin": 0.30,
        }
        for i in range(1, 11)
    }


def test_rank_universe_deciles_follow_composite():
    ranked = xs.rank_universe(_ten_symbol_components())
    assert ranked["s01"]["decile"] == 1
    assert ranked["s10"]["decile"] == 10
    assert ranked["s10"]["composite"] > ranked["s01"]["composite"]
    assert ranked["s05"]["value"] == pytest.approx(0.0)
    assert ranked["s10"]["quality"] > ranked["s01"]["quality"]


def test_rank_universe_errors():
    with pytest.raises(ValueError, match="unknown component"):
        xs.rank_universe({f"s{i:02d}": {"momentum": float(i),
                                        "bogus": 1.0}
                          for i in range(1, 11)})
    with pytest.raises(ValueError, match="at least 10"):
        xs.rank_universe({"s1": {"momentum": 1.0}})
    # A component held by only one symbol cannot be z-scored, so a
    # universe where every symbol's only component is unique fails.
    components = {f"s{i:02d}": {"momentum": float(i)}
                  for i in range(1, 10)}
    components["s10"] = {"roe": 0.5}
    with pytest.raises(ValueError):
        xs.rank_universe(components)


# ---------------------------------------------------------------------------
# Costs and decile spreads
# ---------------------------------------------------------------------------

def test_nepse_round_trip_cost_exact():
    # NPR 100,000: slab 0.33% -> 330 + SEBON 15 + DP 25 = 370 per leg.
    fraction, basis = xs.round_trip_cost_fraction(
        "nepse", trade_value=100_000)
    assert fraction == pytest.approx(740.0 / 100_000)
    assert "CGT excluded" in basis


def test_round_trip_cost_explicit_fraction():
    fraction, basis = xs.round_trip_cost_fraction(
        "us", cost_fraction=0.001)
    assert fraction == pytest.approx(0.002)
    assert "cost_fraction=0.001" in basis


def test_round_trip_cost_unknown_market_raises():
    with pytest.raises(UnknownCostError):
        xs.round_trip_cost_fraction("us", trade_value=100_000)
    with pytest.raises(UnknownCostError):
        xs.round_trip_cost_fraction("generic", trade_value=100_000)
    with pytest.raises(ValueError):
        xs.round_trip_cost_fraction("nepse")  # no value, no fraction


def test_net_forward_return_deducts_costs():
    net, fraction, _basis = xs.net_forward_return(
        0.05, "nepse", trade_value=100_000)
    assert fraction == pytest.approx(0.0074)
    assert net == pytest.approx(0.05 - 0.0074)


def test_spread_verdict_both_branches():
    planted = xs.spread_verdict([0.05] * 12)
    assert planted["mean_spread"] == pytest.approx(0.05)
    assert planted["ci_95"] == (pytest.approx(0.05),
                                pytest.approx(0.05))
    assert planted["verdict"] == "beats zero"
    noise = xs.spread_verdict([0.05, -0.05] * 6)
    assert noise["mean_spread"] == pytest.approx(0.0)
    assert noise["ci_95"][0] < 0 < noise["ci_95"][1]
    assert noise["verdict"] == "no edge"
    losing = xs.spread_verdict([-0.05] * 12)
    assert losing["verdict"] == "no edge"


def test_spread_verdict_deterministic_and_guarded():
    first = xs.spread_verdict([0.03, -0.01, 0.02, 0.01])
    second = xs.spread_verdict([0.03, -0.01, 0.02, 0.01])
    assert first == second
    with pytest.raises(ValueError):
        xs.spread_verdict([0.05])


# ---------------------------------------------------------------------------
# Event studies
# ---------------------------------------------------------------------------

def _linear_series(n_bars=60, jump=0.0, event_idx=40):
    """Market + symbol closes where symbol returns are exactly
    0.001 + 1.5 * market returns + alternating +/-0.001 noise.
    ``jump`` multiplies symbol closes from ``event_idx`` onward.
    """
    market_returns = [0.01 * ((i % 5) - 2) for i in range(n_bars - 1)]
    market_closes = [100.0]
    for r in market_returns:
        market_closes.append(market_closes[-1] * (1.0 + r))
    symbol_closes = [100.0]
    for i, r in enumerate(market_returns):
        noise = 0.001 if i % 2 == 0 else -0.001
        symbol_closes.append(symbol_closes[-1] * (1.0 + 0.001 + 1.5 * r
                                                   + noise))
    if jump:
        symbol_closes = [
            c * (1.0 + jump) if i >= event_idx else c
            for i, c in enumerate(symbol_closes)]
    start = datetime.date(2026, 1, 5)
    dates = [(start + datetime.timedelta(days=i)).isoformat()
             for i in range(n_bars)]
    market = list(zip(dates, market_closes))
    prices = list(zip(dates, symbol_closes))
    return prices, market, dates[event_idx]


def test_event_study_recovers_market_model():
    prices, market, event_date = _linear_series()
    out = es.event_study(prices, event_date, market_prices=market,
                         estimation_window=(-35, -6),
                         event_window=(-2, 2))
    assert out["model"] == "market"
    assert out["alpha"] == pytest.approx(0.001, abs=5e-4)
    assert out["beta"] == pytest.approx(1.5, abs=0.01)
    assert out["n_estimation"] == 30
    assert len(out["abnormal_returns"]) == 5
    assert abs(out["car"]) < 0.005
    assert out["significant"] is False


def test_event_study_planted_jump_is_significant():
    prices, market, event_date = _linear_series(jump=0.05)
    out = es.event_study(prices, event_date, market_prices=market,
                         estimation_window=(-35, -6),
                         event_window=(-2, 2))
    assert out["car"] > 0.04
    assert out["t_stat"] > 1.96
    assert out["bootstrap_p"] < 0.05
    assert out["significant"] is True


def test_event_study_mean_adjusted_fallback_labelled():
    prices, _market, event_date = _linear_series()
    out = es.event_study(prices, event_date, market_prices=None,
                         estimation_window=(-35, -6),
                         event_window=(-2, 2))
    assert out["model"] == "mean_adjusted"
    assert out["beta"] == 0.0
    assert out["alpha"] == pytest.approx(
        sum(es._simple_returns([c for _, c in prices])[4:34]) / 30)


def test_event_study_errors():
    prices, market, event_date = _linear_series()
    with pytest.raises(es.EventStudyError, match="not a trading day"):
        es.event_study(prices, "2026-03-10", market_prices=market,
                       estimation_window=(-35, -6), event_window=(-2, 2))
    with pytest.raises(es.EventStudyError, match="too short"):
        short = prices[:20]
        es.event_study(short, short[15][0], market_prices=market[:20],
                       estimation_window=(-35, -6), event_window=(-2, 2))
    with pytest.raises(es.EventStudyError, match="at least 30"):
        es.event_study(prices, event_date, market_prices=market,
                       estimation_window=(-10, -6),
                       event_window=(-2, 2))
    with pytest.raises(es.EventStudyError, match="end before the event"):
        es.event_study(prices, event_date, market_prices=market,
                       estimation_window=(-35, -1),
                       event_window=(-2, 2))
    shifted = [(d, c) for (d, c) in market]
    shifted = shifted[1:] + [("2026-03-06", shifted[-1][1])]
    with pytest.raises(es.EventStudyError, match="dates differ"):
        es.event_study(prices, event_date, market_prices=shifted,
                       estimation_window=(-35, -6),
                       event_window=(-2, 2))


def test_aggregate_event_study_centred_bootstrap():
    tight = [{"car": c} for c in
             (0.04, 0.05, 0.06, 0.05, 0.04, 0.06, 0.05, 0.05)]
    out = es.aggregate_event_study(tight)
    assert out["caar"] == pytest.approx(0.05)
    assert out["t_stat"] > 1.96
    assert out["bootstrap_p"] < 0.05
    assert out["significant"] is True
    assert out["small_n_caveat"] is True
    zero_mean = [{"car": c} for c in (0.05, -0.05, 0.04, -0.04)]
    out2 = es.aggregate_event_study(zero_mean)
    assert out2["caar"] == pytest.approx(0.0)
    assert out2["significant"] is False
    with pytest.raises(es.EventStudyError):
        es.aggregate_event_study([])


def test_run_event_study_end_to_end_small():
    prices, market, event_date = _linear_series()
    events = [
        {"symbol": "SYN-A", "event_date": event_date,
         "event_type": "dividend", "detail": ""},
        {"symbol": "SYN-B", "event_date": event_date,
         "event_type": "earnings", "detail": ""},
    ]
    out = es.run_event_study(
        events, {"SYN-A": prices, "SYN-B": prices},
        market_prices=market,
        estimation_window=(-35, -6), event_window=(-2, 2))
    assert len(out["events"]) == 2
    assert out["events"][0]["symbol"] == "SYN-A"
    assert out["events"][0]["event_type"] == "dividend"
    assert out["aggregate"]["n_events"] == 2
    assert out["aggregate"]["small_n_caveat"] is True
    with pytest.raises(es.EventStudyError, match="no price series"):
        es.run_event_study(events, {"SYN-A": prices},
                           market_prices=market,
                           estimation_window=(-35, -6),
                           event_window=(-2, 2))


# ---------------------------------------------------------------------------
# Event CSV loader
# ---------------------------------------------------------------------------

def test_load_event_csv_demo():
    events, source = es.load_event_csv(
        FIXTURES / "nepse_events_SYNTHETIC.csv",
        "SYNTHETIC demo events, invented for testing")
    assert len(events) == 8
    assert events[0] == {"symbol": "SYNTHETIC-A",
                         "event_date": "2026-10-12",
                         "event_type": "dividend",
                         "detail": "10% cash dividend (synthetic)"}
    assert {e["event_type"] for e in events} == {
        "dividend", "bonus", "right", "book_closure", "earnings"}
    assert "SYNTHETIC" in source


def test_load_event_csv_errors(tmp_path):
    good = ("symbol,event_date,event_type,detail\n"
            "SYN-A,2026-10-12,dividend,10% cash\n")
    path = tmp_path / "events.csv"
    path.write_text(good)
    with pytest.raises(es.EventStudyError, match="source must be named"):
        es.load_event_csv(path, "  ")
    path.write_text("symbol,event_date\nSYN-A,2026-10-12\n")
    with pytest.raises(es.EventStudyError, match="expected columns"):
        es.load_event_csv(path, "test source")
    path.write_text("symbol,event_date,event_type\n"
                    "SYN-A,2026-10-12,split\n")
    with pytest.raises(es.EventStudyError, match="unknown event_type"):
        es.load_event_csv(path, "test source")
    path.write_text("symbol,event_date,event_type\n"
                    "SYN-A,12 Oct 2026,dividend\n")
    with pytest.raises(es.EventStudyError, match="must be ISO"):
        es.load_event_csv(path, "test source")
    path.write_text("symbol,event_date,event_type\n")
    with pytest.raises(es.EventStudyError, match="no data rows"):
        es.load_event_csv(path, "test source")
    with pytest.raises(es.EventStudyError, match="cannot open"):
        es.load_event_csv(tmp_path / "missing.csv", "test source")


# ---------------------------------------------------------------------------
# Floorsheet importer + analytics
# ---------------------------------------------------------------------------

def test_load_floorsheet_csv_demo():
    rows, source = nf.load_floorsheet_csv(
        FIXTURES / "nepse_floorsheet_SYNTHETIC.csv",
        "SYNTHETIC demo floorsheet, invented for testing")
    assert len(rows) == 10
    assert rows[0] == {"date": "2026-11-13", "broker": "58",
                       "symbol": "SYNTHETIC-A", "side": "buy",
                       "quantity": 100, "price": 500.0, "value": 50000.0}
    assert "SYNTHETIC" in source


def test_broker_flow_exact():
    rows, _ = nf.load_floorsheet_csv(
        FIXTURES / "nepse_floorsheet_SYNTHETIC.csv", "demo")
    flow = nf.broker_flow(rows)
    assert flow["58"] == {"buy_quantity": 150, "sell_quantity": 500,
                          "buy_value": 75500.0, "sell_value": 100000.0,
                          "net_flow": -24500.0, "net_quantity": -350,
                          "n_trades": 3}
    assert flow["45"]["net_flow"] == pytest.approx(37000.0)
    assert flow["45"]["net_quantity"] == 310
    assert flow["21"]["net_flow"] == pytest.approx(70250.0)
    assert flow["21"]["n_trades"] == 4
    with pytest.raises(nf.FloorsheetError):
        nf.broker_flow([])


def test_top_flow_and_concentration_exact():
    rows, _ = nf.load_floorsheet_csv(
        FIXTURES / "nepse_floorsheet_SYNTHETIC.csv", "demo")
    assert nf.top_flow(rows, "SYNTHETIC-A", side="buy") == [
        ("21", 99000.0), ("58", 75500.0), ("45", 15600.0)]
    assert nf.top_flow(rows, "SYNTHETIC-A", side="sell") == [
        ("45", 60600.0), ("21", 40000.0)]
    assert nf.top_flow(rows, "SYNTHETIC-B", side="buy", top_n=1) == [
        ("45", 82000.0)]
    assert nf.top_flow(rows, "NOPE", side="buy") == []
    conc_a = nf.concentration(rows, "SYNTHETIC-A")
    assert conc_a["n_brokers"] == 3
    assert conc_a["top3_share"] == pytest.approx(1.0)
    assert conc_a["hhi"] == pytest.approx(0.3648, abs=1e-4)
    conc_b = nf.concentration(rows, "SYNTHETIC-B")
    assert conc_b["hhi"] == pytest.approx(0.3904, abs=1e-4)
    with pytest.raises(nf.FloorsheetError):
        nf.concentration(rows, "NOPE")


def test_symbol_flow_demo():
    rows, _ = nf.load_floorsheet_csv(
        FIXTURES / "nepse_floorsheet_SYNTHETIC.csv", "demo")
    out = nf.symbol_flow(rows, "SYNTHETIC-A")
    assert out["total_quantity"] == 580
    assert out["total_value"] == pytest.approx(290700.0)
    assert out["top_buyers"][0] == ("21", 99000.0)
    assert out["top_sellers"][0] == ("45", 60600.0)
    assert out["n_brokers"] == 3
    with pytest.raises(nf.FloorsheetError):
        nf.symbol_flow(rows, "NOPE")
    with pytest.raises(ValueError):
        nf.top_flow(rows, "SYNTHETIC-A", side="hold")


def test_load_floorsheet_csv_errors(tmp_path):
    header = "date,broker,symbol,side,quantity,price\n"
    cases = [
        ("bad side", header + "2026-11-13,58,SYN-A,hold,10,500\n",
         "side must be"),
        ("fractional quantity",
         header + "2026-11-13,58,SYN-A,buy,10.5,500\n",
         "quantity must be"),
        ("zero quantity", header + "2026-11-13,58,SYN-A,buy,0,500\n",
         "quantity must be"),
        ("negative price",
         header + "2026-11-13,58,SYN-A,buy,10,-5\n",
         "price must be"),
        ("bad date", header + "13 Nov 2026,58,SYN-A,buy,10,500\n",
         "date must be ISO"),
        ("missing column", ("date,broker,symbol,side,quantity\n"
         "2026-11-13,58,SYN-A,buy,10\n"), "expected columns"),
        ("no rows", header, "no data rows"),
    ]
    for _name, text, match in cases:
        path = tmp_path / "floor.csv"
        path.write_text(text)
        with pytest.raises(nf.FloorsheetError, match=match):
            nf.load_floorsheet_csv(path, "test source")
    path = tmp_path / "floor.csv"
    path.write_text(header + "2026-11-13,58,SYN-A,buy,10,500\n")
    with pytest.raises(nf.FloorsheetError, match="source must be named"):
        nf.load_floorsheet_csv(path, "")
    with pytest.raises(nf.FloorsheetError, match="cannot open"):
        nf.load_floorsheet_csv(tmp_path / "missing.csv", "test source")


# ---------------------------------------------------------------------------
# Evaluation script pinning + capability-matrix rows
# ---------------------------------------------------------------------------

def test_evaluate_script_verdicts_are_deterministic():
    proc = subprocess.run(
        [sys.executable, "scripts/evaluate_cross_sectional.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
        check=False)
    assert proc.returncode == 0, proc.stderr[-2000:]
    result = json.loads(proc.stdout)
    assert result["seed"] == 20261114
    for market in ("nepse", "generic"):
        for rule, verdict in result["ranking"][market]["rules"].items():
            assert verdict["verdict"] == "no edge", (market, rule)
            assert verdict["n"] == 12
    assert result["ranking"]["nepse"]["round_trip_cost_fraction"] == \
        pytest.approx(0.0074)
    assert result["ranking"]["generic"]["round_trip_cost_fraction"] == \
        pytest.approx(0.002)
    assert result["event_study"]["n_events"] == 8
    assert result["event_study"]["aggregate"]["significant"] is False
    assert result["event_study"]["aggregate"]["small_n_caveat"] is True
    assert result["floorsheet"]["n_rows"] == 10


def test_capability_matrix_has_the_three_new_rows():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import generate_capability_matrix as gen

    rows = [(cap, status, test)
            for _name, _repo, _lic, caps in gen.REPOS
            for cap, status, _ev, test in caps]
    wanted = {
        "Cross-sectional ranking (momentum/value/quality proxies, "
        "decile tests net of costs)": "tests/test_cross_sectional.py",
        "Event studies (NEPSE corporate actions on user CSVs + "
        "global earnings)": "tests/test_cross_sectional.py",
        "NEPSE floorsheet CSV importer + broker-flow analytics":
            "tests/test_cross_sectional.py",
    }
    for capability, test_path in wanted.items():
        found = [r for r in rows if r[0] == capability]
        assert len(found) == 1, capability
        assert found[0][1] == "implemented"
        assert found[0][2] == test_path
        assert (REPO_ROOT / test_path).exists()
