"""Groundedness checker — Round 9, PR 9.2.

A pure function over (memo text, event log): every number in a memo
must trace to an ``evidence_ref`` or ``tool_result`` value from the
same run, or the memo is blocked. The checker does not generate text,
does not call an LLM, does not edit the memo, and does not fetch
anything. Stdlib only, no network. This module is the contract item
9.3's dossier pipeline builds against: ``check_memo`` is the entry
point, and its result dict is the stable interface (see
``check_memo`` for the exact shape).

Number grammar (fixed here, before the first fixture was written;
fixtures are written to this grammar, and the grammar is never
widened to make a failing fixture pass):

- A numeric claim is a maximal token matching, after stripping
  surrounding punctuation: an optional sign (``+``/``-``), an
  optional currency symbol (``$``, ``EUR``-style ``EUR`` is NOT one —
  only ``$``, ``\\u20ac`` and ``\\u00a3`` attach), digits with optional
  thousands separators (groups of exactly three after each comma),
  an optional decimal fraction, and an optional unit suffix ``%``
  or ``x``. Examples: ``129.00``, ``$129.00``, ``1,250,000``,
  ``3.2%``, ``12.5x``, ``-4.5``. The currency symbol is presentation
  only: it is ignored when matching. The unit suffix is part of the
  claim: a ``%`` claim matches only ``%`` evidence, and an ``x``
  claim matches only ``x`` evidence.
- These are NOT numeric claims, and are masked before extraction:
  ISO dates (``2026-11-14``, ``2026/11/14``), filing labels
  (``10-K``, ``10-Q``, ``8-K``, ``20-F``), standalone years
  (``1900``–``2099``), and section ordinals (``Section 3``).
- A token that LEADS with a valid number followed by ``-`` and
  letters contributes that leading number as a claim (``30-close``
  claims 30; ``5-day`` claims 5).
- A token matching a quarter/half label (``Q1``–``Q4``, ``H1``,
  ``H2``), a fiscal-year label (``FY25``), an index name (``S&P500``)
  or a version label (``v2.0``) is a proper name, not a claim.
- Anything else containing a digit is UNPARSEABLE and blocks the
  memo fail-closed, naming the token (``147..00``, ``1,23,456``,
  ``Rs129.00`` — write ``Rs 129.00`` with a space instead). A number
  the checker cannot parse is treated as untraced, and the failure
  names the token.

Rounding tolerance (fixed here): a memo number matches an evidence
number when the units agree and ``abs(memo - evidence)`` is at most
``max(REL_TOLERANCE * abs(evidence), ABS_TOLERANCE)`` — a 1%
relative tolerance with a 0.005 absolute floor, so ``12.5%`` matches
evidence ``12.54%`` (0.32% off) and ``42.9%`` does not match
``42.36%`` (1.27% off).

Evidence (the only sources of truth): numeric values carried by
``evidence_ref`` and ``tool_result`` events from the same run —
every int/float field except ``seq``, plus every number matching
the grammar above inside their string fields (``source``,
``detail`` and friends), with dates masked the same way. Numbers
in any other event type (``gate_result`` reason included) are NOT
evidence: a memo that cites a gate count must find it in a
``tool_result`` detail, not in the gate's reason string.

Structure: a memo passes only when every required section is
present as a heading — thesis, key risks, what would change my
mind, gate verdict, sources. A memo missing any section is blocked,
whatever its numbers say.

Sources: every entry listed under the Sources heading must match
an ``evidence_ref`` source from the same run (either string
contains the other after normalisation). An entry with no match is
a fabricated source and blocks the memo, naming the entry. A
Sources section with no entries blocks the memo.

Gate agreement: the memo's stated gate verdict (the first whole
word PASS or BLOCK inside the Gate verdict section) must equal the
verdict of the last ``gate_result`` event in the log. A mismatch
blocks the memo and is reported as a named mismatch, never silently
resolved. If the log has no ``gate_result`` event, gate agreement
is not counted (``None``). Note the checker judges the MEMO, not
the trade: a memo that faithfully reports a BLOCKed gate — numbers
grounded, claim BLOCK — passes the checker. What never passes is
a memo that claims PASS on a BLOCKed run (or BLOCK on a PASSed
run). Downstream consumers that must refuse gate-blocked runs
should read the ``gate_verdict`` field of the result and apply
their own rule; the checker hands them the log's verdict verbatim.
"""

