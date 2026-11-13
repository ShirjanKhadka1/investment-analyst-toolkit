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

try:
    import tomllib
except ImportError:  # Python < 3.11: tomllib is stdlib only from 3.11.
    tomllib = None


def _section_value(text, section, key):
    """Strict, section-scoped parse of one ``key = value`` line.

    Only the named [section] is read; returns the raw value string, or
    None when the section or the key is absent. Used by the Python 3.10
    fallback in _pyproject_values (tomllib is stdlib only from 3.11).
    """
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_section = bool(re.match(
                r"\[\s*" + re.escape(section) + r"\s*\]\s*(?:#.*)?$",
                line))
            continue
        if in_section:
            match = re.match(rf"{re.escape(key)}\s*=\s*(.+?)\s*(?:#.*)?$",
                             line)
            if match:
                return match.group(1)
    return None


def _pyproject_values(root):
    """Return (version, coverage fail_under) from pyproject.toml.

    tomllib is stdlib only from Python 3.11. On 3.10 fall back to a
    strict, section-scoped parse of the same file (only the [project]
    version and the [tool.coverage.report] fail_under are read).
    Raises ValueError if either value cannot be found — never silently
    defaults.
    """
    text = (Path(root) / "pyproject.toml").read_text(encoding="utf-8")
    if tomllib is not None:
        proj = tomllib.loads(text)
        return (proj["project"]["version"],
                proj["tool"]["coverage"]["report"]["fail_under"])
    version_raw = _section_value(text, "project", "version")
    fail_under_raw = _section_value(text, "tool.coverage.report",
                                    "fail_under")
    version_match = (re.fullmatch(r'"([^"]+)"', version_raw or "")
                     or re.fullmatch(r"'([^']+)'", version_raw or ""))
    fail_under_match = re.fullmatch(r"(\d+)", fail_under_raw or "")
    if version_match is None or fail_under_match is None:
        raise ValueError(
            "could not read version/fail_under from pyproject.toml "
            "([project] version or [tool.coverage.report] fail_under "
            "missing or malformed)")
    return version_match.group(1), int(fail_under_match.group(1))


def run(cmd, cwd, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(cmd, check=False, cwd=cwd, env=e, capture_output=True, text=True)


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
    
    try:
        version, fail_under = _pyproject_values(root)
        status["version"] = version
        status["coverage_fail_under"] = fail_under
    except (OSError, KeyError, ValueError) as exc:
        status["version"] = None
        status["version_note"] = f"TO VERIFY: could not read pyproject.toml ({exc})"

    passed, total = pytest_counts(root, args.test_dir,
                                  extra_env={"PYTHONPATH": str(root)} if True else None)
    status["tests_passed"] = passed
    status["tests_total"] = total
    if True:
        run([sys.executable, "-m", "coverage", "run", "-m", "pytest", args.test_dir, "-q"],
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
