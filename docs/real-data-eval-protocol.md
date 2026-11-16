# Real-data evaluation protocol (Round E, E.1) — PRE-REGISTERED

Written 2026-11-15, **before any evaluation in this round was
run**. This protocol fixes the universe, data sources, splits,
models, scoring and decision rules in advance. No parameter is
tuned after seeing results; if the results are bad, the bad
result is published, not re-tuned away. Model rules
(MASTER-ROADMAP.md): a model must beat its baseline out of
sample or be labelled "no edge".

This protocol re-runs four existing evaluations — the forecast
harness, the direction models, the cross-sectional ranking and
the event study — on one bounded REAL universe, replacing the
synthetic fixtures and three-symbol samples the earlier runs
used. The earlier model cards stand as their own records
(2026-11-14); this round's cards are new documents, dated
2026-11-15, and the capability matrix's real-data column is
updated to point at them.

## Survivorship bias — stated plainly, in this protocol and in every card

The universe below is chosen from TODAY's liquid names. Past
losers that delisted are absent, so every backtest in this
round is conditioned on survival to November 2026. Returns,
spreads and verdicts here are therefore optimistic relative to
a true point-in-time universe that included the names that
disappeared. Nothing in this round corrects for that bias; the
cards state it and the numbers are reported as
survivorship-conditioned, not as unbiased history.

## Universe and data

- **Universe (34 symbols): 30 liquid US large caps plus 4
  ETFs.** Caps: AAPL, MSFT, NVDA, GOOGL, AMZN, AVGO, META,
  TSLA, LLY, JPM, V, XOM, JNJ, MA, COST, HD, PG, BAC, UNH,
  CVX, ORCL, CRM, NFLX, ADBE, AMD, CAT, GS, WMT, KO, DIS.
  ETFs: SPY, VT, QQQ, DIA. Every cap is a current S&P 500
  constituent with a 10-K on file; every ETF has a long Yahoo
  history. No crypto, no FX, no NEPSE (no keyless daily feed
  exists for it).
- **Prices:** Yahoo Finance chart API via the toolkit's own
  `toolkit.data.fetch_yahoo_prices`, daily closes only, as
  returned by Yahoo; no adjustment, cleaning or filling. A
  fetch failure aborts the run — no invented prices. The fetch
  is range 5y for every symbol (the cross-sectional ranking
  needs closes back to mid-2024 and the event study needs
  280+ bars before its 2025 events). The harness and direction
  runs use the TRAILING 2y slice of that 5y fetch (closes
  dated within the 2 calendar years ending at the last fetched
  date) — the same window as the 2026-11-14 runs. The window,
  not the rule, is what is bounded: the harness's paired
  bootstrap CI is 10,000 resamples over the pooled folds, so
  its cost scales with the fold count, and a 5y × 34-symbol
  harness would take hours in stdlib Python (measured
  2026-11-15: one 5y symbol's price-model evaluation takes
  ~100 s). Every split, resample count and seed stays exactly
  as the original protocols fix them.
