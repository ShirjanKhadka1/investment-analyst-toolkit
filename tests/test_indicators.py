"""Tests for toolkit/indicators.py — synthetic example data only."""

import pytest

from toolkit import indicators


def _flat(n, value=100.0):
    return [value] * n


def _rising(n, start=100.0, step=1.0):
    return [start + step * i for i in range(n)]


def test_sma_warmup_and_values():
    out = indicators.sma([1, 2, 3, 4, 5], 3)
    assert out == [None, None, 2.0, 3.0, 4.0]


def test_sma_default_window_is_20():
    out = indicators.sma(_flat(25))
    assert out[:19] == [None] * 19
    assert out[19] == 100.0


def test_ema_hand_computed_and_seeded_by_sma():
    # k = 2/(3+1) = 0.5; seed = SMA(10, 11, 12) = 11 at index 2.
    out = indicators.ema([10, 11, 12, 13, 14], 3)
    assert out == [None, None, 11.0, 12.0, 13.0]


def test_ema_flat_series_is_flat():
    assert indicators.ema(_flat(25), 20)[-1] == 100.0


def test_rsi_rising_is_100_falling_is_0_flat_is_50():
    assert indicators.rsi(_rising(20), 14)[-1] == 100.0
    assert indicators.rsi(_rising(20, step=-1.0), 14)[-1] == 0.0
    assert indicators.rsi(_flat(20), 14)[-1] == 50.0


def test_rsi_warmup_is_none():
    out = indicators.rsi(_rising(20), 14)
    assert out[:14] == [None] * 14
    assert out[14] is not None


def test_macd_flat_series_is_zero_after_warmup():
    res = indicators.macd(_flat(40))
    assert res["macd"][25] == 0.0
    assert res["signal"][33] == 0.0
    assert res["histogram"][33] == 0.0
    assert res["macd"][:25] == [None] * 25
    assert res["signal"][:33] == [None] * 33


def test_macd_rejects_fast_not_below_slow():
    with pytest.raises(ValueError):
        indicators.macd(_flat(40), fast=26, slow=12)


def test_bollinger_flat_series_bands_equal_price():
    res = indicators.bollinger_bands(_flat(25))
    assert res["upper"][24] == res["middle"][24] == res["lower"][24] == 100.0


def test_bollinger_hand_computed():
    # Window 1..5: mean 3, sample stdev sqrt(2.5); upper = 3 + 2*sqrt(2.5).
    res = indicators.bollinger_bands([1, 2, 3, 4, 5], window=5, num_std=2.0)
    assert res["middle"][4] == 3.0
    assert res["upper"][4] == pytest.approx(3.0 + 2.0 * (2.5 ** 0.5))
    assert res["lower"][4] == pytest.approx(3.0 - 2.0 * (2.5 ** 0.5))


def test_atr_constant_range():
    out = indicators.atr([102.0] * 20, [100.0] * 20, [101.0] * 20, 14)
    assert out[:13] == [None] * 13
    assert out[-1] == 2.0


def test_atr_true_range_uses_previous_close_gap():
    # TR0 = 2; TR1 = max(10, |110-99|=11, |100-99|=1) = 11; ATR(2) = 6.5.
    out = indicators.atr([100, 110], [98, 100], [99, 105], 2)
    assert out == [None, 6.5]


def test_stochastic_close_at_high_is_100_at_low_is_0_flat_is_50():
    highs = [110.0] * 20
    lows = [90.0] * 20
    assert indicators.stochastic(highs, lows, [110.0] * 20)["%K"][-1] == 100.0
    assert indicators.stochastic(highs, lows, [90.0] * 20)["%K"][-1] == 0.0
    assert indicators.stochastic([100.0] * 20, [100.0] * 20, [100.0] * 20)["%K"][-1] == 50.0


def test_stochastic_d_is_sma_of_k():
    res = indicators.stochastic([110.0] * 20, [90.0] * 20, [110.0] * 20)
    assert res["%K"][13] == 100.0
    assert res["%D"][15] == 100.0
    assert res["%D"][:15] == [None] * 15


def test_adx_strong_trend_is_100_flat_is_0():
    trend = indicators.adx(_rising(40, 100, 1.0), _rising(40, 98, 1.0),
                           _rising(40, 99, 1.0))
    assert trend["adx"][-1] == 100.0
    # +DM is 1/day, true range is 2/day, so +DI converges to 50, not 100.
    assert trend["plus_di"][-1] == pytest.approx(50.0, abs=1.0)
    assert trend["minus_di"][-1] == 0.0
    flat = indicators.adx(_flat(40), _flat(40), _flat(40))
    assert flat["adx"][-1] == 0.0


