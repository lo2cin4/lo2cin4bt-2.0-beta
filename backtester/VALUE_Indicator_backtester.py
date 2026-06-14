

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .IndicatorParams_backtester import IndicatorParams

INDICATOR_FAMILY_GUIDE = """
VALUE 指標家族
--------------
- VALUE1-VALUE4：連續 N 根高於 / 低於門檻值
- VALUE5-VALUE6：落在數值區間內
- 常用參數：n_range, m_range, m1_range, m2_range

常用配置範例
------------
{
  "condition_pairs": [{"entry": ["VALUE1"], "exit": ["VALUE4"]}],
  "indicator_params": {
    "VALUE1_strategy_1": {"n_range": "2:5:1", "m_range": "0:0:1"},
    "VALUE4_strategy_1": {"n_range": "2:5:1", "m_range": "0:0:1"}
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
    def _generate_value_signals_njit(
        predictor_values, n_length, m_value, strat_idx
    ):  # type: ignore[no-untyped-def] # pylint: disable=unused-argument

        n = len(predictor_values)
        signals = np.zeros(n)

        # NOTE: translated to English.
        # NOTE: translated to English.
        # NOTE: translated to English.
        for i in range(n_length - 1, n):
            # NOTE: translated to English.
            if strat_idx == 1:  # NOTE: translated to English.
                all_above = True
                for j in range(i - n_length + 1, i + 1):
                    if predictor_values[j] <= m_value:
                        all_above = False
                        break
                if all_above:
                    signals[i] = 1.0

            elif strat_idx == 2:  # NOTE: translated to English.
                all_above = True
                for j in range(i - n_length + 1, i + 1):
                    if predictor_values[j] <= m_value:
                        all_above = False
                        break
                if all_above:
                    signals[i] = -1.0

            elif strat_idx == 3:  # NOTE: translated to English.
                all_below = True
                for j in range(i - n_length + 1, i + 1):
                    if predictor_values[j] >= m_value:
                        all_below = False
                        break
                if all_below:
                    signals[i] = 1.0

            elif strat_idx == 4:  # NOTE: translated to English.
                all_below = True
                for j in range(i - n_length + 1, i + 1):
                    if predictor_values[j] >= m_value:
                        all_below = False
                        break
                if all_below:
                    signals[i] = -1.0

        return signals

    @njit(fastmath=True, cache=True)  # type: ignore[misc]
    def _generate_value_range_signals_njit(  # type: ignore[no-untyped-def] # pylint: disable=unused-argument
        predictor_values, m1_value, m2_value, strat_idx
    ):

        n = len(predictor_values)
        signals = np.zeros(n)

        # NOTE: translated to English.
        if m1_value >= m2_value:
            return signals

        # NOTE: translated to English.
        for i in range(n):
            curr_val = predictor_values[i]

            # NOTE: translated to English.
            if m1_value <= curr_val <= m2_value:
                if strat_idx == 5:  # NOTE: translated to English.
                    signals[i] = 1.0
                elif strat_idx == 6:  # NOTE: translated to English.
                    signals[i] = -1.0

        return signals


class VALUEIndicator:


    STRATEGY_DESCRIPTIONS = [
        "連續 n 日升穿 m 值時做多",
        "連續 n 日升穿 m 值時做空",
        "連續 n 日跌穿 m 值時做多",
        "連續 n 日跌穿 m 值時做空",
        "在 m1 和 m2 之間做多",
        "在 m1 和 m2 之間做空",
    ]

    @staticmethod
    def get_strategy_descriptions() -> Dict[str, str]:
        # NOTE: translated to English.
        return {
            f"VALUE{i + 1}": desc
            for i, desc in enumerate(VALUEIndicator.STRATEGY_DESCRIPTIONS)
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

        if strat_idx is None:
            raise ValueError("strat_idx 必須由 UserInterface 明確指定且有效")

        param_list = []

        if strat_idx in [1, 2, 3, 4]:
            # NOTE: translated to English.
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
            m_values = list(range(start, end + 1, step))

            for n in n_lengths:
                for m in m_values:
                    param = IndicatorParams("VALUE")
                    param.add_param("n_length", n)
                    param.add_param("m_value", m)
                    param.add_param("strat_idx", strat_idx)
                    param_list.append(param)

        elif strat_idx in [5, 6]:
            # NOTE: translated to English.
            if "m1_range" not in params_config:
                raise ValueError("m1_range 必須由 UserInterface 提供")
            if "m2_range" not in params_config:
                raise ValueError("m2_range 必須由 UserInterface 提供")

            m1_range = params_config["m1_range"]
            m2_range = params_config["m2_range"]

            # NOTE: translated to English.
            start, end, step = map(int, m1_range.split(":"))
            m1_values = list(range(start, end + 1, step))

            # NOTE: translated to English.
            start, end, step = map(int, m2_range.split(":"))
            m2_values = list(range(start, end + 1, step))

            for m1 in m1_values:
                for m2 in m2_values:
                    if m1 < m2:  # NOTE: translated to English.
                        param = IndicatorParams("VALUE")
                        param.add_param("m1_value", m1)
                        param.add_param("m2_value", m2)
                        param.add_param("strat_idx", strat_idx)
                        param_list.append(param)
        else:
            raise ValueError("strat_idx 必須由 UserInterface 明確指定且有效")

        return param_list

    def generate_signals(
        self, predictor: Optional[str] = None
    ) -> Tuple[np.ndarray, np.ndarray]:  # pylint: disable=unused-argument

        strat_idx = self.params.get_param("strat_idx")

        if strat_idx is None:
            raise ValueError("strat_idx 參數必須由外部提供")

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

            if strat_idx in [1, 2, 3, 4]:
                # NOTE: translated to English.
                n_length = self.params.get_param("n_length")
                m_value = self.params.get_param("m_value")

                if n_length is None or m_value is None:
                    raise ValueError("n_length, m_value 參數必須由外部提供")

                # NOTE: translated to English.
                if len(self.data) < n_length:
                    raise ValueError(
                        f"數據長度不足：需要至少{n_length}個數據點，當前只有{len(self.data)}個"
                    )

                # NOTE: translated to English.
                signal_values = _generate_value_signals_njit(
                    predictor_values, n_length, m_value, strat_idx
                )

            elif strat_idx in [5, 6]:
                # NOTE: translated to English.
                m1_value = self.params.get_param("m1_value")
                m2_value = self.params.get_param("m2_value")

                if m1_value is None or m2_value is None:
                    raise ValueError("m1_value, m2_value 參數必須由外部提供")

                # NOTE: translated to English.
                signal_values = _generate_value_range_signals_njit(
                    predictor_values, m1_value, m2_value, strat_idx
                )
            else:
                raise ValueError(f"無效的策略索引: {strat_idx}")

            # NOTE: translated to English.
            signal = pd.Series(signal_values, index=self.data.index)

            # NOTE: translated to English.
            if strat_idx in [1, 2, 3, 4]:
                n_length = self.params.get_param("n_length")
                # NOTE: translated to English.
                min_signal_start = max(n_length - 1, 0)
                signal.iloc[:min_signal_start] = 0

            return signal
        else:
            raise RuntimeError("Numba 未安裝，無法使用 VALUE 指標")

    def get_min_valid_index(self) -> int:
        strat_idx = self.params.get_param("strat_idx")
        if strat_idx is None:
            raise ValueError("strat_idx 參數必須由外部提供")

        if strat_idx in [1, 2, 3, 4]:
            n_length = self.params.get_param("n_length")
            if n_length is None:
                raise ValueError("n_length 參數必須由外部提供")
            # NOTE: translated to English.
            return max(n_length - 1, 0)
        else:
            # NOTE: translated to English.
            return 0

    # NOTE: translated to English.

    @staticmethod
    def vectorized_calculate_value_signals(  # pylint: disable=too-complex
        tasks: List[Tuple[int, Dict[str, Any]]],
        predictor: Optional[str],
        signals_matrix: np.ndarray,
        global_value_cache: Optional[Dict[str, Any]] = None,
        data: Optional[pd.DataFrame] = None,
    ) -> np.ndarray:

        if data is None:
            raise ValueError("data參數必須提供")

        if global_value_cache is None:
            global_value_cache = {}

        # NOTE: translated to English.
        predictor_values = data[predictor].values.astype(np.float64)
        predictor_values = np.nan_to_num(predictor_values, nan=0.0)

        # NOTE: translated to English.
        for task_idx, indicator_idx, param in tasks:
            try:
                strat_idx = param.get_param("strat_idx")

                if strat_idx in [1, 2, 3, 4]:
                    # NOTE: translated to English.
                    n_length = param.get_param("n_length")
                    m_value = param.get_param("m_value")

                    if n_length is not None and m_value is not None:
                        # NOTE: translated to English.
                        signal_values = _generate_value_signals_njit(
                            predictor_values, n_length, m_value, strat_idx
                        )

                        # NOTE: translated to English.
                        signal_values[: n_length - 1] = 0.0

                        # NOTE: translated to English.
                        signals_matrix[:, task_idx, indicator_idx] = signal_values

                elif strat_idx in [5, 6]:
                    # NOTE: translated to English.
                    m1_value = param.get_param("m1_value")
                    m2_value = param.get_param("m2_value")

                    if m1_value is not None and m2_value is not None:
                        # NOTE: translated to English.
                        signal_values = _generate_value_range_signals_njit(
                            predictor_values, m1_value, m2_value, strat_idx
                        )

                        # NOTE: translated to English.
                        signals_matrix[:, task_idx, indicator_idx] = signal_values

            except Exception as e:
                # NOTE: translated to English.
                if hasattr(VALUEIndicator, "logger"):
                    VALUEIndicator.logger.warning(
                        f"VALUE突破信號生成失敗 (task_idx={task_idx}, indicator_idx={indicator_idx}): {e}"
                    )
                signals_matrix[:, task_idx, indicator_idx] = 0

    # NOTE: translated to English.
    # NOTE: translated to English.
