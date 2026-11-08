"""Protocol-level tests for toolkit/mcp_server.py (engine 1.10).

Offline: the server is exercised through handle_request() directly and
through serve() over piped stdin/stdout. Synthetic data only — the
server takes all data as tool arguments and fetches nothing.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11: tomllib is stdlib from 3.11
    tomllib = None

from toolkit import mcp_server

REPO_ROOT = Path(__file__).resolve().parent.parent

CLOSES = [100.0 + i for i in range(30)]  # rising 100 -> 129


def _call(name, arguments, request_id=1):
    return mcp_server.handle_request({
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })


def _text(response):
    assert "error" not in response, response
    assert response["result"]["content"][0]["type"] == "text"
    return json.loads(response["result"]["content"][0]["text"])


# --- handshake ----------------------------------------------------------------

def test_initialize_handshake():
    response = mcp_server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}},
    })
    assert response["result"]["protocolVersion"] == "2024-11-05"
    assert response["result"]["capabilities"] == {"tools": {}}
    assert response["result"]["serverInfo"]["name"] == \
        "investment-analyst-toolkit-mcp"


@pytest.mark.skipif(
    tomllib is None, reason="tomllib is stdlib from Python 3.11")
def test_server_version_matches_pyproject():
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert mcp_server.SERVER_VERSION == pyproject["project"]["version"]


def test_ping_and_initialized_notification():
    assert mcp_server.handle_request(
        {"jsonrpc": "2.0", "id": 7, "method": "ping"})["result"] == {}
    assert mcp_server.handle_request(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_names_all_eight():
    response = mcp_server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [t["name"] for t in response["result"]["tools"]]
    assert names == ["indicators", "screen_setups", "timemachine_simulate",
                     "risk_gate", "quant_risk_summary", "quant_dcf",
                     "nepse_frictions", "research_report"]
    for tool in response["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object"
        assert "description" in tool


# --- tool calls on synthetic data ---------------------------------------------

def test_call_indicators_synthetic():
    value = _text(_call("indicators", {"closes": CLOSES}))
    assert value["n"] == 30
    assert value["latest_close"] == 129.0
    assert value["sma20"] == pytest.approx(119.5)
    assert value["rsi14"] == pytest.approx(100.0)


def test_call_screen_setups_synthetic():
    value = _text(_call("screen_setups", {"closes": CLOSES}))
    # detect_setups returns {name: [matched_bool, reason]} per setup.
    assert value["breakout_20d"][0] is True
    assert value["oversold"][0] is False


def test_call_timemachine_simulate_synthetic():
    closes = [100.0 + i for i in range(45)]  # 44 steps -> 25 windows of 20
    value = _text(_call("timemachine_simulate", {
        "prices": closes, "entry": 144.0, "stop": 135.0, "target": 162.0,
        "n_sims": 100, "seed": 19, "horizon": 20}))
    assert value["seed"] == 19
    assert value["n_sims"] == 100
    assert 0.0 <= value["target_first"] <= 1.0
    assert value["verdict"] in ("Close to a coin flip", "Leans toward target",
                               "Leans toward stop", "Strongly toward target",
                               "Strongly toward stop")


def test_call_risk_gate_synthetic():
    value = _text(_call("risk_gate", {
        "plan": {"entry": 129.0, "stop": 120.0, "target": 147.0}}))
    assert value["verdict"] == "BLOCK"  # entry window not confirmed
    assert value["checks"]["entry_window"]["pass"] is False
    value_ok = _text(_call("risk_gate", {
        "plan": {"entry": 129.0, "stop": 120.0, "target": 147.0},
        "entry_window_ok": True}))
    assert value_ok["checks"]["entry_window"]["pass"] is True


def test_call_quant_risk_summary_synthetic():
    returns = [0.01, -0.02, 0.015, 0.005, -0.01] * 10
    prices = [100.0]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    value = _text(_call("quant_risk_summary",
                        {"returns": returns, "prices": prices}))
    assert value["max_drawdown"] is not None
    assert "historical_var" in value


def test_call_quant_dcf_synthetic():
    value = _text(_call("quant_dcf", {
        "free_cash_flows": [100.0, 110.0, 120.0],
        "discount_rate": 0.10, "terminal_growth": 0.03}))
    assert value["enterprise_value"] > 0


def test_call_nepse_frictions_synthetic():
    value = _text(_call("nepse_frictions", {
        "trade_value": 100000.0, "side": "buy"}))
    assert value["status"] == "KNOWN"
    assert value["exchange"] == "NEPSE"
    assert value["total_cost"] > 0
    value_sell = _text(_call("nepse_frictions", {
        "trade_value": 100000.0, "side": "sell",
        "holding_days": 400, "realised_gain": 10000.0}))
    assert value_sell["capital_gains_tax"] == pytest.approx(375.0)


def test_call_research_report_synthetic():
    value = _text(_call("research_report", {
        "symbol": "DEMO",
        "bars": [{"date": "2026-01-01", "close": 100.0},
                 {"date": "2026-01-02", "close": 101.0}],
        "fundamentals": [{"field": "revenue", "value": 1000.0,
                          "source": "Annual report FY 2081/82",
                          "period": "FY 2081/82"}],
        "peers": [{"symbol": "PEER1", "metric": "pe_ratio", "value": 12.5,
                   "source": ""}],
    }))
    assert value["status"] == "ok"
    assert "Research report" in value["html"]
    assert "UNKNOWN" in value["html"]  # peer with no source


def test_call_research_report_no_data():
    value = _text(_call("research_report", {"symbol": "NABIL"}))
    assert value["status"] == "no_data"
    assert "No data was supplied for NABIL" in value["html"]


# --- errors ---------------------------------------------------------------------

def test_unknown_tool_is_invalid_params():
    response = _call("place_order", {})
    assert response["error"]["code"] == -32602


def test_unknown_method_is_method_not_found():
    response = mcp_server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
    assert response["error"]["code"] == -32601


def test_tool_refusal_is_error_content_not_exception():
    response = _call("timemachine_simulate", {
        "prices": CLOSES, "entry": 129.0, "stop": 130.0, "target": 147.0})
    assert response["result"]["isError"] is True
    assert "stop" in response["result"]["content"][0]["text"]


def test_non_object_request_is_invalid_request():
    assert mcp_server.handle_request([1, 2, 3])["error"]["code"] == -32600
    assert mcp_server.handle_request("hi")["error"]["code"] == -32600


def test_notification_gets_no_response():
    assert mcp_server.handle_request(
        {"jsonrpc": "2.0", "method": "tools/list"}) is None


def test_serve_end_to_end_over_stdio():
    lines = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "indicators",
                               "arguments": {"closes": CLOSES}}}),
        "this is not json",
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "nope"}),
    ]) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "toolkit.mcp_server"],
        cwd=REPO_ROOT, input=lines, capture_output=True, text=True,
        timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr
    responses = [json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip()]
    # Notification produces no response: 6 inputs -> 5 responses.
    assert len(responses) == 5
    assert [r["id"] for r in responses] == [1, 2, 3, None, 4]
    assert responses[3]["error"]["code"] == -32700  # parse error
    assert responses[4]["error"]["code"] == -32601  # unknown method
    assert len(responses[1]["result"]["tools"]) == 8  # tools/list is 2nd
