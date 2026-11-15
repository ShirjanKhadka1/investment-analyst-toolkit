"""SEC EDGAR — US fundamentals from the official public APIs, no key.

Fair-access rules (SEC, https://www.sec.gov/os/accessing-edgar-data —
verified live 8 Nov 2026: data.sec.gov submissions for Apple returned
200 in ~1.1s from this environment, most recent filing 2026-11-06):
- Maximum 10 requests per second per user.
- A declared User-Agent is required (name + contact email); without
  one, requests can return 403.
This module throttles to 5 requests/second (0.2s between calls, half
the ceiling) and sends a declared User-Agent naming the toolkit and
its repository. Callers may pass their own ``user_agent`` (with a
contact email) to identify themselves; the default stays honest
about what it is and where to reach it.

Environment note: from this VM (shared/NAT egress 198.18.138.91),
sec.gov is intermittently tarpitted by User-Agent — the same pattern
as Yahoo (see docs/global-markets-audit.md): the toolkit UA timed
out on www.sec.gov on 8 Nov 2026 while a browser UA got data.sec.gov
in 1.1s. The ticker table lives on www.sec.gov, so
``ticker_to_cik`` can time out from a tarpitted IP; the data.sec.gov
endpoints (submissions, facts, concept) are the same. When the table
cannot be fetched, the error names the endpoint — pass the CIK
directly to ``fetch_submissions`` / ``fetch_company_facts`` instead.
GitHub Actions runners are unaffected (daily refresh green 8 Nov).

Sources (all free, no key):
- Ticker -> CIK: https://www.sec.gov/files/company_tickers.json
- Submissions: https://data.sec.gov/submissions/CIK{10}.json
- Company facts (XBRL): https://data.sec.gov/api/xbrl/companyfacts/CIK{10}.json
- Company concept: https://data.sec.gov/api/xbrl/companyconcept/CIK{10}/{taxonomy}/{tag}.json

Research only. Never investment advice. Every value returned carries
its source, its SEC form, its filing date and its period end, so a
number is never separated from where it came from.
"""
from __future__ import annotations

import datetime as _dt
import gzip as _gzip
import json as _json
import time as _time
import urllib.error as _urlerror
import urllib.parse as _urlparse
import urllib.request as _urlrequest

EDGAR_USER_AGENT = (
    "investment-analyst-toolkit/0.9.0 (research; "
    "+https://github.com/ShirjanKhadka1/investment-analyst-toolkit)"
)


def declared_user_agent(contact_email):
    """A declared SEC User-Agent with a contact email.

    SEC fair-access rules require a declared User-Agent naming
    the requester with a contact email. The module default
    (``EDGAR_USER_AGENT``) carries no email, and the SEC returns
    403 to it from some egresses (seen 2026-11-15: 403 on both
    sec.gov hosts from this VM, 200 with a contact email in the
    UA). Callers that fetch EDGAR must pass a UA built here with
    their own contact email — the email is caller-supplied and
    never stored in this repo (the secrets gate forbids email
    addresses in the tree). Raises on an empty or malformed
    address instead of sending a UA the SEC will reject.
    """
    if not isinstance(contact_email, str) or "@" not in contact_email:
        raise ValueError(
            "contact_email must be a contact email address, got "
            f"{contact_email!r}")
    local, _, domain = contact_email.strip().partition("@")
    if not local or "." not in domain or not domain.split(".")[-1]:
        raise ValueError(
            "contact_email must be a contact email address, got "
            f"{contact_email!r}")
    return (
        "investment-analyst-toolkit/0.9.0 (research; contact "
        f"{contact_email.strip()})"
    )
# SEC ceiling is 10 requests/second; stay at half of it.
_MIN_INTERVAL_SECONDS = 0.2
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_CONCEPT_URL = (
    "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/{taxonomy}/{tag}.json"
)

