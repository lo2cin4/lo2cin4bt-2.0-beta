import json
from pathlib import Path

import pandas as pd

from app.runtime.runtime import AppRuntimeService


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_app_runtime_lists_workspace_configs() -> None:
    runtime = AppRuntimeService(REPO_ROOT)

    run_configs = runtime.list_run_configs()
    wfa_configs = runtime.list_wfa_configs()
    statanalyser_configs = runtime.list_statanalyser_configs()

    assert run_configs
    assert wfa_configs
    assert statanalyser_configs == []
    assert all(item["value"].endswith(".json") for item in run_configs)
    assert all(item["value"].endswith(".json") for item in wfa_configs)
    assert any(
        item["value"].endswith("strategy-run-btcusdt-binance-daily-dual-ma-example.json")
        for item in run_configs
    )
    assert any(
        item["value"].endswith("wfa-run-qqq-yfinance-daily-sma-cross-example.json")
        for item in wfa_configs
    )


def test_app_runtime_materializes_included_examples_into_empty_workspace(tmp_path: Path) -> None:
    import shutil

    repo = tmp_path / "repo"
    examples = repo / "backtester" / "contracts" / "strategy" / "examples"
    examples.mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT
        / "backtester"
        / "contracts"
        / "strategy"
        / "examples"
        / "strategy-run-btcusdt-binance-daily-dual-ma-example.json",
        examples / "strategy-run-btcusdt-binance-daily-dual-ma-example.json",
    )
    shutil.copy2(
        REPO_ROOT
        / "backtester"
        / "contracts"
        / "strategy"
        / "examples"
        / "wfa-run-qqq-yfinance-daily-sma-cross-example.json",
        examples / "wfa-run-qqq-yfinance-daily-sma-cross-example.json",
    )

    runtime = AppRuntimeService(repo)

    run_example = repo / "workspace" / "runs" / "strategy-run-btcusdt-binance-daily-dual-ma-example.json"
    wfa_example = repo / "workspace" / "wfa" / "wfa-run-qqq-yfinance-daily-sma-cross-example.json"
    assert run_example.exists()
    assert wfa_example.exists()
    assert any(item["value"] == str(run_example.resolve()) for item in runtime.list_run_configs())
    assert any(item["value"] == str(wfa_example.resolve()) for item in runtime.list_wfa_configs())


def test_app_runtime_derives_single_strategy_run_runtime_sections() -> None:
    runtime = AppRuntimeService(REPO_ROOT)
    config = {
        "schema_version": "strategy_run",
        "platform": {"strategy_mode_id": "single_asset_signal", "workflow_id": "single_backtest"},
        "data": {
            "provider": "file",
            "frequency": "1D",
            "file_path": "tests/fixtures/smoke/price_data_ma_cross.csv",
            "date_column": "Time",
            "price_column": "Close",
        },
        "universe": {"symbols": ["TEST"]},
        "features": [],
        "signals": {},
        "allocation": {},
        "execution": {},
        "risk": {},
        "parameter_domains": {},
        "outputs": {},
        "metadata": {"strategy_id": "single_primary_probe"},
    }

    dataloader = runtime._strategy_run_dataloader_config(config)  # pylint: disable=protected-access
    backtester = runtime._strategy_run_backtester_config(config)  # pylint: disable=protected-access
    runtime_config = runtime._strategy_run_runtime_config(  # pylint: disable=protected-access
        config,
        backtester_config=backtester,
    )

    assert dataloader["source"] == "strategy_run_market_data"
    assert dataloader["asset_symbols"] == ["TEST"]
    assert backtester["market_data"]["provider"] == "file"
    assert backtester["strategy_mode"] == "multi_asset_portfolio"
    assert runtime_config["metadata"]["legacy_backtester"]["export_config"]["export_parquet"] is True


