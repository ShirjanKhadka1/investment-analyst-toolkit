"""Tests for toolkit/pit_store.py — synthetic example data only.

The leak test (test_future_record_is_invisible_before_publication)
is written so a naive store — one that ignores published_at — FAILS
it: it asserts the future-published record is absent from the query
result. A correct point-in-time store passes it.
"""

import datetime

import pytest

from toolkit import pit_store, timemachine


def _store_with_bars():
    store = pit_store.PointInTimeStore()
    store.add_bar("2026-11-02", "2026-11-02", 100, 102, 99, 101, volume=1000)
    store.add_bar("2026-11-03", "2026-11-03", 101, 103, 100, 102, volume=1200)
    store.add_bar("2026-11-04", "2026-11-04", 102, 104, 101, 103, volume=1100)
    return store


def test_empty_store_returns_empty_lists():
    store = pit_store.PointInTimeStore()
    assert store.bars_as_of("2026-11-05") == []
    assert store.closes_as_of("2026-11-05") == []
    assert store.fundamentals_as_of("2026-11-05") == []
    assert store.latest_fundamental("2026-11-05", "revenue") is None
    assert len(store) == 0
    assert store.as_of_dates() == []


def test_bar_visible_when_published_on_query_date():
    store = _store_with_bars()
    bars = store.bars_as_of("2026-11-03")
    assert [b["date"] for b in bars] == ["2026-11-02", "2026-11-03"]
    assert bars[-1]["close"] == pytest.approx(102.0)
    assert bars[-1]["volume"] == pytest.approx(1200.0)


def test_bar_invisible_before_publication():
    store = _store_with_bars()
    bars = store.bars_as_of("2026-11-02")
    assert [b["date"] for b in bars] == ["2026-11-02"]


def test_future_record_is_invisible_before_publication():
    """The leak test: a record published AFTER the query date must
    not be visible. A naive store that ignores published_at fails
    this test; a point-in-time-correct store passes it."""
    store = _store_with_bars()
    # A bar describing 2026-11-04 but published only on 2026-11-10
    # (e.g. a late restatement): it must not leak into a 2026-11-05 query.
    store.add_bar("2026-11-04", "2026-11-10", 103, 105, 102, 104, volume=900)
    queried = store.bars_as_of("2026-11-05")
    assert [b["date"] for b in queried] == [
        "2026-11-02", "2026-11-03", "2026-11-04"]
    # The 2026-11-04 bar visible on 2026-11-05 is the ORIGINAL
    # (close 103, published 2026-11-04), not the restatement (104).
    assert queried[-1]["close"] == pytest.approx(103.0)
    assert queried[-1]["volume"] == pytest.approx(1100.0)
    # On the publication date itself the restatement becomes visible.
    later = store.bars_as_of("2026-11-10")
    assert later[-1]["close"] == pytest.approx(104.0)
    assert later[-1]["volume"] == pytest.approx(900.0)


def test_revision_supersedes_only_after_its_publication():
    store = pit_store.PointInTimeStore()
    store.add_bar("2026-11-02", "2026-11-02", 100, 102, 99, 101)
    store.add_bar("2026-11-02", "2026-11-06", 100, 102, 99, 99.5)
    assert store.bars_as_of("2026-11-05")[0]["close"] == pytest.approx(101.0)
    assert store.bars_as_of("2026-11-06")[0]["close"] == pytest.approx(99.5)


def test_closes_as_of_is_point_in_time_correct():
    store = _store_with_bars()
    assert store.closes_as_of("2026-11-03") == [
        ("2026-11-02", 101.0), ("2026-11-03", 102.0)]


def test_fundamentals_are_point_in_time_correct():
    store = pit_store.PointInTimeStore()
    store.add_fundamental("2026-10-31", "2026-11-15",
                          revenue=1_000_000, net_income=120_000)
    store.add_fundamental("2026-10-31", "2026-12-01",
                          revenue=1_050_000, net_income=125_000)
    # Nothing published yet on 2026-11-14: the quarter is invisible.
    assert store.fundamentals_as_of("2026-11-14") == []
    assert store.latest_fundamental("2026-11-14", "revenue") is None
    # First release visible 2026-11-15; restatement only from 2026-12-01.
    assert store.fundamentals_as_of("2026-11-20") == [
        {"date": "2026-10-31", "revenue": 1_000_000.0,
         "net_income": 120_000.0}]
    assert store.latest_fundamental("2026-11-20", "revenue") == (
        "2026-10-31", 1_000_000.0)
    assert store.fundamentals_as_of("2026-12-01")[0]["revenue"] == (
        pytest.approx(1_050_000.0))
    assert store.latest_fundamental("2026-12-01", "net_income") == (
        "2026-10-31", 125_000.0)


