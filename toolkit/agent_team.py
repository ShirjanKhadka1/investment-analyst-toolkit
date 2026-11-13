"""Executable agent team (PR B) — analysts -> bull/bear debate -> trader
-> risk team -> portfolio manager. Research only, paper only.

The team orchestrates the EXISTING personas (``toolkit/rules_mode.py``:
the NEPSE team and the five market-agnostic global personas) and the
EXISTING risk gate (``toolkit/position_plan.py``), with the LLM steps
running through PR A's provider layer (``toolkit/llm_providers.py``) —
never an ad-hoc client. The output is a research memo carrying the
risk gate's PASS or BLOCK. It is never an order and never an execution
instruction; nothing in this module writes an order, places a trade,
or talks to a broker.

Rules that make this honest (all enforced in code, all tested):

- **Point-in-time, by construction.** Every tool result carries
  ``as_of`` (the date the data describes) and ``published_at`` (when
  it became available). Price bars are loaded into the point-in-time
  store with ``published_at = bar date + 1 day``, so the team only
  ever sees bars dated strictly before the decision date — a bar
  dated on the decision date is never used, because it may still be
  in progress (the run #3 audit showed an in-progress bar reaching a
  call). A tool result whose ``published_at`` is after the decision
  date is recorded in the tool ledger as ``excluded`` with the reason,
  never silently dropped and never used.
- **LLM outputs are not validated predictions.** The memo says so in
  plain language, and no performance claim is made until the
  ``llm-team`` series has >= 100 settled calls (the same headline
  rule as the pre-registered series).
- **Series separation.** LLM-team calls are written with
  ``series="llm-team"`` and only ever to the llm-team log. The
  pre-registered rules' series (``sma50_trend`` / ``mom20_sign``) is
  calls with no series label (or ``series="pre-registered"``); the
  two are scored and rendered separately, never pooled. A team-written
  call can never land in the pre-registered series: ``write_llm_call``
  refuses any other series value.
- **Log before outcome.** The directional call is written to the
  decision log (``toolkit/decision_log.py``) with inputs, prompt
  hashes and the gate verdict BEFORE any outcome is known, and only
  when the risk gate PASSES. A BLOCKed run writes no call.
- **Checkpoint + resume.** The run state is saved to a JSON
  checkpoint after every step (and after every LLM call inside the
  debate step). An interrupted run resumes from its checkpoint
  without redoing completed LLM calls or double-logging a call.
  A completed run re-run with the same run id returns the stored
  memo and makes no new provider calls.
- **Reflections feed the next run.** After a call settles, a
  reflection is appended (``toolkit/decision_log.py``); the next run
  on the same symbol loads the latest reflections into the portfolio
  manager's prompt.
- **Hard caps.** A run-level token + cost cap is checked after every
  LLM call; the first call that takes the cumulative total over the
  cap stops the run with ``LLMCapError`` — no later step runs, no
  call is logged, no memo is produced.
- **Tools are the repo's connectors only** (Yahoo, EDGAR, CoinGecko,
  ECB, World Bank, CSV via ``toolkit/connectors.py``). CI and all
  tests use only the fake provider: no real keys, no network.
- **Paper only.** The hypothetical plan the risk gate checks is a
  paper plan (``toolkit/position_plan.py``); a PASS is not a yes.

Original code for this toolkit. Research only, not investment advice.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

from . import connectors, decision_log, edgar, rules_mode
from .decision_log import load_calls, load_reflections
from .llm_providers import LLMCapError, LLMSchemaError
from .pit_store import PointInTimeStore
from .position_plan import (
    DEFAULT_RESEARCH_CONFIG,
    extended_risk_gate,
    plan_exit,
    size_position,
)
from .track_record import render_track_record_md, score_settled

__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "DEBATE_SCHEMA",
    "PM_SCHEMA",
    "SERIES_LLM_TEAM",
    "SERIES_PRE_REGISTERED",
    "STEPS",
    "TRADER_SCHEMA",
    "AgentTeam",
    "build_plan",
    "calls_by_series",
    "load_reflections_for_symbol",
    "reflect",
    "render_memo_md",
    "render_series_md",
    "score_series",
    "settle_llm_call",
    "write_llm_call",
]

SERIES_LLM_TEAM = "llm-team"
SERIES_PRE_REGISTERED = "pre-registered"
CHECKPOINT_SCHEMA_VERSION = "1.0.0"
HEADLINE_MIN_N = 100

STEPS = ("gather", "analysts", "debate", "trader", "risk",
         "portfolio_manager", "log_call", "memo")

DEBATE_SYSTEM = (
    "You are one side of a research debate about a single security. "
    "Argue ONLY from the analyst reads and tool data supplied in the "
    "prompt. Every claim must trace to a supplied number or a stated "
    "UNKNOWN — never invent a figure, a filing, or a price. Reply with "
    "valid JSON only."
)
TRADER_SYSTEM = (
    "You are the trader in a research team. Propose a direction for the "
    "next 5 trading days from the debate and the analyst reads only. "
    "You do not execute anything; your proposal is paper research. "
    "Reply with valid JSON only."
)
PM_SYSTEM = (
    "You are the portfolio manager writing a research memo. Write the "
    "thesis, the key risks, and what would change the view, from the "
    "trader's proposal, the debate, and the reflections supplied. "
    "Do not change the direction and do not invent figures. "
    "Reply with valid JSON only."
)

DEBATE_SCHEMA = {
    "type": "object",
    "required": ["side", "claims", "key_risks"],
    "properties": {
        "side": {"type": "string", "enum": ["bull", "bear"]},
        "claims": {"type": "array", "items": {"type": "string"},
                   "minItems": 1, "maxItems": 5},
        "key_risks": {"type": "array", "items": {"type": "string"},
                      "maxItems": 5},
    },
}

TRADER_SCHEMA = {
    "type": "object",
    "required": ["expected", "confidence", "horizon_days", "rationale"],
    "properties": {
        "expected": {"type": "string", "enum": ["up", "down", "range"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "horizon_days": {"type": "integer", "minimum": 1, "maximum": 30},
        "rationale": {"type": "string", "minLength": 1},
        "expected_low": {"type": "number"},
        "expected_high": {"type": "number"},
    },
}

PM_SCHEMA = {
    "type": "object",
    "required": ["thesis", "key_risks", "what_would_change_my_mind"],
    "properties": {
        "thesis": {"type": "string", "minLength": 1},
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "what_would_change_my_mind": {"type": "string", "minLength": 1},
    },
}

_DISCLAIMER = (
    "LLM outputs are not validated predictions. This memo is research "
    "only, not investment advice. No performance claim is made for the "
    "llm-team series until it has >= 100 settled calls."
)


def _prompt_hash(system, prompt):
    """sha256 of the exact system + user text sent to the provider."""
    payload = (system or "") + "\n" + prompt
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_day(value, field):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(
            f"{field} must be a YYYY-MM-DD date, got {value!r}") from None


def _atomic_write_json(path, record):
    """Write JSON atomically (temp file + rename) so a crash mid-write
    never leaves a half-written checkpoint."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name,
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, indent=2))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _jsonable(value):
    """Deep-convert a fetched payload to JSON-safe data (tuples ->
    lists, anything else -> str) so it can live in the checkpoint.
    The checkpoint is the run's input record: the analysts and risk
    steps read the fetched payload from it, never by re-fetching."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# --- series separation ------------------------------------------------------

def calls_by_series(calls, series):
    """Calls belonging to one series. A call with no ``series`` field
    is a pre-registered (rule) call — the daily script never labels
    its calls, so the absence of the label IS the pre-registered
    series, by the same legacy-default rule ``decision_log`` uses for
    the exchange field. ``llm-team`` calls always carry the label."""
    if series == SERIES_LLM_TEAM:
        return [c for c in calls if c.get("series") == SERIES_LLM_TEAM]
    if series == SERIES_PRE_REGISTERED:
        return [c for c in calls
                if c.get("series") in (None, SERIES_PRE_REGISTERED)]
    raise ValueError(f"unknown series {series!r} — use "
                     f"{SERIES_LLM_TEAM!r} or {SERIES_PRE_REGISTERED!r}")


def score_series(calls, series):
    """Score settled calls of one series only, against the same
    baselines (naive + buy-and-hold) as the pre-registered series.
    The two series are never pooled."""
    return score_settled(calls_by_series(calls, series))


def render_series_md(calls, series):
    """Render the track-record page for one series only. The pooled
    headline hit rate appears only at >= 100 settled calls in that
    series — the same rule as the pre-registered series."""
    mine = calls_by_series(calls, series)
    text = render_track_record_md(mine, headline_min_n=HEADLINE_MIN_N)
    if series == SERIES_LLM_TEAM:
        text = text.replace(
            "# Track record — public calls, scored after the fact",
            "# Track record — llm-team series, scored after the fact",
            1)
        text += ("\nLLM-team calls are produced by the executable agent "
                 "team (`toolkit/agent_team.py`) and scored separately "
                 "from the pre-registered rule calls. LLM outputs are "
                 "not validated predictions.\n")
    return text


def write_llm_call(path, call):
    """Write one llm-team call to the decision log. The series is
    forced to ``llm-team`` — any other value (or an attempt to pass
    one in) is refused, so a team-written call can never land in the
    pre-registered series. All other validation is ``write_call``'s.
    Returns the written record."""
    if not isinstance(call, dict):
        raise ValueError("call must be a dict")  # noqa: TRY004
    if call.get("series") not in (None, SERIES_LLM_TEAM):
        raise ValueError(
            f"llm-team calls carry series={SERIES_LLM_TEAM!r}, "
            f"not {call.get('series')!r}")
    record = dict(call)
    record["series"] = SERIES_LLM_TEAM
    return decision_log.write_call(path, record)


def settle_llm_call(path, call_id, realised_close, entry_close,
                    index_return, index_source, as_of, prev_5d_return=None):
    """Settle one llm-team call after its horizon, then score it
    against the same baselines as the pre-registered series: the naive
    baseline predicts the previous 5-trading-day direction continues
    (sign of ``prev_5d_return``; a zero previous return predicts UP),
    and buy-and-hold always predicts UP. Mirrors the daily script's
    settlement exactly. Returns the settled call."""
    settled = decision_log.settle_call(
        path, call_id, realised_close, entry_close, index_return,
        index_source, as_of)
    if prev_5d_return is None:
        naive_expected, naive_hit = None, None
    else:
        naive_expected = "up" if prev_5d_return >= 0 else "down"
        realised = settled["realised_return"]
        naive_hit = (realised > 0) if naive_expected == "up" \
            else (realised < 0)
    bnh_hit = settled["realised_return"] > 0
    return decision_log.update_call(
        path, call_id, naive_expected=naive_expected,
        naive_hit=naive_hit, bnh_hit=bnh_hit)


def reflect(path, call_id, text, author="portfolio-manager"):
    """Append one reflection after a call settles. The next run on the
    same symbol loads it into the portfolio manager's prompt."""
    return decision_log.append_reflection(path, call_id, text,
                                          author=author)


