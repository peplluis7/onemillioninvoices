# Method

## Exact fixed-operation common-day capacity

For each directed edge `(u, v)` and day `t`, the state stores

```text
c_uv(t; s) = sum_i residual_i(s) * 1{issue_i <= t <= due_i}.
```

For a fixed path or circuit `F`, the executable amount is

```text
delta_F(t; s) = min_{e in F} c_e(t; s)
delta_F*(s)   = max_t delta_F(t; s).
```

The earliest maximizing day is used. Source records on every selected edge are consumed
in earliest-due, earliest-issue, stable-UID order. A fragment is never consumed outside
its original issue-due interval.

## Accounting and candidate scores

A cycle of length `k` and matched amount `q` has PMR `kq` and creates no instruction.

A non-bilateral path `A -> B -> C` consumes `q` from both invoice legs, creates an
`A -> C` instruction of `q`, and has PMR `q`. A reciprocal path with `A = C` creates no
instruction and has PMR `2q`.

Both policies rank candidates by actual PMR. The path policy visits intermediaries in
stable identifier order and reaches a local fixed point at each intermediary. The cycle
policy ranks the complete bounded-circuit inventory by `kq`.

## Causal daily annual execution

CDG is the only annual scheduling regime in the paper. The annual series is one rolling
causal state per method. January-February records can first be used during the preceding
cohort bridge; any consumed amount is permanently removed and only the residual amount
continues into the rest of the record's issue year. Every UID is introduced once, and a
global ledger verifies cumulative consumption against original face value. Mixed-year PMR
is attributed from source-fragment issue cohorts.

See `bridge_design.md`. The publication runner is `scripts/run_sequential_cohorts.py`.

## Full-information reference

Continuous source-record allocation LPs are solved only on tractable instances. They are
optimization references, not annual implementations. Every feasible CDG schedule can be
replayed with full information, so the corresponding optimum must weakly dominate CDG.

Legacy full-arrival greedy functions remain in the package for historical regression tests,
but they are not part of the final annual results.

## Legal scope

The numerical PMR interpretation assumes consented discharge after performance of the
redirected payment. Instruction-only and novation modes require separate legal and credit
analysis even though the accounting conservation identity is unchanged.
