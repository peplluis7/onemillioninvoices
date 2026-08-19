#!/usr/bin/env python3
"""Run independently initialized annual cohorts from a normalized-data manifest.

The script never pools records from different manifest rows. A row may specify a short
execution bridge by setting ``execution_end_date`` after ``issue_end_date``. Bridge
records must carry cohort labels; terminal PMR and daily AUC are then attributed from the
fragment log rather than from the aggregate terminal state.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from dataclasses import replace
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

from temporal_invoice_clearing.algorithms import (
    cycle_daily,
    cycle_offline,
    path_daily,
    path_offline,
)
from temporal_invoice_clearing.attribution import (
    cohort_daily_curve_attribution,
    cohort_pmr_attribution,
)
from temporal_invoice_clearing.cycles import enumerate_cycles
from temporal_invoice_clearing.io import load_atomic_csv
from temporal_invoice_clearing.metrics import topology_metrics
from temporal_invoice_clearing.replay import replay_validate
from temporal_invoice_clearing.state import TemporalState


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cycle-bound", type=int, default=8)
    parser.add_argument(
        "--write-logs",
        action="store_true",
        help="persist compressed fragment logs for every annual run",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="skip independent replay; not recommended for publication runs",
    )
    return parser.parse_args()


def fraction_to_string(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def fraction_sum(values: Iterable[Fraction]) -> Fraction:
    return sum(values, start=Fraction(0))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))


def write_daily_curves(
    path: Path,
    start_date: date,
    cycle_curve: list[Fraction],
    path_curve: list[Fraction],
) -> None:
    if len(cycle_curve) != len(path_curve):
        raise ValueError("cycle and path attributed curves must have equal length")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "date",
                "cycle_pmr_cents",
                "path_pmr_cents",
                "advantage_cents",
            ]
        )
        for index, (cycle_value, path_value) in enumerate(zip(cycle_curve, path_curve)):
            writer.writerow(
                [
                    (start_date + timedelta(days=index)).isoformat(),
                    fraction_to_string(cycle_value),
                    fraction_to_string(path_value),
                    fraction_to_string(path_value - cycle_value),
                ]
            )


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest).resolve()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []

    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        manifest = list(csv.DictReader(handle))
    if not manifest:
        raise ValueError("manifest contains no cohorts")

    for entry in manifest:
        cohort = entry["cohort"].strip()
        issue_start = date.fromisoformat(entry["issue_start_date"])
        issue_end = date.fromisoformat(entry["issue_end_date"])
        execution_end = date.fromisoformat(
            entry.get("execution_end_date") or entry["issue_end_date"]
        )
        input_path = Path(entry["input_csv"])
        if not input_path.is_absolute():
            input_path = manifest_path.parent / input_path
        records = load_atomic_csv(input_path)
        records = [
            record if record.cohort else replace(record, cohort=str(record.issue_date.year))
            for record in records
        ]
        state = TemporalState(records, issue_start, execution_end)
        cycles = enumerate_cycles(state, length_bound=args.cycle_bound)
        results = {
            "offline_cycle": cycle_offline(state, cycles, keep_log=True),
            "offline_path": path_offline(state, keep_log=True),
            "daily_cycle": cycle_daily(state, cycles, keep_log=True),
            "daily_path": path_daily(state, keep_log=True),
        }

        validations: dict[str, Any] = {}
        if not args.skip_validation:
            for name, result in results.items():
                validations[name] = replay_validate(records, state, result)
                if not validations[name]["all_pass"]:
                    raise RuntimeError(f"replay failed for {cohort} {name}")

        has_bridge = execution_end > issue_end
        if has_bridge:
            attributed = {
                name: cohort_pmr_attribution(records, result, cohort)["symmetric_cents"]
                for name, result in results.items()
            }
            cycle_curve = cohort_daily_curve_attribution(
                records, results["daily_cycle"], cohort
            )
            path_curve = cohort_daily_curve_attribution(
                records, results["daily_path"], cohort
            )
        else:
            attributed = {
                name: Fraction(result.pmr_cents) for name, result in results.items()
            }
            cycle_curve = [
                Fraction(value) for value in results["daily_cycle"].daily_curve_cents
            ]
            path_curve = [
                Fraction(value) for value in results["daily_path"].daily_curve_cents
            ]

        cohort_records = [record for record in records if record.cohort == cohort]
        denominator = sum(record.amount_cents for record in cohort_records)
        if denominator == 0:
            cohort_records = [
                record for record in records if issue_start <= record.issue_date <= issue_end
            ]
            denominator = sum(record.amount_cents for record in cohort_records)
        if denominator <= 0:
            raise ValueError(f"cohort {cohort} has no denominator records")

        topology = topology_metrics(cohort_records)
        summary = {
            "cohort": cohort,
            "input_name": input_path.name,
            "records": len(cohort_records),
            "mass_cents": denominator,
            "firms": topology["firms"],
            "edges": topology["edges"],
            "cycles": len(cycles),
            "offline_cycle_pmr_cents": fraction_to_string(attributed["offline_cycle"]),
            "offline_path_pmr_cents": fraction_to_string(attributed["offline_path"]),
            "daily_cycle_pmr_cents": fraction_to_string(attributed["daily_cycle"]),
            "daily_path_pmr_cents": fraction_to_string(attributed["daily_path"]),
            "daily_cycle_auc_cent_days": fraction_to_string(fraction_sum(cycle_curve)),
            "daily_path_auc_cent_days": fraction_to_string(fraction_sum(path_curve)),
            "daily_advantage_auc_cent_days": fraction_to_string(
                fraction_sum(path_value - cycle_value for cycle_value, path_value in zip(cycle_curve, path_curve))
            ),
            "offline_cycle_operations": results["offline_cycle"].operations,
            "offline_path_operations": results["offline_path"].operations,
            "daily_cycle_operations": results["daily_cycle"].operations,
            "daily_path_operations": results["daily_path"].operations,
        }
        rows.append(summary)

        cohort_dir = output / cohort
        cohort_dir.mkdir(exist_ok=True)
        write_json(cohort_dir / "summary.json", summary)
        write_json(
            cohort_dir / "run_summaries.json",
            {name: result.summary() for name, result in results.items()},
        )
        if validations:
            write_json(cohort_dir / "validation.json", validations)
        write_daily_curves(
            cohort_dir / "daily_curves.csv", issue_start, cycle_curve, path_curve
        )
        if args.write_logs:
            for name, result in results.items():
                write_log(
                    cohort_dir / f"{name}_run.json.gz",
                    result.to_dict(include_curve=True, include_log=True),
                )

    fieldnames = list(rows[0])
    with (output / "annual_series.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
