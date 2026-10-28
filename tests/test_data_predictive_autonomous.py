import datetime, os
import pytest
from toolkit import autonomous, data, predictive
from toolkit.checks import validate_price_pairs, validate_series

def test_validate_series_rejects_nan_inf_none_str():
    for bad in ([1, float("nan")], [1, float("inf")], [1, None], [1, "2"], [True, 2]):
        with pytest.raises(ValueError): validate_series("x", bad)

def test_validate_price_pairs_rejects_duplicates_and_bad_dates():
    with pytest.raises(ValueError): validate_price_pairs("x", [("2026-01-01", 10), ("2026-01-01", 11)])
    with pytest.raises(ValueError): validate_price_pairs("x", [("01/01/2026", 10)])
    with pytest.raises(ValueError): validate_price_pairs("x", [("2026-01-01", -5)])

def test_freshness_gate_and_future_date():
    today = datetime.date(2026, 10, 28)
    assert data.check_freshness("2026-10-27", max_age_days=7, today=today)["stale"] is False
    assert data.check_freshness("2026-10-01", max_age_days=7, today=today)["stale"] is True
    with pytest.raises(ValueError): data.check_freshness("2026-10-29", today=today)

def test_fallback_skips_stale_then_uses_fresh():
    today = datetime.date(2026, 10, 28)
    stale = lambda s: [("2026-09-01", 10.0), ("2026-09-02", 11.0)]
    fresh = lambda s: [("2026-10-27", 10.0), ("2026-10-28", 11.0)]
    out = data.fetch_fresh_prices("X", [("stale-src", stale), ("fresh-src", fresh)], max_age_days=7, today=today)
    assert out["source"] == "fresh-src" and out["provenance"]["stale"] is False

def test_fallback_never_returns_stale():
    today = datetime.date(2026, 10, 28)
    stale = lambda s: [("2026-09-01", 10.0)]
    with pytest.raises(RuntimeError): data.fetch_fresh_prices("X", [("s", stale)], today=today)

def test_missing_key_error_names_provider_and_env(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ALPHA_VANTAGE_API_KEY"): data.get_api_key("ALPHA_VANTAGE_API_KEY", "Alpha Vantage", "https://example.com")

def test_auto_select_picks_trend_on_trending_series():
    series = [100 + i * 2 for i in range(40)]
    sel = predictive.auto_select_model(series, train_size=20)
    assert sel["best_model"] == "linear_trend" and sel["beats_naive"] is True

def test_anomaly_flags_spike():
    series = [100.0] * 20 + [200.0] + [100.0] * 5
    flags = predictive.anomaly_flags(series, z=2.0)
    assert len(flags) >= 1

def test_autonomous_run_once_fresh_and_failed_symbol(tmp_path):
    today = datetime.date(2026, 10, 28)
    import datetime as _dt
    _start = _dt.date(2026, 9, 19)
    fresh_pairs = [((_start + _dt.timedelta(days=i)).isoformat(), 100.0 + i * 0.5) for i in range(40)]
    def good(s): return fresh_pairs
    def bad(s): raise RuntimeError("network down")
    cfg = {"symbols": ["GOOD", "BAD"], "output_dir": str(tmp_path), "max_age_days": 7, "today": today,
           "now": datetime.datetime(2026, 10, 28, 12, 0, tzinfo=datetime.timezone.utc),
           "fetchers": [("good", good), ("bad", bad)]}
    # GOOD uses good fetcher; BAD: override via per-symbol fetchers not supported, so use a fetcher that fails for BAD
    def router(s):
        if s == "BAD": raise RuntimeError("network down")
        return fresh_pairs
    cfg["fetchers"] = [("router", router)]
    summary = autonomous.run_once(cfg)
    assert summary["symbols"]["GOOD"]["status"] == "fresh"
    assert summary["symbols"]["BAD"]["status"] == "STALE_OR_FAILED"
    assert (tmp_path / "GOOD" / "latest.md").exists()
    assert (tmp_path / "audit.jsonl").exists()

def test_autonomous_loop_bounded(tmp_path):
    cfg = {"symbols": ["X"], "output_dir": str(tmp_path), "today": datetime.date(2026, 10, 28),
           "fetchers": [("f", lambda s: [((_dt.date(2026, 9, 19) + _dt.timedelta(days=i)).isoformat(), 100.0 + i) for i in range(40)])],
           "interval_seconds": 1}
    assert autonomous.run_loop(cfg, max_runs=2, sleep_fn=lambda s: None) == 2
