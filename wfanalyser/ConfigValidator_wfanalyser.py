"""Validation helpers for WFA analyser configs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.table import Table

from backtester.StrategyRunConfig_backtester import StrategyRunConfigError, normalize_wfa_run_config
from utils import show_error, show_success, show_warning


class ConfigValidator:
    """Validate WFA JSON configs across legacy and semantic runtime modes."""

    def __init__(self) -> None:
        self.required_fields = ["wfa_config", "dataloader", "backtester", "metricstracker"]
        self.module_required_fields = {
            "wfa_config": ["mode", "train_set_percentage", "test_set_percentage", "step_size"],
            "dataloader": ["source", "start_date"],
            "backtester": [],
            "metricstracker": ["enable_metrics_analysis"],
        }
        self.valid_run_types = {"production", "test"}
        self.valid_optimizer_modes = {"single_objective", "multi_objective"}
        self.valid_samplers = {"tpe", "nsga2", "gp"}
        self.valid_pruners = {"hyperband", "median", "successive_halving", "none"}
        self.valid_representatives = {"cluster_median", "cluster_center"}
        self.valid_ranking_profiles = {"balanced", "stability_first", "performance_first", "drawdown_aware"}
        self.forbidden_wfa_handoff_fields = {
            "shortlist_candidates",
            "review_mode",
            "shortlist_source_run_id",
            "pack_strategy",
            "pack_preview",
        }

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

    def validate_config(self, config_file: str) -> bool:
        try:
            config = self._load_config(config_file)
            if config is None:
                return False
            if self._is_wfa_run(config):
                return self._validate_wfa_run_config(config, config_file=config_file)
            if not self._validate_structure(config):
                return False
            if not self._validate_content(config):
                return False
            return True
        except Exception as exc:  # pragma: no cover - defensive
            self._display_validation_error(f"Validation failed: {exc}", Path(config_file).name)
            return False

    def validate_configs(self, config_files: List[str]) -> List[bool]:
        return [self.validate_config(config_file) for config_file in config_files]

    def get_validation_errors(self, config_file: str) -> List[str]:
        errors: List[str] = []
        config = self._load_config(config_file)
        if config is None:
            return ["Failed to load config"]
        if self._is_wfa_run(config):
            return self._check_wfa_run_errors(config, config_file=config_file)
        errors.extend(self._check_structure_errors(config))
        errors.extend(self._check_content_errors(config))
        return errors

    def _load_config(self, config_file: str) -> Optional[Dict[str, Any]]:
        try:
            with open(config_file, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                self._display_validation_error("Config must be a JSON object", Path(config_file).name)
                return None
            return data
        except FileNotFoundError:
            self._display_validation_error("Config file not found", Path(config_file).name)
            return None
        except json.JSONDecodeError as exc:
            self._display_validation_error(f"Invalid JSON: {exc}", Path(config_file).name)
            return None
        except Exception as exc:  # pragma: no cover - defensive
            self._display_validation_error(f"Failed to load config: {exc}", Path(config_file).name)
            return None

    def _validate_structure(self, config: Dict[str, Any]) -> bool:
        if self._is_wfa_run(config):
            return self._validate_wfa_run_config(config)
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
                    self._display_validation_error(f"Missing {module}.{field}", "structure")
                    return False
        return True

    def _validate_content(self, config: Dict[str, Any]) -> bool:
        if self._is_wfa_run(config):
            return self._validate_wfa_run_config(config)
        return all(
            [
                self._validate_platform_config(config.get("platform", {})),
                self._validate_wfa_config(config.get("wfa_config", {})),
                self._validate_dataloader_config(config.get("dataloader", {})),
                self._validate_backtester_config(config.get("backtester", {})),
                self._validate_metricstracker_config(config.get("metricstracker", {})),
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

    def _validate_wfa_config(self, config: Dict[str, Any]) -> bool:
        forbidden_fields = sorted(self.forbidden_wfa_handoff_fields.intersection(config.keys()))
        if forbidden_fields:
            self._display_validation_error(
                "WFA config must be driven by strategy parameter ranges, not Parameter Matrix handoff fields: "
                + ", ".join(forbidden_fields),
                "wfa_config",
            )
            return False

        mode = config.get("mode")
        if mode not in ["standard", "anchored"]:
            self._display_validation_error(
                f"Unsupported WFA mode: {mode}; expected standard/anchored",
                "wfa_config",
            )
            return False

        train_pct = config.get("train_set_percentage")
        test_pct = config.get("test_set_percentage")
        if not isinstance(train_pct, (int, float)) or not (0 < train_pct <= 1):
            self._display_validation_error(
                "train_set_percentage must be in (0, 1]",
                "wfa_config",
            )
            return False
        if not isinstance(test_pct, (int, float)) or not (0 < test_pct <= 1):
            self._display_validation_error(
                "test_set_percentage must be in (0, 1]",
                "wfa_config",
            )
            return False
        if train_pct + test_pct > 1.0:
            self._display_validation_error(
                "train_set_percentage + test_set_percentage must be <= 1.0",
                "wfa_config",
            )
            return False

        step_size = config.get("step_size")
        if not isinstance(step_size, int) or step_size <= 0:
            self._display_validation_error("step_size must be a positive integer", "wfa_config")
            return False
        target_window_count = config.get("target_window_count")
        if target_window_count is not None and (
            not isinstance(target_window_count, int) or target_window_count <= 1
        ):
            self._display_validation_error(
                "target_window_count must be an integer greater than 1",
                "wfa_config",
            )
            return False

        objectives = config.get("optimization_objectives", ["sharpe", "calmar"])
        if not isinstance(objectives, list) or not objectives:
            self._display_validation_error(
                "optimization_objectives must be a non-empty list",
                "wfa_config",
            )
            return False
        for obj in objectives:
            if obj not in ["sharpe", "calmar"]:
                self._display_validation_error(
                    f"Unsupported optimization objective: {obj}",
                    "wfa_config",
                )
                return False

        if not self._validate_optimizer_config(config.get("optimizer", {})):
            return False
        if not self._validate_acceptance_config(config.get("acceptance", {})):
            return False
        if not self._validate_robust_selection_config(config.get("robust_selection", {})):
            return False
        if not self._validate_ranking_config(config.get("ranking", {})):
            return False

        return True

    def _validate_optimizer_config(self, config: Dict[str, Any]) -> bool:
        if not config:
            return True
        optimizer_type = str(config.get("type", "optuna")).strip().lower()
        if optimizer_type != "optuna":
            self._display_validation_error("wfa_config.optimizer.type must be optuna", "wfa_config.optimizer")
            return False
        mode = str(config.get("mode", "single_objective")).strip().lower()
        if mode not in self.valid_optimizer_modes:
            self._display_validation_error(
                "wfa_config.optimizer.mode must be single_objective or multi_objective",
                "wfa_config.optimizer",
            )
            return False
        sampler = str(config.get("sampler", "tpe")).strip().lower()
        if sampler not in self.valid_samplers:
            self._display_validation_error(
                "wfa_config.optimizer.sampler must be one of tpe/nsga2/gp",
                "wfa_config.optimizer",
            )
            return False
        pruner = str(config.get("pruner", "hyperband")).strip().lower()
        if pruner not in self.valid_pruners:
            self._display_validation_error(
                "wfa_config.optimizer.pruner must be one of hyperband/median/successive_halving/none",
                "wfa_config.optimizer",
            )
            return False
        for key in ("n_trials", "n_startup_trials", "timeout_seconds"):
            value = config.get(key)
            if value is None:
                continue
            if not isinstance(value, int) or value <= 0:
                self._display_validation_error(
                    f"wfa_config.optimizer.{key} must be a positive integer",
                    "wfa_config.optimizer",
                )
                return False
        multivariate = config.get("multivariate")
        if multivariate is not None and not isinstance(multivariate, bool):
            self._display_validation_error(
                "wfa_config.optimizer.multivariate must be bool",
                "wfa_config.optimizer",
            )
            return False
        return True

    def _validate_acceptance_config(self, config: Dict[str, Any]) -> bool:
        if not config:
            return True
        numeric_keys = [
            "min_oos_is_ratio",
            "max_drawdown_floor",
            "min_profit_factor",
            "min_win_rate",
            "min_trade_count",
        ]
        for key in numeric_keys:
            value = config.get(key)
            if value is None:
                continue
            if not isinstance(value, (int, float)):
                self._display_validation_error(
                    f"wfa_config.acceptance.{key} must be numeric",
                    "wfa_config.acceptance",
                )
                return False
        return True

    def _validate_robust_selection_config(self, config: Dict[str, Any]) -> bool:
        if not config:
            return True
        enabled = config.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            self._display_validation_error(
                "wfa_config.robust_selection.enabled must be bool",
                "wfa_config.robust_selection",
            )
            return False
        pick = str(config.get("pick", "cluster_median")).strip().lower()
        if pick not in self.valid_representatives:
            self._display_validation_error(
                "wfa_config.robust_selection.pick must be cluster_median or cluster_center",
                "wfa_config.robust_selection",
            )
            return False
        top_n = config.get("top_n_candidates")
        if top_n is not None and (not isinstance(top_n, int) or top_n <= 0):
            self._display_validation_error(
                "wfa_config.robust_selection.top_n_candidates must be a positive integer",
                "wfa_config.robust_selection",
            )
            return False
        method = str(config.get("cluster_method", "kmeans")).strip().lower()
        if method != "kmeans":
            self._display_validation_error(
                "wfa_config.robust_selection.cluster_method must be kmeans",
                "wfa_config.robust_selection",
            )
            return False
        return True

    def _validate_ranking_config(self, config: Dict[str, Any]) -> bool:
        if not config:
            return True
        profile = str(config.get("profile", "balanced")).strip().lower()
        if profile not in self.valid_ranking_profiles:
            self._display_validation_error(
                "wfa_config.ranking.profile must be one of balanced/stability_first/performance_first/drawdown_aware",
                "wfa_config.ranking",
            )
            return False
        weights = config.get("weights", {})
        if weights is not None and not isinstance(weights, dict):
            self._display_validation_error(
                "wfa_config.ranking.weights must be a dict",
                "wfa_config.ranking",
            )
            return False
        if isinstance(weights, dict):
            for key, value in weights.items():
                if not isinstance(value, (int, float)):
                    self._display_validation_error(
                        f"wfa_config.ranking.weights.{key} must be numeric",
                        "wfa_config.ranking",
                    )
                    return False
        sort_priority = config.get("sort_priority")
        if sort_priority is not None:
            if not isinstance(sort_priority, list) or not all(isinstance(item, str) for item in sort_priority):
                self._display_validation_error(
                    "wfa_config.ranking.sort_priority must be a list of strings",
                    "wfa_config.ranking",
                )
                return False
        return True

    def _validate_dataloader_config(self, config: Dict[str, Any]) -> bool:
        source = config.get("source")
        valid_sources = ["yfinance", "binance", "coinbase", "file"]
        if source not in valid_sources:
            self._display_validation_error(
                f"Unsupported dataloader source: {source}",
                "dataloader",
            )
            return False

        start_date = config.get("start_date")
        if start_date and not self._validate_date_format(str(start_date)):
            self._display_validation_error(
                "start_date must be YYYY-MM-DD",
                "dataloader",
            )
            return False

        if source == "file":
            file_path = config.get("file_config", {}).get("file_path")
            if not isinstance(file_path, str) or not file_path.strip():
                self._display_validation_error(
                    "file source requires dataloader.file_config.file_path",
                    "dataloader",
                )
                return False

        return True

    def _validate_backtester_config(self, config: Dict[str, Any]) -> bool:
        strategy_mode = str(config.get("strategy_mode", "auto")).strip().lower()
        if strategy_mode not in {"semantic", "auto"}:
            self._display_validation_error(
                "backtester.strategy_mode must be one of semantic/auto",
                "backtester",
            )
            return False

        has_strategy_contract_path = isinstance(config.get("strategy_contract_path"), str) and bool(
            str(config.get("strategy_contract_path")).strip()
        )

        if strategy_mode == "auto":
            if not has_strategy_contract_path:
                self._display_validation_error(
                    "backtester.strategy_mode=auto requires strategy_contract_path",
                    "backtester",
                )
                return False

        if not has_strategy_contract_path:
            self._display_validation_error(
                "semantic mode requires backtester.strategy_contract_path",
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

        return True

    def _validate_metricstracker_config(self, config: Dict[str, Any]) -> bool:
        enable = config.get("enable_metrics_analysis")
        if enable is not None and not isinstance(enable, bool):
            self._display_validation_error(
                "metricstracker.enable_metrics_analysis must be bool",
                "metricstracker",
            )
            return False
        return True

    @staticmethod
    def _validate_date_format(date_str: str) -> bool:
        return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", date_str))

    def _check_structure_errors(self, config: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        if self._is_wfa_run(config):
            return self._check_wfa_run_errors(config)
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
        if self._is_wfa_run(config):
            return self._check_wfa_run_errors(config)

        wfa_config = config.get("wfa_config", {})
        forbidden_fields = sorted(self.forbidden_wfa_handoff_fields.intersection(wfa_config.keys()))
        if forbidden_fields:
            errors.append(
                "WFA config forbids Parameter Matrix handoff fields: "
                + ", ".join(f"wfa_config.{field}" for field in forbidden_fields)
            )
        if wfa_config.get("mode") not in ["standard", "anchored"]:
            errors.append(f"Unsupported WFA mode: {wfa_config.get('mode')}")
        optimizer = wfa_config.get("optimizer", {})
        if optimizer:
            mode = str(optimizer.get("mode", "single_objective")).strip().lower()
            sampler = str(optimizer.get("sampler", "tpe")).strip().lower()
            pruner = str(optimizer.get("pruner", "hyperband")).strip().lower()
            if mode not in self.valid_optimizer_modes:
                errors.append("wfa_config.optimizer.mode must be single_objective or multi_objective")
            if sampler not in self.valid_samplers:
                errors.append("wfa_config.optimizer.sampler must be one of tpe/nsga2/gp")
            if pruner not in self.valid_pruners:
                errors.append("wfa_config.optimizer.pruner must be one of hyperband/median/successive_halving/none")

        dataloader = config.get("dataloader", {})
        source = dataloader.get("source")
        if source not in ["yfinance", "binance", "coinbase", "file"]:
            errors.append(f"Unsupported dataloader source: {source}")

        backtester = config.get("backtester", {})
        strategy_mode = str(backtester.get("strategy_mode", "auto")).strip().lower()
        if strategy_mode not in {"semantic", "auto"}:
            errors.append("backtester.strategy_mode must be one of semantic/auto")
            return errors

        has_strategy_contract_path = isinstance(backtester.get("strategy_contract_path"), str) and bool(
            str(backtester.get("strategy_contract_path")).strip()
        )
        if strategy_mode == "auto":
            if not has_strategy_contract_path:
                errors.append("backtester.strategy_mode=auto requires strategy_contract_path")
                return errors

        if not has_strategy_contract_path:
            errors.append("semantic mode requires backtester.strategy_contract_path")
        legacy_fields = self._has_removed_legacy_fields(backtester)
        if legacy_fields:
            errors.append(
                "semantic mode forbids legacy fields: "
                + ", ".join(f"backtester.{field}" for field in legacy_fields)
            )

        return errors

    @staticmethod
    def _is_wfa_run(config: Dict[str, Any]) -> bool:
        return dict(config or {}).get("schema_version") == "wfa_run"

    def _validate_wfa_run_config(self, config: Dict[str, Any], *, config_file: str | None = None) -> bool:
        errors = self._check_wfa_run_errors(config, config_file=config_file)
        if errors:
            self._display_validation_error(errors[0], "wfa_run")
            return False
        return True

    @staticmethod
    def _check_wfa_run_errors(config: Dict[str, Any], *, config_file: str | None = None) -> List[str]:
        try:
            normalize_wfa_run_config(config, source_path=config_file)
            return []
        except StrategyRunConfigError as exc:
            return [str(exc)]
        except Exception as exc:  # pragma: no cover - defensive
            return [f"wfa_run validation failed: {exc}"]

    def _display_validation_error(self, message: str, context: str = "") -> None:
        show_error("WFANALYSER", message)

    def display_validation_summary(self, config_files: List[str], results: List[bool]) -> None:
        success_count = sum(results)
        total_count = len(results)

        table = Table(title="WFA config validation summary")
        table.add_column("File", style="magenta")
        table.add_column("Status", style="cyan")
        table.add_column("Errors", style="red")

        for config_file, validation_result in zip(config_files, results):
            file_name = Path(config_file).name
            status = "PASS" if validation_result else "FAIL"
            errors = self.get_validation_errors(config_file) if not validation_result else []
            error_text = "; ".join(errors[:3]) if errors else "-"
            if len(errors) > 3:
                error_text += f" ... ({len(errors)} total)"
            table.add_row(file_name, status, error_text)

        from .utils.ConsoleUtils_utils_wfanalyser import get_console

        get_console().print(table)

        if success_count == total_count:
            show_success("WFANALYSER", f"All {total_count} config files passed validation")
        else:
            show_warning(
                "WFANALYSER",
                f"Validation complete: {success_count}/{total_count} passed",
            )
