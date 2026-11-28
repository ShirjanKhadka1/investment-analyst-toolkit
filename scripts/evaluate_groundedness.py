"""Run the pre-registered 9.2 agent eval suite (Round 9, PR 9.2).

Reads the committed fixtures in ``examples/groundedness/``
(memo + event log + ``manifest.json`` — fixed known-answer
fixtures, never generated at eval time) and scores three systems
on them:

- the groundedness checker (``toolkit.groundedness.check_memo``);
- the no-checker baseline: every memo passes;
- the keyword-only baseline: a memo passes if it contains the
  word "source" (case-insensitive) or a citation-looking string
  ``[n]``, with no value matching.

The memos reach the checker through the FakeProvider only
(``toolkit.llm_providers.FakeProvider``: scripted responses, fixed
token counts, no network, no real key): the script is the
committed memo texts in manifest order, one ``complete()`` call
per fixture. The suite prints the per-fixture PASS/BLOCK list in
full — no summary-only reporting — and writes the same table plus
the summary to ``docs/groundedness-eval-results.json``.

Verdict rule (pre-registered in the Round 9 protocol before any
fixture ran): WIN only when the checker blocks 100% of fixture
memos containing any unsourced number (0 false passes) AND passes
at least 95% of the fully-grounded fixture memos AND agrees with
the risk gate on at least 90% of fixture runs AND beats both
baselines on the suite. Anything less is "no edge": the checker
is recorded as not better than the baselines, and no memo is
claimed grounded on its strength.

Usage:
  python3 scripts/evaluate_groundedness.py [--date YYYY-MM-DD]
      [--output PATH]
Default output: docs/groundedness-eval-results.json. Exit 0 on
WIN, exit 1 on "no edge".
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import events, groundedness
from toolkit.llm_providers import FakeProvider

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "examples" / "groundedness"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"
DEFAULT_OUTPUT = ROOT / "docs" / "groundedness-eval-results.json"

# Pre-registered thresholds (Round 9 protocol, fixed before any
# fixture ran): 100% block on unsourced, >=95% pass on grounded,
# >=90% gate agreement, beats both baselines.
BLOCK_RATE_MIN = 1.0
GROUNDED_PASS_MIN = 0.95
GATE_AGREEMENT_MIN = 0.90

# Fixed FakeProvider charge per call: (input, output) tokens.
TOKENS_PER_CALL = (10, 20)

_CITATION_RE = re.compile(r"\[\d+\]")


def no_checker_verdict(memo, log_events):
    """Baseline (a): no checker — every memo passes."""
    return "PASS"


def keyword_checker_verdict(memo, log_events):
    """Baseline (b): keyword-only — passes on "source" or "[n]"."""
    if "source" in memo.lower() or _CITATION_RE.search(memo):
        return "PASS"
    return "BLOCK"


def load_fixtures(fixture_dir=FIXTURE_DIR):
    """Load the committed fixtures: (manifest, [(name, memo, log)])."""
    fixture_dir = Path(fixture_dir)
    manifest = json.loads(
        (fixture_dir / "manifest.json").read_text(encoding="utf-8"))
    fixtures = []
    for entry in manifest:
        name = entry["name"]
        memo = (fixture_dir / f"{name}.md").read_text(encoding="utf-8")
        log = events.load_events(fixture_dir / f"{name}.jsonl")
        fixtures.append((name, memo, log))
    return manifest, fixtures


def run_eval_suite(fixture_dir=FIXTURE_DIR):
    """Run the suite on the FakeProvider; return the results dict.

    Pure with respect to the fixtures: reads the committed files,
    plays the memo texts back through a FakeProvider (one call per
    fixture, fixed token counts), checks each returned memo with
    the checker and both baselines, and aggregates the
    pre-registered rates. No network, no live LLM.
    """
    manifest, fixtures = load_fixtures(fixture_dir)
    expected = {entry["name"]: entry for entry in manifest}
    provider = FakeProvider(
        script=[memo for _name, memo, _log in fixtures],
        tokens_per_call=TOKENS_PER_CALL)
    rows = []
    for name, memo, log in fixtures:
        result = provider.complete(
            prompt=f"Draft the {name} memo.")
        returned = result.text
        if returned != memo:
            raise RuntimeError(
                f"fake provider returned different text for {name}")
        check = groundedness.check_memo(returned, log)
        gate_agree = check["checks"]["gate_agreement"]["pass"]
        rows.append({
            "name": name,
            "kind": expected[name]["kind"],
            "expected": expected[name]["expected"],
            "gate_verdict": expected[name]["gate_verdict"],
            "checker": check["verdict"],
            "no_checker": no_checker_verdict(returned, log),
            "keyword_only": keyword_checker_verdict(returned, log),
            "gate_agreement": gate_agree,
            "numbers_checked": check["numbers_checked"],
            "numbers_grounded": check["numbers_grounded"],
            "reasons": check["reasons"],
        })
    n = len(rows)
    checker_correct = sum(1 for r in rows if r["checker"] == r["expected"])
    false_passes = sum(1 for r in rows
                       if r["expected"] == "BLOCK"
                       and r["checker"] == "PASS")
    false_blocks = sum(1 for r in rows
                       if r["expected"] == "PASS"
                       and r["checker"] == "BLOCK")
    grounded = [r for r in rows if r["expected"] == "PASS"]
    grounded_pass_rate = (sum(1 for r in grounded
                              if r["checker"] == "PASS")
                          / len(grounded)) if grounded else 0.0
    planted = [r for r in rows if r["kind"] == "planted"]
    planted_block_rate = (sum(1 for r in planted
                              if r["checker"] == "BLOCK")
                          / len(planted)) if planted else 0.0
    unsourced = [r for r in rows if r["expected"] == "BLOCK"]
    unsourced_block_rate = (sum(1 for r in unsourced
                                if r["checker"] == "BLOCK")
                            / len(unsourced)) if unsourced else 0.0
    gated = [r for r in rows if r["gate_agreement"] is not None]
    gate_agreement_rate = (sum(1 for r in gated if r["gate_agreement"])
                           / len(gated)) if gated else 0.0
    no_checker_acc = (sum(1 for r in rows
                          if r["no_checker"] == r["expected"]) / n)
    keyword_acc = (sum(1 for r in rows
                       if r["keyword_only"] == r["expected"]) / n)
    checker_acc = checker_correct / n if n else 0.0
    verdict = ("win" if false_passes == 0
               and unsourced_block_rate >= BLOCK_RATE_MIN
               and grounded_pass_rate >= GROUNDED_PASS_MIN
               and gate_agreement_rate >= GATE_AGREEMENT_MIN
               and checker_acc > no_checker_acc
               and checker_acc > keyword_acc else "no edge")
    summary = {
        "n_fixtures": n,
        "checker_correct": checker_correct,
        "checker_accuracy": checker_acc,
        "false_passes": false_passes,
        "false_blocks": false_blocks,
        "grounded_pass_rate": grounded_pass_rate,
        "grounded_passed": sum(1 for r in grounded
                               if r["checker"] == "PASS"),
        "grounded_total": len(grounded),
        "planted_block_rate": planted_block_rate,
        "planted_blocked": sum(1 for r in planted
                               if r["checker"] == "BLOCK"),
        "planted_total": len(planted),
        "unsourced_block_rate": unsourced_block_rate,
        "unsourced_blocked": sum(1 for r in unsourced
                                 if r["checker"] == "BLOCK"),
        "unsourced_total": len(unsourced),
        "gate_agreement_rate": gate_agreement_rate,
        "gate_agreements": sum(1 for r in gated if r["gate_agreement"]),
        "gate_total": len(gated),
        "no_checker_accuracy": no_checker_acc,
        "keyword_only_accuracy": keyword_acc,
        "verdict": verdict,
    }
    return {
        "provider": "fake",
        "tokens_per_call": list(TOKENS_PER_CALL),
        "provider_calls": provider.calls,
        "tokens_used": provider.calls * sum(TOKENS_PER_CALL),
        "fixtures": rows,
        "summary": summary,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="run date YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="results JSON to write (default: "
                        "docs/groundedness-eval-results.json)")
    args = parser.parse_args(argv)
    run_date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results = run_eval_suite()
    results["run_date"] = run_date
    results["thresholds"] = {
        "block_rate_min": BLOCK_RATE_MIN,
        "grounded_pass_min": GROUNDED_PASS_MIN,
        "gate_agreement_min": GATE_AGREEMENT_MIN,
    }
    for row in results["fixtures"]:
        agree = row["gate_agreement"]
        print(f"{row['name']:28s} {row['kind']:20s} "
              f"expected {row['expected']:5s} "
              f"checker {row['checker']:5s} "
              f"no-checker {row['no_checker']:5s} "
              f"keyword {row['keyword_only']:5s} "
              f"gate-agree {agree}")
        if row["reasons"]:
            for reason in row["reasons"]:
                print(f"{'':28s}   - {reason}")
    summary = results["summary"]
    print(f"fixtures: {summary['n_fixtures']}; "
          f"checker correct {summary['checker_correct']}/"
          f"{summary['n_fixtures']} "
          f"({summary['checker_accuracy']:.1%}); "
          f"false passes {summary['false_passes']}; "
          f"false blocks {summary['false_blocks']}")
    print(f"grounded pass rate: {summary['grounded_passed']}/"
          f"{summary['grounded_total']} "
          f"({summary['grounded_pass_rate']:.1%}, "
          f"need >={GROUNDED_PASS_MIN:.0%})")
    print(f"planted block rate: {summary['planted_blocked']}/"
          f"{summary['planted_total']} "
          f"({summary['planted_block_rate']:.1%}); "
          f"unsourced block rate: {summary['unsourced_blocked']}/"
          f"{summary['unsourced_total']} "
          f"({summary['unsourced_block_rate']:.1%}, need 100%)")
    print(f"gate agreement: {summary['gate_agreements']}/"
          f"{summary['gate_total']} "
          f"({summary['gate_agreement_rate']:.1%}, "
          f"need >={GATE_AGREEMENT_MIN:.0%})")
    print(f"baselines: no-checker {summary['no_checker_accuracy']:.1%}, "
          f"keyword-only {summary['keyword_only_accuracy']:.1%}")
    print(f"verdict: {summary['verdict']}")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + "\n",
                      encoding="utf-8")
    print(f"wrote {output}")
    return 0 if summary["verdict"] == "win" else 1


if __name__ == "__main__":
    raise SystemExit(main())
