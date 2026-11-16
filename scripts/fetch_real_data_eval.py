"""Re-fetch the raw data behind the Round E, E.1 real-data evaluation.

The raw vendor data (Yahoo daily closes + SEC EDGAR point-in-time
fundamentals + 10-Q filing dates) is NOT committed to this repo:
data policy is derived metrics only. This script reproduces the
raw cache locally so the evaluation can be re-run and the derived
metrics re-derived:

  EDGAR_CONTACT_EMAIL=<contact> python3 scripts/fetch_real_data_eval.py

It writes ``data/real-data-eval-2026-11-15.json`` (gitignored —
never commit it; the committed record is
``data/real-data-eval-2026-11-15/derived-metrics.json`` plus
``data/real-data-eval-2026-11-15/manifest.json``, which records the
SHA-256 of the raw cache as fetched on 2026-11-15). Then:

  python3 scripts/evaluate_real_data.py

re-runs the four evaluations from the local raw cache and prints
the derived metrics. The EDGAR fetch needs a declared User-Agent
with a contact email (SEC fair-access rule): set
``EDGAR_CONTACT_EMAIL`` — the script refuses to fetch without it,
and the address is never written to the repo.

Stdlib only. Network is used only by this fetch; the evaluation
itself reads the local cache.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import evaluate_real_data as evd

from toolkit import edgar

CACHE_PATH = evd.CACHE_PATH


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(CACHE_PATH),
                        help="where to write the raw cache "
                             "(default: %(default)s)")
    args = parser.parse_args(argv)
    contact = os.environ.get("EDGAR_CONTACT_EMAIL", "")
    if not contact:
        print("EDGAR_CONTACT_EMAIL is not set: EDGAR needs a "
              "declared User-Agent with a contact email (SEC "
              "fair-access rule). Set it and re-run.",
              file=sys.stderr)
        return 2
    user_agent = edgar.declared_user_agent(contact)
    cache = evd._fetch_cache(user_agent)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cache, indent=2, sort_keys=True)
                        + "\n", encoding="utf-8")
    print(f"raw cache written: {out_path}", file=sys.stderr)
    print(f"sha256: {_sha256(out_path)}", file=sys.stderr)
    print("Do NOT commit this file: raw vendor data is local-only. "
          "The committed record is data/real-data-eval-2026-11-15/"
          "derived-metrics.json + manifest.json.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
