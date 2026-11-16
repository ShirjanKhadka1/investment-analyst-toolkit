"""Generate the synthetic real-data evaluation fixture (Round E close-out).

Replaces tests/fixtures/real_data_eval_slice.json, which held real
Yahoo closes and real EDGAR point-in-time fundamentals. This script
produces a fixture of EQUAL STRUCTURE with ALL values synthetic:

- prices: geometric Brownian motion per symbol, seeded, strictly
  positive; starting levels are round numbers, not real closes.
- fundamentals: synthetic CIKs (9100001-9100012, not real filers),
  synthetic accession numbers (the synthetic CIK zero-padded to
  10 digits, e.g. 0009100001-24-000001), synthetic tag values; the structural quirks are preserved
  (GOOGL/META carry no shares outstanding, so market_cap is None on
  every ranking date; banks and a few others carry no GrossProfit).
- market_cap is computed from the synthetic closes and synthetic
  shares, exactly as scripts/evaluate_real_data.py computes it
  (close on or before the ranking date x shares outstanding), so
  the fixture is internally consistent.
- events_10q keeps the same two symbols (AAPL, MSFT) and the same
  filing-date shape; the dates are calendar dates, not market data.

No network: the only inputs are the seed, the trading calendar
(weekdays minus the US market-holiday list below) and the
per-symbol parameter table. Deterministic: the same seed always
produces the same file.

Run: python3 scripts/generate_synthetic_real_data_fixture.py
Writes: tests/fixtures/real_data_eval_slice.json
"""

import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 20261116
RUN_DATE = "2026-11-16"  # generation date; fixed so output is deterministic
PROTOCOL = "docs/real-data-eval-protocol.md"
OUTPUT = (Path(__file__).resolve().parent.parent / "tests" / "fixtures"
          / "real_data_eval_slice.json")

UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM",
            "BAC", "XOM", "CVX", "V", "MA", "SPY"]
# pit_fundamentals covers every universe symbol except SPY (an ETF).
CAPS = [s for s in UNIVERSE if s != "SPY"]

RANKING_DATES = ["2024-12-31", "2025-01-31", "2025-02-28",
                 "2025-03-31", "2025-04-30", "2025-05-30",
                 "2025-06-30", "2025-07-31", "2025-08-29",
                 "2025-09-30", "2025-10-31", "2025-11-28"]

# US market holidays in the price window (weekdays with no bar).
# These are calendar dates, not market data.
HOLIDAYS = {
    "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28",
    "2024-12-25", "2025-01-01", "2025-01-09", "2025-01-20",
    "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19",
    "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
}

# 10-Q filing dates for the event study (same two symbols, same
# shape as the real slice; the dates are calendar dates).
EVENTS_10Q = [
    {"symbol": "AAPL",
     "filings_10q_2025": ["2025-01-31", "2025-05-02", "2025-08-01"]},
    {"symbol": "MSFT",
     "filings_10q_2025": ["2025-01-29", "2025-04-30", "2025-10-29"]},
]

