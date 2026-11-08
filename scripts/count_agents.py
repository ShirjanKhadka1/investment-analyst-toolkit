"""Count the finance-research-agents pack from its own roster, by script.

The toolkit's docs state the pack's size and its NEPSE / world split
(36 agents: 18 NEPSE-side, 18 market-agnostic). Those numbers must
come from the roster itself, never from a typed constant: this script
reads ``agents.json`` (the machine-readable roster) from the
finance-research-agents repo and counts.

Classification (from the roster table in that repo's README):
- the guide file (``agents/WHEN-TO-USE.md``, slug
  ``when-to-use-guide``) is not an agent and is excluded;
- NEPSE-side: slugs starting ``nepse-`` or ``nepal-``;
- market-agnostic: every other agent.
- The NEPSE-side further splits into the 9-agent NEPSE team, the 8
  sector agents, and Nepal Market Researcher (the general Nepal
  market researcher, in neither group) — the 9 + 8 = 17 framing
  undercounts by one because it omits Nepal Market Researcher.
  An NEPSE-side slug in none of those three sets fails the count:
  a new agent must be classified by a human, not guessed.

Runbooks and templates come from the pack's generated
``ops/status.json`` (same source the profile repo uses), and the
script fails if that file's agent total disagrees with the recount
— the two sources must agree.

Usage:
  python3 scripts/count_agents.py [--from-dir DIR] [--snapshot]
      [--snapshot-path PATH]
  --from-dir DIR  read agents.json + ops/status.json from a local
                  clone instead of fetching them (offline runs).
  --snapshot      write docs/agents-roster-counts.json (source URLs
                  + as-of date + counts) for offline gate runs.
Exit 0 and print JSON counts; exit 1 on any inconsistency.
"""
import argparse
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

AGENTS_REPO = "ShirjanKhadka1/finance-research-agents"
AGENTS_JSON_URL = (
    "https://raw.githubusercontent.com/"
    f"{AGENTS_REPO}/main/agents.json")
STATUS_JSON_URL = (
    "https://raw.githubusercontent.com/"
    f"{AGENTS_REPO}/main/ops/status.json")
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT = ROOT / "docs" / "agents-roster-counts.json"

GUIDE_FILE = "agents/WHEN-TO-USE.md"
# The 9-agent NEPSE team and the 8 sector agents, as named in the
# pack's README ("Nine new agents form the NEPSE team", § NEPSE
# sector agents). Kept as explicit sets so a new NEPSE-side slug
# fails loudly instead of being silently bucketed.
TEAM_SLUGS = {
    "nepse-fundamentals-analyst", "nepse-technical-analyst",
    "nepse-news-sentiment-analyst", "nepse-broker-flow-analyst",
    "nepse-macro-analyst", "nepse-corporate-actions-analyst",
    "nepse-trader", "nepse-risk-team", "nepse-portfolio-manager",
}
SECTOR_SLUGS = {
    "nepse-bank-analyst", "nepse-hydropower-analyst",
    "nepse-insurance-analyst", "nepse-microfinance-analyst",
    "nepse-mutual-fund-nav-analyst", "nepse-ipo-analyst",
    "nepse-fees-and-tax-analyst", "nepse-content-writer",
}
OTHER_NEPSE_SLUGS = {"nepal-market-researcher"}


def _fetch(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "investment-analyst-toolkit/qa"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _load(from_dir):
    if from_dir:
        root = Path(from_dir)
        roster = json.loads((root / "agents.json").read_text(
            encoding="utf-8"))
        status = json.loads((root / "ops" / "status.json").read_text(
            encoding="utf-8"))
        return roster, status, f"local clone at {from_dir}"
    roster = _fetch(AGENTS_JSON_URL)
    status = _fetch(STATUS_JSON_URL)
    return roster, status, "live roster"


def count(from_dir=None):
    """Return the counts dict, raising ValueError on inconsistency."""
    roster, status, source = _load(from_dir)
    entries = roster.get("agents", [])
    guide = [e for e in entries if e.get("file") == GUIDE_FILE]
    if len(guide) != 1:
        raise ValueError(
            f"expected exactly 1 guide entry ({GUIDE_FILE}), "
            f"found {len(guide)}")
    agents = [e for e in entries if e.get("file") != GUIDE_FILE]
    nepse, agnostic, unclassified = [], [], []
    for entry in agents:
        slug = entry.get("slug", "")
        if slug.startswith(("nepse-", "nepal-")):
            if (slug in TEAM_SLUGS or slug in SECTOR_SLUGS
                    or slug in OTHER_NEPSE_SLUGS):
                nepse.append(slug)
            else:
                unclassified.append(slug)
        else:
            agnostic.append(slug)
    if unclassified:
        raise ValueError(
            "unclassified NEPSE-side agent slug(s) — classify them in "
            f"TEAM_SLUGS / SECTOR_SLUGS / OTHER_NEPSE_SLUGS: {unclassified}")
    team = [s for s in nepse if s in TEAM_SLUGS]
    sector = [s for s in nepse if s in SECTOR_SLUGS]
    other = [s for s in nepse if s in OTHER_NEPSE_SLUGS]
    if len(team) != len(TEAM_SLUGS) or len(sector) != len(SECTOR_SLUGS):
        raise ValueError(
            "team/sector slug sets drifted from the roster: team "
            f"{len(team)}/{len(TEAM_SLUGS)}, sector "
            f"{len(sector)}/{len(SECTOR_SLUGS)}")
    if status.get("agents") != len(agents):
        raise ValueError(
            "pack ops/status.json agents "
            f"({status.get('agents')}) disagrees with the recount "
            f"({len(agents)})")
    counts = {
        "source": source,
        "agents_json_url": AGENTS_JSON_URL,
        "status_json_url": STATUS_JSON_URL,
        "as_of": date.today().isoformat(),
        "agents_total": len(agents),
        "agents_nepse": len(nepse),
        "agents_agnostic": len(agnostic),
        "nepse_team": len(team),
        "nepse_sector": len(sector),
        "nepse_other": len(other),
        "runbooks": status.get("runbooks"),
        "templates": status.get("templates"),
        "agents_json_entries": len(entries),
        "guide_excluded": GUIDE_FILE,
    }
    if counts["runbooks"] is None or counts["templates"] is None:
        raise ValueError(
            "pack ops/status.json has no runbooks/templates counts")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dir", default=None)
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--snapshot-path", default=str(DEFAULT_SNAPSHOT))
    args = parser.parse_args()
    try:
        counts = count(args.from_dir)
    except Exception as exc:  # noqa: BLE001 — count failure is a
        # gate failure with the reason printed, not a traceback.
        print(f"count_agents FAILED: {exc}", file=sys.stderr)
        return 1
    if args.snapshot:
        path = Path(args.snapshot_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(counts, indent=2) + "\n",
                        encoding="utf-8")
        print(f"snapshot written: {path}")
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
