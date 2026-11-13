"""Tests for the executable agent team (PR B).

All LLM calls go through the fake provider; all data comes from an
injected fetcher. No network, no keys, no real provider anywhere in
this file.
"""

import json

import pytest

from toolkit.agent_team import (
    SERIES_LLM_TEAM,
    SERIES_PRE_REGISTERED,
    AgentTeam,
    build_plan,
    calls_by_series,
    render_memo_md,
    render_series_md,
    score_series,
    settle_llm_call,
    write_llm_call,
)
from toolkit.decision_log import load_calls, settle_call, write_call
from toolkit.llm_providers import FakeProvider, LLMCapError, LLMSchemaError

BULL = json.dumps({"side": "bull", "claims": ["revenue growing",
                                              "margins expanding"],
                   "key_risks": ["valuation is rich"]})
BEAR = json.dumps({"side": "bear", "claims": ["growth is slowing"],
                   "key_risks": ["a miss would re-rate the stock"]})
TRADER = json.dumps({"expected": "up", "confidence": 0.6,
                     "horizon_days": 5,
                     "rationale": "bull case outweighs bear on the numbers"})
PM = json.dumps({"thesis": "Up over 5 trading days on the bull case.",
                 "key_risks": ["valuation is rich"],
                 "what_would_change_my_mind": "a revenue miss"})
FULL_SCRIPT = [BULL, BEAR, TRADER, PM]

AS_OF = "2026-11-13"
SYMBOL = "AAPL"


def _bars(n=30, start=100.0, step=0.5):
    """Monotonic closes: 100, 100.5, 101, ... — visible, completed,
    deterministic. Dates end the day before AS_OF."""
    from datetime import date, timedelta

    base = date.fromisoformat(AS_OF)
    out = []
    for i in range(n):
        day = base - timedelta(days=n - i)
        out.append((day.isoformat(), round(start + i * step, 2)))
    return out


def _fetcher(pairs=None, volumes=True, fundamentals=None):
    """Injected fetcher: prices (+ volumes) and optional EDGAR-style
    fundamentals. Never touches the network."""
    pairs = pairs if pairs is not None else _bars()
    vols = None
    if volumes:
        vols = {d: 1_000_000 for d, _c in pairs}
    fund = fundamentals

    def fetch(symbol, market, as_of, inputs):
        return {"prices": {"source": "test-csv", "pairs": list(pairs),
                           "volumes": vols},
                "fundamentals": fund, "fx": None, "macro": None}

    return fetch


def _team(tmp_path, provider, fetcher=None, **kwargs):
    return AgentTeam(
        provider, decision_log_path=tmp_path / "calls.jsonl",
        reflections_path=tmp_path / "reflections.jsonl",
        checkpoint_path=tmp_path / "checkpoint.json",
        fetcher=fetcher if fetcher is not None else _fetcher(),
        **kwargs)


def _run_pass(tmp_path, **kwargs):
    provider = FakeProvider(script=list(FULL_SCRIPT))
    team = _team(tmp_path, provider, **kwargs)
    memo = team.run(SYMBOL, "us", AS_OF, exchange="Nasdaq")
    return team, provider, memo


# --- the full run ------------------------------------------------------------

def test_full_run_pass_logs_call_and_memo(tmp_path):
    _team, provider, memo = _run_pass(tmp_path)
    assert memo["direction"] == "up"
    assert memo["confidence"] == 0.6
    assert memo["gate_verdict"] == "PASS"
    assert memo["gate_failed_checks"] == []
    assert memo["series"] == SERIES_LLM_TEAM
    assert memo["call_id"] is not None
    assert provider.calls == 4
    calls = load_calls(tmp_path / "calls.jsonl")
    assert len(calls) == 1
    call = calls[0]
    assert call["series"] == SERIES_LLM_TEAM
    assert call["symbol"] == SYMBOL
    assert call["expected"] == "up"
    assert call["status"] == "open"
    assert "debate_bull" in call["prompt_hashes"]
    assert "trader" in call["prompt_hashes"]
    assert memo["disclaimer"].startswith(
        "LLM outputs are not validated predictions")
    assert memo["paper_only"] is True
    assert memo["order"] is None


def test_memo_render_has_verdict_disclaimer_no_order(tmp_path):
    _team, _provider, memo = _run_pass(tmp_path)
    text = render_memo_md(memo)
    assert "Risk gate: **PASS**" in text
    assert "LLM outputs are not validated predictions" in text
    assert "Paper only" in text
    assert "proposes no order" in text
    assert "instructs no execution" in text
    assert memo["call_id"] in text


