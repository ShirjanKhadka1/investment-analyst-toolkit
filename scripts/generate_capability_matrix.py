"""Generate docs/CAPABILITY-MATRIX.md from the repo table below.

Stdlib only. The table is the source of truth: one row per headline
capability of each of the 15 reference repos torn down on 2026-11-07
(see ~/workspace/github-repos/ops/teardown/*.md — the teardown files
are the evidence for the reference side; this repo's code is the
evidence for our side). The doc is GENERATED — edit this script, not
the doc. ``scripts/qa.py`` runs this script with ``--check`` and fails
if the doc is stale (regenerating produces a diff).

Status meanings (world-market side first; NEPSE-only items say so):
- implemented: a tested function in this repo does it. Every such row
  links the test file that proves it.
- partial: part of it exists here (or in a named satellite repo) and
  part does not. The evidence column says which part is which.
- not planned: deliberately out of scope. The evidence column gives
  the reason — usually a programme rule (research-only, no execution,
  no credentials, stdlib-only, model-light).

Counts in the doc are computed from this table, never typed.

Usage:
  python3 scripts/generate_capability_matrix.py [--root REPO_ROOT]
  python3 scripts/generate_capability_matrix.py --check [--root ...]
Exit 0 when the doc is written (or, with --check, when it is fresh);
exit 1 with --check when regenerating would change the doc.
"""

import argparse
import difflib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "CAPABILITY-MATRIX.md"

