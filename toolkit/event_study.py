"""Event studies around corporate actions and earnings.

Stdlib only. Two halves, both on caller-supplied data only:

- NEPSE corporate actions (dividend, bonus, right, book closure)
  from a user-supplied CSV, loaded by ``load_event_csv`` with the
  same loud-error discipline as ``toolkit/nepse_loader.py``: a bad
  row raises, never silently skipped. NEPSE data is CSV-only by
  design — no live feed is claimed or used.
- Global earnings events: caller-supplied dates plus price series,
  same method.

Method (pre-registered in
``docs/cross-sectional-eval-protocol.md``): market-model OLS
abnormal returns over an estimation window ending 30 trading days
before the event, event window [-5, +5], cumulative abnormal
return (CAR) per event, cumulative average abnormal return (CAAR)
across events, significance by t-test AND a seeded bootstrap
p-value — significant only if BOTH |t| > 1.96 AND p < 0.05. The
mean-adjusted fallback (no market series supplied) is labelled as
such in the result. Every aggregated result carries the small-n
caveat: with few events the t-test's normality assumption is thin
and the bootstrap is the more honest of the two — neither is a
proof.

Research only. Never investment advice. A past abnormal return
around an event is not a promise about any future event.
"""
from __future__ import annotations

import csv
import datetime
import math
import random

from .checks import validate_price_pairs

__all__ = [
    "ALLOWED_EVENT_TYPES",
    "BOOTSTRAP_RESAMPLES",
    "BOOTSTRAP_SEED",
    "EventStudyError",
    "aggregate_event_study",
    "event_study",
    "load_event_csv",
    "run_event_study",
]

ALLOWED_EVENT_TYPES = ("dividend", "bonus", "right", "book_closure",
                       "earnings")
# Protocol-fixed defaults (docs/cross-sectional-eval-protocol.md):
# estimation [-250, -31], event [-5, +5], 2000 resamples, seed 20261114.
ESTIMATION_WINDOW = (-250, -31)
EVENT_WINDOW = (-5, 5)
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261114
_MIN_ESTIMATION_BARS = 30


class EventStudyError(ValueError):
    """Raised for any event-study problem: bad CSV row, bad window,
    event date not a trading day in the series."""


# ---------------------------------------------------------------------------
# Event CSV loader (NEPSE corporate actions + global earnings)
# ---------------------------------------------------------------------------

def load_event_csv(path, source):
    """Load a user-supplied event CSV.

    Required columns: ``symbol,event_date,event_type``; optional
    ``detail``. ``event_type`` must be one of dividend, bonus,
    right, book_closure or earnings; ``event_date`` must be ISO
    ``YYYY-MM-DD``. ``#`` comment lines are skipped (the bundled
    demo file carries a SYNTHETIC header comment). Returns
    ``(events, source)`` where events is a list of
    ``{"symbol", "event_date", "event_type", "detail"}``. A missing
    file, a missing column, a bad date or an unknown event type
    raises ``EventStudyError`` naming the line — never silently
    skipped. A source must be named, as in ``nepse_loader``:
    unsourced events are refused, not loaded.
    """
    if not isinstance(source, str) or not source.strip():
        raise EventStudyError(
            "event_study: a source must be named (e.g. 'NEPSE "
            "corporate-action notices, transcribed by hand'). "
            "Unsourced events are refused, not loaded.")
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
    except OSError as exc:
        raise EventStudyError(f"cannot open {path}: {exc}") from exc
    reader = csv.DictReader(lines)
    required = {"symbol", "event_date", "event_type"}
    if reader.fieldnames is None or not required.issubset(
            {f.strip() for f in reader.fieldnames}):
        raise EventStudyError(
            f"{path}: expected columns "
            f"{', '.join(sorted(required))}")
    events = []
    for n, raw in enumerate(reader, start=2):
        row = {(k.strip() if k else k):
               (v.strip() if isinstance(v, str) else v)
               for k, v in raw.items()}
        symbol = row.get("symbol")
        event_date = row.get("event_date")
        event_type = row.get("event_type")
        if not symbol or not event_date or not event_type:
            raise EventStudyError(
                f"{path} line {n}: missing symbol/event_date/event_type")
        try:
            parsed = datetime.date.fromisoformat(event_date)
        except ValueError as exc:
            raise EventStudyError(
                f"{path} line {n}: event_date must be ISO YYYY-MM-DD, "
                f"got {event_date!r}") from exc
        if event_type not in ALLOWED_EVENT_TYPES:
            raise EventStudyError(
                f"{path} line {n}: unknown event_type {event_type!r} "
                f"(allowed: {', '.join(ALLOWED_EVENT_TYPES)})")
        events.append({
            "symbol": symbol,
            "event_date": parsed.isoformat(),
            "event_type": event_type,
            "detail": row.get("detail", "") or "",
        })
    if not events:
        raise EventStudyError(f"{path}: no data rows")
    return events, source.strip()


