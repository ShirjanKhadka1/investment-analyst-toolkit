"""Universe screener over the five named setups (Round 7, PR 7.3).

A scan takes a caller-supplied universe — a mapping of symbol to
``(date, close)`` pairs — and a criteria spec, and evaluates the five
named setups from ``toolkit/terminal.py`` (``evaluate_setups``) per
symbol. The screener never fetches live data itself and never
reimplements a setup rule: it calls the setups, so a rule change in
``terminal.py`` changes the screener with it.

Criteria grammar
----------------
A criteria spec is a plain dict with these keys (unknown keys are
refused, never silently ignored):

- ``as_of`` (required, ISO ``YYYY-MM-DD``): the scan's as-of date.
  Only bars dated on or before ``as_of`` are used — a match can never
  see a future bar.
- ``setups`` (optional, default all five): a non-empty list of setup
  names from ``toolkit/terminal.py``'s ``SETUP_ORDER``, no duplicates.
- ``min_price`` (optional, float > 0): the latest close (as of
  ``as_of``) must be at least this; a symbol below it is filtered
  out, and its audit rows say so.
- ``lookback`` (optional, int >= 1): at most this many of the most
  recent bars (on or before ``as_of``) are evaluated. A lookback
  shorter than a setup's minimum leaves that setup unmatched with
  its usual "too few closes" detail — it is not an error.
- ``min_volume`` (optional, float >= 0): the latest bar's volume must
  be at least this. It applies only where volume data exists for the
  symbol (see ``volumes`` below); a symbol with no volume data is
  scanned without the filter, and its audit rows record
  ``volume_applied: false``.

``parse_criteria`` validates a spec and returns a frozen
``ScreenerCriteria``. Anything invalid raises ``ValueError`` loudly:
an unknown setup, an empty setup list, a duplicate, a non-positive
``min_price``, a negative ``min_volume``, a non-positive
``lookback``, a bad date, a wrong type, or an unknown key. The spec
is parsed safely: plain-dict validation only — no ``eval``, no
``exec``, no dynamic imports, anywhere in this module.

Firing rate vs base rate
------------------------
For each setup the scan reports two numbers:

- ``firing_rate`` — the share of scanned symbols the setup matches
  under the actual criteria (filters and lookback applied).
- ``base_rate`` — the share of the same symbols the setup matches
  with NO criteria filters: the same universe, the same as-of cut,
  full bars (no lookback), no ``min_price`` / ``min_volume`` filter.

The base rate is the unfiltered firing rate on this universe, so the
gap between the two numbers is exactly what the criteria did — a
filter that halves the firing rate says so in the output instead of
hiding it. Both numbers live on the ``ScanResult`` (``rates``) and
on every audit row (``firing_rate`` / ``base_rate``).

Audit log
---------
Every scan returns one audit row per (symbol, setup) pair — matched
or not, filtered or not — so a later reader can reproduce exactly
which criteria produced which matches. A zero-match scan still
returns (and, when ``audit_path`` is given, still writes) its full
rows: an empty result is a result, not a missing log. A row carries
``symbol``, ``setup``, ``matched``, ``as_of``, the canonical
``criteria`` dict, ``bars_used`` (closes evaluated, after the as-of
cut and lookback), ``volume_applied`` (whether the ``min_volume``
filter was consulted for this symbol), the setup's firing and base
rates, and the setup's ``detail`` string (or the filter reason that
kept the symbol out). ``append_audit_log`` appends rows as JSONL
(one object per line); ``read_audit_log`` reads them back. The
in-memory rows are the log — the file is just where the caller
persists them; pass ``audit_path`` to ``scan_universe`` to have the
scan append its rows itself.

Point-in-time discipline: the as-of cut happens before any setup
runs, and ``validate_price_pairs`` rejects a broken series rather
than repairing it. A match is a statement about past closes only —
never a signal, never investment advice.
"""

from __future__ import annotations

