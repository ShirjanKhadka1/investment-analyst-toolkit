"""Wall-clock gate (Round E quality gate): tests must not read the
wall clock unless the clock is injected.

Stdlib only. An AST check over tests/: every call to datetime.now,
datetime.today or time.time inside a test file is a violation UNLESS
the clock is injected in the enclosing test scope.

What "injected" means, precisely — the clock of the enclosing test
function (or fixture/helper) is controlled when at least one of
these holds for that scope:
1. Clock parameter — the function takes a parameter named ``clock``,
   ``now``, ``frozen_time``, ``frozen_datetime`` or ``freezer``
   (an explicit clock or a freezegun fixture passed in).
2. Freeze decorator — a decorator on the function mentions
   ``freeze_time``, ``frozen`` or ``clock`` (e.g. ``@freeze_time(...)``
   or ``@pytest.mark.freeze_time``).
3. Freeze call — the function body calls ``freeze_time(...)`` or uses
   the ``freezer`` fixture (e.g. ``freezer.move_to(...)``).
4. Clock monkeypatch — the function body calls ``monkeypatch.setattr``
   whose target is a clock attribute: the dotted target's last
   segment is one of ``now``, ``today``, ``utcnow``, ``time``,
   ``clock`` or ``monotonic``, or the target mentions ``freeze``.
   Patching the clock makes a later bare call return the patched
   value, so it counts as injected.

What does NOT count as injected: passing an explicit ``now=`` /
``clock=`` keyword argument to the code under test does not excuse
a bare datetime.now() / datetime.today() / time.time() call in the
same test — the call itself still reads the wall clock. A bare call
at module level (outside any function) is always a violation: there
is no scope in which the clock could be injected.

Recognised call spellings (AST, not text search): ``datetime.now``,
``datetime.today`` and ``datetime.datetime.now`` / ``.today`` (the
``datetime`` module, or ``datetime`` imported from it), ``time.time``
(``import time``), and bare ``time()`` when the file has
``from time import time``. Aliased imports (``import datetime as dt``)
are not resolved and are therefore not flagged; keep the plain
spellings in tests so the gate can see them.

Scope: tests/ only. Production code (toolkit/, scripts/) reads the
wall clock legitimately — stamping records, cache TTLs — and is out
of scope for this gate.

Usage: python3 scripts/qa_wall_clock.py [--root REPO_ROOT]
Exit 0 when no violation is found; exit 1 with one line per
violation (path:line: enclosing scope: call).
"""
import argparse
import ast
from pathlib import Path

CLOCK_PARAM_NAMES = ("clock", "now", "frozen_time", "frozen_datetime",
                     "freezer")
CLOCK_ATTR_NAMES = ("now", "today", "utcnow", "time", "clock", "monotonic")


def _is_datetime_now_or_today(node):
    """True for datetime.now / datetime.today / datetime.datetime.now
    / datetime.datetime.today calls."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in ("now", "today"):
        return False
    value = func.value
    if isinstance(value, ast.Name) and value.id == "datetime":
        return True
    return (isinstance(value, ast.Attribute)
            and value.attr == "datetime"
            and isinstance(value.value, ast.Name)
            and value.value.id == "datetime")


def _is_time_time(node, imports_time_name):
    """True for time.time() calls, and bare time() when the file has
    ``from time import time``."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return (func.attr == "time"
                and isinstance(func.value, ast.Name)
                and func.value.id == "time")
    if isinstance(func, ast.Name) and func.id == "time":
        return imports_time_name
    return False


def _is_wall_clock_call(node, imports_time_name):
    return (_is_datetime_now_or_today(node)
            or _is_time_time(node, imports_time_name))


def _decorator_mentions_freeze_or_clock(decorator):
    try:
        text = ast.unparse(decorator)
    except ValueError:
        text = ""
    lowered = text.lower()
    return ("freeze_time" in lowered or "frozen" in lowered
            or "clock" in lowered)


def _call_is_freeze_time(node):
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "freeze_time"
    if isinstance(func, ast.Attribute):
        return func.attr == "freeze_time"
    return False


def _call_is_clock_monkeypatch(node):
    """True for monkeypatch.setattr(...) whose target is a clock
    attribute (see the module docstring, rule 4)."""
    func = node.func
    if not (isinstance(func, ast.Attribute)
            and func.attr == "setattr"
            and isinstance(func.value, ast.Name)
            and func.value.id == "monkeypatch"):
        return False
    targets = []
    for arg in node.args[:2]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            targets.append(arg.value)
        elif isinstance(arg, ast.Attribute):
            targets.append(arg.attr)
    for target in targets:
        last = target.rsplit(".", 1)[-1]
        if last in CLOCK_ATTR_NAMES or "freeze" in target.lower():
            return True
    return False


def _scope_is_injected(func_node):
    """True when the clock is injected in this function scope: a
    clock parameter, a freeze/clock decorator, a freeze_time/freezer
    call, or a monkeypatch of a clock attribute in the body."""
    for arg in list(func_node.args.args) + func_node.args.kwonlyargs:
        if arg.arg in CLOCK_PARAM_NAMES:
            return True
    if func_node.args.vararg is not None and \
            func_node.args.vararg.arg in CLOCK_PARAM_NAMES:
        return True
    for decorator in func_node.decorator_list:
        if _decorator_mentions_freeze_or_clock(decorator):
            return True
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            if _call_is_freeze_time(node):
                return True
            if _call_is_clock_monkeypatch(node):
                return True
        elif isinstance(node, ast.Name) and node.id == "freezer":
            return True
    return False


def _file_imports_time_name(tree):
    for node in ast.walk(tree):
        if (isinstance(node, ast.ImportFrom) and node.module == "time"
                and any(alias.name == "time" for alias in node.names)):
            return True
    return False


def _check_file(path, root):
    """Return the violation lines for one test file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"),
                         filename=str(path))
    except SyntaxError as exc:
        return [(f"{path.relative_to(root)}: "
                 f"unparsable test file: {exc}")]
    imports_time_name = _file_imports_time_name(tree)
    problems = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope_stack = []

        def _scope_name(self):
            if not self.scope_stack:
                return "<module>"
            return self.scope_stack[-1].name

        def visit_FunctionDef(self, node):
            self.scope_stack.append(node)
            self.generic_visit(node)
            self.scope_stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            if _is_wall_clock_call(node, imports_time_name):
                scope = (self.scope_stack[-1]
                         if self.scope_stack else None)
                injected = (scope is not None
                            and _scope_is_injected(scope))
                if not injected:
                    try:
                        call = ast.unparse(node.func)
                    except ValueError:
                        call = "wall-clock call"
                    problems.append(
                        f"{path.relative_to(root)}:{node.lineno}: "
                        f"{self._scope_name()}: bare {call}() with no "
                        f"injected clock")
            self.generic_visit(node)

    _Visitor().visit(tree)
    return problems


def check(root):
    """Return the list of wall-clock violations under root/tests."""
    tests_dir = Path(root) / "tests"
    problems = []
    if not tests_dir.is_dir():
        return [f"{tests_dir}: tests/ directory missing"]
    for path in sorted(tests_dir.rglob("*.py")):
        problems.extend(_check_file(path, Path(root)))
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",
                        default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args(argv)
    problems = check(Path(args.root))
    if problems:
        print("\n".join(problems))
        print(f"FAIL: wall-clock gate found {len(problems)} violation(s) — "
              "tests must not call datetime.now / datetime.today / "
              "time.time unless the clock is injected")
        return 1
    print("PASS: wall-clock gate clean (no bare wall-clock calls in "
          "tests/ without an injected clock)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
