# onemillioninvoices
Atomic temporal invoice-graph algorithms for causal path-enabled multilateral clearing, with reproducibility materials for the 2012–2023 experiments.
Esteva de la Rosa, Peplluis (2026), “Atomic Common-Day Invoice Clearing: Pseudonymized Invoice Records and Reproducibility Data, 2012–2023”, Mendeley Data, V1, doi: 10.17632/28rbmvwsm9.1

# Temporal Invoice Clearing

Reference and reproduction code for atomic common-day path-enabled multilateral clearing,
complete-candidate bounded-cycle netting, mixed policies, and tractable full-information
optimization bounds.

The operative temporal rule is:

```text
c_uv(t; s)   = sum_i residual_i(s) * 1{issue_i <= t <= due_i}
delta_F(t;s) = min_{e in F} c_e(t;s)
delta_F*(s)  = max_t delta_F(t;s)
```

Every consumed amount remains traceable to one source invoice. Consumption is
deterministic: earliest due date, earliest issue date, then stable source UID.

## Publication experiment: rolling causal daily execution

The article uses causal daily greedy scheduling (CDG) as its annual operational regime.
Path and cycle policies receive the same issued and unexpired records on every day and are
ranked by actual payable-mass reduction.

Annual accounting is implemented as one causal state stream per method:

1. January-February records of year `y` enter during the terminal bridge of cohort `y-1`.
2. Any bridge consumption is permanent.
3. Only the residual amount of those records continues into March-December `y`.
4. Residual cohort-`y` records meet January-February arrivals of `y+1` in the terminal bridge.
5. After that bridge, older cohort residuals close; only residual `y+1` records continue.
6. Each UID is introduced once, and cumulative consumption is checked globally against face value.
7. PMR from mixed-year operations is attributed from the issue cohorts of consumed fragments.

The primary runner is `scripts/run_sequential_cohorts.py`; the design and non-reuse
invariant are documented in `docs/bridge_design.md`. The final cohort uses the observed
follow-up available in the supplied source; the paper reports an equal-horizon sensitivity.

Full-information continuous LPs are separate tractable optimization references. They are
not annual implementations.

## Repository layout

- `src/temporal_invoice_clearing/`: typed reference package.
- `scripts/run_sequential_cohorts.py`: rolling publication runner.
- `scripts/`: figures and robustness diagnostics.
- `analysis_results/`: non-identifying comparator and sensitivity analyses.
- `results/`: final annual summaries, non-reuse audit, validations, and daily curves.
- `paper/`: A4 LaTeX manuscript and figures.
- `tests/`: common-day, accounting, causality, optimization, attribution, replay, topology,
  I/O, and bridge non-reuse tests.
- `docs/`: schema, method, reproducibility guidance, and decentralized research agenda.
- `reproduction/frozen/`: source-specific programs retained for confidential-data reruns.

Raw invoices and reversible company mappings are not included.

## Installation and tests

```bash
python -m pip install -e '.[dev,optimization]'
pytest -q
```

Python 3.11 or newer is required.

## Rolling annual run

Prepare one normalized atomic CSV per issue year, named `2012.csv` through `2024.csv`:

```bash
python scripts/run_sequential_cohorts.py \
  --data-dir private/atomic_records \
  --output build/sequential \
  --start-year 2012 \
  --end-year 2023 \
  --last-bridge-end 2024-02-08 \
  --cycle-bound 8
```

The runner independently replays every phase and writes a global UID-consumption audit.
The empirical package distinguishes software reproducibility from independent empirical
replication: confidential commercial invoices are unavailable, while source code, derived
non-identifying outputs, test instances, figures, and replay logic are included.

## Agentic research agenda

No decentralized prototype is claimed. The path primitive has bounded evidence and
authorization scope: a fixed proposal uses two incident edges and at most three firms.
`docs/agentic_architecture.md` maps the deterministic common-day kernel to modern A2A,
MCP, AP2, autonomous-supply-chain, and private-constraint negotiation research. The
proposed experiment compares centralized CDG with asynchronous path, cycle, and hybrid
agent markets under partial participation, latency, reservations, consent, privacy, and
failure. Communication cost, acceptance, and welfare remain empirical questions.

The code is MIT licensed. Commercial invoice data remain subject to their original
confidentiality terms.

## Data

The authoritative research-data deposit is available from Mendeley Data:

DOI: 10.17632/28rbmvwsm9.1


## Reproduction

See REPRODUCING.md for complete instructions.
