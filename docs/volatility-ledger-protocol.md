# Forward volatility-ledger protocol (Round E, E.2) — PRE-REGISTERED

Written 2026-11-15, **before any ledger row exists**. This protocol
fixes the universe, models, ledger schema, scoring rule and claim
threshold in advance. No parameter is tuned after seeing results;
no claim is made before the threshold in "Claim threshold" is met.
If the scored results are bad, the bad result is published, not
re-tuned away. Model rules (MASTER-ROADMAP.md): a model must beat
its baseline out of sample or be labelled "no edge".

This protocol is the forward counterpart of the backtest in
`docs/volatility-forecast-eval-protocol.md` (PR 7.2, run 2026-11-14:
EWMA −8.0264 and GARCH-lite −8.0643 pooled mean QLIKE vs naive
−7.9652 on SPY/AAPL/VT). The backtest scored history; this ledger
scores the future, one day at a time, starting only after this
protocol merges. Nothing scored here may be backdated into the
backtest, and nothing from the backtest counts toward the ledger's
claim threshold.

## Pre-registered summary (everything fixed before the first row)

- **Universe:** the exact 7-ticker watchlist approved by the owner
  on 13 Nov 2026 — SPY, AAPL, VT, NIFTYBEES.NS, ISF.L, EURUSD=X,
  BTC-USD. No additions, no substitutions.
- **Models:** naive (trailing-20-day realised variance), EWMA
  (λ = 0.94), GARCH-lite (α = 0.08, β = 0.90) — the same three
  models, with the same parameters, as
  `toolkit/volatility.py` (`forecast_variance`). No parameter
  search will be run for this ledger.
- **Number of models tried: 0 new.** The three models are the
  backtest's models, carried forward unchanged. No new model may
  be added to the ledger without a protocol amendment.
- **Loss function:** QLIKE primary, MSE on variance secondary.
- **Baseline:** the naive model, scored in the same ledger.
- **Ledger:** append-only, hash-chained JSONL on its own data
  branch (`data/volatility-ledger`), file
  `volatility-ledger.jsonl`, plus `missed_days.jsonl`.
- **History window:** each forecast uses the trailing 253 closes
  (252 log returns) up to and including the origin bar — the
  same 252-return training size as the backtest. Fewer than 253
  closes available (a new listing, a fetch gap) means no
  forecast: a missed day, never a forecast from a shorter
  window. The fetch range is 2y; only the last 253 closes are
  used.
- **Claim threshold:** no verdict of any kind before **100 scored
  observations per symbol** (700 scored observations pooled).
  Before that, the ledger holds forecasts and scores only — no
  "beats baseline", no "no edge", no coverage verdict.

## Ledger schema (version 1.0.0)

One forecast row per (symbol, forecast date, model) — three rows
per symbol per day. A forecast row is written once and NEVER
changes. A score is a SEPARATE appended row that references the
forecast id — scoring never rewrites a forecast row. This is the
same shape as `toolkit/forecast_ledger.py` (PR 8.1) and the same
hash chain as the track record (`toolkit/decision_log.py`:
`canonical_row_bytes` / `row_hash`; `prev_hash` is the sha256 of
the previous row's canonical JSON; the first row's `prev_hash` is
`"genesis"`).

Forecast row fields:

- `schema_version`: `"1.0.0"`.
- `record_type`: `"forecast"`.
- `symbol`: one of the seven watchlist tickers.
- `forecast_date`: the nominal next calendar day after the origin
  bar (`origin_date` + 1 day). The forecast is defined as the
  variance of the FIRST bar after the origin bar, whatever date
  that bar carries; when it lands later (weekend / holiday), the
  score row's `actual_date` is that bar's real date. The nominal
  date is a label, not a promise that the date trades.
- `origin_date`: the ISO date of the last bar used (the entry bar).
- `origin_close`: that bar's close.
- `model`: `"naive"`, `"ewma"` or `"garch_lite"`.
- `forecast_variance`: the one-day-ahead variance forecast.
- `annualised_vol`: `sqrt(forecast_variance * 252)` for equities /
  ETFs; for BTC-USD and EURUSD=X the same 252-day annualisation is
  reported and labelled as such (a 24-hour market has no 252-day
  session year; the figure is a comparability convention, not a
  claim about sessions).
- `bar_complete`: `true` — a forecast row exists only for a
  complete origin bar (see "bar_complete rule").