def load_reflections_for_symbol(reflections_path, calls_path, symbol,
                                limit=3):
    """The latest ``limit`` reflections for one symbol, oldest first.
    Reflections name a call_id, not a symbol, so the symbol is resolved
    through the decision log. Missing files -> empty list."""
    if reflections_path is None or calls_path is None:
        return []
    by_id = {c.get("call_id"): c for c in load_calls(calls_path)}
    mine = [r for r in load_reflections(reflections_path)
            if by_id.get(r.get("call_id"), {}).get("symbol") == symbol]
    return mine[-limit:] if limit else mine


# --- hypothetical paper plan for the risk gate ------------------------------

def _mean_abs_move(closes, window=20):
    """Mean absolute daily move (fraction) over the last ``window``
    closes. None when fewer than 2 closes are visible."""
    if len(closes) < 2:
        return None
    moves = [abs(closes[i] / closes[i - 1] - 1.0)
             for i in range(max(1, len(closes) - window), len(closes))
             if closes[i - 1] != 0]
    return sum(moves) / len(moves) if moves else None


def build_plan(entry_close, closes, volumes=None, config=None,
               budget=10000.0, symbol="", asset_type="equity",
               as_of=None):
    """Build the hypothetical paper plan the risk team checks.

    Stop = entry minus 2x the recent mean absolute daily move,
    target = entry plus 2x (reward:risk 2.0, the repo default); shares
    sized by ``size_position`` under the research config. Returns
    ``(plan, context)`` where context carries what the extended gate
    needs (portfolio value, sector exposure, typical daily volume).
    ``entry_window_ok`` is True by construction: the entry is always
    the last COMPLETED close before the decision date, never an
    in-progress bar — that is what the entry-window check means here.
    ``asset_type`` is the caller's responsibility: the gate's scope is
    equity/ETF only, so a crypto or FX plan BLOCKs on ``asset_type``
    and says so (that is the gate working, not a bug).
    Refusals from ``plan_exit`` / ``size_position`` (flat series, zero
    shares, risk below the 0.05% floor) propagate as ValueError — the
    caller turns a refusal into a BLOCK, never into a weaker plan.
    """
    closes = [float(c) for c in closes]
    if not closes:
        raise ValueError("build_plan needs at least one close")
    entry = float(entry_close)
    if entry <= 0:
        raise ValueError(f"entry_close must be positive, got {entry!r}")
    move = _mean_abs_move(closes)
    if move is None or move <= 0:
        raise ValueError(
            "cannot size a plan: no measurable daily move in the "
            "visible closes (flat series) — refusing rather than "
            "inventing a stop")
    risk_per_share = 2.0 * move * entry
    # No explicit target: plan_exit computes it from the rounded stop
    # at reward:risk 2.0, so the rounded ratio can never dip below
    # the gate's 2.0 floor (a supplied target could).
    exit_plan = plan_exit(entry, entry - risk_per_share)
    cfg = dict(DEFAULT_RESEARCH_CONFIG)
    if config:
        cfg.update(config)
    sizing = size_position(
        budget, cfg["max_loss_per_trade_usd"], cfg["max_position_pct"],
        cfg["max_order_usd"], entry, exit_plan["risk_per_share"],
        cash_available=budget, min_order_usd=cfg["min_order_usd"])
    plan = {"entry": exit_plan["entry"], "stop": exit_plan["stop"],
            "target": exit_plan["target"],
            "risk_per_share": exit_plan["risk_per_share"],
            "reward_per_share": exit_plan["reward_per_share"],
            "reward_risk": exit_plan["reward_risk"],
            "shares": sizing["shares"],
            "position_cost": sizing["position_cost"],
            "max_loss": sizing["max_loss"],
            "limiting_factor": sizing["limiting_factor"],
            "cash_available": float(budget),
            "symbol": symbol, "asset_type": asset_type, "short": False,
            "blocked": False, "paper": True,
            "entry_window_ok": True, "is_duplicate": False,
            "daily_loss_usd": 0.0, "trades_today": 0, "orders_today": 0}
    if as_of is not None:
        plan["date"] = str(as_of)
    typical_volume = None
    if volumes:
        vols = sorted(float(v) for v in volumes if v is not None)
        if vols:
            typical_volume = vols[len(vols) // 2]
    context = {"portfolio_value": float(budget),
               "sector_exposure_usd": 0.0, "sector": "general",
               "typical_daily_volume": typical_volume}
    return plan, context

# --- tool layer: the repo's connectors only -----------------------------------

def _default_fetch(symbol, market, as_of, inputs):
    """Fetch tool data through the repo's connectors. Returns a dict:

    - ``prices``: ``{"source", "pairs": [(date, close), ...],
      "volumes": {date: volume} or None}`` — always attempted; a
      failure here is fatal (no bars, no run).
    - ``fundamentals``: EDGAR values (US market only) — ``{"source",
      "values": {tag: {"value", "end", "filed", ...}}}``; a failure is
      recorded, not fatal (fundamentals read UNKNOWN).
    - ``fx``: ECB fixing (FX market only).
    - ``macro``: World Bank values, only when ``inputs`` names a
      country + indicator.

    No other source is ever consulted. ``inputs`` may carry
    ``csv_path`` (a caller-supplied CSV goes first in the price chain)
    and ``asset_type`` (``equity``/``etf`` — the risk gate's scope).
    """
    kind = "crypto" if market == "crypto" else "stock"
    prices = connectors.fetch_prices(
        symbol, kind=kind, csv_path=inputs.get("csv_path"))
    out = {"prices": {"source": prices.get("source", "unknown"),
                      "pairs": list(prices.get("pairs") or []),
                      "volumes": None},
           "fundamentals": None, "fx": None, "macro": None}
    if market == "us":
        try:
            fund = edgar.get_fundamentals(
                symbol, [("us-gaap", "Revenues"),
                         ("us-gaap", "NetIncomeLoss")])
            out["fundamentals"] = {"source": fund["source"],
                                   "values": fund["values"]}
        except Exception as exc:  # noqa: BLE001 — one tool failing
            # must not kill the run; the analysts read UNKNOWN.
            out["fundamentals"] = {"source": "SEC EDGAR companyfacts",
                                   "status": "failed", "error": str(exc)}
    if market == "fx":
        try:
            fx = connectors.fetch_fx_rates()
            out["fx"] = {"source": fx["source"], "date": fx["date"],
                         "rates": fx["rates"]}
        except Exception as exc:  # noqa: BLE001 — same rule as above.
            out["fx"] = {"source": "ECB euro reference rates",
                         "status": "failed", "error": str(exc)}
    country = inputs.get("macro_country")
    indicator = inputs.get("macro_indicator")
    if country and indicator:
        try:
            macro = connectors.fetch_macro(country, indicator)
            out["macro"] = {"source": macro["source"],
                            "country": country, "indicator": indicator,
                            "values": macro["values"]}
        except Exception as exc:  # noqa: BLE001 — same rule.
            out["macro"] = {"source": "World Bank API v2",
                            "status": "failed", "error": str(exc)}
    return out


# --- the team -----------------------------------------------------------------

class AgentTeam:
    """Orchestrate the analyst personas, debate, trader, risk team
    and portfolio manager into one checkpointed run.

    ``provider`` is a ``llm_providers.BaseProvider`` (the fake
    provider in tests/CI; a real provider via
    ``llm_providers.provider_from_env`` in production). ``store`` is
    the point-in-time store bars are read through — the team never
    reads raw bars. ``decision_log_path`` / ``reflections_path`` /
    ``checkpoint_path`` are file paths (all optional except the log
    path, which a run that logs a call needs). ``fetcher`` is an
    injectable ``(symbol, market, as_of, inputs) -> tool data``; the
    default uses the repo's connectors. ``max_tokens_per_run`` /
    ``max_cost_usd_per_run`` are the run-level hard caps, checked
    after every LLM call.
    """

    def __init__(self, provider, *, store=None, decision_log_path=None,
                 reflections_path=None, checkpoint_path=None,
                 fetcher=None, max_tokens_per_run=4000,
                 max_cost_usd_per_run=0.50, risk_config=None,
                 budget=10000.0):
        if max_tokens_per_run <= 0:
            raise ValueError("max_tokens_per_run must be positive")
        if max_cost_usd_per_run < 0:
            raise ValueError("max_cost_usd_per_run must not be negative")
        self.provider = provider
        self.store = store if store is not None else PointInTimeStore()
        self.decision_log_path = (Path(decision_log_path)
                                  if decision_log_path else None)
        self.reflections_path = (Path(reflections_path)
                                 if reflections_path else None)
        self.checkpoint_path = (Path(checkpoint_path)
                                if checkpoint_path else None)
        self.fetcher = fetcher if fetcher is not None else _default_fetch
        self.max_tokens_per_run = max_tokens_per_run
        self.max_cost_usd_per_run = max_cost_usd_per_run
        self.risk_config = risk_config
        self.budget = float(budget)
        self._state = None

    # -- checkpoint ----------------------------------------------------------

    def _new_state(self, run_id, symbol, market, exchange, as_of,
                   horizon_days):
        return {"schema_version": CHECKPOINT_SCHEMA_VERSION,
                "run_id": run_id, "symbol": symbol, "market": market,
                "exchange": exchange, "as_of": as_of,
                "horizon_days": horizon_days, "completed": [],
                "steps": {}, "prompt_hashes": {}, "tokens_used": 0,
                "cost_usd_estimate": 0.0, "status": "in_progress",
                "failed_step": None, "error": None, "memo": None}

    def _save_checkpoint(self):
        if self.checkpoint_path is not None and self._state is not None:
            _atomic_write_json(self.checkpoint_path, self._state)

    def _load_checkpoint(self, run_id, symbol, market, as_of):
        if self.checkpoint_path is None \
                or not self.checkpoint_path.exists():
            return None
        try:
            state = json.loads(
                self.checkpoint_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        if not isinstance(state, dict):
            return None
        if (state.get("run_id") != run_id
                or state.get("symbol") != symbol
                or state.get("market") != market
                or state.get("as_of") != as_of):
            return None
        if state.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            return None
        return state

    def _restore_store(self):
        """Reload the checkpointed bars into the point-in-time store.
        The store is in-memory, so a resume in a fresh process starts
        empty; the checkpoint's fetched payload is the record of what
        was gathered, and reloading it is idempotent (the store keeps
        one record per as_of date). Without this, a resumed run would
        see no bars and the risk step would refuse the plan."""
        gathered = (self._state or {}).get("steps", {}).get("gather") or {}
        fetched = gathered.get("fetched") or {}
        pairs = (fetched.get("prices") or {}).get("pairs") or []
        volumes = (fetched.get("prices") or {}).get("volumes") or {}
        for date_str, close in pairs:
            bar_date = _parse_day(date_str, "bar date")
            volume = volumes.get(date_str) if isinstance(
                volumes, dict) else None
            self.store.add_bar(bar_date, bar_date + timedelta(days=1),
                               close, close, close, close,
                               volume=volume)

    # -- step machinery -------------------------------------------------------

    def _step(self, name, fn):
        """Run one step, or return its stored output on resume. The
        checkpoint is saved after every completed step; on failure it
        is saved with the failed step named, then the error re-raises.
        A step that already completed is never re-run."""
        if name in self._state["completed"]:
            return self._state["steps"][name]
        try:
            result = fn()
        except BaseException as exc:
            self._state["status"] = "failed"
            self._state["failed_step"] = name
            self._state["error"] = str(exc)
            self._save_checkpoint()
            raise
        self._state["steps"][name] = result
        self._state["completed"].append(name)
        self._save_checkpoint()
        return result

    def _llm(self, step, system, prompt, schema):
        """One schema-validated LLM call through the provider layer.
        Records the prompt hash and the run-level token/cost totals;
        the first call that takes the cumulative total over the run
        cap stops the run with LLMCapError."""
        digest = _prompt_hash(system, prompt)
        self._state["prompt_hashes"][step] = digest
        result = self.provider.complete(prompt, schema=schema,
                                        system=system)
        self._state["tokens_used"] += result.tokens_used
        self._state["cost_usd_estimate"] += result.cost_usd_estimate
        if self._state["tokens_used"] > self.max_tokens_per_run:
            raise LLMCapError(
                f"team token cap exceeded: "
                f"{self._state['tokens_used']} estimated tokens > "
                f"{self.max_tokens_per_run} allowed per run")
        if self._state["cost_usd_estimate"] > self.max_cost_usd_per_run:
            raise LLMCapError(
                f"team cost cap exceeded: estimated "
                f"${self._state['cost_usd_estimate']:.4f} > "
                f"${self.max_cost_usd_per_run:.2f} allowed per run")
        return result.parsed

    # -- the run ---------------------------------------------------------------

    def run(self, symbol, market, as_of, *, run_id=None, exchange=None,
            horizon_days=5, inputs=None):
        """Run the full team for one symbol on one decision date.

        ``as_of`` is the decision date (YYYY-MM-DD): only data
        published on or before it is used, and only price bars dated
        strictly before it. ``run_id`` defaults to
        ``f"{symbol}-{as_of}"`` so a re-run resumes the same
        checkpoint. Returns the research memo dict. A completed run
        re-run with the same run id returns the stored memo and makes
        no new provider calls.
        """
        from .market_profiles import get_profile

        if not symbol or not market:
            raise ValueError("run needs a symbol and a market")
        as_of_d = _parse_day(as_of, "as_of")
        as_of = as_of_d.isoformat()
        get_profile(market)  # fail loudly on an unknown market key
        if isinstance(horizon_days, bool) or not isinstance(
                horizon_days, int) or horizon_days < 1:
            raise ValueError(
                f"horizon_days must be a positive integer, "
                f"got {horizon_days!r}")
        inputs = dict(inputs or {})
        exchange = exchange or market
        run_id = run_id or f"{symbol}-{as_of}"
        state = self._load_checkpoint(run_id, symbol, market, as_of)
        if state is not None and state.get("status") == "complete":
            self._state = state
            return state["memo"]
        self._state = state if state is not None else self._new_state(
            run_id, symbol, market, exchange, as_of, horizon_days)
        self._restore_store()
        self._save_checkpoint()
        ctx = {"symbol": symbol, "market": market,
               "exchange": exchange, "as_of": as_of,
               "horizon_days": horizon_days, "inputs": inputs}
        self._step("gather", lambda: self._gather(ctx))
        self._step("analysts", lambda: self._analysts(ctx))
        self._step("debate", lambda: self._debate(ctx))
        self._step("trader", lambda: self._trader(ctx))
        self._step("risk", lambda: self._risk(ctx))
        self._step("portfolio_manager",
                   lambda: self._portfolio_manager(ctx))
        self._step("log_call", lambda: self._log_call(ctx))
        memo = self._step("memo", lambda: self._memo(ctx))
        self._state["status"] = "complete"
        self._state["memo"] = memo
        self._save_checkpoint()
        return memo

    # -- step implementations ---------------------------------------------------

    def _gather(self, ctx):
        """Fetch tool data, stamp it, load bars into the PIT store.
        Only bars dated strictly before the decision date become
        visible — the store enforces it via published_at = bar date
        + 1 day."""
        as_of = _parse_day(ctx["as_of"], "as_of")
        fetched = self.fetcher(ctx["symbol"], ctx["market"],
                               ctx["as_of"], ctx["inputs"])
        if not isinstance(fetched, dict) \
                or not isinstance(fetched.get("prices"), dict):
            raise TypeError(
                "fetcher must return a dict with a 'prices' dict")
        pairs = fetched["prices"].get("pairs") or []
        if not pairs:
            raise ValueError(
                f"no price bars fetched for {ctx['symbol']} — "
                "cannot run the team on no data")
        volumes = fetched["prices"].get("volumes") or {}
        ledger = []
        for date_str, close in pairs:
            bar_date = _parse_day(date_str, "bar date")
            volume = volumes.get(date_str) if isinstance(
                volumes, dict) else None
            self.store.add_bar(bar_date, bar_date + timedelta(days=1),
                               close, close, close, close,
                               volume=volume)
        visible = self.store.bars_as_of(as_of)
        if len(visible) < 2:
            raise ValueError(
                f"only {len(visible)} completed bar(s) visible before "
                f"{ctx['as_of']} for {ctx['symbol']} — the team needs "
                "at least 2 completed bars")
        ledger.append({
            "tool": "prices",
            "source": fetched["prices"].get("source", "unknown"),
            "as_of": visible[-1]["date"],
            "published_at": (date.fromisoformat(visible[-1]["date"])
                             + timedelta(days=1)).isoformat(),
            "status": "ok", "n_bars": len(visible),
            "note": ("bars dated on the decision date are never used; "
                     "last used bar is the last completed close before "
                     "the decision date")})
        for tool in ("fundamentals", "fx", "macro"):
            entry = fetched.get(tool)
            if entry is None:
                ledger.append({"tool": tool, "source": "not requested",
                               "as_of": None, "published_at": None,
                               "status": "skipped"})
                continue
            if entry.get("status") == "failed":
                ledger.append({"tool": tool,
                               "source": entry.get("source", "unknown"),
                               "as_of": None, "published_at": None,
                               "status": "failed",
                               "error": entry.get("error", "unknown")})
                continue
            if tool == "fundamentals":
                values = entry.get("values") or {}
                filed = [v.get("filed") for v in values.values()
                         if isinstance(v, dict) and v.get("filed")]
                ends = [v.get("end") for v in values.values()
                        if isinstance(v, dict) and v.get("end")]
                ledger.append({
                    "tool": tool, "source": entry.get("source"),
                    "as_of": max(ends) if ends else None,
                    "published_at": max(filed) if filed else None,
                    "status": "ok"})
            elif tool == "fx":
                # A fixing is published on its own date.
                ledger.append({
                    "tool": tool, "source": entry.get("source"),
                    "as_of": entry.get("date"),
                    "published_at": entry.get("date"), "status": "ok"})
            else:
                years = [str(y) for y, _v in entry.get("values") or []]
                ledger.append({
                    "tool": tool, "source": entry.get("source"),
                    "as_of": max(years) if years else None,
                    # World Bank vintage is not stamped on the values,
                    # so the honest publication date is the retrieval
                    # date (today) — never the decision date.
                    "published_at": date.today().isoformat(),
                    "status": "ok"})
        usable = []
        for entry in ledger:
            if entry["status"] != "ok" or entry.get("published_at") is None:
                continue
            if _parse_day(entry["published_at"], "published_at") > as_of:
                entry["status"] = "excluded"
                entry["reason"] = ("published after the decision date "
                                   f"{ctx['as_of']} — not yet available")
            else:
                usable.append(entry["tool"])
        return {"tool_ledger": ledger,
                "n_bars_visible": len(visible),
                "last_close": visible[-1]["close"],
                "last_bar_date": visible[-1]["date"],
                "usable_tools": usable,
                "fetched": _jsonable(fetched)}

    def _analysts(self, ctx):
        """Run the existing deterministic personas over the gathered
        inputs. No LLM here: the analyst reads ground the debate in
        computed numbers."""
        as_of = ctx["as_of"]
        closes = [c for _d, c in self.store.closes_as_of(as_of)]
        gathered = self._state["steps"]["gather"]
        ledger = {e["tool"]: e for e in gathered["tool_ledger"]}
        fetched = gathered.get("fetched") or {}
        inputs = dict(ctx["inputs"])
        inputs["closes"] = closes
        # Only tools the ledger marks usable reach the personas.
        # Excluded or failed tool data never becomes an analyst input.
        if ledger.get("fundamentals", {}).get("status") == "ok":
            values = {}
            raw = (fetched.get("fundamentals") or {}).get("values") or {}
            for tag, row in raw.items():
                if isinstance(row, dict) and row.get("value") is not None:
                    values[tag] = row["value"]
            merged = dict(values)
            merged.update(inputs.get("fundamentals_metrics") or {})
            inputs["fundamentals_metrics"] = merged
        if ledger.get("fx", {}).get("status") == "ok":
            symbol = ctx["symbol"]
            base = symbol[:3] if len(symbol) >= 6 else "UNKNOWN"
            quote = symbol[3:6] if len(symbol) >= 6 else "UNKNOWN"
            inputs["fx_inputs"] = {
                "base_currency": base, "quote_currency": quote}
        sections = rules_mode.run_global_rules_team(
            ctx["symbol"], ctx["market"], inputs)
        if ctx["market"] == "nepse":
            sections["nepse_team"] = rules_mode.run_rules_team(
                ctx["symbol"], ctx["exchange"], inputs)
        return sections

    def _debate(self, ctx):
        """Bull then bear, each one schema-validated LLM call. The
        bull result is checkpointed before the bear call runs, so a
        resume never re-pays for the bull call."""
        partial = self._state["steps"].get("debate") or {}
        analysts = self._state["steps"]["analysts"]
        gathered = self._state["steps"]["gather"]
        brief = json.dumps(
            {"symbol": ctx["symbol"], "market": ctx["market"],
             "as_of": ctx["as_of"],
             "last_close": gathered["last_close"],
             "last_bar_date": gathered["last_bar_date"],
             "n_bars": gathered["n_bars_visible"],
             "analysts": analysts,
             "tools_used": gathered["usable_tools"]},
            sort_keys=True, default=str)
        out = dict(partial)
        if "bull" not in out:
            prompt = (f"Argue the BULL case for {ctx['symbol']}.\n\n"
                      f"Evidence (analyst reads + tool data):\n{brief}")
            out["bull"] = self._llm("debate_bull", DEBATE_SYSTEM, prompt,
                                    DEBATE_SCHEMA)
            if out["bull"].get("side") != "bull":
                raise LLMSchemaError(
                    "bull debate reply must have side='bull'")
            self._state["steps"]["debate"] = dict(out)
            self._save_checkpoint()
        if "bear" not in out:
            prompt = (f"Argue the BEAR case for {ctx['symbol']}.\n\n"
                      f"Evidence (analyst reads + tool data):\n{brief}")
            out["bear"] = self._llm("debate_bear", DEBATE_SYSTEM, prompt,
                                    DEBATE_SCHEMA)
            if out["bear"].get("side") != "bear":
                raise LLMSchemaError(
                    "bear debate reply must have side='bear'")
            self._state["steps"]["debate"] = dict(out)
            self._save_checkpoint()
        out["prompt_hashes"] = {
            "bull": self._state["prompt_hashes"].get("debate_bull"),
            "bear": self._state["prompt_hashes"].get("debate_bear")}
        return out

    def _trader(self, ctx):
        """One LLM call: direction + confidence + horizon from the
        debate and the analyst reads."""
        debate = self._state["steps"]["debate"]
        analysts = self._state["steps"]["analysts"]
        brief = json.dumps(
            {"symbol": ctx["symbol"], "market": ctx["market"],
             "as_of": ctx["as_of"], "debate": debate,
             "technical": analysts.get("technical")},
            sort_keys=True, default=str)
        prompt = (f"Propose the direction for {ctx['symbol']} over the "
                  f"next 5 trading days.\n\nDebate and analyst reads:\n"
                  f"{brief}")
        parsed = self._llm("trader", TRADER_SYSTEM, prompt, TRADER_SCHEMA)
        if parsed["expected"] == "range" and (
                parsed.get("expected_low") is None
                or parsed.get("expected_high") is None):
            raise LLMSchemaError(
                "trader range call needs expected_low and expected_high")
        if parsed["expected"] in ("up", "down") and (
                parsed.get("expected_low") is not None
                or parsed.get("expected_high") is not None):
            raise LLMSchemaError(
                "trader up/down call must not carry range bounds")
        return parsed

    def _risk(self, ctx):
        """The risk team: build the hypothetical paper plan from the
        trader's proposal and run the 19-check extended risk gate.
        A plan refusal, or a gate BLOCK, is recorded — never worked
        around with a weaker plan."""
        gathered = self._state["steps"]["gather"]
        closes = [c for _d, c in self.store.closes_as_of(ctx["as_of"])]
        volumes = None
        fetched = self._state["steps"]["gather"].get("fetched") or {}
        vols = (fetched.get("prices") or {}).get("volumes")
        if isinstance(vols, dict) and vols:
            by_date = dict(self.store.closes_as_of(ctx["as_of"]))
            volumes = [vols.get(d) for d in by_date]
        try:
            plan, context = build_plan(
                gathered["last_close"], closes, volumes=volumes,
                config=self.risk_config, budget=self.budget,
                symbol=ctx["symbol"],
                asset_type=ctx["inputs"].get("asset_type", "equity"),
                as_of=ctx["as_of"])
        except ValueError as exc:
            return {"verdict": "BLOCK", "failed_checks": ["plan_refused"],
                    "reason": str(exc), "plan": None, "gate": None}
        gate = extended_risk_gate(plan, config=self.risk_config,
                                  context=context)
        gate["plan"] = plan
        gate["context"] = context
        return {"verdict": gate["verdict"],
                "failed_checks": gate["failed_checks"],
                "fingerprint": gate["fingerprint"],
                "passed": gate["passed"], "failed": gate["failed"],
                "plan": plan, "gate": gate}

    def _portfolio_manager(self, ctx):
        """The portfolio manager writes the memo text. When the risk
        gate BLOCKs, no LLM call is made — a BLOCK is not something
        an LLM may argue around."""
        risk = self._state["steps"]["risk"]
        if risk["verdict"] != "PASS":
            return {"skipped": True,
                    "reason": ("risk gate BLOCK ("
                               + ", ".join(risk["failed_checks"])
                               + ") — no portfolio-manager call made")}
        trader = self._state["steps"]["trader"]
        debate = self._state["steps"]["debate"]
        reflections = load_reflections_for_symbol(
            self.reflections_path, self.decision_log_path, ctx["symbol"])
        brief = json.dumps(
            {"symbol": ctx["symbol"], "market": ctx["market"],
             "as_of": ctx["as_of"],
             "trader": trader, "debate": debate,
             "gate": "PASS",
             "reflections": reflections},
            sort_keys=True, default=str)
        prompt = (f"Write the research memo for {ctx['symbol']}.\n\n"
                  f"Trader proposal, debate and reflections:\n{brief}")
        parsed = self._llm("portfolio_manager", PM_SYSTEM, prompt,
                           PM_SCHEMA)
        parsed["reflections_used"] = [r.get("call_id") for r in reflections]
        return parsed

    def _log_call(self, ctx):
        """Write the directional call to the decision log — only when
        the gate PASSES, only once per (date, symbol, series), and
        BEFORE any outcome is known. A BLOCKed run writes nothing."""
        risk = self._state["steps"]["risk"]
        if risk["verdict"] != "PASS":
            return {"skipped": True,
                    "reason": "risk gate BLOCK — no call logged"}
        if self.decision_log_path is None:
            return {"skipped": True,
                    "reason": "no decision_log_path configured"}
        trader = self._state["steps"]["trader"]
        pm = self._state["steps"]["portfolio_manager"]
        gathered = self._state["steps"]["gather"]
        closes = self.store.closes_as_of(ctx["as_of"])
        prev_ret = None
        if len(closes) >= 6:
            prev_ret = closes[-1][1] / closes[-6][1] - 1.0
        existing = [c for c in load_calls(self.decision_log_path)
                    if c.get("date") == ctx["as_of"]
                    and c.get("symbol") == ctx["symbol"]
                    and c.get("series") == SERIES_LLM_TEAM]
        if existing:
            return {"skipped": True,
                    "reason": "call already logged for this date/symbol",
                    "call_id": existing[0].get("call_id")}
        call = {"date": ctx["as_of"], "symbol": ctx["symbol"],
                "exchange": ctx["exchange"],
                "thesis": pm["thesis"],
                "horizon_days": int(trader["horizon_days"]),
                "expected": trader["expected"],
                "sources": sorted({e.get("source", "unknown")
                                   for e in gathered["tool_ledger"]
                                   if e.get("status") == "ok"}),
                "confidence": float(trader["confidence"]),
                "confidence_basis": ("trader's stated probability from "
                                     "the llm-team debate"),
                "entry_close": gathered["last_close"],
                "prev_5d_return": prev_ret,
                "series": SERIES_LLM_TEAM,
                "team": "llm-agent-team",
                "run_id": self._state["run_id"],
                "gate": "PASS",
                "prompt_hashes": dict(self._state["prompt_hashes"])}
        if trader["expected"] == "range":
            call["expected_low"] = trader["expected_low"]
            call["expected_high"] = trader["expected_high"]
        record = write_llm_call(self.decision_log_path, call)
        return {"call_id": record["call_id"], "series": SERIES_LLM_TEAM}

    def _memo(self, ctx):
        """Assemble the research memo from the completed steps."""
        risk = self._state["steps"]["risk"]
        trader = self._state["steps"]["trader"]
        debate = self._state["steps"]["debate"]
        pm = self._state["steps"]["portfolio_manager"]
        log = self._state["steps"]["log_call"]
        gathered = self._state["steps"]["gather"]
        direction = trader["expected"] if risk["verdict"] == "PASS" \
            else "no_call"
        memo = {
            "run_id": self._state["run_id"],
            "symbol": ctx["symbol"], "market": ctx["market"],
            "exchange": ctx["exchange"], "as_of": ctx["as_of"],
            "series": SERIES_LLM_TEAM,
            "direction": direction,
            "confidence": (float(trader["confidence"])
                           if risk["verdict"] == "PASS" else None),
            "horizon_days": (int(trader["horizon_days"])
                             if risk["verdict"] == "PASS" else None),
            "thesis": pm.get("thesis"),
            "key_risks": pm.get("key_risks", []),
            "what_would_change_my_mind":
                pm.get("what_would_change_my_mind"),
            "bull": debate["bull"], "bear": debate["bear"],
            "trader": trader,
            "gate_verdict": risk["verdict"],
            "gate_failed_checks": risk["failed_checks"],
            "gate_fingerprint": risk.get("fingerprint"),
            "tool_ledger": gathered["tool_ledger"],
            "prompt_hashes": dict(self._state["prompt_hashes"]),
            "tokens_used": self._state["tokens_used"],
            "cost_usd_estimate": self._state["cost_usd_estimate"],
            "call_id": log.get("call_id"),
            "disclaimer": _DISCLAIMER,
            "paper_only": True,
            "order": None,
        }
        return memo


def render_memo_md(memo):
    """Render a research memo as Markdown. The memo is paper research:
    it carries the risk gate's verdict and the disclaimer, never an
    order and never an execution instruction."""
    lines = [
        f"# Research memo — {memo['symbol']} ({memo['as_of']})",
        "",
        ("> Research only, not investment advice. LLM outputs are not "
         "validated predictions. No performance claim is made for the "
         "llm-team series until it has >= 100 settled calls."),
        "",
        f"- Series: **{memo['series']}** (run {memo['run_id']})",
        f"- Direction: **{memo['direction']}**"
        + (f" (confidence {memo['confidence']:.2f}, "
           f"horizon {memo['horizon_days']} trading days)"
           if memo["direction"] != "no_call" else " (no call)"),
        f"- Risk gate: **{memo['gate_verdict']}**"
        + (f" (failed: {', '.join(memo['gate_failed_checks'])})"
           if memo["gate_failed_checks"] else " (19/19 checks passed)"),
        (f"- Call logged: "
         f"{memo['call_id'] or 'none (gate BLOCK or no log path)'}"),
        "",
        "## Thesis",
        "",
        memo["thesis"] or "_No thesis — the risk gate blocked this run._",
        "",
        "## Bull case",
        "",
    ]
    for claim in memo["bull"].get("claims", []):
        lines.append(f"- {claim}")
    lines += ["", "## Bear case", ""]
    for claim in memo["bear"].get("claims", []):
        lines.append(f"- {claim}")
    lines += ["", "## Key risks", ""]
    for risk_text in memo.get("key_risks") or []:
        lines.append(f"- {risk_text}")
    if memo.get("what_would_change_my_mind"):
        lines += ["", "## What would change this view", "",
                  memo["what_would_change_my_mind"], ""]
    lines += ["", "## Tool calls (point-in-time stamped)", "",
              "| Tool | Source | as_of | published_at | Status |",
              "|---|---|---|---|---|"]
    for entry in memo["tool_ledger"]:
        lines.append(
            f"| {entry['tool']} | {entry.get('source', '')} | "
            f"{entry.get('as_of') or '—'} | "
            f"{entry.get('published_at') or '—'} | {entry['status']} |")
    lines += [
        "",
        "## Provenance",
        "",
        ("- Prompt hashes: "
         + ", ".join(f"{k}={v[:12]}…" for k, v in
                     sorted(memo["prompt_hashes"].items()))),
        (f"- Tokens used (estimate): {memo['tokens_used']}, "
         f"cost (estimate): ${memo['cost_usd_estimate']:.4f}"),
        "",
        ("Paper only: this memo proposes no order and instructs no "
         "execution. A PASS from the risk gate is not a yes — it only "
         "means the hypothetical paper plan passed the 19 stated "
         "checks."),
        "",
    ]
    return "\n".join(lines)
