"""Fetch the market-context inputs for the personas team run.

Round 10, PR 10.3. Fetches, once, the market-context series the
five global personas need as of 2026-11-27 and writes
``examples/personas-team-inputs-2026-11-27.json``. Tests read that
recording, never the network.

What is fetched (all public, no key, Yahoo via the programme's
own connector ``toolkit.connectors.fetch_prices_with_volumes``,
kind="stock" for all three — Yahoo serves them all as symbols):
- SPY daily closes (the market-index context for the etf_index
  persona; the S&P 500 is the index a US large-cap is read
  against);
- EURUSD=X daily closes (the dollar context for the fx_macro
  persona; EUR/USD rising means the dollar is weakening);
- BTC-USD daily closes (the risk-regime context for the crypto
  persona; price vs its 200-day mean is the regime read).

Each series is truncated to dates <= as_of and the last bar must
be as_of, exactly as ``scripts/fetch_real_aapl_dossier.py`` does
for AAPL. No metric is computed here: the input builder
(``toolkit.personas_team.build_personas_team_inputs``) derives
the 1y returns, the BTC 200-day mean / all-time high /
30-day realised volatility from these recorded closes, so the
derivation is code, tested, and reproducible from the fixture.

Usage:
  python3 scripts/fetch_personas_team_inputs.py [--output PATH]
Exit 1 without writing anything if a fetch fails.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit.connectors import fetch_prices_with_volumes

ROOT = Path(__file__).resolve().parent.parent
AS_OF = "2026-11-27"
DEFAULT_OUTPUT = ROOT / "examples" / "personas-team-inputs-2026-11-27.json"

# symbol -> role in the team run. The role is documentation only;
# the input builder reads the series by symbol.
SERIES = (
    ("SPY", "market-index context for the etf_index persona"),
    ("EURUSD=X", ("dollar context for the fx_macro persona "
                  "(EUR/USD: rising = dollar weakening)")),
    ("BTC-USD", "risk-regime context for the crypto persona"),
)


def _yahoo_pairs(symbol):
    """Yahoo closes truncated to dates <= AS_OF (last must be AS_OF)."""
    model = fetch_prices_with_volumes(symbol, kind="stock")
    pairs = [[str(date), float(close)]
             for date, close in (model.get("pairs") or [])]
    pairs = [pair for pair in pairs if pair[0] <= AS_OF]
    if not pairs:
        raise RuntimeError(f"no Yahoo closes for {symbol} by {AS_OF}")
    if pairs[-1][0] != AS_OF:
        raise RuntimeError(
            f"last Yahoo close for {symbol} is {pairs[-1][0]}, "
            f"not as_of {AS_OF}")
    return {"symbol": symbol, "source": model.get("source") or "yahoo",
            "pairs": pairs}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=None,
                        help="output JSON path")
    args = parser.parse_args(argv)
    output = Path(args.output) if args.output else DEFAULT_OUTPUT

    series = {}
    for symbol, role in SERIES:
        record = _yahoo_pairs(symbol)
        record["role"] = role
        series[symbol] = record

    fixture = {
        "as_of": AS_OF,
        "fetched_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "series": series,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")

    print(f"personas team market inputs — as_of {AS_OF}")
    for symbol, record in series.items():
        pairs = record["pairs"]
        print(f"  {symbol}: Yahoo {len(pairs)} bars, "
              f"first {pairs[0]}, last {pairs[-1]}")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
