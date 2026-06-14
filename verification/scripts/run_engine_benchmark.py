from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_repo_path(path_str: str) -> Path:
    candidate = Path(path_str)
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


def _load_benchmark_spec(spec_path: Path) -> Dict[str, Any]:
    spec = _load_json(spec_path)
    if "config_path" in spec:
        config = _load_json(_resolve_repo_path(spec["config_path"]))
    else:
        config = spec["inline_config"]
    return {
        "benchmark_id": spec["benchmark_id"],
        "description": spec.get("description", ""),
        "config": config,
        "modes": spec.get("modes", ["node_ir_auto"]),
        "warmup_runs": int(spec.get("warmup_runs", 1)),
        "repeat": int(spec.get("repeat", 3)),
    }


def _prepare_config(config: Dict[str, Any]) -> Dict[str, Any]:
    prepared = json.loads(json.dumps(config))
    file_config = prepared.get("dataloader", {}).get("file_config", {})
    file_path = file_config.get("file_path")
    if isinstance(file_path, str) and file_path.strip():
        file_config["file_path"] = str(_resolve_repo_path(file_path))
    return prepared


def _run_single_mode(
    config: Dict[str, Any],
    mode: str,
    warmup_runs: int,
    repeat: int,
    profile: bool,
) -> Dict[str, Any]:
    from autorunner.DataLoader_autorunner import DataLoaderAutorunner
    from backtester.NodeIRExecutor_backtester import NodeIRExecutorBacktester
    from autorunner.StrategyCompiler import StrategyCompiler

    prepared = _prepare_config(config)
    prepared.setdefault("backtester", {})["engine_mode"] = mode

    loader = DataLoaderAutorunner()
    data = loader.load_data(prepared["dataloader"])

    if mode not in {"node_ir_python_numba", "node_ir_rust_kernel", "node_ir_auto"}:
        raise ValueError(f"Unsupported benchmark mode: {mode}")

    durations: List[float] = []
    warmup_durations: List[float] = []
    results_count = 0
    trade_rows = 0
    profile_text = None

    total_runs = warmup_runs + repeat
    for run_idx in range(total_runs):
        profiler = cProfile.Profile() if profile and run_idx == 0 else None
        start = time.perf_counter()
        if profiler is not None:
            profiler.enable()
        backtester_cfg = prepared.get("backtester", {})
        strategy_path = backtester_cfg.get("strategy_contract_path")
        if not isinstance(strategy_path, str) or not strategy_path:
            raise ValueError("node_ir benchmark mode requires backtester.strategy_contract_path")
        feature_path = backtester_cfg.get("feature_contract_path")
        compiler = StrategyCompiler()
        compile_result = compiler.compile_from_paths(
            strategy_contract_path=str(_resolve_repo_path(strategy_path)),
            feature_contract_path=str(_resolve_repo_path(feature_path)) if isinstance(feature_path, str) and feature_path else None,
            output_dir=None,
        )
        if not compile_result.valid:
            raise ValueError(f"node_ir benchmark compile failed: {compile_result.errors}")
        executor = NodeIRExecutorBacktester(data)
        trading_params = dict(prepared.get("backtester", {}).get("trading_params", {}) or {})
        if mode == "node_ir_rust_kernel":
            trading_params["execution_backend"] = "rust_kernel"
        elif mode == "node_ir_python_numba":
            trading_params["execution_backend"] = "python_numba"
        else:
            trading_params["execution_backend"] = "auto"
        results = executor.run_from_paths(
            strategy_contract_path=str(_resolve_repo_path(strategy_path)),
            feature_contract_path=str(_resolve_repo_path(feature_path)) if isinstance(feature_path, str) and feature_path else None,
            execution_plan=compile_result.execution_plan,
            trading_params=trading_params,
            predictor_column=str(prepared.get("backtester", {}).get("selected_predictor", "X")),
            symbol="BENCH",
            backtest_id_prefix="bench_node_ir",
        )
        if profiler is not None:
            profiler.disable()
        elapsed = time.perf_counter() - start
        is_measured_run = run_idx >= warmup_runs
        if is_measured_run:
            durations.append(elapsed)
        else:
            warmup_durations.append(elapsed)

        if run_idx == 0:
            results_count = len(results)
            trade_rows = sum(len(item.get("records", [])) for item in results)
            if profiler is not None:
                stream = io.StringIO()
                stats = pstats.Stats(profiler, stream=stream).sort_stats("cumulative")
                stats.print_stats(20)
                profile_text = stream.getvalue()

    return {
        "mode": mode,
        "warmup_runs": warmup_runs,
        "repeat": repeat,
        "warmup_durations_seconds": warmup_durations,
        "durations_seconds": durations,
        "min_seconds": min(durations),
        "median_seconds": statistics.median(durations),
        "mean_seconds": statistics.mean(durations),
        "max_seconds": max(durations),
        "results_count": results_count,
        "trade_rows": trade_rows,
        "profile_text": profile_text,
    }


