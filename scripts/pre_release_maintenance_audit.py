from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JSON_REPORT = Path("outputs/release/pre_release_maintenance_report.json")
DEFAULT_MD_REPORT = Path("outputs/release/pre_release_maintenance_report.md")
ALLOWED_REPORT_DIRS = (Path("outputs/release"), Path("workspace/reports/pre_release"))


QUICK_COMMANDS = [
    ("doctor", [sys.executable, "scripts/doctor.py"]),
    (
        "ruff_static_gate",
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            ".",
            "--select",
            "F401,F841,F811",
            "--exclude",
            "plotter/web",
            "--exclude",
            "workspace",
            "--exclude",
            "outputs",
            "--exclude",
            "logs",
        ],
    ),
    ("public_release_audit", [sys.executable, "scripts/public_release_audit.py"]),
    ("project_consistency_audit", [sys.executable, "scripts/project_consistency_audit.py"]),
    ("frontend_payload_parity_audit", [sys.executable, "scripts/frontend_payload_parity_audit.py"]),
    ("architecture_audit", [sys.executable, "scripts/architecture_audit.py", "--format", "text"]),
    ("workspace_doctor", [sys.executable, "scripts/workspace_doctor.py"]),
    ("indicator_doctor", [sys.executable, "scripts/indicator_doctor.py", "workspace/indicators/extensions"]),
    ("consistency_gate", [sys.executable, "verification/scripts/run_consistency_gate.py"]),
    (
        "template_golden_regression",
        [sys.executable, "-m", "pytest", "tests/test_template_golden_regression.py", "-q"],
    ),
]

FULL_EXTRA_COMMANDS = [
    (
        "coverage_release_gate",
        [
            sys.executable,
            "-m",
            "pytest",
            "--cov=app",
            "--cov=autorunner",
            "--cov=backtester",
            "--cov=dataloader",
            "--cov=metricstracker",
            "--cov=statanalyser",
            "--cov=utils",
            "--cov=verification",
            "--cov=wfanalyser",
            "--cov-report=term",
            "--cov-fail-under=50",
            "-q",
        ],
    ),
    (
        "core_backtest_coverage_gate",
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_multi_asset_portfolio_engine.py",
            "tests/test_cost_slippage_regression.py",
            "tests/test_risk_gate_backtester.py",
            "tests/test_single_asset_portfolio_adapter.py",
            "tests/test_rust_accounting_golden.py",
            "--cov=backtester.MultiAssetPortfolioEngine_backtester",
            "--cov-report=term",
            "--cov-fail-under=80",
            "-q",
        ],
    ),
    (
        "quant_reproducibility_oracle_tests",
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_backtester_oracle_cases.py",
            "tests/test_backtester_pseudo_fuzz.py",
            "tests/test_exporter_snapshot_backtester.py",
            "tests/test_rust_accounting_golden.py",
            "-q",
        ],
    ),
    (
        "bandit_security_audit",
        [
            sys.executable,
            "-m",
            "bandit",
            "-q",
            "-r",
            "app",
            "autorunner",
            "backtester",
            "dataloader",
            "metricstracker",
            "statanalyser",
            "utils",
            "verification",
            "wfanalyser",
            "-ll",
        ],
    ),
    ("pip_audit_dev_requirements", ["pip-audit", "-r", "requirements-dev.txt"]),
    ("npm_ci_frontend", ["npm", "--prefix", "plotter/web", "ci"]),
    ("npm_lint_frontend", ["npm", "--prefix", "plotter/web", "run", "lint"]),
    ("npm_test_frontend", ["npm", "--prefix", "plotter/web", "test"]),
    ("npm_build_frontend", ["npm", "--prefix", "plotter/web", "run", "build"]),
    ("npm_audit_frontend", ["npm", "--prefix", "plotter/web", "audit", "--audit-level=high"]),
    ("rust_core_fmt", ["cargo", "fmt", "--manifest-path", "rust/lo2cin4bt_core/Cargo.toml", "--", "--check"]),
    ("rust_core_check", ["cargo", "check", "--manifest-path", "rust/lo2cin4bt_core/Cargo.toml"]),
    ("rust_core_test", ["cargo", "test", "--manifest-path", "rust/lo2cin4bt_core/Cargo.toml"]),
    (
        "rust_core_clippy",
        ["cargo", "clippy", "--manifest-path", "rust/lo2cin4bt_core/Cargo.toml", "--", "-D", "warnings"],
    ),
    (
        "rust_sim_fmt",
        ["cargo", "fmt", "--manifest-path", "backtester/rust_sim_kernel_rs/Cargo.toml", "--", "--check"],
    ),
    ("rust_sim_check", ["cargo", "check", "--manifest-path", "backtester/rust_sim_kernel_rs/Cargo.toml"]),
    ("rust_sim_test", ["cargo", "test", "--manifest-path", "backtester/rust_sim_kernel_rs/Cargo.toml"]),
    (
        "rust_sim_clippy",
        [
            "cargo",
            "clippy",
            "--manifest-path",
            "backtester/rust_sim_kernel_rs/Cargo.toml",
            "--",
            "-D",
            "warnings",
        ],
    ),
]


