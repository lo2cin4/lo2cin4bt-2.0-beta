

import os  # NOTE: translated to English.

import pandas as pd

from utils import show_error, show_success, get_console

console = get_console()


class DataExporter:
    def __init__(self, data: pd.DataFrame) -> None:

        self.data = data  # NOTE: translated to English.

    def export(self) -> None:
        """????? JSON, CSV ? XLSX?????? records ???"""
        try:
            choice = str(getattr(self, "export_format", "1"))
            if choice not in ["1", "2", "3"]:
                choice = "1"

            default_name = "output_data"
            file_name = getattr(self, "file_name", None) or default_name

            records_dir = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "records", "dataloader"
            )
            os.makedirs(records_dir, exist_ok=True)

            if choice == "1":
                file_path = os.path.join(records_dir, f"{file_name}.csv")
                self.data.to_csv(file_path, index=False)
                show_success("DATALOADER", f"??????? CSV?{file_path}")
            elif choice == "2":
                file_path = os.path.join(records_dir, f"{file_name}.xlsx")
                self.data.to_excel(file_path, index=False, engine="openpyxl")
                show_success("DATALOADER", f"??????? XLSX?{file_path}")
            else:
                file_path = os.path.join(records_dir, f"{file_name}.json")
                self.data.to_json(
                    file_path, orient="records", lines=True, date_format="iso"
                )
                show_success("DATALOADER", f"??????? JSON?{file_path}")

        except PermissionError:
            show_error("DATALOADER", f"錯誤：無法寫入檔案 '{file_path}'，請檢查權限或關閉已開啟的檔案")
        except Exception as e:
            show_error("DATALOADER", f"數據導出錯誤：{e}")
