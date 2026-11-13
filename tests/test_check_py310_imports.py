"""Tests for the Python 3.10 stdlib-import guard (small PR, Round 6 closeout).

scripts/check_py310_imports.py is an AST checker: it fails on imports of
3.11+ stdlib modules (today: tomllib) that are not guarded by a
try/except ImportError (or ModuleNotFoundError) or by an explicit
sys.version_info check proving the branch runs on 3.11+. These tests pin
that contract on small trees, prove the real repo tree is clean, and
prove the scripts/generate_status.py fallback reads pyproject.toml
without tomllib.
"""
import importlib.util
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_script(name):
    spec = importlib.util.spec_from_file_location(
        f"{name}_under_test", REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load_script("check_py310_imports")


def _check_source(code, tmp_path, name="sample.py"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    return check.check_file(path)


def test_catches_bare_tomllib_import(tmp_path):
    violations = _check_source("import tomllib\n", tmp_path)
    assert len(violations) == 1
    _path, lineno, module, minimum = violations[0]
    assert lineno == 1
    assert module == "tomllib"
    assert minimum == (3, 11)


def test_catches_from_tomllib_import(tmp_path):
    violations = _check_source("from tomllib import loads\n", tmp_path)
    assert len(violations) == 1
    assert violations[0][2] == "tomllib"


def test_catches_import_inside_function(tmp_path):
    violations = _check_source(
        "def f():\n    import tomllib\n", tmp_path)
    assert len(violations) == 1


def test_passes_try_except_import_error(tmp_path):
    # The bf05e5e pattern in scripts/qa.py.
    assert _check_source(
        "try:\n    import tomllib\n"
        "except ImportError:\n    tomllib = None\n", tmp_path) == []


def test_passes_try_except_module_not_found(tmp_path):
    # The tests/test_mcp_server.py pattern.
    assert _check_source(
        "try:\n    import tomllib\n"
        "except ModuleNotFoundError:\n    tomllib = None\n", tmp_path) == []


def test_passes_version_info_check(tmp_path):
    assert _check_source(
        "import sys\n"
        "if sys.version_info >= (3, 11):\n    import tomllib\n",
        tmp_path) == []


def test_passes_version_info_check_reversed(tmp_path):
    assert _check_source(
        "import sys\n"
        "if (3, 11) <= sys.version_info:\n    import tomllib\n",
        tmp_path) == []


def test_passes_version_info_check_with_patch_tuple(tmp_path):
    assert _check_source(
        "import sys\n"
        "if sys.version_info >= (3, 11, 0):\n    import tomllib\n",
        tmp_path) == []


def test_passes_sliced_gt_3_10(tmp_path):
    # On the (major, minor) pair there is no micro version left:
    # > (3, 10) means >= (3, 11).
    assert _check_source(
        "import sys\n"
        "if sys.version_info[:2] > (3, 10):\n    import tomllib\n",
        tmp_path) == []


def test_flags_version_check_else_branch(tmp_path):
    violations = _check_source(
        "import sys\n"
        "if sys.version_info >= (3, 11):\n    pass\n"
        "else:\n    import tomllib\n", tmp_path)
    assert len(violations) == 1


def test_flags_lt_branch_else_is_guarded(tmp_path):
    # The else of `< (3, 11)` IS the 3.11+ branch.
    assert _check_source(
        "import sys\n"
        "if sys.version_info < (3, 11):\n    pass\n"
        "else:\n    import tomllib\n", tmp_path) == []


def test_gt_3_10_does_not_guard(tmp_path):
    # 3.10.5 is greater than (3, 10) but still below 3.11: the floor
    # this proves is (3, 10), so the import must still be flagged.
    violations = _check_source(
        "import sys\n"
        "if sys.version_info > (3, 10):\n    import tomllib\n",
        tmp_path)
    assert len(violations) == 1


def test_flags_try_guard_not_covering_deferred_import(tmp_path):
    # The handler is gone by the time the function body runs.
    violations = _check_source(
        "try:\n    import tomllib\n"
        "    def f():\n        import tomllib\n"
        "except ImportError:\n    tomllib = None\n", tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == 4


def test_passes_version_guard_covering_deferred_import(tmp_path):
    # The definition itself is conditional on the version.
    assert _check_source(
        "import sys\n"
        "if sys.version_info >= (3, 11):\n"
        "    def f():\n        import tomllib\n", tmp_path) == []


def test_try_except_other_error_does_not_guard(tmp_path):
    violations = _check_source(
        "try:\n    import tomllib\n"
        "except ValueError:\n    pass\n", tmp_path)
    assert len(violations) == 1


def test_ignores_unrelated_imports(tmp_path):
    assert _check_source(
        "import json\nimport sys\nfrom pathlib import Path\n",
        tmp_path) == []


def test_passes_type_checking_block(tmp_path):
    assert _check_source(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    import tomllib\n", tmp_path) == []


def test_passes_real_qa_py():
    # The guarded import from the bf05e5e fix must pass the check.
    assert check.check_file(REPO_ROOT / "scripts" / "qa.py") == []


def test_passes_real_generate_status_py():
    # The generate_status.py fix in this PR must pass the check.
    assert check.check_file(
        REPO_ROOT / "scripts" / "generate_status.py") == []


def test_real_repo_tree_is_clean():
    assert check.check_tree(REPO_ROOT) == []


def test_main_exits_zero_on_clean_tree(tmp_path, capsys):
    (tmp_path / "toolkit").mkdir()
    (tmp_path / "toolkit" / "a.py").write_text("import json\n",
                                               encoding="utf-8")
    assert check.main(["--root", str(tmp_path)]) == 0
    assert "PASS" in capsys.readouterr().out


def test_main_exits_one_on_violation(tmp_path, capsys):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_x.py").write_text("import tomllib\n",
                                         encoding="utf-8")
    assert check.main(["--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "tests/test_x.py:1" in out
    assert "tomllib" in out


def _load_generate_status_without_tomllib():
    """Load scripts/generate_status.py with tomllib blocked, simulating
    Python 3.10 (tomllib is stdlib only from 3.11)."""
    with mock.patch.dict(sys.modules, {"tomllib": None}):
        return _load_script("generate_status")


def test_generate_status_reads_pyproject_without_tomllib():
    # Regression test for the unguarded import this PR fixes: the
    # module must import and still read the real values via fallback.
    gen = _load_generate_status_without_tomllib()
    assert gen.tomllib is None
    assert gen._pyproject_values(REPO_ROOT) == ("0.9.0", 80)


def test_generate_status_fallback_fails_loudly(tmp_path):
    gen = _load_generate_status_without_tomllib()
    (tmp_path / "pyproject.toml").write_text(
        "[tool.coverage.report]\nshow_missing = true\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="could not read version"):
        gen._pyproject_values(tmp_path)