def _write_summary(output_dir: Path, payload: Dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        f"# {payload['benchmark_id']}",
        "",
        payload.get("description", ""),
        "",
        f"- fixture: `{payload['fixture_name']}`",
        f"- warmup_runs: `{payload['warmup_runs']}`",
        f"- repeat: `{payload['repeat']}`",
        "",
    ]
    for result in payload["results"]:
        lines.extend(
            [
                f"## {result['mode']}",
                f"- median_seconds: `{result['median_seconds']:.6f}`",
                f"- mean_seconds: `{result['mean_seconds']:.6f}`",
                f"- min_seconds: `{result['min_seconds']:.6f}`",
                f"- max_seconds: `{result['max_seconds']:.6f}`",
                f"- results_count: `{result['results_count']}`",
                f"- trade_rows: `{result['trade_rows']}`",
                "",
            ]
        )
    if payload.get("node_ir_relative_speed"):
        lines.extend(
            [
                "## NodeIR Relative Speed",
                f"- rust_vs_python_median: `{payload['node_ir_relative_speed']:.6f}`",
                "",
            ]
        )
    if payload.get("node_ir_auto_relative_speed"):
        lines.extend(
            [
                "## NodeIR Auto Relative Speed",
                f"- auto_vs_python_median: `{payload['node_ir_auto_relative_speed']:.6f}`",
                "",
            ]
        )
    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    for result in payload["results"]:
        if result.get("profile_text"):
            (output_dir / f"profile_{result['mode']}.txt").write_text(
                result["profile_text"],
                encoding="utf-8",
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run backtester engine benchmark baseline.")
    parser.add_argument(
        "--spec",
        required=True,
        help="Path to benchmark spec json.",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Capture cProfile output for the first run of each mode.",
    )
    args = parser.parse_args()

    spec_path = _resolve_repo_path(args.spec)
    benchmark = _load_benchmark_spec(spec_path)
    output_dir = PROJECT_ROOT / "verification" / "results" / "benchmarks" / benchmark["benchmark_id"]

    results = [
        _run_single_mode(
            benchmark["config"],
            mode,
            benchmark["warmup_runs"],
            benchmark["repeat"],
            args.profile,
        )
        for mode in benchmark["modes"]
    ]
    node_ir_python = next((item for item in results if item["mode"] == "node_ir_python_numba"), None)
    node_ir_rust = next((item for item in results if item["mode"] == "node_ir_rust_kernel"), None)
    node_ir_auto = next((item for item in results if item["mode"] == "node_ir_auto"), None)
    node_ir_relative_speed = None
    if node_ir_python and node_ir_rust and node_ir_python["median_seconds"] > 0:
        node_ir_relative_speed = node_ir_rust["median_seconds"] / node_ir_python["median_seconds"]
    node_ir_auto_relative_speed = None
    if node_ir_python and node_ir_auto and node_ir_python["median_seconds"] > 0:
        node_ir_auto_relative_speed = node_ir_auto["median_seconds"] / node_ir_python["median_seconds"]

    summary = {
        "benchmark_id": benchmark["benchmark_id"],
        "description": benchmark["description"],
        "fixture_name": spec_path.name,
        "warmup_runs": benchmark["warmup_runs"],
        "repeat": benchmark["repeat"],
        "results": results,
        "node_ir_relative_speed": node_ir_relative_speed,
        "node_ir_auto_relative_speed": node_ir_auto_relative_speed,
    }
    _write_summary(output_dir, summary)


if __name__ == "__main__":
    main()
