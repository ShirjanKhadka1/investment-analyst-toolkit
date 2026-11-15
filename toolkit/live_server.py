"""Stdlib-only local SSE server for live agent-workspace mode — Round U, PR U.3.

Serves the U.2 workspace UI page and streams a run's events as
Server-Sent Events. The contract is pre-registered in
``docs/u3-live-replay-protocol.md`` — this module implements that
protocol, it does not define it.

Routes: ``GET /`` renders the workspace UI for the current contents
of the events file (the U.2 renderer, reference time = latest event
ts); ``GET /events`` streams SSE (``Content-Type:
text/event-stream``, one ``data:`` frame per event in file order,
then one terminal ``event: end`` frame); anything else is 404.

Modes: ``recorded`` streams the file once, in order, then the end
frame; ``tail`` (default) streams what is already there, then polls
the file every 0.2 s for appended lines until a ``run_finished``
event has been streamed, then the end frame. A missing or empty
events file never crashes the server: recorded mode streams zero
data frames and then the end frame, tail mode waits for the file to
appear, and ``GET /`` renders the empty-log page.

Stdlib only (``http.server``). Binds localhost only. No network
beyond the local socket, no JavaScript served, no animation.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from toolkit import events as events_mod
from toolkit import workspace_ui

__all__ = [
    "POLL_INTERVAL_S",
    "build_server",
    "end_frame",
    "make_handler",
    "serve",
    "sse_data_frame",
]

POLL_INTERVAL_S = 0.2

MODES = ("recorded", "tail")


def sse_data_frame(event):
    """One SSE data frame for an event dict (canonical JSON bytes)."""
    payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
    return f"data: {payload}\n\n".encode()


def end_frame(run_id, count):
    """The terminal SSE frame: ``event: end`` with run id and count."""
    payload = json.dumps({"run_id": run_id, "events": count,
                          "status": "done"}, sort_keys=True,
                         separators=(",", ":"))
    return f"event: end\ndata: {payload}\n\n".encode()


def _run_id_of(events):
    """Run id of the first ``run_started`` event, or None."""
    started = next((e for e in events
                    if e.get("type") == "run_started"), None)
    return started.get("run_id") if started else None


def _read_new_lines(path, sent):
    """``(parsed_rows, lines_consumed)`` for lines after ``sent``.

    Lines are consumed in order; a line that does not parse as JSON
    stops consumption there (it may be mid-write by a live run) and
    is retried on the next poll. A missing file reads as no lines.
    """
    path = Path(path)
    if not path.exists():
        return [], sent
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = []
    consumed = sent
    for line in lines[sent:]:
        if not line.strip():
            consumed += 1
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            break
        consumed += 1
    return rows, consumed


def make_handler(events_path, mode, poll_interval=POLL_INTERVAL_S):
    """Build a request-handler class bound to one events file + mode."""
    if mode not in MODES:
        raise ValueError(
            f"mode must be one of {list(MODES)}, got {mode!r}")

    class LiveWorkspaceHandler(BaseHTTPRequestHandler):
        """One handler; ``events_path``/``mode`` are class attributes."""

        server_version = "LiveWorkspace/1.0"

        def log_message(self, _format, *_args):
            """Quiet: the server prints one start line, nothing per request."""

        def _send(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                self._serve_page()
            elif self.path == "/events":
                self._serve_events()
            else:
                self._send(404, "text/plain; charset=utf-8",
                           b"not found\n")

        def _serve_page(self):
            events = events_mod.load_events(events_path)
            text = workspace_ui.render_events_html(events)
            self._send(200, "text/html; charset=utf-8",
                       text.encode("utf-8"))

        def _serve_events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                if mode == "recorded":
                    self._stream_recorded()
                else:
                    self._stream_tail()
            except (BrokenPipeError, ConnectionResetError):
                # Client went away: not an error, just stop writing.
                return

        def _stream_recorded(self):
            events = events_mod.load_events(events_path)
            for event in events:
                self.wfile.write(sse_data_frame(event))
            self.wfile.write(end_frame(_run_id_of(events),
                                       len(events)))

        def _stream_tail(self):
            sent = 0
            run_id = None
            finished = False
            while not finished:
                rows, sent = _read_new_lines(events_path, sent)
                for event in rows:
                    if run_id is None and event.get("type") == "run_started":
                        run_id = event.get("run_id")
                    self.wfile.write(sse_data_frame(event))
                    if event.get("type") == "run_finished":
                        finished = True
                if finished:
                    break
                time.sleep(poll_interval)
            self.wfile.write(end_frame(run_id, sent))

    return LiveWorkspaceHandler


def build_server(host, port, events_path, mode,
                 poll_interval=POLL_INTERVAL_S):
    """Build (but do not start) a threading SSE server.

    ``port 0`` selects an ephemeral port; the bound port is then
    ``server.server_address[1]``.
    """
    handler = make_handler(events_path, mode, poll_interval=poll_interval)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(host, port, events_path, mode,
          poll_interval=POLL_INTERVAL_S):
    """Build and serve forever. Returns only on error (or SIGINT)."""
    server = build_server(host, port, events_path, mode,
                          poll_interval=poll_interval)
    print(f"Serving {mode} {events_path} on "
          f"http://{host}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
