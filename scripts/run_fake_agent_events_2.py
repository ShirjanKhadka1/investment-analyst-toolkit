"""Second fake-provider fixture run for the U.3 diff view (Round U, PR U.3).

Runs the deterministic rules-mode NEPSE team over the SAME synthetic
input bundle as scripts/run_fake_agent_events.py — the "fake
provider" standing in for fetched data — with a different fixed run
id, an advancing clock (+2 s per recorded event from
2026-11-15T12:00:00Z), and headlines sourced from "Paper A" and
"Paper C" instead of "Paper A" and "Paper B". Three extra events the
rules team has no hook for are recorded at their natural points via
the same EventRecorder: a ``debate_turn`` (bull) after the sentiment
evidence, and a ``gate_result`` (PASS) plus a ``memo_section`` before
``run_finished``, so ``run_finished`` stays last.

The section computation is the real rules-mode computation: the
recorded steps are driven through the same
``toolkit.rules_mode._recorded_step`` /
``toolkit.rules_mode._record_headline_evidence`` helpers the U.1
fixture script uses — only the interleaved debate/gate/memo records
are added here. No network, no LLM, no API key. Fixed run id and
fixed clock, so the output is byte-identical on every run; re-running
deletes the previous output first, so the fixture never accumulates
duplicate runs.

Usage:
  python3 scripts/run_fake_agent_events_2.py [--output PATH]
Default output: examples/agent-run-events-2.jsonl (the committed
second fixture). Exit 0 when the written log verifies clean, else 1.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_fake_agent_events as fixture1

from toolkit import events, rules_mode

FIXED_RUN_ID = "u3-fixture-run-2"
FIXED_START = "2026-11-15T12:00:00Z"
STEP_SECONDS = 2
DEFAULT_OUTPUT = (Path(__file__).resolve().parent.parent
                  / "examples" / "agent-run-events-2.jsonl")

# Same synthetic bundle as the U.1 fixture, except the second sourced
# headline comes from "Paper C" instead of "Paper B" — that one
# changed source is the diff view's removed/added evidence pair.
INPUTS = dict(fixture1.INPUTS)
INPUTS["headlines"] = [
    {"text": "Bank reports record profit growth", "lang": "en",
     "source": "Paper A"},
    {"text": "Bank launches new digital service", "lang": "en",
     "source": "Paper C"},
    {"text": "Company announces bonus shares", "lang": "en"},
]


class _AdvancingClock:
    """Callable clock: +STEP_SECONDS per call, from a fixed start."""

    def __init__(self, start, step_seconds):
        self.current = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
        self.current = self.current.replace(tzinfo=timezone.utc)
        self.step = timedelta(seconds=step_seconds)

    def __call__(self):
        ts = self.current.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.current += self.step
        return ts


def run(output):
    """Run the second fixture and return (path, recorded events)."""
    output = Path(output)
    if output.exists():
        output.unlink()
    clock = _AdvancingClock(FIXED_START, STEP_SECONDS)
    recorder = events.EventRecorder(output, FIXED_RUN_ID, now=clock)
    inputs = INPUTS
    steps = ["technical", "fundamentals", "sentiment", "broker_flow",
             "macro", "corporate_actions"]
    recorder.record("run_started", team="nepse_rules", symbol="DEMO",
                    exchange="NEPSE")
    recorder.record("plan", steps=list(steps))
    rules_mode._recorded_step(
        recorder, "technical", "technical_read",
        lambda: rules_mode.technical_read(inputs.get("closes")))
    rules_mode._recorded_step(
        recorder, "fundamentals", "sector_fundamentals_check",
        lambda: rules_mode.sector_fundamentals_check(
            inputs.get("sector", ""), inputs.get("metrics") or {}))
    rules_mode._recorded_step(
        recorder, "sentiment", "sentiment_score",
        lambda: rules_mode.sentiment_score(inputs.get("headlines") or []))
    rules_mode._record_headline_evidence(recorder, "DEMO",
                                         inputs.get("headlines") or [])
    recorder.record("debate_turn", speaker="bull_agent",
                    stance="bull",
                    text="Profit growth and a new digital service "
                         "support the bull case.")
    rules_mode._recorded_step(
        recorder, "broker_flow", "broker_flow_read",
        lambda: rules_mode.broker_flow_read(
            inputs.get("broker_rows") or []))
    rules_mode._recorded_step(
        recorder, "macro", "macro_checklist",
        lambda: rules_mode.macro_checklist(inputs.get("macro") or {}))
    rules_mode._recorded_step(
        recorder, "corporate_actions", "corporate_action_flags",
        lambda: rules_mode.corporate_action_flags(
            inputs.get("actions") or []))
    recorder.record("gate_result", gate="risk_limits", verdict="PASS",
                    reason="all sections ok, no limits breached")
    recorder.record("memo_section", section="verdict", status="ok",
                    symbol="DEMO")
    recorder.record("run_finished", status="ok", symbol="DEMO")
    return output, events.load_events(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="events.jsonl to write "
                             "(default: examples/agent-run-events-2.jsonl)")
    args = parser.parse_args(argv)
    output, rows = run(args.output)
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