def test_latest_fundamental_returns_none_for_missing_field():
    store = pit_store.PointInTimeStore()
    store.add_fundamental("2026-10-31", "2026-11-15", revenue=500)
    assert store.latest_fundamental("2026-11-20", "net_income") is None


def test_bars_sorted_by_as_of_regardless_of_insertion_order():
    store = pit_store.PointInTimeStore()
    store.add_bar("2026-11-04", "2026-11-04", 102, 104, 101, 103)
    store.add_bar("2026-11-02", "2026-11-02", 100, 102, 99, 101)
    assert [b["date"] for b in store.bars_as_of("2026-11-04")] == [
        "2026-11-02", "2026-11-04"]
    assert store.as_of_dates() == ["2026-11-02", "2026-11-04"]


def test_len_counts_visible_and_unpublished_records():
    store = _store_with_bars()
    store.add_bar("2026-11-05", "2026-11-20", 103, 105, 102, 104)
    store.add_fundamental("2026-10-31", "2026-11-15", revenue=10)
    assert len(store) == 5


def test_volume_is_optional_and_defaults_to_none():
    store = pit_store.PointInTimeStore()
    store.add_bar("2026-11-02", "2026-11-02", 100, 102, 99, 101)
    assert store.bars_as_of("2026-11-02")[0]["volume"] is None


def test_date_objects_are_accepted():
    store = pit_store.PointInTimeStore()
    store.add_bar(datetime.date(2026, 11, 2), datetime.date(2026, 11, 2),
                  100, 102, 99, 101)
    assert store.bars_as_of(datetime.date(2026, 11, 2))[0]["date"] == (
        "2026-11-02")


def test_published_before_as_of_is_rejected():
    store = pit_store.PointInTimeStore()
    with pytest.raises(ValueError, match="must not precede"):
        store.add_bar("2026-11-05", "2026-11-02", 100, 102, 99, 101)
    with pytest.raises(ValueError, match="must not precede"):
        store.add_fundamental("2026-10-31", "2026-10-01", revenue=10)


def test_bad_dates_are_rejected():
    store = pit_store.PointInTimeStore()
    with pytest.raises(ValueError, match="ISO YYYY-MM-DD"):
        store.add_bar("02/11/2026", "2026-11-02", 100, 102, 99, 101)
    with pytest.raises(ValueError, match="ISO YYYY-MM-DD"):
        store.bars_as_of("November 2")


def test_bad_prices_are_rejected():
    store = pit_store.PointInTimeStore()
    with pytest.raises(ValueError):
        store.add_bar("2026-11-02", "2026-11-02", 100, 102, 99, 0)
    with pytest.raises(ValueError):
        store.add_bar("2026-11-02", "2026-11-02", 100, 99, 101, 101)
    with pytest.raises(ValueError):
        store.add_bar("2026-11-02", "2026-11-02", 100, 102, 99, 103)
    with pytest.raises(ValueError):
        store.add_bar("2026-11-02", "2026-11-02", 100, 102, 99, 98)
    with pytest.raises(ValueError):
        store.add_bar("2026-11-02", "2026-11-02", 100, 102, 99, 101,
                      volume=-5)


def test_fundamental_needs_a_field_and_finite_values():
    store = pit_store.PointInTimeStore()
    with pytest.raises(ValueError, match="at least one"):
        store.add_fundamental("2026-10-31", "2026-11-15")
    with pytest.raises(ValueError):
        store.add_fundamental("2026-10-31", "2026-11-15",
                              revenue=float("nan"))


def test_bars_for_timemachine_feeds_simulate_without_lookahead():
    """Walk-forward check: a simulation run on bars visible on
    2026-11-05 must not contain the bar published 2026-11-10,
    even though that bar was already added to the store."""
    store = pit_store.PointInTimeStore()
    # 60 synthetic bars, published the day they describe, plus one
    # late-published bar for the last date.
    close = 100.0
    for day in range(1, 61):
        date = f"2026-08-{day:02d}" if day <= 31 else f"2026-09-{day - 31:02d}"
        close *= 1.001
        store.add_bar(date, date, close * 0.99, close * 1.01,
                      close * 0.99, close)
    store.add_bar("2026-09-29", "2026-10-05", 200, 202, 199, 201)
    visible = store.bars_for_timemachine("2026-09-29")
    assert len(visible) == 60
    assert all(b["close"] < 150 for b in visible)  # the 201 leak is absent
    res = timemachine.simulate(visible, 100, 95, 110,
                               n_sims=100, seed=19)
    assert res["method"] == "simulation, not a prediction"
    assert res["n_windows"] == 41  # 60 steps - 20 + 1
    # Once published, the late bar is visible (same as_of, so still
    # 60 bars — but the last close is now the restated 201).
    later = store.bars_for_timemachine("2026-10-05")
    assert len(later) == 60
    assert later[-1]["close"] == pytest.approx(201.0)
