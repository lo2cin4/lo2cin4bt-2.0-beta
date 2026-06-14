"""
Base_backtester.py

【功能說明】
------------------------------------------------------------
本模組為 Lo2cin4BT 回測框架的「回測流程協調器」，負責協調數據載入、用戶互動、回測執行、結果導出等全流程。
- 負責主流程調用、用戶參數收集、回測結果摘要與導出
- 提供完整的 CLI 互動界面，支援多策略配置與參數驗證
- 整合 Rich Panel 美化顯示，提供步驟跟蹤與進度提示

【流程與數據流】
------------------------------------------------------------
- 主流程：數據載入 → 用戶互動 → 回測執行 → 結果導出
- 各模組間數據流明確，流程如下：

```mermaid
flowchart TD
    A[main.py] -->|調用| B(BaseBacktester)
    B -->|載入數據| C[DataImporter]
    B -->|用戶互動| D[UserInterface]
    B -->|執行回測| E[NodeIR/native runtime]
    E -->|產生信號| F[Indicators]
    E -->|模擬交易| G[TradeSimulator]
    B -->|導出結果| H[TradeRecordExporter]
```

【維護與擴充重點】
------------------------------------------------------------
- 新增流程步驟、結果欄位、參數顯示時，請同步更新 run/_export_results/頂部註解
- 若參數結構有變動，需同步更新 IndicatorParams、TradeRecordExporter 等依賴模組
- 新增/修改流程、結果格式、參數顯示時，務必同步更新本檔案與所有依賴模組
- CLI 互動邏輯與 Rich Panel 顯示需保持一致
- 用戶輸入驗證與錯誤處理需完善

【常見易錯點】
------------------------------------------------------------
- 結果摘要顯示邏輯未同步更新，導致參數顯示錯誤
- 用戶互動流程與主流程不同步，導致參數遺漏
- 指標參數驗證邏輯不完整，導致回測失敗
- 預設策略配置與用戶自定義策略衝突

【錯誤處理】
------------------------------------------------------------
- 參數驗證失敗時提供詳細錯誤訊息
- 用戶輸入錯誤時提供重新輸入選項
- 流程執行失敗時提供診斷建議
- 數據載入失敗時提供備用方案

【範例】
------------------------------------------------------------
- 執行完整回測流程：BaseBacktester().run()
- 導出回測結果摘要：_export_results(config)
- 用戶配置收集：get_user_config(predictors)

【與其他模組的關聯】
------------------------------------------------------------
- 由 main.py 調用，協調 DataImporter、UserInterface、NodeIR/native runtime、TradeRecordExporter
- 參數結構依賴 IndicatorParams
- 指標配置依賴 IndicatorsBacktester
- 結果導出依賴 TradeRecordExporter_backtester

【版本與變更記錄】
------------------------------------------------------------
- v1.0: 初始版本，定義基本流程
- v1.1: 新增 Rich Panel 顯示和步驟跟蹤
- v1.2: 重構為模組化架構，支援多指標組合
- Version 2.0: 整合向量化回測引擎，優化性能
- Version 2.1: 完善 CLI 互動與參數驗證

【參考】
------------------------------------------------------------
- 詳細流程規範如有變動，請同步更新本註解與 README
- 其他模組如有依賴本模組的行為，請於對應模組頂部註解標明
- CLI 美化規範請參考專案記憶體中的用戶偏好設定
"""

import logging
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

import pandas as pd


from .DataImporter_backtester import DataImporter
from .Indicators_backtester import IndicatorsBacktester
from .TradeRecordExporter_backtester import TradeRecordExporter_backtester
from .utils.ConsoleUtils_utils_backtester import get_console
from utils import show_error, show_step_panel

logger = logging.getLogger("lo2cin4bt")
console = get_console()
BacktestEngine = None


def convert_single_value_to_range(value: str) -> str:
    """
    將單一數值轉換為範圍格式

    Args:
        value: 輸入值，可能是單一數值或已經是範圍格式

    Returns:
        範圍格式字串 (如 "20:20:1" 或保持原格式)
    """
    if not isinstance(value, str):
        return str(value)

    value = value.strip()

    # NOTE: translated to English.
    if ":" in value:
        return value

    # NOTE: translated to English.
    if value.isdigit():
        single_value = int(value)
        return f"{single_value}:{single_value}:1"

    # NOTE: translated to English.
    return value