def test_adx_first_value_lands_at_2w_minus_2():
    out = indicators.adx(_rising(40, 100, 1.0), _rising(40, 98, 1.0),
                         _rising(40, 99, 1.0))["adx"]
    assert out[:26] == [None] * 26
    assert out[26] is not None


def test_keltner_flat_bars_hand_computed():
    # EMA(20) of 101 = 101; ATR(10) of range 2 = 2; bands 101 +/- 2*2.
    res = indicators.keltner_channels([102.0] * 30, [100.0] * 30, [101.0] * 30)
    assert res["middle"][19] == 101.0
    assert res["upper"][19] == 105.0
    assert res["lower"][19] == 97.0


def test_ichimoku_flat_series_and_displacement():
    bars = 100
    res = indicators.ichimoku([110.0] * bars, [90.0] * bars, [100.0] * bars)
    assert res["tenkan"][8] == 100.0
    assert res["tenkan"][:8] == [None] * 8
    assert res["kijun"][25] == 100.0
    # Senkou A first appears at 26 + 25 = 51; senkou B at 26 + 51 = 77.
    assert res["senkou_a"][51] == 100.0
    assert res["senkou_a"][:51] == [None] * 51
    assert res["senkou_b"][77] == 100.0
    assert res["senkou_b"][:77] == [None] * 77
    # Chikou is the close shifted 26 bars back; last 26 are None.
    assert res["chikou"][0] == 100.0
    assert res["chikou"][-1] is None
    assert res["chikou"][73] == 100.0
    assert res["chikou"][74] is None


def test_vwap_hand_computed_and_zero_volume():
    out = indicators.vwap([102.0, 102.0], [100.0, 100.0], [101.0, 101.0],
                          [10.0, 30.0])
    assert out == [101.0, 101.0]
    assert indicators.vwap([102.0], [100.0], [101.0], [0.0]) == [None]


def test_vwap_rejects_negative_volume_and_mismatched_length():
    with pytest.raises(ValueError):
        indicators.vwap([102.0], [100.0], [101.0], [-1.0])
    with pytest.raises(ValueError):
        indicators.vwap([102.0, 102.0], [100.0, 100.0], [101.0, 101.0], [1.0])


def test_compute_all_keys_lengths_and_vwap_option():
    closes = _rising(60, 100, 0.5)
    res = indicators.compute_all(closes)
    expected = {"sma_20", "ema_20", "rsi_14", "macd", "macd_signal",
                "macd_histogram", "bollinger_upper", "bollinger_middle",
                "bollinger_lower", "atr_14", "stochastic_k", "stochastic_d",
                "adx_14", "plus_di_14", "minus_di_14", "keltner_upper",
                "keltner_middle", "keltner_lower", "ichimoku_tenkan",
                "ichimoku_kijun", "ichimoku_senkou_a", "ichimoku_senkou_b",
                "ichimoku_chikou"}
    assert set(res) == expected
    assert all(len(v) == 60 for v in res.values())
    assert "vwap" not in res
    with_vol = indicators.compute_all(closes, volumes=[1000.0] * 60)
    assert "vwap" in with_vol and len(with_vol["vwap"]) == 60


def test_compute_all_defaults_to_close_as_high_low():
    res = indicators.compute_all(_flat(30))
    assert res["atr_14"][-1] == 0.0
    assert res["stochastic_k"][-1] == 50.0


def test_indicator_defaults_cover_every_indicator_with_a_limit():
    for name in ("sma", "ema", "rsi", "macd", "bollinger", "atr",
                 "stochastic", "adx", "keltner", "ichimoku", "vwap"):
        assert name in indicators.INDICATOR_DEFAULTS
        assert "cannot_prove" in indicators.INDICATOR_DEFAULTS[name]
        assert indicators.INDICATOR_DEFAULTS[name]["cannot_prove"]
    assert indicators.INDICATOR_DEFAULTS["rsi"]["window"] == 14
    assert indicators.INDICATOR_DEFAULTS["macd"] == {
        "fast": 12, "slow": 26, "signal": 9,
        "cannot_prove": indicators.INDICATOR_DEFAULTS["macd"]["cannot_prove"],
    }


def test_validation_rejects_bad_windows_and_mismatched_series():
    with pytest.raises(ValueError):
        indicators.sma([1, 2, 3], 0)
    with pytest.raises(ValueError):
        indicators.atr([1, 2], [1, 2], [1, 2, 3])
    with pytest.raises(ValueError):
        indicators.rsi([1, 2, None], 14)
