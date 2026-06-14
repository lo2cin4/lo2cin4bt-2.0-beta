

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .IndicatorParams_backtester import IndicatorParams


class NDayCycleIndicator:
    """NDAY family metadata and parameter expansion."""

    STRATEGY_DESCRIPTIONS = [
        "NDAY1：實際開多倉後，持有滿 N 根 bar / N 日後發出平多信號（僅可作為平倉信號）",
        "NDAY2：實際開空倉後，持有滿 N 根 bar / N 日後發出平空信號（僅可作為平倉信號）",
    ]

    @staticmethod
    def get_strategy_descriptions() -> Dict[str, str]:
        return {
            f"NDAY{i + 1}": desc
            for i, desc in enumerate(NDayCycleIndicator.STRATEGY_DESCRIPTIONS)
        }

    @staticmethod
    def _parse_n_days(params_config: Dict[str, Any]) -> List[int]:
        raw_value = params_config.get("n_days_range", params_config.get("n_days"))
        if raw_value is None:
            raise ValueError("NDAY requires n_days or n_days_range")

        if isinstance(raw_value, int):
            values = [raw_value]
        elif isinstance(raw_value, str):
            text = raw_value.strip()
            if text.isdigit():
                values = [int(text)]
            else:
                parts = [part.strip() for part in text.split(":")]
                if len(parts) != 3 or not all(part.isdigit() for part in parts):
                    raise ValueError("NDAY n_days_range must be 'start:end:step'")
                start, end, step = map(int, parts)
                if start <= 0 or end <= 0 or step <= 0 or start > end:
                    raise ValueError("NDAY n_days_range must use positive integers")
                values = list(range(start, end + 1, step))
        else:
            raise ValueError("NDAY n_days must be an integer or range string")

        if not values or any(value <= 0 for value in values):
            raise ValueError("NDAY n_days must be positive")
        return values

    @classmethod
    def get_params(
        cls,
        strat_idx: Optional[int] = None,
        params_config: Optional[Dict[str, Any]] = None,
    ) -> List[IndicatorParams]:
        if params_config is None:
            raise ValueError("params_config 必須由 UserInterface 提供，且不得為 None")
        if strat_idx not in (1, 2):
            raise ValueError("NDAY 僅支援 NDAY1 / NDAY2")

        signal_direction = 1 if strat_idx == 1 else -1
        params_list: List[IndicatorParams] = []
        for n_days in cls._parse_n_days(params_config):
            params = IndicatorParams("NDAY")
            params.add_param("n_days", int(n_days))
            params.add_param("signal_direction", signal_direction)
            params.add_param("strat_idx", int(strat_idx))
            params.add_param("mode", "stateful")
            params_list.append(params)
        return params_list

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def generate_signals(self, *_args: Any, **_kwargs: Any):
        raise ValueError("NDAY requires the sequential engine and cannot be precomputed")
