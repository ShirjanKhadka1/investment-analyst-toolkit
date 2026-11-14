"""Run the pre-registered volatility-forecast evaluation (PR 7.2).

Reads the protocol in docs/volatility-forecast-eval-protocol.md and
runs it, nothing else: SPY / AAPL / VT daily closes from Yahoo
(range 2y), log returns, expanding walk-forward from 252 returns,
EWMA (lam 0.94) and GARCH-lite (alpha 0.08, beta 0.90) vs the naive
trailing-20-day realised-vol baseline, scored by QLIKE (primary) and
MSE on variance (secondary), verdict by the protocol decision rule
(pooled mean QLIKE lower AND the 95% paired-bootstrap CI entirely
below zero, else "no edge"). Interval coverage (80% / 95%) is
computed as a separate target check per the protocol amendment of
2026-11-14: for each fold the model's forecast interval is
+/-z * sigma from its variance forecast, and pooled empirical
coverage is reported against the nominal 80% / 95% targets. Coverage
never rewrites the QLIKE verdict.

Stdlib only. Network is used ONLY for the Yahoo fetch; a fetch
failure aborts the run instead of inventing prices. This script is
NOT run by CI or by scripts/qa.py — it is the reproducibility record
for docs/volatility-forecast-model-card.md. Run it from the repo root:

  python3 scripts/evaluate_volatility_forecast.py
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from toolkit import data, volatility

SYMBOLS = ("SPY", "AAPL", "VT")
RANGE = "2y"
TRAIN_SIZE = 252


def main():
    per_symbol = []
    results = []
    for symbol in SYMBOLS:
        pairs = data.fetch_yahoo_prices(symbol, range_=RANGE, interval="1d")
        closes = [close for _date, close in pairs]
        returns = volatility.log_returns(closes)
        wf = volatility.walk_forward_volatility(
            returns, train_size=TRAIN_SIZE)
        results.append(wf)
        scores = volatility.score_volatility_forecasts(wf)
        per_symbol.append({
            "symbol": symbol,
            "n_closes": len(closes),
            "first_date": pairs[0][0],
            "last_date": pairs[-1][0],
            "n_returns": len(returns),
            "n_test": wf["n_test"],
            "scores": {model: {"mean_qlike": s["mean_qlike"],
                               "mean_mse_variance": s["mean_mse_variance"]}
                       for model, s in scores.items()},
        })
    comparison = volatility.compare_volatility_models(results)
    coverage = volatility.score_interval_coverage(results)
    out = {
        "protocol": "docs/volatility-forecast-eval-protocol.md",
        "run_date": date.today().isoformat(),
        "symbols": list(SYMBOLS),
        "range": RANGE,
        "train_size": TRAIN_SIZE,
        "models_tried": 2,
        "per_symbol": per_symbol,
        "pooled": {
            "n_folds": comparison["n_folds"],
            "n_symbols": comparison["n_symbols"],
            "models": {
                model: {
                    "mean_qlike": m["mean_qlike"],
                    "mean_qlike_diff_vs_baseline":
                        m["mean_qlike_diff_vs_baseline"],
                    "ci_95": list(m["ci_95"]),
                    "mean_mse_variance": m["mean_mse_variance"],
                    "verdict": m["verdict"],
                    "coverage_80":
                        coverage["models"][model]["coverage_80"],
                    "coverage_95":
                        coverage["models"][model]["coverage_95"],
                }
                for model, m in comparison["models"].items()
            },
            "coverage": {
                "levels": coverage["levels"],
                "n_folds": coverage["n_folds"],
                "per_symbol": [
                    {"symbol": symbol,
                     **{model: per[model]
                        for model in per if model != "n"}}
                    for symbol, per in zip(SYMBOLS, coverage["per_symbol"])
                ],
            },
        },
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
