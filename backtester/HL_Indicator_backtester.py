

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .IndicatorParams_backtester import IndicatorParams

INDICATOR_FAMILY_GUIDE = """
HL 指標家族
-----------
- HL1-HL4：連續 N 根觸及最近 M 根高點 / 低點
- 常用參數：n_range, m_range

常用配置範例
------------
{
  "condition_pairs": [{"entry": ["HL1"], "exit": ["HL4"]}],
  "indicator_params": {
    "HL1_strategy_1": {"n_range": "1:1:1", "m_range": "10:200:20"},
    "HL4_strategy_1": {"n_range": "1:1:1", "m_range": "10:200:20"}
  }
}
"""

# NOTE: translated to English.
try:
    from numba import njit

    NUMBA_AVAILABLE = True
except ImportError:
    NUMBA_AVAILABLE = False
    # NOTE: translated to English.

# NOTE: translated to English.
if NUMBA_AVAILABLE:

    @njit(fastmath=True, cache=True)  # type: ignore[misc]
    def _generate_hl_signals_njit(
        predictor_values, n_length, m_length, strat_idx
    ):  # type: ignore[no-untyped-def] # pylint: disable=too-complex

        n = len(predictor_values)
        signals = np.zeros(n)

        # NOTE: translated to English.
        # NOTE: translated to English.
        # NOTE: translated to English.
        for i in range(m_length + n_length - 1, n):
            # NOTE: translated to English.
            start_idx = i - m_length + 1  # NOTE: translated to English.
            end_idx = i + 1  # NOTE: translated to English.

            _ = predictor_values[start_idx:end_idx].max()  # historical_high
            _ = predictor_values[start_idx:end_idx].min()  # historical_low

            # NOTE: translated to English.
            if strat_idx == 1:  # NOTE: translated to English.
                # NOTE: translated to English.
                # NOTE: translated to English.
                if i >= n_length - 1:
                    all_equal_high = True
                    for j in range(i - n_length + 1, i + 1):
                        # NOTE: translated to English.
                        if j < 0:
                            all_equal_high = False
                            break
                        # NOTE: translated to English.
                        j_start = max(0, j - m_length + 1)
                        j_end = j + 1
                        j_historical_high = predictor_values[j_start:j_end].max()
                        # NOTE: translated to English.
                        if abs(predictor_values[j] - j_historical_high) > 1e-10:
                            all_equal_high = False
                            break
                    if all_equal_high:
                        signals[i] = 1.0

            elif strat_idx == 2:  # NOTE: translated to English.
                # NOTE: translated to English.
                # NOTE: translated to English.
                if i >= n_length - 1:
                    all_equal_high = True
                    for j in range(i - n_length + 1, i + 1):
                        # NOTE: translated to English.
                        if j < 0:
                            all_equal_high = False
                            break
                        # NOTE: translated to English.
                        j_start = max(0, j - m_length + 1)
                        j_end = j + 1
                        j_historical_high = predictor_values[j_start:j_end].max()
                        # NOTE: translated to English.
                        if abs(predictor_values[j] - j_historical_high) > 1e-10:
                            all_equal_high = False
                            break
                    if all_equal_high:
                        signals[i] = -1.0

            elif strat_idx == 3:  # NOTE: translated to English.
                # NOTE: translated to English.
                # NOTE: translated to English.
                if i >= n_length - 1:
                    all_equal_low = True
                    for j in range(i - n_length + 1, i + 1):
                        # NOTE: translated to English.
                        if j < 0:
                            all_equal_low = False
                            break
                        # NOTE: translated to English.
                        j_start = max(0, j - m_length + 1)
                        j_end = j + 1
                        j_historical_low = predictor_values[j_start:j_end].min()
                        # NOTE: translated to English.
                        if abs(predictor_values[j] - j_historical_low) > 1e-10:
                            all_equal_low = False
                            break
                    if all_equal_low:
                        signals[i] = 1.0

            elif strat_idx == 4:  # NOTE: translated to English.
                # NOTE: translated to English.
                # NOTE: translated to English.
                if i >= n_length - 1:
                    all_equal_low = True
                    for j in range(i - n_length + 1, i + 1):
                        # NOTE: translated to English.
                        if j < 0:
                            all_equal_low = False
                            break
                        # NOTE: translated to English.
                        j_start = max(0, j - m_length + 1)
                        j_end = j + 1
                        j_historical_low = predictor_values[j_start:j_end].min()
                        # NOTE: translated to English.
                        if abs(predictor_values[j] - j_historical_low) > 1e-10:
                            all_equal_low = False
                            break
                    if all_equal_low:
                        signals[i] = -1.0

        return signals


