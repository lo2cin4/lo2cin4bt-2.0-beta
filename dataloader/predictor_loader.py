"""
predictor_loader.py

【功能說明】
------------------------------------------------------------
本模組為 lo2cin4bt 數據預測與特徵工程模組，負責對行情數據進行特徵提取、預測欄位生成、機器學習前處理與差分處理，並確保數據結構與下游模組一致。

【流程與數據流】
------------------------------------------------------------
- 由 DataLoader、DataImporter 或 BacktestEngine 調用，對原始數據進行特徵工程與預測欄位生成
- 處理後數據傳遞給 Calculator、Validator、BacktestEngine 等模組

```mermaid
flowchart TD
    A[DataLoader/DataImporter/BacktestEngine] -->|調用| B(predictor_loader)
    B -->|特徵/預測欄位| C[數據DataFrame]
    C -->|傳遞| D[Calculator/Validator/BacktestEngine]
```

【維護與擴充重點】
------------------------------------------------------------
- 新增/修改特徵類型、欄位、差分邏輯時，請同步更新頂部註解與下游流程
- 若特徵工程流程、欄位結構有變動，需同步更新本檔案與下游模組
- 特徵生成公式如有調整，請同步通知協作者
- 新增/修改特徵類型、欄位、差分邏輯時，務必同步更新本檔案與下游模組
- 欄位名稱、型態需與下游模組協調一致

【常見易錯點】
------------------------------------------------------------
- 預測因子檔案格式錯誤或缺失時間欄位會導致合併失敗
- 欄位型態不符或缺失值未處理會影響下游計算
- 差分選項未正確選擇會導致特徵異常

【錯誤處理】
------------------------------------------------------------
- 檔案不存在時提供明確錯誤訊息
- 時間欄位缺失時自動識別並提示
- 數據對齊失敗時提供詳細診斷

【範例】
------------------------------------------------------------
- loader = PredictorLoader(price_data)
  df = loader.load()
- df, diff_cols, used_series = loader.process_difference(df, '因子欄位名')

【與其他模組的關聯】
------------------------------------------------------------
- 由 DataLoader、DataImporter、BacktestEngine 調用，數據傳遞給 Calculator、Validator、BacktestEngine
- 需與下游欄位結構保持一致

【版本與變更記錄】
------------------------------------------------------------
- v1.0: 初始版本，支援基本預測因子載入
- v1.1: 新增差分處理功能
- v1.2: 支援多種檔案格式和自動時間欄位識別

【參考】
------------------------------------------------------------
- pandas 官方文件
- base_loader.py、DataValidator、calculator_loader
- 專案 README
"""

import glob
import os
from pathlib import Path
from typing import List, Optional, Tuple, Union

import pandas as pd
from rich.table import Table

from utils import get_console, show_error, show_info, show_success, show_warning

console = get_console()


