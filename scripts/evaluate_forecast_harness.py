"""Run the pre-registered forecast-harness evaluation (PR 8.1).

Reads the protocol in docs/forecast-harness-eval-protocol.md and
runs it, nothing else: SPY / AAPL / VT daily closes from Yahoo
(range 2y), expanding walk-forward from 252 closes (price) /
252 returns (volatility), 9 tried price models + naive baseline,
EWMA / GARCH-lite + naive volatility baseline re-run through the
PR 7.2 QLIKE rule, plus the purged/embargoed second split check
(purge 5, embargo 5), the probabilistic block (Brier / log score /
10-bin calibration, Platt fitted on the first half of the folds),
and the reports/daily scoring under
docs/forecast-scoring-protocol.md. Every number in
docs/forecast-harness-model-card.md comes from this run — the card
is rendered here, never hand-typed.

Stdlib only. Network is used ONLY for the Yahoo fetch; a fetch
failure aborts the run instead of inventing prices. This script is
NOT run by CI or by scripts/qa.py — it is the reproducibility
record for docs/forecast-harness-model-card.md. Run it from the
repo root:

  python3 scripts/evaluate_forecast_harness.py

It also appends one experiment record to
docs/forecast-harness-experiments.jsonl (the harness recorder:
config + model count + verdicts per run; re-running adds a line,
it never rewrites the past).
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from toolkit import data
from toolkit import forecast_harness as fh

SYMBOLS = ("SPY", "AAPL", "VT")
RANGE = "2y"
TRAIN_SIZE = 252
CARD_PATH = ROOT / "docs" / "forecast-harness-model-card.md"
EXPERIMENTS_PATH = ROOT / "docs" / "forecast-harness-experiments.jsonl"

# Pooled mean QLIKE from docs/volatility-forecast-model-card.md
# (PR 7.2) — the reproduction reference for the volatility re-run
# below. When the Yahoo 2y window is unchanged the re-run should
# reproduce these values; when it has moved, the values move with
# it and the card says so.
VOL_72_POOLED_QLIKE = {"naive": -7.9652, "ewma": -8.0264,
                       "garch_lite": -8.0643}


def _fmt(value, places=4):
    if value is None:
        return "—"
    return f"{value:.{places}f}"


def _fmt_ci(ci):
    if ci is None:
        return "—"
    return f"[{ci[0]:.4f}, {ci[1]:.4f}]"


def _price_table(price_eval):
    lines = [("| Model | MAE | RMSE | MAE diff vs naive | 95% CI on diff | "
              "Direction acc. | Up-day base rate | Verdict |"),
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, m in sorted(price_eval["models"].items(),
                          key=lambda kv: kv[1]["mae"]):
        lines.append(
            f"| {name} | {_fmt(m['mae'])} | {_fmt(m['rmse'])} | "
            f"{_fmt(m['mae_diff_vs_baseline'])} | "
            f"{_fmt_ci(m['ci_95'])} | "
            f"{m['direction_accuracy']:.3f} "
            f"({m['direction_n_correct']}/{m['n']}) | "
            f"{m['up_day_base_rate']:.3f} | {m['verdict']} |")
    return "\n".join(lines)


def _prob_table(price_eval):
    lines = [("| Model | Brier (eval half) | Brier always-0.5 | "
              "Log score (eval half) | Log score always-0.5 |"),
             "| --- | --- | --- | --- | --- |"]
    for name, m in sorted(price_eval["models"].items()):
        prob = m["probabilistic"]
        if "note" in prob:
            lines.append(f"| {name} | {prob['note']} | — | — | — |")
            continue
        lines.append(
            f"| {name} | {_fmt(prob['brier'])} | "
            f"{_fmt(prob['brier_always_half'])} | "
            f"{_fmt(prob['log_score'])} | "
            f"{_fmt(prob['log_score_always_half'])} |")
    return "\n".join(lines)


def render_card(run_date, per_symbol_data, evaluation, purged,
                daily, experiments_path):
    """Render docs/forecast-harness-model-card.md from run values."""
    parts = []
    parts.append("# Forecast-harness model card (PR 8.1)\n")
    parts.append("Research only. Never a buy/sell/hold recommendation. "
                 "A lower past out-of-sample MAE or QLIKE is not a "
                 "guarantee of future accuracy.\n")
    parts.append("## Protocol\n")
    parts.append("Pre-registered in "
                 "`forecast-harness-eval-protocol.md` **before** this "
                 "evaluation ran: SPY / AAPL / VT daily closes from "
                 "Yahoo (range 2y), expanding walk-forward from 252 "
                 "closes (price) / 252 returns (volatility), horizon 1, "
                 "9 tried price models + the naive baseline, EWMA / "
                 "GARCH-lite + the naive volatility baseline re-run "
                 "through the PR 7.2 QLIKE rule, paired bootstrap CIs "
                 "(10,000 resamples, seed 20261114). A price model "
                 "beats the baseline on a symbol only if its "
                 "out-of-sample mean MAE is lower than naive AND the "
                 "95% CI on the mean MAE difference lies entirely "
                 "below zero; overall \"beats baseline\" needs a win "
                 "on all three symbols, otherwise \"no edge\". "
                 "Direction accuracy is reported against 50% and the "
                 "up-day base rate and never decides a verdict. "
                 "Reproduce with:\n")
    parts.append("```text\npython3 scripts/evaluate_forecast_harness.py\n```\n")
    parts.append("## Run\n")
    run_lines = [(f"- **Run date:** {run_date}. **Data:** Yahoo chart "
                  "API via `toolkit.data.fetch_yahoo_prices`, range 2y, "
                  "interval 1d."),
                 (f"- **Models tried:** {evaluation['models_tried']} "
                  "(9 price + 2 volatility) plus 2 baselines."),
                 ("- **Implementation:** `toolkit/forecast_harness.py` "
                  "(stdlib only).")]
    for symbol in SYMBOLS:
        info = per_symbol_data[symbol]
        run_lines.append(
            f"- **{symbol}:** {info['n_closes']} closes, "
            f"{info['first_date']} to {info['last_date']} "
            f"({info['n_returns']} log returns, "
            f"{info['n_folds']} out-of-sample price folds, "
            f"{info['n_vol_folds']} volatility folds).")
    parts.append("\n".join(run_lines) + "\n")
    parts.append("## Results — price models (per symbol)\n")
    parts.append("Pooled verdict rule: overall \"beats baseline\" only "
                 "on a win on all three symbols.\n")
    for symbol in SYMBOLS:
        price = evaluation["per_symbol"][symbol]["price"]
        parts.append(f"### {symbol} (n = {price['n_folds']} folds)\n")
        parts.append(_price_table(price) + "\n")
    parts.append("## Overall price verdicts\n")
    lines = ["| Model | SPY | AAPL | VT | Overall |",
             "| --- | --- | --- | --- | --- |"]
    for name in fh.price_models():
        verdicts = [evaluation["per_symbol"][s]["price"]["models"][name][
            "verdict"] for s in SYMBOLS]
        lines.append(f"| {name} | {' | '.join(verdicts)} | "
                     f"{evaluation['overall_price_verdicts'][name]} |")
    parts.append("\n".join(lines) + "\n")
    parts.append("## Pooled direction accuracy\n")
    lines = ["| Model | Pooled accuracy | 95% CI | Up-day base rate | n |",
             "| --- | --- | --- | --- | --- |"]
    for name, pooled in evaluation["pooled_direction"].items():
        lines.append(f"| {name} | {pooled['accuracy']:.3f} | "
                     f"{_fmt_ci(pooled['ci_95'])} | "
                     f"{pooled['up_day_base_rate']:.3f} | "
                     f"{pooled['n']} |")
    parts.append("\n".join(lines) + "\n")
    parts.append("Direction accuracy is reported against 50% (coin "
                 "flip) and the up-day base rate. It never decides a "
                 "verdict.\n")
    parts.append("## Probabilistic scores (Brier / log score)\n")
    parts.append("Platt scaling fitted on the first half of each "
                 "model's out-of-sample folds, scored on the second "
                 "half only. Lower is better; always forecasting 0.5 "
                 "scores Brier 0.25 and log score ln 2 ≈ 0.6931.\n")
    for symbol in SYMBOLS:
        price = evaluation["per_symbol"][symbol]["price"]
        parts.append(f"### {symbol}\n")
        parts.append(_prob_table(price) + "\n")
    parts.append("## Volatility re-run (PR 7.2 rule, same data window)\n")
    parts.append("QLIKE primary (lower is better), MSE on variance "
                 "secondary. Verdict by the PR 7.2 rule: pooled mean "
                 "QLIKE lower than naive AND the 95% paired-bootstrap "
                 "CI entirely below zero, else \"no edge\". The 7.2 "
                 "card's numbers came from its own fetch window; this "
                 "re-run fetches the 2y window fresh, so the values "
                 "are whatever today's window gives. The rule is the "
                 "same, and any verdict change would be published "
                 "here, not resolved silently.\n")
    for symbol in SYMBOLS:
        vol = evaluation["per_symbol"][symbol]["volatility"]
        scores = vol["scores"]
        lines = [f"### {symbol} (n = {vol['walk_forward']['n_test']} folds)",
                 "| Model | Mean QLIKE | Mean MSE on variance |",
                 "| --- | --- | --- |"]
        for name, s in sorted(scores.items(),
                              key=lambda kv: kv[1]["mean_qlike"]):
            lines.append(f"| {name} | {_fmt(s['mean_qlike'])} | "
                         f"{s['mean_mse_variance']:.3e} |")
        parts.append("\n".join(lines) + "\n")
    comp = evaluation["pooled_volatility"]
    lines = [("Pooled across symbols "
              f"(n = {comp['n_folds']} folds, "
              f"{comp['n_symbols']} symbols):"),
             ("| Model | Mean QLIKE | Diff vs naive | 95% CI on diff | "
              "Mean MSE on variance | Verdict |"),
             "| --- | --- | --- | --- | --- | --- |"]
    for name, m in sorted(comp["models"].items(),
                          key=lambda kv: kv[1]["mean_qlike"]):
        lines.append(f"| {name} | {_fmt(m['mean_qlike'])} | "
                     f"{_fmt(m['mean_qlike_diff_vs_baseline'])} | "
                     f"{_fmt_ci(m['ci_95'])} | "
                     f"{m['mean_mse_variance']:.3e} | {m['verdict']} |")
    parts.append("\n".join(lines) + "\n")
    diffs = {name: m["mean_qlike"] - VOL_72_POOLED_QLIKE[name]
             for name, m in comp["models"].items()}
    if all(abs(d) < 0.00005 for d in diffs.values()):
        parts.append("Reproduction check: the pooled mean QLIKE values "
                     "reproduce the 7.2 card to 4 decimals — the 2y "
                     "window is unchanged from the 7.2 fetch.\n")
    else:
        parts.append("Reproduction check: the pooled mean QLIKE values "
                     "differ from the 7.2 card by "
                     + ", ".join(f"{name} {d:+.4f}"
                                 for name, d in sorted(diffs.items()))
                     + " — the 2y window has moved since the 7.2 "
                     "fetch. The verdicts above are this run's, by "
                     "the same rule.\n")
    parts.append("## Purged / embargoed second split check "
                 "(purge 5, embargo 5)\n")
    parts.append("Same origins, training window drops its last 5 "
                 "observations and the test observation is delayed 5 "
                 "observations after the origin. Verdicts below use "
                 "the same decision rule. If a model's verdict differs "
                 "between the two splits, both tables show it — a win "
                 "on one split alone never changes the primary "
                 "verdict.\n")
    lines = ["| Model | SPY | AAPL | VT | Overall |",
             "| --- | --- | --- | --- | --- |"]
    for name in fh.price_models():
        verdicts = [purged["per_symbol"][s]["price"]["models"][name][
            "verdict"] for s in SYMBOLS]
        overall = ("beats baseline"
                   if all(v == "beats baseline" for v in verdicts)
                   else "no edge")
        if name == "naive":
            overall = "baseline"
        lines.append(f"| {name} | {' | '.join(verdicts)} | {overall} |")
    parts.append("\n".join(lines) + "\n")
    parts.append("## Daily stream scoring "
                 "(docs/forecast-scoring-protocol.md)\n")
    counts = daily["counts"]
    parts.append(f"Forecasts made: **{counts['made']}**, scored: "
                 f"**{counts['scored']}**, pending: "
                 f"**{counts['pending']}**. Claim: "
                 f"**{daily['claim']}** (no claim until "
                 f"{daily['min_scored_for_claim']} scored forecasts; "
                 "the 34 scored forecasts in the file are the "
                 "labelled development period, not a validation "
                 "set).\n")
    pooled = daily["pooled"]
    if pooled["n_scored"]:
        lines = [("| | MAE | RMSE | Baseline MAE | Baseline RMSE | "
                  "MAE diff | 95% CI | Direction acc. | Base rate |"),
                 "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                 (f"| Pooled (n = {pooled['n_scored']}) | "
                  f"{_fmt(pooled['mae'])} | {_fmt(pooled['rmse'])} | "
                  f"{_fmt(pooled['baseline_mae'])} | "
                  f"{_fmt(pooled['baseline_rmse'])} | "
                  f"{_fmt(pooled['mae_diff_vs_baseline'])} | "
                  f"{_fmt_ci(pooled['ci_95'])} | "
                  f"{pooled['direction_accuracy']:.3f} | "
                  f"{pooled['up_day_base_rate']:.3f} |")]
        for symbol, per in sorted(daily["per_symbol"].items()):
            lines.append(
                f"| {symbol} (n = {per['n_scored']}) | "
                f"{_fmt(per['mae'])} | {_fmt(per['rmse'])} | "
                f"{_fmt(per['baseline_mae'])} | "
                f"{_fmt(per['baseline_rmse'])} | "
                f"{_fmt(per['mae_diff_vs_baseline'])} | "
                f"{_fmt_ci(per['ci_95'])} | "
                f"{per['direction_accuracy']:.3f} | "
                f"{per['up_day_base_rate']:.3f} |")
        parts.append("\n".join(lines) + "\n")
    parts.append("## Experiment record\n")
    parts.append("This run is recorded in "
                 f"`{experiments_path.name}` (config + model count + "
                 "verdicts, one append-only line per run).\n")
    return "\n".join(parts)


def main():
    series_by_symbol = {}
    per_symbol_data = {}
    for symbol in SYMBOLS:
        pairs = data.fetch_yahoo_prices(symbol, range_=RANGE, interval="1d")
        closes = [close for _date, close in pairs]
        series_by_symbol[symbol] = closes
        returns = fh.log_returns(closes)
        wf = fh.walk_forward_volatility(returns, train_size=TRAIN_SIZE)
        per_symbol_data[symbol] = {
            "symbol": symbol, "n_closes": len(closes),
            "first_date": pairs[0][0], "last_date": pairs[-1][0],
            "n_returns": len(returns),
            "n_folds": len(closes) - TRAIN_SIZE,
            "n_vol_folds": wf["n_test"],
        }
    evaluation = fh.run_full_evaluation(series_by_symbol,
                                        train_size=TRAIN_SIZE)
    purged = fh.run_full_evaluation(series_by_symbol,
                                    train_size=TRAIN_SIZE,
                                    purge=fh.PURGE, embargo=fh.EMBARGO)
    rows = fh.read_daily_predictions(
        ROOT / "reports" / "daily" / "predictions.jsonl")
    daily = fh.score_daily_predictions(rows)
    run_date = date.today().isoformat()
    card = render_card(run_date, per_symbol_data, evaluation, purged,
                       daily, EXPERIMENTS_PATH)
    CARD_PATH.write_text(card, encoding="utf-8")
    record = fh.record_harness_experiment(
        f"forecast-harness-{run_date}", evaluation, EXPERIMENTS_PATH)
    out = {
        "protocol": "docs/forecast-harness-eval-protocol.md",
        "run_date": run_date,
        "symbols": list(SYMBOLS),
        "range": RANGE,
        "train_size": TRAIN_SIZE,
        "models_tried": evaluation["models_tried"],
        "per_symbol": per_symbol_data,
        "overall_price_verdicts": evaluation["overall_price_verdicts"],
        "pooled_direction": {
            name: {"accuracy": p["accuracy"],
                   "ci_95": list(p["ci_95"]),
                   "up_day_base_rate": p["up_day_base_rate"],
                   "n": p["n"]}
            for name, p in evaluation["pooled_direction"].items()},
        "volatility_pooled": {
            name: {"mean_qlike": m["mean_qlike"],
                   "mean_qlike_diff_vs_baseline":
                       m["mean_qlike_diff_vs_baseline"],
                   "ci_95": list(m["ci_95"]), "verdict": m["verdict"]}
            for name, m in evaluation["pooled_volatility"]["models"].items()},
        "daily": daily,
        "card": str(CARD_PATH),
        "experiment_record": record["name"],
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
