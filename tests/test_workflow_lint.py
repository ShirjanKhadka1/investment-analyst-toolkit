"""Workflow lint: every workflow file is valid YAML with the required
keys, deployed workflow names are unique, and every name in
failure-alert.yml's watch list matches a workflow that exists
(deployed or staged).

Runs wherever PyYAML is available: the quality job installs it from
requirements-dev.txt, so the lint is enforced there. The matrix and
full jobs install only pytest (+ tzdata / numpy), so this test is
skipped there — not failed — by pytest.importorskip below. That is
deliberate: the lint is a quality-gate check, not a matrix check.

Scope notes (read before "fixing" a failure by renaming):
- Uniqueness is enforced only within .github/workflows (the
  deployed set). ops/ready-for-browser holds staging copies and
  variants that share a name by design — e.g. the staged
  daily-track-record.yml is the same workflow as the deployed one,
  and yahoo-ci-probe.yml / yahoo-ci-probe-inputs.yml are two
  variants of the same probe (the inputs one deploys AS
  yahoo-ci-probe.yml). Renaming a staged file to force uniqueness
  would break the deploy step, not fix the lint.
- The watch-list check reads .github/workflows/failure-alert.yml
  and matches each listed name against the union of deployed and
  staged workflow names, so a not-yet-deployed workflow (e.g.
  "Daily volatility ledger") passes as long as its file is staged
  under ops/ready-for-browser.
"""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOYED_DIR = REPO_ROOT / ".github" / "workflows"
STAGED_DIR = REPO_ROOT / "ops" / "ready-for-browser"
FAILURE_ALERT = DEPLOYED_DIR / "failure-alert.yml"


def _load_workflow(path):
    """Parse one workflow file. Returns the document dict."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), f"{path}: not a YAML mapping"
    return doc


def _trigger(doc, path):
    """The `on:` trigger — PyYAML (YAML 1.1) reads the key `on` as
    boolean True, so accept either spelling."""
    if "on" in doc:
        return doc["on"]
    assert True in doc, f"{path}: missing required key: on"
    return doc[True]


def _workflows(directory):
    """(filename, document) for every .yml file in a directory."""
    out = []
    for path in sorted(directory.glob("*.yml")):
        out.append((path.name, _load_workflow(path)))
    assert out, f"no workflow files in {directory}"
    return out


def _name(doc, path):
    name = doc.get("name")
    assert isinstance(name, str) and name.strip(), \
        f"{path}: name must be a non-empty string"
    return name


def test_all_workflow_files_are_valid_yaml_with_required_keys():
    for directory in (DEPLOYED_DIR, STAGED_DIR):
        for filename, doc in _workflows(directory):
            path = f"{directory.name}/{filename}"
            for key in ("name", "jobs"):
                assert key in doc, f"{path}: missing required key: {key}"
            _trigger(doc, path)
            _name(doc, path)
            assert isinstance(doc["jobs"], dict) and doc["jobs"], \
                f"{path}: jobs must be a non-empty mapping"


def test_deployed_workflow_names_are_unique():
    names = {}
    for filename, doc in _workflows(DEPLOYED_DIR):
        name = _name(doc, filename)
        assert name not in names, \
            f"duplicate deployed workflow name {name!r}: " \
            f"{names[name]} and {filename}"
        names[name] = filename


def test_failure_alert_watch_list_matches_existing_workflows():
    alert = _load_workflow(FAILURE_ALERT)
    trigger = _trigger(alert, "failure-alert.yml")
    assert isinstance(trigger, dict) and "workflow_run" in trigger, \
        "failure-alert.yml: expected an on.workflow_run trigger"
    watch = trigger["workflow_run"].get("workflows")
    assert isinstance(watch, list) and watch, \
        "failure-alert.yml: workflow_run.workflows must be a " \
        "non-empty list"
    existing = set()
    for directory in (DEPLOYED_DIR, STAGED_DIR):
        for filename, doc in _workflows(directory):
            existing.add(_name(doc, filename))
    missing = [name for name in watch if name not in existing]
    assert not missing, \
        "failure-alert.yml watches " + str(missing) + " but no " \
        "workflow with that name exists in .github/workflows or " \
        "ops/ready-for-browser"
