"""Deterministic path-enabled and bounded-cycle clearing policies.

The fixed-operation temporal amount is exact. The global sequence is a deterministic
policy and is not claimed to solve the globally optimal schedule.
"""
from __future__ import annotations

import heapq
import time
from collections import defaultdict
from typing import Any, Optional, Sequence

from .cycles import Cycle
from .models import Fragment, Operation, RunResult
from .state import TemporalState


def _fragment_dicts(fragments: Sequence[Fragment]) -> list[dict[str, Any]]:
    return [fragment.to_dict() for fragment in fragments]


def _pair_acceleration(
    incoming: Sequence[Fragment],
    outgoing: Sequence[Fragment],
) -> tuple[int, int, int]:
    """Return total, positive-only, and positive-mass acceleration numerators."""

    index_in = index_out = 0
    remaining_in = incoming[0].amount_cents
    remaining_out = outgoing[0].amount_cents
    total_numerator = 0
    positive_numerator = 0
    positive_mass = 0

    while index_in < len(incoming) and index_out < len(outgoing):
        paired = min(remaining_in, remaining_out)
        acceleration = max(
            0,
            incoming[index_in].due_ordinal - outgoing[index_out].due_ordinal,
        )
        total_numerator += paired * acceleration
        if acceleration > 0:
            positive_numerator += paired * acceleration
            positive_mass += paired
        remaining_in -= paired
        remaining_out -= paired
        if remaining_in == 0:
            index_in += 1
            remaining_in = (
                incoming[index_in].amount_cents if index_in < len(incoming) else 0
            )
        if remaining_out == 0:
            index_out += 1
            remaining_out = (
                outgoing[index_out].amount_cents if index_out < len(outgoing) else 0
            )

    return total_numerator, positive_numerator, positive_mass


