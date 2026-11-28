"""Tests for the 9.2 groundedness checker (Round 9, PR 9.2).

The checker is a pure function over (memo text, event log). These
tests pin its fixed number grammar, rounding tolerance, structure
rule, fabricated-source rule and gate-agreement rule with
known-answer cases — including the exact tolerance boundary (1%
relative passes, 1.01% blocks) and the fail-closed unparseable
rule. No network, no LLM, no fixtures from disk: every event log
here is built inline.
"""

import pytest

from toolkit import groundedness
from toolkit.groundedness import check_memo, collect_evidence, extract_numbers

FIXED_TS = "2026-11-28T12:00:00Z"


def _event(event_type, seq, **fields):
    event = {"schema_version": "1.0.0", "run_id": "test-run",
             "seq": seq, "ts": FIXED_TS, "type": event_type}
    event.update(fields)
    return event


def _evidence_log(gate_verdict="PASS"):
    """Small log with the evidence numbers the memos below cite."""
    return [
        _event("tool_result", 0, tool="technical_read", status="ok",
               n_points=30,
               detail="DEMO closes: last 129.00, 30 closes, "
                      "mean 124.50, 2026-11-14"),
        _event("tool_result", 1, tool="fundamentals_check",
               status="ok",
               detail="NPL 3.2%, capital adequacy 12.54%, "
                      "revenue 385.00"),
        _event("evidence_ref", 2, source="Yahoo chart: DEMO close "
               "129.00 on 2026-11-14"),
        _event("evidence_ref", 3, source="EDGAR 10-K: revenue 385.00, "
               "net income 97.00"),
        _event("gate_result", 4, gate="extended_risk_gate",
               verdict=gate_verdict,
               reason="extended risk gate passed: 19 checks"),
    ]


def _grounded_memo(gate_line="Gate verdict: PASS."):
    return (
        "# DEMO memo — 2026-11-14\n\n"
        "## Thesis\n"
        "DEMO closed at 129.00, above its 30-close mean of 124.50. "
        "Revenue was 385.00 with net income 97.00.\n\n"
        "## Key risks\n"
        "NPL is 3.2% and capital adequacy is 12.54%.\n\n"
        "## What would change my mind\n"
        "A close below 124.50.\n\n"
        "## Gate verdict\n"
        f"{gate_line}\n\n"
        "## Sources\n"
        "- Yahoo chart: DEMO close 129.00 on 2026-11-14\n"
        "- EDGAR 10-K: revenue 385.00, net income 97.00\n"
    )


# --- Number grammar ---------------------------------------------------

def test_extract_plain_percent_multiple_currency_commas():
    claims, unparseable = extract_numbers(
        "Closed at $129.00, NPL 3.2%, trades at 12.5x, "
        "volume 1,250,000, down -4.5.")
    assert unparseable == []
    assert [(c["value"], c["unit"]) for c in claims] == [
        (129.00, ""), (3.2, "%"), (12.5, "x"), (1250000.0, ""),
        (-4.5, "")]


def test_extract_masks_dates_filing_labels_years_section_ordinals():
    claims, unparseable = extract_numbers(
        "On 2026-11-14 the 10-K for 2026 was filed. See Section 3. "
        "The 10-Q followed on 2026/11/15.")
    assert claims == []
    assert unparseable == []


def test_extract_leading_number_in_hyphenated_word_is_a_claim():
    claims, unparseable = extract_numbers(
        "The 30-close mean and the 5-day move.")
    assert unparseable == []
    assert [c["value"] for c in claims] == [30.0, 5.0]


def test_extract_name_labels_are_not_claims():
    claims, unparseable = extract_numbers(
        "Q3 revenue rose. FY25 was strong. DEMO is not in the "
        "S&P500. Version v2.0 shipped in H2.")
    assert claims == []
    assert unparseable == []


@pytest.mark.parametrize("token", ["147..00", "1,23,456", "Rs129.00",
                                   "12.34.56", "1.2.3"])