# The only hosts this module ever fetches. The check in _get_json
# compares the parsed hostname exactly — a substring test would also
# match lookalikes such as data.sec.gov.evil.com (CodeQL, 8 Nov 2026).
_ALLOWED_EDGAR_HOSTS = frozenset({"data.sec.gov", "www.sec.gov"})

_last_call_monotonic = 0.0
_ticker_cache: dict[str, int] | None = None

__all__ = [
    "EDGAR_USER_AGENT",
    "annual_value_as_of",
    "declared_user_agent",
    "fetch_company_concept",
    "fetch_company_facts",
    "fetch_submissions",
    "freshest_annual_value_as_of",
    "get_fundamentals",
    "get_fundamentals_as_of",
    "latest_annual_value",
    "ticker_to_cik",
]


def _throttle():
    """Sleep so at least _MIN_INTERVAL_SECONDS pass between EDGAR calls."""
    global _last_call_monotonic  # module-level rate limiter
    now = _time.monotonic()
    wait = _MIN_INTERVAL_SECONDS - (now - _last_call_monotonic)
    if wait > 0:
        _time.sleep(wait)
    _last_call_monotonic = _time.monotonic()


def _check_edgar_url(url):
    """Allow only https URLs on the two SEC hosts, else ValueError.

    The hostname is compared exactly after parsing: lookalikes such
    as ``data.sec.gov.evil.com`` or ``evil.com/data.sec.gov`` are
    rejected, as is any non-https scheme. No caller may fetch an
    arbitrary host through this module.
    """
    parsed = _urlparse.urlparse(url)
    if parsed.scheme != "https" \
            or parsed.hostname not in _ALLOWED_EDGAR_HOSTS:
        raise ValueError(f"refusing non-EDGAR URL: {url!r}")
    return url


def _get_json(url, user_agent=None):
    _check_edgar_url(url)
    _throttle()
    # No manual Host header: urllib sets it from the URL, which is
    # always one of the two allowed hosts above.
    headers = {"User-Agent": user_agent or EDGAR_USER_AGENT,
               "Accept-Encoding": "gzip, deflate"}
    req = _urlrequest.Request(url, headers=headers)
    try:
        with _urlrequest.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            # The request advertises gzip; when the server honours
            # it the body arrives compressed (gzip magic 1f 8b) and
            # must be decompressed before decoding — the old code
            # decoded the compressed bytes as UTF-8 and died.
            if raw[:2] == b"\x1f\x8b":
                raw = _gzip.decompress(raw)
            return _json.loads(raw.decode("utf-8"))
    except _urlerror.HTTPError as exc:
        raise RuntimeError(
            f"EDGAR request failed ({exc.code}) for {url}") from exc
    except _urlerror.URLError as exc:
        raise RuntimeError(
            f"EDGAR network call failed for {url}: {exc.reason}") from exc


def ticker_to_cik(ticker, user_agent=None):
    """Ticker (e.g. 'AAPL') -> CIK int (e.g. 320193). Case-insensitive.

    Raises ValueError for an unknown ticker. The ticker table is
    fetched once per process and cached.
    """
    global _ticker_cache  # one fetch per process
    key = str(ticker).strip().upper()
    if not key:
        raise ValueError("ticker must be non-empty")
    if _ticker_cache is None:
        table = _get_json(_TICKERS_URL, user_agent=user_agent)
        _ticker_cache = {
            str(row["ticker"]).upper(): int(row["cik_str"])
            for row in table.values()}
    if key not in _ticker_cache:
        raise ValueError(f"unknown ticker {ticker!r} in SEC EDGAR table")
    return _ticker_cache[key]


def fetch_submissions(cik, user_agent=None):
    """SEC submissions (filings index) for a CIK. CIK must be digits."""
    if not str(cik).isdigit():
        raise ValueError(f"CIK must be digits, got {cik!r}")
    return _get_json(
        _SUBMISSIONS_URL.format(cik=int(cik)), user_agent=user_agent)


