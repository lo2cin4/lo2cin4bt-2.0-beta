import importlib
import json
import uuid

import numpy as np
import pandas as pd
import pytest


def _executor(data: pd.DataFrame):
    mod = importlib.import_module("backtester.NodeIRExecutor_backtester")
    return mod.NodeIRExecutorBacktester(data)


def _base_frame(close: np.ndarray, vix: np.ndarray | None = None) -> pd.DataFrame:
    if vix is None:
        vix = np.full_like(close, 20.0, dtype=float)
    n = len(close)
    return pd.DataFrame(
        {
            "Time": pd.date_range("2024-01-01", periods=n, freq="D"),
            "Open": close,
            "High": close,
            "Low": close,
            "Close": close,
            "Volume": np.ones(n),
            "X": np.ones(n),
            "price.close": close,
            "feature.vix.close": vix,
        }
    )


def test_node_ir_single_condition_close_gt_ma200():
    close = np.linspace(1.0, 300.0, 300)
    ex = _executor(_base_frame(close))
    plan = {
        "field_catalog": {},
        "feature_dag": {
            "feat_ma200": {
                "feature": "ta.sma",
                "source": "price.close",
                "params": {"period": 200},
            }
        },
        "node_ir": {
            "entry": {
                "op": "gt",
                "left": {"field_ref": {"field": "price.close"}},
                "right": {"feature_ref": {"feature_key": "feat_ma200"}},
            },
            "exit": {"op": "lt", "left": {"field_ref": {"field": "price.close"}}, "right": 0},
        },
    }
    entry, _, _, _, _ = ex._evaluate_signals_for_combo(  # pylint: disable=protected-access
        execution_plan=plan,
        feature_contract={},
        combo={},
    )
    assert np.count_nonzero(entry == 1.0) > 50


def test_node_ir_multi_condition_vix_and_close_gt_ma200():
    close = np.linspace(1.0, 300.0, 300)
    vix = np.concatenate([np.full(150, 35.0), np.full(150, 25.0)])
    ex = _executor(_base_frame(close, vix))
    plan = {
        "field_catalog": {},
        "feature_dag": {
            "feat_ma200": {
                "feature": "ta.sma",
                "source": "price.close",
                "params": {"period": 200},
            }
        },
        "node_ir": {
            "entry": {
                "op": "and",
                "nodes": [
                    {
                        "op": "lt",
                        "left": {"field_ref": {"field": "feature.vix.close"}},
                        "right": 30,
                    },
                    {
                        "op": "gt",
                        "left": {"field_ref": {"field": "price.close"}},
                        "right": {"feature_ref": {"feature_key": "feat_ma200"}},
                    },
                ],
            },
            "exit": {"op": "lt", "left": {"field_ref": {"field": "price.close"}}, "right": 0},
        },
    }
    entry, _, _, _, _ = ex._evaluate_signals_for_combo(  # pylint: disable=protected-access
        execution_plan=plan,
        feature_contract={},
        combo={},
    )
    assert np.count_nonzero(entry == 1.0) > 0
    assert np.count_nonzero(entry[:180] == 1.0) == 0


def test_node_ir_multi_feature_contract_supports_a_and_b_and_price_conditions():
    close = np.linspace(1.0, 300.0, 300)
    data = pd.DataFrame(
        {
            "Time": pd.date_range("2024-01-01", periods=300, freq="D"),
            "Open": close,
            "High": close,
            "Low": close,
            "Close": close,
            "Volume": np.ones(300),
            "X": np.ones(300),
            "vix_raw": np.concatenate([np.full(180, 35.0), np.full(120, 25.0)]),
            "regime_score_raw": np.concatenate([np.zeros(210), np.ones(90)]),
        }
    )
    ex = _executor(data)
    feature_contract = {
        "features": [
            {
                "field": "feature.vix.close",
                "source": {"type": "file", "uri": "workspace/datasets/a.csv", "column": "vix_raw"},
                "frequency": "1D",
                "timezone": "UTC",
                "dtype": "float",
                "fill_policy": "none",
                "lag_bars": 0,
            },
            {
                "field": "feature.regime.score",
                "source": {"type": "file", "uri": "workspace/datasets/b.csv", "column": "regime_score_raw"},
                "frequency": "1D",
                "timezone": "UTC",
                "dtype": "float",
                "fill_policy": "none",
                "lag_bars": 0,
            },
        ]
    }
    plan = {
        "field_catalog": {},
        "feature_dag": {
            "feat_ma200": {
                "feature": "ta.sma",
                "source": "price.close",
                "params": {"period": 200},
            }
        },
        "node_ir": {
            "entry": {
                "op": "and",
                "nodes": [
                    {
                        "op": "lt",
                        "left": {"field_ref": {"field": "feature.vix.close"}},
                        "right": 30,
                    },
                    {
                        "op": "ge",
                        "left": {"field_ref": {"field": "feature.regime.score"}},
                        "right": 1,
                    },
                    {
                        "op": "gt",
                        "left": {"field_ref": {"field": "price.close"}},
                        "right": {"feature_ref": {"feature_key": "feat_ma200"}},
                    },
                ],
            },
            "exit": {"op": "lt", "left": {"field_ref": {"field": "price.close"}}, "right": 0},
        },
    }
    entry, _, _, _, _ = ex._evaluate_signals_for_combo(  # pylint: disable=protected-access
        execution_plan=plan,
        feature_contract=feature_contract,
        combo={},
    )
    assert np.count_nonzero(entry == 1.0) > 0
    assert np.count_nonzero(entry[:210] == 1.0) == 0


