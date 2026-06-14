import json
from pathlib import Path

import pandas as pd
import pytest

from app.api.service import AppAPIService


pytestmark = pytest.mark.regression


def _write_minimal_portfolio_metrics_run(
    service: AppAPIService,
    run_id: str,
    *,
    config_filename: str,
    created_at: str,
) -> None:
    artifact_dir = service.registry.build_run_paths(run_id)["snapshot_dir"] / "managed_artifacts" / "portfolio"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    equity_path = artifact_dir / f"{run_id}_portfolio-equity.parquet"
    metadata_path = artifact_dir / f"{run_id}_portfolio-metadata.json"
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "Equity_value": [100.0, 101.0],
            "Portfolio_return": [0.0, 0.01],
            "Turnover": [1.0, 0.0],
            "Selected_count": [1, 1],
            "Cash_weight": [0.0, 0.0],
        }
    ).to_parquet(equity_path, index=False)
    metadata_path.write_text(
        json.dumps(
            {
                "artifact_type": "multi_asset_portfolio_backtest",
                "strategy_id": run_id,
                "summary": {"rebalance_count": 1},
                "config": {
                    "strategy_id": run_id,
                    "universe": {"symbols": ["AAA"]},
                    "rebalance": {"trigger": {"op": "calendar.month_start"}},
                    "allocation": {"method": "fixed_weights", "weights": {"AAA": 1.0}},
                },
            }
        ),
        encoding="utf-8",
    )
    service.registry.write_registry_entry(
        {
            "run_id": run_id,
            "module": "autorunner",
            "entrypoint": "test",
            "status": "completed",
            "created_at": created_at,
            "completed_at": created_at,
            "config_filename": config_filename,
            "strategy_mode": "multi_asset_portfolio",
            "run_type": "test",
        }
    )
    service.registry.write_artifact_manifest(
        run_id,
        {
            "schema_version": "1.0",
            "artifacts": [
                {"artifact_type": "portfolio_equity_curve_parquet", "path": str(equity_path), "status": "ready"},
                {"artifact_type": "portfolio_metadata_json", "path": str(metadata_path), "status": "ready"},
            ],
        },
    )


def test_app_metrics_accepts_portfolio_artifacts(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)
    run_id = "20260501_port01"
    artifact_dir = service.registry.build_run_paths(run_id)["snapshot_dir"] / "managed_artifacts" / "portfolio"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    equity_path = artifact_dir / "20260501_portfolio_demo_equity_curve.parquet"
    holdings_path = artifact_dir / "20260501_portfolio_demo_holdings.parquet"
    rebalance_path = artifact_dir / "20260501_portfolio_demo_rebalance_audit.parquet"
    metadata_path = artifact_dir / "20260501_portfolio_demo_metadata.json"

    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
            "Equity_value": [100.0, 101.0, 102.0],
            "Portfolio_return": [0.0, 0.01, 0.00990099],
            "Turnover": [1.0, 0.0, 0.0],
            "Selected_count": [2, 2, 2],
            "Cash_weight": [0.0, 0.0, 0.0],
        }
    ).to_parquet(equity_path, index=False)
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02"]),
            "Asset": ["AAA"],
            "Rank": [1],
            "Selected": [True],
            "Eligible": [True],
            "Score": [1.23],
            "Target_weight": [0.5],
        }
    ).to_parquet(holdings_path, index=False)
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02"]),
            "Selected_assets": [["AAA", "BBB"]],
            "Selected_count": [2],
            "Turnover": [1.0],
            "Equity_value": [100.0],
        }
    ).to_parquet(rebalance_path, index=False)
    metadata_path.write_text(
        json.dumps(
            {
                "artifact_type": "multi_asset_portfolio_backtest",
                "strategy_id": "demo_portfolio",
                "summary": {"rebalance_count": 1},
                "run_validation": {
                    "status": "valid",
                    "expected_symbols": ["AAA", "BBB"],
                    "loaded_symbols": ["AAA", "BBB"],
                    "missing_symbols": [],
                    "effective_start_date": "2024-01-02",
                },
                "config": {
                    "strategy_id": "demo_portfolio",
                    "universe": {"symbols": ["AAA", "BBB"]},
                    "rebalance": {"trigger": {"op": "calendar.month_start"}},
                    "selection": {"rank_by": "momentum_20", "top_n": 2},
                    "allocation": {"position_limit": 0.5},
                },
            }
        ),
        encoding="utf-8",
    )

    service.registry.write_registry_entry(
        {
            "run_id": run_id,
            "module": "autorunner",
            "entrypoint": "test",
            "status": "completed",
            "created_at": "2026-05-01T00:00:00",
            "completed_at": "2026-05-01T00:00:01",
            "config_filename": "backtest_20260501_MULTI_ASSET_demo.json",
            "strategy_mode": "multi_asset_portfolio",
            "run_type": "test",
        }
    )
    service.registry.write_artifact_manifest(
        run_id,
        {
            "schema_version": "1.0",
            "artifacts": [
                {"artifact_type": "portfolio_equity_curve_parquet", "path": str(equity_path), "status": "ready"},
                {"artifact_type": "portfolio_holdings_parquet", "path": str(holdings_path), "status": "ready"},
                {"artifact_type": "portfolio_rebalance_audit_parquet", "path": str(rebalance_path), "status": "ready"},
                {"artifact_type": "portfolio_metadata_json", "path": str(metadata_path), "status": "ready"},
            ],
        },
    )

    runs = service.metrics_runs()
    assert [row["run_id"] for row in runs] == [run_id]
    payload = service.metrics_overview(run_id)
    assert payload["result_type"] == "portfolio"
    assert abs(payload["rows"][0]["total_return"] - 0.02) < 1e-9
    assert payload["portfolio"]["holdings_preview"][0]["Asset"] == "AAA"
    assert payload["portfolio"]["rebalance_preview"][0]["Selected_assets"] == ["AAA", "BBB"]
    assert payload["portfolio"]["data_quality"]["status"] == "valid"
    assert payload["portfolio"]["universe_provenance"]["survivorship_bias_risk"] == "high"
    assert payload["rows"][0]["universe_survivorship_risk"] == "high"
    assert payload["portfolio"]["runs"][0]["turnover_summary"]["trade_events"] == 1

    matrix = service.parameter_matrix(run_id)
    assert matrix["availability"] == "no_parameter_domain"
    assert matrix["result_type"] == "portfolio"
    assert "dataset_label" in matrix
    assert matrix["source_row_count"] == 1
    assert matrix["rows"] == []


