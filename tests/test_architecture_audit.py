from __future__ import annotations

from pathlib import Path

from scripts.architecture_audit import analyze_repo

SOURCE_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _rules(result: dict[str, object]) -> set[str]:
    return {item["rule"] for item in result["violations"]}  # type: ignore[index]


def test_current_repo_passes_architecture_audit() -> None:
    result = analyze_repo(SOURCE_ROOT)

    assert result["parse_errors"] == []
    assert result["violations"] == []


def test_detects_utils_importing_domain_package(tmp_path: Path) -> None:
    _write(tmp_path / "utils" / "helpers.py", "import backtester.engine\n")
    _write(tmp_path / "backtester" / "engine.py", "VALUE = 1\n")

    result = analyze_repo(tmp_path)

    assert "utils_must_not_import_domain" in _rules(result)


def test_detects_engine_importing_app(tmp_path: Path) -> None:
    _write(tmp_path / "backtester" / "engine.py", "from app.runtime import labels\n")
    _write(tmp_path / "app" / "runtime" / "labels.py", "VALUE = 1\n")

    result = analyze_repo(tmp_path)

    assert "engine_must_not_import_app" in _rules(result)


def test_type_checking_imports_still_count_as_architecture_dependencies(tmp_path: Path) -> None:
    _write(
        tmp_path / "backtester" / "engine.py",
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from app.runtime import labels\n",
    )
    _write(tmp_path / "app" / "runtime" / "labels.py", "VALUE = 1\n")

    result = analyze_repo(tmp_path)

    assert "engine_must_not_import_app" in _rules(result)


def test_detects_production_sys_path_mutation(tmp_path: Path) -> None:
    _write(
        tmp_path / "backtester" / "loader.py",
        "import sys\nsys.path.insert(0, 'legacy')\n",
    )

    result = analyze_repo(tmp_path)

    assert "no_production_sys_path_mutation" in _rules(result)
