"""Run the pre-registered real-data evaluation (Round E, E.1).

Reads the protocol in docs/real-data-eval-protocol.md and runs it,
nothing else: the forecast harness, the direction models, the
cross-sectional ranking and the event study on one bounded REAL
universe — 30 liquid US large caps plus 4 ETFs, Yahoo 5y daily
closes (harness and direction use the trailing 2y slice), EDGAR
point-in-time fundamentals for the ranking (no look-ahead), and
10-Q filing dates as the earnings events.

Survivorship bias, stated here and in every card: the universe is
chosen from today's liquid names, so past losers that delisted
are absent and every result here is survivorship-conditioned.

Stdlib only. Network is used ONLY for the Yahoo/EDGAR fetch under
``--refresh``; the default run reads the local cache file
``data/real-data-eval-2026-11-15.json`` instead of the network,
so the run is reproducible offline. That raw cache is LOCAL-ONLY
(gitignored, never committed — data policy is derived metrics
only); reproduce it with ``scripts/fetch_real_data_eval.py``.
The committed record is
``data/real-data-eval-2026-11-15/derived-metrics.json`` (this
script's JSON output for the 2026-11-15 run) plus
``data/real-data-eval-2026-11-15/manifest.json`` (source data
with its SHA-256). The EDGAR fetch needs a
declared User-Agent with a contact email (SEC fair-access rule):
set ``EDGAR_CONTACT_EMAIL`` — the script refuses to fetch EDGAR
without it, and the address is never written to the repo. This
script is NOT run by CI or by scripts/qa.py — it is the
reproducibility record for the four new 2026-11-15 model cards.
Run it from the repo root:

  EDGAR_CONTACT_EMAIL=<contact> python3 scripts/evaluate_real_data.py --refresh
  python3 scripts/evaluate_real_data.py
"""
import json
import math
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from toolkit import cross_sectional as xs
from toolkit import data, edgar
from toolkit import direction_models as dm
from toolkit import event_study as es
from toolkit import forecast_harness as fh

CAPS = ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "AVGO", "META",
        "TSLA", "LLY", "JPM", "V", "XOM", "JNJ", "MA", "COST", "HD",
        "PG", "BAC", "UNH", "CVX", "ORCL", "CRM", "NFLX", "ADBE",
        "AMD", "CAT", "GS", "WMT", "KO", "DIS")
ETFS = ("SPY", "VT", "QQQ", "DIA")
UNIVERSE = CAPS + ETFS
CACHE_PATH = ROOT / "data" / "real-data-eval-2026-11-15.json"
RANGE = "5y"
TRAIN_SIZE = 252
PURGE = 5
EMBARGO = 5
FORWARD_BARS = 21
GLOBAL_COST_FRACTION = 0.001
RANKING_MONTHS = [(2024, 12), (2025, 1), (2025, 2), (2025, 3),
                  (2025, 4), (2025, 5), (2025, 6), (2025, 7),
                  (2025, 8), (2025, 9), (2025, 10), (2025, 11)]
EVENT_YEAR = 2025
MAX_EVENTS = 10
TWO_YEARS_DAYS = 730

