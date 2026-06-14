import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtester.FeatureContractMaterializer_backtester import (
    FeatureContractMaterializerBacktester,
)
from backtester.NodeIRExecutor_backtester import NodeIRExecutorBacktester

pytestmark = pytest.mark.regression


def _base_price_frame(n: int = 5) -> pd.DataFrame:
    close = np.linspace(100.0, 100.0 + n - 1, n)
    return pd.DataFrame(
        {
            "Time": pd.date_range("2024-01-01", periods=n, freq="D"),
            "Open": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": 1000.0,
            "X": 1.0,
        }
    )


def test_feature_contract_materializer_left_join_applies_fill_and_lag(tmp_path: Path) -> None:
    source_path = tmp_path / "vix.csv"
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-05"]),
            "vix_close": [20.0, 21.0, 23.0],
        }
    ).to_csv(source_path, index=False)

    feature_contract = {
        "primary_key": {"time_field": "Time"},
        "features": [
            {
                "field": "feature.vix.close",
                "source": {"type": "file", "uri": str(source_path), "column": "vix_close", "time_field": "Time"},
                "fill_policy": "ffill",
                "lag_bars": 1,
                "staleness_max_bars": 1,
            }
        ],
        "alignment_policy": {"join_mode": "left"},
    }

    materializer = FeatureContractMaterializerBacktester(
        base_data=_base_price_frame(5),
        repo_root=tmp_path,
    )
    out = materializer.materialize(feature_contract=feature_contract, feature_contract_path=None)

    series = out["feature.vix.close"]
    assert np.isnan(series.iloc[0])
    assert np.isnan(series.iloc[1])
    assert float(series.iloc[2]) == 20.0
    assert float(series.iloc[3]) == 21.0
    assert float(series.iloc[4]) == 21.0
    assert "__audit__feature_vix_close__source_id" in out.columns
    assert "__audit__feature_vix_close__value_origin" in out.columns
    assert "__audit__feature_vix_close__lag_applied" in out.columns
    assert out["__audit__feature_vix_close__lag_applied"].iloc[0] == 1
    assert pd.isna(out["__audit__feature_vix_close__source_time"].iloc[1])
    assert out["__audit__feature_vix_close__source_time"].iloc[2] == pd.Timestamp("2024-01-02")
    assert out["__audit__feature_vix_close__source_time"].iloc[3] == pd.Timestamp("2024-01-03")
    assert out["__audit__feature_vix_close__source_time"].iloc[4] == pd.Timestamp("2024-01-03")
    assert out["__audit__feature_vix_close__value_origin"].tolist() == [
        "missing",
        "missing",
        "exact",
        "exact",
        "ffill",
    ]
    assert out["__audit__feature_vix_close__age_bars"].tolist() == [pd.NA, pd.NA, 1.0, 1.0, 2.0]


def test_feature_contract_materializer_zero_fill_marks_zero_fill_origin(tmp_path: Path) -> None:
    source_path = tmp_path / "regime.csv"
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02", "2024-01-04"]),
            "score": [1.0, 2.0],
        }
    ).to_csv(source_path, index=False)

    feature_contract = {
        "primary_key": {"time_field": "Time"},
        "features": [
            {
                "field": "feature.regime.score",
                "source": {"type": "file", "uri": str(source_path), "column": "score", "time_field": "Time"},
                "fill_policy": "zero",
                "lag_bars": 0,
                "staleness_max_bars": 0,
            }
        ],
        "alignment_policy": {"join_mode": "left"},
    }

    materializer = FeatureContractMaterializerBacktester(
        base_data=_base_price_frame(5),
        repo_root=tmp_path,
    )
    out = materializer.materialize(feature_contract=feature_contract, feature_contract_path=None)

    assert out["feature.regime.score"].tolist() == [0.0, 1.0, 0.0, 2.0, 0.0]
    assert out["__audit__feature_regime_score__value_origin"].tolist() == [
        "zero_fill",
        "exact",
        "zero_fill",
        "exact",
        "zero_fill",
    ]


