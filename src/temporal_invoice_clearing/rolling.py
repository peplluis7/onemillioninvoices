"""Sequential annual CDG cohorts with non-reusable bridge invoices.

The annual bridge design is implemented as one causal state stream per clearing method.
An invoice record is introduced exactly once, can be consumed only up to its original
amount, and any residual amount used in the January-February bridge is the only amount
carried into the following issue-year phase.  Thus bridge records are never reloaded at
full face value for the next annual calculation.
"""
from __future__ import annotations

import heapq
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import networkx as nx
import numpy as np

from .algorithms import path_daily_local
from .attribution import operation_cohort_pmr
from .cycles import canonicalize_cycle
from .io import load_atomic_csv
from .models import AtomicRecord, Operation, RunResult
from .replay import replay_validate
from .state import TemporalState
from .bridges import residual_records


@dataclass(frozen=True, slots=True)
class ScreenedCycleIndex:
    cycles: list[tuple[tuple[int, ...], tuple[int, ...], int]]
    inventory_by_length: dict[int, int]
    total_circuits: int
    initially_feasible_circuits: int
    enumeration_seconds: float


@dataclass(frozen=True, slots=True)
class PhaseResult:
    start_date: date
    end_date: date
    input_records: int
    input_mass_cents: int
    result: RunResult
    residual_records: list[AtomicRecord]
    validation: dict[str, object]
    cycle_index: ScreenedCycleIndex | None = None


@dataclass(frozen=True, slots=True)
class CohortResult:
    year: int
    issue_mass_cents: int
    bridge_end: date
    bridge_days: int
    bridge_complete_jan_feb: bool
    dec31_pmr_cents: Fraction
    terminal_pmr_cents: Fraction
    terminal_conservative_cents: Fraction
    terminal_liberal_cents: Fraction
    opening_pmr_cents: Fraction
    main_phase_pmr_cents: int
    terminal_bridge_increment_cents: Fraction
    cumulative_curve_cents: list[Fraction]
    main_operations: int
    terminal_bridge_operations: int
    main_runtime_seconds: float
    terminal_bridge_runtime_seconds: float
    main_cycle_inventory: dict[int, int] | None = None
    main_initially_feasible_cycles: int | None = None
    bridge_new_records: int = 0
    bridge_new_mass_cents: int = 0
    bridge_new_consumed_mass_cents: int = 0
    bridge_residual_carried_records: int = 0
    bridge_residual_carried_mass_cents: int = 0
    bridge_fully_consumed_records: int = 0


@dataclass(frozen=True, slots=True)
class SequentialMethodResult:
    method: str
    cohorts: list[CohortResult]
    calendar_operations: int
    total_runtime_seconds: float
    global_consumption_audit: dict[str, object]
    phase_validations: list[dict[str, object]]


def _positive_day_mask(capacity: np.ndarray) -> int:
    packed = np.packbits((capacity > 0).astype(np.uint8), bitorder="little")
    return int.from_bytes(packed.tobytes(), "little")


def enumerate_feasible_cycles(
    state: TemporalState,
    *,
    length_bound: int = 8,
) -> ScreenedCycleIndex:
    """Enumerate every bounded circuit and retain those active on at least one day.

    The screen is exact for the daily run because residual edge-day capacities only
    decrease.  A circuit with no positive common-day capacity in the initial phase state
    cannot become feasible later in that phase.
    """

    if length_bound < 2:
        raise ValueError("length_bound must be at least 2")
    start = time.perf_counter()
    graph = nx.DiGraph()
    graph.add_edges_from((int(edge["u"]), int(edge["v"])) for edge in state.edges)
    edge_masks = [_positive_day_mask(edge["capacity"]) for edge in state.edges]
    inventory: Counter[int] = Counter()
    feasible: list[tuple[tuple[int, ...], tuple[int, ...], int]] = []

    for raw_nodes in nx.simple_cycles(graph, length_bound=length_bound):
        nodes = canonicalize_cycle(raw_nodes)
        length = len(nodes)
        inventory[length] += 1
        edge_ids = tuple(
            state.edge_map[(nodes[index], nodes[(index + 1) % length])]
            for index in range(length)
        )
        mask = edge_masks[edge_ids[0]]
        for edge_id in edge_ids[1:]:
            mask &= edge_masks[edge_id]
            if mask == 0:
                break
        if mask:
            feasible.append((nodes, edge_ids, mask))

    feasible.sort(key=lambda item: (len(item[0]), item[0]))
    return ScreenedCycleIndex(
        cycles=feasible,
        inventory_by_length=dict(sorted(inventory.items())),
        total_circuits=sum(inventory.values()),
        initially_feasible_circuits=len(feasible),
        enumeration_seconds=time.perf_counter() - start,
    )