def test_metrics_runs_preserves_same_config_run_history(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)
    config_filename = "strategy-run-demo-template.json"
    _write_minimal_portfolio_metrics_run(
        service,
        "20260519_oldrun",
        config_filename=config_filename,
        created_at="2026-05-19T21:00:00+08:00",
    )
    _write_minimal_portfolio_metrics_run(
        service,
        "20260604_newrun",
        config_filename=config_filename,
        created_at="2026-06-04T18:00:00+08:00",
    )

    run_ids = [row["run_id"] for row in service.metrics_runs()]

    assert run_ids[:2] == ["20260604_newrun", "20260519_oldrun"]


def test_app_pairs_multiple_canonical_portfolio_artifacts(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)
    run_id = "20260501_port_matrix"
    artifact_dir = service.registry.build_run_paths(run_id)["snapshot_dir"] / "managed_artifacts" / "portfolio"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for suffix, end_equity in [("abc123", 101.0), ("abc123_2", 104.0)]:
        equity_path = artifact_dir / f"backtest_20260501_MULTI_PRICE_rotation_matrix_portfolio-equity_{suffix}.parquet"
        holdings_path = artifact_dir / f"backtest_20260501_MULTI_PRICE_rotation_matrix_portfolio-holdings_{suffix}.parquet"
        rebalance_path = artifact_dir / f"backtest_20260501_MULTI_PRICE_rotation_matrix_portfolio-rebalance-audit_{suffix}.parquet"
        metadata_path = artifact_dir / f"backtest_20260501_MULTI_PRICE_rotation_matrix_portfolio-metadata_{suffix}.json"
        pd.DataFrame(
            {
                "Time": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "Equity_value": [100.0, end_equity],
                "Portfolio_return": [0.0, end_equity / 100.0 - 1.0],
                "Turnover": [1.0, 0.0],
                "Selected_count": [1, 1],
                "Cash_weight": [0.0, 0.0],
            }
        ).to_parquet(equity_path, index=False)
        pd.DataFrame(
            {
                "Time": pd.to_datetime(["2024-01-02"]),
                "Asset": ["AAA"],
                "Rank": [1],
                "Selected": [True],
                "Eligible": [True],
                "Score": [1.0],
                "Target_weight": [1.0],
            }
        ).to_parquet(holdings_path, index=False)
        pd.DataFrame(
            {
                "Time": pd.to_datetime(["2024-01-02"]),
                "Selected_assets": [["AAA"]],
                "Selected_count": [1],
                "Turnover": [1.0],
                "Equity_value": [100.0],
            }
        ).to_parquet(rebalance_path, index=False)
        metadata_path.write_text(
            json.dumps(
                {
                    "artifact_type": "multi_asset_portfolio_backtest",
                    "strategy_id": f"portfolio_{suffix}",
                    "summary": {"rebalance_count": 1},
                    "config": {
                        "strategy_id": f"portfolio_{suffix}",
                        "universe": {"symbols": ["AAA"]},
                        "rebalance": {"trigger": {"op": "calendar.every_session"}},
                        "selection": {"rank_by": "momentum", "top_n": 1},
                        "allocation": {"position_limit": 1.0},
                    },
                }
            ),
            encoding="utf-8",
        )
        artifacts.extend(
            [
                {"artifact_type": "portfolio_equity_curve_parquet", "path": str(equity_path), "status": "ready"},
                {"artifact_type": "portfolio_holdings_parquet", "path": str(holdings_path), "status": "ready"},
                {"artifact_type": "portfolio_rebalance_audit_parquet", "path": str(rebalance_path), "status": "ready"},
                {"artifact_type": "portfolio_metadata_json", "path": str(metadata_path), "status": "ready"},
            ]
        )

    service.registry.write_registry_entry(
        {
            "run_id": run_id,
            "module": "autorunner",
            "entrypoint": "test",
            "status": "completed",
            "created_at": "2026-05-01T00:00:00",
            "completed_at": "2026-05-01T00:00:01",
            "config_filename": "backtest_20260501_MULTI_PRICE_rotation_matrix.json",
            "strategy_mode": "multi_asset_portfolio",
            "run_type": "test",
        }
    )
    service.registry.write_artifact_manifest(
        run_id,
        {"schema_version": "1.0", "artifacts": artifacts},
    )

    payload = service.metrics_overview(run_id)

    assert payload["result_type"] == "portfolio"
    assert len(payload["rows"]) == 2
    assert len(payload["portfolio"]["runs"]) == 2
    assert {round(row["total_return"], 2) for row in payload["rows"]} == {0.01, 0.04}


