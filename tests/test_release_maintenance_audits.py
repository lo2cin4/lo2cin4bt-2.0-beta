from pathlib import Path

import pytest

from scripts.architecture_audit import analyze_repo


REPO_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.regression


def test_release_audit_support_files_exist() -> None:
    required = [
        ".github/workflows/ci.yml",
        ".github/workflows/codeql.yml",
        ".github/workflows/dependency-review.yml",
        ".github/workflows/semgrep.yml",
        "scripts/public_release_audit.py",
        "scripts/quality_gate.py",
        "scripts/pre_release_maintenance_audit.py",
        "scripts/project_consistency_audit.py",
        "scripts/architecture_audit.py",
        "scripts/workspace_doctor.py",
        "scripts/indicator_doctor.py",
        "tests/test_template_golden_regression.py",
        "docs/QUALITY_GATES.md",
        "docs/STRATEGY_BUILDING_BLOCKS.md",
        "Lecture/Module_12_Validation_Tools/index.html",
        "Lecture/Module_13_C4_Architecture/index.html",
    ]
    missing = [item for item in required if not (REPO_ROOT / item).exists()]
    assert missing == []


def test_architecture_audit_current_repo_has_no_smell() -> None:
    result = analyze_repo(REPO_ROOT)
    assert result["parse_errors"] == []
    assert result["violations"] == []


def test_lecture_validation_and_c4_pages_are_in_sidebar() -> None:
    script = (REPO_ROOT / "Lecture/assets/lecture.js").read_text(encoding="utf-8")
    module00 = (REPO_ROOT / "Lecture/Module_00_Getting_Started/index.html").read_text(encoding="utf-8")
    module10 = (REPO_ROOT / "Lecture/Module_10_Data_Provider_Extension/index.html").read_text(encoding="utf-8")
    module12 = (REPO_ROOT / "Lecture/Module_12_Validation_Tools/index.html").read_text(encoding="utf-8")
    module13 = (REPO_ROOT / "Lecture/Module_13_C4_Architecture/index.html").read_text(encoding="utf-8")
    css = (REPO_ROOT / "Lecture/assets/lecture.css").read_text(encoding="utf-8")

    assert "pageGroups" in script
    assert "新手主線" in script
    assert "進階與維護者" in script
    assert "實作練習" in script
    assert "出錯時先做健康檢查" in module00
    assert "python scripts/quality_gate.py --quick" in module00
    assert "checklist-grid adapter-checklist" in module10
    assert "checklist-card" in module10
    assert ".checklist-grid" in css
    assert "font-size: 16px" in css
    assert "Module_12_Validation_Tools/index.html" in script
    assert "Module_13_C4_Architecture/index.html" in script
    assert "新手只需要記住這件事" not in module12
    assert "python scripts/quality_gate.py --quick" in module12
    assert "進階：背後包含的工具" in module12
    assert "Golden regression" in module12 or "golden regression" in module12
    assert "pytest" in module12
    assert "Rust" in module12
    assert "Bandit" in module12
    assert "GitHub Summary" in module12
    assert "Context" in module13
    assert "Container" in module13
    assert "Component" in module13


def test_quality_gate_wraps_pre_release_audit_and_golden_templates() -> None:
    quality_gate = (REPO_ROOT / "scripts/quality_gate.py").read_text(encoding="utf-8")
    audit = (REPO_ROOT / "scripts/pre_release_maintenance_audit.py").read_text(encoding="utf-8")
    consistency = (REPO_ROOT / "scripts/project_consistency_audit.py").read_text(encoding="utf-8")
    golden = (REPO_ROOT / "tests/test_template_golden_regression.py").read_text(encoding="utf-8")

    assert "pre_release_maintenance_audit" in quality_gate
    assert "project_consistency_audit" in audit
    assert "REMOVED_PUBLIC_TOKENS" in consistency
    assert "strategy_run" in consistency
    assert "template_golden_regression" in audit
    assert "tests/test_template_golden_regression.py" in audit
    assert "moving_average" in golden
    assert "calendar_effect" in golden
    assert "scheduled_rebalance" in golden
    assert "momentum_rotation" in golden
