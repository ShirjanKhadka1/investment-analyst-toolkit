# Benchmarks (Phase 7, 28 Oct 2026)

Measured once, on this VM (Python 3.12.3), so these numbers are a
baseline, not a guarantee — hardware differs, and a benchmark is
only useful against the same machine and the same code path.

| Benchmark | Measured |
| --- | --- |
| GBM paths, 1000 paths × 252 steps (seed 42) | 0.129 s |
| Monte Carlo option price, 10000 paths (seed 42) | 1.387 s, price 10.5042 |
| Full test suite, 124 tests | ~0.6 s (pytest, warm cache) |

No automated regression alert exists yet: these numbers are
recorded here so a future CI timing job can compare against
them. Do not quote them as performance claims for other machines.
