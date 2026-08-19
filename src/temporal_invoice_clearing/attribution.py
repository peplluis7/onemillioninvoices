"""Issue-cohort attribution for a short cross-year execution bridge."""
from __future__ import annotations

from fractions import Fraction
from typing import Literal, Mapping, Sequence

from .models import AtomicRecord, Operation, RunResult

AttributionRule = Literal["symmetric", "conservative", "liberal"]


def _fragment_sum(
    fragments: Sequence[dict[str, object]],
    record_cohort: Mapping[str, str],
    cohort: str,
) -> int:
    return sum(
        int(fragment["amount_cents"])
        for fragment in fragments
        if record_cohort.get(str(fragment["uid"])) == cohort
    )


def operation_cohort_pmr(
    operation: Operation,
    record_cohort: Mapping[str, str],
    cohort: str,
    *,
    rule: AttributionRule = "symmetric",
) -> Fraction:
    """Return the PMR of one logged operation attributable to ``cohort``.

    Cycle PMR is assigned to the issue cohort of each reduced invoice fragment. A
    reciprocal path has the same two-leg attribution. For a non-bilateral path, the
    one-unit PMR is divided equally between the incoming and outgoing invoice legs under
    the symmetric rule. Conservative and liberal rules assign a mixed-cohort operation
    only when both legs, or when either leg, contain cohort fragments.
    """

    payload = operation.payload
    amount = operation.amount_cents
    if operation.kind == "cycle":
        return Fraction(
            sum(
                _fragment_sum(entry["fragments"], record_cohort, cohort)
                for entry in payload["edge_fragments"]
            )
        )

    incoming_mass = _fragment_sum(payload["in_fragments"], record_cohort, cohort)
    outgoing_mass = _fragment_sum(payload["out_fragments"], record_cohort, cohort)
    if bool(payload["bilateral"]):
        return Fraction(incoming_mass + outgoing_mass)
    if rule == "symmetric":
        return Fraction(incoming_mass + outgoing_mass, 2)
    if rule == "conservative":
        return Fraction(amount if incoming_mass == amount and outgoing_mass == amount else 0)
    if rule == "liberal":
        return Fraction(amount if incoming_mass > 0 or outgoing_mass > 0 else 0)
    raise ValueError(f"unknown attribution rule: {rule}")


def cohort_pmr_attribution(
    records: Sequence[AtomicRecord],
    result: RunResult,
    cohort: str,
) -> dict[str, Fraction]:
    """Attribute terminal PMR to ``cohort`` under three transparent rules."""

    record_cohort = {record.uid: record.cohort for record in records}
    return {
        f"{rule}_cents": sum(
            (
                operation_cohort_pmr(
                    operation,
                    record_cohort,
                    cohort,
                    rule=rule,
                )
                for operation in result.operation_log
            ),
            start=Fraction(0),
        )
        for rule in ("symmetric", "conservative", "liberal")
    }


def cohort_daily_curve_attribution(
    records: Sequence[AtomicRecord],
    result: RunResult,
    cohort: str,
    *,
    rule: AttributionRule = "symmetric",
) -> list[Fraction]:
    """Build a cumulative cohort-attributed PMR curve from the fragment log."""

    if result.regime != "daily":
        raise ValueError("daily attribution requires a daily run")
    horizon = len(result.daily_curve_cents)
    record_cohort = {record.uid: record.cohort for record in records}
    increments = [Fraction(0) for _ in range(horizon)]
    for operation in result.operation_log:
        if not 0 <= operation.day_index < horizon:
            raise ValueError("operation day lies outside the daily curve horizon")
        increments[operation.day_index] += operation_cohort_pmr(
            operation,
            record_cohort,
            cohort,
            rule=rule,
        )
    cumulative: list[Fraction] = []
    running = Fraction(0)
    for increment in increments:
        running += increment
        cumulative.append(running)
    return cumulative
