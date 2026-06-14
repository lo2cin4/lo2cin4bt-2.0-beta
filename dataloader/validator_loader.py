"""
validator_loader.py

【功能說明】
------------------------------------------------------------
本模組為 Lo2cin4BT 數據驗證模組，負責對行情數據進行完整性、型態、欄位、缺失值等多層次驗證與清洗，確保下游流程數據正確且一致。

【流程與數據流】
------------------------------------------------------------
- 由 DataLoader、DataImporter 或 BacktestEngine 調用，對原始或處理後數據進行驗證與清洗
- 驗證結果傳遞給 Calculator、Predictor、BacktestEngine 等模組

```mermaid
flowchart TD
    A[DataLoader/DataImporter/BacktestEngine] -->|調用| B(validator_loader)
    B -->|驗證/清洗| C[數據DataFrame]
    C -->|傳遞| D[Calculator/Predictor/BacktestEngine]
```

【維護與擴充重點】
------------------------------------------------------------
- 新增/修改驗證規則、欄位、缺失值處理方式時，請同步更新頂部註解與下游流程
- 若驗證流程、欄位結構有變動，需同步更新本檔案與下游模組
- 缺失值處理策略如有調整，請同步通知協作者

【常見易錯點】
------------------------------------------------------------
- 欄位名稱拼寫錯誤或型態不符會導致驗證失敗
- 時間欄位缺失或格式錯誤會影響下游流程
- 缺失值處理策略未同步更新會導致資料不一致

【範例】
------------------------------------------------------------
- validator = DataValidator(df)
  df = validator.validate_and_clean()

【與其他模組的關聯】
------------------------------------------------------------
- 由 DataLoader、DataImporter、BacktestEngine 調用，數據傳遞給 Calculator、Predictor、BacktestEngine
- 需與下游欄位結構保持一致

【參考】
------------------------------------------------------------
- pandas 官方文件
- base_loader.py、calculator_loader、predictor_loader
- 專案 README
"""

from typing import Optional

import pandas as pd
from rich.table import Table

from utils import show_error, show_info, show_success, show_warning, get_console

console = get_console()


def print_dataframe_table(df: pd.DataFrame, title: Optional[str] = None) -> None:
    table = Table(title=title, show_lines=True, border_style="#dbac30")
    for col in df.columns:
        table.add_column(str(col), style="bold white")
    for _, row in df.iterrows():
        table.add_row(
            *[
                (
                    f"[#1e90ff]{v}[/#1e90ff]"
                    if isinstance(v, (int, float, complex)) and not isinstance(v, bool)
                    else str(v)
                )
                for v in row
            ]
        )
    console.print(table)


