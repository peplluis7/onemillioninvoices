#!/usr/bin/env python3
"""Run publication-style annual CDG cohorts with causally separated bridges.

Manifest columns:
  cohort,issue_csv,bridge_csv,issue_start_date,issue_end_date,
  bridge_start_date,execution_end_date

Both CSVs use ``docs/data_schema.md``. The bridge may support execution, but only PMR
attributed to the issue cohort is reported against issue-year mass.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

from temporal_invoice_clearing.bridges import run_two_phase_cdg
from temporal_invoice_clearing.io import load_atomic_csv


def fstr(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def pct(value: Fraction, denominator: int) -> float:
    return float(value / denominator * 100)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cycle-bound", type=int, default=8)
    parser.add_argument("--skip-validation", action="store_true")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        manifest = list(csv.DictReader(handle))
    if not manifest:
        raise ValueError("manifest is empty")

    annual: list[dict[str, object]] = []
    for entry in manifest:
        cohort = entry["cohort"].strip()
        issue_path = Path(entry["issue_csv"])
        bridge_path = Path(entry["bridge_csv"])
        if not issue_path.is_absolute():
            issue_path = manifest_path.parent / issue_path
        if not bridge_path.is_absolute():
            bridge_path = manifest_path.parent / bridge_path
        result = run_two_phase_cdg(
            load_atomic_csv(issue_path),
            load_atomic_csv(bridge_path),
            cohort=cohort,
            issue_start=date.fromisoformat(entry["issue_start_date"]),
            issue_end=date.fromisoformat(entry["issue_end_date"]),
            bridge_start=date.fromisoformat(entry["bridge_start_date"]),
            execution_end=date.fromisoformat(entry["execution_end_date"]),
            cycle_bound=args.cycle_bound,
            validate=not args.skip_validation,
        )
        row = {
            "cohort": cohort,
            "issue_records": len(load_atomic_csv(issue_path)),
            "bridge_records": len(load_atomic_csv(bridge_path)),
            "issue_mass_cents": result.issue_mass_cents,
            "execution_end": result.execution_end.isoformat(),
            "cycle_dec31_pmr_cents": fstr(result.cycle.dec31_cohort_pmr_cents),
            "cycle_terminal_pmr_cents": fstr(result.cycle.terminal_cohort_pmr_cents),
            "cycle_terminal_pct": pct(result.cycle.terminal_cohort_pmr_cents, result.issue_mass_cents),
            "path_dec31_pmr_cents": fstr(result.path.dec31_cohort_pmr_cents),
            "path_terminal_pmr_cents": fstr(result.path.terminal_cohort_pmr_cents),
            "path_terminal_conservative_cents": fstr(result.path.terminal_conservative_cents),
            "path_terminal_liberal_cents": fstr(result.path.terminal_liberal_cents),
            "path_terminal_pct": pct(result.path.terminal_cohort_pmr_cents, result.issue_mass_cents),
            "terminal_advantage_pp": pct(
                result.path.terminal_cohort_pmr_cents - result.cycle.terminal_cohort_pmr_cents,
                result.issue_mass_cents,
            ),
            "cycle_operations": result.cycle.issue_result.operations + result.cycle.bridge_result.operations,
            "path_operations": result.path.issue_result.operations + result.path.bridge_result.operations,
        }
        annual.append(row)
        cohort_dir = output / cohort
        cohort_dir.mkdir(exist_ok=True)
        (cohort_dir / "summary.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
        with (cohort_dir / "curves.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "cycle_cohort_pmr_cents", "path_cohort_pmr_cents", "phase"])
            dates = [
                result.issue_start + timedelta(days=index)
                for index in range((result.issue_end - result.issue_start).days + 1)
            ] + [
                result.bridge_start + timedelta(days=index)
                for index in range((result.execution_end - result.bridge_start).days + 1)
            ]
            issue_days = (result.issue_end - result.issue_start).days + 1
            for index, (day, cycle_value, path_value) in enumerate(
                zip(dates, result.cycle.cohort_curve_cents, result.path.cohort_curve_cents)
            ):
                writer.writerow([
                    day.isoformat(),
                    fstr(cycle_value),
                    fstr(path_value),
                    "issue-year" if index < issue_days else "bridge",
                ])

    fieldnames = list(annual[0])
    with (output / "annual_uniform_bridges.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(annual)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
