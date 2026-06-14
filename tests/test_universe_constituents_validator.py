import hashlib
from pathlib import Path

import pytest

from backtester.UniverseConstituentsValidator_backtester import (
    constituents_path_declared,
    constituents_source_ref,
    declared_constituents_hash,
    validate_historical_universe_constituents,
)

pytestmark = pytest.mark.regression


def _write_csv(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_constituents_metadata_helpers_prefer_explicit_snapshot_fields() -> None:
    universe = {
        "source": "fallback-provider",
        "source_ref": "fallback-ref",
        "historical_constituents_path": "workspace/universe/pit.csv",
        "historical_constituents_hash": "abc123",
    }

    assert constituents_source_ref(universe) == "workspace/universe/pit.csv"
    assert declared_constituents_hash(universe) == "abc123"
    assert constituents_path_declared(universe) is True
    assert constituents_path_declared({"source_ref": "provider-only"}) is False


def test_validate_historical_constituents_reports_missing_file(tmp_path: Path) -> None:
    result = validate_historical_universe_constituents(
        universe={"historical_constituents_path": str(tmp_path / "missing.csv")},
        configured_symbols=["AAA"],
        as_of_date="2024-01-31",
        repo_root=tmp_path,
    )

    assert result["status"] == "missing"
    assert result["errors"] == ["historical_constituents_file_missing"]
    assert result["configured_symbols"] == ["AAA"]


def test_validate_historical_constituents_accepts_effective_interval_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "constituents.csv"
    content_hash = _write_csv(
        path,
        "\n".join(
            [
                "symbol,effective_start,effective_end",
                "AAA,2024-01-01,",
                "BBB,2024-01-01,2024-12-31",
                "OLD,2023-01-01,2023-12-31",
            ]
        ),
    )

    result = validate_historical_universe_constituents(
        universe={
            "historical_constituents_path": str(path),
            "historical_constituents_hash": content_hash.removeprefix("sha256:"),
        },
        configured_symbols=["AAA", "BBB"],
        as_of_date="2024-06-30",
        repo_root=tmp_path,
    )

    assert result["status"] == "valid"
    assert result["hash_matches"] is True
    assert result["date_semantics"] == "effective_interval"
    assert result["as_of_covered"] is True
    assert result["missing_configured_symbols"] == []
    assert result["errors"] == []


def test_validate_historical_constituents_flags_duplicate_and_hash_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "constituents.csv"
    _write_csv(
        path,
        "\n".join(
            [
                "symbol,as_of_date",
                "AAA,2024-01-31",
                "AAA,2024-01-31",
                "BBB,2024-01-31",
            ]
        ),
    )

    result = validate_historical_universe_constituents(
        universe={
            "historical_constituents_path": str(path),
            "historical_constituents_hash": "not-the-real-hash",
        },
        configured_symbols=["AAA", "BBB"],
        as_of_date="2024-01-31",
        repo_root=tmp_path,
    )

    assert result["status"] == "invalid"
    assert result["hash_matches"] is False
    assert result["duplicate_row_count"] == 1
    assert "historical_constituents_hash_mismatch" in result["errors"]
    assert "historical_constituents_duplicate_rows" in result["warnings"]


def test_validate_historical_constituents_requires_exact_snapshot_date(tmp_path: Path) -> None:
    path = tmp_path / "constituents.csv"
    _write_csv(
        path,
        "\n".join(
            [
                "ticker,snapshot_date",
                "AAA,2024-01-31",
                "BBB,2024-01-31",
            ]
        ),
    )

    result = validate_historical_universe_constituents(
        universe={"historical_constituents_path": str(path)},
        configured_symbols=["AAA", "BBB"],
        as_of_date="2024-02-29",
        repo_root=tmp_path,
    )

    assert result["status"] == "invalid"
    assert result["date_semantics"] == "exact_as_of_snapshot"
    assert result["as_of_covered"] is False
    assert result["missing_configured_symbols_at_as_of"] == ["AAA", "BBB"]
    assert "historical_constituents_exact_as_of_snapshot_missing" in result["errors"]


def test_validate_historical_constituents_warns_when_as_of_missing(tmp_path: Path) -> None:
    path = tmp_path / "constituents.csv"
    _write_csv(path, "asset,start_date,end_date\nAAA,2024-01-01,\n")

    result = validate_historical_universe_constituents(
        universe={"historical_constituents_path": str(path)},
        configured_symbols=["AAA"],
        as_of_date=None,
        repo_root=tmp_path,
    )

    assert result["status"] == "valid"
    assert result["as_of_covered"] is None
    assert result["warnings"] == ["historical_constituents_as_of_not_declared"]