# ---------------------------------------------------------------------------
# Event study
# ---------------------------------------------------------------------------

def _simple_returns(closes):
    return [closes[i + 1] / closes[i] - 1.0
            for i in range(len(closes) - 1)]


def _ols(xs, ys):
    """OLS y = alpha + beta * x. Returns (alpha, beta)."""
    n = len(xs)
    mean_x = math.fsum(xs) / n
    mean_y = math.fsum(ys) / n
    var_x = math.fsum((x - mean_x) ** 2 for x in xs)
    if var_x == 0:
        raise EventStudyError(
            "market returns are constant over the estimation window — "
            "no market-model beta can be estimated")
    cov = math.fsum((x - mean_x) * (y - mean_y)
                    for x, y in zip(xs, ys))
    beta = cov / var_x
    return mean_y - beta * mean_x, beta


def _sample_sd(values):
    n = len(values)
    if n < 2:
        raise EventStudyError(
            "need at least 2 estimation abnormal returns for a "
            "standard deviation")
    mean = math.fsum(values) / n
    return math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (n - 1))


def _bootstrap_p(estimation_ars, observed_car, window_len,
                 resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED):
    """Two-sided seeded-bootstrap p-value for a CAR.

    Resamples the estimation-window abnormal returns with
    replacement, sums ``window_len`` draws per resample, and reports
    the fraction of resampled |CAR| at least as large as the
    observed |CAR|. Deterministic for a given seed.
    """
    if not isinstance(resamples, int) or isinstance(resamples, bool) \
            or resamples < 100:
        raise ValueError(f"need at least 100 resamples, got {resamples}")
    rng = random.Random(seed)
    n = len(estimation_ars)
    target = abs(observed_car)
    extreme = 0
    for _ in range(resamples):
        total = math.fsum(estimation_ars[rng.randrange(n)]
                          for _ in range(window_len))
        if abs(total) >= target:
            extreme += 1
    return extreme / resamples


