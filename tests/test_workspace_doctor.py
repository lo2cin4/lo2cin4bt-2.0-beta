from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STRATEGY_EXAMPLE = REPO_ROOT / "backtester" / "contracts" / "strategy" / "examples" / "strategy-run-qqq-yfinance-daily-sma-cross-matrix-example.json"
WFA_EXAMPLE = REPO_ROOT / "backtester" / "contracts" / "strategy" / "examples" / "wfa-run-qqq-yfinance-daily-sma-cross-example.json"
FEATURE_EXAMPLE = REPO_ROOT / "backtester" / "contracts" / "feature" / "examples" / "feature-contract-vix-price-v1.json"


def _run_doctor(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/workspace_doctor.py", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_workspace_json(relative_path: str, payload: dict) -> Path:
    target = REPO_ROOT / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def test_workspace_doctor_accepts_public_strategy_run_config() -> None:
    rel = "workspace/runs/.doctor-public-strategy-run-example.json"
    target = _write_workspace_json(rel, _load_json(STRATEGY_EXAMPLE))
    try:
        result = _run_doctor("--config", rel)
    finally:
        target.unlink(missing_ok=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "strategy_run runnable contract valid" in result.stdout


def test_workspace_doctor_accepts_wfa_config_and_referenced_run() -> None:
    strategy_rel = "workspace/runs/.doctor-wfa-strategy-example.json"
    wfa_rel = "workspace/wfa/.doctor-wfa-qqq-example.json"
    strategy_target = _write_workspace_json(strategy_rel, _load_json(STRATEGY_EXAMPLE))
    wfa_payload = _load_json(WFA_EXAMPLE)
    wfa_payload["strategy_config_path"] = strategy_rel
    wfa_target = _write_workspace_json(wfa_rel, wfa_payload)
    try:
        result = _run_doctor("--config", wfa_rel)
    finally:
        wfa_target.unlink(missing_ok=True)
        strategy_target.unlink(missing_ok=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "wfa_run wrapper valid" in result.stdout


def test_workspace_doctor_rejects_missing_workspace_config() -> None:
    result = _run_doctor("--config", "workspace/runs/missing-doctor-test.json")

    assert result.returncode == 1
    assert "invalid config path" in result.stdout


def test_workspace_doctor_rejects_wrong_run_schema() -> None:
    target = REPO_ROOT / "workspace" / "runs" / ".doctor-invalid-schema-test.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"schema_version": "legacy"}), encoding="utf-8")
    try:
        result = _run_doctor("--config", "workspace/runs/.doctor-invalid-schema-test.json")
    finally:
        target.unlink(missing_ok=True)

    assert result.returncode == 1
    assert "schema_version=strategy_run" in result.stdout


def test_workspace_doctor_rejects_broken_strategy_feature_ref() -> None:
    target = REPO_ROOT / "workspace" / "strategies" / ".doctor-broken-feature-ref.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "schema_version": "strategy_contract",
                "strategy_id": "doctor_broken_feature_ref",
                "data_context": {
                    "primary_instrument": "QQQ",
                    "frequency": "1D",
                    "timezone": "America/New_York",
                    "calendar": "XNYS",
                    "feature_contract_ref": "workspace/features/missing-doctor-feature.json",
                },
                "parameter_domains": {},
                "entry": {"op": "calendar.every_session"},
                "exit": {"op": "calendar.every_session"},
            }
        ),
        encoding="utf-8",
    )
    try:
        result = _run_doctor("--config", "workspace/strategies/.doctor-broken-feature-ref.json")
    finally:
        target.unlink(missing_ok=True)

    assert result.returncode == 1
    assert "feature_contract_ref" in result.stdout


def test_workspace_doctor_rejects_feature_without_data_availability() -> None:
    base = _load_json(FEATURE_EXAMPLE)
    target = REPO_ROOT / "workspace" / "features" / ".doctor-missing-data-availability.json"
    payload = copy.deepcopy(base)
    payload["features"][0].pop("data_availability", None)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")
    try:
        result = _run_doctor("--config", "workspace/features/.doctor-missing-data-availability.json")
    finally:
        target.unlink(missing_ok=True)

    assert result.returncode == 1
    assert "data_availability" in result.stdout


def test_workspace_doctor_rejects_close_data_used_at_same_open() -> None:
    base = _load_json(FEATURE_EXAMPLE)
    target = REPO_ROOT / "workspace" / "features" / ".doctor-lookahead-data-availability.json"
    payload = copy.deepcopy(base)
    payload["features"][0]["data_availability"]["observed_at"] = "bar_close"
    payload["features"][0]["data_availability"]["usable_from"] = "same_bar_open"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")
    try:
        result = _run_doctor("--config", "workspace/features/.doctor-lookahead-data-availability.json")
    finally:
        target.unlink(missing_ok=True)

    assert result.returncode == 1
    assert "cannot use bar_close data at same_bar_open" in result.stdout


