"""Graph, temporal, and accounting metrics."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Sequence

import networkx as nx

from .models import AtomicRecord


def topology_metrics(records: Sequence[AtomicRecord]) -> dict[str, float | int]:
    graph = nx.DiGraph()
    weights: dict[tuple[str, str], int] = defaultdict(int)
    for record in records:
        weights[(record.debtor, record.creditor)] += record.amount_cents
    graph.add_edges_from(weights)
    node_count = graph.number_of_nodes()
    edge_count = graph.number_of_edges()
    local_paths = sum(graph.in_degree(node) * graph.out_degree(node) for node in graph.nodes)
    strongly_connected = list(nx.strongly_connected_components(graph))
    cyclic_nodes: set[str] = set()
    for component in strongly_connected:
        if len(component) > 1:
            cyclic_nodes.update(component)
    cyclic_paths = sum(
        graph.in_degree(node) * graph.out_degree(node) for node in cyclic_nodes
    )
    weakly_connected = list(nx.weakly_connected_components(graph))
    reciprocated = sum(1 for u, v in graph.edges if graph.has_edge(v, u))
    return {
        "firms": node_count,
        "edges": edge_count,
        "directed_density": nx.density(graph),
        "reciprocity": reciprocated / edge_count if edge_count else 0.0,
        "weak_components": len(weakly_connected),
        "largest_wcc_share": (
            max((len(component) for component in weakly_connected), default=0) / node_count
            if node_count
            else 0.0
        ),
        "strong_components": len(strongly_connected),
        "largest_scc_share": (
            max((len(component) for component in strongly_connected), default=0) / node_count
            if node_count
            else 0.0
        ),
        "firms_nontrivial_scc": len(cyclic_nodes),
        "local_two_edge_paths": local_paths,
        "cyclic_local_two_edge_paths": cyclic_paths,
        "cycle_closure_ratio": cyclic_paths / local_paths if local_paths else 0.0,
        "topological_opportunity_ratio": (
            local_paths / cyclic_paths if cyclic_paths else math.inf
        ),
    }


def area_under_curve(curve_cents: Sequence[int]) -> int:
    """Discrete cumulative-relief area in cent-days."""

    return int(sum(curve_cents))


def dated_curve(
    start_date: date,
    values_cents: Sequence[int],
) -> list[tuple[date, int]]:
    return [
        (start_date + timedelta(days=index), int(value))
        for index, value in enumerate(values_cents)
    ]
