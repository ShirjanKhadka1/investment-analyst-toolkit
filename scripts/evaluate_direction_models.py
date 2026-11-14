"""Run the pre-registered direction-models evaluation (PR 8.2).

Reads the protocol in docs/direction-models-eval-protocol.md and
runs it, nothing else: SPY / AAPL / VT daily closes from Yahoo
(range 2y), expanding walk-forward from 252 closes, three base
probabilistic direction models (logistic gradient-descent,
Gaussian naive Bayes, fixed-weight score) + one
regime-conditioned logistic variant + two ensembles, scored by
Brier (primary) and log score (secondary) against the coin-flip,
base-rate and pre-registered direction-rule baselines, verdict by
the protocol decision rule (pooled mean Brier lower AND the 95%
paired-bootstrap CI entirely below zero, 2000 resamples, seed
20261115; "edge" only when a model beats ALL THREE baselines,
else "no edge"). Reliability bins and regime diagnostics (CUSUM
alarms, HMM-lite transitions) are reported as diagnostics, never
as verdict rules.

Stdlib only. Network is used ONLY for the Yahoo fetch; a fetch
failure aborts the run instead of inventing prices. This script is
NOT run by CI or by scripts/qa.py — it is the reproducibility
record for docs/direction-models-model-card.md. Run it from the
repo root:

  python3 scripts/evaluate_direction_models.py
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from toolkit import data
from toolkit import direction_models as dm

SYMBOLS = ("SPY", "AAPL", "VT")
RANGE = "2y"
TRAIN_SIZE = 252


def main():
    results = []
    per_symbol = []
    for symbol in SYMBOLS:
        pairs = data.fetch_yahoo_prices(symbol, range_=RANGE, interval="1d")
        closes = [close for _date, close in pairs]
        wf = dm.walk_forward_direction(closes, symbol, train_size=TRAIN_SIZE)
        results.append(wf)
        outcomes = [f["outcome"] for f in wf["folds"]]
        hmm = dm.hmm_lite_transitions(closes)
        cusum = dm.cusum_alarms(dm._simple_returns(closes))
        per_symbol.append({
            "symbol": symbol,
            "n_closes": len(closes),
            "first_date": pairs[0][0],
            "last_date": pairs[-1][0],
            "n_test": wf["n_test"],
            "up_rate": sum(outcomes) / len(outcomes) if outcomes else None,
            "n_high_regime_folds": sum(
                1 for f in wf["folds"] if f["regime"] == "high"),
            "cusum_alarms_full_series": cusum["n_alarms"],
            "hmm_lite_transitions": hmm["transitions"],
            "hmm_lite_n_volatile": hmm["n_volatile"],
            "hmm_lite_n_calm": hmm["n_calm"],
        })
    comparison = dm.compare_direction_models(results)
    # Reliability bins per model, pooled across symbols.
    pooled_probs = {m: [] for m in dm.EVALUATED_MODELS}
    pooled_outcomes = []
    for wf in results:
        for fold in wf["folds"]:
            pooled_outcomes.append(fold["outcome"])
            for m in dm.EVALUATED_MODELS:
                pooled_probs[m].append(fold["probs"][m])
    reliability = {
        m: [{"bin": [b["bin"][0], b["bin"][1]], "count": b["count"],
             "mean_probability": b["mean_probability"],
             "observed_up_rate": b["observed_up_rate"]}
            for b in dm.reliability_bins(pooled_probs[m], pooled_outcomes)]
        for m in dm.EVALUATED_MODELS
    }
    out = {
        "protocol": "docs/direction-models-eval-protocol.md",
        "run_date": date.today().isoformat(),
        "symbols": list(SYMBOLS),
        "range": RANGE,
        "train_size": TRAIN_SIZE,
        "models_tried": 3,
        "evaluated_entries": list(dm.EVALUATED_MODELS),
        "per_symbol": per_symbol,
        "pooled": {
            "n_folds": comparison["n_folds"],
            "n_symbols": comparison["n_symbols"],
            "models": {
                model: {
                    "mean_brier": m["mean_brier"],
                    "mean_brier_raw": m["mean_brier_raw"],
                    "mean_log_score": m["mean_log_score"],
                    "accuracy_at_0.5": m["accuracy_at_0.5"],
                    "verdict": m["verdict"],
                    "per_baseline": {
                        b: {
                            "baseline_brier": pb["baseline_brier"],
                            "mean_brier_diff": pb["mean_brier_diff"],
                            "ci_95": list(pb["ci_95"]),
                            "beats_baseline": pb["beats_baseline"],
                        }
                        for b, pb in m["per_baseline"].items()
                    },
                }
                for model, m in comparison["models"].items()
            },
            "regime_conditioning": {
                "mean_brier_diff_vs_plain_logistic":
                    comparison["regime_conditioning"]
                    ["mean_brier_diff_vs_plain_logistic"],
                "ci_95": list(comparison["regime_conditioning"]["ci_95"]),
                "verdict": comparison["regime_conditioning"]["verdict"],
            },
        },
        "reliability_bins": reliability,
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
