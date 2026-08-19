#!/usr/bin/env python3
"""Run the publication annual sequence without bridge-record reuse."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

from temporal_invoice_clearing.rolling import run_sequential_annual_cdg


def fstr(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def ffloat(value: Fraction) -> float:
    return value.numerator / value.denominator


def pct(value: Fraction, denominator: int) -> float:
    return ffloat(value) / denominator * 100


def load_paths(data_dir: Path, start_year: int, end_year: int) -> dict[int, Path]:
    return {year: data_dir / f"{year}.csv" for year in range(start_year, end_year + 2)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cycle-bound", type=int, default=8)
    parser.add_argument("--start-year", type=int, default=2012)
    parser.add_argument("--end-year", type=int, default=2023)
    parser.add_argument("--last-bridge-end", type=date.fromisoformat, default=date(2024, 2, 8))
    parser.add_argument("--skip-validation", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    paths = load_paths(args.data_dir, args.start_year, args.end_year)

    def progress(message: str) -> None:
        print(message, flush=True)

    cycle = run_sequential_annual_cdg(
        paths,
        start_year=args.start_year,
        end_year=args.end_year,
        last_bridge_end=args.last_bridge_end,
        method="cycle",
        cycle_bound=args.cycle_bound,
        validate=not args.skip_validation,
        progress=progress,
    )
    path = run_sequential_annual_cdg(
        paths,
        start_year=args.start_year,
        end_year=args.end_year,
        last_bridge_end=args.last_bridge_end,
        method="path",
        cycle_bound=args.cycle_bound,
        validate=not args.skip_validation,
        progress=progress,
    )
    cycle_by_year = {row.year: row for row in cycle.cohorts}
    path_by_year = {row.year: row for row in path.cohorts}

    rows: list[dict[str, object]] = []
    for year in range(args.start_year, args.end_year + 1):
        c = cycle_by_year[year]
        p = path_by_year[year]
        if c.issue_mass_cents != p.issue_mass_cents:
            raise AssertionError("method denominators differ")
        mass = c.issue_mass_cents
        row = {
            "year": year,
            "issue_mass_cents": mass,
            "issue_mass_bn": mass / 1e11,
            "bridge_end": c.bridge_end.isoformat(),
            "bridge_days": c.bridge_days,
            "bridge_complete_jan_feb": c.bridge_complete_jan_feb,
            "cycle_opening_pmr_cents": fstr(c.opening_pmr_cents),
            "cycle_dec31_pmr_cents": fstr(c.dec31_pmr_cents),
            "cycle_dec31_pct": pct(c.dec31_pmr_cents, mass),
            "cycle_terminal_pmr_cents": fstr(c.terminal_pmr_cents),
            "cycle_terminal_pct": pct(c.terminal_pmr_cents, mass),
            "cycle_terminal_bridge_increment_cents": fstr(c.terminal_bridge_increment_cents),
            "path_opening_pmr_cents": fstr(p.opening_pmr_cents),
            "path_dec31_pmr_cents": fstr(p.dec31_pmr_cents),
            "path_dec31_pct": pct(p.dec31_pmr_cents, mass),
            "path_terminal_pmr_cents": fstr(p.terminal_pmr_cents),
            "path_terminal_pct": pct(p.terminal_pmr_cents, mass),
            "path_terminal_conservative_cents": fstr(p.terminal_conservative_cents),
            "path_terminal_liberal_cents": fstr(p.terminal_liberal_cents),
            "path_terminal_bridge_increment_cents": fstr(p.terminal_bridge_increment_cents),
            "terminal_advantage_cents": fstr(p.terminal_pmr_cents - c.terminal_pmr_cents),
            "terminal_advantage_pp": pct(p.terminal_pmr_cents - c.terminal_pmr_cents, mass),
            "cycle_main_operations": c.main_operations,
            "cycle_terminal_bridge_operations": c.terminal_bridge_operations,
            "path_main_operations": p.main_operations,
            "path_terminal_bridge_operations": p.terminal_bridge_operations,
            "cycle_main_runtime_seconds": c.main_runtime_seconds,
            "path_main_runtime_seconds": p.main_runtime_seconds,
            "cycle_main_initially_feasible_cycles": c.main_initially_feasible_cycles,
            "cycle_main_inventory": json.dumps(c.main_cycle_inventory or {}, sort_keys=True),
            "bridge_new_records": c.bridge_new_records,
            "bridge_new_mass_cents": c.bridge_new_mass_cents,
            "cycle_bridge_new_consumed_mass_cents": c.bridge_new_consumed_mass_cents,
            "cycle_bridge_residual_carried_records": c.bridge_residual_carried_records,
            "cycle_bridge_residual_carried_mass_cents": c.bridge_residual_carried_mass_cents,
            "cycle_bridge_fully_consumed_records": c.bridge_fully_consumed_records,
            "path_bridge_new_consumed_mass_cents": p.bridge_new_consumed_mass_cents,
            "path_bridge_residual_carried_records": p.bridge_residual_carried_records,
            "path_bridge_residual_carried_mass_cents": p.bridge_residual_carried_mass_cents,
            "path_bridge_fully_consumed_records": p.bridge_fully_consumed_records,
        }
        rows.append(row)

        curve_path = args.output / f"annual_{year}_sequential_bridge_curves.csv"
        start = date(year, 1, 1)
        dates = [start + timedelta(days=index) for index in range(len(c.cumulative_curve_cents))]
        with curve_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "cycle_cohort_pmr_cents", "path_cohort_pmr_cents", "advantage_cents"])
            for day, cv, pv in zip(dates, c.cumulative_curve_cents, p.cumulative_curve_cents):
                writer.writerow([day.isoformat(), fstr(cv), fstr(pv), fstr(pv - cv)])

    with (args.output / "annual_sequential_bridge_results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)

    audit_fields = [
        "year", "bridge_end", "bridge_new_records", "bridge_new_mass_cents",
        "cycle_bridge_new_consumed_mass_cents", "cycle_bridge_residual_carried_records",
        "cycle_bridge_residual_carried_mass_cents", "cycle_bridge_fully_consumed_records",
        "path_bridge_new_consumed_mass_cents", "path_bridge_residual_carried_records",
        "path_bridge_residual_carried_mass_cents", "path_bridge_fully_consumed_records",
        "reintroduced_at_full_face_value",
    ]
    with (args.output / "boundary_nonreuse_audit.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=audit_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, False if key == "reintroduced_at_full_face_value" else "") for key in audit_fields})

    total_mass = sum(int(row["issue_mass_cents"]) for row in rows)
    cycle_total = sum(cycle_by_year[y].terminal_pmr_cents for y in range(args.start_year, args.end_year + 1))
    path_total = sum(path_by_year[y].terminal_pmr_cents for y in range(args.start_year, args.end_year + 1))
    complete_years = [y for y in range(args.start_year, args.end_year + 1) if cycle_by_year[y].bridge_complete_jan_feb]
    complete_mass = sum(cycle_by_year[y].issue_mass_cents for y in complete_years)
    complete_cycle = sum(cycle_by_year[y].terminal_pmr_cents for y in complete_years)
    complete_path = sum(path_by_year[y].terminal_pmr_cents for y in complete_years)
    summary = {
        "design": "single causal stream per method; bridge invoices introduced once; residual bridge amounts carry into the next cohort",
        "years": f"{args.start_year}-{args.end_year}",
        "issue_mass_cents": total_mass,
        "cycle_terminal_pmr_cents": fstr(cycle_total),
        "cycle_terminal_pct": pct(cycle_total, total_mass),
        "path_terminal_pmr_cents": fstr(path_total),
        "path_terminal_pct": pct(path_total, total_mass),
        "advantage_cents": fstr(path_total-cycle_total),
        "advantage_pp": pct(path_total-cycle_total, total_mass),
        "complete_bridge_subset_years": f"{min(complete_years)}-{max(complete_years)}" if complete_years else "none",
        "complete_bridge_subset_mass_cents": complete_mass,
        "complete_bridge_subset_cycle_pct": pct(complete_cycle, complete_mass),
        "complete_bridge_subset_path_pct": pct(complete_path, complete_mass),
        "complete_bridge_subset_advantage_pp": pct(complete_path-complete_cycle, complete_mass),
        "path_wins": sum(path_by_year[y].terminal_pmr_cents > cycle_by_year[y].terminal_pmr_cents for y in range(args.start_year, args.end_year + 1)),
        "cycle_wins": sum(path_by_year[y].terminal_pmr_cents < cycle_by_year[y].terminal_pmr_cents for y in range(args.start_year, args.end_year + 1)),
        "ties": sum(path_by_year[y].terminal_pmr_cents == cycle_by_year[y].terminal_pmr_cents for y in range(args.start_year, args.end_year + 1)),
        "cycle_calendar_operations": cycle.calendar_operations,
        "path_calendar_operations": path.calendar_operations,
        "cycle_total_runtime_seconds": cycle.total_runtime_seconds,
        "path_total_runtime_seconds": path.total_runtime_seconds,
        "cycle_nonreuse_audit": cycle.global_consumption_audit,
        "path_nonreuse_audit": path.global_consumption_audit,
        "cycle_phase_replay_all_pass": all(v.get("all_pass", False) for v in cycle.phase_validations),
        "path_phase_replay_all_pass": all(v.get("all_pass", False) for v in path.phase_validations),
    }
    (args.output / "aggregate_sequential_bridge_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (args.output / "cycle_phase_validations.json").write_text(json.dumps(cycle.phase_validations, indent=2), encoding="utf-8")
    (args.output / "path_phase_validations.json").write_text(json.dumps(path.phase_validations, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
