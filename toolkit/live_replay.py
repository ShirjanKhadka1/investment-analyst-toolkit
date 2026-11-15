"""Live-mode replay helpers — Round U, PR U.3.

Pure functions over U.1 event logs: the cost/latency meter, the
run-diff view, and the recorded-run replay page. The contract is
pre-registered in ``docs/u3-live-replay-protocol.md`` — this module
implements that protocol, it does not define it.

The replay page REUSES ``toolkit.workspace_ui.render_events_html``
— there is no second renderer here, only a banner, a meter card and
a diff section inserted into the U.2 render. The meter never invents
a cost: the U.1 schema records no token counts, so the cost is
``$0.00`` unless the caller supplies both a token count and a
per-token rate (the rate defaults to 0). Latency is always
``tool_result`` ts minus the matching ``tool_call`` ts, paired by
the same rule as the U.2 renderer.

Stdlib only. No network, no JavaScript, no animation.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from toolkit import events as events_mod
from toolkit import workspace_ui

__all__ = [
    "compute_cost_latency",
    "diff_runs",
    "render_diff_html",
    "render_meter_html",
    "render_replay_file",
    "render_replay_html",
    "step_status",
]

# Identity key fields: everything that makes an event the same slot
# in two runs. schema_version, run_id, seq, ts and prev_hash are
# deliberately NOT here — they differ between any two runs by
# construction and must not manufacture differences.
_IDENTITY_FIELDS = ("type", "step", "tool", "symbol", "source", "gate",
                    "section", "speaker")

# Content fields compared for a "changed" pair (identity key matches
# but the payload differs).
_CONTENT_FIELDS = ("status", "verdict", "text", "reason", "n_points",
                   "detail", "last_date", "stance", "steps", "team",
                   "exchange", "market", "symbols")


def _parse_ts(value):
    """Parse an ISO 8601 timestamp. Returns a datetime or None.

    Same rule as ``toolkit.events``: the ``Z`` suffix is normalised
    to ``+00:00``, and the timestamp must be timezone-aware.
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


def step_status(events, step):
    """Derived status of one plan step from the run's events.

    The SAME rule as ``toolkit.workspace_ui._step_status`` (duplicated
    here so the diff module does not depend on renderer privates):
    ``failed`` when a ``tool_result`` for the step has status
    ``"failed"`` (a failure is never hidden behind a later success),
    ``done`` when one has status ``"ok"``, ``in progress`` when a
    ``step_started`` exists but no result yet, ``not started``
    otherwise.
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


def _paired_latencies(events):
    """Matched call/result latencies, in file order.

    Pairing is the U.2 renderer's rule: a ``tool_call`` pairs with
    the next ``tool_result`` after it with the same ``tool`` and the
    same ``step``. An unmatched call contributes no latency.
    """
    pairs = []
    consumed = set()
    for index, event in enumerate(events):
        if event.get("type") != "tool_call":
            continue
        call_dt = _parse_ts(event.get("ts"))
        for j in range(index + 1, len(events)):
            if j in consumed:
                continue
            candidate = events[j]
            if (candidate.get("type") == "tool_result"
                    and candidate.get("tool") == event.get("tool")
                    and candidate.get("step") == event.get("step")):
                consumed.add(j)
                result_dt = _parse_ts(candidate.get("ts"))
                if call_dt is not None and result_dt is not None:
                    latency = (result_dt - call_dt).total_seconds()
                    pairs.append({"tool": event.get("tool"),
                                  "step": event.get("step"),
                                  "latency_s": latency})
                break
    return pairs


def compute_cost_latency(events, rate_per_token_usd=0.0, tokens=None):
    """Cost/latency meter for one event list. Pure; validates input.

    ``rate_per_token_usd`` is an explicit caller-supplied parameter
    (default 0) and ``tokens`` an explicit caller-supplied token
    count (default None, treated as 0): the U.1 schema records no
    token counts, so no cost is ever inferred from the events.
    ``cost_usd`` is ``(tokens or 0) * rate_per_token_usd`` — ``$0.00``
    unless the caller supplies both a count and a non-zero rate.
    """
    for event in events:
        events_mod.validate_event(event)
    if rate_per_token_usd < 0:
        raise ValueError(
            "rate_per_token_usd must not be negative, got "
            f"{rate_per_token_usd!r}")
    if tokens is not None and (not isinstance(tokens, int)
                               or isinstance(tokens, bool) or tokens < 0):
        raise ValueError(
            f"tokens must be a non-negative integer or None, got "
            f"{tokens!r}")
    n_calls = sum(1 for e in events if e.get("type") == "tool_call")
    n_results = sum(1 for e in events if e.get("type") == "tool_result")
    pairs = _paired_latencies(events)
    n_unmatched = n_calls - len(pairs)
    total = sum(p["latency_s"] for p in pairs)
    mean = total / len(pairs) if pairs else 0.0
    slowest = None
    for pair in pairs:
        if slowest is None or pair["latency_s"] > slowest["latency_s"]:
            slowest = pair
    cost = (tokens or 0) * rate_per_token_usd
    if tokens is None:
        basis = ("no token counts recorded in the event log; cost is "
                 "$0.00 unless the caller supplies a token count and "
                 "a per-token rate")
    elif rate_per_token_usd == 0:
        basis = (f"{tokens} tokens supplied but no per-token rate "
                 "supplied (default 0)")
    else:
        basis = (f"{tokens} tokens at ${rate_per_token_usd} per token")
    return {"n_calls": n_calls,
            "n_results": n_results,
            "n_unmatched_calls": n_unmatched,
            "total_latency_s": total,
            "mean_latency_s": mean,
            "slowest": slowest,
            "per_tool": pairs,
            "cost_usd": cost,
            "cost_basis": basis}


def _esc(value):
    """HTML-escape a value (``None`` renders as an empty string)."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _fmt_seconds(value):
    """A latency in seconds, two decimals, no invented precision."""
    return f"{value:.2f} s"


