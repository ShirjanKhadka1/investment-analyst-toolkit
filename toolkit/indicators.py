"""Technical indicators on plain OHLCV lists — research and education only.

Attribution (ideas only, no code copied): the indicator list and the
canonical defaults in INDICATOR_DEFAULTS follow the defaults table in
FinceptTerminal (https://github.com/Fincept-Corporation/FinceptTerminal,
commit 7716ee8, GNU AGPL-3.0-or-later). No Fincept code, text, layout or
command syntax was copied: every formula below is reimplemented from
scratch in this file from the standard published definitions, in the
toolkit's own zero-dependency style.

Research only: not investment advice, never a buy/sell/hold
recommendation. An indicator describes the past; it cannot prove what a
price will do next — each entry in INDICATOR_DEFAULTS says so in one
line. Every function returns ``None`` for warm-up bars (not enough data
yet) instead of inventing a value. Inputs are plain lists of floats;
outputs are plain lists of floats (or ``None``) of the same length.
"""
from __future__ import annotations

import math

from .checks import require_positive, validate_series

__all__ = [
    "INDICATOR_DEFAULTS",
    "adx",
    "atr",
    "bollinger_bands",
    "compute_all",
    "ema",
    "ichimoku",
    "keltner_channels",
    "macd",
    "rsi",
    "sma",
    "stochastic",
    "vwap",
]

# Canonical defaults (Fincept defaults table — ideas only, see header)
# plus one line each on what the indicator cannot prove.
INDICATOR_DEFAULTS = {
    "sma": {
        "window": 20,
        "cannot_prove": "A moving average lags price; a cross above it cannot prove an uptrend will continue.",
    },
    "ema": {
        "window": 20,
        "cannot_prove": "An EMA still lags price; it cannot prove a trend change, only that recent prices sit above older ones.",
    },
    "rsi": {
        "window": 14,
        "smoothing": "Wilder",
        "cannot_prove": "Overbought/oversold cannot prove a reversal; RSI can stay overbought through a long rally.",
    },
    "macd": {
        "fast": 12,
        "slow": 26,
        "signal": 9,
        "cannot_prove": "A MACD crossover cannot prove momentum will persist; it is a difference of two lagging averages.",
    },
    "bollinger": {
        "window": 20,
        "num_std": 2,
        "cannot_prove": "Touching a band cannot prove a reversal; prices can walk a band for a long time.",
    },
    "atr": {
        "window": 14,
        "smoothing": "Wilder",
        "cannot_prove": "ATR measures how much prices moved, not which way they will move next.",
    },
    "stochastic": {
        "k_period": 14,
        "d_period": 3,
        "cannot_prove": "A %K above 80 cannot prove a top; in a strong trend it can stay high for weeks.",
    },
    "adx": {
        "window": 14,
        "smoothing": "Wilder",
        "cannot_prove": "ADX measures trend strength, not trend direction; a high ADX cannot prove the trend is up.",
    },
    "keltner": {
        "window": 20,
        "atr_window": 10,
        "atr_multiplier": 2,
        "cannot_prove": "A close outside a Keltner channel cannot prove a breakout will hold; channels expand after the move.",
    },
    "ichimoku": {
        "tenkan_period": 9,
        "kijun_period": 26,
        "senkou_period": 52,
        "displacement": 26,
        "cannot_prove": "Cloud support/resistance cannot prove price will respect it; the cloud is drawn from past midpoints.",
    },
    "vwap": {
        "window": "cumulative (session)",
        "cannot_prove": "VWAP is a volume-weighted average of past trades, not a fair-value anchor; price can stay away from it all day.",
    },
}


def _validate_window(name, window):
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ValueError(f"{name} must be a positive integer, got {window!r}")
    return window


def _validate_ohlc(highs, lows, closes):
    highs = validate_series("highs", highs, min_length=1)
    lows = validate_series("lows", lows, min_length=1)
    closes = validate_series("closes", closes, min_length=1)
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows and closes must have the same length")
    return highs, lows, closes


def _mean(xs):
    return sum(xs) / len(xs)


def _sample_stdev(xs):
    """Sample stdev (n - 1), the convention used by toolkit/performance.py."""
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _true_ranges(highs, lows, closes):
    """True range per bar: max(high-low, |high-prev close|, |low-prev close|).

    First bar has no previous close, so its true range is high - low.
    """
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i],
                      abs(highs[i] - closes[i - 1]),
                      abs(lows[i] - closes[i - 1])))
    return tr


