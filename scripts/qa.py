"""Quality-gate entry point (Phase 4): run every gate in order and stop
at the first failure.

Stdlib only. Gates run the same commands CI runs (see
.github/workflows/test.yml, quality job):
  lint         ruff check toolkit/ tests/ scripts/
  typecheck    mypy toolkit/
  py310-imports
               scripts/check_py310_imports.py (no unguarded imports of
               3.11+ stdlib modules, e.g. tomllib)
  tzdata-pin   scripts/check_tzdata_pin.py (tzdata pin in test.yml
               must equal requirements-tzdata.txt)
  tests        pytest tests/ -q, then coverage (fail_under from pyproject)
  lookahead    scripts/qa_lookahead.py (suites + leaking-variant mutation)
  licence      scripts/qa_licence.py
  secrets      scripts/qa_secrets.py (tree + git history)
  links        scripts/qa_links.py (internal must resolve; external best-effort)
  readme-claim scripts/qa_readme.py (regenerates status.json, checks numbers)
  counts       scripts/qa_counts.py (pack numbers in the docs must match
               a fresh recount of the finance-research-agents roster)
  capability-matrix
               scripts/generate_capability_matrix.py --check (the matrix
               doc must match what the generator renders)
  fresh-clone  scripts/qa_fresh_clone.py (clone + demo under 60 s)

Dev tools come from requirements-dev.txt (pinned versions identical
to CI). Runtime stays zero-dependency: nothing here adds a runtime
dependency.

Usage:
  python scripts/qa.py [--only GATE] [--skip GATE,...] [--offline]
                       [--list]
  --offline passes through to the link gate (skip external checks).
Exit 0 only when every run gate passes. Prints a per-gate PASS/FAIL
summary at the end.
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11: tomllib is stdlib only from 3.11.
    # ImportError (not ModuleNotFoundError) is deliberate: it also
    # covers the sys.modules["tomllib"] = None simulation used by the
    # regression test, which raises ImportError, not ModuleNotFoundError.
    tomllib = None

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _tool(name):
    """Prefer a binary on PATH, else `python -m <name>` (ruff/mypy/
    pytest/coverage are installed as both in CI and locally)."""
    binary = shutil.which(name)
    if binary:
        return [binary]
    return [sys.executable, "-m", name]


def _run(cmd, cwd=ROOT):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          check=False, timeout=900)


def gate_lint(_args):
    proc = _run(_tool("ruff") + ["check", "toolkit/", "tests/", "scripts/"])
    print(proc.stdout.strip() or proc.stderr.strip())
    return proc.returncode


def gate_typecheck(_args):
    proc = _run(_tool("mypy") + ["toolkit/"])
    print(proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else
          proc.stderr.strip())
    return proc.returncode


def _coverage_fail_under():
    """Read [tool.coverage.report] fail_under from pyproject.toml.

    tomllib is stdlib only from Python 3.11. On 3.10 fall back to a
    strict, section-scoped parse of the same file (only the
    [tool.coverage.report] section is read, and only an integer
    fail_under is accepted). Fails loudly if the value cannot be
    found — never silently defaults.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if tomllib is not None:
        return tomllib.loads(text)["tool"]["coverage"]["report"]["fail_under"]
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_section = bool(re.match(
                r"\[\s*tool\.coverage\.report\s*\]\s*(?:#.*)?$", line))
            continue
        if in_section:
            match = re.match(r"fail_under\s*=\s*(\d+)\s*(?:#.*)?$", line)
            if match:
                return int(match.group(1))
    raise ValueError(
        "could not read fail_under from pyproject.toml "
        "([tool.coverage.report] section missing or malformed)")


