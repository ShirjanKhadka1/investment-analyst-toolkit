#!/usr/bin/env python3
"""Round 3 number check: fail if any checked file carries a count that
disagrees with status.json.

Usage: python3 scripts/check_numbers.py [--root REPO_ROOT]
Exit 0 only when every checked prose count matches status.json, every
cheap file count re-counted from disk matches status.json, and (where
the repo has a test suite) the suite recorded in status.json is green
(tests_passed == tests_total) -- because the prose claims "passing".

How historical numbers are handled: a match is skipped ONLY when an
exact allowlisted substring (which includes the historical number
itself) appears next to the match. Changing a historical number breaks
its allowlist entry, so the check fails instead of silently passing.
Historical lines are therefore preserved, not laundered.

This checker does not re-run the test suite; in CI the workflow runs
scripts/generate_status.py first, so status.json is fresh when this
check runs. Locally, run the generator first, then this check.
"""
import argparse
import json
import re
from pathlib import Path

# ---- per-repo spec (Round 3) ----
STATUS_FIELDS = ["repo", "version", "tests_passed", "tests_total", "coverage_percent", "coverage_fail_under", "generated_by", "generated_at"]
PROSE_FILES = ["README.md"]
PATTERNS = [
    (r"(\d+)/(\d+) tests", "tests_total", "tests total (N/N form)"),
    (r"(\d+) tests passing", "tests_total", "tests passing"),
    (r"(\d+) tests, all passing", "tests_total", "tests, all passing"),
    (r"Tests: (\d+)/(\d+)", "tests_total", "Tests: N/N form (incl. history sections)"),
    (r"(\d+)/(\d+) passed", "tests_total", "N/N passed form (incl. history sections)"),
    (r"all (\d+) tests pass", "tests_total", "all N tests pass"),
    (r"at v0\.4: (\d+) tests", "tests_total", "v0.4 count"),
    (r"coverage (\d+)%", "coverage_percent", "coverage percent"),
    (r"coverage \*\*(\d+)%\*\*", "coverage_percent", "coverage percent (bold)"),
]
ALLOWLIST = [
    "Tests at v0.4: 33 tests",
    "Tests at v0.3: 23/23 passed",
    "Tests: 124/124 (118 at Phase 5",
    "Tests: 118/118 (106 at v0.7.0",
    "Tests: 106/106 passed (66 at v0.6.0",
    "Tests: 66/66 passed (52 at v0.5.1",
    "Tests at v0.5.1: 52/52 passed",
    "Tests at v0.5.0: 52/52 passed",
    "all 148 tests pass",
    "coverage **82%** total",
]
FILE_COUNTS = []
REQUIRE_GREEN = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    root = Path(ap.parse_args().root)
    status_path = root / "status.json"
    errors = []
    if not status_path.exists():
        print(f"FAIL: {status_path} missing; run the generate_status.py script first")
        return 1
    status = json.loads(status_path.read_text(encoding="utf-8"))
    for field in STATUS_FIELDS:
        if field not in status:
            errors.append(f"status.json missing field: {field}")
    for glob, field, desc in FILE_COUNTS:
        found = len([p for p in root.glob(glob) if p.is_file()])
        want = status.get(field)
        if want is not None and found != want:
            errors.append(f"file count {desc}: disk={found} status.json {field}={want}")
    if REQUIRE_GREEN and status.get("tests_passed") is not None:
        if status["tests_passed"] != status.get("tests_total"):
            errors.append(
                f"suite not green in status.json: "
                f"{status['tests_passed']}/{status.get('tests_total')}")
    for rel in PROSE_FILES:
        path = root / rel
        if not path.exists():
            errors.append(f"checked file missing: {rel}")
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for regex, field, desc in PATTERNS:
                for m in re.finditer(regex, line):
                    window = line[max(0, m.start() - 30):m.end() + 30]
                    if any(a in window for a in ALLOWLIST):
                        continue
                    want = status.get(field)
                    if want is None:
                        continue  # field TO VERIFY: nothing to check against
                    try:
                        got = int(m.group(1))
                        if m.re.groups >= 2 and m.group(2) is not None:
                            got2 = int(m.group(2))
                            if got2 != want:
                                errors.append(
                                    f"{rel}:{lineno}: {desc} says {got}/{got2}, "
                                    f"status.json {field}={want}: {line.strip()[:140]}")
                                continue
                    except (IndexError, ValueError):
                        continue
                    if got != want:
                        errors.append(
                            f"{rel}:{lineno}: {desc} says {got}, "
                            f"status.json {field}={want}: {line.strip()[:140]}")
    if errors:
        print("FAIL: number check found disagreements:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PASS: all checked numbers agree with status.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
