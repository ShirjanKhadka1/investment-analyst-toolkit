"""Generate the committed 9.2 groundedness fixtures (Round 9, PR 9.2).

Writes ``examples/groundedness/<name>.md`` (memo text) paired with
``examples/groundedness/<name>.jsonl`` (event log), plus
``examples/groundedness/manifest.json`` (fixture inventory with the
expected verdict of each). One shared synthetic evidence set is
used by every fixture — a DEMO bank with fixed closes, fundamentals
and gate numbers — so a memo is grounded exactly when every number
it cites comes from that set.

The event logs are written with ``toolkit.events.EventRecorder``
(fixed run id per fixture, fixed clock), following the U.1 fixture
pattern in ``scripts/run_fake_agent_events.py``: re-running this
script deletes the previous outputs first, so the fixtures never
accumulate duplicate runs, and every log verifies clean.

Generation-time self-check: after writing each pair, the script
runs the 9.2 checker (``toolkit.groundedness.check_memo``) over it
and FAILS if the verdict differs from the manifest's expected
verdict. A failure means the fixture or the checker is wrong — fix
the checker first (its unit tests in ``tests/test_groundedness.py``
pin the grammar independently); never widen the grammar or edit a
fixture's expected verdict to make generation pass. The eval suite
(``scripts/evaluate_groundedness.py``) re-checks the committed
files independently; this script is the generator, not the verdict.

Usage:
  python3 scripts/generate_groundedness_fixtures.py [--output DIR]
Default output: examples/groundedness. Exit 0 when every fixture
is written, verifies clean, and matches its expected verdict.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import events, groundedness

FIXED_TS = "2026-11-28T12:00:00Z"
DEFAULT_OUTPUT = (Path(__file__).resolve().parent.parent
                  / "examples" / "groundedness")

# The one shared synthetic evidence set. Every number a grounded
# fixture memo may cite comes from these strings — nothing else.
TECHNICAL_DETAIL = ("DEMO closes: last 129.00, 30 closes, "
                    "mean 124.50, 2026-11-14")
FUNDAMENTALS_DETAIL = ("NPL 3.2%, capital adequacy 12.54%, "
                       "gross margin 42.36%, revenue 385.00, "
                       "net income 97.00")
MACRO_DETAIL = "policy rate 5.5%"
GATE_PASS_DETAIL = ("extended risk gate: 19 checks, 19 passed, "
                    "0 failed, stop 120.00, target 147.00, "
                    "reward:risk 2.0, shares 100, max loss 900.00")
GATE_BLOCK_DETAIL = ("extended risk gate: 19 checks, 18 passed, "
                     "1 failed, stop 120.00, target 147.00, "
                     "reward:risk 2.0, shares 100, max loss 900.00")
YAHOO_SOURCE = "Yahoo chart: DEMO close 129.00 on 2026-11-14"
EDGAR_SOURCE = "EDGAR 10-K: revenue 385.00, net income 97.00"
NRB_SOURCE = "NRB policy rate 5.5%"

SOURCES_BLOCK = (
    "## Sources\n"
    "- Yahoo chart: DEMO close 129.00 on 2026-11-14\n"
    "- EDGAR 10-K: revenue 385.00, net income 97.00\n"
    "- NRB policy rate 5.5%\n"
)


def _memo(thesis, risks, mind, gate, sources=SOURCES_BLOCK):
    """Assemble a memo from its five sections (headings fixed)."""
    return (
        "# DEMO memo — 2026-11-14\n\n"
        f"## Thesis\n{thesis}\n\n"
        f"## Key risks\n{risks}\n\n"
        f"## What would change my mind\n{mind}\n\n"
        f"## Gate verdict\n{gate}\n\n"
        f"{sources}"
    )


BASE_THESIS = ("DEMO closed at 129.00, above its 30-close mean of "
               "124.50. Revenue was 385.00 with net income 97.00.")
BASE_RISKS = ("NPL is 3.2% and capital adequacy is 12.54%. "
              "The policy rate is 5.5%.")
BASE_MIND = ("A close below the stop of 120.00, or NPL rising "
             "above 3.2%.")
BASE_GATE_PASS = ("Gate verdict: PASS. The extended risk gate ran "
                  "19 checks, 19 passed, 0 failed. Stop 120.00, "
                  "target 147.00, reward:risk 2.0, shares 100, "
                  "max loss 900.00.")
BASE_GATE_BLOCK = ("Gate verdict: BLOCK. The extended risk gate ran "
                   "19 checks, 1 failed. Stop 120.00, target 147.00, "
                   "reward:risk 2.0, shares 100, max loss 900.00.")


def _grounded_variants():
    """Eight fully-grounded memos (expected PASS), varied wording."""
    return {
        "grounded-01": _memo(BASE_THESIS, BASE_RISKS, BASE_MIND,
                             BASE_GATE_PASS),
        "grounded-02": _memo(
            "DEMO closed at $129.00, above its 30-close mean of "
            "124.50. Gross margin is 42.36% on revenue of 385.00.",
            "NPL is 3.2% and capital adequacy is 12.54%. The "
            "policy rate is 5.5%.",
            "A close below the stop of 120.00, or NPL rising "
            "above 3.2%.",
            BASE_GATE_PASS),
        "grounded-03": _memo(
            "DEMO closed at 129.00.",
            "NPL is 3.2%.",
            "A close below the stop of 120.00.",
            "Gate verdict: PASS. The extended risk gate ran "
            "19 checks, 19 passed, 0 failed.",
            ),
        "grounded-04": _memo(
            "DEMO closed at 129.00, above its 30-close mean of "
            "124.50.",
            "Capital adequacy is 12.54% and the policy rate is 5.5%.",
            "A close below the stop of 120.00, or a target cut "
            "below 147.00.",
            "Gate verdict: PASS. Stop 120.00, target 147.00, "
            "reward:risk 2.0, shares 100, max loss 900.00."),
        "grounded-05": _memo(
            "Revenue was 385.00 with net income 97.00. DEMO closed "
            "at 129.00.",
            "NPL is 3.2% and capital adequacy is 12.54%.",
            "Net income falling below 97.00.",
            "Gate verdict: PASS. The extended risk gate ran "
            "19 checks, 19 passed, 0 failed."),
        "grounded-06": _memo(
            "Q3 revenue was 385.00 with net income 97.00. DEMO "
            "closed at 129.00.",
            "NPL is 3.2% and capital adequacy is 12.54%.",
            "A close below the stop of 120.00.",
            BASE_GATE_PASS),
        "grounded-07": _memo(
            "DEMO closed at 129.00. DEMO is not in the S&P500.",
            "NPL is 3.2% and capital adequacy is 12.54%. The "
            "policy rate is 5.5%.",
            "A close below the stop of 120.00.",
            BASE_GATE_PASS),
        "grounded-08": _memo(
            "DEMO closed above its recent mean on higher revenue.",
            "Asset quality and the policy rate are the watch items.",
            "A close below the stop, or a rise in NPL.",
            "Gate verdict: PASS. The extended risk gate passed.",
            "## Sources\n"
            "- Yahoo chart\n"
            "- EDGAR 10-K\n"
            "- NRB policy rate\n"),
    }


def _fixture_memos():
    """Every fixture memo, by name. Expected verdicts are in MANIFEST."""
    memos = _grounded_variants()
    memos["rounded-in-01"] = _memo(
        BASE_THESIS,
        "NPL is 3.2% and capital adequacy is 12.5%. The policy "
        "rate is 5.5%.",
        BASE_MIND, BASE_GATE_PASS)
    memos["rounded-in-02"] = _memo(
        "DEMO closed at 129.0, above its 30-close mean of 124.50. "
        "Gross margin is 42.4% on revenue of 385.00.",
        BASE_RISKS, BASE_MIND, BASE_GATE_PASS)
    memos["rounded-out-01"] = _memo(
        "DEMO closed at 129.00. Gross margin is 42.9% on revenue "
        "of 385.00.",
        BASE_RISKS, BASE_MIND, BASE_GATE_PASS)
    memos["rounded-out-02"] = _memo(
        "DEMO closed at 132.00, above its 30-close mean of 124.50.",
        BASE_RISKS, BASE_MIND, BASE_GATE_PASS)
    memos["planted-01"] = _memo(
        BASE_THESIS, BASE_RISKS, BASE_MIND,
        "Gate verdict: PASS. The extended risk gate ran 19 checks, "
        "19 passed, 0 failed. Stop 120.00, target 45.00, "
        "reward:risk 2.0, shares 100, max loss 900.00.")
    memos["planted-02"] = _memo(
        "DEMO closed at 129.00, above its 30-close mean of 124.50. "
        "Revenue was 250.00 with net income 97.00.",
        BASE_RISKS, BASE_MIND, BASE_GATE_PASS)
    memos["planted-03"] = _memo(
        BASE_THESIS,
        "NPL is 7.8% and capital adequacy is 12.54%. The policy "
        "rate is 5.5%.",
        BASE_MIND, BASE_GATE_PASS)
    memos["planted-04"] = _memo(
        BASE_THESIS + " DEMO trades at 15.5x earnings.",
        BASE_RISKS, BASE_MIND, BASE_GATE_PASS)
    memos["planted-05"] = _memo(
        BASE_THESIS + " Volume was 1000000 shares.",
        BASE_RISKS, BASE_MIND, BASE_GATE_PASS)
    memos["planted-06"] = _memo(
        BASE_THESIS,
        BASE_RISKS + " The fee is 0.25.",
        BASE_MIND, BASE_GATE_PASS)
    memos["fabricated-source-01"] = _memo(
        BASE_THESIS, BASE_RISKS, BASE_MIND, BASE_GATE_PASS,
        SOURCES_BLOCK + "- Bloomberg Terminal\n")
    memos["fabricated-source-02"] = _memo(
        BASE_THESIS, BASE_RISKS, BASE_MIND, BASE_GATE_PASS,
        "## Sources\n"
        "- Yahoo chart: DEMO close 129.00 on 2026-11-14\n"
        "- EDGAR 10-Q: revenue 385.00, net income 97.00\n"
        "- NRB policy rate 5.5%\n")
    memos["missing-section-01"] = (
        "# DEMO memo — 2026-11-14\n\n"
        f"## Thesis\n{BASE_THESIS}\n\n"
        f"## What would change my mind\n{BASE_MIND}\n\n"
        f"## Gate verdict\n{BASE_GATE_PASS}\n\n"
        f"{SOURCES_BLOCK}")
    memos["missing-section-02"] = (
        "# DEMO memo — 2026-11-14\n\n"
        f"## Thesis\n{BASE_THESIS}\n\n"
        f"## Key risks\n{BASE_RISKS}\n\n"
        f"## What would change my mind\n{BASE_MIND}\n\n"
        f"## Gate verdict\n{BASE_GATE_PASS}\n")
    memos["gate-mismatch-01"] = _memo(
        BASE_THESIS, BASE_RISKS, BASE_MIND,
        "Gate verdict: PASS. The extended risk gate ran 19 checks, "
        "1 failed. Stop 120.00, target 147.00, reward:risk 2.0, "
        "shares 100, max loss 900.00.")
    memos["gate-mismatch-02"] = _memo(
        BASE_THESIS, BASE_RISKS, BASE_MIND,
        "Gate verdict: BLOCK. The extended risk gate ran 19 checks, "
        "19 passed, 0 failed. Stop 120.00, target 147.00, "
        "reward:risk 2.0, shares 100, max loss 900.00.")
    memos["gate-blocked-honest-01"] = _memo(
        BASE_THESIS, BASE_RISKS, BASE_MIND, BASE_GATE_BLOCK)
    memos["unparseable-01"] = _memo(
        BASE_THESIS, BASE_RISKS, BASE_MIND,
        "Gate verdict: PASS. The extended risk gate ran 19 checks, "
        "19 passed, 0 failed. Stop 120.00, target 147..00, "
        "reward:risk 2.0, shares 100, max loss 900.00.")
    return memos


# name -> (kind, expected verdict, gate verdict in the log, note).
MANIFEST = {
    "grounded-01": ("grounded", "PASS", "PASS",
                    "fully grounded, all numbers exact"),
    "grounded-02": ("grounded", "PASS", "PASS",
                    "currency symbol and gross margin cited"),
    "grounded-03": ("grounded", "PASS", "PASS",
                    "minimal memo, few numbers"),
    "grounded-04": ("grounded", "PASS", "PASS",
                    "plan numbers cited, no gate counts"),
    "grounded-05": ("grounded", "PASS", "PASS",
                    "fundamentals-led thesis"),
    "grounded-06": ("grounded", "PASS", "PASS",
                    "Q3 quarter label is a name, not a claim"),
    "grounded-07": ("grounded", "PASS", "PASS",
                    "S&P500 index name is a name, not a claim"),
    "grounded-08": ("grounded", "PASS", "PASS",
                    "no numeric tokens in the body at all"),
    "rounded-in-01": ("rounded-in", "PASS", "PASS",
                      "12.5% vs evidence 12.54% (0.32% off)"),
    "rounded-in-02": ("rounded-in", "PASS", "PASS",
                      ("42.4% vs evidence 42.36% (0.09% off); "
                       "129.0 formatting variant")),
    "rounded-out-01": ("rounded-out", "BLOCK", "PASS",
                       "42.9% vs evidence 42.36% (1.27% off)"),
    "rounded-out-02": ("rounded-out", "BLOCK", "PASS",
                       "132.00 vs evidence 129.00 (2.33% off)"),
    "planted-01": ("planted", "BLOCK", "PASS",
                   "planted target 45.00, evidence target 147.00"),
    "planted-02": ("planted", "BLOCK", "PASS",
                   "planted revenue 250.00, evidence 385.00"),
    "planted-03": ("planted", "BLOCK", "PASS",
                   "planted NPL 7.8%, evidence 3.2%"),
    "planted-04": ("planted", "BLOCK", "PASS",
                   "planted multiple 15.5x, no x evidence"),
    "planted-05": ("planted", "BLOCK", "PASS",
                   "planted volume 1000000"),
    "planted-06": ("planted", "BLOCK", "PASS",
                   "planted fee 0.25"),
    "fabricated-source-01": ("fabricated-source", "BLOCK", "PASS",
                             ("sources list adds Bloomberg Terminal, "
                              "absent from the log")),
    "fabricated-source-02": ("fabricated-source", "BLOCK", "PASS",
                             "sources cite EDGAR 10-Q, log has 10-K"),
    "missing-section-01": ("missing-section", "BLOCK", "PASS",
                           "no Key risks section"),
    "missing-section-02": ("missing-section", "BLOCK", "PASS",
                           "no Sources section"),
    "gate-mismatch-01": ("gate-mismatch", "BLOCK", "BLOCK",
                         "log gate BLOCK, memo claims PASS"),
    "gate-mismatch-02": ("gate-mismatch", "BLOCK", "PASS",
                         "log gate PASS, memo claims BLOCK"),
    "gate-blocked-honest-01": ("gate-blocked-honest", "PASS", "BLOCK",
                               ("log gate BLOCK, memo faithfully "
                                "claims BLOCK")),
    "unparseable-01": ("unparseable", "BLOCK", "PASS",
                       "target written 147..00, fail-closed"),
}


def build_events(path, run_id, gate_verdict):
    """Write one fixture's event log and return the events."""
    path = Path(path)
    if path.exists():
        path.unlink()
    gate_detail = (GATE_PASS_DETAIL if gate_verdict == "PASS"
                   else GATE_BLOCK_DETAIL)
    recorder = events.EventRecorder(path, run_id, now=FIXED_TS)
    recorder.record("run_started", team="groundedness-fixture",
                    symbol="DEMO")
    recorder.record("plan", steps=["technical", "fundamentals",
                                   "macro", "gate"])
    recorder.record("step_started", step="technical")
    recorder.record("tool_call", step="technical",
                    tool="technical_read")
    recorder.record("tool_result", step="technical",
                    tool="technical_read", status="ok",
                    n_points=30, detail=TECHNICAL_DETAIL)
    recorder.record("step_started", step="fundamentals")
    recorder.record("tool_call", step="fundamentals",
                    tool="fundamentals_check")
    recorder.record("tool_result", step="fundamentals",
                    tool="fundamentals_check", status="ok",
                    detail=FUNDAMENTALS_DETAIL)
    recorder.record("step_started", step="macro")
    recorder.record("tool_call", step="macro", tool="macro_checklist")
    recorder.record("tool_result", step="macro", tool="macro_checklist",
                    status="ok", detail=MACRO_DETAIL)
    recorder.record("evidence_ref", step="technical", symbol="DEMO",
                    source=YAHOO_SOURCE, last_date="2026-11-14",
                    n_points=30)
    recorder.record("evidence_ref", step="fundamentals", symbol="DEMO",
                    source=EDGAR_SOURCE)
    recorder.record("evidence_ref", step="macro", symbol="DEMO",
                    source=NRB_SOURCE)
    recorder.record("step_started", step="gate")
    recorder.record("tool_call", step="gate", tool="risk_gate")
    recorder.record("tool_result", step="gate", tool="risk_gate",
                    status="ok", detail=gate_detail)
    recorder.record("gate_result", gate="extended_risk_gate",
                    verdict=gate_verdict,
                    reason=("extended risk gate passed"
                            if gate_verdict == "PASS"
                            else "extended risk gate blocked: "
                            "sector concentration"))
    for section in groundedness.REQUIRED_SECTIONS:
        recorder.record("memo_section", section=section)
    recorder.record("run_finished", status="ok", symbol="DEMO")
    return events.load_events(path)