def test_extract_unparseable_tokens_fail_closed(token):
    claims, unparseable = extract_numbers(f"Target {token}.")
    assert claims == []
    assert [u["token"] for u in unparseable] == [token]


def test_extract_reports_1_based_line_numbers():
    claims, _ = extract_numbers("No numbers here.\nClose 129.00.")
    assert claims[0]["line"] == 2
    assert claims[0]["token"] == "129.00"


def test_extract_non_string_memo_raises():
    with pytest.raises(ValueError):
        extract_numbers(129.00)


# --- Evidence collection ----------------------------------------------

def test_collect_evidence_only_from_evidence_and_tool_results():
    log = _evidence_log()
    evidence = collect_evidence(log)
    values = sorted((e["value"], e["unit"]) for e in evidence)
    # 30 (n_points) + 129.00 + 30 + 124.50 + 3.2% + 12.54% + 385.00
    # + 129.00 + 385.00 + 97.00. The gate reason's "19 checks" is
    # NOT evidence (gate_result is not a source of truth).
    assert (19.0, "") not in values
    assert (129.00, "") in values
    assert (3.2, "%") in values
    assert (97.00, "") in values
    # seq numbers are never evidence.
    assert all(e["field"] != "seq" for e in evidence)


def test_collect_evidence_rejects_non_list():
    with pytest.raises(ValueError):
        collect_evidence({"type": "tool_result"})


def test_collect_evidence_rejects_non_dict_event():
    with pytest.raises(ValueError):
        collect_evidence([{"type": "tool_result"}, "not-an-event"])


def test_collect_evidence_ignores_bool_fields():
    log = [_event("tool_result", 0, tool="t", status="ok",
                  flag=True, detail="value 5.0")]
    values = [(e["value"], e["unit"]) for e in collect_evidence(log)]
    assert (5.0, "") in values
    # True must not be collected as the number 1.0.
    assert (1.0, "") not in values


def test_evidence_detail_leading_number_counts():
    # A hyphenated number inside a tool_result detail ("30-close")
    # contributes its leading number as evidence, like in memos.
    log = [_event("tool_result", 0, tool="t", status="ok",
                  detail="30-close mean 124.50"),
           _event("evidence_ref", 1, source="t"),
           _event("gate_result", 2, gate="g", verdict="PASS")]
    memo = ("## Thesis\nThe 30-close mean is 124.50.\n\n"
            "## Key risks\nNone.\n\n"
            "## What would change my mind\nA close below 124.50.\n\n"
            "## Gate verdict\nGate verdict: PASS.\n\n"
            "## Sources\n- t\n")
    assert check_memo(memo, log)["verdict"] == "PASS"


# --- check_memo: the PASS case ----------------------------------------

def test_grounded_memo_passes():
    result = check_memo(_grounded_memo(), _evidence_log())
    assert result["verdict"] == "PASS"
    assert result["reasons"] == []
    assert result["numbers_checked"] > 0
    assert result["numbers_grounded"] == result["numbers_checked"]
    assert result["gate_verdict"] == "PASS"
    assert result["checks"]["gate_agreement"]["pass"] is True


def test_check_is_deterministic():
    first = check_memo(_grounded_memo(), _evidence_log())
    second = check_memo(_grounded_memo(), _evidence_log())
    assert first == second


def test_hyphenated_section_heading_counts():
    memo = _grounded_memo().replace(
        "## What would change my mind",
        "## What-would-change-my-mind")
    assert check_memo(memo, _evidence_log())["verdict"] == "PASS"


def test_gate_verdict_on_heading_line_counts():
    memo = _grounded_memo().replace(
        "## Gate verdict\nGate verdict: PASS.",
        "## Gate verdict: PASS")
    assert check_memo(memo, _evidence_log())["verdict"] == "PASS"


# --- check_memo: groundedness -----------------------------------------

