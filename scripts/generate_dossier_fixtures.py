"""Regenerate the Round 9.3 dossier fixtures, byte-identically.

Writes (never generated at eval time — the eval reads these files):
- examples/dossier/synthetic-edgar-facts.json — synthetic
  companyfacts for DEMO Corporation (fixed CIK, fixed filing dates,
  known concept values). Includes a 10-Q filed AFTER the early
  as-of date (point-in-time discipline: it must never leak into the
  early dossier) and one concept absent entirely (R&D expense ->
  UNKNOWN). The company is synthetic; no real filer is named.
- examples/dossier/bnt-runbook-baseline.json — the Bottlers Nepal
  worked example (finance-research-agents
  docs/worked-example/bottlers-nepal-terai-bnt-research-note.md)
  as the runbook baseline: its section list, its numeric-claim
  count and unparseable-token count under the 9.2 checker grammar
  (via toolkit.groundedness.extract_numbers), and its stated
  limits. Needs --agents-repo pointing at a finance-research-agents
  checkout (default: ../finance-research-agents next to this repo);
  fails loudly if the note is absent rather than keeping stale
  values.

Usage:
  python3 scripts/generate_dossier_fixtures.py [--agents-repo PATH]
      [--check]
--check exits non-zero when the committed files differ from a fresh
regeneration (CI-style guard, same pattern as
generate_groundedness_fixtures.py).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit.groundedness import extract_numbers

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "examples" / "dossier"
EDGAR_FIXTURE = FIXTURE_DIR / "synthetic-edgar-facts.json"
BNT_BASELINE = FIXTURE_DIR / "bnt-runbook-baseline.json"
BNT_NOTE = ("docs/worked-example/"
            "bottlers-nepal-terai-bnt-research-note.md")

# DEMO Corporation — synthetic filer. All values USD. The 10-K is
# FY2025 (end 2025-12-31, filed 2026-02-14); the 10-Q is Q1 2026
# (end 2026-03-31, filed 2026-05-10) and must never leak into a
# dossier as of 2026-03-31. "Revenues" carries only a 2024 value so
# the freshest-tag logic must pick the 2025
# RevenueFromContractWithCustomerExcludingAssessedTax value.
# ResearchAndDevelopmentExpense is absent entirely -> UNKNOWN.
DEMO_CIK = "0001234567"


def _entry(value, end, filed, form, fy, fp="FY"):
    return {"val": value, "end": end, "filed": filed, "form": form,
            "accn": "0001234567-26-000001", "fy": fy, "fp": fp}


def synthetic_edgar_facts():
    """DEMO Corporation companyfacts (synthetic, fixed)."""
    us_gaap = {}

    def tag(name, entries):
        us_gaap[name] = {"label": name,
                         "description": "Synthetic demo fixture.",
                         "units": {"USD": entries}}

    tag("RevenueFromContractWithCustomerExcludingAssessedTax", [
        _entry(385000000, "2025-12-31", "2026-02-14", "10-K", 2025),
        _entry(100000000, "2026-03-31", "2026-05-10", "10-Q", 2026, "Q1"),
    ])
    tag("Revenues", [
        _entry(350000000, "2024-12-31", "2025-02-14", "10-K", 2024),
    ])
    tag("NetIncomeLoss", [
        _entry(97000000, "2025-12-31", "2026-02-14", "10-K", 2025),
    ])
    tag("GrossProfit", [
        _entry(210000000, "2025-12-31", "2026-02-14", "10-K", 2025),
    ])
    tag("OperatingIncomeLoss", [
        _entry(120000000, "2025-12-31", "2026-02-14", "10-K", 2025),
    ])
    tag("Assets", [
        _entry(900000000, "2025-12-31", "2026-02-14", "10-K", 2025),
    ])
    tag("Liabilities", [
        _entry(400000000, "2025-12-31", "2026-02-14", "10-K", 2025),
    ])
    tag("StockholdersEquity", [
        _entry(500000000, "2025-12-31", "2026-02-14", "10-K", 2025),
    ])
    tag("CashAndCashEquivalentsAtCarryingValue", [
        _entry(80000000, "2025-12-31", "2026-02-14", "10-K", 2025),
    ])
    tag("NetCashProvidedByOperatingActivities", [
        _entry(150000000, "2025-12-31", "2026-02-14", "10-K", 2025),
    ])
    tag("PaymentsToAcquirePropertyPlantAndEquipment", [
        _entry(40000000, "2025-12-31", "2026-02-14", "10-K", 2025),
    ])
    tag("CommonStockSharesOutstanding", [
        _entry(50000000, "2025-12-31", "2026-02-14", "10-K", 2025),
    ])
    return {"cik": DEMO_CIK, "entityName": "Demo Corporation",
            "facts": {"us-gaap": us_gaap}}


def bnt_baseline(agents_repo):
    """Runbook baseline from the Bottlers Nepal worked example."""
    note_path = Path(agents_repo) / BNT_NOTE
    if not note_path.exists():
        raise SystemExit(
            f"BNT note not found: {note_path} "
            "(pass --agents-repo pointing at finance-research-agents)")
    text = note_path.read_text(encoding="utf-8")
    claims, unparseable = extract_numbers(text)
    sections = [line.strip() for line in text.splitlines()
                if line.startswith("## ")]
    return {
        "note": f"finance-research-agents/{BNT_NOTE}",
        "company": "Bottlers Nepal (Terai) — NEPSE: BNT",
        "sections": sections,
        "numeric_claim_count": len(claims),
        "unparseable_token_count": len(unparseable),
        "has_event_log": False,
        "stated_limits": [
            "Verified-inputs dataset plus model skeleton; no DCF claimed.",
            "The ~Rs 81M bridge gap is unresolved.",
            "EM beverage beta anchor vintage unverified.",
        ],
        "note_counts_method": (
            "toolkit.groundedness.extract_numbers over the note text; "
            "the note was never written to the checker grammar, so its "
            "202 numeric claims have no event log to trace against."),
    }


def _dump(path, data):
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-repo", default=None,
                        help="finance-research-agents checkout path")
    parser.add_argument("--check", action="store_true",
                        help="fail if committed files differ")
    args = parser.parse_args(argv)
    agents_repo = args.agents_repo or str(ROOT.parent
                                          / "finance-research-agents")
    edgar_text = _dump(EDGAR_FIXTURE, synthetic_edgar_facts())
    baseline = bnt_baseline(agents_repo)
    baseline_text = _dump(BNT_BASELINE, baseline)
    if args.check:
        problems = []
        for path, text in ((EDGAR_FIXTURE, edgar_text),
                           (BNT_BASELINE, baseline_text)):
            if not path.exists() or path.read_text(
                    encoding="utf-8") != text:
                problems.append(str(path))
        if problems:
            print("FIXTURE MISMATCH (regenerate with "
                  "scripts/generate_dossier_fixtures.py):")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("fixtures match regeneration")
        return 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    EDGAR_FIXTURE.write_text(edgar_text, encoding="utf-8")
    BNT_BASELINE.write_text(baseline_text, encoding="utf-8")
    print(f"wrote {EDGAR_FIXTURE} "
          f"({len(edgar_text)} bytes)")
    print(f"wrote {BNT_BASELINE} "
          f"({len(baseline_text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