def test_workspace_doctor_rejects_local_market_data_without_time_column() -> None:
    base = _load_json(STRATEGY_EXAMPLE)
    target = REPO_ROOT / "workspace" / "runs" / ".doctor-local-market-data-test.json"
    dataset = REPO_ROOT / "workspace" / "datasets" / ".doctor-close.csv"
    payload = copy.deepcopy(base)
    payload["data"]["market_data"] = {
        "close": {
            "path": "workspace/datasets/.doctor-close.csv",
        }
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text("Time,QQQ\n2024-01-02,100\n", encoding="utf-8")
    target.write_text(json.dumps(payload), encoding="utf-8")
    try:
        result = _run_doctor("--config", "workspace/runs/.doctor-local-market-data-test.json")
    finally:
        target.unlink(missing_ok=True)
        dataset.unlink(missing_ok=True)

    assert result.returncode == 1
    assert "data.market_data.close.time_column is required" in result.stdout


def test_workspace_doctor_rejects_file_provider_without_explicit_columns() -> None:
    base = _load_json(STRATEGY_EXAMPLE)
    target = REPO_ROOT / "workspace" / "runs" / ".doctor-file-provider-test.json"
    dataset = REPO_ROOT / "workspace" / "datasets" / ".doctor-price.csv"
    payload = copy.deepcopy(base)
    payload["data"]["provider"] = "file"
    payload["data"]["file_path"] = "workspace/datasets/.doctor-price.csv"
    payload["data"].pop("date_column", None)
    payload["data"].pop("price_column", None)
    target.parent.mkdir(parents=True, exist_ok=True)
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text("Time,Close\n2024-01-02,100\n", encoding="utf-8")
    target.write_text(json.dumps(payload), encoding="utf-8")
    try:
        result = _run_doctor("--config", "workspace/runs/.doctor-file-provider-test.json")
    finally:
        target.unlink(missing_ok=True)
        dataset.unlink(missing_ok=True)

    assert result.returncode == 1
    assert "local file data requires explicit date_column" in result.stdout
    assert "local file data requires explicit price_column" in result.stdout


def test_workspace_doctor_rejects_parameter_matrix_with_one_combo() -> None:
    base = _load_json(STRATEGY_EXAMPLE)
    target = REPO_ROOT / "workspace" / "runs" / ".doctor-one-combo-matrix-test.json"
    payload = copy.deepcopy(base)
    payload["parameter_domains"] = {
        "threshold": {"type": "set", "values": [15]},
        "holding_days": {"type": "set", "values": [250]},
    }
    target.write_text(json.dumps(payload), encoding="utf-8")
    try:
        result = _run_doctor("--config", "workspace/runs/.doctor-one-combo-matrix-test.json")
    finally:
        target.unlink(missing_ok=True)

    assert result.returncode == 1
    assert "workflow_id=parameter_matrix requires parameter_domains with at least 2 combinations" in result.stdout


def test_workspace_doctor_rejects_parameter_matrix_with_empty_domain_values() -> None:
    base = _load_json(STRATEGY_EXAMPLE)
    target = REPO_ROOT / "workspace" / "runs" / ".doctor-empty-domain-matrix-test.json"
    payload = copy.deepcopy(base)
    payload["parameter_domains"] = {
        "threshold": {"type": "set", "values": []},
        "holding_days": {"type": "set", "values": [50, 100]},
    }
    target.write_text(json.dumps(payload), encoding="utf-8")
    try:
        result = _run_doctor("--config", "workspace/runs/.doctor-empty-domain-matrix-test.json")
    finally:
        target.unlink(missing_ok=True)

    assert result.returncode == 1
    assert "workflow_id=parameter_matrix requires parameter_domains with at least 2 combinations" in result.stdout


def test_workspace_doctor_rejects_single_backtest_with_parameter_domains() -> None:
    base = _load_json(STRATEGY_EXAMPLE)
    target = REPO_ROOT / "workspace" / "runs" / ".doctor-single-backtest-domain-test.json"
    payload = copy.deepcopy(base)
    payload["platform"]["workflow_id"] = "single_backtest"
    payload["parameter_domains"] = {
        "threshold": {"type": "set", "values": [10, 11, 12, 13, 14, 15]},
        "holding_days": {"type": "set", "values": [50, 100, 150, 200, 250]},
    }
    target.write_text(json.dumps(payload), encoding="utf-8")
    try:
        result = _run_doctor("--config", "workspace/runs/.doctor-single-backtest-domain-test.json")
    finally:
        target.unlink(missing_ok=True)

    assert result.returncode == 1
    assert "workflow_id=single_backtest must not carry parameter_domains" in result.stdout