def _wilder_smooth(values, window):
    """Wilder smoothing: first value is the mean of the first `window`
    values (at index window - 1); later values are
    (prev * (window - 1) + current) / window. None before the seed."""
    out = [None] * len(values)
    if len(values) < window:
        return out
    out[window - 1] = _mean(values[:window])
    for i in range(window, len(values)):
        out[i] = (out[i - 1] * (window - 1) + values[i]) / window
    return out


def _ema_from_values(values, window):
    """EMA over a list that may contain None (e.g. a MACD line).

    Seeds with the simple average of the first `window` non-None values;
    the seed lands on the bar of the last value in that seed window.
    Multiplier k = 2 / (window + 1).
    """
    out = [None] * len(values)
    k = 2.0 / (window + 1)
    seed = []
    seed_idx = None
    for i, v in enumerate(values):
        if v is None:
            continue
        seed.append(v)
        if len(seed) == window:
            seed_idx = i
            break
    if seed_idx is None:
        return out
    out[seed_idx] = _mean(seed)
    prev = out[seed_idx]
    for i in range(seed_idx + 1, len(values)):
        if values[i] is None:
            continue
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def sma(prices, window=20):
    """Simple moving average: mean of the last `window` closes.

    Inputs: closes (list of floats), window (positive int, default 20).
    Output: list, same length; None for the first window - 1 bars.
    Limit: lags price by construction; a rising SMA cannot prove
    the next bar will rise.
    """
    prices = validate_series("prices", prices, min_length=1)
    _validate_window("window", window)
    out = [None] * len(prices)
    for i in range(window - 1, len(prices)):
        out[i] = _mean(prices[i - window + 1:i + 1])
    return out


def ema(prices, window=20):
    """Exponential moving average: EMA_t = k * price_t + (1 - k) * EMA_{t-1},
    k = 2 / (window + 1), seeded with the SMA of the first `window` closes
    (so the first non-None value is at index window - 1).

    Inputs: closes (list of floats), window (positive int, default 20).
    Output: list, same length; None before the seed bar.
    Limit: still a lagging average — the seed choice moves early values.
    """
    prices = validate_series("prices", prices, min_length=1)
    _validate_window("window", window)
    return _ema_from_values(prices, window)


def rsi(prices, window=14):
    """Relative Strength Index (Wilder, 1978): RSI = 100 - 100 / (1 + RS),
    RS = average gain / average loss over `window` closes. Averages are
    Wilder-smoothed: first average is the mean of the first `window`
    changes; later averages are (prev * (window - 1) + current) / window.
    A period with no loss gives RSI 100; a flat series gives 50.

    Inputs: closes (list of floats), window (positive int, default 14).
    Output: list, same length; None for the first `window` bars.
    Limit: 70/30 "overbought/oversold" are conventions, not reversal
    proofs — RSI can stay above 70 through a whole rally.
    """
    prices = validate_series("prices", prices, min_length=1)
    _validate_window("window", window)
    out = [None] * len(prices)
    if len(prices) <= window:
        return out
    gains = [max(0.0, prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    losses = [max(0.0, prices[i - 1] - prices[i]) for i in range(1, len(prices))]
    avg_gain = _mean(gains[:window])
    avg_loss = _mean(losses[:window])
    out[window] = _rsi_value(avg_gain, avg_loss)
    for i in range(window + 1, len(prices)):
        avg_gain = (avg_gain * (window - 1) + gains[i - 1]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i - 1]) / window
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain, avg_loss):
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def macd(prices, fast=12, slow=26, signal=9):
    """MACD (Appel): macd = EMA(fast) - EMA(slow); signal = EMA(macd, 9);
    histogram = macd - signal.

    Inputs: closes (list of floats); fast/slow/signal positive ints
    with fast < slow (defaults 12/26/9).
    Output: dict with "macd", "signal", "histogram" lists, each the same
    length as the input; None where the underlying EMA is not seeded yet
    (macd from index slow - 1, signal from index slow - 1 + signal - 1).
    Limit: a histogram crossover is a difference of two lagging averages;
    it cannot prove momentum will persist.
    """
    prices = validate_series("prices", prices, min_length=1)
    _validate_window("fast", fast)
    _validate_window("slow", slow)
    _validate_window("signal", signal)
    if fast >= slow:
        raise ValueError(f"need fast < slow, got fast={fast} slow={slow}")
    fast_ema = ema(prices, fast)
    slow_ema = ema(prices, slow)
    line = [None if (f is None or s is None) else f - s
            for f, s in zip(fast_ema, slow_ema)]
    sig = _ema_from_values(line, signal)
    hist = [None if (m is None or s is None) else m - s
            for m, s in zip(line, sig)]
    return {"macd": line, "signal": sig, "histogram": hist}