def cycle_daily_indexed(
    base: TemporalState,
    index: ScreenedCycleIndex,
    *,
    keep_log: bool = True,
) -> RunResult:
    """Exact CDG bounded-cycle execution using an initial active-day index."""

    state = base.copy()
    start = time.perf_counter()
    candidates_by_day: list[list[int]] = [[] for _ in range(state.horizon)]
    for cycle_index, (_, _, mask0) in enumerate(index.cycles):
        mask = mask0
        while mask:
            least = mask & -mask
            day = least.bit_length() - 1
            if day < state.horizon:
                candidates_by_day[day].append(cycle_index)
            mask -= least

    operations = 0
    pmr = 0
    curve: list[int] = []
    log: list[Operation] = []

    for day_index in range(state.horizon):
        heap: list[tuple[Any, ...]] = []
        for cycle_index in candidates_by_day[day_index]:
            nodes, edge_ids, _ = index.cycles[cycle_index]
            amount = min(state.edge_capacity_on_day(edge_id, day_index) for edge_id in edge_ids)
            if amount <= 0:
                continue
            versions = tuple(int(state.edges[edge_id]["version"]) for edge_id in edge_ids)
            heapq.heappush(
                heap,
                (-len(nodes) * amount, len(nodes), nodes, cycle_index, amount, versions),
            )

        while heap:
            _, length, nodes, cycle_index, amount, versions = heapq.heappop(heap)
            edge_ids = index.cycles[cycle_index][1]
            current_versions = tuple(int(state.edges[edge_id]["version"]) for edge_id in edge_ids)
            if current_versions != versions:
                refreshed = min(
                    state.edge_capacity_on_day(edge_id, day_index) for edge_id in edge_ids
                )
                if refreshed > 0:
                    heapq.heappush(
                        heap,
                        (
                            -length * refreshed,
                            length,
                            nodes,
                            cycle_index,
                            refreshed,
                            current_versions,
                        ),
                    )
                continue

            refreshed = min(
                state.edge_capacity_on_day(edge_id, day_index) for edge_id in edge_ids
            )
            if refreshed != amount:
                if refreshed > 0:
                    heapq.heappush(
                        heap,
                        (
                            -length * refreshed,
                            length,
                            nodes,
                            cycle_index,
                            refreshed,
                            current_versions,
                        ),
                    )
                continue

            edge_fragments: list[dict[str, Any]] = []
            for edge_id in edge_ids:
                fragments = state.consume(edge_id, day_index, amount)
                edge_fragments.append(
                    {
                        "edge_id": edge_id,
                        "fragments": [fragment.to_dict() for fragment in fragments],
                    }
                )
            operations += 1
            pmr += length * amount
            if keep_log:
                log.append(
                    Operation(
                        kind="cycle",
                        day_index=day_index,
                        amount_cents=amount,
                        payload={
                            "nodes": list(nodes),
                            "edges": list(edge_ids),
                            "edge_fragments": edge_fragments,
                        },
                    )
                )
        curve.append(pmr)

    return RunResult(
        method="cycle",
        regime="daily",
        operations=operations,
        pmr_cents=pmr,
        compression_cents=pmr,
        instruction_mass_cents=0,
        residual_mass_cents=state.residual_mass_cents(),
        runtime_seconds=time.perf_counter() - start,
        daily_curve_cents=curve,
        operation_log=log,
    )


def _phase_attribution_curve(
    records: Sequence[AtomicRecord],
    result: RunResult,
    cohort: str,
    *,
    rule: str = "symmetric",
) -> list[Fraction]:
    record_cohort = {record.uid: record.cohort for record in records}
    increments = [Fraction(0) for _ in result.daily_curve_cents]
    for operation in result.operation_log:
        increments[operation.day_index] += operation_cohort_pmr(
            operation,
            record_cohort,
            cohort,
            rule=rule,  # type: ignore[arg-type]
        )
    running = Fraction(0)
    curve: list[Fraction] = []
    for increment in increments:
        running += increment
        curve.append(running)
    return curve


def _phase_attribution_total(
    records: Sequence[AtomicRecord],
    result: RunResult,
    cohort: str,
    *,
    rule: str = "symmetric",
) -> Fraction:
    curve = _phase_attribution_curve(records, result, cohort, rule=rule)
    return curve[-1] if curve else Fraction(0)


