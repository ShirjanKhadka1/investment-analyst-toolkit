# Executable agent team

`toolkit/agent_team.py` turns the existing analyst personas into an
executable team that produces a research memo. It is the debate runner
the capability matrix pointed at: analysts → bull/bear debate →
trader → risk team → portfolio manager, with a checkpoint after every
step so an interrupted run resumes where it stopped.

## What it is not

- **Not a trading system.** The output is a research memo carrying
  the risk gate's PASS or BLOCK. It proposes no order and instructs
  no execution. Paper only.
- **Not validated predictions.** LLM outputs are not validated
  predictions. Nothing in a memo is a performance claim. LLM-team
  calls are scored only after they settle, against the same naive
  and buy-and-hold baselines as the pre-registered rules, and no
  hit rate is published before 100 settled calls.
- **Not a new LLM client.** Every LLM call goes through PR A's
  provider layer (`toolkit/llm_providers.py`). There is no ad-hoc
  client anywhere in this module.

## The flow

One `AgentTeam.run(symbol, market, as_of)` executes eight steps,
in order:

1. **gather** — fetches prices through the repo's existing
   connectors only (Yahoo, EDGAR, CoinGecko, ECB, World Bank, CSV).
   Each tool call is stamped with `as_of` and `published_at` and
   recorded in a tool ledger. A tool whose data is not yet published
   on the decision date is marked `excluded` and never reaches the
   analysts. Prices load into the point-in-time store with
   `published_at = bar date + 1 day`, so only bars dated strictly
   before the decision date are visible. The memo's entry close is
   therefore the last *completed* close before the decision date —
   not the decision-day close the daily track-record script uses.
   At least two completed bars are required.
2. **analysts** — the existing deterministic personas from
   `toolkit/rules_mode.py` (`run_global_rules_team`, or
   `run_rules_team` for NEPSE) run over the visible bars and the
   ledger-ok tool data. Caller-supplied `fundamentals_metrics`
   override EDGAR values.
3. **debate** — one LLM call for the bull case, one for the bear
   case, each schema-validated (side, 1–5 claims, up to 5 key
   risks). The bull result is checkpointed before the bear call,
   so a failure there does not redo the bull.
4. **trader** — one LLM call proposing a direction (`up` / `down` /
   `range`, confidence 0–1, horizon 1–30 days, rationale). A
   `range` call must carry `expected_low`/`expected_high`; `up` and
   `down` must not.
5. **risk** — the plan is built from the completed closes
   (`build_plan`: stop 2× mean absolute daily move below entry,
   target 2× above, reward:risk 2.0) and run through the 19-check
   `extended_risk_gate` from `toolkit/position_plan.py`. A refused
   plan (flat series, zero shares, risk below the 0.05% floor) is a
   BLOCK with `plan_refused` as the failed check.
6. **portfolio_manager** — skipped, with no LLM call, when the gate
   BLOCKs. Otherwise one LLM call (thesis, key risks,
   what-would-change-my-mind) with the latest reflections for the
   symbol in the prompt. The PM writes the memo's thesis; the
   trader decides the direction.
7. **log_call** — only when the gate PASSes. The call is written to
   the decision log (inputs, prompt hashes, outputs) *before* any
   outcome is known. Idempotent: a call for the same date, symbol
   and series is never written twice.
8. **memo** — the research memo. On a BLOCK its direction is
   `no_call` and confidence/horizon are `None`.

## Checkpoint and resume

The checkpoint is one JSON file (`checkpoint_path`), written
atomically (temp file + `os.replace`) after every step and after
each LLM call inside the debate. It records: schema version
(`1.0.0`), run id, symbol, market, decision date, horizon, the
completed steps in order, each step's output, the fetched payload
(JSON-safe: tuples become lists), cumulative tokens and cost, and
the prompt hashes. A failed step records `status: "failed"`,
`failed_step` and the error, then re-raises.