def test_planted_number_blocks_and_names_the_token():
    memo = _grounded_memo().replace("129.00, above", "132.00, above")
    result = check_memo(memo, _evidence_log())
    assert result["verdict"] == "BLOCK"
    assert result["checks"]["groundedness"]["unsourced"] == [
        {"token": "132.00", "value": 132.00, "unit": "", "line": 4}]
    assert any("132.00" in r for r in result["reasons"])


def test_unit_mismatch_blocks():
    # Evidence carries 3.2% (a percent); the memo cites plain 3.2.
    memo = _grounded_memo().replace("NPL is 3.2%", "NPL is 3.2")
    result = check_memo(memo, _evidence_log())
    assert result["verdict"] == "BLOCK"
    assert result["checks"]["groundedness"]["unsourced"][0]["token"] == "3.2"


def test_tolerance_boundary_exactly_one_percent_passes():
    # 101.00 vs 100.00 is exactly 1% — inside tolerance.
    log = [_event("tool_result", 0, tool="t", status="ok",
                  detail="value 100.00"),
           _event("evidence_ref", 1, source="t"),
           _event("gate_result", 2, gate="g", verdict="PASS")]
    memo = ("## Thesis\nValue is 101.00.\n\n## Key risks\nNone.\n\n"
            "## What would change my mind\nValue below 100.00.\n\n"
            "## Gate verdict\nGate verdict: PASS.\n\n"
            "## Sources\n- t\n")
    result = check_memo(memo, log)
    assert result["verdict"] == "PASS"


def test_tolerance_boundary_just_over_one_percent_blocks():
    log = [_event("tool_result", 0, tool="t", status="ok",
                  detail="value 100.00"),
           _event("evidence_ref", 1, source="t"),
           _event("gate_result", 2, gate="g", verdict="PASS")]
    memo = ("## Thesis\nValue is 101.01.\n\n## Key risks\nNone.\n\n"
            "## What would change my mind\nValue below 100.00.\n\n"
            "## Gate verdict\nGate verdict: PASS.\n\n"
            "## Sources\n- t\n")
    result = check_memo(memo, log)
    assert result["verdict"] == "BLOCK"
    assert result["checks"]["groundedness"]["unsourced"][0]["token"] == "101.01"


def test_rounded_inside_tolerance_passes():
    memo = _grounded_memo().replace("12.54%", "12.5%")
    assert check_memo(memo, _evidence_log())["verdict"] == "PASS"


def test_rounded_outside_tolerance_blocks():
    memo = _grounded_memo().replace("12.54%", "12.9%")
    result = check_memo(memo, _evidence_log())
    assert result["verdict"] == "BLOCK"
    assert result["checks"]["groundedness"]["unsourced"][0]["token"] == "12.9%"


def test_unparseable_number_blocks_and_names_the_token():
    memo = _grounded_memo().replace("129.00, above", "129..00, above")
    result = check_memo(memo, _evidence_log())
    assert result["verdict"] == "BLOCK"
    assert result["checks"]["groundedness"]["unparseable"] == [
        {"token": "129..00", "line": 4}]
    assert any("129..00" in r for r in result["reasons"])


def test_number_only_in_gate_reason_blocks():
    # "19 checks" appears only in the gate_result reason, which is
    # not evidence. A memo citing it is unsourced.
    memo = _grounded_memo().replace(
        "Gate verdict: PASS.",
        "Gate verdict: PASS. The gate ran 19 checks.")
    result = check_memo(memo, _evidence_log())
    assert result["verdict"] == "BLOCK"
    assert result["checks"]["groundedness"]["unsourced"][0]["token"] == "19"


# --- check_memo: structure --------------------------------------------

@pytest.mark.parametrize("section,heading", [
    ("thesis", "## Thesis\n"),
    ("key risks", "## Key risks\n"),
    ("what would change my mind",
     "## What would change my mind\n"),
    ("gate verdict", "## Gate verdict\n"),
    ("sources", "## Sources\n"),
])
def test_each_missing_section_blocks(section, heading):
    memo = _grounded_memo()
    assert heading in memo
    memo_without = memo.replace(heading, "", 1)
    if section == "gate verdict":
        # The body line restates the section name, which the
        # checker (by design) reads as a heading — drop it too,
        # so the section is genuinely absent.
        memo_without = memo_without.replace(
            "Gate verdict: PASS.\n", "", 1)
    result = check_memo(memo_without, _evidence_log())
    assert result["verdict"] == "BLOCK"
    assert section in result["checks"]["structure"]["missing_sections"]
    assert any(section in r for r in result["reasons"])


