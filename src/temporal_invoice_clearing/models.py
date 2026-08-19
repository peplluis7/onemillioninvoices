"""Core data models for atomic temporal invoice clearing.

All monetary amounts are integer cents. Dates are inclusive: a record is active on
``t`` when ``issue_date <= t <= due_date``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class AtomicRecord:
    """One source invoice record retained as an indivisible provenance object.

    The record may be partially consumed, but it is never merged with another record.
    ``cohort`` is optional and is useful when a short execution bridge is admitted after
    the end of an issue year.
    """

    uid: str
    debtor: str
    creditor: str
    amount_cents: int
    issue_date: date
    due_date: date
    status: str = ""
    source: str = ""
    fingerprint: str = ""
    cohort: str = ""

    def __post_init__(self) -> None:
        if not self.uid:
            raise ValueError("uid must be non-empty")
        if not self.debtor or not self.creditor:
            raise ValueError("debtor and creditor must be non-empty")
        if self.debtor == self.creditor:
            raise ValueError("self-obligations are not permitted")
        if self.amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        if self.due_date < self.issue_date:
            raise ValueError("due_date cannot precede issue_date")


@dataclass(frozen=True, slots=True)
class Fragment:
    """A consumed portion of one atomic record."""

    uid: str
    amount_cents: int
    issue_index: int
    due_index: int
    issue_ordinal: int
    due_ordinal: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Operation:
    """Serializable operation log used by replay validation."""

    kind: Literal["path", "cycle"]
    day_index: int
    amount_cents: int
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "day_index": self.day_index,
            "amount_cents": self.amount_cents,
            **self.payload,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Operation":
        """Reconstruct an operation from a serialized fragment log."""

        required = {"kind", "day_index", "amount_cents"}
        missing = required.difference(payload)
        if missing:
            raise ValueError(f"operation log is missing fields: {sorted(missing)}")
        body = {key: value for key, value in payload.items() if key not in required}
        return cls(
            kind=payload["kind"],
            day_index=int(payload["day_index"]),
            amount_cents=int(payload["amount_cents"]),
            payload=body,
        )


@dataclass(slots=True)
class RunResult:
    """Terminal metrics, daily curve, and auditable operations for one run."""

    method: str
    regime: Literal["offline", "daily"]
    operations: int
    pmr_cents: int
    compression_cents: int
    instruction_mass_cents: int
    residual_mass_cents: int
    runtime_seconds: float
    daily_curve_cents: list[int] = field(default_factory=list)
    operation_log: list[Operation] = field(default_factory=list)
    mean_acceleration_days: float = 0.0
    positive_only_mean_days: float = 0.0
    accelerated_mass_share: float = 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "regime": self.regime,
            "operations": self.operations,
            "pmr_cents": self.pmr_cents,
            "compression_cents": self.compression_cents,
            "instruction_mass_cents": self.instruction_mass_cents,
            "residual_mass_cents": self.residual_mass_cents,
            "runtime_seconds": self.runtime_seconds,
            "mean_acceleration_days": self.mean_acceleration_days,
            "positive_only_mean_days": self.positive_only_mean_days,
            "accelerated_mass_share": self.accelerated_mass_share,
        }

    def to_dict(self, include_curve: bool = True, include_log: bool = True) -> dict[str, Any]:
        result = self.summary()
        if include_curve:
            result["daily_curve_cents"] = list(self.daily_curve_cents)
        if include_log:
            result["operation_log"] = [op.to_dict() for op in self.operation_log]
        return result

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunResult":
        """Reconstruct a run result written by :meth:`to_dict`."""

        return cls(
            method=str(payload["method"]),
            regime=payload["regime"],
            operations=int(payload["operations"]),
            pmr_cents=int(payload["pmr_cents"]),
            compression_cents=int(payload["compression_cents"]),
            instruction_mass_cents=int(payload["instruction_mass_cents"]),
            residual_mass_cents=int(payload["residual_mass_cents"]),
            runtime_seconds=float(payload.get("runtime_seconds", 0.0)),
            daily_curve_cents=[int(value) for value in payload.get("daily_curve_cents", [])],
            operation_log=[
                Operation.from_dict(operation)
                for operation in payload.get("operation_log", [])
            ],
            mean_acceleration_days=float(payload.get("mean_acceleration_days", 0.0)),
            positive_only_mean_days=float(payload.get("positive_only_mean_days", 0.0)),
            accelerated_mass_share=float(payload.get("accelerated_mass_share", 0.0)),
        )
