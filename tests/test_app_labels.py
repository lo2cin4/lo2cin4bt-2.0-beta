import json
import re
from pathlib import Path

import pytest

import app.api.labels as api_labels
from app.api.labels import (
    canonical_artifact_filename,
    config_filename,
    decorate_config_item,
    decorate_run_label,
    display_run_type,
    infer_label_badges,
    load_app_config_metadata,
    normalize_run_type,
)

pytestmark = pytest.mark.regression


def test_api_labels_exports_are_explicit() -> None:
    expected_exports = {
        "LEGACY_TEST_RUN_TYPES",
        "MODE_DISPLAY",
        "MODULE_DISPLAY",
        "VALID_RUN_TYPES",
        "WORKFLOW_SLUG",
        "build_trading_identity",
        "canonical_artifact_filename",
        "canonical_config_filename",
        "canonical_output_prefix",
        "canonical_stem",
        "config_filename",
        "decorate_config_item",
        "decorate_run_label",
        "display_identity_label",
        "display_run_type",
        "infer_label_badges",
        "load_app_config_metadata",
        "normalize_run_type",
        "public_identity",
    }

    assert set(api_labels.__all__) == expected_exports

    namespace: dict[str, object] = {}
    exec("from app.api.labels import *", {}, namespace)

    assert set(namespace) == expected_exports
    assert "json" not in namespace
    assert "Path" not in namespace
    assert "datetime" not in namespace


def test_normalize_run_type_collapses_legacy_terms_to_test() -> None:
    assert normalize_run_type("production") == "production"
    assert normalize_run_type("test") == "test"
    assert normalize_run_type("smoke") == "test"
    assert normalize_run_type("latest") == "test"
    assert normalize_run_type("sweep") == "test"


def test_display_run_type_only_shows_production_or_test() -> None:
    assert display_run_type("production") == "Production"
    assert display_run_type("smoke") == "Test"
    assert display_run_type("latest") == "Test"
    assert display_run_type("unknown") == ""


def test_infer_label_badges_is_no_longer_filename_driven() -> None:
    assert infer_label_badges("run-sweep-multigrid-smoke.user.json") == []


def test_config_filename_returns_leaf_name() -> None:
    assert (
        config_filename(r"C:\workspace\foo\run-spy-mmfi-vix-hold-reset.user.json")
        == "run-spy-mmfi-vix-hold-reset.user.json"
    )


def test_decorate_config_item_uses_filename_and_config_run_type() -> None:
    item = decorate_config_item(
        {
            "label": r"C:\workspace\foo\run-spy-mmfi-vix-hold-reset.user.json",
            "value": "x",
            "summary": {},
            "platform": {"run_type": "production"},
            "raw_config": {
                "dataloader": {"yfinance_config": {"symbol": "SPY"}},
                "backtester": {
                    "strategy_contract_path": "workspace/strategies/strategy-spy-mmfi-vix-hold-reset.user.json",
                    "feature_contract_path": "workspace/features/feature-contract-spy-mmfi-vix-v1.user.json",
                    "selected_predictor": "X",
                },
            },
            "config_hash": "abcdef",
        },
        "autorunner",
    )
    assert item["filename"].endswith("run-spy-mmfi-vix-hold-reset.user.json")
    assert item["display_label"].startswith(
        "Backtest | "
    )
    assert "SPY | MMFI + VIX | Single Backtest | cfg abcdef" in item["display_label"]
    assert "Hold Reset" not in item["display_label"]
    assert re.match(r"backtest_\d{8}_SPY_MMFI-VIX_hold-reset_single_abcdef\.json", item["canonical_filename"])
    assert item["badges"] == ["Production"]
    assert item["metadata_complete"] is True


def test_decorate_run_label_prefers_config_filename_and_module_display() -> None:
    payload = decorate_run_label(
        {
            "module": "wfanalyser",
            "semantic_label": "wfa-app-smoke",
            "config_filename": "wfa-latest.user.json",
            "run_type": "test",
            "run_id": "20260425_5fd126d7d8c6",
            "dataloader_config": {"yfinance_config": {"symbol": "QQQ"}},
            "backtester_config": {
                "strategy_contract_path": "workspace/strategies/strategy-qqq-price-ma-cross-sweep.user.json",
                "feature_contract_path": "workspace/features/feature-contract-qqq-price-only-v1.user.json",
                "selected_predictor": "X",
            },
        }
    )
    assert payload["display_label"] == (
        "Walk-Forward | 2026-04-25 | QQQ | Price | Rolling Windows | run 5fd126"
    )
    assert payload["selector_label"] == payload["display_label"]
    assert payload["label_badges"] == ["Test"]
    assert payload["module_display"] == "Walk-Forward"


