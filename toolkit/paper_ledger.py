"""Paper-only ledger for hypothetical plans — Engine 1.5 (Phase 1).

Records hypothetical plans and their risk-gate verdicts so a plan
can be reviewed, re-read and audited later. This ledger holds
hypothetical plans only: no orders, no execution, no broker, no
real money. A recorded PASS is not a recommendation — the ledger
stores the verdict alongside the plan and the gate version that
produced it, so a later reader can see exactly what was checked.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

__all__ = ["list_plans", "plans_for_symbol", "record_plan"]

SCHEMA_VERSION = "1.5.0"


def record_plan(path, plan, gate_result):
    """Append one hypothetical plan + its gate verdict to the ledger.

    ``plan`` is the plan dict passed to the gate; ``gate_result`` is
    the dict returned by ``risk_gate`` / ``extended_risk_gate`` and
    must carry a ``verdict`` and a ``fingerprint``. ``plan`` must
    carry a non-empty ``exchange`` label — required since Round 3, so
    a global plan can never be silently labelled NEPSE. Returns the
    ledger record. Raises ValueError when either is missing the
    fields the ledger needs. Reads of ledgers written before the
    exchange requirement (``list_plans``) still default a missing
    exchange to ``NEPSE`` — the default lives only on the read path,
    never on writes.
    """
    if not isinstance(plan, dict) or not plan.get("symbol"):
        raise ValueError("plan must be a dict with a symbol")
    if not isinstance(plan.get("exchange"), str) \
            or not plan["exchange"].strip():
        raise ValueError("plan must carry a non-empty exchange label, "
                         "e.g. 'NEPSE'")
    if not isinstance(gate_result, dict) \
            or gate_result.get("verdict") not in ("PASS", "BLOCK") \
            or not gate_result.get("fingerprint"):
        raise ValueError("gate_result must carry verdict (PASS/BLOCK) "
                         "and fingerprint")
    record = {"schema_version": SCHEMA_VERSION,
              "recorded_date": date.today().isoformat(),
              "symbol": plan["symbol"],
              "exchange": plan["exchange"],
              "plan": plan,
              "verdict": gate_result["verdict"],
              "fingerprint": gate_result["fingerprint"],
              "failed_checks": list(gate_result.get("failed_checks", [])),
              "note": "hypothetical plan only — a PASS is not a "
                      "recommendation, and nothing here is an order"}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    return record


def list_plans(path):
    """Read all ledger records. Missing file -> empty list.

    Legacy read default: a record written before the exchange
    requirement (Round 3) has no ``exchange`` field; it reads back
    as ``NEPSE``, on the record and on the nested plan. Writes never
    default — see ``record_plan``.
    """
    path = Path(path)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        record.setdefault("exchange", "NEPSE")
        if isinstance(record.get("plan"), dict):
            record["plan"].setdefault("exchange", "NEPSE")
        records.append(record)
    return records


def plans_for_symbol(path, symbol):
    """Ledger records for one symbol, in recorded order."""
    return [r for r in list_plans(path) if r.get("symbol") == symbol]
