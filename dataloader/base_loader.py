

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Tuple, Union

import pandas as pd

from dataloader.validator_loader import print_dataframe_table
from utils import (
    show_error,
    show_info,
    show_success,
    show_step_panel,
    show_warning,
    get_console,
)

# NOTE: translated to English.
console = get_console()


class AbstractDataLoader(ABC):
    """Abstract base class for all data loaders with common functionality"""

    def __init__(self) -> None:
        self.console = get_console()

    def show_error(self, message: str) -> None:
        """Display error message in standardised panel"""
        show_error("DATALOADER", message)

    def show_success(self, message: str) -> None:
        """Display success message in standardised panel"""
        show_success("DATALOADER", message)

    def show_warning(self, message: str) -> None:
        """Display warning message in standardised panel"""
        show_warning("DATALOADER", message)

    def show_info(self, message: str) -> None:
        """Display informational message in standardised panel"""
        show_info("DATALOADER", message)

    def get_date_range(
        self, default_start: str = "2020-01-01", default_end: Optional[str] = None
    ) -> Tuple[str, str]:
        """Return configured date range without prompting."""
        if default_end is None:
            default_end = datetime.now().strftime("%Y-%m-%d")

        start_date = getattr(self, "start_date", None) or default_start
        end_date = getattr(self, "end_date", None) or default_end
        return start_date, end_date

    def get_frequency(self, default: str = "1d") -> str:
        """Get data frequency from config defaults without prompting."""
        return getattr(self, "interval", None) or default

    def display_missing_values(
        self, data: pd.DataFrame, columns: Optional[List[str]] = None
    ) -> None:
        """Display missing value statistics for specified columns"""
        if columns is None:
            columns = ["Open", "High", "Low", "Close", "Volume"]

        missing_msgs = []
        for col in columns:
            if col in data.columns:
                missing_ratio = data[col].isna().mean()
                missing_msgs.append(f"{col} 缺失值比例：{missing_ratio:.2%}")

        if missing_msgs:
                self.show_info("\n".join(missing_msgs))

    def standardize_columns(self, data: pd.DataFrame) -> pd.DataFrame:
        """Standardize column names to expected format"""
        col_map = {}
        time_col_found = False  # NOTE: translated to English.

        for col in data.columns:
            col_lower = str(col).lower()
            # NOTE: translated to English.
            if col_lower in ["date", "time", "timestamp", "datetime", "period"]:
                if not time_col_found:
                    col_map[col] = "Time"
                    time_col_found = True
            elif col_lower in ["open", "o"]:
                col_map[col] = "Open"
            elif col_lower in ["high", "h"]:
                col_map[col] = "High"
            elif col_lower in ["low", "l"]:
                col_map[col] = "Low"
            elif col_lower in ["close", "c"]:
                col_map[col] = "Close"
            elif col_lower in ["volume", "vol", "v", "vol."]:
                col_map[col] = "Volume"

        return data.rename(columns=col_map)

    def ensure_required_columns(
        self, data: pd.DataFrame, required_cols: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """Ensure all required columns exist in dataframe"""
        if required_cols is None:
            required_cols = ["Time", "Open", "High", "Low", "Close", "Volume"]

        missing_cols = [col for col in required_cols if col not in data.columns]

        if missing_cols:
            self.show_warning(f"缺少欄位 {missing_cols}，將設為缺失值")
            for col in missing_cols:
                data[col] = pd.NA

        # Keep only required columns
        return data[required_cols]

    def convert_numeric_columns(
        self, data: pd.DataFrame, numeric_cols: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """Convert specified columns to numeric types"""
        if numeric_cols is None:
            numeric_cols = ["Open", "High", "Low", "Close", "Volume"]

        for col in numeric_cols:
            if col in data.columns:
                try:
                    # NOTE: translated to English.
                    if not pd.api.types.is_numeric_dtype(data[col]):
                        s = data[col].astype(str).str.upper().str.replace(',', '', regex=False).str.strip()
                        s = s.str.replace('K', 'E3', regex=False).str.replace('M', 'E6', regex=False).str.replace('B', 'E9', regex=False).str.replace('%', 'E-2', regex=False)
                        data[col] = pd.to_numeric(s, errors="coerce")
                    else:
                        data[col] = pd.to_numeric(data[col], errors="coerce")
                except Exception as e:
                    self.show_warning(f"無法轉換欄位 '{col}' 為數值：{e}")
                    data[col] = pd.NA

        return data

    def detect_and_convert_timestamp(
        self, data: pd.DataFrame, time_col: str = "Time"
    ) -> pd.DataFrame:

        if time_col not in data.columns:
            return data

        try:
            # NOTE: translated to English.
            if pd.api.types.is_datetime64_any_dtype(data[time_col]):
                return data

            # NOTE: translated to English.
            sample_value = data[time_col].iloc[0]

            # NOTE: translated to English.
            if pd.api.types.is_numeric_dtype(data[time_col]):
                # NOTE: translated to English.
                # NOTE: translated to English.
                # NOTE: translated to English.
                import numpy as np
                if isinstance(sample_value, (int, float, np.integer, np.floating)):
                    if sample_value > 1e10:  # NOTE: translated to English.
                        self.show_info("檢測到毫秒級timestamp格式，正在轉換...")
                        data[time_col] = pd.to_datetime(data[time_col], unit="ms")
                    else:  # NOTE: translated to English.
                        self.show_info("檢測到秒級timestamp格式，正在轉換...")
                        data[time_col] = pd.to_datetime(data[time_col], unit="s")

                    self.show_success(f"timestamp轉換成功，格式為：{data[time_col].iloc[0]}")
            else:
                # NOTE: translated to English.
                try:
                    numeric_value = pd.to_numeric(data[time_col].iloc[0])
                    if numeric_value > 1e10:  # NOTE: translated to English.
                        self.show_info("檢測到毫秒級timestamp格式，正在轉換...")
                        data[time_col] = pd.to_numeric(data[time_col])
                        data[time_col] = pd.to_datetime(data[time_col], unit="ms")
                    else:  # NOTE: translated to English.
                        self.show_info("檢測到秒級timestamp格式，正在轉換...")
                        data[time_col] = pd.to_numeric(data[time_col])
                        data[time_col] = pd.to_datetime(data[time_col], unit="s")

                    self.show_success(f"timestamp轉換成功，格式為：{data[time_col].iloc[0]}")
                except (ValueError, TypeError):
                    # NOTE: translated to English.
                    pass

        except Exception as e:
            self.show_warning(f"timestamp檢測時出錯：{e}，將嘗試其他方式解析時間")

        return data

    @abstractmethod
    def load(self) -> Tuple[Optional[pd.DataFrame], str]:
        """Abstract method that must be implemented by all subclasses"""


class BaseDataLoader:


    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.data: Optional[pd.DataFrame] = None
        self.frequency: Optional[str] = None
        self.source: Optional[str] = None
        self.logger = logger or logging.getLogger("BaseDataLoader")

    @staticmethod
    def get_steps() -> List[str]:
        """Get the list of steps for data loading process."""
        return [
            "選擇價格數據來源",
            "輸入預測因子",
            "導出合併後數據",
            "選擇差分預測因子",
        ]

    def process_difference(
        self, data: pd.DataFrame, predictor_col: Optional[str] = None
    ) -> Tuple[pd.DataFrame, Optional[List[str]], Optional[pd.Series]]:
        """
        ?????????????????????
        """
        available_factors = [
            col
            for col in data.columns
            if col
            not in [
                "Time",
                "Open",
                "High",
                "Low",
                "Close",
                "Volume",
                "open_return",
                "close_return",
                "open_logreturn",
                "close_logreturn",
            ]
        ]

        if not available_factors:
            self._print_step_panel(
                4,
                "?????????????????????",
            )
            return data, None, None

        default = available_factors[0]
        predictor_col = predictor_col or getattr(self, "predictor_col", None)
        if predictor_col is None:
            predictor_col = default

        if str(predictor_col).lower() == "price":
            self._print_step_panel(4, "??????????????????????")
            return data, None, None

        if predictor_col not in available_factors:
            show_warning(
                "DATALOADER",
                f"???? '{predictor_col}' ?????????? {default}?",
            )
            predictor_col = default

        from .predictor_loader import PredictorLoader

        predictor_loader = PredictorLoader(data)
        data, diff_cols, used_series = predictor_loader.process_difference(
            data, predictor_col
        )
        return data, diff_cols, used_series

    @staticmethod
    def print_step_panel(current_step: int, desc: str = "") -> None:
        """Print a step panel with progress information."""
        steps = BaseDataLoader.get_steps()
        show_step_panel("DATALOADER", current_step, steps, desc)

    def _print_step_panel(self, current_step: int, desc: str = "") -> None:
        # NOTE: translated to English.
        BaseDataLoader.print_step_panel(current_step, desc)

    def run(  # noqa: C901 # pylint: disable=too-many-statements, too-many-branches
        self,
    ) -> Optional[Union[pd.DataFrame, str]]:
        """
        ???????????????????
        """
        try:
            self._print_step_panel(
                1,
                "??????????Excel/CSV?Yahoo Finance?Binance API?Coinbase API??",
            )

            choice = str(
                getattr(self, "source", None)
                or getattr(self, "data_source", None)
                or "1"
            )
            if choice not in ["1", "2", "3", "4"]:
                show_warning("DATALOADER", f"???? '{choice}' ????? 1?")
                choice = "1"
            self.source = choice

            if self.source == "1":
                from .file_loader import FileLoader
                loader = FileLoader()
            elif self.source == "2":
                from .yfinance_loader import YahooFinanceLoader
                loader = YahooFinanceLoader()
            elif self.source == "3":
                from .binance_loader import BinanceLoader
                loader = BinanceLoader()
            else:
                from .coinbase_loader import CoinbaseLoader
                loader = CoinbaseLoader()

            self.data, self.frequency = loader.load()
            if hasattr(loader, 'symbol'):
                self.symbol = loader.symbol
            else:
                self.symbol = "X"

            if self.data is None:
                show_error("DATALOADER", "?????????")
                return None

            from .validator_loader import DataValidator
            validator = DataValidator(self.data)
            self.data = validator.validate_and_clean()
            if self.data is None:
                show_error("DATALOADER", "???????")
                return None

            from .calculator_loader import ReturnCalculator
            calculator = ReturnCalculator(self.data)
            self.data = calculator.calculate_returns()
            price_data = self.data

            print_dataframe_table(self.data.head(), title="???????????")

            self._print_step_panel(
                2,
                "????????????????????????",
            )

            from .predictor_loader import PredictorLoader
            predictor_loader = PredictorLoader(price_data=price_data)
            predictor_data = predictor_loader.load()
            self.predictor_file_name = predictor_loader.predictor_file_name

            if (
                isinstance(predictor_data, str)
                and predictor_data == "__SKIP_STATANALYSER__"
            ):
                if not hasattr(self, "frequency") or self.frequency is None:
                    self.frequency = "1d"
                self.skip_statanalyser = True
                self.data = price_data
            elif predictor_data is not None:
                self.data = predictor_data
            else:
                show_info("DATALOADER", "???????????????")
                self.data = price_data

            from .validator_loader import DataValidator
            validator = DataValidator(self.data)
            self.data = validator.validate_and_clean()
            if self.data is None:
                show_error("DATALOADER", "?????????")
                return None

            print_dataframe_table(
                self.data.head(), title="??/???????"
            )

            self._print_step_panel(
                3,
                "??? config ??????????",
            )

            export_choice = bool(getattr(self, "export_data", False))
            if export_choice:
                from .data_exporter_loader import DataExporter
                exporter = DataExporter(self.data)
                exporter.export()
            else:
                show_info("DATALOADER", "???????")

            return self.data

        except Exception as err:  # pylint: disable=broad-exception-caught
            self.logger.error(f"??????: {err}")
            show_error("DATALOADER", f"??????: {err}")
            return None



class DataLoader:  # pylint: disable=too-few-public-methods
    """Data loader wrapper class for backward compatibility."""

    def __init__(self) -> None:

        self.data: Optional[Union[pd.DataFrame, str]] = (
            None  # NOTE: translated to English.
        )
        self.source: Optional[str] = (
            None  # NOTE: translated to English.
        )
        self.frequency: Optional[str] = None  # NOTE: translated to English.

    def load_data(self) -> Optional[Union[pd.DataFrame, str]]:
        """Load data using BaseDataLoader."""
        # NOTE: translated to English.
        loader = BaseDataLoader()
        result = loader.run()
        if isinstance(result, str) and result == "__SKIP_STATANALYSER__":
            self.data = loader.data
            self.frequency = loader.frequency
            return "__SKIP_STATANALYSER__"
        self.data = result
        self.frequency = loader.frequency
        return result
