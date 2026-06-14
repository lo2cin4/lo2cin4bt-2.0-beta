"""
ConfigLoader_wfanalyser.py

【功能說明】
------------------------------------------------------------
本模組負責 WFA 配置文件載入功能，從 JSON 文件中讀取配置數據，
解析和轉換配置參數，為後續模組提供標準化的配置數據結構。

【流程與數據流】
------------------------------------------------------------
- 主流程：讀取文件 → 解析 JSON → 驗證數據 → 轉換格式 → 返回配置
- 數據流：文件路徑 → JSON 數據 → 配置字典 → 標準化配置

【維護與擴充重點】
------------------------------------------------------------
- 新增配置欄位時，請同步更新載入邏輯
- 若配置格式有變動，需同步更新解析邏輯

【常見易錯點】
------------------------------------------------------------
- JSON 解析錯誤導致配置載入失敗
- 配置數據轉換錯誤導致參數不正確
- 缺少必要配置時沒有提供預設值

【範例】
------------------------------------------------------------
- 載入單個配置：loader.load_config("config.json") -> ConfigData
- 載入多個配置：loader.load_configs(["config1.json", "config2.json"]) -> [ConfigData1, ConfigData2]

【與其他模組的關聯】
------------------------------------------------------------
- 被 Base_wfanalyser 調用，提供配置載入功能
- 依賴 json 進行配置文件解析
- 為 WalkForwardEngine 等提供配置數據

【版本與變更記錄】
------------------------------------------------------------
- v1.0: 初始版本，基本載入功能

【參考】
------------------------------------------------------------
- Base_wfanalyser.py: WFA 框架核心控制器
- ConfigValidator_wfanalyser.py: 配置驗證器
- wfanalyser/README.md: WFA 模組詳細說明
"""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional


from backtester.StrategyRunConfig_backtester import normalize_wfa_run_config
from .utils.ConsoleUtils_utils_wfanalyser import get_console
from utils import show_error, show_warning

console = get_console()


class WFAConfigData:
    """
    WFA 配置數據容器

    封裝配置文件的數據結構，提供標準化的配置訪問介面。
    """

    def __init__(self, config_dict: Dict[str, Any], file_path: str):
        """
        初始化 WFAConfigData

        Args:
            config_dict: 配置字典
            file_path: 配置文件路徑
        """
        self.file_path = file_path
        self.file_name = Path(file_path).name
        self.raw_config = config_dict.copy()

        # NOTE: translated to English.
        self.wfa_config = config_dict.get("wfa_config", {})
        self.dataloader_config = config_dict.get("dataloader", {})
        self.backtester_config = config_dict.get("backtester", {})
        self.metricstracker_config = config_dict.get("metricstracker", {})

        # NOTE: translated to English.
        self.predictor_config = self.dataloader_config.get("predictor_config", {})

        # Keep source config path for downstream relative-path resolution.
        self.wfa_config.setdefault("__config_file_path", self.file_path)
        self.dataloader_config.setdefault("__config_file_path", self.file_path)
        self.backtester_config.setdefault("__config_file_path", self.file_path)
        self.metricstracker_config.setdefault("__config_file_path", self.file_path)

    def get_summary(self) -> Dict[str, Any]:
        """
        獲取配置摘要

        Returns:
            Dict[str, Any]: 配置摘要信息
        """
        config_summary = {
            "file_name": self.file_name,
            "file_path": self.file_path,
            "wfa_mode": self.wfa_config.get("mode", "standard"),
            "train_set_percentage": self.wfa_config.get("train_set_percentage", 0.6),
            "test_set_percentage": self.wfa_config.get("test_set_percentage", 0.2),
            "step_size": self.wfa_config.get("step_size", 30),
            "dataloader_source": self.dataloader_config.get("source", "unknown"),
            "backtester_pairs": len(self.backtester_config.get("condition_pairs", [])),
        }

        return config_summary