def test_app_runtime_delegates_multi_strategy_run_market_data() -> None:
    runtime = AppRuntimeService(REPO_ROOT)
    config = {
        "schema_version": "strategy_run",
        "platform": {"strategy_mode_id": "multi_asset_portfolio", "workflow_id": "parameter_matrix"},
        "data": {"provider": "yfinance", "frequency": "1D", "start_date": "2020-01-01"},
        "universe": {"symbols": ["VOO", "GLD"]},
        "features": [],
        "selection": {},
        "allocation": {},
        "rebalance": {},
        "execution": {},
        "risk": {},
        "parameter_domains": {},
        "outputs": {},
        "metadata": {"strategy_id": "multi_primary_probe"},
    }

    dataloader = runtime._strategy_run_dataloader_config(config)  # pylint: disable=protected-access
    backtester = runtime._strategy_run_backtester_config(config)  # pylint: disable=protected-access

    assert runtime._strategy_run_uses_internal_market_loader(config) is True  # pylint: disable=protected-access
    assert dataloader["source"] == "strategy_run_market_data"
    assert dataloader["asset_symbols"] == ["VOO", "GLD"]
    assert backtester["strategy_mode"] == "multi_asset_portfolio"


def test_data_lineage_manifest_hashes_local_file_source(tmp_path: Path) -> None:
    runtime = AppRuntimeService(tmp_path)
    data_path = tmp_path / "workspace" / "datasets" / "prices.csv"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text("Time,Close\n2026-01-01,100\n2026-01-02,101\n", encoding="utf-8")
    data = pd.DataFrame(
        {
            "Time": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "Close": [100.0, 101.0],
        }
    )

    manifest = runtime._build_data_lineage_manifest(  # pylint: disable=protected-access
        run_id="lineage_local",
        module="autorunner",
        dataloader_config={
            "source": "file",
            "frequency": "1D",
            "file_config": {"file_path": "workspace/datasets/prices.csv"},
        },
        data=data,
        raw_config={
            "data": {
                "provider": "file",
                "file_path": "workspace/datasets/prices.csv",
                "frequency": "1D",
            },
            "universe": {"symbols": ["TEST"], "universe_policy": "fixed_symbols"},
            "execution": {"timing": "bar_offset", "entry_price": "open", "entry_delay_bars": 1, "exit_price": "open", "exit_delay_bars": 1},
        },
        primary_artifact=None,
        dataloader_health={"missing_ratio": 0.0, "warnings": [], "errors": []},
    )

    assert manifest["lineage_status"] == "partial"
    assert manifest["coverage_level"] == "run"
    source = manifest["input_sources"][0]
    assert source["source_type"] == "file"
    assert source["content_hash"]
    assert source["uri_or_path"] == "workspace\\datasets\\prices.csv" or source["uri_or_path"] == "workspace/datasets/prices.csv"
    assert source["actual_start"].startswith("2026-01-01")
    assert manifest["audit"]["row_count"] == 2
    assert manifest["validity_flags"]["survivorship_known"] is False
    assert manifest["universe_provenance"]["survivorship_bias_risk"] == "high"
    assert "Configured universe symbols may be a current/static list with survivorship bias." in manifest["lineage_claims"]["unknown"]


def test_data_lineage_manifest_keeps_provider_source_partial() -> None:
    runtime = AppRuntimeService(REPO_ROOT)
    data = pd.DataFrame(
        {
            "Time": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "Close": [100.0, 101.0],
        }
    )

    manifest = runtime._build_data_lineage_manifest(  # pylint: disable=protected-access
        run_id="lineage_provider",
        module="autorunner",
        dataloader_config={
            "source": "yfinance",
            "frequency": "1D",
            "yfinance_config": {"symbol": "QQQ", "interval": "1d"},
        },
        data=data,
        raw_config={
            "data": {
                "provider": "yfinance",
                "frequency": "1D",
                "interval": "1d",
                "timezone": "America/New_York",
                "calendar": "XNYS",
            },
            "universe": {"symbols": ["QQQ"]},
            "execution": {"timing": "bar_offset", "entry_price": "open", "entry_delay_bars": 1, "exit_price": "open", "exit_delay_bars": 1},
        },
        primary_artifact=None,
        dataloader_health={"missing_ratio": 0.0, "warnings": [], "errors": []},
    )

    assert manifest["lineage_status"] == "partial"
    assert manifest["input_sources"][0]["source_type"] == "provider"
    assert manifest["input_sources"][0]["content_hash"] is None
    assert manifest["universe_provenance"]["survivorship_bias_risk"] == "high"
    assert "Provider content hash is not available." in manifest["lineage_claims"]["unknown"]


