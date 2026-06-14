from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run quality gate for node_ir + WFA semantic pipeline.")
    parser.add_argument(
        "--speed-profile",
        choices=["auto", "rust", "all"],
        default="all",
        help="Speed gate profile.",
    )
    parser.add_argument(
        "--gate-runs",
        type=int,
        default=3,
        help="Number of benchmark runs per speed profile (median aggregation).",
    )
    args = parser.parse_args()

    smoke_tests = [
        "tests/test_wfa_e2e_smoke.py",
        "tests/test_wfa_bridge.py",
        "tests/test_node_ir_executor_backtester.py",
        "tests/test_strategy_compiler_autorunner.py",
    ]
    _run([sys.executable, "-m", "pytest", *smoke_tests, "-q"])
    profiles = ["auto", "rust"] if args.speed_profile == "all" else [args.speed_profile]
    for profile in profiles:
        _run(
            [
                sys.executable,
                str(PROJECT_ROOT / "verification" / "scripts" / "check_speed_gate.py"),
                "--profile",
                profile,
                "--gate-runs",
                str(max(1, int(args.gate_runs))),
            ]
        )
    print(f"[quality-gate] PASS (speed_profile={args.speed_profile}, gate_runs={max(1, int(args.gate_runs))})")


if __name__ == "__main__":
    main()
