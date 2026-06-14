from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def _run_benchmark(spec_path: Path) -> None:
    script = PROJECT_ROOT / "verification" / "scripts" / "run_engine_benchmark.py"
    cmd = [sys.executable, str(script), "--spec", str(spec_path)]
    subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True)


def _extract_ratio(summary: Dict[str, Any], ratio_key: str) -> float:
    value = summary.get(ratio_key)
    if not isinstance(value, (int, float)):
        raise ValueError(f"ratio key '{ratio_key}' missing or not numeric in benchmark summary")
    return float(value)


def _aggregate_gate_ratio(ratios: List[float]) -> float:
    if not ratios:
        raise ValueError("ratios must not be empty")
    return float(statistics.median(ratios))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run node_ir speed gate and enforce threshold.")
    parser.add_argument(
        "--spec",
        default="verification/benchmarks/fixtures/benchmark_node_ir_speed_gate.json",
        help="Benchmark spec path.",
    )
    parser.add_argument(
        "--threshold-file",
        default="verification/benchmarks/thresholds/node_ir_speed_gate_thresholds.json",
        help="Threshold json path.",
    )
    parser.add_argument(
        "--profile",
        choices=["auto", "rust"],
        default="auto",
        help="Gate profile to enforce.",
    )
    parser.add_argument(
        "--gate-runs",
        type=int,
        default=3,
        help="How many benchmark runs to aggregate for gate decision (median).",
    )
    args = parser.parse_args()

    spec_path = _resolve(args.spec)
    threshold_path = _resolve(args.threshold_file)
    thresholds = _load_json(threshold_path)
    profile = thresholds.get(args.profile, {})
    if not isinstance(profile, dict):
        raise ValueError(f"missing profile '{args.profile}' in threshold file")

    ratio_key = str(profile.get("ratio_key", "node_ir_auto_relative_speed"))
    max_ratio = float(profile.get("max_ratio", 1.0))

    benchmark_id = _load_json(spec_path).get("benchmark_id")
    if not isinstance(benchmark_id, str) or not benchmark_id:
        raise ValueError("benchmark spec missing benchmark_id")

    summary_path = PROJECT_ROOT / "verification" / "results" / "benchmarks" / benchmark_id / "summary.json"
    gate_runs = max(1, int(args.gate_runs))
    ratios: List[float] = []
    for _ in range(gate_runs):
        _run_benchmark(spec_path)
        summary = _load_json(summary_path)
        ratios.append(_extract_ratio(summary, ratio_key))

    gate_ratio = _aggregate_gate_ratio(ratios)
    ratios_str = ",".join(f"{r:.6f}" for r in ratios)
    print(
        f"[speed-gate] profile={args.profile} ratio_key={ratio_key} "
        f"runs={gate_runs} ratios=[{ratios_str}] median={gate_ratio:.6f} max={max_ratio:.6f}"
    )
    if gate_ratio > max_ratio:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
