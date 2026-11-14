"""Tests for the append-only forecast ledger (PR 8.1).

Offline only. The migration test reads the real
reports/daily/predictions.jsonl but never writes to it: counts are
derived from the file at test time, never hardcoded.
"""
import json

import pytest

from toolkit import forecast_ledger as fl
from toolkit.decision_log import row_hash
from toolkit.forecast_harness import read_daily_predictions


def _forecast(symbol="AAPL", forecast=100.0, last_close=99.0,
              model="naive"):
    return {"symbol": symbol, "predicted_at": "20261114T000000Z",
            "predicted_for": "next_step", "forecast": forecast,
            "last_close": last_close, "model": model}


def _score(actual=101.0, status="scored"):
    return {"actual": actual, "actual_date": "2026-11-14",
            "error": 1.0, "direction_correct": True, "status": status}


def test_append_forecast_chains_from_genesis(tmp_path):
    path = tmp_path / "ledger.jsonl"
    first = fl.append_forecast(path, _forecast())
    assert first["record_type"] == "forecast"
    assert first["prev_hash"] == "genesis"
    assert first["forecast_id"] == fl.forecast_id(first)
    assert first["schema_version"] == fl.LEDGER_SCHEMA_VERSION
    second = fl.append_forecast(path, _forecast(symbol="MSFT"))
    assert second["prev_hash"] == row_hash(
        {k: v for k, v in first.items()})
    assert second["forecast_id"] != first["forecast_id"]
    assert fl.verify_ledger(path) == []


def test_forecast_rows_never_change_when_scored(tmp_path):
    path = tmp_path / "ledger.jsonl"
    stored = fl.append_forecast(path, _forecast())
    before = path.read_bytes()
    fl.append_score(path, stored["forecast_id"], _score())
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == before.decode("utf-8").splitlines()[0]
    assert json.loads(lines[0])["forecast"] == 100.0
    assert "actual" not in json.loads(lines[0])
    assert fl.verify_ledger(path) == []


def test_append_score_merges_into_load_ledger(tmp_path):
    path = tmp_path / "ledger.jsonl"
    stored = fl.append_forecast(path, _forecast())
    fl.append_forecast(path, _forecast(symbol="MSFT"))
    fl.append_score(path, stored["forecast_id"], _score())
    merged = fl.load_ledger(path)
    assert len(merged) == 2
    assert len(merged[0]["scores"]) == 1
    assert merged[0]["scores"][0]["actual"] == 101.0
    assert merged[1]["scores"] == []
    assert len(fl.load_scores(path)) == 1


def test_append_score_refuses_unknown_id_and_second_score(tmp_path):
    path = tmp_path / "ledger.jsonl"
    stored = fl.append_forecast(path, _forecast())
    with pytest.raises(ValueError, match="unknown forecast_id"):
        fl.append_score(path, "deadbeef", _score())
    fl.append_score(path, stored["forecast_id"], _score())
    with pytest.raises(ValueError, match="already has a score"):
        fl.append_score(path, stored["forecast_id"], _score())


def test_append_validates_fields(tmp_path):
    path = tmp_path / "ledger.jsonl"
    bad = _forecast()
    del bad["model"]
    with pytest.raises(ValueError, match="missing required field"):
        fl.append_forecast(path, bad)
    with pytest.raises(ValueError, match="must be a number"):
        fl.append_forecast(path, _forecast(forecast="high"))
    stored = fl.append_forecast(path, _forecast())
    with pytest.raises(ValueError):
        fl.append_score(path, stored["forecast_id"],
                        {"actual": 1.0, "actual_date": "2026-11-14",
                         "status": "pending"})
    with pytest.raises(ValueError):
        fl.append_score(path, stored["forecast_id"],
                        {"actual_date": "2026-11-14", "status": "scored"})


def test_verify_ledger_catches_tampered_forecast(tmp_path):
    path = tmp_path / "ledger.jsonl"
    stored = fl.append_forecast(path, _forecast())
    fl.append_score(path, stored["forecast_id"], _score())
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines()]
    rows[0]["forecast"] = 999.0
    path.write_text("\n".join(json.dumps(r, sort_keys=True)
                              for r in rows) + "\n", encoding="utf-8")
    problems = fl.verify_ledger(path)
    assert any("forecast_id mismatch" in p for p in problems)