class ConfigLoader:
    """
    WFA 配置文件載入器

    負責從 JSON 文件中載入配置數據，解析和轉換配置參數，
    提供標準化的配置數據結構。
    """

    def __init__(self) -> None:
        """初始化 ConfigLoader"""
        # NOTE: translated to English.
        self.default_config = {
            "wfa_config": {
                "mode": "standard",
                "train_set_percentage": 0.6,
                "test_set_percentage": 0.2,
                "step_size": 30,
                "optimization_objectives": ["sharpe", "calmar"],
            },
            "dataloader": {
                "source": "yfinance",
                "start_date": "2020-01-01",
            },
            "backtester": {
                "strategy_mode": "auto",
                "condition_pairs": [],
            },
            "metricstracker": {
                "enable_metrics_analysis": True,
            },
        }

    def load_config(self, config_file: str) -> Optional[WFAConfigData]:
        """
        載入單個配置文件

        Args:
            config_file: 配置文件路徑

        Returns:
            Optional[WFAConfigData]: 配置數據對象，如果載入失敗則返回 None
        """
        try:
            # NOTE: translated to English.
            config_dict = self._read_config_file(config_file)
            if config_dict is None:
                return None
            if config_dict.get("schema_version") == "wfa_run":
                config_dict = self._legacy_shell_from_wfa_run(config_dict, config_file)

            # NOTE: translated to English.
            merged_config = self._merge_with_defaults(config_dict)

            # NOTE: translated to English.
            processed_config = self._process_config(merged_config)

            # NOTE: translated to English.
            config_data_obj = WFAConfigData(processed_config, config_file)

            return config_data_obj

        except Exception as e:
            print(f"❌ [ERROR] 載入配置文件失敗: {e}")
            self._display_load_error(f"載入失敗: {e}", Path(config_file).name)
            return None

    def load_configs(self, config_files: List[str]) -> List[WFAConfigData]:
        """
        載入多個配置文件

        Args:
            config_files: 配置文件路徑列表

        Returns:
            List[WFAConfigData]: 配置數據對象列表
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
            with open(config_file, "r", encoding="utf-8-sig") as f:
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
        merged_config = self.default_config.copy()

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
        if "wfa_config" in processed_config:
            processed_config["wfa_config"] = self._process_wfa_config(
                processed_config["wfa_config"]
            )

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

    def _legacy_shell_from_wfa_run(self, config_dict: Dict[str, Any], config_file: str) -> Dict[str, Any]:
        """Adapt the canonical WFA shell into the runtime shape used by the current engine."""
        normalized = normalize_wfa_run_config(config_dict, source_path=Path(config_file))
        strategy_config = self._load_wfa_strategy_config(normalized, config_file)
        wfa_platform = normalized.get("platform", {}) if isinstance(normalized.get("platform"), dict) else {}
        platform = strategy_config.get("platform", {}) if isinstance(strategy_config, dict) else {}
        data_cfg = strategy_config.get("data", {}) if isinstance(strategy_config, dict) else {}
        universe = strategy_config.get("universe", {}) if isinstance(strategy_config, dict) else {}
        metadata = strategy_config.get("metadata", {}) if isinstance(strategy_config, dict) else {}
        legacy_backtester = deepcopy(metadata.get("legacy_backtester", {}))
        strategy_mode = str(platform.get("strategy_mode_id") or legacy_backtester.get("strategy_mode") or "auto")
        symbols = [str(item).strip().upper() for item in universe.get("symbols", []) if str(item).strip()]
        primary_symbol = symbols[0] if symbols else str(data_cfg.get("symbol") or "AAPL").upper()

        wfa_config = self._wfa_config_from_wfa_run(normalized)
        dataloader = self._dataloader_config_from_wfa_run(data_cfg, primary_symbol)
        backtester = self._backtester_config_from_wfa_run(
            strategy_config=strategy_config,
            legacy_backtester=legacy_backtester,
            strategy_mode=strategy_mode,
        )

        return {
            "schema_version": "wfa_run",
            "strategy_config_path": normalized.get("strategy_config_path", ""),
            "platform": {
                "run_type": str(wfa_platform.get("run_type") or platform.get("run_type") or "test"),
                "display_label": wfa_platform.get("display_label")
                or platform.get("display_label")
                or config_dict.get("display_label"),
            },
            "wfa_config": wfa_config,
            "dataloader": dataloader,
            "backtester": backtester,
            "metricstracker": {"enable_metrics_analysis": True},
            "legacy_embedded_strategy_config": strategy_config,
        }

    def _load_wfa_strategy_config(self, normalized: Dict[str, Any], config_file: str) -> Dict[str, Any]:
        embedded = normalized.get("legacy_embedded_strategy_config")
        if isinstance(embedded, dict) and embedded:
            return deepcopy(embedded)
        strategy_path = str(normalized.get("strategy_config_path") or "").strip()
        if not strategy_path:
            return {}
        path = self._resolve_wfa_strategy_config_path(strategy_path, config_file)
        with open(path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _resolve_wfa_strategy_config_path(strategy_path: str, config_file: str) -> Path:
        path = Path(strategy_path)
        config_parent = Path(config_file).resolve().parent
        candidates = [path] if path.is_absolute() else []
        if not path.is_absolute():
            candidates.append(config_parent / strategy_path)
            for parent in [config_parent, *config_parent.parents]:
                if (parent / "workspace").exists() or (parent / "backtester").exists():
                    candidates.append(parent / strategy_path)
            candidates.append(Path.cwd() / strategy_path)
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        return candidates[0].resolve() if candidates else path.resolve()

    @staticmethod
    def _wfa_config_from_wfa_run(normalized: Dict[str, Any]) -> Dict[str, Any]:
        windowing = normalized.get("windowing", {}) if isinstance(normalized.get("windowing"), dict) else {}
        optimizer = normalized.get("optimizer", {}) if isinstance(normalized.get("optimizer"), dict) else {}
        outputs = normalized.get("outputs", {}) if isinstance(normalized.get("outputs"), dict) else {}
        mode = str(windowing.get("mode") or "standard").strip().lower()
        if mode == "rolling":
            mode = "standard"
        return {
            "engine": normalized.get("engine") or normalized.get("runtime") or optimizer.get("engine"),
            "runtime": normalized.get("runtime") or normalized.get("engine") or optimizer.get("runtime"),
            "mode": mode if mode in {"standard", "anchored"} else "standard",
            "windowing": deepcopy(windowing),
            "size_mode": windowing.get("size_mode") or windowing.get("window_size_mode"),
            "train_size": windowing.get("train_size"),
            "test_size": windowing.get("test_size"),
            "train_ratio": windowing.get("train_ratio"),
            "test_ratio": windowing.get("test_ratio"),
            "train_set_percentage": windowing.get("train_ratio") or 0.6,
            "test_set_percentage": windowing.get("test_ratio") or 0.2,
            "step_size": int(windowing["step_size"]) if windowing.get("step_size") is not None else None,
            "target_window_count": windowing.get("target_window_count"),
            "optimization_objectives": list(optimizer.get("objectives") or ["sharpe", "calmar"]),
            "optimizer": deepcopy(optimizer),
            "acceptance": deepcopy(normalized.get("acceptance", {})),
            "outputs": deepcopy(outputs),
            "window_backtests": bool(outputs.get("window_backtests", False)),
        }

    @staticmethod
    def _dataloader_config_from_wfa_run(data_cfg: Dict[str, Any], primary_symbol: str) -> Dict[str, Any]:
        provider = str(data_cfg.get("provider") or data_cfg.get("source") or "yfinance").strip().lower()
        start_date = str(data_cfg.get("start_date") or data_cfg.get("start") or "2020-01-01")
        if provider in {"local", "multi_asset"}:
            source = "multi_asset"
        elif provider in {"file", "csv", "parquet"}:
            source = "file"
        else:
            source = "yfinance"
        dataloader = {
            "source": source,
            "start_date": start_date,
            "frequency": data_cfg.get("frequency") or "1D",
            "predictor_config": {"skip_predictor": True},
        }
        if source == "yfinance":
            dataloader["yfinance_config"] = {
                "symbol": primary_symbol,
                "period": data_cfg.get("period") or "max",
                "interval": data_cfg.get("interval") or "1d",
            }
        if source == "file":
            dataloader["file_config"] = deepcopy(data_cfg.get("file_config", {}))
        return dataloader

    @staticmethod
    def _backtester_config_from_wfa_run(
        *,
        strategy_config: Dict[str, Any],
        legacy_backtester: Dict[str, Any],
        strategy_mode: str,
    ) -> Dict[str, Any]:
        backtester = deepcopy(legacy_backtester) if isinstance(legacy_backtester, dict) else {}
        if strategy_mode == "multi_asset_portfolio":
            backtester.setdefault("strategy_mode", "multi_asset_portfolio")
            backtester.setdefault("portfolio_config", deepcopy(strategy_config))
        else:
            backtester.setdefault("strategy_mode", "auto")
        backtester.setdefault("strategy_config", deepcopy(strategy_config))
        backtester.setdefault("strategy_run_config", deepcopy(strategy_config))
        data_cfg = strategy_config.get("data", {}) if isinstance(strategy_config.get("data"), dict) else {}
        universe_cfg = strategy_config.get("universe", {}) if isinstance(strategy_config.get("universe"), dict) else {}
        symbols = [
            str(item).strip().upper()
            for item in universe_cfg.get("symbols", [])
            if str(item).strip()
        ]
        market_data = backtester.setdefault("market_data", {})
        if isinstance(market_data, dict):
            market_data.setdefault("provider", data_cfg.get("provider") or "yfinance")
            market_data.setdefault("symbols", symbols)
            market_data.setdefault("start", data_cfg.get("start_date") or data_cfg.get("start") or "1990-01-01")
            market_data.setdefault("interval", data_cfg.get("interval") or data_cfg.get("frequency") or "1d")
            market_data.setdefault("frequency", data_cfg.get("frequency") or data_cfg.get("interval") or "1D")
            if data_cfg.get("calendar"):
                market_data.setdefault("calendar", data_cfg.get("calendar"))
            if data_cfg.get("timezone"):
                market_data.setdefault("timezone", data_cfg.get("timezone"))
            market_data.setdefault("start_policy", data_cfg.get("start_policy") or "common_available")
        backtester.setdefault("trading_params", {
            "transaction_cost": 0.001,
            "slippage": 0.0005,
            "trade_delay": 0,
            "trade_price": "close",
        })
        return backtester

    def _process_wfa_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """處理 WFA 配置"""
        processed = config.copy()

        # NOTE: translated to English.
        train_pct = processed.get("train_set_percentage", 0.6)
        test_pct = processed.get("test_set_percentage", 0.2)
        if train_pct + test_pct > 1.0:
            show_warning("WFANALYSER",
                f"訓練集百分比 ({train_pct}) + 測試集百分比 ({test_pct}) > 1.0，"
                "將自動調整為 0.6 和 0.2"
            )
            processed["train_set_percentage"] = 0.6
            processed["test_set_percentage"] = 0.2

        # NOTE: translated to English.
        mode = processed.get("mode", "standard")
        if mode not in ["standard", "anchored"]:
            show_warning("WFANALYSER", f"無效的 WFA 模式: {mode}，將使用 'standard'")
            processed["mode"] = "standard"

        return processed

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
            processed["enable_metrics_analysis"] = True

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
        show_error("WFANALYSER", message)
