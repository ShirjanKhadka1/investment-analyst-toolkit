"""Static workspace UI renderer — Round U, PR U.2.

Turns a U.1 ``events.jsonl`` log into ONE self-contained HTML file:
plan rail, agent lanes, live feed with collapsible tool calls, an
evidence panel, bull/bear debate columns, a risk-gate card with an
unmissable veto banner on BLOCK, and a memo with citations.

The render contract is pre-registered in
``docs/u2-workspace-ui-protocol.md`` — this module implements that
protocol, it does not define it. The renderer is a pure function of
``(events, reference_time)``: same input renders byte-identical HTML.
It renders exactly what the events carry and invents nothing — a
number whose (step, symbol) has no matching ``evidence_ref`` renders
as UNKNOWN, and a panel driven by an event type the log does not
contain renders an explicit empty state.

Stdlib only. No network, no JavaScript, no external reference of any
kind in the output: all CSS is one inline ``<style>`` block, dark
mode is ``prefers-color-scheme`` only, and collapsible tool calls are
native ``<details><summary>`` so the page is keyboard-operable with
no script and no ``tabindex`` anywhere.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path

from toolkit import events as events_mod

__all__ = [
    "freshness",
    "render_events_file",
    "render_events_html",
]

# Fixed reference time for the committed example render
# (examples/agent-run-workspace.html): 12 seconds after every fixture
# event, so every fixture stamp reads "12s ago". Fixed by the U.2
# protocol — not a default the renderer invents at run time.
EXAMPLE_REFERENCE_TIME = "2026-11-14T12:00:12Z"

_CSS = """\
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, -apple-system, "Segoe UI",
  sans-serif; line-height: 1.5; color: #1a1a1a; background: #ffffff; }