def test_empty_memo_blocks_with_all_sections_missing():
    result = check_memo("", _evidence_log())
    assert result["verdict"] == "BLOCK"
    assert result["checks"]["structure"]["missing_sections"] == list(
        groundedness.REQUIRED_SECTIONS)


# --- check_memo: sources ----------------------------------------------

def test_fabricated_source_blocks_and_names_the_entry():
    memo = _grounded_memo() + "- Bloomberg Terminal\n"
    result = check_memo(memo, _evidence_log())
    assert result["verdict"] == "BLOCK"
    assert result["checks"]["sources"]["fabricated"] == [
        "Bloomberg Terminal"]
    assert any("Bloomberg Terminal" in r for r in result["reasons"])


def test_empty_sources_section_blocks():
    memo = _grounded_memo().replace(
        "## Sources\n- Yahoo chart: DEMO close 129.00 on 2026-11-14\n"
        "- EDGAR 10-K: revenue 385.00, net income 97.00\n",
        "## Sources\n")
    result = check_memo(memo, _evidence_log())
    assert result["verdict"] == "BLOCK"
    assert any("no source" in r for r in result["reasons"])


# --- check_memo: gate agreement ---------------------------------------

def test_gate_mismatch_blocks_and_names_it():
    memo = _grounded_memo(gate_line="Gate verdict: PASS.")
    result = check_memo(memo, _evidence_log(gate_verdict="BLOCK"))
    assert result["verdict"] == "BLOCK"
    assert result["checks"]["gate_agreement"]["pass"] is False
    assert result["checks"]["gate_agreement"]["gate_verdict"] == "BLOCK"
    assert result["checks"]["gate_agreement"][
        "claimed_gate_verdict"] == "PASS"
    assert any("gate mismatch" in r for r in result["reasons"])


def test_gate_mismatch_other_direction_blocks():
    memo = _grounded_memo(gate_line="Gate verdict: BLOCK.")
    result = check_memo(memo, _evidence_log(gate_verdict="PASS"))
    assert result["verdict"] == "BLOCK"
    assert result["checks"]["gate_agreement"]["pass"] is False


def test_gate_blocked_memo_that_reports_the_block_passes():
    memo = _grounded_memo(gate_line="Gate verdict: BLOCK.")
    result = check_memo(memo, _evidence_log(gate_verdict="BLOCK"))
    assert result["verdict"] == "PASS"
    assert result["checks"]["gate_agreement"]["pass"] is True


def test_gate_section_with_no_verdict_blocks():
    memo = _grounded_memo(gate_line="The gate was consulted.")
    result = check_memo(memo, _evidence_log())
    assert result["verdict"] == "BLOCK"
    assert result["checks"]["gate_agreement"]["pass"] is False
    assert result["checks"]["gate_agreement"]["mismatch"] is not None


def test_no_gate_event_means_gate_agreement_not_counted():
    log = [e for e in _evidence_log()
           if e["type"] != "gate_result"]
    result = check_memo(_grounded_memo(), log)
    assert result["verdict"] == "PASS"
    assert result["checks"]["gate_agreement"]["pass"] is None
    assert result["gate_verdict"] is None


def test_check_rejects_non_string_memo_and_non_list_log():
    with pytest.raises(ValueError):
        check_memo(42, _evidence_log())
    with pytest.raises(ValueError):
        check_memo(_grounded_memo(), {"type": "tool_result"})


def test_result_is_json_serialisable():
    import json

    result = check_memo(_grounded_memo(), _evidence_log())
    assert json.loads(json.dumps(result)) == result