def bollinger_bands(prices, window=20, num_std=2.0):
    """Bollinger Bands: middle = SMA(window); upper/lower = middle
    +/- num_std * sample stdev (n - 1) of the same window of closes.

    Inputs: closes (list of floats), window (positive int, default 20),
    num_std (positive number, default 2).
    Output: dict with "upper", "middle", "lower" lists, same length;
    None for the first window - 1 bars.
    Limit: bands widen after volatility arrives; touching a band cannot
    prove a reversal.
    """
    prices = validate_series("prices", prices, min_length=1)
    _validate_window("window", window)
    require_positive("num_std", num_std)
    upper = [None] * len(prices)
    middle = [None] * len(prices)
    lower = [None] * len(prices)
    for i in range(window - 1, len(prices)):
        w = prices[i - window + 1:i + 1]
        m = _mean(w)
        sd = _sample_stdev(w)
        middle[i] = m
        upper[i] = m + num_std * sd
        lower[i] = m - num_std * sd
    return {"upper": upper, "middle": middle, "lower": lower}


def atr(highs, lows, closes, window=14):
    """Average True Range (Wilder): true range is
    max(high-low, |high-prev close|, |low-prev close|); ATR is the
    Wilder-smoothed true range (first value = mean of the first
    `window` true ranges, at index window - 1).

    Inputs: highs, lows, closes (equal-length lists of floats),
    window (positive int, default 14).
    Output: list, same length; None for the first window - 1 bars.
    Limit: ATR measures how much prices moved, not which way they
    will move next.
    """
    highs, lows, closes = _validate_ohlc(highs, lows, closes)
    _validate_window("window", window)
    return _wilder_smooth(_true_ranges(highs, lows, closes), window)


def stochastic(highs, lows, closes, k_period=14, d_period=3):
    """Stochastic oscillator (Lane): %K = 100 * (close - lowest low) /
    (highest high - lowest low) over the last `k_period` bars;
    %D = SMA(`d_period`) of %K. A zero range (flat window) gives %K 50.

    Inputs: highs, lows, closes (equal-length lists), k_period and
    d_period (positive ints, defaults 14/3).
    Output: dict with "%K" and "%D" lists, same length; %K is None for
    the first k_period - 1 bars, %D None until k_period - 1 + d_period - 1.
    Limit: %K above 80 cannot prove a top; it can stay high in a trend.
    """
    highs, lows, closes = _validate_ohlc(highs, lows, closes)
    _validate_window("k_period", k_period)
    _validate_window("d_period", d_period)
    pct_k = [None] * len(closes)
    for i in range(k_period - 1, len(closes)):
        lo = min(lows[i - k_period + 1:i + 1])
        hi = max(highs[i - k_period + 1:i + 1])
        pct_k[i] = 50.0 if hi == lo else 100.0 * (closes[i] - lo) / (hi - lo)
    pct_d = [None] * len(closes)
    for i in range(k_period - 1 + d_period - 1, len(closes)):
        pct_d[i] = _mean(pct_k[i - d_period + 1:i + 1])
    return {"%K": pct_k, "%D": pct_d}


