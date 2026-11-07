"""Demo assets (Phase 3 front door) are generated from a real terminal run.

The worked-example numbers in the README come from docs/demo-transcript.md,
which scripts/generate_demo_assets.py writes from a live
``python -m toolkit.terminal demo.csv`` run. These tests pin that chain:
the transcript must match a fresh live run, and the SVG/GIF must carry
the same numbers. If the terminal output changes, regenerate the assets
with the script — do not edit them by hand.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPT = REPO_ROOT / "docs" / "demo-transcript.md"
SVG = REPO_ROOT / "docs" / "demo.svg"
GIF = REPO_ROOT / "docs" / "demo.gif"


def _transcript_output():
    text = TRANSCRIPT.read_text(encoding="utf-8")
    start = text.index("```text\n") + len("```text\n")
    end = text.index("\n```", start)
    return text[start:end]


def _live_output():
    proc = subprocess.run(
        [sys.executable, "-m", "toolkit.terminal", "demo.csv"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_transcript_matches_a_live_run():
    assert _transcript_output() == _live_output()


def test_transcript_carries_the_readme_worked_example_numbers():
    out = _transcript_output()
    for needle in (
        "Last close: 1284.91",
        "RSI(14)      88.2",
        "Target first 57.5%",
        "Matches: 2 of 5 setups",
        "BLOCK (15/16 checks passed)",
    ):
        assert needle in out, f"transcript missing: {needle}"


def test_demo_svg_is_real_output_not_hand_typed():
    svg = SVG.read_text(encoding="utf-8")
    assert svg.lstrip().startswith("<svg")
    assert "1284.91" in svg
    assert "57.5%" in svg


def test_demo_gif_is_a_valid_gif():
    data = GIF.read_bytes()
    assert data[:6] in (b"GIF89a", b"GIF87a")
    assert len(data) > 10_000, "GIF suspiciously small — check the generator"
