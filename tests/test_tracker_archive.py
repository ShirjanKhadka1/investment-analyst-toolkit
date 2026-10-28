from toolkit import archive, tracker

def test_prediction_scored_and_failure_logged(tmp_path):
    ledger = str(tmp_path / "pred.jsonl")
    tracker.record_prediction(ledger, "X", 110.0, "trend", "2026-10-27", "next", 100.0)
    scored = tracker.score_pending(ledger, "X", 105.0, "2026-10-28")
    assert scored[0]["direction_correct"] is True and scored[0]["abs_error"] == 5.0
    rep = tracker.accuracy_report(ledger, "X")
    assert rep["n_scored"] == 1 and rep["direction_accuracy"] == 1.0

def test_direction_wrong_is_failure(tmp_path):
    ledger = str(tmp_path / "pred.jsonl")
    tracker.record_prediction(ledger, "X", 110.0, "trend", "2026-10-27", "next", 100.0)
    scored = tracker.score_pending(ledger, "X", 95.0, "2026-10-28")
    assert scored[0]["status"] == "failed" and "direction wrong" in scored[0]["failure_reason"]

def test_pending_never_counts_as_win(tmp_path):
    ledger = str(tmp_path / "pred.jsonl")
    tracker.record_prediction(ledger, "X", 110.0, "trend", "2026-10-27", "next", 100.0)
    assert tracker.accuracy_report(ledger, "X")["n_scored"] == 0

def test_archive_roundtrip_and_prune(tmp_path):
    out = archive.archive_run(str(tmp_path), "X", "# report", {"a": 1}, "20261028T120000Z")
    assert out["index_entries"] == 1
    import os
    assert os.path.exists(out["report"])
    assert archive.prune_archive(str(tmp_path), "X", 365, today=__import__("datetime").date(2026, 10, 28)) == []