# (repo, licence, rows) — rows: (capability, status, evidence, test path)
# Test paths are relative to the repo root; "" means no test (only for
# "not planned" rows and for partial rows whose work lives outside this
# repo).
REPOS = [
    ("agency-agents",
     "msitarzewski/agency-agents", "MIT",
     [
         (("Specialist agent persona library (283 prompts; finance "
          "slice: DCF/comps, bear-case discipline)"),
          "partial",
          ("Finance agent prompts live in finance-research-agents, not "
          "this repo; this repo ships the deterministic rules-mode "
          "fallback those agents can run without an LLM."),
          "tests/test_rules_mode.py"),
         ("Runbooks + phase playbooks (multi-agent workflow)",
          "partial",
          ("Runbooks live in finance-research-agents; this repo has no "
          "runbook runner."),
          ""),
         (("Multi-tool converter/installer (one prompt set, many agent "
          "tools)"),
          "not planned",
          ("The toolkit is a Python package, not a prompt installer for "
          "other agent tools."),
          ""),
     ]),
    ("augur",
     "BruceLanLan/augur", "MIT",
     [
         (("Point-in-time evidence store (business / availability / "
          "fetch time per fact)"),
          "implemented",
          ("toolkit/pit_store.py: a query as of a date sees only "
          "records published by that date."),
          "tests/test_pit_store.py"),
         (("Immutable run bundles + change ledger (what changed since "
          "the last run)"),
          "implemented",
          ("toolkit/decision_log.py logs every call before its outcome "
          "as a hash-chained append-only row (prev_hash per row); "
          "scripts/track_record_daily.py refuses to publish unless "
          "the remote rows are byte-identical and the diff has only "
          "additions; scripts/verify_track_record.py walks the "
          "branch history and fails if any commit changed an earlier "
          "row; toolkit/track_record.py settles it; reflections are "
          "append-only."),
          "tests/test_decision_log.py"),
         (("Missing-data abstention (mark missing, never silent "
          "zero-fill)"),
          "implemented",
          ("toolkit/connectors.py and toolkit/free_data.py record every "
          "failed source in `tried`; a total failure lists every "
          "attempt."),
          "tests/test_connectors.py"),
         (("Multi-master persona consensus (18 investing masters, "
          "weighted)"),
          "not planned",
          ("Uncalibrated persona scores are not evidence; presenting a "
          "consensus score would break the no-fake-numbers rule."),
          ""),
     ]),
    ("day-trader-skill",
     "Jakeschincariol/day-trader-skill", "MIT",
     [
         ("Time Machine (seeded Monte-Carlo + historical replay)",
          "implemented",
          "toolkit/timemachine.py, daily bars, NEPSE-style CSVs.",
          "tests/test_timemachine.py"),
         ("Scanner rule table + named setups",
          "partial",
          ("The scanner lives in nepali-capital-market-research; "
          "toolkit/terminal.py reimplements its five named setups."),
          "tests/test_terminal.py"),
         ("Exit plan before entry + R arithmetic + risk gate",
          "implemented",
          "toolkit/position_plan.py.",
          "tests/test_position_plan.py"),
         ("Budget sizing (min-of-limits, one config)",
          "implemented",
          "toolkit/position_plan.py (DEFAULT_RESEARCH_CONFIG).",
          "tests/test_position_plan.py"),
         ("Broker order execution (Alpaca/IBKR)",
          "not planned",
          "Research only: no order execution, ever.",
          ""),
     ]),
    ("eigenledger",
     "santoshlite/EigenLedger", "Apache-2.0",
     [
         (("Portfolio optimisation (efficient frontier, HRP, "
          "Black-Litterman)"),
          "partial",
          ("Mean-variance, HRP and max-diversification are implemented "
          "(toolkit/quant.py, toolkit/portfolio.py); Black-Litterman "
          "is not."),
          "tests/test_quant.py"),
         ("Performance / risk tear sheets",
          "implemented",
          "toolkit/performance.py + toolkit/quant.py risk_summary.",
          "tests/test_phase4_engines.py"),
         ("PDF report generation",
          "partial",
          ("toolkit/report.py produces one-page HTML reports; no PDF "
          "output exists."),
          "tests/test_report.py"),
         ("Single friendly entry point over specialist libraries",
          "implemented",
          "toolkit/quant.py portfolio_analysis().",
          "tests/test_quant.py"),
     ]),
    ("finceptterminal",
     "Fincept-Corporation/FinceptTerminal", "AGPL-3.0 (ideas only)",
     [
         ("Indicator library + canonical defaults table",
          "implemented",
          ("toolkit/indicators.py (formulas reimplemented from the "
          "standard definitions; defaults follow Fincept's table)."),
          "tests/test_indicators.py"),
         ("DCF / valuation pipeline",
          "implemented",
          "toolkit/valuation.py + toolkit/quant.py DCF.",
          "tests/test_quant.py"),
         ("Risk sheet (Sharpe/Sortino/drawdown/MC VaR)",
          "implemented",
          "toolkit/performance.py, toolkit/montecarlo.py.",
          "tests/test_phase4_engines.py"),
         ("Screener criteria grammar + audit log",
          "partial",
          ("Named setups exist in toolkit/terminal.py; no criteria "
          "grammar or audit log in this repo."),
          "tests/test_terminal.py"),
         ("Connector catalogue (201 connectors)",
          "partial",
          ("toolkit/connectors.py fallback chain + toolkit/free_data.py "
          "exist; the catalogue JSON is deferred (see README)."),
          "tests/test_connectors.py"),
         ("Live brokerage / algo-trading execution",
          "not planned",
          "Research only: no order execution, no auto-trading.",
          ""),
     ]),
    ("finquant",
     "fmilthaler/FinQuant", "MIT",
     [
         ("Portfolio container (prices + returns + optimisation)",
          "implemented",
          ("toolkit/portfolio.py + toolkit/quant.py "
          "portfolio_analysis()."),
          "tests/test_quant.py"),
         ("Efficient-frontier + Monte-Carlo optimisation",
          "implemented",
          ("toolkit/portfolio.py efficient_frontier; "
          "toolkit/montecarlo.py."),
          "tests/test_phase4_engines.py"),
         ("Moving-average bands / Bollinger Bands",
          "implemented",
          "toolkit/indicators.py.",
          "tests/test_indicators.py"),
         ("yfinance / Quandl fetch paths",
          "not planned",
          ("Fetch paths that depend on third-party APIs break (see the "
          "reference repo's own issue #136); this repo is CSV-first "
          "plus keyless sources."),
          ""),
     ]),
    ("maybe",
     "maybe-finance/maybe", "AGPL-3.0 (ideas only)",
     [
         ("Net-worth / balance-sheet model",
          "not planned",
          ("Personal-finance app scope: this toolkit is research-only "
          "and never holds bank or brokerage credentials."),
          ""),
         ("Budget + category model",
          "not planned",
          "Same reason as above.",
          ""),
         ("Bank / brokerage import pipeline",
          "not planned",
          ("Same reason as above; no credentials in the research "
          "programme."),
          ""),
     ]),
    ("multi-agent-market-research",
     "Intelica/multi-agent-market-research", "No licence (ideas only)",
     [
         (("Sequential specialist crew (research -> use cases -> "
          "resources)"),
          "partial",
          ("toolkit/rules_mode.py gives deterministic per-agent "
          "outputs; the crew itself lives in finance-research-agents."),
          "tests/test_rules_mode.py"),
         ("Structured JSON handoffs between agents",
          "partial",
          ("This repo: toolkit/llm_providers.py schema-validated JSON "
          "outputs (PR A). Agent-side handoff schemas live in "
          "finance-research-agents."),
          "tests/test_llm_providers.py"),
         ("Streamlit UI + PDF export",
          "not planned",
          ("No app UI in this repo; the surfaces are the terminal and "
          "HTML reports."),
          ""),
     ]),
    ("openbb",
     "OpenBB/OpenBB", "Apache-2.0",
     [
         (("Provider contract (one standard model per endpoint; core "
          "never touches vendor APIs)"),
          "partial",
          ("toolkit/connectors.py returns standardised series through "
          "one chain; per-endpoint models are not yet defined and the "
          "catalogue JSON is deferred."),
          "tests/test_connectors.py"),
         ("Standardised output models",
          "partial",
          ("toolkit/free_data.py series carry source, retrieval time, "
          "last date and age."),
          "tests/test_free_data.py"),
         ("MCP server surface for AI agents",
          "implemented",
          "toolkit/mcp_server.py (8 tools, all four hints declared).",
          "tests/test_mcp_server.py"),
         ("Point-in-time fundamentals",
          "implemented",
          "toolkit/pit_store.py.",
          "tests/test_pit_store.py"),
         ("FRED / ECB / IMF / SEC provider implementations",
          "partial",
          ("SEC EDGAR is implemented (toolkit/edgar.py); FRED, ECB and "
          "IMF are not."),
          "tests/test_edgar.py"),
     ]),
    ("qf-lib",
     "quarkfin/qf-lib", "Apache-2.0",
     [
         ("Data-provider abstraction (one interface, CSV first-class)",
          "implemented",
          ("toolkit/connectors.py + toolkit/data_layer.py; CSV loader "
          "toolkit/nepse_loader.py."),
          "tests/test_connectors.py"),
         ("Event-driven backtester with look-ahead-bias guards",
          "partial",
          ("toolkit/backtest.py is signal-based with no-lookahead "
          "rules, not event-driven; look-ahead guards exist via "
          "toolkit/pit_store.py."),
          "tests/test_phase4_engines.py"),
         ("Templated PDF study summaries",
          "partial",
          "HTML reports in toolkit/report.py; no PDF output exists.",
          "tests/test_report.py"),
     ]),
    ("qlib",
     "microsoft/qlib", "MIT",
     [
         ("Expression-engine feature layer",
          "not planned",
          ("No expression engine: factors are plain functions in "
          "toolkit/quant.py by design (stdlib-only, model-light)."),
          ""),
         ("Point-in-time dataset discipline",
          "implemented",
          "toolkit/pit_store.py.",
          "tests/test_pit_store.py"),
         ("Alpha158 factor set (158 factors as canonical baseline)",
          "partial",
          ("toolkit/quant.py alpha158_baseline_factors() implements "
          "the baseline subset; the full 158-factor set is not "
          "implemented."),
          "tests/test_quant.py"),
         (("Experiment workflow / recorder (config + model + metrics "
          "per run)"),
          "partial",
          ("toolkit/decision_log.py is the run recorder; there is no "
          "config/model/metrics experiment tracker."),
          "tests/test_decision_log.py"),
         ("Model zoo (LightGBM / LSTM / Transformers / GATs)",
          "not planned",
          ("Model-light by design: baselines are naive, moving average, "
          "trend and exponential smoothing, selected walk-forward "
          "(toolkit/predictive.py)."),
          ""),
     ]),
    ("quantinveststrats",
     "ArturSepp/QuantInvestStrats", "MIT",
     [
         (("Drift-aware portfolio history (units held between "
          "rebalances, explicit costs)"),
          "partial",
          ("toolkit/backtest.py charges costs and reports against "
          "buy-and-hold; drift-aware unit accounting is not "
          "implemented."),
          "tests/test_phase4_engines.py"),
         ("Performance stats + multiple-testing correction",
          "implemented",
          ("toolkit/quant.py (Sharpe standard error / confidence "
          "interval, Bonferroni, Benjamini-Hochberg)."),
          "tests/test_quant.py"),
         ("Factsheet report archetypes",
          "partial",
          ("One-page HTML report in toolkit/report.py; no multi-page "
          "factsheet archetypes."),
          "tests/test_report.py"),
         ("Regime classifier + signal diagnostics",
          "partial",
          ("Volatility regime labelling exists "
          "(toolkit/predictive.py volatility_regime); there is no "
          "signal-diagnostics suite."),
          "tests/test_phase4_engines.py"),
     ]),
    ("skfolio",
     "skfolio/skfolio", "BSD-3-Clause",
     [
         (("Optimiser taxonomy (mean-risk, risk-budgeting, "
          "max-diversification, hierarchical, robust)"),
          "partial",
          ("Mean-variance, HRP and max-diversification are implemented; "
          "risk-budgeting and distributionally-robust optimisation "
          "are not."),
          "tests/test_quant.py"),
         ("Risk-measure library (CVaR, EVaR, drawdown measures)",
          "implemented",
          ("toolkit/quant.py + toolkit/performance.py (CVaR, EVaR, max "
          "drawdown)."),
          "tests/test_quant.py"),
         ("Shrinkage covariance estimators",
          "implemented",
          ("toolkit/portfolio.py shrunk_covariance (simplified "
          "shrinkage, documented as not full Ledoit-Wolf)."),
          "tests/test_new_modules.py"),
         ("sklearn-compatible estimator interface",
          "not planned",
          "Stdlib-only by design; no scikit-learn dependency.",
          ""),
     ]),
    ("tradingagents",
     "TauricResearch/TradingAgents", "Apache-2.0",
     [
         (("Multi-agent debate (bull/bear researchers -> research "
          "manager; three-way risk debate)"),
          "partial",
          ("The debate structure lives in finance-research-agents "
          "runbooks; this repo has no debate runner."),
          ""),
         (("Analyst team roles (fundamentals / market / news / "
          "sentiment)"),
          "partial",
          ("toolkit/rules_mode.py covers deterministic analyst outputs; "
          "the news-sentiment agent lives in finance-research-agents."),
          "tests/test_rules_mode.py"),
         (("Risk-management debate (aggressive / neutral / "
          "conservative)"),
          "partial",
          ("The deterministic analogue is the risk gate in "
          "toolkit/position_plan.py."),
          "tests/test_position_plan.py"),
         ("Memory log + reflection + settlement vs benchmark",
          "implemented",
          ("toolkit/decision_log.py (append-only log + reflections) and "
          "toolkit/track_record.py (settlement vs naive and "
          "buy-and-hold baselines)."),
          "tests/test_decision_log.py"),
         ("Point-in-time / look-ahead discipline",
          "implemented",
          ("toolkit/pit_store.py; direction-rule code is pinned by git "
          "blob hash in every logged call."),
          "tests/test_pit_store.py"),
         ("Multi-provider LLM client factory",
          "partial",
          ("This repo: toolkit/llm_providers.py (Ollama, "
          "OpenAI-compatible, Anthropic, Gemini, fake) with caps and "
          "schema-validated JSON (PR A). No model catalogue: the "
          "caller names the model."),
          "tests/test_llm_providers.py"),
         ("Trade execution / portfolio manager's send-order step",
          "not planned",
          "Research only: no order execution, simulated or otherwise.",
          ""),
     ]),
    ("union",
     "unionlabs/union", "Apache-2.0 / MIT",
     [
         ("ZK bridging / cross-chain messaging",
          "not planned",
          ("No finance-research content; the programme never moves "
          "value between chains."),
          ""),
         ("Monorepo component table (docs pattern)",
          "partial",
          ("Used at programme level (portfolio-shape docs in the ops "
          "repo), not as a table in this repo."),
          ""),
     ]),
]

