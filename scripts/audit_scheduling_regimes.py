#!/usr/bin/env python3
"""Print the canonical FASG < CDG example and full-information LP bound."""
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from temporal_invoice_clearing.algorithms import path_daily, path_offline
from temporal_invoice_clearing.models import AtomicRecord
from temporal_invoice_clearing.optimization import full_information_lp_upper_bound
from temporal_invoice_clearing.state import TemporalState


def main() -> None:
    records = [
        AtomicRecord("ab", "A", "B", 10, date(2025, 1, 1), date(2025, 1, 3)),
        AtomicRecord("bc", "B", "C", 10, date(2025, 1, 3), date(2025, 1, 3)),
        AtomicRecord("bd", "B", "D", 6, date(2025, 1, 1), date(2025, 1, 1)),
        AtomicRecord("eb", "E", "B", 6, date(2025, 1, 2), date(2025, 1, 3)),
    ]
    state = TemporalState(records, date(2025, 1, 1), date(2025, 1, 3))
    fasg = path_offline(state, keep_log=True)
    cdg = path_daily(state, keep_log=True)
    bound = full_information_lp_upper_bound(
        records, date(2025, 1, 1), date(2025, 1, 3), move_set="path"
    )
    print(f"FASG path PMR: {fasg.pmr_cents}")
    print(f"CDG path PMR:  {cdg.pmr_cents}")
    print(f"LP upper bound: {bound['objective_cents']:.0f}")
    if not (fasg.pmr_cents == 10 and cdg.pmr_cents == 16):
        raise SystemExit("unexpected regression result")
    if not bound["success"] or bound["objective_cents"] < cdg.pmr_cents:
        raise SystemExit("full-information dominance check failed")


if __name__ == "__main__":
    main()