def test_portfolio_period_returns_support_sparse_indexed_equity_curve(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)
    run_id = "20260513_btc_sparse_periods"
    artifact_dir = service.registry.build_run_paths(run_id)["snapshot_dir"] / "managed_artifacts" / "portfolio"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    equity_path = artifact_dir / "btc_monthly_sparse_equity_curve.parquet"
    metadata_path = artifact_dir / "btc_monthly_sparse_metadata.json"
    equity = pd.DataFrame(
        {
            "equity": [100.0, 110.0, 121.0],
            "Portfolio_return": [0.0, 0.10, 0.10],
            "Turnover": [1.0, 1.0, 1.0],
        },
        index=pd.to_datetime(
            [
                "2024-01-15T00:00:00Z",
                "2024-02-15T00:00:00Z",
                "2024-04-15T00:00:00Z",
            ],
            utc=True,
        ),
    )
    equity.to_parquet(equity_path)
    metadata_path.write_text(
        json.dumps(
            {
                "artifact_type": "multi_asset_portfolio_backtest",
                "strategy_id": "btcusdt_monthly_sparse",
                "summary": {"rebalance_count": 3},
                "config": {
                    "strategy_mode_id": "calendar_event_session",
                    "strategy_id": "btcusdt_monthly_sparse",
                    "resolved_params": {"month_week": 1, "weekday": "monday"},
                    "universe": {"symbols": ["BTCUSDT"]},
                },
            }
        ),
        encoding="utf-8",
    )
    service.registry.write_registry_entry(
        {
            "run_id": run_id,
            "module": "autorunner",
            "entrypoint": "test",
            "status": "completed",
            "created_at": "2026-05-13T00:00:00",
            "completed_at": "2026-05-13T00:00:01",
            "config_filename": "backtest_BTCUSDT_binance_monthly-nth-weekday-same-session_matrix_example.json",
            "strategy_mode": "calendar_event_session",
            "run_type": "test",
        }
    )
    service.registry.write_artifact_manifest(
        run_id,
        {
            "schema_version": "1.0",
            "artifacts": [
                {"artifact_type": "portfolio_equity_curve_parquet", "path": str(equity_path), "status": "ready"},
                {"artifact_type": "portfolio_metadata_json", "path": str(metadata_path), "status": "ready"},
            ],
        },
    )

    overview = service.metrics_overview(run_id)
    assert overview["rows"][0]["date_range_start"].startswith("2024-01-15")
    detail = service.backtest_detail(run_id, "btcusdt_monthly_sparse")

    monthly = {row["period"]: row for row in detail["monthly_return_rows"]}
    assert abs(monthly["2024-02"]["return"] - 0.10) < 1e-9
    assert monthly["2024-03"]["return"] == 0.0
    assert abs(monthly["2024-04"]["return"] - 0.10) < 1e-9
    assert detail["portfolio_visual_availability"]["monthly_return_heatmap"] is True
    assert abs(detail["yearly_return_rows"][0]["return"] - 0.21) < 1e-9


