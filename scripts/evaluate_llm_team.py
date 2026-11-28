"""Evaluate the llm-team series (Round 9.1, protocol
docs/round-9-protocol.md section 9.1).

Reads a decision-log JSONL, takes the ``llm-team`` series only, and
scores its settled direction calls: Brier (primary) and hit rate
(secondary) against the naive and buy-and-hold baselines, with a
seeded paired-bootstrap confidence interval on the Brier
difference.

Brier definitions (fixed here, before any evaluation):
- Model probability of "up": the call's ``confidence`` when
  ``expected`` is "up", ``1 - confidence`` when "down". Outcome is
  1 when ``realised_return`` > 0, else 0 (a flat outcome is not up).
- Naive baseline: deterministic — probability 1 when
  ``naive_expected`` is "up", 0 when "down". Buy-and-hold:
  probability 1 always (it always predicts up).
- "range" calls carry no binary up/down probability and are
  excluded from Brier (counted as ``n_range_excluded``). Settled
  calls missing a ``naive_expected`` are excluded from the paired
  naive comparison (counted as ``n_missing_naive``).

Verdict rule (pre-registered in the protocol):
- Fewer than 100 scored calls: "withheld" — no prediction
  verdict at all, counts only.
- At 100+ scored calls: "beats naive" iff the mean paired Brier
  difference (naive minus model) is positive AND its 95%
  paired-bootstrap CI (10000 resamples, seed 20261114, the
  ``forecast_harness`` defaults) lies entirely above zero;
  otherwise "no edge".

Stdlib only. No network, no LLM, no real prices: the input is a
log file. This script is NOT run by CI or by scripts/qa.py — it
is the reproducibility record for docs/llm-team-model-card.md.
Run it from the repo root:

  python3 scripts/evaluate_llm_team.py --calls path/to/calls.jsonl
  python3 scripts/evaluate_llm_team.py --calls path/to/calls.jsonl \
      --out evaluation.json
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from toolkit.agent_team import SERIES_LLM_TEAM, score_series
from toolkit.decision_log import load_calls
from toolkit.forecast_harness import paired_bootstrap_ci
from toolkit.predictive import brier_score

MIN_SCORED_FOR_CLAIM = 100


def evaluate_llm_team(calls):
    """Score the llm-team series in ``calls`` (decision-log rows).

    Returns a JSON-able dict: counts, Brier scores, hit rates,
    the paired bootstrap CI (only at >= 100 scored calls), and
    the verdict ("withheld" / "beats naive" / "no edge").
    """
    series = [c for c in calls if c.get("series") == SERIES_LLM_TEAM]
    settled = [c for c in series if c.get("status") == "settled"]
    open_calls = [c for c in series if c.get("status") == "open"]
    scored = []
    n_range_excluded = 0
    n_missing_naive = 0
    for call in settled:
        if call.get("expected") == "range":
            n_range_excluded += 1
            continue
        if call.get("expected") not in ("up", "down"):
            n_missing_naive += 1
            continue
        if call.get("naive_expected") not in ("up", "down"):
            n_missing_naive += 1
            continue
        if call.get("realised_return") is None:
            n_missing_naive += 1
            continue
        conf = float(call["confidence"])
        p_model = conf if call["expected"] == "up" else 1.0 - conf
        outcome = 1.0 if float(call["realised_return"]) > 0 else 0.0
        p_naive = 1.0 if call["naive_expected"] == "up" else 0.0
        scored.append({"p_model": p_model, "p_naive": p_naive,
                       "p_bnh": 1.0, "outcome": outcome})
    n = len(scored)
    hits = score_series(calls, SERIES_LLM_TEAM)
    result = {
        "series": SERIES_LLM_TEAM,
        "n_llm_team_total": len(series),
        "n_settled": len(settled),
        "n_open": len(open_calls),
        "n_scored": n,
        "n_range_excluded": n_range_excluded,
        "n_missing_naive": n_missing_naive,
        "min_scored_for_claim": MIN_SCORED_FOR_CLAIM,
        "brier_model": None,
        "brier_naive": None,
        "brier_buy_and_hold": None,
        "brier_diff_naive_minus_model": None,
        "brier_diff_ci_95": None,
        "hit_rate_model": hits["pooled"]["model"]["hit_rate"],
        "hit_rate_naive": hits["pooled"]["naive"]["hit_rate"],
        "hit_rate_buy_and_hold":
            hits["pooled"]["buy_and_hold"]["hit_rate"],
        "verdict": "withheld",
    }
    if n == 0:
        return result
    result["brier_model"] = brier_score(
        [s["p_model"] for s in scored], [s["outcome"] for s in scored])
    result["brier_naive"] = brier_score(
        [s["p_naive"] for s in scored], [s["outcome"] for s in scored])
    result["brier_buy_and_hold"] = brier_score(
        [s["p_bnh"] for s in scored], [s["outcome"] for s in scored])
    diffs = [(s["p_naive"] - s["outcome"]) ** 2
             - (s["p_model"] - s["outcome"]) ** 2 for s in scored]
    mean_diff = sum(diffs) / len(diffs)
    result["brier_diff_naive_minus_model"] = mean_diff
    if n >= MIN_SCORED_FOR_CLAIM:
        lo, hi = paired_bootstrap_ci(diffs)
        result["brier_diff_ci_95"] = [lo, hi]
        result["verdict"] = ("beats naive" if mean_diff > 0 and lo > 0
                             else "no edge")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Score the llm-team series (Brier primary, "
                    "bootstrap CI, verdict withheld before 100 "
                    "settled calls).")
    parser.add_argument("--calls", required=True,
                        help="decision-log JSONL to read")
    parser.add_argument("--out", default=None,
                        help="write the JSON result here as well")
    args = parser.parse_args(argv)
    calls = load_calls(Path(args.calls))
    result = evaluate_llm_team(calls)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
