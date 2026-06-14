from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    ".github/workflows/ci.yml",
    ".github/workflows/codeql.yml",
    ".github/workflows/dependency-review.yml",
    ".github/workflows/semgrep.yml",
    "README.md",
    "README.en.md",
    "docs/QUALITY_GATES.md",
    "docs/QUANT_VALIDATION_GATES.md",
    "docs/STRATEGY_BUILDING_BLOCKS.md",
    "scripts/pre_release_maintenance_audit.py",
    "scripts/frontend_payload_parity_audit.py",
    "scripts/architecture_audit.py",
    "scripts/workspace_doctor.py",
    "scripts/indicator_doctor.py",
    "Lecture/Module_12_Validation_Tools/index.html",
    "Lecture/Module_13_C4_Architecture/index.html",
]

REQUIRED_TEXT = {
    ".github/workflows/ci.yml": [
        "python scripts/public_release_audit.py",
        "python scripts/frontend_payload_parity_audit.py",
        "python scripts/pre_release_maintenance_audit.py --quick",
        "python scripts/architecture_audit.py --format text",
        "python scripts/workspace_doctor.py",
        "python scripts/indicator_doctor.py workspace/indicators/extensions",
        "python verification/scripts/run_consistency_gate.py",
        "python -m bandit",
        "pip-audit -r requirements-dev.txt",
        "npm audit --audit-level=high",
        "--cov-fail-under=50",
        "--cov-fail-under=80",
        "cargo fmt --manifest-path rust/lo2cin4bt_core/Cargo.toml -- --check",
        "cargo clippy --manifest-path backtester/rust_sim_kernel_rs/Cargo.toml -- -D warnings",
        "GITHUB_STEP_SUMMARY",
    ],
    "README.md": ["coverage_gate-50%25_min"],
    "README.en.md": ["coverage_gate-50%25_min"],
    "docs/QUALITY_GATES.md": ["Coverage baseline", "Rust crate gates", "Core 80% target"],
    "docs/STRATEGY_BUILDING_BLOCKS.md": [
        "registry_supported",
        "unsupported_needs_new_building_block",
        "Look-Ahead Boundary",
    ],
    "Lecture/Module_12_Validation_Tools/index.html": ["pytest", "Rust", "Bandit", "GitHub Summary"],
    "Lecture/Module_13_C4_Architecture/index.html": ["C4", "Context", "Container", "Component"],
}

FORBIDDEN_PUBLIC_TEXT = [
    "live trading enabled",
    "place broker order",
    "enable fund movement",
    "authorize fund movement",
    "allow account setting change",
    "enable account setting change",
]

FORBIDDEN_PUBLIC_SURFACE_TEXT = [
    "RELEASE_VERSIONING",
    "docs/refactor",
    "release checklist",
    "public boundary",
    "public/GitHub boundary",
    "README/public-doc release",
    "Public GitHub boundary",
    "GitHub/public boundaries",
    "Public Repo Boundary",
    "publishable",
    "public release branch",
    "Company-level",
    "READMEAcceptance",
    "BeginnerAccessibility",
    "OnboardingTerminology",
    "VisualCapture",
    "QuantReviewSubAgent",
    "SpecComplianceSubAgent",
    "CodeQualityReviewSubAgent",
    "CloseoutSubAgent",
    "幫我驗收",
    "幫我檢查",
    "能不能發布",
    "public repo 邊界",
    "發布檢查",
]

PUBLIC_SAFETY_TEXT_FILES = [
    "README.md",
    "README.en.md",
    "docs/QUALITY_GATES.md",
    "docs/QUANT_VALIDATION_GATES.md",
    "docs/STRATEGY_BUILDING_BLOCKS.md",
    "Lecture/Module_12_Validation_Tools/index.html",
    "Lecture/Module_13_C4_Architecture/index.html",
]

PUBLIC_AUTHORING_GLOBS = [
    "README*.md",
    "docs/**/*.md",
    "Lecture/**/*.html",
    "agents/*.md",
    "skills/**/*.md",
    "backtester/contracts/strategy/*.json",
    "backtester/contracts/strategy/examples/*.json",
    "workspace/runs/*.json",
    "workspace/wfa/*.json",
]

PUBLIC_SURFACE_GLOBS = [
    "README*.md",
    "AGENTS.md",
    "docs/**/*.md",
    "Lecture/**/*.html",
    "agents/*.md",
    "skills/**/*.md",
    "scripts/README.md",
    "workspace/README.md",
    "workspace/reports/agents/README.md",
]

FORBIDDEN_PUBLIC_AUTHORING_TEXT = [
    "next_bar_after_signal",
    "signal_close_for_next_bar",
    "signal_bar",
    "close_to_next_open",
    "close_to_close",
    "entry_timing",
    "exit_timing",
    "next_session_open",
    "next_session_close",
]

FORBIDDEN_PUBLIC_AUTHORING_PATTERNS = [
    re.compile(r'["\']cost_model["\']\s*:'),
    re.compile(r'["\']slippage_model["\']\s*:'),
    re.compile(r"(?m)^\s*cost_model\s*:"),
    re.compile(r"(?m)^\s*slippage_model\s*:"),
]

FORBIDDEN_STRATEGY_JSON_PATTERNS = [
    re.compile(r'(?m)^\s*"features"\s*:'),
    re.compile(r'(?m)^\s*"indicators"\s*:'),
    re.compile(r'(?m)^\s*"execution"\s*:'),
]


def _tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def main() -> int:
    errors: list[str] = []
    tracked = _tracked_files()

    for relative in REQUIRED_FILES:
        if relative not in tracked and not (ROOT / relative).exists():
            errors.append(f"missing required file: {relative}")

    for relative, snippets in REQUIRED_TEXT.items():
        path = ROOT / relative
        if not path.exists():
            continue
        text = _read(relative)
        for snippet in snippets:
            if snippet not in text:
                errors.append(f"{relative} missing text: {snippet}")

    public_text = "\n".join(_read(path) for path in PUBLIC_SAFETY_TEXT_FILES if (ROOT / path).exists())
    lowered = public_text.lower()
    for token in FORBIDDEN_PUBLIC_TEXT:
        if token in lowered:
            errors.append(f"forbidden public wording: {token}")

    for pattern in PUBLIC_SURFACE_GLOBS:
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT).as_posix()
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in FORBIDDEN_PUBLIC_SURFACE_TEXT:
                if token in text:
                    errors.append(f"{relative} uses internal public-surface wording: {token}")

    for pattern in PUBLIC_AUTHORING_GLOBS:
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT).as_posix()
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in FORBIDDEN_PUBLIC_AUTHORING_TEXT:
                if token in text:
                    errors.append(
                        f"{relative} uses legacy public authoring timing token: {token}"
                    )
            for pattern_obj in FORBIDDEN_PUBLIC_AUTHORING_PATTERNS:
                if pattern_obj.search(text):
                    errors.append(
                        f"{relative} uses legacy public authoring field pattern: {pattern_obj.pattern}"
                    )
            if (
                relative.startswith("workspace/runs/")
                or (
                    relative.startswith("backtester/contracts/strategy/examples/")
                    and "/strategy-run-" in f"/{relative}"
                )
                or relative.endswith("multi-asset-portfolio-full-config-v1.json")
            ) and relative.endswith(".json"):
                for pattern_obj in FORBIDDEN_STRATEGY_JSON_PATTERNS:
                    if pattern_obj.search(text):
                        errors.append(
                            f"{relative} uses removed strategy_run field pattern: {pattern_obj.pattern}"
                        )

    if errors:
        print("Public release audit failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Public release audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