# Ranked build plan: impact for a world-market user (1-5) divided by
# effort (1-5). Rank is the tie-break (dependency order) when ratios
# are equal. These seven items are NOT started — this table is the plan,
# not the work.
BUILD_PLAN = [
    ("Universe screener",
     5, 2,
     ("Highest impact per effort: the named-setup rules already exist "
     "(toolkit/terminal.py) and the scanner table exists in the "
     "satellite repo; a universe scan is a small step from both.")),
    ("Second price source",
     4, 2,
     ("Data reliability for world markets: the fallback chain in "
     "toolkit/connectors.py already supports a second source; adding "
     "one more keyless source is bounded work.")),
    ("Volatility forecasting",
     3, 2,
     ("Builds directly on toolkit/performance.py (rolling volatility) "
     "and toolkit/options.py (implied volatility): EWMA/GARCH-lite "
     "forecasts with walk-forward evaluation."),
     ),
    ("News and macro feed",
     4, 3,
     ("Feeds the analyst agents: the EDGAR + free-data patterns exist, "
     "but a feed needs source selection, freshness rules and a "
     "sentiment-evaluation step."),
     ),
    ("Derivatives and fixed income",
     4, 4,
     ("Extends toolkit/options.py (Black-Scholes, Greeks) to futures "
     "and bonds (duration/convexity, yield curve); a larger, "
     "self-contained build."),
     ),
    ("Local dashboard",
     3, 3,
     ("A local HTML view over the terminal output; new surface, no new "
     "maths — behind derivatives because the maths matters more than "
     "the surface."),
     ),
    ("Pages demo with real global data",
     3, 4,
     ("Last on purpose: it depends on the second price source and on "
     "the freshness discipline being proven, or it would publish "
     "stale data as if it were live."),
     ),
]

