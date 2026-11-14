"""Pin check: the tzdata version in CI must equal the optional extra.

Stdlib only. The tzdata pin lives in two places:
  - .github/workflows/test.yml (the CI test job installs the inline
    pin), and
  - requirements-tzdata.txt (the optional extra for systems with no
    IANA time-zone database).
The owner waived the test.yml change (PR #30 close-out): the CI line
keeps its inline pin. This check is what stops the two pins drifting
apart — it fails when they differ, or when either pin is missing.

Usage:
  python3 scripts/check_tzdata_pin.py [--root REPO_ROOT]
Exit 0 when both pins exist and are equal, 1 otherwise (and a line
naming each pin found).
"""
import argparse
import re
from pathlib import Path

WORKFLOW = Path(".github/workflows/test.yml")
REQUIREMENTS = Path("requirements-tzdata.txt")
_PIN = re.compile(r"tzdata==([A-Za-z0-9_.\-+]+)")


def _pin_in_text(text):
    match = _PIN.search(text)
    return match.group(1) if match else None


def pin_in_workflow(root):
    """The tzdata pin in the CI test job, or None when it is missing."""
    path = Path(root) / WORKFLOW
    if not path.exists():
        return None
    return _pin_in_text(path.read_text(encoding="utf-8"))


def pin_in_requirements(root):
    """The tzdata pin in the optional extra, or None when missing."""
    path = Path(root) / REQUIREMENTS
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pin = _pin_in_text(stripped)
        if pin is not None:
            return pin
    return None


def check(root):
    """Return a list of problems (empty when the pins match)."""
    root = Path(root)
    problems = []
    workflow_pin = pin_in_workflow(root)
    requirements_pin = pin_in_requirements(root)
    if workflow_pin is None:
        problems.append(f"{WORKFLOW.as_posix()}: no tzdata== pin found")
    if requirements_pin is None:
        problems.append(
            f"{REQUIREMENTS.as_posix()}: no tzdata== pin found")
    if (workflow_pin is not None and requirements_pin is not None
            and workflow_pin != requirements_pin):
        problems.append(
            f"tzdata pin drift: {WORKFLOW.as_posix()} has "
            f"tzdata=={workflow_pin}, {REQUIREMENTS.as_posix()} has "
            f"tzdata=={requirements_pin}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",
                        default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args(argv)
    problems = check(args.root)
    if problems:
        print("FAIL: tzdata pin drift:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"PASS: tzdata pin matches: "
          f"tzdata=={pin_in_workflow(args.root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
