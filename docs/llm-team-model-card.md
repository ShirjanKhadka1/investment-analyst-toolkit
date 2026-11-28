# llm-team model card

Series: `llm-team` — direction calls from the executable agent team
(`toolkit/agent_team.py`). Written 2026-11-28, at the 9.1 rebase.
Counts only: no settled llm-team call exists yet, so there is no
prediction verdict of any kind here.

## Series and separation rule

- `llm-team` calls are written only by `write_llm_call`, which
  forces `series="llm-team"` and refuses any other value, so a
  team-written call can never land in the pre-registered rules'
  series (`sma50_trend` / `mom20_sign`, unlabelled or
  `series="pre-registered"`).
- `score_series` and `render_series_md` count and render one
  series at a time; the two series are never pooled.
- The pre-registered series is untouched by this PR: no
  track-record file, workflow, or calls file was modified.

## Rebase

- Base: main `94a6db6b3bae773fbcc6fe9f9397476c7dd790e0`
  (2026-11-27). PR #29 (`llm-agent-team`, commit `0431e3f`)
  rebased onto it on the new branch `round-9-1-agent-team`;
  PR #29 itself was not modified, and this PR does not merge it.
- Conflicts (4, all resolved by taking main and re-applying the
  PR #29 change on top): `README.md` (test-count line),
  `docs/CAPABILITY-MATRIX.md` (regenerated from the generator),
  `status.json` (regenerated from the live run),
  `toolkit/__init__.py` (main's `__all__` plus `agent_team`).
  `scripts/generate_capability_matrix.py` merged cleanly.
- The PR #29 volume gap is closed by adoption, not by new code:
  the default fetcher now calls
  `connectors.fetch_prices_with_volumes` (`BarsConnector` over
  Yahoo, Round 7.1). Stooq stays opt-in (`include_stooq=False`);
  the live chain is Yahoo-only. A serving source with no bars
  (CSV, CoinGecko) still yields no volumes and the gate
  fail-closes on exit-liquidity.

## What 9.1 hardens (tests)

- Debate: bull then bear, each schema-validated JSON
  (side, 1–5 claims, up to 5 key risks); the bull result is
  checkpointed before the bear call, and a bear failure does not
  redo the bull. Wrong-side replies are refused.
- Reflection: the latest 3 reflections for the symbol feed the
  portfolio-manager prompt; a run with no reflections is
  unchanged.
- Resume: interrupted at debate, trader, or portfolio-manager,
  a fresh process resumes from the failed step — no completed
  step re-run, no LLM call repeated, no call double-logged. A
  completed re-run returns the stored memo and makes no provider
  calls.
- Risk gate: the 19-check extended gate is the veto. On BLOCK
  the portfolio manager makes no LLM call, no call is logged,
  and the memo is direction `no_call` with confidence and
  horizon `None`. On PASS the memo carries the gate's PASS.
  The volume-less exit-liquidity path still fail-closes (BLOCK).
- Caps: 4000 tokens / USD 0.50 per run, checked after every LLM
  call; the first call over the cap stops the run.
- Look-ahead: a future bar leaves the memo unchanged; mutating
  the last used bar changes it; excluded tool data never reaches
  the analysts.
- Test counts: `tests/test_agent_team.py` 27 tests (19 from PR #29,
  8 new in 9.1); `tests/test_evaluate_llm_team.py` 7 tests.
  All run on the fake provider with injected fetchers — no
  network, no key, no real prices.

## Evaluation (counts only)

Evaluator: `scripts/evaluate_llm_team.py` — Brier primary, hit
rate secondary, naive and buy-and-hold baselines, seeded paired
bootstrap CI (10000 resamples, seed 20261114). Verdict rule
(pre-registered in `docs/round-9-protocol.md`): below 100 scored
calls the verdict is "withheld"; at 100+ it is "beats naive"
iff the mean paired Brier difference (naive minus model) is
positive and its 95% CI lies entirely above zero, else "no edge".

Run against the live log (`data/track-record` branch,
`calls-v2.jsonl`, 23 rows, 2026-11-28):

- llm-team calls total: **0**
- Settled: **0** — open: **0** — scored for Brier: **0**
- Brier (model / naive / buy-and-hold): not computed (n=0)
- Hit rates: not computed (n=0)
- **Verdict: withheld** (n=0 < 100)

## Known gaps

- The series has no calls at all: nothing has been logged by a
  live team run, so no prediction claim exists to evaluate.
  The first 100 settled calls are the entire prediction question.
- The debate is bull/bear only; the three-way risk debate stays
  unimplemented — the deterministic 19-check gate stands in for
  it (capability matrix row wording).
- Groundedness is not yet checked: a memo number that does not
  trace to a tool output is not blocked by 9.1 — that is 9.2.
- No live agent run has ever executed: every 9.1 run in tests
  uses the fake provider. A real-provider run has no cost,
  latency, or failure record yet.
- The evaluator's naive Brier treats the naive baseline as a
  deterministic forecast (probability 0 or 1); that is the
  honest deterministic form, but it makes the naive Brier
  0 or 1 per call, never 0.25.