def render_meter_html(meter):
    """Render a cost/latency meter dict to one HTML section.

    Deterministic: same meter dict renders byte-identical HTML.
    """
    slowest = meter["slowest"]
    slowest_text = ("—" if slowest is None else
                    f"{_esc(slowest['tool'])}"
                    f"{' — ' + _esc(slowest['step']) if slowest.get('step') else ''} "
                    f"({_fmt_seconds(slowest['latency_s'])})")
    parts = [("<section id=\"cost-latency-meter\" "
             "aria-labelledby=\"cost-latency-meter-heading\">\n"
             "<h2 id=\"cost-latency-meter-heading\">Cost / latency "
             "meter</h2>\n"
             f"<p>Tool calls: {meter['n_calls']} · Results: "
             f"{meter['n_results']} · Unmatched calls: "
             f"{meter['n_unmatched_calls']}</p>\n"
             f"<p>Total latency: "
             f"{_fmt_seconds(meter['total_latency_s'])} · Mean latency: "
             f"{_fmt_seconds(meter['mean_latency_s'])} · Slowest call: "
             f"{slowest_text}</p>\n")]
    if meter["per_tool"]:
        parts.append("<ul>\n")
        for pair in meter["per_tool"]:
            parts.append(f"<li>{_esc(pair['tool'])}"
                         f"{' — ' + _esc(pair['step']) if pair.get('step') else ''}: "
                         f"{_fmt_seconds(pair['latency_s'])}</li>\n")
        parts.append("</ul>\n")
    parts.append(f"<p>Cost: ${meter['cost_usd']:.2f} — "
                 f"{_esc(meter['cost_basis'])}</p>\n"
                 "</section>\n")
    return "".join(parts)


def _identity_key(event):
    """Identity key of an event: the slot it occupies in a run."""
    return tuple(event.get(field) for field in _IDENTITY_FIELDS)


def _content(event):
    """The compared content fields of an event (present ones only)."""
    return {field: event[field] for field in _CONTENT_FIELDS
            if field in event}