def test_statanalyser_release_label_uses_factor_analysis_display_with_predictor_slug() -> None:
    payload = decorate_run_label(
        {
            "module": "statanalyser",
            "run_id": "20260501_abc123def456",
            "dataloader_config": {"yfinance_config": {"symbol": "SPY"}},
            "backtester_config": {
                "feature_contract_path": "workspace/features/feature-contract-spy-mmfi-v1.user.json",
                "selected_predictor": "X",
            },
        }
    )
    assert payload["module_display"] == "Factor Analysis"
    assert payload["display_label"].startswith("Factor Analysis | 2026-05-01 | SPY | MMFI")
    assert payload["identity"]["workflow"] == "predictor"


def test_file_source_asset_uses_dataset_label_even_with_feature_metadata(tmp_path: Path, monkeypatch) -> None:
    feature_path = tmp_path / "workspace" / "features" / "feature-contract-vix-price-v1.user.json"
    feature_path.parent.mkdir(parents=True)
    feature_path.write_text(
        json.dumps({"schema_version": "1.0", "dataset_id": "qqq_price_plus_vix_daily_v1"}),
        encoding="utf-8-sig",
    )
    monkeypatch.chdir(tmp_path)
    payload = decorate_run_label(
        {
            "module": "wfanalyser",
            "run_id": "20260415_d1c3219130cc",
            "dataloader_config": {
                "source": "file",
                "file_config": {"file_path": "workspace/datasets/price.csv"},
            },
            "backtester_config": {
                "feature_contract_path": "workspace/features/feature-contract-vix-price-v1.user.json",
                "strategy_contract_path": "workspace/strategies/strategy-vix-regime-ma-cross.user.json",
                "selected_predictor": "X",
            },
        }
    )
    assert payload["identity"]["asset"] == "DATASET"
    assert payload["display_label"] == (
        "Walk-Forward | 2026-04-15 | DATASET | VIX + Price | Rolling Windows | run d1c321"
    )
    assert "LOCAL" not in payload["display_label"]


def test_file_source_without_asset_metadata_uses_dataset_placeholder() -> None:
    payload = decorate_run_label(
        {
            "module": "wfanalyser",
            "run_id": "20260415_d1c3219130cc",
            "dataloader_config": {
                "source": "file",
                "file_config": {"file_path": "workspace/datasets/price.csv"},
            },
            "backtester_config": {
                "feature_contract_path": "workspace/features/feature-contract-custom-price-v1.user.json",
                "selected_predictor": "X",
            },
        }
    )
    assert payload["identity"]["asset"] == "DATASET"
    assert "LOCAL" not in payload["display_label"]


def test_decorate_run_label_marks_legacy_incomplete_state_flags() -> None:
    payload = decorate_run_label(
        {
            "module": "autorunner",
            "semantic_label": "run-latest",
            "config_filename": "run-latest.user.json",
            "run_type": "test",
            "run_id": "20260425_abc123def456",
            "semantic_index_complete": False,
            "strategy_label_mode": "internal_id_fallback",
        }
    )
    assert payload["label_badges"] == ["Test"]
    assert payload["is_legacy_result"] is True
    assert payload["has_incomplete_strategy_labels"] is True


def test_strategy_run_labels_do_not_infer_semantics_from_filename() -> None:
    payload = decorate_config_item(
        {
            "label": "workspace/runs/qqq-price-ma-cross-sweep.json",
            "value": "workspace/runs/qqq-price-ma-cross-sweep.json",
            "summary": {},
            "platform": {"run_type": "test"},
            "raw_config": {
                "schema_version": "strategy_run",
                "platform": {
                    "strategy_mode_id": "multi_asset_portfolio",
                    "workflow_id": "single_backtest",
                },
                "data": {"provider": "yfinance", "frequency": "1D"},
                "universe": {"symbols": ["SPY"]},
                "computed_fields": [],
                "signals": {},
                "selection": {},
                "allocation": {"method": "fixed_weights"},
                "rebalance": {"trigger": {"op": "calendar.year_start"}},
                "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
                "risk": {},
                "parameter_domains": {},
                "outputs": {},
            },
            "config_hash": "123456",
        },
        "autorunner",
    )

    assert "SPY" in payload["display_label"]
    assert "QQQ" not in payload["display_label"]
    assert "Matrix" not in payload["display_label"]
    assert payload["identity"]["asset"] == "SPY"
    assert payload["identity"]["factor_slug"] == "ALLOCATION"
    assert payload["identity"]["mode"] == "single"


