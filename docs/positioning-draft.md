# Positioning draft — NEPSE and world market, stated separately (Step 5, revised 8 Nov 2026)

**DRAFT — NOT APPLIED.** This file proposes wording only. Nothing
here has been applied to the README, the site (`docs/index.html`),
either repo description, or the topics. Applying it needs the user's
approval.

## Why this revision exists

The user looked at the two repo pages and asked: "free open nepse
only?" The round-1 draft answered with one blended sentence
("market-agnostic engine, first-class NEPSE support") that still
lets a reader take the programme as NEPSE-only — or, read the other
way, as claiming the whole programme is global. This revision states
the two sides SEPARATELY, in every place a reader can land, with no
blended wording that could read as either.

Rule for every statement below: name the NEPSE side and the world
side as two facts, never one blended claim. "NEPSE-first" is retired
as a headline: NEPSE is the native, tested, default market — that is
a fact about the NEPSE side, not a summary of the whole programme.

---

## (a) Separate positioning statements

### NEPSE (native, first-class) — the statement

> NEPSE is the native market. Sourced Nepal frictions (broker slabs,
> SEBON fee, DP charge, CGT split), effective-dated rules (trading
> week, circuit limits, pre-open band, index breaker, settlement —
> each with the date it changed), a Time Machine that clips by the
> rule in force on each bar's date, a 9-agent NEPSE team plus 8
> sector agents, and a rules mode that runs with zero LLM and zero
> keys. NEPSE data is CSV-only by design: no live feed is invented.

### World market (global equities, ETFs/indices, FX, crypto) — the statement

> The world side is a market-agnostic engine, not a NEPSE port.
> Seven sourced market profiles (NEPSE, US, India, UK, crypto, FX,
> generic), SEC EDGAR fundamentals (ticker to CIK to XBRL facts),
> keyless Yahoo/CoinGecko/ECB/World Bank connectors, a 5-persona
> global rules team, and walk-forward models that publish "no edge"
> when there is none (SPY, tested). Every global number carries its
> source, its period, and its tested/experimental label.

### For the toolkit README opening (replaces lines 1–14)

Keep the badges and the one-command demo. Replace the intro paragraph
with the two statements, in order:

> A free terminal that turns any daily-bars CSV into indicators,
> setups, honest Time-Machine odds and a risk gate — with every
> number reproducible.
>
> **NEPSE, native:** sourced frictions, effective-dated rules, the
> Time Machine and a zero-LLM rules mode — NEPSE is the tested,
> default market, and NEPSE data is CSV-only by design.
>
> **World markets, supported:** US, India, UK, crypto and FX via
> sourced market profiles, SEC EDGAR fundamentals and keyless public
> connectors — labelled tested or experimental in
> `docs/global-evidence.md`.

### For the site (`docs/index.html`)

- `<title>`: "Investment Analyst Toolkit — free, open research
  terminal: NEPSE native, world markets supported"
- Meta description and hero paragraph: the two statements above, in
  the same order, NEPSE first, world second, separated — not one
  blended sentence.

### For the toolkit repo description (replaces the current one)

> Free, open research engine. NEPSE, native: sourced frictions,
> effective-dated rules, Time Machine, zero-LLM rules mode.
> World: global equities, ETFs, FX, crypto — market profiles, SEC
> EDGAR, 5 global personas. Zero keys, zero dependencies. Research
> only.

### For the finance-research-agents repo description (replaces the current one)

> Free finance research agent pack and skill library — 36 agents,
> 12 runbooks. NEPSE: 9-agent NEPSE team + 8 sector agents.
> World: 18 market-agnostic agents (World Market Researcher,
> Investment Researcher, Valuation Analyst and 15 more) + a global
> rules team in the companion toolkit. Research only.

