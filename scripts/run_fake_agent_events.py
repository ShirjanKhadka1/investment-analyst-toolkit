"""Fake-provider fixture run for the U.1 event log (Round U, PR U.1).

Runs the deterministic rules-mode NEPSE team over a fixed, synthetic
input bundle — the "fake provider" standing in for fetched data — with
an ``EventRecorder``, and writes a real ``events.jsonl``. No network,
no LLM, no API key: rules mode needs none of those. Fixed run id and
fixed clock, so the output is byte-identical on every run; re-running
deletes the previous output first, so the fixture never accumulates
duplicate runs.

Usage:
  python3 scripts/run_fake_agent_events.py [--output PATH]
Default output: examples/agent-run-events.jsonl (the committed
fixture). Exit 0 when the written log verifies clean, else 1.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import events, rules_mode

FIXED_RUN_ID = "u1-fixture-run"
FIXED_TS = "2026-11-14T12:00:00Z"
DEFAULT_OUTPUT = (Path(__file__).resolve().parent.parent
                  / "examples" / "agent-run-events.jsonl")

# Synthetic demo inputs only — no real prices, no real headlines.
# A steady 30-bar rise, a banks sector with two sourced metrics,
# three headlines (two with sources), two broker-flow rows, one macro
# field and one bonus corporate action.
INPUTS = {
    "closes": [100.0 + i for i in range(30)],
    "sector": "banks",
    "metrics": {"npl_pct": 3.2, "capital_adequacy_pct": 12.5},
    "headlines": [
        {"text": "Bank reports record profit growth", "lang": "en",
         "source": "Paper A"},
        {"text": "Market opens on Monday", "lang": "en",
         "source": "Paper B"},
        {"text": "Company announces bonus shares", "lang": "en"},
    ],
    "broker_rows": [
        {"broker": 58, "buy_value": 900.0, "sell_value": 100.0},
        {"broker": 12, "buy_value": 100.0, "sell_value": 800.0},
    ],
    "macro": {"nrb_policy_rate_pct": 5.5},
    "actions": [{"type": "bonus", "detail": "10% bonus shares",
                 "date": "2026-11-14"}],
}


def run(output):
    """Run the fixture and return (path, recorded events, team output)."""
    output = Path(output)
    if output.exists():
        output.unlink()
    recorder = events.EventRecorder(output, FIXED_RUN_ID, now=FIXED_TS)
    result = rules_mode.run_rules_team("DEMO", "NEPSE", INPUTS,
                                       recorder=recorder)
    return output, events.load_events(output), result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="events.jsonl to write "
                             "(default: examples/agent-run-events.jsonl)")
    args = parser.parse_args(argv)
    output, rows, _result = run(args.output)
    problems = events.verify_events(output)
    if problems:
        print(f"FAIL: {output} does not verify:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"wrote {output}: {len(rows)} events, chain OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
