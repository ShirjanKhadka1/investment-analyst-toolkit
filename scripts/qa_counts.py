"""Agent-count drift gate (Round 3): the pack numbers claimed in the
docs must match a fresh recount of the finance-research-agents
roster, by script — never a typed constant.

Runs ``scripts/count_agents.py`` (live roster fetch); if the fetch
fails (offline run), falls back to the vendored snapshot
``docs/agents-roster-counts.json`` and says so. Then checks:
- README.md states the split as "<total> agents — <nepse> NEPSE-side
  ... and <agnostic> market-agnostic", "<runbooks> runbooks" and
  "<templates> templates" matching the recount;
- docs/positioning-draft.md states the same split in its proposed
  agents description ("<total> agents, <runbooks> runbooks" and
  "<nepse> NEPSE / <agnostic> market-agnostic");
- the removed claims never come back: "36 analyst agents" and
  "NEPSE-first" must not appear in README.md or docs/index.html
  (the 36 agents live in finance-research-agents, not this repo,
  and "NEPSE-first" is retired as a headline — see
  docs/positioning-draft.md).

Usage: python3 scripts/qa_counts.py [--root REPO_ROOT]
       [--from-dir DIR] [--offline]
Exit 0 only when every claimed number matches the recount.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COUNT_SCRIPT = ROOT / "scripts" / "count_agents.py"
SNAPSHOT = ROOT / "docs" / "agents-roster-counts.json"

# (file, pattern, fields) — each pattern's groups must equal the
# named counts, in order.
CLAIMS = [
    ("README.md",
     r"(\d+)\s+agents\s+—\s+(\d+)\s+NEPSE-side",
     ("agents_total", "agents_nepse")),
    ("README.md",
     r"(\d+)\s+market-agnostic",
     ("agents_agnostic",)),
    ("README.md",
     r"(\d+)\s+runbooks",
     ("runbooks",)),
    ("README.md",
     r"(\d+)\s+templates",
     ("templates",)),
    ("docs/positioning-draft.md",
     r"(\d+)\s+agents,\s+(\d+)\s+runbooks",
     ("agents_total", "runbooks")),
    ("docs/positioning-draft.md",
     r"(\d+)\s+NEPSE\s+/\s+(\d+)\s+market-agnostic",
     ("agents_nepse", "agents_agnostic")),
]
# Claims that must never appear in these files (removed wording).
BANNED = [
    ("README.md", "36 analyst agents"),
    ("docs/index.html", "36 analyst agents"),
    ("README.md", "NEPSE-first"),
    ("docs/index.html", "NEPSE-first"),
]


def _recount(from_dir):
    """Fresh recount, or the vendored snapshot when offline."""
    cmd = [sys.executable, str(COUNT_SCRIPT)]
    if from_dir:
        cmd += ["--from-dir", from_dir]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                          check=False)
    if proc.returncode == 0:
        # The script prints the counts JSON as its last output block.
        text = proc.stdout.strip()
        start = text.rindex("{")
        return json.loads(text[start:]), "live recount"
    if SNAPSHOT.exists():
        print("count_agents live fetch failed; using vendored snapshot "
              f"({SNAPSHOT.name}) — re-run online to re-verify")
        return json.loads(SNAPSHOT.read_text(encoding="utf-8")), "snapshot"
    print("count_agents failed and no snapshot exists:")
    print(proc.stdout[-1000:])
    print(proc.stderr[-1000:], file=sys.stderr)
    return None, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--from-dir", default=None)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    root = Path(args.root)
    if args.offline:
        if SNAPSHOT.exists():
            counts = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
            source = "snapshot (--offline)"
        else:
            print("offline and no snapshot exists")
            return 1
    else:
        counts, source = _recount(args.from_dir)
        if counts is None:
            return 1
    print(f"counts ({source}): {counts['agents_total']} agents = "
          f"{counts['agents_nepse']} NEPSE "
          f"({counts['nepse_team']} team + {counts['nepse_sector']} "
          f"sector + {counts['nepse_other']} other) + "
          f"{counts['agents_agnostic']} agnostic; "
          f"{counts['runbooks']} runbooks, {counts['templates']} templates")
    errors = []
    for rel, pattern, fields in CLAIMS:
        text = (root / rel).read_text(encoding="utf-8")
        match = re.search(pattern, text)
        if not match:
            errors.append(f"{rel}: claim pattern not found: {pattern!r}")
            continue
        for group, field in zip(match.groups(), fields):
            if int(group) != counts[field]:
                errors.append(
                    f"{rel}: claims {field}={group}, recount says "
                    f"{counts[field]} (pattern {pattern!r})")
    for rel, banned in BANNED:
        text = (root / rel).read_text(encoding="utf-8")
        if banned in text:
            errors.append(f"{rel}: removed claim still present: {banned!r}")
    if errors:
        print("qa_counts FAILED:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("qa_counts PASS: every claimed pack number matches the recount")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
