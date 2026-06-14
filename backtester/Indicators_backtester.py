

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .IndicatorParams_backtester import IndicatorParams
from .IndicatorManifestRegistry_backtester import IndicatorManifestRegistry

# NOTE: translated to English.
logger = logging.getLogger("lo2cin4bt")

pd.set_option("future.no_silent_downcasting", True)

# NOTE: translated to English.
try:
    from numba import njit

    NUMBA_AVAILABLE = True
except ImportError:
    NUMBA_AVAILABLE = False
    print("Numba 未安裝，將使用標準 Python 計算。建議安裝 numba 以獲得更好的性能。")

# NOTE: translated to English.
if NUMBA_AVAILABLE:

    @njit(fastmath=True)
    def _combine_signals_njit(signals_list: List[np.ndarray]) -> np.ndarray:  # pylint: disable=unused-argument

        if len(signals_list) == 0:
            return np.zeros(0)

        n = len(signals_list[0])
        result = np.zeros(n)

        for i in range(n):
            for signal in signals_list:
                if i < len(signal):
                    result[i] += signal[i]

        return result


# NOTE: translated to English.


class IndicatorsBacktester:


    def __init__(self, logger: Optional[logging.Logger] = None):  # pylint: disable=unused-argument
        self.logger = logger or logging.getLogger("IndicatorsBacktester")
        self.indicator_map = {
            "ma": "MovingAverage_Indicator_backtester",
            # NOTE: translated to English.
        }

        # NOTE: translated to English.
        self.new_indicators = {
            "MA": "MovingAverage_Indicator_backtester",
            "BOLL": "BollingerBand_Indicator_backtester",
            "HL": "HL_Indicator_backtester",
            "PERC": "Percentile_Indicator_backtester",
            "VALUE": "VALUE_Indicator_backtester",
            "NDAY": "NDayCycle_Indicator_backtester",
        }

        manifest_data = IndicatorManifestRegistry(self.logger).load()
        manifest_alias_map = manifest_data.get("alias_map", {})
        manifest_modules = manifest_data.get("family_modules", {})
        self.manifest_index = manifest_data.get("manifest_index", {})
        self.family_backend_specs = manifest_data.get("family_backend_specs", {})
        if isinstance(manifest_modules, dict):
            self.new_indicators.update(
                {
                    key: value
                    for key, value in manifest_modules.items()
                    if isinstance(key, str) and isinstance(value, str) and value
                }
            )

        # NOTE: translated to English.
        if isinstance(manifest_alias_map, dict) and manifest_alias_map:
            self.indicator_alias_map = manifest_alias_map
        else:
            self.indicator_alias_map = self._build_indicator_alias_map_legacy()

    def _build_indicator_alias_map_legacy(self) -> Dict[str, Tuple[str, int]]:  # pylint: disable=too-complex
        alias_map = {}
        # MA
        try:
            module = importlib.import_module(
                "backtester.MovingAverage_Indicator_backtester"
            )
            if hasattr(module, "MovingAverageIndicator"):
                descs = module.MovingAverageIndicator.get_strategy_descriptions()
                for code, desc in descs.items():
                    # code: 'MA1'~'MA12'
                    idx = int(str(code).replace("MA", ""))
                    alias_map[code.upper()] = ("MA", idx)
        except Exception as e:
            self.logger.warning(f"無法獲取MA指標描述: {e}")
        # BOLL
        try:
            module = importlib.import_module(
                "backtester.BollingerBand_Indicator_backtester"
            )
            if hasattr(module, "BollingerBandIndicator") and hasattr(
                module.BollingerBandIndicator, "STRATEGY_DESCRIPTIONS"
            ):
                for i, desc in enumerate(
                    module.BollingerBandIndicator.STRATEGY_DESCRIPTIONS, 1
                ):
                    if i <= 4:
                        alias_map[f"BOLL{i}"] = ("BOLL", i)
        except Exception as e:
            self.logger.warning(f"無法獲取BOLL指標描述: {e}")
        # HL
        try:
            module = importlib.import_module("backtester.HL_Indicator_backtester")
            if hasattr(module, "HLIndicator") and hasattr(
                module.HLIndicator, "STRATEGY_DESCRIPTIONS"
            ):
                for i, desc in enumerate(module.HLIndicator.STRATEGY_DESCRIPTIONS, 1):
                    if i <= 4:
                        alias_map[f"HL{i}"] = ("HL", i)
        except Exception as e:
            self.logger.warning(f"無法獲取HL指標描述: {e}")
        # PERC
        try:
            module = importlib.import_module(
                "backtester.Percentile_Indicator_backtester"
            )
            if hasattr(module, "PercentileIndicator") and hasattr(
                module.PercentileIndicator, "STRATEGY_DESCRIPTIONS"
            ):
                for i, desc in enumerate(
                    module.PercentileIndicator.STRATEGY_DESCRIPTIONS, 1
                ):
                    if i <= 6:
                        alias_map[f"PERC{i}"] = ("PERC", i)
        except Exception as e:
            self.logger.warning(f"無法獲取PERC指標描述: {e}")
        # VALUE
        try:
            module = importlib.import_module("backtester.VALUE_Indicator_backtester")
            if hasattr(module, "VALUEIndicator") and hasattr(
                module.VALUEIndicator, "STRATEGY_DESCRIPTIONS"
            ):
                for i, desc in enumerate(
                    module.VALUEIndicator.STRATEGY_DESCRIPTIONS, 1
                ):
                    if i <= 6:
                        alias_map[f"VALUE{i}"] = ("VALUE", i)
        except Exception as e:
            self.logger.warning(f"無法獲取VALUE指標描述: {e}")

        # NDAY
        try:
            module = importlib.import_module("backtester.NDayCycle_Indicator_backtester")
            if hasattr(module, "NDayCycleIndicator"):
                descs = module.NDayCycleIndicator.get_strategy_descriptions()
                for code, desc in descs.items():
                    idx = int(str(code).replace("NDAY", ""))
                    alias_map[code.upper()] = ("NDAY", idx)
        except Exception as e:
            self.logger.warning(f"無法獲取NDAY指標描述: {e}")

        return alias_map

    def get_all_indicator_aliases(self) -> List[str]:

        return list(self.indicator_alias_map.keys())

    def get_indicator_params(  # pylint: disable=unused-argument
        self, indicator_type: str, params_config: Optional[dict] = None
    ) -> List[Any]:

        alias = self.indicator_alias_map.get(indicator_type.upper())
        if alias:
            main_type, strat_idx = alias
            indicator_cls = self._load_indicator_class(main_type)
            if hasattr(indicator_cls, "get_params"):
                actual_strat_idx = strat_idx
                if params_config and "strat_idx" in params_config:
                    actual_strat_idx = params_config["strat_idx"]
                return indicator_cls.get_params(actual_strat_idx, params_config)
            raise ValueError(f"Indicator {indicator_type} does not expose get_params()")
        raise ValueError(f"Unknown indicator type: {indicator_type}")

    def run_indicator(
        self, indicator_name: str, data: pd.DataFrame, params: Dict[str, Any]
    ) -> np.ndarray:  # pylint: disable=unused-argument

        if indicator_name not in self.indicator_map:
            raise ValueError(f"未知指標: {indicator_name}")
        module_name = self.indicator_map[indicator_name]
        module = importlib.import_module(f"backtester.{module_name}")
        # NOTE: translated to English.
        if hasattr(module, "MovingAverageIndicator"):
            indicator_cls = getattr(module, "MovingAverageIndicator")
        else:
            indicator_cls = getattr(module, "Indicator")
        indicator = indicator_cls(data, params, logger=self.logger)
        return indicator.generate_signals(params.get("predictor"))

    # NOTE: translated to English.
    def get_available_indicators(self) -> List[str]:  # pylint: disable=too-complex

        # MA
        try:
            module = importlib.import_module(
                "backtester.MovingAverage_Indicator_backtester"
            )
            if hasattr(module, "MovingAverageIndicator"):
                descs = module.MovingAverageIndicator.get_strategy_descriptions()
                for code, desc in descs:
                    indicator_descs[code] = desc
        except Exception as e:
            self.logger.warning(f"無法獲取MA指標描述: {e}")
        # BOLL
        try:
            module = importlib.import_module(
                "backtester.BollingerBand_Indicator_backtester"
            )
            if hasattr(module, "BollingerBandIndicator") and hasattr(
                module.BollingerBandIndicator, "STRATEGY_DESCRIPTIONS"
            ):
                for i, desc in enumerate(
                    module.BollingerBandIndicator.STRATEGY_DESCRIPTIONS, 1
                ):
                    indicator_descs[f"BOLL{i}"] = desc
        except Exception as e:
            self.logger.warning(f"無法獲取BOLL指標描述: {e}")
        # PERC (added)
        try:
            module = importlib.import_module(
                "backtester.Percentile_Indicator_backtester"
            )
            if hasattr(module, "PercentileIndicator") and hasattr(
                module.PercentileIndicator, "STRATEGY_DESCRIPTIONS"
            ):
                for i, desc in enumerate(
                    module.PercentileIndicator.STRATEGY_DESCRIPTIONS, 1
                ):
                    indicator_descs[f"PERC{i}"] = desc
        except Exception as e:
            self.logger.warning(f"無法獲取PERC指標描述: {e}")
        # NOTE: translated to English.
        print("\n可用技術指標與說明：")
        for code, desc in indicator_descs.items():
            print(f"{code}: {desc}")
        return list(self.new_indicators.keys())

    def calculate_signals(  # pylint: disable=unused-argument
        self,
        indicator_type: str,
        data: pd.DataFrame,
        params: "IndicatorParams",
        predictor: Optional[str] = None,
        entry_signal: Optional[pd.Series] = None,
    ) -> np.ndarray:

        # NOTE: translated to English.
        if indicator_type == "MA":
            signals = self._calculate_ma_signals(data, params, predictor)
        elif indicator_type == "BOLL":
            signals = self._calculate_boll_signals(data, params, predictor)
        elif indicator_type == "HL":
            signals = self._calculate_hl_signals(data, params, predictor)
        elif indicator_type == "VALUE":
            signals = self._calculate_value_signals(data, params, predictor)
        elif indicator_type == "PERC":
            signals = self._calculate_percentile_signals(data, params, predictor)
        elif indicator_type == "NDAY":
            signals = self._calculate_nday_signals(data, params, predictor)
        elif indicator_type in self.family_backend_specs:
            signals = self._calculate_manifest_indicator_signals(
                indicator_type, data, params, predictor
            )
        else:
            raise ValueError(f"Unknown indicator type: {indicator_type}")

        return signals

    def _calculate_manifest_indicator_signals(
        self,
        indicator_type: str,
        data: pd.DataFrame,
        params: "IndicatorParams",
        predictor: Optional[str] = None,
    ) -> np.ndarray:
        indicator_cls = self._load_indicator_class(indicator_type)
        indicator = indicator_cls(data, params, logger=self.logger)
        signals = indicator.generate_signals(predictor)
        return np.asarray(signals, dtype=np.float64)

    def _load_indicator_class(self, family_code: str):
        backend_spec = self.family_backend_specs.get(family_code, {})
        entrypoint = str(backend_spec.get("entrypoint", "")).strip()
        if not entrypoint:
            module_name = self.new_indicators.get(family_code)
            if not module_name:
                raise ValueError(f"indicator family backend not registered: {family_code}")
            module = importlib.import_module(f"backtester.{module_name}")
            indicator_cls_name = self._resolve_indicator_class_name(family_code, "")
            if not hasattr(module, indicator_cls_name):
                raise ValueError(f"indicator class not found for family {family_code}")
            return getattr(module, indicator_cls_name)

        module_ref, _, class_name = entrypoint.partition(":")
        module = self._load_indicator_backend_module(family_code, module_ref, backend_spec)
        indicator_cls_name = self._resolve_indicator_class_name(family_code, class_name)
        if not hasattr(module, indicator_cls_name):
            raise ValueError(
                f"indicator class '{indicator_cls_name}' not found for family {family_code}"
            )
        return getattr(module, indicator_cls_name)

    def _load_indicator_backend_module(
        self, family_code: str, module_ref: str, backend_spec: Dict[str, Any]
    ):
        artifact_full_path = str(backend_spec.get("artifact_full_path", "")).strip()
        if artifact_full_path:
            path = Path(artifact_full_path)
            if not path.exists():
                raise ValueError(
                    f"indicator artifact_path not found for family {family_code}: {path}"
                )
            module_name = self._build_extension_module_name(family_code, path)
            cached_module = sys.modules.get(module_name)
            if cached_module is not None:
                return cached_module
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ValueError(
                    f"failed to load indicator artifact for family {family_code}: {path}"
                )
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            return module

        if not module_ref:
            raise ValueError(
                f"indicator entrypoint missing module path for family {family_code}"
            )
        return importlib.import_module(module_ref)

    @staticmethod
    def _build_extension_module_name(family_code: str, path: Path) -> str:
        safe_stem = "".join(ch if ch.isalnum() else "_" for ch in path.stem)
        safe_family = "".join(ch if ch.isalnum() else "_" for ch in family_code.lower())
        return f"lo2cin4bt_ext_{safe_family}_{safe_stem}_{abs(hash(str(path.resolve())))}"

    @staticmethod
    def _resolve_indicator_class_name(family_code: str, explicit_name: str) -> str:
        if explicit_name:
            return explicit_name
        indicator_cls_name_map = {
            "MA": "MovingAverageIndicator",
            "BOLL": "BollingerBandIndicator",
            "HL": "HLIndicator",
            "PERC": "PercentileIndicator",
            "VALUE": "VALUEIndicator",
            "NDAY": "NDayCycleIndicator",
        }
        return indicator_cls_name_map.get(
            family_code, family_code.capitalize() + "Indicator"
        )

    def _calculate_ma_signals(
        self, data: pd.DataFrame, params: "IndicatorParams", predictor: Optional[str] = None
    ) -> np.ndarray:  # pylint: disable=unused-argument
        try:
            # NOTE: translated to English.
            module = importlib.import_module(
                "backtester.MovingAverage_Indicator_backtester"
            )
            indicator_cls = getattr(module, "MovingAverageIndicator")

            indicator = indicator_cls(data, params, logger=self.logger)

            signals = indicator.generate_signals(predictor)

            return signals
        except Exception:
            # NOTE: translated to English.
            import traceback

            traceback.print_exc()
            raise

    def _calculate_boll_signals(
        self, data: pd.DataFrame, params: "IndicatorParams", predictor: Optional[str] = None
    ) -> np.ndarray:  # pylint: disable=unused-argument
        try:
            # NOTE: translated to English.
            module = importlib.import_module(
                "backtester.BollingerBand_Indicator_backtester"
            )
            indicator_cls = getattr(module, "BollingerBandIndicator")

            indicator = indicator_cls(data, params, logger=self.logger)

            signals = indicator.generate_signals(predictor)

            return signals
        except Exception:
            # NOTE: translated to English.
            import traceback

            traceback.print_exc()
            raise

    def _calculate_hl_signals(
        self, data: pd.DataFrame, params: "IndicatorParams", predictor: Optional[str] = None
    ) -> np.ndarray:  # pylint: disable=unused-argument
        try:
            # NOTE: translated to English.
            module = importlib.import_module("backtester.HL_Indicator_backtester")
            indicator_cls = getattr(module, "HLIndicator")

            indicator = indicator_cls(data, params, logger=self.logger)

            signals = indicator.generate_signals(predictor)

            return signals
        except Exception:
            # NOTE: translated to English.
            import traceback

            traceback.print_exc()
            raise

    def _calculate_value_signals(
        self, data: pd.DataFrame, params: "IndicatorParams", predictor: Optional[str] = None
    ) -> np.ndarray:  # pylint: disable=unused-argument
        try:
            # NOTE: translated to English.
            module = importlib.import_module("backtester.VALUE_Indicator_backtester")
            indicator_cls = getattr(module, "VALUEIndicator")

            indicator = indicator_cls(data, params, logger=self.logger)

            signals = indicator.generate_signals(predictor)

            return signals
        except Exception:
            # NOTE: translated to English.
            import traceback

            traceback.print_exc()
            raise

    def _calculate_percentile_signals(
        self, data: pd.DataFrame, params: "IndicatorParams", predictor: Optional[str] = None
    ) -> np.ndarray:  # pylint: disable=unused-argument
        try:
            # NOTE: translated to English.
            module = importlib.import_module(
                "backtester.Percentile_Indicator_backtester"
            )
            indicator_cls = getattr(module, "PercentileIndicator")

            indicator = indicator_cls(data, params, logger=self.logger)

            signals = indicator.generate_signals(predictor)

            return signals
        except Exception:
            # NOTE: translated to English.
            import traceback

            traceback.print_exc()
            raise

    def _calculate_nday_signals(
        self, data: pd.DataFrame, params: "IndicatorParams", predictor: Optional[str] = None
    ) -> np.ndarray:  # pylint: disable=unused-argument
        raise ValueError("NDAY requires the sequential engine and cannot be precomputed")