def test_memo_without_log_path_still_renders(tmp_path):
    provider = FakeProvider(script=list(FULL_SCRIPT))
    team = AgentTeam(provider, checkpoint_path=tmp_path / "cp.json",
                     fetcher=_fetcher())
    memo = team.run(SYMBOL, "us", AS_OF, exchange="Nasdaq")
    assert memo["direction"] == "up"
    assert memo["call_id"] is None


# --- look-ahead ---------------------------------------------------------------

def test_future_bar_does_not_change_memo(tmp_path):
    _t1, _p1, memo_plain = _run_pass(tmp_path / "a")
    future = _bars() + [("2026-11-14", 500.0), ("2026-11-20", 900.0)]
    _t2, _p2, memo_future = _run_pass(
        tmp_path / "b", fetcher=_fetcher(pairs=future))
    assert memo_future == memo_plain


def test_last_bar_mutation_changes_memo(tmp_path):
    _t1, _p1, memo_plain = _run_pass(tmp_path / "a")
    moved = _bars()
    moved[-1] = (moved[-1][0], 130.0)
    _t2, _p2, memo_moved = _run_pass(
        tmp_path / "b", fetcher=_fetcher(pairs=moved))
    assert (memo_moved["prompt_hashes"]["debate_bull"]
            != memo_plain["prompt_hashes"]["debate_bull"])
    assert memo_moved["thesis"] == memo_plain["thesis"]  # same fake text
    state = json.loads((tmp_path / "b" / "checkpoint.json").read_text(
        encoding="utf-8"))
    assert state["steps"]["analysts"]["technical"]["last_close"] == 130.0


# --- checkpoint + resume -------------------------------------------------------