def test_node_ir_cross_up_ma10_ma20():
    close = np.concatenate(
        [
            np.full(80, 100.0),
            np.linspace(100.0, 160.0, 60),
            np.full(80, 160.0),
        ]
    )
    ex = _executor(_base_frame(close))
    plan = {
        "field_catalog": {},
        "feature_dag": {
            "feat_ma10": {
                "feature": "ta.sma",
                "source": "price.close",
                "params": {"period": 10},
            },
            "feat_ma20": {
                "feature": "ta.sma",
                "source": "price.close",
                "params": {"period": 20},
            },
        },
        "node_ir": {
            "entry": {
                "op": "cross_up",
                "left": {"feature_ref": {"feature_key": "feat_ma10"}},
                "right": {"feature_ref": {"feature_key": "feat_ma20"}},
            },
            "exit": {
                "op": "cross_down",
                "left": {"feature_ref": {"feature_key": "feat_ma10"}},
                "right": {"feature_ref": {"feature_key": "feat_ma20"}},
            },
        },
    }
    entry, _, _, _, _ = ex._evaluate_signals_for_combo(  # pylint: disable=protected-access
        execution_plan=plan,
        feature_contract={},
        combo={},
    )
    assert np.count_nonzero(entry == 1.0) >= 1


def test_node_ir_extracts_timer_bars_exit():
    close = np.linspace(1.0, 20.0, 20)
    ex = _executor(_base_frame(close))
    plan = {
        "field_catalog": {},
        "feature_dag": {},
        "node_ir": {
            "entry": {
                "op": "gt",
                "left": {"field_ref": {"field": "price.close"}},
                "right": 0,
            },
            "exit": {"op": "timer_bars", "value": 3},
        },
    }
    entry, exit_signal, timer_bars, timer_requires_exit, timer_mode = ex._evaluate_signals_for_combo(  # pylint: disable=protected-access
        execution_plan=plan,
        feature_contract={},
        combo={},
    )
    assert np.count_nonzero(entry == 1.0) > 0
    assert np.count_nonzero(exit_signal != 0.0) == 0
    assert timer_bars == 3
    assert timer_requires_exit is False
    assert timer_mode == "timer_only"


def test_node_ir_extracts_timer_bars_param_ref_exit():
    close = np.linspace(1.0, 20.0, 20)
    ex = _executor(_base_frame(close))
    plan = {
        "field_catalog": {},
        "feature_dag": {},
        "node_ir": {
            "entry": {
                "op": "gt",
                "left": {"field_ref": {"field": "price.close"}},
                "right": 0,
            },
            "exit": {"op": "timer_bars", "value": {"param_ref": "hold_days"}},
        },
    }
    entry, exit_signal, timer_bars, timer_requires_exit, timer_mode = ex._evaluate_signals_for_combo(  # pylint: disable=protected-access
        execution_plan=plan,
        feature_contract={},
        combo={"hold_days": 7},
    )
    assert np.count_nonzero(entry == 1.0) > 0
    assert np.count_nonzero(exit_signal != 0.0) == 0
    assert timer_bars == 7
    assert timer_requires_exit is False
    assert timer_mode == "timer_only"


def test_node_ir_logic_or_not():
    close = np.concatenate([np.full(10, 10.0), np.full(10, 30.0)])
    vix = np.concatenate([np.full(10, 35.0), np.full(10, 20.0)])
    ex = _executor(_base_frame(close, vix))
    plan = {
        "field_catalog": {},
        "feature_dag": {},
        "node_ir": {
            "entry": {
                "op": "or",
                "nodes": [
                    {
                        "op": "lt",
                        "left": {"field_ref": {"field": "feature.vix.close"}},
                        "right": 25,
                    },
                    {
                        "op": "not",
                        "node": {
                            "op": "le",
                            "left": {"field_ref": {"field": "price.close"}},
                            "right": 15,
                        },
                    },
                ],
            },
            "exit": {"op": "lt", "left": {"field_ref": {"field": "price.close"}}, "right": 0},
        },
    }
    entry, _, _, _, _ = ex._evaluate_signals_for_combo(  # pylint: disable=protected-access
        execution_plan=plan,
        feature_contract={},
        combo={},
    )
    assert np.count_nonzero(entry[:10] == 1.0) == 0
    assert np.count_nonzero(entry[10:] == 1.0) == 10


