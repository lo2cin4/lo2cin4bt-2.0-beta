"""
File_loader.py

【功能說明】
------------------------------------------------------------
本模組為 Lo2cin4BT 數據載入器，負責從本地 Excel、CSV 等檔案載入行情數據，支援多種格式、欄位自動標準化，並確保數據結構與下游模組一致。

【流程與數據流】
------------------------------------------------------------
- 由 DataLoader 或 DataImporter 調用，作為行情數據來源之一
- 載入數據後傳遞給 DataValidator、ReturnCalculator、BacktestEngine 等模組

```mermaid
flowchart TD
    A[DataLoader/DataImporter] -->|選擇本地檔案| B(File_loader)
    B -->|載入數據| C[DataValidator]
    C -->|驗證清洗| D[ReturnCalculator]
    D -->|計算收益率| E[BacktestEngine/下游模組]
```

【維護與擴充重點】
------------------------------------------------------------
- 新增/修改支援格式、欄位時，請同步更新頂部註解與下游流程
- 若欄位標準化邏輯有變動，需同步更新本檔案與 base_loader
- 檔案格式、欄位結構如有調整，請同步通知協作者

【常見易錯點】
------------------------------------------------------------
- 檔案不存在或格式錯誤會導致載入失敗
- 欄位缺失或型態不符會影響下游驗證與計算
- 欄位標準化未同步更新，易導致資料結構不一致

【範例】
------------------------------------------------------------
- loader = FileLoader()
  df = loader.load()
- 可於 DataLoader 互動式選擇本地檔案作為行情來源

【與其他模組的關聯】
------------------------------------------------------------
- 由 DataLoader/DataImporter 調用，數據傳遞給 DataValidator、ReturnCalculator、BacktestEngine
- 需與 base_loader 介面保持一致

【參考】
------------------------------------------------------------
- base_loader.py、DataValidator、ReturnCalculator
- 專案 README
"""

import glob  # NOTE: translated to English.
import os  # NOTE: translated to English.
from typing import List, Optional, Tuple

import pandas as pd  # NOTE: translated to English.

from dataloader.validator_loader import print_dataframe_table

from .base_loader import AbstractDataLoader


class FileLoader(AbstractDataLoader):
    def load(self) -> Tuple[Optional[pd.DataFrame], str]:
        """從 Excel 或 CSV 文件載入數據
        使用模組:
            - pandas (pd): 讀取 Excel/CSV 文件（read_excel, read_csv），數據處理
            - os: 檢查文件是否存在（os.path.exists）
            - glob: 檢測目錄內的文件
        功能: 交互式選擇文件來源，讀取 Excel/CSV 文件，標準化欄位並返回數據
        返回: pandas DataFrame 或 None（若載入失敗）
        """
        # NOTE: translated to English.
        file_name = getattr(self, "file_path", None)
        if not file_name:
            file_name = self._get_file_path()

        if file_name is None:
            self.show_error(
                "FileLoader requires file_path in config; interactive selection is disabled."
            )
            return None

        # NOTE: translated to English.
        frequency = getattr(self, "interval", None)
        if not frequency:
            frequency = self._get_frequency()

        # NOTE: translated to English.
        return self._read_and_process_file(file_name, frequency)

    def _get_file_path(self) -> Optional[str]:
        """??????????? config / deterministic fallback?"""
        file_name = getattr(self, "file_path", None)
        if file_name:
            return file_name

        import_dir = os.path.join("records", "dataloader", "import")
        available_files = self._get_available_files(import_dir)

        if len(available_files) == 1:
            return available_files[0]

        if available_files:
            selected_file = available_files[0]
            self.show_warning(
                f"???????????????????{os.path.basename(selected_file)}?"
                f"?? config ????? file_path?"
            )
            return selected_file

        self.show_error(
            f"No import files found in {import_dir}; set file_path explicitly in config."
        )
        return None

    def _get_frequency(self) -> str:
        """???????"""
        return self.get_frequency("1d")

    def _read_and_process_file(
        self, file_name: str, frequency: str
    ) -> Optional[Tuple[pd.DataFrame, str]]:
        """讀取並處理文件
        參數:
            file_name: 文件路徑
            frequency: 數據頻率
        返回: (DataFrame, frequency) 或 None
        """
        try:
            # NOTE: translated to English.
            if not os.path.exists(file_name):
                self.show_error(f"找不到文件 '{file_name}'")
                return None

            # NOTE: translated to English.
            data = self._read_file(file_name)
            if data is None:
                return None

            # NOTE: translated to English.
            data = self.standardize_columns(data)

            # NOTE: translated to English.
            data = self.detect_and_convert_timestamp(data, "Time")

            # NOTE: translated to English.
            self._show_success_info(data)
            return data, frequency

        except Exception as e:
            self.show_error(f"讀取文件時出錯：{e}")
            return None

    def _read_file(self, file_name: str) -> Optional[pd.DataFrame]:
        """根據文件擴展名讀取文件
        參數:
            file_name: 文件路徑
        返回: DataFrame 或 None
        """
        if file_name.endswith(".xlsx"):
            return pd.read_excel(file_name)
        elif file_name.endswith(".csv"):
            return pd.read_csv(file_name)
        else:
            self.show_error("僅支援 .xlsx 或 .csv 文件")
            return None

    def _show_success_info(self, data: pd.DataFrame) -> None:
        """顯示成功載入信息
        參數:
            data: 載入的數據
        """
        print_dataframe_table(data.head(), title="數據加載成功，預覽（前5行）")
        self.show_success(f"數據加載成功，行數：{len(data)}")

    def _get_available_files(self, directory: str) -> List[str]:
        """檢測目錄內可用的 xlsx 和 csv 文件
        參數:
            directory: str - 要檢測的目錄路徑
        返回: list - 可用文件列表
        """
        if not os.path.exists(directory):
            return []

        # NOTE: translated to English.
        xlsx_files = glob.glob(os.path.join(directory, "*.xlsx"))
        csv_files = glob.glob(os.path.join(directory, "*.csv"))

        # NOTE: translated to English.
        return sorted(xlsx_files + csv_files)

    def _standardize_columns(self, data: pd.DataFrame) -> pd.DataFrame:
        """將數據欄位標準化為 Time, Open, High, Low, Close, Volume - now delegates to base class"""
        # First use base class standardization
        data = super().standardize_columns(data)

        # NOTE: translated to English.
        required_cols = ["Time", "Open", "High", "Low", "Close"]
        missing_cols = [
            col for col in required_cols if col not in data.columns
        ]  # NOTE: translated to English.
        if missing_cols:
            self.show_warning(f"缺少欄位 {missing_cols}，將從用戶輸入補充")
            for col in missing_cols:
                data[col] = pd.NA  # NOTE: translated to English.

        # NOTE: translated to English.
        if "Volume" not in data.columns:  # NOTE: translated to English.
            self.show_warning(
                "數據缺少 Volume 欄位，已自動填充 0.0；如需其他處理請在上游 config 修正。"
            )
            data["Volume"] = 0.0  # NOTE: translated to English.

        return data
