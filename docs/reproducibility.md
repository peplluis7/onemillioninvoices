# Reproducibility workflow

1. Normalize each issue year into the atomic CSV schema in `docs/data_schema.md`.
2. Maintain stable entity identifiers across adjacent years; unresolved cross-source entities
   remain distinct rather than being guessed.
3. Provide one file per year, including the next-year file needed for the final bridge.
4. Run `scripts/run_sequential_cohorts.py` for cycle and path methods.
5. Introduce every UID exactly once and accumulate physical consumption globally.
6. Carry only residual January-February amounts into the remainder of their own issue year.
7. Attribute mixed-year PMR from source-fragment cohort labels.
8. Independently replay all within-year and bridge phases.
9. Persist terminal metrics, daily curves, cycle inventories, and boundary non-reuse audits.

```bash
python scripts/run_sequential_cohorts.py \
  --data-dir private/atomic_records \
  --output build/sequential \
  --start-year 2012 \
  --end-year 2023 \
  --last-bridge-end 2024-02-08 \
  --cycle-bound 8
```

Private source-specific preparation and entity linkage are documented by the versioned
programs under `reproduction/frozen`. Raw identifiers are intentionally absent from the
public repository. Derived, non-identifying summaries and curves are provided under
`results/`.

Before release, inspect staged files for machine-local paths, raw identifiers, reversible
mappings, credentials, and identifiable logs.
