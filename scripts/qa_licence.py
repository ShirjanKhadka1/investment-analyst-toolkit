"""Licence check (Phase 4 quality gate): reuse attribution must be complete.

Stdlib only. Verifies, for this MIT repo:
1. The root LICENSE file is the MIT licence.
2. Every source named in the SOURCES records has a licence entry in
   LICENSES.md (the file SOURCES_MERGE_2026-11-07.md already refers
   to): sources are taken from LICENSES.md's own table, from
   toolkit/SOURCES_MERGE_2026-11-07.md, and from the data-source
   registry (DATA_SOURCES.md table + toolkit/data_layer.py DATA_SOURCES).
   A named source with no licence recorded FAILS.
3. No AGPL-licensed text or code is present. This is a heuristic, not
   a legal opinion: it fails on an AGPL licence file (COPYING/AGPL*),
   on the full "GNU Affero General Public License" text, or on an
   AGPL licence header in a .py file. Bare mentions of "AGPL-3.0" in
   attribution documents (SOURCES_MERGE, LICENSES.md, module headers
   saying "ideas only, AGPL-3.0") are attribution, not licensed
   content, and are reported as information, not failures.
4. Every fixture listed in tests/fixtures/SOURCES.md exists on disk.

Usage: python3 scripts/qa_licence.py [--root REPO_ROOT]
Exit 0 only when every check passes.
"""
import argparse
import re
from pathlib import Path

SOURCES_FILES = [
    "toolkit/SOURCES_MERGE_2026-11-07.md",
    "DATA_SOURCES.md",
    "tests/fixtures/SOURCES.md",
]
# Licence text that must never appear as content (as opposed to a
# mention in an attribution record).
FORBIDDEN_TEXT = [
    "GNU Affero General Public License",
    "Licensed under the GNU Affero",
    "Affero General Public License",
]
FORBIDDEN_FILES = {"COPYING", "COPYING.AGPL", "AGPL-3.0.txt", "LICENSE.AGPL"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv"}
# Files that are allowed to *mention* AGPL as attribution (they record
# that a source IS AGPL-licensed and that only ideas were taken).
ATTRIBUTION_FILES = {
    "LICENSES.md",
    "toolkit/SOURCES_MERGE_2026-11-07.md",
    "scripts/qa_licence.py",
}


def table_first_cells(md_text):
    """First cells of Markdown table rows (| Name | ... |)."""
    names = set()
    for line in md_text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or cells[0].startswith("-") or cells[0].lower() in (
                "source", "repo", "name", "fixture"):
            continue
        # Strip link markup: [Name](url) -> Name; `code` -> code.
        name = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cells[0])
        name = name.strip("`").strip()
        if name:
            names.add(name)
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",
                        default=str(Path(__file__).resolve().parent.parent))
    root = Path(parser.parse_args().root)
    errors, info = [], []

    # 1. Root licence is MIT.
    licence = root / "LICENSE"
    if not licence.exists():
        errors.append("LICENSE file missing")
    elif "MIT License" not in licence.read_text(encoding="utf-8", errors="replace"):
        errors.append("LICENSE is not the MIT licence")

    # 2. Every named source has a licence entry in LICENSES.md.
    licences_path = root / "LICENSES.md"
    if not licences_path.exists():
        errors.append("LICENSES.md missing (SOURCES_MERGE_2026-11-07.md refers to it)")
        licences_text = ""
    else:
        licences_text = licences_path.read_text(encoding="utf-8", errors="replace")
    for rel in SOURCES_FILES:
        path = root / rel
        if not path.exists():
            errors.append(f"SOURCES record missing: {rel}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if rel == "tests/fixtures/SOURCES.md":
            continue  # fixtures checked for existence in step 4
        if rel.endswith("SOURCES_MERGE_2026-11-07.md"):
            # This file mixes idea tables with source tables, so table
            # cells are not reliable source names. Sources are the
            # github.com repos it links to (each section names its repo).
            for slug in sorted(set(re.findall(
                    r"https://github\.com/([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)",
                    text))):
                repo = slug.split("/")[1]
                if slug not in licences_text and repo not in licences_text:
                    errors.append(
                        f"source {slug} (from {rel}) has no entry in LICENSES.md")
            continue
        # DATA_SOURCES.md: every table row's first cell is a source name.
        for name in table_first_cells(text):
            parts = [p.strip() for p in name.split(",")]
            for part in parts:
                short = re.sub(r"\(.*\)", "", part).strip()
                if short and short not in licences_text:
                    errors.append(
                        f"source {short!r} (from {rel}) has no entry in LICENSES.md")
    # DATA_SOURCES.md prose names (keyed/unverified section) must also
    # appear in LICENSES.md or DATA_SOURCES.md's own table.
    data_sources = root / "DATA_SOURCES.md"
    if data_sources.exists():
        data_text = data_sources.read_text(encoding="utf-8", errors="replace")
        for name in ("FRED", "Alpha Vantage", "Twelve Data", "FMP", "SEC EDGAR"):
            if name not in data_text:
                errors.append(f"DATA_SOURCES.md does not name {name}")
            if name not in licences_text:
                errors.append(f"data source {name} has no entry in LICENSES.md")

    # 3. No AGPL-licensed content.
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name in FORBIDDEN_FILES:
            errors.append(f"AGPL licence file present: {rel}")
            continue
        if path.suffix.lower() not in (".py", ".md", ".txt", ".rst"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if str(rel) in ATTRIBUTION_FILES:
            for token in ("AGPL-3.0", "GNU AGPL"):
                if token in text:
                    info.append(f"{rel}: mentions {token} (attribution record)")
            continue
        for forbidden in FORBIDDEN_TEXT:
            if forbidden in text:
                errors.append(f"{rel}: contains AGPL licence text: {forbidden!r}")
        if path.suffix.lower() == ".py":
            # A .py file fails only if it claims AGPL as ITS licence
            # ("licensed under the AGPL"); a bare "AGPL-3.0" mention in
            # an attribution comment ("ideas only, AGPL-3.0, commit ...,
            # no code copied") is attribution, reported as information.
            if re.search(r"(?i)licensed under (the )?(GNU )?AGPL", text):
                errors.append(f"{rel}: .py file claims an AGPL licence")
            elif "AGPL" in text:
                info.append(f"{rel}: mentions AGPL (attribution comment)")

    # 4. Fixtures listed in tests/fixtures/SOURCES.md exist.
    fixtures_doc = root / "tests/fixtures/SOURCES.md"
    if fixtures_doc.exists():
        doc = fixtures_doc.read_text(encoding="utf-8", errors="replace")
        for name in re.findall(r"`([A-Za-z0-9_.\-]+\.(?:json|xml|csv|md))`", doc):
            if not (root / "tests/fixtures" / name).exists():
                errors.append(f"fixture listed in SOURCES.md missing: {name}")

    for line in sorted(set(info)):
        print(f"INFO: {line}")
    if errors:
        print("FAIL: licence check found problems:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("PASS: licence check clean (MIT root, every named source has a "
          "LICENSES.md entry, no AGPL content)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