- **Fundamentals (cross-sectional only):** SEC EDGAR
  companyfacts via `toolkit.edgar`, point-in-time: for each
  ranking date, each concept is the latest 10-K value with a
  filing date on or before the ranking date
  (`annual_value_as_of`, added by this round). No look-ahead:
  a filing made after the ranking date is never used for it.
  Concepts: NetIncomeLoss, StockholdersEquity, Revenues,
  GrossProfit, Assets (us-gaap). XBRL tags are NOT uniform
  across filers, so each concept is the FRESHEST value across
  a fixed candidate list (`freshest_annual_value_as_of`):
  revenue tries Revenues, SalesRevenueNet, then
  RevenueFromContractWithCustomerExcludingAssessedTax, then
  RevenuesNetOfInterestExpense (Goldman Sachs files no
  top-line revenue tag at all — its revenue is net of interest
  expense, seen 2026-11-15); equity tries StockholdersEquity,
  then StockholdersEquityIncludingPortionAttributableToNon-
  controllingInterest. A single tag would silently return a
  stale value for some filers (Microsoft's `Revenues` ends
  2010-12-31; Amazon files no `Revenues` at all; Visa's
  `StockholdersEquity` stops in 2011 — all seen 2026-11-15).
  The card records which tag supplied each value. One ticker
  mapping is overridden, not trusted: the SEC's own
  company_tickers.json maps XOM to CIK 2115436 ("ExxonMobil
  Holdings Corp"), whose submissions endpoint 404s, while the
  real filer EXXON MOBIL CORP (CIK 34088) has an empty ticker
  list and is absent from that table entirely — so XOM is
  mapped to 34088 by an explicit override in the eval script,
  and the card states it. Market cap
  is shares outstanding — the latest
  `dei:EntityCommonStockSharesOutstanding` value filed on or
  before the ranking date, any form — times the close on the
  ranking date. Alphabet and Meta carry no shares-outstanding
  concept in EDGAR companyfacts at all, so they have no market
  cap and no value/quality components at any rebalance
  (momentum only); the card states that. A concept missing as
  of a ranking date excludes that component for that symbol at
  that rebalance (recorded in the card); no value is ever
  invented or defaulted. In particular, banks and payment
  networks carry no GrossProfit concept: their quality score
  uses ROE only, and the card states the per-symbol concept
  coverage.
- **Cache (local-only, NOT committed):** the fetched prices and
  point-in-time fundamentals are written to
  `data/real-data-eval-2026-11-15.json` by
  `scripts/fetch_real_data_eval.py` (the evaluation script's
  `--refresh` does the same fetch), and the default run reads
  the cache instead of the network, so the run is reproducible
  offline. The raw cache is gitignored — data policy is derived
  metrics only. The committed record is
  `data/real-data-eval-2026-11-15/derived-metrics.json` plus
  `data/real-data-eval-2026-11-15/manifest.json`, which records
  the fetch date, the source of every value, the CIK per ticker
  and the SHA-256 of the raw cache as fetched. The EDGAR fetch needs a
  declared User-Agent with a contact email (SEC fair-access
  rule); the script reads it from the `EDGAR_CONTACT_EMAIL`
  environment variable and refuses to fetch EDGAR without it.
  The contact email is never written to the repo (the secrets
  gate forbids email addresses in the tree).
- **EDGAR access note (2026-11-15):** from this VM the SEC
  returns 403 to the toolkit's previous User-Agent (name + URL,
  no contact email) and to the www.sec.gov ticker table; a
  declared User-Agent WITH a contact email returns 200 on both
  data.sec.gov and www.sec.gov. This round fixes
  `toolkit/edgar.py` accordingly (gzip decompression, which the
  server honours and the old code did not handle, plus a
  `declared_user_agent(contact_email)` constructor). The fix is
  in the E.1 implementation commit, and the evaluation script
  is the first caller.

## Evaluations (all four, same script)

The evaluation is run by `scripts/evaluate_real_data.py`
(stdlib only; network only for the Yahoo/EDGAR fetch under
`--refresh`). Its JSON output is the input to the four new
model cards. The offline test suite never touches the network:
it runs the same scoring and decision-rule code on a bundled
SYNTHETIC slice of equal structure (tests/fixtures/
real_data_eval_slice.json, generated by scripts/
generate_synthetic_real_data_fixture.py, seed 20261116 — no
vendor prices, no real EDGAR facts) plus deterministic fixtures.
The 2026-11-15 real-data run itself is recorded in the four
model cards and data/real-data-eval-2026-11-15/; the tests
validate the code, not that run.

### 1. Forecast harness (all 34 symbols, trailing 2y closes)

`toolkit/forecast_harness.run_full_evaluation`, train_size 252,
purge 5, embargo 5, 10,000 resamples, seed 20261114 — the same
splits and rules as `docs/forecast-harness-eval-protocol.md`
(9 price models + 2 volatility models = 11 models tried).
Overall price verdict per model is "beats baseline" only if it
beats the baseline on every symbol; otherwise "no edge".
Pooled direction accuracy and pooled volatility comparison are
reported as in the original protocol.

### 2. Direction models (all 34 symbols, 2y closes)