# Per-symbol parameters. start: synthetic GBM starting close (a round
# number, not a real close). drift/vol: annualised GBM parameters.
# fundamentals: base filing-1 values in USD (Assets, Revenues,
# NetIncomeLoss, StockholdersEquity) and shares outstanding.
# gross_profit: True when the symbol files a GrossProfit concept;
# the real slice has it only for AAPL/AMZN/MSFT/NVDA.
# shares: None means no shares outstanding (GOOGL/META).
PARAMS = {
    "AAPL": {"start": 200.0, "drift": 0.08, "vol": 0.25, "assets": 350_000_000_000, "revenues": 390_000_000_000, "net_income": 95_000_000_000, "equity": 60_000_000_000, "shares": 15_000_000_000, "gross_profit": True},
    "MSFT": {"start": 410.0, "drift": 0.09, "vol": 0.24, "assets": 500_000_000_000, "revenues": 240_000_000_000, "net_income": 85_000_000_000, "equity": 280_000_000_000, "shares": 7_400_000_000, "gross_profit": True},
    "NVDA": {"start": 115.0, "drift": 0.15, "vol": 0.45, "assets": 80_000_000_000, "revenues": 110_000_000_000, "net_income": 55_000_000_000, "equity": 60_000_000_000, "shares": 24_500_000_000, "gross_profit": True},
    "AMZN": {"start": 180.0, "drift": 0.10, "vol": 0.30, "assets": 600_000_000_000, "revenues": 600_000_000_000, "net_income": 55_000_000_000, "equity": 280_000_000_000, "shares": 10_500_000_000, "gross_profit": True},
    "GOOGL": {"start": 170.0, "drift": 0.09, "vol": 0.28, "assets": 400_000_000_000, "revenues": 350_000_000_000, "net_income": 100_000_000_000, "equity": 290_000_000_000, "shares": None, "gross_profit": False},
    "META": {"start": 480.0, "drift": 0.08, "vol": 0.32, "assets": 250_000_000_000, "revenues": 160_000_000_000, "net_income": 60_000_000_000, "equity": 160_000_000_000, "shares": None, "gross_profit": False},
    "JPM": {"start": 200.0, "drift": 0.07, "vol": 0.22, "assets": 4_000_000_000_000, "revenues": 170_000_000_000, "net_income": 50_000_000_000, "equity": 300_000_000_000, "shares": 2_900_000_000, "gross_profit": False},
    "BAC": {"start": 40.0, "drift": 0.06, "vol": 0.25, "assets": 3_200_000_000_000, "revenues": 100_000_000_000, "net_income": 27_000_000_000, "equity": 270_000_000_000, "shares": 7_500_000_000, "gross_profit": False},
    "XOM": {"start": 115.0, "drift": 0.05, "vol": 0.28, "assets": 380_000_000_000, "revenues": 340_000_000_000, "net_income": 35_000_000_000, "equity": 200_000_000_000, "shares": 4_300_000_000, "gross_profit": False},
    "CVX": {"start": 160.0, "drift": 0.05, "vol": 0.26, "assets": 240_000_000_000, "revenues": 190_000_000_000, "net_income": 20_000_000_000, "equity": 140_000_000_000, "shares": 1_900_000_000, "gross_profit": False},
    "V": {"start": 270.0, "drift": 0.08, "vol": 0.24, "assets": 90_000_000_000, "revenues": 35_000_000_000, "net_income": 18_000_000_000, "equity": 40_000_000_000, "shares": 2_000_000_000, "gross_profit": False},
    "MA": {"start": 440.0, "drift": 0.08, "vol": 0.26, "assets": 40_000_000_000, "revenues": 28_000_000_000, "net_income": 12_000_000_000, "equity": 7_000_000_000, "shares": 900_000_000, "gross_profit": False},
    "SPY": {"start": 530.0, "drift": 0.07, "vol": 0.16, "assets": None, "revenues": None, "net_income": None, "equity": None, "shares": None, "gross_profit": False},
}

# Synthetic CIKs: 9100001-9100012, one per cap, in universe order.
# None of these is a real filer's CIK.
CIKS = {symbol: 9_100_000 + i + 1
        for i, symbol in enumerate(CAPS)}

# XBRL tag strings carried as metadata (the eval script keys on the
# dict key, not on this field).
TAGS = {
    "Assets": "us-gaap:Assets",
    "Revenues": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "NetIncomeLoss": "us-gaap:NetIncomeLoss",
    "GrossProfit": "us-gaap:GrossProfit",
    "StockholdersEquity": "us-gaap:StockholdersEquity",
    "CommonStockSharesOutstanding":
        "dei:EntityCommonStockSharesOutstanding",
}
# XOM files its top line under the plain Revenues tag, as in the
# real slice.
XOM_REVENUE_TAG = "us-gaap:Revenues"


def trading_days():
    """Weekdays from 2024-06-03 to 2026-11-13, minus HOLIDAYS."""
    days = []
    cur = date(2024, 6, 3)
    end = date(2026, 11, 13)
    while cur <= end:
        if cur.weekday() < 5 and cur.isoformat() not in HOLIDAYS:
            days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def generate_prices(rng, days):
    """Seeded GBM closes per symbol; strictly positive."""
    prices = {}
    for symbol in UNIVERSE:
        p = PARAMS[symbol]
        mu = p["drift"] / 252.0
        sigma = p["vol"] / math.sqrt(252.0)
        close = p["start"]
        rows = []
        for day in days:
            z = rng.gauss(0.0, 1.0)
            close *= math.exp(mu - 0.5 * sigma * sigma + sigma * z)
            rows.append([day, round(close, 4)])
        prices[symbol] = rows
    return prices


def _fact(accn, end, filed, form, fy, tag, value):
    return {"accn": accn, "end": end, "filed": filed, "form": form,
            "fy": fy, "tag": tag, "value": value}


def _missing(tag, ranking_date):
    return {"missing": f"none of {tag} has a value filed by "
                       f"{ranking_date}"}


def _close_on_or_before(pairs, day):
    best = None
    for pair in pairs:
        if pair[0] <= day:
            best = pair
        else:
            break
    return best


