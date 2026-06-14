from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from wfanalyser.ResultsExporter_wfanalyser import ResultsExporter
from wfanalyser.WalkForwardEngine_wfanalyser import WalkForwardEngine


def _make_data(n: int = 140) -> pd.DataFrame:
    ts = pd.date_range("2024-01-01", periods=n, freq="D")
    close = pd.Series(range(100, 100 + n), dtype=float)
    return pd.DataFrame(
        {
            "Time": ts,
            "Open": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": 1000.0,
            "X": 1.0,
            "vix": 20.0,
        }
    )


def test_wfa_e2e_smoke_exports_plotter_ready_fields(tmp_path: Path) -> None:
    strategy_path = tmp_path / "strategy-smoke.json"
    strategy_path.write_text(
        json.dumps(
                {
                    "schema_version": "strategy_contract",
                    "strategy_id": "smoke.wfa.semantic",
                    "data_context": {
                        "primary_instrument": "BTCUSDT",
                        "frequency": "1D",
                        "timezone": "UTC",
                        "calendar": "CRYPTO",
                    },
                    "max_combos": 16,
                "parameter_domains": {"p_fast": {"type": "set", "values": [5, 10]}},
                "entry": {
                    "op": "gt",
                    "left": {"field": "price.close"},
                    "right": {
                        "feature": "ta.sma",
                        "source": "price.close",
                        "params": {"period": {"param_ref": "p_fast"}},
                    },
                },
                "exit": {"op": "timer_bars", "value": 3},
            }
        ),
        encoding="utf-8",
    )
    feature_path = tmp_path / "feature-v1-smoke.json"
    feature_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "dataset_id": "smoke_feature_contract",
                "features": [
                    {
                        "field": "price.close",
                        "source": {"type": "price", "uri": "workspace/datasets/price.csv", "column": "Close"},
                        "frequency": "1D",
                        "timezone": "UTC",
                        "dtype": "float",
                        "fill_policy": "none",
                        "lag_bars": 0,
                        "calendar": "CRYPTO",
                    },
                    {
                        "field": "feature.vix.close",
                        "source": {
                            "type": "file",
                            "uri": "workspace/datasets/price.csv",
                            "column": "vix",
                            "source_id": "vix_inline",
                        },
                        "frequency": "1D",
                        "timezone": "UTC",
                        "dtype": "float",
                        "fill_policy": "ffill",
                        "lag_bars": 1,
                        "calendar": "CRYPTO",
                        "staleness_max_bars": 1,
                    },
                ],
                "alignment_policy": {
                    "join_mode": "left",
                    "max_forward_fill_bars": 1,
                    "drop_if_missing_ratio_gt": 0.05,
                    "enforce_monotonic_time": True,
                    "calendar_policy": "primary",
                },
                "time_semantics": {
                    "signal_observation_time": "bar_close",
                    "trade_earliest_time": "next_bar",
                    "default_feature_lag_bars": 1,
                },
            }
        ),
        encoding="utf-8",
    )

    config_data = SimpleNamespace(
        file_path=str(tmp_path / "wfa-smoke.json"),
        wfa_config={
            "mode": "standard",
            "train_set_percentage": 0.6,
            "test_set_percentage": 0.2,
            "step_size": 40,
            "optimization_objectives": ["sharpe"],
            "output_csv": True,
        },
        dataloader_config={"source": "file", "start_date": "2024-01-01"},
        predictor_config={"skip_predictor": True},
        backtester_config={
            "strategy_mode": "semantic",
            "strategy_contract_path": str(strategy_path),
            "feature_contract_path": str(feature_path),
            "selected_predictor": "X",
            "trading_params": {
                "transaction_cost": 0.0,
                "slippage": 0.0,
                "trade_delay": 0,
                "trade_price": "close",
                "execution_backend": "python_numba",
            },
        },
        metricstracker_config={"enable_metrics_analysis": True},
        symbol="BTCUSDT",
    )

    engine = WalkForwardEngine(config_data=config_data)
    engine._load_data = lambda: (setattr(engine, "data", _make_data()), setattr(engine, "frequency", "1D"))  # type: ignore[attr-defined]
    results = engine.run()
    assert results is not None
    assert "contract_audit" in results

    exporter = ResultsExporter(results=results, output_dir=tmp_path, config_data=config_data, data=engine.data)
    exporter.export()

    main_csv_files = sorted(
        p for p in tmp_path.glob("*_wfa_sharpe_*.csv") if "_wfa_ranking_" not in p.name
    )
    main_parquet_files = sorted(
        p for p in tmp_path.glob("*_wfa_sharpe_*.parquet") if "_wfa_ranking_" not in p.name and not p.name.endswith("_audit.parquet")
    )
    ranking_csv_files = sorted(tmp_path.glob("*_wfa_ranking_sharpe_*_top*.csv"))
    assert main_csv_files
    assert main_parquet_files
    assert ranking_csv_files
    df = pd.read_csv(main_csv_files[-1])
    for col in [
        "strategy_mode",
        "strategy_contract_path",
        "feature_contract_path",
        "execution_plan_path",
        "execution_plan_id",
        "execution_plan_hash",
        "window_result_hash",
        "semantic_combo",
    ]:
        assert col in df.columns
    assert df["window_result_hash"].astype(str).str.len().min() == 64
    metadata_path = main_parquet_files[-1].with_name(main_parquet_files[-1].stem + "_metadata.json")
    audit_json_path = main_parquet_files[-1].with_name(main_parquet_files[-1].stem + "_audit.json")
    audit_parquet_path = main_parquet_files[-1].with_name(main_parquet_files[-1].stem + "_audit.parquet")
    assert metadata_path.exists()
    assert audit_json_path.exists()
    assert audit_parquet_path.exists()


