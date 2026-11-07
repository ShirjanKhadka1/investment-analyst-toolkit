"""Look-ahead gate (Phase 4 quality gate): the point-in-time test must
be meaningful, not just green.

Stdlib only. Two steps:
1. Run the look-ahead-sensitive suites on the real tree
   (tests/test_pit_store.py, tests/test_timemachine.py,
   tests/test_timemachine_nepse.py) — they must pass.
2. Mutation check, in a scratch copy only (never in this repo): patch
   toolkit/pit_store.py so _latest_visible ignores published_at (a
   deliberately leaking store), then run tests/test_pit_store.py
   against the leak. The suite MUST FAIL — if it passes on a leaking
   implementation, the test is meaningless and this gate fails loudly.

Usage: python3 scripts/qa_lookahead.py [--root REPO_ROOT]
Exit 0 only when the real suites pass AND the leaking variant fails.
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SUITES = ["tests/test_pit_store.py", "tests/test_timemachine.py",
          "tests/test_timemachine_nepse.py"]
LEAK_GUARD = '''            if record["published_at"] > query_date:
                continue  # not yet published: invisible to this query
'''
LEAK_REPLACEMENT = '''            # MUTATION (qa_lookahead scratch copy only): leak — ignore
            # published_at so future data is visible. The test suite
            # must catch this.
            pass
'''


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          check=False, timeout=600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",
                        default=str(Path(__file__).resolve().parent.parent))
    root = Path(parser.parse_args().root)

    real = run([sys.executable, "-m", "pytest"] + SUITES + ["-q"], root)
    print(real.stdout.strip().splitlines()[-1] if real.stdout.strip() else "")
    if real.returncode != 0:
        print(real.stdout[-2000:])
        print(real.stderr[-2000:])
        print("FAIL: look-ahead suites do not pass on the real tree")
        return 1
    print("look-ahead suites pass on the real tree; running mutation check...")

    tmp = Path(tempfile.mkdtemp(prefix="qa-lookahead-"))
    try:
        scratch = tmp / "repo"
        shutil.copytree(root, scratch, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".pytest_cache", ".mypy_cache",
            ".ruff_cache"))
        target = scratch / "toolkit" / "pit_store.py"
        text = target.read_text(encoding="utf-8")
        if LEAK_GUARD not in text:
            print("FAIL: pit_store.py guard text not found; cannot build "
                  "the leaking variant (implementation changed — update "
                  "this gate, do not skip it)")
            return 1
        target.write_text(text.replace(LEAK_GUARD, LEAK_REPLACEMENT),
                          encoding="utf-8")
        leaked = run([sys.executable, "-m", "pytest",
                      "tests/test_pit_store.py", "-q"], scratch)
        if leaked.returncode == 0:
            print("FAIL: test_pit_store.py PASSED on the deliberately "
                  "leaking store — the look-ahead test is meaningless")
            return 1
        failed = [line for line in leaked.stdout.splitlines()
                  if "failed" in line]
        print(f"mutation caught: leaking store fails as required "
              f"({failed[-1].strip() if failed else 'non-zero exit'})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS: look-ahead gate clean (suites green; leaking variant caught)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