BANNED_WORDS = (
    "revolutionary", "cutting-edge", "world-class", "best-in-class",
    "unmatched", "game-changing", "state-of-the-art", "industry-leading",
)


def _counts():
    total = sum(len(rows) for _name, _repo, _lic, rows in REPOS)
    implemented = sum(1 for _n, _r, _l, rows in REPOS
                      for _c, s, _e, _t in rows if s == "implemented")
    partial = sum(1 for _n, _r, _l, rows in REPOS
                  for _c, s, _e, _t in rows if s == "partial")
    not_planned = total - implemented - partial
    return total, implemented, partial, not_planned


def _per_repo_counts():
    """(repo, implemented, partial, not_planned, total) per repo."""
    out = []
    for _name, repo, _lic, rows in REPOS:
        implemented = sum(1 for _c, s, _e, _t in rows
                          if s == "implemented")
        partial = sum(1 for _c, s, _e, _t in rows if s == "partial")
        not_planned = len(rows) - implemented - partial
        out.append((repo, implemented, partial, not_planned, len(rows)))
    return out


README_START = "<!-- BEGIN GENERATED COVERAGE TABLE -->"
README_END = "<!-- END GENERATED COVERAGE TABLE -->"


def render_coverage_table():
    """Per-reference-repo coverage table, generated — never
    hand-written. Plain integers only: no "tests" or "%" forms, so
    scripts/check_numbers.py has nothing to match here."""
    total, implemented, partial, not_planned = _counts()
    lines = []
    lines.append("### Coverage by reference repo")
    lines.append("")
    lines.append("Generated by scripts/generate_capability_matrix.py "
                 "from the same data as docs/CAPABILITY-MATRIX.md.")
    lines.append("")
    lines.append("| Repo | Implemented | Partial | Not planned | Total |")
    lines.append("|---|---|---|---|---|")
    for repo, impl, part, notp, tot in _per_repo_counts():
        lines.append(f"| {repo} | {impl} | {part} | {notp} | {tot} |")
    lines.append(f"| **Total** | **{implemented}** | **{partial}** | "
                 f"**{not_planned}** | **{total}** |")
    return "\n".join(lines)


