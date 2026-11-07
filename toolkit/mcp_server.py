"""MCP server for the Investment Analyst Toolkit (Phase 1 engine 1.10).

A small standard-library-only MCP server: JSON-RPC 2.0 over stdio,
implementing the MCP protocol basics (``initialize``, ``tools/list``,
``tools/call``, ``ping``). It exposes the toolkit's data and analytics
as tools. Read-only analytics on caller-supplied data: no execution,
no network, no keys, no live data.

Prior-art check (web search, 2026-11-07 — NEPSE MCP servers already
exist, so this server is NOT the first):
- ``razaanstha/nepse-mcp-server`` (GitHub) — "NEPSE Stock Data MCP
  Server": TypeScript, fetches live NEPSE data from the ShareBazaar API
  (https://sharebazaar.vercel.app/api/zaan), 20+ tools (trending stocks,
  chart data, technical indicators, broker info, financial data).
- ``nlethetech/nepse-quant-terminal``, ``apps/mcp/server.py`` — FastMCP
  server over a quant terminal's control plane: market snapshots,
  signals, OSINT search — and an order lifecycle (paper orders, live
  trade intents with operator confirmation).
- Lipi (https://www.lipi.com.np/mcp-documentation) — remote MCP server
  for Lipi books, including a ``get_nepse_index`` tool (OAuth 2.1).

How this server differs: it fetches nothing and executes nothing. Every
tool takes its data as arguments (price closes, returns, a hypothetical
plan, trade facts) and returns analytics computed offline from those
arguments. It is the MCP front door to the toolkit's own tested
functions, not a live-data feed and not a trading interface. If live
NEPSE data is what a caller needs, the servers above are the existing
options; this one deliberately does not compete with them.

Protocol notes:
- One JSON-RPC message per line on stdin; one response per line on
  stdout. A request without an ``id`` is a notification and gets no
  response (e.g. ``notifications/initialized``).
- Malformed JSON -> parse error (-32700, id null). A non-object message
  -> invalid request (-32600). An unknown method -> -32601. Bad or
  missing tool arguments -> -32602 from the dispatcher, or a tool
  error (``isError: true``) when the arguments are well-formed but the
  toolkit function refuses them (e.g. stop above entry).
- ``initialize`` returns protocol version ``2024-11-05``,
  capabilities ``{"tools": {}}``, and server info. The client must send
  ``notifications/initialized`` afterwards (ignored if it does not).

Usage: ``python -m toolkit.mcp_server`` (reads stdin, writes stdout).
"""

from __future__ import annotations

import json
import sys

from . import indicators, nepal_frictions, position_plan, quant, report, timemachine

__all__ = ["TOOLS", "handle_request", "main", "serve"]

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "investment-analyst-toolkit-mcp"
SERVER_VERSION = "0.8.0"

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602

