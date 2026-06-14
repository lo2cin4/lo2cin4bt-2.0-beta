

import os

import pandas as pd

from utils import show_error, show_info, show_step_panel

from .DataImporter_metricstracker import (
    list_parquet_files,
    select_files,
    show_parquet_files,
)
from .MetricsExporter_metricstracker import MetricsExporter


class BaseMetricTracker:
    @staticmethod
    def get_steps():

        return ["選擇要分析的 Parquet 檔案", "設定分析參數", "計算績效指標[自動]"]

    @staticmethod
    def print_step_panel(current_step: int, desc: str = ""):

        steps = BaseMetricTracker.get_steps()
        show_step_panel("METRICSTRACKER", current_step, steps, desc)

    def _print_step_panel(self, current_step: int, desc: str = ""):

        self.print_step_panel(current_step, desc)

    def run_analysis(self, directory=None):
        """????? metricstracker ?????"""
        if directory is None:
            directory = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "records", "backtester"
            )
            directory = os.path.abspath(directory)

        self._print_step_panel(
            1,
            "Parquet ???????????????? config ????????",
        )

        files = list_parquet_files(directory)
        if not files:
            show_error("METRICSTRACKER", f"????? Parquet ???{directory}")
            return False

        show_parquet_files(files)
        selected_files = getattr(self, "selected_files", None)
        if selected_files is None:
            selected_files = files
        elif isinstance(selected_files, str):
            selected_files = select_files(files, selected_files)
        else:
            selected_files = [f for f in selected_files if f in files]

        if not selected_files:
            show_error("METRICSTRACKER", "??????????????")
            return False

        file_list = "\n".join([f"  - {f}" for f in selected_files])
        show_info("METRICSTRACKER", f"???????\n{file_list}")

        for orig_parquet_path in selected_files:
            show_info("METRICSTRACKER", f"?????{orig_parquet_path}")
            self._print_step_panel(
                2,
                "- ????? config ????????????????\n"
                "- ???? metrics pipeline?????????",
            )
            time_unit, risk_free_rate = self._get_analysis_params()
            self._print_step_panel(
                3,
                "- ?? parquet ??? MetricsExporter ??????",
            )
            df = pd.read_parquet(orig_parquet_path)
            MetricsExporter.export(df, orig_parquet_path, time_unit, risk_free_rate)

        return True

    def _get_analysis_params(self):
        """???????"""
        time_unit = int(getattr(self, "time_unit", 365))
        risk_free_rate = getattr(self, "risk_free_rate", 4.0)
        if risk_free_rate > 1:
            risk_free_rate = risk_free_rate / 100

        return time_unit, float(risk_free_rate)

    def analyze(self, file_list):

        show_info("METRICSTRACKER", "收到以下檔案進行分析：\n" + "\n".join([f"  - {f}" for f in file_list]))

    def load_data(self, file_path: str):

        raise NotImplementedError

    def calculate_metrics(self, df):

        raise NotImplementedError

    def export(self, df, output_path: str):

        raise NotImplementedError
