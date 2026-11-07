"""README-claim check (Phase 4 quality gate): every number in the README
must match the output of the script that generates it.

Stdlib only. Runs, in order:
1. scripts/generate_status.py — regenerates status.json from a live
   pytest run (never a typed constant), then
2. scripts/check_numbers.py — fails if any checked prose count in
   README.md disagrees with status.json, and
3. per-file test counts: every "tests/<file>.py (N tests)" claim in
   README.md is compared against a real `pytest --collect-only` count
   for that file.

This is the gate that prevents the stale "ruff clean" class of error
from recurring: a number may only change when the generator re-runs.

Usage: python3 scripts/qa_readme.py [--root REPO_ROOT]
Exit 0 only when the generator runs, the number check passes, and
every per-file count matches.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = "investment-analyst-toolkit"
# "tests/<file>.py (N tests)" and "tests/<file>.py`, N tests" forms.
PER_FILE = re.compile(r"tests/(test_[A-Za-z0-9_]+\.py)[` ]+(?:\(|, )(\d+) tests")


def run(cmd, root):
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True,
                          check=False, timeout=600)


def collect_counts(root):
    proc = run([sys.executable, "-m", "pytest", "tests/", "--collect-only",
                "-q"], root)
    if proc.returncode != 0:
        raise RuntimeError("pytest --collect-only failed:\n"
                           + (proc.stdout + proc.stderr)[-2000:])
    counts = {}
    for line in proc.stdout.splitlines():
        match = re.match(r"tests/(test_[A-Za-z0-9_]+\.py)::", line)
        if match:
            counts[match.group(1)] = counts.get(match.group(1), 0) + 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",
                        default=str(Path(__file__).resolve().parent.parent))
    root = Path(parser.parse_args().root)
    errors = []

    gen = run([sys.executable, "scripts/generate_status.py", "--root",
               str(root), "--repo", REPO], root)
    if gen.returncode != 0:
        print(gen.stdout[-2000:])
        print(gen.stderr[-2000:])
        errors.append("generate_status.py failed; status.json is stale")
    else:
        status = json.loads((root / "status.json").read_text(encoding="utf-8"))
        print(f"status.json regenerated: {status.get('tests_passed')}/"
              f"{status.get('tests_total')} tests, "
              f"coverage {status.get('coverage_percent')}%")

    check = run([sys.executable, "scripts/check_numbers.py", "--root",
                 str(root)], root)
    print(check.stdout.strip())
    if check.returncode != 0:
        errors.append("check_numbers.py failed (see above)")

    try:
        counts = collect_counts(root)
    except RuntimeError as exc:
        errors.append(str(exc))
        counts = {}
    readme = (root / "README.md").read_text(encoding="utf-8")
    claimed = {}
    for match in PER_FILE.finditer(readme):
        claimed[match.group(1)] = int(match.group(2))
    for name, want in sorted(claimed.items()):
        got = counts.get(name)
        if got is None:
            errors.append(f"README claims {name} ({want} tests) but the "
                          f"file has no collected tests")
        elif got != want:
            errors.append(f"README claims {name} has {want} tests; "
                          f"pytest collects {got}")
    if claimed:
        print(f"per-file counts checked: {len(claimed)} files, "
              f"{sum(claimed.values())} tests named in README")

    if errors:
        print("FAIL: README-claim check found drift:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("PASS: README numbers match the generators (status.json + "
          "per-file pytest counts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