_TOOLS = [
    {
        "name": "indicators",
        "description": (
            "Indicator snapshot from caller-supplied closes: latest close, "
            "SMA-20/50, Wilder RSI-14. Research only; past bars only."),
        "inputSchema": {
            "type": "object",
            "properties": {"closes": {"type": "array", "items": {"type": "number"}}},
            "required": ["closes"],
        },
    },
    {
        "name": "screen_setups",
        "description": (
            "Named-setup screening on caller-supplied closes (breakout_20d, "
            "bull_flag, reclaim_ma20, oversold, oversold_bounce). A match "
            "means past closes satisfy the rule; it is not a signal."),
        "inputSchema": {
            "type": "object",
            "properties": {"closes": {"type": "array", "items": {"type": "number"}}},
            "required": ["closes"],
        },
    },
    {
        "name": "timemachine_simulate",
        "description": (
            "Seeded Monte-Carlo replay of a hypothetical exit plan on "
            "caller-supplied closes. Simulation, not a prediction."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prices": {"type": "array", "items": {"type": "number"}},
                "entry": {"type": "number"},
                "stop": {"type": "number"},
                "target": {"type": "number"},
                "n_sims": {"type": "integer"},
                "seed": {"type": "integer"},
                "horizon": {"type": "integer"},
            },
            "required": ["prices", "entry", "stop", "target"],
        },
    },
    {
        "name": "risk_gate",
        "description": (
            "Risk gate on a hypothetical exit plan {entry, stop, target, "
            "optionally budget and entry_window_ok}. A PASS is not a yes; "
            "a BLOCK names the failing check. Never an order."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan": {"type": "object"},
                "entry_window_ok": {"type": "boolean"},
            },
            "required": ["plan"],
        },
    },
    {
        "name": "quant_risk_summary",
        "description": (
            "Risk summary (Sharpe, Sortino, max drawdown, VaR/CVaR) from "
            "caller-supplied returns and prices."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "returns": {"type": "array", "items": {"type": "number"}},
                "prices": {"type": "array", "items": {"type": "number"}},
            },
            "required": ["returns", "prices"],
        },
    },
    {
        "name": "quant_dcf",
        "description": (
            "FCFF DCF value from caller-supplied free cash flows, discount "
            "rate, and terminal growth. A model output, not a valuation."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "free_cash_flows": {"type": "array", "items": {"type": "number"}},
                "discount_rate": {"type": "number"},
                "terminal_growth": {"type": "number"},
            },
            "required": ["free_cash_flows", "discount_rate", "terminal_growth"],
        },
    },
    {
        "name": "nepse_frictions",
        "description": (
            "NEPSE trade-cost breakdown from sourced frictions (broker "
            "slabs, SEBON fee, DP charge, CGT by holding period). Sourced "
            "28 Oct 2026; re-check a current notice before relying on it."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "trade_value": {"type": "number"},
                "side": {"type": "string"},
                "holding_days": {"type": "integer"},
                "realised_gain": {"type": "number"},
            },
            "required": ["trade_value", "side"],
        },
    },
    {
        "name": "research_report",
        "description": (
            "One-page HTML research report for a symbol from "
            "caller-supplied bars, fundamentals, and peer rows. Every "
            "number traces to an input row or is UNKNOWN; no data means "
            "the report says so plainly."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "bars": {"type": "array", "items": {"type": "object"}},
                "fundamentals": {"type": "array", "items": {"type": "object"}},
                "peers": {"type": "array", "items": {"type": "object"}},
                "sector": {"type": "string"},
            },
            "required": ["symbol"],
        },
    },
]

TOOLS = _TOOLS


def _numbers(name, value):
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list of numbers")
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            # Toolkit convention is ValueError for all input validation
            # (see toolkit/checks.py); a TypeError here would be the only
            # one in the toolkit.
            raise ValueError(  # noqa: TRY004
                f"{name} must contain only numbers, got {item!r}")
        out.append(float(item))
    return out


def _tool_indicators(args):
    closes = _numbers("closes", args.get("closes"))
    result = {"n": len(closes), "latest_close": closes[-1]}
    if len(closes) >= 20:
        result["sma20"] = indicators.sma(closes, 20)[-1]
    if len(closes) >= 50:
        result["sma50"] = indicators.sma(closes, 50)[-1]
    if len(closes) >= 15:
        result["rsi14"] = indicators.rsi(closes, 14)[-1]
    return result


def _tool_screen_setups(args):
    closes = _numbers("closes", args.get("closes"))
    return timemachine.detect_setups(closes)


def _tool_timemachine_simulate(args):
    prices = _numbers("prices", args.get("prices"))
    kwargs = {}
    for key in ("n_sims", "seed", "horizon"):
        if args.get(key) is not None:
            kwargs[key] = args[key]
    return timemachine.simulate(
        prices, args.get("entry"), args.get("stop"), args.get("target"),
        **kwargs)


def _tool_risk_gate(args):
    plan = args.get("plan")
    if not isinstance(plan, dict):
        # Toolkit convention is ValueError for all input validation
        # (see toolkit/checks.py).
        raise ValueError(  # noqa: TRY004
            "plan must be an object with entry, stop, target")
    for key in ("entry", "stop", "target"):
        if plan.get(key) is None:
            raise ValueError(f"plan.{key} is required")
    built = position_plan.plan_exit(
        plan["entry"], plan["stop"], plan.get("target"))
    config = dict(position_plan.DEFAULT_RESEARCH_CONFIG)
    budget = plan.get("budget")
    if budget is not None:
        config["budget"] = budget
    sized = position_plan.size_position(
        config["budget"], config["max_loss_per_trade_usd"],
        config["max_position_pct"], config["max_order_usd"],
        built["entry"], built["risk_per_share"],
        min_order_usd=config["min_order_usd"])
    full_plan = dict(built)
    full_plan.update(sized)
    full_plan.update({
        "symbol": plan.get("symbol", "MCP"),
        "asset_type": "equity",
        "entry_window_ok": bool(args.get("entry_window_ok")),
        "cash_available": config["budget"],
    })
    return position_plan.risk_gate(full_plan, config)