def path_daily_local(base: TemporalState, *, keep_log: bool = True) -> RunResult:
    """Run the causal daily path policy, ranking candidates by actual PMR.

    Reciprocal ``A -> B -> A`` operations receive score ``2q`` because they create no
    instruction; non-bilateral ``A -> B -> C`` operations receive score ``q``.  The
    earlier implementation ranked both by ``q`` and therefore did not use the same
    economic objective as the cycle comparator.
    """

    state = base.copy()
    start_time = time.perf_counter()
    incoming: dict[int, list[int]] = defaultdict(list)
    outgoing: dict[int, list[int]] = defaultdict(list)
    for edge_id, edge in enumerate(state.edges):
        outgoing[int(edge["u"])].append(edge_id)
        incoming[int(edge["v"])].append(edge_id)
    intermediaries = sorted(set(incoming).intersection(outgoing))

    operations = pmr = compression = instruction_mass = 0
    bilateral_pmr = nonbilateral_pmr = 0
    curve: list[int] = []
    log: list[Operation] = []
    acceleration_numerator = positive_acceleration_numerator = 0
    positive_acceleration_mass = 0

    def clean(heap: list[tuple[int, int, int]], day: int) -> tuple[int, int, int] | None:
        while heap:
            neg_capacity, endpoint, edge_id = heap[0]
            capacity = state.edge_capacity_on_day(edge_id, day)
            if capacity <= 0 or -neg_capacity != capacity:
                heapq.heappop(heap)
                continue
            return capacity, endpoint, edge_id
        return None

    def best_nonbilateral(
        in_heap: list[tuple[int, int, int]],
        out_heap: list[tuple[int, int, int]],
        day: int,
    ) -> tuple[int, int, int, int, int] | None:
        top_in = clean(in_heap, day)
        top_out = clean(out_heap, day)
        if top_in is None or top_out is None:
            return None
        in_capacity, payer, in_edge = top_in
        out_capacity, payee, out_edge = top_out
        if payer != payee:
            return min(in_capacity, out_capacity), payer, payee, in_edge, out_edge

        removed_in = heapq.heappop(in_heap)
        alternative_in = clean(in_heap, day)
        heapq.heappush(in_heap, removed_in)
        removed_out = heapq.heappop(out_heap)
        alternative_out = clean(out_heap, day)
        heapq.heappush(out_heap, removed_out)
        candidates: list[tuple[int, int, int, int, int]] = []
        if alternative_in is not None and alternative_in[1] != payee:
            candidates.append(
                (
                    min(alternative_in[0], out_capacity),
                    alternative_in[1],
                    payee,
                    alternative_in[2],
                    out_edge,
                )
            )
        if alternative_out is not None and alternative_out[1] != payer:
            candidates.append(
                (
                    min(in_capacity, alternative_out[0]),
                    payer,
                    alternative_out[1],
                    in_edge,
                    alternative_out[2],
                )
            )
        if not candidates:
            return None
        return min(candidates, key=lambda item: (-item[0], item[1:]))

    def clean_reciprocal(
        heap: list[tuple[int, int, int, int, int, int, int]], day: int
    ) -> tuple[int, int, int, int] | None:
        while heap:
            neg_score, neg_amount, endpoint, in_edge, out_edge, in_version, out_version = heap[0]
            if (
                int(state.edges[in_edge]["version"]) != in_version
                or int(state.edges[out_edge]["version"]) != out_version
            ):
                heapq.heappop(heap)
                continue
            amount = min(
                state.edge_capacity_on_day(in_edge, day),
                state.edge_capacity_on_day(out_edge, day),
            )
            if amount <= 0 or -neg_score != 2 * amount or -neg_amount != amount:
                heapq.heappop(heap)
                continue
            return amount, endpoint, in_edge, out_edge
        return None

    for day_index in range(state.horizon):
        for intermediary in intermediaries:
            in_heap: list[tuple[int, int, int]] = []
            out_heap: list[tuple[int, int, int]] = []
            in_by_endpoint: dict[int, int] = {}
            out_by_endpoint: dict[int, int] = {}
            for edge_id in incoming[intermediary]:
                capacity = state.edge_capacity_on_day(edge_id, day_index)
                if capacity > 0:
                    payer = int(state.edges[edge_id]["u"])
                    heapq.heappush(in_heap, (-capacity, payer, edge_id))
                    in_by_endpoint[payer] = edge_id
            if not in_heap:
                continue
            for edge_id in outgoing[intermediary]:
                capacity = state.edge_capacity_on_day(edge_id, day_index)
                if capacity > 0:
                    payee = int(state.edges[edge_id]["v"])
                    heapq.heappush(out_heap, (-capacity, payee, edge_id))
                    out_by_endpoint[payee] = edge_id
            if not out_heap:
                continue

            reciprocal_heap: list[tuple[int, int, int, int, int, int, int]] = []
            for endpoint, in_edge in in_by_endpoint.items():
                out_edge = out_by_endpoint.get(endpoint)
                if out_edge is None:
                    continue
                amount = min(
                    state.edge_capacity_on_day(in_edge, day_index),
                    state.edge_capacity_on_day(out_edge, day_index),
                )
                if amount > 0:
                    heapq.heappush(
                        reciprocal_heap,
                        (
                            -2 * amount,
                            -amount,
                            endpoint,
                            in_edge,
                            out_edge,
                            int(state.edges[in_edge]["version"]),
                            int(state.edges[out_edge]["version"]),
                        ),
                    )

            while True:
                nonbilateral = best_nonbilateral(in_heap, out_heap, day_index)
                reciprocal = clean_reciprocal(reciprocal_heap, day_index)
                if nonbilateral is None and reciprocal is None:
                    break
                nonbilateral_key = None
                if nonbilateral is not None:
                    q, payer, payee, in_edge, out_edge = nonbilateral
                    nonbilateral_key = (-q, -q, 1, payer, payee, in_edge, out_edge)
                reciprocal_key = None
                if reciprocal is not None:
                    q, endpoint, in_edge, out_edge = reciprocal
                    reciprocal_key = (-2 * q, -q, 0, endpoint, endpoint, in_edge, out_edge)

                if reciprocal_key is not None and (
                    nonbilateral_key is None or reciprocal_key < nonbilateral_key
                ):
                    amount, payer, in_edge, out_edge = reciprocal
                    payee = payer
                    bilateral = True
                else:
                    assert nonbilateral is not None
                    amount, payer, payee, in_edge, out_edge = nonbilateral
                    bilateral = False

                in_fragments = state.consume(in_edge, day_index, amount)
                out_fragments = state.consume(out_edge, day_index, amount)
                operations += 1
                compression += 2 * amount
                if bilateral:
                    pmr += 2 * amount
                    bilateral_pmr += 2 * amount
                else:
                    pmr += amount
                    nonbilateral_pmr += amount
                    instruction_mass += amount
                    total, positive, positive_mass = _pair_acceleration(
                        in_fragments, out_fragments
                    )
                    acceleration_numerator += total
                    positive_acceleration_numerator += positive
                    positive_acceleration_mass += positive_mass

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

                in_capacity = state.edge_capacity_on_day(in_edge, day_index)
                out_capacity = state.edge_capacity_on_day(out_edge, day_index)
                if in_capacity > 0:
                    heapq.heappush(in_heap, (-in_capacity, payer, in_edge))
                if out_capacity > 0:
                    heapq.heappush(out_heap, (-out_capacity, payee, out_edge))
                for endpoint in {payer, payee}:
                    reciprocal_in = in_by_endpoint.get(endpoint)
                    reciprocal_out = out_by_endpoint.get(endpoint)
                    if reciprocal_in is None or reciprocal_out is None:
                        continue
                    q = min(
                        state.edge_capacity_on_day(reciprocal_in, day_index),
                        state.edge_capacity_on_day(reciprocal_out, day_index),
                    )
                    if q > 0:
                        heapq.heappush(
                            reciprocal_heap,
                            (
                                -2 * q,
                                -q,
                                endpoint,
                                reciprocal_in,
                                reciprocal_out,
                                int(state.edges[reciprocal_in]["version"]),
                                int(state.edges[reciprocal_out]["version"]),
                            ),
                        )
        curve.append(pmr)

    result = RunResult(
        method="path",
        regime="daily",
        operations=operations,
        pmr_cents=pmr,
        compression_cents=compression,
        instruction_mass_cents=instruction_mass,
        residual_mass_cents=state.residual_mass_cents(),
        runtime_seconds=time.perf_counter() - start_time,
        daily_curve_cents=curve,
        operation_log=log,
        mean_acceleration_days=(
            acceleration_numerator / instruction_mass if instruction_mass else 0.0
        ),
        positive_only_mean_days=(
            positive_acceleration_numerator / positive_acceleration_mass
            if positive_acceleration_mass
            else 0.0
        ),
        accelerated_mass_share=(
            positive_acceleration_mass / instruction_mass if instruction_mass else 0.0
        ),
    )
    return result