def _key_text(key):
    """One-line readable form of an identity key (NOT escaped —
    the renderer escapes it once, at render time)."""
    labelled = [f"{field}={value!r}" for field, value in
                zip(_IDENTITY_FIELDS, key) if value is not None]
    return ", ".join(labelled)


def diff_runs(events_a, events_b):
    """Diff two event lists. Pure; validates both inputs.

    Returns ``{"run_a", "run_b", "n_a", "n_b", "added", "removed",
    "changed", "step_status_changes"}``. ``added``/``removed`` are
    identity-key entries with no pair in the other run; ``changed``
    are pairs with the same key but different content fields (each
    carries ``before``/``after`` content dicts);
    ``step_status_changes`` are steps whose derived status (same rule
    as ``step_status``) differs between the runs.
    """
    for event in list(events_a) + list(events_b):
        events_mod.validate_event(event)

    def run_id(events):
        started = next((e for e in events
                        if e.get("type") == "run_started"), None)
        return started.get("run_id") if started else None

    def grouped(events):
        order = []
        by_key = {}
        for event in events:
            key = _identity_key(event)
            if key not in by_key:
                by_key[key] = []
                order.append(key)
            by_key[key].append(event)
        return order, by_key

    order_a, by_key_a = grouped(events_a)
    order_b, by_key_b = grouped(events_b)
    added = []
    removed = []
    changed = []
    for key in order_b:
        in_a = by_key_a.get(key, [])
        in_b = by_key_b[key]
        for i, event_b in enumerate(in_b):
            if i >= len(in_a):
                added.append({"key": _key_text(key),
                              "type": event_b.get("type")})
            elif _content(in_a[i]) != _content(event_b):
                changed.append({"key": _key_text(key),
                                "type": event_b.get("type"),
                                "before": _content(in_a[i]),
                                "after": _content(event_b)})
    for key in order_a:
        in_a = by_key_a[key]
        in_b = by_key_b.get(key, [])
        for i, event_a in enumerate(in_a):
            if i >= len(in_b):
                removed.append({"key": _key_text(key),
                                "type": event_a.get("type")})
    steps = []
    for events in (events_a, events_b):
        for event in events:
            step = event.get("step")
            if (event.get("type") in ("step_started", "tool_call",
                                      "tool_result") and step
                    and step not in steps):
                steps.append(step)
    step_status_changes = []
    for step in steps:
        before = step_status(events_a, step)
        after = step_status(events_b, step)
        if before != after:
            step_status_changes.append({"step": step,
                                        "before": before,
                                        "after": after})
    return {"run_a": run_id(events_a),
            "run_b": run_id(events_b),
            "n_a": len(events_a),
            "n_b": len(events_b),
            "added": added,
            "removed": removed,
            "changed": changed,
            "step_status_changes": step_status_changes}


def render_diff_html(diff):
    """Render a ``diff_runs`` dict to one HTML section.

    Deterministic: same diff dict renders byte-identical HTML.
    """
    n_added = len(diff["added"])
    n_removed = len(diff["removed"])
    n_changed = len(diff["changed"])
    n_steps = len(diff["step_status_changes"])
    parts = [("<section id=\"run-diff\" "
             "aria-labelledby=\"run-diff-heading\">\n"
             "<h2 id=\"run-diff-heading\">Run diff — "
             f"{_esc(diff['run_a'])} vs {_esc(diff['run_b'])}</h2>\n"
             f"<p>{n_added} added, {n_removed} removed, {n_changed} "
             f"changed; {n_steps} per-step status changes "
             f"({diff['n_a']} events vs {diff['n_b']} events)</p>\n")]
    for label, entries in (("Added", diff["added"]),
                           ("Removed", diff["removed"]),
                           ("Changed", diff["changed"])):
        if not entries:
            continue
        parts.append(f"<h3>{label}</h3>\n<ul>\n")
        for entry in entries:
            if "before" in entry:
                before = json.dumps(entry["before"], sort_keys=True)
                after = json.dumps(entry["after"], sort_keys=True)
                parts.append(f"<li>{_esc(entry['type'])} "
                             f"({_esc(entry['key'])}): "
                             f"{_esc(before)} → {_esc(after)}</li>\n")
            else:
                parts.append(f"<li>{_esc(entry['type'])} "
                             f"({_esc(entry['key'])})</li>\n")
        parts.append("</ul>\n")
    if diff["step_status_changes"]:
        parts.append("<h3>Per-step status changes</h3>\n<ul>\n")
        for change in diff["step_status_changes"]:
            parts.append(f"<li>{_esc(change['step'])}: "
                         f"{_esc(change['before'])} → "
                         f"{_esc(change['after'])}</li>\n")
        parts.append("</ul>\n")
    else:
        parts.append("<p>No per-step status changes.</p>\n")
    parts.append("</section>\n")
    return "".join(parts)


