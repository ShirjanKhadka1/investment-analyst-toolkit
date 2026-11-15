"""Screenshot the rendered workspace page at two widths.

Round U, PR U.3. Tooling only — NOT a repo dependency and never
imported by any test or toolkit module. Uses Playwright for Python
plus a Chromium binary downloaded by
``python3 -m playwright install chromium`` (there is no chromium
binary in PATH on this VM). Opens the committed render
``examples/agent-run-workspace.html`` from disk (``file://``), no
network, no scrolling, no interaction, device scale factor 1.

Usage:
  python3 scripts/screenshot_workspace.py [--html PATH]
      [--out-375 PATH] [--out-1280 PATH]
Defaults: --html examples/agent-run-workspace.html,
--out-375 examples/workspace-375px.png (viewport 375x800),
--out-1280 examples/workspace-1280px.png (viewport 1280x900).
Exit 0 on success, printing both output paths; exit 1 when the
input HTML is missing or no browser can be launched.
"""

import argparse
import sys
from pathlib import Path

DEFAULT_HTML = (Path(__file__).resolve().parent.parent
                / "examples" / "agent-run-workspace.html")
DEFAULT_OUT_375 = (Path(__file__).resolve().parent.parent
                   / "examples" / "workspace-375px.png")
DEFAULT_OUT_1280 = (Path(__file__).resolve().parent.parent
                    / "examples" / "workspace-1280px.png")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", default=str(DEFAULT_HTML),
                        help="rendered workspace HTML to screenshot "
                             "(default: examples/agent-run-workspace.html)")
    parser.add_argument("--out-375", default=str(DEFAULT_OUT_375),
                        help="375px screenshot output "
                             "(default: examples/workspace-375px.png)")
    parser.add_argument("--out-1280", default=str(DEFAULT_OUT_1280),
                        help="1280px screenshot output "
                             "(default: examples/workspace-1280px.png)")
    args = parser.parse_args(argv)
    html_path = Path(args.html)
    if not html_path.exists():
        print(f"FAIL: HTML file not found: {html_path}",
              file=sys.stderr)
        return 1
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("FAIL: Playwright is not installed; run "
              "`python3 -m playwright install chromium` first",
              file=sys.stderr)
        return 1
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 375,
                                              "height": 800},
                                    device_scale_factor=1)
            page.goto(html_path.resolve().as_uri())
            page.wait_for_load_state("load")
            page.screenshot(path=args.out_375)
            page.set_viewport_size({"width": 1280, "height": 900})
            page.screenshot(path=args.out_1280)
            browser.close()
    except Exception as exc:  # noqa: BLE001 — report, do not fake output.
        print(f"FAIL: could not take screenshots: {exc}",
              file=sys.stderr)
        return 1
    print(f"wrote {args.out_375} (375px)")
    print(f"wrote {args.out_1280} (1280px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
