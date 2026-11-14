"""Walk a branch's history and fail if any commit removed or changed
an earlier row (PR 7.0, after the November 2026 overwrite incident).

The track-record log is append-only: every commit's version of the
file must start with the previous version's rows, compared
canonically (see ``_canonical_rows``), and the tip's hash chain
must verify (see ``toolkit.decision_log.verify_chain``).
This script checks both, oldest commit first. It is wired as a
post-publish step in ``.github/workflows/daily-track-record.yml``.

Usage:
  python3 scripts/verify_track_record.py [--repo PATH]
      [--branch BRANCH] [--file PATH] [--no-fetch]

- ``--repo``: a git checkout with an ``origin`` remote (default: the
  repo root). ``--branch`` defaults to ``data/track-record`` and
  ``--file`` to ``calls-v2.jsonl`` — the new series, named as it
  appears in the branch tree (the branch root holds the published
  files). The legacy ``calls.jsonl`` is deliberately NOT checked
  here: its history contains the known overwrite (commit c35ce63
  replaced the 13 Nov rows), so it fails by design; see
  ``docs/track-record-incidents.md``.
- ``--no-fetch`` skips ``git fetch origin <branch>`` (local runs
  against a repo whose remote is already up to date).

Exit 0 when the history is clean; exit 1 with a clear message on
the first violation. Stdlib only.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit.decision_log import canonical_row_bytes, verify_chain

DEFAULT_BRANCH = "data/track-record"
# Path of the log WITHIN the data branch (the branch root holds the
# published files, not a data/track-record/ subdirectory).
DEFAULT_FILE = "calls-v2.jsonl"


def _git(repo, *args, check=True):
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc


def _file_at(repo, commit, path):
    """File bytes at ``commit`` (b"" when the file is absent there)."""
    proc = _git(repo, "show", f"{commit}:{path}", check=False)
    if proc.returncode != 0:
        return b""
    return proc.stdout


def _canonical_rows(data):
    """Canonical row bytes for each JSONL row in ``data``.

    Line endings, key order and spacing are ignored: two versions
    of a row that differ only in those compare equal, so a log
    checked out with CRLF endings still verifies. A row whose
    content changed still differs — the "no row removed or
    changed" guarantee is preserved.
    """
    rows = []
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(canonical_row_bytes(json.loads(line)))
    return rows


def check_history(repo, branch=DEFAULT_BRANCH, path=DEFAULT_FILE,
                  fetch=True):
    """Return a list of problems found in the branch history.

    Empty list means clean: every version of ``path`` starts with
    the previous version's canonical rows, and the tip's hash
    chain verifies.
    """
    repo = Path(repo)
    if fetch:
        _git(repo, "fetch", "origin", branch)
    proc = _git(repo, "log", "--format=%H", "--reverse",
                f"origin/{branch}", "--", path)
    commits = [line for line in
               proc.stdout.decode("utf-8").splitlines() if line.strip()]
    problems = []
    if not commits:
        return [(f"no commits touch {path} on origin/{branch} — "
                 "the log file was never published")]
    previous = []
    for commit in commits:
        try:
            current = _canonical_rows(_file_at(repo, commit, path))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            problems.append(
                f"commit {commit[:7]}: {path} is not valid JSONL: "
                f"{exc}")
            return problems
        if current[:len(previous)] != previous:
            problems.append(
                f"commit {commit[:7]} removed or changed an earlier "
                f"row of {path}: its version does not start with the "
                "previous version's rows")
            return problems  # one violation is enough to fail
        previous = current
    with tempfile.NamedTemporaryFile(suffix=".jsonl",
                                    delete=False) as tmp:
        tmp.write(_file_at(repo, commits[-1], path))
        tmp_path = tmp.name
    try:
        for problem in verify_chain(tmp_path):
            problems.append(f"tip hash chain: {problem}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",
                        default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--file", default=DEFAULT_FILE)
    parser.add_argument("--no-fetch", action="store_true",
                        help="skip git fetch origin <branch>")
    args = parser.parse_args(argv)
    try:
        problems = check_history(args.repo, args.branch, args.file,
                                 fetch=not args.no_fetch)
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if problems:
        print(f"FAIL: {args.file} on {args.branch} is not append-only:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"OK: {args.file} on {args.branch} is append-only "
          "(every version's rows are a prefix of the next; "
          "tip chain holds)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
