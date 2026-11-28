"""Fetch the live Apple dossier inputs and commit them as a fixture.

Round 10, PR 10.2. Fetches, once, the real AAPL inputs as of
2026-11-27 and writes ``examples/real-aapl-2026-11-27.json`` plus
the SIC browse-edgar page recordings under
``tests/fixtures/sic-3571-browse-edgar-page*.html``. Tests read
those recordings, never the network.

What is fetched (all public, no key):
- EDGAR submissions for Apple (name, SIC 3571, SIC description);
- EDGAR companyfacts, subsetted to exactly the XBRL tags the
  dossier reads (``toolkit.dossier.DOSSIER_EDGAR_CONCEPTS``) —
  the subset keeps every entry for those tags, so point-in-time
  filtering still works on the recording;
- Yahoo daily closes for AAPL (truncated to dates <= as_of);
- the SIC 3571 browse-edgar pages (all pages) + the SEC bulk
  company_tickers_exchange.json, via ``edgar.get_sic_peers``;
- for every ticker-mapped SIC peer except Apple itself: a Yahoo
  close and the peer's EDGAR net income + shares outstanding as
  of the same date, giving a P/E multiple (price / EPS). A peer
  with no Yahoo price, no filed net income / shares, or
  non-positive EPS is EXCLUDED with its reason recorded — never
  given a made-up multiple.

The DCF forecast is NOT fetched: the eval builds it from the
filed free cash flow plus the assumptions recorded in the
fixture's ``dcf_assumptions`` (flat forecast, stated discount
rate and terminal growth) — assumptions, never filings.

Usage:
  EDGAR_CONTACT_EMAIL=you@example.com \
      python3 scripts/fetch_real_aapl_dossier.py [--output PATH]
The contact email goes into the SEC User-Agent only; it is never
written to any file. Exit 1 without refetching anything if a
fetch fails.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import dossier as dossier_mod
from toolkit import edgar
from toolkit.connectors import fetch_prices_with_volumes

ROOT = Path(__file__).resolve().parent.parent
TICKER = "AAPL"
AS_OF = "2026-11-27"
DEFAULT_OUTPUT = ROOT / "examples" / "real-aapl-2026-11-27.json"
FIXTURE_DIR = ROOT / "tests" / "fixtures"

# DCF assumptions (caller-supplied, stated in the model card):
# the forecast is the filed FY2026 free cash flow, flat for 5
# years — no growth is assumed in the forecast period.
DCF_ASSUMPTIONS = {
    "forecast": "flat at the filed free cash flow for 5 years",
    "forecast_years": 5,
    "discount_rate": 0.09,
    "terminal_growth": 0.025,
    "terminal_year_fcf_rule": "filed free cash flow x (1 + terminal_growth)",
}


def _subset_facts(facts):
    """Keep only the XBRL tags the dossier reads, all entries."""
    wanted = {tag for pairs in dossier_mod.DOSSIER_EDGAR_CONCEPTS.values()
              for _taxonomy, tag in pairs}
    kept = {}
    for tag, body in facts.get("facts", {}).get("us-gaap", {}).items():
        if tag in wanted:
            kept[tag] = body
    return {"cik": facts.get("cik"),
            "entityName": facts.get("entityName"),
            "facts": {"us-gaap": kept}}


def _yahoo_pairs(ticker):
    """Yahoo closes truncated to dates <= AS_OF (last must be AS_OF)."""
    model = fetch_prices_with_volumes(ticker, kind="stock")
    pairs = [[str(date), float(close)]
             for date, close in (model.get("pairs") or [])]
    pairs = [pair for pair in pairs if pair[0] <= AS_OF]
    if not pairs:
        raise RuntimeError(f"no Yahoo closes for {ticker} by {AS_OF}")
    if pairs[-1][0] != AS_OF:
        raise RuntimeError(
            f"last Yahoo close for {ticker} is {pairs[-1][0]}, "
            f"not as_of {AS_OF}")
    return {"source": model.get("source") or "yahoo", "pairs": pairs}


def _peer_multiple(peer, user_agent):
    """P/E for one discovered peer, or (None, reason) to exclude it.

    Price is the Yahoo last close as of AS_OF; EPS is the peer's
    latest annual net income / shares outstanding filed by AS_OF.
    """
    ticker = peer["ticker"]
    try:
        price_model = fetch_prices_with_volumes(ticker, kind="stock")
    except Exception as exc:  # noqa: BLE001 - reason is recorded
        return None, f"no Yahoo price: {exc}"
    price_pairs = [(str(d), float(c)) for d, c in
                   (price_model.get("pairs") or []) if str(d) <= AS_OF]
    if not price_pairs:
        return None, "no Yahoo close by as_of"
    price_date, price = price_pairs[-1]
    try:
        facts = edgar.fetch_company_facts(
            int(peer["cik"]), user_agent=user_agent)
    except Exception as exc:  # noqa: BLE001 - reason is recorded
        return None, f"no EDGAR facts: {exc}"
    try:
        net_income = edgar.annual_value_as_of(
            facts, "us-gaap", "NetIncomeLoss", AS_OF)
    except ValueError:
        return None, "no NetIncomeLoss filed by as_of"
    try:
        shares = edgar.annual_value_as_of(
            facts, "us-gaap", "CommonStockSharesOutstanding", AS_OF)
    except ValueError:
        return None, "no CommonStockSharesOutstanding filed by as_of"
    if not shares["value"]:
        return None, "shares outstanding is 0 in the latest filing"
    eps = net_income["value"] / shares["value"]
    if eps <= 0:
        return None, (f"non-positive EPS {eps:.4f} "
                      "(no meaningful P/E)")
    return {
        "cik": peer["cik"], "name": peer["name"], "ticker": ticker,
        "exchange": peer["exchange"],
        "price": round(price, 2), "price_date": price_date,
        "price_source": price_model.get("source") or "yahoo",
        "net_income": net_income["value"],
        "net_income_end": net_income.get("end"),
        "net_income_filed": net_income.get("filed"),
        "net_income_form": net_income.get("form"),
        "shares_outstanding": shares["value"],
        "shares_filed": shares.get("filed"),
        "shares_form": shares.get("form"),
        "eps": round(eps, 4),
        "pe_ratio": round(price / eps, 2),
    }, ""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=None,
                        help="output JSON path")
    args = parser.parse_args(argv)
    output = Path(args.output) if args.output else DEFAULT_OUTPUT
    contact = os.environ.get("EDGAR_CONTACT_EMAIL", "").strip()
    if not contact or "@" not in contact:
        print("FAIL: set EDGAR_CONTACT_EMAIL to a contact email "
              "address (it goes into the SEC User-Agent only)")
        return 1
    user_agent = edgar.declared_user_agent(contact)

    cik = edgar.ticker_to_cik(TICKER, user_agent=user_agent)
    assert cik == 320193, f"unexpected CIK for AAPL: {cik}"
    submissions = edgar.fetch_submissions(cik, user_agent=user_agent)
    facts = edgar.fetch_company_facts(cik, user_agent=user_agent)
    prices = _yahoo_pairs(TICKER)

    # SIC browse-edgar pages, recorded byte-identical as fixtures.
    sic = str(submissions["sic"])
    page1 = edgar.fetch_sic_browse_page(
        sic, count=100, start=0, user_agent=user_agent)
    page2 = edgar.fetch_sic_browse_page(
        sic, count=100, start=100, user_agent=user_agent)
    sic_peers = edgar.get_sic_peers(
        sic, exclude_cik=cik, user_agent=user_agent)
    for name, html in (("sic-3571-browse-edgar-page1.html", page1),
                       ("sic-3571-browse-edgar-page2.html", page2)):
        (FIXTURE_DIR / name).write_text(html, encoding="utf-8")

    multiples = []
    exclusions = []
    for peer in sic_peers["peers"]:
        multiple, reason = _peer_multiple(peer, user_agent)
        if multiple is None:
            exclusions.append({"cik": peer["cik"], "name": peer["name"],
                               "ticker": peer["ticker"], "reason": reason})
        else:
            multiples.append(multiple)

    fixture = {
        "ticker": TICKER,
        "as_of": AS_OF,
        "fetched_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "cik": cik,
        "company_name": submissions.get("name"),
        "sic": sic,
        "sic_description": submissions.get("sicDescription"),
        "exchange": (submissions.get("exchanges") or [None])[0],
        "edgar_facts": _subset_facts(facts),
        "submissions": submissions,
        "prices": prices,
        "sic_peers": sic_peers,
        "peer_multiples": multiples,
        "peer_exclusions": exclusions,
        "dcf_assumptions": DCF_ASSUMPTIONS,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")

    print(f"real AAPL fixture — as_of {AS_OF}")
    print(f"  filer: {fixture['company_name']} CIK {cik} "
          f"SIC {sic} ({fixture['sic_description']})")
    print(f"  prices: Yahoo {len(prices['pairs'])} bars, "
          f"last {prices['pairs'][-1]}")
    print(f"  SIC peers: {sic_peers['rows_parsed']} rows in "
          f"{sic_peers['pages']} pages, "
          f"{len(sic_peers['peers'])} ticker-mapped (excl. filer), "
          f"{sic_peers['without_ticker']} without a ticker")
    for multiple in multiples:
        print(f"  peer {multiple['ticker']}: price "
              f"{multiple['price']} EPS {multiple['eps']} "
              f"P/E {multiple['pe_ratio']}x")
    for item in exclusions:
        print(f"  peer {item['ticker']}: EXCLUDED — {item['reason']}")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
