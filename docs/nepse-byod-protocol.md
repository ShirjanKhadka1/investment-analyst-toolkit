# NEPSE bring-your-own-data kit protocol (Round E, E.3) — PRE-REGISTERED

Written 2026-11-15, **before any kit run was made**. This
protocol fixes the CSV schemas, the importer checks and the
one-command harness run in advance. The kit evaluates only
data the user supplies; it fetches nothing, and it names no
NEPSE data provider, API or download page anywhere — the data
is "user-supplied", full stop.

## Data source — deliberately undocumented

The kit takes CSV files the user supplies. No provider, API,
download page or feed is named, linked or described in any
doc, comment or test in this round. Any doc that needs to say
where the data came from says "user-supplied" and nothing
more. The existing loader discipline stands: a source must be
named by the caller (a free-text label, e.g. "NEPSE published
data, session of 2026-11-13, transcribed by hand"), and the
label travels with any published result.

## Schemas (what the importer accepts)

### NEPSE price CSV — multi-symbol

Columns (exactly the `toolkit/nepse_loader.py` format):
`symbol,sector,date,close,high,low,volume`.

- `symbol`: non-empty. `sector`: free text (may be empty only
  when every row for the symbol carries the same empty value —
  the loader keeps the first non-empty sector seen).
- `date`: ISO `YYYY-MM-DD`, strictly increasing and unique per
  symbol (the loader enforces this; the kit additionally
  checks each symbol's dates in file order before the loader
  runs, so a shuffled file is refused with its own error and
  line number — see "Importer checks").
- `close` > 0, `high` >= `low`, all numerics finite.
- `volume`: a number (>= 0).

Single-symbol form (no `symbol`/`sector` columns):
`date,close,high,low,volume`, same value rules, dates strictly
increasing and unique.

### NEPSE floorsheet CSV

Columns (exactly the `toolkit/nepse_floorsheet.py` format):
`date,broker,symbol,side,quantity,price`.

- `date`: ISO `YYYY-MM-DD`. `broker`, `symbol`: non-empty.
- `side`: `buy` or `sell`. `quantity`: a positive integer.
- `price`: a positive finite number.

## Importer checks (LOUD refusal — never silently skip)

The kit (`toolkit/nepse_byod.py`) validates before any
evaluation runs, and refuses with a named error on the first
violation:

1. Missing file, or a file with no data rows.
2. Missing or misspelled required columns (the error names
   the expected columns).
3. A bad date (not ISO `YYYY-MM-DD`), naming the line.
4. Non-monotonic or duplicate dates per symbol in the price
   CSV (the existing loader checks sortedness per symbol; the
   kit additionally checks each symbol's dates in file order
   before the loader runs, so a shuffled file is refused with
   the kit's own error and the line number).
5. A missing value in any required cell (empty string counts
   as missing — the loader's float parse already refuses
   empties; the kit checks empties explicitly so the error
   names the field, not the parse).
6. A non-positive close, `high < low`, a non-finite number, a
   non-positive quantity, a bad `side`, or a non-positive
   price — each naming the line.
7. No source label (`--source` empty) — unsourced data is
   refused, not loaded.
8. Too few symbols or bars for the requested evaluation: the
   harness and the direction models need at least 260 closes
   per symbol (`train_size` 252 plus a test fold); ranking
   needs at least 150 closes per symbol (126-bar momentum
   formation plus a 21-bar forward window) and at least 10
   symbols (deciles); the event study needs at least one
   event. A symbol too short for an evaluation is excluded
   from that evaluation and recorded; an evaluation with no
   qualifying symbol refuses instead of reporting on nothing.

`#` comment lines are skipped in floorsheet and event CSVs
(the bundled demo files carry a SYNTHETIC header comment);
the price CSV follows the loader's existing behaviour.

## One-command harness run

```text
python3 scripts/nepse_byod_run.py --prices <price.csv> \
    --floorsheet <floorsheet.csv> --source "<label>" \
    [--events <events.csv>] [--trade-value 100000] [--out <dir>]
```

The script:

1. Loads and validates the price CSV (multi-symbol; a
   single-symbol file is accepted with `--single-symbol`),
   the floorsheet CSV and, when given, the event CSV
   (`symbol,event_date,event_type[,detail]`, event types
   dividend / bonus / right / book_closure / earnings, per
   `toolkit/event_study.load_event_csv`).
2. Runs, on the user-supplied prices only:
   - the forecast harness (`run_full_evaluation`, train_size
     252, purge 5, embargo 5) per symbol with enough bars;
   - the direction models (`walk_forward_direction`,
     train_size 252) per symbol with enough closes;
   - momentum-only cross-sectional ranking
     (`rank_universe` on momentum components) with 21-day
     forward decile spreads net of the NEPSE cost schedule at
     `--trade-value` (default NPR 100,000 per name), verdict
     by `spread_verdict` — no fundamentals are involved, so
     value/quality components are absent by design and the
     report says so;
   - the event study (`run_event_study`) when events are
     supplied, market model vs the first symbol's series only
     when dates align, otherwise mean-adjusted;
   - the floorsheet analytics (`broker_flow`, `top_flow`,
     `concentration`, `symbol_flow`) per symbol in the
     floorsheet.
3. Writes a JSON report and a Markdown report to `--out`
   (default `reports/nepse-byod/`), each carrying the source
   label, the file names, the row counts, the per-symbol bar
   counts and date ranges, and every result above. The reports
   state plainly that the data is user-supplied and that the
   harness/direction verdicts are computed on it — no claim
   about the data's provenance is made.

## Tests (no network)

`tests/test_nepse_byod.py` runs the kit end-to-end on bundled
SYNTHETIC NEPSE-shaped fixture CSVs
(`tests/fixtures/nepse_byod_prices_SYNTHETIC.csv`,
`tests/fixtures/nepse_byod_floorsheet_SYNTHETIC.csv`,
`tests/fixtures/nepse_byod_events_SYNTHETIC.csv`): a valid
run produces a well-formed report; each refusal in "Importer
checks" is asserted with the exact error; the floorsheet
analytics match hand-computed values from the fixture. The
fixtures are synthetic by construction (geometric random walk
from a fixed seed, invented brokers) and labelled SYNTHETIC in
the files — they prove the kit runs, not that any real NEPSE
pattern exists.

## What this kit does not do

- It does not fetch, scrape or name any NEPSE data source.
- It does not add a live NEPSE feed, an execution path, a new
  dependency (stdlib only) or a model. The evaluations are the
  existing harness / direction / ranking / event-study code,
  run on user-supplied rows.
- It does not touch any track-record file.
- The floorsheet analytics stay descriptive (who bought/sold
  what): a broker buying heavily is a fact about the supplied
  rows, not a recommendation.