def test_load_app_config_metadata_uses_filename_and_explicit_run_type(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "run-demo.user.json"
    config_path.write_text(
        json.dumps(
            {
                "platform": {
                    "display_label": "Ignored Demo Label",
                    "run_type": "production",
                },
                "dataloader": {"yfinance_config": {"symbol": "SPY"}},
                "backtester": {
                    "strategy_contract_path": "workspace/strategies/strategy-spy-mmfi-vix-hold-reset.user.json",
                    "feature_contract_path": "workspace/features/feature-contract-spy-mmfi-vix-v1.user.json",
                    "selected_predictor": "X",
                },
            }
        ),
        encoding="utf-8",
    )
    payload = load_app_config_metadata(str(config_path), "autorunner")
    assert "SPY | MMFI + VIX | Single Backtest | cfg " in payload["display_label"]
    assert "Hold Reset" not in payload["display_label"]
    assert payload["canonical_filename"].startswith("backtest_")
    assert payload["badges"] == ["Production"]
    assert payload["metadata_complete"] is True


def test_strategy_run_multi_asset_label_uses_universe_symbols() -> None:
    item = decorate_config_item(
        {
            "label": "backtest_20260501_MULTI_VOO-GLD_price-momentum-sma-rotation_matrix_7a91c4.json",
            "value": "x",
            "summary": {},
            "platform": {"run_type": "test"},
            "raw_config": {
                "schema_version": "strategy_run",
                "platform": {
                    "strategy_mode_id": "multi_asset_portfolio",
                    "workflow_id": "parameter_matrix",
                    "run_type": "test",
                },
                "universe": {"symbols": ["VOO", "GLD"]},
                "features": [
                    {"name": "return_momentum", "op": "indicator.momentum", "source": "close"},
                    {"name": "sma_filter", "op": "indicator.sma", "source": "close"},
                ],
                "allocation": {"method": "equal_weight"},
                "metadata": {"strategy_id": "voo_gld_momentum_sma_rotation"},
            },
            "config_hash": "376aa3",
        },
        "autorunner",
    )
    assert item["display_label"] == (
        "Backtest | 2026-05-01 | VOO-GLD | Price | Parameter Matrix | cfg 376aa3"
    )
    assert item["canonical_filename"].startswith(
        "backtest_20260501_VOO-GLD_PRICE_momentum-sma-rotation_matrix_376aa3"
    )


def test_strategy_run_fixed_allocation_label_uses_all_assets() -> None:
    item = decorate_config_item(
        {
            "label": "backtest_20260501_MULTI_VTI-AVUV-VXUS-SGOL-DBMF_fixed-annual-rebalance_single_2b84d1.json",
            "value": "x",
            "summary": {},
            "platform": {"run_type": "test"},
            "raw_config": {
                "schema_version": "strategy_run",
                "platform": {
                    "strategy_mode_id": "multi_asset_portfolio",
                    "workflow_id": "single_backtest",
                    "run_type": "test",
                },
                "universe": {"symbols": ["VTI", "AVUV", "VXUS", "SGOL", "DBMF"]},
                "features": [],
                "allocation": {"method": "fixed_weights"},
                "metadata": {"strategy_id": "vti_avuv_vxus_sgol_dbmf_fixed_annual_rebalance"},
            },
            "config_hash": "195913",
        },
        "autorunner",
    )
    assert item["display_label"] == (
        "Backtest | 2026-05-01 | VTI-AVUV-VXUS-SGOL-DBMF | Allocation | Single Backtest | cfg 195913"
    )


def test_canonical_artifact_filename_keeps_run_id_at_end() -> None:
    identity = {
        "workflow": "wfa",
        "date": "20260425",
        "asset": "SPY",
        "factor_slug": "MMFI-VIX",
        "strategy_slug": "hold-reset",
        "mode": "windows",
        "short_id": "5fd126",
    }
    filename = canonical_artifact_filename(
        identity=identity,
        artifact_type="wfa_parquet",
        source_name="20260425_SPY_mmfi_vix_wfa_sharpe_abc.parquet",
        suffix="",
    )
    assert filename == "wfa_20260425_SPY_MMFI-VIX_hold-reset_windows_sharpe_5fd126.parquet"
