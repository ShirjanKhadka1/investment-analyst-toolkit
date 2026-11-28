# Personas team model card — Round 10, PR 10.3

Run the five global personas as a team on a real company, score
each persona section, and measure cross-persona agreement. The
personas are the deterministic rules functions in
`toolkit/rules_mode.py` (`GLOBAL_TEAM`) — no LLM is involved, so
no LLM output is ever presented as a validated prediction.

## Inputs (fixed before the run)

- **Filer:** Apple Inc. (AAPL), CIK 320193, SIC 3571 Electronic
  Computers. **as_of 2026-11-27** (last close $358.54).
- **AAPL fixture:** `examples/real-aapl-2026-11-27.json` (the 10.2
  recording: EDGAR companyfacts subset, submissions, Yahoo
  closes, SIC peers, peer multiples).
- **Market-context fixture:**
  `examples/personas-team-inputs-2026-11-27.json` (this PR's
  recording, fetched once by `scripts/fetch_personas_team_inputs.py`
  via the programme's Yahoo connector): SPY 251 bars
  (2025-11-28 → 2026-11-27), EURUSD=X 260 bars
  (2025-11-27 → 2026-11-27), BTC-USD 365 bars
  (2025-11-28 → 2026-11-27). All three series end on as_of.
- **Run id:** `personas-team-aapl-2026-11-27` (fixed; no
  wall-clock anywhere, so the same fixtures re-run
  byte-identically — asserted by a test).

## How the inputs are built

`toolkit/personas_team.build_personas_team_inputs` derives the
team inputs from the two fixtures; nothing is typed, fetched,
or guessed. A metric that cannot be derived is omitted, so the
persona's check reports it UNKNOWN.

- **Closes:** the 251 AAPL Yahoo closes from the 10.2 fixture.
- **Fundamentals metrics** (from the EDGAR facts, point-in-time
  as_of): revenue growth +15.06% (FY2026 $478,834,000,000 vs
  FY2025 $416,161,000,000, read at the FY2026 year-end before
  the FY2026 10-K was filed), net margin 27.60%, free-cash-flow
  margin 28.41% (FCF $136,056,000,000 / revenue), debt/equity
  2.61 (total liabilities $292,455,000,000 / equity
  $112,093,000,000 — the same liabilities/equity convention
  the 10.2 dossier's rules baseline uses). Interest coverage
  and return on invested capital are NOT derivable: interest
  expense and income-tax expense are not in the 10.2 recording
  (it subsets to the dossier's concept map), so those two stay
  UNKNOWN.
- **ETF metrics:** SPY 1y return +6.57% (return over the
  fetched window, base 2025-11-28 — the 1y-ago date,
  2025-11-27, was Thanksgiving, a US market holiday, so the
  first bar in the window is the base). ETF-specific metrics
  (expense ratio, tracking error, AUM, premium/discount) are
  not in the data and stay UNKNOWN.
- **FX inputs:** EUR/USD, 1y change −5.43% (base 2025-11-27).
  Policy rates and inflation are not in the data and stay
  UNKNOWN.
- **Crypto metrics:** BTC price $59,866.37, all-time high
  $96,929.33 (max close in the window), 200-day mean
  $71,810.93, 30-day realised volatility 2.41% (population std
  of the last 30 daily returns, same convention as
  `technical_read`). Market cap, 24h volume and supply are not
  in the Yahoo chart data and stay UNKNOWN.
- **Earnings event:** Q4 FY2026, dated 2026-11-03 (the 10-K
  filed date — the sourced date the Q4 results entered the
  filed record). Reported revenue $114,477,000,000 and reported
  EPS $2.11 are DERIVED: the FY2026 annual filing minus the
  9-month year-to-date figure from the Q3 10-Q (revenue YTD
  $364,357,000,000; net income YTD $101,464,000,000 — the YTD
  figure is the value with the earliest `start` date among
  values sharing the quarter end). EPS uses the dossier's
  convention (net income / shares outstanding). Expected EPS
  and revenue are absent: no consensus-estimate source exists
  in any public data this programme uses, so the surprises are
  UNKNOWN — never an invented expectation.
- **Headlines:** none (no news feed in this round); sentiment
  is NO HEADLINES.

## Per-persona scores and stances

Scoring (fixed rubric, protocol §10.3): a section is **sourced**
when it carries at least one concrete value traced to an input
row or tool output; otherwise **UNKNOWN**. Stances are the fixed
rules in protocol §10.3 — no rule was chosen after seeing the
outputs.

| Persona | Score | Sourced values | Stance | Stance reason |
|---|---|---|---|---|
| us_large_cap_fundamentals | sourced | debt_to_equity, free_cash_flow_margin_pct, net_margin_pct, revenue_growth_pct | **constructive** | 3 healthy vs 1 flagged of the sourced checks (debt/equity 2.61 flags above 2.0x) |
| etf_index | sourced | index_return_1y_pct | **constructive** | SPY 1y return +6.57% — rising |
| fx_macro | sourced | pair_change_1y_pct | **cautious** | EUR/USD 1y change −5.43% — a falling EUR/USD is a strengthening dollar, cautious for AAPL's overseas earnings |
| crypto | sourced | price_usd, all_time_high_usd, mean_200d_usd, realised_vol_30d_daily_pct, drawdown_from_ath_pct, price_vs_mean_200d_pct | **cautious** | BTC price vs 200-day mean −16.63% — below the mean is a risk-off read |
| earnings | UNKNOWN | — | **UNKNOWN** | Most recent event carries no computable surprise (no consensus expectation in any public source this programme uses) |

**4/5 personas sourced.** Earnings scores UNKNOWN even though
the event carries reported values: the earnings check's output
row carries only the surprise percentages, and both are UNKNOWN
without an expectation to compare against — so the section
carries no concrete value. The reported values are recorded in
the input builder and in this card, not laundered into the
section.

## Agreement, measured

Pairwise agreement among the 4 non-UNKNOWN stances (6 pairs):

| Pair | Stances | Match |
|---|---|---|
| crypto vs etf_index | cautious vs constructive | no |
| crypto vs fx_macro | cautious vs cautious | **yes** |
| crypto vs us_large_cap_fundamentals | cautious vs constructive | no |
| etf_index vs fx_macro | constructive vs cautious | no |
| etf_index vs us_large_cap_fundamentals | constructive vs constructive | **yes** |
| fx_macro vs us_large_cap_fundamentals | cautious vs constructive | no |

**Agreement rate: 2/6 = 0.3333. Chance baseline (3 stances):
0.3333.** The rate is exactly at chance — a measurement, not a
failure and not a pass threshold. The split is informative: the
company-level reads (fundamentals, ETF/index) are constructive
while the regime reads (FX/dollar, crypto risk) are cautious.
The technical section (momentum 20d +2.76%, price vs 20d mean
+2.69%, RSI(14) 37.55, daily volatility 1.57%, 251 bars) is
reported alongside but is not a persona stance and does not
enter the agreement rate. Sentiment: NO HEADLINES.

## Verdict: WIN

All 5 protocol tests pass: the team runs on the real AAPL
fixture with no crashes, every persona section is scored (4
sourced, 1 UNKNOWN with its reason stated), the agreement rate
is computed (2/6) and reproducible (byte-identical re-runs),
and `scripts/evaluate_personas_team.py` exits 0. The win is
for AAPL as of 2026-11-27 only — one real filer is a first
proof, not a general claim.

## Known gaps

- **One real filer only.** The win is AAPL as of 2026-11-27.
  No second filer has been run; a filer with restatements,
  non-USD units, or an uncovered concept tag could break the
  input builder (it would report UNKNOWN, not fill in).
- **Earnings needs an estimates source.** The earnings persona
  cannot produce a stance until a public consensus-expectation
  source exists; the programme has none (Yahoo, CoinGecko,
  ECB, World Bank carry no estimates). Until then its stance
  is UNKNOWN on every filer.
- **The stance rules are a first, coarse reduction.** A
  persona's full section carries more than its stance: the
  fundamentals stance counts flagged vs healthy checks (a
  metric with no flag rule, e.g. free-cash-flow margin, is
  "healthy" when sourced — the flags are the assessment's only
  weak-side signal); the crypto stance is a trend proxy
  (price vs 200-day mean), not a full risk-regime model; the
  FX stance reads one pair (EUR/USD). The context personas
  (ETF, FX, crypto) assess the regime, not the company.
- **Agreement is a measurement, not a consensus.** The rate
  says how often the personas' coarse stances match; it is not
  a weighted consensus score and must never be presented as
  one (matrix row "Multi-master persona consensus" stays not
  planned — uncalibrated persona scores are not evidence).
- **No live re-fetch path.** The run is a recording frozen at
  2026-11-28; no workflow re-runs it on fresh data, and no
  headline feed exists, so sentiment is always NO HEADLINES
  until a news source is added.
- **1y returns use the fetched window.** The SPY base is
  2025-11-28 (the 1y-ago date was a market holiday); the
  returns are over the fetched 1y window, with base dates
  recorded in the eval provenance.