def render_readme_block():
    """The full marker-delimited block inserted into README.md."""
    return f"{README_START}\n{render_coverage_table()}\n{README_END}"


def readme_block_fresh(root=ROOT):
    """True when README.md carries the current generated block."""
    readme = Path(root) / "README.md"
    if not readme.exists():
        return False
    text = readme.read_text(encoding="utf-8")
    return render_readme_block() in text


def write_readme_block(root=ROOT):
    """Insert or replace the generated block in README.md.

    First write: the block goes after the comparison table in the
    "How it compares" section (just before "## Architecture").
    Later writes replace the text between the markers.
    """
    readme = Path(root) / "README.md"
    text = readme.read_text(encoding="utf-8")
    block = render_readme_block()
    if README_START in text and README_END in text:
        start = text.index(README_START)
        end = text.index(README_END) + len(README_END)
        text = text[:start] + block + text[end:]
    else:
        anchor = "\n## Architecture\n"
        if anchor not in text:
            raise RuntimeError(
                "README.md has no '## Architecture' anchor for the "
                "generated coverage table")
        text = text.replace(anchor, f"\n{block}\n{anchor}", 1)
    readme.write_text(text, encoding="utf-8")
    return readme


def render():
    """Render the matrix doc from REPOS + BUILD_PLAN. Pure function."""
    total, implemented, partial, not_planned = _counts()
    lines = []
    lines.append("# Capability Matrix — 15 reference repos vs this toolkit")
    lines.append("")
    lines.append("<!-- GENERATED by scripts/generate_capability_matrix.py — "
                 "do not edit by hand. -->")
    lines.append("")
    lines.append("Generated by scripts/generate_capability_matrix.py. "
                 "Source: the teardown files in "
                 "`~/workspace/github-repos/ops/teardown/` (2026-11-07) "
                 "for the reference side; this repo's code for our side. "
                 "No date is printed here on purpose: the qa gate fails "
                 "if the doc drifts from the script, and a date line "
                 "would drift every day.")
    lines.append("")
    lines.append("## How to read")
    lines.append("")
    lines.append("- World-market capabilities first. NEPSE-only items say "
                 "so in the evidence column.")
    lines.append("- **implemented** — a tested function in this repo does "
                 "it. Every such row links the test file that proves it.")
    lines.append("- **partial** — part exists here (or in a named "
                 "satellite repo) and part does not. The evidence column "
                 "says which part is which.")
    lines.append("- **not planned** — deliberately out of scope. The "
                 "evidence column gives the reason, usually a programme "
                 "rule: research-only, no execution, no credentials, "
                 "stdlib-only, model-light.")
    lines.append("- AGPL and unlicensed repos are ideas only: nothing was "
                 "copied from them. Apache-2.0/MIT code is never pasted "
                 "without attribution plus a LICENSES.md entry.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"{total} headline capabilities across 15 reference repos: "
                 f"**{implemented} implemented**, **{partial} partial**, "
                 f"**{not_planned} not planned**.")
    lines.append("")
    lines.append("The implemented share is concentrated where the toolkit "
                 "already lives — indicators, portfolio maths, risk "
                 "measures, point-in-time discipline, decision logging — "
                 "and thin where the programme deliberately stops: "
                 "execution, personal finance, model zoos, and app UIs.")
    lines.append("")
    for _name, repo, licence, rows in REPOS:
        lines.append(f"## {repo} ({licence})")
        lines.append("")
        lines.append("| Capability | Status | Evidence / reason | Test |")
        lines.append("|---|---|---|---|")
        for capability, status, evidence, test in rows:
            test_cell = (f"[{test}](../{test})" if test else "—")
            lines.append(f"| {capability} | {status} | {evidence} | "
                         f"{test_cell} |")
        lines.append("")
    lines.append("## Ranked build plan")
    lines.append("")
    lines.append("Impact for a world-market user (1–5) divided by effort "
                 "(1–5). Rank is the tie-break when ratios are equal. "
                 "None of these is started — this table is the plan, not "
                 "the work.")
    lines.append("")
    lines.append("| Rank | Item | Impact | Effort | Impact/effort | Why here |")
    lines.append("|---|---|---|---|---|---|")
    ranked = sorted(
        enumerate(BUILD_PLAN, start=1),
        key=lambda pair: (-(pair[1][1] / pair[1][2]), pair[0]))
    for display_rank, (_original_rank, (item, impact, effort, why)) \
            in enumerate(ranked, start=1):
        ratio = impact / effort
        lines.append(f"| {display_rank} | {item} | {impact} | {effort} | "
                     f"{ratio:.2f} | {why} |")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- The scanner row (day-trader-skill) and the runbook "
                 "rows (agency-agents, tradingagents) point at "
                 "finance-research-agents and "
                 "nepali-capital-market-research, the satellite repos "
                 "where that work lives. This matrix covers this repo; "
                 "the satellites have their own counts.")
    lines.append("- The LLM provider layer (toolkit/llm_providers.py) "
                 "moves two rows from 'not planned' to 'partial': "
                 "structured JSON handoffs (multi-agent-market-research) "
                 "and the multi-provider LLM factory (tradingagents). "
                 "The executable agent team that would use it (PR B) is "
                 "not in scope for this change.")
    lines.append("")
    return "\n".join(lines)