def _build_path_index(state: TemporalState) -> tuple[dict[int, list[int]], dict[int, list[int]], list[int]]:
    incoming: dict[int, list[int]] = defaultdict(list)
    outgoing: dict[int, list[int]] = defaultdict(list)
    for edge_id, edge in enumerate(state.edges):
        outgoing[int(edge["u"])].append(edge_id)
        incoming[int(edge["v"])].append(edge_id)
    return incoming, outgoing, sorted(set(incoming).intersection(outgoing))


def _best_path_candidate_on_day(
    state: TemporalState,
    intermediary: int,
    in_edges: Sequence[int],
    out_edges: Sequence[int],
    day_index: int,
) -> dict[str, Any] | None:
    """Return the current PMR-maximizing path candidate at one intermediary.

    Reciprocal paths have score ``2q`` and non-bilateral paths score ``q``.  The
    construction finds the best non-bilateral pair from the leading edge and at most
    one alternative on either side, and examines reciprocal endpoints explicitly.
    """

    active_in: list[tuple[int, int, int]] = []
    active_out: list[tuple[int, int, int]] = []
    in_by_endpoint: dict[int, tuple[int, int]] = {}
    out_by_endpoint: dict[int, tuple[int, int]] = {}
    for edge_id in in_edges:
        capacity = state.edge_capacity_on_day(edge_id, day_index)
        if capacity > 0:
            payer = int(state.edges[edge_id]["u"])
            active_in.append((capacity, payer, edge_id))
            in_by_endpoint[payer] = (capacity, edge_id)
    for edge_id in out_edges:
        capacity = state.edge_capacity_on_day(edge_id, day_index)
        if capacity > 0:
            payee = int(state.edges[edge_id]["v"])
            active_out.append((capacity, payee, edge_id))
            out_by_endpoint[payee] = (capacity, edge_id)
    if not active_in or not active_out:
        return None

    active_in.sort(key=lambda item: (-item[0], item[1], item[2]))
    active_out.sort(key=lambda item: (-item[0], item[1], item[2]))
    candidates: list[tuple[Any, ...]] = []

    in_capacity, payer, in_edge = active_in[0]
    out_capacity, payee, out_edge = active_out[0]
    if payer != payee:
        amount = min(in_capacity, out_capacity)
        candidates.append((-amount, -amount, 1, payer, payee, in_edge, out_edge))
    else:
        for alt_capacity, alt_payer, alt_edge in active_in[1:]:
            if alt_payer != payee:
                amount = min(alt_capacity, out_capacity)
                candidates.append(
                    (-amount, -amount, 1, alt_payer, payee, alt_edge, out_edge)
                )
                break
        for alt_capacity, alt_payee, alt_edge in active_out[1:]:
            if alt_payee != payer:
                amount = min(in_capacity, alt_capacity)
                candidates.append(
                    (-amount, -amount, 1, payer, alt_payee, in_edge, alt_edge)
                )
                break

    if len(in_by_endpoint) <= len(out_by_endpoint):
        reciprocal_iter = in_by_endpoint.items()
        for endpoint, (left_capacity, left_edge) in reciprocal_iter:
            right = out_by_endpoint.get(endpoint)
            if right is None:
                continue
            right_capacity, right_edge = right
            amount = min(left_capacity, right_capacity)
            candidates.append(
                (-2 * amount, -amount, 0, endpoint, endpoint, left_edge, right_edge)
            )
    else:
        for endpoint, (right_capacity, right_edge) in out_by_endpoint.items():
            left = in_by_endpoint.get(endpoint)
            if left is None:
                continue
            left_capacity, left_edge = left
            amount = min(left_capacity, right_capacity)
            candidates.append(
                (-2 * amount, -amount, 0, endpoint, endpoint, left_edge, right_edge)
            )

    if not candidates:
        return None
    key = min(candidates)
    neg_score, neg_amount, _, payer, payee, in_edge, out_edge = key
    return {
        "key": key,
        "score": -int(neg_score),
        "amount": -int(neg_amount),
        "payer": int(payer),
        "payee": int(payee),
        "in_edge": int(in_edge),
        "out_edge": int(out_edge),
        "bilateral": payer == payee,
        "versions": (
            int(state.edges[in_edge]["version"]),
            int(state.edges[out_edge]["version"]),
        ),
    }


