"""AST check: no unguarded imports of 3.11+ stdlib modules (Phase 4 gate).

Stdlib only. Scans toolkit/, scripts/ and tests/ for imports of modules
that only exist from a given Python version (see GUARDED_MODULES) and
fails on any import that is not guarded.

A guarded import is one of:
  - inside a try block whose handlers catch ImportError or
    ModuleNotFoundError (the bf05e5e pattern in scripts/qa.py), or
  - inside a branch that provably runs on a Python version at or above
    the module's minimum (e.g. ``if sys.version_info >= (3, 11): ...``),
    or
  - inside an ``if TYPE_CHECKING:`` block (never executed at runtime).

Why this exists: tomllib is stdlib only from Python 3.11. An unguarded
``import tomllib`` passes on 3.11/3.12 and fails the whole 3.10 CI job
(PR #26, job test ubuntu-latest 3.10: 1 failed, 658 passed). The 3.10
job was the only thing that caught it, and only after the fact — this
check catches the same class of bug before CI. It bit twice: the
pre-existing import in scripts/qa.py, and the unguarded one in
scripts/generate_status.py (fixed in the same PR as this checker).

Guard rules, pinned by tests/test_check_py310_imports.py:
  - A version check proves a *floor*: the branch runs on Python >=
    that floor. An import is guarded when the module's minimum is at
    or below the enclosing floor. ``> (3, 10)`` on the full 5-tuple
    proves only a (3, 10) floor (3.10.5 is greater than (3, 10) but
    still below 3.11), so it does not guard a tomllib import.
  - A try/except guard covers imports in the try body in the same
    scope. It does NOT cover an import inside a function or class
    defined within the try body: the import runs at call time, after
    the handler is gone.
  - A version guard DOES cover imports inside functions defined in
    the guarded branch: the definition itself is conditional on the
    version, so the import can only run on a new enough Python.
  - Only ImportError / ModuleNotFoundError handlers count. A handler
    for any other exception does not guard the import.
  - The else branch of ``if sys.version_info < (3, 11):`` is the
    3.11+ branch, so an import there is guarded.

Not covered (documented, not a bug): dynamic imports
(``importlib.import_module("tomllib")``) and attribute use
(``datetime.UTC``) are not imports and are not scanned.

Usage:
  python3 scripts/check_py310_imports.py [--root REPO_ROOT]
Exit 0 when no unguarded import is found, 1 otherwise (and a list of
each violation, ``path:line: ...``).
"""
import argparse
import ast
from pathlib import Path

# module -> first Python version that ships it in the stdlib.
# Data-driven: add a row (never a special case) when the next 3.11+
# module is needed. Only modules the repo actually imports belong here.
GUARDED_MODULES = {
    "tomllib": (3, 11),
}

SCAN_DIRS = ("toolkit", "scripts", "tests")
GUARD_EXCEPTIONS = ("ImportError", "ModuleNotFoundError")
# A TYPE_CHECKING body never executes at runtime: any version floor.
TYPE_CHECKING_FLOOR = (99, 99)


def _top_module(name):
    """Top-level module of an import name (``tomllib.foo`` -> ``tomllib``)."""
    return name.split(".")[0]


def _is_version_info(node):
    """True when node is ``sys.version_info`` (or ``version_info``)."""
    if isinstance(node, ast.Attribute):
        return (node.attr == "version_info"
                and isinstance(node.value, ast.Name)
                and node.value.id == "sys")
    if isinstance(node, ast.Name):
        return node.id == "version_info"
    return False


def _version_tuple(node):
    """Tuple of ints from a ``(3, 11)`` literal, else None."""
    if not isinstance(node, ast.Tuple):
        return None
    values = []
    for elt in node.elts:
        if not isinstance(elt, ast.Constant) or not isinstance(elt.value, int):
            return None
        values.append(elt.value)
    return tuple(values)