def test_feature_contract_materializer_asof_respects_tolerance(tmp_path: Path) -> None:
    source_path = tmp_path / "vix_asof.csv"
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-01", "2024-01-04"]),
            "vix_close": [10.0, 40.0],
        }
    ).to_csv(source_path, index=False)

    feature_contract = {
        "primary_key": {"time_field": "Time"},
        "features": [
            {
                "field": "feature.vix.close",
                "source": {"type": "file", "uri": str(source_path), "column": "vix_close", "time_field": "Time"},
                "fill_policy": "none",
                "lag_bars": 0,
                "staleness_max_bars": 1,
            }
        ],
        "alignment_policy": {"join_mode": "asof", "asof_tolerance_bars": 1},
    }

    materializer = FeatureContractMaterializerBacktester(
        base_data=_base_price_frame(5),
        repo_root=tmp_path,
    )
    out = materializer.materialize(feature_contract=feature_contract, feature_contract_path=None)
    series = out["feature.vix.close"]

    assert float(series.iloc[0]) == 10.0
    assert float(series.iloc[1]) == 10.0
    assert np.isnan(series.iloc[2])
    assert float(series.iloc[3]) == 40.0
    assert float(series.iloc[4]) == 40.0
    assert out["__audit__feature_vix_close__value_origin"].iloc[0] == "exact"
    assert out["__audit__feature_vix_close__value_origin"].iloc[1] == "asof"


def test_feature_contract_materializer_respects_explicit_dayfirst_and_time_format(tmp_path: Path) -> None:
    source_path = tmp_path / "mmfi.csv"
    pd.DataFrame(
        {
            "time": ["13/01/2024", "14/01/2024", "15/01/2024"],
            "close": [11.0, 12.0, 13.0],
        }
    ).to_csv(source_path, index=False)

    feature_contract = {
        "primary_key": {"time_field": "Time"},
        "features": [
            {
                "field": "feature.mmfi.close",
                "source": {
                    "type": "file",
                    "uri": str(source_path),
                    "column": "close",
                    "time_field": "time",
                    "dayfirst": True,
                    "time_format": "%d/%m/%Y",
                },
                "fill_policy": "none",
                "lag_bars": 0,
                "staleness_max_bars": 0,
            }
        ],
        "alignment_policy": {"join_mode": "left"},
    }

    materializer = FeatureContractMaterializerBacktester(
        base_data=_base_price_frame(20),
        repo_root=tmp_path,
    )
    out = materializer.materialize(feature_contract=feature_contract, feature_contract_path=None)

    series = out["feature.mmfi.close"]
    assert np.isnan(series.iloc[11])
    assert float(series.iloc[12]) == 11.0
    assert float(series.iloc[13]) == 12.0
    assert float(series.iloc[14]) == 13.0


def test_feature_contract_materializer_left_join_rejects_duplicate_source_keys(tmp_path: Path) -> None:
    source_path = tmp_path / "dup_vix.csv"
    pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02", "2024-01-02"]),
            "vix_close": [20.0, 21.0],
        }
    ).to_csv(source_path, index=False)

    feature_contract = {
        "primary_key": {"time_field": "Time"},
        "features": [
            {
                "field": "feature.vix.close",
                "source": {"type": "file", "uri": str(source_path), "column": "vix_close", "time_field": "Time"},
                "fill_policy": "none",
                "lag_bars": 0,
                "staleness_max_bars": 0,
            }
        ],
        "alignment_policy": {"join_mode": "left"},
    }

    materializer = FeatureContractMaterializerBacktester(
        base_data=_base_price_frame(5),
        repo_root=tmp_path,
    )

    try:
        materializer.materialize(feature_contract=feature_contract, feature_contract_path=None)
    except ValueError as exc:
        assert "unique source keys" in str(exc)
    else:
        raise AssertionError("expected duplicate source keys to raise ValueError")