def path_daily(base: TemporalState, *, keep_log: bool = True) -> RunResult:
    """Run the causal daily *global-PMR-priority* path policy.

    On each day the best current candidate at every intermediary is placed in a global
    heap.  The selected move maximizes actual payable-mass reduction, not raw matched
    amount.  Future records may reside in memory, but ``edge_capacity_on_day`` masks
    them until their issue date; this is equivalent to streaming causal arrivals.
    """

    state = base.copy()
    start_time = time.perf_counter()
    incoming, outgoing, intermediaries = _build_path_index(state)
    operations = pmr = compression = instruction_mass = 0
    curve: list[int] = []
    log: list[Operation] = []
    acceleration_numerator = positive_acceleration_numerator = 0
    positive_acceleration_mass = 0

    for day_index in range(state.horizon):
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
            in_edge = int(fresh["in_edge"])
            out_edge = int(fresh["out_edge"])
            payer = int(fresh["payer"])
            payee = int(fresh["payee"])
            in_fragments = state.consume(in_edge, day_index, amount)
            out_fragments = state.consume(out_edge, day_index, amount)
            bilateral = payer == payee
            operations += 1
            compression += 2 * amount
            if bilateral:
                pmr += 2 * amount
            else:
                pmr += amount
                instruction_mass += amount
                total, positive, positive_mass = _pair_acceleration(
                    in_fragments, out_fragments
                )
                acceleration_numerator += total
                positive_acceleration_numerator += positive
                positive_acceleration_mass += positive_mass
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
        curve.append(pmr)

    return RunResult(
        method="path_global_pmr",
        regime="daily",
        operations=operations,
        pmr_cents=pmr,
        compression_cents=compression,
        instruction_mass_cents=instruction_mass,
        residual_mass_cents=state.residual_mass_cents(),
        runtime_seconds=time.perf_counter() - start_time,
        daily_curve_cents=curve,
        operation_log=log,
        mean_acceleration_days=(
            acceleration_numerator / instruction_mass if instruction_mass else 0.0
        ),
        positive_only_mean_days=(
            positive_acceleration_numerator / positive_acceleration_mass
            if positive_acceleration_mass
            else 0.0
        ),
        accelerated_mass_share=(
            positive_acceleration_mass / instruction_mass if instruction_mass else 0.0
        ),
    )


