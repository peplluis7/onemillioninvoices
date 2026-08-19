"""Two-phase causal annual execution with next-year bridge records.

The issue-year phase is executed before any bridge record is loaded.  Method-specific
residual source records are then carried into a second causal phase containing the actual
bridge-period invoices.  This prevents future bridge topology, identifiers, or tie-breaks
from affecting issue-year decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from fractions import Fraction
from typing import Sequence

from .algorithms import cycle_daily, path_daily_local
from .attribution import cohort_daily_curve_attribution, cohort_pmr_attribution
from .cycles import enumerate_cycles
from .models import AtomicRecord, Operation, RunResult
from .replay import replay_validate
from .state import TemporalState


def _operation_fragments(operation: Operation) -> list[dict[str, object]]:
    if operation.kind == "cycle":
        return [
            fragment
            for edge in operation.payload["edge_fragments"]
            for fragment in edge["fragments"]
        ]
    return list(operation.payload["in_fragments"]) + list(
        operation.payload["out_fragments"]
    )


def residual_records(
    records: Sequence[AtomicRecord], result: RunResult
) -> list[AtomicRecord]:
    """Reconstruct residual atomic records from an auditable operation log."""

    consumed: dict[str, int] = {}
    for operation in result.operation_log:
        for fragment in _operation_fragments(operation):
            uid = str(fragment["uid"])
            amount = int(fragment["amount_cents"])
            consumed[uid] = consumed.get(uid, 0) + amount

    residual: list[AtomicRecord] = []
    for record in records:
        amount = record.amount_cents - consumed.get(record.uid, 0)
        if amount < 0:
            raise ValueError(f"record {record.uid} is overconsumed by {-amount} cents")
        if amount > 0:
            residual.append(replace(record, amount_cents=amount))
    return residual


@dataclass(frozen=True, slots=True)
class MethodBridgeResult:
    issue_result: RunResult
    bridge_result: RunResult
    dec31_cohort_pmr_cents: Fraction
    terminal_cohort_pmr_cents: Fraction
    terminal_conservative_cents: Fraction
    terminal_liberal_cents: Fraction
    cohort_curve_cents: list[Fraction]
    issue_validation: dict[str, object]
    bridge_validation: dict[str, object]


@dataclass(frozen=True, slots=True)
class TwoPhaseBridgeResult:
    cohort: str
    issue_start: date
    issue_end: date
    bridge_start: date
    execution_end: date
    issue_mass_cents: int
    cycle: MethodBridgeResult
    path: MethodBridgeResult


def _combine_curve(
    issue_curve: Sequence[int], bridge_curve: Sequence[Fraction]
) -> list[Fraction]:
    if not issue_curve:
        raise ValueError("issue-year daily curve is empty")
    base = Fraction(issue_curve[-1])
    return [Fraction(value) for value in issue_curve] + [base + value for value in bridge_curve]


def run_two_phase_cdg(
    issue_records: Sequence[AtomicRecord],
    bridge_records: Sequence[AtomicRecord],
    *,
    cohort: str,
    issue_start: date,
    issue_end: date,
    bridge_start: date,
    execution_end: date,
    cycle_bound: int = 8,
    validate: bool = True,
) -> TwoPhaseBridgeResult:
    """Run cycle and intermediary-local path CDG in two causally separated phases.

    ``issue_records`` must contain the issue cohort only. ``bridge_records`` may support
    execution after year-end but are excluded from ``issue_mass_cents``. Cohort PMR in
    the bridge is computed from source-fragment issue labels.
    """

    if bridge_start <= issue_end:
        raise ValueError("bridge_start must follow issue_end")
    if execution_end < bridge_start:
        raise ValueError("execution_end cannot precede bridge_start")
    all_uids = [record.uid for record in issue_records] + [record.uid for record in bridge_records]
    if len(all_uids) != len(set(all_uids)):
        raise ValueError("record UIDs must be unique across issue and bridge phases")

    issue = [replace(record, cohort=cohort) for record in issue_records]
    bridge = [
        record if record.cohort else replace(record, cohort=str(record.issue_date.year))
        for record in bridge_records
    ]
    issue_mass = sum(record.amount_cents for record in issue)
    if issue_mass <= 0:
        raise ValueError("issue cohort has zero mass")

    issue_state = TemporalState(issue, issue_start, issue_end)
    issue_cycles = enumerate_cycles(issue_state, length_bound=cycle_bound)
    issue_cycle = cycle_daily(issue_state, issue_cycles, keep_log=True)
    issue_path = path_daily_local(issue_state, keep_log=True)

    cycle_residual = residual_records(issue, issue_cycle)
    path_residual = residual_records(issue, issue_path)

    cycle_bridge_records = cycle_residual + list(bridge)
    path_bridge_records = path_residual + list(bridge)
    cycle_bridge_state = TemporalState(cycle_bridge_records, bridge_start, execution_end)
    path_bridge_state = TemporalState(path_bridge_records, bridge_start, execution_end)
    bridge_cycles = enumerate_cycles(cycle_bridge_state, length_bound=cycle_bound)
    bridge_cycle = cycle_daily(cycle_bridge_state, bridge_cycles, keep_log=True)
    bridge_path = path_daily_local(path_bridge_state, keep_log=True)

    cycle_attr = cohort_pmr_attribution(cycle_bridge_records, bridge_cycle, cohort)
    path_attr = cohort_pmr_attribution(path_bridge_records, bridge_path, cohort)
    cycle_bridge_curve = cohort_daily_curve_attribution(
        cycle_bridge_records, bridge_cycle, cohort
    )
    path_bridge_curve = cohort_daily_curve_attribution(
        path_bridge_records, bridge_path, cohort
    )

    issue_cycle_validation = (
        replay_validate(issue, issue_state, issue_cycle) if validate else {}
    )
    issue_path_validation = (
        replay_validate(issue, issue_state, issue_path) if validate else {}
    )
    bridge_cycle_validation = (
        replay_validate(cycle_bridge_records, cycle_bridge_state, bridge_cycle)
        if validate
        else {}
    )
    bridge_path_validation = (
        replay_validate(path_bridge_records, path_bridge_state, bridge_path)
        if validate
        else {}
    )
    if validate and not all(
        result.get("all_pass", False)
        for result in (
            issue_cycle_validation,
            issue_path_validation,
            bridge_cycle_validation,
            bridge_path_validation,
        )
    ):
        raise RuntimeError("independent replay failed in a bridge phase")

    cycle = MethodBridgeResult(
        issue_result=issue_cycle,
        bridge_result=bridge_cycle,
        dec31_cohort_pmr_cents=Fraction(issue_cycle.pmr_cents),
        terminal_cohort_pmr_cents=Fraction(issue_cycle.pmr_cents)
        + cycle_attr["symmetric_cents"],
        terminal_conservative_cents=Fraction(issue_cycle.pmr_cents)
        + cycle_attr["conservative_cents"],
        terminal_liberal_cents=Fraction(issue_cycle.pmr_cents)
        + cycle_attr["liberal_cents"],
        cohort_curve_cents=_combine_curve(
            issue_cycle.daily_curve_cents, cycle_bridge_curve
        ),
        issue_validation=issue_cycle_validation,
        bridge_validation=bridge_cycle_validation,
    )
    path = MethodBridgeResult(
        issue_result=issue_path,
        bridge_result=bridge_path,
        dec31_cohort_pmr_cents=Fraction(issue_path.pmr_cents),
        terminal_cohort_pmr_cents=Fraction(issue_path.pmr_cents)
        + path_attr["symmetric_cents"],
        terminal_conservative_cents=Fraction(issue_path.pmr_cents)
        + path_attr["conservative_cents"],
        terminal_liberal_cents=Fraction(issue_path.pmr_cents)
        + path_attr["liberal_cents"],
        cohort_curve_cents=_combine_curve(
            issue_path.daily_curve_cents, path_bridge_curve
        ),
        issue_validation=issue_path_validation,
        bridge_validation=bridge_path_validation,
    )
    return TwoPhaseBridgeResult(
        cohort=cohort,
        issue_start=issue_start,
        issue_end=issue_end,
        bridge_start=bridge_start,
        execution_end=execution_end,
        issue_mass_cents=issue_mass,
        cycle=cycle,
        path=path,
    )
