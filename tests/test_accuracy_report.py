"""Markdown accuracy report from the tracker ledger (Phase 4, slice 2).

Offline fixtures only: ledgers are written here with tracker.record_prediction
/ tracker.score_pending, never from live data. The report must:

- never count a pending prediction as a win (or in any rate);
- state n next to every accuracy / error figure;
- show a model with zero scored predictions as "no scored predictions",
  never as 0%;
- list failures with their reason and action.
"""
from toolkit import accuracy_report, tracker


def _ledger_with_scored_and_pending(tmp_path):
    ledger = str(tmp_path / "pred.jsonl")
    # Scored, direction correct: forecast up (110 > 100), actual up (105).
    tracker.record_prediction(ledger, "X", 110.0, "trend", "2026-10-27", "next", 100.0)
    tracker.score_pending(ledger, "X", 105.0, "2026-10-28")
    # Pending only: must never appear in any rate.
    tracker.record_prediction(ledger, "Y", 90.0, "pending-model", "2026-10-28", "next", 100.0)
    return ledger


def test_pending_never_counts_as_win_or_in_rates(tmp_path):
    ledger = _ledger_with_scored_and_pending(tmp_path)
    data = accuracy_report.build_accuracy_data(ledger)
    assert data["n_scored"] == 1
    assert data["n_pending"] == 1
    assert data["direction_accuracy"] == 1.0
    assert data["mean_abs_error"] == 5.0
    assert data["n_failed"] == 0
    md = accuracy_report.generate_accuracy_report(ledger)
    assert "Pending predictions (not counted as wins): n = 1" in md
    # The scored line must say n = 1, and the pending model must not
    # inflate the scored count anywhere in the table.
    assert "Scored predictions: n = 1" in md


def test_n_is_stated_and_failures_listed_with_reason_and_action(tmp_path):
    ledger = str(tmp_path / "pred.jsonl")
    # Direction-wrong failure: forecast up (110 > 100), actual down (95).
    tracker.record_prediction(ledger, "X", 110.0, "trend", "2026-10-27", "next", 100.0)
    tracker.score_pending(ledger, "X", 95.0, "2026-10-28")
    data = accuracy_report.build_accuracy_data(ledger)
    assert data["n_scored"] == 1
    assert data["failure_rate"] == 1.0
    assert data["direction_accuracy"] == 0.0
    assert data["mean_abs_error"] == 15.0
    md = accuracy_report.generate_accuracy_report(ledger)
    assert "n = 1" in md
    assert "direction wrong" in md
    assert "demote model in next auto-selection" in md
    assert "15.0" in md  # abs error of the failure, stated explicitly


def test_zero_scored_model_shows_no_scored_predictions_not_zero_percent(tmp_path):
    ledger = _ledger_with_scored_and_pending(tmp_path)
    md = accuracy_report.generate_accuracy_report(ledger)
    pending_row = next(line for line in md.splitlines() if "pending-model" in line)
    assert "no scored predictions" in pending_row
    assert "0%" not in pending_row
    assert "0.00%" not in pending_row
    data = accuracy_report.build_accuracy_data(ledger)
    entry = data["per_model"]["pending-model"]
    assert entry["n_scored"] == 0
    assert entry["n_pending"] == 1
    assert entry["direction_accuracy"] is None
    assert entry["mean_abs_error"] is None


def test_empty_ledger_shows_no_scored_predictions(tmp_path):
    ledger = str(tmp_path / "empty.jsonl")
    data = accuracy_report.build_accuracy_data(ledger)
    assert data["n_scored"] == 0 and data["n_pending"] == 0
    assert data["direction_accuracy"] is None
    md = accuracy_report.generate_accuracy_report(ledger)
    assert "no scored predictions" in md
    assert "Scored predictions: n = 0" in md


def test_symbol_filter_and_version_grouping(tmp_path):
    ledger = str(tmp_path / "pred.jsonl")
    tracker.record_prediction(ledger, "X", 110.0, "trend", "2026-10-27", "next", 100.0)
    tracker.score_pending(ledger, "X", 105.0, "2026-10-28")
    tracker.record_prediction(ledger, "Z", 110.0, "trend", "2026-10-27", "next", 100.0)
    tracker.score_pending(ledger, "Z", 95.0, "2026-10-28")
    data_x = accuracy_report.build_accuracy_data(ledger, symbol="X")
    assert data_x["n_scored"] == 1 and data_x["direction_accuracy"] == 1.0
    data_all = accuracy_report.build_accuracy_data(ledger)
    assert data_all["n_scored"] == 2 and data_all["direction_accuracy"] == 0.5
