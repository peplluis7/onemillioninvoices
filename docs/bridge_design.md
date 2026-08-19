# Sequential annual execution and non-reuse

The publication experiment defines annual results by invoice issue cohort while maintaining
one physical causal state stream per clearing method.

For cohort `y`:

1. January-February records of year `y` have already entered during cohort `y-1`'s bridge.
2. Any amount consumed in that bridge is permanently removed.
3. Only residual January-February balance continues into the March-December phase of `y`.
4. Residual `y` records are combined with actual January-February `y+1` arrivals in the
   terminal bridge.
5. After the bridge, unresolved `y` records close for cohort accounting and only residual
   `y+1` records continue.

For every bridge record `i` and method `m`:

```text
original_amount_i = bridge_consumption_i,m + residual_carried_i,m
```

Each UID is introduced once. A global consumption ledger rejects duplicate introduction
or cumulative consumption above the original amount.

The denominator for cohort `y` is all original mass issued in `y`, including its
January-February records. Physical PMR from a mixed-year operation is partitioned by the
issue cohorts of the consumed source fragments. For a non-bilateral path, the primary
rule splits its one unit of PMR symmetrically between incoming and outgoing fragments;
conservative and liberal bounds are also reported.

The public runner is:

```bash
python scripts/run_sequential_cohorts.py \
  --data-dir private/atomic_records \
  --output build/sequential \
  --start-year 2012 \
  --end-year 2023 \
  --last-bridge-end 2024-02-08 \
  --cycle-bound 8
```

Cohorts 2012-2022 use complete January-February bridges. The 2023 cohort is included with
the available 1 January-8 February 2024 source; an equal-horizon 2012-2022 subset is
reported separately.
