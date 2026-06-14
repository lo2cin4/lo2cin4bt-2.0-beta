"""Validation helpers for autorunner configs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.table import Table

from backtester.StrategyRunConfig_backtester import (
    StrategyRunConfigError,
    validate_strategy_run_config,
)
from utils import show_error, show_success, show_warning

VALID_ENGINE_MODES = {"auto", "node_ir"}


class ConfigValidator:
    def __init__(self) -> None:
        self.required_fields = ["dataloader", "backtester", "metricstracker"]
        self.module_required_fields = {
            "dataloader": ["source", "start_date"],
            "backtester": [],
            "metricstracker": ["enable_metrics_analysis"],
        }
        self.valid_run_types = {"production", "test"}

    def _parse_bool_like(self, value: Any) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ("1", "true", "yes", "y"):
                return True
            if normalized in ("0", "false", "no", "n", ""):
                return False
        return None

    def _parse_non_negative_int_like(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, float) and value.is_integer():
            candidate = int(value)
            return candidate if candidate >= 0 else None
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                return int(text)
        return None

    def _parse_positive_range_like(self, value: Any) -> Optional[List[int]]:
        if isinstance(value, int) and value > 0:
            return [value]
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                candidate = int(text)
                return [candidate] if candidate > 0 else None
            parts = [part.strip() for part in text.split(":")]
            if len(parts) == 3 and all(part.isdigit() for part in parts):
                start, end, step = map(int, parts)
                if start <= 0 or end <= 0 or step <= 0 or start > end:
                    return None
                return list(range(start, end + 1, step))
        return None

    def _indicator_base(self, indicator: Any) -> str:
        text = str(indicator or "").strip().upper()
        match = re.match(r"([A-Z_]+)", text)
        return match.group(1) if match else text

    @staticmethod
    def _has_removed_legacy_fields(config: Dict[str, Any]) -> List[str]:
        legacy_fields: List[str] = []
        condition_pairs = config.get("condition_pairs")
        if isinstance(condition_pairs, list) and condition_pairs:
            legacy_fields.append("condition_pairs")
        indicator_params = config.get("indicator_params")
        if isinstance(indicator_params, dict) and indicator_params:
            legacy_fields.append("indicator_params")
        return legacy_fields

    def _validate_nday_usage(
        self,
        backtester_config: Dict[str, Any],
        errors: Optional[List[str]] = None,
    ) -> bool:
        target_errors = errors if errors is not None else []
        condition_pairs = backtester_config.get("condition_pairs", [])
        indicator_params = backtester_config.get("indicator_params", {})

        for strategy_idx, pair in enumerate(condition_pairs, start=1):
            for indicator in pair.get("entry", []) or []:
                if self._indicator_base(indicator) == "NDAY":
                    target_errors.append(
                        "NDAY currently only supports exit conditions because the timer starts after actual entry fills"
                    )

            for indicator in pair.get("exit", []) or []:
                if self._indicator_base(indicator) != "NDAY":
                    continue
                param_key = f"{indicator}_strategy_{strategy_idx}"
                params_config = indicator_params.get(param_key)
                if not isinstance(params_config, dict):
                    target_errors.append(f"{param_key} requires indicator_params")
                    continue

                raw_n_days = params_config.get("n_days_range", params_config.get("n_days"))
                if self._parse_positive_range_like(raw_n_days) is None:
                    target_errors.append(f"{param_key} requires positive n_days or n_days_range")

        return not target_errors

    def validate_config(self, config_file: str) -> bool:
        try:
            config = self._load_config(config_file)
            if config is None:
                return False
            if self._is_strategy_run(config):
                return self._validate_strategy_run_config(config)
            if not self._validate_structure(config):
                return False
            if not self._validate_content(config):
                return False
            return True
        except Exception as e:  # pragma: no cover - defensive
            show_error("AUTORUNNER", f"Validation failed: {e}")
            self._display_validation_error(f"Validation failed: {e}", Path(config_file).name)
            return False

    def validate_configs(self, config_files: List[str]) -> List[bool]:
        return [self.validate_config(config_file) for config_file in config_files]

    def get_validation_errors(self, config_file: str) -> List[str]:
        errors: List[str] = []

        try:
            config = self._load_config(config_file)
            if config is None:
                errors.append("Failed to load config")
                return errors
            if self._is_strategy_run(config):
                return self._check_strategy_run_errors(config)

            errors.extend(self._check_structure_errors(config))
            errors.extend(self._check_content_errors(config))
            return errors
        except Exception as e:  # pragma: no cover - defensive
            errors.append(f"Validation error: {e}")
            return errors

    def _load_config(self, config_file: str) -> Optional[Dict[str, Any]]:
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            self._display_validation_error("Config file not found", Path(config_file).name)
            return None
        except json.JSONDecodeError as e:
            self._display_validation_error(f"Invalid JSON: {e}", Path(config_file).name)
            return None
        except Exception as e:  # pragma: no cover - defensive
            self._display_validation_error(f"Failed to load config: {e}", Path(config_file).name)
            return None

    def _validate_structure(self, config: Dict[str, Any]) -> bool:
        if self._is_strategy_run(config):
            return self._validate_strategy_run_config(config)
        for field in self.required_fields:
            if field not in config:
                self._display_validation_error(f"Missing top-level field: {field}", "structure")
                return False

        for module, required_fields in self.module_required_fields.items():
            module_config = config.get(module, {})
            if not isinstance(module_config, dict):
                self._display_validation_error(f"{module} must be a dict", "structure")
                return False
            for field in required_fields:
                if field not in module_config:
                    self._display_validation_error(
                        f"Missing {module}.{field}", "structure"
                    )
                    return False
        return True

    def _validate_content(self, config: Dict[str, Any]) -> bool:
        if self._is_strategy_run(config):
            return self._validate_strategy_run_config(config)
        return all(
            [
                self._validate_platform_config(config.get("platform", {})),
                self._validate_dataloader_config(config.get("dataloader", {})),
                self._validate_backtester_config(config.get("backtester", {})),
                self._validate_metricstracker_config(config.get("metricstracker", {})),
                self._validate_statanalyser_config(config.get("statanalyser", {})),
            ]
        )

    def _validate_platform_config(self, config: Dict[str, Any]) -> bool:
        if not config:
            return True
        run_type = str(config.get("run_type", "")).strip().lower()
        if not run_type:
            return True
        if run_type not in self.valid_run_types:
            self._display_validation_error(
                "platform.run_type must be one of production/test",
                "platform",
            )
            return False
        return True

    def _validate_dataloader_config(self, config: Dict[str, Any]) -> bool:
        try:
            source = config.get("source")
            valid_sources = ["yfinance", "binance", "coinbase", "file", "multi_asset"]
            if source not in valid_sources:
                self._display_validation_error(
                    f"Unsupported dataloader source: {source}",
                    "dataloader",
                )
                return False

            start_date = config.get("start_date")
            if start_date and not self._validate_date_format(str(start_date)):
                return False

            if source == "file":
                file_config = config.get("file_config", {})
                file_path = file_config.get("file_path")
                if not isinstance(file_path, str) or not file_path.strip():
                    self._display_validation_error(
                        "file source requires file_config.file_path",
                        "dataloader",
                    )
                    return False

            return True
        except Exception as e:  # pragma: no cover - defensive
            self._display_validation_error(f"dataloader validation failed: {e}", "dataloader")
            return False

    def _validate_backtester_config(self, config: Dict[str, Any]) -> bool:
        try:
            strategy_mode = str(config.get("strategy_mode", "auto")).strip().lower()
            if strategy_mode not in {"semantic", "auto", "multi_asset_portfolio", "single_asset_portfolio"}:
                self._display_validation_error(
                    "backtester.strategy_mode must be one of semantic/auto/multi_asset_portfolio/single_asset_portfolio",
                    "backtester",
                )
                return False
            if strategy_mode in {"multi_asset_portfolio", "single_asset_portfolio"}:
                portfolio_config = config.get("portfolio_config")
                if strategy_mode == "multi_asset_portfolio" and not isinstance(portfolio_config, dict):
                    self._display_validation_error(
                        "backtester.portfolio_config is required for multi_asset_portfolio",
                        "backtester",
                    )
                    return False
                return True

            has_strategy_contract_path = isinstance(config.get("strategy_contract_path"), str) and bool(
                str(config.get("strategy_contract_path")).strip()
            )
            if strategy_mode == "auto":
                if not has_strategy_contract_path:
                    self._display_validation_error(
                        "backtester.strategy_mode=auto requires strategy_contract_path or portfolio mode",
                        "backtester",
                    )
                    return False

            if not has_strategy_contract_path:
                self._display_validation_error(
                    "backtester.strategy_contract_path is required in semantic mode",
                    "backtester",
                )
                return False
            legacy_fields = self._has_removed_legacy_fields(config)
            if legacy_fields:
                self._display_validation_error(
                    "semantic mode forbids legacy fields: "
                    + ", ".join(f"backtester.{field}" for field in legacy_fields),
                    "backtester",
                )
                return False

            engine_mode = config.get("engine_mode", "auto")
            if not isinstance(engine_mode, str) or engine_mode.strip().lower() not in VALID_ENGINE_MODES:
                self._display_validation_error(
                    "backtester.engine_mode must be one of auto/node_ir",
                    "backtester",
                )
                return False

            trading_params = config.get("trading_params", {})
            if not isinstance(trading_params, dict):
                self._display_validation_error(
                    "backtester.trading_params must be a dict",
                    "backtester",
                )
                return False

            for param in ["transaction_cost", "slippage", "trade_delay"]:
                value = trading_params.get(param)
                if value is not None and (not isinstance(value, (int, float)) or value < 0):
                    self._display_validation_error(
                        f"backtester.trading_params.{param} must be non-negative numeric",
                        "backtester",
                    )
                    return False

            holding_period_days = trading_params.get("holding_period_days")
            parsed_holding_period_days = self._parse_non_negative_int_like(holding_period_days)
            if holding_period_days is not None and parsed_holding_period_days is None:
                self._display_validation_error(
                    "backtester.trading_params.holding_period_days must be non-negative integer",
                    "backtester",
                )
                return False

            export_config = config.get("export_config", {})
            if not isinstance(export_config, dict):
                self._display_validation_error(
                    "backtester.export_config must be a dict",
                    "backtester",
                )
                return False

            for field in ["export_parquet", "export_csv", "export_excel"]:
                value = export_config.get(field)
                # Backward compatible: accept bool-like strings/ints (older JSON configs
                # may store these values as "true"/"false").
                if value is None:
                    continue
                if self._parse_bool_like(value) is None:
                    self._display_validation_error(
                        f"backtester.export_config.{field} must be bool",
                        "backtester",
                    )
                    return False

            return True
        except Exception as e:  # pragma: no cover - defensive
            self._display_validation_error(f"backtester validation failed: {e}", "backtester")
            return False

    def _validate_metricstracker_config(self, config: Dict[str, Any]) -> bool:
        try:
            enable = config.get("enable_metrics_analysis")
            parsed_enable = self._parse_bool_like(enable)
            if enable is not None and parsed_enable is None:
                self._display_validation_error(
                    "metricstracker.enable_metrics_analysis must be bool",
                    "metricstracker",
                )
                return False
            if not parsed_enable:
                return True

            for field in ["risk_free_rate", "time_unit"]:
                value = config.get(field)
                if value is not None and not isinstance(value, (int, float, str)):
                    self._display_validation_error(
                        f"metricstracker.{field} must be numeric or string",
                        "metricstracker",
                    )
                    return False

            return True
        except Exception as e:  # pragma: no cover - defensive
            self._display_validation_error(f"metricstracker validation failed: {e}", "metricstracker")
            return False

    def _validate_date_format(self, date_str: str) -> bool:
        if not isinstance(date_str, str):
            self._display_validation_error("start_date must be a string", "dataloader")
            return False
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            self._display_validation_error(
                f"Invalid date format: {date_str}; expected YYYY-MM-DD",
                "dataloader",
            )
            return False
        return True

    def _check_structure_errors(self, config: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        if self._is_strategy_run(config):
            return self._check_strategy_run_errors(config)

        for field in self.required_fields:
            if field not in config:
                errors.append(f"Missing top-level field: {field}")

        for module, required_fields in self.module_required_fields.items():
            module_config = config.get(module, {})
            if not isinstance(module_config, dict):
                errors.append(f"{module} must be a dict")
                continue
            for field in required_fields:
                if field not in module_config:
                    errors.append(f"Missing {module}.{field}")

        return errors

    def _check_content_errors(self, config: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        if self._is_strategy_run(config):
            return self._check_strategy_run_errors(config)

        dataloader = config.get("dataloader", {})
        source = dataloader.get("source")
        if source not in ["yfinance", "binance", "coinbase", "file", "multi_asset"]:
            errors.append(f"Unsupported dataloader source: {source}")

        backtester = config.get("backtester", {})
        strategy_mode = str(backtester.get("strategy_mode", "auto")).strip().lower()
        if strategy_mode not in {"semantic", "auto", "multi_asset_portfolio", "single_asset_portfolio"}:
            errors.append("backtester.strategy_mode must be one of semantic/auto/multi_asset_portfolio/single_asset_portfolio")

        if strategy_mode in {"multi_asset_portfolio", "single_asset_portfolio"}:
            if strategy_mode == "multi_asset_portfolio" and not isinstance(backtester.get("portfolio_config"), dict):
                errors.append("backtester.portfolio_config is required for multi_asset_portfolio")
            return errors

        has_strategy_contract_path = isinstance(backtester.get("strategy_contract_path"), str) and bool(
            str(backtester.get("strategy_contract_path")).strip()
        )
        if strategy_mode == "auto":
            if not has_strategy_contract_path:
                errors.append("backtester.strategy_mode=auto requires strategy_contract_path or portfolio mode")
                return errors

        if not has_strategy_contract_path:
            errors.append("backtester.strategy_contract_path is required in semantic mode")
        legacy_fields = self._has_removed_legacy_fields(backtester)
        if legacy_fields:
            errors.append(
                "semantic mode forbids legacy fields: "
                + ", ".join(f"backtester.{field}" for field in legacy_fields)
            )

        engine_mode = backtester.get("engine_mode", "auto")
        if not isinstance(engine_mode, str) or engine_mode.strip().lower() not in VALID_ENGINE_MODES:
            errors.append("backtester.engine_mode must be one of auto/node_ir")

        export_config = backtester.get("export_config", {})
        if isinstance(export_config, dict):
            for field in ["export_parquet", "export_csv", "export_excel"]:
                value = export_config.get(field)
                if value is not None and not isinstance(value, bool):
                    errors.append(f"backtester.export_config.{field} must be bool")

        trading_params = backtester.get("trading_params", {})
        if isinstance(trading_params, dict):
            holding_period_days = trading_params.get("holding_period_days")
            if (
                holding_period_days is not None
                and self._parse_non_negative_int_like(holding_period_days) is None
            ):
                errors.append(
                    "backtester.trading_params.holding_period_days must be non-negative integer"
                )

        statanalyser = config.get("statanalyser", {})
        if isinstance(statanalyser, dict):
            enabled = self._parse_bool_like(statanalyser.get("enabled"))
            if statanalyser.get("enabled") is not None and enabled is None:
                errors.append("statanalyser.enabled must be bool")
            if enabled:
                errors.extend(self._check_statanalyser_errors(statanalyser))

        return errors

    @staticmethod
    def _is_strategy_run(config: Dict[str, Any]) -> bool:
        return dict(config or {}).get("schema_version") == "strategy_run"

    def _validate_strategy_run_config(self, config: Dict[str, Any]) -> bool:
        errors = self._check_strategy_run_errors(config)
        if errors:
            self._display_validation_error(errors[0], "strategy_run")
            return False
        return True

    @staticmethod
    def _check_strategy_run_errors(config: Dict[str, Any]) -> List[str]:
        try:
            validate_strategy_run_config(config)
            return []
        except StrategyRunConfigError as exc:
            return [str(exc)]
        except Exception as exc:  # pragma: no cover - defensive
            return [f"strategy_run validation failed: {exc}"]

    def _validate_statanalyser_config(self, config: Dict[str, Any]) -> bool:
        try:
            if not config:
                return True
            if not isinstance(config, dict):
                self._display_validation_error("statanalyser must be a dict", "statanalyser")
                return False

            enabled = self._parse_bool_like(config.get("enabled"))
            if config.get("enabled") is not None and enabled is None:
                self._display_validation_error(
                    "statanalyser.enabled must be bool",
                    "statanalyser",
                )
                return False
            if not enabled:
                return True

            target = config.get("target", {})
            if not isinstance(target, dict):
                self._display_validation_error(
                    "statanalyser.target must be a dict",
                    "statanalyser",
                )
                return False
            for field in ["predictor_column", "return_column", "diff_mode"]:
                value = target.get(field)
                if value is not None and not isinstance(value, str):
                    self._display_validation_error(
                        f"statanalyser.target.{field} must be a string",
                        "statanalyser",
                    )
                    return False

            tests = config.get("tests", {})
            if not isinstance(tests, dict):
                self._display_validation_error(
                    "statanalyser.tests must be a dict",
                    "statanalyser",
                )
                return False
            if not self._validate_statanalyser_tests(tests):
                return False

            report = config.get("report", {})
            if not isinstance(report, dict):
                self._display_validation_error(
                    "statanalyser.report must be a dict",
                    "statanalyser",
                )
                return False
            for field in ["include_plots", "include_raw_tables", "fail_on_error"]:
                value = report.get(field)
                if value is not None and self._parse_bool_like(value) is None:
                    self._display_validation_error(
                        f"statanalyser.report.{field} must be bool",
                        "statanalyser",
                    )
                    return False
            formats = report.get("formats", [])
            if formats and (
                not isinstance(formats, list)
                or any(not isinstance(item, str) for item in formats)
            ):
                self._display_validation_error(
                    "statanalyser.report.formats must be a list of strings",
                    "statanalyser",
                )
                return False

            return True
        except Exception as e:  # pragma: no cover - defensive
            self._display_validation_error(f"statanalyser validation failed: {e}", "statanalyser")
            return False

    def _validate_statanalyser_tests(self, tests: Dict[str, Any]) -> bool:
        known_tests = {
            "stationarity",
            "correlation",
            "autocorrelation",
            "distribution",
            "seasonality",
        }
        enabled_any = False
        for test_name, test_config in tests.items():
            if test_name not in known_tests:
                self._display_validation_error(
                    f"Unsupported statanalyser test: {test_name}",
                    "statanalyser",
                )
                return False
            if not isinstance(test_config, dict):
                self._display_validation_error(
                    f"statanalyser.tests.{test_name} must be a dict",
                    "statanalyser",
                )
                return False
            enabled = self._parse_bool_like(test_config.get("enabled", True))
            if test_config.get("enabled") is not None and enabled is None:
                self._display_validation_error(
                    f"statanalyser.tests.{test_name}.enabled must be bool",
                    "statanalyser",
                )
                return False
            if not enabled:
                continue
            enabled_any = True
            output = test_config.get("output", [])
            if output and (
                not isinstance(output, list)
                or any(not isinstance(item, str) for item in output)
            ):
                self._display_validation_error(
                    f"statanalyser.tests.{test_name}.output must be a list of strings",
                    "statanalyser",
                )
                return False
            if test_name == "autocorrelation":
                lags = test_config.get("lags", [])
                if lags and (
                    not isinstance(lags, list)
                    or any(not isinstance(item, int) or item <= 0 for item in lags)
                ):
                    self._display_validation_error(
                        "statanalyser.tests.autocorrelation.lags must be a list of positive integers",
                        "statanalyser",
                    )
                    return False
            if test_name == "stationarity":
                methods = test_config.get("methods", [])
                if methods and (
                    not isinstance(methods, list)
                    or any(not isinstance(item, str) for item in methods)
                ):
                    self._display_validation_error(
                        "statanalyser.tests.stationarity.methods must be a list of strings",
                        "statanalyser",
                    )
                    return False
        if tests and not enabled_any:
            self._display_validation_error(
                "statanalyser.enabled is true but no tests are enabled",
                "statanalyser",
            )
            return False
        return True

    def _check_statanalyser_errors(self, config: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        target = config.get("target", {})
        if isinstance(target, dict):
            for field in ["predictor_column", "return_column", "diff_mode"]:
                value = target.get(field)
                if value is not None and not isinstance(value, str):
                    errors.append(f"statanalyser.target.{field} must be a string")
        tests = config.get("tests", {})
        if isinstance(tests, dict):
            for test_name, test_config in tests.items():
                if not isinstance(test_config, dict):
                    errors.append(f"statanalyser.tests.{test_name} must be a dict")
                    continue
                output = test_config.get("output", [])
                if output and (
                    not isinstance(output, list)
                    or any(not isinstance(item, str) for item in output)
                ):
                    errors.append(
                        f"statanalyser.tests.{test_name}.output must be a list of strings"
                    )
        return errors

    def _display_validation_error(self, message: str, context: str = "") -> None:
        show_error("AUTORUNNER", message)

    def display_validation_summary(
        self, config_files: List[str], results: List[bool]
    ) -> None:
        success_count = sum(results)
        total_count = len(results)

        table = Table(title="Config validation summary")
        table.add_column("File", style="magenta")
        table.add_column("Status", style="cyan")
        table.add_column("Errors", style="red")

        for config_file, validation_result in zip(config_files, results):
            file_name = Path(config_file).name
            status = "PASS" if validation_result else "FAIL"
            errors = []
            if not validation_result:
                errors = self.get_validation_errors(config_file)
            error_text = "\n".join(errors) if errors else "-"
            table.add_row(file_name, status, error_text)

        from autorunner.utils import get_console

        get_console().print(table)
        if success_count == total_count:
            show_success("AUTORUNNER", "All config files passed validation")
        else:
            show_warning("AUTORUNNER", f"Validation complete: {success_count}/{total_count} passed")
