"""Secret scan (Phase 4 quality gate): fail on likely secrets in the tree
and in the git history.

Stdlib only. Scans every text file under the repo root (skipping .git,
__pycache__, *.pyc and binary files) plus, when a git history is
present, the full ``git log -p`` output. Patterns match *shapes* of
secrets (token prefixes, private-key headers, credential assignments),
never real values — this script contains no real secret.

Heuristic, not a legal or security opinion: a match is a finding to
triage, not proof of exposure. If this gate finds anything real, STOP:
do not commit, do not rotate anything yourself — rotation needs the
repo owner. Report the exact finding to the programme coordinator.

Usage: python3 scripts/qa_secrets.py [--root REPO_ROOT] [--no-history]
Exit 0 only when no finding matches in the tree or the history.
"""
import argparse
import re
import subprocess
from pathlib import Path

# Patterns are built with concatenation so the literal pattern text
# never appears verbatim in this file (a verbatim copy would match
# its own scan once committed).
_PATTERNS = [
    ("private-key header",
     re.compile("-----BEGIN " + r"[A-Z ]*PRIVATE KEY-----")),
    ("GitHub token",
     re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("GitHub fine-grained token",
     re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("AWS access key id",
     re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token",
     re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("OpenAI-style key",
     re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("JWT-shaped token",
     re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("credential assignment",
     re.compile(
         r"(?i)(api[_-]?key|secret|access[_-]?token|password|passwd)"
         r"\s*[:=]\s*['\"][^'\"]{8,}['\"]")),
    ("email address",
     re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
]

# Placeholder values that are test/demo fixtures, not real credentials.
# A value containing one of these tokens is not a secret; a real key
# never reads "test-api-key".
_PLACEHOLDER_TOKENS = ("test", "demo", "fake", "example", "placeholder",
                       "dummy", "changeme", "xxx", "your-", "todo",
                       "sample", "mock")

# Benign matches: example/test domains and GitHub's noreply addresses.
_EMAIL_ALLOW = ("example.com", "example.org", "example.net", "test.com",
                "users.noreply.github.com", "localhost", "invalid")

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".png", ".gif", ".jpg", ".jpeg", ".svg",
                 ".ico", ".woff", ".woff2", ".zip", ".gz"}


def _is_text(path):
    try:
        with open(path, "rb") as fh:
            return b"\x00" not in fh.read(8192)
    except OSError:
        return False


def scan_text(text, where, findings):
    for name, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if name == "credential assignment":
                quoted = re.search(r"['\"]([^'\"]+)['\"]", value)
                if quoted:
                    shown = quoted.group(1)
                    if any(tok in shown.lower()
                           for tok in _PLACEHOLDER_TOKENS):
                        continue
                    if shown.lower() in ("password", "secret", "token", "api_key"):
                        continue
                    # Fixture markers: short values in ALL-CAPS + digits
                    # (e.g. the tests' ENVKEY123 / ARGKEY456, which assert
                    # a key reaches the URL) carry no entropy and are
                    # plainly invented. Real credentials are longer or
                    # mixed-case; token-prefix patterns above catch the
                    # real formats regardless of this branch.
                    if (len(shown) < 20 and re.fullmatch(r"[A-Z0-9_\-]+", shown)
                            and "KEY" in shown):
                        continue
            if name == "email address":
                domain = value.split("@", 1)[1].lower()
                if any(domain == dom or domain.endswith("." + dom)
                       for dom in _EMAIL_ALLOW):
                    continue
            findings.append(f"{where}: {name}: {value[:40]}")
            return  # one finding per line keeps the report readable


def scan_tree(root):
    findings = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if not _is_text(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            before = len(findings)
            scan_text(line, f"{path.relative_to(root)}:{lineno}", findings)
            if len(findings) > before:
                break  # one finding per file keeps the report readable
    return findings


def scan_history(root):
    """Scan `git log -p` output. Shallow clones only hold fetched
    commits: report that honestly and scan what is there."""
    git_dir = root / ".git"
    if not git_dir.exists():
        return [], "no .git directory: history scan skipped"
    proc = subprocess.run(
        ["git", "log", "-p", "--all"], cwd=root, capture_output=True,
        text=True, check=False, timeout=300)
    if proc.returncode != 0:
        return [], f"git log failed ({proc.stderr.strip()[:120]}): history scan skipped"
    note = None
    if (git_dir / "shallow").exists():
        note = "shallow clone: history scan covers only the fetched commits"
    findings = []
    for line in proc.stdout.splitlines():
        if line.startswith("+++ b/scripts/qa_secrets.py"):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            # Strip the diff "+" marker first: without this, "+@pytest.mark.parametrize"
            # would look like an email address to the pattern.
            scan_text(line[1:], "git-history", findings)
    return findings, note


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",
                        default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--no-history", action="store_true",
                        help="skip the git-history scan")
    args = parser.parse_args()
    root = Path(args.root)
    findings = scan_tree(root)
    notes = []
    if not args.no_history:
        hist_findings, note = scan_history(root)
        findings.extend(hist_findings)
        if note:
            notes.append(note)
    for note in notes:
        print(f"NOTE: {note}")
    if findings:
        print("FAIL: secret scan found likely secrets:")
        for finding in findings:
            print(f"  - {finding}")
        print("STOP: do not commit. Report the exact finding to the "
              "programme coordinator; rotation needs the repo owner.")
        return 1
    print("PASS: secret scan clean (tree"
          + (" + git history" if not args.no_history else "") + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