DEFAULT_LONG_STRATEGY_PAIRS = [
    ("MA1", "MA4"),
    (["MA1", "MA9"], "MA4"),
    ("BOLL1", "BOLL4"),
    ("BOLL3", "BOLL2"),
    ("HL1", "HL4"),
    ("PERC1", "PERC4"),
    ("PERC3", "PERC2"),
    ("HL1", "HL4"),
]

DEFAULT_SHORT_STRATEGY_PAIRS = [
    ("MA4", "MA1"),
    (["MA4", "MA12"], "MA1"),
    ("BOLL4", "BOLL1"),
    ("BOLL2", "BOLL3"),
    ("HL4", "HL1"),
    ("PERC4", "PERC1"),
    ("PERC2", "PERC3"),
    ("HL4", "HL1"),
]

DEFAULT_ALL_STRATEGY_PAIRS = DEFAULT_LONG_STRATEGY_PAIRS + DEFAULT_SHORT_STRATEGY_PAIRS

"""
本模組所有參數詢問Panel（如MA長度、BOLL長度、NDAY範圍等）
- 顯示時自動將半形冒號 : 換成全形冒號 ：，避免Windows終端機將 :100: 等誤判為emoji。
- 用戶輸入後自動將全形冒號 ： 轉回半形冒號 : 再做驗證與處理。
- 這樣可確保CLI美觀且不影響內部邏輯。
"""


