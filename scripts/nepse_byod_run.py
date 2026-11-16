"""One-command NEPSE bring-your-own-data run (Round E, E.3).

Takes a user-supplied NEPSE price CSV and a user-supplied
floorsheet CSV (plus an optional event CSV) and runs the
existing evaluations on them: the forecast harness, the
direction models, a momentum-only cross-sectional ranking and
the event study, plus the floorsheet analytics. The data is
user-supplied only — this script fetches nothing, and it names
no NEPSE data provider, API or download page anywhere. The
protocol is docs/nepse-byod-protocol.md; the kit is
toolkit/nepse_byod.py.

Stdlib only. No network. This script is NOT run by CI or by
scripts/qa.py — it is the reproducibility record for
docs/nepse-byod-kit.md. Run it from the repo root:

  python3 scripts/nepse_byod_run.py --prices <price.csv>
      --floorsheet <floorsheet.csv> --source "<label>"
      [--events <events.csv>] [--trade-value 100000]
      [--out reports/nepse-byod]

Exit 0 on a completed report; exit 2 when the kit refuses the
data (the refusal names the file and the line).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from toolkit.nepse_byod import NepseByodError, run_byod


def _render_md(report):
    """A short Markdown summary of the report dict."""
    lines = ["# NEPSE bring-your-own-data run",
             "",
             f"Source: {report['source']}",
             "",
             ("User-supplied data only — no provider, feed or API "
              "is involved, and none is named here."),
             "",
             "## Symbols",
             "",
             "| Symbol | Bars | First | Last |",
             "| --- | --- | --- | --- |"]
    for symbol, info in sorted(report["per_symbol"].items()):
        lines.append(f"| {symbol} | {info['n_bars']} | "
                     f"{info['first_date']} | {info['last_date']} |")
    harness = report["harness"]
    lines += ["",
              "## Forecast harness",
              "",
              f"Symbols: {', '.join(harness['symbols'])}",
              f"Models tried: {harness['models_tried']}",
              "",
              "| Model | Overall verdict |",
              "| --- | --- |"]
    for model, verdict in harness["overall_price_verdicts"].items():
        lines.append(f"| {model} | {verdict} |")
    lines += ["",
              "## Direction models",
              "",
              f"Symbols: {', '.join(report['direction']['symbols'])}",
              "",
              "## Cross-sectional ranking (momentum-only)",
              "",
              report["ranking"]["note"],
              "",
              "| Rule | Mean net spread | 95% CI | Verdict |",
              "| --- | --- | --- | --- |"]
    for rule, verdict in report["ranking"]["rules"].items():
        lo, hi = verdict["ci_95"]
        lines.append(f"| {rule} | {verdict['mean_spread']:.4f} | "
                     f"[{lo:.4f}, {hi:.4f}] | {verdict['verdict']} |")
    if "event_study" in report:
        study = report["event_study"]
        lines += ["",
                  "## Event study",
                  "",
                  (f"Events: {study['n_events']} "
                   f"(market proxy: {study['market_proxy']})"),
                  (f"CAAR: {study['aggregate']['caar']:.4f}, "
                   f"t = {study['aggregate']['t_stat']:.2f}, "
                   f"bootstrap p = {study['aggregate']['bootstrap_p']:.3f}, "
                   f"significant: {study['aggregate']['significant']}, "
                   f"small-n caveat: {study['aggregate']['small_n_caveat']}")]
    floor = report["floorsheet"]
    lines += ["",
              "## Floorsheet (descriptive only)",
              "",
              f"Rows: {floor['n_rows']}",
              "",
              "| Broker | Net flow (value) | Trades |",
              "| --- | --- | --- |"]
    for broker, flow in sorted(floor["broker_flow"].items()):
        lines.append(f"| {broker} | {flow['net_flow']:.2f} | "
                     f"{flow['n_trades']} |")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", required=True,
                        help="user-supplied NEPSE price CSV")
    parser.add_argument("--floorsheet", required=True,
                        help="user-supplied NEPSE floorsheet CSV")
    parser.add_argument("--source", required=True,
                        help="source label for the supplied data")
    parser.add_argument("--events", default=None,
                        help="optional user-supplied event CSV")
    parser.add_argument("--trade-value", type=float, default=100_000,
                        help="NEPSE trade value per name in NPR "
                        "(default 100000)")
    parser.add_argument("--single-symbol", action="store_true",
                        help="price CSV is the single-symbol form")
    parser.add_argument("--out", default="reports/nepse-byod",
                        help="output directory for the reports")
    args = parser.parse_args(argv)
    try:
        report = run_byod(args.prices, args.floorsheet, args.source,
                          events_path=args.events,
                          trade_value=args.trade_value,
                          single_symbol=args.single_symbol)
    except NepseByodError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "nepse-byod-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")
    (out_dir / "nepse-byod-report.md").write_text(
        _render_md(report), encoding="utf-8")
    print(f"report written to {out_dir}/nepse-byod-report.json "
          f"and {out_dir}/nepse-byod-report.md")
    print(f"symbols: {len(report['per_symbol'])}, "
          f"harness verdicts: "
          f"{report['harness']['overall_price_verdicts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