def adx(highs, lows, closes, window=14):
    """Average Directional Index (Wilder): +DM is the upward move when it
    exceeds the downward move (else 0), -DM the mirror; +DI = 100 *
    Wilder-smoothed +DM / Wilder-smoothed true range (and -DI likewise);
    DX = 100 * |+DI - -DI| / (+DI + -DI); ADX is the Wilder-smoothed DX,
    seeded with the mean of the first `window` DX values (so the first
    ADX lands at index 2 * window - 2).

    Inputs: highs, lows, closes (equal-length lists), window
    (positive int, default 14).
    Output: dict with "adx", "plus_di", "minus_di" lists, same length;
    DI values start at index window - 1, ADX at index 2 * window - 2.
    Limit: ADX measures trend strength, not direction; a high ADX
    cannot prove the trend is up.
    """
    highs, lows, closes = _validate_ohlc(highs, lows, closes)
    _validate_window("window", window)
    tr = _true_ranges(highs, lows, closes)
    plus_dm = [0.0] * len(closes)
    minus_dm = [0.0] * len(closes)
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if up > 0 and up > down else 0.0
        minus_dm[i] = down if down > 0 and down > up else 0.0
    str_ = _wilder_smooth(tr, window)
    splus = _wilder_smooth(plus_dm, window)
    sminus = _wilder_smooth(minus_dm, window)
    plus_di = [None] * len(closes)
    minus_di = [None] * len(closes)
    dx = [None] * len(closes)
    for i in range(window - 1, len(closes)):
        if str_[i] == 0.0:
            plus_di[i] = 0.0
            minus_di[i] = 0.0
            dx[i] = 0.0
            continue
        plus_di[i] = 100.0 * splus[i] / str_[i]
        minus_di[i] = 100.0 * sminus[i] / str_[i]
        total = plus_di[i] + minus_di[i]
        dx[i] = 0.0 if total == 0.0 else 100.0 * abs(plus_di[i] - minus_di[i]) / total
    out = [None] * len(closes)
    first_adx = 2 * window - 2
    if len(closes) > first_adx:
        out[first_adx] = _mean(dx[window - 1:first_adx + 1])
        for i in range(first_adx + 1, len(closes)):
            out[i] = (out[i - 1] * (window - 1) + dx[i]) / window
    return {"adx": out, "plus_di": plus_di, "minus_di": minus_di}


def keltner_channels(highs, lows, closes, window=20, atr_window=10, atr_multiplier=2.0):
    """Keltner Channels: middle = EMA(`window`) of closes; upper/lower =
    middle +/- `atr_multiplier` * ATR(`atr_window`, Wilder).

    Inputs: highs, lows, closes (equal-length lists), window (default 20),
    atr_window (default 10), atr_multiplier (positive number, default 2).
    Output: dict with "upper", "middle", "lower" lists, same length;
    None until both the EMA and the ATR are seeded.
    Limit: a close outside a channel cannot prove a breakout will hold;
    channels expand after the move.
    """
    highs, lows, closes = _validate_ohlc(highs, lows, closes)
    _validate_window("window", window)
    _validate_window("atr_window", atr_window)
    require_positive("atr_multiplier", atr_multiplier)
    mid = ema(closes, window)
    atr_vals = atr(highs, lows, closes, atr_window)
    upper = [None] * len(closes)
    lower = [None] * len(closes)
    for i in range(len(closes)):
        if mid[i] is not None and atr_vals[i] is not None:
            upper[i] = mid[i] + atr_multiplier * atr_vals[i]
            lower[i] = mid[i] - atr_multiplier * atr_vals[i]
    return {"upper": upper, "middle": mid, "lower": lower}


def ichimoku(highs, lows, closes, tenkan_period=9, kijun_period=26, senkou_period=52):
    """Ichimoku Kinko Hyo (Hosoda): tenkan = (highest high + lowest low)/2
    over `tenkan_period`; kijun the same over `kijun_period`; senkou A =
    (tenkan + kijun)/2 plotted `kijun_period` bars ahead; senkou B =
    (highest high + lowest low)/2 over `senkou_period`, also plotted
    `kijun_period` bars ahead; chikou = close plotted `kijun_period`
    bars behind (chikou[i] = close[i + kijun_period]).

    Inputs: highs, lows, closes (equal-length lists); periods are
    positive ints (defaults 9/26/52).
    Output: dict with "tenkan", "kijun", "senkou_a", "senkou_b",
    "chikou" lists, each the same length as the input; span values are
    None until their source period is complete and, for the senkou
    spans, until the 26-bar displacement has passed.
    Limit: cloud support/resistance is drawn from past midpoints; it
    cannot prove price will respect it.
    """
    highs, lows, closes = _validate_ohlc(highs, lows, closes)
    _validate_window("tenkan_period", tenkan_period)
    _validate_window("kijun_period", kijun_period)
    _validate_window("senkou_period", senkou_period)

    def _midpoint(period):
        out = [None] * len(closes)
        for i in range(period - 1, len(closes)):
            out[i] = (max(highs[i - period + 1:i + 1])
                      + min(lows[i - period + 1:i + 1])) / 2.0
        return out

    tenkan = _midpoint(tenkan_period)
    kijun = _midpoint(kijun_period)
    senkou_b_raw = _midpoint(senkou_period)
    senkou_a = [None] * len(closes)
    senkou_b = [None] * len(closes)
    for i in range(len(closes)):
        j = i - kijun_period
        if j >= 0:
            if tenkan[j] is not None and kijun[j] is not None:
                senkou_a[i] = (tenkan[j] + kijun[j]) / 2.0
            if senkou_b_raw[j] is not None:
                senkou_b[i] = senkou_b_raw[j]
    chikou = [None] * len(closes)
    for i in range(len(closes) - kijun_period):
        chikou[i] = closes[i + kijun_period]
    return {"tenkan": tenkan, "kijun": kijun, "senkou_a": senkou_a,
            "senkou_b": senkou_b, "chikou": chikou}