def fetch_company_facts(cik, user_agent=None):
    """Full XBRL company facts for a CIK. CIK must be digits.

    Throttled, declared-UA version of
    ``toolkit.data.fetch_sec_company_facts``; prefer this one for new
    EDGAR calls so the rate limit is honoured.
    """
    if not str(cik).isdigit():
        raise ValueError(f"CIK must be digits, got {cik!r}")
    return _get_json(_FACTS_URL.format(cik=int(cik)), user_agent=user_agent)


def fetch_company_concept(cik, taxonomy, tag, user_agent=None):
    """One XBRL concept for a CIK, e.g. taxonomy='us-gaap', tag='Revenues'."""
    if not str(cik).isdigit():
        raise ValueError(f"CIK must be digits, got {cik!r}")
    if not taxonomy or not tag:
        raise ValueError("taxonomy and tag must be non-empty")
    return _get_json(
        _CONCEPT_URL.format(cik=int(cik), taxonomy=taxonomy, tag=tag),
        user_agent=user_agent)


def latest_annual_value(facts, taxonomy, tag):
    """Most recent audited annual (10-K) value of one XBRL concept.

    ``facts`` is the dict from fetch_company_facts. Prefers 10-K
    entries; falls back to the most recent entry of any form only if
    no 10-K exists for the concept. Returns {"value", "end", "form",
    "filed", "accn", "fy"} — all from the filing itself, nothing
    derived. Raises ValueError if the concept is absent.
    """
    try:
        units = facts["facts"][taxonomy][tag]["units"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"concept {taxonomy}:{tag} not in company facts") from exc
    entries = [e for vals in units.values() for e in vals]
    if not entries:
        raise ValueError(f"concept {taxonomy}:{tag} has no values")
    annual = [e for e in entries if e.get("form") == "10-K"]
    pool = annual or entries
    best = max(pool, key=lambda e: (e.get("end", ""), e.get("filed", "")))
    return {
        "value": best["val"],
        "end": best.get("end"),
        "form": best.get("form"),
        "filed": best.get("filed"),
        "accn": best.get("accn"),
        "fy": best.get("fy"),
    }


def annual_value_as_of(facts, taxonomy, tag, as_of, prefer_10k=True):
    """Point-in-time annual value of one XBRL concept.

    ``facts`` is the dict from fetch_company_facts; ``as_of`` is
    an ISO date (a ranking date). Only entries FILED on or before
    ``as_of`` are visible — a filing made after the ranking date
    is never used for it, so no look-ahead. With ``prefer_10k``
    (the default) the latest 10-K entry filed by ``as_of`` wins,
    falling back to the most recent entry of any form only when
    no 10-K was filed by then (the same fallback as
    ``latest_annual_value``). With ``prefer_10k=False`` the most
    recent entry of any form filed by ``as_of`` wins — for
    point-in-time counts such as shares outstanding, where a
    10-Q is as current as a 10-K. Returns the same
    {"value", "end", "form", "filed", "accn", "fy"} shape as
    ``latest_annual_value``. Raises ValueError if the concept is
    absent or nothing was filed by ``as_of``.
    """
    try:
        units = facts["facts"][taxonomy][tag]["units"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"concept {taxonomy}:{tag} not in company facts") from exc
    entries = [e for vals in units.values() for e in vals]
    if not entries:
        raise ValueError(f"concept {taxonomy}:{tag} has no values")
    visible = [e for e in entries
               if (e.get("filed") or "") <= str(as_of)]
    if not visible:
        raise ValueError(
            f"concept {taxonomy}:{tag} has no value filed by {as_of}")
    if prefer_10k:
        annual = [e for e in visible if e.get("form") == "10-K"]
        pool = annual or visible
    else:
        pool = visible
    best = max(pool, key=lambda e: (e.get("end", ""), e.get("filed", "")))
    return {
        "value": best["val"],
        "end": best.get("end"),
        "form": best.get("form"),
        "filed": best.get("filed"),
        "accn": best.get("accn"),
        "fy": best.get("fy"),
    }


