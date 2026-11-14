"""Stooq runner probe (Round 7.1) — STAGED, not deployed.

Purpose: measure whether Stooq's daily CSV endpoint answers from a
GitHub-hosted runner. Stooq was removed 8 Nov 2026 after three failed
verifications from a developer VM, and from this VM it still answers
with a JavaScript-challenge HTML page instead of CSV (checked
14 Nov 2026) — so the re-test must happen on a runner, the same way
the Yahoo probe did (ops/ready-for-browser/yahoo-ci-probe.yml).

Manual dispatch ONLY (workflow_dispatch, no schedule, no
push/pull_request triggers): this is a measurement, not a gate.
The probe always exits 0 and prints a per-symbol OK/FAIL line with
the exact error text plus a summary count — a failing probe is
data, not a broken build. Do NOT turn it into a required check.

Deploy: copy ops/ready-for-browser/stooq-ci-probe.yml to
.github/workflows/stooq-ci-probe.yml (browser route — the API token
has no workflow scope), then dispatch it from the Actions tab and
read the log. A workflow_dispatch workflow only registers once its
file is on the default branch.

Local verification: set STOOQ_PROBE_FIXTURE to a Stooq-format CSV
file (e.g. tests/fixtures/stooq_aapl_daily_2026-11-07.csv) and the
probe runs its exact commands against that file instead of the
network — that is how CI tests this script offline. It proves the
script works; it does NOT prove Stooq answers from a runner.
Only a dispatched run on main proves that.
"""
from __future__ import annotations

import os
import sys

# A script run as `python3 scripts/probe_stooq.py` has scripts/ (not
# the repo root) on sys.path; make the repo root importable so the
# exact command the staged workflow runs works from a clean checkout.
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toolkit.free_data import fetch_stooq_prices, parse_stooq_csv

DEFAULT_SYMBOLS = ["SPY", "AAPL"]


def run_probe(fetch_fn, symbols):
    """Probe each symbol with ``fetch_fn(symbol) -> pairs``.

    Returns ``(lines, ok_count)`` where lines are the exact lines
    printed: one ``<symbol> OK n=… last=… close=…`` or
    ``<symbol> FAIL <error>`` per symbol, then a
    ``PROBE <ok>/<n> OK`` summary. Pure apart from ``fetch_fn``,
    so tests run it with fake fetchers and never touch the network.
    """
    lines = []
    ok = 0
    for symbol in symbols:
        try:
            pairs = fetch_fn(symbol)
            lines.append(
                f"{symbol} OK n={len(pairs)} "
                f"last={pairs[-1][0]} close={pairs[-1][1]}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 — the error IS the data
            lines.append(f"{symbol} FAIL {type(exc).__name__}: {exc}")
    lines.append(f"PROBE {ok}/{len(symbols)} OK")
    return lines, ok


def _fixture_fetch_fn(path):
    """A fetch function that answers every symbol from one Stooq CSV
    file. Local-verification mode only — see the module docstring."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    pairs, _volumes = parse_stooq_csv(text)
    return lambda symbol: list(pairs)


def main(argv=None):
    symbols = [s.strip() for s in
               os.environ.get("PROBE_SYMBOLS", ",".join(DEFAULT_SYMBOLS))
               .split(",") if s.strip()]
    fixture = os.environ.get("STOOQ_PROBE_FIXTURE")
    if fixture:
        fetch_fn = _fixture_fetch_fn(fixture)
    else:
        fetch_fn = fetch_stooq_prices
    lines, _ok = run_probe(fetch_fn, symbols)
    for line in lines:
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
