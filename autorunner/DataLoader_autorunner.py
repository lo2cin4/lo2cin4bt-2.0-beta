# pylint: disable=too-many-lines
"""
DataLoader_autorunner.py

【功能說明】
------------------------------------------------------------
本模組負責數據載入功能，直接使用原版 dataloader 模組，
根據配置文件自動載入數據，無需用戶互動輸入。

【流程與數據流】
------------------------------------------------------------
- 主流程：讀取配置 → 調用原版 dataloader → 返回數據
- 數據流：配置數據 → 原版載入器 → 標準化數據

【維護與擴充重點】
------------------------------------------------------------
- 直接使用原版 dataloader 模組，避免重複實現
- 若 dataloader 介面有變動，需同步更新調用邏輯
- 新增/修改數據處理時，優先考慮在原版 dataloader 中實現

【常見易錯點】
------------------------------------------------------------
- 數據源配置錯誤導致載入失敗
- 預測因子處理錯誤導致數據不完整
- 數據格式不統一導致後續處理失敗

【範例】
------------------------------------------------------------
- 載入數據：loader.load_data(config) -> DataFrame
- 獲取載入摘要：loader.get_loading_summary() -> dict

【與其他模組的關聯】
------------------------------------------------------------
- 被 Base_autorunner 調用，提供數據載入功能
- 直接調用原版 dataloader 模組進行實際數據載入
- 為 BacktestRunner 提供標準化數據

【版本與變更記錄】
------------------------------------------------------------
- v1.0: 初始版本，基本載入功能
- v1.1: 新增預測因子處理
- v1.2: 新增 Rich Panel 顯示和調試輸出
- Version 2.0: 重構為直接使用原版 dataloader 模組，避免重複實現

【參考】
------------------------------------------------------------
- autorunner/DEVELOPMENT_PLAN.md
- Development_Guideline.md
- Base_autorunner.py
- dataloader/base_loader.py
"""

import logging
import traceback
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from rich.table import Table

from autorunner.utils import get_console
from utils import show_error, show_info, show_success, show_warning
from utils.path_resolver import resolve_input_path

console = get_console()


