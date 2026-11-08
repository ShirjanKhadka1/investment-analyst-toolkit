"""Tests for the public track-record watchlist (docs/watchlist-proposal.md).

The watchlist is a proposal until the user approves it — but the
mapping must already be exact: every ticker in the doc table maps
to a profile that exists, the doc table and WATCHLIST_PROFILES
agree, and removed tickers (QQQ, 7203.T) stay removed.
"""
from pathlib import Path

from toolkit.market_profiles import WATCHLIST_PROFILES, get_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_DOC = REPO_ROOT / "docs" / "watchlist-proposal.md"

REMOVED = ("QQQ", "7203.T")


def _doc_table():
    """Parse the watchlist table: {ticker: profile_key}."""
    lines = WATCHLIST_DOC.read_text(encoding="utf-8").splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("|") and "Ticker" in line and "Profile" in line:
            header_idx = i
            break
    assert header_idx is not None, "watchlist doc has no Ticker/Profile table"
    header = [c.strip() for c in lines[header_idx].strip().strip("|").split("|")]
    ticker_col = header.index("Ticker")
    profile_col = header.index("Profile")
    table = {}
    for line in lines[header_idx + 2:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        table[cells[ticker_col]] = cells[profile_col]
    return table


def test_watchlist_has_seven_tickers():
    table = _doc_table()
    assert len(table) == 7, f"watchlist must have 7 tickers, has {len(table)}"
    assert len(set(table)) == 7, "watchlist has a duplicate ticker"


def test_every_mapping_resolves_to_a_profile():
    table = _doc_table()
    for ticker, profile_key in table.items():
        profile = get_profile(profile_key)  # raises on an unknown key
        assert profile.key == profile_key, f"{ticker} -> {profile_key} mismatch"


def test_doc_table_matches_code_mapping():
    assert _doc_table() == WATCHLIST_PROFILES


def test_removed_tickers_stay_removed():
    table = _doc_table()
    for ticker in REMOVED:
        assert ticker not in table, f"{ticker} was removed from the watchlist"


def test_watchlist_profiles_are_distinct_markets():
    """The seven tickers must cover more than one market — a
    single-market list would not test the global engine."""
    table = _doc_table()
    assert len(set(table.values())) >= 5, (
        f"watchlist spans only {sorted(set(table.values()))}")
