"""Independent fragment-level replay of a benchmark run."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from .models import AtomicRecord, RunResult
from .state import TemporalState


def replay_validate(
    records: Sequence[AtomicRecord],
    base: TemporalState,
    result: RunResult,
) -> dict[str, Any]:
    """Replay operations without invoking the benchmark executor.

    Checks temporal eligibility, edge identity, source-record consumption, PMR and
    instruction identities, nonnegative residuals, and combined firm net positions.
    """

    by_uid = base.uid_to_record.copy()
    remaining = {uid: record.amount_cents for uid, record in by_uid.items()}

    initial_net: dict[int, int] = defaultdict(int)
    for record in by_uid.values():
        if record.debtor not in base.node_to_int or record.creditor not in base.node_to_int:
            continue
        debtor = base.node_to_int[record.debtor]
        creditor = base.node_to_int[record.creditor]
        initial_net[creditor] += record.amount_cents
        initial_net[debtor] -= record.amount_cents

    instructions_in: dict[int, int] = defaultdict(int)
    instructions_out: dict[int, int] = defaultdict(int)
    temporal_violations = 0
    bad_edge_fragments = 0
    bad_sums = 0
    overconsumed = 0
    reconstructed_pmr = 0
    reconstructed_instruction = 0
    fragment_count = 0

    def apply_fragments(
        edge_id: int,
        fragments: Sequence[dict[str, Any]],
        expected_cents: int,
        day_index: int,
    ) -> None:
        nonlocal temporal_violations, bad_edge_fragments, bad_sums, overconsumed, fragment_count
        edge = base.edges[edge_id]
        total = 0
        for fragment in fragments:
            fragment_count += 1
            uid = str(fragment["uid"])
            amount = int(fragment["amount_cents"])
            record = by_uid.get(uid)
            if record is None:
                bad_edge_fragments += 1
                continue
            if (
                base.node_to_int.get(record.debtor) != edge["u"]
                or base.node_to_int.get(record.creditor) != edge["v"]
            ):
                bad_edge_fragments += 1
            issue_index = max(0, (record.issue_date - base.start_date).days)
            due_index = min(base.horizon - 1, (record.due_date - base.start_date).days)
            if not (issue_index <= day_index <= due_index):
                temporal_violations += 1
            if amount <= 0 or remaining.get(uid, 0) < amount:
                overconsumed += 1
            remaining[uid] = remaining.get(uid, 0) - amount
            total += amount
        if total != expected_cents:
            bad_sums += 1

    for operation in result.operation_log:
        payload = operation.payload
        amount = operation.amount_cents
        day_index = operation.day_index
        if operation.kind == "cycle":
            for edge_entry in payload["edge_fragments"]:
                apply_fragments(
                    int(edge_entry["edge_id"]),
                    edge_entry["fragments"],
                    amount,
                    day_index,
                )
            reconstructed_pmr += len(payload["edges"]) * amount
        else:
            apply_fragments(
                int(payload["in_edge"]), payload["in_fragments"], amount, day_index
            )
            apply_fragments(
                int(payload["out_edge"]), payload["out_fragments"], amount, day_index
            )
            if bool(payload["bilateral"]):
                reconstructed_pmr += 2 * amount
            else:
                reconstructed_pmr += amount
                reconstructed_instruction += amount
                payer = int(payload["payer"])
                payee = int(payload["payee"])
                instructions_out[payer] += amount
                instructions_in[payee] += amount

    residual_mass = sum(remaining.values())
    terminal_net: dict[int, int] = defaultdict(int)
    for uid, residual in remaining.items():
        record = by_uid[uid]
        if record.debtor not in base.node_to_int or record.creditor not in base.node_to_int:
            continue
        debtor = base.node_to_int[record.debtor]
        creditor = base.node_to_int[record.creditor]
        terminal_net[creditor] += residual
        terminal_net[debtor] -= residual
    for node, amount in instructions_in.items():
        terminal_net[node] += amount
    for node, amount in instructions_out.items():
        terminal_net[node] -= amount

    all_nodes = set(initial_net).union(terminal_net)
    max_net_error = max(
        (abs(initial_net[node] - terminal_net[node]) for node in all_nodes),
        default=0,
    )
    negative_residuals = sum(1 for value in remaining.values() if value < 0)
    identity_pmr = base.initial_mass_cents - (residual_mass + reconstructed_instruction)

    checks: dict[str, Any] = {
        "fragment_count": fragment_count,
        "temporal_violations": temporal_violations,
        "bad_edge_fragments": bad_edge_fragments,
        "bad_sums": bad_sums,
        "overconsumed_fragments": overconsumed,
        "negative_residuals": negative_residuals,
        "max_net_error_cents": max_net_error,
        "reconstructed_pmr_cents": reconstructed_pmr,
        "reported_pmr_cents": result.pmr_cents,
        "pmr_identity_cents": identity_pmr,
        "reconstructed_instruction_mass_cents": reconstructed_instruction,
        "reported_instruction_mass_cents": result.instruction_mass_cents,
        "residual_mass_cents": residual_mass,
        "reported_residual_mass_cents": result.residual_mass_cents,
    }
    checks["all_pass"] = all(
        (
            temporal_violations == 0,
            bad_edge_fragments == 0,
            bad_sums == 0,
            overconsumed == 0,
            negative_residuals == 0,
            max_net_error == 0,
            reconstructed_pmr == result.pmr_cents,
            identity_pmr == result.pmr_cents,
            reconstructed_instruction == result.instruction_mass_cents,
            residual_mass == result.residual_mass_cents,
        )
    )
    return checks