def freshest_annual_value_as_of(facts, candidates, as_of,
                                prefer_10k=True):
    """Freshest annual value across several XBRL tags, as of a date.

    XBRL tags are not uniform across filers: Apple and Microsoft
    report revenue under
    ``RevenueFromContractWithCustomerExcludingAssessedTax`` while
    Alphabet reports ``Revenues`` and Amazon reports no
    ``Revenues`` at all (seen 2026-11-15); Visa's
    ``StockholdersEquity`` stops in 2011 while
    ``StockholdersEquityIncludingPortionAttributableToNoncontrolling
    Interest`` is current. A single tag would silently return a
    15-year-stale value for some filers (Microsoft's ``Revenues``
    ends 2010-12-31). ``candidates`` is a list of
    (taxonomy, tag) pairs for one economic concept; this returns
    the candidate whose latest value filed by ``as_of`` has the
    most recent (end, filed) — the freshest concept, not the
    first listed. Returns (value_dict, taxonomy, tag), the value
    dict in the ``latest_annual_value`` shape. Raises ValueError
    naming every candidate when none has a value filed by
    ``as_of``.
    """
    best = None
    for taxonomy, tag in candidates:
        try:
            value = annual_value_as_of(facts, taxonomy, tag, as_of,
                                       prefer_10k=prefer_10k)
        except ValueError:
            continue
        key = (value.get("end", ""), value.get("filed", ""))
        if best is None or key > best[0]:
            best = (key, value, taxonomy, tag)
    if best is None:
        names = ", ".join(f"{t}:{g}" for t, g in candidates)
        raise ValueError(
            f"none of {names} has a value filed by {as_of}")
    _key, value, taxonomy, tag = best
    return value, taxonomy, tag


def get_fundamentals_as_of(ticker, concepts, as_of, user_agent=None):
    """Latest audited annual values as of ``as_of``, by ticker.

    Point-in-time ``get_fundamentals``: ``concepts`` is a list of
    (taxonomy, tag) pairs, and every value is the latest 10-K
    entry filed on or before ``as_of`` (see
    ``annual_value_as_of``). Returns {"ticker", "cik", "source",
    "retrieved_at", "as_of", "values": {tag:
    annual_value_as_of(...)}}. A missing concept raises
    ValueError naming it — no value is ever invented or
    defaulted. Callers that need a per-concept fallback (a
    symbol that carries no GrossProfit, e.g. a bank) catch that
    error per concept instead of calling this helper.
    """
    cik = ticker_to_cik(ticker, user_agent=user_agent)
    facts = fetch_company_facts(cik, user_agent=user_agent)
    values = {}
    for taxonomy, tag in concepts:
        values[tag] = annual_value_as_of(facts, taxonomy, tag, as_of)
    return {
        "ticker": str(ticker).strip().upper(),
        "cik": cik,
        "source": "SEC EDGAR companyfacts",
        "retrieved_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"),
        "as_of": str(as_of),
        "values": values,
    }


def get_fundamentals(ticker, concepts, user_agent=None):
    """Latest audited annual values for several XBRL concepts, by ticker.

    ``concepts`` is a list of (taxonomy, tag) pairs, e.g.
    [("us-gaap", "Revenues"), ("us-gaap", "NetIncomeLoss")].
    Returns {"ticker", "cik", "source", "retrieved_at", "values":
    {tag: latest_annual_value(...)}}. A missing concept raises
    ValueError naming it — no value is ever invented or defaulted.
    """
    cik = ticker_to_cik(ticker, user_agent=user_agent)
    facts = fetch_company_facts(cik, user_agent=user_agent)
    values = {}
    for taxonomy, tag in concepts:
        values[tag] = latest_annual_value(facts, taxonomy, tag)
    return {
        "ticker": str(ticker).strip().upper(),
        "cik": cik,
        "source": "SEC EDGAR companyfacts",
        "retrieved_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"),
        "values": values,
    }