def test_node_ir_semantic_results_use_random_backtest_id_and_multi_predictor_columns(monkeypatch):
    module = importlib.import_module("backtester.NodeIRExecutor_backtester")
    monkeypatch.setattr(
        module.uuid,
        "uuid4",
        lambda: uuid.UUID("12345678123456781234567812345678"),
    )
    data = pd.DataFrame(
        {
            "Time": pd.date_range("2024-01-01", periods=4, freq="D"),
            "Open": [100.0, 101.0, 102.0, 103.0],
            "High": [100.0, 101.0, 102.0, 103.0],
            "Low": [100.0, 101.0, 102.0, 103.0],
            "Close": [100.0, 101.0, 102.0, 103.0],
            "Volume": [1.0, 1.0, 1.0, 1.0],
            "X": [1.0, 1.0, 1.0, 1.0],
            "feature.mmfi.close": [10.0, 11.0, 12.0, 13.0],
            "feature.vix.close": [35.0, 34.0, 33.0, 32.0],
        }
    )
    ex = module.NodeIRExecutorBacktester(data)
    result = ex._simulate_single_strategy(  # pylint: disable=protected-access
        combo_idx=0,
        combo={},
        entry_signal=np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64),
        exit_signal=np.array([0.0, 0.0, -1.0, 0.0], dtype=np.float64),
        timer_bars=None,
        timer_requires_exit_signal=False,
        timer_combine_mode="timer_only",
        trading_params={"transaction_cost": 0.0, "slippage": 0.0, "trade_delay": 0, "trade_price": "close"},
        predictor_column="X",
        symbol="SPY",
        backtest_id_prefix="spy_mmfi_vix_250d_reset",
        semantic_predictor_fields=["feature.mmfi.close", "feature.vix.close"],
    )
    assert result["Backtest_id"] == "123456781234"
    assert result["params"]["strategy_mode"] == "semantic"
    assert result["params"]["semantic_run_label"] == "spy_mmfi_vix_250d_reset"
    assert result["params"]["semantic_fields"] == ["feature.mmfi.close", "feature.vix.close"]
    assert result["params"]["predictor"] == "feature.mmfi.close"
    records = result["records"]
    assert "Predictor_1_name" in records.columns
    assert "Predictor_1_value" in records.columns
    assert "Predictor_2_name" in records.columns
    assert "Predictor_2_value" in records.columns
    assert records.loc[0, "Predictor_1_name"] == "feature.mmfi.close"
    assert records.loc[0, "Predictor_2_name"] == "feature.vix.close"
    assert records.loc[0, "Predictor_1_value"] == 10.0
    assert records.loc[0, "Predictor_2_value"] == 35.0


def test_collect_semantic_export_fields_resolves_feature_only_strategy_inputs():
    close = np.linspace(1.0, 60.0, 60)
    ex = _executor(_base_frame(close))
    plan = {
        "field_catalog": {},
        "feature_dag": {
            "feat_ma_fast": {
                "feature": "ta.sma",
                "source": "price.close",
                "params": {"period": 5},
            },
            "feat_ma_slow": {
                "feature": "ta.sma",
                "source": "price.close",
                "params": {"period": 10},
            },
        },
        "node_ir": {
            "entry": {
                "op": "cross_up",
                "left": {"feature_ref": {"feature_key": "feat_ma_fast"}},
                "right": {"feature_ref": {"feature_key": "feat_ma_slow"}},
            },
            "exit": {
                "op": "cross_down",
                "left": {"feature_ref": {"feature_key": "feat_ma_fast"}},
                "right": {"feature_ref": {"feature_key": "feat_ma_slow"}},
            },
        },
    }

    semantic_fields = ex._collect_semantic_export_fields(plan)  # pylint: disable=protected-access

    assert semantic_fields == ["price.close"]


def test_node_ir_exit_and_timer_keeps_non_timer_condition():
    close = np.concatenate([np.full(10, 10.0), np.full(10, 30.0)])
    ex = _executor(_base_frame(close))
    plan = {
        "field_catalog": {},
        "feature_dag": {},
        "node_ir": {
            "entry": {"op": "gt", "left": {"field_ref": {"field": "price.close"}}, "right": 0},
            "exit": {
                "op": "and",
                "nodes": [
                    {"op": "timer_bars", "value": 3},
                    {"op": "gt", "left": {"field_ref": {"field": "price.close"}}, "right": 20},
                ],
            },
        },
    }
    _, exit_signal, timer_bars, timer_requires_exit, timer_mode = ex._evaluate_signals_for_combo(  # pylint: disable=protected-access
        execution_plan=plan,
        feature_contract={},
        combo={},
    )
    assert timer_bars == 3
    assert timer_requires_exit is True
    assert timer_mode == "and"
    assert np.count_nonzero(exit_signal[:10] != 0.0) == 0
    assert np.count_nonzero(exit_signal[10:] == -1.0) == 10