from __future__ import annotations

import re

__all__ = [
    "ABS_TOLERANCE",
    "NAME_LABEL_RE",
    "REL_TOLERANCE",
    "REQUIRED_SECTIONS",
    "check_memo",
    "collect_evidence",
    "extract_numbers",
]

# Relative rounding tolerance (1%) and absolute floor (0.005).
REL_TOLERANCE = 0.01
ABS_TOLERANCE = 0.005

# Required memo sections, in the order they must be headed.
REQUIRED_SECTIONS = (
    "thesis",
    "key risks",
    "what would change my mind",
    "gate verdict",
    "sources",
)

# --- Number grammar (see the module docstring) -----------------------

_DATE_RE = re.compile(r"\b\d{4}[-/]\d{2}[-/]\d{2}\b")
_FILING_RE = re.compile(r"\b\d{1,2}-[A-Z]{1,2}\b")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_SECTION_ORDINAL_RE = re.compile(r"(?i)\bsection\s+\d+\b")

# A valid number: optional sign, optional $/EUR/GBP symbol, digits
# with optional thousands groups, optional fraction, optional %/x.
_NUMBER_RE = re.compile(
    r"[+-]?[$\u20ac\u00a3]?"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.\d+)?"
    r"[%x]?"
)

# A token that leads with a valid number followed by "-" and letters
# (``30-close``, ``5-day``): the leading number is a claim.
_LEADING_NUMBER_RE = re.compile(
    r"^([+-]?[$\u20ac\u00a3]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?[%x]?)-[A-Za-z]"
)

# Proper-name labels that contain digits but are not claims.
NAME_LABEL_RE = re.compile(
    r"^(?:Q[1-4]|H[12]|FY\d{2}|S&P\d+|v\d+(?:\.\d+)?)$")

# Characters stripped from the ends of a whitespace token before it
# is judged (brackets, quotes and sentence punctuation only — never
# digits, letters, %, x, $ or internal punctuation).
_STRIP_CHARS = " \t([{<'\"`.,;:!?)]}>'\"`"


def _mask_non_claims(text):
    """Blank out dates, filing labels, years and section ordinals.

    Each match is replaced by spaces of the same length, so line and
    column positions of everything else are unchanged.
    """
    for pattern in (_DATE_RE, _FILING_RE, _YEAR_RE, _SECTION_ORDINAL_RE):
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _parse_number_token(token):
    """Parse one stripped token as a number claim.

    Returns ``(value, unit)`` for a full grammar match, or None.
    ``unit`` is ``"%"``, ``"x"`` or ``""`` (plain). The currency
    symbol, if any, is dropped: it is presentation, not a unit.
    """
    match = _NUMBER_RE.fullmatch(token)
    if match is None:
        return None
    raw = match.group(0)
    unit = ""
    if raw.endswith("%"):
        unit = "%"
        raw = raw[:-1]
    elif raw.endswith("x"):
        unit = "x"
        raw = raw[:-1]
    raw = raw.lstrip("$\u20ac\u00a3")
    raw = raw.replace(",", "")
    try:
        return float(raw), unit
    except ValueError:
        return None


def extract_numbers(memo):
    """Extract the numeric claims (and unparseable tokens) of a memo.

    Returns ``(claims, unparseable)`` where ``claims`` is a list of
    dicts ``{"token", "value", "unit", "line"}`` (``line`` is
    1-based) and ``unparseable`` is a list of dicts ``{"token",
    "line"}`` for digit-containing tokens the grammar cannot parse.
    Non-claims (dates, filing labels, years, section ordinals,
    proper-name labels) are excluded before extraction, per the
    module docstring.
    """
    if not isinstance(memo, str):
        # Toolkit convention is ValueError for all input validation.
        raise ValueError(  # noqa: TRY004
            f"memo must be a string, got {type(memo).__name__}")
    claims = []
    unparseable = []
    for line_no, line in enumerate(memo.splitlines(), start=1):
        masked = _mask_non_claims(line)
        for raw_token in masked.split():
            token = raw_token.strip(_STRIP_CHARS)
            if not token or not any(ch.isdigit() for ch in token):
                continue
            parsed = _parse_number_token(token)
            if parsed is not None:
                value, unit = parsed
                claims.append({"token": token, "value": value,
                               "unit": unit, "line": line_no})
                continue
            leading = _LEADING_NUMBER_RE.match(token)
            if leading is not None:
                parsed = _parse_number_token(leading.group(1))
                if parsed is not None:
                    value, unit = parsed
                    claims.append({"token": leading.group(1),
                                   "value": value, "unit": unit,
                                   "line": line_no})
                    continue
            if NAME_LABEL_RE.match(token):
                continue
            unparseable.append({"token": token, "line": line_no})
    return claims, unparseable


