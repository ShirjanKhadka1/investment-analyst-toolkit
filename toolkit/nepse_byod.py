"""NEPSE bring-your-own-data kit (Round E, E.3).

The kit runs the existing evaluations — forecast harness,
direction models, momentum-only cross-sectional ranking, event
study, floorsheet analytics — on CSV files the user supplies.
It fetches nothing, and it names no NEPSE data provider, API
or download page anywhere: the data is "user-supplied", full
stop. The protocol is docs/nepse-byod-protocol.md.

Schemas (exactly the existing loaders' formats):
- Price CSV, multi-symbol:
  ``symbol,sector,date,close,high,low,volume``.
- Price CSV, single-symbol:
  ``date,close,high,low,volume``.
- Floorsheet CSV:
  ``date,broker,symbol,side,quantity,price``.
- Event CSV (optional):
  ``symbol,event_date,event_type[,detail]``.

Importer discipline is LOUD refusal: a missing file, a
missing or misspelled column, a bad date, non-monotonic or
duplicate dates, a missing value, a non-positive close,
``high < low``, a non-finite number, a bad ``side`` or a
non-positive price raises naming the file and the line — never
silently skipped. A source label is required: unsourced data
is refused, not loaded. The kit adds two checks on top of the
loaders: an explicit per-symbol as-supplied date-order
pre-check (with the kit's own error, before the loader runs),
and evaluation-readiness minimums (a symbol needs 260 closes
for the harness/direction runs and 150 for ranking; fewer
means the symbol is excluded from that evaluation, recorded —
and when no symbol qualifies for an evaluation, the run
refuses instead of reporting on nothing).

The floorsheet analytics stay descriptive (who bought/sold
what): a broker buying heavily is a fact about the supplied
rows, not a recommendation. Research only. Never investment
advice.
"""
from __future__ import annotations

import csv
import datetime as _dt
import math
from pathlib import Path

from .cross_sectional import (
    assign_deciles,
    momentum_score,
    net_forward_return,
    rank_universe,
    spread_verdict,
)
from .direction_models import walk_forward_direction
from .event_study import (
    EventStudyError,
    aggregate_event_study,
    event_study,
    load_event_csv,
)
from .forecast_harness import run_full_evaluation
from .nepse_floorsheet import (
    FloorsheetError,
    broker_flow,
    load_floorsheet_csv,
    symbol_flow,
)
from .nepse_loader import (
    NepseLoaderError,
    load_nepse_user_csv,
    load_nepse_user_csv_single,
)

__all__ = [
    "MIN_CLOSES_HARNESS",
    "MIN_CLOSES_RANKING",
    "MIN_SYMBOLS_RANKING",
    "NepseByodError",
    "run_byod",
    "validate_nepse_floorsheet_csv",
    "validate_nepse_price_csv",
]

# Evaluation-readiness minimums (protocol): the harness and
# direction runs use train_size 252, so a symbol needs 260
# closes; ranking needs the 126-bar momentum formation plus a
# 21-bar forward window, so 150 closes; ranking needs at least
# 10 symbols for deciles.
MIN_CLOSES_HARNESS = 260
MIN_CLOSES_RANKING = 150
MIN_SYMBOLS_RANKING = 10
FORWARD_BARS = 21
RANKING_STEP_BARS = 21
MOMENTUM_FORMATION_BARS = 126


class NepseByodError(ValueError):
    """Raised for any BYOD problem: bad CSV, no source, too little data."""


def _require_source(source):
    if not isinstance(source, str) or not source.strip():
        raise NepseByodError(
            "nepse_byod: a source must be named (e.g. 'NEPSE "
            "published data, session of 2026-11-13, transcribed by "
            "hand'). Unsourced data is refused, not loaded.")
    return source.strip()


