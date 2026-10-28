"""Markdown accuracy report from the prediction ledger — not a model.

Reads the tracker ledger (JSONL written by toolkit.tracker, predictions
recorded BEFORE their outcome is known) and produces a Markdown table
per model/version with:

- direction accuracy and mean absolute error, always WITH n;
- pending predictions shown separately and NEVER counted as wins
  (pending records are excluded from every accuracy/error/failure
  rate, exactly as tracker.accuracy_report does);
- failures listed in full, each with its reason and action;
- a model with zero scored predictions shown as "no scored predictions",
  never as 0% — 0 scored is "unknown", not "always wrong".

This module changes no model and no forecast. It only reports what the
ledger already contains. A missing ledger is treated as an empty one
(same as tracker._load): n = 0, pending = 0, "no scored predictions".

Research only: past ledger accuracy is not a guarantee of future
accuracy, and this report is not a buy/sell/hold signal.
"""
from __future__ import annotations

import json
import os

_NO_SCORED = "no scored predictions"


def _load_records(ledger_path):
    """Ledger records as a list; missing file -> []. Blank lines skipped."""
    if not ledger_path or not os.path.exists(ledger_path):
        return []
    records = []
    with open(ledger_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def _model_key(record):
    """Group key: model, with version appended when the record carries one."""
    model = record.get("model", "unknown")
    version = record.get("model_version") or record.get("version")
    return f"{model} ({version})" if version else str(model)


def _fmt_rate(numerator, denominator):
    if denominator == 0:
        return _NO_SCORED
    return f"{numerator / denominator * 100:.2f}% ({numerator}/{denominator})"


def _fmt_mean_abs_error(total_abs_error, n_scored):
    if n_scored == 0:
        return _NO_SCORED
    return f"{total_abs_error / n_scored:.4f}"


def build_accuracy_data(ledger_path, symbol=None):
    """Structured accuracy data from a ledger.

    Returns a dict with n_scored, n_pending, n_failed, direction_accuracy
    (None when n_scored == 0), mean_abs_error (None when n_scored == 0),
    failure_rate (None when n_scored == 0), and per_model: one entry per
    model/version that appears in the ledger — including models that
    have ONLY pending predictions (n_scored == 0 for those). Each
    per-model entry carries its own n_scored, n_pending, n_failed,
    direction_accuracy, mean_abs_error, failure_rate, and failures list
    (failed records with reason and action preserved).
    """
    records = _load_records(ledger_path)
    if symbol is not None:
        records = [r for r in records if r.get("symbol") == symbol]
    scored = [r for r in records if r.get("status") in ("scored", "failed")]
    pending = [r for r in records if r.get("status") == "pending"]
    failed = [r for r in scored if r.get("status") == "failed"]

    per_model = {}
    for rec in records:
        key = _model_key(rec)
        entry = per_model.setdefault(
            key, {"model": key, "n_scored": 0, "n_pending": 0, "n_failed": 0,
                  "direction_correct": 0, "total_abs_error": 0.0, "failures": []})
        if rec.get("status") in ("scored", "failed"):
            entry["n_scored"] += 1
            if rec.get("direction_correct") is True:
                entry["direction_correct"] += 1
            if isinstance(rec.get("abs_error"), (int, float)):
                entry["total_abs_error"] += rec["abs_error"]
            if rec.get("status") == "failed":
                entry["n_failed"] += 1
                entry["failures"].append({
                    "symbol": rec.get("symbol"),
                    "model": key,
                    "predicted_at": rec.get("predicted_at"),
                    "predicted_for": rec.get("predicted_for"),
                    "forecast": rec.get("forecast"),
                    "actual": rec.get("actual"),
                    "abs_error": rec.get("abs_error"),
                    "failure_reason": rec.get("failure_reason", "not recorded"),
                    "action": rec.get("action", "not recorded"),
                })
        elif rec.get("status") == "pending":
            entry["n_pending"] += 1

    for entry in per_model.values():
        n = entry["n_scored"]
        entry["direction_accuracy"] = (entry["direction_correct"] / n) if n else None
        entry["mean_abs_error"] = (entry["total_abs_error"] / n) if n else None
        entry["failure_rate"] = (entry["n_failed"] / n) if n else None
        del entry["direction_correct"], entry["total_abs_error"]

    n_scored = len(scored)
    return {
        "symbol": symbol,
        "n_scored": n_scored,
        "n_pending": len(pending),
        "n_failed": len(failed),
        "direction_accuracy": (
            sum(1 for r in scored if r.get("direction_correct") is True) / n_scored
            if n_scored else None),
        "mean_abs_error": (
            sum(r["abs_error"] for r in scored
                if isinstance(r.get("abs_error"), (int, float))) / n_scored
            if n_scored else None),
        "failure_rate": (len(failed) / n_scored) if n_scored else None,
        "per_model": per_model,
    }


def render_accuracy_markdown(data, symbol=None):
    """Render Markdown from build_accuracy_data() output."""
    sym = symbol if symbol is not None else data.get("symbol")
    title_symbol = f" — {sym}" if sym else ""
    lines = [f"# Prediction Accuracy Report{title_symbol}", ""]
    lines.append("Research only. Not investment advice. Past ledger accuracy "
                 "is not a guarantee of future accuracy.")
    lines.append("")
    n_scored = data["n_scored"]
    lines.append(f"- Scored predictions: n = {n_scored}")
    # Pending is always its own line, never folded into a rate.
    lines.append(f"- Pending predictions (not counted as wins): n = {data['n_pending']}")
    lines.append(f"- Failures: n = {data['n_failed']}")
    if n_scored:
        lines.append(f"- Overall direction accuracy: {_fmt_rate(round(data['direction_accuracy'] * n_scored), n_scored)}")
        lines.append(f"- Overall mean abs error: {data['mean_abs_error']:.4f}")
        lines.append(f"- Overall failure rate: {_fmt_rate(data['n_failed'], n_scored)}")
    else:
        lines.append("- Overall direction accuracy: no scored predictions")
        lines.append("- Overall mean abs error: no scored predictions")
        lines.append("- Overall failure rate: no scored predictions")
    lines.append("")
    lines.append("## Accuracy by model")
    lines.append("")
    lines.append("| Model | n scored | Pending | Direction accuracy | Mean abs error | Failures | Failure rate |")
    lines.append("| --- | ---: | ---: | --- | --- | ---: | --- |")
    if not data["per_model"]:
        lines.append(f"| (none) | 0 | 0 | {_NO_SCORED} | {_NO_SCORED} | 0 | {_NO_SCORED} |")
    for key in sorted(data["per_model"]):
        entry = data["per_model"][key]
        lines.append(
            f"| {key} | {entry['n_scored']} | {entry['n_pending']} | "
            f"{_fmt_rate(round(entry['direction_accuracy'] * entry['n_scored']), entry['n_scored']) if entry['n_scored'] else _NO_SCORED} | "
            f"{entry['mean_abs_error']:.4f} | {entry['n_failed']} | "
            f"{_fmt_rate(entry['n_failed'], entry['n_scored']) if entry['n_scored'] else _NO_SCORED} |"
            if entry["n_scored"] else
            f"| {key} | 0 | {entry['n_pending']} | {_NO_SCORED} | {_NO_SCORED} | {entry['n_failed']} | {_NO_SCORED} |")
    lines.append("")
    lines.append("## Failures (reason + action)")
    lines.append("")
    all_failures = [f for entry in data["per_model"].values() for f in entry["failures"]]
    if not all_failures:
        lines.append("No failures recorded.")
    else:
        for f in all_failures:
            lines.append(
                f"- {f['symbol']} / {f['model']}: predicted {f['predicted_at']} "
                f"for {f['predicted_for']}, forecast {f['forecast']}, "
                f"actual {f['actual']}, abs error {f['abs_error']}; "
                f"reason: {f['failure_reason']}; action: {f['action']}")
    lines.append("")
    lines.append("*Pending predictions are never counted as wins. A model with "
                 "zero scored predictions is reported as \"no scored "
                 "predictions\", not 0%.*")
    return "\n".join(lines) + "\n"


def generate_accuracy_report(ledger_path, symbol=None):
    """One-call Markdown report for a ledger path (optionally one symbol)."""
    return render_accuracy_markdown(build_accuracy_data(ledger_path, symbol=symbol), symbol=symbol)


def write_accuracy_report(ledger_path, output_path, symbol=None):
    """Write the Markdown report to output_path; returns the path."""
    text = generate_accuracy_report(ledger_path, symbol=symbol)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
    return output_path
