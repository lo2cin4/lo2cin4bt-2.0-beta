from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "dist",
    "logs",
    "node_modules",
    "outputs",
    "__pycache__",
}

REMOVED_PUBLIC_TOKENS = (
    "strategy_run" + "." + "v" + "2",
    "wfa_run" + "." + "v" + "2",
    "strategy-run-" + "v" + "2",
    "wfa-run-" + "v" + "2",
)

REMOVED_INTERNAL_NAME_TOKENS = (
    "strategy_run_" + "v" + "2",
    "wfa_run_" + "v" + "2",
    "StrategyCompiler" + "V" + "2",
    "StrategyContractValidator" + "V" + "2",
    "StrategyPreview" + "V" + "2",
    "StrategyCompiler_" + "v" + "2",
    "StrategyContractValidator_" + "v" + "2",
    "StrategyPreview_" + "v" + "2",
    "strategy-contract-" + "v" + "2",
    "strategy-" + "v" + "2" + "-",
    "run-" + "v" + "2" + "-",
    "wfa-" + "v" + "2",
    "strategy_mode" + "\": \"" + "v" + "2",
)


def iter_text_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current_root, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_PARTS]
        current_path = Path(current_root)
        for name in names:
            path = current_path / name
            if path.suffix.lower() in TEXT_SUFFIXES:
                files.append(path)
    return files


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def check_removed_public_tokens(root: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in iter_text_files(root):
        text = read_text(path)
        for token in REMOVED_PUBLIC_TOKENS:
            if token not in text:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if token in line:
                    findings.append(
                        {
                            "check": "removed_public_token",
                            "path": path.relative_to(root).as_posix(),
                            "line": line_no,
                            "token": token,
                            "detail": line.strip()[:220],
                        }
                    )
    return findings


def check_removed_internal_name_tokens(root: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in iter_text_files(root):
        text = read_text(path)
        for token in REMOVED_INTERNAL_NAME_TOKENS:
            if token not in text:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if token in line:
                    findings.append(
                        {
                            "check": "removed_internal_name_token",
                            "path": path.relative_to(root).as_posix(),
                            "line": line_no,
                            "token": token,
                            "detail": line.strip()[:220],
                        }
                    )
    return findings


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def check_schema_versions(root: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    strategy_schema = root / "backtester/contracts/strategy/strategy-run.schema.json"
    wfa_schema = root / "backtester/contracts/strategy/wfa-run.schema.json"
    expected = {
        strategy_schema: "strategy_run",
        wfa_schema: "wfa_run",
    }
    for path, value in expected.items():
        if not path.exists():
            findings.append(
                {
                    "check": "schema_file_exists",
                    "path": path.relative_to(root).as_posix(),
                    "detail": "required schema file is missing",
                }
            )
            continue
        schema = load_json(path)
        const = (
            schema.get("properties", {})
            .get("schema_version", {})
            .get("const")
        )
        if const != value:
            findings.append(
                {
                    "check": "schema_version_const",
                    "path": path.relative_to(root).as_posix(),
                    "expected": value,
                    "actual": const,
                }
            )
    return findings


def check_workspace_configs(root: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in sorted((root / "workspace/runs").glob("*.json")):
        payload = load_json(path)
        if payload.get("schema_version") != "strategy_run":
            findings.append(
                {
                    "check": "workspace_run_schema",
                    "path": path.relative_to(root).as_posix(),
                    "expected": "strategy_run",
                    "actual": payload.get("schema_version"),
                }
            )
    for path in sorted((root / "workspace/wfa").glob("*.json")):
        payload = load_json(path)
        if payload.get("schema_version") != "wfa_run":
            findings.append(
                {
                    "check": "workspace_wfa_schema",
                    "path": path.relative_to(root).as_posix(),
                    "expected": "wfa_run",
                    "actual": payload.get("schema_version"),
                }
            )
    return findings


def run_audit(root: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    findings.extend(check_removed_public_tokens(root))
    findings.extend(check_removed_internal_name_tokens(root))
    findings.extend(check_schema_versions(root))
    findings.extend(check_workspace_configs(root))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="lo2cin4bt project consistency audit")
    parser.add_argument("--json", type=Path, default=None, help="Optional JSON report path.")
    args = parser.parse_args()

    findings = run_audit(ROOT)
    report = {"status": "failed" if findings else "passed", "findings": findings}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if findings:
        print("Project consistency audit failed:")
        for item in findings[:50]:
            print(json.dumps(item, ensure_ascii=False))
        if len(findings) > 50:
            print(f"... {len(findings) - 50} more findings")
        return 1
    print("Project consistency audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
