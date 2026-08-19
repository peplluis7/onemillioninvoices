"""Sequential mixed path--cycle causal schedules.

The hybrid is an operational comparator, not a global optimizer.  On each day it runs one
move family to a fixed point and then the other.  Because residual capacities only
decrease, the first fixed point cannot be made newly feasible by the second phase.
"""
from __future__ import annotations

import heapq
import time
from collections import defaultdict
from typing import Any, Literal, Sequence

from .cycles import Cycle
from .algorithms import _best_path_candidate_on_day, _build_path_index
from .models import Operation, RunResult
from .state import TemporalState

HybridOrder = Literal["cycle_then_path", "path_then_cycle"]


def _fragment_dicts(fragments):
    return [fragment.to_dict() for fragment in fragments]


def _cycle_phase(
    state: TemporalState,
    cycles: Sequence[Cycle],
    day_index: int,
    log: list[Operation],
    keep_log: bool,
) -> tuple[int, int]:
    heap: list[tuple[Any, ...]] = []
    for cycle_index, (nodes, edge_ids) in enumerate(cycles):
        amount = min(state.edge_capacity_on_day(edge, day_index) for edge in edge_ids)
        if amount <= 0:
            continue
        versions = tuple(int(state.edges[edge]["version"]) for edge in edge_ids)
        heapq.heappush(
            heap,
            (-len(nodes) * amount, len(nodes), nodes, cycle_index, amount, versions),
        )
    pmr = operations = 0
    while heap:
        _, length, nodes, cycle_index, amount, versions = heapq.heappop(heap)
        edge_ids = cycles[cycle_index][1]
        current_versions = tuple(int(state.edges[edge]["version"]) for edge in edge_ids)
        refreshed = min(state.edge_capacity_on_day(edge, day_index) for edge in edge_ids)
        if current_versions != versions or refreshed != amount:
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
        edge_fragments = []
        for edge_id in edge_ids:
            fragments = state.consume(edge_id, day_index, amount)
            edge_fragments.append(
                {"edge_id": edge_id, "fragments": _fragment_dicts(fragments)}
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
    return pmr, operations


def _path_phase(
    state: TemporalState,
    day_index: int,
    incoming: dict[int, list[int]],
    outgoing: dict[int, list[int]],
    intermediaries: list[int],
    log: list[Operation],
    keep_log: bool,
) -> tuple[int, int, int, int]:
    """Global-PMR-priority path phase; returns PMR, operations, compression, instructions."""

    pmr = operations = compression = instructions = 0
    heap: list[tuple[Any, ...]] = []
    for intermediary in intermediaries:
        candidate = _best_path_candidate_on_day(
            state,
            intermediary,
            incoming[intermediary],
            outgoing[intermediary],
            day_index,
        )
        if candidate is not None:
            heapq.heappush(heap, (candidate["key"], intermediary, candidate))

    while heap:
        _, intermediary, candidate = heapq.heappop(heap)
        fresh = _best_path_candidate_on_day(
            state,
            intermediary,
            incoming[intermediary],
            outgoing[intermediary],
            day_index,
        )
        if fresh is None:
            continue
        if fresh["key"] != candidate["key"] or fresh["versions"] != candidate["versions"]:
            heapq.heappush(heap, (fresh["key"], intermediary, fresh))
            continue

        amount = int(fresh["amount"])
        payer = int(fresh["payer"])
        payee = int(fresh["payee"])
        in_edge = int(fresh["in_edge"])
        out_edge = int(fresh["out_edge"])
        bilateral = payer == payee
        in_fragments = state.consume(in_edge, day_index, amount)
        out_fragments = state.consume(out_edge, day_index, amount)
        operations += 1
        compression += 2 * amount
        if bilateral:
            pmr += 2 * amount
        else:
            pmr += amount
            instructions += amount
        if keep_log:
            log.append(
                Operation(
                    kind="path",
                    day_index=day_index,
                    amount_cents=amount,
                    payload={
                        "payer": payer,
                        "intermediary": intermediary,
                        "payee": payee,
                        "in_edge": in_edge,
                        "out_edge": out_edge,
                        "bilateral": bilateral,
                        "pmr_cents": 2 * amount if bilateral else amount,
                        "in_fragments": _fragment_dicts(in_fragments),
                        "out_fragments": _fragment_dicts(out_fragments),
                    },
                )
            )
        refreshed = _best_path_candidate_on_day(
            state,
            intermediary,
            incoming[intermediary],
            outgoing[intermediary],
            day_index,
        )
        if refreshed is not None:
            heapq.heappush(heap, (refreshed["key"], intermediary, refreshed))
    return pmr, operations, compression, instructions


def sequential_daily_hybrid(
    base: TemporalState,
    cycles: Sequence[Cycle],
    *,
    order: HybridOrder = "cycle_then_path",
    keep_log: bool = True,
) -> RunResult:
    """Execute a causal daily mixed policy in the requested phase order."""

    state = base.copy()
    start = time.perf_counter()
    incoming, outgoing, intermediaries = _build_path_index(state)

    pmr = operations = compression = instructions = 0
    curve: list[int] = []
    log: list[Operation] = []
    for day_index in range(state.horizon):
        if order == "cycle_then_path":
            value, count = _cycle_phase(state, cycles, day_index, log, keep_log)
            pmr += value
            compression += value
            operations += count
            value, count, removed, created = _path_phase(
                state,
                day_index,
                incoming,
                outgoing,
                intermediaries,
                log,
                keep_log,
            )
        else:
            value, count, removed, created = _path_phase(
                state,
                day_index,
                incoming,
                outgoing,
                intermediaries,
                log,
                keep_log,
            )
            pmr += value
            compression += removed
            instructions += created
            operations += count
            value, count = _cycle_phase(state, cycles, day_index, log, keep_log)
            removed = value
            created = 0
        pmr += value
        compression += removed
        instructions += created
        operations += count
        curve.append(pmr)

    return RunResult(
        method=f"mixed_{order}",
        regime="daily",
        operations=operations,
        pmr_cents=pmr,
        compression_cents=compression,
        instruction_mass_cents=instructions,
        residual_mass_cents=state.residual_mass_cents(),
        runtime_seconds=time.perf_counter() - start,
        daily_curve_cents=curve,
        operation_log=log,
    )
