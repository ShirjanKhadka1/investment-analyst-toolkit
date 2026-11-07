# Contributing

1. Open an issue first for anything bigger than a typo, or comment on an
   existing issue asking to be assigned.
2. Fork, branch `fix/issue-<number>-<slug>`, smallest fix, test included.
3. Run `python -m pytest tests/ -v` — all tests must pass.
4. Run `python scripts/qa.py` before opening a PR (install pinned dev
   tools first: `pip install -r requirements-dev.txt`). It runs lint,
   typecheck, coverage, the look-ahead mutation check, licence, secret,
   link, README-claim and fresh-clone gates in order and stops at the
   first failure — a PR that fails `qa.py` is not ready.
5. Every new function needs a docstring stating its definition and its
   edge-case behaviour, and at least one edge-case test.