class PredictorLoader:
    def __init__(self, price_data: pd.DataFrame) -> None:
        """初始化 PredictorLoader，必須提供價格數據"""
        self.price_data = price_data
        self.predictor_file_name = None  # NOTE: translated to English.

    def load(self) -> Optional[Union[pd.DataFrame, str]]:
        """載入預測因子數據，與價格數據對齊並合併"""
        try:
            # NOTE: translated to English.
            file_path = self._get_file_path()
            if file_path == "__SKIP_STATANALYSER__":
                self.predictor_file_name = None  # NOTE: translated to English.
                return "__SKIP_STATANALYSER__"
            if file_path is None:
                return None

            # NOTE: translated to English.
            self.predictor_file_name = os.path.splitext(os.path.basename(file_path))[0]

            # NOTE: translated to English.
            time_format = self._get_time_format()

            # NOTE: translated to English.
            data = self._read_file(file_path)
            if data is None:
                return None

            # NOTE: translated to English.
            data = self._process_time_column(data, file_path, time_format)
            if data is None:
                return None

            # NOTE: translated to English.
            merged_data = self._clean_and_merge_data(data)
            if merged_data is None:
                return None

            self._show_success_message(merged_data)
            return merged_data

        except Exception as e:
            show_error("DATALOADER", f"PredictorLoader 錯誤：{e}")
            return None

    def _get_file_path(self) -> Optional[str]:
        """??????????? config / deterministic fallback?"""
        explicit_path = getattr(self, "predictor_file_path", None) or getattr(self, "file_path", None)
        if explicit_path:
            explicit_path_obj = Path(explicit_path)
            if not explicit_path_obj.is_absolute():
                project_root = Path(__file__).parent.parent
                explicit_path_obj = project_root / explicit_path_obj
            if explicit_path_obj.exists():
                return str(explicit_path_obj)
            show_warning("DATALOADER", f"?????????????????{explicit_path_obj}")

        import_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "records",
            "dataloader",
            "import",
        )
        found_files = self._scan_for_files(import_dir)

        if not found_files:
            return "__SKIP_STATANALYSER__"

        if len(found_files) > 1:
            show_warning(
                "DATALOADER",
                f"????????????????????{os.path.basename(found_files[0])}"
            )
        return found_files[0]

    def _get_time_format(self) -> Optional[str]:
        """?????????"""
        return getattr(self, "time_format", None)

    def _scan_for_files(self, import_dir: str) -> List[str]:
        """掃描指定目錄下的檔案"""
        file_patterns = ["*.xlsx", "*.xls", "*.csv", "*.json"]
        found_files = []
        for pat in file_patterns:
            found_files.extend(glob.glob(os.path.join(import_dir, pat)))
        return sorted(found_files)

    def _detect_and_convert_timestamp_predictor(
        self, data: pd.DataFrame, time_col: str = "Time"
    ) -> pd.DataFrame:
        """
        檢測並轉換timestamp格式為標準datetime格式

        如果時間欄位是timestamp格式（Unix時間戳），自動轉換為datetime
        支援秒級和毫秒級timestamp

        Args:
            data: 數據DataFrame
            time_col: 時間欄位名稱，預設為 "Time"

        Returns:
            轉換後的DataFrame
        """
        if time_col not in data.columns:
            return data

        try:
            # NOTE: translated to English.
            if pd.api.types.is_datetime64_any_dtype(data[time_col]):
                show_info("DATALOADER", "Time欄位已經是datetime格式，跳過轉換")
                return data

            # NOTE: translated to English.
            sample_value = data[time_col].iloc[0]

            show_info("DATALOADER",
                f"🔍 檢測timestamp：\n"
                f"   sample_value = {sample_value}\n"
                f"   type = {type(sample_value)}\n"
                f"   isinstance(int/float) = {isinstance(sample_value, (int, float))}\n"
                f"   is_numeric_dtype = {pd.api.types.is_numeric_dtype(data[time_col])}"
            )

            # NOTE: translated to English.
            if pd.api.types.is_numeric_dtype(data[time_col]):
                # NOTE: translated to English.
                import numpy as np
                if isinstance(sample_value, (int, float, np.integer, np.floating)):
                    if sample_value > 1e10:  # NOTE: translated to English.
                        show_info("DATALOADER", "檢測到毫秒級timestamp格式，正在轉換...")
                        data[time_col] = pd.to_datetime(data[time_col], unit="ms")
                    else:  # NOTE: translated to English.
                        show_info("DATALOADER", "檢測到秒級timestamp格式，正在轉換...")
                        data[time_col] = pd.to_datetime(data[time_col], unit="s")

                    show_success("DATALOADER", f"timestamp轉換成功，格式為：{data[time_col].iloc[0]}")
                else:
                    show_warning("DATALOADER", f"數值類型不匹配：{type(sample_value)}")
            else:
                # NOTE: translated to English.
                try:
                    numeric_value = pd.to_numeric(data[time_col].iloc[0])
                    if numeric_value > 1e10:  # NOTE: translated to English.
                        show_info("DATALOADER", "檢測到毫秒級timestamp格式，正在轉換...")
                        data[time_col] = pd.to_numeric(data[time_col])
                        data[time_col] = pd.to_datetime(data[time_col], unit="ms")
                    else:  # NOTE: translated to English.
                        show_info("DATALOADER", "檢測到秒級timestamp格式，正在轉換...")
                        data[time_col] = pd.to_numeric(data[time_col])
                        data[time_col] = pd.to_datetime(data[time_col], unit="s")

                    show_success("DATALOADER", f"timestamp轉換成功，格式為：{data[time_col].iloc[0]}")
                except (ValueError, TypeError):
                    # NOTE: translated to English.
                    pass

        except Exception as e:
            show_warning("DATALOADER", f"timestamp檢測時出錯：{e}，將嘗試其他方式解析時間")

        return data

    def _read_file(self, file_path: str) -> Optional[pd.DataFrame]:
        """讀取檔案數據"""
        # NOTE: translated to English.
        if not os.path.exists(file_path):
            show_error("DATALOADER", f"找不到文件 '{file_path}'")
            return None

        # NOTE: translated to English.
        if file_path.endswith(".xlsx"):
            data = pd.read_excel(file_path, engine="openpyxl")
        elif file_path.endswith(".csv"):
            data = pd.read_csv(file_path)
        else:
            show_error("DATALOADER", "僅支持 .xlsx 或 .csv 格式")
            return None

        show_success("DATALOADER", f"載入檔案 '{file_path}' 成功，原始欄位：{list(data.columns)}")
        return data

    def _process_time_column(
        self, data: pd.DataFrame, file_path: str, time_format: Optional[str]
    ) -> Optional[pd.DataFrame]:
        """處理時間欄位"""
        # NOTE: translated to English.
        time_col = self._identify_time_col(data.columns, file_path)
        if not time_col:
            show_error("DATALOADER", "無法確定時間欄位，程式終止")
            return None

        # NOTE: translated to English.
        if time_col != "Time" and "Time" in data.columns:
            # NOTE: translated to English.
            show_warning("DATALOADER", f"檢測到多個時間欄位，將使用 '{time_col}' 作為主要時間欄位")
            data = data.drop(columns=["Time"])

        data = data.rename(columns={time_col: "Time"})

        # NOTE: translated to English.
        show_info("DATALOADER",
            f"🔍 重命名後Time欄位信息：\n"
            f"   第一個值：{data['Time'].iloc[0]}\n"
            f"   數據類型：{data['Time'].dtype}\n"
            f"   是否為數值：{pd.api.types.is_numeric_dtype(data['Time'])}"
        )

        # NOTE: translated to English.
        data = self._detect_and_convert_timestamp_predictor(data, "Time")

        # NOTE: translated to English.
        show_info("DATALOADER",
            f"🔍 轉換後Time欄位信息：\n"
            f"   第一個值：{data['Time'].iloc[0]}\n"
            f"   數據類型：{data['Time'].dtype}\n"
            f"   是否為datetime：{pd.api.types.is_datetime64_any_dtype(data['Time'])}"
        )

        try:
            # NOTE: translated to English.
            if not pd.api.types.is_datetime64_any_dtype(data["Time"]):
                # NOTE: translated to English.
                if time_format:
                    data["Time"] = pd.to_datetime(
                        data["Time"], format=time_format, errors="coerce"
                    )
                else:
                    # NOTE: translated to English.
                    data["Time"] = pd.to_datetime(
                        data["Time"], dayfirst=True, errors="coerce"
                    )
            else:
                show_success("DATALOADER", "時間欄位已為datetime格式，跳過轉換")

            if data["Time"].isna().sum() > 0:
                show_warning("DATALOADER",
                    f"{data['Time'].isna().sum()} 個時間值無效，將移除\n"
                    f"以下是檔案的前幾行數據：\n{data.head()}\n"
                    f"建議：請檢查 '{file_path}' 的 'Time' 欄，"
                    f"確保日期格式為 YYYY-MM-DD（如 31-12-2000）或其他一致格式"
                )
                data = data.dropna(subset=["Time"])

        except Exception as e:
            show_error("DATALOADER",
                f"時間格式轉換失敗：{e}\n"
                f"以下是檔案的前幾行數據：\n{data.head()}\n"
                f"建議：請檢查 '{file_path}' 的 'Time' 欄，"
                f"確保日期格式為 YYYY-MM-DD（如 2023-01-01）或其他一致格式"
            )
            return None

        return data

    def _clean_and_merge_data(self, data: pd.DataFrame) -> Optional[pd.DataFrame]:
        """清洗並合併數據"""
        # NOTE: translated to English.
        show_info("DATALOADER",
            f"🔍 清洗前Time欄位信息：\n"
            f"   第一個值：{data['Time'].iloc[0]}\n"
            f"   數據類型：{data['Time'].dtype}\n"
            f"   是否為datetime：{pd.api.types.is_datetime64_any_dtype(data['Time'])}"
        )

        # NOTE: translated to English.
        try:
            from dataloader.validator_loader import DataValidator

            validator = DataValidator(data)
            cleaned_data = validator.validate_and_clean()
        except ImportError:
            # NOTE: translated to English.
            cleaned_data = self._basic_clean_data(data)

        # NOTE: translated to English.
        if cleaned_data is not None and not cleaned_data.empty:
            show_info("DATALOADER",
                f"🔍 清洗後Time欄位信息：\n"
                f"   第一個值：{cleaned_data['Time'].iloc[0]}\n"
                f"   數據類型：{cleaned_data['Time'].dtype}\n"
                f"   是否為datetime：{pd.api.types.is_datetime64_any_dtype(cleaned_data['Time'])}"
            )

        if cleaned_data is None or cleaned_data.empty:
            show_error("DATALOADER", "資料清洗後為空")
            return None

        # NOTE: translated to English.
        return self._align_and_merge(cleaned_data)

    def _basic_clean_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """基本數據清洗，當無法導入 DataValidator 時使用"""
        # NOTE: translated to English.
        data = data.dropna(axis=1, how="all")
        # NOTE: translated to English.
        data = data.dropna(axis=0, how="all")
        # NOTE: translated to English.
        numeric_cols = data.select_dtypes(include=["number"]).columns
        data[numeric_cols] = data[numeric_cols].fillna(0)
        return data

    def _show_success_message(self, merged_data: pd.DataFrame) -> None:
        """顯示成功訊息"""
        show_success("DATALOADER", f"合併數據成功，行數：{len(merged_data)}")

    def get_diff_options(self, series: pd.Series) -> List[str]:
        """獲取差分選項"""
        if (series == 0).any():
            return ["sub"]  # NOTE: translated to English.
        else:
            return ["sub", "div"]  # NOTE: translated to English.

    def apply_diff(self, series: pd.Series, diff_type: str) -> pd.Series:
        """應用差分"""
        if diff_type == "sub":
            diff = series.diff()
        elif diff_type == "div":
            diff = series.pct_change()
        else:
            raise ValueError("未知差分方式")
        return diff

    def process_difference(
        self, data: pd.DataFrame, predictor_col: str
    ) -> Tuple[pd.DataFrame, List[str], pd.Series]:
        """
        處理預測因子的差分選項 - 自動判斷並執行差分

        Args:
            data: 原始數據
            predictor_col: 預測因子欄名

        Returns:
            tuple: (updated_data, diff_cols, used_series)
        """
        df = data.copy()
        factor_series = df[predictor_col]

        # NOTE: translated to English.
        has_zero = (factor_series == 0).any()
        diff_cols = [predictor_col]
        diff_col_map = {predictor_col: factor_series}

        if has_zero:
            show_warning("DATALOADER", f"檢測到 {predictor_col} 包含 0 值，只能進行減數差分")
            diff_series = factor_series.diff().fillna(0)
            diff_col_name = predictor_col + "_diff_sub"
            diff_cols.append(diff_col_name)
            diff_col_map[diff_col_name] = diff_series
            used_series = diff_series
            diff_msg = (
                f"已產生減數差分欄位 {diff_col_name}\n"
                f"差分處理完成，新增欄位：{[col for col in diff_cols if col != predictor_col]}"
            )
            show_success("DATALOADER", diff_msg)
        else:
            show_info("DATALOADER", f"{predictor_col} 無 0 值，同時產生減數差分和除數差分")
            diff_series_sub = factor_series.diff().fillna(0)
            diff_series_div = factor_series.pct_change().fillna(0)
            diff_col_name_sub = predictor_col + "_diff_sub"
            diff_col_name_div = predictor_col + "_diff_div"
            diff_cols.extend([diff_col_name_sub, diff_col_name_div])
            diff_col_map[diff_col_name_sub] = diff_series_sub
            diff_col_map[diff_col_name_div] = diff_series_div
            used_series = diff_series_sub
            diff_msg = (
                f"已產生減數差分欄位 {diff_col_name_sub} 和除數差分欄位 "
                f"{diff_col_name_div}\n差分處理完成，新增欄位："
                f"{[col for col in diff_cols if col != predictor_col]}"
            )
            show_success("DATALOADER", diff_msg)

        # NOTE: translated to English.
        for col, series in diff_col_map.items():
            df[col] = series

        # NOTE: translated to English.
        preview = df.head(10)
        table = Table(
            title="目前數據（含差分欄位）", show_lines=True, border_style="#dbac30"
        )
        for col in preview.columns:
            table.add_column(str(col), style="bold white")
        for _, row in preview.iterrows():
            table.add_row(
                *[
                    (
                        f"[#1e90ff]{v}[/#1e90ff]"
                        if isinstance(v, (int, float, complex))
                        and not isinstance(v, bool)
                        else str(v)
                    )
                    for v in row
                ]
            )
        console.print(table)

        return df, diff_cols, used_series

    def _identify_time_col(self, columns: pd.Index, file_path: str) -> Optional[str]:
        """識別時間欄位，若自動識別失敗則詢問用戶

        優先順序：timestamp > datetime > date > time > period
        這樣可以在有多個時間欄位時選擇最適合的
        """
        # NOTE: translated to English.
        time_candidates = ["timestamp", "datetime", "date", "time", "period"]

        # NOTE: translated to English.
        matched_cols = []
        for col in columns:
            col_lower = col.lower()
            col_str = str(col)
            # NOTE: translated to English.
            has_numeric_suffix = (
                col_str.endswith('.1') or col_str.endswith('.2') or
                col_str.endswith('_1') or col_str.endswith('_2') or
                '.1' in col_str or '.2' in col_str
            )
            if col_lower in time_candidates and not has_numeric_suffix:
                matched_cols.append(col)

        # NOTE: translated to English.
        if matched_cols:
            for candidate in time_candidates:
                for col in matched_cols:
                    if col.lower() == candidate:
                        if len(matched_cols) > 1:
                            show_info("DATALOADER", f"檢測到多個時間欄位：{matched_cols}，將使用 '{col}'")
                        return col

        # NOTE: translated to English.
        for col in columns:
            col_lower = col.lower()
            if any(candidate in col_lower for candidate in time_candidates):
                # NOTE: translated to English.
                if not any(c in str(col) for c in ['.1', '.2', '_1', '_2']):
                    return col

        # NOTE: translated to English.
        show_warning("DATALOADER", f"無法自動識別 '{file_path}' 的時間欄位")
        show_info("DATALOADER", f"可用欄位：{list(columns)}")
        console.print(
            "[bold #dbac30]請指定時間欄位（輸入欄位名稱，例如 'Date'）：[/bold #dbac30]"
        )
        while True:
            user_col = columns[0] if len(columns) > 0 else None
            if user_col in columns:
                return user_col
            show_error("DATALOADER", f"錯誤：'{user_col}' 不在欄位中，請選擇以下欄位之一：{list(columns)}")

    def _align_and_merge(self, predictor_data: pd.DataFrame) -> Optional[pd.DataFrame]:
        """與價格數據進行時間對齊並合併"""
        try:
            # NOTE: translated to English.
            price_data = self.price_data.copy()
            if "Time" not in price_data.index.names:
                if "Time" in price_data.columns:
                    price_data = price_data.set_index("Time")
                else:
                    show_error("DATALOADER", "錯誤：價格數據缺少 'Time' 欄位或索引")
                    return None

            # NOTE: translated to English.
            if "Time" not in predictor_data.index.names:
                if "Time" in predictor_data.columns:
                    predictor_data = predictor_data.set_index("Time")
                else:
                    show_error("DATALOADER", "錯誤：預測因子數據缺少 'Time' 欄位或索引")
                    return None

            # NOTE: translated to English.
            show_info("DATALOADER",
                f"📅 價格數據時間範圍：\n"
                f"   起始：{price_data.index.min()}\n"
                f"   結束：{price_data.index.max()}\n"
                f"   筆數：{len(price_data)}\n"
                f"   類型：{price_data.index.dtype}\n\n"
                f"📅 預測因子時間範圍：\n"
                f"   起始：{predictor_data.index.min()}\n"
                f"   結束：{predictor_data.index.max()}\n"
                f"   筆數：{len(predictor_data)}\n"
                f"   類型：{predictor_data.index.dtype}"
            )

            # NOTE: translated to English.
            merged = price_data.merge(
                predictor_data, left_index=True, right_index=True, how="inner",
                suffixes=('', '_predictor')
            )

            if merged.empty:
                show_error("DATALOADER",
                    "錯誤：價格數據與預測因子數據無時間交集，無法合併\n\n"
                    "可能原因：\n"
                    "1. 時間範圍沒有重疊\n"
                    "2. 時間精度不同（一個精確到秒，一個只有日期）\n"
                    "3. 時區不同\n\n"
                    "建議：請檢查上方的時間範圍診斷信息"
                )
                return None

            # NOTE: translated to English.
            merged = merged.reset_index()

            show_success("DATALOADER", f"成功合併！交集筆數：{len(merged)}")

            return merged

        except Exception as e:
            show_error("DATALOADER", f"時間對齊與合併錯誤：{e}")
            return None
