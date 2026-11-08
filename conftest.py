"""Repo-root conftest: make the package importable under every pytest
invocation.

`python -m pytest` puts the working directory on sys.path, so
`import toolkit` works when tests are run that way — but the bare
`pytest` binary (used by scripts/qa.py and therefore by CI) does
not, and pytest only adds the test file's own basedir (tests/, which
has no __init__.py). That made CI collection fail with
ModuleNotFoundError: No module named 'toolkit' while local runs
passed. Inserting the repo root here fixes every invocation.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
