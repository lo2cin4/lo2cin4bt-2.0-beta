"""WFA analyser entrypoint."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional

from .utils.ConsoleUtils_utils_wfanalyser import get_console
from utils import show_error, show_info, show_success, show_welcome
from utils.path_resolver import ensure_outputs_structure, ensure_workspace_structure

console = get_console()


class BaseWFAAnalyser:
    """Run WFA configs in JSON mode."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("lo2cin4bt.wfanalyser")
        self.console = get_console()

        self.project_root = Path(__file__).resolve().parent.parent
        self.workspace_paths = ensure_workspace_structure(self.project_root)
        self.outputs_paths = ensure_outputs_structure(self.project_root)

        self.configs_dir = self.workspace_paths["wfa"]
        self.output_dir = self.outputs_paths["wfanalyser"]

        self._ensure_directories()

        self.config_loader = None
        self.config_validator = None
        self.config_selector = None
        self.data_loader = None
        self.walk_forward_engine = None
        self.results_exporter = None

    def _ensure_directories(self) -> None:
        directories = [
            self.configs_dir,
            self.output_dir,
            self.project_root / "logs",
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def run(self) -> None:
        self.logger.info("Starting WFA analyser")

        try:
            self._display_welcome()
            self.run_json_mode()
        except Exception as exc:
            self.logger.error("WFA analyser failed: %s", exc)
            self._display_error(f"WFA analyser failed: {exc}")
            raise

    def run_json_mode(self) -> None:
        self.logger.info("Running JSON mode")

        try:
            from wfanalyser.ConfigLoader_wfanalyser import ConfigLoader
            from wfanalyser.ConfigSelector_wfanalyser import ConfigSelector
            from wfanalyser.ConfigValidator_wfanalyser import ConfigValidator

            self.config_loader = ConfigLoader()
            self.config_validator = ConfigValidator()
            self.config_selector = ConfigSelector(
                self.configs_dir,
            )

            selected_configs = self.config_selector.discover_configs()
            if not selected_configs:
                return

            valid_configs = self._validate_configs(selected_configs)
            if not valid_configs:
                return

            config_data_list = self._load_configs(valid_configs)
            if not config_data_list:
                return

            self._execute_wfa_configs(config_data_list)

        except Exception as exc:
            self.logger.error("JSON mode failed: %s", exc)
            self._display_error(f"JSON mode failed: {exc}")
            raise

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

    def _execute_wfa_configs(self, config_data_list: List[Any]) -> None:
        for i, config_data in enumerate(config_data_list, 1):
            try:
                self._execute_wfa_single(config_data, i, len(config_data_list))
            except Exception as exc:
                self.logger.error("Config %s failed: %s", getattr(config_data, "file_name", i), exc)
                self._display_error(f"Config failed: {exc}")
                continue

    def _execute_wfa_single(
        self,
        config_data: Any,
        current: int = 1,
        total: int = 1,
    ) -> None:
        self._display_execution_progress(current, total, getattr(config_data, "file_name", "config"))

        try:
            from wfanalyser.WalkForwardEngine_wfanalyser import WalkForwardEngine

            self.walk_forward_engine = WalkForwardEngine(config_data, logger=self.logger)
            results = self.walk_forward_engine.run()

            if results:
                from wfanalyser.ResultsExporter_wfanalyser import ResultsExporter

                data = results.get("data") if isinstance(results, dict) else None
                self.results_exporter = ResultsExporter(
                    results,
                    output_dir=self.output_dir,
                    config_data=config_data,
                    logger=self.logger,
                    data=data,
                )
                self.results_exporter.export()
                self._display_success("WFA completed")
            else:
                self._display_error("WFA failed")

        except ImportError as exc:
            self._display_error(f"Missing component: {exc}")
            self.logger.error("Missing component: %s", exc)
        except Exception as exc:
            self._display_error(f"WFA execution failed: {exc}")
            self.logger.error("WFA execution failed: %s", exc)
            raise

    def _display_welcome(self) -> None:
        content = (
            "[bold #dbac30]lo2cin4bt Walk-Forward Analysis[/bold #dbac30]\n"
            "[white]Workspace-only config discovery is enabled.[/white]\n\n"
            "[white]Primary input:[/white] workspace/wfa"
        )
        show_welcome("Walk-Forward Analysis", content)

    def _display_execution_progress(self, current: int, total: int, config_name: str) -> None:
        show_info("WFANALYSER", f"Running WFA config {current}/{total}: {config_name}")

    def _display_error(self, message: str) -> None:
        show_error("WFANALYSER", message)

    def _display_success(self, message: str) -> None:
        show_success("WFANALYSER", message)
