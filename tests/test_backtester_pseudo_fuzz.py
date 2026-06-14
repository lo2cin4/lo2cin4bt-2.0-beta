from __future__ import annotations

import json
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

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "backtester"


def _frame(close: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close})


def _simulate(close: np.ndarray, entry_signal: np.ndarray, exit_signal: np.ndarray, **trading_params):
    simulator = TradeSimulator_backtester(
        data=_frame(close),
        entry_signal=pd.Series(entry_signal),
        exit_signal=pd.Series(exit_signal),
        transaction_cost=float(trading_params.get("transaction_cost", 0.0)),
        slippage=float(trading_params.get("slippage", 0.0)),
        trade_delay=int(trading_params.get("trade_delay", 0)),
        trade_price=str(trading_params.get("trade_price", "close")),
        Backtest_id="pseudo_fuzz",
        predictor="X",
        trading_instrument="TEST",
    )
    return simulator.simulate_trades_sequential(
        entry_signal=entry_signal.astype(np.float64),
        exit_signal=exit_signal.astype(np.float64),
        trading_params={
            "transaction_cost": float(trading_params.get("transaction_cost", 0.0)),
            "slippage": float(trading_params.get("slippage", 0.0)),
            "trade_delay": int(trading_params.get("trade_delay", 0)),
            "trade_price": str(trading_params.get("trade_price", "close")),
            **({"nday_rules": trading_params["nday_rules"]} if "nday_rules" in trading_params else {}),
        },
    )


def test_pseudo_fuzz_higher_fees_never_improve_final_equity() -> None:
    rng = np.random.default_rng(20260412)
    for _ in range(30):
        close = np.cumprod(1.0 + rng.normal(0.001, 0.01, 80)) * 100.0
        entry_signal = np.zeros(80, dtype=np.float64)
        exit_signal = np.zeros(80, dtype=np.float64)
        entry_candidates = np.sort(rng.choice(np.arange(0, 70), size=4, replace=False))
        hold = int(rng.integers(2, 6))
        for idx in entry_candidates:
            entry_signal[idx] = 1.0
            exit_idx = min(idx + hold, 79)
            exit_signal[exit_idx] = -1.0

        low_fee = _simulate(close, entry_signal, exit_signal, transaction_cost=0.0, slippage=0.0)
        high_fee = _simulate(close, entry_signal, exit_signal, transaction_cost=0.003, slippage=0.002)
        assert high_fee["equity_values"][-1] <= low_fee["equity_values"][-1] + 1e-9


def test_pseudo_fuzz_scaling_price_level_keeps_percentage_equity_path() -> None:
    rng = np.random.default_rng(20260413)
    for _ in range(25):
        close = np.cumprod(1.0 + rng.normal(0.001, 0.008, 60)) * 100.0
        entry_signal = np.zeros(60, dtype=np.float64)
        exit_signal = np.zeros(60, dtype=np.float64)
        entry_signal[5] = 1.0
        exit_signal[20] = -1.0
        entry_signal[30] = -1.0
        exit_signal[45] = 1.0

        base = _simulate(close, entry_signal, exit_signal)
        scaled = _simulate(close * 10.0, entry_signal, exit_signal)
        assert np.allclose(base["equity_values"], scaled["equity_values"], atol=1e-9, rtol=1e-9)
        assert np.array_equal(base["trade_actions"], scaled["trade_actions"])


def test_pseudo_fuzz_trade_delay_never_moves_first_entry_earlier() -> None:
    rng = np.random.default_rng(20260414)
    for _ in range(30):
        close = np.cumprod(1.0 + rng.normal(0.001, 0.01, 50)) * 100.0
        entry_signal = np.zeros(50, dtype=np.float64)
        exit_signal = np.zeros(50, dtype=np.float64)
        candidate = int(rng.integers(0, 40))
        entry_signal[candidate] = 1.0
        exit_signal[min(candidate + 5, 49)] = -1.0

        delay0 = _simulate(close, entry_signal, exit_signal, trade_delay=0)
        delay2 = _simulate(close, entry_signal, exit_signal, trade_delay=2)
        first0 = int(np.where(delay0["trade_actions"] == 1.0)[0][0])
        first2 = int(np.where(delay2["trade_actions"] == 1.0)[0][0])
        assert first2 >= first0


def test_pseudo_fuzz_timer_extension_never_exits_earlier() -> None:
    close = np.linspace(100.0, 150.0, 20)
    entry_signal = np.zeros(20, dtype=np.float64)
    exit_signal = np.zeros(20, dtype=np.float64)
    entry_signal[1] = 1.0

    hold2 = _simulate(
        close,
        entry_signal,
        exit_signal,
        nday_rules={"exit_long_days": 2, "exit_short_days": 0, "has_non_nday_exit": False, "combine_mode": "timer_only"},
    )
    hold4 = _simulate(
        close,
        entry_signal,
        exit_signal,
        nday_rules={"exit_long_days": 4, "exit_short_days": 0, "has_non_nday_exit": False, "combine_mode": "timer_only"},
    )
    exit2 = int(np.where(hold2["trade_actions"] == 4.0)[0][0])
    exit4 = int(np.where(hold4["trade_actions"] == 4.0)[0][0])
    assert exit4 >= exit2


def test_golden_regression_oracle_long_trade_fixture() -> None:
    fixture = json.loads((_FIXTURE_DIR / "oracle_long_trade_v1.json").read_text(encoding="utf-8-sig"))
    close = np.asarray(fixture["close"], dtype=np.float64)
    entry_signal = np.asarray(fixture["entry_signal"], dtype=np.float64)
    exit_signal = np.asarray(fixture["exit_signal"], dtype=np.float64)
    result = _simulate(close, entry_signal, exit_signal, **fixture["trading_params"])

    expected = fixture["expected"]
    assert result["positions"].tolist() == expected["positions"]
    assert result["trade_actions"].tolist() == expected["trade_actions"]
    assert result["equity_values"].tolist() == pytest.approx(expected["equity_values"])
    assert result["returns"].tolist() == pytest.approx(expected["returns"])