def _tool_quant_risk_summary(args):
    returns = _numbers("returns", args.get("returns"))
    prices = _numbers("prices", args.get("prices"))
    return quant.risk_summary(returns, prices)


def _tool_quant_dcf(args):
    flows = _numbers("free_cash_flows", args.get("free_cash_flows"))
    return quant.dcf_fcff(
        flows, args.get("discount_rate"), args.get("terminal_growth"))


def _tool_nepse_frictions(args):
    frictions = nepal_frictions.sourced_nepal_frictions()
    return nepal_frictions.trade_cost_report(
        args.get("trade_value"), args.get("side"), frictions,
        realised_gain=args.get("realised_gain"),
        holding_days=args.get("holding_days"))


def _tool_research_report(args):
    symbol = args.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    bars = []
    for row in args.get("bars") or []:
        if not isinstance(row, dict) or "close" not in row:
            raise ValueError("each bar needs at least a close")
        import datetime
        raw_date = row.get("date")
        date = (datetime.date.fromisoformat(raw_date) if isinstance(raw_date, str)
                else datetime.date.today())
        bars.append({"date": date, "close": float(row["close"])})
    built = report.build_report(
        symbol, bars=bars or None,
        fundamentals=args.get("fundamentals") or None,
        peers=args.get("peers") or None,
        sector=args.get("sector"))
    return {"status": built["status"], "thesis": built["thesis"],
            "html": report.render_html(built)}


_HANDLERS = {
    "indicators": _tool_indicators,
    "screen_setups": _tool_screen_setups,
    "timemachine_simulate": _tool_timemachine_simulate,
    "risk_gate": _tool_risk_gate,
    "quant_risk_summary": _tool_quant_risk_summary,
    "quant_dcf": _tool_quant_dcf,
    "nepse_frictions": _tool_nepse_frictions,
    "research_report": _tool_research_report,
}


def _result(request_id, payload):
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def handle_request(request):
    """Handle one decoded JSON-RPC message (dict).

    Returns a response dict, or None for notifications (no ``id``).
    Never raises: malformed messages produce error responses.
    """
    if not isinstance(request, dict):
        return _error(None, _INVALID_REQUEST, "request must be a JSON object")
    method = request.get("method")
    request_id = request.get("id")
    is_notification = "id" not in request
    if not isinstance(method, str) or not method:
        if is_notification:
            return None
        return _error(request_id, _INVALID_REQUEST,
                      "request needs a string method")
    if method == "notifications/initialized":
        return None
    if is_notification:
        return None
    if method == "ping":
        return _result(request_id, {})
    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "tools/list":
        return _result(request_id, {"tools": _TOOLS})
    if method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict):
            return _error(request_id, _INVALID_PARAMS,
                          "tools/call needs params {name, arguments}")
        name = params.get("name")
        handler = _HANDLERS.get(name) if isinstance(name, str) else None
        if handler is None:
            return _error(request_id, _INVALID_PARAMS,
                          f"unknown tool: {name!r}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(request_id, _INVALID_PARAMS,
                          "tools/call arguments must be an object")
        try:
            value = handler(args)
        except (ValueError, TypeError) as exc:
            return _result(request_id, {
                "content": [{"type": "text",
                             "text": f"tool error: {exc}"}],
                "isError": True,
            })
        return _result(request_id, {
            "content": [{"type": "text",
                         "text": json.dumps(value, default=str)}],
        })
    return _error(request_id, _METHOD_NOT_FOUND,
                  f"unknown method: {method!r}")


def serve(in_stream=None, out_stream=None):
    """Serve JSON-RPC over stdio until EOF (one message per line)."""
    in_stream = sys.stdin if in_stream is None else in_stream
    out_stream = sys.stdout if out_stream is None else out_stream
    for line in in_stream:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = _error(None, _PARSE_ERROR, "Parse error")
        else:
            response = handle_request(request)
        if response is not None:
            out_stream.write(json.dumps(response) + "\n")
            out_stream.flush()


def main(argv=None):
    """CLI entry point: ``python -m toolkit.mcp_server``."""
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
