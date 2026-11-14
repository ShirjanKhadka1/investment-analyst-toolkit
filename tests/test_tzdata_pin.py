"""Tests for the tzdata pin drift check (PR #30 close-out).

scripts/check_tzdata_pin.py pins the tzdata version in two places
together: .github/workflows/test.yml (CI installs the inline pin) and
requirements-tzdata.txt (the optional extra). The owner waived the
test.yml change, so the check — a qa gate plus these tests — is what
stops the two pins drifting apart. The version is never hard-coded
here: the tests build trees with known pins and assert the checker
compares what it reads.
"""
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

WORKFLOW = """name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pip install "pytest==9.2.0" "tzdata==2026.6"
"""

REQUIREMENTS = "tzdata==2026.6\n"


def _load_script(name):
    spec = importlib.util.spec_from_file_location(
        f"{name}_under_test", REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load_script("check_tzdata_pin")


def _tree(tmp_path, workflow=WORKFLOW, requirements=REQUIREMENTS):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    if workflow is not None:
        (wf / "test.yml").write_text(workflow, encoding="utf-8")
    if requirements is not None:
        (tmp_path / "requirements-tzdata.txt").write_text(
            requirements, encoding="utf-8")
    return tmp_path


def test_real_repo_pins_match():
    assert check.check(REPO_ROOT) == []
    assert check.pin_in_workflow(REPO_ROOT) == \
        check.pin_in_requirements(REPO_ROOT)


def test_main_exits_zero_on_real_repo(capsys):
    assert check.main(["--root", str(REPO_ROOT)]) == 0
    assert "PASS" in capsys.readouterr().out


def test_mismatch_fails(tmp_path):
    root = _tree(tmp_path, requirements="tzdata==2026.5\n")
    problems = check.check(root)
    assert len(problems) == 1
    assert "2026.6" in problems[0] and "2026.5" in problems[0]
    assert check.main(["--root", str(root)]) == 1


def test_missing_pin_in_workflow_fails(tmp_path):
    workflow = WORKFLOW.replace(' "tzdata==2026.6"', "")
    problems = check.check(_tree(tmp_path, workflow=workflow))
    assert len(problems) == 1
    assert ".github/workflows/test.yml" in problems[0]


def test_missing_pin_in_requirements_fails(tmp_path):
    problems = check.check(
        _tree(tmp_path, requirements="# no pin here\n"))
    assert len(problems) == 1
    assert "requirements-tzdata.txt" in problems[0]


def test_missing_workflow_file_fails(tmp_path):
    problems = check.check(_tree(tmp_path, workflow=None))
    assert len(problems) == 1
    assert ".github/workflows/test.yml" in problems[0]


def test_missing_requirements_file_fails(tmp_path):
    problems = check.check(_tree(tmp_path, requirements=None))
    assert len(problems) == 1
    assert "requirements-tzdata.txt" in problems[0]


def _load_qa():
    spec = importlib.util.spec_from_file_location(
        "qa_under_test", REPO_ROOT / "scripts" / "qa.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qa_registers_the_tzdata_pin_gate():
    qa = _load_qa()
    names = [name for name, _gate in qa.GATES]
    assert "tzdata-pin" in names
    assert names.index("tzdata-pin") > names.index("py310-imports")
    assert names.index("tzdata-pin") < names.index("tests")