def test_node_ir_timer_requires_exit_signal_controls_close_behavior():
    close = np.linspace(1.0, 20.0, 20)
    ex = _executor(_base_frame(close))
    entry_signal = np.where(close > 0, 1.0, 0.0).astype(np.float64)
    exit_none = np.zeros_like(entry_signal)

    # timer only: should close eventually
    result_timer_only = ex._simulate_single_strategy(  # pylint: disable=protected-access
        combo_idx=0,
        combo={},
        entry_signal=entry_signal,
        exit_signal=exit_none,
        timer_bars=3,
        timer_requires_exit_signal=False,
        timer_combine_mode="timer_only",
        trading_params={"transaction_cost": 0.0, "slippage": 0.0, "trade_delay": 0, "trade_price": "close"},
        predictor_column="X",
        symbol="X",
        backtest_id_prefix="t",
    )
    actions_timer_only = result_timer_only["records"]["Trade_action"].to_numpy()
    assert np.count_nonzero(actions_timer_only == 4) > 0

    # timer + condition required, but condition never true: should not close by timer alone
    result_timer_and_cond = ex._simulate_single_strategy(  # pylint: disable=protected-access
        combo_idx=1,
        combo={},
        entry_signal=entry_signal,
        exit_signal=exit_none,
        timer_bars=3,
        timer_requires_exit_signal=True,
        timer_combine_mode="and",
        trading_params={"transaction_cost": 0.0, "slippage": 0.0, "trade_delay": 0, "trade_price": "close"},
        predictor_column="X",
        symbol="X",
        backtest_id_prefix="t",
    )
    actions_timer_and_cond = result_timer_and_cond["records"]["Trade_action"].to_numpy()
    assert np.count_nonzero(actions_timer_and_cond == 4) == 0


def test_node_ir_exit_or_timer_mode():
    close = np.linspace(1.0, 20.0, 20)
    ex = _executor(_base_frame(close))
    plan = {
        "field_catalog": {},
        "feature_dag": {},
        "node_ir": {
            "entry": {"op": "gt", "left": {"field_ref": {"field": "price.close"}}, "right": 0},
            "exit": {
                "op": "or",
                "nodes": [
                    {"op": "timer_bars", "value": 3},
                    {"op": "gt", "left": {"field_ref": {"field": "price.close"}}, "right": 999},
                ],
            },
        },
    }
    entry_signal, exit_signal, timer_bars, timer_requires_exit, timer_mode = ex._evaluate_signals_for_combo(  # pylint: disable=protected-access
        execution_plan=plan,
        feature_contract={},
        combo={},
    )
    assert timer_bars == 3
    assert timer_requires_exit is False
    assert timer_mode == "or"
    # condition branch never true, so exit signal array should remain zero and timer should still close.
    assert np.count_nonzero(exit_signal != 0.0) == 0
    result = ex._simulate_single_strategy(  # pylint: disable=protected-access
        combo_idx=2,
        combo={},
        entry_signal=entry_signal,
        exit_signal=exit_signal,
        timer_bars=timer_bars,
        timer_requires_exit_signal=timer_requires_exit,
        timer_combine_mode=timer_mode,
        trading_params={"transaction_cost": 0.0, "slippage": 0.0, "trade_delay": 0, "trade_price": "close"},
        predictor_column="X",
        symbol="X",
        backtest_id_prefix="t",
    )
    actions = result["records"]["Trade_action"].to_numpy()
    assert np.count_nonzero(actions == 4) > 0