def is_fresh(root=ROOT):
    """True when the generated doc on disk matches a fresh render."""
    target = Path(root) / "docs" / "CAPABILITY-MATRIX.md"
    if not target.exists():
        return False
    return target.read_text(encoding="utf-8") == render()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the doc is stale")
    args = parser.parse_args()
    root = Path(args.root)
    target = root / "docs" / "CAPABILITY-MATRIX.md"
    if args.check:
        problems = []
        if not is_fresh(root):
            problems.append("capability-matrix: STALE — regenerate "
                            "with scripts/generate_capability_matrix.py")
        if not readme_block_fresh(root):
            problems.append("capability-matrix: README coverage table "
                            "STALE — regenerate with "
                            "scripts/generate_capability_matrix.py")
        if problems:
            print("\n".join(problems))
            if not is_fresh(root):
                rendered = render()
                existing = (target.read_text(encoding="utf-8")
                            if target.exists() else "")
                diff = "\n".join(difflib.unified_diff(
                    existing.splitlines(), rendered.splitlines(),
                    fromfile=str(target), tofile="(regenerated)",
                    lineterm=""))
                print(diff[:3000])
            return 1
        print("capability-matrix: fresh")
        return 0
    target.write_text(render(), encoding="utf-8")
    write_readme_block(root)
    total, implemented, partial, not_planned = _counts()
    print(f"wrote {target}: {total} rows "
          f"({implemented} implemented, {partial} partial, "
          f"{not_planned} not planned)")
    print(f"wrote README coverage table ({total} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