# XBRL tag candidates per economic concept (see
# toolkit.edgar.freshest_annual_value_as_of): filers do not use
# one revenue or equity tag, so the freshest candidate wins.
# A single tag would silently return a 15-year-stale value for
# some filers (Microsoft's "Revenues" ends 2010-12-31), and
# Goldman Sachs files no top-line revenue tag at all — its
# revenue is "RevenuesNetOfInterestExpense" (seen 2026-11-15).
CONCEPT_CANDIDATES = {
    "NetIncomeLoss": [("us-gaap", "NetIncomeLoss")],
    "StockholdersEquity": [
        ("us-gaap", "StockholdersEquity"),
        ("us-gaap",
         ("StockholdersEquityIncludingPortionAttributableTo"
          "NoncontrollingInterest"))],
    "Revenues": [
        ("us-gaap", "Revenues"),
        ("us-gaap", "SalesRevenueNet"),
        ("us-gaap",
         "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "RevenuesNetOfInterestExpense")],
    "GrossProfit": [("us-gaap", "GrossProfit")],
    "Assets": [("us-gaap", "Assets")],
}
# The SEC's own company_tickers.json maps XOM to CIK 2115436
# ("ExxonMobil Holdings Corp"), whose submissions endpoint 404s;
# the real filer, EXXON MOBIL CORP (CIK 34088), has an empty
# ticker list and is absent from that table entirely (checked
# 2026-11-15). The override is the only correction; every other
# ticker uses the table.
CIK_OVERRIDES = {"XOM": 34088}
SHARES_CANDIDATES = [("dei", "EntityCommonStockSharesOutstanding")]


def _log(message):
    print(message, file=sys.stderr, flush=True)


def _ranking_dates(spy_pairs):
    """Last trading day of each protocol month, from SPY's dates."""
    by_month = {}
    for day, _close in spy_pairs:
        year, month = int(day[:4]), int(day[5:7])
        by_month[(year, month)] = day
    dates = []
    for key in RANKING_MONTHS:
        if key not in by_month:
            raise RuntimeError(
                f"no SPY trading day in {key[0]}-{key[1]:02d} — "
                "cannot fix the ranking date")
        dates.append(by_month[key])
    return dates


def _close_on_or_before(pairs, day):
    """Last (date, close) with date <= ``day``; None when absent."""
    best = None
    for pair in pairs:
        if pair[0] <= day:
            best = pair
        else:
            break
    return best


def _fetch_cache(user_agent):
    """Fetch Yahoo prices + EDGAR point-in-time data. Returns the cache."""
    prices = {}
    for symbol in UNIVERSE:
        pairs = data.fetch_yahoo_prices(symbol, range_=RANGE,
                                        interval="1d")
        prices[symbol] = [[d, c] for d, c in pairs]
        _log(f"yahoo {symbol}: {len(pairs)} closes "
             f"{pairs[0][0]}..{pairs[-1][0]}")
        time.sleep(0.5)
    ranking_dates = _ranking_dates(prices["SPY"])
    _log(f"ranking dates: {ranking_dates[0]}..{ranking_dates[-1]}")
    pit = {}
    events = []
    for symbol in CAPS:
        cik = CIK_OVERRIDES.get(symbol) or edgar.ticker_to_cik(
            symbol, user_agent=user_agent)
        facts = edgar.fetch_company_facts(cik, user_agent=user_agent)
        _log(f"edgar {symbol}: CIK {cik}, facts fetched")
        per_date = {}
        for ranking_date in ranking_dates:
            entry = {}
            for _taxonomy, tag in xs.PROXY_CONCEPTS:
                try:
                    value, tax, used = edgar.freshest_annual_value_as_of(
                        facts, CONCEPT_CANDIDATES[tag], ranking_date)
                    entry[tag] = {**value, "tag": f"{tax}:{used}"}
                except ValueError as exc:
                    entry[tag] = {"missing": str(exc)}
            try:
                value, tax, used = edgar.freshest_annual_value_as_of(
                    facts, SHARES_CANDIDATES, ranking_date,
                    prefer_10k=False)
                entry["CommonStockSharesOutstanding"] = {
                    **value, "tag": f"{tax}:{used}"}
            except ValueError as exc:
                entry["CommonStockSharesOutstanding"] = {
                    "missing": str(exc)}
            close_pair = _close_on_or_before(prices[symbol],
                                             ranking_date)
            shares = entry["CommonStockSharesOutstanding"]
            if "value" in shares and close_pair is not None:
                entry["market_cap"] = (float(shares["value"])
                                       * close_pair[1])
            else:
                entry["market_cap"] = None
            per_date[ranking_date] = entry
        pit[symbol] = {"cik": cik, "per_ranking_date": per_date}
        submissions = edgar.fetch_submissions(cik,
                                              user_agent=user_agent)
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        filings_2025 = sorted({
            d for form, d in zip(forms, filing_dates)
            if form == "10-Q" and d.startswith(str(EVENT_YEAR))})
        events.append({"symbol": symbol,
                       "filings_10q_2025": filings_2025})
        _log(f"edgar {symbol}: {len(filings_2025)} 10-Q filings "
             f"in {EVENT_YEAR}")
    return {
        "protocol": "docs/real-data-eval-protocol.md",
        "run_date": date.today().isoformat(),
        "universe": {"caps": list(CAPS), "etfs": list(ETFS)},
        "range": RANGE,
        "ranking_dates": ranking_dates,
        "prices": prices,
        "pit_fundamentals": pit,
        "events_10q": events,
        "sources": {
            "prices": "Yahoo Finance chart API via "
                      "toolkit.data.fetch_yahoo_prices",
            "fundamentals": "SEC EDGAR companyfacts via "
                            "toolkit.edgar (point-in-time, filed "
                            "<= ranking date)",
            "events": "SEC EDGAR submissions (10-Q filing dates)",
        },
    }


def _two_year_closes(pairs):
    """Trailing 2y slice of a 5y [(date, close)] series."""
    last = pairs[-1][0]
    cutoff = (date.fromisoformat(last)
              - timedelta(days=TWO_YEARS_DAYS)).isoformat()
    closes = [c for d, c in pairs if d >= cutoff]
    return closes


def _evaluate_harness(cache, symbols=None):
    symbols = list(symbols or UNIVERSE)
    _log(f"harness: running run_full_evaluation on "
         f"{len(symbols)} symbols (2y)...")
    series = {symbol: _two_year_closes(cache["prices"][symbol])
              for symbol in symbols}
    result = fh.run_full_evaluation(series, train_size=TRAIN_SIZE,
                                    purge=PURGE, embargo=EMBARGO)
    out = {
        "symbols": result["symbols"],
        "n_closes_2y": {s: len(series[s]) for s in symbols},
        "models_tried": result["models_tried"],
        "overall_price_verdicts": result["overall_price_verdicts"],
        "pooled_direction": {
            name: {"accuracy": v["accuracy"],
                   "ci_95": list(v["ci_95"]), "n": v["n"],
                   "up_day_base_rate": v["up_day_base_rate"]}
            for name, v in result["pooled_direction"].items()},
        "pooled_volatility": {
            "n_folds": result["pooled_volatility"]["n_folds"],
            "n_symbols": result["pooled_volatility"]["n_symbols"],
            "models": {
                model: {"mean_qlike": m["mean_qlike"],
                        "mean_qlike_diff_vs_baseline":
                            m["mean_qlike_diff_vs_baseline"],
                        "ci_95": list(m["ci_95"]),
                        "mean_mse_variance": m["mean_mse_variance"],
                        "verdict": m["verdict"]}
                for model, m in
                result["pooled_volatility"]["models"].items()}},
        "per_symbol": {
            symbol: {
                "n_folds": per["price"]["n_folds"],
                "price_verdicts": {
                    name: m["verdict"]
                    for name, m in per["price"]["models"].items()},
                "volatility": {
                    model: {"mean_qlike": s["mean_qlike"],
                            "mean_mse_variance":
                                s["mean_mse_variance"]}
                    for model, s in
                    per["volatility"]["scores"].items()},
            }
            for symbol, per in result["per_symbol"].items()},
    }
    return out


def _evaluate_direction(cache, symbols=None):
    symbols = list(symbols or UNIVERSE)
    _log(f"direction: walk-forward on {len(symbols)} symbols (2y)...")
    results = []
    per_symbol = []
    for symbol in symbols:
        closes = _two_year_closes(cache["prices"][symbol])
        wf = dm.walk_forward_direction(closes, symbol,
                                       train_size=TRAIN_SIZE)
        results.append(wf)
        outcomes = [f["outcome"] for f in wf["folds"]]
        per_symbol.append({
            "symbol": symbol,
            "n_closes": len(closes),
            "n_test": wf["n_test"],
            "up_rate": (sum(outcomes) / len(outcomes)
                        if outcomes else None),
            "mean_brier": {
                m: dm.brier_score([f["probs"][m] for f in wf["folds"]],
                                  outcomes)
                for m in dm.EVALUATED_MODELS},
            "mean_brier_baselines": {
                b: dm.brier_score([f["baselines"][b]
                                   for f in wf["folds"]], outcomes)
                for b in dm.BASELINES},
        })
        _log(f"direction {symbol}: {wf['n_test']} folds")
    comparison = dm.compare_direction_models(results)
    pooled_probs = {m: [] for m in dm.EVALUATED_MODELS}
    pooled_outcomes = []
    for wf in results:
        for fold in wf["folds"]:
            pooled_outcomes.append(fold["outcome"])
            for m in dm.EVALUATED_MODELS:
                pooled_probs[m].append(fold["probs"][m])
    reliability = {
        m: [{"bin": [b["bin"][0], b["bin"][1]], "count": b["count"],
             "mean_probability": b["mean_probability"],
             "observed_up_rate": b["observed_up_rate"]}
            for b in dm.reliability_bins(pooled_probs[m],
                                         pooled_outcomes)]
        for m in dm.EVALUATED_MODELS}
    return {
        "symbols": list(symbols),
        "train_size": TRAIN_SIZE,
        "models_tried": 3,
        "evaluated_entries": list(dm.EVALUATED_MODELS),
        "per_symbol": per_symbol,
        "pooled": {
            "n_folds": comparison["n_folds"],
            "n_symbols": comparison["n_symbols"],
            "models": {
                model: {
                    "mean_brier": m["mean_brier"],
                    "mean_brier_raw": m["mean_brier_raw"],
                    "mean_log_score": m["mean_log_score"],
                    "accuracy_at_0.5": m["accuracy_at_0.5"],
                    "verdict": m["verdict"],
                    "per_baseline": {
                        b: {"baseline_brier": pb["baseline_brier"],
                            "mean_brier_diff": pb["mean_brier_diff"],
                            "ci_95": list(pb["ci_95"]),
                            "beats_baseline": pb["beats_baseline"]}
                        for b, pb in m["per_baseline"].items()},
                }
                for model, m in comparison["models"].items()},
            "regime_conditioning": {
                "mean_brier_diff_vs_plain_logistic":
                    comparison["regime_conditioning"]
                    ["mean_brier_diff_vs_plain_logistic"],
                "ci_95": list(comparison["regime_conditioning"]
                              ["ci_95"]),
                "verdict": comparison["regime_conditioning"]
                           ["verdict"]},
        },
        "reliability_bins": reliability,
    }


def _proxies_for(cache, symbol, ranking_date):
    """Point-in-time proxies for one symbol at one ranking date.

    A concept missing as of the date leaves that component
    absent (recorded in ``missing_concepts``) — no value is ever
    invented or defaulted. Returns (proxies dict, missing list).
    """
    entry = cache["pit_fundamentals"][symbol]["per_ranking_date"][
        ranking_date]
    missing = [tag for tag, value in entry.items()
               if isinstance(value, dict) and "missing" in value]
    market_cap = entry.get("market_cap")
    if market_cap is None:
        return None, missing + ["market_cap"]
    facts = {tag: entry[tag] for _, tag in xs.PROXY_CONCEPTS
             if isinstance(entry.get(tag), dict)
             and "value" in entry[tag]}
    if not facts:
        return None, missing
    proxies = {}
    values = {tag: float(facts[tag]["value"]) for tag in facts}
    if "NetIncomeLoss" in values and "StockholdersEquity" in values:
        proxies["earnings_yield"] = xs.earnings_yield(
            values["NetIncomeLoss"], market_cap)
        proxies["book_to_price"] = xs.book_to_price(
            values["StockholdersEquity"], market_cap)
        proxies["roe"] = xs.return_on_equity(
            values["NetIncomeLoss"], values["StockholdersEquity"])
    if "GrossProfit" in values and "Revenues" in values:
        proxies["gross_margin"] = xs.gross_margin(
            values["GrossProfit"], values["Revenues"])
    return proxies, missing


def _evaluate_ranking(cache, symbols=None):
    symbols = list(symbols or CAPS)
    _log(f"ranking: 12 monthly rebalances on {len(symbols)} caps...")
    prices = cache["prices"]
    ranking_dates = cache["ranking_dates"]
    _, cost_frac, basis = xs.net_forward_return(
        0.0, "generic", cost_fraction=GLOBAL_COST_FRACTION)
    rules = ("momentum", "value", "quality", "composite")
    spreads = {rule: [] for rule in rules}
    baseline = []
    missing_report = {}
    for ranking_date in ranking_dates:
        components = {}
        forwards = {}
        for symbol in symbols:
            pairs = prices[symbol]
            idx = None
            for i, (day, _close) in enumerate(pairs):
                if day <= ranking_date:
                    idx = i
                else:
                    break
            if idx is None or idx + FORWARD_BARS >= len(pairs):
                raise RuntimeError(
                    f"{symbol}: no ranking/forward bar around "
                    f"{ranking_date}")
            closes = [c for _d, c in pairs[:idx + 1]]
            proxies, missing = _proxies_for(cache, symbol,
                                            ranking_date)
            if missing:
                missing_report.setdefault(symbol, []).append(
                    {"ranking_date": ranking_date,
                     "missing": missing})
            comp = {"momentum": xs.momentum_score(closes)}
            if proxies:
                comp.update(proxies)
            components[symbol] = comp
            gross = pairs[idx + FORWARD_BARS][1] / pairs[idx][1] - 1.0
            forwards[symbol] = gross
        ranked = xs.rank_universe(components)
        net = {s: r - cost_frac for s, r in forwards.items()}
        for rule in rules:
            ranked_rule = xs.assign_deciles(
                {s: ranked[s][rule] for s in ranked
                 if ranked[s][rule] is not None})
            top = [s for s, d in ranked_rule.items() if d == 10]
            bottom = [s for s, d in ranked_rule.items() if d == 1]
            top_mean = math.fsum(net[s] for s in top) / len(top)
            bottom_mean = math.fsum(net[s] for s in bottom) / len(bottom)
            spreads[rule].append(top_mean - bottom_mean)
        baseline.append(math.fsum(net.values()) / len(net))
    return {
        "ranking_dates": ranking_dates,
        "forward_bars": FORWARD_BARS,
        "round_trip_cost_fraction": cost_frac,
        "cost_basis": basis,
        "mean_baseline_net_return": math.fsum(baseline) / len(baseline),
        "rules": {rule: xs.spread_verdict(values)
                  for rule, values in spreads.items()},
        "spreads": spreads,
        "missing_concepts": missing_report,
    }


def _evaluate_event_study(cache):
    _log("event study: 10-Q filing-date earnings events...")
    prices = cache["prices"]
    chosen = []
    for entry in cache["events_10q"]:
        symbol = entry["symbol"]
        pairs = prices[symbol]
        dates = [d for d, _ in pairs]
        for filing_date in reversed(entry["filings_10q_2025"]):
            if filing_date not in dates:
                continue
            idx = dates.index(filing_date)
            if idx >= 280:
                chosen.append({"symbol": symbol,
                               "event_date": filing_date,
                               "event_type": "earnings",
                               "detail": "10-Q filing date (SEC "
                                         "EDGAR submissions)"})
                break
        if len(chosen) >= MAX_EVENTS:
            break
    chosen.sort(key=lambda e: (e["symbol"], e["event_date"]))
    per_event = []
    for event in chosen:
        symbol = event["symbol"]
        try:
            result = es.event_study(prices[symbol],
                                    event["event_date"],
                                    market_prices=prices["SPY"])
            model_used = "market"
        except es.EventStudyError as exc:
            if "dates differ" not in str(exc):
                raise
            result = es.event_study(prices[symbol],
                                    event["event_date"])
            model_used = "mean_adjusted"
        result["symbol"] = symbol
        result["event_type"] = event["event_type"]
        result["model_used"] = model_used
        per_event.append(result)
    aggregate = es.aggregate_event_study(
        [{k: r[k] for k in ("car", "t_stat", "bootstrap_p",
                           "significant")} for r in per_event])
    return {
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


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true",
                        help="fetch Yahoo + EDGAR and rewrite the cache")
    parser.add_argument("--cache", default=str(CACHE_PATH),
                        help="cache file path")
    parser.add_argument("--only", default="",
                        choices=["", "harness", "direction", "ranking",
                                 "events"],
                        help="run one evaluation only")
    args = parser.parse_args(argv)
    cache_path = Path(args.cache)
    if args.refresh:
        contact = os.environ.get("EDGAR_CONTACT_EMAIL", "")
        if not contact:
            print("EDGAR_CONTACT_EMAIL is not set: EDGAR needs a "
                  "declared User-Agent with a contact email (SEC "
                  "fair-access rule). Set it and re-run with "
                  "--refresh.", file=sys.stderr)
            return 2
        user_agent = edgar.declared_user_agent(contact)
        cache = _fetch_cache(user_agent)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2,
                                         sort_keys=True) + "\n",
                              encoding="utf-8")
        _log(f"cache written: {cache_path}")
    elif not cache_path.exists():
        print(f"cache not found: {cache_path} — run with --refresh "
              "first (needs EDGAR_CONTACT_EMAIL).", file=sys.stderr)
        return 2
    else:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    out = {
        "protocol": "docs/real-data-eval-protocol.md",
        "run_date": date.today().isoformat(),
        "cache": str(cache_path),
        "cache_run_date": cache.get("run_date"),
        "universe": cache["universe"],
        "survivorship_bias": (
            "Universe chosen from today's liquid names; past losers "
            "that delisted are absent, so every result here is "
            "survivorship-conditioned, not unbiased history."),
    }
    if args.only in ("", "harness"):
        out["harness"] = _evaluate_harness(cache)
    if args.only in ("", "direction"):
        out["direction"] = _evaluate_direction(cache)
    if args.only in ("", "ranking"):
        out["ranking"] = _evaluate_ranking(cache)
    if args.only in ("", "events"):
        out["event_study"] = _evaluate_event_study(cache)
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
