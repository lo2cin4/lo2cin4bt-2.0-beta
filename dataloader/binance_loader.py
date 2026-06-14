

from typing import Optional, Tuple

import pandas as pd
from binance.client import Client

from .base_loader import AbstractDataLoader
from .calculator_loader import ReturnCalculator


class BinanceLoader(AbstractDataLoader):
    def load(self) -> Tuple[Optional[pd.DataFrame], str]:

        symbol = getattr(self, "symbol", None)
        interval = getattr(self, "interval", None)
        start_date = getattr(self, "start_date", None)
        end_date = getattr(self, "end_date", None)

        if symbol is None:
            symbol = getattr(self, "ticker", None) or "BTCUSDT"
        if interval is None:
            interval = self.get_frequency("1d")
        if start_date is None or end_date is None:
            start_date, end_date = self.get_date_range()

        try:
            # NOTE: translated to English.
            client = Client()
            klines = client.get_historical_klines(
                symbol, interval, start_date, end_date
            )
            if not klines:
                self.show_error(f"無法獲取 '{symbol}' 的數據")
                return None, interval

            # NOTE: translated to English.
            data = pd.DataFrame(
                klines,
                columns=[
                    "timestamp",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "close_time",
                    "quote_asset_volume",
                    "number_of_trades",
                    "taker_buy_base_asset_volume",
                    "taker_buy_quote_asset_volume",
                    "ignore",
                ],
            )

            # NOTE: translated to English.
            data = data.rename(
                columns={
                    "timestamp": "Time",
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "volume": "Volume",
                }
            )

            # NOTE: translated to English.
            data["Time"] = pd.to_datetime(data["Time"], unit="ms")

            # NOTE: translated to English.
            data = data[["Time", "Open", "High", "Low", "Close", "Volume"]]

            # NOTE: translated to English.
            data[["Open", "High", "Low", "Close", "Volume"]] = data[
                ["Open", "High", "Low", "Close", "Volume"]
            ].astype(float)

            # NOTE: translated to English.
            calculator = ReturnCalculator(data)
            data = calculator.calculate_returns()

            # NOTE: translated to English.
            self.display_missing_values(data)
            self.show_success(f"從 Binance 載入 '{symbol}' 成功，行數：{len(data)}")
            # NOTE: translated to English.
            self.symbol = symbol
            return data, interval
        except Exception as err:  # pylint: disable=broad-exception-caught
            self.show_error(str(err))
            return None, interval
