import numpy as np
import pandas as pd

from utils import get_console, show_error, show_success

console = get_console()


class ReturnCalculator:
    def __init__(self, data: pd.DataFrame) -> None:
        self.data = data.copy()

    def calculate_returns(self) -> pd.DataFrame:
        open_col = self._first_existing_column(["Open", "open"])
        close_col = self._first_existing_column(["Close", "close"])
        if open_col is None or close_col is None:
            show_error("DATALOADER", "Missing open/Open or close/Close column; returns were not calculated.")
            return self.data

        open_prices = self.data[open_col].to_numpy(dtype=float, copy=False)
        close_prices = self.data[close_col].to_numpy(dtype=float, copy=False)

        self.data["open_return"] = self._calc_simple_return(open_prices)
        self.data["close_return"] = self._calc_simple_return(close_prices)
        self.data["open_logreturn"] = self._calc_log_return(open_prices)
        self.data["close_logreturn"] = self._calc_log_return(close_prices)
        show_success(
            "DATALOADER",
            "Calculated open_return, close_return, open_logreturn, and close_logreturn.",
        )
        return self.data

    def _first_existing_column(self, candidates: list[str]) -> str | None:
        for candidate in candidates:
            if candidate in self.data.columns:
                return candidate
        return None

    @staticmethod
    def _calc_simple_return(prices: np.ndarray) -> np.ndarray:
        returns = np.zeros(len(prices), dtype=np.float64)
        if len(prices) < 2:
            return returns
        previous = prices[:-1]
        current = prices[1:]
        valid = previous != 0
        returns[1:][valid] = (current[valid] - previous[valid]) / previous[valid]
        return returns

    @staticmethod
    def _calc_log_return(prices: np.ndarray) -> np.ndarray:
        returns = np.zeros(len(prices), dtype=np.float64)
        if len(prices) < 2:
            return returns
        previous = prices[:-1]
        current = prices[1:]
        valid = (current > 0) & (previous > 0)
        returns[1:][valid] = np.log(current[valid] / previous[valid])
        return returns
