"""Track-record scoring: Wilson intervals, baselines, rendering.

Scoring rules (see ``docs/watchlist-proposal.md``):

- One settled call contributes one hit/miss to the model, one to the
  naive baseline and one to the buy-and-hold baseline, all on the
  SAME settled calls.
- **Naive baseline:** predicts the direction of the previous
  5-trading-day window continues (sign of the return over the 5
  trading days before the call date; a zero previous return predicts
  UP). A settled call carries its naive outcome in ``naive_hit``.
- **Buy-and-hold baseline:** always predicts UP. A settled call
  carries it in ``bnh_hit``.
- A zero realised return is a MISS for the model and for both
  baselines: the predicted direction did not happen.
- Every rate is reported with **n** and a **95% Wilson score
  interval** (z = 1.96), per ticker and pooled.
- **No headline hit rate until 100 settled calls** (pooled n >= 100).
  Below that the page shows n and the intervals, but no "hit rate
  X%" headline figure.
- **Tickers are correlated**, so pooled intervals UNDERSTATE
  uncertainty: seven tickers moving together are not seven
  independent trials. The pooled interval is reported with that
  caveat printed next to it, never as if the calls were
  independent.
- **Excluded calls are never scored.** Call_ids listed in
  ``docs/track-record-exclusions.json`` stay in the log and are
  never deleted, but ``score_settled`` skips them — even if a
  settlement row exists for one — and ``toolkit/decision_log.py``
  refuses to settle them. See ``load_excluded_call_ids``.

Original code for this toolkit. Research only.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

__all__ = [
    "HEADLINE_MIN_N",
    "headline_allowed",
    "load_excluded_call_ids",
    "render_track_record_md",
    "score_settled",
    "wilson_interval",
]

HEADLINE_MIN_N = 100
_Z = 1.96

# The exclusions file lives at the repo root, next to this package:
# ``toolkit/`` is one level below the root, so the docs dir is the
# parent of this file's parent.
DEFAULT_EXCLUSIONS_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs" / "track-record-exclusions.json"
)


def load_excluded_call_ids(exclusions_path=None):
    """Call_ids excluded from scoring and settlement.

    Reads ``docs/track-record-exclusions.json`` (or
    ``exclusions_path`` when given) and returns the excluded
    ``call_id`` values as a frozenset. A missing file means no
    exclusions — checkouts predating the file still score. A
    present but malformed file raises: silently ignoring it
    would silently score excluded calls.
    """
    path = (Path(exclusions_path) if exclusions_path is not None
            else DEFAULT_EXCLUSIONS_PATH)
    if not path.exists():
        return frozenset()
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("excluded", [])
    if not isinstance(entries, list):
        # Toolkit convention is ValueError for all input validation.
        raise ValueError(  # noqa: TRY004
            f"{path}: 'excluded' must be a list")
    ids = set()
    for entry in entries:
        call_id = entry.get("call_id") if isinstance(entry, dict) else None
        if not isinstance(call_id, str) or not call_id:
            raise ValueError(f"{path}: every excluded entry needs a "
                             f"string call_id, got {entry!r}")
        ids.add(call_id)
    return frozenset(ids)


def wilson_interval(hits, n, z=_Z):
    """95% Wilson score interval on a hit rate.

    Returns ``(low, high)`` as fractions, or ``(None, None)`` when
    n == 0. Formula is the one in ``docs/watchlist-proposal.md``.
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError(f"n must be a non-negative integer, got {n!r}")
    if isinstance(hits, bool) or not isinstance(hits, int) \
            or hits < 0 or hits > n:
        raise ValueError(f"hits must be an integer in [0, n], got {hits!r}")
    if n == 0:
        return None, None
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _rate(calls, field):
    """``(n, hits, (low, high))`` for one outcome field, or n=0."""
    scored = [c for c in calls if c.get(field) is not None]
    n = len(scored)
    hits = sum(1 for c in scored if c.get(field))
    return n, hits, wilson_interval(hits, n)


def _entry(calls, field):
    n, hits, (low, high) = _rate(calls, field)
    return {"n": n, "hits": hits, "hit_rate": (hits / n if n else None),
            "wilson_low": low, "wilson_high": high}


