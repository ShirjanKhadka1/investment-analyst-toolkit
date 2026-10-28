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

## House rules

Research only, not investment advice; never a buy/sell recommendation.
No credentials, no private data, no paywalled scraping.
