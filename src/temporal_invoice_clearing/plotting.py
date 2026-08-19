"""Publication-oriented plots for cumulative and annual PMR results."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def plot_cumulative_pmr(
    output: str | Path,
    start_date: date,
    cycle_curve_cents: Sequence[int | float],
    path_curve_cents: Sequence[int | float],
    *,
    title: str = "Daily cumulative payable-mass reduction",
    bridge_start: date | None = None,
) -> None:
    if len(cycle_curve_cents) != len(path_curve_cents):
        raise ValueError("curve lengths must match")
    dates = [start_date + timedelta(days=index) for index in range(len(path_curve_cents))]
    cycle_billions = [value / 100 / 1e9 for value in cycle_curve_cents]
    path_billions = [value / 100 / 1e9 for value in path_curve_cents]

    figure, axis = plt.subplots(figsize=(8.2, 4.8))
    axis.plot(dates, cycle_billions, label="Cycle L=8")
    axis.plot(dates, path_billions, label="Path-enabled")
    if bridge_start is not None:
        axis.axvline(bridge_start, linestyle="--", linewidth=1.0, label="Bridge start")
    axis.set_title(title)
    axis.set_xlabel("Date")
    axis.set_ylabel("Cumulative PMR (EUR billions)")
    axis.grid(True, linewidth=0.4, alpha=0.45)
    axis.legend()
    figure.autofmt_xdate()
    figure.tight_layout()
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, bbox_inches="tight")
    plt.close(figure)
