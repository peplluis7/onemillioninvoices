"""Mutable atomic-record state and exact common-day capacity operations."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Iterable, Sequence

import numpy as np

from .models import AtomicRecord, Fragment


def _node_order(label: str) -> tuple[int, int, str]:
    """Deterministic order that remains natural for labels such as ``cif:123``."""

    namespace, _, token = label.partition(":")
    try:
        numeric = int(token)
    except ValueError:
        numeric = 0
    namespace_rank = 0 if namespace in {"cif", "company"} else 1
    return numeric, namespace_rank, label


class TemporalState:
    """Residual invoice state with one exact amount-capacity vector per directed edge.

    For edge ``(u, v)`` and day ``t`` the vector stores

    ``c_uv(t; s) = sum_i r_i(s) * 1{issue_i <= t <= due_i}``.

    Consuming a fragment subtracts it from every day in that source record's active
    interval. Therefore capacities never increase during a run.
    """

    def __init__(
        self,
        records: Sequence[AtomicRecord],
        start_date: date,
        end_date: date,
    ) -> None:
        if end_date < start_date:
            raise ValueError("end_date cannot precede start_date")
        if not records:
            raise ValueError("at least one record is required")

        self.start_date = start_date
        self.end_date = end_date
        self.horizon = (end_date - start_date).days + 1
        nodes = sorted(
            {node for record in records for node in (record.debtor, record.creditor)},
            key=_node_order,
        )
        self.node_to_int = {node: index for index, node in enumerate(nodes)}
        self.int_to_node = nodes

        groups: dict[tuple[int, int], list[AtomicRecord]] = defaultdict(list)
        for record in records:
            issue_index = (record.issue_date - start_date).days
            due_index = (record.due_date - start_date).days
            if due_index < 0 or issue_index >= self.horizon:
                continue
            groups[
                (self.node_to_int[record.debtor], self.node_to_int[record.creditor])
            ].append(record)

        if not groups:
            raise ValueError("no records overlap the requested execution horizon")

        self.edge_map: dict[tuple[int, int], int] = {}
        self.edges: list[dict[str, Any]] = []
        self.uid_to_record: dict[str, AtomicRecord] = {}

        for edge_id, (uv, edge_records) in enumerate(sorted(groups.items())):
            self.edge_map[uv] = edge_id
            mutable_records: list[list[Any]] = []
            difference = np.zeros(self.horizon + 1, dtype=np.int64)
            for record in edge_records:
                issue_index = max(0, (record.issue_date - start_date).days)
                due_index = min(self.horizon - 1, (record.due_date - start_date).days)
                if due_index < issue_index:
                    continue
                self.uid_to_record[record.uid] = record
                mutable_records.append(
                    [
                        record.amount_cents,
                        issue_index,
                        due_index,
                        record.issue_date.toordinal(),
                        record.due_date.toordinal(),
                        record.uid,
                    ]
                )
                difference[issue_index] += record.amount_cents
                difference[due_index + 1] -= record.amount_cents

            # Deterministic consumption: earliest due, earliest issue, stable uid.
            mutable_records.sort(key=lambda row: (row[4], row[3], row[5]))
            capacity = np.cumsum(difference[:-1])
            self.edges.append(
                {
                    "u": uv[0],
                    "v": uv[1],
                    "records": mutable_records,
                    "capacity": capacity,
                    "version": 0,
                    "total_cents": sum(row[0] for row in mutable_records),
                }
            )

        self.initial_mass_cents = sum(
            int(edge["total_cents"]) for edge in self.edges
        )

    def copy(self) -> "TemporalState":
        duplicate = object.__new__(TemporalState)
        duplicate.start_date = self.start_date
        duplicate.end_date = self.end_date
        duplicate.horizon = self.horizon
        duplicate.initial_mass_cents = self.initial_mass_cents
        duplicate.node_to_int = self.node_to_int.copy()
        duplicate.int_to_node = self.int_to_node.copy()
        duplicate.edge_map = self.edge_map.copy()
        duplicate.uid_to_record = self.uid_to_record.copy()
        duplicate.edges = []
        for edge in self.edges:
            duplicate.edges.append(
                {
                    "u": edge["u"],
                    "v": edge["v"],
                    "records": [row.copy() for row in edge["records"]],
                    "capacity": edge["capacity"].copy(),
                    "version": 0,
                    "total_cents": edge["total_cents"],
                }
            )
        return duplicate

    def edge_capacity_on_day(self, edge_id: int, day_index: int) -> int:
        return int(self.edges[edge_id]["capacity"][day_index])

    def pair_common_day_capacity(self, first_edge: int, second_edge: int) -> tuple[int, int]:
        """Return exact ``max_t min(c_first(t), c_second(t))`` and earliest argmax."""

        matched = np.minimum(
            self.edges[first_edge]["capacity"],
            self.edges[second_edge]["capacity"],
        )
        day_index = int(matched.argmax())
        return int(matched[day_index]), day_index

    def cycle_common_day_capacity(self, edge_ids: Sequence[int]) -> tuple[int, int]:
        """Return exact fixed-circuit common-day capacity and earliest argmax."""

        if not edge_ids:
            raise ValueError("edge_ids must be non-empty")
        matched = self.edges[edge_ids[0]]["capacity"].copy()
        for edge_id in edge_ids[1:]:
            np.minimum(matched, self.edges[edge_id]["capacity"], out=matched)
        day_index = int(matched.argmax())
        return int(matched[day_index]), day_index

    def consume(self, edge_id: int, day_index: int, amount_cents: int) -> list[Fragment]:
        """Consume ``amount_cents`` from active atomic records on one edge."""

        if amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        edge = self.edges[edge_id]
        remaining = amount_cents
        fragments: list[Fragment] = []
        for row in edge["records"]:
            if remaining == 0:
                break
            residual, issue_index, due_index, issue_ord, due_ord, uid = row
            if residual <= 0 or not (issue_index <= day_index <= due_index):
                continue
            consumed = min(int(residual), remaining)
            row[0] -= consumed
            remaining -= consumed
            edge["total_cents"] -= consumed
            edge["capacity"][issue_index : due_index + 1] -= consumed
            fragments.append(
                Fragment(
                    uid=uid,
                    amount_cents=consumed,
                    issue_index=issue_index,
                    due_index=due_index,
                    issue_ordinal=issue_ord,
                    due_ordinal=due_ord,
                )
            )

        if remaining != 0:
            raise RuntimeError(
                f"cannot consume {amount_cents} cents on edge {edge_id}, "
                f"day {day_index}; {remaining} cents remain"
            )
        if np.any(edge["capacity"] < 0):
            raise AssertionError("negative edge-day capacity after consumption")
        edge["version"] += 1
        return fragments

    def residual_mass_cents(self) -> int:
        return sum(int(edge["total_cents"]) for edge in self.edges)

    def edge_tuple(self, edge_id: int) -> tuple[int, int]:
        edge = self.edges[edge_id]
        return int(edge["u"]), int(edge["v"])

    def active_edge_ids(self, day_index: int) -> Iterable[int]:
        for edge_id, edge in enumerate(self.edges):
            if edge["capacity"][day_index] > 0:
                yield edge_id