def vwap(highs, lows, closes, volumes):
    """Volume-Weighted Average Price, cumulative from the first bar:
    VWAP_t = sum(typical * volume) / sum(volume), typical = (high + low
    + close) / 3, sums running from bar 0 to bar t.

    Inputs: highs, lows, closes (equal-length lists), volumes
    (equal-length list of non-negative numbers).
    Output: list, same length; None on any bar where cumulative volume
    is still zero.
    Limit: VWAP is an average of past trades, not a fair-value anchor;
    price can stay away from it all day.
    """
    highs, lows, closes = _validate_ohlc(highs, lows, closes)
    volumes = validate_series("volumes", volumes, min_length=1)
    if len(volumes) != len(closes):
        raise ValueError("volumes must have the same length as closes")
    if any(v < 0 for v in volumes):
        raise ValueError("volumes must not be negative")
    out = [None] * len(closes)
    cum_tp_vol = 0.0
    cum_vol = 0.0
    for i in range(len(closes)):
        typical = (highs[i] + lows[i] + closes[i]) / 3.0
        cum_tp_vol += typical * volumes[i]
        cum_vol += volumes[i]
        out[i] = None if cum_vol == 0.0 else cum_tp_vol / cum_vol
    return out


def compute_all(closes, highs=None, lows=None, volumes=None):
    """One dict of every indicator in this module, on the same bars.

    Inputs: closes (list of floats); highs/lows default to closes
    (so high = low = close, which makes ATR 0 and %K 50 — pass real
    highs/lows for range indicators); volumes optional — the "vwap"
    key is present only when volumes are given.
    Output: dict of indicator name -> list (same length as closes),
    using the canonical defaults in INDICATOR_DEFAULTS:
    sma_20, ema_20, rsi_14, macd, macd_signal, macd_histogram,
    bollinger_upper/middle/lower, atr_14, stochastic_k/d, adx_14,
    plus_di_14, minus_di_14, keltner_upper/middle/lower,
    ichimoku_tenkan/kijun/senkou_a/senkou_b/chikou, and vwap if asked.
    Limit: a dict of indicators is still only a description of the
    past — no combination of them can prove a future move.
    """
    closes = validate_series("closes", closes, min_length=1)
    n = len(closes)
    highs = list(closes) if highs is None else validate_series("highs", highs, min_length=1)
    lows = list(closes) if lows is None else validate_series("lows", lows, min_length=1)
    if not (len(highs) == len(lows) == n):
        raise ValueError("highs, lows and closes must have the same length")
    bands = bollinger_bands(closes)
    macd_vals = macd(closes)
    stoch = stochastic(highs, lows, closes)
    adx_vals = adx(highs, lows, closes)
    keltner = keltner_channels(highs, lows, closes)
    ichi = ichimoku(highs, lows, closes)
    result = {
        "sma_20": sma(closes),
        "ema_20": ema(closes),
        "rsi_14": rsi(closes),
        "macd": macd_vals["macd"],
        "macd_signal": macd_vals["signal"],
        "macd_histogram": macd_vals["histogram"],
        "bollinger_upper": bands["upper"],
        "bollinger_middle": bands["middle"],
        "bollinger_lower": bands["lower"],
        "atr_14": atr(highs, lows, closes),
        "stochastic_k": stoch["%K"],
        "stochastic_d": stoch["%D"],
        "adx_14": adx_vals["adx"],
        "plus_di_14": adx_vals["plus_di"],
        "minus_di_14": adx_vals["minus_di"],
        "keltner_upper": keltner["upper"],
        "keltner_middle": keltner["middle"],
        "keltner_lower": keltner["lower"],
        "ichimoku_tenkan": ichi["tenkan"],
        "ichimoku_kijun": ichi["kijun"],
        "ichimoku_senkou_a": ichi["senkou_a"],
        "ichimoku_senkou_b": ichi["senkou_b"],
        "ichimoku_chikou": ichi["chikou"],
    }
    if volumes is not None:
        result["vwap"] = vwap(highs, lows, closes, volumes)
    return result