def _operation_fragments(operation: Operation) -> Iterable[dict[str, object]]:
    if operation.kind == "cycle":
        for edge_entry in operation.payload["edge_fragments"]:
            yield from edge_entry["fragments"]
    else:
        yield from operation.payload["in_fragments"]
        yield from operation.payload["out_fragments"]


def _run_phase(
    records: Sequence[AtomicRecord],
    *,
    start_date: date,
    end_date: date,
    method: str,
    cycle_bound: int,
    validate: bool,
) -> PhaseResult:
    if not records:
        raise ValueError("phase records cannot be empty")
    state = TemporalState(records, start_date, end_date)
    cycle_index: ScreenedCycleIndex | None = None
    if method == "cycle":
        cycle_index = enumerate_feasible_cycles(state, length_bound=cycle_bound)
        result = cycle_daily_indexed(state, cycle_index, keep_log=True)
    elif method == "path":
        result = path_daily_local(state, keep_log=True)
    else:
        raise ValueError(f"unknown method {method!r}")
    validation = replay_validate(records, state, result) if validate else {}
    if validate and not validation.get("all_pass", False):
        raise RuntimeError(f"independent replay failed for {method} phase {start_date}--{end_date}")
    return PhaseResult(
        start_date=start_date,
        end_date=end_date,
        input_records=len(records),
        input_mass_cents=sum(record.amount_cents for record in records),
        result=result,
        residual_records=residual_records(records, result),
        validation=validation,
        cycle_index=cycle_index,
    )


def _fraction_curve_add(base: Fraction, curve: Sequence[Fraction]) -> list[Fraction]:
    return [base + value for value in curve]


def _audit_phase_consumption(
    result: RunResult,
    *,
    original_amount: Mapping[str, int],
    cumulative_consumed: dict[str, int],
) -> None:
    for operation in result.operation_log:
        for fragment in _operation_fragments(operation):
            uid = str(fragment["uid"])
            amount = int(fragment["amount_cents"])
            cumulative_consumed[uid] = cumulative_consumed.get(uid, 0) + amount
            if uid not in original_amount:
                raise AssertionError(f"operation consumed unknown record {uid}")
            if cumulative_consumed[uid] > original_amount[uid]:
                raise AssertionError(
                    f"record {uid} consumed {cumulative_consumed[uid]} > {original_amount[uid]}"
                )


