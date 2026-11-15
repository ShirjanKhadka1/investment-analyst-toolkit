"""Render a recorded agent run to a static replay HTML page.

Round U, PR U.3. Stdlib only, runnable offline. The replay contract
is pre-registered in docs/u3-live-replay-protocol.md: the page is
the U.2 render of the recorded run plus a "recorded demo" banner
(run date derived from the log, provider labelled honestly — the
fixture provider is the fake provider, FAKE), a cost/latency meter
card, and a run-diff section against a second run.

Usage:
  python3 scripts/render_replay.py [--events PATH]
      [--diff-events PATH] [--output PATH] [--reference-time ISO]
      [--provider FAKE]
Defaults: --events examples/agent-run-events.jsonl, --diff-events
examples/agent-run-events-2.jsonl, --output docs/workspace-replay.html,
--reference-time 2026-11-14T12:00:12Z (the U.2 protocol's fixed
reference time), --provider FAKE. Exit 0 on success, printing the
output path and the event count; exit 1 when an input file is
missing or a log fails U.1 validation.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import live_replay

DEFAULT_EVENTS = (Path(__file__).resolve().parent.parent
                  / "examples" / "agent-run-events.jsonl")
DEFAULT_DIFF_EVENTS = (Path(__file__).resolve().parent.parent
                       / "examples" / "agent-run-events-2.jsonl")
DEFAULT_OUTPUT = (Path(__file__).resolve().parent.parent
                  / "docs" / "workspace-replay.html")
DEFAULT_REFERENCE_TIME = "2026-11-14T12:00:12Z"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", default=str(DEFAULT_EVENTS),
                        help="events.jsonl to replay "
                             "(default: examples/agent-run-events.jsonl)")
    parser.add_argument("--diff-events", default=str(DEFAULT_DIFF_EVENTS),
                        help="second events.jsonl for the diff section "
                             "(default: examples/agent-run-events-2.jsonl)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="HTML file to write "
                             "(default: docs/workspace-replay.html)")
    parser.add_argument("--reference-time",
                        default=DEFAULT_REFERENCE_TIME,
                        help="ISO 8601 reference time for freshness "
                             "stamps (default: 2026-11-14T12:00:12Z)")
    parser.add_argument("--provider", default="FAKE",
                        help="provider label for the run "
                             "(default: FAKE)")
    args = parser.parse_args(argv)
    for label, path in (("events", args.events),
                        ("diff-events", args.diff_events)):
        if not Path(path).exists():
            print(f"FAIL: {label} file not found: {path}",
                  file=sys.stderr)
            return 1
    try:
        count = live_replay.render_replay_file(
            args.events, args.output, diff_path=args.diff_events,
            reference_time=args.reference_time, provider=args.provider)
    except ValueError as exc:
        print(f"FAIL: {args.events} does not validate: {exc}",
              file=sys.stderr)
        return 1
    print(f"wrote {args.output}: {count} events rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
