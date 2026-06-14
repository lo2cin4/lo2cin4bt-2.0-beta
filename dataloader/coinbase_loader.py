

from datetime import datetime, timedelta
from typing import Optional, Tuple

import pandas as pd
import requests

from .base_loader import AbstractDataLoader
from .calculator_loader import ReturnCalculator


class CoinbaseLoader(AbstractDataLoader):
    def load(self) -> Tuple[Optional[pd.DataFrame], str]:

        symbol = getattr(self, "symbol", None)
        if symbol is None:
            symbol = getattr(self, "ticker", None) or "BTC-USD"

        # NOTE: translated to English.
        interval_map = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "6h": 21600,
            "1d": 86400,
        }

        interval_input = getattr(self, "interval", None)
        if interval_input is None:
            interval_input = getattr(self, "interval", None) or "1d"

        if interval_input not in interval_map:
            self.show_warning(f"不支援的時間周期 '{interval_input}'，將使用預設值 1d")
            interval_input = "1d"

        granularity = interval_map[interval_input]

        # NOTE: translated to English.
        start_date_str = getattr(self, "start_date", None)
        end_date_str = getattr(self, "end_date", None)

        if start_date_str is None or end_date_str is None:
            start_date_str, end_date_str = self.get_date_range()

        try:
            # NOTE: translated to English.
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d")

            # NOTE: translated to English.
            # NOTE: translated to English.
            all_data = []

            # NOTE: translated to English.
            max_candles = 300
            seconds_per_candle = granularity
            batch_seconds = max_candles * seconds_per_candle

            current_start = start_date

            self.show_info(f"正在從 Coinbase 下載 {symbol} 數據...")

            while current_start < end_date:
                current_end = min(
                    current_start + timedelta(seconds=batch_seconds), end_date
                )

                # Coinbase Exchange API endpoint (public, no auth required)
                # Note: api.exchange.coinbase.com is the current public endpoint
                # The old api.pro.coinbase.com has been deprecated
                url = f"https://api.exchange.coinbase.com/products/{symbol}/candles"
                params = {
                    "start": current_start.isoformat(),
                    "end": current_end.isoformat(),
                    "granularity": granularity,
                }

                response = requests.get(url, params=params, timeout=30)

                if response.status_code != 200:
                    self.show_error(
                        f"API 請求失敗：{response.status_code} - {response.text}"
                    )
                    return None, interval_input

                candles = response.json()

                if candles:
                    all_data.extend(candles)

                # NOTE: translated to English.
                current_start = current_end

            if not all_data:
                self.show_error(f"無法獲取 '{symbol}' 的數據")
                return None, interval_input

            # NOTE: translated to English.
            # NOTE: translated to English.
            data = pd.DataFrame(
                all_data,
                columns=["timestamp", "low", "high", "open", "close", "volume"],
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
            data["Time"] = pd.to_datetime(data["Time"], unit="s")

            # NOTE: translated to English.
            data = data[["Time", "Open", "High", "Low", "Close", "Volume"]]

            # NOTE: translated to English.
            data = data.sort_values("Time").reset_index(drop=True)

            # NOTE: translated to English.
            numeric_columns = ["Open", "High", "Low", "Close", "Volume"]
            data[numeric_columns] = data[numeric_columns].astype(float)

            # NOTE: translated to English.
            calculator = ReturnCalculator(data)
            data = calculator.calculate_returns()

            # NOTE: translated to English.
            self.display_missing_values(data)
            self.show_success(f"從 Coinbase 載入 '{symbol}' 成功，行數：{len(data)}")
            # NOTE: translated to English.
            self.symbol = symbol
            return data, interval_input

        except Exception as e:
            self.show_error(str(e))
            return None, interval_input