def _evidence_numbers_from_string(text):
    """All grammar numbers inside one evidence string field."""
    if not isinstance(text, str):
        return []
    found = []
    for raw_token in _mask_non_claims(text).split():
        token = raw_token.strip(_STRIP_CHARS)
        if not token or not any(ch.isdigit() for ch in token):
            continue
        parsed = _parse_number_token(token)
        if parsed is not None:
            value, unit = parsed
            found.append({"value": value, "unit": unit})
            continue
        leading = _LEADING_NUMBER_RE.match(token)
        if leading is not None:
            parsed = _parse_number_token(leading.group(1))
            if parsed is not None:
                value, unit = parsed
                found.append({"value": value, "unit": unit})
    return found


def collect_evidence(events):
    """Collect the evidence numbers of an event log.

    ``events`` is a list of event dicts (as returned by
    ``toolkit.events.load_events``). Only ``evidence_ref`` and
    ``tool_result`` events count. Returns a list of dicts
    ``{"value", "unit", "event_type", "field"}`` — one per evidence
    number found. The ``seq`` field is never evidence.
    """
    if not isinstance(events, list):
        # Toolkit convention is ValueError for all input validation.
        raise ValueError(  # noqa: TRY004
            f"events must be a list, got {type(events).__name__}")
    evidence = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError(  # noqa: TRY004
                f"each event must be a dict, got {type(event).__name__}")
        if event.get("type") not in ("evidence_ref", "tool_result"):
            continue
        event_type = event["type"]
        for field, value in event.items():
            if field == "seq":
                continue
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                evidence.append({"value": float(value), "unit": "",
                                 "event_type": event_type, "field": field})
            elif isinstance(value, str):
                for found in _evidence_numbers_from_string(value):
                    found = dict(found)
                    found["event_type"] = event_type
                    found["field"] = field
                    evidence.append(found)
    return evidence


def _matches_evidence(claim, evidence):
    """True when a claim matches an evidence number within tolerance."""
    for item in evidence:
        if item["unit"] != claim["unit"]:
            continue
        if abs(claim["value"] - item["value"]) <= max(
                REL_TOLERANCE * abs(item["value"]), ABS_TOLERANCE):
            return True
    return False


