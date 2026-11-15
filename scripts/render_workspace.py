"""Render an agent-run events.jsonl to a single HTML workspace page.

Round U, PR U.2. Stdlib only, runnable offline. The render contract
is pre-registered in docs/u2-workspace-ui-protocol.md.

Usage:
  python3 scripts/render_workspace.py [--events PATH]
      [--output PATH] [--reference-time ISO]
Defaults: --events examples/agent-run-events.jsonl, --output
examples/agent-run-workspace.html, --reference-time the latest event
ts in the log. The committed example uses --reference-time
2026-11-14T12:00:12Z (fixed by the protocol). Exit 0 on success,
printing the output path and the event count; exit 1 when the log
fails U.1 validation or the input file is missing.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import workspace_ui

DEFAULT_EVENTS = (Path(__file__).resolve().parent.parent
                  / "examples" / "agent-run-events.jsonl")
DEFAULT_OUTPUT = (Path(__file__).resolve().parent.parent
                  / "examples" / "agent-run-workspace.html")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", default=str(DEFAULT_EVENTS),
                        help="events.jsonl to render "
                             "(default: examples/agent-run-events.jsonl)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="HTML file to write "
                             "(default: examples/agent-run-workspace.html)")
    parser.add_argument("--reference-time", default=None,
                        help="ISO 8601 reference time for freshness "
                             "stamps (default: latest event ts)")
    args = parser.parse_args(argv)
    events_path = Path(args.events)
    if not events_path.exists():
        print(f"FAIL: events file not found: {events_path}",
              file=sys.stderr)
        return 1
    try:
        count = workspace_ui.render_events_file(
            events_path, args.output,
            reference_time=args.reference_time)
    except ValueError as exc:
        print(f"FAIL: {events_path} does not validate: {exc}",
              file=sys.stderr)
        return 1
    print(f"wrote {args.output}: {count} events rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
