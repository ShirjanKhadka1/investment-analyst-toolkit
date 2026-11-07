# Good First Issues

Small, well-scoped tasks a new contributor can pick up. This is a list, not
a promise: no maintainer response time is promised, and no GitHub issue has
been created for these items. Open an issue first and ask to be assigned
before starting, so two people do not do the same task.

## 1. Add one new ratio, with a test and a docstring citing its definition

This is the contribution type named in `CONTRIBUTING.md`: one new ratio
function, one docstring stating its definition and its edge-case behaviour,
and at least one edge-case test. Follow the style of the existing functions
in `toolkit/ratios.py` and run `python -m pytest tests/ -v` — all tests must
pass.

Acceptance: the ratio's definition is cited in the docstring; edge cases
(zero or negative inputs, where applicable) raise or return as the existing
functions do, never a silently plausible number; no new dependency.

## 2. Add one worked example in `examples/`, with a test

Demo data only, no network, no key — following `examples/demo_company.py`.
The example must print its inputs next to its outputs, and a test must
assert the printed output (see `tests/test_demo_assets.py` for the pattern
of pinning an example to a live run).

Acceptance: the example runs with `python examples/<name>.py` from a clean
clone; its outputs are labelled demo data; a test pins its key numbers.

## 3. Proofread and extend `README.ne.md`

The Nepali front door (hook, install, first run, what it is/is not,
licence). Check the finance terms against the English README and note any
term kept in English; extend the translation to one more README section
only if the English wording is settled.

Acceptance: every Nepali sentence matches the English sentence it
translates; no invented claim; the list of terms kept in English at the
bottom of the file is updated if a new term is kept.

## House rules

Research only, not investment advice; never a buy/sell recommendation.
No credentials, no private data, no paywalled scraping.
