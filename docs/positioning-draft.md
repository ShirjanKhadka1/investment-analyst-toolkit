# Positioning draft — market-agnostic engine, first-class NEPSE support (Step 5)

**DRAFT — NOT APPLIED.** This file proposes new wording only. Nothing
here has been applied to the README, the site (`docs/index.html`), or
the repo description/topics. Applying it needs the user's approval.

## What changes and why

Today the toolkit calls itself "NEPSE-first" everywhere. The engine
is now market-agnostic (Step 1 profiles for NEPSE, US, India, UK,
crypto, FX, generic; Step 2 EDGAR; Step 3 global rules team; Step 4
SPY evidence) while NEPSE behaviour stays byte-identical and NEPSE
stays the default market. The wording should say that: global engine,
NEPSE native — not "NEPSE-only", and not "global with NEPSE as an
afterthought".

## Proposed repo description (replaces the current one)

> Free, open market-agnostic research engine — global equities, ETFs,
> FX and crypto, with first-class NEPSE support: honest backtests
> (Time Machine), point-in-time data, walk-forward models that say
> "no edge" when there is none, 9-agent rules team + 5 global
> personas, MCP server. Zero keys, zero dependencies. Research only.

Notes on the current description, which this draft also corrects:

- It says "36 analyst agents". That is wrong for this repo: the 36
  agents live in the separate `finance-research-agents` repo. This
  repo has the 9-agent NEPSE rules team and 5 global personas
  (rules mode, zero LLM). The draft says so honestly.
- "NEPSE-first" becomes "first-class NEPSE support": NEPSE is still
  the default market and the tested market; it is no longer the
  only market the words allow.

## Proposed README opening (replaces lines 1–14)

Keep the badges and the one-command demo. Replace the intro paragraph
with:

> A free terminal that turns any daily-bars CSV into indicators,
> setups, honest Time-Machine odds and a risk gate — for any market:
> NEPSE, US, India, UK, crypto, FX — with every number reproducible.
> NEPSE is the native, tested market; every other market is labelled
> tested or experimental in `docs/global-evidence.md`.

## Proposed site wording (`docs/index.html`)

- `<title>`: "Investment Analyst Toolkit — free, open market-agnostic
  research terminal (first-class NEPSE support)"
- Meta description and the hero paragraph: the repo-description text
  above, verbatim.

## Proposed topics (add; do not remove the Nepal ones)

Add: `global-markets`, `etf`, `forex`, `crypto`, `sec-edgar`.
Keep: `nepse`, `nepal`, and all existing topics.

## What stays exactly as it is

- "Research and education only — not investment advice, never a
  buy/sell/hold recommendation." (unchanged, everywhere)
- "Zero keys, zero dependencies." (unchanged — still true: the
  toolkit is standard-library only; EDGAR, Yahoo and CoinGecko are
  keyless)
- The n=0 track-record badge and its honest wording (unchanged)
- NEPSE as the default market in code and docs (unchanged)
