"""Input and output for the public normalized atomic-record schema."""
from __future__ import annotations

import csv
import gzip
import hashlib
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable, Sequence, TextIO

from .models import AtomicRecord

REQUIRED_COLUMNS = {
    "uid",
    "debtor",
    "creditor",
    "amount_cents",
    "issue_date",
    "due_date",
}
OPTIONAL_COLUMNS = {"status", "source", "fingerprint", "cohort"}


def _open_text(path: Path, mode: str) -> TextIO:
    """Open UTF-8 CSV text, optionally gzip-compressed by filename suffix."""

    if path.suffix.lower() == ".gz":
        return gzip.open(path, mode + "t", encoding="utf-8-sig" if "r" in mode else "utf-8", newline="")
    return path.open(mode, encoding="utf-8-sig" if "r" in mode else "utf-8", newline="")


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"invalid ISO date {value!r}; expected YYYY-MM-DD") from exc


def parse_amount_cents(value: str) -> int:
    text = value.strip()
    if not text:
        raise ValueError("amount_cents cannot be blank")
    try:
        amount = int(text)
    except ValueError as exc:
        raise ValueError(f"amount_cents must be an integer, received {value!r}") from exc
    if amount <= 0:
        raise ValueError("amount_cents must be positive")
    return amount


def euros_to_cents(value: str) -> int:
    """Convert a decimal euro string to integer cents without binary floating point."""

    try:
        decimal_value = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError(f"invalid euro amount {value!r}") from exc
    return int((decimal_value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def stable_fingerprint(parts: Iterable[object]) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_atomic_csv(path: str | Path) -> list[AtomicRecord]:
    """Load a UTF-8 CSV conforming to ``docs/data_schema.md``."""

    input_path = Path(path)
    records: list[AtomicRecord] = []
    seen_uids: set[str] = set()
    with _open_text(input_path, "r") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("input CSV has no header")
        missing = REQUIRED_COLUMNS.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"input CSV is missing required columns: {sorted(missing)}")
        for line_number, row in enumerate(reader, start=2):
            try:
                uid = (row.get("uid") or "").strip()
                if uid in seen_uids:
                    raise ValueError(f"duplicate uid {uid!r}")
                seen_uids.add(uid)
                record = AtomicRecord(
                    uid=uid,
                    debtor=(row.get("debtor") or "").strip(),
                    creditor=(row.get("creditor") or "").strip(),
                    amount_cents=parse_amount_cents(row.get("amount_cents") or ""),
                    issue_date=parse_iso_date(row.get("issue_date") or ""),
                    due_date=parse_iso_date(row.get("due_date") or ""),
                    status=(row.get("status") or "").strip(),
                    source=(row.get("source") or "").strip(),
                    fingerprint=(row.get("fingerprint") or "").strip(),
                    cohort=(row.get("cohort") or "").strip(),
                )
            except ValueError as exc:
                raise ValueError(f"{input_path}:{line_number}: {exc}") from exc
            records.append(record)
    if not records:
        raise ValueError("input CSV contains no records")
    return records


def write_atomic_csv(path: str | Path, records: Sequence[AtomicRecord]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "uid",
        "debtor",
        "creditor",
        "amount_cents",
        "issue_date",
        "due_date",
        "status",
        "source",
        "fingerprint",
        "cohort",
    ]
    with _open_text(output_path, "w") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "uid": record.uid,
                    "debtor": record.debtor,
                    "creditor": record.creditor,
                    "amount_cents": record.amount_cents,
                    "issue_date": record.issue_date.isoformat(),
                    "due_date": record.due_date.isoformat(),
                    "status": record.status,
                    "source": record.source,
                    "fingerprint": record.fingerprint,
                    "cohort": record.cohort,
                }
            )
