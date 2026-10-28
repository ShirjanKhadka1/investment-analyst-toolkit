"""Input validation. Analyst rule: a bad input must fail loudly, never silently,
never with invented data, and never with a partial result presented as complete.

"Zero errors" does not mean "never raises" — it means every bad input is
caught at the boundary, with a message that says what was wrong and what
to fix, instead of producing a wrong number downstream.
"""
from __future__ import annotations

import math


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def require_positive(name, value):
    if value is None:
        raise ValueError(f"{name} is required")
    if not _is_number(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def require_non_negative(name, value):
    if value is None:
        raise ValueError(f"{name} is required")
    if not _is_number(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    return value


def require_rate(name, value):
    if value is None:
        raise ValueError(f"{name} is required")
    if not _is_number(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    if not -1 < value < 1:
        raise ValueError(f"{name} must be between -1 and 1, got {value}")
    return value


def validate_series(name, series, min_length=1):
    """A clean numeric series: non-empty, finite, no None/NaN/inf/bool/str."""
    if series is None:
        raise ValueError(f"{name} is required")
    try:
        values = list(series)
    except TypeError as e:
        raise ValueError(f"{name} must be a list of numbers") from e
    if len(values) < min_length:
        raise ValueError(f"{name} needs at least {min_length} values, got {len(values)}")
    for i, v in enumerate(values):
        if not _is_number(v):
            raise ValueError(f"{name}[{i}] must be a finite number, got {v!r}")
    return [float(v) for v in values]


def validate_price_pairs(name, pairs):
    """(date, close) pairs: ISO dates, strictly increasing, unique, close > 0.
    Returns a normalised list. Any violation raises — a broken series is
    never silently repaired, because repair would invent data."""
    import datetime
    if not pairs:
        raise ValueError(f"{name} is empty")
    out = []
    prev = None
    for i, row in enumerate(pairs):
        try:
            date_str, close = row
        except (TypeError, ValueError) as e:
            raise ValueError(f"{name}[{i}] must be a (date, close) pair, got {row!r}") from e
        try:
            d = datetime.date.fromisoformat(str(date_str))
        except ValueError as e:
            raise ValueError(f"{name}[{i}] date must be ISO YYYY-MM-DD, got {date_str!r}") from e
        if not _is_number(close) or close <= 0:
            raise ValueError(f"{name}[{i}] close must be a positive finite number, got {close!r}")
        if prev is not None and d <= prev:
            raise ValueError(f"{name} dates must be strictly increasing and unique; problem at index {i} ({date_str})")
        prev = d
        out.append((d.isoformat(), float(close)))
    return out