def _normalise_heading(line):
    """Lowercase a heading line, dropping markers and punctuation."""
    text = line.strip().lstrip("#").strip().lower()
    text = text.replace("-", " ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_sections(memo):
    """Map required-section name -> list of its body lines.

    A heading line is a line whose normalised form is a required
    section name, or starts with one followed by more text (e.g.
    ``Gate verdict: PASS``). Lines before the first required heading
    belong to no section.
    """
    sections = {}
    current = None
    for line in memo.splitlines():
        heading = _normalise_heading(line)
        matched = None
        for section in REQUIRED_SECTIONS:
            if heading == section or heading.startswith(section + " "):
                matched = section
                break
        if matched is not None:
            current = matched
            sections.setdefault(current, [])
            # Anything after the heading on the same line (e.g. the
            # verdict after "Gate verdict:") belongs to the section.
            rest = heading[len(matched):].strip()
            if rest:
                sections[current].append(rest)
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def _normalise_source(text):
    """Lowercase a source string, dropping punctuation and spaces."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _source_entries(section_lines):
    """Source entries: non-empty lines of the Sources section."""
    entries = []
    for line in section_lines:
        text = line.strip()
        text = re.sub(r"^[-*\d.)\s]+", "", text).strip()
        if text:
            entries.append(text)
    return entries


def check_memo(memo, events):
    """Check one memo against one event log. Pure function.

    ``memo`` is the memo text (str); ``events`` is the run's event
    log as a list of event dicts. Returns a JSON-serialisable dict::

        {
          "verdict": "PASS" | "BLOCK",
          "checks": {
            "structure": {"pass": bool, "missing_sections": [...]},
            "groundedness": {"pass": bool,
                             "unsourced": [{"token", "value", "unit",
                                            "line"}],
                             "unparseable": [{"token", "line"}]},
            "sources": {"pass": bool, "fabricated": [...]},
            "gate_agreement": {"pass": bool | None,
                               "gate_verdict": "PASS" | "BLOCK" | None,
                               "claimed_gate_verdict": ... | None,
                               "mismatch": str | None},
          },
          "numbers_checked": int,
          "numbers_grounded": int,
          "gate_verdict": "PASS" | "BLOCK" | None,
          "reasons": [str, ...],
        }

    ``verdict`` is PASS only when every sub-check passes (or, for
    gate agreement, when the log carries no ``gate_result`` event).
    Every BLOCK names its reason: the unsourced or unparseable
    token, the missing section, the fabricated source entry, or the
    gate mismatch. Raises ValueError on a non-string memo or a
    non-list event log.
    """
    claims, unparseable = extract_numbers(memo)
    evidence = collect_evidence(events)

    reasons = []

    # 1. Structure.
    sections = _split_sections(memo)
    missing = [s for s in REQUIRED_SECTIONS if s not in sections]
    structure_pass = not missing
    for section in missing:
        reasons.append(f"missing section: {section}")

    # 2. Groundedness (fail-closed on unparseable numbers).
    unsourced = [c for c in claims if not _matches_evidence(c, evidence)]
    for item in unsourced:
        reasons.append(
            f"unsourced number: {item['token']!r} (line {item['line']})")
    for item in unparseable:
        reasons.append(
            f"unparseable number: {item['token']!r} (line {item['line']})")
    groundedness_pass = not unsourced and not unparseable

    # 3. Sources.
    fabricated = []
    if "sources" in sections:
        entries = _source_entries(sections["sources"])
        if not entries:
            reasons.append("sources section lists no source")
            fabricated = ["<no source listed>"]
        else:
            ref_sources = [
                _normalise_source(e.get("source", ""))
                for e in events
                if isinstance(e, dict) and e.get("type") == "evidence_ref"
                and isinstance(e.get("source"), str)
            ]
            for entry in entries:
                norm = _normalise_source(entry)
                grounded = any(
                    norm in ref or ref in norm
                    for ref in ref_sources if ref)
                if not grounded:
                    fabricated.append(entry)
                    reasons.append(f"fabricated source: {entry!r}")
    sources_pass = not fabricated and "sources" in sections

    # 4. Gate agreement.
    gate_events = [e for e in events
                   if isinstance(e, dict)
                   and e.get("type") == "gate_result"]
    gate_verdict = None
    claimed = None
    mismatch = None
    gate_pass = None
    if gate_events:
        gate_verdict = gate_events[-1].get("verdict")
        gate_lines = sections.get("gate verdict", [])
        gate_text = " ".join(gate_lines)
        found = re.findall(r"(?i)\b(PASS|BLOCK)\b", gate_text)
        claimed = found[0].upper() if found else None
        if claimed is None:
            mismatch = ("gate verdict section states neither PASS nor "
                        "BLOCK")
            reasons.append(mismatch)
            gate_pass = False
        elif claimed != gate_verdict:
            mismatch = (f"memo claims gate {claimed} but the log's "
                        f"gate verdict is {gate_verdict}")
            reasons.append(f"gate mismatch: {mismatch}")
            gate_pass = False
        else:
            gate_pass = True

    verdict = ("PASS" if structure_pass and groundedness_pass
               and sources_pass and gate_pass is not False else "BLOCK")
    return {
        "verdict": verdict,
        "checks": {
            "structure": {"pass": structure_pass,
                          "missing_sections": missing},
            "groundedness": {"pass": groundedness_pass,
                             "unsourced": unsourced,
                             "unparseable": unparseable},
            "sources": {"pass": sources_pass, "fabricated": fabricated},
            "gate_agreement": {"pass": gate_pass,
                               "gate_verdict": gate_verdict,
                               "claimed_gate_verdict": claimed,
                               "mismatch": mismatch},
        },
        "numbers_checked": len(claims),
        "numbers_grounded": len(claims) - len(unsourced),
        "gate_verdict": gate_verdict,
        "reasons": reasons,
    }