`toolkit/direction_models.walk_forward_direction`, train_size
252 — the same models, baselines and decision rule as
`docs/direction-models-eval-protocol.md` (logistic
gradient-descent, Gaussian naive Bayes, fixed-weight score,
regime-conditioned logistic, equal / performance-weighted
ensembles; coin-flip, base-rate and direction-rule baselines;
Brier primary, log score secondary; a model is "edge" only
when it beats ALL THREE baselines on pooled mean Brier with
the 95% paired-bootstrap CI entirely below zero, 2000
resamples, seed 20261115; otherwise "no edge"). Reliability
bins and regime diagnostics (CUSUM alarms, HMM-lite
transitions) are reported as diagnostics, never as verdict
rules. The SPY/AAPL/VT-only guard in `walk_forward_direction`
is lifted by this round: the models are symbol-agnostic
(features are per-symbol returns; nothing in the maths is
symbol-specific), and the lift is recorded in the card. No
other change to the models is made.

### 3. Cross-sectional ranking (30 caps only — ETFs carry no EDGAR fundamentals)

- **Rebalances:** 12 monthly ranking dates — the last trading
  day of each month, December 2024 through November 2025,
  determined from the fetched Yahoo dates. Scores use closes
  up to the ranking date only; forward returns start the next
  bar.
- **Components:** momentum (126-bar formation, skip 21),
  value (earnings yield, book-to-price) and quality (ROE,
  gross margin) from the point-in-time fundamentals above,
  cross-sectional z-scores, composite as the mean of the
  available scores — exactly `toolkit/cross_sectional.
  rank_universe`. A symbol needs at least one component; a
  component needs at least two symbols carrying it.
- **Spreads:** per rebalance, the top-decile mean net 21-day
  forward return minus the bottom-decile mean net forward
  return, for each of the four rules (momentum, value, quality,
  composite), net of costs at the global flat cost_fraction
  0.001 per leg (round trip doubled) — the same cost model as
  the synthetic evaluation. The verdict per rule is
  `spread_verdict`: "beats zero" only if the mean net spread
  is greater than zero AND the 95% seeded-bootstrap CI on the
  mean spread lies entirely above zero (2000 resamples, seed
  20261114); otherwise "no edge".
- **Baseline:** the equal-weight universe mean net forward
  return per rebalance, reported alongside the spreads.

### 4. Event study (earnings filings)

- **Events:** for each of the 30 caps, its most recent 10-Q
  filing dated in 2025 (from EDGAR submissions); keep the
  first 10 by ticker (alphabetical) whose filing date is a
  trading day in the price series with at least 280 prior
  bars (the estimation window needs [-250, -31]). The event
  date is the FILING date — the public disclosure; the
  earnings release itself may precede the filing by days, and
  the card states that limitation. Event type: `earnings`.
- **Method:** `toolkit/event_study.run_event_study`, market
  model vs SPY when the symbol and SPY series share dates
  exactly, otherwise mean-adjusted (recorded per event);
  estimation window [-250, -31], event window [-5, +5];
  CAR per event with t-test AND seeded-bootstrap p;
  aggregate CAAR with the small-n caveat (n = 10 < 30, so the
  caveat flag is True and the card carries it). Significant
  only when BOTH |t| > 1.96 AND bootstrap p < 0.05.

## What counts as "models tried"

Harness 11 (9 price + 2 volatility), direction 6 evaluated
models (3 base + regime logistic + 2 ensembles), ranking 4
rules, event study 1 method. No parameter search is run for
any of them: every parameter is fixed by the protocols above
or by the modules' pre-registered constants. If a model fails
its rule, the card says "no edge" — the evaluation is the
deliverable either way.

## Reproducibility

```text
EDGAR_CONTACT_EMAIL=<contact> python3 scripts/fetch_real_data_eval.py       # re-fetch the raw data locally (gitignored, never committed)
EDGAR_CONTACT_EMAIL=<contact> python3 scripts/evaluate_real_data.py --refresh   # fetch + cache + evaluate
python3 scripts/evaluate_real_data.py                                            # evaluate from the local cache
```

The script prints one JSON document: universe, fetch date,
per-symbol series lengths and date ranges, harness results,
direction results, ranking spreads and verdicts, event-study
per-event and aggregate results, and the per-symbol
fundamentals coverage. That JSON is committed as
`data/real-data-eval-2026-11-15/derived-metrics.json` (the raw
cache itself is never committed); the four new model cards
(`docs/forecast-harness-real-data-model-card.md`,
`docs/direction-models-real-data-model-card.md`,
`docs/cross-sectional-real-data-model-card.md`,
`docs/event-study-real-data-model-card.md`) are written from
that JSON output, and each states the survivorship bias above.
