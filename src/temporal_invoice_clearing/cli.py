"""Command-line interface for the reference implementation."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence, TextIO

from .algorithms import cycle_daily, cycle_offline, path_daily, path_offline
from .cycles import Cycle, enumerate_cycles
from .io import load_atomic_csv
from .metrics import area_under_curve, topology_metrics
from .models import RunResult
from .plotting import plot_cumulative_pmr
from .replay import replay_validate
from .state import TemporalState


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("dates must use YYYY-MM-DD") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _open_json_text(path: Path, mode: str) -> TextIO:
    if path.suffix.lower() == ".gz":
        return gzip.open(path, mode + "t", encoding="utf-8")
    return path.open(mode, encoding="utf-8")


def _write_log(path: Path, result_dict: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _open_json_text(path, "w") as handle:
        json.dump(result_dict, handle, separators=(",", ":"))


def _read_log(path: Path) -> RunResult:
    with _open_json_text(path, "r") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"run log must contain a JSON object: {path}")
    return RunResult.from_dict(payload)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_curve(
    path: Path,
    start_date: date,
    cycle_curve: Sequence[int] | None,
    path_curve: Sequence[int] | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    length = max(len(cycle_curve or []), len(path_curve or []))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "date",
                "cycle_pmr_cents",
                "path_pmr_cents",
                "advantage_cents",
            ]
        )
        for index in range(length):
            cycle_value = int(cycle_curve[index]) if cycle_curve is not None else 0
            path_value = int(path_curve[index]) if path_curve is not None else 0
            writer.writerow(
                [
                    (start_date + timedelta(days=index)).isoformat(),
                    cycle_value,
                    path_value,
                    path_value - cycle_value,
                ]
            )


def _topology_sha256(state: TemporalState) -> str:
    """Hash the labeled directed topology used by a cycle cache."""

    digest = hashlib.sha256()
    for edge in state.edges:
        debtor = state.int_to_node[int(edge["u"])]
        creditor = state.int_to_node[int(edge["v"])]
        digest.update(debtor.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(creditor.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _load_or_build_cycles(
    state: TemporalState,
    length_bound: int,
    cache_path: Path | None,
) -> list[Cycle]:
    """Load or construct a topology-bound JSON cycle cache.

    Unlike a pickle cache, this representation is inspectable and does not execute code
    when opened. The topology digest prevents accidental reuse on a different graph.
    """

    topology_hash = _topology_sha256(state)
    if cache_path is not None and cache_path.exists():
        with _open_json_text(cache_path, "r") as handle:
            payload = json.load(handle)
        if int(payload.get("length_bound", -1)) != length_bound:
            raise ValueError("cycle cache length bound does not match the requested bound")
        if payload.get("topology_sha256") != topology_hash:
            raise ValueError("cycle cache topology does not match the input graph")
        cycles: list[Cycle] = []
        for entry in payload.get("cycles", []):
            nodes = tuple(int(value) for value in entry["nodes"])
            edges = tuple(int(value) for value in entry["edges"])
            cycles.append((nodes, edges))
        return cycles

    cycles = enumerate_cycles(state, length_bound=length_bound)
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": "temporal-invoice-clearing-cycle-cache-v1",
            "length_bound": length_bound,
            "topology_sha256": topology_hash,
            "cycles": [
                {"nodes": list(nodes), "edges": list(edges)} for nodes, edges in cycles
            ],
        }
        with _open_json_text(cache_path, "w") as handle:
            json.dump(payload, handle, separators=(",", ":"))
    return cycles


def run_command(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    records = load_atomic_csv(input_path)
    state = TemporalState(records, args.start_date, args.end_date)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    selected_methods = {"path", "cycle"} if args.method == "both" else {args.method}
    selected_regimes = {"offline", "daily"} if args.regime == "both" else {args.regime}
    keep_log = args.write_log or not args.no_validate

    cycles: list[Cycle] = []
    if "cycle" in selected_methods:
        cycles = _load_or_build_cycles(
            state,
            args.cycle_bound,
            Path(args.cycle_cache) if args.cycle_cache else None,
        )

    run_results: dict[str, Any] = {}
    run_objects: dict[str, RunResult] = {}
    for regime in sorted(selected_regimes):
        for method in sorted(selected_methods):
            key = f"{regime}_{method}"
            if regime == "offline" and method == "path":
                result = path_offline(state, keep_log=keep_log)
            elif regime == "offline" and method == "cycle":
                result = cycle_offline(state, cycles, keep_log=keep_log)
            elif regime == "daily" and method == "path":
                result = path_daily(state, keep_log=keep_log)
            else:
                result = cycle_daily(state, cycles, keep_log=keep_log)

            summary = result.summary()
            summary["pmr_share"] = result.pmr_cents / state.initial_mass_cents
            if result.daily_curve_cents:
                summary["auc_cent_days"] = area_under_curve(result.daily_curve_cents)
            run_results[key] = summary
            run_objects[key] = result

            if not args.no_validate:
                validation = replay_validate(records, state, result)
                _write_json(output_dir / f"{key}_validation.json", validation)
                if not validation["all_pass"]:
                    raise RuntimeError(f"independent replay failed for {key}")
            if args.write_log:
                _write_log(
                    output_dir / f"{key}_run.json.gz",
                    result.to_dict(include_curve=True, include_log=True),
                )

    audit = {
        "input_name": input_path.name,
        "input_sha256": _sha256_file(input_path),
        "start_date": args.start_date.isoformat(),
        "end_date": args.end_date.isoformat(),
        "record_count_submitted": len(records),
        "record_count_in_horizon": len(state.uid_to_record),
        "initial_mass_cents": state.initial_mass_cents,
        "cycle_bound": args.cycle_bound,
        "cycle_count": len(cycles),
        "topology": topology_metrics(records),
        "runs": run_results,
    }
    _write_json(output_dir / "summary.json", audit)

    if "daily" in selected_regimes:
        cycle_curve = (
            run_objects["daily_cycle"].daily_curve_cents
            if "daily_cycle" in run_objects
            else None
        )
        path_curve = (
            run_objects["daily_path"].daily_curve_cents
            if "daily_path" in run_objects
            else None
        )
        _write_curve(output_dir / "daily_curves.csv", args.start_date, cycle_curve, path_curve)

    print(json.dumps(audit, indent=2, sort_keys=True))
    return 0


def replay_command(args: argparse.Namespace) -> int:
    records = load_atomic_csv(args.input)
    state = TemporalState(records, args.start_date, args.end_date)
    result_dir = Path(args.result_dir)
    requested = list(args.run or [])
    if requested:
        log_paths = [result_dir / f"{name}_run.json.gz" for name in requested]
    else:
        log_paths = sorted(result_dir.glob("*_run.json.gz"))
    if not log_paths:
        raise ValueError("no compressed run logs were found; run with --write-log first")

    validations: dict[str, Any] = {}
    for log_path in log_paths:
        if not log_path.exists():
            raise ValueError(f"run log does not exist: {log_path}")
        result = _read_log(log_path)
        key = log_path.name.removesuffix("_run.json.gz")
        validation = replay_validate(records, state, result)
        validations[key] = validation
        _write_json(result_dir / f"{key}_validation.json", validation)
    payload = {"all_pass": all(item["all_pass"] for item in validations.values()), "runs": validations}
    _write_json(result_dir / "replay_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not payload["all_pass"]:
        raise RuntimeError("one or more replay validations failed")
    return 0


def plot_command(args: argparse.Namespace) -> int:
    curve_path = Path(args.curves)
    with curve_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("daily curve file is empty")
    required = {"date", "cycle_pmr_cents", "path_pmr_cents"}
    missing = required.difference(rows[0])
    if missing:
        raise ValueError(f"daily curve file is missing columns: {sorted(missing)}")
    start_date = date.fromisoformat(rows[0]["date"])
    def parse_cents(value: str) -> float:
        return float(Fraction(value))

    cycle_curve = [parse_cents(row["cycle_pmr_cents"]) for row in rows]
    path_curve = [parse_cents(row["path_pmr_cents"]) for row in rows]
    plot_cumulative_pmr(
        args.output,
        start_date,
        cycle_curve,
        path_curve,
        title=args.title,
        bridge_start=args.bridge_start,
    )
    return 0


def topology_command(args: argparse.Namespace) -> int:
    records = load_atomic_csv(args.input)
    payload = topology_metrics(records)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="temporal-invoice-clearing",
        description="Atomic common-day path and bounded-cycle invoice clearing.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="execute one normalized cohort")
    run_parser.add_argument("--input", "--records", dest="input", required=True, help="normalized atomic CSV or CSV.GZ")
    run_parser.add_argument("--output", "--out", dest="output", required=True, help="output directory")
    run_parser.add_argument("--start-date", "--start", dest="start_date", required=True, type=_date)
    run_parser.add_argument("--end-date", "--end", dest="end_date", required=True, type=_date)
    run_parser.add_argument("--regime", choices=("offline", "daily", "both"), default="both")
    run_parser.add_argument("--method", choices=("path", "cycle", "both"), default="both")
    run_parser.add_argument("--cycle-bound", "--max-cycle-length", dest="cycle_bound", type=int, default=8)
    run_parser.add_argument(
        "--cycle-cache",
        help="optional inspectable JSON or JSON.GZ cycle cache bound to this topology",
    )
    run_parser.add_argument(
        "--write-log",
        action="store_true",
        help="write compressed fragment-level operation logs",
    )
    run_parser.add_argument(
        "--no-validate",
        action="store_true",
        help="skip independent fragment-level replay",
    )
    run_parser.set_defaults(func=run_command)

    replay_parser = subparsers.add_parser(
        "replay", help="independently replay compressed fragment logs"
    )
    replay_parser.add_argument("--input", "--records", dest="input", required=True)
    replay_parser.add_argument("--result-dir", required=True)
    replay_parser.add_argument("--start-date", "--start", dest="start_date", required=True, type=_date)
    replay_parser.add_argument("--end-date", "--end", dest="end_date", required=True, type=_date)
    replay_parser.add_argument(
        "--run",
        action="append",
        choices=("offline_cycle", "offline_path", "daily_cycle", "daily_path"),
        help="replay one named run; repeat the option or omit it to replay every log",
    )
    replay_parser.set_defaults(func=replay_command)

    plot_parser = subparsers.add_parser("plot", help="plot a daily-curves CSV")
    plot_parser.add_argument("--curves", required=True)
    plot_parser.add_argument("--output", required=True)
    plot_parser.add_argument("--title", default="Daily cumulative payable-mass reduction")
    plot_parser.add_argument("--bridge-start", type=_date)
    plot_parser.set_defaults(func=plot_command)

    topology_parser = subparsers.add_parser(
        "topology", help="report invoice-network diagnostics"
    )
    topology_parser.add_argument("--input", "--records", dest="input", required=True)
    topology_parser.set_defaults(func=topology_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    sys.exit(main())
