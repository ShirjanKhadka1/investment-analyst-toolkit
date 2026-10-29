#!/usr/bin/env python3
"""Round 3 status generator (pytest repos). Counts real test results,
never a typed constant.

Usage: python3 scripts/generate_status.py [--root REPO_ROOT]
Writes status.json next to the repo root: tests_passed/tests_total
parsed from an actual pytest run, version read from pyproject.toml
when the repo has one, coverage measured with coverage.py when it is
installed (otherwise coverage_percent is null and labelled TO VERIFY --
never guessed).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path


def run(cmd, cwd, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True)


def pytest_counts(root, test_dir, extra_env=None):
    """Return (passed, total) from a real pytest run, or raise."""
    r = run([sys.executable, "-m", "pytest", test_dir, "-q"], cwd=root, env=extra_env)
    out = r.stdout + r.stderr
    m = re.search(r"(\d+) passed", out)
    if not m:
        raise RuntimeError(f"pytest did not report a pass count:\n{out[-2000:]}")
    passed = int(m.group(1))
    f = re.search(r"(\d+) failed", out)
    total = passed + (int(f.group(1)) if f else 0)
    return passed, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--test-dir", default="tests")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--extra", default="{}",
                    help="JSON object of extra status fields computed by the caller (unused here)")
    args = ap.parse_args()
    root = Path(args.root)
    status = {
        "repo": args.repo,
        "generated_by": "scripts/generate_status.py (Round 3: pytest run + file reads)",
        "generated_at": date.today().isoformat(),
        "method": "tests counted from a live pytest run in this checkout; "
                  "version read from pyproject.toml when present",
    }
    
    import tomllib
    try:
        proj = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        status["version"] = proj["project"]["version"]
        status["coverage_fail_under"] = proj["tool"]["coverage"]["report"]["fail_under"]
    except Exception as exc:
        status["version"] = None
        status["version_note"] = f"TO VERIFY: could not read pyproject.toml ({exc})"

    passed, total = pytest_counts(root, args.test_dir,
                                  extra_env={"PYTHONPATH": str(root)} if True else None)
    status["tests_passed"] = passed
    status["tests_total"] = total
    if True:
        cov = run([sys.executable, "-m", "coverage", "run", "-m", "pytest", args.test_dir, "-q"],
                  cwd=root, env={"PYTHONPATH": str(root)})
        rep = run([sys.executable, "-m", "coverage", "report"], cwd=root)
        m = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", rep.stdout)
        if m:
            status["coverage_percent"] = int(m.group(1))
            status["coverage_note"] = "measured by coverage.py in this run"
        else:
            status["coverage_percent"] = None
            status["coverage_note"] = "TO VERIFY: coverage.py not installed or report unparsable"
    (root / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
