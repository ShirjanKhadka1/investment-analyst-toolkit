"""Run the pre-registered cross-sectional + event-study evaluation (PR 8.3).

Reads the protocol in docs/cross-sectional-eval-protocol.md and runs
it, nothing else: a 20-symbol synthetic universe (400 daily closes
each, geometric random walk from seed 20261114, no factor structure
planted), 12 monthly ranking dates, 21-day forward returns, decile
spreads net of costs (NEPSE sourced schedule at NPR 100,000 per
name; global flat cost_fraction 0.001 per leg), verdict by the
protocol decision rule (mean spread > 0 AND the 95% bootstrap CI
entirely above zero, else "no edge"); the bundled synthetic event
CSV through the market-model event study; and the bundled synthetic
floorsheet through the broker-flow analytics.

Stdlib only. NO network: every series is generated from the fixed
seed, and the demo CSVs are bundled fixtures. This script is NOT run
by CI or by scripts/qa.py — it is the reproducibility record for
docs/cross-sectional-model-card.md. Run it from the repo root:

  python3 scripts/evaluate_cross_sectional.py
"""
import json
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from toolkit import cross_sectional as xs
from toolkit import event_study as es
from toolkit import nepse_floorsheet as nf

SEED = 20261114
N_SYMBOLS = 20
N_BARS = 400
START_DATE = date(2026, 1, 5)
RANKING_BARS = [126 + 21 * k for k in range(12)]
FORWARD_BARS = 21
NEPSE_TRADE_VALUE = 100_000
GLOBAL_COST_FRACTION = 0.001


def _dates(n_bars=N_BARS):
    return [(START_DATE + timedelta(days=i)).isoformat()
            for i in range(n_bars)]


def _random_walk(rng, n_bars, start, daily_vol):
    closes = [float(start)]
    for _ in range(n_bars - 1):
        closes.append(closes[-1] * math.exp(rng.gauss(0.0, daily_vol)))
    return closes


def _synthetic_fundamentals(rng):
    market_cap = 10 ** rng.uniform(9.0, 10.0)
    net_income = market_cap * rng.uniform(0.01, 0.15)
    equity = market_cap * rng.uniform(0.2, 0.8)
    revenues = market_cap * rng.uniform(0.5, 2.0)
    gross_profit = revenues * rng.uniform(0.2, 0.6)
    assets = market_cap * rng.uniform(0.8, 1.5)
    return {
        "NetIncomeLoss": net_income,
        "StockholdersEquity": equity,
        "Revenues": revenues,
        "GrossProfit": gross_profit,
        "Assets": assets,
        "market_cap": market_cap,
    }


def _ranking_universe(rng):
    """20 synthetic symbols: closes + one annual fundamentals snapshot."""
    universe = {}
    for i in range(N_SYMBOLS):
        symbol = f"SYN-{i + 1:02d}"
        closes = _random_walk(rng, N_BARS, 100.0, 0.02)
        facts = _synthetic_fundamentals(rng)
        universe[symbol] = {"closes": closes, "facts": facts}
    return universe