class DataValidator:
    def __init__(self, data: pd.DataFrame) -> None:
        self.data = data.copy()

    def validate_and_clean(self) -> pd.DataFrame:
        """驗證和清洗數據，支援動態欄位"""
        if "Time" not in self.data.columns:
            show_warning("DATALOADER", "無 'Time' 欄位，將生成序列索引")
            self.data["Time"] = pd.date_range(
                start="2020-01-01", periods=len(self.data)
            )

        # NOTE: translated to English.
        numeric_cols = [col for col in self.data.columns if col != "Time"]

        # NOTE: translated to English.
        for col in numeric_cols:
            if not pd.api.types.is_numeric_dtype(self.data[col]):
                s = self.data[col].astype(str).str.upper().str.replace(',', '', regex=False).str.strip()
                # NOTE: translated to English.
                s = s.str.replace('K', 'E3', regex=False).str.replace('M', 'E6', regex=False).str.replace('B', 'E9', regex=False).str.replace('%', 'E-2', regex=False)
                self.data[col] = pd.to_numeric(s, errors='coerce')

        missing_df = pd.DataFrame(
            {
                "欄位": numeric_cols,
                "缺失值比例": [
                    f"{self.data[col].isna().mean():.2%}" for col in numeric_cols
                ],
            }
        )
        print_dataframe_table(missing_df)

        self._handle_time_index()
        return self.data

    def _smart_convert_datetime(self, time_series: pd.Series) -> pd.Series:
        """
        智能檢測並轉換時間格式
        1. 先檢測是否為timestamp格式
        2. 再嘗試不同的日期字符串格式
        """
        try:
            # NOTE: translated to English.
            if pd.api.types.is_numeric_dtype(time_series):
                sample_value = time_series.iloc[0]
                import numpy as np
                if isinstance(sample_value, (int, float, np.integer, np.floating)):
                    if sample_value > 1e10:  # NOTE: translated to English.
                        show_info("DATALOADER", "檢測到毫秒級timestamp格式，正在轉換...")
                        return pd.to_datetime(time_series, unit="ms", errors="coerce")
                    else:  # NOTE: translated to English.
                        show_info("DATALOADER", "檢測到秒級timestamp格式，正在轉換...")
                        return pd.to_datetime(time_series, unit="s", errors="coerce")
            else:
                # NOTE: translated to English.
                try:
                    numeric_value = pd.to_numeric(time_series.iloc[0])
                    if numeric_value > 1e10:  # NOTE: translated to English.
                        show_info("DATALOADER", "檢測到毫秒級timestamp格式，正在轉換...")
                        numeric_series = pd.to_numeric(time_series, errors="coerce")
                        return pd.to_datetime(numeric_series, unit="ms", errors="coerce")
                    else:  # NOTE: translated to English.
                        show_info("DATALOADER", "檢測到秒級timestamp格式，正在轉換...")
                        numeric_series = pd.to_numeric(time_series, errors="coerce")
                        return pd.to_datetime(numeric_series, unit="s", errors="coerce")
                except (ValueError, TypeError):
                    # NOTE: translated to English.
                    pass

            # NOTE: translated to English.
            sample_dates = time_series.head(5).tolist()
            show_info("DATALOADER",
                f"🔍 智能檢測日期格式：\n"
                f"   樣本日期: {sample_dates}\n"
                f"   嘗試解析為 DD/MM/YYYY 格式..."
            )

            # NOTE: translated to English.
            result = pd.to_datetime(time_series, dayfirst=True, errors="coerce")
            invalid_count = result.isna().sum()

            if invalid_count == 0:
                show_success("DATALOADER", "成功解析為 DD/MM/YYYY 格式")
                return result
            else:
                # NOTE: translated to English.
                show_warning("DATALOADER", f"DD/MM/YYYY 格式解析失敗 {invalid_count} 個值，嘗試 MM/DD/YYYY 格式...")
                result2 = pd.to_datetime(time_series, dayfirst=False, errors="coerce")
                invalid_count2 = result2.isna().sum()

                if invalid_count2 < invalid_count:
                    show_success("DATALOADER", "成功解析為 MM/DD/YYYY 格式")
                    return result2
                else:
                    # NOTE: translated to English.
                    show_warning("DATALOADER", "兩種格式都失敗，使用自動推斷格式...")
                    return pd.to_datetime(time_series, errors="coerce")

        except Exception as e:
            show_warning("DATALOADER", f"智能時間轉換失敗：{e}，使用預設格式")
            return pd.to_datetime(time_series, errors="coerce")

    def _handle_missing_values(self, col: str) -> None:
        """??????????????????"""
        strategy = getattr(self, "missing_value_strategy", "ffill")
        fill_value = getattr(self, "missing_value_fill_value", 0)

        if strategy == "mean":
            self.data[col] = self.data[col].fillna(
                self.data[col].rolling(window=5, min_periods=1).mean()
            )
            show_info("DATALOADER", f"???????? {col}")
        elif strategy == "constant":
            self.data[col] = self.data[col].fillna(fill_value)
            show_info("DATALOADER", f"????? {fill_value} ?? {col}")
        else:
            self.data[col] = self.data[col].ffill()
            show_info("DATALOADER", f"???????? {col}")

        remaining_nans = self.data[col].isna().sum()
        if remaining_nans > 0:
            show_warning("DATALOADER", f"{col} ?? {remaining_nans} ??????? 0 ??")
            self.data[col] = self.data[col].fillna(0)

    def _handle_time_index(self) -> None:
        """處理時間索引，確保格式正確，但保留 Time 欄位"""
        try:
            # NOTE: translated to English.
            # NOTE: translated to English.
            if not pd.api.types.is_datetime64_any_dtype(self.data["Time"]):
                # NOTE: translated to English.
                show_info("DATALOADER",
                    f"🔍 時間轉換前檢查：\n"
                    f"   Time欄位類型: {self.data['Time'].dtype}\n"
                    f"   前5個值: {self.data['Time'].head().tolist()}\n"
                    f"   後5個值: {self.data['Time'].tail().tolist()}\n"
                    f"   唯一值數量: {self.data['Time'].nunique()}\n"
                    f"   總行數: {len(self.data)}"
                )

                # NOTE: translated to English.
                original_time = self.data["Time"].copy()
                self.data["Time"] = self._smart_convert_datetime(self.data["Time"])

                # NOTE: translated to English.
                invalid_mask = self.data["Time"].isna()
                if invalid_mask.any():
                    invalid_indices = invalid_mask[invalid_mask].index.tolist()
                    invalid_values = original_time[invalid_mask].tolist()

                    show_error("DATALOADER",
                        f"發現無效時間值：\n"
                        f"   無效值數量: {len(invalid_values)}\n"
                        f"   無效值索引: {invalid_indices[:10]}{'...' if len(invalid_indices) > 10 else ''}\n"
                        f"   無效值樣本: {invalid_values[:10]}{'...' if len(invalid_values) > 10 else ''}\n"
                        f"   原始值類型: {[type(v) for v in invalid_values[:5]]}"
                    )

            if self.data["Time"].isna().sum() > 0:
                show_warning("DATALOADER", f"{self.data['Time'].isna().sum()} 個時間值無效，將移除")
                self.data = self.data.dropna(subset=["Time"])

            if self.data["Time"].duplicated().any():
                show_warning("DATALOADER", "'Time' 欄位有重複值，將按 Time 聚合（取平均值）")
                self.data = (
                    self.data.groupby("Time").mean(numeric_only=True).reset_index()
                )

            # NOTE: translated to English.
            self.data = self.data.reset_index(drop=True)  # NOTE: translated to English.
            self.data = self.data.infer_objects()  # NOTE: translated to English.
            self.data = self.data.sort_values("Time")

        except Exception as e:
            show_error("DATALOADER", f"處理時間索引時出錯：{e}")
            self.data["Time"] = pd.date_range(
                start="2020-01-01", periods=len(self.data)
            )
            self.data = self.data.reset_index(drop=True)
            self.data = self.data.infer_objects()  # NOTE: translated to English.
