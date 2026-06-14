from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backtester.TradeSimulator_backtester import TradeSimulator_backtester


pytestmark = pytest.mark.regression


def _frame(close: list[float]) -> pd.DataFrame:
    prices = np.asarray(close, dtype=np.float64)
    return pd.DataFrame(
        {
            "Open": prices,
            "High": prices,
            "Low": prices,
            "Close": prices,
        }
    )


def _simulate(
    close: list[float],
    entry_signal: list[float],
    exit_signal: list[float],
    **trading_params,
):
    data = _frame(close)
    simulator = TradeSimulator_backtester(
        data=data,
        entry_signal=pd.Series(entry_signal, dtype=np.float64),
        exit_signal=pd.Series(exit_signal, dtype=np.float64),
        transaction_cost=float(trading_params.get("transaction_cost", 0.0)),
        slippage=float(trading_params.get("slippage", 0.0)),
        trade_delay=int(trading_params.get("trade_delay", 0)),
        trade_price=str(trading_params.get("trade_price", "close")),
        Backtest_id="oracle",
        predictor="X",
        trading_instrument="TEST",
    )
    result = simulator.simulate_trades_sequential(
        entry_signal=np.asarray(entry_signal, dtype=np.float64),
        exit_signal=np.asarray(exit_signal, dtype=np.float64),
        trading_params={
            "transaction_cost": float(trading_params.get("transaction_cost", 0.0)),
            "slippage": float(trading_params.get("slippage", 0.0)),
            "trade_delay": int(trading_params.get("trade_delay", 0)),
            "trade_price": str(trading_params.get("trade_price", "close")),
            **({"nday_rules": trading_params["nday_rules"]} if "nday_rules" in trading_params else {}),
            **(
                {"reset_timer_on_reentry_signal": bool(trading_params["reset_timer_on_reentry_signal"])}
                if "reset_timer_on_reentry_signal" in trading_params
                else {}
            ),
        },
    )
    return simulator, result


def test_oracle_long_trade_exact_positions_actions_and_equity() -> None:
    _, result = _simulate(
        close=[100.0, 110.0, 120.0, 130.0],
        entry_signal=[1.0, 0.0, 0.0, 0.0],
        exit_signal=[0.0, 0.0, -1.0, 0.0],
    )

    assert result["positions"].tolist() == [1.0, 1.0, 0.0, 0.0]
    assert result["trade_actions"].tolist() == [1.0, 0.0, 4.0, 0.0]
    assert result["equity_values"].tolist() == pytest.approx([100.0, 110.0, 120.0, 120.0])
    assert result["returns"].tolist() == pytest.approx([0.0, 0.10, 0.09090909090909083, 0.0])


def test_oracle_short_trade_exact_positions_actions_and_equity() -> None:
    _, result = _simulate(
        close=[100.0, 90.0, 80.0, 70.0],
        entry_signal=[-1.0, 0.0, 0.0, 0.0],
        exit_signal=[0.0, 0.0, 1.0, 0.0],
    )

    assert result["positions"].tolist() == [-1.0, -1.0, 0.0, 0.0]
    assert result["trade_actions"].tolist() == [1.0, 0.0, 4.0, 0.0]
    assert result["equity_values"].tolist() == pytest.approx([100.0, 110.0, 120.0, 120.0])
    assert result["returns"].tolist() == pytest.approx([0.0, 0.10, 0.09090909090909083, 0.0])


def test_oracle_trade_delay_shifts_execution_bars_exactly() -> None:
    _, result = _simulate(
        close=[100.0, 110.0, 120.0, 130.0, 140.0],
        entry_signal=[1.0, 0.0, 0.0, 0.0, 0.0],
        exit_signal=[0.0, 0.0, -1.0, 0.0, 0.0],
        trade_delay=1,
    )

    assert result["positions"].tolist() == [0.0, 1.0, 1.0, 0.0, 0.0]
    assert result["trade_actions"].tolist() == [0.0, 1.0, 0.0, 4.0, 0.0]
    assert result["equity_values"].tolist() == pytest.approx(
        [100.0, 100.0, 109.0909090909, 118.1818181818, 118.1818181818]
    )


def test_oracle_entry_and_exit_costs_apply_exactly_once_each() -> None:
    transaction_cost = 0.01
    slippage = 0.02
    _, result = _simulate(
        close=[100.0, 110.0, 110.0],
        entry_signal=[1.0, 0.0, 0.0],
        exit_signal=[0.0, -1.0, 0.0],
        transaction_cost=transaction_cost,
        slippage=slippage,
    )

    cost_factor = (1.0 - transaction_cost) * (1.0 - slippage)
    expected_final_equity = 100.0 * cost_factor * 1.1 * cost_factor

    assert result["trade_actions"].tolist() == [1.0, 4.0, 0.0]
    assert result["equity_values"][0] == pytest.approx(100.0 * cost_factor)
    assert result["equity_values"][1] == pytest.approx(expected_final_equity)
    assert result["equity_values"][2] == pytest.approx(expected_final_equity)


def test_oracle_nday_timer_starts_after_fill_not_signal_bar() -> None:
    _, result = _simulate(
        close=[100.0, 101.0, 102.0, 103.0, 104.0],
        entry_signal=[0.0, 1.0, 0.0, 0.0, 0.0],
        exit_signal=[0.0, 0.0, 0.0, 0.0, 0.0],
        nday_rules={
            "exit_long_days": 2,
            "exit_short_days": 0,
            "has_non_nday_exit": False,
            "combine_mode": "timer_only",
        },
    )

    assert result["positions"].tolist() == [0.0, 1.0, 1.0, 0.0, 0.0]
    assert result["trade_actions"].tolist() == [0.0, 1.0, 0.0, 4.0, 0.0]
    assert result["equity_values"].tolist() == pytest.approx([100.0, 100.0, 100.9900990099, 101.9801980198, 101.9801980198])


def test_oracle_generate_single_result_matches_exact_trade_return_and_times() -> None:
    simulator, result = _simulate(
        close=[100.0, 110.0, 120.0],
        entry_signal=[1.0, 0.0, 0.0],
        exit_signal=[0.0, -1.0, 0.0],
    )
    single = simulator.generate_single_result(
        task_idx=0,
        entry_signal=np.asarray([1.0, 0.0, 0.0], dtype=np.float64),
        exit_signal=np.asarray([0.0, -1.0, 0.0], dtype=np.float64),
        position=result["positions"],
        returns=result["returns"],
        trade_actions=result["trade_actions"],
        equity_values=result["equity_values"],
        predictor="X",
        backtest_id="oracle_trade_record",
        entry_params={},
        exit_params={},
        trading_params={
            "transaction_cost": 0.0,
            "slippage": 0.0,
            "trade_delay": 0,
            "trade_price": "close",
        },
    )
    records = single["records"]

    open_row = records.iloc[0]
    close_row = records.iloc[1]

    assert int(open_row["Trade_action"]) == 1
    assert int(close_row["Trade_action"]) == 4
    assert float(close_row["Trade_return"]) == pytest.approx(0.10)
    assert pd.Timestamp(open_row["Time"]) == pd.Timestamp(close_row["Open_time"])
    assert pd.Timestamp(close_row["Time"]) == pd.Timestamp(close_row["Close_time"])
