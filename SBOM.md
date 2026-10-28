# SBOM — investment-analyst-toolkit (Phase 7, 28 Oct 2026)

## Runtime dependencies
None. The toolkit is standard-library only: no third-party package
is imported at runtime. That is the whole runtime SBOM.

## CI / developer dependencies (pinned, versions measured 28 Oct 2026)
| Tool | Version | Used for |
| --- | --- | --- |
| pytest | 9.2.0 | Test suite (124 tests) |
| ruff | 0.17.3 | Lint (`ruff check toolkit/ tests/`) |
| mypy | 2.4.0 | Type guardrail (`mypy toolkit/`, default settings — see mypy.ini) |
| coverage | 7.17.0 | Coverage gate (fail_under=80, measured 82%) |

CI installs exactly these pinned versions (see .github/workflows/test.yml).
No other dependency exists in this repo: no requirements.txt, no
compiled steps, no network fetch at build time.

## Supply-chain status (honest split: config vs enabled)
- Dependency pinning: DONE (this file + pinned CI installs).
- CodeQL workflow: config file added (.github/workflows/codeql.yml);
  GitHub-side enablement is TO VERIFY after deployment.
- Dependabot config: added (.github/dependabot.yml, weekly, pip +
  GitHub Actions); enablement is TO VERIFY after deployment.
- Secret scanning / push protection: GitHub settings, NOT a repo
  file — TO VERIFY after deployment; no secret has ever been
  committed to this repo (keys are env-only, see DATA_SOURCES.md).
- Reproducible builds: pure Python, no compiled steps, tests use
  fixed seeds where randomness exists — a rebuild from the same
  commit produces the same code. No reproducible-build attestation
  is claimed beyond that.