a { color: #0b5cad; }
a:focus-visible, summary:focus-visible {
  outline: 3px solid #0b5cad; outline-offset: 2px; }
.skip-link { position: absolute; left: -9999px; }
.skip-link:focus { position: static; display: block; padding: 0.5rem 1rem;
  background: #0b5cad; color: #ffffff; }
header, main, footer, nav, aside, section { padding: 1rem; }
header { border-bottom: 2px solid #1a1a1a; }
h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
h2 { font-size: 1.1rem; margin: 0 0 0.5rem; }
.chip { display: inline-block; padding: 0.05rem 0.45rem; border-radius: 1rem;
  border: 1px solid currentColor; font-size: 0.85rem; font-weight: 600; }
.chip-done, .chip-pass { color: #145a1b; background: #e2f3e5; }
.chip-failed, .chip-block { color: #7a1010; background: #fbe3e3; }
.chip-progress { color: #6b4e00; background: #fff3c4; }
.chip-pending { color: #444444; background: #eeeeee; }
.chip-unknown { color: #5b2d8e; background: #efe6f7; }
.veto-banner { background: #7a1010; color: #ffffff; padding: 1rem;
  font-size: 1.15rem; font-weight: 700; border: 3px solid #000000; }
.veto-banner p { margin: 0.25rem 0 0; font-weight: 400; font-size: 1rem; }
ul { margin: 0.25rem 0; padding-left: 1.25rem; }
details { border: 1px solid #cccccc; border-radius: 0.25rem;
  padding: 0.5rem; margin: 0.5rem 0; }
summary { cursor: pointer; font-weight: 600; }
.feed-row { border-bottom: 1px solid #dddddd; padding: 0.4rem 0; }
.feed-row time, .stamp { color: #555555; font-size: 0.85rem; }
.empty { color: #555555; font-style: italic; }
.debate-cols { display: grid; gap: 1rem; }
.lane { border: 1px solid #cccccc; border-radius: 0.25rem;
  padding: 0.75rem; margin: 0.5rem 0; }
@media (min-width: 900px) {
  main { display: grid; grid-template-columns: 1fr 2fr; gap: 1rem; }
  .debate-cols { grid-template-columns: 1fr 1fr; }
}
@media (prefers-color-scheme: dark) {
  body { color: #eaeaea; background: #121212; }
  a { color: #8ec2ff; }
  a:focus-visible, summary:focus-visible { outline-color: #8ec2ff; }
  header { border-bottom-color: #eaeaea; }
  .chip-done, .chip-pass { color: #b8e6bf; background: #173d1e; }
  .chip-failed, .chip-block { color: #ffc4c4; background: #4a1212; }
  .chip-progress { color: #ffe08a; background: #4a3a00; }
  .chip-pending { color: #dddddd; background: #333333; }
  .chip-unknown { color: #ddc8f5; background: #33194d; }
  .veto-banner { background: #a31616; border-color: #ffffff; }
  details, .lane { border-color: #555555; }
  .feed-row { border-bottom-color: #333333; }
  .feed-row time, .stamp, .empty { color: #bbbbbb; }
}
"""

# Freshness buckets: (upper bound in seconds, unit, divisor).
_FRESHNESS_BUCKETS = (
    (60, "s", 1),
    (3600, "m", 60),
    (86400, "h", 3600),
)


def _parse_ts(value):
    """Parse an ISO 8601 timestamp. Returns a datetime or None.

    Same rule as ``toolkit.events``: the ``Z`` suffix is normalised
    to ``+00:00`` (``datetime.fromisoformat`` on Python 3.10 does not
    accept ``Z``), and the timestamp must be timezone-aware.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def freshness(ts, reference_time):
    """Relative freshness of ``ts`` against ``reference_time``.

    ``"{n}s ago"`` under 60 s, ``"{n}m ago"`` under 60 min,
    ``"{n}h ago"`` under 24 h, ``"{n}d ago"`` otherwise; ``n`` is the
    whole-unit floor. An event at or after the reference time renders
    ``"0s ago"``. Both arguments are ISO 8601 strings; a bad one
    raises ValueError.
    """
    event_dt = _parse_ts(ts)
    ref_dt = _parse_ts(reference_time)
    if event_dt is None:
        raise ValueError(f"ts must be a timezone-aware ISO 8601 "
                         f"timestamp, got {ts!r}")
    if ref_dt is None:
        raise ValueError(f"reference_time must be a timezone-aware "
                         f"ISO 8601 timestamp, got {reference_time!r}")
    seconds = int((ref_dt - event_dt).total_seconds())
    if seconds <= 0:
        return "0s ago"
    for bound, unit, divisor in _FRESHNESS_BUCKETS:
        if seconds < bound:
            return f"{seconds // divisor}{unit} ago"
    return f"{seconds // 86400}d ago"


def _esc(value):
    """HTML-escape an event value (``None`` renders as an empty string)."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _step_status(events, step):
    """Derived status of one plan step from the run's events.

    ``done`` when a ``tool_result`` for the step has status ``"ok"``,
    ``failed`` when one has status ``"failed"``, ``in progress`` when
    a ``step_started`` exists but no result yet, ``not started``
    otherwise. A step with both an ok and a failed result counts as
    failed — a failure must never be hidden behind a later success.
    """
    started = False
    saw_ok = False
    for event in events:
        if event.get("step") != step:
            continue
        if event.get("type") == "step_started":
            started = True
        elif event.get("type") == "tool_result":
            if event.get("status") == "failed":
                return "failed"
            if event.get("status") == "ok":
                saw_ok = True
    if saw_ok:
        return "done"
    if started:
        return "in progress"
    return "not started"


def _status_chip(status):
    """A status chip: the status word as text, never colour-only."""
    css = {"done": "chip-done", "ok": "chip-done",
           "failed": "chip-failed",
           "in progress": "chip-progress",
           "not started": "chip-pending"}.get(status, "chip-pending")
    return (f'<span class="chip {css}">{_esc(status)}</span>')


def _reference_time(events, reference_time):
    """Resolve the reference time: the caller's, else the latest event ts."""
    if reference_time is not None:
        if _parse_ts(reference_time) is None:
            raise ValueError(
                "reference_time must be a timezone-aware ISO 8601 "
                f"timestamp, got {reference_time!r}")
        return reference_time
    latest = None
    for event in events:
        parsed = _parse_ts(event.get("ts"))
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    if latest is None:
        return None
    return latest.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _evidence_entries(events):
    """``evidence_ref`` events in file order, with their anchor ids.

    Anchors are numbered by evidence ordinal (``evidence-0`` is the
    first ``evidence_ref`` in the file), so links stay stable and
    short.
    """
    return [(f"evidence-{i}", event)
            for i, event in enumerate(
                e for e in events if e.get("type") == "evidence_ref")]


def _evidence_for(events, step, symbol):
    """Anchor id of the first evidence_ref matching (step, symbol).

    A match needs both fields equal, where a missing field counts as
    equal only to a missing field — an evidence row for another step
    or symbol never sources this number. Returns None when there is
    no match.
    """
    for anchor, event in _evidence_entries(events):
        if (event.get("step") == step
                and event.get("symbol") == symbol):
            return anchor
    return None


def _number_html(events, event):
    """Render a ``tool_result``'s ``n_points`` number, sourced or UNKNOWN.

    A matching ``evidence_ref`` (same step and symbol) links the
    number to its evidence anchor; with no match the number carries
    an UNKNOWN badge and no link. A result with no ``n_points``
    renders nothing here — the renderer never invents a number.
    """
    if "n_points" not in event:
        return ""
    anchor = _evidence_for(events, event.get("step"),
                           event.get("symbol"))
    number = _esc(event["n_points"])
    if anchor is not None:
        return (f' — {number} points '
                f'(<a href="#{anchor}">source</a>)')
    return (f' — {number} points '
            f'<span class="chip chip-unknown">UNKNOWN</span>')


def _feed_row(events, event, reference_time):
    """One live-feed row for an event that is not a paired tool call."""
    stamp = ""
    if reference_time is not None:
        stamp = (f' <time datetime="{_esc(event.get("ts"))}">'
                 f'{_esc(freshness(event.get("ts"), reference_time))}'
                 f'</time>')
    label = _esc(event.get("type"))
    detail = ""
    if event.get("type") == "tool_result":
        detail = (f' {_esc(event.get("tool"))} '
                  f'{_status_chip(event.get("status", ""))}'
                  f'{_number_html(events, event)}')
    elif event.get("type") == "error":
        detail = (f' <span class="chip chip-failed">error</span> '
                  f'{_esc(event.get("error"))}')
    elif event.get("step"):
        detail = f' {_esc(event.get("step"))}'
    return f'<div class="feed-row"><strong>{label}</strong>{detail}{stamp}</div>'


def render_events_html(events, reference_time=None):
    """Render an event list to a single self-contained HTML string.

    ``events`` are dicts in file order; each is validated with
    ``toolkit.events.validate_event`` (a bad event raises ValueError
    — the renderer never renders a log that fails U.1 validation).
    ``reference_time`` is an ISO 8601 string, or None for the latest
    event ts. Pure: same ``(events, reference_time)`` renders
    byte-identical HTML.
    """
    for event in events:
        events_mod.validate_event(event)
    ref = _reference_time(events, reference_time)

    run_started = next((e for e in events
                        if e.get("type") == "run_started"), None)
    run_finished = next((e for e in events
                         if e.get("type") == "run_finished"), None)
    run_status = (run_finished.get("status") if run_finished
                  else "in progress")
    team = run_started.get("team") if run_started else None
    symbol = run_started.get("symbol") if run_started else None
    symbols = run_started.get("symbols") if run_started else None
    symbol_text = (", ".join(symbols) if symbols
                   else (symbol or "—"))
    run_id = run_started.get("run_id") if run_started else "—"

    plan = next((e for e in events if e.get("type") == "plan"), None)
    plan_steps = plan.get("steps") if plan else []

    # Agent lanes: one per step, in first-appearance order across
    # step_started / tool_call / tool_result.
    lanes = []
    for event in events:
        step = event.get("step")
        if (event.get("type") in ("step_started", "tool_call",
                                  "tool_result") and step
                and step not in lanes):
            lanes.append(step)

    evidence = _evidence_entries(events)
    gates = [e for e in events if e.get("type") == "gate_result"]
    blocked = [e for e in gates if e.get("verdict") == "BLOCK"]
    passed = [e for e in gates if e.get("verdict") == "PASS"]
    debates = [e for e in events if e.get("type") == "debate_turn"]
    memos = [e for e in events if e.get("type") == "memo_section"]

    parts = []
    parts.append("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
                 "<meta charset=\"utf-8\">\n"
                 "<meta name=\"viewport\" content=\"width=device-width, "
                 "initial-scale=1\">\n"
                 f"<title>Agent workspace — run {_esc(run_id)}</title>\n"
                 f"<style>\n{_CSS}</style>\n</head>\n<body>\n"
                 "<a class=\"skip-link\" href=\"#main\">Skip to main "
                 "content</a>\n"
                 "<header>\n"
                 f"<h1>Agent workspace — run {_esc(run_id)}</h1>\n"
                 f"<p>Team: {_esc(team or '—')} · Symbol: "
                 f"{_esc(symbol_text)} · Status: "
                 f"{_status_chip(run_status)}</p>\n"
                 "</header>\n")

    # Plan rail.
    parts.append("<nav id=\"plan-rail\" aria-label=\"Plan\">\n"
                 "<h2 id=\"plan-heading\">Plan</h2>\n")
    if plan_steps:
        parts.append("<ol>\n")
        for step in plan_steps:
            status = _step_status(events, step)
            parts.append(f"<li>{_esc(step)} {_status_chip(status)}</li>\n")
        parts.append("</ol>\n")
    else:
        parts.append("<p class=\"empty\">No plan recorded yet.</p>\n")
    parts.append("</nav>\n")

    parts.append("<main id=\"main\">\n")

    # Risk-veto banner: ONLY when a gate BLOCKs, first inside main.
    if blocked:
        gate_names = ", ".join(_esc(e.get("gate")) for e in blocked)
        reasons = " ".join(_esc(e.get("reason")) for e in blocked
                           if e.get("reason"))
        parts.append("<div id=\"risk-veto-banner\" class=\"veto-banner\" "
                     "role=\"alert\">\n"
                     f"RISK VETO — {gate_names} BLOCKED\n")
        if reasons:
            parts.append(f"<p>{reasons}</p>\n")
        parts.append("</div>\n")

    # Live feed, with tool_call/tool_result pairs collapsed.
    parts.append("<section id=\"live-feed\" "
                 "aria-labelledby=\"live-feed-heading\">\n"
                 "<h2 id=\"live-feed-heading\">Live feed</h2>\n")
    if not events:
        parts.append("<p class=\"empty\">No events recorded yet.</p>\n")
    consumed_results = set()
    for index, event in enumerate(events):
        if index in consumed_results:
            continue
        if event.get("type") == "tool_call":
            match = None
            for j in range(index + 1, len(events)):
                candidate = events[j]
                if (candidate.get("type") == "tool_result"
                        and j not in consumed_results
                        and candidate.get("tool") == event.get("tool")
                        and candidate.get("step") == event.get("step")):
                    match = j
                    break
            stamp = ""
            if ref is not None:
                stamp = (f' <time datetime="{_esc(event.get("ts"))}">'
                         f'{_esc(freshness(event.get("ts"), ref))}'
                         f'</time>')
            parts.append("<details>\n"
                         f"<summary>{_esc(event.get('tool'))}"
                         f"{_esc(' — ' + event['step']) if event.get('step') else ''}"
                         f"{stamp}</summary>\n")
            if match is not None:
                consumed_results.add(match)
                result = events[match]
                parts.append(f"<p>Result: "
                             f"{_status_chip(result.get('status', ''))}"
                             f"{_number_html(events, result)}")
                if result.get("detail"):
                    parts.append(f" — {_esc(result.get('detail'))}")
                parts.append("</p>\n")
            else:
                parts.append("<p class=\"empty\">No result recorded "
                             "yet.</p>\n")
            parts.append("</details>\n")
        else:
            parts.append(_feed_row(events, event, ref) + "\n")
    parts.append("</section>\n")

    # Agent lanes.
    parts.append("<section id=\"agent-lanes\" "
                 "aria-labelledby=\"agent-lanes-heading\">\n"
                 "<h2 id=\"agent-lanes-heading\">Agent lanes</h2>\n")
    if not lanes:
        parts.append("<p class=\"empty\">No agent steps recorded yet.</p>\n")
    for step in lanes:
        status = _step_status(events, step)
        tools = sorted({e.get("tool") for e in events
                        if e.get("step") == step
                        and e.get("type") in ("tool_call", "tool_result")
                        and e.get("tool")})
        parts.append(f"<div class=\"lane\">\n<h3>{_esc(step)} "
                     f"{_status_chip(status)}</h3>\n")
        if tools:
            parts.append("<ul>\n")
            for tool in tools:
                parts.append(f"<li>{_esc(tool)}</li>\n")
            parts.append("</ul>\n")
        else:
            parts.append("<p class=\"empty\">No tool calls yet.</p>\n")
        parts.append("</div>\n")
    parts.append("</section>\n")

    # Debate: bull / bear columns, unclassified below.
    bull = [e for e in debates
            if str(e.get("stance", "")).lower() in ("bull", "bullish")]
    bear = [e for e in debates
            if str(e.get("stance", "")).lower() in ("bear", "bearish")]
    neutral = [e for e in debates if e not in bull and e not in bear]
    parts.append("<section id=\"debate\" "
                 "aria-labelledby=\"debate-heading\">\n"
                 "<h2 id=\"debate-heading\">Bull / bear debate</h2>\n")
    if not debates:
        parts.append("<p class=\"empty\">No debate turns recorded yet.</p>\n")
    else:
        parts.append("<div class=\"debate-cols\">\n"
                     "<div><h3>Bull</h3>\n")
        parts.extend(_debate_turn_html(e) for e in bull)
        parts.append("</div>\n<div><h3>Bear</h3>\n")
        parts.extend(_debate_turn_html(e) for e in bear)
        parts.append("</div>\n</div>\n")
        if neutral:
            parts.append("<h3>Neutral / unclassified</h3>\n")
            parts.extend(_debate_turn_html(e) for e in neutral)
    parts.append("</section>\n")

    # Risk-gate card.
    parts.append("<section id=\"risk-gates\" "
                 "aria-labelledby=\"risk-gates-heading\">\n"
                 f"<h2 id=\"risk-gates-heading\">Risk gates — "
                 f"{len(passed)} passed, {len(blocked)} blocked</h2>\n")
    if not gates:
        parts.append("<p class=\"empty\">No gate results recorded yet.</p>\n")
    else:
        parts.append("<ul>\n")
        for gate in gates:
            verdict = gate.get("verdict")
            css = ("chip-block" if verdict == "BLOCK"
                   else "chip-pass")
            parts.append(f"<li>{_esc(gate.get('gate'))} "
                         f"<span class=\"chip {css}\">"
                         f"{_esc(verdict)}</span>")
            if gate.get("reason"):
                parts.append(f" — {_esc(gate.get('reason'))}")
            parts.append("</li>\n")
        parts.append("</ul>\n")
    parts.append("</section>\n")

    # Memo with citations.
    parts.append("<section id=\"memo\" aria-labelledby=\"memo-heading\">\n"
                 "<h2 id=\"memo-heading\">Memo</h2>\n")
    if not memos:
        parts.append("<p class=\"empty\">No memo sections recorded yet.</p>\n")
    for memo in memos:
        symbol = memo.get("symbol")
        cites = [anchor for anchor, ev in evidence
                 if symbol is not None
                 and ev.get("symbol") == symbol]
        parts.append(f"<div>\n<h3>{_esc(memo.get('section'))}</h3>\n")
        if memo.get("status"):
            parts.append(f"<p>Status: {_esc(memo.get('status'))}</p>\n")
        if cites:
            links = ", ".join(
                f'<a href="#{anchor}">evidence {i + 1}</a>'
                for i, anchor in enumerate(cites))
            parts.append(f"<p>Citations: {links}</p>\n")
        else:
            parts.append("<p>No evidence linked — "
                         "<span class=\"chip chip-unknown\">UNKNOWN</span>"
                         "</p>\n")
        parts.append("</div>\n")
    parts.append("</section>\n")
    parts.append("</main>\n")

    # Evidence panel.
    parts.append("<aside id=\"evidence-panel\" "
                 "aria-label=\"Evidence\">\n"
                 "<h2 id=\"evidence-heading\">Evidence</h2>\n")
    if not evidence:
        parts.append("<p class=\"empty\">No evidence recorded yet.</p>\n")
    else:
        parts.append("<ol>\n")
        for anchor, event in evidence:
            parts.append(f"<li id=\"{anchor}\">"
                         f"{_esc(event.get('source'))}")
            if event.get("symbol"):
                parts.append(f" — {_esc(event.get('symbol'))}")
            if event.get("step"):
                parts.append(f" ({_esc(event.get('step'))})")
            if event.get("last_date"):
                parts.append(f" — last date "
                             f"{_esc(event.get('last_date'))}")
            if "n_points" in event:
                parts.append(f" — {_esc(event['n_points'])} points")
            parts.append("</li>\n")
        parts.append("</ol>\n")
    parts.append("</aside>\n")

    parts.append("<footer>\n"
                 "<p>Rendered from events.jsonl (schema "
                 f"{_esc(events_mod.SCHEMA_VERSION)}) — static, "
                 "no live data.</p>\n"
                 "</footer>\n</body>\n</html>\n")
    return "".join(parts)


def _debate_turn_html(event):
    """One debate turn: speaker and text, both escaped."""
    return (f"<p><strong>{_esc(event.get('speaker'))}:</strong> "
            f"{_esc(event.get('text'))}</p>\n")


def render_events_file(input_path, output_path, reference_time=None):
    """Render an ``events.jsonl`` file to a single HTML file.

    Reads with ``toolkit.events.load_events`` (a missing file reads
    as an empty log, per U.1), validates every row through
    ``render_events_html``, and writes the HTML with LF endings.
    Returns the number of events rendered.
    """
    events = events_mod.load_events(input_path)
    text = render_events_html(events, reference_time=reference_time)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8", newline="\n")
    return len(events)