def cycle_daily(
    base: TemporalState,
    cycles: Sequence[Cycle],
    *,
    keep_log: bool = True,
) -> RunResult:
    """Run the causal current-value bounded-cycle policy on every day."""

    state = base.copy()
    start_time = time.perf_counter()
    operations = 0
    pmr = 0
    curve: list[int] = []
    log: list[Operation] = []

    for day_index in range(state.horizon):
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

        while heap:
            _, length, nodes, cycle_index, amount, versions = heapq.heappop(heap)
            edge_ids = cycles[cycle_index][1]
            current_versions = tuple(int(state.edges[edge]["version"]) for edge in edge_ids)
            if current_versions != versions:
                refreshed = min(
                    state.edge_capacity_on_day(edge, day_index) for edge in edge_ids
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
                state.edge_capacity_on_day(edge, day_index) for edge in edge_ids
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

        curve.append(pmr)

    return RunResult(
        method="cycle",
        regime="daily",
        operations=operations,
        pmr_cents=pmr,
        compression_cents=pmr,
        instruction_mass_cents=0,
        residual_mass_cents=state.residual_mass_cents(),
        runtime_seconds=time.perf_counter() - start_time,
        daily_curve_cents=curve,
        operation_log=log,
    )


def path_offline_local(base: TemporalState, *, keep_log: bool = True) -> RunResult:
    """Run the full-arrival-set greedy path schedule, ranked by actual PMR.

    This is a feasible noncausal heuristic, not an offline optimum or upper bound.
    """

    state = base.copy()
    start_time = time.perf_counter()
    incoming: dict[int, list[int]] = defaultdict(list)
    outgoing: dict[int, list[int]] = defaultdict(list)
    for edge_id, edge in enumerate(state.edges):
        outgoing[int(edge["u"])].append(edge_id)
        incoming[int(edge["v"])].append(edge_id)

    operations = 0
    pmr = 0
    compression = 0
    instruction_mass = 0
    log: list[Operation] = []
    acceleration_numerator = 0
    positive_acceleration_numerator = 0
    positive_acceleration_mass = 0

    for intermediary in sorted(set(incoming).union(outgoing)):
        in_edges = incoming.get(intermediary, [])
        out_edges = outgoing.get(intermediary, [])
        if not in_edges or not out_edges:
            continue

        heap: list[tuple[Any, ...]] = []
        for in_edge in in_edges:
            for out_edge in out_edges:
                capacity, day_index = state.pair_common_day_capacity(in_edge, out_edge)
                if capacity <= 0:
                    continue
                payer = int(state.edges[in_edge]["u"])
                payee = int(state.edges[out_edge]["v"])
                heapq.heappush(
                    heap,
                    (
                        -(2 if payer == payee else 1) * capacity,
                        -capacity,
                        day_index,
                        0 if payer == payee else 1,
                        payer,
                        payee,
                        in_edge,
                        out_edge,
                        int(state.edges[in_edge]["version"]),
                        int(state.edges[out_edge]["version"]),
                    ),
                )

        while heap:
            (
                negative_score,
                negative_capacity,
                day_index,
                bilateral_rank,
                payer,
                payee,
                in_edge,
                out_edge,
                in_version,
                out_version,
            ) = heapq.heappop(heap)

            if (
                in_version != state.edges[in_edge]["version"]
                or out_version != state.edges[out_edge]["version"]
            ):
                capacity, refreshed_day = state.pair_common_day_capacity(in_edge, out_edge)
                if capacity > 0:
                    heapq.heappush(
                        heap,
                        (
                            -(2 if payer == payee else 1) * capacity,
                            -capacity,
                            refreshed_day,
                            0 if payer == payee else 1,
                            payer,
                            payee,
                            in_edge,
                            out_edge,
                            int(state.edges[in_edge]["version"]),
                            int(state.edges[out_edge]["version"]),
                        ),
                    )
                continue

            capacity = -negative_capacity
            refreshed, refreshed_day = state.pair_common_day_capacity(in_edge, out_edge)
            if refreshed != capacity or refreshed_day != day_index:
                if refreshed > 0:
                    heapq.heappush(
                        heap,
                        (
                            -(2 if payer == payee else 1) * refreshed,
                            -refreshed,
                            refreshed_day,
                            0 if payer == payee else 1,
                            payer,
                            payee,
                            in_edge,
                            out_edge,
                            int(state.edges[in_edge]["version"]),
                            int(state.edges[out_edge]["version"]),
                        ),
                    )
                continue

            in_fragments = state.consume(in_edge, day_index, capacity)
            out_fragments = state.consume(out_edge, day_index, capacity)
            bilateral = payer == payee
            operations += 1
            compression += 2 * capacity
            if bilateral:
                pmr += 2 * capacity
            else:
                pmr += capacity
                instruction_mass += capacity
                total, positive, positive_mass = _pair_acceleration(
                    in_fragments, out_fragments
                )
                acceleration_numerator += total
                positive_acceleration_numerator += positive
                positive_acceleration_mass += positive_mass

            if keep_log:
                log.append(
                    Operation(
                        kind="path",
                        day_index=day_index,
                        amount_cents=capacity,
                        payload={
                            "payer": payer,
                            "intermediary": intermediary,
                            "payee": payee,
                            "in_edge": in_edge,
                            "out_edge": out_edge,
                            "bilateral": bilateral,
                            "in_fragments": _fragment_dicts(in_fragments),
                            "out_fragments": _fragment_dicts(out_fragments),
                        },
                    )
                )

    return RunResult(
        method="path",
        regime="offline",
        operations=operations,
        pmr_cents=pmr,
        compression_cents=compression,
        instruction_mass_cents=instruction_mass,
        residual_mass_cents=state.residual_mass_cents(),
        runtime_seconds=time.perf_counter() - start_time,
        operation_log=log,
        mean_acceleration_days=(
            acceleration_numerator / instruction_mass if instruction_mass else 0.0
        ),
        positive_only_mean_days=(
            positive_acceleration_numerator / positive_acceleration_mass
            if positive_acceleration_mass
            else 0.0
        ),
        accelerated_mass_share=(
            positive_acceleration_mass / instruction_mass if instruction_mass else 0.0
        ),
    )



def path_offline(base: TemporalState, *, keep_log: bool = True) -> RunResult:
    """Run the full-arrival-set *global-PMR-priority* path heuristic.

    All annual records are available for candidate evaluation, every operation is still
    assigned an exact common day, and candidates are ranked by actual PMR.  This is a
    feasible noncausal heuristic, not a full-information optimum or upper bound.
    """

    state = base.copy()
    start_time = time.perf_counter()
    incoming, outgoing, intermediaries = _build_path_index(state)
    candidates: list[tuple[int, int, int, int, int, bool]] = []
    for intermediary in intermediaries:
        for in_edge in incoming[intermediary]:
            payer = int(state.edges[in_edge]["u"])
            for out_edge in outgoing[intermediary]:
                payee = int(state.edges[out_edge]["v"])
                candidates.append(
                    (
                        intermediary,
                        in_edge,
                        out_edge,
                        payer,
                        payee,
                        payer == payee,
                    )
                )

    heap: list[tuple[Any, ...]] = []

    def push(index: int) -> None:
        intermediary, in_edge, out_edge, payer, payee, bilateral = candidates[index]
        amount, day_index = state.pair_common_day_capacity(in_edge, out_edge)
        if amount <= 0:
            return
        score = (2 if bilateral else 1) * amount
        versions = (
            int(state.edges[in_edge]["version"]),
            int(state.edges[out_edge]["version"]),
        )
        heapq.heappush(
            heap,
            (
                -score,
                -amount,
                day_index,
                0 if bilateral else 1,
                intermediary,
                payer,
                payee,
                in_edge,
                out_edge,
                index,
                versions,
            ),
        )

    for index in range(len(candidates)):
        push(index)

    operations = pmr = compression = instruction_mass = 0
    log: list[Operation] = []
    acceleration_numerator = positive_acceleration_numerator = 0
    positive_acceleration_mass = 0

    while heap:
        item = heapq.heappop(heap)
        *_, index, versions = item
        intermediary, in_edge, out_edge, payer, payee, bilateral = candidates[index]
        current_versions = (
            int(state.edges[in_edge]["version"]),
            int(state.edges[out_edge]["version"]),
        )
        amount, day_index = state.pair_common_day_capacity(in_edge, out_edge)
        if amount <= 0:
            continue
        score = (2 if bilateral else 1) * amount
        refreshed = (
            -score,
            -amount,
            day_index,
            0 if bilateral else 1,
            intermediary,
            payer,
            payee,
            in_edge,
            out_edge,
            index,
            current_versions,
        )
        if current_versions != versions or refreshed != item:
            heapq.heappush(heap, refreshed)
            continue

        in_fragments = state.consume(in_edge, day_index, amount)
        out_fragments = state.consume(out_edge, day_index, amount)
        operations += 1
        compression += 2 * amount
        if bilateral:
            pmr += 2 * amount
        else:
            pmr += amount
            instruction_mass += amount
            total, positive, positive_mass = _pair_acceleration(
                in_fragments, out_fragments
            )
            acceleration_numerator += total
            positive_acceleration_numerator += positive
            positive_acceleration_mass += positive_mass
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

    return RunResult(
        method="path_global_pmr",
        regime="offline",
        operations=operations,
        pmr_cents=pmr,
        compression_cents=compression,
        instruction_mass_cents=instruction_mass,
        residual_mass_cents=state.residual_mass_cents(),
        runtime_seconds=time.perf_counter() - start_time,
        operation_log=log,
        mean_acceleration_days=(
            acceleration_numerator / instruction_mass if instruction_mass else 0.0
        ),
        positive_only_mean_days=(
            positive_acceleration_numerator / positive_acceleration_mass
            if positive_acceleration_mass
            else 0.0
        ),
        accelerated_mass_share=(
            positive_acceleration_mass / instruction_mass if instruction_mass else 0.0
        ),
    )


def cycle_offline(
    base: TemporalState,
    cycles: Sequence[Cycle],
    *,
    keep_log: bool = True,
) -> RunResult:
    """Run the full-arrival-set greedy cycle schedule.

    This is a feasible noncausal heuristic, not an offline optimum or upper bound.
    """

    state = base.copy()
    start_time = time.perf_counter()
    heap: list[tuple[Any, ...]] = []
    for cycle_index, (nodes, edge_ids) in enumerate(cycles):
        capacity, day_index = state.cycle_common_day_capacity(edge_ids)
        if capacity <= 0:
            # A zero-capacity circuit cannot become feasible because capacities decrease.
            continue
        versions = tuple(int(state.edges[edge]["version"]) for edge in edge_ids)
        heapq.heappush(
            heap,
            (
                -len(nodes) * capacity,
                len(nodes),
                nodes,
                day_index,
                cycle_index,
                capacity,
                versions,
            ),
        )

    operations = 0
    pmr = 0
    log: list[Operation] = []
    while heap:
        _, length, nodes, day_index, cycle_index, capacity, versions = heapq.heappop(heap)
        edge_ids = cycles[cycle_index][1]
        current_versions = tuple(int(state.edges[edge]["version"]) for edge in edge_ids)
        if current_versions != versions:
            refreshed, refreshed_day = state.cycle_common_day_capacity(edge_ids)
            if refreshed > 0:
                heapq.heappush(
                    heap,
                    (
                        -length * refreshed,
                        length,
                        nodes,
                        refreshed_day,
                        cycle_index,
                        refreshed,
                        current_versions,
                    ),
                )
            continue

        refreshed, refreshed_day = state.cycle_common_day_capacity(edge_ids)
        if refreshed != capacity or refreshed_day != day_index:
            if refreshed > 0:
                heapq.heappush(
                    heap,
                    (
                        -length * refreshed,
                        length,
                        nodes,
                        refreshed_day,
                        cycle_index,
                        refreshed,
                        current_versions,
                    ),
                )
            continue

        edge_fragments: list[dict[str, Any]] = []
        for edge_id in edge_ids:
            fragments = state.consume(edge_id, day_index, capacity)
            edge_fragments.append(
                {"edge_id": edge_id, "fragments": _fragment_dicts(fragments)}
            )
        operations += 1
        pmr += length * capacity
        if keep_log:
            log.append(
                Operation(
                    kind="cycle",
                    day_index=day_index,
                    amount_cents=capacity,
                    payload={
                        "nodes": list(nodes),
                        "edges": list(edge_ids),
                        "edge_fragments": edge_fragments,
                    },
                )
            )

    return RunResult(
        method="cycle",
        regime="offline",
        operations=operations,
        pmr_cents=pmr,
        compression_cents=pmr,
        instruction_mass_cents=0,
        residual_mass_cents=state.residual_mass_cents(),
        runtime_seconds=time.perf_counter() - start_time,
        operation_log=log,
    )