class BaseBacktester:
    """
    重構後的回測框架核心協調器，只負責調用各模組
    """

    def __init__(
        self,
        data: pd.DataFrame | None = None,
        frequency: str | None = None,
        logger: Optional[logging.Logger] = None,
        predictor_file_name: str | None = None,
        symbol: str | None = None,
    ) -> None:
        self.data = data
        self.frequency = frequency
        self.logger = logger or logging.getLogger("BaseBacktester")
        self.results: List[Any] = []
        self.data_importer = DataImporter()
        self.predictor_file_name = predictor_file_name
        self.symbol = symbol or "X"
        self.predictor_column: Optional[str] = None  # NOTE: translated to English.
        self.indicators_helper = IndicatorsBacktester(logger=self.logger)
        self.backtest_engine: Optional[Any] = None
        self.exporter = None

    def run(self, predictor_col: Optional[str] = None) -> None:
        """
        主執行函數，協調預測因子選擇、用戶配置獲取、回測執行與結果導出。
        """
        # Get user config (includes Step 1-4)
        config = self.get_user_config([])

        if not config:
            show_error("BACKTESTER", "用戶取消操作，程式終止。")
            return

        # NOTE: translated to English.
        self._print_step_panel(5, "開始執行回測引擎，生成回測任務並並行執行")

        # NOTE: translated to English.
        if BacktestEngine is None:
            raise RuntimeError("Public legacy backtest engine path has been removed; use strategy_run.")
        self.backtest_engine = BacktestEngine(self.data, self.frequency or "1D", self.logger, getattr(self, 'symbol', 'X'))
        self.results = self.backtest_engine.run_backtests(config)

        # NOTE: translated to English.
        self._export_results(config)
        self.logger.info("Backtester run finished.")

    @staticmethod
    def get_steps() -> List[str]:
        return [
            "選擇要用於回測的預測因子",
            "選擇回測開倉及平倉指標",
            "輸入指標參數",
            "輸入回測環境參數",
            "開始回測[自動]",
            "導出回測結果",
        ]

    @staticmethod
    def print_step_panel(current_step: int, desc: str = "") -> None:
        steps = BaseBacktester.get_steps()
        show_step_panel("BACKTESTER", current_step, steps, desc)

    def _print_step_panel(self, current_step: int, desc: str = "") -> None:
        # NOTE: translated to English.
        BaseBacktester.print_step_panel(current_step, desc)

    def _select_predictor(self, predictor_col: Optional[str] = None) -> str:
        """Resolve the predictor column from config, attributes, or data defaults."""
        if self.data is None:
            raise ValueError("Backtester data is not loaded")

        all_predictors = [
            col for col in self.data.columns if col not in ["Time", "High", "Low"]
        ]
        config = getattr(self, "backtest_config", {}) or {}
        selected = (
            predictor_col
            or config.get("predictor_column")
            or getattr(self, "predictor_column", None)
            or getattr(self, "predictor", None)
            or getattr(self, "selected_predictor", None)
        )
        if isinstance(selected, list):
            selected = selected[0] if selected else None
        if selected in all_predictors:
            self.predictor_column = str(selected)
            return str(selected)

        columns = list(self.data.columns)
        if "close_logreturn" in columns:
            idx = columns.index("close_logreturn")
            if idx + 1 < len(columns):
                default = columns[idx + 1]
            elif "Close" in columns:
                default = "Close"
            else:
                default = all_predictors[0] if all_predictors else None
        elif "Close" in columns:
            default = "Close"
        else:
            default = all_predictors[0] if all_predictors else None

        if default not in all_predictors:
            default = all_predictors[0] if all_predictors else None
        if default is None:
            raise ValueError("No predictor column is available")
        self.predictor_column = str(default)
        return str(default)

    def _export_results(self, config: Dict) -> None:
        """導出結果"""
        if not self.results:
            print("無結果可導出")
            return

        # NOTE: translated to English.
        self._print_step_panel(6, "將回測結果導出為檔案格式")

        # NOTE: translated to English.
        exporter = TradeRecordExporter_backtester(
            trade_records=pd.DataFrame(),
            frequency=self.frequency or "1D",
            results=self.results,
            data=self.data,
            Backtest_id=config.get("Backtest_id", ""),
            predictor_file_name=self.predictor_file_name,
            predictor_column=self.predictor_column,
            symbol=self.symbol,  # NOTE: translated to English.
            **config["trading_params"],
        )

        # NOTE: translated to English.
        exporter.export_to_parquet()

        # NOTE: translated to English.
        exporter.display_backtest_summary()

    def get_user_config(self, predictors: List[str]) -> Dict:
        """Return a deterministic backtest config without interactive prompts."""
        config = getattr(self, "backtest_config", None)
        if isinstance(config, dict) and config:
            return config

        selected_predictor = (
            getattr(self, "predictor_column", None)
            or getattr(self, "predictor", None)
            or getattr(self, "selected_predictor", None)
            or (predictors[0] if predictors else "Close")
        )
        if isinstance(selected_predictor, list):
            predictors_list = selected_predictor
        else:
            predictors_list = [str(selected_predictor)]

        condition_pairs = getattr(self, "condition_pairs", None)
        if not condition_pairs:
            condition_pairs = DEFAULT_ALL_STRATEGY_PAIRS

        indicator_params = getattr(self, "indicator_params", None) or {}
        trading_params = getattr(self, "trading_params", None) or {
            "transaction_cost": 0.001,
            "slippage": 0.0005,
            "trade_delay": 1,
            "trade_price": "open",
        }

        return {
            "condition_pairs": condition_pairs,
            "indicator_params": indicator_params,
            "predictors": predictors_list,
            "trading_params": trading_params,
            "initial_capital": getattr(self, "initial_capital", 1000000),
        }

    def _display_available_indicators(self) -> str:  # pylint: disable=too-complex
        """動態分組指標顯示，返回說明內容"""
        all_aliases = self.indicators_helper.get_all_indicator_aliases()
        indicator_descs = {}
        try:
            module = __import__(
                "backtester.MovingAverage_Indicator_backtester",
                fromlist=["MovingAverageIndicator"],
            )
            if hasattr(module, "MovingAverageIndicator"):
                descs = module.MovingAverageIndicator.get_strategy_descriptions()
                for code, desc in descs.items():
                    indicator_descs[code] = desc
        except Exception as e:
            self.logger.warning(f"無法獲取MA指標描述: {e}")
        try:
            module = __import__(
                "backtester.BollingerBand_Indicator_backtester",
                fromlist=["BollingerBandIndicator"],
            )
            if hasattr(module, "BollingerBandIndicator") and hasattr(
                module.BollingerBandIndicator, "STRATEGY_DESCRIPTIONS"
            ):
                for i, desc in enumerate(
                    module.BollingerBandIndicator.STRATEGY_DESCRIPTIONS, 1
                ):
                    if i <= 4:
                        indicator_descs[f"BOLL{i}"] = desc
        except Exception as e:
            self.logger.warning(f"無法獲取BOLL指標描述: {e}")
        # HL
        try:
            module = __import__(
                "backtester.HL_Indicator_backtester", fromlist=["HLIndicator"]
            )
            if hasattr(module, "HLIndicator") and hasattr(
                module.HLIndicator, "STRATEGY_DESCRIPTIONS"
            ):
                for i, desc in enumerate(module.HLIndicator.STRATEGY_DESCRIPTIONS, 1):
                    if i <= 4:
                        indicator_descs[f"HL{i}"] = desc
        except Exception as e:
            self.logger.warning(f"無法獲取HL指標描述: {e}")
        indicator_descs["NDAY1"] = "NDAY1：開倉後N日做多（僅可作為平倉信號）"
        indicator_descs["NDAY2"] = "NDAY2：開倉後N日做空（僅可作為平倉信號）"
        # PERC
        try:
            module = __import__(
                "backtester.Percentile_Indicator_backtester",
                fromlist=["PercentileIndicator"],
            )
            if hasattr(module, "PercentileIndicator"):
                descs = module.PercentileIndicator.get_strategy_descriptions()
                for code, desc in descs.items():
                    indicator_descs[code] = desc
        except Exception as e:
            self.logger.warning(f"無法獲取PERC指標描述: {e}")
        # VALUE
        try:
            module = __import__(
                "backtester.VALUE_Indicator_backtester", fromlist=["VALUEIndicator"]
            )
            if hasattr(module, "VALUEIndicator"):
                descs = module.VALUEIndicator.get_strategy_descriptions()
                for code, desc in descs.items():
                    indicator_descs[code] = desc
        except Exception as e:
            self.logger.warning(f"無法獲取VALUE指標描述: {e}")
        # NOTE: translated to English.
        group_dict = defaultdict(list)
        for alias in all_aliases:
            m = re.match(r"^([A-Z]+)", alias)
            group = m.group(1) if m else "其他"
            group_dict[group].append(
                (alias, indicator_descs.get(alias, f"未知策略 {alias}"))
            )
        # Dynamic grouping order
        group_order = ["MA", "BOLL", "HL", "PERC", "VALUE", "NDAY"] + [
            g
            for g in sorted(group_dict.keys())
            if g not in ["MA", "BOLL", "HL", "PERC", "VALUE", "NDAY"]
        ]
        group_texts = []
        for group in group_order:
            if group in group_dict:
                group_title = f"[bold #dbac30]{group} 指標[/bold #dbac30]"
                lines = [
                    f"    [#1e90ff]{alias}[/#1e90ff]: {desc}"
                    for alias, desc in group_dict[group]
                ]
                group_texts.append(f"{group_title}\n" + "\n".join(lines))
        # NOTE: translated to English.
        desc = (
            "\n\n[bold #dbac30]說明[/bold #dbac30]\n"
            "- 此步驟用於設定回測策略的開倉與平倉條件，可同時回測多組策略。\n"
            "- 每組策略需依序輸入開倉條件、再輸入平倉條件，系統會自動組合成一個策略。\n"
            "- 可同時輸入多個開倉/平倉條件，只有全部條件同時滿足才會觸發開倉/平倉。\n"
            "- 請避免多空衝突：若開倉做多，所有開倉條件都應為做多，反之亦然，否則策略會失敗。\n"
            "- 開倉與平倉條件方向必須對立（如開倉做多，平倉應為做空），否則策略會失敗。。\n"
            "- 支援同時回測多組不同條件的策略，靈活組合。\n"
            "- 格式：先輸入開倉條件（如MA1,BOLL1），再輸入平倉條件（如 MA2,BOLL2），即可建立一組策略。\n"
            "- [bold yellow]如不確定如何選擇，建議先用預設策略體驗流程，\n"
            "  在開倉和平倉條件同時輸入'defaultlong'(長倉)/'defaultshort'(短倉)/'defaultall'(全部)即可。[/bold yellow]\n"
            "- ※ 輸入多個指標時，必須全部同時滿足才會開倉/平倉。"
        )
        content = desc + "\n\n" + "\n\n".join(group_texts)
        return content

    def _collect_condition_pairs(self) -> list:  # pylint: disable=too-complex
        """Resolve a numeric option from config, attributes, or defaults."""
        config = getattr(self, "backtest_config", {}) or {}
        condition_pairs = (
            getattr(self, "condition_pairs", None)
            or config.get("condition_pairs")
            or config.get("strategy_pairs")
        )
        if condition_pairs:
            normalized_pairs = []
            for pair in condition_pairs:
                if isinstance(pair, dict):
                    entry = pair.get("entry", [])
                    exit_ = pair.get("exit", [])
                elif isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    entry, exit_ = pair[0], pair[1]
                else:
                    continue
                entry_list = entry if isinstance(entry, list) else [entry]
                exit_list = exit_ if isinstance(exit_, list) else [exit_]
                normalized_pairs.append({"entry": entry_list, "exit": exit_list})
            if normalized_pairs:
                return normalized_pairs

        strategy_type = str(
            config.get("strategy_type")
            or config.get("default_strategy_type")
            or getattr(self, "strategy_type", "")
            or ""
        ).lower()
        if strategy_type in {"long", "defaultlong"}:
            source_pairs = DEFAULT_LONG_STRATEGY_PAIRS
        elif strategy_type in {"short", "defaultshort"}:
            source_pairs = DEFAULT_SHORT_STRATEGY_PAIRS
        else:
            source_pairs = DEFAULT_ALL_STRATEGY_PAIRS

        normalized = []
        for entry, exit_ in source_pairs:
            entry_list = entry if isinstance(entry, list) else [entry]
            exit_list = exit_ if isinstance(exit_, list) else [exit_]
            normalized.append({"entry": entry_list, "exit": exit_list})
        return normalized

    def _collect_indicator_params(self, condition_pairs: list) -> dict:  # pylint: disable=too-complex
        """Resolve an indicator strategy index from config or attributes."""
        config = getattr(self, "backtest_config", {}) or {}
        params = getattr(self, "indicator_params", None) or config.get("indicator_params") or {}
        if isinstance(params, dict):
            return params
        return {}

    def _get_indicator_input(self, prompt: str, valid_indicators: list) -> list:
        """Build indicator parameters from config, attributes, and strategy defaults."""
        config = getattr(self, "backtest_config", {}) or {}
        selection = (
            config.get("indicator_selection")
            or getattr(self, "indicator_selection", None)
            or config.get("strategy_type")
            or config.get("default_strategy_type")
            or getattr(self, "strategy_type", None)
        )
        if isinstance(selection, list):
            return [item for item in selection if item in valid_indicators]
        if isinstance(selection, str):
            selection_lower = selection.lower()
            if selection_lower in {"none", "off", "disable"}:
                return []
            if selection_lower in {"defaultlong", "long"}:
                return ["__DEFAULT_LONG__"]
            if selection_lower in {"defaultshort", "short"}:
                return ["__DEFAULT_SHORT__"]
            if selection_lower in {"defaultall", "all"}:
                return ["__DEFAULT_ALL__"]
            if selection in valid_indicators:
                return [selection]
        if valid_indicators:
            return [valid_indicators[0]]
        return []

    def _get_trading_param(self, prompt: str) -> float:
        """Parse a free-text prompt into supported backtest config fields."""
        config = getattr(self, "backtest_config", {}) or {}
        trading_params = getattr(self, "trading_params", None) or config.get("trading_params") or {}
        normalized_prompt = str(prompt).lower()

        if isinstance(trading_params, dict):
            hint_map = {
                "transaction_cost": ("transaction", "cost"),
                "slippage": ("slippage", "??"),
                "trade_delay": ("delay", "??"),
                "trade_price": ("trade_price", "price"),
            }
            for key, hints in hint_map.items():
                if any(hint in normalized_prompt for hint in hints):
                    value = trading_params.get(key)
                    if value is not None:
                        try:
                            return float(value)
                        except (TypeError, ValueError):
                            break

        if any(token in normalized_prompt for token in ("slippage", "??")):
            return 0.0005
        if any(token in normalized_prompt for token in ("transaction", "cost")):
            return 0.001
        if any(token in normalized_prompt for token in ("delay", "??")):
            return 1.0
        return 0.0

    def get_results(self) -> List[Dict]:
        """
        獲取回測結果

        Returns:
            List[Dict]: 回測結果列表，每個元素包含一個策略的回測結果
        """
        return self.results
