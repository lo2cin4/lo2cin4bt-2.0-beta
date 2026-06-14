"""Autorunner entrypoint."""

from __future__ import annotations

import gc
import logging
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from autorunner.BacktestRunner_autorunner import BacktestRunnerAutorunner
from autorunner.ConfigLoader_autorunner import ConfigLoader
from autorunner.ConfigSelector_autorunner import ConfigSelector
from autorunner.ConfigValidator_autorunner import ConfigValidator
from autorunner.DataLoader_autorunner import DataLoaderAutorunner
from autorunner.MetricsRunner_autorunner import MetricsRunnerAutorunner
from autorunner.StatAnalyserRunner_autorunner import StatAnalyserRunnerAutorunner
from autorunner.utils import get_console
from utils import show_error, show_info, show_welcome
from utils.path_resolver import ensure_outputs_structure, ensure_workspace_structure

console = get_console()


class BaseAutorunner:
    """Run one or more autorunner backtest configs."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("lo2cin4bt.autorunner")
        self.console = get_console()

        self.project_root = Path(__file__).resolve().parent.parent
        self.workspace_paths = ensure_workspace_structure(self.project_root)
        self.outputs_paths = ensure_outputs_structure(self.project_root)

        self.configs_dir = self.workspace_paths["runs"]
        self.templates_dir = self.project_root / "autorunner" / "templates"

        self._ensure_directories()

        self.config_selector = ConfigSelector(
            self.configs_dir,
            self.templates_dir,
        )
        self.config_validator = ConfigValidator()
        self.config_loader = ConfigLoader()
        self.data_loader = DataLoaderAutorunner(logger=self.logger)

        self.data_loader_frequency: Optional[str] = None
        self.backtest_runner: Optional[Any] = None
        self.metrics_runner: Optional[Any] = None
        self.statanalyser_runner: Optional[Any] = None

    def _ensure_directories(self) -> None:
        directories = [
            self.configs_dir,
            self.templates_dir,
            self.project_root / "logs",
            self.outputs_paths["backtester"],
            self.outputs_paths["metricstracker"],
            self.outputs_paths["statanalyser"],
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def run(self) -> None:
        self.logger.info("Starting autorunner")
        try:
            self._display_welcome()

            selected_configs = self._select_configs()
            if not selected_configs:
                return

            valid_configs = self._validate_configs(selected_configs)
            if not valid_configs:
                return

            config_data_list = self._load_configs(valid_configs)
            if not config_data_list:
                return

            self._execute_configs(config_data_list)

        except Exception as exc:
            self.logger.error("autorunner failed: %s", exc)
            self._display_error(f"autorunner failed: {exc}")
            raise

    def _display_welcome(self) -> None:
        content = (
            "[bold #dbac30]lo2cin4bt Autorunner[/bold #dbac30]\n"
            "[white]Workspace-only config discovery is enabled.[/white]\n\n"
            "[white]Primary input:[/white] workspace/runs"
        )
        show_welcome("Autorunner", content)

    def _select_configs(self) -> List[str]:
        selected = self.config_selector.select_configs()
        if not selected:
            self._display_error("No runnable config selected")
            return []
        return selected

    def _validate_configs(self, config_files: List[str]) -> List[str]:
        validation_results = self.config_validator.validate_configs(config_files)
        self.config_validator.display_validation_summary(config_files, validation_results)

        valid_configs: List[str] = []
        for config_file, is_valid in zip(config_files, validation_results):
            if is_valid:
                valid_configs.append(config_file)
        return valid_configs

    def _load_configs(self, config_files: List[str]) -> List[Any]:
        return self.config_loader.load_configs(config_files)

    def _execute_configs(self, config_data_list: List[Any]) -> None:
        for i, config_data in enumerate(config_data_list, 1):
            try:
                self._execute_single_config(config_data, i, len(config_data_list))
            except Exception as exc:
                self._display_error(f"Config {config_data.file_name} failed: {exc}")
                continue

    def _execute_single_config(self, config_data: Any, current: int, total: int) -> None:
        self._display_execution_progress(current, total, config_data.file_name)

        full_dataloader_config = {
            **config_data.dataloader_config,
            "predictor_config": config_data.predictor_config,
            "__config_file_path": config_data.file_path,
        }
        data = self.data_loader.load_data(full_dataloader_config)
        self.data_loader_frequency = self.data_loader.frequency

        if data is None:
            self._display_error("Failed to load data")
            return

        self.data_loader.display_loading_summary()

        if getattr(self.data_loader, "using_price_predictor_only", False):
            predictor_col = getattr(self.data_loader, "current_predictor_column", None)
            if predictor_col:
                config_data.backtester_config["selected_predictor"] = predictor_col

        backtest_results = self._execute_backtest(
            data,
            config_data.backtester_config,
            config_data,
        )
        if backtest_results is None:
            self._display_error("Backtest failed")
            return

        self._display_backtest_summary(backtest_results)
        self._execute_metrics(backtest_results, config_data.metricstracker_config)
        self._execute_statanalyser(data, config_data.statanalyser_config, config_data)

        del data, backtest_results
        gc.collect()

    def _execute_backtest(
        self,
        data: Any,
        backtest_config: Dict[str, Any],
        config_data: Any = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            backtest_runner = BacktestRunnerAutorunner()
            if config_data:
                config = {
                    "backtester": {
                        **backtest_config,
                        "__config_file_path": config_data.file_path,
                    },
                    "dataloader": {
                        **config_data.dataloader_config,
                        "frequency": self.data_loader_frequency
                        or config_data.dataloader_config.get("frequency", "1D"),
                        "predictor_config": config_data.predictor_config,
                        "__config_file_path": config_data.file_path,
                    },
                }
            else:
                config = {
                    "backtester": backtest_config,
                    "dataloader": {
                        "frequency": self.data_loader_frequency or "1D",
                    },
                }
            return backtest_runner.run_backtest(data, config)
        except Exception as exc:
            show_error(
                "AUTORUNNER",
                f"Backtest execution failed: {exc}\n\n{traceback.format_exc()}",
            )
            return None

    def _display_backtest_summary(self, backtest_results: Dict[str, Any]) -> None:
        try:
            if not backtest_results:
                return

            results = backtest_results.get("results", [])
            if not results:
                return

            from backtester.TradeRecordExporter_backtester import TradeRecordExporter_backtester

            exporter = TradeRecordExporter_backtester(
                trade_records=pd.DataFrame(),
                frequency=backtest_results.get(
                    "frequency", self.data_loader_frequency or "1D"
                ),
                results=results,
                data=pd.DataFrame(),
                Backtest_id=backtest_results.get(
                    "Backtest_id", backtest_results.get("config", {}).get("Backtest_id", "")
                ),
                predictor_file_name=backtest_results.get("predictor_file_name", ""),
                predictor_column=backtest_results.get("predictor_column", ""),
                symbol=backtest_results.get("symbol"),
                **backtest_results.get(
                    "trading_params",
                    backtest_results.get("config", {}).get("trading_params", {}),
                ),
            )
            exporter.display_backtest_summary()
        except Exception as exc:
            self.logger.warning("Failed to render backtest summary: %s", exc)

    def _display_execution_progress(self, current: int, total: int, config_name: str) -> None:
        show_info(
            "AUTORUNNER",
            f"Running config {current}/{total}: {config_name}",
        )

    def _display_error(self, message: str) -> None:
        show_error("AUTORUNNER", message)

    def _execute_metrics(
        self,
        backtest_results: Dict[str, Any],
        metrics_config: Dict[str, Any],
    ) -> None:
        try:
            self.metrics_runner = self.metrics_runner or MetricsRunnerAutorunner(
                logger=self.logger
            )
            summary = self.metrics_runner.run(backtest_results, metrics_config)
            if summary:
                self.logger.info("Metrics summary: %s", summary)
        except Exception as exc:
            show_error("AUTORUNNER", f"Metrics stage failed: {exc}")

    def _execute_statanalyser(
        self,
        data: pd.DataFrame,
        statanalyser_config: Dict[str, Any],
        config_data: Any,
    ) -> None:
        try:
            enabled = statanalyser_config.get("enabled", False)
            if isinstance(enabled, str):
                enabled = enabled.strip().lower() in {"1", "true", "yes", "y"}
            elif isinstance(enabled, (int, float)):
                enabled = bool(enabled)

            if not enabled:
                self.logger.info("Statanalyser disabled, skip")
                return

            self.statanalyser_runner = self.statanalyser_runner or StatAnalyserRunnerAutorunner(
                logger=self.logger
            )
            summary = self.statanalyser_runner.run(
                data,
                {
                    "dataloader": config_data.dataloader_config,
                    "backtester": config_data.backtester_config,
                    "metricstracker": config_data.metricstracker_config,
                    "statanalyser": statanalyser_config,
                },
            )
            if summary:
                self.logger.info("Statanalyser summary: %s", summary)
        except Exception as exc:
            show_error("AUTORUNNER", f"Statanalyser stage failed: {exc}")


if __name__ == "__main__":
    test_logger = logging.getLogger("test")
    test_logger.setLevel(logging.DEBUG)
    BaseAutorunner(logger=test_logger).run()