import datetime
import json
import math
from dataclasses import dataclass
from pathlib import Path

from .checks import validate_price_pairs
from .terminal import SETUP_ORDER, evaluate_setups

__all__ = [
    "ScanResult",
    "ScreenerCriteria",
    "append_audit_log",
    "parse_criteria",
    "read_audit_log",
    "scan_universe",
]

_SETUP_NAMES = frozenset(SETUP_ORDER)

_CRITERIA_KEYS = frozenset(
    {"as_of", "setups", "min_price", "lookback", "min_volume"})


def _is_int(value):
    """True for a real int (bool is not an int here)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return (isinstance(value, (int, float))
            and not isinstance(value, bool) and math.isfinite(value))


@dataclass(frozen=True)
class ScreenerCriteria:
    """A validated screener criteria spec (see module docstring)."""

    as_of: str
    setups: tuple
    min_price: float | None = None
    lookback: int | None = None
    min_volume: float | None = None

    def to_dict(self):
        """Canonical dict form — the form stored in every audit row."""
        return {
            "as_of": self.as_of,
            "setups": list(self.setups),
            "min_price": self.min_price,
            "lookback": self.lookback,
            "min_volume": self.min_volume,
        }


def parse_criteria(spec):
    """Validate a criteria spec dict and return a ScreenerCriteria.

    Raises ValueError on any invalid spec — unknown keys, unknown or
    duplicated setup names, an empty setup list, a non-positive
    min_price, a negative min_volume, a non-positive lookback, a
    missing or malformed as_of date, or a wrong type anywhere.
    """
    if not isinstance(spec, dict):
        raise ValueError(  # noqa: TRY004
            f"criteria must be a dict, got {type(spec).__name__}")
    unknown = sorted(set(spec) - _CRITERIA_KEYS)
    if unknown:
        raise ValueError(
            f"unknown criteria key(s): {', '.join(unknown)} — "
            f"allowed keys are {', '.join(sorted(_CRITERIA_KEYS))}")
    as_of = spec.get("as_of")
    if not isinstance(as_of, str):
        raise ValueError(  # noqa: TRY004
            "criteria.as_of is required and must be an "
            "ISO date string YYYY-MM-DD")
    try:
        datetime.date.fromisoformat(as_of)
    except ValueError as e:
        raise ValueError(
            f"criteria.as_of must be an ISO date YYYY-MM-DD, "
            f"got {as_of!r}") from e
    setups = spec.get("setups", list(SETUP_ORDER))
    if not isinstance(setups, list) or not setups:
        raise ValueError(
            "criteria.setups must be a non-empty list of setup names")
    for name in setups:
        if not isinstance(name, str) or name not in _SETUP_NAMES:
            raise ValueError(
                f"unknown setup {name!r} — allowed: "
                f"{', '.join(SETUP_ORDER)}")
    if len(set(setups)) != len(setups):
        raise ValueError(
            f"criteria.setups must not repeat a setup: {setups!r}")
    min_price = spec.get("min_price")
    if min_price is not None:
        if not _is_number(min_price) or min_price <= 0:
            raise ValueError(
                f"criteria.min_price must be a positive number, "
                f"got {min_price!r}")
        min_price = float(min_price)
    lookback = spec.get("lookback")
    if lookback is not None and (not _is_int(lookback) or lookback < 1):
        raise ValueError(
            f"criteria.lookback must be a positive integer, "
            f"got {lookback!r}")
    min_volume = spec.get("min_volume")
    if min_volume is not None:
        if not _is_number(min_volume) or min_volume < 0:
            raise ValueError(
                f"criteria.min_volume must be a non-negative number, "
                f"got {min_volume!r}")
        min_volume = float(min_volume)
    return ScreenerCriteria(
        as_of=as_of, setups=tuple(setups), min_price=min_price,
        lookback=lookback, min_volume=min_volume)


@dataclass
class ScanResult:
    """The outcome of one scan: matches, audit log, and per-setup
    firing vs base rates (see the module docstring for the base-rate
    definition)."""

    as_of: str
    criteria: dict
    matches: list
    audit_rows: list
    rates: dict


def _validate_volumes(symbol, price_dates, volume_pairs):
    """Validate one symbol's (date, volume) pairs against its price
    dates. Returns {date: volume}. Loud on any mismatch — volume
    aligned to the wrong bar would silently filter the wrong day."""
    if volume_pairs is None:
        raise ValueError(f"volumes[{symbol!r}] is None")
    try:
        rows = list(volume_pairs)
    except TypeError as e:
        raise ValueError(
            f"volumes[{symbol!r}] must be a list of (date, volume) "
            f"pairs") from e
    volumes = {}
    for i, row in enumerate(rows):
        try:
            date_str, volume = row
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"volumes[{symbol!r}][{i}] must be a (date, volume) "
                f"pair, got {row!r}") from e
        try:
            d = datetime.date.fromisoformat(str(date_str))
        except ValueError as e:
            raise ValueError(
                f"volumes[{symbol!r}][{i}] date must be ISO "
                f"YYYY-MM-DD, got {date_str!r}") from e
        if not _is_number(volume) or volume < 0:
            raise ValueError(
                f"volumes[{symbol!r}][{i}] volume must be a "
                f"non-negative finite number, got {volume!r}")
        if d.isoformat() in volumes:
            raise ValueError(
                f"volumes[{symbol!r}] repeats date {date_str!r}")
        volumes[d.isoformat()] = float(volume)
    if sorted(volumes) != sorted(price_dates):
        raise ValueError(
            f"volumes[{symbol!r}] dates must match the symbol's "
            f"price dates exactly (one volume per price bar)")
    return volumes


def scan_universe(universe, criteria, volumes=None, audit_path=None):
    """Scan a caller-supplied universe with validated criteria.

    ``universe`` maps symbol -> list of (date, close) pairs;
    ``criteria`` is a spec dict or a ``ScreenerCriteria``; ``volumes``
    optionally maps symbol -> list of (date, volume) pairs aligned
    one-to-one with that symbol's price bars. Returns a
    ``ScanResult`` with ``matches`` (one dict per matched setup),
    ``audit_rows`` (one row per (symbol, setup), matched or not —
    including a zero-match scan), and ``rates`` (per setup:
    firing vs base rate, match counts, symbols scanned).

    When ``audit_path`` is given, the scan appends its audit rows to
    that JSONL file itself — every scan, including a zero-match one.

    Raises ValueError on an empty universe, a broken price series, a
    symbol with no bar on or before ``as_of``, a volumes entry for an
    unknown symbol, or volume pairs that do not align with the
    symbol's price bars. No live data is fetched.
    """
    parsed = (criteria if isinstance(criteria, ScreenerCriteria)
              else parse_criteria(criteria))
    if not isinstance(universe, dict) or not universe:
        raise ValueError("universe must be a non-empty dict of "
                         "symbol -> (date, close) pairs")
    volumes = volumes or {}
    if not isinstance(volumes, dict):
        raise ValueError(  # noqa: TRY004
            "volumes must be a dict of symbol -> (date, volume) pairs")
    for symbol in volumes:
        if symbol not in universe:
            raise ValueError(
                f"volumes has an entry for unknown symbol {symbol!r}")
    as_of = datetime.date.fromisoformat(parsed.as_of)
    criteria_dict = parsed.to_dict()
    n_symbols = len(universe)
    base_hits = {name: 0 for name in parsed.setups}
    firing_hits = {name: 0 for name in parsed.setups}
    per_symbol = []
    for symbol in universe:
        if not isinstance(symbol, str) or not symbol:
            raise ValueError(
                f"universe keys must be non-empty strings, got "
                f"{symbol!r}")
        pairs = validate_price_pairs(symbol, universe[symbol])
        # Point-in-time cut first: nothing after as_of is ever seen.
        usable = [(d, c) for d, c in pairs
                  if datetime.date.fromisoformat(d) <= as_of]
        if not usable:
            raise ValueError(
                f"{symbol}: no bar on or before as_of {parsed.as_of}")
        # Base rate: the same symbol, same as-of cut, full bars, no
        # filters and no lookback — the unfiltered firing rate.
        base_results = evaluate_setups([c for _d, c in usable])
        for name in parsed.setups:
            if base_results[name][0]:
                base_hits[name] += 1
        if parsed.lookback is not None:
            usable = usable[-parsed.lookback:]
        closes = [c for _d, c in usable]
        latest_close = closes[-1]
        latest_date = usable[-1][0]
        symbol_volumes = None
        if symbol in volumes:
            symbol_volumes = _validate_volumes(
                symbol, [d for d, _c in pairs], volumes[symbol])
        volume_applied = (parsed.min_volume is not None
                          and symbol_volumes is not None)
        filter_reason = None
        if parsed.min_price is not None \
                and latest_close < parsed.min_price:
            filter_reason = (
                f"latest close {latest_close:.2f} below min_price "
                f"{parsed.min_price:.2f}")
        elif volume_applied and symbol_volumes[latest_date] \
                < parsed.min_volume:
            filter_reason = (
                f"latest volume {symbol_volumes[latest_date]:.0f} "
                f"below min_volume {parsed.min_volume:.0f}")
        if filter_reason is not None:
            results = {name: (False, filter_reason)
                       for name in parsed.setups}
        else:
            results = evaluate_setups(closes)
        for name in parsed.setups:
            if results[name][0]:
                firing_hits[name] += 1
        per_symbol.append((symbol, results, len(closes),
                           volume_applied))
    rates = {}
    for name in parsed.setups:
        rates[name] = {
            "symbols": n_symbols,
            "base_matches": base_hits[name],
            "firing_matches": firing_hits[name],
            "base_rate": base_hits[name] / n_symbols,
            "firing_rate": firing_hits[name] / n_symbols,
        }
    matches = []
    audit_rows = []
    for symbol, results, bars_used, volume_applied in per_symbol:
        for name in parsed.setups:
            matched, detail = results[name]
            if matched:
                matches.append({
                    "symbol": symbol,
                    "setup": name,
                    "as_of": parsed.as_of,
                    "detail": detail,
                })
            audit_rows.append({
                "symbol": symbol,
                "setup": name,
                "matched": matched,
                "as_of": parsed.as_of,
                "criteria": criteria_dict,
                "bars_used": bars_used,
                "volume_applied": volume_applied,
                "firing_rate": rates[name]["firing_rate"],
                "base_rate": rates[name]["base_rate"],
                "detail": detail,
            })
    if audit_path is not None:
        append_audit_log(audit_path, audit_rows)
    return ScanResult(as_of=parsed.as_of, criteria=criteria_dict,
                      matches=matches, audit_rows=audit_rows,
                      rates=rates)


def append_audit_log(path, rows):
    """Append audit rows to a JSONL file (one object per line).

    Creates the file if it does not exist; never rewrites earlier
    rows. Raises ValueError if a row is not a dict.
    """
    path = Path(path)
    lines = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(  # noqa: TRY004
                f"audit rows must be dicts, got {type(row).__name__}")
        lines.append(json.dumps(row, sort_keys=True))
    with path.open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    return len(lines)


def read_audit_log(path):
    """Read a JSONL audit log back into a list of row dicts.

    Raises FileNotFoundError for a missing file and ValueError for a
    malformed line — a corrupt log is never silently skipped.
    """
    rows = []
    with Path(path).open(encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"{path}:{i}: malformed audit-log line: {e}") from e
            if not isinstance(row, dict):
                raise ValueError(  # noqa: TRY004
                    f"{path}:{i}: audit-log line is not an object")
            rows.append(row)
    return rows
