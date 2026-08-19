"""Bounded elementary-circuit construction."""
from __future__ import annotations

from typing import Sequence

import networkx as nx

from .state import TemporalState

Cycle = tuple[tuple[int, ...], tuple[int, ...]]


def canonicalize_cycle(nodes: Sequence[int]) -> tuple[int, ...]:
    """Canonicalize a directed circuit by its lexicographically smallest rotation."""

    if len(nodes) < 2:
        raise ValueError("cycles must contain at least two nodes")
    sequence = list(nodes)
    rotation = min(
        range(len(sequence)),
        key=lambda index: tuple(sequence[index:] + sequence[:index]),
    )
    return tuple(sequence[rotation:] + sequence[:rotation])


def enumerate_cycles(state: TemporalState, length_bound: int = 8) -> list[Cycle]:
    """Enumerate every unique elementary directed circuit through ``length_bound``."""

    if length_bound < 2:
        raise ValueError("length_bound must be at least 2")
    graph = nx.DiGraph()
    graph.add_edges_from((edge["u"], edge["v"]) for edge in state.edges)
    unique: dict[tuple[int, ...], tuple[int, ...]] = {}
    for node_cycle in nx.simple_cycles(graph, length_bound=length_bound):
        canonical = canonicalize_cycle(node_cycle)
        edge_ids = tuple(
            state.edge_map[(canonical[index], canonical[(index + 1) % len(canonical)])]
            for index in range(len(canonical))
        )
        unique[canonical] = edge_ids
    return sorted(unique.items(), key=lambda item: (len(item[0]), item[0]))