def test_node_ir_run_from_paths_supports_backend_switch(tmp_path):
    close = np.linspace(1.0, 60.0, 60)
    data = _base_frame(close)
    ex = _executor(data)

    strategy_path = tmp_path / "strategy.json"
    strategy_payload = {
        "parameter_domains": {
            "period": {"type": "set", "values": [5, 10]},
        }
    }
    strategy_path.write_text(json.dumps(strategy_payload), encoding="utf-8")

    feature_path = tmp_path / "feature.json"
    feature_payload = {"features": []}
    feature_path.write_text(json.dumps(feature_payload), encoding="utf-8")

    plan = {
        "field_catalog": {},
        "feature_dag": {
            "feat_ma": {
                "feature": "ta.sma",
                "source": "price.close",
                "params": {"period": {"param_ref": "period"}},
            }
        },
        "node_ir": {
            "entry": {
                "op": "gt",
                "left": {"field_ref": {"field": "price.close"}},
                "right": {"feature_ref": {"feature_key": "feat_ma"}},
            },
            "exit": {
                "op": "lt",
                "left": {"field_ref": {"field": "price.close"}},
                "right": {"feature_ref": {"feature_key": "feat_ma"}},
            },
        },
    }

    results_python = ex.run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={"execution_backend": "python_numba"},
        predictor_column="X",
        symbol="TEST",
        backtest_id_prefix="b",
    )
    assert len(results_python) == 2

    results_rust_stub = ex.run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={"execution_backend": "rust_kernel"},
        predictor_column="X",
        symbol="TEST",
        backtest_id_prefix="b",
    )
    assert len(results_rust_stub) == 2


def test_node_ir_rust_backend_supports_timer_stateful_exit(tmp_path):
    close = np.linspace(1.0, 40.0, 40)
    data = _base_frame(close)
    ex = _executor(data)

    strategy_path = tmp_path / "strategy_timer.json"
    strategy_path.write_text(json.dumps({"parameter_domains": {}}), encoding="utf-8")
    feature_path = tmp_path / "feature_timer.json"
    feature_path.write_text(json.dumps({"features": []}), encoding="utf-8")

    plan = {
        "field_catalog": {},
        "feature_dag": {},
        "node_ir": {
            "entry": {
                "op": "gt",
                "left": {"field_ref": {"field": "price.close"}},
                "right": 0,
            },
            "exit": {"op": "timer_bars", "value": 3},
        },
    }

    results = ex.run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={"execution_backend": "rust_kernel", "transaction_cost": 0.0, "slippage": 0.0, "trade_delay": 0},
        predictor_column="X",
        symbol="TEST",
        backtest_id_prefix="timer",
    )
    assert len(results) == 1
    params = results[0].get("params", {})
    assert params.get("execution_backend") == "rust_kernel"
    assert params.get("timer_bars") == 3
    actions = results[0]["records"]["Trade_action"].to_numpy()
    assert np.count_nonzero(actions == 4) > 0


def test_node_ir_auto_backend_can_force_python(tmp_path):
    close = np.linspace(1.0, 60.0, 60)
    ex = _executor(_base_frame(close))
    strategy_path = tmp_path / "strategy_auto_py.json"
    strategy_path.write_text(json.dumps({"parameter_domains": {"period": {"type": "set", "values": [5, 10]}}}), encoding="utf-8")
    feature_path = tmp_path / "feature_auto_py.json"
    feature_path.write_text(json.dumps({"features": []}), encoding="utf-8")
    plan = {
        "field_catalog": {},
        "feature_dag": {"feat_ma": {"feature": "ta.sma", "source": "price.close", "params": {"period": {"param_ref": "period"}}}},
        "node_ir": {
            "entry": {"op": "gt", "left": {"field_ref": {"field": "price.close"}}, "right": {"feature_ref": {"feature_key": "feat_ma"}}},
            "exit": {"op": "lt", "left": {"field_ref": {"field": "price.close"}}, "right": {"feature_ref": {"feature_key": "feat_ma"}}},
        },
    }
    results = ex.run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={"execution_backend": "auto", "auto_rust_min_combos": 9999},
        predictor_column="X",
        symbol="TEST",
        backtest_id_prefix="auto",
    )
    assert len(results) == 2
    assert all(r.get("params", {}).get("execution_backend") == "python_numba" for r in results)


def test_node_ir_auto_backend_can_force_rust(tmp_path):
    close = np.linspace(1.0, 60.0, 60)
    ex = _executor(_base_frame(close))
    strategy_path = tmp_path / "strategy_auto_rust.json"
    strategy_path.write_text(json.dumps({"parameter_domains": {"period": {"type": "set", "values": [5, 10]}}}), encoding="utf-8")
    feature_path = tmp_path / "feature_auto_rust.json"
    feature_path.write_text(json.dumps({"features": []}), encoding="utf-8")
    plan = {
        "field_catalog": {},
        "feature_dag": {"feat_ma": {"feature": "ta.sma", "source": "price.close", "params": {"period": {"param_ref": "period"}}}},
        "node_ir": {
            "entry": {"op": "gt", "left": {"field_ref": {"field": "price.close"}}, "right": {"feature_ref": {"feature_key": "feat_ma"}}},
            "exit": {"op": "lt", "left": {"field_ref": {"field": "price.close"}}, "right": {"feature_ref": {"feature_key": "feat_ma"}}},
        },
    }
    results = ex.run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={"execution_backend": "auto", "auto_rust_min_combos": 1},
        predictor_column="X",
        symbol="TEST",
        backtest_id_prefix="auto",
    )
    assert len(results) == 2
    assert all(r.get("params", {}).get("execution_backend") == "rust_kernel" for r in results)
    assert all(r.get("params", {}).get("signal_kernel_backend") in {"rust", "numpy"} for r in results)