def _decile_spreads(universe, market, trade_value=None,
                    cost_fraction=None):
    """Per-rebalance top-minus-bottom net spreads for one cost model.

    At each ranking date the four rules (momentum, value, quality,
    composite) each rank the universe; the spread is the top-decile
    mean net 21-day forward return minus the bottom-decile mean net
    forward return. Returns {rule: [12 spreads]} plus the
    equal-weight baseline's mean net forward return per rebalance.
    """
    rules = ("momentum", "value", "quality", "composite")
    spreads = {rule: [] for rule in rules}
    baseline = []
    for bar in RANKING_BARS:
        components = {}
        forwards = {}
        for symbol, data in universe.items():
            closes = data["closes"]
            proxies = xs.proxies_from_facts(data["facts"],
                                            data["facts"]["market_cap"])
            components[symbol] = {
                "momentum": xs.momentum_score(closes[:bar + 1]),
                "earnings_yield": proxies["earnings_yield"],
                "book_to_price": proxies["book_to_price"],
                "roe": proxies["roe"],
                "gross_margin": proxies["gross_margin"],
            }
            forwards[symbol] = (closes[bar + FORWARD_BARS] / closes[bar]
                                - 1.0)
        ranked = xs.rank_universe(components)
        _, cost_frac, _ = xs.net_forward_return(
            0.0, market, trade_value=trade_value,
            cost_fraction=cost_fraction)
        net = {s: r - cost_frac for s, r in forwards.items()}
        for rule in rules:
            # Deciles in rank_universe rank the composite; per-rule
            # spreads re-rank by the rule's own score.
            ranked_rule = xs.assign_deciles(
                {s: ranked[s][rule] for s in ranked
                 if ranked[s][rule] is not None})
            top = [s for s, d in ranked_rule.items() if d == 10]
            bottom = [s for s, d in ranked_rule.items() if d == 1]
            top_mean = math.fsum(net[s] for s in top) / len(top)
            bottom_mean = math.fsum(net[s] for s in bottom) / len(bottom)
            spreads[rule].append(top_mean - bottom_mean)
        baseline.append(math.fsum(net.values()) / len(net))
    return spreads, baseline


def main():
    rng = random.Random(SEED)
    dates = _dates()
    universe = _ranking_universe(rng)

    ranking = {}
    for market, kwargs in (
            ("nepse", {"trade_value": NEPSE_TRADE_VALUE}),
            ("generic", {"cost_fraction": GLOBAL_COST_FRACTION})):
        spreads, baseline = _decile_spreads(universe, market, **kwargs)
        _, cost_frac, basis = xs.net_forward_return(
            0.0, market, **kwargs)
        ranking[market] = {
            "round_trip_cost_fraction": cost_frac,
            "cost_basis": basis,
            "mean_baseline_net_return": math.fsum(baseline) / len(baseline),
            "rules": {rule: xs.spread_verdict(values)
                      for rule, values in spreads.items()},
        }

    # Event study on the bundled synthetic demo CSV. The market index
    # and the eight event symbols continue the same seeded stream.
    market_closes = _random_walk(rng, N_BARS, 1000.0, 0.015)
    market_prices = list(zip(dates, market_closes))
    events, events_source = es.load_event_csv(
        ROOT / "tests" / "fixtures" / "nepse_events_SYNTHETIC.csv",
        "SYNTHETIC demo events, invented for testing")
    prices_by_symbol = {}
    for event in events:
        closes = _random_walk(rng, N_BARS, 100.0, 0.02)
        prices_by_symbol[event["symbol"]] = list(zip(dates, closes))
    study = es.run_event_study(events, prices_by_symbol,
                               market_prices=market_prices)

    # Floorsheet analytics on the bundled synthetic demo CSV.
    rows, floorsheet_source = nf.load_floorsheet_csv(
        ROOT / "tests" / "fixtures" / "nepse_floorsheet_SYNTHETIC.csv",
        "SYNTHETIC demo floorsheet, invented for testing")
    floorsheet = {
        "n_rows": len(rows),
        "broker_flow": nf.broker_flow(rows),
        "symbols": {symbol: nf.symbol_flow(rows, symbol)
                    for symbol in sorted({r["symbol"] for r in rows})},
    }

    result = {
        "protocol": "docs/cross-sectional-eval-protocol.md",
        "seed": SEED,
        "universe": {"n_symbols": N_SYMBOLS, "n_bars": N_BARS,
                     "ranking_bars": RANKING_BARS,
                     "forward_bars": FORWARD_BARS},
        "ranking": ranking,
        "event_study": {
            "source": events_source,
            "n_events": len(events),
            "aggregate": study["aggregate"],
            "per_event": [
                {"symbol": e["symbol"], "event_type": e["event_type"],
                 "event_date": e["event_date"], "car": e["car"],
                 "t_stat": e["t_stat"], "bootstrap_p": e["bootstrap_p"],
                 "significant": e["significant"]}
                for e in study["events"]],
        },
        "floorsheet": {
            "source": floorsheet_source,
            "n_rows": floorsheet["n_rows"],
            "broker_flow": floorsheet["broker_flow"],
            "symbols": floorsheet["symbols"],
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
