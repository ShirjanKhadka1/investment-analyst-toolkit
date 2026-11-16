"""Tests for the wall-clock gate (Round E).

scripts/qa_wall_clock.py is an AST check over tests/: a call to
datetime.now, datetime.today or time.time inside a test is a
violation unless the clock is injected in that test's scope — via a
clock parameter, a freeze_time/frozen/clock decorator, a
freeze_time/freezer call, or a monkeypatch of a clock attribute.
Passing an explicit now=/clock= keyword to the code under test does
NOT excuse a bare wall-clock call in the same test. Module-level
calls are always violations. Production code is out of scope.
"""
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_script(name):
    spec = importlib.util.spec_from_file_location(
        f"{name}_under_test", REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_script("qa_wall_clock")


def _tree(tmp_path, files):
    tests = tmp_path / "tests"
    tests.mkdir()
    for name, text in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


def test_real_repo_is_clean():
    assert gate.check(REPO_ROOT) == []


def test_main_exits_zero_on_real_repo(capsys):
    assert gate.main(["--root", str(REPO_ROOT)]) == 0
    assert "PASS" in capsys.readouterr().out


def test_bare_datetime_now_fails(tmp_path):
    root = _tree(tmp_path, {"tests/test_x.py": """\
import datetime


def test_uses_now():
    stamp = datetime.datetime.now(datetime.timezone.utc)
    assert stamp is not None
"""})
    problems = gate.check(root)
    assert len(problems) == 1
    assert "test_x.py:5" in problems[0]
    assert "test_uses_now" in problems[0]
    assert "datetime.now" in problems[0]
    assert gate.main(["--root", str(root)]) == 1


def test_bare_datetime_today_fails(tmp_path):
    root = _tree(tmp_path, {"tests/test_x.py": """\
import datetime


def test_uses_today():
    assert datetime.date.today().year > 2000
    assert datetime.datetime.today().year > 2000
"""})
    problems = gate.check(root)
    assert len(problems) == 1
    assert "datetime.today" in problems[0]


def test_bare_time_time_fails(tmp_path):
    root = _tree(tmp_path, {"tests/test_x.py": """\
import time


def test_uses_time():
    assert time.time() > 0
    time.sleep(0)  # sleeping is fine: only time.time is flagged
"""})
    problems = gate.check(root)
    assert len(problems) == 1
    assert "time.time" in problems[0]


def test_bare_time_imported_from_time_fails(tmp_path):
    root = _tree(tmp_path, {"tests/test_x.py": """\
from time import time


def test_uses_time():
    assert time() > 0
"""})
    assert len(gate.check(root)) == 1


def test_module_level_call_fails(tmp_path):
    root = _tree(tmp_path, {"tests/test_x.py": """\
import datetime

STAMP = datetime.datetime.now(datetime.timezone.utc)


def test_uses_stamp():
    assert STAMP is not None
"""})
    problems = gate.check(root)
    assert len(problems) == 1
    assert "<module>" in problems[0]


def test_freeze_time_decorator_passes(tmp_path):
    root = _tree(tmp_path, {"tests/test_x.py": """\
import datetime

from freezegun import freeze_time


@freeze_time("2026-11-15")
def test_frozen():
    assert datetime.datetime.now().year == 2026
"""})
    assert gate.check(root) == []


def test_freezer_fixture_passes(tmp_path):
    root = _tree(tmp_path, {"tests/test_x.py": """\
import datetime


def test_frozen(freezer):
    freezer.move_to("2026-11-15")
    assert datetime.datetime.now().year == 2026
"""})
    assert gate.check(root) == []


def test_clock_parameter_passes(tmp_path):
    root = _tree(tmp_path, {"tests/test_x.py": """\
import datetime


def test_with_clock(clock):
    assert datetime.datetime.now() is not None
    assert clock is not None
"""})
    assert gate.check(root) == []


def test_monkeypatched_clock_passes(tmp_path):
    root = _tree(tmp_path, {"tests/test_x.py": """\
import datetime


def test_patched(monkeypatch):
    monkeypatch.setattr(datetime.datetime, "now",
                        lambda tz=None: datetime.datetime(2026, 11, 15))
    assert datetime.datetime.now().year == 2026
"""})
    assert gate.check(root) == []


def test_explicit_now_kwarg_does_not_excuse_bare_call(tmp_path):
    # Passing now= to the code under test does not inject the clock
    # for the test's own datetime.now() call: the call still reads
    # the wall clock, so it is still a violation.
    root = _tree(tmp_path, {"tests/test_x.py": """\
import datetime


def stamp(now):
    return now.isoformat()


def test_passes_now():
    expected = datetime.datetime.now(datetime.timezone.utc)
    assert stamp(now=expected) == expected.isoformat()
"""})
    problems = gate.check(root)
    assert len(problems) == 1
    assert "test_passes_now" in problems[0]


def test_production_code_is_out_of_scope(tmp_path):
    root = _tree(tmp_path, {
        "toolkit/stamper.py": """\
import datetime


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
""",
        "tests/test_x.py": """\
def test_clean():
    assert 1 + 1 == 2
""",
    })
    assert gate.check(root) == []


def _load_qa():
    spec = importlib.util.spec_from_file_location(
        "qa_under_test", REPO_ROOT / "scripts" / "qa.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qa_registers_the_wall_clock_gate():
    qa = _load_qa()
    names = [name for name, _gate in qa.GATES]
    assert len(names) == 15
    assert "wall-clock" in names
    assert names.index("wall-clock") > names.index("lookahead")
    assert names.index("wall-clock") < names.index("licence")
    assert "evidence-ratchet" in names
    assert names.index("evidence-ratchet") > names.index(
        "capability-matrix")
    assert names.index("evidence-ratchet") < names.index("fresh-clone")