def generate_fundamentals(rng, prices):
    """Synthetic point-in-time fundamentals per cap.

    Two filings per symbol (fy 2024, then fy 2025); the second
    filing's filed date is seeded per symbol, and ranking dates
    from the first date on or after it use the second filing.
    Filing-2 values are filing-1 values grown by a seeded factor.
    """
    pit = {}
    for symbol in CAPS:
        p = PARAMS[symbol]
        cik = CIKS[symbol]
        # Filing 1: filed 2024-11-01, fiscal year-end 2024-09-28.
        f1 = {"filed": "2024-11-01", "end": "2024-09-28", "fy": 2024,
              "accn": f"{cik:010d}-24-000001"}
        # Filing 2: filed date seeded per symbol; values grown.
        f2_filed = rng.choice(["2025-02-28", "2025-05-15",
                               "2025-07-30", "2025-08-29",
                               "2025-10-15"])
        f2_end = (date.fromisoformat(f2_filed)
                  - timedelta(days=90)).isoformat()
        f2 = {"filed": f2_filed, "end": f2_end,
              "fy": date.fromisoformat(f2_end).year,
              "accn": f"{cik:010d}-25-000002"}
        # Seeded growth per tag for filing 2.
        growth = {tag: 1.0 + rng.uniform(-0.05, 0.15)
                  for tag in ("Assets", "Revenues", "NetIncomeLoss",
                              "GrossProfit", "StockholdersEquity",
                              "CommonStockSharesOutstanding")}
        shares_growth = 1.0 + rng.uniform(-0.02, 0.01)
        base = {
            "Assets": p["assets"],
            "Revenues": p["revenues"],
            "NetIncomeLoss": p["net_income"],
            "GrossProfit": (int(p["revenues"] * 0.45)
                            if p["gross_profit"] else None),
            "StockholdersEquity": p["equity"],
            "CommonStockSharesOutstanding": p["shares"],
        }
        per_date = {}
        for ranking_date in RANKING_DATES:
            filing = f2 if ranking_date >= f2_filed else f1
            g = growth if filing is f2 else None
            entry = {}
            for tag in ("Assets", "Revenues", "NetIncomeLoss",
                        "GrossProfit", "StockholdersEquity",
                        "CommonStockSharesOutstanding"):
                if tag == "GrossProfit" and not p["gross_profit"]:
                    entry[tag] = _missing(TAGS[tag], ranking_date)
                    continue
                if (tag == "CommonStockSharesOutstanding"
                        and p["shares"] is None):
                    entry[tag] = _missing(TAGS[tag], ranking_date)
                    continue
                value = base[tag]
                if g is not None:
                    factor = (shares_growth if tag
                              == "CommonStockSharesOutstanding"
                              else g[tag])
                    value = round(value * factor)
                tag_str = (XOM_REVENUE_TAG if symbol == "XOM"
                           and tag == "Revenues" else TAGS[tag])
                entry[tag] = _fact(filing["accn"], filing["end"],
                                   filing["filed"], "10-K",
                                   filing["fy"], tag_str, value)
            # market_cap: synthetic close x synthetic shares,
            # None when the symbol carries no shares outstanding.
            if p["shares"] is None:
                entry["market_cap"] = None
            else:
                shares = entry["CommonStockSharesOutstanding"]["value"]
                close_pair = _close_on_or_before(prices[symbol],
                                                 ranking_date)
                entry["market_cap"] = float(shares) * close_pair[1]
            per_date[ranking_date] = entry
        pit[symbol] = {"cik": cik, "per_ranking_date": per_date}
    return pit


def build():
    rng = random.Random(SEED)
    days = trading_days()
    assert len(days) == 616, len(days)
    prices = generate_prices(rng, days)
    pit = generate_fundamentals(rng, prices)
    return {
        "synthetic": True,
        "generator": ("scripts/generate_synthetic_real_data_fixture.py"
                      f" (seed {SEED})"),
        "universe": list(UNIVERSE),
        "run_date": RUN_DATE,
        "protocol": PROTOCOL,
        "ranking_dates": list(RANKING_DATES),
        "prices": prices,
        "pit_fundamentals": pit,
        "events_10q": EVENTS_10Q,
    }


def main():
    fixture = build()
    OUTPUT.write_text(json.dumps(fixture, indent=1, sort_keys=True)
                      + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT}: {len(fixture['prices'])} symbols, "
          f"{len(fixture['ranking_dates'])} ranking dates, "
          f"{len(fixture['events_10q'])} events")


if __name__ == "__main__":
    main()
