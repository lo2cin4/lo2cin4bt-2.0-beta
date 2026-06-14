"""
TradeSimulator_backtester.py

【功能說明】
------------------------------------------------------------
本模組為 Lo2cin4BT 回測框架的交易模擬器，負責根據信號序列模擬持倉、平倉與資金變化。
**本模組是全系統唯一的持倉/平倉邏輯守門員，所有交易行為規則皆在此集中管控。**

【新增功能】
------------------------------------------------------------
- 向量化交易模擬：支援批量處理多個策略，大幅提升性能
- 統一接口：為 BacktestEngine 和 NodeIR/native runtime 提供統一的交易模擬接口
- 完整交易記錄：包含所有必要的交易信息欄位
- Numba JIT 編譯優化：使用 Numba 加速向量化計算
- 智能持倉管理：支援多種持倉狀態與交易邏輯

【流程與數據流】
------------------------------------------------------------
- 輸入：來自 NodeIR/native runtime 的信號序列
- 依據信號與當前持倉狀態，決定是否開倉/平倉
- 每次交易動作都會記錄於交易記錄（DataFrame）
- 交易模擬流程如下：

```mermaid
flowchart TD
    A[BacktestEngine/VBT] -->|產生信號| B(TradeSimulator)
    B -->|模擬交易| C{持倉狀態}
    C -- 無持倉且信號=1 --> D[開多倉]
    C -- 有多倉且信號=-1 --> E[平多倉]
    C -- 有空倉且信號=1 --> F[平空倉]
    C -- 其他情況 --> G[無動作]
    D & E & F & G --> H[記錄交易]
    H --> I[回傳交易記錄]
```

【主要方法】
------------------------------------------------------------
- simulate_trades(): 單個策略交易模擬（向後兼容）
- simulate_trades_vectorized(): 向量化交易模擬（供 VBT 調用）
- generate_single_result(): 生成完整交易記錄
- _vectorized_trade_simulation_njit(): Numba 加速的向量化交易邏輯

【維護與擴充重點】
------------------------------------------------------------
- **任何涉及交易邏輯的需求（如反手開倉、複合信號、停損/停利）都必須在本檔案集中設計與維護**
- 未來如需支援「反手開倉」等特殊行為，只需在本模組集中修改
- 若信號產生邏輯有變動（如允許2, -2等複合信號），需同步調整本模組的判斷邏輯
- **每次開發新 indicator 或信號型態時，務必檢查本模組的持倉/平倉判斷是否仍然正確**
- 任何涉及 position, signal 判斷的邏輯都在本檔案 for 迴圈內
- 若有新信號型態，需同步更新本檔案的判斷分支
- 若有交易記錄欄位變動，需同步更新 record 結構
- 向量化邏輯需要與單個策略邏輯保持一致

【常見易錯點】
------------------------------------------------------------
- 平倉信號在無持倉時出現，不會開空倉，只會記錄信號
- 同一根K線同時出現 signal==1 和 signal==-1（合併後為0），則不會有任何動作
- 交易延遲、交易成本、滑點等皆可自訂，需注意參數傳遞正確
- 向量化計算與單個策略計算結果不一致
- 持倉狀態管理錯誤導致交易邏輯異常

【錯誤處理】
------------------------------------------------------------
- 信號格式錯誤時提供詳細診斷
- 持倉狀態異常時提供修正建議
- 交易記錄錯誤時提供備用方案
- 向量化計算失敗時自動降級為單個策略計算

【範例：收到的信號與產生的交易記錄】
------------------------------------------------------------
- 輸入信號序列（pd.Series）：[0, 1, 0, 0, -1, 0, 1, -1, 0]
- 產生的交易記錄（DataFrame）：
    - 第2根K線 signal==1 → 開多倉
    - 第5根K線 signal==-1 → 平多倉
    - 第7根K線 signal==1 → 再次開多倉
    - 第8根K線 signal==-1 → 再次平多倉

【與其他模組的關聯】
------------------------------------------------------------
- 接受 BacktestEngine 和 NodeIR/native runtime 的信號
- 交易記錄會傳給 TradeRecorder_backtester.py 驗證與導出
- 若需擴充交易型態，需同步通知 TradeRecorder/Exporter
- 與 NodeIR/native runtime 共享向量化計算邏輯

【版本與變更記錄】
------------------------------------------------------------
- v1.0: 初始版本，支援基本開平倉邏輯
- v1.1: 新增交易記錄詳細欄位
- v1.2: 優化持倉狀態管理
- Version 2.0: 新增向量化交易模擬，統一接口
- Version 2.1: 整合 Numba JIT 編譯優化
- Version 2.2: 完善錯誤處理與邏輯驗證

【參考】
------------------------------------------------------------
- 詳細交易規則如有變動，請同步更新本註解與 README
- 其他模組如有依賴本模組的行為，請於對應模組頂部註解標明
- Numba 向量化計算最佳實踐
- 交易邏輯設計與驗證方法
"""

import logging
import os
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

# NOTE: translated to English.
from numba import njit

# NOTE: translated to English.
from backtester.IndicatorParams_backtester import IndicatorParams


# NOTE: translated to English.
@njit(fastmath=True, cache=True)
def _vectorized_trade_simulation_njit(  # pylint: disable=too-complex
    entry_signals: np.ndarray,
    exit_signals: np.ndarray,
    close_prices: np.ndarray,
    open_prices: np.ndarray,
    transaction_cost: float,
    slippage: float,
    trade_price: str = "open",
    trade_delay: int = 1,
    holding_period_days: int = 0,
) -> Dict[str, Any]:
    """
    向量化交易模擬 - 移植自 VBT
    """
    n_time, n_strategies = entry_signals.shape

    # NOTE: translated to English.
    positions = np.zeros((n_time, n_strategies))
    returns = np.zeros((n_time, n_strategies))
    trade_actions = np.zeros((n_time, n_strategies))
    equity_values = np.zeros((n_time, n_strategies))

    # NOTE: translated to English.
    for s in range(n_strategies):
        # NOTE: translated to English.
        current_state = 0.0  # NOTE: translated to English.
        equity = 1.0
        open_price = 0.0  # NOTE: translated to English.
        open_equity = 1.0  # NOTE: translated to English.
        holding_period_count = 0

        for t in range(n_time):
            # NOTE: translated to English.
            signal_index = t - trade_delay
            entry_sig = (
                entry_signals[signal_index, s] if 0 <= signal_index < n_time else 0.0
            )
            exit_sig = (
                exit_signals[signal_index, s] if 0 <= signal_index < n_time else 0.0
            )

            # NOTE: translated to English.
            if t > 0 and current_state != 0.0 and open_price > 0.0:
                if trade_price == "close":
                    current_close = close_prices[t]
                    if current_state == 1.0:  # NOTE: translated to English.
                        price_return = (current_close - open_price) / open_price
                    else:  # NOTE: translated to English.
                        price_return = (open_price - current_close) / open_price
                else:  # trade_price == 'open'
                    current_open = open_prices[t]
                    if current_state == 1.0:  # NOTE: translated to English.
                        price_return = (current_open - open_price) / open_price
                    else:  # NOTE: translated to English.
                        price_return = (open_price - current_open) / open_price

                # NOTE: translated to English.
                equity = open_equity * (1.0 + price_return)

                # NOTE: translated to English.
                if equity_values[t-1, s] > 0:
                    returns[t, s] = (equity * 100.0) / equity_values[t-1, s] - 1.0
                else:
                    returns[t, s] = 0.0
            else:
                returns[t, s] = 0.0

            # NOTE: translated to English.
            should_time_close = False
            if t > 0 and current_state != 0.0 and open_price > 0.0:
                holding_period_count += 1
                if holding_period_days > 0 and holding_period_count >= holding_period_days:
                    should_time_close = True
            if current_state == 0.0:  # NOTE: translated to English.
                if entry_sig == 1.0:  # NOTE: translated to English.
                    current_state = 1.0
                    trade_actions[t, s] = 1
                    # NOTE: translated to English.
                    if trade_price == "close":
                        open_price = close_prices[t]
                    else:
                        open_price = open_prices[t]
                    # NOTE: translated to English.
                    equity *= (1.0 - slippage) * (1.0 - transaction_cost)
                    holding_period_count = 0
                    open_equity = equity  # NOTE: translated to English.
                elif entry_sig == -1.0:  # NOTE: translated to English.
                    current_state = -1.0
                    trade_actions[t, s] = 1
                    # NOTE: translated to English.
                    if trade_price == "close":
                        open_price = close_prices[t]
                    else:
                        open_price = open_prices[t]
                    # NOTE: translated to English.
                    equity *= (1.0 - slippage) * (1.0 - transaction_cost)
                    holding_period_count = 0
                    open_equity = equity  # NOTE: translated to English.
            elif current_state == 1.0:  # NOTE: translated to English.
                if exit_sig == -1.0 or should_time_close:  # NOTE: translated to English.
                    current_state = 0.0
                    trade_actions[t, s] = 4
                    open_price = 0.0  # NOTE: translated to English.
                    open_equity = 1.0  # NOTE: translated to English.
                    # NOTE: translated to English.
                    equity *= (1.0 - slippage) * (1.0 - transaction_cost)
                    holding_period_count = 0
            elif current_state == -1.0:  # NOTE: translated to English.
                if exit_sig == 1.0 or should_time_close:  # NOTE: translated to English.
                    current_state = 0.0
                    trade_actions[t, s] = 4
                    open_price = 0.0  # NOTE: translated to English.
                    open_equity = 1.0  # NOTE: translated to English.
                    # NOTE: translated to English.
                    equity *= (1.0 - slippage) * (1.0 - transaction_cost)

                    holding_period_count = 0
            positions[t, s] = current_state
            equity_values[t, s] = equity * 100.0

    return {"positions": positions, "returns": returns, "trade_actions": trade_actions, "equity_values": equity_values}