def run_sequential_annual_cdg(
    year_paths: Mapping[int, str | Path],
    *,
    start_year: int = 2012,
    end_year: int = 2023,
    last_bridge_end: date = date(2024, 2, 8),
    method: str,
    cycle_bound: int = 8,
    validate: bool = True,
    progress: Callable[[str], None] | None = None,
) -> SequentialMethodResult:
    """Run one causal annual stream with bridge records introduced only once.

    For cohort ``y`` the calendar sequence is:

    * January-February ``y`` already executed as the bridge of ``y-1``;
    * the residual of those records, plus March-December arrivals, forms the main phase;
    * residual ``y`` records are combined with January-February ``y+1`` for the terminal
      bridge;
    * after the bridge, unresolved ``y`` records are closed and only residual ``y+1``
      records continue.

    PMR generated in mixed-year operations is assigned by source-fragment issue cohort.
    No record UID is introduced more than once and no consumed amount is restored.
    """

    if method not in {"cycle", "path"}:
        raise ValueError("method must be 'cycle' or 'path'")
    missing = [year for year in range(start_year, end_year + 2) if year not in year_paths]
    # Only the final bridge year is needed beyond end_year.
    missing = [year for year in missing if year <= end_year + 1]
    if missing:
        raise ValueError(f"year_paths missing years: {missing}")

    def emit(message: str) -> None:
        if progress:
            progress(message)

    year_cache: dict[int, list[AtomicRecord]] = {}

    def get_year(year: int) -> list[AtomicRecord]:
        if year not in year_cache:
            year_cache[year] = load_atomic_csv(year_paths[year])
        return year_cache[year]

    original_amount: dict[str, int] = {}
    introduced_uids: set[str] = set()
    cumulative_consumed: dict[str, int] = {}
    phase_validations: list[dict[str, object]] = []
    cohorts: list[CohortResult] = []
    carry_residual: list[AtomicRecord] = []
    opening_pmr = Fraction(0)
    opening_conservative = Fraction(0)
    opening_liberal = Fraction(0)
    opening_curve: list[Fraction] = []
    calendar_operations = 0
    total_runtime = 0.0

    def introduce(records: Sequence[AtomicRecord]) -> None:
        for record in records:
            if record.uid in introduced_uids:
                raise AssertionError(f"record {record.uid} was introduced more than once")
            introduced_uids.add(record.uid)
            original_amount[record.uid] = record.amount_cents

    for year in range(start_year, end_year + 1):
        all_year_records = get_year(year)
        issue_mass = sum(record.amount_cents for record in all_year_records)
        if year == start_year:
            main_start = date(year, 1, 1)
            main_new = list(all_year_records)
            introduce(main_new)
            main_input = main_new
            opening_pmr = Fraction(0)
            opening_conservative = Fraction(0)
            opening_liberal = Fraction(0)
            opening_curve = []
        else:
            previous_bridge_end = date(year, 2, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28)
            main_start = previous_bridge_end + timedelta(days=1)
            fresh = [record for record in all_year_records if record.issue_date > previous_bridge_end]
            introduce(fresh)
            main_input = list(carry_residual) + fresh
            carried_uids = {record.uid for record in carry_residual}
            if any(record.uid in carried_uids for record in fresh):
                raise AssertionError("fresh records overlap carried residual records")

        main_end = date(year, 12, 31)
        emit(f"{method} {year} main {main_start}--{main_end}: {len(main_input):,} records")
        main_phase = _run_phase(
            main_input,
            start_date=main_start,
            end_date=main_end,
            method=method,
            cycle_bound=cycle_bound,
            validate=validate,
        )
        phase_validations.append(
            {
                "phase": f"{year}-main",
                "method": method,
                **main_phase.validation,
            }
        )
        _audit_phase_consumption(
            main_phase.result,
            original_amount=original_amount,
            cumulative_consumed=cumulative_consumed,
        )
        calendar_operations += main_phase.result.operations
        total_runtime += main_phase.result.runtime_seconds
        if main_phase.cycle_index:
            total_runtime += main_phase.cycle_index.enumeration_seconds

        dec31_pmr = opening_pmr + Fraction(main_phase.result.pmr_cents)
        dec31_conservative = opening_conservative + Fraction(main_phase.result.pmr_cents)
        dec31_liberal = opening_liberal + Fraction(main_phase.result.pmr_cents)
        if opening_curve:
            year_curve = list(opening_curve) + _fraction_curve_add(opening_pmr, [Fraction(v) for v in main_phase.result.daily_curve_cents])
        else:
            year_curve = [Fraction(v) for v in main_phase.result.daily_curve_cents]

        next_year = year + 1
        next_records_all = get_year(next_year)
        if year < end_year:
            bridge_end = date(next_year, 2, 29 if next_year % 4 == 0 and (next_year % 100 != 0 or next_year % 400 == 0) else 28)
        else:
            bridge_end = last_bridge_end
        bridge_start = date(next_year, 1, 1)
        bridge_new = [record for record in next_records_all if bridge_start <= record.issue_date <= bridge_end]
        introduce(bridge_new)
        bridge_input = list(main_phase.residual_records) + bridge_new
        emit(f"{method} {year} bridge {bridge_start}--{bridge_end}: {len(bridge_input):,} records")
        bridge_phase = _run_phase(
            bridge_input,
            start_date=bridge_start,
            end_date=bridge_end,
            method=method,
            cycle_bound=cycle_bound,
            validate=validate,
        )
        phase_validations.append(
            {
                "phase": f"{year}-bridge",
                "method": method,
                **bridge_phase.validation,
            }
        )
        _audit_phase_consumption(
            bridge_phase.result,
            original_amount=original_amount,
            cumulative_consumed=cumulative_consumed,
        )
        calendar_operations += bridge_phase.result.operations
        total_runtime += bridge_phase.result.runtime_seconds
        if bridge_phase.cycle_index:
            total_runtime += bridge_phase.cycle_index.enumeration_seconds

        cohort = str(year)
        next_cohort = str(next_year)
        bridge_current_curve = _phase_attribution_curve(bridge_input, bridge_phase.result, cohort, rule="symmetric")
        bridge_current_conservative = _phase_attribution_total(bridge_input, bridge_phase.result, cohort, rule="conservative")
        bridge_current_liberal = _phase_attribution_total(bridge_input, bridge_phase.result, cohort, rule="liberal")
        bridge_next_curve = _phase_attribution_curve(bridge_input, bridge_phase.result, next_cohort, rule="symmetric")
        bridge_next_conservative = _phase_attribution_total(bridge_input, bridge_phase.result, next_cohort, rule="conservative")
        bridge_next_liberal = _phase_attribution_total(bridge_input, bridge_phase.result, next_cohort, rule="liberal")

        terminal_pmr = dec31_pmr + (bridge_current_curve[-1] if bridge_current_curve else Fraction(0))
        terminal_conservative = dec31_conservative + bridge_current_conservative
        terminal_liberal = dec31_liberal + bridge_current_liberal
        terminal_curve = year_curve + _fraction_curve_add(dec31_pmr, bridge_current_curve)

        next_residual_records = [
            record for record in bridge_phase.residual_records if record.cohort == next_cohort
        ]
        residual_by_uid = {record.uid: record.amount_cents for record in next_residual_records}
        bridge_new_mass = sum(record.amount_cents for record in bridge_new)
        bridge_residual_mass = sum(residual_by_uid.get(record.uid, 0) for record in bridge_new)
        bridge_consumed_mass = bridge_new_mass - bridge_residual_mass
        bridge_fully_consumed = sum(1 for record in bridge_new if residual_by_uid.get(record.uid, 0) == 0)
        if bridge_consumed_mass < 0:
            raise AssertionError("bridge residual mass exceeds introduced bridge mass")

        cohorts.append(
            CohortResult(
                year=year,
                issue_mass_cents=issue_mass,
                bridge_end=bridge_end,
                bridge_days=(bridge_end - bridge_start).days + 1,
                bridge_complete_jan_feb=(bridge_end.month == 2 and bridge_end.day in {28, 29}),
                dec31_pmr_cents=dec31_pmr,
                terminal_pmr_cents=terminal_pmr,
                terminal_conservative_cents=terminal_conservative,
                terminal_liberal_cents=terminal_liberal,
                opening_pmr_cents=opening_pmr,
                main_phase_pmr_cents=main_phase.result.pmr_cents,
                terminal_bridge_increment_cents=(bridge_current_curve[-1] if bridge_current_curve else Fraction(0)),
                cumulative_curve_cents=terminal_curve,
                main_operations=main_phase.result.operations,
                terminal_bridge_operations=bridge_phase.result.operations,
                main_runtime_seconds=main_phase.result.runtime_seconds,
                terminal_bridge_runtime_seconds=bridge_phase.result.runtime_seconds,
                main_cycle_inventory=(main_phase.cycle_index.inventory_by_length if main_phase.cycle_index else None),
                main_initially_feasible_cycles=(main_phase.cycle_index.initially_feasible_circuits if main_phase.cycle_index else None),
                bridge_new_records=len(bridge_new),
                bridge_new_mass_cents=bridge_new_mass,
                bridge_new_consumed_mass_cents=bridge_consumed_mass,
                bridge_residual_carried_records=sum(1 for record in bridge_new if residual_by_uid.get(record.uid, 0) > 0),
                bridge_residual_carried_mass_cents=bridge_residual_mass,
                bridge_fully_consumed_records=bridge_fully_consumed,
            )
        )

        # Only next-year residuals continue.  Older cohort residuals are closed after the
        # prescribed bridge, and bridge records are never reloaded from their original
        # face values in the following main phase.
        carry_residual = next_residual_records
        opening_pmr = bridge_next_curve[-1] if bridge_next_curve else Fraction(0)
        opening_conservative = bridge_next_conservative
        opening_liberal = bridge_next_liberal
        opening_curve = bridge_next_curve

        # Release no-longer-needed raw year cache entries to limit memory.
        if year - 1 in year_cache:
            del year_cache[year - 1]

    overconsumed = {
        uid: consumed - original_amount[uid]
        for uid, consumed in cumulative_consumed.items()
        if consumed > original_amount[uid]
    }
    introduced_once = len(introduced_uids) == len(original_amount)
    consumed_bridge_uids = sum(
        1
        for uid in introduced_uids
        if any(uid.startswith(prefix) for prefix in ("main:", "xlsx2021:", "buffer24:"))
        and cumulative_consumed.get(uid, 0) > 0
    )
    audit = {
        "method": method,
        "introduced_records": len(introduced_uids),
        "introduced_once": introduced_once,
        "consumed_records": sum(1 for value in cumulative_consumed.values() if value > 0),
        "consumed_record_uid_count": consumed_bridge_uids,
        "overconsumed_records": len(overconsumed),
        "maximum_overconsumption_cents": max(overconsumed.values(), default=0),
        "all_pass": introduced_once and not overconsumed,
    }
    if not audit["all_pass"]:
        raise RuntimeError(f"global non-reuse audit failed for {method}: {audit}")

    return SequentialMethodResult(
        method=method,
        cohorts=cohorts,
        calendar_operations=calendar_operations,
        total_runtime_seconds=total_runtime,
        global_consumption_audit=audit,
        phase_validations=phase_validations,
    )
