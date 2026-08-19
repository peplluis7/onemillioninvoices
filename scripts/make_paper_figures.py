#!/usr/bin/env python3
"""Regenerate annual and representative cumulative PMR figures.

The utility accepts the public annual summary and one or more daily-curve CSV files.
Historical curve files use two schemas:

* ``cycle_pmr_cents`` / ``path_pmr_cents`` for ordinary annual runs;
* ``cycle_2023_symmetric_eur`` / ``path_2023_symmetric_eur`` for the
  issue-year-attributed 2023 carryover experiment.

Both are normalized to integer cents before plotting. Representative cumulative plots
are expressed as a percentage of the corresponding issue-year invoice mass, matching
the manuscript figures.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from pathlib import Path

import matplotlib.pyplot as plt


@dataclass(frozen=True, slots=True)
class Curve:
    start: date
    cycle_cents: list[int]
    path_cents: list[int]


def read_annual(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"annual summary is empty: {path}")
    return rows


def annual_plot(rows: list[dict[str, str]], output: Path, regime: str) -> None:
    """Plot one annual scheduling regime.

    The public CSV retains the historical field prefixes ``offline`` and ``daily`` for
    backward compatibility.  In the paper these are FASG and CDG, respectively.
    """
    years = [int(row["year"]) for row in rows]
    cycle = [float(row[f"{regime}_cycle_pct"]) for row in rows]
    path = [float(row[f"{regime}_path_pct"]) for row in rows]
    figure, axis = plt.subplots(figsize=(8.2, 4.8))
    axis.plot(years, cycle, marker="o", label="Cycle L=8")
    axis.plot(years, path, marker="o", label="Path-enabled")
    axis.set_xlabel("Issue year")
    axis.set_ylabel("Payable-mass reduction (%)")
    display = {"offline": "FASG", "daily": "CDG"}.get(regime, regime)
    axis.set_title(f"Annual {display} payable-mass reduction")
    axis.grid(True, linewidth=0.4, alpha=0.45)
    axis.legend()
    axis.set_xticks(years)
    figure.tight_layout()
    figure.savefig(output, bbox_inches="tight")
    plt.close(figure)


def _field_pair(fieldnames: list[str]) -> tuple[str, str, float]:
    """Return cycle field, path field, and multiplier to integer cents."""
    fields = set(fieldnames)
    candidates = [
        ("cycle_pmr_cents", "path_pmr_cents", 1.0),
        # The 2023 paper curve excludes 2024-issued PMR by symmetric issue-year
        # attribution. Prefer it to the un-attributed total columns when present.
        ("cycle_2023_symmetric_eur", "path_2023_symmetric_eur", 100.0),
        ("cycle_total_cents", "path_total_cents", 1.0),
    ]
    for cycle_key, path_key, multiplier in candidates:
        if cycle_key in fields and path_key in fields:
            return cycle_key, path_key, multiplier
    raise ValueError(
        "daily curve must contain either cycle_pmr_cents/path_pmr_cents, "
        "cycle_2023_symmetric_eur/path_2023_symmetric_eur, or "
        "cycle_total_cents/path_total_cents"
    )


def read_curve(path: Path) -> Curve:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    if not rows:
        raise ValueError(f"daily curve is empty: {path}")
    cycle_key, path_key, multiplier = _field_pair(fieldnames)
    start = date.fromisoformat(rows[0]["date"])
    def numeric(value: str) -> float:
        try:
            return float(Fraction(value))
        except (ValueError, ZeroDivisionError):
            return float(value)

    cycle = [int(round(numeric(row[cycle_key]) * multiplier)) for row in rows]
    path_values = [int(round(numeric(row[path_key]) * multiplier)) for row in rows]
    return Curve(start=start, cycle_cents=cycle, path_cents=path_values)


def plot_cumulative_percent(
    output: Path,
    curve: Curve,
    mass_cents: int,
    *,
    title: str,
    bridge_start: date | None = None,
) -> None:
    if mass_cents <= 0:
        raise ValueError("issue-year mass must be positive")
    if len(curve.cycle_cents) != len(curve.path_cents):
        raise ValueError("cycle and path curve lengths must match")

    from datetime import timedelta

    dates = [curve.start + timedelta(days=index) for index in range(len(curve.path_cents))]
    cycle_pct = [100.0 * value / mass_cents for value in curve.cycle_cents]
    path_pct = [100.0 * value / mass_cents for value in curve.path_cents]

    figure, axis = plt.subplots(figsize=(8.2, 4.8))
    axis.plot(dates, cycle_pct, label="Cycle L=8")
    axis.plot(dates, path_pct, label="Path-enabled")
    if bridge_start is not None:
        axis.axvline(bridge_start, linestyle="--", linewidth=1.0, label="Bridge start")
    axis.set_title(title)
    axis.set_xlabel("Date")
    axis.set_ylabel("Cumulative PMR (% of issue-year mass)")
    axis.grid(True, linewidth=0.4, alpha=0.45)
    axis.legend()
    figure.autofmt_xdate()
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, bbox_inches="tight")
    plt.close(figure)


def parse_mapping(values: list[str], option: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{option} expects YEAR=VALUE, received {value!r}")
        key, item = value.split("=", 1)
        parsed[key.strip()] = item.strip()
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annual", required=True, help="annual summary CSV")
    parser.add_argument("--output", required=True, help="output directory")
    parser.add_argument("--curve", action="append", default=[], help="YEAR=path.csv")
    parser.add_argument(
        "--bridge",
        action="append",
        default=[],
        help="optional YEAR=YYYY-MM-DD bridge boundary for a cumulative curve",
    )
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    rows = read_annual(Path(args.annual))
    annual_by_year = {row["year"]: row for row in rows}
    bridge_by_year = parse_mapping(args.bridge, "--bridge")

    annual_plot(rows, output / "annual_fasg_pmr.pdf", "offline")
    annual_plot(rows, output / "annual_cdg_pmr.pdf", "daily")

    for year, file_name in parse_mapping(args.curve, "--curve").items():
        if year not in annual_by_year:
            raise ValueError(f"curve year {year} is missing from annual summary")
        mass_cents = int(round(float(annual_by_year[year]["mass_bn"]) * 1e9 * 100))
        bridge_start = (
            date.fromisoformat(bridge_by_year[year]) if year in bridge_by_year else None
        )
        plot_cumulative_percent(
            output / f"cumulative_pmr_{year}.pdf",
            read_curve(Path(file_name)),
            mass_cents,
            title=f"Cumulative daily PMR: {year}",
            bridge_start=bridge_start,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
