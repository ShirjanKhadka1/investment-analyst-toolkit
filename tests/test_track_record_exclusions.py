"""Tests for track-record exclusions (Incident 3 / TRACK-RECORD
INCIDENT 2).

The three calls logged 2026-11-19 overlap the still-unsettled
14 Nov first-series calls and are excluded from scoring by the
owner's decision of 2026-11-22. They stay in the log, labelled;
the scorer skips them and the settler refuses them, both loading
``docs/track-record-exclusions.json``.

No network: every test builds its log and its exclusions file in
tmp_path, except the tests that read the repo's own exclusions
file — which is a local file read, not a fetch.
"""
import json
from pathlib import Path

import pytest

from toolkit.decision_log import load_calls, settle_call
from toolkit.track_record import (
    load_excluded_call_ids,
    render_track_record_md,
    score_settled,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EXCLUSIONS_FILE = REPO_ROOT / "docs" / "track-record-exclusions.json"

# The three excluded calls (data-branch commit 0c61959,
# 2026-11-19T02:57:01Z), verified against the data branch.
EXCLUDED = {
    "3f257a877c7df6d0": "NIFTYBEES.NS",
    "5332effdbd924ff7": "ISF.L",
    "429bd0aa0af7e9e2": "BTC-USD",
}


def _settled(symbol, hit, call_id=None, naive_hit=True, bnh_hit=True):
    call = {"symbol": symbol, "status": "settled", "hit": hit,
            "naive_hit": naive_hit, "bnh_hit": bnh_hit}
    if call_id is not None:
        call["call_id"] = call_id
    return call


def _open_call_row(call_id, symbol, date="2026-11-19"):
    """One raw open call row, as stored in calls-v2.jsonl."""
    return {"record_type": "call", "call_id": call_id, "date": date,
            "symbol": symbol, "exchange": "NSE",
            "thesis": "test", "horizon_days": 5, "expected": "up",
            "sources": ["test"], "confidence": 0.5,
            "entry_close": 100.0, "status": "open",
            "schema_version": "1.5.0"}


# --- the exclusions file ---------------------------------------------------

def test_loader_reads_the_three_excluded_call_ids():
    assert load_excluded_call_ids() == frozenset(EXCLUDED)


def test_exclusions_file_records_the_decision():
    data = json.loads(EXCLUSIONS_FILE.read_text(encoding="utf-8"))
    assert data["decision_date"] == "2026-11-22"
    assert "never deleted" in data["note"]
    by_id = {e["call_id"]: e for e in data["excluded"]}
    assert set(by_id) == set(EXCLUDED)
    for call_id, symbol in EXCLUDED.items():
        entry = by_id[call_id]
        assert entry["symbol"] == symbol
        assert entry["commit_time"] == "2026-11-19T02:57:01Z"
        assert entry["decision_date"] == "2026-11-22"
        assert "observable when logged" in entry["reason"]


def test_loader_with_explicit_path_and_missing_file(tmp_path):
    custom = tmp_path / "exclusions.json"
    custom.write_text(json.dumps(
        {"excluded": [{"call_id": "aaaa0000aaaa0000"},
                      {"call_id": "bbbb1111bbbb1111"}]}),
        encoding="utf-8")
    assert load_excluded_call_ids(custom) == frozenset(
        {"aaaa0000aaaa0000", "bbbb1111bbbb1111"})
    assert load_excluded_call_ids(tmp_path / "absent.json") == frozenset()
    broken = tmp_path / "broken.json"
    broken.write_text('{"excluded": [{"symbol": "SPY"}]}',
                      encoding="utf-8")
    with pytest.raises(ValueError):
        load_excluded_call_ids(broken)


# --- the scorer ------------------------------------------------------------

def test_score_settled_skips_excluded_calls():
    calls = [_settled("NIFTYBEES.NS", True, "3f257a877c7df6d0"),
             _settled("SPY", True), _settled("SPY", False)]
    scores = score_settled(calls)
    assert scores["pooled"]["model"]["n"] == 2
    assert scores["pooled"]["model"]["hits"] == 1
    assert "NIFTYBEES.NS" not in scores["per_ticker"]
    assert scores["per_ticker"]["SPY"]["model"]["n"] == 2
    # Explicit exclusion set: same result without reading the file.
    scores = score_settled(calls, excluded_call_ids={"3f257a877c7df6d0"})
    assert scores["pooled"]["model"]["n"] == 2
    # Empty exclusion set: the excluded call counts again (control).
    scores = score_settled(calls, excluded_call_ids=set())
    assert scores["pooled"]["model"]["n"] == 3
    assert scores["pooled"]["model"]["hits"] == 2


def test_score_settled_skips_all_three_excluded_ids():
    calls = [_settled(symbol, True, call_id)
             for call_id, symbol in EXCLUDED.items()]
    calls.append(_settled("SPY", True))
    scores = score_settled(calls)
    assert scores["pooled"]["model"]["n"] == 1
    assert scores["per_ticker"] == {
        "SPY": scores["per_ticker"]["SPY"]}


def test_render_excludes_excluded_calls():
    page = render_track_record_md(
        [_settled("BTC-USD", True, "429bd0aa0af7e9e2")])
    assert "Calls settled (pooled): **0**" in page
    assert "currently n=0" in page


# --- the settler -----------------------------------------------------------

def test_settle_call_refuses_excluded_call_id(tmp_path):
    path = tmp_path / "calls-v2.jsonl"
    rows = [_open_call_row("3f257a877c7df6d0", "NIFTYBEES.NS"),
            _open_call_row("0123456789abcdef", "SPY")]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match="excluded from scoring"):
        settle_call(path, "3f257a877c7df6d0", 110.0, 100.0, 0.0,
                    "test:synthetic", "2026-11-24")
    # The refused settlement appended nothing: the log is unchanged
    # and the excluded call is still open in it.
    assert len(load_calls(path)) == 2
    assert next(c for c in load_calls(path)
                if c["call_id"] == "3f257a877c7df6d0")["status"] == "open"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    # Control: a non-excluded call in the same log still settles.
    settled = settle_call(path, "0123456789abcdef", 110.0, 100.0, 0.0,
                          "test:synthetic", "2026-11-24")
    assert settled["status"] == "settled"
    assert settled["hit"] is True
    assert len(load_calls(path)) == 2