def _read_data_lines(path):
    """File lines with '#' comment lines removed, like the
    floorsheet and event loaders do. Line numbers in kit errors
    count the data lines only, matching the loaders."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise NepseByodError(f"cannot open {path}: {exc}") from exc
    return [line for line in text.splitlines()
            if not line.startswith("#")]


def _check_date_order(path, single_symbol=False):
    """Refuse non-monotonic or duplicate dates, as supplied.

    Reads the raw rows before the loader runs: per symbol, the
    dates must be strictly increasing in file order. A shuffled
    file is refused with the line number, even when the loader's
    own grouped check would also catch it — this error names
    the file order, not the grouped sort.
    """
    required = {"date"} if single_symbol else {"date", "symbol"}
    lines = _read_data_lines(path)
    reader = csv.DictReader(lines)
    if reader.fieldnames is None or not required.issubset(
            {f.strip() for f in reader.fieldnames}):
        raise NepseByodError(
            f"{path}: expected columns "
            f"{', '.join(sorted(required))}")
    last_seen = {}
    for n, raw in enumerate(reader, start=2):
        row = {(k.strip() if k else k):
               (v.strip() if isinstance(v, str) else v)
               for k, v in raw.items()}
        symbol = row.get("symbol") or ""
        day = row.get("date") or ""
        if not day:
            raise NepseByodError(
                f"{path} line {n}: missing date")
        try:
            _dt.date.fromisoformat(day)
        except ValueError as exc:
            raise NepseByodError(
                f"{path} line {n}: date must be ISO "
                f"YYYY-MM-DD, got {day!r}") from exc
        previous = last_seen.get(symbol)
        if previous is not None and day <= previous:
            raise NepseByodError(
                f"{path} line {n}: dates for symbol "
                f"{symbol or '(single)'} are not strictly "
                f"increasing as supplied ({previous} then "
                f"{day})")
        last_seen[symbol] = day


def _check_no_empty_cells(path, required):
    """Refuse an empty cell in any required column, naming it."""
    lines = _read_data_lines(path)
    reader = csv.DictReader(lines)
    if reader.fieldnames is None:
        raise NepseByodError(f"{path}: no header row")
    for n, raw in enumerate(reader, start=2):
        row = {(k.strip() if k else k):
               (v.strip() if isinstance(v, str) else v)
               for k, v in raw.items()}
        for field in sorted(required):
            if row.get(field) in (None, ""):
                raise NepseByodError(
                    f"{path} line {n}: missing {field}")


def validate_nepse_price_csv(path, source, single_symbol=False):
    """Validate a user-supplied NEPSE price CSV. Returns the
    loader's groups (multi-symbol) or (closes, dates, source)
    (single-symbol).

    Runs the kit's own checks first (empty cells, as-supplied
    date order), then the loader (columns, value rules,
    grouped date order). Any violation raises NepseByodError
    or NepseLoaderError naming the file and the line.
    """
    source = _require_source(source)
    if single_symbol:
        required = {"date", "close", "high", "low", "volume"}
        _check_no_empty_cells(path, required)
        _check_date_order(path, single_symbol=True)
        try:
            return load_nepse_user_csv_single(path, source)
        except NepseLoaderError as exc:
            raise NepseByodError(str(exc)) from exc
    required = {"symbol", "sector", "date", "close", "high", "low",
                "volume"}
    _check_no_empty_cells(path, required)
    _check_date_order(path, single_symbol=False)
    try:
        return load_nepse_user_csv(path, source)
    except NepseLoaderError as exc:
        raise NepseByodError(str(exc)) from exc


def validate_nepse_floorsheet_csv(path, source):
    """Validate a user-supplied NEPSE floorsheet CSV. Returns
    (rows, source) from the loader, after the kit's own
    empty-cell check. Any violation raises NepseByodError or
    FloorsheetError naming the file and the line.
    """
    source = _require_source(source)
    required = {"date", "broker", "symbol", "side", "quantity",
                "price"}
    _check_no_empty_cells(path, required)
    try:
        return load_floorsheet_csv(path, source)
    except FloorsheetError as exc:
        raise NepseByodError(str(exc)) from exc


def _ranking_bars(n_closes):
    """Ranking bar indices: 126, 147, ... while 21 forward bars fit."""
    bars = []
    bar = MOMENTUM_FORMATION_BARS
    while bar + FORWARD_BARS < n_closes:
        bars.append(bar)
        bar += RANKING_STEP_BARS
    return bars


def _run_ranking(groups, trade_value):
    """Momentum-only ranking + 21-day forward decile spreads.

    No fundamentals are involved, so value/quality components
    are absent by design: each symbol ranks on momentum alone.
    Costs use the NEPSE schedule at ``trade_value`` per name.
    Returns the ranking result dict (or a refusal when fewer
    than 10 symbols qualify).
    """
    qualified = {s: g for s, g in groups.items()
                 if len(g["closes"]) >= MIN_CLOSES_RANKING}
    excluded = sorted(set(groups) - set(qualified))
    if len(qualified) < MIN_SYMBOLS_RANKING:
        raise NepseByodError(
            f"ranking needs at least {MIN_SYMBOLS_RANKING} symbols "
            f"with {MIN_CLOSES_RANKING}+ closes, got "
            f"{len(qualified)} (excluded: {excluded})")
    n_bars = min(len(g["closes"]) for g in qualified.values())
    bars = _ranking_bars(n_bars)
    if not bars:
        raise NepseByodError(
            "ranking needs at least one ranking bar with 21 forward "
            "bars after it")
    _, cost_frac, basis = net_forward_return(
        0.0, "nepse", trade_value=trade_value)
    spreads = {"momentum": [], "composite": []}
    baseline = []
    for bar in bars:
        components = {}
        forwards = {}
        for symbol, group in qualified.items():
            closes = group["closes"]
            components[symbol] = {
                "momentum": momentum_score(closes[:bar + 1])}
            forwards[symbol] = (closes[bar + FORWARD_BARS]
                                / closes[bar] - 1.0)
        ranked = rank_universe(components)
        net = {s: r - cost_frac for s, r in forwards.items()}
        for rule in ("momentum", "composite"):
            ranked_rule = assign_deciles(
                {s: ranked[s][rule] for s in ranked
                 if ranked[s][rule] is not None})
            top = [s for s, d in ranked_rule.items() if d == 10]
            bottom = [s for s, d in ranked_rule.items() if d == 1]
            top_mean = math.fsum(net[s] for s in top) / len(top)
            bottom_mean = math.fsum(net[s] for s in bottom) / len(bottom)
            spreads[rule].append(top_mean - bottom_mean)
        baseline.append(math.fsum(net.values()) / len(net))
    return {
        "n_symbols": len(qualified),
        "excluded_symbols": excluded,
        "ranking_bars": bars,
        "forward_bars": FORWARD_BARS,
        "round_trip_cost_fraction": cost_frac,
        "cost_basis": basis,
        "mean_baseline_net_return": math.fsum(baseline) / len(baseline),
        "rules": {rule: spread_verdict(values)
                  for rule, values in spreads.items()},
        "spreads": spreads,
        "note": ("Momentum-only ranking: no fundamentals are "
                 "involved, so value/quality components are absent "
                 "by design."),
    }


def _run_event_study(events, prices_by_symbol):
    """Event study over the supplied events and price series.

    The market model uses the alphabetically-first symbol's
    series as the market proxy when its dates align with the
    event symbol's; otherwise the event is mean-adjusted
    (recorded per event). Returns the aggregate + per-event
    results.
    """
    market_symbol = min(prices_by_symbol)
    market_prices = prices_by_symbol[market_symbol]
    per_event = []
    for event in events:
        symbol = event["symbol"]
        try:
            result = event_study(prices_by_symbol[symbol],
                                 event["event_date"],
                                 market_prices=market_prices)
            model_used = "market"
        except EventStudyError as exc:
            if "dates differ" not in str(exc):
                raise
            result = event_study(prices_by_symbol[symbol],
                                 event["event_date"])
            model_used = "mean_adjusted"
        result["symbol"] = symbol
        result["event_type"] = event["event_type"]
        result["model_used"] = model_used
        per_event.append(result)
    aggregate = aggregate_event_study(per_event)
    return {
        "market_proxy": market_symbol,
        "n_events": len(per_event),
        "aggregate": aggregate,
        "per_event": [
            {"symbol": e["symbol"], "event_type": e["event_type"],
             "event_date": e["event_date"], "model": e["model"],
             "model_used": e["model_used"], "car": e["car"],
             "t_stat": e["t_stat"], "bootstrap_p": e["bootstrap_p"],
             "significant": e["significant"]}
            for e in per_event],
    }


def run_byod(prices_path, floorsheet_path, source, events_path=None,
             trade_value=100_000, single_symbol=False):
    """Run the whole kit on user-supplied CSVs. Returns the report dict.

    Validates everything first (any violation raises before any
    evaluation runs), then runs the harness, the direction
    models, the momentum-only ranking, the event study when
    events are supplied, and the floorsheet analytics. Symbols
    too short for an evaluation are excluded from that
    evaluation and recorded; an evaluation with no qualifying
    symbol refuses instead of reporting on nothing.
    """
    source = _require_source(source)
    loaded = validate_nepse_price_csv(prices_path, source,
                                      single_symbol=single_symbol)
    if single_symbol:
        closes, dates, _src = loaded
        groups = {"SINGLE": {"sector": "", "closes": closes,
                             "dates": dates}}
    else:
        groups, _src = loaded
    floor_rows, _floor_src = validate_nepse_floorsheet_csv(
        floorsheet_path, source)
    events = None
    if events_path is not None:
        events, _events_src = load_event_csv(events_path, source)
    prices_by_symbol = {
        symbol: list(zip(group["dates"], group["closes"]))
        for symbol, group in groups.items()}
    per_symbol = {
        symbol: {"n_bars": len(group["closes"]),
                 "first_date": group["dates"][0],
                 "last_date": group["dates"][-1],
                 "sector": group["sector"]}
        for symbol, group in groups.items()}
    report = {
        "source": source,
        "prices_file": str(prices_path),
        "floorsheet_file": str(floorsheet_path),
        "events_file": str(events_path) if events_path else None,
        "per_symbol": per_symbol,
        "floorsheet": {
            "n_rows": len(floor_rows),
            "broker_flow": broker_flow(floor_rows),
            "symbols": {
                symbol: symbol_flow(floor_rows, symbol)
                for symbol in sorted({r["symbol"]
                                     for r in floor_rows})},
        },
    }
    # Harness + direction: symbols with 260+ closes only.
    long_enough = {s: g["closes"] for s, g in groups.items()
                   if len(g["closes"]) >= MIN_CLOSES_HARNESS}
    excluded_harness = sorted(set(groups) - set(long_enough))
    if not long_enough:
        raise NepseByodError(
            f"harness/direction need at least one symbol with "
            f"{MIN_CLOSES_HARNESS}+ closes, got none (excluded: "
            f"{excluded_harness})")
    harness = run_full_evaluation(long_enough, train_size=252,
                                  purge=5, embargo=5)
    report["harness"] = {
        "symbols": harness["symbols"],
        "excluded_symbols": excluded_harness,
        "models_tried": harness["models_tried"],
        "overall_price_verdicts": harness["overall_price_verdicts"],
        "pooled_direction": {
            name: {"accuracy": v["accuracy"],
                   "ci_95": list(v["ci_95"]), "n": v["n"]}
            for name, v in harness["pooled_direction"].items()},
        "pooled_volatility": {
            "n_folds": harness["pooled_volatility"]["n_folds"],
            "models": {
                model: {"mean_qlike": m["mean_qlike"],
                        "verdict": m["verdict"]}
                for model, m in
                harness["pooled_volatility"]["models"].items()}},
    }
    direction = {}
    for symbol, closes in long_enough.items():
        wf = walk_forward_direction(closes, symbol, train_size=252)
        direction[symbol] = {"n_test": wf["n_test"]}
    report["direction"] = {
        "symbols": sorted(direction),
        "excluded_symbols": excluded_harness,
        "per_symbol": direction,
    }
    report["ranking"] = _run_ranking(groups, trade_value)
    if events is not None:
        missing = [e["symbol"] for e in events
                   if e["symbol"] not in prices_by_symbol]
        if missing:
            raise NepseByodError(
                f"events name symbols with no price series: {missing}")
        report["event_study"] = _run_event_study(
            events, prices_by_symbol)
    return report
