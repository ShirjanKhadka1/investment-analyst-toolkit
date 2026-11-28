"""Evaluate the personas team run on the real AAPL fixture.

Round 10, PR 10.3. Runs the five global personas against the
committed AAPL recording (10.2) plus the market-context
recording (this PR), scores each persona section, reduces each
section to a stance by the fixed rules in
``docs/round-10-protocol.md`` section 10.3, and measures
cross-persona agreement. Prints everything in full — no
summary-only reporting — and writes
``docs/personas-team-eval-results.json``.

Exit 0 on WIN (the team runs with no crashes, every persona
section is scored, and the agreement rate is computed).
Exit 1 ("no edge") if any persona section crashes or fewer
than 2 personas produce a non-UNKNOWN stance (so no agreement
rate exists). The agreement rate is measured against the chance
baseline (1/3 for three stances) but is never thresholded: a
rate at chance is a measurement, not a failure.

Usage:
  python3 scripts/evaluate_personas_team.py [--date YYYY-MM-DD]
                                            [--output PATH]
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import personas_team

ROOT = Path(__file__).resolve().parent.parent
AAPL_FIXTURE = ROOT / "examples" / "real-aapl-2026-11-27.json"
MARKET_FIXTURE = ROOT / "examples" / "personas-team-inputs-2026-11-27.json"
DEFAULT_OUTPUT = ROOT / "docs" / "personas-team-eval-results.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="run date YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--output", default=None,
                        help="output JSON path")
    args = parser.parse_args(argv)
    run_date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output = Path(args.output) if args.output else DEFAULT_OUTPUT

    aapl = json.loads(AAPL_FIXTURE.read_text(encoding="utf-8"))
    market = json.loads(MARKET_FIXTURE.read_text(encoding="utf-8"))
    result = personas_team.run_personas_team(aapl, market)

    scores = result["scores"]
    stances = result["stances"]
    agreement = result["agreement"]
    n_sourced = sum(1 for s in scores.values()
                    if s["score"] == "sourced")
    # Win = the team runs (a crashing section raises out of
    # run_personas_team and exits non-zero) and the agreement
    # rate is computed (>= 2 non-UNKNOWN stances). A non-UNKNOWN
    # stance implies a sourced section, so no separate sourced
    # threshold is needed.
    win = agreement["rate"] is not None
    summary = {
        "date": run_date,
        "verdict": "win" if win else "no edge",
        "run_id": result["run_id"],
        "symbol": result["symbol"],
        "market": result["market"],
        "as_of": result["as_of"],
        "filer": result["filer"],
        "sic": result["sic"],
        "sic_description": result["sic_description"],
        "last_close": result["last_close"],
        "scores": scores,
        "stances": stances,
        "agreement": agreement,
        "technical": result["technical"],
        "sentiment": result["sentiment"],
        "provenance": result["provenance"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")

    print(f"Round 10.3 personas team eval — {run_date}")
    print(f"  {result['filer']} (SIC {result['sic']} "
          f"{result['sic_description']}), as_of {result['as_of']}, "
          f"last close ${result['last_close']:.2f}")
    for key in personas_team.PERSONA_KEYS:
        score = scores[key]
        stance = stances[key]
        print(f"  [{key}] score={score['score']} "
              f"stance={stance['stance']}")
        print(f"    score: {score['reason']}")
        if score["sourced_values"]:
            print(f"    sourced: {', '.join(score['sourced_values'])}")
        print(f"    stance: {stance['reason']}")
    if agreement["rate"] is None:
        print(f"  [agreement] rate=UNKNOWN — {agreement['reason']}")
    else:
        print(f"  [agreement] rate={agreement['rate']:.4f} "
              f"({agreement['matching_pairs']}/"
              f"{agreement['n_pairs']} pairs, "
              f"chance baseline "
              f"{agreement['chance_baseline']:.4f})")
        for pair in agreement["pairs"]:
            mark = "MATCH" if pair["match"] else "     "
            print(f"    {mark} {pair['a']} ({pair['stance_a']}) vs "
                  f"{pair['b']} ({pair['stance_b']})")
    tech = result["technical"]
    print(f"  [technical] momentum_20d={tech['momentum_20d_pct']:.2f}% "
          f"price_vs_mean_20d={tech['price_vs_mean_20d_pct']:.2f}% "
          f"rsi_14={tech['rsi_14']:.2f} "
          f"vol_daily={tech['volatility_daily_pct']:.2f}% "
          f"n_bars={tech['n_bars']} (not a persona stance)")
    print(f"  [sentiment] label={result['sentiment']['label']}")
    print(f"\nVERDICT: {'WIN' if win else 'NO EDGE'} "
          f"({n_sourced}/5 personas sourced)")
    print(f"wrote {output}")
    return 0 if win else 1


if __name__ == "__main__":
    raise SystemExit(main())
