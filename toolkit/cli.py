"""Command-line interface: python -m toolkit.cli <command> [args]

Commands (all demo/offline unless a CSV is given):
  portfolio --csv prices.csv   portfolio weights from a CSV price file
  forecast --demo              walk-forward forecast on demo data
  option --S 100 --K 100 --T 1 --r 0.05 --sigma 0.2   Black-Scholes price + Greeks
No network, no key, unless a data function is called explicitly.
"""
import argparse
import sys

from . import data, forecast, options


def main(argv=None):
    p = argparse.ArgumentParser(prog="investment-analyst-toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("forecast-demo")
    pd = sub.add_parser("data")
    pd.add_argument("--symbol", required=True)
    pd.add_argument("--source", default="yahoo", choices=["yahoo", "stooq"])
    pd.add_argument("--max-age-days", type=int, default=7)
    pp = sub.add_parser("predict")
    pp.add_argument("--csv", default=None)
    pa = sub.add_parser("autonomous-once")
    pa.add_argument("--symbols", required=True, help="comma-separated, e.g. AAPL,MSFT")
    pa.add_argument("--output-dir", default="autonomous_reports")
    po = sub.add_parser("option")
    po.add_argument("--S", type=float, required=True)
    po.add_argument("--K", type=float, required=True)
    po.add_argument("--T", type=float, required=True)
    po.add_argument("--r", type=float, required=True)
    po.add_argument("--sigma", type=float, required=True)
    po.add_argument("--kind", default="call")
    args = p.parse_args(argv)
    if args.cmd == "data":
        from . import data as _data
        fn = _data.fetch_yahoo_prices if args.source == "yahoo" else (lambda s: _data.fetch_stooq_prices(s.lower() + ".us"))
        pairs = fn(args.symbol)
        fresh = _data.check_freshness(pairs[-1][0], max_age_days=args.max_age_days)
        print({"symbol": args.symbol, "source": args.source, "n_points": len(pairs), "last_date": pairs[-1][0], "last_close": pairs[-1][1], "freshness": fresh})
    elif args.cmd == "predict":
        from . import data as _data
        from . import predictive as _pred
        series = _data.load_csv_prices(args.csv) if args.csv else _data.demo_series()
        closes = [c for _, c in series] if args.csv else series
        sel = _pred.auto_select_model(closes, train_size=max(10, len(closes)//2))
        print(sel)
    elif args.cmd == "autonomous-once":
        from . import autonomous as _auto
        print(_auto.run_once({"symbols": [s.strip() for s in args.symbols.split(",") if s.strip()], "output_dir": args.output_dir}))
    elif args.cmd == "forecast-demo":
        series = data.demo_series()
        res = forecast.walk_forward_evaluate(series, {"naive": forecast.naive_forecast, "trend": forecast.linear_trend_forecast}, train_size=10)
        print(res)
    elif args.cmd == "option":
        print("price:", options.black_scholes(args.S, args.K, args.T, args.r, args.sigma, args.kind))
        print("greeks:", options.greeks(args.S, args.K, args.T, args.r, args.sigma, args.kind))
    return 0


if __name__ == "__main__":
    sys.exit(main())