- `source`: the fetch result's actual source (e.g. `"yahoo"`).
- `prev_hash`, `forecast_id`: the chain link and the deterministic
  id (sha256 of the row's content, first 16 hex), exactly as in
  `toolkit/forecast_ledger.py`.

Score row fields:

- `schema_version`: `"1.0.0"`.
- `record_type`: `"score"`.
- `forecast_id`: the forecast row this score settles.
- `actual_date`: the ISO date of the realised bar — the first bar
  after the origin bar.
- `realised_variance`: the squared log return of that bar
  (`ln(C_t / C_{t-1})²`, where `C_{t-1}` is the origin bar's
  close).
- `qlike`, `mse_variance`: the two scores for this forecast.
- `status`: `"scored"` or `"failed"` (`"failed"` when the realised
  bar never arrived — the forecast stays unscored forever, it is
  never scored against a later bar's close).
- `prev_hash`: the chain link.

A forecast is scored at most once. A correction is a new forecast
row, never an edit or a second score.

## bar_complete rule (same as the track record)

The rule is the track record's rule
(`scripts/track_record_daily.py::_bar_complete`), applied to the
origin bar:

- Equities/ETFs: the exchange session for the bar's date must have
  closed by the run time (UTC). A bar whose session has not closed
  is incomplete.
- Crypto (BTC-USD) and FX (EURUSD=X) have no daily session, so
  their bar is complete only when it is dated before the current
  UTC day — a bar dated today is still in progress.
- When the session state is UNKNOWN (no IANA time-zone database,
  so the session close cannot be computed), the run refuses —
  it never guesses.

A forecast is logged ONLY from a complete bar. An incomplete or
missing bar is recorded as a **missed day** in `missed_days.jsonl`
and never produces a forecast row — never filled in, never
forecast from a partial bar. A run with zero missed days writes
no missed-day record (the file is still published, empty when
nothing was missed). There is no provisional mode: a manual
dispatch before the close is refused, not logged as provisional
(the track record's provisional calls are never scored; the
volatility ledger is stricter — an incomplete bar produces no
row at all).

## Runner and data branch

- The runner is `scripts/volatility_ledger_daily.py`. It fetches
  the seven tickers, takes the last COMPLETE bar per symbol,
  computes the three variance forecasts from log returns up to
  that bar, appends the forecast rows, then scores any forecast
  rows whose `forecast_date` now has a realised bar (the realised
  variance is that bar's squared log return).
- The ledger lives on its OWN data branch, `data/volatility-ledger`
  — it is never written to `data/track-record`, and the track
  record is never written to this branch.
- Publishing follows the track record's PR 7.0 invariant: the
  local ledger must start with the remote branch's bytes
  (additions only, no row removed or changed) and the hash chain
  must verify, or the publish is refused. The branch history is
  checked after publishing by
  `scripts/verify_volatility_ledger.py` (same append-only walk as
  `scripts/verify_track_record.py`, with the volatility ledger's
  defaults).
- A second run must APPEND, never overwrite. The overwrite
  incident of November 2026 (a scheduled run that overwrote the
  track-record log because main held no calls file) must not
  repeat: the runner seeds its local ledger from the data branch
  before running, and the multi-run test (see "Acceptance
  criteria") proves the second run appends.

## Scoring rule and claim threshold

- **Primary: QLIKE** — `QLIKE = ln(σ̂²) + σ²/σ̂²`, averaged over
  scored observations. Lower is better.
- **Secondary: MSE on variance** — `mean((σ² − σ̂²)²)`. Reported
  for completeness; it never decides the verdict.
- **Interval coverage** (80% / 95%, `±z·σ̂` intervals exactly as
  in the backtest protocol) is a separate target check, reported
  alongside the verdict, never rewriting it.
- **Claim threshold: 100 scored observations per symbol.** No
  verdict — pooled or per-symbol — is published before every
  symbol has at least 100 scored observations. Before that, the
  ledger doc (`docs/volatility-ledger.md`) reports counts only:
  forecasts logged, scores settled, missed days. A model "beats
  baseline" only when the threshold is met AND its pooled mean
  QLIKE is lower than the baseline's AND the 95% paired-bootstrap
  CI on the mean QLIKE difference (model − baseline, 2000
  resamples, seed 20261114) lies entirely below zero — the same
  two-part rule as the backtest. Otherwise the verdict is
  "no edge". A win on MSE alone never counts. A win on one symbol
  alone never counts.

## Workflow plan

- File: `.github/workflows/daily-volatility-ledger.yml`, staged
  for the owner to deploy (the owner deploys it himself; this
  round does not deploy any workflow).
- Schedule: daily, 22:30 UTC — after the NYSE close (21:00 UTC)
  and after the daily track record (22:00 UTC).
- Steps: checkout, setup Python 3.12, seed the local ledger and
  missed-days files from the `data/volatility-ledger` branch
  (genesis when the branch or the files do not exist yet), run
  the daily ledger step with `--publish`, verify the branch
  history is append-only.
- No `workflow_dispatch` provisional mode. A manual dispatch
  before the close is refused by the runner.

## Acceptance criteria

1. This protocol merges before any ledger implementation or row.
2. The ledger module, runner, verifier, ledger doc and tests
   land in a follow-up PR stacked on this one; no ledger row is
   written before that PR merges.
3. The multi-run test proves a second run appends and does not
   overwrite (three consecutive simulated runs against a local
   bare remote, mirroring
   `tests/test_track_record_append_only.py`).
4. The verifier catches a tampered row, an orphan score and a
   duplicate score.
5. `scripts/qa.py --offline` passes 13/13 and the full test
   suite passes at the implementation PR's tip.
6. No claim is published before 100 scored observations per
  symbol. The ledger doc states the count honestly until then.

## What this protocol does not do

- It does not change the backtest, its verdicts or its model
  card. The backtest stands as the 2026-11-14 record.
- It does not touch any track-record file, workflow or branch.
- It does not add a model, a parameter or a new dependency
  (stdlib only).
- It does not promise the ledger will show an edge. The ledger
  may show "no edge" for every model — that result is published
  as it is.