def test_resume_after_interruption_no_redo_no_double_log(tmp_path):
    log = tmp_path / "calls.jsonl"
    cp = tmp_path / "checkpoint.json"
    boom = FakeProvider(script=[BULL, Exception("boom")],
                        max_attempts=1)
    team1 = AgentTeam(boom, decision_log_path=log,
                      reflections_path=tmp_path / "reflections.jsonl",
                      checkpoint_path=cp, fetcher=_fetcher())
    with pytest.raises(Exception, match="boom"):
        team1.run(SYMBOL, "us", AS_OF, exchange="Nasdaq")
    state = json.loads(cp.read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["failed_step"] == "debate"
    assert state["completed"] == ["gather", "analysts"]
    assert state["steps"]["debate"]["bull"]["side"] == "bull"
    assert load_calls(log) == []

    provider2 = FakeProvider(script=[BEAR, TRADER, PM])
    team2 = AgentTeam(provider2, decision_log_path=log,
                      reflections_path=tmp_path / "reflections.jsonl",
                      checkpoint_path=cp, fetcher=_fetcher())
    memo = team2.run(SYMBOL, "us", AS_OF, exchange="Nasdaq")
    assert memo["direction"] == "up"
    assert provider2.calls == 3  # bear, trader, pm — bull NOT redone
    assert len(load_calls(log)) == 1
    state = json.loads(cp.read_text(encoding="utf-8"))
    assert state["status"] == "complete"


def test_rerun_completed_run_makes_no_provider_calls(tmp_path):
    _t, p1, memo1 = _run_pass(tmp_path)
    assert p1.calls == 4
    provider2 = FakeProvider(script=list(FULL_SCRIPT))
    team2 = _team(tmp_path, provider2)
    memo2 = team2.run(SYMBOL, "us", AS_OF, exchange="Nasdaq")
    assert memo2 == memo1
    assert provider2.calls == 0
    assert len(load_calls(tmp_path / "calls.jsonl")) == 1


# --- schema failure / cost cap --------------------------------------------------

def test_schema_failure_stops_run_cleanly(tmp_path):
    provider = FakeProvider(script=["not json", "also not json", "nope"],
                            max_attempts=3)
    team = _team(tmp_path, provider)
    with pytest.raises(LLMSchemaError):
        team.run(SYMBOL, "us", AS_OF, exchange="Nasdaq")
    assert load_calls(tmp_path / "calls.jsonl") == []
    state = json.loads((tmp_path / "checkpoint.json").read_text(
        encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["failed_step"] == "debate"


def test_cost_cap_exceeded_stops_run(tmp_path):
    provider = FakeProvider(script=list(FULL_SCRIPT),
                            tokens_per_call=(10, 20))
    team = _team(tmp_path, provider, max_tokens_per_run=65)
    with pytest.raises(LLMCapError):
        team.run(SYMBOL, "us", AS_OF, exchange="Nasdaq")
    assert provider.calls == 3  # bull, bear, trader — pm never called
    assert load_calls(tmp_path / "calls.jsonl") == []
    state = json.loads((tmp_path / "checkpoint.json").read_text(
        encoding="utf-8"))
    assert state["failed_step"] == "trader"


def test_range_call_without_bounds_is_schema_failure(tmp_path):
    trader_range = json.dumps({"expected": "range", "confidence": 0.5,
                               "horizon_days": 5,
                               "rationale": "stuck in a range"})
    provider = FakeProvider(script=[BULL, BEAR, trader_range])
    team = _team(tmp_path, provider)
    with pytest.raises(LLMSchemaError, match="expected_low"):
        team.run(SYMBOL, "us", AS_OF, exchange="Nasdaq")
    assert load_calls(tmp_path / "calls.jsonl") == []


# --- risk gate -------------------------------------------------------------------

def test_gate_block_without_volume_logs_nothing(tmp_path):
    _t, provider, memo = _run_pass(tmp_path, fetcher=_fetcher(volumes=False))
    assert memo["direction"] == "no_call"
    assert memo["gate_verdict"] == "BLOCK"
    assert "exit_liquidity" in memo["gate_failed_checks"]
    assert memo["call_id"] is None
    assert provider.calls == 3  # bull, bear, trader — pm skipped
    assert load_calls(tmp_path / "calls.jsonl") == []


def test_flat_series_plan_refused_is_block(tmp_path):
    flat = [(d, 100.0) for d, _c in _bars()]
    _t, _p, memo = _run_pass(tmp_path, fetcher=_fetcher(pairs=flat))
    assert memo["direction"] == "no_call"
    assert memo["gate_verdict"] == "BLOCK"
    assert "plan_refused" in memo["gate_failed_checks"]


def test_build_plan_shape():
    closes = [c for _d, c in _bars()]
    plan, context = build_plan(closes[-1], closes,
                               volumes=[1_000_000] * len(closes),
                               symbol="AAPL", as_of="2026-11-13")
    assert plan["stop"] < plan["entry"] < plan["target"]
    assert plan["reward_risk"] >= 2.0
    assert plan["shares"] > 0
    assert plan["entry_window_ok"] is True
    assert context["typical_daily_volume"] == 1_000_000
    with pytest.raises(ValueError, match="flat series"):
        build_plan(100.0, [100.0] * 30)


# --- series separation ------------------------------------------------------------

def _settled_mixed_log(path):
    pre1 = write_call(path, {"date": "2026-11-06", "symbol": "SPY",
                             "exchange": "NYSE Arca", "thesis": "rule",
                             "horizon_days": 5, "expected": "up",
                             "sources": ["yahoo"], "confidence": 0.5,
                             "entry_close": 100.0})
    pre2 = write_call(path, {"date": "2026-11-06", "symbol": "AAPL",
                             "exchange": "Nasdaq", "thesis": "rule",
                             "horizon_days": 5, "expected": "down",
                             "sources": ["yahoo"], "confidence": 0.5,
                             "entry_close": 200.0})
    llm = write_llm_call(path, {"date": "2026-11-06", "symbol": "VT",
                                "exchange": "NYSE Arca",
                                "thesis": "team", "horizon_days": 5,
                                "expected": "up", "sources": ["test"],
                                "confidence": 0.6, "entry_close": 50.0})
    for call_id in (pre1["call_id"], pre2["call_id"]):
        settle_call(path, call_id, 101.0, 100.0, 0.0, "test:index",
                    "2026-11-12")
    settle_llm_call(path, llm["call_id"], 51.0, 50.0, 0.0,
                    "test:index", "2026-11-12", prev_5d_return=0.01)
    return load_calls(path)


def test_series_separation(tmp_path):
    calls = _settled_mixed_log(tmp_path / "calls.jsonl")
    assert len(calls_by_series(calls, SERIES_LLM_TEAM)) == 1
    assert len(calls_by_series(calls, SERIES_PRE_REGISTERED)) == 2
    llm_score = score_series(calls, SERIES_LLM_TEAM)
    assert llm_score["pooled"]["model"]["n"] == 1
    pre_score = score_series(calls, SERIES_PRE_REGISTERED)
    assert pre_score["pooled"]["model"]["n"] == 2
    llm_text = render_series_md(calls, SERIES_LLM_TEAM)
    assert "VT" in llm_text and "SPY" not in llm_text
    assert "withheld until 100 settled calls" in llm_text
    pre_text = render_series_md(calls, SERIES_PRE_REGISTERED)
    assert "SPY" in pre_text and "VT" not in pre_text
    with pytest.raises(ValueError, match="unknown series"):
        calls_by_series(calls, "nope")


def test_write_llm_call_refuses_other_series(tmp_path):
    path = tmp_path / "calls.jsonl"
    base = {"date": "2026-11-06", "symbol": "VT",
            "exchange": "NYSE Arca", "thesis": "team", "horizon_days": 5,
            "expected": "up", "sources": ["test"], "confidence": 0.6}
    with pytest.raises(ValueError, match="llm-team"):
        write_llm_call(path, dict(base, series="pre-registered"))
    record = write_llm_call(path, base)
    assert record["series"] == SERIES_LLM_TEAM


def test_settle_llm_call_baselines(tmp_path):
    path = tmp_path / "calls.jsonl"
    record = write_llm_call(path, {"date": "2026-11-06", "symbol": "VT",
                                   "exchange": "NYSE Arca",
                                   "thesis": "team", "horizon_days": 5,
                                   "expected": "up", "sources": ["test"],
                                   "confidence": 0.6, "entry_close": 50.0,
                                   "prev_5d_return": 0.02})
    settled = settle_llm_call(path, record["call_id"], 51.0, 50.0, 0.0,
                              "test:index", "2026-11-12",
                              prev_5d_return=0.02)
    assert settled["status"] == "settled"
    assert settled["naive_expected"] == "up"
    assert settled["naive_hit"] is True
    assert settled["bnh_hit"] is True


# --- reflections -------------------------------------------------------------------

class _RecordingProvider(FakeProvider):
    """Fake provider that records every prompt it receives."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prompts = []

    def _complete_once(self, messages, last_errors):
        self.prompts.append("\n".join(m["content"] for m in messages))
        return super()._complete_once(messages, last_errors)


def test_reflections_feed_next_run(tmp_path):
    log = tmp_path / "calls.jsonl"
    refl = tmp_path / "reflections.jsonl"
    _t, _p, memo = _run_pass(tmp_path)
    call_id = memo["call_id"]
    from toolkit.agent_team import reflect
    # Entry close is the last completed close before the decision date.
    settle_llm_call(log, call_id, 115.0, 114.5, 0.0, "test:index",
                    "2026-11-18", prev_5d_return=0.01)
    reflect(refl, call_id,
            "Momentum faded after earnings; the bull case was over-weighted.")
    provider2 = _RecordingProvider(script=list(FULL_SCRIPT))
    team2 = AgentTeam(provider2, decision_log_path=log,
                      reflections_path=refl,
                      checkpoint_path=tmp_path / "cp2.json",
                      fetcher=_fetcher())
    memo2 = team2.run(SYMBOL, "us", "2026-11-20", exchange="Nasdaq")
    assert memo2["direction"] == "up"
    pm_prompt = provider2.prompts[-1]
    assert "Momentum faded after earnings" in pm_prompt


def test_excluded_tool_data_never_reaches_analysts(tmp_path):
    fund = {"source": "SEC EDGAR companyfacts",
            "values": {"Revenues": {"value": 999999.0, "end": "2025-12-31",
                                    "filed": "2026-12-01",
                                    "form": "10-K"}}}
    provider = FakeProvider(script=list(FULL_SCRIPT))
    team = _team(tmp_path, provider,
                 fetcher=_fetcher(fundamentals=fund))
    team.run(SYMBOL, "us", AS_OF, exchange="Nasdaq")
    state = json.loads((tmp_path / "checkpoint.json").read_text(
        encoding="utf-8"))
    ledger = {e["tool"]: e
              for e in state["steps"]["gather"]["tool_ledger"]}
    assert ledger["fundamentals"]["status"] == "excluded"
    metrics = state["steps"]["analysts"]["us_fundamentals"]["metrics"]
    assert all(m["value"] == "UNKNOWN" for m in metrics)


# --- validation ---------------------------------------------------------------------

def test_unknown_market_and_missing_bars_raise(tmp_path):
    provider = FakeProvider(script=list(FULL_SCRIPT))
    team = _team(tmp_path, provider)
    with pytest.raises(ValueError, match="unknown market"):
        team.run(SYMBOL, "nope", AS_OF)
    empty = _fetcher(pairs=[])
    team2 = _team(tmp_path, provider, fetcher=empty)
    with pytest.raises(ValueError, match="no price bars"):
        team2.run(SYMBOL, "us", AS_OF)
    one_bar = _fetcher(pairs=_bars()[:1])
    team3 = _team(tmp_path, provider, fetcher=one_bar)
    with pytest.raises(ValueError, match="at least 2 completed bars"):
        team3.run(SYMBOL, "us", AS_OF)