def generate(output):
    """Write all fixtures + manifest; return (names, problems)."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for stale in output.glob("*.md"):
        stale.unlink()
    for stale in output.glob("*.jsonl"):
        stale.unlink()
    memos = _fixture_memos()
    assert set(memos) == set(MANIFEST), (
        "fixture memos and manifest disagree: "
        f"{set(memos) ^ set(MANIFEST)}")
    manifest = []
    problems = []
    for name in sorted(MANIFEST):
        kind, expected, gate_verdict, note = MANIFEST[name]
        memo_path = output / f"{name}.md"
        log_path = output / f"{name}.jsonl"
        memo_path.write_text(memos[name], encoding="utf-8")
        rows = build_events(log_path, f"groundedness-{name}",
                            gate_verdict)
        chain_problems = events.verify_events(log_path)
        if chain_problems:
            problems.extend(f"{name}: {p}" for p in chain_problems)
            continue
        result = groundedness.check_memo(memos[name], rows)
        if result["verdict"] != expected:
            problems.append(
                f"{name}: checker says {result['verdict']}, manifest "
                f"expects {expected}: {result['reasons']}")
            continue
        manifest.append({"name": name, "kind": kind,
                         "expected": expected,
                         "gate_verdict": gate_verdict, "note": note})
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return [m["name"] for m in manifest], problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="directory for the fixture files "
                        "(default: examples/groundedness)")
    args = parser.parse_args(argv)
    names, problems = generate(args.output)
    if problems:
        print("FAIL: fixture generation found problems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"wrote {len(names)} fixtures to {args.output}, "
          "all logs verify clean, all verdicts match the manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
