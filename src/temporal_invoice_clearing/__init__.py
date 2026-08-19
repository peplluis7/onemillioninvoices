"""Atomic common-day invoice clearing reference implementation."""

from .algorithms import cycle_daily, cycle_offline, path_daily, path_offline
from .hybrid import sequential_daily_hybrid
from .optimization import full_information_lp_upper_bound
from .attribution import cohort_daily_curve_attribution, cohort_pmr_attribution
from .cycles import enumerate_cycles
from .io import load_atomic_csv, write_atomic_csv
from .metrics import area_under_curve, topology_metrics
from .models import AtomicRecord, Fragment, Operation, RunResult
from .replay import replay_validate
from .rolling import run_sequential_annual_cdg
from .state import TemporalState

__all__ = [
    "AtomicRecord",
    "Fragment",
    "Operation",
    "RunResult",
    "TemporalState",
    "area_under_curve",
    "cohort_daily_curve_attribution",
    "cohort_pmr_attribution",
    "cycle_daily",
    "cycle_offline",
    "enumerate_cycles",
    "load_atomic_csv",
    "path_daily",
    "path_offline",
    "sequential_daily_hybrid",
    "full_information_lp_upper_bound",
    "replay_validate",
    "run_sequential_annual_cdg",
    "topology_metrics",
    "write_atomic_csv",
]

__version__ = "1.3.0"