def test_verify_ledger_catches_broken_chain(tmp_path):
    path = tmp_path / "ledger.jsonl"
    fl.append_forecast(path, _forecast())
    fl.append_forecast(path, _forecast(symbol="MSFT"))
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines()]
    rows[1]["prev_hash"] = "tampered"
    path.write_text("\n".join(json.dumps(r, sort_keys=True)
                              for r in rows) + "\n", encoding="utf-8")
    problems = fl.verify_ledger(path)
    assert any("prev_hash mismatch" in p for p in problems)


def test_verify_ledger_catches_orphan_score(tmp_path):
    path = tmp_path / "ledger.jsonl"
    stored = fl.append_forecast(path, _forecast())
    fl.append_score(path, stored["forecast_id"], _score())
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines()]
    rows[1]["forecast_id"] = "0000000000000000"
    path.write_text("\n".join(json.dumps(r, sort_keys=True)
                              for r in rows) + "\n", encoding="utf-8")
    problems = fl.verify_ledger(path)
    assert any("unknown forecast_id" in p for p in problems)


def test_ledger_file_is_lf_only(tmp_path):
    path = tmp_path / "ledger.jsonl"
    stored = fl.append_forecast(path, _forecast())
    fl.append_score(path, stored["forecast_id"], _score())
    assert b"\r" not in path.read_bytes()


def test_migrate_development_period(tmp_path):
    source = tmp_path / "old.jsonl"
    old_rows = [
        {"symbol": "AAPL", "predicted_at": "20261101T000000Z",
         "predicted_for": "next_step", "forecast": 100.0,
         "last_close": 99.0, "model": "naive", "status": "scored",
         "actual": 101.0, "actual_date": "2026-11-02", "error": 1.0,
         "direction_correct": True},
        {"symbol": "AAPL", "predicted_at": "20261102T000000Z",
         "predicted_for": "next_step", "forecast": 100.0,
         "last_close": 99.0, "model": "naive", "status": "failed",
         "actual": 98.0, "actual_date": "2026-11-03", "error": -2.0,
         "direction_correct": False},
        {"symbol": "MSFT", "predicted_at": "20261103T000000Z",
         "predicted_for": "next_step", "forecast": 200.0,
         "last_close": 199.0, "model": "naive", "status": "pending"},
    ]
    source.write_text("\n".join(json.dumps(r) for r in old_rows) + "\n",
                      encoding="utf-8")
    before = source.read_bytes()
    dest = tmp_path / "new.jsonl"
    out = fl.migrate_development_period(source, dest)
    assert out == {"forecasts_written": 3, "scores_written": 2,
                   "pending": 1, "source": str(source),
                   "dest": str(dest)}
    assert source.read_bytes() == before  # source never modified
    assert fl.verify_ledger(dest) == []
    merged = fl.load_ledger(dest)
    assert all(f["development_period"] is True for f in merged)
    assert all(f["source"] == fl.DEVELOPMENT_PERIOD_SOURCE
               for f in merged)
    assert [len(f["scores"]) for f in merged] == [1, 1, 0]
    assert merged[1]["scores"][0]["status"] == "failed"
    with pytest.raises(ValueError, match="already holds rows"):
        fl.migrate_development_period(source, dest)


def test_migrate_real_daily_ledger_counts_match_the_file(tmp_path):
    import pathlib
    real = pathlib.Path(__file__).resolve().parent.parent \
        / "reports" / "daily" / "predictions.jsonl"
    rows = read_daily_predictions(real)
    scored = [r for r in rows if r.get("actual") is not None]
    dest = tmp_path / "migrated.jsonl"
    out = fl.migrate_development_period(real, dest)
    assert out["forecasts_written"] == len(rows)
    assert out["scores_written"] == len(scored)
    assert out["pending"] == len(rows) - len(scored)
    assert fl.verify_ledger(dest) == []
    assert len(fl.load_ledger(dest)) == len(rows)