def test_node_ir_auto_default_prefers_rust_when_available(tmp_path):
    close = np.linspace(1.0, 60.0, 60)
    ex = _executor(_base_frame(close))
    strategy_path = tmp_path / "strategy_auto_default.json"
    strategy_path.write_text(json.dumps({"parameter_domains": {"period": {"type": "set", "values": [5, 10]}}}), encoding="utf-8")
    feature_path = tmp_path / "feature_auto_default.json"
    feature_path.write_text(json.dumps({"features": []}), encoding="utf-8")
    plan = {
        "field_catalog": {},
        "feature_dag": {"feat_ma": {"feature": "ta.sma", "source": "price.close", "params": {"period": {"param_ref": "period"}}}},
        "node_ir": {
            "entry": {"op": "gt", "left": {"field_ref": {"field": "price.close"}}, "right": {"feature_ref": {"feature_key": "feat_ma"}}},
            "exit": {"op": "lt", "left": {"field_ref": {"field": "price.close"}}, "right": {"feature_ref": {"feature_key": "feat_ma"}}},
        },
    }
    results = ex.run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={"execution_backend": "auto"},
        predictor_column="X",
        symbol="TEST",
        backtest_id_prefix="auto",
    )
    assert len(results) == 2
    assert all(r.get("params", {}).get("execution_backend") in {"rust_kernel", "python_numba"} for r in results)


def test_node_ir_auto_forces_python_on_timer_stateful_exit(tmp_path):
    close = np.linspace(1.0, 80.0, 80)
    ex = _executor(_base_frame(close))
    strategy_path = tmp_path / "strategy_auto_timer.json"
    strategy_path.write_text(
        json.dumps({"parameter_domains": {"hold_days": {"type": "set", "values": [5, 10]}}}),
        encoding="utf-8",
    )
    feature_path = tmp_path / "feature_auto_timer.json"
    feature_path.write_text(json.dumps({"features": []}), encoding="utf-8")
    plan = {
        "field_catalog": {},
        "feature_dag": {},
        "stateful_flags": {"requires_sequential_exit_state": True, "has_timer_bars": True},
        "node_ir": {
            "entry": {"op": "gt", "left": {"field_ref": {"field": "price.close"}}, "right": 0},
            "exit": {"op": "timer_bars", "value": {"param_ref": "hold_days"}},
        },
    }
    results = ex.run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={"execution_backend": "auto", "auto_rust_min_combos": 1},
        predictor_column="X",
        symbol="TEST",
        backtest_id_prefix="auto_timer",
    )
    assert len(results) == 2
    assert all(r.get("params", {}).get("execution_backend") == "python_numba" for r in results)


def test_node_ir_auto_forces_python_when_reset_timer_on_reentry_signal_enabled(tmp_path):
    close = np.linspace(1.0, 80.0, 80)
    ex = _executor(_base_frame(close))
    strategy_path = tmp_path / "strategy_auto_reentry.json"
    strategy_path.write_text(
        json.dumps({"parameter_domains": {"period": {"type": "set", "values": [5, 10]}}}),
        encoding="utf-8",
    )
    feature_path = tmp_path / "feature_auto_reentry.json"
    feature_path.write_text(json.dumps({"features": []}), encoding="utf-8")
    plan = {
        "field_catalog": {},
        "feature_dag": {
            "feat_ma": {
                "feature": "ta.sma",
                "source": "price.close",
                "params": {"period": {"param_ref": "period"}},
            }
        },
        "node_ir": {
            "entry": {
                "op": "gt",
                "left": {"field_ref": {"field": "price.close"}},
                "right": {"feature_ref": {"feature_key": "feat_ma"}},
            },
            "exit": {
                "op": "lt",
                "left": {"field_ref": {"field": "price.close"}},
                "right": {"feature_ref": {"feature_key": "feat_ma"}},
            },
        },
    }
    results = ex.run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={
            "execution_backend": "auto",
            "auto_rust_min_combos": 1,
            "reset_timer_on_reentry_signal": True,
        },
        predictor_column="X",
        symbol="TEST",
        backtest_id_prefix="auto_reentry",
    )
    assert len(results) == 2
    assert all(r.get("params", {}).get("execution_backend") == "python_numba" for r in results)