def test_data_lineage_manifest_writes_consumed_provider_snapshot(tmp_path: Path) -> None:
    runtime = AppRuntimeService(tmp_path)
    data = pd.DataFrame(
        {
            "Time": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "Close": [100.0, 101.0],
        }
    )

    manifest = runtime._write_data_lineage_manifest(  # pylint: disable=protected-access
        run_id="lineage_snapshot",
        module="autorunner",
        dataloader_config={"source": "yfinance", "frequency": "1D", "asset_symbols": ["QQQ"]},
        data=data,
        raw_config={
            "data": {"provider": "yfinance", "frequency": "1D"},
            "universe": {"symbols": ["QQQ"]},
            "execution": {"timing": "bar_offset", "entry_price": "open", "entry_delay_bars": 1, "exit_price": "open", "exit_delay_bars": 1},
        },
        primary_artifact=None,
        dataloader_health={"missing_ratio": 0.0, "warnings": [], "errors": []},
    )

    snapshot = manifest["consumed_data_snapshot"]
    assert snapshot["status"] == "captured"
    assert snapshot["content_hash"].startswith("sha256:")
    assert (tmp_path / snapshot["path"]).is_file()
    assert manifest["input_sources"][0]["content_hash"] == snapshot["content_hash"]
    assert manifest["input_sources"][0]["cache"]["status"] == "captured"
    assert "Provider content hash is not available." not in manifest["lineage_claims"]["unknown"]


def test_data_lineage_manifest_point_in_time_universe_sets_low_survivorship_risk(tmp_path: Path) -> None:
    runtime = AppRuntimeService(REPO_ROOT)
    constituents_path = tmp_path / "historical_constituents.csv"
    constituents_path.write_text(
        "symbol,effective_start,effective_end\n"
        "AAA,2019-01-01,\n"
        "BBB,2019-01-01,\n",
        encoding="utf-8",
    )
    data = pd.DataFrame(
        {
            "Time": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "Close": [100.0, 101.0],
        }
    )

    manifest = runtime._build_data_lineage_manifest(  # pylint: disable=protected-access
        run_id="lineage_pit",
        module="autorunner",
        dataloader_config={
            "source": "yfinance",
            "frequency": "1D",
            "asset_symbols": ["AAA", "BBB"],
        },
        data=data,
        raw_config={
            "data": {"provider": "yfinance", "frequency": "1D"},
            "universe": {
                "symbols": ["AAA", "BBB"],
                "universe_policy": "point_in_time_snapshot",
                "historical_constituents_path": str(constituents_path),
                "as_of_date": "2020-01-01",
                "delisted_policy": "include_when_historically_tradable",
            },
            "execution": {"timing": "bar_offset", "entry_price": "open", "entry_delay_bars": 1, "exit_price": "open", "exit_delay_bars": 1},
        },
        primary_artifact=None,
        dataloader_health={"missing_ratio": 0.0, "warnings": [], "errors": []},
    )

    assert manifest["validity_flags"]["point_in_time_known"] is True
    assert manifest["validity_flags"]["survivorship_known"] is True
    assert manifest["universe_provenance"]["source_type"] == "historical_universe_constituents"
    assert manifest["universe_provenance"]["point_in_time_constituents"] is True
    assert manifest["universe_provenance"]["constituents_validation"]["status"] == "valid"
    assert manifest["universe_provenance"]["survivorship_bias_risk"] == "low"