def gate_tests(_args):
    proc = _run(_tool("pytest") + ["tests/", "-q"])
    print(proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "")
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-2000:])
        return proc.returncode
    cov = _run(_tool("coverage") + ["run", "-m", "pytest", "tests/", "-q"])
    if cov.returncode != 0:
        print("coverage run failed:")
        print(cov.stdout[-2000:])
        print(cov.stderr[-2000:])
        return cov.returncode
    report = _run(_tool("coverage") + ["report"])
    total = None
    for line in report.stdout.splitlines():
        if line.startswith("TOTAL"):
            total = int(line.split()[-1].rstrip("%"))
    print(report.stdout.strip().splitlines()[-1] if report.stdout.strip() else "")
    fail_under = _coverage_fail_under()
    if total is None:
        print("could not parse coverage total")
        return 1
    if total < fail_under:
        print(f"coverage {total}% is below fail_under={fail_under}")
        return 1
    print(f"coverage {total}% (fail_under={fail_under})")
    return 0


def _script_gate(name):
    def gate(args):
        cmd = [sys.executable, str(SCRIPTS / name)]
        if name == "qa_links.py" and args.offline:
            cmd.append("--offline")
        proc = _run(cmd)
        print(proc.stdout.strip())
        if proc.stderr.strip():
            print(proc.stderr.strip()[-1000:])
        return proc.returncode
    return gate


def gate_capability_matrix(_args):
    proc = _run([sys.executable,
                 str(SCRIPTS / "generate_capability_matrix.py"), "--check"])
    print(proc.stdout.strip())
    if proc.stderr.strip():
        print(proc.stderr.strip()[-1000:])
    return proc.returncode


GATES = [
    ("lint", gate_lint),
    ("typecheck", gate_typecheck),
    ("py310-imports", _script_gate("check_py310_imports.py")),
    ("tzdata-pin", _script_gate("check_tzdata_pin.py")),
    ("tests", gate_tests),
    ("lookahead", _script_gate("qa_lookahead.py")),
    ("licence", _script_gate("qa_licence.py")),
    ("secrets", _script_gate("qa_secrets.py")),
    ("links", _script_gate("qa_links.py")),
    ("readme-claim", _script_gate("qa_readme.py")),
    ("counts", _script_gate("qa_counts.py")),
    ("capability-matrix", gate_capability_matrix),
    ("fresh-clone", _script_gate("qa_fresh_clone.py")),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default=None,
                        help="run a single gate by name")
    parser.add_argument("--skip", default="",
                        help="comma-separated gates to skip")
    parser.add_argument("--offline", action="store_true",
                        help="link gate: skip external checks")
    parser.add_argument("--list", action="store_true",
                        help="list gate names and exit")
    args = parser.parse_args()
    if args.list:
        for name, _gate in GATES:
            print(name)
        return 0
    skipped = {s.strip() for s in args.skip.split(",") if s.strip()}
    selected = [(n, g) for n, g in GATES
                if (args.only is None or n == args.only) and n not in skipped]
    if args.only and not any(n == args.only for n, _g in GATES):
        print(f"unknown gate: {args.only} (see --list)")
        return 2
    results = []
    for name, gate in selected:
        print(f"\n=== gate: {name} ===")
        try:
            code = gate(args)
        except Exception as exc:  # noqa: BLE001 — a crashing gate is a
            # failing gate by design: any exception here means the gate
            # did not pass, and qa.py must report it, not traceback.
            print(f"gate {name} raised {type(exc).__name__}: {exc}")
            code = 1
        status = "PASS" if code == 0 else "FAIL"
        print(f"[{status}] {name}")
        results.append((name, code))
        if code != 0:
            break  # fail fast: stop at the first failure
    print("\n=== qa summary ===")
    for name, code in results:
        print(f"  {'PASS' if code == 0 else 'FAIL'}  {name}")
    pending = [n for n, _g in GATES
               if n not in {r[0] for r in results}]
    for name in pending:
        print(f"  SKIP  {name} (stopped at first failure)")
    failed = [n for n, c in results if c != 0]
    if failed:
        print(f"qa FAILED at gate: {failed[0]}")
        return 1
    print("qa PASSED: all gates green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
