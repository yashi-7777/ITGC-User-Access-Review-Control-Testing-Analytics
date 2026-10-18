# Architecture

```
            ┌────────────────────────── itgc-uar  (CLI) ──────────────────────────┐
            │  generate            run                                all          │
            └────┬─────────────────┬──────────────────────────────────┬────────────┘
                 │                 │                                  │
        datagen.py          dataset.py  ──►  dataquality.py (DQ01-DQ10, stops on ERROR)
   (seeded SIMULATED data         │
    + ground_truth.csv)           ▼
                         ┌──────────────── engine.py ────────────────┐
                         │ for each control T1..T6:                  │
                         │   SQL  (sql/Tn_*.sql on in-memory SQLite) │
                         │   pandas twin (controls.py)               │
                         │   reconcile.py  → MATCH / MISMATCH        │
                         │   classify → type, severity, evidence     │
                         │   scoring.py → score, priority, due date  │
                         └───────────────┬───────────────────────────┘
                                         ▼
        reporting/  csvs.py · excel.py · dashboard.py(+template) · run_manifest.json
                                         ▼
                    validate.py  (SIMULATED data only: detections vs ground truth)
```

## Design decisions

* **Two independent implementations.** SQL is the reported result (what an analyst would run on a data warehouse); pandas
  is the cross-check. They share no detection code. The reconciliation compares the key set, every attribute and the
  population count, and a mismatch makes the CLI exit with code 2.
* **Configuration, not constants.** Thresholds, tolerable rates, scores, bands and SLAs live in YAML, are validated
  (unknown keys, wrong types and inconsistent bands are rejected) and are fingerprinted into every run manifest.
* **Data quality before testing.** An audit result is only as good as the data (IPE). Ten checks run first; errors stop
  the run unless `--force` is given.
* **Traceable evidence.** Each finding keeps the source table, row IDs and the rule that fired, in the register, the
  Excel workpaper and the dashboard drill-down.
* **Ground truth for simulated data.** The generator records what it planted and what it built as a boundary "decoy".
  `validate.py` then measures recall and false positives. The control tests never read this file.
* **Reproducibility.** Seeded generation; the baseline profile reproduces the original v1 CSVs byte-for-byte (a test
  checks the SHA-256); `run_manifest.json` stores input hashes, config fingerprint and library versions.

## Module map

| Module | Responsibility |
|--------|----------------|
| `config.py` | load / merge / validate YAML into an immutable `Config` |
| `dataset.py` | read the 4 CSVs as text, hash them, build the SQLite copy |
| `dataquality.py` | schema, key, value, date, consistency and referential checks |
| `datagen.py` | `baseline` (400 employees) and `enterprise` (2,000 + decoys) profiles |
| `controls.py` | per-control SQL runner, pandas twin, population, classification |
| `reconcile.py` | SQL-vs-pandas comparison |
| `scoring.py` | score, priority, due date, explanation text |
| `engine.py` | orchestration, register and summary tables |
| `reporting/` | CSV, Excel, dashboard writers |
| `validate.py` | detections vs generator ground truth |
| `cli.py` | `generate`, `run`, `all`; exit codes 0 / 1 / 2 / 3 |