def test_data_lineage_manifest_snapshot_date_only_constituents_require_exact_as_of(tmp_path: Path) -> None:
    runtime = AppRuntimeService(REPO_ROOT)
    constituents_path = tmp_path / "historical_constituents.csv"
    constituents_path.write_text(
        "symbol,snapshot_date\n"
        "AAA,2019-01-01\n"
        "BBB,2019-01-01\n",
        encoding="utf-8",
    )
    data = pd.DataFrame(
        {
            "Time": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "Close": [100.0, 101.0],
        }
    )

    manifest = runtime._build_data_lineage_manifest(  # pylint: disable=protected-access
        run_id="lineage_stale_snapshot_constituents",
        module="autorunner",
        dataloader_config={
            "source": "yfinance",
            "frequency": "1D",
            "asset_symbols": ["AAA", "BBB"],
        },
        data=data,
        raw_config={
            "data": {"provider": "yfinance", "frequency": "1D"},
            "universe": {
                "symbols": ["AAA", "BBB"],
                "universe_policy": "point_in_time_snapshot",
                "historical_constituents_path": str(constituents_path),
                "as_of_date": "2020-01-01",
                "delisted_policy": "include_when_historically_tradable",
            },
            "execution": {"timing": "bar_offset", "entry_price": "open", "entry_delay_bars": 1, "exit_price": "open", "exit_delay_bars": 1},
        },
        primary_artifact=None,
        dataloader_health={"missing_ratio": 0.0, "warnings": [], "errors": []},
    )

    validation = manifest["universe_provenance"]["constituents_validation"]
    assert manifest["validity_flags"]["point_in_time_known"] is False
    assert manifest["validity_flags"]["survivorship_known"] is False
    assert manifest["universe_provenance"]["survivorship_bias_risk"] == "medium"
    assert validation["status"] == "invalid"
    assert "historical_constituents_exact_as_of_snapshot_missing" in validation["errors"]
    assert "historical_constituents_content_validation_failed" in manifest["audit"]["warnings"]


def test_data_lineage_manifest_current_provider_source_cannot_prove_survivorship() -> None:
    runtime = AppRuntimeService(REPO_ROOT)
    data = pd.DataFrame(
        {
            "Time": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "Close": [100.0, 101.0],
        }
    )

    manifest = runtime._build_data_lineage_manifest(  # pylint: disable=protected-access
        run_id="lineage_current_provider",
        module="autorunner",
        dataloader_config={"source": "yfinance", "frequency": "1D", "asset_symbols": ["AAA"]},
        data=data,
        raw_config={
            "data": {"provider": "yfinance", "frequency": "1D"},
            "universe": {
                "symbols": ["AAA"],
                "universe_policy": "point_in_time_snapshot",
                "source_type": "current_provider_list",
                "source": "sp500",
                "as_of_date": "2020-01-01",
                "delisted_policy": "include_when_historically_tradable",
            },
            "execution": {"timing": "bar_offset", "entry_price": "open", "entry_delay_bars": 1, "exit_price": "open", "exit_delay_bars": 1},
        },
        primary_artifact=None,
        dataloader_health={"missing_ratio": 0.0, "warnings": [], "errors": []},
    )

    assert manifest["validity_flags"]["point_in_time_known"] is False
    assert manifest["validity_flags"]["survivorship_known"] is False
    assert manifest["universe_provenance"]["survivorship_bias_risk"] == "medium"
    assert "current_or_static_universe_source_not_point_in_time" in manifest["audit"]["warnings"]