def test_feature_contract_materializer_price_source_uses_base_frame_without_file_load(tmp_path: Path) -> None:
    feature_contract = {
        "primary_key": {"time_field": "Time"},
        "features": [
            {
                "field": "price.close",
                "source": {"type": "price", "uri": "yfinance:QQQ", "column": "Close"},
                "fill_policy": "none",
                "lag_bars": 0,
                "staleness_max_bars": 0,
            }
        ],
        "alignment_policy": {"join_mode": "left"},
    }

    materializer = FeatureContractMaterializerBacktester(
        base_data=_base_price_frame(4),
        repo_root=tmp_path,
    )
    out = materializer.materialize(feature_contract=feature_contract, feature_contract_path=None)

    assert out["price.close"].tolist() == [100.0, 101.0, 102.0, 103.0]
    assert "__audit__price_close__source_uri" in out.columns
    assert out["__audit__price_close__source_uri"].iloc[0] == "yfinance:QQQ"


def test_feature_contract_materializer_asof_respects_instrument_fields_with_different_names(tmp_path: Path) -> None:
    base = pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03"]),
            "Trading_instrument": ["SPY", "QQQ", "SPY", "QQQ"],
            "Open": [100.0, 200.0, 101.0, 201.0],
            "High": [101.0, 201.0, 102.0, 202.0],
            "Low": [99.0, 199.0, 100.0, 200.0],
            "Close": [100.0, 200.0, 101.0, 201.0],
            "Volume": [1000.0, 1000.0, 1000.0, 1000.0],
        }
    )
    source_path = tmp_path / "multi_symbol_vix.csv"
    pd.DataFrame(
        {
            "ObsTime": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-03", "2024-01-03"]),
            "Symbol": ["SPY", "QQQ", "SPY", "QQQ"],
            "regime_score": [10.0, 90.0, 11.0, 91.0],
        }
    ).to_csv(source_path, index=False)

    feature_contract = {
        "primary_key": {"time_field": "Time", "instrument_field": "Trading_instrument"},
        "features": [
            {
                "field": "feature.regime.score",
                "source": {
                    "type": "file",
                    "uri": str(source_path),
                    "column": "regime_score",
                    "time_field": "ObsTime",
                    "instrument_field": "Symbol",
                },
                "fill_policy": "none",
                "lag_bars": 0,
                "staleness_max_bars": 1,
            }
        ],
        "alignment_policy": {"join_mode": "asof", "asof_tolerance_bars": 2},
    }

    materializer = FeatureContractMaterializerBacktester(
        base_data=base,
        repo_root=tmp_path,
    )
    out = materializer.materialize(feature_contract=feature_contract, feature_contract_path=None)

    spy_values = out.loc[out["Trading_instrument"] == "SPY", "feature.regime.score"].tolist()
    qqq_values = out.loc[out["Trading_instrument"] == "QQQ", "feature.regime.score"].tolist()
    assert spy_values == [10.0, 11.0]
    assert qqq_values == [90.0, 91.0]


def test_feature_contract_materializer_requires_explicit_instrument_keys_for_multi_instrument_join(tmp_path: Path) -> None:
    base = pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03"]),
            "Trading_instrument": ["SPY", "QQQ", "SPY", "QQQ"],
            "Open": [100.0, 200.0, 101.0, 201.0],
            "High": [101.0, 201.0, 102.0, 202.0],
            "Low": [99.0, 199.0, 100.0, 200.0],
            "Close": [100.0, 200.0, 101.0, 201.0],
            "Volume": [1000.0, 1000.0, 1000.0, 1000.0],
        }
    )
    source_path = tmp_path / "multi_symbol_vix_missing_keys.csv"
    pd.DataFrame(
        {
            "ObsTime": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-03", "2024-01-03"]),
            "Symbol": ["SPY", "QQQ", "SPY", "QQQ"],
            "regime_score": [10.0, 90.0, 11.0, 91.0],
        }
    ).to_csv(source_path, index=False)

    feature_contract = {
        "primary_key": {"time_field": "Time"},
        "features": [
            {
                "field": "feature.regime.score",
                "source": {
                    "type": "file",
                    "uri": str(source_path),
                    "column": "regime_score",
                    "time_field": "ObsTime",
                },
                "fill_policy": "none",
                "lag_bars": 0,
                "staleness_max_bars": 1,
            }
        ],
        "alignment_policy": {"join_mode": "asof", "asof_tolerance_bars": 2},
    }

    materializer = FeatureContractMaterializerBacktester(
        base_data=base,
        repo_root=tmp_path,
    )

    try:
        materializer.materialize(feature_contract=feature_contract, feature_contract_path=None)
    except ValueError as exc:
        assert "explicit primary_key.instrument_field" in str(exc)
    else:
        raise AssertionError("expected multi-instrument join without explicit keys to raise ValueError")


