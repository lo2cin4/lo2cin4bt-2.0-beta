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


def test_sequential_equity_carries_after_close_and_into_next_trade() -> None:
    data = pd.DataFrame(
        {
            "Open": [100.0, 102.0, 110.0, 108.0, 105.0, 115.0],
            "High": [101.0, 103.0, 111.0, 109.0, 106.0, 116.0],
            "Low": [99.0, 101.0, 109.0, 107.0, 104.0, 114.0],
            "Close": [100.0, 104.0, 110.0, 108.0, 105.0, 115.0],
        }
    )
    entry_signal = np.array([1.0, 0.0, 0.0, 0.0, 1.0, 0.0], dtype=np.float64)
    exit_signal = np.array([0.0, 0.0, -1.0, 0.0, 0.0, -1.0], dtype=np.float64)

    simulator = TradeSimulator_backtester(
        data=data,
        entry_signal=pd.Series(entry_signal),
        exit_signal=pd.Series(exit_signal),
        transaction_cost=0.0,
        slippage=0.0,
        trade_delay=0,
        trade_price="close",
        Backtest_id="equity_carry",
        predictor="X",
        trading_instrument="SPY",
    )

    result = simulator.simulate_trades_sequential(
        entry_signal=entry_signal,
        exit_signal=exit_signal,
        trading_params={
            "transaction_cost": 0.0,
            "slippage": 0.0,
            "trade_delay": 0,
            "trade_price": "close",
        },
    )

    equity = result["equity_values"]

    # First trade: 100 -> 110, flat period should keep the realized equity.
    assert equity[2] == pytest.approx(110.0)
    assert equity[3] == pytest.approx(110.0)

    # Second trade starts from carried equity and ends higher, not reset to 100.
    assert equity[4] == pytest.approx(110.0)
    assert equity[5] > 110.0


def test_sequential_reentry_timer_reset_defers_nday_close() -> None:
    data = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            "High": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            "Low": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            "Close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
        }
    )
    entry_signal = np.array([1.0, 0.0, 1.0, 0.0, 0.0, 0.0], dtype=np.float64)
    exit_signal = np.zeros_like(entry_signal)

    simulator = TradeSimulator_backtester(
        data=data,
        entry_signal=pd.Series(entry_signal),
        exit_signal=pd.Series(exit_signal),
        transaction_cost=0.0,
        slippage=0.0,
        trade_delay=0,
        trade_price="close",
        Backtest_id="timer_reset",
        predictor="X",
        trading_instrument="SPY",
    )

    result = simulator.simulate_trades_sequential(
        entry_signal=entry_signal,
        exit_signal=exit_signal,
        trading_params={
            "transaction_cost": 0.0,
            "slippage": 0.0,
            "trade_delay": 0,
            "trade_price": "close",
            "reset_timer_on_reentry_signal": True,
            "nday_rules": {
                "exit_long_days": 2,
                "exit_short_days": 0,
                "has_non_nday_exit": False,
            },
        },
    )

    assert result["trade_actions"].tolist() == [1.0, 0.0, 0.0, 0.0, 4.0, 0.0]


def test_result_matrix_invariant_rejects_exit_while_flat() -> None:
    simulator = TradeSimulator_backtester(
        data=pd.DataFrame({"Open": [1.0, 1.0], "High": [1.0, 1.0], "Low": [1.0, 1.0], "Close": [1.0, 1.0]}),
        entry_signal=pd.Series([0.0, 0.0]),
        exit_signal=pd.Series([0.0, 0.0]),
    )

    with pytest.raises(RuntimeError, match="exit action must close an existing position into flat state"):
        simulator._assert_result_matrix_invariants(  # pylint: disable=protected-access
            np.array([0.0, 0.0], dtype=np.float64),
            np.array([4.0, 0.0], dtype=np.float64),
            np.array([100.0, 100.0], dtype=np.float64),
        )


def test_runtime_invariant_rejects_timer_before_fill() -> None:
    simulator = TradeSimulator_backtester(
        data=pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0]}),
        entry_signal=pd.Series([0.0]),
        exit_signal=pd.Series([0.0]),
    )

    with pytest.raises(RuntimeError, match="long timer cannot be ready before a long fill exists"):
        simulator._assert_runtime_state_invariants(  # pylint: disable=protected-access
            index=0,
            previous_state=0.0,
            current_state=0.0,
            trade_action=0.0,
            previous_open_price=0.0,
            open_price=0.0,
            open_equity=1.0,
            holding_period_count=0,
            long_timer_ready=True,
            short_timer_ready=False,
            reentry_reset_applied=False,
            equity=1.0,
            equity_value=100.0,
            current_return=0.0,
        )