def test_data_lineage_manifest_factor_audit_blocks_unproven_pit_factor() -> None:
    runtime = AppRuntimeService(REPO_ROOT)

    manifest = runtime._build_data_lineage_manifest(  # pylint: disable=protected-access
        run_id="lineage_factor_audit",
        module="autorunner",
        dataloader_config={"source": "yfinance", "frequency": "1D", "asset_symbols": ["AAA"]},
        data=pd.DataFrame({"Time": pd.to_datetime(["2026-01-01"]), "Close": [100.0]}),
        raw_config={
            "data": {"provider": "yfinance", "frequency": "1D"},
            "universe": {"symbols": ["AAA"]},
            "factor_pipeline": {
                "schema_version": "factor_pipeline.v1",
                "data_requirements": {"point_in_time_required": True},
                "construction": [{"name": "value", "op": "factor.book_to_market"}],
            },
            "execution": {"timing": "bar_offset", "entry_price": "open", "entry_delay_bars": 1, "exit_price": "open", "exit_delay_bars": 1},
        },
        primary_artifact=None,
        dataloader_health={"missing_ratio": 0.0, "warnings": [], "errors": []},
    )

    assert manifest["factor_feature_audit"]["status"] == "invalid"
    assert manifest["validity_flags"]["feature_lag_verified"] is False
    assert "factor_point_in_time_metadata_missing" in manifest["factor_feature_audit"]["errors"]
    assert "Factor point-in-time metadata or feature lag audit is not valid." in manifest["lineage_claims"]["unknown"]


def test_data_lineage_manifest_fixed_source_type_cannot_prove_survivorship() -> None:
    runtime = AppRuntimeService(REPO_ROOT)

    manifest = runtime._build_data_lineage_manifest(  # pylint: disable=protected-access
        run_id="lineage_fixed_source_type",
        module="autorunner",
        dataloader_config={"source": "yfinance", "frequency": "1D", "asset_symbols": ["AAA"]},
        data=pd.DataFrame({"Time": pd.to_datetime(["2026-01-01"]), "Close": [100.0]}),
        raw_config={
            "data": {"provider": "yfinance", "frequency": "1D"},
            "universe": {
                "symbols": ["AAA"],
                "universe_policy": "point_in_time_snapshot",
                "source_type": "fixed_symbols",
                "historical_constituents_path": "workspace/universe/current_symbols.parquet",
                "as_of_date": "2020-01-01",
                "delisted_policy": "include_when_historically_tradable",
            },
            "execution": {"timing": "bar_offset", "entry_price": "open", "entry_delay_bars": 1, "exit_price": "open", "exit_delay_bars": 1},
        },
        primary_artifact=None,
        dataloader_health={"missing_ratio": 0.0, "warnings": [], "errors": []},
    )

    assert manifest["validity_flags"]["survivorship_known"] is False
    assert manifest["universe_provenance"]["survivorship_bias_risk"] == "medium"
    assert "current_or_static_universe_source_not_point_in_time" in manifest["audit"]["warnings"]


def test_data_lineage_manifest_historical_constituents_path_requires_as_of_date() -> None:
    runtime = AppRuntimeService(REPO_ROOT)

    manifest = runtime._build_data_lineage_manifest(  # pylint: disable=protected-access
        run_id="lineage_missing_as_of",
        module="autorunner",
        dataloader_config={"source": "yfinance", "frequency": "1D", "asset_symbols": ["AAA"]},
        data=pd.DataFrame({"Time": pd.to_datetime(["2026-01-01"]), "Close": [100.0]}),
        raw_config={
            "data": {"provider": "yfinance", "frequency": "1D"},
            "universe": {
                "symbols": ["AAA"],
                "universe_policy": "point_in_time_snapshot",
                "historical_constituents_path": "workspace/universe/historical_constituents.parquet",
                "delisted_policy": "include_when_historically_tradable",
            },
            "execution": {"timing": "bar_offset", "entry_price": "open", "entry_delay_bars": 1, "exit_price": "open", "exit_delay_bars": 1},
        },
        primary_artifact=None,
        dataloader_health={"missing_ratio": 0.0, "warnings": [], "errors": []},
    )

    assert manifest["validity_flags"]["point_in_time_known"] is False
    assert manifest["universe_provenance"]["survivorship_bias_risk"] == "medium"
    assert "point_in_time_universe_claim_missing_as_of_date" in manifest["audit"]["warnings"]