def test_app_pairs_renamed_portfolio_artifacts_with_source_hash_suffix(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)
    run_id = "20260504_matrix"
    artifact_dir = service.registry.build_run_paths(run_id)["snapshot_dir"] / "managed_artifacts" / "backtester"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    variant = "20260504_portfolio_qqq_price_ma_cross_sweep_v1_entry_ma_10_exit_ma_50_qqq_price_ma"
    prefix = "backtest_20260504_QQQ_PRICE_ma-cross_matrix"
    equity_path = artifact_dir / f"{prefix}_portfolio-equity_{variant}_equity_curve_153225_91486b.parquet"
    holdings_path = artifact_dir / f"{prefix}_portfolio-holdings_{variant}_holdings_e77dbe_91486b.parquet"
    rebalance_path = artifact_dir / f"{prefix}_portfolio-rebalance-audit_{variant}_rebalance_audit_eed538_91486b.parquet"
    metadata_path = artifact_dir / f"{prefix}_portfolio-metadata_{variant}_metadata_851fc1_91486b.json"

    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "Equity_value": [100.0, 105.0],
            "Portfolio_return": [0.0, 0.05],
            "Turnover": [1.0, 0.0],
            "Selected_count": [1, 1],
            "Cash_weight": [0.0, 0.0],
        }
    ).to_parquet(equity_path, index=False)
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02"]),
            "Asset": ["QQQ"],
            "Rank": [1],
            "Selected": [True],
            "Eligible": [True],
            "Score": [1.0],
            "Target_weight": [1.0],
        }
    ).to_parquet(holdings_path, index=False)
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02"]),
            "Selected_assets": [["QQQ"]],
            "Selected_count": [1],
            "Turnover": [1.0],
            "Equity_value": [100.0],
        }
    ).to_parquet(rebalance_path, index=False)
    metadata_path.write_text(
        json.dumps(
            {
                "artifact_type": "multi_asset_portfolio_backtest",
                "strategy_id": "qqq_price_ma_cross_sweep_v1_entry_ma_10_exit_ma_50",
                "summary": {"rebalance_count": 1},
                "config": {
                    "strategy_id": "qqq_price_ma_cross_sweep_v1_entry_ma_10_exit_ma_50",
                    "resolved_params": {"entry_ma": 10, "exit_ma": 50},
                    "universe": {"symbols": ["QQQ"]},
                    "rebalance": {"trigger": {"op": "signal.change"}},
                    "allocation": {"method": "signal_target_weight", "position_limit": 1.0},
                },
            }
        ),
        encoding="utf-8",
    )
    service.registry.write_registry_entry(
        {
            "run_id": run_id,
            "module": "autorunner",
            "entrypoint": "test",
            "status": "completed",
            "created_at": "2026-05-04T00:00:00",
            "completed_at": "2026-05-04T00:00:01",
            "config_filename": "backtest_20260504_QQQ_PRICE_ma-cross_matrix.json",
            "strategy_mode": "single_asset_portfolio",
            "run_type": "test",
        }
    )
    service.registry.write_artifact_manifest(
        run_id,
        {
            "schema_version": "1.0",
            "artifacts": [
                {"artifact_type": "portfolio_equity_curve_parquet", "path": str(equity_path), "status": "ready"},
                {"artifact_type": "portfolio_holdings_parquet", "path": str(holdings_path), "status": "ready"},
                {"artifact_type": "portfolio_rebalance_audit_parquet", "path": str(rebalance_path), "status": "ready"},
                {"artifact_type": "portfolio_metadata_json", "path": str(metadata_path), "status": "ready"},
            ],
        },
    )

    payload = service.metrics_overview(run_id)

    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["label"] == "QQQ | MA Cross | entry_ma=10 | exit_ma=50"
    assert payload["rows"][0]["label_source"] == "portfolio_display_label"
    assert payload["rows"][0]["semantic_combo"] == {"entry_ma": 10, "exit_ma": 50}
    assert payload["portfolio"]["runs"][0]["artifact_paths"]["holdings"] == str(holdings_path)