def event_study(prices, event_date, market_prices=None,
                estimation_window=ESTIMATION_WINDOW,
                event_window=EVENT_WINDOW,
                bootstrap_resamples=BOOTSTRAP_RESAMPLES,
                seed=BOOTSTRAP_SEED):
    """One event: abnormal returns around ``event_date``.

    ``prices`` and ``market_prices`` (when given) are [(ISO date,
    close)] pairs, validated like every other price input in this
    repo (strictly increasing unique dates, closes > 0). The market
    series must share the symbol series' dates exactly. Windows are
    offsets in trading days relative to the event day's return:
    the estimation window (default [-250, -31], 220 bars) estimates
    the normal-return model; the event window (default [-5, +5],
    11 bars) is where abnormal returns are measured. The event date
    must be a trading day in the series — a non-trading-day event
    raises instead of silently shifting to a nearby day.

    Returns ``{"event_date", "model", "alpha", "beta", "n_estimation",
    "abnormal_returns", "car", "t_stat", "bootstrap_p",
    "significant"}``. ``significant`` is True only when BOTH
    |t| > 1.96 AND bootstrap p < 0.05. With ``market_prices`` the
    model is the market model (OLS); without it, mean-adjusted
    returns (estimation-window mean as the normal return, alpha =
    that mean, beta = 0.0) and the result says so.
    """
    pairs = validate_price_pairs("prices", prices)
    dates = [d for d, _ in pairs]
    closes = [c for _, c in pairs]
    if event_date not in dates:
        raise EventStudyError(
            f"event date {event_date} is not a trading day in the "
            "price series — event studies never shift an event to a "
            "nearby day silently")
    est_start, est_end = estimation_window
    evt_start, evt_end = event_window
    if not (isinstance(est_start, int) and isinstance(est_end, int)
            and est_start < est_end):
        raise EventStudyError(
            f"estimation_window must be (start, end) ints with "
            f"start < end, got {estimation_window!r}")
    if not (isinstance(evt_start, int) and isinstance(evt_end, int)
            and evt_start <= evt_end):
        raise EventStudyError(
            f"event_window must be (start, end) ints with start <= "
            f"end, got {event_window!r}")
    if est_end - est_start + 1 < _MIN_ESTIMATION_BARS:
        raise EventStudyError(
            f"estimation window has {est_end - est_start + 1} bars, "
            f"need at least {_MIN_ESTIMATION_BARS}")
    if est_end >= -1:
        raise EventStudyError(
            "estimation window must end before the event "
            f"(end offset {est_end} reaches the event window)")
    event_idx = dates.index(event_date)
    # Return index of the event day's return: closes[e] / closes[e-1].
    event_ret = event_idx - 1
    est_lo = event_ret + est_start
    est_hi = event_ret + est_end
    evt_lo = event_ret + evt_start
    evt_hi = event_ret + evt_end
    if est_lo < 0 or evt_hi > len(closes) - 2:
        raise EventStudyError(
            "price series too short for these windows around "
            f"{event_date}: need estimation returns from index "
            f"{est_lo} and event returns to index {evt_hi}, series "
            f"has {len(closes) - 1} returns")
    symbol_returns = _simple_returns(closes)
    if market_prices is not None:
        market_pairs = validate_price_pairs("market_prices", market_prices)
        if [d for d, _ in market_pairs] != dates:
            raise EventStudyError(
                "market_prices dates differ from prices dates — the "
                "market model needs aligned series")
        market_returns = _simple_returns([c for _, c in market_pairs])
        est_x = market_returns[est_lo:est_hi + 1]
        est_y = symbol_returns[est_lo:est_hi + 1]
        alpha, beta = _ols(est_x, est_y)
        model = "market"
        est_ars = [y - (alpha + beta * x)
                   for x, y in zip(est_x, est_y)]
        evt_ars = [symbol_returns[i] - (alpha + beta * market_returns[i])
                   for i in range(evt_lo, evt_hi + 1)]
    else:
        model = "mean_adjusted"
        est_y = symbol_returns[est_lo:est_hi + 1]
        alpha = math.fsum(est_y) / len(est_y)
        beta = 0.0
        est_ars = [y - alpha for y in est_y]
        evt_ars = [symbol_returns[i] - alpha
                   for i in range(evt_lo, evt_hi + 1)]
    window_len = evt_hi - evt_lo + 1
    car = math.fsum(evt_ars)
    sd = _sample_sd(est_ars)
    t_stat = car / (sd * math.sqrt(window_len)) if sd > 0 else 0.0
    p_value = _bootstrap_p(est_ars, car, window_len,
                           resamples=bootstrap_resamples, seed=seed)
    significant = abs(t_stat) > 1.96 and p_value < 0.05
    return {
        "event_date": event_date,
        "model": model,
        "alpha": alpha,
        "beta": beta,
        "n_estimation": len(est_ars),
        "abnormal_returns": evt_ars,
        "car": car,
        "t_stat": t_stat,
        "bootstrap_p": p_value,
        "significant": significant,
    }