def test_data_lineage_manifest_internal_strategy_loader_is_partial() -> None:
    runtime = AppRuntimeService(REPO_ROOT)

    manifest = runtime._build_data_lineage_manifest(  # pylint: disable=protected-access
        run_id="lineage_internal",
        module="autorunner",
        dataloader_config={
            "source": "strategy_run_market_data",
            "frequency": "1D",
            "asset_symbols": ["VOO", "GLD"],
        },
        data=pd.DataFrame(),
        raw_config={
            "schema_version": "strategy_run",
            "data": {"provider": "yfinance", "frequency": "1D"},
            "universe": {"symbols": ["VOO", "GLD"]},
        },
        primary_artifact=None,
        dataloader_health={"missing_ratio": 0.0, "warnings": [], "errors": []},
    )

    assert manifest["lineage_status"] == "partial"
    assert manifest["input_sources"][0]["source_type"] == "generated"
    assert manifest["universe_provenance"]["survivorship_bias_risk"] == "high"
    assert "Internal market loader did not expose a consumed data content snapshot." in manifest["lineage_claims"]["unknown"]


def test_data_lineage_manifest_captures_wfa_windows() -> None:
    runtime = AppRuntimeService(REPO_ROOT)
    selected = pd.DataFrame(
        [
            {
                "window_id": 1,
                "train_start": pd.Timestamp("2020-01-01"),
                "train_end": pd.Timestamp("2020-06-30"),
                "test_start": pd.Timestamp("2020-07-01"),
                "test_end": pd.Timestamp("2020-12-31"),
            }
        ]
    )

    manifest = runtime._build_data_lineage_manifest(  # pylint: disable=protected-access
        run_id="lineage_wfa",
        module="wfanalyser",
        dataloader_config={
            "source": "yfinance",
            "frequency": "1D",
            "yfinance_config": {"symbol": "QQQ", "interval": "1d"},
        },
        data=pd.DataFrame({"Time": pd.to_datetime(["2020-01-01"])}),
        raw_config={
            "schema_version": "wfa_run",
            "data": {"provider": "yfinance", "frequency": "1D"},
            "universe": {"symbols": ["QQQ"]},
        },
        primary_artifact=None,
        dataloader_health={"missing_ratio": 0.0, "warnings": [], "errors": []},
        wfa_results={"selected_optimum": selected},
    )

    assert manifest["coverage_level"] == "window"
    assert manifest["windows"][0]["window_id"] == 1
    assert manifest["windows"][0]["train_start"].startswith("2020-01-01")
    assert manifest["universe_provenance"]["window_count"] == 1
    assert manifest["windows"][0]["universe_provenance"]["survivorship_bias_risk"] == "high"
    assert "wfa_windows_use_run_level_universe_without_point_in_time_constituents" in manifest["audit"]["warnings"]