def test_node_ir_missing_field_raises_key_error():
    close = np.linspace(1.0, 30.0, 30)
    ex = _executor(_base_frame(close))
    plan = {
        "field_catalog": {},
        "feature_dag": {},
        "node_ir": {
            "entry": {
                "op": "gt",
                "left": {"field_ref": {"field": "feature.nonexistent.close"}},
                "right": 0,
            },
            "exit": {"op": "lt", "left": {"field_ref": {"field": "price.close"}}, "right": 0},
        },
    }
    with pytest.raises(KeyError):
        ex._evaluate_signals_for_combo(  # pylint: disable=protected-access
            execution_plan=plan,
            feature_contract={},
            combo={},
        )


def test_node_ir_nan_input_does_not_create_spurious_entry_signal():
    close = np.linspace(1.0, 60.0, 60)
    close[20:30] = np.nan
    ex = _executor(_base_frame(close))
    plan = {
        "field_catalog": {},
        "feature_dag": {
            "feat_ma10": {
                "feature": "ta.sma",
                "source": "price.close",
                "params": {"period": 10},
            }
        },
        "node_ir": {
            "entry": {
                "op": "gt",
                "left": {"field_ref": {"field": "price.close"}},
                "right": {"feature_ref": {"feature_key": "feat_ma10"}},
            },
            "exit": {"op": "lt", "left": {"field_ref": {"field": "price.close"}}, "right": 0},
        },
    }
    entry, _, _, _, _ = ex._evaluate_signals_for_combo(  # pylint: disable=protected-access
        execution_plan=plan,
        feature_contract={},
        combo={},
    )
    # NaN segment should not become True due to implicit casts.
    assert np.count_nonzero(entry[20:30] != 0.0) == 0


def test_node_ir_python_and_rust_backends_parity_on_equity_and_actions(tmp_path):
    close = np.concatenate([np.linspace(100.0, 130.0, 60), np.linspace(130.0, 110.0, 60)])
    ex = _executor(_base_frame(close))
    strategy_path = tmp_path / "strategy_parity.json"
    strategy_path.write_text(
        json.dumps({"parameter_domains": {"period": {"type": "set", "values": [5, 10]}}}),
        encoding="utf-8",
    )
    feature_path = tmp_path / "feature_parity.json"
    feature_path.write_text(json.dumps({"features": []}), encoding="utf-8")
    plan = {
        "field_catalog": {},
        "feature_dag": {
            "feat_ma": {
                "feature": "ta.sma",
                "source": "price.close",
                "params": {"period": {"param_ref": "period"}},
            }
        },
        "node_ir": {
            "entry": {
                "op": "cross_up",
                "left": {"field_ref": {"field": "price.close"}},
                "right": {"feature_ref": {"feature_key": "feat_ma"}},
            },
            "exit": {
                "op": "cross_down",
                "left": {"field_ref": {"field": "price.close"}},
                "right": {"feature_ref": {"feature_key": "feat_ma"}},
            },
        },
    }
    base_tp = {"transaction_cost": 0.0, "slippage": 0.0, "trade_delay": 0, "trade_price": "close"}
    py_results = ex.run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={**base_tp, "execution_backend": "python_numba"},
        predictor_column="X",
        symbol="TEST",
        backtest_id_prefix="parity",
    )
    rust_results = ex.run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={**base_tp, "execution_backend": "rust_kernel"},
        predictor_column="X",
        symbol="TEST",
        backtest_id_prefix="parity",
    )
    assert len(py_results) == len(rust_results)
    for py_item, rust_item in zip(py_results, rust_results):
        py_records = py_item["records"]
        rust_records = rust_item["records"]
        py_actions = py_records["Trade_action"].to_numpy(dtype=np.int64, copy=False)
        rust_actions = rust_records["Trade_action"].to_numpy(dtype=np.int64, copy=False)
        assert np.array_equal(py_actions, rust_actions)
        py_equity = py_records["Equity_value"].to_numpy(dtype=np.float64, copy=False)
        rust_equity = rust_records["Equity_value"].to_numpy(dtype=np.float64, copy=False)
        assert py_equity.shape == rust_equity.shape
        assert np.allclose(py_equity, rust_equity, atol=1e-10, rtol=1e-10)