def aggregate_event_study(results, bootstrap_resamples=BOOTSTRAP_RESAMPLES,
                          seed=BOOTSTRAP_SEED):
    """Aggregate per-event results into a CAAR verdict.

    ``results`` is a list of ``event_study`` result dicts. Returns
    ``{"caar", "t_stat", "bootstrap_p", "n_events", "significant",
    "small_n_caveat"}``. The t-statistic is the mean CAR over its
    standard error across events; the bootstrap p resamples the
    per-event CARs centred on zero first (imposing the null of no
    average effect — resampling uncentred CARs would return p ~= 0.5
    by construction). Significant only when BOTH |t| > 1.96 AND
    p < 0.05. ``small_n_caveat`` is always True in the returned dict
    when n_events < 30 — with few events the t-test's normality
    assumption is thin and the bootstrap is the more honest of the
    two; the flag is part of the result so no caller can quote the
    CAAR without it.
    """
    if not isinstance(results, list) or not results:
        raise EventStudyError("need at least one per-event result")
    cars = [r["car"] for r in results]
    n = len(cars)
    caar = math.fsum(cars) / n
    if n >= 2:
        sd = _sample_sd(cars)
        t_stat = caar / (sd / math.sqrt(n)) if sd > 0 else 0.0
    else:
        t_stat = 0.0
    rng = random.Random(seed)
    if n >= 2:
        # Null bootstrap: centre the CARs on zero FIRST (imposing the
        # null of no average effect), then resample. Resampling the
        # uncentred CARs would cluster the resampled means around the
        # observed CAAR and return p ~= 0.5 by construction — a test
        # of nothing. (The single-event _bootstrap_p needs no such
        # step: OLS estimation residuals already have mean zero.)
        centred = [c - caar for c in cars]
        extreme = 0
        for _ in range(bootstrap_resamples):
            mean = math.fsum(centred[rng.randrange(n)]
                             for _ in range(n)) / n
            if abs(mean) >= abs(caar):
                extreme += 1
        p_value = extreme / bootstrap_resamples
    else:
        p_value = 1.0
    significant = abs(t_stat) > 1.96 and p_value < 0.05
    return {
        "caar": caar,
        "t_stat": t_stat,
        "bootstrap_p": p_value,
        "n_events": n,
        "significant": significant,
        "small_n_caveat": n < 30,
    }


def run_event_study(events, prices_by_symbol, market_prices=None,
                    **kwargs):
    """Run ``event_study`` for each event in ``events`` and aggregate.

    ``events`` is a list of ``load_event_csv`` dicts; ``prices_by_symbol``
    maps symbol -> [(date, close)] pairs; ``market_prices`` is the
    shared market series (or None for mean-adjusted). Extra keyword
    arguments pass through to ``event_study`` (windows, resamples,
    seed). Returns ``{"events": [per-event results with symbol and
    event_type attached], "aggregate": aggregate_event_study(...)}``.
    An event whose symbol has no price series raises — a missing
    series is never skipped silently.
    """
    if not isinstance(events, list) or not events:
        raise EventStudyError("need at least one event")
    per_event = []
    for event in events:
        symbol = event["symbol"]
        if symbol not in prices_by_symbol:
            raise EventStudyError(
                f"no price series supplied for symbol {symbol!r}")
        result = event_study(
            prices_by_symbol[symbol], event["event_date"],
            market_prices=market_prices, **kwargs)
        result["symbol"] = symbol
        result["event_type"] = event["event_type"]
        per_event.append(result)
    aggregate_kwargs = {k: kwargs[k] for k in ("bootstrap_resamples", "seed")
                        if k in kwargs}
    return {"events": per_event,
            "aggregate": aggregate_event_study(per_event,
                                               **aggregate_kwargs)}