def test_data_lineage_manifest_wfa_uses_referenced_strategy_universe(tmp_path: Path) -> None:
    runtime = AppRuntimeService(tmp_path)
    strategy_path = tmp_path / "workspace" / "runs" / "strategy_run.json"
    wfa_path = tmp_path / "workspace" / "wfa" / "wfa_run.json"
    constituents_path = tmp_path / "workspace" / "universe" / "historical_constituents.csv"
    strategy_path.parent.mkdir(parents=True, exist_ok=True)
    wfa_path.parent.mkdir(parents=True, exist_ok=True)
    constituents_path.parent.mkdir(parents=True, exist_ok=True)
    constituents_path.write_text(
        "symbol,effective_start,effective_end\n"
        "AAA,2019-01-01,\n"
        "BBB,2019-01-01,\n",
        encoding="utf-8",
    )
    strategy_path.write_text(
        json.dumps(
            {
                "schema_version": "strategy_run",
                "platform": {
                    "strategy_mode_id": "multi_asset_portfolio",
                    "workflow_id": "single_backtest",
                },
                "data": {"provider": "yfinance", "frequency": "1D"},
                    "universe": {
                        "symbols": ["AAA", "BBB"],
                        "universe_policy": "point_in_time_snapshot",
                        "historical_constituents_path": "workspace/universe/historical_constituents.csv",
                        "as_of_date": "2020-01-01",
                        "delisted_policy": "include_when_historically_tradable",
                },
                "features": [],
                "selection": {},
                "allocation": {},
                "rebalance": {},
                "execution": {"timing": "bar_offset", "entry_price": "open", "entry_delay_bars": 1, "exit_price": "open", "exit_delay_bars": 1},
                "risk": {},
                "parameter_domains": {},
                "outputs": {},
            }
        ),
        encoding="utf-8",
    )
    raw_wfa = {"schema_version": "wfa_run", "strategy_config_path": "workspace/runs/strategy_run.json"}
    wfa_path.write_text(json.dumps(raw_wfa), encoding="utf-8")
    lineage_raw_config = runtime._lineage_raw_config_with_wfa_strategy(  # pylint: disable=protected-access
        raw_wfa,
        wfa_path,
    )
    selected = pd.DataFrame(
        [
            {
                "window_id": 1,
                "train_start": pd.Timestamp("2020-01-01"),
                "train_end": pd.Timestamp("2020-06-30"),
                "test_start": pd.Timestamp("2020-07-01"),
                "test_end": pd.Timestamp("2020-12-31"),
            }
        ]
    )

    manifest = runtime._build_data_lineage_manifest(  # pylint: disable=protected-access
        run_id="lineage_wfa_strategy_ref",
        module="wfanalyser",
        dataloader_config={"source": "yfinance", "frequency": "1D", "asset_symbols": ["AAA", "BBB"]},
        data=pd.DataFrame({"Time": pd.to_datetime(["2020-01-01"])}),
        raw_config=lineage_raw_config,
        primary_artifact=None,
        dataloader_health={"missing_ratio": 0.0, "warnings": [], "errors": []},
        wfa_results={"selected_optimum": selected},
    )

    assert manifest["universe_provenance"]["survivorship_bias_risk"] == "low"
    assert manifest["validity_flags"]["survivorship_known"] is True
    assert manifest["windows"][0]["symbols"] == ["AAA", "BBB"]
    assert manifest["windows"][0]["universe_provenance"]["survivorship_bias_risk"] == "low"


def test_failed_run_writes_unknown_data_lineage_manifest(tmp_path: Path) -> None:
    runtime = AppRuntimeService(tmp_path)
    run_id = "failed_lineage"
    paths = runtime.registry.build_run_paths(run_id)
    registry_payload = runtime._base_registry(  # pylint: disable=protected-access
        run_id=run_id,
        module="autorunner",
        entrypoint="test",
        status="running",
    )
    registry_payload["config_snapshot_dir"] = str(paths["snapshot_dir"])
    registry_payload["artifact_manifest_path"] = str(paths["artifact_manifest"])
    registry_payload["dataloader_health_path"] = str(paths["dataloader_health"])
    registry_payload["data_lineage_manifest_path"] = str(paths["data_lineage_manifest"])
    stage_status = runtime._new_stage_status(run_id, "autorunner")  # pylint: disable=protected-access

    runtime._fail_run(  # pylint: disable=protected-access
        run_id=run_id,
        registry_payload=registry_payload,
        stage_status=stage_status,
        stage_name="config_validation",
        message="validation failed",
    )

    lineage = json.loads(paths["data_lineage_manifest"].read_text(encoding="utf-8"))
    registry_entry = runtime.registry.load_registry_entry(run_id)
    assert lineage["lineage_status"] == "unknown"
    assert registry_entry["lineage_status"] == "unknown"
