"""Link check (Phase 4 quality gate): every link in the repo's Markdown
must resolve.

Stdlib only. Checks all ``*.md`` files under the repo root, except
.git:
- internal links (relative paths, ``#anchors``) must resolve to a file
  or directory in the repo, and anchors must match a heading in the
  target file. Internal failures FAIL the gate — this is the
  deterministic, offline part.
- external links (http/https) are checked best-effort with a HEAD
  request (10 s timeout, redirects followed). A network failure or a
  dead external link is a WARNING, recorded in the output, not a
  failure — the gate must stay deterministic offline. Use --offline
  to skip external checks entirely (e.g. in a sandbox with no net).

Usage: python3 scripts/qa_links.py [--root REPO_ROOT] [--offline]
Exit 0 only when every internal link resolves.
"""
import argparse
import re
import urllib.request
from pathlib import Path

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
REF_LINK = re.compile(r"(?m)^\[[^\]]+\]:\s+(\S+)")
BARE_URL = re.compile(r"https?://[^\s)\"'<>`]+")
ANGLE_URL = re.compile(r"<(https?://[^>]+)>")
HEADING = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")
INLINE_CODE = re.compile(r"`[^`]*`")

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv"}


def slugify(heading):
    """GitHub heading-anchor slug: lowercase, drop punctuation except
    hyphens/underscores, spaces become hyphens."""
    text = heading.strip().lower()
    text = re.sub(r"[^a-z0-9 _-]", "", text)
    return re.sub(r"\s+", "-", text).strip("-")


def anchors_of(md_text):
    anchors = set()
    for match in HEADING.finditer(md_text):
        anchors.add(slugify(match.group(2)))
    return anchors


def collect_links(md_path):
    text = md_path.read_text(encoding="utf-8", errors="replace")
    text = INLINE_CODE.sub("", text)
    links = []
    for match in MD_LINK.finditer(text):
        links.append(match.group(1))
    for match in REF_LINK.finditer(text):
        links.append(match.group(1))
    for match in ANGLE_URL.finditer(text):
        links.append(match.group(1))
    for match in BARE_URL.finditer(text):
        links.append(match.group(0).rstrip(".,;:"))
    return text, sorted(set(links))


def check_external(url, timeout=10):
    req = urllib.request.Request(url, method="HEAD")
    req.add_header("User-Agent", "investment-analyst-toolkit-link-check/1.0")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # Network failure: warning, not failure (gate stays deterministic).
        return f"unreachable ({type(exc).__name__})"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",
                        default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--offline", action="store_true",
                        help="skip external (http/https) checks")
    args = parser.parse_args()
    root = Path(args.root)
    md_files = [p for p in sorted(root.rglob("*.md"))
                if not any(part in SKIP_DIRS for part in p.relative_to(root).parts)]
    anchor_cache = {}
    errors, warnings = [], []
    checked_internal, checked_external = 0, 0
    for md_path in md_files:
        text, links = collect_links(md_path)
        rel = md_path.relative_to(root)
        for link in links:
            if link.startswith(("mailto:", "tel:")):
                continue
            if link.startswith(("http://", "https://")):
                if args.offline:
                    continue
                checked_external += 1
                status = check_external(link)
                if status != 200:
                    warnings.append(
                        f"{rel}: external {link} -> {status} (warning only)")
                continue
            target, _, anchor = link.partition("#")
            if not target:
                target_path, target_text = md_path, text
            else:
                target_path = (md_path.parent / target).resolve()
                try:
                    target_path.relative_to(root.resolve())
                except ValueError:
                    errors.append(f"{rel}: link {link} escapes the repo")
                    continue
                if not target_path.exists():
                    errors.append(f"{rel}: link {link} -> missing {target}")
                    continue
                if target_path.is_dir():
                    continue
                if target_path.suffix.lower() != ".md":
                    checked_internal += 1
                    continue
                target_text = target_path.read_text(
                    encoding="utf-8", errors="replace")
            checked_internal += 1
            if anchor:
                if target_path not in anchor_cache:
                    anchor_cache[target_path] = anchors_of(target_text)
                if anchor not in anchor_cache[target_path]:
                    errors.append(
                        f"{rel}: link {link} -> anchor #{anchor} not found "
                        f"in {target_path.relative_to(root)}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    if errors:
        print("FAIL: internal links do not resolve:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"PASS: all internal links resolve ({checked_internal} internal, "
          f"{checked_external} external checked"
          f"{' (offline: external skipped)' if args.offline else ''}"
          f"{'; ' + str(len(warnings)) + ' external warning(s)' if warnings else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