@njit(cache=True)
def _build_trade_ledger_njit(
    position: np.ndarray,
    trade_actions: np.ndarray,
    current_prices: np.ndarray,
) -> Tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
]:
    """
    Build deterministic per-row trade ledger arrays from batch simulation output.

    Returns:
        trade_seq_arr,
        position_type_code_arr,
        open_price_arr,
        close_price_arr,
        open_idx_arr,
        close_idx_arr,
        holding_period_count_arr,
        holding_period_arr,
        trade_return_arr,
    """
    n_rows = len(position)
    trade_seq_arr = np.zeros(n_rows, dtype=np.int64)
    position_type_code_arr = np.zeros(n_rows, dtype=np.int8)
    open_price_arr = np.zeros(n_rows, dtype=np.float64)
    close_price_arr = np.zeros(n_rows, dtype=np.float64)
    open_idx_arr = np.full(n_rows, -1, dtype=np.int64)
    close_idx_arr = np.full(n_rows, -1, dtype=np.int64)
    holding_period_count_arr = np.zeros(n_rows, dtype=np.int64)
    holding_period_arr = np.full(n_rows, -1, dtype=np.int64)
    trade_return_arr = np.full(n_rows, np.nan, dtype=np.float64)

    next_trade_seq = 0
    active_trade_seq = 0
    active_open_price = 0.0
    active_open_idx = -1
    holding_period_count = 0

    for idx in range(n_rows):
        action = int(trade_actions[idx])
        current_price = current_prices[idx]

        if action == 1:
            next_trade_seq += 1
            active_trade_seq = next_trade_seq
            trade_seq_arr[idx] = active_trade_seq
            active_open_price = current_price
            active_open_idx = idx
            open_price_arr[idx] = active_open_price
            open_idx_arr[idx] = idx
            holding_period_count = 0
            if position[idx] > 0.0:
                position_type_code_arr[idx] = 1
            else:
                position_type_code_arr[idx] = 2
        elif action == 4:
            if active_trade_seq > 0:
                trade_seq_arr[idx] = active_trade_seq
            close_price_arr[idx] = current_price
            close_idx_arr[idx] = idx
            open_idx_arr[idx] = active_open_idx
            if idx > 0:
                prev_position = position[idx - 1]
                if prev_position > 0.0:
                    position_type_code_arr[idx] = 3
                elif prev_position < 0.0:
                    position_type_code_arr[idx] = 4
            if active_trade_seq > 0 and active_open_idx >= 0:
                holding_period_count += 1
                holding_period_count_arr[idx] = holding_period_count
                holding_period_arr[idx] = holding_period_count
                if active_open_price > 0.0:
                    if position_type_code_arr[idx] == 3:
                        trade_return_arr[idx] = (current_price - active_open_price) / active_open_price
                    elif position_type_code_arr[idx] == 4:
                        trade_return_arr[idx] = (active_open_price - current_price) / active_open_price
            active_trade_seq = 0
            active_open_price = 0.0
            active_open_idx = -1
            holding_period_count = 0
        elif active_trade_seq > 0:
            trade_seq_arr[idx] = active_trade_seq
            holding_period_count += 1
            holding_period_count_arr[idx] = holding_period_count

    return (
        trade_seq_arr,
        position_type_code_arr,
        open_price_arr,
        close_price_arr,
        open_idx_arr,
        close_idx_arr,
        holding_period_count_arr,
        holding_period_arr,
        trade_return_arr,
    )


logger = logging.getLogger("lo2cin4bt")


