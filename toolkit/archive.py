"""Archiving — every run is kept, timestamped, and indexed. Nothing is
overwritten: `latest` is a pointer, the archive is the record.

archive_run(output_dir, symbol, report_text, summary_dict, run_at):
  writes archive/<symbol>/<run_at>.md and .json, and updates
  archive/index.json (list of runs, newest last). Pruning is opt-in
  only (retention_days) and never deletes the latest run for a symbol.
"""
from __future__ import annotations

import json
import os


def archive_run(output_dir, symbol, report_text, summary_dict, run_at):
    arch_dir = os.path.join(output_dir, "archive", symbol)
    os.makedirs(arch_dir, exist_ok=True)
    md_path = os.path.join(arch_dir, f"{run_at}.md")
    json_path = os.path.join(arch_dir, f"{run_at}.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(summary_dict, indent=2))
    index_path = os.path.join(output_dir, "archive", "index.json")
    index = []
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    index.append({"symbol": symbol, "run_at": run_at, "report": md_path, "summary": json_path})
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(index, indent=2))
    return {"report": md_path, "summary": json_path, "index_entries": len(index)}


def prune_archive(output_dir, symbol, retention_days, today=None):
    """Delete archived runs older than retention_days, except the latest
    run for the symbol. Returns the list of deleted paths. retention_days
    must be positive; a missing archive returns []."""
    import datetime
    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    today = today or datetime.date.today()
    arch_dir = os.path.join(output_dir, "archive", symbol)
    if not os.path.isdir(arch_dir):
        return []
    files = sorted(f for f in os.listdir(arch_dir) if f.endswith(".md"))
    if len(files) <= 1:
        return []
    deleted = []
    for name in files[:-1]:  # keep the latest
        try:
            d = datetime.datetime.strptime(name[:-3], "%Y%m%dT%H%M%SZ").date()
        except ValueError:
            continue
        if (today - d).days > retention_days:
            for ext in (".md", ".json"):
                p = os.path.join(arch_dir, name[:-3] + ext)
                if os.path.exists(p):
                    os.remove(p); deleted.append(p)
    return deleted