def _sliced_to_two(node):
    """True when node is ``sys.version_info[:2]`` (a (major, minor) pair)."""
    if not isinstance(node, ast.Subscript):
        return False
    if not _is_version_info(node.value):
        return False
    sl = node.slice
    if isinstance(sl, ast.Constant):
        return sl.value == 2
    if isinstance(sl, ast.Slice):
        return (sl.lower is None
                and isinstance(sl.upper, ast.Constant)
                and sl.upper.value == 2)
    return False


def _floor(const):
    """Lower bound (major, minor) a comparison constant proves."""
    return tuple(const[:2])


def _compare_proof(test):
    """Proof from one version comparison, or None.

    Returns (branch, floor): branch is "body" when the if-body runs
    on Python >= floor, "orelse" when the else branch does. A floor is
    a *lower bound* only: ``> (3, 10)`` proves (3, 10), not (3, 11),
    because 3.10.5 is greater than (3, 10) but still below 3.11.
    """
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return None
    op = test.ops[0]
    left, right = test.left, test.comparators[0]
    if _is_version_info(left) or _sliced_to_two(left):
        flipped = False
    elif _is_version_info(right) or _sliced_to_two(right):
        left, right = right, left
        flipped = True
    else:
        return None
    const = _version_tuple(right)
    if const is None:
        return None
    sliced = _sliced_to_two(left)
    if flipped:
        # (3, 11) <= version_info is version_info >= (3, 11).
        flipped_op = {ast.GtE: ast.LtE, ast.Gt: ast.Lt,
                      ast.LtE: ast.GtE, ast.Lt: ast.Gt}.get(type(op))
        if flipped_op is None:
            return None
        op = flipped_op()
    if isinstance(op, ast.GtE):
        return ("body", _floor(const))
    if isinstance(op, ast.Gt):
        if sliced and len(const) >= 2:
            # On the (major, minor) pair there is no micro version
            # left: > (3, 10) means >= (3, 11).
            return ("body", (const[0], const[1] + 1))
        return ("body", _floor(const))
    if isinstance(op, ast.Lt):
        return ("orelse", _floor(const))
    if isinstance(op, ast.LtE):
        if sliced and len(const) >= 2:
            return ("orelse", (const[0], const[1] + 1))
        return ("orelse", _floor(const))
    return None


def _version_proof(test):
    """Proof from an if-test, or None: (branch, floor).

    Handles a direct comparison, ``not`` of one, and ``and``/``or``
    combinations (``and``: the body runs only when every conjunct
    holds, so its floor is the max of the conjuncts' body floors;
    ``or``: the else branch runs only when every disjunct fails, so
    its floor is the max of the disjuncts' else floors).
    """
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        proof = _version_proof(test.operand)
        if proof is None:
            return None
        branch, floor = proof
        return ("orelse" if branch == "body" else "body", floor)
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        floors = [p[1] for v in test.values
                  if (p := _compare_proof(v)) is not None
                  and p[0] == "body"]
        return ("body", max(floors)) if floors else None
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        floors = [p[1] for v in test.values
                  if (p := _compare_proof(v)) is not None
                  and p[0] == "orelse"]
        return ("orelse", max(floors)) if floors else None
    return _compare_proof(test)