class HLIndicator:


    STRATEGY_DESCRIPTIONS = [
        "連續n日等同過去m日高位做多",
        "連續n日等同過去m日高位做空",
        "連續n日等同過去m日低位做多",
        "連續n日等同過去m日低位做空",
    ]

    @staticmethod
    def get_strategy_descriptions() -> Dict[str, str]:
        # NOTE: translated to English.
        return {
            f"HL{i + 1}": desc for i, desc in enumerate(HLIndicator.STRATEGY_DESCRIPTIONS)
        }

    def __init__(
        self, data: pd.DataFrame, params: "IndicatorParams", logger: Optional[logging.Logger] = None
    ) -> None:  # pylint: disable=unused-argument
        self.data = data  # NOTE: translated to English.
        self.params = params
        self.logger = logger or logging.getLogger(self.__class__.__name__)

    @classmethod
    def get_params(
        cls, strat_idx: Optional[int] = None, params_config: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:  # pylint: disable=unused-argument

        if params_config is None:
            raise ValueError("params_config 必須由 UserInterface 提供，且不得為 None")

        if "n_range" not in params_config:
            raise ValueError("n_range 必須由 UserInterface 提供")
        if "m_range" not in params_config:
            raise ValueError("m_range 必須由 UserInterface 提供")

        n_range = params_config["n_range"]
        m_range = params_config["m_range"]

        # NOTE: translated to English.
        start, end, step = map(int, n_range.split(":"))
        n_lengths = list(range(start, end + 1, step))

        # NOTE: translated to English.
        start, end, step = map(int, m_range.split(":"))
        m_lengths = list(range(start, end + 1, step))

        param_list = []
        if strat_idx in [1, 2, 3, 4]:
            for n in n_lengths:
                for m in m_lengths:
                    # NOTE: translated to English.
                    if n <= m:
                        param = IndicatorParams("HL")
                        param.add_param("n_length", n)
                        param.add_param("m_length", m)
                        param.add_param("strat_idx", strat_idx)
                        param_list.append(param)
        else:
            raise ValueError("strat_idx 必須由 UserInterface 明確指定且有效")
        return param_list

    def generate_signals(
        self, predictor: Optional[str] = None
    ) -> Tuple[np.ndarray, np.ndarray]:  # pylint: disable=unused-argument

        n_length = self.params.get_param("n_length")  # NOTE: translated to English.
        m_length = self.params.get_param("m_length")  # NOTE: translated to English.
        strat_idx = self.params.get_param("strat_idx")  # NOTE: translated to English.

        if n_length is None or m_length is None or strat_idx is None:
            raise ValueError("n_length, m_length, strat_idx 參數必須由外部提供")

        # NOTE: translated to English.
        if n_length > m_length:
            raise ValueError(f"連續天數({n_length})不能大於歷史回看天數({m_length})")

        # NOTE: translated to English.
        min_required_length = (
            m_length + n_length - 1
        )  # NOTE: translated to English.
        if len(self.data) < min_required_length:
            raise ValueError(
                f"數據長度不足：需要至少{min_required_length}個數據點，當前只有{len(self.data)}個"
            )

        # NOTE: translated to English.
        if predictor is None:
            predictor_series = self.data["Close"]
            self.logger.warning("未指定預測因子，使用 Close 價格作為預測因子")
        else:
            if predictor in self.data.columns:
                predictor_series = self.data[predictor]
            else:
                raise ValueError(
                    f"預測因子 '{predictor}' 不存在於數據中，可用欄位: {list(self.data.columns)}"
                )

        # NOTE: translated to English.
        if NUMBA_AVAILABLE:
            # NOTE: translated to English.
            predictor_values = predictor_series.values.astype(np.float64)

            # NOTE: translated to English.
            predictor_values = np.nan_to_num(predictor_values, nan=0.0)

            # NOTE: translated to English.
            signal_values = _generate_hl_signals_njit(
                predictor_values, n_length, m_length, strat_idx
            )

            # NOTE: translated to English.
            signal = pd.Series(signal_values, index=self.data.index)

        # NOTE: translated to English.
        signal.iloc[: min_required_length - 1] = 0

        return signal

    def get_min_valid_index(self) -> int:
        n_length = self.params.get_param("n_length")  # NOTE: translated to English.
        m_length = self.params.get_param("m_length")  # NOTE: translated to English.
        if n_length is None or m_length is None:
            raise ValueError("n_length 和 m_length 參數必須由外部提供")
        # NOTE: translated to English.
        return m_length + n_length - 2

    # NOTE: translated to English.

    @staticmethod
    def vectorized_calculate_hl_signals(  # pylint: disable=too-complex
        tasks: List[Tuple[int, Dict[str, Any]]],
        predictor: Optional[str],
        signals_matrix: np.ndarray,
        global_hl_cache: Optional[Dict[str, Any]] = None,
        data: Optional[pd.DataFrame] = None,
    ) -> np.ndarray:

        if data is None:
            raise ValueError("data參數必須提供")

        if global_hl_cache is None:
            global_hl_cache = {}

        # NOTE: translated to English.
        n_lengths = []
        m_lengths = []
        strat_indices = []
        task_indices = []
        indicator_indices = []

        for task_idx, indicator_idx, param in tasks:
            n_length = param.get_param("n_length")
            m_length = param.get_param("m_length")
            strat_idx = param.get_param("strat_idx", 1)
            if n_length is not None and m_length is not None:
                # NOTE: translated to English.
                if n_length <= m_length:
                    n_lengths.append(n_length)
                    m_lengths.append(m_length)
                    strat_indices.append(strat_idx)
                    task_indices.append(task_idx)
                    indicator_indices.append(indicator_idx)

        if not n_lengths:
            return

        # NOTE: translated to English.
        predictor_values = data[predictor].values.astype(np.float64)
        predictor_values = np.nan_to_num(predictor_values, nan=0.0)

        # NOTE: translated to English.
        for i, (n_length, m_length, strat_idx, task_idx, indicator_idx) in enumerate(
            zip(n_lengths, m_lengths, strat_indices, task_indices, indicator_indices)
        ):
            try:
                # NOTE: translated to English.
                signal_values = _generate_hl_signals_njit(
                    predictor_values, n_length, m_length, strat_idx
                )

                # NOTE: translated to English.
                # NOTE: translated to English.
                min_required_length = m_length + n_length - 1
                min_signal_start = max(min_required_length - 1, n_length - 1)
                signal_values[:min_signal_start] = 0.0

                # NOTE: translated to English.
                signals_matrix[:, task_idx, indicator_idx] = signal_values

            except Exception as e:
                # NOTE: translated to English.
                if hasattr(HLIndicator, "logger"):
                    HLIndicator.logger.warning(
                        f"HL突破信號生成失敗 (task_idx={task_idx}, indicator_idx={indicator_idx}): {e}"
                    )
                signals_matrix[:, task_idx, indicator_idx] = 0

    # NOTE: translated to English.
    # NOTE: translated to English.