A second `run()` with the same run id resumes: completed steps are
returned from the checkpoint without re-running, the point-in-time
store is rebuilt from the checkpointed bars, and the run continues
from the failed step. A completed run re-run returns the stored
memo and makes no provider calls. No step is redone and no call is
double-logged (tests cover both).

## Cost and token caps

The team enforces a per-run cap (`max_tokens_per_run`, default
4000 tokens; `max_cost_usd_per_run`, default USD 0.50) checked
after every LLM call — the first call that takes the cumulative
total over the cap stops the run with `LLMCapError`. The provider
layer enforces its own per-call caps underneath. CI uses only the
fake provider: no real keys, no network in tests.

## The llm-team series

LLM-team calls live in their own labelled series, `"llm-team"`, in
the decision log — never mixed into the pre-registered rules'
(`sma50_trend` / `mom20_sign`) series. `write_llm_call` forces the
series and refuses any other value; `calls_by_series` treats a
missing series as `"pre-registered"` (the legacy exchange default
in the decision log); `score_series` and `render_series_md` count
and render one series at a time, with the same 100-call headline
rule as the track record. `settle_llm_call` settles against the
same baselines as the daily script: the naive baseline predicts
the previous 5-day direction continues, buy-and-hold always
predicts up.

## Reflections

After a call settles, `reflect(path, call_id, text, author)` appends
a reflection to the reflections file. The next run for that symbol
loads the latest three reflections into the portfolio manager's
prompt (`load_reflections_for_symbol` resolves call ids to symbols
through the decision log). Reflections are how a settled outcome
feeds the next run — never the current one.

## Volume gap — closed by the Round 7.1 rebase

The gap this section used to describe is closed: Round 7.1 added
per-bar volumes to the connector layer
(`toolkit/connectors.py`: `BarsConnector`,
`fetch_prices_with_volumes`), and the 9.1 rebase adopts it — the
default fetcher now calls `fetch_prices_with_volumes` with
`include_stooq=False`, so a normal Yahoo run supplies
`typical_daily_volume` and the extended gate's `exit_liquidity`
check can pass. Stooq stays opt-in; the live chain is Yahoo-only.
A serving source with no bars (CSV, CoinGecko) still returns no
volumes, and the gate then fail-closes (BLOCK) on exit-liquidity,
as it must — the no-volume → BLOCK path is still covered by a
dedicated test. No Yahoo volume adapter lives in this module: the
volume path is the existing connector, not a new one.

## How to run it (fake provider)

```python
from toolkit.agent_team import AgentTeam
from toolkit.llm_providers import FakeProvider

team = AgentTeam(
    FakeProvider(script=[bull_json, bear_json, trader_json, pm_json]),
    decision_log_path="calls.jsonl",
    reflections_path="reflections.jsonl",
    checkpoint_path="checkpoint.json",
)
memo = team.run("AAPL", "us", "2026-11-13", exchange="Nasdaq")
```

The script is played in order: bull, bear, trader, portfolio
manager. With a real provider, use `provider_from_env` and pass
the provider in — the team never reads keys itself.

## Tests

`tests/test_agent_team.py` (27 tests): full PASS run, memo
rendering, no look-ahead (a future bar leaves the memo unchanged;
mutating the last used bar changes it), resume after interruption
at debate, trader, and portfolio manager (fresh process, no redo,
no double log), completed re-run makes no provider calls, schema
failure, cost cap, range-without-bounds, bear wrong-side refusal,
gate BLOCK without volume (memo is `no_call` with confidence and
horizon `None`), flat-series plan refusal, series separation,
settlement baselines, reflections feeding the next run, a run with
no reflections unchanged, excluded tool data never reaching the
analysts, the default fetcher using the Round 7.1 volume chain
(Yahoo-only, Stooq opt-in off; a volume-less source stays
volume-less), and invalid market / missing-bars validation.
`tests/test_evaluate_llm_team.py` (7 tests) covers the llm-team
evaluator: verdict withheld before 100 scored calls, "beats
naive" and "no edge" at 100, range-call exclusion, and other
series never scored.