def test_node_ir_python_and_rust_backends_parity_with_open_trade_delay(tmp_path):
    close = np.concatenate([np.linspace(100.0, 130.0, 60), np.linspace(130.0, 110.0, 60)])
    open_ = close + np.sin(np.linspace(0.0, 8.0, len(close))) * 2.5
    data = _base_frame(close)
    data["Open"] = open_
    ex = _executor(data)
    strategy_path = tmp_path / "strategy_parity_open_delay.json"
    strategy_path.write_text(
        json.dumps({"parameter_domains": {"period": {"type": "set", "values": [5, 10]}}}),
        encoding="utf-8",
    )
    feature_path = tmp_path / "feature_parity_open_delay.json"
    feature_path.write_text(json.dumps({"features": []}), encoding="utf-8")
    plan = {
        "field_catalog": {},
        "feature_dag": {
            "feat_ma": {
                "feature": "ta.sma",
                "source": "price.close",
                "params": {"period": {"param_ref": "period"}},
            }
        },
        "node_ir": {
            "entry": {
                "op": "cross_up",
                "left": {"field_ref": {"field": "price.close"}},
                "right": {"feature_ref": {"feature_key": "feat_ma"}},
            },
            "exit": {
                "op": "cross_down",
                "left": {"field_ref": {"field": "price.close"}},
                "right": {"feature_ref": {"feature_key": "feat_ma"}},
            },
        },
    }
    base_tp = {
        "transaction_cost": 0.001,
        "slippage": 0.0005,
        "trade_delay": 1,
        "trade_price": "open",
    }
    py_results = ex.run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={**base_tp, "execution_backend": "python_numba"},
        predictor_column="X",
        symbol="TEST",
        backtest_id_prefix="parity",
    )
    rust_results = ex.run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={**base_tp, "execution_backend": "rust_kernel"},
        predictor_column="X",
        symbol="TEST",
        backtest_id_prefix="parity",
    )
    assert len(py_results) == len(rust_results)
    for py_item, rust_item in zip(py_results, rust_results):
        py_records = py_item["records"]
        rust_records = rust_item["records"]
        py_actions = py_records["Trade_action"].to_numpy(dtype=np.int64, copy=False)
        rust_actions = rust_records["Trade_action"].to_numpy(dtype=np.int64, copy=False)
        assert np.array_equal(py_actions, rust_actions)
        py_equity = py_records["Equity_value"].to_numpy(dtype=np.float64, copy=False)
        rust_equity = rust_records["Equity_value"].to_numpy(dtype=np.float64, copy=False)
        assert py_equity.shape == rust_equity.shape
        assert np.allclose(py_equity, rust_equity, atol=1e-10, rtol=1e-10)


def test_node_ir_feature_contract_applies_fill_and_lag_policy():
    close = np.linspace(1.0, 6.0, 6)
    data = _base_frame(close)
    data["vix"] = [np.nan, 20.0, 21.0, np.nan, 23.0, 24.0]
    ex = _executor(data)
    feature_contract = {
        "features": [
            {
                "field": "feature.vix.close",
                "source": {"column": "vix"},
                "fill_policy": "ffill",
                "lag_bars": 1,
            }
        ]
    }
    series = ex._resolve_field_series(  # pylint: disable=protected-access
        "feature.vix.close",
        feature_contract=feature_contract,
        resolver_cache={},
    )
    # ffill first -> [nan,20,21,21,23,24], then lag1 -> [nan,nan,20,21,21,23]
    assert np.isnan(series.iloc[0])
    assert np.isnan(series.iloc[1])
    assert float(series.iloc[2]) == 20.0
    assert float(series.iloc[3]) == 21.0
    assert float(series.iloc[4]) == 21.0
    assert float(series.iloc[5]) == 23.0


def test_node_ir_feature_contract_none_fill_keeps_missing_values():
    close = np.linspace(1.0, 5.0, 5)
    data = _base_frame(close)
    data["vix"] = [10.0, np.nan, 12.0, np.nan, 14.0]
    ex = _executor(data)
    feature_contract = {
        "features": [
            {
                "field": "feature.vix.close",
                "source": {"column": "vix"},
                "fill_policy": "none",
                "lag_bars": 0,
            }
        ]
    }
    series = ex._resolve_field_series(  # pylint: disable=protected-access
        "feature.vix.close",
        feature_contract=feature_contract,
        resolver_cache={},
    )
    assert np.isnan(series.iloc[1])
    assert np.isnan(series.iloc[3])


def test_node_ir_reentry_signal_can_reset_timer_count():
    close = np.linspace(1.0, 12.0, 12)
    ex = _executor(_base_frame(close))
    entry_signal = np.zeros(12, dtype=np.float64)
    entry_signal[[0, 2, 4, 6, 8]] = 1.0
    exit_signal = np.zeros(12, dtype=np.float64)
    result = ex._simulate_single_strategy(  # pylint: disable=protected-access
        combo_idx=0,
        combo={},
        entry_signal=entry_signal,
        exit_signal=exit_signal,
        timer_bars=3,
        timer_requires_exit_signal=False,
        timer_combine_mode="timer_only",
        trading_params={
            "transaction_cost": 0.0,
            "slippage": 0.0,
            "trade_delay": 0,
            "trade_price": "close",
            "reset_timer_on_reentry_signal": True,
        },
        predictor_column="X",
        symbol="X",
        backtest_id_prefix="reentry",
    )
    actions = result["records"]["Trade_action"].to_numpy()
    close_actions = int(np.count_nonzero(actions == 4))
    assert close_actions <= 1