def test_portfolio_strategy_table_label_removes_source_noise(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)

    label = service.payloads._portfolio_strategy_table_label(
        run_id="20260515_noise_label",
        metadata={},
        config={
            "strategy_id": "qqq_daily_sma_cross_yfinance_example_short_ma_60_long_ma_290",
            "universe": {"symbols": ["QQQ"]},
            "resolved_params": {"short_ma": 60, "long_ma": 290},
        },
        strategy_id="qqq_daily_sma_cross_yfinance_example_short_ma_60_long_ma_290",
        params={"short_ma": 60, "long_ma": 290},
    )

    assert label == "QQQ | Daily SMA Cross | short_ma=60 | long_ma=290"


def test_backtest_label_cleans_internal_strategy_ids(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)

    label = service.payloads._build_backtest_label(
        "qqq_tqqq_quarterly_third_friday_short_overlay_yfinance_example",
        {
            "strategy_id": "qqq_tqqq_quarterly_third_friday_short_overlay_yfinance_example",
            "strategy_display_label": "qqq_tqqq_quarterly_third_friday_short_overlay_yfinance_example",
            "semantic_combo": {},
        },
    )

    assert label == "QQQ-TQQQ | Quarterly Third Friday Short Overlay"


def test_portfolio_benchmark_fallback_for_single_symbol_unified_run(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)
    resolved = service.payloads._portfolio_config_with_benchmark_fallback(
        "missing_source_run",
        {
            "strategy_id": "qqq_signal",
            "universe": {"symbols": ["QQQ"]},
            "allocation": {"method": "signal_target_weight", "position_limit": 1.0},
        },
        {},
    )

    assert resolved["benchmark"]["symbol"] == "QQQ"
    assert resolved["benchmark"]["label"] == "QQQ Buy & Hold"


def test_payload_filename_sanitizes_backtest_ids_for_windows(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)
    safe_name = service.payloads._safe_payload_filename(
        "Backtest | 2026-05-06 | BTCUSDT-ETHUSDT | Binance | MA Above"
    )

    assert "|" not in safe_name
    assert ":" not in safe_name
    assert " " not in safe_name
    assert safe_name.endswith("_" + safe_name.rsplit("_", 1)[-1])


def test_portfolio_benchmark_fallback_does_not_invent_multi_asset_benchmark(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)
    resolved = service.payloads._portfolio_config_with_benchmark_fallback(
        "missing_source_run",
        {
            "strategy_id": "multi_rotation",
            "universe": {"symbols": ["VOO", "GLD"]},
            "allocation": {"method": "equal_weight", "position_limit": 1.0},
        },
        {},
    )

    assert "benchmark" not in resolved


def test_portfolio_benchmark_payload_uses_explicit_aligned_benchmark_series(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)
    benchmark_path = tmp_path / "benchmark-close.csv"
    pd.DataFrame(
        {
            "Time": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "SPY": [200.0, 210.0, 220.0],
        }
    ).to_csv(benchmark_path, index=False)
    equity = pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
            "Equity_value": [100.0, 90.0, 95.0],
        }
    )

    payload = service.payloads._portfolio_benchmark_payload(
        {
            "provider": "file",
            "benchmark": {"symbol": "SPY", "provider": "file", "label": "SPY benchmark"},
            "market_data": {"close": {"path": str(benchmark_path), "time_column": "Time"}},
        },
        equity,
    )

    assert payload["label"] == "SPY benchmark"
    assert [point["value"] for point in payload["series"]] == pytest.approx([100.0, 105.0, 110.0])
    assert payload["metrics"]["total_return"] == pytest.approx(0.10)


