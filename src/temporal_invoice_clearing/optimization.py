"""Full-information linear-programming upper bounds for tractable instances.

The large annual policies are deterministic heuristics.  This module constructs a
continuous source-record allocation model for path-only, cycle-only, or mixed move sets.
Because monetary fragments are allowed to be continuously divisible, the objective is a
valid upper bound on the integer-cent schedule.  It is intended for small and medium
subgraphs, not for the full annual networks.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Sequence

import networkx as nx
import numpy as np

from .models import AtomicRecord

MoveSet = Literal["path", "cycle", "mixed"]


@dataclass(frozen=True, slots=True)
class Candidate:
    kind: Literal["path", "cycle"]
    edges: tuple[tuple[str, str], ...]
    pmr_per_unit: int
    label: tuple[Any, ...]


def _canonical_cycle(nodes: list[str]) -> tuple[str, ...]:
    rotation = min(range(len(nodes)), key=lambda i: tuple(nodes[i:] + nodes[:i]))
    return tuple(nodes[rotation:] + nodes[:rotation])


def enumerate_candidates(
    records: Sequence[AtomicRecord],
    move_set: MoveSet,
    cycle_bound: int | None = 8,
) -> list[Candidate]:
    graph = nx.DiGraph()
    graph.add_edges_from((record.debtor, record.creditor) for record in records)
    candidates: dict[tuple[Any, ...], Candidate] = {}
    if move_set in {"path", "mixed"}:
        for intermediary in sorted(graph.nodes):
            for first in sorted(graph.in_edges(intermediary)):
                for second in sorted(graph.out_edges(intermediary)):
                    payer = first[0]
                    payee = second[1]
                    label = ("p", payer, intermediary, payee)
                    candidates[label] = Candidate(
                        kind="path",
                        edges=(first, second),
                        pmr_per_unit=2 if payer == payee else 1,
                        label=label,
                    )
    if move_set in {"cycle", "mixed"}:
        kwargs: dict[str, int] = {}
        if cycle_bound is not None:
            kwargs["length_bound"] = cycle_bound
        for raw in nx.simple_cycles(graph, **kwargs):
            if len(raw) < 2:
                continue
            nodes = _canonical_cycle(raw)
            edges = tuple(
                (nodes[index], nodes[(index + 1) % len(nodes)])
                for index in range(len(nodes))
            )
            label = ("c", *nodes)
            candidates[label] = Candidate(
                kind="cycle",
                edges=edges,
                pmr_per_unit=len(nodes),
                label=label,
            )
    return [candidates[key] for key in sorted(candidates)]


def _representative_days(records: Sequence[AtomicRecord], start: date, end: date) -> list[int]:
    """Days at which an edge-capacity vector may change.

    Capacity is constant between issue events and the day after a due date, so evaluation
    at these event days is exact for the continuous model.
    """

    horizon = (end - start).days + 1
    events = {0}
    for record in records:
        issue = max(0, (record.issue_date - start).days)
        due = min(horizon - 1, (record.due_date - start).days)
        if 0 <= issue < horizon and due >= issue:
            events.add(issue)
            if due + 1 < horizon:
                events.add(due + 1)
    return sorted(events)


def full_information_lp_upper_bound(
    records: Sequence[AtomicRecord],
    start_date: date,
    end_date: date,
    *,
    move_set: MoveSet = "path",
    cycle_bound: int | None = 8,
    time_limit_seconds: float | None = None,
) -> dict[str, Any]:
    """Solve the continuous full-information source-record allocation model.

    Each candidate-day variable is linked to one allocation variable for every supporting
    edge and active source record.  Record capacity is shared across all operation days.
    The model weakly dominates every feasible causal daily schedule for the same move set.
    """

    try:
        from scipy.optimize import linprog
        from scipy.sparse import coo_matrix
    except ImportError as exc:  # pragma: no cover - exercised only without optional extra
        raise RuntimeError(
            "Install the optimization extra: pip install temporal-invoice-clearing[optimization]"
        ) from exc

    retained = [
        record
        for record in records
        if record.issue_date <= end_date and record.due_date >= start_date
    ]
    candidates = enumerate_candidates(retained, move_set, cycle_bound)
    days = _representative_days(retained, start_date, end_date)
    by_edge: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, record in enumerate(retained):
        by_edge[(record.debtor, record.creditor)].append(index)

    operation_days: list[tuple[int, int, list[list[int]]]] = []
    for candidate_index, candidate in enumerate(candidates):
        for day_index in days:
            calendar_day = start_date.toordinal() + day_index
            active_by_edge: list[list[int]] = []
            feasible = True
            for edge in candidate.edges:
                active = [
                    record_index
                    for record_index in by_edge[edge]
                    if retained[record_index].issue_date.toordinal()
                    <= calendar_day
                    <= retained[record_index].due_date.toordinal()
                ]
                if not active:
                    feasible = False
                    break
                active_by_edge.append(active)
            if feasible:
                operation_days.append((candidate_index, day_index, active_by_edge))

    x_count = len(operation_days)
    allocations: list[tuple[int, int, int]] = []
    for operation_day_index, (_, _, active_by_edge) in enumerate(operation_days):
        for edge_position, active_records in enumerate(active_by_edge):
            for record_index in active_records:
                allocations.append((operation_day_index, edge_position, record_index))
    variable_count = x_count + len(allocations)
    if variable_count == 0:
        return {
            "success": True,
            "objective_cents": 0.0,
            "candidates": len(candidates),
            "candidate_days": 0,
            "variables": 0,
            "records": len(retained),
        }

    objective = np.zeros(variable_count)
    for operation_day_index, (candidate_index, _, _) in enumerate(operation_days):
        objective[operation_day_index] = -candidates[candidate_index].pmr_per_unit

    equality_index: dict[tuple[int, int], int] = {}
    row = 0
    for operation_day_index, (_, _, active_by_edge) in enumerate(operation_days):
        for edge_position in range(len(active_by_edge)):
            equality_index[(operation_day_index, edge_position)] = row
            row += 1

    eq_rows: list[int] = []
    eq_cols: list[int] = []
    eq_data: list[float] = []
    for operation_day_index, (_, _, active_by_edge) in enumerate(operation_days):
        for edge_position in range(len(active_by_edge)):
            eq_rows.append(equality_index[(operation_day_index, edge_position)])
            eq_cols.append(operation_day_index)
            eq_data.append(-1.0)
    for variable_index, (operation_day_index, edge_position, _) in enumerate(
        allocations, start=x_count
    ):
        eq_rows.append(equality_index[(operation_day_index, edge_position)])
        eq_cols.append(variable_index)
        eq_data.append(1.0)
    equality_matrix = coo_matrix(
        (eq_data, (eq_rows, eq_cols)), shape=(row, variable_count)
    ).tocsr()

    capacity_rows: list[int] = []
    capacity_cols: list[int] = []
    capacity_data: list[float] = []
    for variable_index, (_, _, record_index) in enumerate(allocations, start=x_count):
        capacity_rows.append(record_index)
        capacity_cols.append(variable_index)
        capacity_data.append(1.0)
    capacity_matrix = coo_matrix(
        (capacity_data, (capacity_rows, capacity_cols)),
        shape=(len(retained), variable_count),
    ).tocsr()
    capacity_rhs = np.array([record.amount_cents for record in retained], dtype=float)

    options: dict[str, float] = {}
    if time_limit_seconds is not None:
        options["time_limit"] = time_limit_seconds
    result = linprog(
        objective,
        A_ub=capacity_matrix,
        b_ub=capacity_rhs,
        A_eq=equality_matrix,
        b_eq=np.zeros(row),
        bounds=(0, None),
        method="highs",
        options=options,
    )
    return {
        "success": bool(result.success),
        "status": int(result.status),
        "message": result.message,
        "objective_cents": float(-result.fun) if result.success else None,
        "candidates": len(candidates),
        "candidate_days": len(operation_days),
        "variables": variable_count,
        "equalities": row,
        "records": len(retained),
        "representative_days": len(days),
        "continuous_relaxation": True,
    }
