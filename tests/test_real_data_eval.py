"""Tests for the real-data evaluation script (Round E, E.1).

No network: every test runs on the bundled slice of the real
2026-11-15 cache (tests/fixtures/real_data_eval_slice.json) —
12 caps + SPY, real Yahoo closes from 2024-06-03, real EDGAR
point-in-time fundamentals at the 12 ranking dates, and real
10-Q filing dates for AAPL/MSFT. The slice is a deterministic
fixture derived from real data; nothing here fetches anything.

The harness and direction tests run on a further-trimmed
300-close window (train_size 252 leaves 47 test folds) so the
suite stays fast; the full 2y runs are the script's own
reproducibility record, not a test.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "scripts"))

import evaluate_real_data as evd

FIXTURE = (Path(__file__).resolve().parent / "fixtures"
           / "real_data_eval_slice.json")


def _load_slice():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _trimmed(cache, n_closes=300):
    """A cache copy with each price series cut to its last
    ``n_closes`` bars (for the slow walk-forward tests)."""
    trimmed = dict(cache)
    trimmed["prices"] = {s: pairs[-n_closes:]
                         for s, pairs in cache["prices"].items()}
    return trimmed


def test_slice_is_real_cached_data():
    cache = _load_slice()
    assert cache["run_date"] == "2026-11-15"
    assert len(cache["prices"]) == 13
    assert len(cache["ranking_dates"]) == 12
    # XOM's fundamentals come from the real filer (CIK 34088 via
    # the override), not the SEC ticker table's ghost CIK.
    assert cache["pit_fundamentals"]["XOM"]["cik"] == 34088
    xom = cache["pit_fundamentals"]["XOM"]["per_ranking_date"][
        "2024-12-31"]
    assert xom["Revenues"]["value"] == 344_582_000_000
    # GOOGL/META carry no shares outstanding in EDGAR at all.
    for symbol in ("GOOGL", "META"):
        entry = cache["pit_fundamentals"][symbol][
            "per_ranking_date"]["2024-12-31"]
        assert entry["market_cap"] is None


def test_cik_override_and_revenue_candidates():
    assert evd.CIK_OVERRIDES == {"XOM": 34088}
    revenue_tags = [tag for _tax, tag in
                    evd.CONCEPT_CANDIDATES["Revenues"]]
    assert "RevenuesNetOfInterestExpense" in revenue_tags
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" \
        in revenue_tags


def test_ranking_on_slice():
    cache = _load_slice()
    result = evd._evaluate_ranking(
        cache, symbols=list(cache["pit_fundamentals"]))
    assert result["ranking_dates"] == cache["ranking_dates"]
    assert result["forward_bars"] == 21
    assert result["round_trip_cost_fraction"] == 0.002
    for rule in ("momentum", "value", "quality", "composite"):
        verdict = result["rules"][rule]
        assert verdict["n"] == 12
        assert verdict["verdict"] in {"edge", "no edge"}
        assert len(result["spreads"][rule]) == 12
    # GOOGL/META have no market cap at any rebalance, so they are
    # recorded as missing on every date — momentum only, never
    # invented.
    for symbol in ("GOOGL", "META"):
        entries = result["missing_concepts"][symbol]
        assert len(entries) == 12
        assert all("market_cap" in e["missing"] for e in entries)
    # Deterministic: a second run gives identical spreads.
    again = evd._evaluate_ranking(
        cache, symbols=list(cache["pit_fundamentals"]))
    assert again["spreads"] == result["spreads"]


def test_event_study_on_slice():
    cache = _load_slice()
    result = evd._evaluate_event_study(cache)
    assert result["n_events"] == 2
    assert {e["symbol"] for e in result["per_event"]} == {
        "AAPL", "MSFT"}
    assert all(e["model_used"] == "market"
               for e in result["per_event"])
    assert set(result["aggregate"]) >= {
        "caar", "t_stat", "bootstrap_p", "significant",
        "small_n_caveat"}
    assert result["aggregate"]["small_n_caveat"] is True


def test_harness_on_trimmed_slice():
    cache = _trimmed(_load_slice())
    result = evd._evaluate_harness(cache, symbols=["AAPL", "MSFT"])
    assert result["symbols"] == ["AAPL", "MSFT"]
    assert result["models_tried"] == 11
    assert set(result["overall_price_verdicts"]) == {
        "naive", "moving_average_5", "linear_trend",
        "exp_smoothing_0.3", "ar1", "holt", "holt_damped",
        "drift", "ensemble_equal", "ensemble_adaptive"}
    assert result["pooled_volatility"]["n_folds"] > 0
    assert result["per_symbol"]["AAPL"]["n_folds"] > 0


def test_direction_on_trimmed_slice():
    cache = _trimmed(_load_slice())
    result = evd._evaluate_direction(cache, symbols=["MSFT"])
    assert result["symbols"] == ["MSFT"]
    per = result["per_symbol"][0]
    assert per["symbol"] == "MSFT"
    assert per["n_test"] > 0
    assert per["n_closes"] == 300
    assert result["pooled"]["n_folds"] == per["n_test"]
    for model in result["pooled"]["models"].values():
        assert model["verdict"] in {"edge", "no edge"}