(The 36 split 18 NEPSE-specific / 18 market-agnostic — counted from
the roster table in that repo's README, 8 Nov 2026.)

### Proposed topics (add; do not remove the Nepal ones)

Toolkit — add: `global-markets`, `etf`, `forex`, `crypto`,
`sec-edgar`. Keep: `nepse`, `nepal`, and all existing topics.
Agents — add: `global-markets`, `etf`, `forex`, `crypto`. Keep all
existing topics.

## What stays exactly as it is

- "Research and education only — not investment advice, never a
  buy/sell/hold recommendation." (unchanged, everywhere)
- "Zero keys, zero dependencies." (unchanged — still true: the
  toolkit is standard-library only; EDGAR, Yahoo and CoinGecko are
  keyless)
- The n=0 track-record badge and its honest wording (unchanged)
- NEPSE as the default market in code and docs (unchanged)

---

## (b) World-market implementation list — what can be built FROM these two repos

Each item maps to existing code, or is named explicitly as a gap.
Nothing here is a promise; "existing" means the code is on the
`global-markets` branch (PR #9) or on main, as noted.

| # | World-market capability | Where it lives | Status |
|---|---|---|---|
| W1 | Market profiles for US, India, UK, crypto, FX, generic (weekdays, session, limits, lot size, settlement, fees/taxes, each with source URL + as-of) | `toolkit/market_profiles.py` (PROFILES, `get_profile`, `profile_config`) | Existing (PR #9) |
| W2 | SEC EDGAR fundamentals: ticker to CIK, submissions, XBRL companyfacts/concept, 5 req/s throttle, declared User-Agent, URL allowlist by parsed hostname | `toolkit/edgar.py` | Existing (PR #9; allowlist hardened this round) |
| W3 | Keyless public connectors: Yahoo Finance chart API, CoinGecko, ECB eurofxref, World Bank API v2 | `toolkit/connectors.py`, `toolkit/free_data.py` | Existing; Yahoo reliability from GitHub runners UNVERIFIED — probe staged at `ops/ready-for-browser/yahoo-ci-probe.yml`, not yet run |
| W4 | Market-agnostic quant: indicators, portfolio weights, covariance, efficient frontier | `toolkit/indicators.py`, `toolkit/quant.py`, `toolkit/portfolio.py` | Existing (main) |
| W5 | Walk-forward predictive layer that publishes "no edge" when there is none | `toolkit/predictive.py` + `docs/global-evidence.md` (SPY fixture, 251 bars, naive wins, MAE 4.9124) | Existing (PR #9) |
| W6 | Global rules-mode team: 5 personas — `us_large_cap_fundamentals`, `etf_index`, `fx_macro`, `crypto`, `earnings` | `toolkit/rules_mode.py` v1.4.0 (`GLOBAL_TEAM`, `list_global_personas`, `run_global_rules_team`) | Existing (PR #9) |
| W7 | Time Machine under any profile: per-market circuit clipping, lot-size enforcement, sourced fees only, unmodelled fees listed (never silently zero) | `toolkit/timemachine.py` (`simulate_with_profile`); per-bar dated limits this round | Existing (PR #9 + this round) |
| W8 | 16-check risk gate under any profile, with the trading rules in force on the plan's date | `toolkit/position_plan.py` (`risk_gate`, `profile_config(..., on=date)`) | Existing (main + this round) |
| W9 | Read-only MCP server over caller-supplied data (no execution) | `toolkit/mcp_server.py` | Existing (main) |
| W10 | Decision memory / scoreboard: calls logged before their outcome, settled after their horizon | `toolkit/decision_log.py`, `toolkit/paper_ledger.py`, `docs/track-record.md` (n=0) | Existing (main); global calls must pass `exchange` explicitly (see C3) |
| W11 | Agent pack, world side: 18 market-agnostic agents — World Market Researcher, Investment Researcher, Valuation Analyst, Forensic, Financial, FP&A, Debate, Accuracy Auditor and 10 more — plus the `macro-to-company` runbook (World → Nepal → company) | `finance-research-agents` (`agents/`, `runbooks/macro-to-company.md`) | Existing (main) |
| W12 | 7-ticker watchlist (SPY, QQQ, AAPL, VT, 7203.T, EURUSD=X, BTC-USD) with fixed-list logging rules | `docs/watchlist-proposal.md` | Draft only — not active, no daily run |

### Gaps — named, not hidden

- **G1 — One stock-price source.** Yahoo is the only verified global
  equities source; Stooq was removed after three failed
  verifications. If Yahoo fails from runners, global price data has
  no fallback. Fallback is PROPOSED only (opt-in free-key provider
  via repo secret, or CSV snapshots) — not implemented.
- **G2 — No India/UK ticker verified end to end.** The profiles
  exist; only SPY/AAPL have live fixtures. No India/UK ticker is
  claimed as tested.
- **G3 — No symbol-to-market auto-detection.** Nothing infers the
  market from a symbol: `7203.T` does not auto-select Japan, `AAPL`
  does not auto-select US. The caller passes the profile/exchange
  explicitly (`simulate_with_profile`, `run_global_rules_team`,
  terminal `--exchange`). A wrong profile is a caller error the code
  does not catch (see C1).
- **G4 — No NEPSE live feed, by design.** The world side has no
  "native" data source either: Yahoo is unofficial/best-effort, not
  a licensed feed.
- **G5 — NEPSE T+1 / intraday: future only.** SEBON's blueprint
  places T+1 in 2026–2027; nothing is modelled until a dated
  announcement exists.
- **G6 — The agents README does not mention the 5 global
  rules-mode personas.** The world side of the pack is
  under-described there (see sweep item 7).

---

## (c) Conflict sweep — 8 Nov 2026

Every claim in the toolkit README/site/description and the agents
repo README/description was checked against the code on the branch.
"Verified clean" means the claim matches the code as read on 8 Nov
2026.

### Conflicting statements (fix the wording)

| # | Location | Current wording | Why it conflicts | Corrected wording |
|---|---|---|---|---|
| 1 | Toolkit repo description (live, via API) | "...36 analyst agents..." | The 36 agents live in `finance-research-agents`, not this repo. This repo has a 6-section rules mode + 5 global personas. | Remove the claim; use the separated toolkit description in (a). |
| 2 | `docs/index.html` meta description + hero paragraph | Same "...36 analyst agents..." text as the repo description | Same error, in the site's two most-read lines. | Same correction as 1; use the separated site wording in (a). |
| 3 | `docs/index.html` `<title>` | "free, open NEPSE-first research terminal" | Not false — NEPSE is first/default — but beside the description it reads NEPSE-only, which is the question the user asked. | "free, open research terminal: NEPSE native, world markets supported". |
| 4 | Toolkit README opening (lines 1–14) | No global mention; the worked example is "NEPSE-style prices in NPR" | The engine is market-agnostic, but the opening reads NEPSE-only. | The two-statement README opening in (a). |
| 5 | This draft, round 1 | "9-agent rules team" | The toolkit's rules mode has 6 sections (`run_rules_team`: technical, fundamentals, sentiment, broker_flow, macro, corporate_actions) mirroring the 9-agent NEPSE team — "9-agent rules team" overstates the toolkit by 3. | "6-section rules mode mirroring the 9-agent NEPSE team, plus 5 global personas". |
| 6 | Agents README, NEPSE Fees & Tax agent (§ NEPSE sector agents) | "circuit ±15%, settlement T+2 — every figure from the user-supplied Nepal frictions of 28 Oct 2026" | Outdated single values: circuit is ±15% only from 20 Apr 2026 (±10% before); settlement T+2 only from Jan 2021 (T+3 before). | "circuit ±15% from 20 Apr 2026 (±10% before), settlement T+2 (T+3 before Jan 2021) — effective-dated rules, see the toolkit's `market_profiles.py`". |
| 7 | Agents README (whole file) | No mention of the 5 global rules-mode personas | The world side of the pack is under-described: only the World Market Researcher agent names it. | Add a section naming the 5 personas (`toolkit/rules_mode.py` v1.4.0) and the `macro-to-company` runbook. |
| 8 | Agents repo description (live, via API) | "36 agents, 12 runbooks" | True, but says nothing about the scope split — a reader cannot tell it is not NEPSE-only. | The separated agents description in (a): 18 NEPSE / 18 market-agnostic. |

### Verified clean (no change needed)

- Toolkit README comparison table: "±15% circuit limits since 20
  Apr 2026 — 10% before — T+2" (fixed this round).
- "Zero keys, zero dependencies" — toolkit is standard-library
  only; EDGAR, Yahoo, CoinGecko are keyless.
- Agents README "36 agents" — counted 36 rows in the roster table;
  "12 runbooks" — counted 12; "8 templates" — counted 8.
- Agents README "track record n=0" — matches
  `docs/track-record.md` ("n=0 — no calls settled yet").
- Agents README "16 checks" (hypothetical position review) —
  matches `GATE_CHECKS` (16) in `toolkit/position_plan.py`.
- Agents README "No live NEPSE feed is provided or claimed
  anywhere in this repo" — true; NEPSE stays CSV-only.

### Conflicting code paths (and the tests that prove no conflict)

| # | Path | Conflict | Defence + proving test |
|---|---|---|---|
| C1 | `toolkit/timemachine.py` `simulate_nepse()` | Applies NEPSE frictions to ANY bars passed in — a global CSV run through it is silently mispriced as NEPSE. Nothing detects the market from the bars. | The function name, its docstring, and the profile router `simulate_with_profile`. Tests: `test_simulate_us_no_clip_unmodelled_brokerage` (US profile: no clip, brokerage unmodelled) and `test_simulate_nepse_profile_delegates_byte_identical`. Residual: the caller must choose the right entry point; see gap G3. |
| C2 | `toolkit/connectors.py` `evaluate_freshness(kind=None)` | Defaults to Mon–Fri (the US calendar) — a NEPSE series judged without a kind gets the US week. Mirror of C1. | `kind` is required for NEPSE; `data_health.py` passes `kind="nepse_csv_daily"`. Test: `test_evaluate_freshness_crypto_counts_weekends` pins kind=None to Mon–Fri. |
| C3 | `toolkit/decision_log.py`, `toolkit/paper_ledger.py`, `toolkit/report.py`, `toolkit/terminal.py` (`--exchange`) | `exchange` defaults to `"NEPSE"` — a global call logged without an exchange is labelled NEPSE. Label-only, but a mislabel. | The label is caller-controlled and overridable. Test (added this round): `test_exchange_label_defaults_to_nepse_but_is_caller_controlled` — default is NEPSE, explicit `"NYSE"` is honoured. Global work must pass its exchange explicitly. |
| C4 | `toolkit/market_profiles.py` `default_profile()` | The default market is NEPSE — any caller using the default gets NEPSE rules. | By design and documented in the function docstring ("The default market is NEPSE — existing behaviour is unchanged"). Test: `test_default_profile` asserts the key is `"nepse"`. |
| C5 | `toolkit/rules_mode.py` `run_rules_team(symbol, exchange, ...)` | None — this one is correct: `exchange` is required, with no default. A call without it raises ValueError instead of silently assuming NEPSE. | `run_rules_team` raises ValueError when symbol or exchange is missing. The global team (`run_global_rules_team`) likewise requires `market`. |
| C6 | `toolkit/connectors.py` `TRADING_WEEKDAYS` | Was a single flat table (Sun–Thu for NEPSE) — a pre-change bar judged by today's rule. | Fixed this round: `evaluate_freshness` resolves the rule in force on EACH day via the profile. Test: `test_evaluate_freshness_nepse_week_is_effective_dated`. |