def _is_type_checking(test):
    """True for ``if TYPE_CHECKING:`` / ``if typing.TYPE_CHECKING:``."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return (isinstance(test, ast.Attribute)
            and test.attr == "TYPE_CHECKING")


def _handler_guards(handler):
    """True when an except handler catches ImportError/ModuleNotFoundError."""
    exc = handler.type
    if exc is None:
        return False
    if isinstance(exc, ast.Name):
        return exc.id in GUARD_EXCEPTIONS
    if isinstance(exc, ast.Tuple):
        return any(isinstance(e, ast.Name)
                   and e.id in GUARD_EXCEPTIONS for e in exc.elts)
    return False


class _Scanner(ast.NodeVisitor):
    """Walk one file, recording unguarded imports of guarded modules."""

    def __init__(self, path):
        self.path = path
        self.violations = []
        # Highest Python version proven by enclosing version checks
        # (None = no proof). A version proof is a lower bound.
        self.version_floor = None
        self.try_guarded = False

    def _check_import(self, node, module):
        top = _top_module(module)
        if top not in GUARDED_MODULES:
            return
        if self.try_guarded:
            return
        minimum = GUARDED_MODULES[top]
        if (self.version_floor is not None
                and minimum <= self.version_floor):
            return
        self.violations.append((self.path, node.lineno, top, minimum))

    def visit_Import(self, node):
        for alias in node.names:
            self._check_import(node, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            self._check_import(node, node.module)
        self.generic_visit(node)

    def visit_Try(self, node):
        guarded = any(_handler_guards(h) for h in node.handlers)
        saved = self.try_guarded
        if guarded:
            self.try_guarded = True
        for child in node.body:
            self.visit(child)
        self.try_guarded = saved
        for child in node.orelse + node.finalbody:
            self.visit(child)
        for handler in node.handlers:
            self.visit(handler)

    def visit_If(self, node):
        proof = _version_proof(node.test)
        saved = self.version_floor
        if _is_type_checking(node.test):
            self.version_floor = TYPE_CHECKING_FLOOR
            for child in node.body:
                self.visit(child)
            self.version_floor = saved
            for child in node.orelse:
                self.visit(child)
            self.visit(node.test)
            return
        if proof is not None:
            branch, floor = proof
            floor = max(saved, floor) if saved is not None else floor
        else:
            branch, floor = None, saved
        if branch == "body":
            self.version_floor = floor
            for child in node.body:
                self.visit(child)
            self.version_floor = saved
            for child in node.orelse:
                self.visit(child)
        elif branch == "orelse":
            for child in node.body:
                self.visit(child)
            self.version_floor = floor
            for child in node.orelse:
                self.visit(child)
            self.version_floor = saved
        else:
            for child in node.body + node.orelse:
                self.visit(child)
        self.visit(node.test)

    def visit_FunctionDef(self, node):
        self._visit_scoped(node)

    def visit_AsyncFunctionDef(self, node):
        self._visit_scoped(node)

    def visit_ClassDef(self, node):
        self._visit_scoped(node)

    def visit_Lambda(self, node):
        self._visit_scoped(node)

    def _visit_scoped(self, node):
        # A try/except guard does not cover deferred execution: the
        # handler is gone by the time the body runs. A version guard
        # does cover it: the definition itself is conditional.
        saved = self.try_guarded
        self.try_guarded = False
        self.generic_visit(node)
        self.try_guarded = saved


def check_file(path):
    """Return [(path, lineno, module, min_version)] violations in one file."""
    path = Path(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"),
                         filename=str(path))
    except SyntaxError as exc:
        return [(path, exc.lineno or 0, f"unparseable: {exc.msg}", None)]
    scanner = _Scanner(path)
    scanner.visit(tree)
    return scanner.violations


def check_tree(root):
    """Return all violations under root's toolkit/, scripts/, tests/."""
    root = Path(root)
    violations = []
    for dirname in SCAN_DIRS:
        directory = root / dirname
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            violations.extend(check_file(path))
    return violations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",
                        default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args(argv)
    root = Path(args.root)
    violations = check_tree(root)
    if not violations:
        print("PASS: no unguarded imports of 3.11+ stdlib modules")
        return 0
    print("FAIL: unguarded imports of 3.11+ stdlib modules:")
    for path, lineno, module, minimum in violations:
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        if minimum is None:
            print(f"  {rel}:{lineno}: {module}")
        else:
            print(f"  {rel}:{lineno}: unguarded import of '{module}' "
                  f"(stdlib since Python {minimum[0]}.{minimum[1]}); "
                  f"guard with try/except ImportError or "
                  f"sys.version_info >= ({minimum[0]}, {minimum[1]})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
