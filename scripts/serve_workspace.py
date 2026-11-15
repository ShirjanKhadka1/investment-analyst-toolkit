"""Serve the agent workspace UI live over Server-Sent Events.

Round U, PR U.3. Stdlib only (``toolkit.live_server``). Serves the
U.2 workspace page at ``/`` and streams the run's events as SSE at
``/events`` — see docs/u3-live-replay-protocol.md for the contract.

Usage:
  python3 scripts/serve_workspace.py [--events PATH] [--host HOST]
      [--port PORT] [--mode tail|recorded]
Defaults: --events examples/agent-run-events.jsonl, --host 127.0.0.1,
--port 8000, --mode tail. ``--port 0`` selects an ephemeral port.
Serves until interrupted (Ctrl-C exits 0). Exit 1 on a bad --mode or
an unbindable port.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import live_server

DEFAULT_EVENTS = (Path(__file__).resolve().parent.parent
                  / "examples" / "agent-run-events.jsonl")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", default=str(DEFAULT_EVENTS),
                        help="events.jsonl to serve "
                             "(default: examples/agent-run-events.jsonl)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="port to bind, 0 for ephemeral "
                             "(default: 8000)")
    parser.add_argument("--mode", default="tail",
                        help="recorded: stream the file once; tail: "
                             "follow appended lines until run_finished "
                             "(default: tail)")
    args = parser.parse_args(argv)
    if args.mode not in live_server.MODES:
        print(f"FAIL: --mode must be one of {list(live_server.MODES)}, "
              f"got {args.mode!r}", file=sys.stderr)
        return 1
    try:
        return live_server.serve(args.host, args.port, args.events,
                                 args.mode)
    except OSError as exc:
        print(f"FAIL: cannot serve: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