def _run_date(events):
    """Date part (``YYYY-MM-DD``) of the run's first event ts.

    The run date is derived from the log, never typed: the
    ``run_started`` ts when one exists, else the first event's ts,
    else ``"unknown"``.
    """
    first = next((e for e in events
                  if e.get("type") == "run_started"), None)
    if first is None and events:
        first = events[0]
    if first is None:
        return "unknown"
    parsed = _parse_ts(first.get("ts"))
    if parsed is None:
        return "unknown"
    return parsed.strftime("%Y-%m-%d")


def render_replay_html(events, diff_events=None, reference_time=None,
                       provider="FAKE"):
    """Render a recorded-run replay page (static, no server needed).

    ``events`` is the recorded run; ``diff_events`` is the second run
    for the diff section (None omits it). The page is the U.2 render
    plus, in order: a ``recorded demo`` banner directly under
    ``<body>``, then a cost/latency meter card and (when given) a run
    diff section directly after ``</header>``. ``provider`` is the
    caller-supplied provider label for the run — the U.1 fixture
    provider is the fake provider, labelled honestly as ``FAKE``.
    Pure: same inputs render byte-identical HTML.
    """
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError(
            f"provider must be a non-empty string, got {provider!r}")
    for event in events:
        events_mod.validate_event(event)
    if diff_events is not None:
        for event in diff_events:
            events_mod.validate_event(event)
    base = workspace_ui.render_events_html(events,
                                           reference_time=reference_time)
    run_id = next((e.get("run_id") for e in events
                   if e.get("type") == "run_started"), "—")
    date = _run_date(events)
    if provider == "FAKE":
        provider_note = (" (fake provider — synthetic demo data, no "
                         "live data, no real cost)")
    else:
        provider_note = ""
    banner = (f"<div id=\"recorded-demo-banner\" role=\"note\">\n"
              f"<h2>Recorded demo</h2>\n"
              f"<p>This is a recorded demo of run {_esc(run_id)} — a "
              f"replay of a past run, not a live run. Run date: "
              f"{_esc(date)}. Provider: {_esc(provider)}"
              f"{provider_note}.</p>\n"
              f"</div>\n")
    meter = render_meter_html(compute_cost_latency(events))
    extra = meter
    if diff_events is not None:
        extra += render_diff_html(diff_runs(events, diff_events))
    replay = base.replace("<body>\n", "<body>\n" + banner, 1)
    replay = replay.replace("</header>\n",
                            "</header>\n" + extra, 1)
    return replay


def render_replay_file(input_path, output_path, diff_path=None,
                       reference_time=None, provider="FAKE"):
    """Render an ``events.jsonl`` file (+ optional diff log) to a
    static replay HTML file. Reads with
    ``toolkit.events.load_events`` (a missing input file reads as an
    empty log, per U.1 — the CLI refuses a missing file before this
    is called), validates every row, and writes the HTML with LF
    endings. Returns the number of events rendered.
    """
    events = events_mod.load_events(input_path)
    diff_events = (events_mod.load_events(diff_path)
                   if diff_path is not None else None)
    text = render_replay_html(events, diff_events=diff_events,
                              reference_time=reference_time,
                              provider=provider)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8", newline="\n")
    return len(events)