def test_wfa_e2e_smoke_supports_external_multisource_feature_contract(tmp_path: Path) -> None:
    strategy_path = tmp_path / "strategy-multisource.json"
    strategy_path.write_text(
        json.dumps(
            {
                "schema_version": "strategy_contract",
                "strategy_id": "smoke.wfa.semantic.multisource",
                "data_context": {
                    "primary_instrument": "SPY",
                    "frequency": "1D",
                    "timezone": "UTC",
                    "calendar": "XNYS",
                },
                "parameter_domains": {},
                "entry": {
                    "op": "lt",
                    "left": {"field": "feature.vix.close"},
                    "right": 30,
                },
                "exit": {"op": "timer_bars", "value": 2},
            }
        ),
        encoding="utf-8",
    )

    vix_path = tmp_path / "vix.csv"
    pd.DataFrame(
        {
            "Time": pd.date_range("2024-01-01", periods=140, freq="D"),
            "vix_close": [25.0 if idx % 10 < 3 else 35.0 for idx in range(140)],
        }
    ).to_csv(vix_path, index=False)
    price_path = tmp_path / "price.csv"
    _make_data().drop(columns=["vix"]).to_csv(price_path, index=False)

    feature_path = tmp_path / "feature-v1-multisource.json"
    feature_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "dataset_id": "smoke_multisource_feature_contract",
                "primary_key": {"time_field": "Time"},
                "features": [
                    {
                        "field": "price.close",
                        "source": {"type": "price", "uri": str(price_path), "column": "Close", "time_field": "Time"},
                        "frequency": "1D",
                        "timezone": "UTC",
                        "dtype": "float",
                        "fill_policy": "none",
                        "lag_bars": 0,
                        "calendar": "XNYS",
                    },
                    {
                        "field": "feature.vix.close",
                        "source": {
                            "type": "file",
                            "uri": str(vix_path),
                            "column": "vix_close",
                            "time_field": "Time",
                            "source_id": "vix_daily",
                        },
                        "frequency": "1D",
                        "timezone": "UTC",
                        "dtype": "float",
                        "fill_policy": "none",
                        "lag_bars": 0,
                        "calendar": "CBOE",
                        "staleness_max_bars": 1,
                    },
                ],
                "alignment_policy": {
                    "join_mode": "left",
                    "max_forward_fill_bars": 1,
                    "drop_if_missing_ratio_gt": 0.05,
                    "enforce_monotonic_time": True,
                    "calendar_policy": "primary",
                },
                "time_semantics": {
                    "signal_observation_time": "bar_close",
                    "trade_earliest_time": "next_bar",
                    "default_feature_lag_bars": 1,
                },
            }
        ),
        encoding="utf-8",
    )

    base_data = _make_data().drop(columns=["vix"])
    config_data = SimpleNamespace(
        file_path=str(tmp_path / "wfa-smoke-multisource.json"),
        wfa_config={
            "mode": "standard",
            "train_set_percentage": 0.6,
            "test_set_percentage": 0.2,
            "step_size": 40,
            "optimization_objectives": ["sharpe"],
            "output_csv": True,
        },
        dataloader_config={"source": "file", "start_date": "2024-01-01"},
        predictor_config={"skip_predictor": True},
        backtester_config={
            "strategy_mode": "semantic",
            "strategy_contract_path": str(strategy_path),
            "feature_contract_path": str(feature_path),
            "selected_predictor": "X",
            "trading_params": {
                "transaction_cost": 0.0,
                "slippage": 0.0,
                "trade_delay": 0,
                "trade_price": "close",
                "execution_backend": "python_numba",
            },
        },
        metricstracker_config={"enable_metrics_analysis": True},
        symbol="SPY",
    )

    engine = WalkForwardEngine(config_data=config_data)
    engine._load_data = lambda: (setattr(engine, "data", base_data.copy()), setattr(engine, "frequency", "1D"))  # type: ignore[attr-defined]
    results = engine.run()

    assert results is not None
    assert "contract_audit" in results
    assert results["results_by_objective"]["sharpe"]
