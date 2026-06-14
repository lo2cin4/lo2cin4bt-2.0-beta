from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


@pytest.mark.smoke
def test_quality_gates_docs_match_current_release_gates() -> None:
    text = _read("docs/QUALITY_GATES.md")

    for phrase in [
        "# Quality Gates",
        "python scripts/doctor.py",
        "python scripts/public_release_audit.py",
        "python scripts/pre_release_maintenance_audit.py --quick",
        "python scripts/architecture_audit.py --format text",
        "python verification/scripts/run_consistency_gate.py",
        "pytest-cov",
        "50%",
        "80%",
        "Rust crate gates",
        "cargo fmt",
        "cargo check",
        "cargo test",
        "cargo clippy",
        "bandit",
        "pip-audit",
        "npm audit",
        "GitHub Summary",
        "Quant oracle",
    ]:
        assert phrase in text


@pytest.mark.smoke
def test_public_quality_docs_link_current_quality_and_quant_gates() -> None:
    quality = _read("docs/QUALITY_GATES.md")
    quant = _read("docs/QUANT_VALIDATION_GATES.md")

    for phrase in [
        "python scripts/public_release_audit.py",
        "python scripts/pre_release_maintenance_audit.py --quick",
        "python scripts/architecture_audit.py --format text",
        "Coverage baseline",
        "50%",
        "80%",
        "cargo fmt",
        "cargo clippy",
        "GitHub Summary",
    ]:
        assert phrase in quality

    for phrase in [
        "Cost and slippage",
        "Open/close execution timing",
        "WFA selection",
        "QuantReview",
        "revised-history",
    ]:
        assert phrase in quant


@pytest.mark.smoke
def test_quant_validation_and_workspace_adaptor_docs_state_boundaries() -> None:
    quant = _read("docs/QUANT_VALIDATION_GATES.md")
    adaptor = _read("docs/WORKSPACE_ADAPTORS.md")

    for phrase in [
        "Cost and slippage",
        "Open/close execution timing",
        "delay bars",
        "Parameter matrix shape",
        "WFA selection",
        "AI should not invent hidden synthetic series",
        "QuantReview",
        "not trading edge",
    ]:
        assert phrase in quant

    for phrase in [
        "# Workspace Adaptors",
        "User input stays under `workspace/`",
        "IPO CSV Adaptor Example",
        "workspace/datasets/ipo_calendar.csv",
        "symbol",
        "ipo_date",
        "source_asof_date",
        "workspace/runs/",
        "workspace/reports/agents/",
        "point-in-time",
        "survivorship-safe",
        "stop and report the missing contract",
    ]:
        assert phrase in adaptor


@pytest.mark.smoke
def test_ci_and_security_workflows_are_declared() -> None:
    ci = _read(".github/workflows/ci.yml")
    codeql = _read(".github/workflows/codeql.yml")
    dependency_review = _read(".github/workflows/dependency-review.yml")
    semgrep_workflow = _read(".github/workflows/semgrep.yml")
    semgrep_rules = _read(".semgrep/lo2cin4bt.yml")
    readme = "\n".join([_read("README.md"), _read("README.en.md")])

    for phrase in [
        "--cov=app",
        "--cov=backtester",
        "--cov-report=xml",
        "--cov-fail-under=50",
        "--cov-fail-under=80",
        "python scripts/public_release_audit.py",
        "python scripts/pre_release_maintenance_audit.py --quick",
        "cargo fmt --manifest-path rust/lo2cin4bt_core/Cargo.toml -- --check",
        "cargo clippy --manifest-path backtester/rust_sim_kernel_rs/Cargo.toml -- -D warnings",
        "GITHUB_STEP_SUMMARY",
    ]:
        assert phrase in ci

    for phrase in [
        "github/codeql-action/init@v4",
        "github/codeql-action/analyze@v4",
        "python",
        "javascript-typescript",
    ]:
        assert phrase in codeql

    for phrase in [
        "actions/dependency-review-action@v4",
        "fail-on-severity: high",
    ]:
        assert phrase in dependency_review

    assert "semgrep scan --config .semgrep/lo2cin4bt.yml --error" in semgrep_workflow
    assert "lo2cin4bt-no-private-company-paths" in semgrep_rules
    assert "0.0.0.0" in semgrep_rules
    assert "coverage_gate-50%25_min" in readme
    assert "SECURITY.md" in readme
    assert "CONTRIBUTING.md" in readme


@pytest.mark.smoke
def test_public_release_audit_enforces_required_files_and_legacy_timing_block() -> None:
    script = _read("scripts/public_release_audit.py")

    for phrase in [
        "REQUIRED_FILES",
        "REQUIRED_TEXT",
        "FORBIDDEN_PUBLIC_AUTHORING_TEXT",
        "next_bar_after_signal",
        "signal_close_for_next_bar",
        "close_to_close",
        "Public release audit passed.",
    ]:
        assert phrase in script