def resolve_report_path(repo_root: Path, requested: Path | None, default: Path) -> tuple[Path, Path]:
    relative = requested or default
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise ValueError("Report output must be repo-relative and must not escape its directory.")
    resolved = (repo_root / relative).resolve()
    allowed = [(repo_root / item).resolve() for item in ALLOWED_REPORT_DIRS]
    if not any(resolved == base or resolved.is_relative_to(base) for base in allowed):
        raise ValueError("Report output must stay under outputs/release/ or workspace/reports/pre_release/.")
    return resolved, relative


def run_command(name: str, argv: Sequence[str]) -> dict[str, object]:
    resolved_argv = list(argv)
    if os.name == "nt" and resolved_argv and resolved_argv[0] == "npm":
        resolved_argv[0] = "npm.cmd"
    try:
        result = subprocess.run(
            resolved_argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
    except FileNotFoundError as exc:
        return {
            "name": name,
            "argv": resolved_argv,
            "returncode": 127,
            "stdout_tail": "",
            "stderr_tail": f"command not found: {exc.filename or resolved_argv[0]}",
        }
    return {
        "name": name,
        "argv": resolved_argv,
        "returncode": result.returncode,
        "stdout_tail": (result.stdout or "")[-3000:],
        "stderr_tail": (result.stderr or "")[-3000:],
    }


def build_markdown(report: dict[str, object]) -> str:
    summary = report["summary"]  # type: ignore[index]
    lines = [
        "# Pre-Release Maintenance Audit",
        "",
        f"- Status: `{summary['status']}`",  # type: ignore[index]
        f"- Mode: `{report['mode']}`",
        f"- Generated: `{report['generated_at_utc']}`",
        "",
        "## Commands",
        "",
        "| Command | Return code |",
        "| :--- | ---: |",
    ]
    for command in report["commands"]:  # type: ignore[index]
        lines.append(f"| `{command['name']}` | `{command['returncode']}` |")
    lines.extend(
        [
            "",
            "This audit does not modify tracked files. It may write ignored reports, build artifacts, and test/cache files.",
            "Use the JSON report for command stdout/stderr tails.",
            "",
        ]
    )
    return "\n".join(lines)


def write_github_summary(markdown: str) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with Path(target).open("a", encoding="utf-8") as handle:
            handle.write(markdown)
            handle.write("\n")


def run_audit(mode: str, json_output: Path | None, md_output: Path | None) -> tuple[int, dict[str, object]]:
    commands = QUICK_COMMANDS + (FULL_EXTRA_COMMANDS if mode == "full" else [])
    results = [run_command(name, argv) for name, argv in commands]
    failed = [item for item in results if item["returncode"] != 0]
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": mode,
        "summary": {
            "status": "failed" if failed else "passed",
            "failed_command_names": [item["name"] for item in failed],
        },
        "commands": results,
    }
    json_path, json_relative = resolve_report_path(ROOT, json_output, DEFAULT_JSON_REPORT)
    md_path, md_relative = resolve_report_path(ROOT, md_output, DEFAULT_MD_REPORT)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    report["report_paths"] = {"json": json_relative.as_posix(), "markdown": md_relative.as_posix()}
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = build_markdown(report)
    md_path.write_text(markdown, encoding="utf-8")
    write_github_summary(markdown)
    return (1 if failed else 0), report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_REPORT)
    args = parser.parse_args()
    mode = "full" if args.full else "quick"
    try:
        code, report = run_audit(mode, args.json_output, args.md_output)
    except ValueError as exc:
        print(f"Pre-release maintenance audit blocked: {exc}", file=sys.stderr)
        return 2
    print(
        f"Pre-release maintenance audit {report['summary']['status']}. "  # type: ignore[index]
        f"JSON: {report['report_paths']['json']}; Markdown: {report['report_paths']['markdown']}"  # type: ignore[index]
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