class DataLoaderAutorunner:
    """
    數據載入封裝器

    直接使用原版 dataloader 模組，根據配置文件自動載入數據，
    無需用戶互動輸入，提供標準化的數據載入介面。
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        初始化 DataLoaderAutorunner

        Args:
            logger: 日誌記錄器
        """

        self.logger = logger or logging.getLogger("DataLoaderAutorunner")
        self.data: Optional[pd.DataFrame] = None
        self.frequency: Optional[str] = None
        self.source: Optional[str] = None
        self.loading_summary: Dict[str, Any] = {}
        self.current_predictor_column: Optional[str] = None
        self.using_price_predictor_only: bool = False
        self.project_root = Path(__file__).resolve().parent.parent

        # NOTE: translated to English.
        from dataloader.file_loader import FileLoader
        self._loader_helper = FileLoader()

    def _validate_and_get_end_date(self, config: Dict[str, Any]) -> str:
        """
        驗證並獲取結束日期

        Args:
            config: 數據載入配置

        Returns:
            str: 有效的結束日期（YYYY-MM-DD格式），如果無效則返回今天的日期
        """
        import re
        from datetime import datetime

        end_date = config.get("end_date")

        # NOTE: translated to English.
        if not end_date or end_date == "/" or end_date.strip() == "":
            return datetime.now().strftime("%Y-%m-%d")

        # NOTE: translated to English.
        if re.match(r"^\d{4}-\d{2}-\d{2}$", str(end_date)):
            return str(end_date)
        else:
            # NOTE: translated to English.
            self.logger.warning(
                f"end_date 格式無效: {end_date}，應為 YYYY-MM-DD 格式，將使用今天的日期"
            )
            return datetime.now().strftime("%Y-%m-%d")

    def load_data(self, config: Dict[str, Any]) -> Optional[pd.DataFrame]:
        """
        根據配置載入數據 - 直接使用原版 dataloader 模組

        Args:
            config: 數據載入配置

        Returns:
            Optional[pd.DataFrame]: 載入的數據，如果載入失敗則返回 None
        """

        try:

            # NOTE: translated to English.
            source = config.get("source", "yfinance")

            # NOTE: translated to English.
            if source == "yfinance":
                from dataloader.yfinance_loader import YahooFinanceLoader
                loader = YahooFinanceLoader()
                # NOTE: translated to English.
                yfinance_config = config.get("yfinance_config", {})
                loader.symbol = yfinance_config.get("symbol", "AAPL")
                loader.interval = yfinance_config.get("interval", "1d")
                loader.start_date = config.get("start_date", "2020-01-01")
                # NOTE: translated to English.
                loader.end_date = self._validate_and_get_end_date(config)

            elif source == "binance":
                from dataloader.binance_loader import BinanceLoader
                loader = BinanceLoader()
                # NOTE: translated to English.
                binance_config = config.get("binance_config", {})
                loader.symbol = binance_config.get("symbol", "BTCUSDT")
                loader.interval = binance_config.get("interval", "1d")
                loader.start_date = config.get("start_date", "2020-01-01")
                # NOTE: translated to English.
                loader.end_date = self._validate_and_get_end_date(config)

            elif source == "coinbase":
                from dataloader.coinbase_loader import CoinbaseLoader
                loader = CoinbaseLoader()
                # NOTE: translated to English.
                coinbase_config = config.get("coinbase_config", {})
                loader.symbol = coinbase_config.get("symbol", "BTC-USD")
                loader.interval = (
                    coinbase_config.get("interval")
                    or config.get("frequency")
                    or config.get("interval")
                    or "1d"
                )
                loader.start_date = config.get("start_date", "2020-01-01")
                # NOTE: translated to English.
                loader.end_date = self._validate_and_get_end_date(config)

            elif source == "file":
                from dataloader.file_loader import FileLoader
                loader = FileLoader()
                # NOTE: translated to English.
                file_config = config.get("file_config", {})
                raw_file_path = str(file_config.get("file_path", "")).strip()
                if raw_file_path:
                    resolved_file = resolve_input_path(
                        raw_file_path,
                        repo_root=self.project_root,
                        config_file_path=config.get("__config_file_path"),
                    )
                    loader.file_path = str(resolved_file.path)
                else:
                    loader.file_path = ""
                loader.interval = (
                    file_config.get("frequency")
                    or config.get("frequency")
                    or config.get("interval")
                    or "1d"
                )
                loader.time_column = file_config.get("time_column", "Time")
                loader.open_column = file_config.get("open_column", "Open")
                loader.high_column = file_config.get("high_column", "High")
                loader.low_column = file_config.get("low_column", "Low")
                loader.close_column = file_config.get("close_column", "Close")
                loader.volume_column = file_config.get("volume_column", "Volume")

            elif source == "multi_asset":
                data = pd.DataFrame()
                frequency = config.get("frequency") or config.get("interval") or "1D"
                self.data = data
                self.frequency = frequency
                self.source = source
                self.current_predictor_column = None
                self.using_price_predictor_only = False
                self._update_loading_summary(config)
                return self.data

            else:
                console.print(
show_error("AUTORUNNER", f"不支援的數據源: {source}")
                )
                return None

            # NOTE: translated to English.
            data, frequency = loader.load()

            if data is None:
                show_error("AUTORUNNER", "數據載入失敗")
                return None

            # NOTE: translated to English.
            self.data = data
            self.frequency = frequency
            self.source = source

            # NOTE: translated to English.
            if config.get("returns_config", {}).get("calculate_returns", False):
                self.data = self._calculate_returns(config)

            # NOTE: translated to English.
            predictor_config = config.get("predictor_config", {})
            skip_predictor = predictor_config.get("skip_predictor", False)

            if skip_predictor:
                # NOTE: translated to English.
                if "Close" in self.data.columns:
                    self.data["X"] = self.data["Close"].copy()
                    self.current_predictor_column = "X"
                else:
                    show_error("AUTORUNNER", "數據中找不到 Close 欄位")
            else:
                # NOTE: translated to English.
                self.data = self._load_predictor_data(config)

            # NOTE: translated to English.
            if config.get("difference_config", {}).get("enable_difference", False):
                self.data = self._process_difference(config)

            # NOTE: translated to English.
            self._update_loading_summary(config)

            return self.data

        except Exception as e:
            show_error("AUTORUNNER", f"數據載入失敗: {e}\n\n詳細錯誤:\n{traceback.format_exc()}")
            self._display_error(f"數據載入失敗: {e}")
            return None


    def _load_predictor_data(self, config: Dict[str, Any]) -> Optional[pd.DataFrame]:
        """載入預測因子數據 - 使用 config 中的設置"""
        try:
            predictor_config = config.get("predictor_config", {})
            predictor_path = predictor_config.get("predictor_path", "")
            predictor_column = predictor_config.get("predictor_column", "X")

            if not predictor_path:
                show_warning("AUTORUNNER", "預測因子路徑為空，使用價格數據")
                self.data["X"] = self.data["Close"].copy()
                self.current_predictor_column = "X"
                return self.data

            # NOTE: translated to English.
            resolved_predictor = resolve_input_path(
                predictor_path,
                repo_root=self.project_root,
                config_file_path=config.get("__config_file_path"),
            )
            predictor_path_obj = resolved_predictor.path

            if not predictor_path_obj.exists():
                show_warning(
                    "AUTORUNNER",
                    f"Predictor file does not exist: {predictor_path_obj}\nFallback: using price data as predictor",
                )
                self.data["X"] = self.data["Close"].copy()
                self.current_predictor_column = "X"
                return self.data

            # NOTE: translated to English.
            if predictor_path_obj.suffix.lower() in [".xlsx", ".xls"]:
                predictor_df = pd.read_excel(predictor_path_obj)
            elif predictor_path_obj.suffix.lower() == ".csv":
                predictor_df = pd.read_csv(predictor_path_obj)
            else:
                show_error("AUTORUNNER", f"不支援的預測因子文件格式: {predictor_path_obj.suffix}")
                self.data["X"] = self.data["Close"].copy()
                self.current_predictor_column = "X"
                return self.data

            # NOTE: translated to English.
            time_column = predictor_config.get("time_column")
            if not time_column or time_column not in predictor_df.columns:
                # NOTE: translated to English.
                time_candidates = ["time", "date", "timestamp", "datetime", "period"]
                for col in predictor_df.columns:
                    if col.lower() in time_candidates:
                        time_column = col
                        break

            if not time_column or time_column not in predictor_df.columns:
                show_error("AUTORUNNER", "無法識別預測因子文件中的時間欄位")
                self.data["X"] = self.data["Close"].copy()
                self.current_predictor_column = "X"
                return self.data

            # NOTE: translated to English.
            if predictor_column not in predictor_df.columns:
                show_error("AUTORUNNER", f"預測因子欄位 {predictor_column} 不存在於文件中\n\n可用欄位: {list(predictor_df.columns)}")
                self.data["X"] = self.data["Close"].copy()
                self.current_predictor_column = "X"
                return self.data

            # NOTE: translated to English.
            predictor_df = predictor_df[[time_column, predictor_column]].copy()

            # NOTE: translated to English.
            predictor_df = self._loader_helper.detect_and_convert_timestamp(predictor_df, time_column)

            # NOTE: translated to English.
            time_format = predictor_config.get("time_format")
            # NOTE: translated to English.
            if not pd.api.types.is_datetime64_any_dtype(predictor_df[time_column]):
                if time_format:
                    try:
                        predictor_df[time_column] = pd.to_datetime(predictor_df[time_column], format=time_format)
                    except Exception as e:
                        show_warning("AUTORUNNER", f"時間格式轉換失敗: {e}，嘗試自動推斷")
                        predictor_df[time_column] = pd.to_datetime(predictor_df[time_column])
                else:
                    predictor_df[time_column] = pd.to_datetime(predictor_df[time_column])

            # NOTE: translated to English.
            predictor_df = predictor_df.set_index(time_column)

            # NOTE: translated to English.
            # NOTE: translated to English.
            if "Time" in self.data.columns:
                price_df = self.data.set_index("Time")
            else:
                price_df = self.data

            # NOTE: translated to English.
            merged_df = price_df.merge(predictor_df, left_index=True, right_index=True, how="inner")

            if merged_df.empty:
                show_warning("AUTORUNNER", "價格數據與預測因子數據無時間交集，使用價格數據")
                self.data["X"] = self.data["Close"].copy()
                self.current_predictor_column = "X"
                return self.data

            # NOTE: translated to English.
            merged_df = merged_df.reset_index()
            merged_df = merged_df.rename(columns={"index": "Time"})

            # NOTE: translated to English.
            show_success("DATALOADER", f"合併數據成功，行數：{len(merged_df)}")

            # NOTE: translated to English.
            show_info("DATALOADER", f"Using predictor column: {predictor_column}")

            self.current_predictor_column = predictor_column
            return merged_df

        except Exception as e:
            show_error("AUTORUNNER", f"預測因子載入失敗: {e}\n\n詳細錯誤:\n{traceback.format_exc()}")
            self.data["X"] = self.data["Close"].copy()
            self.current_predictor_column = "X"
            return self.data

    def _calculate_returns(self, config: Dict[str, Any]) -> pd.DataFrame:
        """計算收益率 - 直接使用原版 dataloader"""
        try:
            from dataloader.calculator_loader import ReturnCalculator

            calculator = ReturnCalculator(self.data)
            return calculator.calculate_returns()

        except Exception as e:
            show_error("AUTORUNNER", f"收益率計算失敗: {e}")
            return self.data

    def _process_difference(self, config: Dict[str, Any]) -> pd.DataFrame:
        """處理差分 - 直接使用原版 dataloader"""
        try:
            from dataloader.predictor_loader import PredictorLoader

            predictor_config = config.get("predictor_config", {})
            selected_predictor = predictor_config.get("predictor_column", "aggregated")

            predictor_loader = PredictorLoader(self.data)
            data_with_difference, _, _ = predictor_loader.process_difference(
                self.data, selected_predictor
            )

            return data_with_difference

        except Exception as e:
            show_error("AUTORUNNER", f"差分處理失敗: {e}")
            return self.data

    def _update_loading_summary(self, config: Dict[str, Any]) -> None:
        """更新載入摘要"""

        self.loading_summary = {
            "source": self.source,
            "frequency": self.frequency,
            "data_shape": self.data.shape if self.data is not None else (0, 0),
            "columns": list(self.data.columns) if self.data is not None else [],
            "date_range": self._get_date_range(),
            "config_used": {
                "source": config.get("source"),
                "start_date": config.get("start_date"),
                "end_date": config.get("end_date"),
            },
        }

    def _get_date_range(self) -> Tuple[str, str]:
        """獲取數據日期範圍"""
        if self.data is None or "Time" not in self.data.columns:
            return "N/A", "N/A"

        try:
            start_date = self.data["Time"].min().strftime("%Y-%m-%d")
            end_date = self.data["Time"].max().strftime("%Y-%m-%d")
            return start_date, end_date
        except Exception:
            return "N/A", "N/A"

    def get_loading_summary(self) -> Dict[str, Any]:
        """
        獲取載入摘要

        Returns:
            Dict[str, Any]: 載入摘要信息
        """
        return self.loading_summary.copy()

    def display_loading_summary(self) -> None:
        """顯示載入摘要"""

        if not self.loading_summary:
            show_error("AUTORUNNER", "沒有載入摘要信息")
            return

        # NOTE: translated to English.
        table = Table(title="Data Loading Summary")
        table.add_column("項目", style="cyan")
        table.add_column("值", style="magenta")

        table.add_row("數據源", self.loading_summary.get("source", "N/A"))
        table.add_row("頻率", self.loading_summary.get("frequency", "N/A"))
        table.add_row(
            "數據形狀",
            f"{self.loading_summary.get('data_shape', (0, 0))[0]} 行 x {self.loading_summary.get('data_shape', (0, 0))[1]} 列",
        )

        date_range = self.loading_summary.get("date_range", ("N/A", "N/A"))
        table.add_row("日期範圍", f"{date_range[0]} 至 {date_range[1]}")

        columns = self.loading_summary.get("columns", [])
        table.add_row("欄位數量", str(len(columns)))
        table.add_row(
            "主要欄位", ", ".join(columns[:5]) + ("..." if len(columns) > 5 else "")
        )

        console.print(table)

        # NOTE: translated to English.
        show_success("DATALOADER", f"數據載入成功！載入了 {self.loading_summary.get('data_shape', (0, 0))[0]} 行數據")

    def _display_error(self, message: str) -> None:
        """
        顯示錯誤信息

        Args:
            message: 錯誤信息
        """

        show_error("AUTORUNNER", message)
