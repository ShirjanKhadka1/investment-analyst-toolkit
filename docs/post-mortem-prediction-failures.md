# Post-mortem: prediction failures

> Research only. Not investment advice. Nothing here is a buy, sell, or
> hold recommendation, and no accuracy figure here is a claim about real
> markets. All cases below are offline fixtures, not live trading data.

## Scope — read this first

This post-mortem covers **exactly one** failure case: the only prediction
failure for which evidence exists locally in this repository at the time
of writing (2026-10-28).

Evidence search performed before writing:

- Searched the repository for a prediction ledger (`predictions.jsonl`),
  archived runs, and failure records. Result: **no ledger file exists in
  this repository** — `reports/daily/` contains only its `README.md`, and
  no `*.jsonl` ledger was found.
- The only failure evidence found locally is the offline fixture test
  `tests/test_tracker_archive.py::test_direction_wrong_is_failure`, which
  exercises `toolkit/tracker.py`'s scoring path, reproduced below.
- No other failure evidence exists locally. **No failures have been
  invented to fill this document.** If a live-run ledger later produces
  real scored predictions, this file must be extended with those cases —
  with their n stated — not rewritten to smooth over them.

## The one case: direction-wrong prediction, logged as failure

### What failed

A single prediction was scored as **failed** because its direction was
wrong: it forecast a rise, the actual moved the other way.

### On what data (fixture, not a real security)

- Symbol: `X` — a test fixture symbol, not a real company or security.
- Model (as recorded in the ledger): `trend`.
- `last_close`: 100.0
- Forecast: 110.0 (a rise from `last_close`, so predicted direction: up).
- Predicted at: `2026-10-27`; predicted for: `next` (the next step, as
  recorded by the fixture).
- Actual close on scoring: 95.0 on `2026-10-28` (actual direction: down
  from `last_close`).
- Error: actual − forecast = −15.0; absolute error = 15.0.
- Direction correct: `False`.

### What the evidence was

Reproducing the fixture through the production code path
(`tracker.record_prediction` then `tracker.score_pending`, no threshold
argument) produces exactly this scored record:

- `status`: `failed`
- `failure_reason`: `direction wrong`
- `action`: `demote model in next auto-selection (inverse-MAE
  re-weighting); review in accuracy report`
- `direction_correct`: `False`, `abs_error`: 15.0

The ledger summary for this case, via `tracker.accuracy_report`, is:

| Metric | Value |
| --- | --- |
| Scored predictions (n) | **1** |
| Direction accuracy | 0.0 (0 correct out of 1) |
| Mean absolute error | 15.0 |
| Failure rate | **1.0 (1 failure out of 1)** |

### Stating n honestly — do not smooth this

**n = 1.** A failure rate of 1.0 over a single scored prediction is
not evidence that the model fails 100% of the time, and a direction
accuracy of 0.0 over n = 1 is not evidence that it is always wrong.
Both numbers are what one observation gives, nothing more. Any report
that quotes "100% failure" or "0% accuracy" from this case without
stating n = 1 is misrepresenting the evidence. That is why the accuracy
report added in this slice (see below) always prints n next to every
accuracy and error figure, shows pending predictions separately, and
never counts a pending prediction as a win.

### What changed as a result

Nothing about the model itself was silently altered — `toolkit/predictive.py`
was not modified in this slice. What the failure path already does, and
what this slice makes visible, is:

1. **Failure retained, not deleted.** The scored record stays in the
   ledger with `status: failed`, its reason, and its action. Failures
   are never edited out of the record (`toolkit/tracker.py`,
   `score_pending`).
2. **Inverse-MAE demotion.** The recorded action is demotion in the next
   auto-selection: `predictive.ensemble_forecast` weights models by
   inverse out-of-sample MAE, so a model whose walk-forward MAE worsens
   loses weight automatically. It is never kept because it sounded good.
3. **Naive comparison, always.** Every model must beat the naive
   forecast (next value = last value) out of sample or it is not an
   improvement (`toolkit/predictive.py::compare_models_walk_forward`,
   `toolkit/forecast.py::walk_forward_evaluate`). A model that fails
   directionally on a live ledger still has to clear that bar
   out-of-sample before it is selected.
4. **Accuracy report that cannot hide n or pending.**
   `toolkit/accuracy_report.py` (added in this slice, not a model
   change) reads the tracker ledger and produces a Markdown table per
   model/version with direction accuracy and mean absolute error **with
   n**, pending counts shown separately and never counted as wins, and
   failures listed with their reason and action. A model with zero
   scored predictions is shown as "no scored predictions", never as 0%.

## What this post-mortem does NOT claim

- No accuracy claim for any real market, security, or future period.
- No claim that one fixture failure generalises to any model.
- No claim that demotion or naive comparison prevents future failures.
  They only make failures measured, visible, and acted on — which is
  the honest limit stated in the README: no system is failure-free.

## Follow-up

- When a real ledger exists, append each new failure here with its
  symbol, model, dates, forecast, actual, error, reason, and action —
  and update the n for every rate quoted.
- Revisit this file if a failure pattern repeats across models or
  symbols; a single n = 1 case is a record, not yet a pattern.

## Phase 10 appendix (29 Oct 2026) — new evidenced failures, not smoothed

The Phase 10 evaluation (synthetic fixtures only, `compare_models_walk_forward`
with `horizons=(1, 3, 5)`, `include_new_models=True`, ensembles on) adds
these evidenced results. They are fixture results at stated horizons and n,
not claims about real markets.

- **Level shift 100 → 150, train 15, all horizons: naive wins.**
  h=1 naive MAE 3.333, h=3 3.846, h=5 4.545. Every non-naive model loses
  at every horizon (worst: holt at h=5, 19.424). AR(1) ties naive exactly
  at every horizon (3.333 / 3.846 / 4.545) — a tie is not a win under the
  strict `<` rule, and is reported as such. The adaptive ensemble does
  NOT rescue the shift (6.276 / 7.690 / 10.382): adaptive weighting
  re-weights lagging models, it does not make them fast.
- **Drift loses the noisy-flat fixture at every horizon** (2.906 vs naive
  2.900 at h=1; 2.164 vs 1.833 at h=3; 3.668 vs 3.438 at h=5). This is the
  documented drift failure mode: on a series with no real drift, the mean
  recent change is noise, and adding it hurts. Drift is kept in the pack
  as a candidate with this loss on its record, not removed to flatter it.
- **Trending fixture, +2/step: exact wins, honestly bounded.** linear_trend,
  drift, holt, and the adaptive ensemble all score MAE 0.000 at h=1/3/5
  on a perfect trend — because the fixture has no noise. That is a
  correctness check (the maths reproduces a straight line), not evidence
  of real-world accuracy, and it is labelled as such wherever quoted.