def score_settled(calls, excluded_call_ids=None):
    """Per-ticker and pooled scoring for settled calls.

    ``calls`` are settled call records (as written by
    ``toolkit/decision_log.py`` plus the baseline fields the daily
    script adds at settlement: ``naive_hit`` and ``bnh_hit``).
    Returns ``{"pooled": {...}, "per_ticker": {symbol: {...}}}``,
    each entry carrying model / naive / buy-and-hold ``_entry``
    dicts. Baseline n may be smaller than model n when a settled
    call has no baseline fields (legacy) — the two ns are reported
    separately, never silently equalised.

    Excluded calls (``docs/track-record-exclusions.json``) are
    skipped: they contribute nothing to any rate, even if a
    settlement row exists for one. ``excluded_call_ids`` defaults
    to loading that file; pass a set explicitly to score against
    a different exclusion set.
    """
    if excluded_call_ids is None:
        excluded_call_ids = load_excluded_call_ids()
    excluded = set(excluded_call_ids)
    settled = [c for c in calls
               if c.get("status") == "settled"
               and c.get("call_id") not in excluded]
    pooled = {"model": _entry(settled, "hit"),
              "naive": _entry(settled, "naive_hit"),
              "buy_and_hold": _entry(settled, "bnh_hit")}
    per_ticker = {}
    for symbol in sorted({c["symbol"] for c in settled}):
        mine = [c for c in settled if c["symbol"] == symbol]
        per_ticker[symbol] = {"model": _entry(mine, "hit"),
                              "naive": _entry(mine, "naive_hit"),
                              "buy_and_hold": _entry(mine, "bnh_hit")}
    return {"pooled": pooled, "per_ticker": per_ticker}


def headline_allowed(n, threshold=HEADLINE_MIN_N):
    """True when a headline hit rate may be shown at ``n`` settled."""
    return n >= threshold


def _fmt_rate(entry):
    """``"n=12, 7 hits, 95% Wilson [31.5%, 80.4%]"`` or the n=0 text.

    The point-estimate percentage is deliberately NOT printed here:
    only a headline (see ``render_track_record_md``) ever shows it.
    Everywhere else the rate is n + interval only.
    """
    if entry["n"] == 0:
        return "n=0 — no settled calls"
    low = entry["wilson_low"] * 100
    high = entry["wilson_high"] * 100
    return (f"n={entry['n']}, {entry['hits']} hits, "
            f"95% Wilson [{low:.1f}%, {high:.1f}%]")


def render_track_record_md(calls, headline_min_n=HEADLINE_MIN_N,
                           excluded_call_ids=None):
    """Render the public track-record page from settled calls.

    The pooled headline hit rate appears only at
    ``headline_min_n`` settled calls; below that the page shows n
    and the Wilson intervals, with the headline explicitly withheld.
    The correlation caveat is printed with the pooled numbers.
    Excluded calls are skipped by ``score_settled`` (see above);
    pass ``excluded_call_ids`` through to score against a different
    exclusion set.
    """
    scores = score_settled(calls, excluded_call_ids=excluded_call_ids)
    pooled = scores["pooled"]["model"]
    lines = [
        "# Track record — public calls, scored after the fact",
        "",
        ("> Research only, not investment advice. Every call below "
         "was logged before its outcome was known and settled after "
         "its 5-trading-day horizon. Losses stay in the record."),
        "",
        f"- Calls settled (pooled): **{pooled['n']}**",
    ]
    if headline_allowed(pooled["n"], headline_min_n):
        lines.append(f"- Pooled hit rate (headline): "
                     f"**{pooled['hit_rate'] * 100:.1f}%** "
                     f"(95% Wilson interval "
                     f"[{pooled['wilson_low'] * 100:.1f}%, "
                     f"{pooled['wilson_high'] * 100:.1f}%], "
                     f"n={pooled['n']})")
    elif pooled["n"]:
        lines.append(
            f"- Pooled hit rate: **withheld until {headline_min_n} "
            f"settled calls** (currently n={pooled['n']}). "
            f"Wilson interval: "
            f"[{pooled['wilson_low'] * 100:.1f}%, "
            f"{pooled['wilson_high'] * 100:.1f}%].")
    else:
        lines.append(
            f"- Pooled hit rate: **withheld until {headline_min_n} "
            f"settled calls** (currently n=0).")
    lines += [
        ("- Pooled naive baseline: " + _fmt_rate(scores["pooled"]["naive"])),
        ("- Pooled buy-and-hold baseline: "
         + _fmt_rate(scores["pooled"]["buy_and_hold"])),
        "",
        ("**Tickers are correlated, so the pooled interval understates "
         "uncertainty:** seven tickers moving together are not seven "
         "independent trials. Read the pooled interval as a lower bound "
         "on the uncertainty, not as if the calls were independent."),
        "",
        "## Per ticker",
        "",
        "| Ticker | Model | Naive | Buy-and-hold |",
        "|---|---|---|---|",
    ]
    for symbol, entry in scores["per_ticker"].items():
        lines.append(f"| {symbol} | {_fmt_rate(entry['model'])} | "
                     f"{_fmt_rate(entry['naive'])} | "
                     f"{_fmt_rate(entry['buy_and_hold'])} |")
    lines += [
        "",
        ("Per-ticker rates are reported with n and Wilson intervals; "
         "a point-estimate percentage appears only in the pooled "
         "headline, once the 100-call minimum is met."),
        "",
        ("Scored from the decision log on the data branch "
         "(`data/track-record`) — never edited by hand."),
        "",
    ]
    return "\n".join(lines)