class TradeSimulator_backtester:
    """
    模擬交易，生成交易記錄（基於百分比）。

    Attributes:
        data (pd.DataFrame): 輸入數據，包含價格與預測因子。
        signals (pd.Series): 交易信號序列。
        transaction_cost (float): 交易成本（小數，預設0.001）。
        slippage (float): 滑點（小數，預設0.0005）。
        trade_delay (int): 交易延遲數據點數。
        trade_price (str): 交易價格（open/close）。
        Backtest_id (str): 回測唯一ID。
        parameter_set_id (str): 參數集唯一ID。
        predictor (str): 預測因子名稱。
        initial_equity (float): 初始權益（預設1.0）。
        logger (logging.Logger): 日誌記錄器。
        indicators (list): 所有參與的 indicator 實例列表，預設 None。
    """

    def __init__(  # pylint: disable=unused-argument
        self,
        data: pd.DataFrame,
        entry_signal: pd.Series,
        exit_signal: pd.Series,
        transaction_cost: float = 0.001,
        slippage: float = 0.0005,
        trade_delay: int = 0,
        trade_price: str = "close",
        Backtest_id: Optional[str] = None,
        parameter_set_id: Optional[str] = None,
        predictor: Optional[str] = None,
        initial_equity: float = 1.0,
        indicators: Optional[List[str]] = None,
        trading_instrument: Optional[str] = None,
        holding_period_days: int = 0,
    ):
        self.data = data
        self.entry_signal = entry_signal
        self.exit_signal = exit_signal
        self.transaction_cost = transaction_cost
        self.slippage = slippage
        self.trade_delay = trade_delay
        self.trade_price = trade_price
        self.Backtest_id = Backtest_id
        self.trading_instrument = trading_instrument or "X"
        self.parameter_set_id = parameter_set_id
        self.predictor = predictor
        self.initial_equity = initial_equity
        self.logger = logger
        try:
            self.holding_period_days = int(holding_period_days)
        except (TypeError, ValueError):
            self.holding_period_days = 0
        if self.holding_period_days < 0:
            self.holding_period_days = 0
        self.indicators = indicators  # NOTE: translated to English.

    def _raise_invariant_violation(self, message: str, **context: Any) -> None:
        parts = [f"{key}={value}" for key, value in context.items()]
        suffix = f" ({', '.join(parts)})" if parts else ""
        raise RuntimeError(f"TradeSimulator invariant violation: {message}{suffix}")

    def _assert_result_matrix_invariants(
        self,
        positions: np.ndarray,
        trade_actions: np.ndarray,
        equity_values: np.ndarray,
    ) -> None:
        valid_positions = {-1.0, 0.0, 1.0}
        valid_actions = {0.0, 1.0, 4.0}
        for idx in range(len(positions)):
            current_position = float(positions[idx])
            action = float(trade_actions[idx])
            equity_value = float(equity_values[idx])
            previous_position = float(positions[idx - 1]) if idx > 0 else 0.0

            if current_position not in valid_positions:
                self._raise_invariant_violation(
                    "position must be one of -1/0/1",
                    index=idx,
                    position=current_position,
                )
            if action not in valid_actions:
                self._raise_invariant_violation(
                    "trade_action must be one of 0/1/4",
                    index=idx,
                    trade_action=action,
                )
            if not np.isfinite(equity_value) or equity_value < 0.0:
                self._raise_invariant_violation(
                    "equity_value must be finite and non-negative",
                    index=idx,
                    equity_value=equity_value,
                )
            if action == 1.0:
                if previous_position != 0.0 or current_position == 0.0:
                    self._raise_invariant_violation(
                        "entry action must open from flat state",
                        index=idx,
                        previous_position=previous_position,
                        current_position=current_position,
                    )
            elif action == 4.0:
                if previous_position == 0.0 or current_position != 0.0:
                    self._raise_invariant_violation(
                        "exit action must close an existing position into flat state",
                        index=idx,
                        previous_position=previous_position,
                        current_position=current_position,
                    )
            else:
                if current_position != previous_position:
                    self._raise_invariant_violation(
                        "position cannot change without an entry/exit action",
                        index=idx,
                        previous_position=previous_position,
                        current_position=current_position,
                    )

    def _assert_runtime_state_invariants(
        self,
        *,
        index: int,
        previous_state: float,
        current_state: float,
        trade_action: float,
        previous_open_price: float,
        open_price: float,
        open_equity: float,
        holding_period_count: int,
        long_timer_ready: bool,
        short_timer_ready: bool,
        reentry_reset_applied: bool,
        equity: float,
        equity_value: float,
        current_return: float,
        current_unrealized_return: float = 0.0,
    ) -> None:
        if current_state not in (-1.0, 0.0, 1.0):
            self._raise_invariant_violation(
                "current_state must be one of -1/0/1",
                index=index,
                current_state=current_state,
            )
        if trade_action not in (0.0, 1.0, 4.0):
            self._raise_invariant_violation(
                "trade_action must be one of 0/1/4",
                index=index,
                trade_action=trade_action,
            )
        if previous_state == 0.0 and trade_action == 4.0:
            self._raise_invariant_violation(
                "cannot exit while flat",
                index=index,
                previous_state=previous_state,
            )
        if previous_state != 0.0 and trade_action == 1.0:
            self._raise_invariant_violation(
                "cannot open a new position while already holding one",
                index=index,
                previous_state=previous_state,
            )
        if previous_state != 0.0 and current_state != 0.0 and previous_state != current_state:
            self._raise_invariant_violation(
                "direct long/short flips are not allowed without flattening first",
                index=index,
                previous_state=previous_state,
                current_state=current_state,
            )
        if long_timer_ready and short_timer_ready:
            self._raise_invariant_violation(
                "long and short timers cannot both be ready on the same bar",
                index=index,
            )
        if long_timer_ready and previous_state != 1.0:
            self._raise_invariant_violation(
                "long timer cannot be ready before a long fill exists",
                index=index,
                previous_state=previous_state,
            )
        if short_timer_ready and previous_state != -1.0:
            self._raise_invariant_violation(
                "short timer cannot be ready before a short fill exists",
                index=index,
                previous_state=previous_state,
            )
        if (long_timer_ready or short_timer_ready) and previous_open_price <= 0.0:
            self._raise_invariant_violation(
                "timer cannot run before open_price is established",
                index=index,
                previous_open_price=previous_open_price,
            )
        if current_state == 0.0:
            if open_price != 0.0:
                self._raise_invariant_violation(
                    "flat state must not keep an open_price",
                    index=index,
                    open_price=open_price,
                )
            if holding_period_count != 0:
                self._raise_invariant_violation(
                    "holding_period_count must be zero while flat",
                    index=index,
                    holding_period_count=holding_period_count,
                )
        else:
            if open_price <= 0.0:
                self._raise_invariant_violation(
                    "open position requires positive open_price",
                    index=index,
                    open_price=open_price,
                )
            if open_equity <= 0.0:
                self._raise_invariant_violation(
                    "open position requires positive open_equity",
                    index=index,
                    open_equity=open_equity,
                )
            if holding_period_count < 0:
                self._raise_invariant_violation(
                    "holding_period_count cannot be negative",
                    index=index,
                    holding_period_count=holding_period_count,
                )
        if reentry_reset_applied and holding_period_count != 0:
            self._raise_invariant_violation(
                "re-entry timer reset must zero holding_period_count immediately",
                index=index,
                holding_period_count=holding_period_count,
            )
        if not np.isfinite(equity) or equity < 0.0:
            self._raise_invariant_violation(
                "equity must stay finite and non-negative",
                index=index,
                equity=equity,
            )
        if not np.isfinite(equity_value) or equity_value < 0.0:
            self._raise_invariant_violation(
                "equity_value must stay finite and non-negative",
                index=index,
                equity_value=equity_value,
            )
        if current_state == 0.0:
            expected_equity_value = equity * 100.0
            if not np.isclose(equity_value, expected_equity_value, rtol=1e-9, atol=1e-9):
                self._raise_invariant_violation(
                    "flat-state equity_value must equal realized equity",
                    index=index,
                    equity_value=equity_value,
                    expected_equity_value=expected_equity_value,
                )
        elif trade_action != 1.0:
            expected_equity_value = open_equity * (1.0 + current_unrealized_return) * 100.0
            if not np.isclose(equity_value, expected_equity_value, rtol=1e-9, atol=1e-6):
                self._raise_invariant_violation(
                    "open-position equity_value must equal marked-to-market equity",
                    index=index,
                    equity_value=equity_value,
                    expected_equity_value=expected_equity_value,
                )

    def simulate_trades(self) -> pd.DataFrame:
        """
        模擬交易，生成交易記錄

        Returns:
            tuple: (records_df, warning_msg) 包含交易記錄DataFrame和警告訊息

        Note:
            entry_signal: 1=開多, -1=開空, 0=無操作
            exit_signal: -1=平多, 1=平空, 0=無操作
        """
        # NOTE: translated to English.
        entry_signals_matrix = self.entry_signal.values.reshape(-1, 1).astype(
            np.float64
        )
        exit_signals_matrix = self.exit_signal.values.reshape(-1, 1).astype(np.float64)

        # NOTE: translated to English.
        entry_signals_matrix = np.nan_to_num(entry_signals_matrix, nan=0.0)
        exit_signals_matrix = np.nan_to_num(exit_signals_matrix, nan=0.0)

        # NOTE: translated to English.
        trading_params = {
            "transaction_cost": self.transaction_cost,
            "slippage": self.slippage,
            "trade_delay": self.trade_delay,
            "trade_price": self.trade_price,
            "holding_period_days": self.holding_period_days,
        }

        trade_results = self.simulate_trades_vectorized(
            entry_signals_matrix, exit_signals_matrix, trading_params
        )

        # NOTE: translated to English.
        positions = trade_results["positions"][:, 0]
        returns = trade_results["returns"][:, 0]
        trade_actions = trade_results["trade_actions"][:, 0]
        equity_values = trade_results["equity_values"][:, 0]

        # NOTE: translated to English.
        result = self.generate_single_result(
            0,  # task_idx
            self.entry_signal.values,
            self.exit_signal.values,
            positions,
            returns,
            trade_actions,
            equity_values,
            self.predictor or "",
            self.Backtest_id or "",
            {},  # NOTE: translated to English.
            {},  # NOTE: translated to English.
            trading_params,
        )

        return result["records"], None  # NOTE: translated to English.

    def simulate_trades_vectorized(  # pylint: disable=unused-argument
        self, entry_signals_matrix: pd.Series, exit_signals_matrix: pd.Series, trading_params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        向量化交易模擬 - 供 VBT 調用

        Args:
            entry_signals_matrix: numpy.ndarray, shape (n_time, n_strategies)
            exit_signals_matrix: numpy.ndarray, shape (n_time, n_strategies)
            trading_params: dict, 包含交易參數

        Returns:
            dict: 包含向量化交易結果
        """
        # NOTE: translated to English.
        try:
            result = _vectorized_trade_simulation_njit(
                entry_signals_matrix,
                exit_signals_matrix,
                self.data["Close"].values.astype(np.float64),
                self.data["Open"].values.astype(np.float64),
                trading_params.get("transaction_cost", 0.001),
                trading_params.get("slippage", 0.0005),
                trading_params.get("trade_price", "close"),
                trading_params.get("trade_delay", 0),
                    0,
            )
            if os.getenv("NUMBA_DISABLE_JIT", "0") == "1" or "PYTEST_CURRENT_TEST" in os.environ:
                raise RuntimeError("deterministic test/debug mode active; use Python matrix fallback")
            result_map = {
                "positions": np.asarray(result["positions"], dtype=np.float64),
                "returns": np.asarray(result["returns"], dtype=np.float64),
                "trade_actions": np.asarray(result["trade_actions"], dtype=np.float64),
                "equity_values": np.asarray(result["equity_values"], dtype=np.float64),
            }
        except Exception:  # pragma: no cover - defensive fallback
            result_map = self._simulate_trades_python_matrix(
                np.asarray(entry_signals_matrix, dtype=np.float64),
                np.asarray(exit_signals_matrix, dtype=np.float64),
                trading_params,
            )
        positions = result_map["positions"]
        returns = result_map["returns"]
        trade_actions = result_map["trade_actions"]
        equity_values = result_map["equity_values"]
        for strategy_idx in range(positions.shape[1]):
            self._assert_result_matrix_invariants(
                positions[:, strategy_idx],
                trade_actions[:, strategy_idx],
                equity_values[:, strategy_idx],
            )

        return {
            "positions": positions,
            "returns": returns,
            "trade_actions": trade_actions,
            "equity_values": equity_values,
        }

    def _simulate_trades_python_matrix(
        self,
        entry_signals_matrix: np.ndarray,
        exit_signals_matrix: np.ndarray,
        trading_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Python fallback for matrix-based trade simulation."""
        n_time, n_strategies = entry_signals_matrix.shape
        positions = np.zeros((n_time, n_strategies), dtype=np.float64)
        returns = np.zeros((n_time, n_strategies), dtype=np.float64)
        trade_actions = np.zeros((n_time, n_strategies), dtype=np.float64)
        equity_values = np.zeros((n_time, n_strategies), dtype=np.float64)

        for strategy_idx in range(n_strategies):
            single_result = self.simulate_trades_sequential(
                entry_signals_matrix[:, strategy_idx],
                exit_signals_matrix[:, strategy_idx],
                trading_params,
            )
            positions[:, strategy_idx] = single_result["positions"]
            returns[:, strategy_idx] = single_result["returns"]
            trade_actions[:, strategy_idx] = single_result["trade_actions"]
            equity_values[:, strategy_idx] = single_result["equity_values"]

        return {
            "positions": positions,
            "returns": returns,
            "trade_actions": trade_actions,
            "equity_values": equity_values,
        }

    def simulate_trades_sequential(
        self,
        entry_signal: np.ndarray,
        exit_signal: np.ndarray,
        trading_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Sequential trade simulation for a single strategy.

        This keeps the external result contract identical to the vectorized path
        while executing the state machine bar by bar in Python.
        """
        entry_signal = np.nan_to_num(np.asarray(entry_signal, dtype=np.float64), nan=0.0)
        exit_signal = np.nan_to_num(np.asarray(exit_signal, dtype=np.float64), nan=0.0)

        close_prices = self.data["Close"].values.astype(np.float64)
        open_prices = self.data["Open"].values.astype(np.float64)
        trade_delay = int(trading_params.get("trade_delay", 0) or 0)
        transaction_cost = float(trading_params.get("transaction_cost", 0.001) or 0.0)
        slippage = float(trading_params.get("slippage", 0.0005) or 0.0)
        trade_price = str(trading_params.get("trade_price", "close") or "close").lower()
        # `holding_period_days` was an earlier experiment. The current product
        # contract still only supports the existing technical indicators, so the
        # field is kept as a compatibility placeholder and does not affect live
        # trade simulation yet. NDAY will move into the dedicated sequential
        # engine path instead of piggybacking on this flag.
        holding_period_days = 0
        nday_rules = trading_params.get("nday_rules", {}) or {}
        nday_exit_long_days = int(nday_rules.get("exit_long_days", 0) or 0)
        nday_exit_short_days = int(nday_rules.get("exit_short_days", 0) or 0)
        has_non_nday_exit = bool(nday_rules.get("has_non_nday_exit", False))
        nday_combine_mode = str(nday_rules.get("combine_mode", "") or "").lower()
        reset_timer_on_reentry_signal = bool(
            trading_params.get("reset_timer_on_reentry_signal", False)
        )
        if nday_combine_mode not in {"", "timer_only", "and", "or"}:
            nday_combine_mode = ""

        n_time = len(entry_signal)
        positions = np.zeros(n_time, dtype=np.float64)
        returns = np.zeros(n_time, dtype=np.float64)
        trade_actions = np.zeros(n_time, dtype=np.float64)
        equity_values = np.zeros(n_time, dtype=np.float64)

        current_state = 0.0
        equity = 1.0
        open_price = 0.0
        open_equity = 1.0
        holding_period_count = 0

        for idx in range(n_time):
            previous_state = current_state
            previous_open_price = open_price
            previous_equity_value = equity_values[idx - 1] if idx > 0 else 0.0
            current_unrealized_return = 0.0
            reentry_reset_applied = False
            signal_index = idx - trade_delay
            entry_sig = entry_signal[signal_index] if 0 <= signal_index < n_time else 0.0
            exit_sig = exit_signal[signal_index] if 0 <= signal_index < n_time else 0.0

            if idx > 0 and current_state != 0.0 and open_price > 0.0:
                current_mark = close_prices[idx] if trade_price == "close" else open_prices[idx]
                if current_state > 0:
                    current_unrealized_return = (current_mark - open_price) / open_price
                else:
                    current_unrealized_return = (open_price - current_mark) / open_price
                equity_values[idx] = open_equity * (1.0 + current_unrealized_return) * 100.0
            else:
                equity_values[idx] = equity * 100.0

            long_timer_ready = False
            short_timer_ready = False
            if idx > 0 and current_state != 0.0 and open_price > 0.0:
                holding_period_count += 1
                if holding_period_days > 0 and holding_period_count >= holding_period_days:
                    long_timer_ready = current_state == 1.0
                    short_timer_ready = current_state == -1.0
                if (
                    nday_exit_long_days > 0
                    and current_state == 1.0
                    and holding_period_count >= nday_exit_long_days
                ):
                    long_timer_ready = True
                if (
                    nday_exit_short_days > 0
                    and current_state == -1.0
                    and holding_period_count >= nday_exit_short_days
                ):
                    short_timer_ready = True

            # Optional behavior: while position is open, a same-side re-entry signal
            # can re-arm the timer without forcing a close/re-open transaction.
            if reset_timer_on_reentry_signal and current_state != 0.0:
                if (current_state == 1.0 and entry_sig == 1.0) or (
                    current_state == -1.0 and entry_sig == -1.0
                ):
                    holding_period_count = 0
                    long_timer_ready = False
                    short_timer_ready = False
                    reentry_reset_applied = True

            if current_state == 0.0:
                if entry_sig == 1.0:
                    current_state = 1.0
                    trade_actions[idx] = 1.0
                    open_price = close_prices[idx] if trade_price == "close" else open_prices[idx]
                    equity *= (1.0 - slippage) * (1.0 - transaction_cost)
                    open_equity = equity
                    holding_period_count = 0
                elif entry_sig == -1.0:
                    current_state = -1.0
                    trade_actions[idx] = 1.0
                    open_price = close_prices[idx] if trade_price == "close" else open_prices[idx]
                    equity *= (1.0 - slippage) * (1.0 - transaction_cost)
                    open_equity = equity
                    holding_period_count = 0
            elif current_state == 1.0:
                if nday_exit_long_days > 0:
                    if nday_combine_mode == "and":
                        should_close_long = exit_sig == -1.0 and long_timer_ready
                    elif nday_combine_mode == "or":
                        should_close_long = (exit_sig == -1.0) or long_timer_ready
                    elif nday_combine_mode == "timer_only":
                        should_close_long = long_timer_ready
                    else:
                        should_close_long = (
                            exit_sig == -1.0 and long_timer_ready
                        ) if has_non_nday_exit else long_timer_ready
                else:
                    should_close_long = exit_sig == -1.0 or long_timer_ready
                if should_close_long:
                    # Realize marked-to-market equity on the close bar before costs.
                    if idx >= 0 and equity_values[idx] > 0.0:
                        equity = equity_values[idx] / 100.0
                    current_state = 0.0
                    trade_actions[idx] = 4.0
                    open_price = 0.0
                    open_equity = 1.0
                    equity *= (1.0 - slippage) * (1.0 - transaction_cost)
                    holding_period_count = 0
            elif current_state == -1.0:
                if nday_exit_short_days > 0:
                    if nday_combine_mode == "and":
                        should_close_short = exit_sig == 1.0 and short_timer_ready
                    elif nday_combine_mode == "or":
                        should_close_short = (exit_sig == 1.0) or short_timer_ready
                    elif nday_combine_mode == "timer_only":
                        should_close_short = short_timer_ready
                    else:
                        should_close_short = (
                            exit_sig == 1.0 and short_timer_ready
                        ) if has_non_nday_exit else short_timer_ready
                else:
                    should_close_short = exit_sig == 1.0 or short_timer_ready
                if should_close_short:
                    # Realize marked-to-market equity on the close bar before costs.
                    if idx >= 0 and equity_values[idx] > 0.0:
                        equity = equity_values[idx] / 100.0
                    current_state = 0.0
                    trade_actions[idx] = 4.0
                    open_price = 0.0
                    open_equity = 1.0
                    equity *= (1.0 - slippage) * (1.0 - transaction_cost)
                    holding_period_count = 0

            positions[idx] = current_state
            if current_state == 0.0:
                equity_values[idx] = equity * 100.0
            elif trade_actions[idx] == 1.0:
                # Reflect entry costs immediately on the fill bar so the
                # sequential path matches vectorized cost realization.
                equity_values[idx] = equity * 100.0
            if previous_equity_value > 0.0:
                returns[idx] = equity_values[idx] / previous_equity_value - 1.0
            else:
                returns[idx] = 0.0
            self._assert_runtime_state_invariants(
                index=idx,
                previous_state=previous_state,
                current_state=current_state,
                trade_action=trade_actions[idx],
                previous_open_price=previous_open_price,
                open_price=open_price,
                open_equity=open_equity,
                holding_period_count=holding_period_count,
                long_timer_ready=long_timer_ready,
                short_timer_ready=short_timer_ready,
                reentry_reset_applied=reentry_reset_applied,
                equity=equity,
                equity_value=equity_values[idx],
                current_return=returns[idx],
                current_unrealized_return=current_unrealized_return,
            )

        self._assert_result_matrix_invariants(positions, trade_actions, equity_values)

        return {
            "positions": positions,
            "returns": returns,
            "trade_actions": trade_actions,
            "equity_values": equity_values,
        }

    def generate_single_result(  # pylint: disable=too-complex
        self,
        task_idx: int,
        entry_signal: np.ndarray,
        exit_signal: np.ndarray,
        position: np.ndarray,
        returns: np.ndarray,
        trade_actions: np.ndarray,
        equity_values: np.ndarray,
        predictor: str,
        backtest_id: str,
        entry_params: Dict[str, Any],
        exit_params: Dict[str, Any],
        trading_params: Dict[str, Any],
        semantic_predictor_fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Generate a single backtest result using the vectorbt-compatible path.
        """
        _ = task_idx

        trade_price = str(trading_params.get("trade_price", "close") or "close")
        price_column = "Open" if trade_price == "open" else "Close"
        current_prices = self.data[price_column].to_numpy(dtype=np.float64, copy=False)
        n_rows = len(position)

        try:
            ledger_arrays = _build_trade_ledger_njit(
                np.asarray(position, dtype=np.float64),
                np.asarray(trade_actions, dtype=np.float64),
                current_prices,
            )
        except Exception as exc:  # pragma: no cover - numba environment fallback
            self.logger.warning("trade ledger numba path failed; using python fallback: %s", exc)
            ledger_arrays = _build_trade_ledger_njit.py_func(
                np.asarray(position, dtype=np.float64),
                np.asarray(trade_actions, dtype=np.float64),
                current_prices,
            )

        (
            trade_seq_arr,
            position_type_code_arr,
            open_price_arr,
            close_price_arr,
            open_idx_arr,
            close_idx_arr,
            holding_period_count_arr,
            holding_period_arr,
            trade_return_arr,
        ) = ledger_arrays

        strategy_name = self._generate_parameter_set_id(
            entry_params, exit_params, predictor
        )
        time_values = self._resolve_time_values()
        open_time_arr, close_time_arr = self._build_trade_time_arrays(
            time_values=time_values,
            open_idx_arr=open_idx_arr,
            close_idx_arr=close_idx_arr,
            position_type_code_arr=position_type_code_arr,
        )

        action_arr = np.asarray(trade_actions, dtype=np.int64)
        transaction_cost_value = float(trading_params.get("transaction_cost", 0.001) or 0.0)
        slippage_cost_value = float(trading_params.get("slippage", 0.0005) or 0.0)
        cost_mask = action_arr != 0
        transaction_cost_arr = np.where(cost_mask, transaction_cost_value, 0.0)
        slippage_cost_arr = np.where(cost_mask, slippage_cost_value, 0.0)

        trade_group_id_arr = self._build_trade_group_ids(
            backtest_id=backtest_id,
            trade_seq_arr=trade_seq_arr,
        )
        position_type_arr = self._build_position_type_labels(position_type_code_arr)
        holding_period_value_arr = np.where(
            holding_period_arr >= 0,
            holding_period_arr.astype(np.float64),
            np.nan,
        )

        records_payload: Dict[str, Any] = {
            "Time": time_values,
            "Open": self.data["Open"].to_numpy(dtype=np.float64, copy=False),
            "High": self.data["High"].to_numpy(dtype=np.float64, copy=False),
            "Low": self.data["Low"].to_numpy(dtype=np.float64, copy=False),
            "Close": self.data["Close"].to_numpy(dtype=np.float64, copy=False),
            "Trading_instrument": np.full(
                n_rows,
                getattr(self, "trading_instrument", "X"),
                dtype=object,
            ),
            "Position_type": position_type_arr,
            "Open_position_price": open_price_arr,
            "Close_position_price": close_price_arr,
            "Position_size": np.asarray(position, dtype=np.float64),
            "Return": np.asarray(returns, dtype=np.float64),
            "Trade_group_id": trade_group_id_arr,
            "Trade_seq": trade_seq_arr,
            "Trade_action": action_arr,
            "Open_time": open_time_arr,
            "Close_time": close_time_arr,
            "Parameter_set_id": np.full(n_rows, strategy_name, dtype=object),
            "Equity_value": np.asarray(equity_values, dtype=np.float64),
            "Transaction_cost": transaction_cost_arr,
            "Slippage_cost": slippage_cost_arr,
            "Entry_signal": np.asarray(entry_signal, dtype=np.float64),
            "Exit_signal": np.asarray(exit_signal, dtype=np.float64),
            "Holding_period_count": holding_period_count_arr,
            "Holding_period": holding_period_value_arr,
            "Trade_return": trade_return_arr,
            "Backtest_id": np.full(n_rows, backtest_id, dtype=object),
        }
        records_payload.update(
            self._build_predictor_columns(
                predictor=predictor,
                semantic_predictor_fields=semantic_predictor_fields,
                row_count=n_rows,
            )
        )
        records_df = pd.DataFrame(records_payload)

        params_dict = {
            "entry": [
                (
                    param.to_dict()
                    if hasattr(param, "to_dict")
                    else self._param_to_dict(param)
                )
                for param in entry_params
            ],
            "exit": [
                (
                    param.to_dict()
                    if hasattr(param, "to_dict")
                    else self._param_to_dict(param)
                )
                for param in exit_params
            ],
            "predictor": predictor,
        }
        if isinstance(semantic_predictor_fields, list) and semantic_predictor_fields:
            params_dict["semantic_fields"] = list(semantic_predictor_fields)

        result = {
            "Backtest_id": backtest_id,
            "strategy_id": strategy_name,
            "params": params_dict,
            "records": records_df,
            "warning_msg": None,
            "error": None,
        }

        return result

    def generate_same_session_result(  # pylint: disable=too-many-arguments,too-complex
        self,
        task_idx: int,
        *,
        entry_signal: np.ndarray,
        side: str,
        entry_price: str,
        exit_price: str,
        predictor: str,
        backtest_id: str,
        trading_params: Dict[str, Any],
        semantic_predictor_fields: Optional[List[str]] = None,
        combo: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build records for event trades opened and closed inside one session.

        Calendar predicates can be reused by future portfolio workflows, but
        this adapter deliberately emits the current single-result record shape.
        """
        _ = task_idx

        entry_signal = np.nan_to_num(np.asarray(entry_signal, dtype=np.float64), nan=0.0)
        trade_delay = int(trading_params.get("trade_delay", self.trade_delay) or 0)
        transaction_cost = float(trading_params.get("transaction_cost", self.transaction_cost) or 0.0)
        slippage_cost = float(trading_params.get("slippage", self.slippage) or 0.0)
        entry_column = "Open" if str(entry_price or "open").lower() == "open" else "Close"
        exit_column = "Open" if str(exit_price or "close").lower() == "open" else "Close"
        side_key = str(side or "long").strip().lower()
        side_code = -1.0 if side_key == "short" else 1.0
        entry_label = "new_short" if side_code < 0 else "new_long"
        exit_label = "close_short" if side_code < 0 else "close_long"
        combo_label = self._format_combo_label(combo)
        strategy_name = f"Calendar_{side_key}_{entry_column.lower()}_to_{exit_column.lower()}"
        if combo_label:
            strategy_name = f"{strategy_name}_{combo_label}"
        time_values = self._resolve_time_values()

        equity = 1.0
        previous_equity_value = equity * 100.0
        records: List[Dict[str, Any]] = []
        execution_audit_rows: List[Dict[str, Any]] = []
        trade_seq = 0

        for idx, row in self.data.reset_index(drop=True).iterrows():
            signal_index = idx - trade_delay
            signal_value = entry_signal[signal_index] if 0 <= signal_index < len(entry_signal) else 0.0
            time_value = time_values[idx]
            if signal_value > 0.0:
                open_price = float(row[entry_column])
                close_price = float(row[exit_column])
                resolved_session_date = self._date_string(time_value)
                if (
                    pd.isna(open_price)
                    or pd.isna(close_price)
                    or open_price <= 0.0
                    or close_price <= 0.0
                ):
                    skip_reason = "missing_or_invalid_entry_exit_price"
                    execution_audit_rows.append(
                        {
                            "resolved_session_date": resolved_session_date,
                            "execution_status": "skipped",
                            "execution_skip_reason": skip_reason,
                            "entry_price": open_price,
                            "exit_price": close_price,
                        }
                    )
                    flat_record = self._base_trade_record(
                        row=row,
                        time_value=time_value,
                        position_type=None,
                        open_price=0.0,
                        close_price=0.0,
                        position_size=0.0,
                        row_return=0.0,
                        trade_group_id=None,
                        trade_seq=0,
                        trade_action=0,
                        open_time=None,
                        close_time=None,
                        parameter_set_id=strategy_name,
                        equity_value=equity * 100.0,
                        transaction_cost=0.0,
                        slippage_cost=0.0,
                        entry_signal=side_code,
                        exit_signal=0.0,
                        holding_period_count=0,
                        holding_period=np.nan,
                        trade_return=np.nan,
                        backtest_id=backtest_id,
                        predictor=predictor,
                        semantic_predictor_fields=semantic_predictor_fields,
                    )
                    flat_record["Calendar_triggered"] = True
                    flat_record["Calendar_execution_status"] = "skipped"
                    flat_record["Calendar_skip_reason"] = skip_reason
                    records.append(flat_record)
                    continue

                trade_seq += 1
                trade_group_id = f"{backtest_id}:T{trade_seq}"
                entry_equity = equity * (1.0 - slippage_cost) * (1.0 - transaction_cost)
                trade_return = (
                    (close_price - open_price) / open_price
                    if side_code > 0
                    else (open_price - close_price) / open_price
                ) if open_price > 0.0 else 0.0
                exit_equity = entry_equity * (1.0 + trade_return)
                exit_equity *= (1.0 - slippage_cost) * (1.0 - transaction_cost)

                entry_record = self._base_trade_record(
                    row=row,
                    time_value=time_value,
                    position_type=entry_label,
                    open_price=open_price,
                    close_price=0.0,
                    position_size=side_code,
                    row_return=entry_equity * 100.0 / previous_equity_value - 1.0
                    if previous_equity_value > 0.0
                    else 0.0,
                    trade_group_id=trade_group_id,
                    trade_seq=trade_seq,
                    trade_action=1,
                    open_time=time_value,
                    close_time=None,
                    parameter_set_id=strategy_name,
                    equity_value=entry_equity * 100.0,
                    transaction_cost=transaction_cost,
                    slippage_cost=slippage_cost,
                    entry_signal=side_code,
                    exit_signal=0.0,
                    holding_period_count=0,
                    holding_period=np.nan,
                    trade_return=np.nan,
                    backtest_id=backtest_id,
                    predictor=predictor,
                    semantic_predictor_fields=semantic_predictor_fields,
                )
                entry_record["Calendar_triggered"] = True
                entry_record["Calendar_execution_status"] = "executed"
                entry_record["Calendar_skip_reason"] = None
                records.append(entry_record)

                exit_record = self._base_trade_record(
                    row=row,
                    time_value=time_value,
                    position_type=exit_label,
                    open_price=open_price,
                    close_price=close_price,
                    position_size=0.0,
                    row_return=exit_equity * 100.0 / (entry_equity * 100.0) - 1.0
                    if entry_equity > 0.0
                    else 0.0,
                    trade_group_id=trade_group_id,
                    trade_seq=trade_seq,
                    trade_action=4,
                    open_time=time_value,
                    close_time=time_value,
                    parameter_set_id=strategy_name,
                    equity_value=exit_equity * 100.0,
                    transaction_cost=transaction_cost,
                    slippage_cost=slippage_cost,
                    entry_signal=0.0,
                    exit_signal=-side_code,
                    holding_period_count=1,
                    holding_period=1.0,
                    trade_return=trade_return,
                    backtest_id=backtest_id,
                    predictor=predictor,
                    semantic_predictor_fields=semantic_predictor_fields,
                )
                exit_record["Calendar_triggered"] = True
                exit_record["Calendar_execution_status"] = "executed"
                exit_record["Calendar_skip_reason"] = None
                records.append(exit_record)
                execution_audit_rows.append(
                    {
                        "resolved_session_date": resolved_session_date,
                        "execution_status": "executed",
                        "execution_skip_reason": None,
                        "entry_price": open_price,
                        "exit_price": close_price,
                    }
                )
                equity = exit_equity
                previous_equity_value = equity * 100.0
                continue

            flat_record = self._base_trade_record(
                row=row,
                time_value=time_value,
                position_type=None,
                open_price=0.0,
                close_price=0.0,
                position_size=0.0,
                row_return=0.0,
                trade_group_id=None,
                trade_seq=0,
                trade_action=0,
                open_time=None,
                close_time=None,
                parameter_set_id=strategy_name,
                equity_value=equity * 100.0,
                transaction_cost=0.0,
                slippage_cost=0.0,
                entry_signal=0.0,
                exit_signal=0.0,
                holding_period_count=0,
                holding_period=np.nan,
                trade_return=np.nan,
                backtest_id=backtest_id,
                predictor=predictor,
                semantic_predictor_fields=semantic_predictor_fields,
            )
            flat_record["Calendar_triggered"] = False
            flat_record["Calendar_execution_status"] = None
            flat_record["Calendar_skip_reason"] = None
            records.append(flat_record)

        params_dict = {
            "entry": [],
            "exit": [],
            "predictor": predictor,
            "execution_mode": "same_session",
            "side": side_key,
            "entry_price": entry_column.lower(),
            "exit_price": exit_column.lower(),
        }
        if isinstance(combo, dict) and combo:
            params_dict["combo"] = dict(combo)
        if isinstance(semantic_predictor_fields, list) and semantic_predictor_fields:
            params_dict["semantic_fields"] = list(semantic_predictor_fields)

        result = {
            "Backtest_id": backtest_id,
            "strategy_id": strategy_name,
            "params": params_dict,
            "records": pd.DataFrame(records),
            "warning_msg": None,
            "error": None,
        }
        if execution_audit_rows:
            result["calendar_execution_audit"] = pd.DataFrame(execution_audit_rows)
        return result

    def _base_trade_record(
        self,
        *,
        row: pd.Series,
        time_value: Any,
        position_type: Optional[str],
        open_price: float,
        close_price: float,
        position_size: float,
        row_return: float,
        trade_group_id: Optional[str],
        trade_seq: int,
        trade_action: int,
        open_time: Any,
        close_time: Any,
        parameter_set_id: str,
        equity_value: float,
        transaction_cost: float,
        slippage_cost: float,
        entry_signal: float,
        exit_signal: float,
        holding_period_count: int,
        holding_period: float,
        trade_return: float,
        backtest_id: str,
        predictor: str,
        semantic_predictor_fields: Optional[List[str]],
    ) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "Time": time_value,
            "Open": float(row["Open"]),
            "High": float(row["High"]),
            "Low": float(row["Low"]),
            "Close": float(row["Close"]),
            "Trading_instrument": getattr(self, "trading_instrument", "X"),
            "Position_type": position_type,
            "Open_position_price": open_price,
            "Close_position_price": close_price,
            "Position_size": position_size,
            "Return": row_return,
            "Trade_group_id": trade_group_id,
            "Trade_seq": trade_seq,
            "Trade_action": trade_action,
            "Open_time": open_time,
            "Close_time": close_time,
            "Parameter_set_id": parameter_set_id,
            "Equity_value": equity_value,
            "Transaction_cost": transaction_cost,
            "Slippage_cost": slippage_cost,
            "Entry_signal": entry_signal,
            "Exit_signal": exit_signal,
            "Holding_period_count": holding_period_count,
            "Holding_period": holding_period,
            "Trade_return": trade_return,
            "Backtest_id": backtest_id,
        }
        self._append_predictor_values_to_record(
            record=record,
            row=row,
            predictor=predictor,
            semantic_predictor_fields=semantic_predictor_fields,
        )
        return record

    @staticmethod
    def _format_combo_label(combo: Optional[Dict[str, Any]]) -> str:
        if not isinstance(combo, dict) or not combo:
            return ""
        parts = []
        for key in sorted(combo):
            value = combo[key]
            safe_value = str(value).strip().lower().replace(" ", "-")
            parts.append(f"{key}={safe_value}")
        return "__".join(parts)

    @staticmethod
    def _date_string(value: Any) -> Optional[str]:
        if value is None or pd.isna(value):
            return None
        return pd.Timestamp(value).normalize().strftime("%Y-%m-%d")

    def _resolve_time_values(self) -> np.ndarray:
        if isinstance(self.data.index, pd.DatetimeIndex):
            return pd.Index(self.data.index).to_numpy(dtype="datetime64[ns]", copy=False)
        if "Time" in self.data.columns:
            time_series = self.data["Time"]
            if pd.api.types.is_datetime64_any_dtype(time_series):
                return pd.to_datetime(time_series).to_numpy(dtype="datetime64[ns]", copy=False)
            return time_series.to_numpy(copy=False)
        return np.arange(len(self.data), dtype=np.int64)

    @staticmethod
    def _build_position_type_labels(position_type_code_arr: np.ndarray) -> np.ndarray:
        labels = np.empty(len(position_type_code_arr), dtype=object)
        labels[:] = None
        mapping = {
            1: "new_long",
            2: "new_short",
            3: "close_long",
            4: "close_short",
        }
        for code, label in mapping.items():
            labels[position_type_code_arr == code] = label
        return labels

    @staticmethod
    def _build_trade_group_ids(*, backtest_id: str, trade_seq_arr: np.ndarray) -> np.ndarray:
        trade_group_id_arr = np.empty(len(trade_seq_arr), dtype=object)
        trade_group_id_arr[:] = None
        active_mask = trade_seq_arr > 0
        if np.any(active_mask):
            trade_group_id_arr[active_mask] = [
                f"{backtest_id}:T{int(trade_seq)}"
                for trade_seq in trade_seq_arr[active_mask]
            ]
        return trade_group_id_arr

    @staticmethod
    def _build_trade_time_arrays(
        *,
        time_values: np.ndarray,
        open_idx_arr: np.ndarray,
        close_idx_arr: np.ndarray,
        position_type_code_arr: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        is_datetime = np.issubdtype(np.asarray(time_values).dtype, np.datetime64)
        n_rows = len(time_values)
        if is_datetime:
            open_time_arr = np.full(n_rows, np.datetime64("NaT"), dtype="datetime64[ns]")
            close_time_arr = np.full(n_rows, np.datetime64("NaT"), dtype="datetime64[ns]")
        else:
            open_time_arr = np.empty(n_rows, dtype=object)
            close_time_arr = np.empty(n_rows, dtype=object)
            open_time_arr[:] = None
            close_time_arr[:] = None

        entry_mask = (position_type_code_arr == 1) | (position_type_code_arr == 2)
        close_mask = (position_type_code_arr == 3) | (position_type_code_arr == 4)

        if np.any(entry_mask):
            open_time_arr[entry_mask] = time_values[entry_mask]
        if np.any(close_mask):
            close_time_arr[close_mask] = time_values[close_mask]
            valid_close_open_mask = close_mask & (open_idx_arr >= 0)
            if np.any(valid_close_open_mask):
                open_time_arr[valid_close_open_mask] = time_values[open_idx_arr[valid_close_open_mask]]
        return open_time_arr, close_time_arr

    def _build_predictor_columns(
        self,
        *,
        predictor: str,
        semantic_predictor_fields: Optional[List[str]],
        row_count: int,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if isinstance(semantic_predictor_fields, list) and semantic_predictor_fields:
            for idx, field_name in enumerate(semantic_predictor_fields, start=1):
                payload[f"Predictor_{idx}_name"] = np.full(row_count, field_name, dtype=object)
                if field_name in self.data.columns:
                    payload[f"Predictor_{idx}_value"] = pd.to_numeric(
                        self.data[field_name], errors="coerce"
                    ).to_numpy(dtype=np.float64, copy=False)
                else:
                    payload[f"Predictor_{idx}_value"] = np.full(row_count, np.nan, dtype=np.float64)
            primary_field = semantic_predictor_fields[0]
            payload["Predictor_name"] = np.full(row_count, primary_field, dtype=object)
            if primary_field in self.data.columns:
                payload["Predictor_value"] = pd.to_numeric(
                    self.data[primary_field], errors="coerce"
                ).to_numpy(dtype=np.float64, copy=False)
            else:
                payload["Predictor_value"] = np.full(row_count, np.nan, dtype=np.float64)
            return payload

        payload["Predictor_name"] = np.full(row_count, predictor, dtype=object)
        if predictor in self.data.columns:
            payload["Predictor_value"] = pd.to_numeric(
                self.data[predictor], errors="coerce"
            ).to_numpy(dtype=np.float64, copy=False)
        else:
            payload["Predictor_value"] = np.zeros(row_count, dtype=np.float64)
        return payload

    def _append_predictor_values_to_record(
        self,
        *,
        record: Dict[str, Any],
        row: pd.Series,
        predictor: str,
        semantic_predictor_fields: Optional[List[str]],
    ) -> None:
        if isinstance(semantic_predictor_fields, list) and semantic_predictor_fields:
            for idx, field_name in enumerate(semantic_predictor_fields, start=1):
                record[f"Predictor_{idx}_name"] = field_name
                record[f"Predictor_{idx}_value"] = (
                    row[field_name] if field_name in self.data.columns else np.nan
                )
            primary_field = semantic_predictor_fields[0]
            record["Predictor_name"] = primary_field
            record["Predictor_value"] = (
                row[primary_field] if primary_field in self.data.columns else np.nan
            )
            return
        record["Predictor_name"] = predictor
        record["Predictor_value"] = (
            row[predictor] if predictor in self.data.columns else 0.0
        )

    def _param_to_dict(self, param: Any) -> Dict[str, Any]:  # pylint: disable=unused-argument
        """將參數物件轉換為字典格式"""
        if param is None:
            return {}

        result = {"indicator_type": param.indicator_type}
        for key, value in param.params.items():
            result[key] = value
        return result

    def _generate_parameter_set_id(
        self, entry_params: Union[List[IndicatorParams], Dict[str, Any]], exit_params: Union[List[IndicatorParams], Dict[str, Any]], predictor: str
    ) -> str:  # pylint: disable=too-complex
        """
        根據 entry/exit 參數生成有意義的 parameter_set_id
        """
        # NOTE: translated to English.
        if isinstance(entry_params, dict):
            # NOTE: translated to English.
            return f"Strategy_{predictor}_{len(entry_params)}"

        # NOTE: translated to English.
        entry_str = ""
        for i, param in enumerate(entry_params):
            if param.indicator_type == "MA":
                period = param.get_param("period")
                ma_type = param.get_param("ma_type", "SMA")
                strat_idx = param.get_param("strat_idx", 1)
                # NOTE: translated to English.
                if strat_idx in [9, 10, 11, 12]:
                    m = param.get_param("m", 2)
                    entry_str += f"MA{strat_idx}_{ma_type}({period},{m})"
                else:
                    entry_str += f"MA{strat_idx}_{ma_type}({period})"
            elif param.indicator_type == "BOLL":
                ma_length = param.get_param("ma_length")
                std_multiplier = param.get_param("std_multiplier")
                strat_idx = param.get_param("strat_idx", 1)
                entry_str += f"BOLL{strat_idx}({ma_length},{std_multiplier})"
            elif param.indicator_type == "HL":
                n_length = param.get_param("n_length")
                m_length = param.get_param("m_length")
                strat_idx = param.get_param("strat_idx", 1)
                entry_str += f"HL{strat_idx}({n_length},{m_length})"
            elif param.indicator_type == "VALUE":
                strat_idx = param.get_param("strat_idx", 1)
                if strat_idx in [1, 2, 3, 4]:
                    n_length = param.get_param("n_length")
                    m_value = param.get_param("m_value")
                    entry_str += f"VALUE{strat_idx}({n_length},{m_value})"
                elif strat_idx in [5, 6]:
                    m1_value = param.get_param("m1_value")
                    m2_value = param.get_param("m2_value")
                    entry_str += f"VALUE{strat_idx}({m1_value},{m2_value})"
                else:
                    entry_str += f"VALUE{strat_idx}"

            elif param.indicator_type == "PERC":
                window = param.get_param("window")
                strat_idx = param.get_param("strat_idx", 1)
                if strat_idx in [1, 2, 3, 4]:
                    percentile = param.get_param("percentile")
                    entry_str += f"PERC{strat_idx}(W={window},P={percentile})"
                elif strat_idx in [5, 6]:
                    m1 = param.get_param("m1")
                    m2 = param.get_param("m2")
                    entry_str += f"PERC{strat_idx}(W={window},M1={m1},M2={m2})"
                else:
                    entry_str += f"PERC{strat_idx}(W={window})"

            if i < len(entry_params) - 1:
                entry_str += "+"

        # NOTE: translated to English.
        exit_str = ""
        # NOTE: translated to English.
        for i, param in enumerate(exit_params):
            if param.indicator_type == "MA":
                period = param.get_param("period")
                ma_type = param.get_param("ma_type", "SMA")
                strat_idx = param.get_param("strat_idx", 1)
                # NOTE: translated to English.
                if strat_idx in [9, 10, 11, 12]:
                    m = param.get_param("m", 2)
                    exit_str += f"MA{strat_idx}_{ma_type}({period},{m})"
                else:
                    exit_str += f"MA{strat_idx}_{ma_type}({period})"
            elif param.indicator_type == "BOLL":
                ma_length = param.get_param("ma_length")
                std_multiplier = param.get_param("std_multiplier")
                strat_idx = param.get_param("strat_idx", 1)
                exit_str += f"BOLL{strat_idx}({ma_length},{std_multiplier})"
            elif param.indicator_type == "HL":
                n_length = param.get_param("n_length")
                m_length = param.get_param("m_length")
                strat_idx = param.get_param("strat_idx", 1)
                exit_str += f"HL{strat_idx}({n_length},{m_length})"
            elif param.indicator_type == "VALUE":
                strat_idx = param.get_param("strat_idx", 1)
                if strat_idx in [1, 2, 3, 4]:
                    n_length = param.get_param("n_length")
                    m_value = param.get_param("m_value")
                    exit_str += f"VALUE{strat_idx}({n_length},{m_value})"
                elif strat_idx in [5, 6]:
                    m1_value = param.get_param("m1_value")
                    m2_value = param.get_param("m2_value")
                    exit_str += f"VALUE{strat_idx}({m1_value},{m2_value})"
                else:
                    exit_str += f"VALUE{strat_idx}"

            elif param.indicator_type == "PERC":
                window = param.get_param("window")
                strat_idx = param.get_param("strat_idx", 1)
                if strat_idx in [1, 2, 3, 4]:
                    percentile = param.get_param("percentile")
                    exit_str += f"PERC{strat_idx}(W={window},P={percentile})"
                elif strat_idx in [5, 6]:
                    m1 = param.get_param("m1")
                    m2 = param.get_param("m2")
                    exit_str += f"PERC{strat_idx}(W={window},M1={m1},M2={m2})"
                else:
                    exit_str += f"PERC{strat_idx}(W={window})"

            if i < len(exit_params) - 1:
                exit_str += "+"

        # NOTE: translated to English.
        if exit_str:
            return f"{entry_str}_{predictor}_{exit_str}"
        else:
            return f"{entry_str}_{predictor}"
