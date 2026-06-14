"""
ConfigLoader_autorunner.py

【功能說明】
------------------------------------------------------------
本模組負責配置文件載入功能，從 JSON 文件中讀取配置數據，
解析和轉換配置參數，為後續模組提供標準化的配置數據結構。

【流程與數據流】
------------------------------------------------------------
- 主流程：讀取文件 → 解析 JSON → 驗證數據 → 轉換格式 → 返回配置
- 數據流：文件路徑 → JSON 數據 → 配置字典 → 標準化配置

【維護與擴充重點】
------------------------------------------------------------
- 新增配置欄位時，請同步更新載入邏輯
- 若配置格式有變動，需同步更新解析邏輯
- 新增/修改配置轉換、數據驗證、錯誤處理時，務必同步更新本檔案

【常見易錯點】
------------------------------------------------------------
- JSON 解析錯誤導致配置載入失敗
- 配置數據轉換錯誤導致參數不正確
- 缺少必要配置時沒有提供預設值

【範例】
------------------------------------------------------------
- 載入單個配置：loader.load_config("config.json") -> ConfigData
- 載入多個配置：loader.load_configs(["config1.json", "config2.json"]) -> [ConfigData1, ConfigData2]
- 獲取配置摘要：loader.get_config_summary(config_data) -> dict

【與其他模組的關聯】
------------------------------------------------------------
- 被 Base_autorunner 調用，提供配置載入功能
- 依賴 json 進行配置文件解析
- 為 DataLoader、BacktestRunner 等提供配置數據

【版本與變更記錄】
------------------------------------------------------------
- v1.0: 初始版本，基本載入功能
- v1.1: 新增配置轉換和預設值處理
- v1.2: 新增 Rich Panel 顯示和調試輸出

【參考】
------------------------------------------------------------
- autorunner/DEVELOPMENT_PLAN.md
- Development_Guideline.md
- Base_autorunner.py
- config_template.json
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


from backtester.StrategyRunConfig_backtester import normalize_strategy_run_config
from autorunner.utils import get_console
from utils import show_error

console = get_console()


class ConfigData:
    """
    配置數據容器

    封裝配置文件的數據結構，提供標準化的配置訪問介面。
    """

    def __init__(self, config_dict: Dict[str, Any], file_path: str):
        """
        初始化 ConfigData

        Args:
            config_dict: 配置字典
            file_path: 配置文件路徑
        """

        self.file_path = file_path
        self.file_name = Path(file_path).name
        self.raw_config = config_dict.copy()

        # NOTE: translated to English.
        self.dataloader_config = config_dict.get("dataloader", {})
        self.backtester_config = config_dict.get("backtester", {})
        self.metricstracker_config = config_dict.get("metricstracker", {})
        self.statanalyser_config = config_dict.get("statanalyser", {})

        # NOTE: translated to English.
        dataloader_config = config_dict.get("dataloader", {})
        self.predictor_config = dataloader_config.get("predictor_config", {})

        # Keep source config path for downstream relative-path resolution.
        self.dataloader_config.setdefault("__config_file_path", self.file_path)
        self.backtester_config.setdefault("__config_file_path", self.file_path)
        self.metricstracker_config.setdefault("__config_file_path", self.file_path)
        self.statanalyser_config.setdefault("__config_file_path", self.file_path)

    def get_summary(self) -> Dict[str, Any]:
        """
        獲取配置摘要

        Returns:
            Dict[str, Any]: 配置摘要信息
        """

        config_summary = {
            "file_name": self.file_name,
            "file_path": self.file_path,
            "dataloader_source": self.dataloader_config.get("source", "unknown"),
            "backtester_pairs": len(self.backtester_config.get("condition_pairs", [])),
            "metricstracker_enabled": self.metricstracker_config.get(
                "enable_metrics_analysis", False
            ),
            "statanalyser_enabled": self.statanalyser_config.get("enabled", False),
        }

        return config_summary


class ConfigLoader:
    """
    配置文件載入器

    負責從 JSON 文件中載入配置數據，解析和轉換配置參數，
    提供標準化的配置數據結構。
    """

    def __init__(self) -> None:
        """
        初始化 ConfigLoader
        """

        # NOTE: translated to English.
        self.default_config = {
            "dataloader": {
                "source": "yfinance",
                "start_date": "2020-01-01",
            },
            "backtester": {
                "condition_pairs": [],
            },
            "metricstracker": {
                "enable_metrics_analysis": False,
            },
            "statanalyser": {
                "enabled": False,
                "target": {
                    "predictor_column": None,
                    "return_column": None,
                    "diff_mode": "none",
                },
                "tests": {},
                "report": {
                    "formats": ["md", "json"],
                    "output_dir": "outputs/statanalyser",
                    "include_plots": False,
                    "include_raw_tables": True,
                    "fail_on_error": False,
                },
            },
        }

    def load_config(self, config_file: str) -> Optional[ConfigData]:
        """
        載入單個配置文件

        Args:
            config_file: 配置文件路徑

        Returns:
            Optional[ConfigData]: 配置數據對象，如果載入失敗則返回 None
        """

        try:
            # NOTE: translated to English.
            config_dict = self._read_config_file(config_file)
            if config_dict is None:
                return None
            if config_dict.get("schema_version") == "strategy_run":
                config_dict = self._runtime_shell_from_strategy_run(config_dict, config_file)

            # NOTE: translated to English.
            merged_config = self._merge_with_defaults(config_dict)

            # NOTE: translated to English.
            processed_config = self._process_config(merged_config)

            # NOTE: translated to English.
            config_data_obj = ConfigData(processed_config, config_file)

            return config_data_obj

        except Exception as e:
            print(f"❌ [ERROR] 載入配置文件失敗: {e}")
            self._display_load_error(f"載入失敗: {e}", Path(config_file).name)
            return None

    def load_configs(self, config_files: List[str]) -> List[ConfigData]:
        """
        載入多個配置文件

        Args:
            config_files: 配置文件路徑列表

        Returns:
            List[ConfigData]: 配置數據對象列表
        """

        config_data_list = []
        for config_file in config_files:
            config_data_obj = self.load_config(config_file)
            if config_data_obj is not None:
                config_data_list.append(config_data_obj)

        return config_data_list

    def _read_config_file(self, config_file: str) -> Optional[Dict[str, Any]]:
        """
        讀取配置文件

        Args:
            config_file: 配置文件路徑

        Returns:
            Optional[Dict[str, Any]]: 配置字典，如果讀取失敗則返回 None
        """

        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config_dict = json.load(f)

            return config_dict

        except FileNotFoundError:
            print(f"❌ [ERROR] 配置文件不存在: {config_file}")
            self._display_load_error("配置文件不存在", Path(config_file).name)
            return None
        except json.JSONDecodeError as e:
            print(f"❌ [ERROR] JSON 格式錯誤: {e}")
            self._display_load_error(f"JSON 格式錯誤: {e}", Path(config_file).name)
            return None
        except Exception as e:
            print(f"❌ [ERROR] 讀取配置文件失敗: {e}")
            self._display_load_error(f"讀取失敗: {e}", Path(config_file).name)
            return None

    def _merge_with_defaults(self, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        合併預設配置

        Args:
            config_dict: 原始配置字典

        Returns:
            Dict[str, Any]: 合併後的配置字典
        """

        merged_config = copy.deepcopy(self.default_config)

        # NOTE: translated to English.
        for key, value in config_dict.items():
            if (
                key in merged_config
                and isinstance(merged_config[key], dict)
                and isinstance(value, dict)
            ):
                merged_dict_key = merged_config[key]
                value_dict = value
                if isinstance(merged_dict_key, dict) and isinstance(value_dict, dict):
                    merged_config[key] = {**merged_dict_key, **value_dict}
            else:
                merged_config[key] = value

        return merged_config

    def _process_config(self, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        處理配置數據

        Args:
            config_dict: 配置字典

        Returns:
            Dict[str, Any]: 處理後的配置字典
        """

        processed_config = config_dict.copy()

        # NOTE: translated to English.
        if "dataloader" in processed_config:
            processed_config["dataloader"] = self._process_dataloader_config(
                processed_config["dataloader"]
            )

        # NOTE: translated to English.
        if "backtester" in processed_config:
            processed_config["backtester"] = self._process_backtester_config(
                processed_config["backtester"]
            )

        # NOTE: translated to English.
        if "metricstracker" in processed_config:
            processed_config["metricstracker"] = self._process_metricstracker_config(
                processed_config["metricstracker"]
            )

        return processed_config

    def _runtime_shell_from_strategy_run(self, config_dict: Dict[str, Any], config_file: str) -> Dict[str, Any]:
        """Adapt canonical StrategyRunConfig into the existing autorunner ConfigData shape."""
        normalized = normalize_strategy_run_config(config_dict, source_path=Path(config_file))
        platform = normalized.get("platform", {}) if isinstance(normalized.get("platform"), dict) else {}
        mode = str(platform.get("strategy_mode_id") or "").strip().lower()
        workflow = str(platform.get("workflow_id") or "").strip().lower()
        data_cfg = normalized.get("data", {}) if isinstance(normalized.get("data"), dict) else {}
        universe = normalized.get("universe", {}) if isinstance(normalized.get("universe"), dict) else {}
        metadata = normalized.get("metadata", {}) if isinstance(normalized.get("metadata"), dict) else {}
        legacy_backtester = metadata.get("legacy_backtester") if isinstance(metadata.get("legacy_backtester"), dict) else {}
        dataloader = self._strategy_run_dataloader_config(normalized)
        backtester = self._strategy_run_backtester_config(normalized)
        return {
            "schema_version": "strategy_run",
            "platform": {
                "run_type": platform.get("run_type", "test"),
                "display_label": platform.get("display_label", ""),
                "strategy_mode_id": mode,
                "workflow_id": workflow,
            },
            "dataloader": dataloader,
            "backtester": {**copy.deepcopy(legacy_backtester), **backtester},
            "metricstracker": copy.deepcopy(config_dict.get("metricstracker", {})),
            "statanalyser": copy.deepcopy(config_dict.get("statanalyser", {})),
            "strategy_run_config": normalized,
            "data": copy.deepcopy(data_cfg),
            "universe": copy.deepcopy(universe),
        }

    @classmethod
    def _strategy_run_uses_internal_market_loader(cls, config: Dict[str, Any]) -> bool:
        platform = config.get("platform") if isinstance(config.get("platform"), dict) else {}
        mode = str(platform.get("strategy_mode_id") or "").strip().lower()
        return mode in {
            "single_asset_signal",
            "calendar_event_session",
            "multi_factor_entry_exit_roles",
            "multi_asset_portfolio",
            "multi_asset_trigger_selection",
            "dynamic_allocation_rules",
        }

    def _strategy_run_dataloader_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        data = config.get("data") if isinstance(config.get("data"), dict) else {}
        universe = config.get("universe") if isinstance(config.get("universe"), dict) else {}
        symbols = [str(item).strip().upper() for item in universe.get("symbols", []) if str(item).strip()]
        provider = str(data.get("provider") or data.get("source") or "yfinance").strip().lower()
        frequency = str(data.get("frequency") or "1D")
        if self._strategy_run_uses_internal_market_loader(config):
            return {
                "source": "multi_asset",
                "frequency": frequency,
                "start_date": str(data.get("start_date") or ""),
                "asset_symbols": symbols,
                "predictor_config": copy.deepcopy(data.get("predictor_config", {})),
            }
        if provider in {"file", "local_csv", "csv"}:
            file_config = copy.deepcopy(data.get("file_config", {}))
            file_path = data.get("file_path") or data.get("path") or file_config.get("file_path")
            if file_path:
                file_config["file_path"] = str(file_path)
            file_config.setdefault("date_column", data.get("date_column", "Time"))
            file_config.setdefault("price_column", data.get("price_column", "Close"))
            return {
                "source": "file",
                "frequency": frequency,
                "start_date": str(data.get("start_date") or ""),
                "end_date": str(data.get("end_date") or ""),
                "file_config": file_config,
                "predictor_config": copy.deepcopy(data.get("predictor_config", {})),
            }
        symbol = symbols[0] if symbols else str(data.get("symbol") or "AAPL")
        return {
            "source": "yfinance",
            "frequency": frequency,
            "start_date": str(data.get("start_date") or "2020-01-01"),
            "end_date": str(data.get("end_date") or ""),
            "yfinance_config": {
                "symbol": symbol,
                "interval": str(data.get("interval") or data.get("frequency") or "1d"),
            },
            "predictor_config": copy.deepcopy(data.get("predictor_config", {})),
        }

    def _strategy_run_backtester_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        platform = config.get("platform") if isinstance(config.get("platform"), dict) else {}
        metadata = config.get("metadata") if isinstance(config.get("metadata"), dict) else {}
        mode = str(platform.get("strategy_mode_id") or "").strip().lower()
        export_config = copy.deepcopy(
            (metadata.get("legacy_backtester") or {}).get("export_config", {})
            if isinstance(metadata.get("legacy_backtester"), dict)
            else {}
        )
        export_config.setdefault("export_parquet", True)
        export_config.setdefault("export_csv", False)
        return {
            "strategy_mode": (
                "multi_asset_portfolio"
                if mode in {"multi_asset_portfolio", "multi_asset_trigger_selection", "dynamic_allocation_rules"}
                else "single_asset_portfolio"
            ),
            "engine_mode": "strategy_run",
            "Backtest_id": str(metadata.get("strategy_id") or mode or "strategy_run"),
            "export_config": export_config,
            "strategy_run_config": copy.deepcopy(config),
        }

    def _process_dataloader_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """處理數據載入器配置"""

        processed = config.copy()

        # NOTE: translated to English.
        if "start_date" in processed:
            processed["start_date"] = str(processed["start_date"])

        # NOTE: translated to English.
        source = processed.get("source", "yfinance")
        if source == "yfinance" and "yfinance_config" not in processed:
            processed["yfinance_config"] = {
                "symbol": "AAPL",
                "period": "1y",
                "interval": "1d",
            }

        return processed

    def _process_backtester_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """處理回測器配置"""

        processed = config.copy()

        # NOTE: translated to English.
        if "condition_pairs" not in processed:
            processed["condition_pairs"] = []

        # NOTE: translated to English.
        if "trading_params" not in processed:
            processed["trading_params"] = {
                "transaction_cost": 0.001,
                "slippage": 0.0005,
                "trade_delay": 0,
                "trade_price": "close",
            }

        return processed

    def _process_metricstracker_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """處理績效追蹤器配置"""

        processed = config.copy()

        if "enable_metrics_analysis" not in processed:
            processed["enable_metrics_analysis"] = False

        processed.setdefault("file_selection_mode", "auto")
        processed.setdefault("parquet_directory", "outputs/backtester/")
        processed.setdefault("time_unit", 365)
        processed.setdefault("risk_free_rate", 0.04)

        return processed

    def _display_load_error(self, message: str, context: str = "") -> None:
        """
        顯示載入錯誤信息

        Args:
            message: 錯誤信息
            context: 錯誤上下文
        """

        title = "⚠️ 配置載入錯誤"
        if context:
            title += f" - {context}"

        show_error("AUTORUNNER", message)


if __name__ == "__main__":
    # NOTE: translated to English.

    # NOTE: translated to English.
    loader = ConfigLoader()

    # NOTE: translated to English.
    test_config = "workspace/runs/config_template.json"
    if Path(test_config).exists():
        config_data = loader.load_config(test_config)
        if config_data:
            summary = config_data.get_summary()