def test_portfolio_detail_marks_legacy_missing_validation_and_contribution_summary(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)
    run_id = "20260501_legacy_portfolio"
    artifact_dir = service.registry.build_run_paths(run_id)["snapshot_dir"] / "managed_artifacts" / "portfolio"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    equity_path = artifact_dir / "legacy_portfolio_equity_curve.parquet"
    holdings_path = artifact_dir / "legacy_portfolio_holdings.parquet"
    rebalance_path = artifact_dir / "legacy_portfolio_rebalance_audit.parquet"
    metadata_path = artifact_dir / "legacy_portfolio_metadata.json"
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "Equity_value": [100.0, 105.0],
            "Portfolio_return": [0.0, 0.05],
            "Turnover": [1.0, 0.0],
            "Trade_cost": [0.15, 0.0],
            "Selected_count": [2, 2],
            "Cash_weight": [0.0, 0.0],
            "Weight_AAA": [0.5, 0.5],
            "Weight_BBB": [0.5, 0.5],
            "Contribution_AAA": [0.0, 0.03],
            "Contribution_BBB": [0.0, 0.02],
        }
    ).to_parquet(equity_path, index=False)
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02", "2024-01-02"]),
            "Asset": ["AAA", "BBB"],
            "Selected": [True, True],
            "Eligible": [True, True],
            "Target_weight": [0.5, 0.5],
        }
    ).to_parquet(holdings_path, index=False)
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02"]),
            "Selected_assets": [["AAA", "BBB"]],
            "Selected_count": [2],
            "Turnover": [1.0],
            "Trade_cost": [0.15],
            "Equity_value": [99.85],
        }
    ).to_parquet(rebalance_path, index=False)
    metadata_path.write_text(
        json.dumps(
            {
                "artifact_type": "multi_asset_portfolio_backtest",
                "strategy_id": "legacy_portfolio",
                "config": {
                    "strategy_id": "legacy_portfolio",
                    "universe": {"symbols": ["AAA", "BBB"]},
                    "rebalance": {"trigger": {"op": "calendar.month_start"}},
                    "selection": {"rank_by": "momentum", "top_n": 2},
                    "allocation": {"position_limit": 0.5},
                },
            }
        ),
        encoding="utf-8",
    )
    service.registry.write_registry_entry(
        {
            "run_id": run_id,
            "module": "autorunner",
            "entrypoint": "test",
            "status": "completed",
            "created_at": "2026-05-01T00:00:00",
            "completed_at": "2026-05-01T00:00:01",
            "config_filename": "backtest_20260501_MULTI_legacy.json",
            "strategy_mode": "multi_asset_portfolio",
            "run_type": "test",
        }
    )
    service.registry.write_artifact_manifest(
        run_id,
        {
            "schema_version": "1.0",
            "artifacts": [
                {"artifact_type": "portfolio_equity_curve_parquet", "path": str(equity_path), "status": "ready"},
                {"artifact_type": "portfolio_holdings_parquet", "path": str(holdings_path), "status": "ready"},
                {"artifact_type": "portfolio_rebalance_audit_parquet", "path": str(rebalance_path), "status": "ready"},
                {"artifact_type": "portfolio_metadata_json", "path": str(metadata_path), "status": "ready"},
            ],
        },
    )

    overview = service.metrics_overview(run_id)
    assert overview["portfolio"]["data_quality"]["legacy_missing_validation"] is True
    assert overview["portfolio"]["universe_provenance"]["survivorship_bias_risk"] == "high"
    assert overview["portfolio"]["truth_source_policy"]["mode"] == "artifact_only"
    assert "legacy_metadata_missing_universe_provenance" in overview["portfolio"]["truth_warnings"]
    detail = service.backtest_detail(run_id, "legacy_portfolio")
    assert detail["data_quality"]["expected_symbols"] == ["AAA", "BBB"]
    assert detail["universe_provenance"]["survivorship_bias_risk"] == "high"
    assert detail["truth_source_policy"]["silent_recompute_allowed"] is False
    assert "legacy_metadata_missing_universe_provenance" in detail["truth_warnings"]
    assert detail["asset_contribution_summary"]["total_asset_contribution"] == 0.05
    assert len(detail["asset_contribution_rows"]) == 2
    assert detail["portfolio_visual_availability"]["asset_contribution"] is True
    assert detail["portfolio_visual_availability"]["drawdown_curve"] is True
    assert len(detail["drawdown_series"]) == 2
    assert detail["turnover_distribution"][0]["turnover"] == 1.0


