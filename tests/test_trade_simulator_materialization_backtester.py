from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backtester.NodeIRExecutor_backtester import NodeIRExecutorBacktester
from backtester.TradeSimulator_backtester import TradeSimulator_backtester


pytestmark = pytest.mark.regression


def _sample_data() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Time": pd.date_range("2024-01-01", periods=5, freq="D"),
            "Open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "High": [100.0, 101.0, 102.0, 103.0, 104.0],
            "Low": [100.0, 101.0, 102.0, 103.0, 104.0],
            "Close": [100.0, 101.0, 102.0, 103.0, 104.0],
            "feature.alpha": [10.0, 11.0, 12.0, 13.0, 14.0],
        }
    )


def test_generate_single_result_uses_deterministic_trade_group_ids_and_trade_seq() -> None:
    data = _sample_data()
    simulator = TradeSimulator_backtester(
        data=data,
        entry_signal=pd.Series([1.0, 0.0, 0.0, 0.0, 0.0]),
        exit_signal=pd.Series([0.0, 0.0, 0.0, -1.0, 0.0]),
        transaction_cost=0.0,
        slippage=0.0,
        trade_delay=0,
        trade_price="close",
        Backtest_id="bt_alpha",
        predictor="feature.alpha",
        trading_instrument="QQQ",
    )

    result = simulator.generate_single_result(
        task_idx=0,
        entry_signal=np.array([1.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float64),
        exit_signal=np.array([0.0, 0.0, 0.0, -1.0, 0.0], dtype=np.float64),
        position=np.array([1.0, 1.0, 1.0, 0.0, 0.0], dtype=np.float64),
        returns=np.array([0.0, 0.01, 0.00990099, 0.00980392, 0.0], dtype=np.float64),
        trade_actions=np.array([1.0, 0.0, 0.0, 4.0, 0.0], dtype=np.float64),
        equity_values=np.array([100.0, 101.0, 102.0, 103.0, 103.0], dtype=np.float64),
        predictor="feature.alpha",
        backtest_id="bt_alpha",
        entry_params=[],
        exit_params=[],
        trading_params={"trade_price": "close", "transaction_cost": 0.0, "slippage": 0.0},
        semantic_predictor_fields=None,
    )

    records = result["records"]
    assert list(records["Trade_seq"]) == [1, 1, 1, 1, 0]
    assert records.loc[0, "Trade_group_id"] == "bt_alpha:T1"
    assert records.loc[3, "Trade_group_id"] == "bt_alpha:T1"
    assert pd.isna(records.loc[4, "Trade_group_id"])


def test_generate_single_result_preserves_core_trade_fields_and_predictor_columns() -> None:
    data = _sample_data()
    simulator = TradeSimulator_backtester(
        data=data,
        entry_signal=pd.Series([1.0, 0.0, 0.0, 0.0, 0.0]),
        exit_signal=pd.Series([0.0, 0.0, 0.0, -1.0, 0.0]),
        transaction_cost=0.0,
        slippage=0.0,
        trade_delay=0,
        trade_price="close",
        Backtest_id="bt_beta",
        predictor="feature.alpha",
        trading_instrument="QQQ",
    )

    result = simulator.generate_single_result(
        task_idx=0,
        entry_signal=np.array([1.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float64),
        exit_signal=np.array([0.0, 0.0, 0.0, -1.0, 0.0], dtype=np.float64),
        position=np.array([1.0, 1.0, 1.0, 0.0, 0.0], dtype=np.float64),
        returns=np.array([0.0, 0.01, 0.00990099, 0.00980392, 0.0], dtype=np.float64),
        trade_actions=np.array([1.0, 0.0, 0.0, 4.0, 0.0], dtype=np.float64),
        equity_values=np.array([100.0, 101.0, 102.0, 103.0, 103.0], dtype=np.float64),
        predictor="feature.alpha",
        backtest_id="bt_beta",
        entry_params=[],
        exit_params=[],
        trading_params={"trade_price": "close", "transaction_cost": 0.0, "slippage": 0.0},
        semantic_predictor_fields=["feature.alpha"],
    )

    records = result["records"]
    assert records.loc[0, "Position_type"] == "new_long"
    assert records.loc[3, "Position_type"] == "close_long"
    assert records.loc[0, "Open_position_price"] == pytest.approx(100.0)
    assert records.loc[3, "Close_position_price"] == pytest.approx(103.0)
    assert records.loc[3, "Holding_period_count"] == 3
    assert records.loc[3, "Holding_period"] == pytest.approx(3.0)
    assert records.loc[3, "Trade_return"] == pytest.approx(0.03)
    assert records.loc[3, "Equity_value"] == pytest.approx(103.0)
    assert records.loc[2, "Predictor_1_name"] == "feature.alpha"
    assert records.loc[2, "Predictor_1_value"] == pytest.approx(12.0)
    assert records.loc[2, "Predictor_name"] == "feature.alpha"
    assert records.loc[2, "Predictor_value"] == pytest.approx(12.0)


def test_node_ir_semantic_result_metadata_still_present() -> None:
    executor = NodeIRExecutorBacktester(_sample_data())
    result = {"params": {}}
    audit_index = {
        "strategy_contract_path": "workspace/strategies/strategy-demo.user.json",
        "feature_contract_hash": "feat_hash",
        "execution_plan_hash": "plan_hash",
        "source_audit_id": "audit123",
    }

    executor._decorate_semantic_result(  # pylint: disable=protected-access
        result=result,
        combo={"entry_ma": 10, "exit_ma": 20},
        backtest_id_prefix="qqq_demo",
        semantic_predictor_fields=["feature.alpha"],
        audit_index=audit_index,
        symbol="QQQ",
        signal_kernel_backend="rust",
        timer_bars=5,
        timer_requires_exit_signal=False,
        timer_combine_mode="timer_only",
    )

    params = result["params"]
    assert params["strategy_mode"] == "semantic"
    assert params["semantic_combo"] == {"entry_ma": 10, "exit_ma": 20}
    assert params["semantic_fields"] == ["feature.alpha"]
    assert params["semantic_run_label"] == "qqq_demo"
    assert params["symbol"] == "QQQ"
    assert params["signal_kernel_backend"] == "rust"
    assert params["feature_contract_hash"] == "feat_hash"
    assert params["execution_plan_hash"] == "plan_hash"
    assert params["source_audit_id"] == "audit123"
    assert params["timer_bars"] == 5