def test_node_ir_executor_run_from_paths_materializes_external_multisource_feature(tmp_path: Path) -> None:
    base = _base_price_frame(6)
    base["price.close"] = base["Close"]

    source_path = tmp_path / "vix.csv"
    pd.DataFrame(
        {
            "Time": pd.date_range("2024-01-01", periods=6, freq="D"),
            "vix_close": [35.0, 25.0, 35.0, 25.0, 35.0, 25.0],
        }
    ).to_csv(source_path, index=False)

    strategy_path = tmp_path / "strategy.json"
    strategy_path.write_text(
        json.dumps(
            {
                "schema_version": "strategy_contract",
                "strategy_id": "runtime.multisource.smoke",
                "data_context": {
                    "primary_instrument": "SPY",
                    "frequency": "1D",
                    "timezone": "UTC",
                    "calendar": "XNYS",
                },
                "parameter_domains": {},
                "entry": {
                    "op": "lt",
                    "left": {"field_ref": {"field": "feature.vix.close"}},
                    "right": 30,
                },
                "exit": {"op": "timer_bars", "value": 1},
            }
        ),
        encoding="utf-8",
    )
    feature_path = tmp_path / "feature.json"
    feature_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "dataset_id": "runtime_multisource_test",
                "primary_key": {"time_field": "Time"},
                "features": [
                    {
                        "field": "price.close",
                        "source": {"type": "price", "column": "Close"},
                        "frequency": "1D",
                        "timezone": "UTC",
                        "dtype": "float",
                        "fill_policy": "none",
                        "lag_bars": 0,
                    },
                    {
                        "field": "feature.vix.close",
                        "source": {
                            "type": "file",
                            "uri": str(source_path),
                            "column": "vix_close",
                            "time_field": "Time",
                            "source_id": "vix_daily",
                        },
                        "frequency": "1D",
                        "timezone": "UTC",
                        "dtype": "float",
                        "fill_policy": "none",
                        "lag_bars": 0,
                        "staleness_max_bars": 0,
                    },
                ],
                "alignment_policy": {"join_mode": "left"},
                "time_semantics": {
                    "signal_observation_time": "bar_close",
                    "trade_earliest_time": "next_bar",
                    "default_feature_lag_bars": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    plan = {
        "field_catalog": {},
        "feature_dag": {},
        "node_ir": {
            "entry": {
                "op": "lt",
                "left": {"field_ref": {"field": "feature.vix.close"}},
                "right": 30,
            },
            "exit": {"op": "timer_bars", "value": 1},
        },
    }

    executor = NodeIRExecutorBacktester(base)
    results = executor.run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={
            "transaction_cost": 0.0,
            "slippage": 0.0,
            "trade_delay": 0,
            "trade_price": "close",
            "execution_backend": "python_numba",
        },
        predictor_column="X",
        symbol="SPY",
        backtest_id_prefix="multisource",
    )

    assert len(results) == 1
    records = results[0]["records"]
    assert "feature.vix.close" in executor.data.columns
    assert int((records["Trade_action"] == 1).sum()) >= 1