def test_portfolio_detail_exposes_risk_gate_artifacts(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)
    run_id = "20260508_risk_gate_portfolio"
    artifact_dir = service.registry.build_run_paths(run_id)["snapshot_dir"] / "managed_artifacts" / "portfolio"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    base = "20260508_portfolio_risk_gate_demo"
    equity_path = artifact_dir / f"{base}_equity_curve.parquet"
    holdings_path = artifact_dir / f"{base}_holdings.parquet"
    rebalance_path = artifact_dir / f"{base}_rebalance_audit.parquet"
    metadata_path = artifact_dir / f"{base}_metadata.json"
    risk_events_path = artifact_dir / f"{base}_risk_gate_events.parquet"
    risk_summary_path = artifact_dir / f"{base}_risk_gate_summary.json"

    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "Equity_value": [100.0, 99.0],
            "Portfolio_return": [0.0, -0.01],
            "Turnover": [1.0, 0.5],
            "Selected_count": [2, 1],
            "Cash_weight": [0.0, 0.5],
        }
    ).to_parquet(equity_path, index=False)
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02"]),
            "Asset": ["AAA"],
            "Selected": [True],
            "Eligible": [True],
            "Target_weight": [1.0],
        }
    ).to_parquet(holdings_path, index=False)
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "Selected_assets": [["AAA"], []],
            "Selected_count": [1, 0],
            "Turnover": [1.0, 0.5],
            "Trade_cost": [0.1, 0.05],
            "Equity_value": [99.9, 98.95],
        }
    ).to_parquet(rebalance_path, index=False)
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-03"]),
            "Gate": ["max_drawdown"],
            "Threshold": [0.005],
            "Observed": [0.01],
            "Action": ["flatten"],
            "Affected_assets": [["AAA"]],
            "Resulting_target_weights": ["{}"],
        }
    ).to_parquet(risk_events_path, index=False)
    risk_summary_path.write_text(
        json.dumps(
            {
                "schema_version": "risk_gate_summary.v1",
                "event_count": 1,
                "gates_triggered": ["max_drawdown"],
                "events_by_gate": {"max_drawdown": 1},
            }
        ),
        encoding="utf-8",
    )
    metadata_path.write_text(
        json.dumps(
            {
                "artifact_type": "multi_asset_portfolio_backtest",
                "strategy_id": "risk_gate_demo",
                "run_validation": {
                    "risk_gate_summary": {
                        "schema_version": "risk_gate_summary.v1",
                        "event_count": 1,
                        "gates_triggered": ["max_drawdown"],
                    }
                },
                "config": {
                    "strategy_id": "risk_gate_demo",
                    "universe": {"symbols": ["AAA"]},
                    "risk": {"gates": {"max_drawdown": 0.005}},
                },
            }
        ),
        encoding="utf-8",
    )
    service.registry.write_registry_entry(
        {
            "run_id": run_id,
            "module": "autorunner",
            "entrypoint": "test",
            "status": "completed",
            "created_at": "2026-05-08T00:00:00",
            "completed_at": "2026-05-08T00:00:01",
            "config_filename": "backtest_20260508_risk_gate_demo.json",
            "strategy_mode": "multi_asset_portfolio",
            "run_type": "test",
        }
    )
    service.registry.write_artifact_manifest(
        run_id,
        {
            "schema_version": "1.0",
            "artifacts": [
                {"artifact_type": "portfolio_equity_curve_parquet", "path": str(equity_path), "status": "ready"},
                {"artifact_type": "portfolio_holdings_parquet", "path": str(holdings_path), "status": "ready"},
                {"artifact_type": "portfolio_rebalance_audit_parquet", "path": str(rebalance_path), "status": "ready"},
                {"artifact_type": "portfolio_metadata_json", "path": str(metadata_path), "status": "ready"},
            ],
        },
    )

    overview = service.metrics_overview(run_id)
    assert overview["portfolio"]["runs"][0]["risk_gate_summary"]["event_count"] == 1
    detail = service.backtest_detail(run_id, "risk_gate_demo")
    assert detail["risk_gate_summary"]["gates_triggered"] == ["max_drawdown"]
    assert detail["risk_gate_rows"][0]["Gate"] == "max_drawdown"
    assert detail["portfolio_visual_availability"]["rebalance_turnover_distribution"] is True
