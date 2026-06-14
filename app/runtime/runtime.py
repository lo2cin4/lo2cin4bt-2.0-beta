from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from autorunner.BacktestRunner_autorunner import BacktestRunnerAutorunner
from autorunner.ConfigLoader_autorunner import ConfigLoader as AutorunnerConfigLoader
from autorunner.ConfigValidator_autorunner import (
    ConfigValidator as AutorunnerConfigValidator,
)
from autorunner.DataLoader_autorunner import DataLoaderAutorunner
from autorunner.MetricsRunner_autorunner import MetricsRunnerAutorunner
from autorunner.StatAnalyserRunner_autorunner import StatAnalyserRunnerAutorunner
from backtester.AuditReader_backtester import AuditReaderBacktester
from backtester.UniverseConstituentsValidator_backtester import (
    CONSTITUENTS_SOURCE_TYPES,
    constituents_path_declared,
    constituents_source_ref,
    declared_constituents_hash,
    validate_historical_universe_constituents,
)
try:
    from backtester.StrategyRunConfig_backtester import (
        normalize_strategy_run_config,
        normalize_wfa_run_config,
        plan_strategy_execution,
    )
except Exception:  # pragma: no cover - optional bridge during partial deployments
    normalize_strategy_run_config = None  # type: ignore[assignment]
    normalize_wfa_run_config = None  # type: ignore[assignment]
    plan_strategy_execution = None  # type: ignore[assignment]
from utils.path_resolver import (
    ensure_outputs_structure,
    ensure_workspace_structure,
    resolve_input_path,
)
from wfanalyser.ConfigLoader_wfanalyser import ConfigLoader as WFAConfigLoader
from wfanalyser.ConfigValidator_wfanalyser import ConfigValidator as WFAConfigValidator
from wfanalyser.ResultsExporter_wfanalyser import ResultsExporter
from wfanalyser.WalkForwardEngine_wfanalyser import WalkForwardEngine

from app.runtime.labels import (
    build_trading_identity,
    canonical_artifact_filename,
    canonical_config_filename,
    canonical_stem,
    decorate_config_item,
    display_identity_label,
)

from .registry import AppRegistry

_STAGE_ORDER = [
    "config_validation",
    "dataloader",
    "backtester",
    "metricstracker",
    "statanalyser",
    "wfanalyser",
    "app_export",
]


class AppJobManager:
    """Single foreground job manager for the browser-first app."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Dict[str, Any] = {
            "running": False,
            "job_type": None,
            "label": "",
            "status": "idle",
            "run_id": None,
            "logs": [],
            "error": None,
        }
        self._thread: Optional[threading.Thread] = None

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self._current["running"],
                "job_type": self._current["job_type"],
                "label": self._current["label"],
                "status": self._current["status"],
                "run_id": self._current["run_id"],
                "logs": list(self._current["logs"]),
                "error": self._current["error"],
            }

    def start(
        self,
        job_type: str,
        label: str,
        target: Callable[[Callable[[str, str], None]], Dict[str, Any]],
    ) -> tuple[bool, str]:
        with self._lock:
            if self._current["running"]:
                return False, "An app job is already running."
            self._current = {
                "running": True,
                "job_type": job_type,
                "label": label,
                "status": "queued",
                "run_id": None,
                "logs": [f"Queued {job_type}: {label}"],
                "error": None,
            }

        def emit(stage: str, message: str) -> None:
            with self._lock:
                self._current["status"] = stage
                self._current["logs"].append(f"[{stage}] {message}")
                self._current["logs"] = self._current["logs"][-200:]

        def runner() -> None:
            try:
                emit("running", f"Starting {label}")
                result = target(emit)
                with self._lock:
                    self._current["running"] = False
                    self._current["status"] = str(result.get("status", "completed"))
                    self._current["run_id"] = result.get("run_id")
                    self._current["logs"].append(
                        f"[completed] {label} -> {result.get('run_id', 'no-run-id')}"
                    )
            except Exception as exc:  # pragma: no cover - defensive
                with self._lock:
                    self._current["running"] = False
                    self._current["status"] = "failed"
                    self._current["error"] = str(exc)
                    self._current["logs"].append(f"[failed] {exc}")

        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()
        return True, "Job started."


class AppRuntimeService:
    """Run managed app jobs and write registry/snapshot/manifests."""

    def __init__(self, repo_root: Path, logger: Optional[logging.Logger] = None):
        self.repo_root = Path(repo_root).resolve()
        self.logger = logger or logging.getLogger("lo2cin4bt.app.runtime")
        self.registry = AppRegistry(self.repo_root)
        self.outputs = ensure_outputs_structure(self.repo_root)
        self.workspace = ensure_workspace_structure(self.repo_root)
        self._ensure_included_example_configs()

    def _ensure_included_example_configs(self) -> None:
        examples_dir = self.repo_root / "backtester" / "contracts" / "strategy" / "examples"
        if not examples_dir.exists():
            return

        copy_plan = [
            ("strategy-run-*.json", self.workspace["runs"]),
            ("wfa-run-*.json", self.workspace["wfa"]),
        ]
        for pattern, target_dir in copy_plan:
            target_dir.mkdir(parents=True, exist_ok=True)
            for source_path in sorted(examples_dir.glob(pattern)):
                target_path = target_dir / source_path.name
                if target_path.exists():
                    continue
                shutil.copy2(source_path, target_path)

    def list_run_configs(self) -> List[Dict[str, Any]]:
        return self._list_configs(self.workspace["runs"], "autorunner")

    def list_wfa_configs(self) -> List[Dict[str, Any]]:
        return self._list_configs(self.workspace["wfa"], "wfa")

    def list_statanalyser_configs(self) -> List[Dict[str, Any]]:
        # Factor/statanalyser execution is not enabled from Run Center yet.  Do
        # not mirror backtest configs into the disabled factor-analysis column.
        return []

    def run_autorunner_config(
        self,
        config_path: str,
        emit: Callable[[str, str], None],
    ) -> Dict[str, Any]:
        run_id = self._new_run_id()
        run_paths = self.registry.build_run_paths(run_id)
        config_file = Path(config_path).resolve()
        config_meta = self._load_app_config_metadata(config_file)
        stage_status = self._new_stage_status(run_id, "autorunner")
        registry_payload = self._base_registry(
            run_id=run_id,
            module="autorunner",
            entrypoint="app-run-center",
            status="running",
        )
        registry_payload["config_snapshot_dir"] = str(run_paths["snapshot_dir"])
        registry_payload["artifact_manifest_path"] = str(run_paths["artifact_manifest"])
        registry_payload["dataloader_health_path"] = str(run_paths["dataloader_health"])
        registry_payload["data_lineage_manifest_path"] = str(run_paths["data_lineage_manifest"])
        self.registry.write_registry_entry(registry_payload)
        self.registry.write_stage_status(run_id, stage_status)

        emit("config_validation", f"Validating {config_file.name}")
        validator = AutorunnerConfigValidator()
        if not validator.validate_config(str(config_file)):
            validation_errors = validator.get_validation_errors(str(config_file))
            message = (
                f"Validation failed for {config_file.name}: " + "; ".join(validation_errors)
                if validation_errors
                else f"Validation failed for {config_file.name}"
            )
            return self._fail_run(
                run_id=run_id,
                registry_payload=registry_payload,
                stage_status=stage_status,
                stage_name="config_validation",
                message=message,
            )

        loader = AutorunnerConfigLoader()
        config_data = loader.load_config(str(config_file))
        if config_data is None:
            raise RuntimeError(f"Unable to load autorunner config: {config_file}")
        raw_config = self._load_json_config(config_file)
        is_strategy_run = self._is_strategy_run_config(raw_config)
        if is_strategy_run:
            self._attach_strategy_run_runtime_sections(
                config_data,
                raw_config=raw_config,
                config_file=config_file,
            )
        self._prepare_managed_autorunner_outputs(run_id, config_data)
        self._mark_stage(stage_status, "config_validation", "completed", "Config valid")
        self._write_run_snapshot(
            run_id,
            config_data=config_data,
            module="autorunner",
            execution_plan_path=None,
        )

        emit("dataloader", "Loading data")
        data_loader = DataLoaderAutorunner(logger=self.logger)
        full_dataloader_config = {
            **config_data.dataloader_config,
            "predictor_config": config_data.predictor_config,
            "__config_file_path": config_data.file_path,
        }
        if is_strategy_run and self._strategy_run_uses_internal_market_loader(raw_config):
            data = pd.DataFrame()
            self._mark_stage(
                stage_status,
                "dataloader",
                "completed",
                "Market data delegated to unified strategy_run loader",
            )
        else:
            data = data_loader.load_data(full_dataloader_config)
        if data is None:
            return self._fail_run(
                run_id=run_id,
                registry_payload=registry_payload,
                stage_status=stage_status,
                stage_name="dataloader",
                message="dataloader failed",
            )
        if not self._stage_is_completed(stage_status, "dataloader"):
            self._mark_stage(
                stage_status,
                "dataloader",
                "completed",
                f"Loaded {len(data)} rows",
            )

        emit("backtester", self._running_stage_message("Running backtest", raw_config))
        backtest_runner = BacktestRunnerAutorunner(logger=self.logger)
        backtest_config = (
            self._strategy_run_runtime_config(
                raw_config,
                backtester_config=config_data.backtester_config,
            )
            if is_strategy_run
            else {
                "dataloader": {
                    **config_data.dataloader_config,
                    "frequency": data_loader.frequency
                    or config_data.dataloader_config.get("frequency", "1D"),
                    "predictor_config": config_data.predictor_config,
                    "__config_file_path": config_data.file_path,
                },
                "backtester": {
                    **config_data.backtester_config,
                    "__config_file_path": config_data.file_path,
                },
            }
        )
        backtest_results = backtest_runner.run_backtest(
            None if self._strategy_run_uses_internal_market_loader(raw_config) else data,
            backtest_config,
        )
        if not backtest_results:
            return self._fail_run(
                run_id=run_id,
                registry_payload=registry_payload,
                stage_status=stage_status,
                stage_name="backtester",
                message="backtester failed",
            )
        self._mark_stage(
            stage_status,
            "backtester",
            "completed",
            f"Engine={backtest_results.get('resolved_engine_mode', 'unknown')}",
        )

        execution_plan_path = backtest_results.get("execution_plan_path")
        self._write_run_snapshot(
            run_id,
            config_data=config_data,
            module="autorunner",
            execution_plan_path=execution_plan_path,
        )
        self._write_backtest_result_index(run_id, backtest_results)

        metrics_summary = self._run_metrics_stage(
            stage_status,
            backtest_results,
            config_data.metricstracker_config,
            emit,
        )
        stat_summary = self._run_statanalyser_stage(
            stage_status,
            data,
            config_data,
            emit,
        )

        artifacts = self._collect_autorunner_artifacts(
            backtest_results=backtest_results,
            metrics_summary=metrics_summary,
            stat_summary=stat_summary,
        )
        artifacts = self._normalize_managed_artifact_names(
            run_id=run_id,
            module="autorunner",
            config_file=config_file,
            config_data=config_data,
            artifacts=artifacts,
        )
        primary_backtester = self._select_primary_artifact(artifacts, "backtester_parquet")
        dataloader_health = self._build_dataloader_health(
            run_id=run_id,
            dataloader_config=config_data.dataloader_config,
            data=data,
            primary_artifact=primary_backtester,
        )
        self.registry.write_snapshot_file(
            run_id, "dataloader_health.json", dataloader_health
        )
        data_lineage_manifest = self._write_data_lineage_manifest(
            run_id=run_id,
            module="autorunner",
            dataloader_config=config_data.dataloader_config,
            data=data,
            raw_config=raw_config,
            primary_artifact=primary_backtester,
            dataloader_health=dataloader_health,
        )
        lineage_path = self.registry.build_run_paths(run_id)["data_lineage_manifest"]
        artifacts.append(lineage_path)
        if stat_summary is not None:
            self.registry.write_snapshot_file(
                run_id, "statanalyser_summary.json", stat_summary
            )
        artifact_manifest = self._build_artifact_manifest(run_id, artifacts)
        chart_artifacts = self._write_backtest_chart_payloads(run_id, artifacts)
        if chart_artifacts:
            artifact_manifest["artifacts"].extend(chart_artifacts)
        self.registry.write_artifact_manifest(run_id, artifact_manifest)
        data_lineage_manifest = self._finalize_data_lineage_manifest_links(
            run_id,
            data_lineage_manifest,
        )

        emit("app_export", "Finalizing app registry")
        self._mark_stage(
            stage_status,
            "app_export",
            "completed",
            "Registry and manifests written",
        )
        final_status = "completed"
        if any(
            item["status"] == "failed" and item["optional"]
            for item in stage_status["stages"]
        ):
            final_status = "partial"
        identity = self._identity_for_config_data(run_id, "autorunner", config_file, config_data)
        registry_payload.update(
            {
                "status": final_status,
                "completed_at": self._now_iso(),
                "config_filename": config_file.name,
                "canonical_config_filename": canonical_config_filename(identity),
                "symbol": self._extract_symbol(config_data.dataloader_config),
                "frequency": data_loader.frequency
                or config_data.dataloader_config.get("frequency", "1D"),
                "strategy_mode": str(
                    config_data.backtester_config.get("strategy_mode", "auto")
                ),
                "semantic_label": canonical_stem(identity),
                "display_label": config_meta.get("display_label")
                or display_identity_label(identity, id_prefix="run"),
                "run_type": config_meta.get("run_type"),
                "warning_count": len(dataloader_health.get("warnings", [])),
                "error_count": 0,
                "stage_status_path": str(run_paths["stage_status"]),
                "lineage_status": data_lineage_manifest.get("lineage_status"),
                "audit_summary": {
                    "source_audit_id": dataloader_health.get("source_audit_id"),
                    "warning_count": len(dataloader_health.get("warnings", [])),
                },
                "artifacts_ready": sum(
                    1
                    for item in artifact_manifest["artifacts"]
                    if item["status"] == "ready"
                ),
                "artifacts_total": len(artifact_manifest["artifacts"]),
                "warnings": dataloader_health.get("warnings", []),
                "errors": [],
            }
        )
        self.registry.write_stage_status(run_id, stage_status)
        self.registry.write_registry_entry(registry_payload)
        return {"run_id": run_id, "status": final_status}

    @staticmethod
    def _load_json_config(config_file: Path) -> Dict[str, Any]:
        try:
            payload = json.loads(Path(config_file).read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _lineage_raw_config_with_wfa_strategy(
        self,
        raw_config: Dict[str, Any],
        config_file: Path,
    ) -> Dict[str, Any]:
        if raw_config.get("schema_version") != "wfa_run":
            return raw_config
        lineage_config = copy.deepcopy(raw_config)
        strategy_config = self._lineage_resolve_wfa_strategy_config(raw_config, config_file)
        if not isinstance(strategy_config, dict) or not strategy_config:
            return lineage_config
        lineage_config["strategy_run_config"] = copy.deepcopy(strategy_config)
        for key in ("universe", "data", "execution"):
            value = strategy_config.get(key)
            if isinstance(value, dict):
                lineage_config[key] = copy.deepcopy(value)
        features = strategy_config.get("features")
        if isinstance(features, list):
            lineage_config["features"] = copy.deepcopy(features)
        return lineage_config

    def _lineage_resolve_wfa_strategy_config(
        self,
        raw_config: Dict[str, Any],
        config_file: Path,
    ) -> Dict[str, Any]:
        embedded = raw_config.get("legacy_embedded_strategy_config")
        if isinstance(embedded, dict):
            return self._lineage_normalize_strategy_config(embedded, config_file)
        wfa_config = raw_config.get("wfa_config") if isinstance(raw_config.get("wfa_config"), dict) else {}
        strategy_config_path = str(
            raw_config.get("strategy_config_path")
            or wfa_config.get("strategy_config_path")
            or ""
        ).strip()
        if not strategy_config_path:
            return {}
        resolved = self._resolve_optional_config_path(strategy_config_path, config_file=config_file)
        if resolved is None or not resolved.exists():
            return {}
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
        return self._lineage_normalize_strategy_config(
            payload if isinstance(payload, dict) else {},
            resolved,
        )

    def _lineage_normalize_strategy_config(
        self,
        payload: Dict[str, Any],
        source_path: Path,
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        if normalize_strategy_run_config is None:
            return payload
        try:
            return normalize_strategy_run_config(
                payload,
                source_path=source_path,
                repo_root=self.repo_root,
            )
        except Exception:
            return payload

    @staticmethod
    def _is_strategy_run_config(config: Dict[str, Any]) -> bool:
        return dict(config or {}).get("schema_version") == "strategy_run"

    @staticmethod
    def _strategy_run_mode(config: Dict[str, Any]) -> str:
        platform = config.get("platform") if isinstance(config.get("platform"), dict) else {}
        return str(platform.get("strategy_mode_id") or "").strip().lower()

    @classmethod
    def _strategy_run_uses_internal_market_loader(cls, config: Dict[str, Any]) -> bool:
        return cls._strategy_run_mode(config) in {
            "single_asset_signal",
            "multi_factor_entry_exit_roles",
            "calendar_event_session",
            "multi_asset_portfolio",
            "multi_asset_trigger_selection",
            "dynamic_allocation_rules",
        }

    @classmethod
    def _running_stage_message(cls, base: str, config: Dict[str, Any]) -> str:
        candidate_count = cls._parameter_candidate_count(config)
        if candidate_count > 1:
            return f"{base} ({candidate_count} parameter candidates)"
        return base

    @classmethod
    def _parameter_candidate_count(cls, config: Dict[str, Any]) -> int:
        domains = config.get("parameter_domains") if isinstance(config, dict) else {}
        if not isinstance(domains, dict) or not domains:
            return 0
        count = 1
        for spec in domains.values():
            values = cls._parameter_domain_values(spec)
            if not values:
                continue
            count *= len(values)
        return count

    @staticmethod
    def _parameter_domain_values(spec: Any) -> List[Any]:
        if isinstance(spec, list):
            return list(spec)
        if not isinstance(spec, dict):
            return []
        if isinstance(spec.get("values"), list):
            return list(spec["values"])
        if str(spec.get("type", "")).lower() == "range" or {"start", "end"}.issubset(spec.keys()):
            try:
                start = int(spec.get("start"))
                end = int(spec.get("end"))
                step = int(spec.get("step") or 1)
            except (TypeError, ValueError):
                return []
            if step == 0:
                return []
            if start <= end and step > 0:
                return list(range(start, end + 1, step))
            if start >= end and step < 0:
                return list(range(start, end - 1, step))
        return []

    @staticmethod
    def _stage_is_completed(stage_status: Dict[str, Any], stage_name: str) -> bool:
        for item in stage_status.get("stages", []):
            if item.get("stage") == stage_name:
                return item.get("status") == "completed"
        return False

    def _attach_strategy_run_runtime_sections(
        self,
        config_data: Any,
        *,
        raw_config: Dict[str, Any],
        config_file: Path,
    ) -> None:
        dataloader_config = self._strategy_run_dataloader_config(raw_config)
        backtester_config = self._strategy_run_backtester_config(raw_config)
        dataloader_config["__config_file_path"] = str(config_file)
        backtester_config["__config_file_path"] = str(config_file)

        config_data.raw_config = copy.deepcopy(raw_config)
        config_data.dataloader_config = dataloader_config
        config_data.backtester_config = backtester_config
        config_data.metricstracker_config = (
            raw_config.get("metricstracker", {})
            if isinstance(raw_config.get("metricstracker"), dict)
            else {}
        )
        config_data.statanalyser_config = (
            raw_config.get("statanalyser", {})
            if isinstance(raw_config.get("statanalyser"), dict)
            else {}
        )
        config_data.predictor_config = dataloader_config.get("predictor_config", {})

    def _strategy_run_dataloader_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        data = config.get("data") if isinstance(config.get("data"), dict) else {}
        universe = config.get("universe") if isinstance(config.get("universe"), dict) else {}
        symbols = [str(item) for item in universe.get("symbols", []) if str(item).strip()]
        provider = str(data.get("provider") or data.get("source") or "yfinance").strip().lower()
        frequency = str(data.get("frequency") or "1D")

        if self._strategy_run_uses_internal_market_loader(config):
            return {
                "source": "strategy_run_market_data",
                "frequency": frequency,
                "start_date": str(data.get("start_date") or ""),
                "asset_symbols": symbols,
            }

        if provider in {"file", "local_csv", "csv"}:
            file_config = dict(data.get("file_config") or {})
            file_path = data.get("file_path") or data.get("path") or file_config.get("file_path")
            if file_path:
                file_config["file_path"] = str(file_path)
            file_config.setdefault("date_column", data.get("date_column", "Time"))
            file_config.setdefault("price_column", data.get("price_column", "Close"))
            return {
                "source": "file",
                "frequency": frequency,
                "start_date": str(data.get("start_date") or ""),
                "end_date": str(data.get("end_date") or ""),
                "file_config": file_config,
                "predictor_config": dict(data.get("predictor_config") or {}),
            }

        symbol = symbols[0] if symbols else str(data.get("symbol") or "AAPL")
        return {
            "source": "yfinance",
            "frequency": frequency,
            "start_date": str(data.get("start_date") or "2020-01-01"),
            "end_date": str(data.get("end_date") or ""),
            "yfinance_config": {
                "symbol": symbol,
                "interval": str(data.get("interval") or data.get("frequency") or "1d"),
            },
            "predictor_config": dict(data.get("predictor_config") or {}),
        }

    def _strategy_run_backtester_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        mode = self._strategy_run_mode(config)
        metadata = config.get("metadata") if isinstance(config.get("metadata"), dict) else {}
        legacy_backtester = (
            metadata.get("legacy_backtester")
            if isinstance(metadata.get("legacy_backtester"), dict)
            else {}
        )
        export_config = dict(legacy_backtester.get("export_config") or {})
        export_config.setdefault("export_parquet", True)
        export_config.setdefault("export_csv", False)
        backtester_config = {
            "strategy_mode": (
                "multi_asset_portfolio"
                if self._strategy_run_uses_internal_market_loader(config)
                else "single_asset_portfolio"
            ),
            "engine_mode": "strategy_run",
            "Backtest_id": str(metadata.get("strategy_id") or mode or "strategy_run"),
            "export_config": export_config,
        }
        if self._strategy_run_uses_internal_market_loader(config):
            data = config.get("data") if isinstance(config.get("data"), dict) else {}
            universe = config.get("universe") if isinstance(config.get("universe"), dict) else {}
            symbols = [str(item).strip().upper() for item in universe.get("symbols", []) if str(item).strip()]
            market_data = dict(legacy_backtester.get("market_data") or {})
            market_data.setdefault("provider", data.get("provider") or "yfinance")
            market_data.setdefault("symbols", symbols)
            market_data.setdefault("start", data.get("start_date") or "1990-01-01")
            market_data.setdefault("interval", data.get("interval") or data.get("frequency") or "1d")
            market_data.setdefault("start_policy", data.get("start_policy") or "common_available")
            portfolio_config = copy.deepcopy(legacy_backtester.get("portfolio_config") or config)
            if isinstance(portfolio_config, dict):
                portfolio_config.setdefault("strategy_id", backtester_config["Backtest_id"])
            backtester_config["market_data"] = market_data
            backtester_config["portfolio_config"] = portfolio_config
        return backtester_config

    @staticmethod
    def _strategy_run_runtime_config(
        config: Dict[str, Any],
        *,
        backtester_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        runtime_config = copy.deepcopy(config)
        metadata = runtime_config.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            runtime_config["metadata"] = metadata
        legacy_backtester = metadata.setdefault("legacy_backtester", {})
        if not isinstance(legacy_backtester, dict):
            legacy_backtester = {}
            metadata["legacy_backtester"] = legacy_backtester
        legacy_backtester["export_config"] = dict(backtester_config.get("export_config") or {})
        legacy_backtester.setdefault("Backtest_id", backtester_config.get("Backtest_id"))
        if "market_data" in backtester_config:
            legacy_backtester["market_data"] = copy.deepcopy(backtester_config.get("market_data") or {})
        if "portfolio_config" in backtester_config:
            legacy_backtester["portfolio_config"] = copy.deepcopy(backtester_config.get("portfolio_config") or {})
        return runtime_config

    def run_statanalyser_config(
        self,
        config_path: str,
        emit: Callable[[str, str], None],
    ) -> Dict[str, Any]:
        run_id = self._new_run_id()
        run_paths = self.registry.build_run_paths(run_id)
        config_file = Path(config_path).resolve()
        config_meta = self._load_app_config_metadata(config_file)
        stage_status = self._new_stage_status(run_id, "statanalyser")
        registry_payload = self._base_registry(
            run_id=run_id,
            module="statanalyser",
            entrypoint="app-run-center",
            status="running",
        )
        registry_payload["config_snapshot_dir"] = str(run_paths["snapshot_dir"])
        registry_payload["artifact_manifest_path"] = str(run_paths["artifact_manifest"])
        registry_payload["dataloader_health_path"] = str(run_paths["dataloader_health"])
        registry_payload["data_lineage_manifest_path"] = str(run_paths["data_lineage_manifest"])
        self.registry.write_registry_entry(registry_payload)
        self.registry.write_stage_status(run_id, stage_status)

        emit("config_validation", f"Validating {config_file.name}")
        validator = AutorunnerConfigValidator()
        if not validator.validate_config(str(config_file)):
            validation_errors = validator.get_validation_errors(str(config_file))
            message = (
                f"Validation failed for {config_file.name}: " + "; ".join(validation_errors)
                if validation_errors
                else f"Validation failed for {config_file.name}"
            )
            return self._fail_run(
                run_id=run_id,
                registry_payload=registry_payload,
                stage_status=stage_status,
                stage_name="config_validation",
                message=message,
            )

        loader = AutorunnerConfigLoader()
        config_data = loader.load_config(str(config_file))
        if config_data is None:
            raise RuntimeError(f"Unable to load statanalyser config: {config_file}")
        self._prepare_managed_statanalyser_outputs(run_id, config_data)
        self._mark_stage(stage_status, "config_validation", "completed", "Config valid")
        self._write_run_snapshot(
            run_id,
            config_data=config_data,
            module="statanalyser",
            execution_plan_path=None,
        )

        emit("dataloader", "Loading data")
        data_loader = DataLoaderAutorunner(logger=self.logger)
        full_dataloader_config = {
            **config_data.dataloader_config,
            "predictor_config": config_data.predictor_config,
            "__config_file_path": config_data.file_path,
        }
        data = data_loader.load_data(full_dataloader_config)
        if data is None:
            return self._fail_run(
                run_id=run_id,
                registry_payload=registry_payload,
                stage_status=stage_status,
                stage_name="dataloader",
                message="dataloader failed",
            )
        self._mark_stage(
            stage_status,
            "dataloader",
            "completed",
            f"Loaded {len(data)} rows",
        )
        self._mark_stage(
            stage_status,
            "backtester",
            "skipped",
            "Backtester skipped for statanalyser-only run",
        )
        self._mark_stage(
            stage_status,
            "metricstracker",
            "skipped",
            "Metricstracker skipped for statanalyser-only run",
        )
        self._mark_stage(
            stage_status,
            "wfanalyser",
            "skipped",
            "WFA skipped for statanalyser-only run",
        )

        stat_summary = self._run_statanalyser_stage(
            stage_status,
            data,
            config_data,
            emit,
        )
        self.registry.write_snapshot_file(
            run_id,
            "statanalyser_summary.json",
            stat_summary or {},
        )

        artifacts = self._collect_statanalyser_artifacts(stat_summary)
        artifacts = self._normalize_managed_artifact_names(
            run_id=run_id,
            module="statanalyser",
            config_file=config_file,
            config_data=config_data,
            artifacts=artifacts,
        )
        dataloader_health = self._build_dataloader_health(
            run_id=run_id,
            dataloader_config=config_data.dataloader_config,
            data=data,
            primary_artifact=None,
        )
        self.registry.write_snapshot_file(
            run_id,
            "dataloader_health.json",
            dataloader_health,
        )
        raw_config = self._load_json_config(config_file)
        data_lineage_manifest = self._write_data_lineage_manifest(
            run_id=run_id,
            module="statanalyser",
            dataloader_config=config_data.dataloader_config,
            data=data,
            raw_config=raw_config,
            primary_artifact=None,
            dataloader_health=dataloader_health,
        )
        lineage_path = self.registry.build_run_paths(run_id)["data_lineage_manifest"]
        artifacts.append(lineage_path)
        artifact_manifest = self._build_artifact_manifest(run_id, artifacts)
        self.registry.write_artifact_manifest(run_id, artifact_manifest)
        data_lineage_manifest = self._finalize_data_lineage_manifest_links(
            run_id,
            data_lineage_manifest,
        )

        emit("app_export", "Finalizing app registry")
        self._mark_stage(
            stage_status,
            "app_export",
            "completed",
            "Registry and manifests written",
        )
        final_status = "completed"
        if any(
            item["status"] == "failed" and item["optional"]
            for item in stage_status["stages"]
        ):
            final_status = "partial"
        identity = self._identity_for_config_data(run_id, "statanalyser", config_file, config_data)
        registry_payload.update(
            {
                "status": final_status,
                "completed_at": self._now_iso(),
                "config_filename": config_file.name,
                "canonical_config_filename": canonical_config_filename(identity),
                "symbol": self._extract_symbol(config_data.dataloader_config),
                "frequency": data_loader.frequency
                or config_data.dataloader_config.get("frequency", "1D"),
                "strategy_mode": str(
                    config_data.backtester_config.get("strategy_mode", "auto")
                ),
                "semantic_label": canonical_stem(identity),
                "display_label": config_meta.get("display_label")
                or display_identity_label(identity, id_prefix="run"),
                "run_type": config_meta.get("run_type"),
                "warning_count": len(dataloader_health.get("warnings", [])),
                "error_count": 0 if stat_summary is not None else 1,
                "stage_status_path": str(run_paths["stage_status"]),
                "lineage_status": data_lineage_manifest.get("lineage_status"),
                "audit_summary": {
                    "source_audit_id": dataloader_health.get("source_audit_id"),
                    "warning_count": len(dataloader_health.get("warnings", [])),
                },
                "artifacts_ready": sum(
                    1
                    for item in artifact_manifest["artifacts"]
                    if item["status"] == "ready"
                ),
                "artifacts_total": len(artifact_manifest["artifacts"]),
                "warnings": dataloader_health.get("warnings", []),
                "errors": [] if stat_summary is not None else ["statanalyser failed"],
            }
        )
        self.registry.write_stage_status(run_id, stage_status)
        self.registry.write_registry_entry(registry_payload)
        return {"run_id": run_id, "status": final_status}

    def run_wfa_config(
        self,
        config_path: str,
        emit: Callable[[str, str], None],
    ) -> Dict[str, Any]:
        run_id = self._new_run_id()
        run_paths = self.registry.build_run_paths(run_id)
        config_file = Path(config_path).resolve()
        config_meta = self._load_app_config_metadata(config_file)
        stage_status = self._new_stage_status(run_id, "wfanalyser")
        registry_payload = self._base_registry(
            run_id=run_id,
            module="wfanalyser",
            entrypoint="app-run-center",
            status="running",
        )
        registry_payload["config_snapshot_dir"] = str(run_paths["snapshot_dir"])
        registry_payload["artifact_manifest_path"] = str(run_paths["artifact_manifest"])
        registry_payload["dataloader_health_path"] = str(run_paths["dataloader_health"])
        registry_payload["data_lineage_manifest_path"] = str(run_paths["data_lineage_manifest"])
        self.registry.write_registry_entry(registry_payload)
        self.registry.write_stage_status(run_id, stage_status)

        emit("config_validation", f"Validating {config_file.name}")
        validator = WFAConfigValidator()
        if not validator.validate_config(str(config_file)):
            validation_errors = validator.get_validation_errors(str(config_file))
            message = (
                f"Validation failed for {config_file.name}: " + "; ".join(validation_errors)
                if validation_errors
                else f"Validation failed for {config_file.name}"
            )
            return self._fail_run(
                run_id=run_id,
                registry_payload=registry_payload,
                stage_status=stage_status,
                stage_name="config_validation",
                message=message,
            )

        loader = WFAConfigLoader()
        config_data = loader.load_config(str(config_file))
        if config_data is None:
            raise RuntimeError(f"Unable to load WFA config: {config_file}")
        managed_root = self.registry.build_run_paths(run_id)["snapshot_dir"] / "managed_artifacts" / "wfanalyser"
        managed_root.mkdir(parents=True, exist_ok=True)
        self._mark_stage(stage_status, "config_validation", "completed", "Config valid")
        self._write_run_snapshot(
            run_id,
            config_data=config_data,
            module="wfanalyser",
            execution_plan_path=None,
        )

        wfa_strategy_config = (
            config_data.backtester_config.get("portfolio_config")
            or config_data.backtester_config.get("strategy_config")
            or config_data.backtester_config.get("strategy_run_config")
            or {}
        )
        emit("wfanalyser", self._running_stage_message("Running WFA", wfa_strategy_config))
        engine = WalkForwardEngine(config_data, logger=self.logger)
        results = engine.run()
        if not results:
            return self._fail_run(
                run_id=run_id,
                registry_payload=registry_payload,
                stage_status=stage_status,
                stage_name="wfanalyser",
                message="wfanalyser failed",
            )
        if not self._wfa_results_have_successful_windows(results):
            failure_message = self._summarize_wfa_failure(results)
            return self._fail_run(
                run_id=run_id,
                registry_payload=registry_payload,
                stage_status=stage_status,
                stage_name="wfanalyser",
                message=failure_message,
            )
        if self._is_unified_wfa_payload(results):
            self._export_unified_wfa_payload(results, managed_root, run_id=run_id)
        else:
            exporter = ResultsExporter(
                results,
                output_dir=managed_root,
                config_data=config_data,
                logger=self.logger,
                data=results.get("data") if isinstance(results, dict) else None,
            )
            exporter.export()
        self._mark_stage(
            stage_status,
            "wfanalyser",
            "completed",
            "WFA results exported",
        )

        artifacts = self._collect_directory_artifacts(managed_root)
        artifacts = self._normalize_managed_artifact_names(
            run_id=run_id,
            module="wfanalyser",
            config_file=config_file,
            config_data=config_data,
            artifacts=artifacts,
        )
        primary_wfa = self._select_primary_artifact(artifacts, "wfa_parquet")
        dataloader_health = self._build_dataloader_health(
            run_id=run_id,
            dataloader_config=config_data.dataloader_config,
            data=engine.data if engine.data is not None else pd.DataFrame(),
            primary_artifact=primary_wfa,
        )
        self.registry.write_snapshot_file(
            run_id, "dataloader_health.json", dataloader_health
        )
        raw_config = self._load_json_config(config_file)
        lineage_raw_config = self._lineage_raw_config_with_wfa_strategy(
            raw_config,
            config_file,
        )
        data_lineage_manifest = self._write_data_lineage_manifest(
            run_id=run_id,
            module="wfanalyser",
            dataloader_config=config_data.dataloader_config,
            data=engine.data if engine.data is not None else pd.DataFrame(),
            raw_config=lineage_raw_config,
            primary_artifact=primary_wfa,
            wfa_results=results,
            wfa_engine=engine,
            dataloader_health=dataloader_health,
        )
        lineage_path = self.registry.build_run_paths(run_id)["data_lineage_manifest"]
        artifacts.append(lineage_path)
        artifact_manifest = self._build_artifact_manifest(run_id, artifacts)
        chart_artifacts = self._write_wfa_chart_payloads(run_id, artifacts)
        if chart_artifacts:
            artifact_manifest["artifacts"].extend(chart_artifacts)
        self.registry.write_artifact_manifest(run_id, artifact_manifest)
        data_lineage_manifest = self._finalize_data_lineage_manifest_links(
            run_id,
            data_lineage_manifest,
        )

        emit("app_export", "Finalizing app registry")
        self._mark_stage(
            stage_status,
            "app_export",
            "completed",
            "Registry and manifests written",
        )
        identity = self._identity_for_config_data(run_id, "wfanalyser", config_file, config_data)
        registry_payload.update(
            {
                "status": "completed",
                "completed_at": self._now_iso(),
                "config_filename": config_file.name,
                "canonical_config_filename": canonical_config_filename(identity),
                "symbol": self._extract_symbol(config_data.dataloader_config),
                "frequency": str(config_data.dataloader_config.get("frequency", "1D")),
                "strategy_mode": str(
                    config_data.backtester_config.get("strategy_mode", "auto")
                ),
                "semantic_label": canonical_stem(identity),
                "display_label": config_meta.get("display_label")
                or display_identity_label(identity, id_prefix="run"),
                "run_type": config_meta.get("run_type"),
                "warning_count": len(dataloader_health.get("warnings", [])),
                "error_count": 0,
                "stage_status_path": str(run_paths["stage_status"]),
                "lineage_status": data_lineage_manifest.get("lineage_status"),
                "audit_summary": {
                    "source_audit_id": dataloader_health.get("source_audit_id"),
                    "warning_count": len(dataloader_health.get("warnings", [])),
                },
                "artifacts_ready": sum(
                    1
                    for item in artifact_manifest["artifacts"]
                    if item["status"] == "ready"
                ),
                "artifacts_total": len(artifact_manifest["artifacts"]),
                "warnings": dataloader_health.get("warnings", []),
                "errors": [],
            }
        )
        self.registry.write_stage_status(run_id, stage_status)
        self.registry.write_registry_entry(registry_payload)
        return {"run_id": run_id, "status": "completed"}

    @staticmethod
    def _wfa_results_have_successful_windows(results: Any) -> bool:
        if not isinstance(results, dict):
            return False
        selected = results.get("selected_optimum")
        if isinstance(selected, pd.DataFrame):
            return not selected.empty
        if isinstance(selected, list):
            return bool(selected)
        by_objective = results.get("results_by_objective", {})
        if not isinstance(by_objective, dict):
            return False
        for objective_rows in by_objective.values():
            if isinstance(objective_rows, list) and objective_rows:
                return True
        return False

    @staticmethod
    def _is_unified_wfa_payload(results: Any) -> bool:
        if not isinstance(results, dict):
            return False
        return "selected_optimum" in results or "candidate_diagnostics" in results

    @staticmethod
    def _export_unified_wfa_payload(
        results: Dict[str, Any],
        output_dir: Path,
        *,
        run_id: str = "",
    ) -> None:
        from backtester.MultiAssetPortfolioExporter_backtester import (
            MultiAssetPortfolioExporterBacktester,
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        selected = results.get("selected_optimum")
        selected_frame = selected.copy() if isinstance(selected, pd.DataFrame) else None
        window_backtest_refs: Dict[tuple[int, str], Dict[str, str]] = {}
        wfa_config = results.get("wfa_config", {}) if isinstance(results.get("wfa_config"), dict) else {}
        outputs_cfg = wfa_config.get("outputs", {}) if isinstance(wfa_config.get("outputs"), dict) else {}
        if bool(outputs_cfg.get("window_backtests", False)):
            window_output_dir = output_dir / "window_backtests"
            for item in results.get("window_backtests", []) or []:
                if not isinstance(item, dict):
                    continue
                result = item.get("oos_result")
                if result is None:
                    continue
                window_id = int(item.get("window_id") or 0)
                objective = str(item.get("objective") or "")
                backtest_id = str(item.get("backtest_id") or getattr(result, "strategy_id", "") or "").strip()
                if backtest_id:
                    result.strategy_id = backtest_id
                    if isinstance(getattr(result, "config", None), dict):
                        result.config["strategy_id"] = backtest_id
                exported_paths = MultiAssetPortfolioExporterBacktester(
                    result=result,
                    output_dir=window_output_dir,
                    run_id=backtest_id or f"wfa_window_{window_id:03d}_{objective}",
                    export_csv=False,
                ).export()
                if exported_paths and backtest_id:
                    window_backtest_refs[(window_id, objective)] = {
                        "run_id": str(run_id),
                        "backtest_id": backtest_id,
                    }

        if selected_frame is not None and not selected_frame.empty and window_backtest_refs:
            linked_run_ids: List[str] = []
            linked_backtest_ids: List[str] = []
            for row in selected_frame.itertuples(index=False):
                window_id = int(getattr(row, "window_id", 0) or 0)
                objective = str(getattr(row, "objective", "") or "")
                ref = window_backtest_refs.get((window_id, objective), {})
                linked_run_ids.append(str(ref.get("run_id", "")))
                linked_backtest_ids.append(str(ref.get("backtest_id", "")))
            selected_frame["linked_backtest_run_id"] = linked_run_ids
            selected_frame["linked_backtest_id"] = linked_backtest_ids

        if isinstance(selected_frame, pd.DataFrame) and not selected_frame.empty:
            objectives = selected_frame.get("objective")
            objective_values = (
                sorted({str(item) for item in objectives.dropna().tolist()})
                if objectives is not None
                else ["all"]
            )
            for objective in objective_values or ["all"]:
                if objective == "all" or "objective" not in selected_frame.columns:
                    subset = selected_frame.copy()
                else:
                    subset = selected_frame[selected_frame["objective"].astype(str) == objective].copy()
                if subset.empty:
                    continue
                subset.to_parquet(
                    output_dir / f"wfa_unified_selected_optimum_{objective}.parquet",
                    index=False,
                )
        diagnostics = results.get("candidate_diagnostics")
        if isinstance(diagnostics, pd.DataFrame) and not diagnostics.empty:
            diagnostics.to_parquet(
                output_dir / "wfa_unified_candidate_diagnostics.parquet",
                index=False,
            )
        metadata = dict(results.get("metadata") or {})
        metadata.update(
            {
                "schema_version": metadata.get(
                    "schema_version", "unified_portfolio_wfa_result.v1"
                ),
                "row_contract": metadata.get(
                    "row_contract", "selected_optimum_per_window"
                ),
            }
        )
        (output_dir / "wfa_unified_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _summarize_wfa_failure(results: Any) -> str:
        if not isinstance(results, dict):
            return "wfanalyser failed with no usable results"
        failure_summary = results.get("failure_summary")
        if isinstance(failure_summary, dict) and failure_summary:
            parts = [f"{reason}: {count}" for reason, count in failure_summary.items()]
            return "wfanalyser produced no successful windows; " + "; ".join(parts)
        return "wfanalyser produced no successful windows"

    def _run_metrics_stage(
        self,
        stage_status: Dict[str, Any],
        backtest_results: Dict[str, Any],
        metricstracker_config: Dict[str, Any],
        emit: Callable[[str, str], None],
    ) -> Optional[Dict[str, Any]]:
        emit("metricstracker", "Running metrics")
        try:
            summary = MetricsRunnerAutorunner(logger=self.logger).run(
                backtest_results,
                metricstracker_config,
            )
            if not summary:
                stage_state = "skipped"
                message = "Metrics stage skipped"
            elif (
                summary.get("executed")
                and int(summary.get("success", 0) or 0) == 0
                and int(summary.get("failed", 0) or 0) > 0
            ):
                stage_state = "failed"
                message = "Metrics stage failed for every target parquet"
            else:
                stage_state = "completed"
                message = (
                    f"Metrics stage finished: {int(summary.get('success', 0) or 0)} "
                    f"success, {int(summary.get('failed', 0) or 0)} failed"
                )
            self._mark_stage(
                stage_status,
                "metricstracker",
                stage_state,
                message,
            )
            return summary
        except Exception as exc:  # pragma: no cover - defensive
            self._mark_stage(stage_status, "metricstracker", "failed", str(exc))
            return None

    def _run_statanalyser_stage(
        self,
        stage_status: Dict[str, Any],
        data: pd.DataFrame,
        config_data: Any,
        emit: Callable[[str, str], None],
    ) -> Optional[Dict[str, Any]]:
        emit("statanalyser", "Running statanalyser")
        try:
            summary = StatAnalyserRunnerAutorunner(logger=self.logger).run(
                data,
                {
                    "dataloader": config_data.dataloader_config,
                    "backtester": config_data.backtester_config,
                    "metricstracker": config_data.metricstracker_config,
                    "statanalyser": config_data.statanalyser_config,
                },
            )
            stage_state = "completed" if summary else "skipped"
            self._mark_stage(
                stage_status,
                "statanalyser",
                stage_state,
                "StatAnalyser stage finished",
            )
            return summary
        except Exception as exc:  # pragma: no cover - defensive
            self._mark_stage(stage_status, "statanalyser", "failed", str(exc))
            return None

    def _prepare_managed_autorunner_outputs(self, run_id: str, config_data: Any) -> None:
        managed_root = self.registry.build_run_paths(run_id)["snapshot_dir"] / "managed_artifacts"
        backtester_dir = managed_root / "backtester"
        metric_dir = managed_root / "metricstracker"
        portfolio_dir = managed_root / "portfolio"
        stat_dir = managed_root / "statanalyser"
        backtester_dir.mkdir(parents=True, exist_ok=True)
        metric_dir.mkdir(parents=True, exist_ok=True)
        portfolio_dir.mkdir(parents=True, exist_ok=True)
        stat_dir.mkdir(parents=True, exist_ok=True)

        backtester_cfg = getattr(config_data, "backtester_config", {})
        if isinstance(backtester_cfg, dict):
            export_cfg = backtester_cfg.setdefault("export_config", {})
            if isinstance(export_cfg, dict):
                if str(backtester_cfg.get("strategy_mode", "")).strip() == "multi_asset_portfolio":
                    export_cfg["output_dir"] = str(portfolio_dir)
                else:
                    export_cfg["output_dir"] = str(backtester_dir)

        stat_cfg = getattr(config_data, "statanalyser_config", {})
        if isinstance(stat_cfg, dict):
            report_cfg = stat_cfg.setdefault("report", {})
            if isinstance(report_cfg, dict):
                report_cfg["output_dir"] = str(stat_dir)

    def _prepare_managed_statanalyser_outputs(self, run_id: str, config_data: Any) -> None:
        managed_root = self.registry.build_run_paths(run_id)["snapshot_dir"] / "managed_artifacts"
        stat_dir = managed_root / "statanalyser"
        stat_dir.mkdir(parents=True, exist_ok=True)
        stat_cfg = getattr(config_data, "statanalyser_config", {})
        if isinstance(stat_cfg, dict):
            report_cfg = stat_cfg.setdefault("report", {})
            if isinstance(report_cfg, dict):
                report_cfg["output_dir"] = str(stat_dir)

    @staticmethod
    def _existing_paths(paths: List[Optional[Path]]) -> List[Path]:
        rows: List[Path] = []
        seen: set[str] = set()
        for path in paths:
            if path is None:
                continue
            resolved = Path(path)
            if not resolved.exists():
                continue
            key = str(resolved.resolve())
            if key in seen:
                continue
            seen.add(key)
            rows.append(resolved)
        return rows

    def _artifact_sidecars(self, primary: Path) -> List[Path]:
        base = primary.with_suffix("")
        candidates = [
            base.with_name(base.name + "_metadata.json"),
            base.with_name(base.name + "_audit.json"),
            base.with_name(base.name + "_audit.parquet"),
        ]
        return [item for item in candidates if item.exists()]

    def _collect_autorunner_artifacts(
        self,
        *,
        backtest_results: Dict[str, Any],
        metrics_summary: Optional[Dict[str, Any]],
        stat_summary: Optional[Dict[str, Any]],
    ) -> List[Path]:
        rows: List[Path] = []
        exported_files = [
            Path(item)
            for item in backtest_results.get("exported_files", [])
            if isinstance(item, str) and item.strip()
        ]
        metric_roots_seen: set[Path] = set()
        audit_row_dirs_seen: set[Path] = set()
        for path in exported_files:
            rows.append(path)
            rows.extend(self._artifact_sidecars(path))
            if path.parent not in audit_row_dirs_seen:
                audit_row_dirs_seen.add(path.parent)
                rows.extend(sorted(path.parent.glob("*_audit_rows_*.jsonl")))
            managed_root = path.parent.parent
            metric_root = managed_root / "metricstracker"
            if metric_root not in metric_roots_seen:
                metric_roots_seen.add(metric_root)
                rows.extend(self._collect_directory_artifacts(metric_root))

        if isinstance(metrics_summary, dict):
            for task in metrics_summary.get("tasks", []):
                if not isinstance(task, dict):
                    continue
                output_path = task.get("output_path")
                if isinstance(output_path, str) and output_path.strip():
                    metric_path = Path(output_path)
                    rows.append(metric_path)
                    rows.extend(self._artifact_sidecars(metric_path))
                    if metric_path.parent not in audit_row_dirs_seen:
                        audit_row_dirs_seen.add(metric_path.parent)
                        rows.extend(sorted(metric_path.parent.glob("*_audit_rows_*.jsonl")))

        if isinstance(stat_summary, dict):
            rows.extend(self._collect_statanalyser_artifacts(stat_summary))

        return self._existing_paths(rows)

    def _collect_statanalyser_artifacts(
        self,
        stat_summary: Optional[Dict[str, Any]],
    ) -> List[Path]:
        rows: List[Path] = []
        if not isinstance(stat_summary, dict):
            return rows
        for report_path in stat_summary.get("report_paths", []):
            if isinstance(report_path, str) and report_path.strip():
                rows.append(Path(report_path))
        return self._existing_paths(rows)

    def _collect_directory_artifacts(self, directory: Path) -> List[Path]:
        if not directory.exists():
            return []
        return self._existing_paths([path for path in directory.rglob("*") if path.is_file()])

    def _identity_for_config_data(
        self,
        run_id: str,
        module: str,
        config_file: Path,
        config_data: Any,
    ) -> Dict[str, str]:
        return build_trading_identity(
            module=module,
            config_filename=config_file.name,
            semantic_label=self._semantic_label_from_config(config_file),
            run_id=run_id,
            raw_config=getattr(config_data, "raw_config", {}),
            dataloader_config=getattr(config_data, "dataloader_config", {}),
            backtester_config=getattr(config_data, "backtester_config", {}),
            wfa_config=getattr(config_data, "wfa_config", {}),
        )

    def _normalize_managed_artifact_names(
        self,
        *,
        run_id: str,
        module: str,
        config_file: Path,
        config_data: Any,
        artifacts: List[Path],
    ) -> List[Path]:
        identity = self._identity_for_config_data(run_id, module, config_file, config_data)
        rows: List[Path] = []
        rename_map: Dict[Path, Path] = {}
        planned_targets: set[Path] = set()
        for path in artifacts:
            artifact_type, _, _ = self._classify_artifact(path)
            if not artifact_type:
                rows.append(path)
                continue
            suffix = self._artifact_suffix(path)
            target_name = canonical_artifact_filename(
                identity=identity,
                artifact_type=artifact_type,
                source_name=path.name,
                suffix=suffix,
            )
            target = path.with_name(target_name)
            if target == path:
                rows.append(path)
                continue
            target = self._dedupe_target_path(target, planned_targets=planned_targets)
            planned_targets.add(target)
            rename_map[path] = target

        for source, target in rename_map.items():
            if not source.exists():
                continue
            source.replace(target)
            rows.append(target)

        renamed_sources = set(rename_map.keys())
        for path in artifacts:
            if path not in renamed_sources:
                rows.append(path)
        return self._existing_paths(rows)

    @staticmethod
    def _dedupe_target_path(target: Path, *, planned_targets: Optional[set[Path]] = None) -> Path:
        planned_targets = planned_targets or set()
        if not target.exists() and target not in planned_targets:
            return target
        stem = target.stem
        suffix = target.suffix
        for index in range(2, 10000):
            candidate = target.with_name(f"{stem}_{index}{suffix}")
            if not candidate.exists() and candidate not in planned_targets:
                return candidate
        return target.with_name(f"{stem}_{secrets.token_hex(3)}{suffix}")

    @staticmethod
    def _artifact_suffix(path: Path) -> str:
        name = path.name.lower()
        objective = ""
        if "_ranking_sharpe" in name:
            objective = "ranking-sharpe"
        elif "_ranking_calmar" in name:
            objective = "ranking-calmar"
        elif "_wfa_candidate_diagnostics_sharpe" in name:
            objective = "candidate-diagnostics-sharpe"
        elif "_wfa_candidate_diagnostics_calmar" in name:
            objective = "candidate-diagnostics-calmar"
        elif "_wfa_sharpe" in name:
            objective = "wfa-sharpe"
        elif "_wfa_calmar" in name:
            objective = "wfa-calmar"
        sidecar = ""
        if "_metadata" in name:
            sidecar = "metadata"
        elif "_audit_rows" in name:
            sidecar = "audit-rows"
        elif "calendar_signal_audit" in name:
            sidecar = "calendar-signal-audit"
        elif "_equity_curve" in name:
            sidecar = "equity-curve"
        elif "_holdings" in name:
            sidecar = "holdings"
        elif "_rebalance_trades" in name:
            sidecar = "rebalance-trades"
        elif "_rebalance_audit" in name:
            sidecar = "rebalance-audit"
        elif "_audit" in name:
            sidecar = "audit"
        if objective and sidecar:
            return f"{objective}-{sidecar}"
        if objective:
            return objective
        if sidecar:
            return sidecar
        for suffix in [
            "_metrics",
            "_backtests",
        ]:
            if suffix in name:
                return suffix.strip("_").replace("_", "-")
        return path.stem

    def _write_run_snapshot(
        self,
        run_id: str,
        *,
        config_data: Any,
        module: str,
        execution_plan_path: Optional[str],
    ) -> None:
        config_file = Path(config_data.file_path).resolve()
        raw_config = json.loads(config_file.read_text(encoding="utf-8-sig"))
        self.registry.write_snapshot_file(run_id, "run_config.json", raw_config)
        normalized_contract = self._write_normalized_contract_snapshot(
            run_id=run_id,
            module=module,
            raw_config=raw_config,
            config_file=config_file,
        )
        if hasattr(config_data, "dataloader_config"):
            self.registry.write_snapshot_file(
                run_id, "dataloader_config.json", config_data.dataloader_config
            )
        if hasattr(config_data, "backtester_config"):
            self.registry.write_snapshot_file(
                run_id, "backtester_config.json", config_data.backtester_config
            )

        strategy_contract = self._resolve_contract_ref(
            getattr(config_data, "backtester_config", {}).get("strategy_contract_path"),
            config_data.file_path,
        )
        feature_contract = self._resolve_contract_ref(
            getattr(config_data, "backtester_config", {}).get("feature_contract_path"),
            config_data.file_path,
        )

        execution_plan_snapshot = {"path": None, "hash": None}
        if isinstance(execution_plan_path, str) and execution_plan_path.strip():
            execution_plan_source = Path(execution_plan_path)
            if execution_plan_source.exists():
                target = self.registry.build_run_paths(run_id)["execution_plan_snapshot"]
                shutil.copy2(execution_plan_source, target)
                execution_plan_snapshot = {
                    "path": str(target),
                    "hash": self._hash_file(target),
                }

        snapshot_payload = {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-run-snapshot-v1",
            "run_id": run_id,
            "resolved_configs": {
                "run_config": {"config_path": str(config_file)},
                "strategy_run": normalized_contract.get("strategy_run"),
                "wfa_run": normalized_contract.get("wfa_run"),
                "dataloader_config": getattr(config_data, "dataloader_config", {}),
                "backtester_config": getattr(config_data, "backtester_config", {}),
                "metricstracker_config": getattr(
                    config_data, "metricstracker_config", None
                ),
                "statanalyser_config": getattr(config_data, "statanalyser_config", None),
                "wfa_config": getattr(config_data, "wfa_config", None),
            },
            "contract_refs": {
                "strategy_contract": strategy_contract,
                "feature_contract": feature_contract,
            },
            "execution_plan": execution_plan_snapshot,
            "execution_plan": normalized_contract.get("execution_plan"),
            "app_runtime": {
                "app_version": "v1-foundation-review",
                "server_mode": "browser_first",
                "registry_mode": "new_results_only",
                "entry_thread": None,
                "python_version": None,
                "module": module,
            },
        }
        self.registry.write_snapshot_file(run_id, "run_snapshot.json", snapshot_payload)

    def _write_normalized_contract_snapshot(
        self,
        *,
        run_id: str,
        module: str,
        raw_config: Dict[str, Any],
        config_file: Path,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "strategy_run": None,
            "wfa_run": None,
            "execution_plan": None,
        }
        try:
            if module == "wfanalyser":
                if normalize_wfa_run_config is None:
                    return result
                wfa_contract = normalize_wfa_run_config(
                    raw_config,
                    source_path=config_file,
                    repo_root=self.repo_root,
                )
                wfa_path = self.registry.write_snapshot_file(run_id, "wfa_run.json", wfa_contract)
                result["wfa_run"] = {"config_path": str(wfa_path), "schema_version": "wfa_run"}
                embedded = wfa_contract.get("legacy_embedded_strategy_config")
                strategy_contract = embedded if isinstance(embedded, dict) else None
                source = "legacy_embedded_wfa_strategy" if strategy_contract else ""
                strategy_config_path = str(wfa_contract.get("strategy_config_path") or "").strip()
                if strategy_contract is None and strategy_config_path and normalize_strategy_run_config is not None:
                    resolved_strategy = self._resolve_optional_config_path(
                        strategy_config_path,
                        config_file=config_file,
                    )
                    if resolved_strategy and resolved_strategy.exists():
                        strategy_contract = normalize_strategy_run_config(
                            json.loads(resolved_strategy.read_text(encoding="utf-8-sig")),
                            source_path=resolved_strategy,
                            repo_root=self.repo_root,
                        )
                        source = "wfa_strategy_config_path"
                if isinstance(strategy_contract, dict):
                    strategy_path = self.registry.write_snapshot_file(
                        run_id,
                        "strategy_run.json",
                        strategy_contract,
                    )
                    result["strategy_run"] = {
                        "config_path": str(strategy_path),
                        "schema_version": "strategy_run",
                        "source": source,
                    }
                    if plan_strategy_execution is not None:
                        plan_path = self.registry.write_snapshot_file(
                            run_id,
                            "execution_plan.json",
                            plan_strategy_execution(strategy_contract),
                        )
                        result["execution_plan"] = {"path": str(plan_path), "schema_version": "execution_plan.v1"}
            else:
                if normalize_strategy_run_config is None:
                    return result
                strategy_contract = normalize_strategy_run_config(
                    raw_config,
                    source_path=config_file,
                    repo_root=self.repo_root,
                )
                strategy_path = self.registry.write_snapshot_file(
                    run_id,
                    "strategy_run.json",
                    strategy_contract,
                )
                result["strategy_run"] = {
                    "config_path": str(strategy_path),
                    "schema_version": "strategy_run",
                }
                if plan_strategy_execution is not None:
                    plan_path = self.registry.write_snapshot_file(
                        run_id,
                        "execution_plan.json",
                        plan_strategy_execution(strategy_contract),
                    )
                    result["execution_plan"] = {"path": str(plan_path), "schema_version": "execution_plan.v1"}
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def _resolve_optional_config_path(self, path_text: str, *, config_file: Path) -> Optional[Path]:
        path = Path(path_text)
        candidates = []
        if path.is_absolute():
            candidates.append(path)
        candidates.append(config_file.parent / path_text)
        candidates.append(self.repo_root / path_text)
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        return None

    def _write_backtest_result_index(
        self,
        run_id: str,
        backtest_results: Dict[str, Any],
    ) -> None:
        rows: List[Dict[str, Any]] = []
        for item in backtest_results.get("results", []):
            params = item.get("params", {}) if isinstance(item, dict) else {}
            rows.append(
                {
                    "backtest_id": item.get("Backtest_id"),
                    "strategy_id": item.get("strategy_id"),
                    "strategy_display_label": self._build_strategy_display_label(
                        semantic_combo=dict(params.get("semantic_combo", {}) or {}),
                        semantic_run_label=params.get("semantic_run_label"),
                        strategy_id=item.get("strategy_id"),
                        backtest_id=item.get("Backtest_id"),
                    ),
                    "predictor": params.get("predictor"),
                    "semantic_fields": list(params.get("semantic_fields", []) or []),
                    "semantic_combo": dict(params.get("semantic_combo", {}) or {}),
                    "semantic_run_label": params.get("semantic_run_label"),
                    "signal_kernel_backend": params.get("signal_kernel_backend"),
                    "execution_backend": params.get("execution_backend"),
                    "strategy_contract_path": params.get("strategy_contract_path"),
                    "feature_contract_path": params.get("feature_contract_path"),
                    "feature_contract_hash": params.get("feature_contract_hash"),
                    "execution_plan_hash": params.get("execution_plan_hash"),
                    "source_audit_id": params.get("source_audit_id"),
                }
            )
        for item in backtest_results.get("portfolio_results", []) or []:
            config = getattr(item, "config", {}) if item is not None else {}
            if not isinstance(config, dict):
                config = {}
            params = config.get("resolved_params", {}) if isinstance(config.get("resolved_params"), dict) else {}
            backtest_id = str(getattr(item, "strategy_id", "") or config.get("strategy_id") or "")
            semantic_combo = {str(key): value for key, value in params.items()}
            rows.append(
                {
                    "backtest_id": backtest_id,
                    "strategy_id": backtest_id,
                    "strategy_display_label": self._build_strategy_display_label(
                        semantic_combo=semantic_combo,
                        semantic_run_label=None,
                        strategy_id=backtest_id,
                        backtest_id=backtest_id,
                    ),
                    "predictor": None,
                    "semantic_fields": list(semantic_combo.keys()),
                    "semantic_combo": semantic_combo,
                    "semantic_run_label": None,
                    "signal_kernel_backend": None,
                    "execution_backend": "portfolio_accounting",
                    "strategy_contract_path": None,
                    "feature_contract_path": None,
                    "feature_contract_hash": None,
                    "execution_plan_hash": None,
                    "source_audit_id": None,
                    "result_type": "portfolio",
                }
            )
        payload = {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-backtest-result-index-v1",
            "run_id": run_id,
            "requested_engine_mode": backtest_results.get("requested_engine_mode"),
            "resolved_engine_mode": backtest_results.get("resolved_engine_mode"),
            "strategy_mode": backtest_results.get("strategy_mode"),
            "execution_plan_path": backtest_results.get("execution_plan_path"),
            "field_symbol_table": list(backtest_results.get("field_symbol_table", []) or []),
            "backtests": rows,
        }
        self.registry.write_snapshot_file(run_id, "backtest_result_index.json", payload)

    @staticmethod
    def _build_strategy_display_label(
        *,
        semantic_combo: Dict[str, Any],
        semantic_run_label: Any,
        strategy_id: Any,
        backtest_id: Any,
    ) -> str:
        if isinstance(semantic_combo, dict) and semantic_combo:
            return " | ".join(
                f"{key}={semantic_combo[key]}" for key in sorted(semantic_combo.keys())
            )
        if isinstance(semantic_run_label, str) and semantic_run_label.strip():
            return semantic_run_label.strip()
        if isinstance(strategy_id, str) and strategy_id.strip():
            return strategy_id.strip()
        return f"Strategy {str(backtest_id or '')[:8]}"

    def _resolve_contract_ref(
        self,
        raw_path: Any,
        config_file_path: str,
    ) -> Dict[str, Optional[str]]:
        if not isinstance(raw_path, str) or not raw_path.strip():
            return {"path": None, "hash": None}
        resolved = resolve_input_path(
            raw_path,
            repo_root=self.repo_root,
            config_file_path=config_file_path,
        )
        if not resolved.path.exists():
            return {"path": str(resolved.path), "hash": None}
        return {"path": str(resolved.path), "hash": self._hash_file(resolved.path)}

    def _build_dataloader_health(
        self,
        *,
        run_id: str,
        dataloader_config: Dict[str, Any],
        data: pd.DataFrame,
        primary_artifact: Optional[Path],
    ) -> Dict[str, Any]:
        total_cells = int(data.shape[0] * data.shape[1]) if not data.empty else 0
        missing_ratio = (
            float(data.isna().sum().sum() / total_cells) if total_cells else 0.0
        )
        source_list = self._source_list_from_config(dataloader_config)
        quality = (
            AuditReaderBacktester.summarize_quality(primary_artifact)
            if primary_artifact
            else {}
        )
        audit_meta = (
            AuditReaderBacktester.load_metadata(primary_artifact)
            if primary_artifact
            else {}
        )
        return {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-dataloader-health-v1",
            "run_id": run_id,
            "source_list": source_list,
            "primary_source": source_list[0]["source_id"] if source_list else "unknown",
            "missing_ratio": missing_ratio,
            "fill_ratio": float(quality.get("fill_ratio", 0.0) or 0.0),
            "stale_ratio": float(quality.get("stale_ratio", 0.0) or 0.0),
            "max_age_bars": int(quality.get("max_age_bars", 0) or 0),
            "join_mode": "mixed" if len(source_list) > 1 else "primary",
            "calendar_policy": "primary_market_calendar",
            "source_audit_id": audit_meta.get("source_audit_id"),
            "warnings": list(quality.get("warnings", [])),
            "errors": [],
        }

    def _write_data_lineage_manifest(
        self,
        *,
        run_id: str,
        module: str,
        dataloader_config: Dict[str, Any],
        data: pd.DataFrame,
        raw_config: Dict[str, Any],
        primary_artifact: Optional[Path],
        dataloader_health: Optional[Dict[str, Any]] = None,
        wfa_results: Optional[Dict[str, Any]] = None,
        wfa_engine: Any = None,
    ) -> Dict[str, Any]:
        consumed_data_snapshot = self._write_consumed_data_snapshot(run_id, data)
        manifest = self._build_data_lineage_manifest(
            run_id=run_id,
            module=module,
            dataloader_config=dataloader_config,
            data=data,
            raw_config=raw_config,
            primary_artifact=primary_artifact,
            dataloader_health=dataloader_health or {},
            wfa_results=wfa_results,
            wfa_engine=wfa_engine,
            consumed_data_snapshot=consumed_data_snapshot,
        )
        self.registry.write_snapshot_file(run_id, "data_lineage_manifest.json", manifest)
        return manifest

    def _build_data_lineage_manifest(
        self,
        *,
        run_id: str,
        module: str,
        dataloader_config: Dict[str, Any],
        data: pd.DataFrame,
        raw_config: Dict[str, Any],
        primary_artifact: Optional[Path],
        dataloader_health: Dict[str, Any],
        wfa_results: Optional[Dict[str, Any]] = None,
        wfa_engine: Any = None,
        consumed_data_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        data_range = self._lineage_data_range(data)
        input_sources = self._lineage_input_sources(
            dataloader_config=dataloader_config,
            raw_config=raw_config,
            data=data,
        )
        input_sources = self._lineage_sources_with_consumed_snapshot(
            input_sources,
            consumed_data_snapshot,
        )
        windows = (
            self._lineage_wfa_windows(wfa_results=wfa_results, wfa_engine=wfa_engine)
            if module == "wfanalyser"
            else []
        )
        universe_provenance = self._lineage_universe_provenance(
            dataloader_config=dataloader_config,
            raw_config=raw_config,
            windows=windows,
        )
        windows = self._lineage_windows_with_universe_provenance(
            windows,
            universe_provenance,
        )
        if not input_sources or all(source.get("source_type") == "unknown" for source in input_sources):
            lineage_status = "unknown"
        else:
            lineage_status = "partial"
        coverage_level = self._lineage_coverage_level(module, raw_config, windows)

        transformations = self._lineage_transformations(module, raw_config, dataloader_config)
        factor_feature_audit = self._lineage_factor_feature_audit(raw_config)
        audit = self._lineage_audit(data, dataloader_health)
        warnings = list(audit.get("warnings", []))
        warnings.extend(str(item) for item in universe_provenance.get("warnings", []))
        warnings.extend(str(item) for item in factor_feature_audit.get("warnings", []))
        unknown_claims = self._lineage_unknown_claims(
            input_sources=input_sources,
            module=module,
            windows=windows,
            raw_config=raw_config,
            universe_provenance=universe_provenance,
            factor_feature_audit=factor_feature_audit,
        )
        if lineage_status != "complete":
            warnings.append(f"Lineage status is {lineage_status}; unknown claims require manual review.")
        validity_flags = {
            "point_in_time_known": bool(universe_provenance.get("point_in_time_constituents")),
            "survivorship_known": universe_provenance.get("survivorship_bias_risk") == "low",
            "corporate_actions_known": self._lineage_corporate_actions_known(raw_config),
            "feature_lag_verified": bool(factor_feature_audit.get("feature_lag_verified")),
            "lookahead_guard_verified": bool(factor_feature_audit.get("lookahead_guard_verified")),
        }
        audit["warnings"] = warnings
        return {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-data-lineage-manifest-v1",
            "run_id": run_id,
            "module": module,
            "generated_at": self._now_iso(),
            "lineage_status": lineage_status,
            "coverage_level": coverage_level,
            "config": self._lineage_config_refs(run_id=run_id, raw_config=raw_config, dataloader_config=dataloader_config),
            "input_sources": input_sources,
            "consumed_data_snapshot": consumed_data_snapshot or self._empty_consumed_data_snapshot(),
            "universe_provenance": universe_provenance,
            "factor_feature_audit": factor_feature_audit,
            "transformations": transformations,
            "audit": audit,
            "validity_flags": validity_flags,
            "lineage_claims": {
                "proven": self._lineage_proven_claims(
                    input_sources=input_sources,
                    primary_artifact=primary_artifact,
                    windows=windows,
                ),
                "inferred": self._lineage_inferred_claims(input_sources, data_range),
                "unknown": unknown_claims,
            },
            "linked_artifacts": self._lineage_linked_artifacts(
                run_id=run_id,
                primary_artifact=primary_artifact,
            ),
            "windows": windows,
            "derived_from_artifacts": self._lineage_derived_from_artifacts(
                module=module,
                primary_artifact=primary_artifact,
            ),
        }

    def _write_consumed_data_snapshot(self, run_id: str, data: pd.DataFrame) -> Dict[str, Any]:
        snapshot = self._empty_consumed_data_snapshot()
        if not isinstance(data, pd.DataFrame) or data.empty:
            return snapshot
        paths = self.registry.build_run_paths(run_id)
        snapshot_dir = paths["snapshot_dir"]
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = snapshot_dir / "consumed_market_data.parquet"
        try:
            data.to_parquet(parquet_path, index=False)
            path = parquet_path
            fmt = "parquet"
        except Exception:
            path = snapshot_dir / "consumed_market_data.csv"
            data.to_csv(path, index=False)
            fmt = "csv"
        return {
            "schema_version": "consumed_data_snapshot.v1",
            "status": "captured",
            "path": self._lineage_display_path(path),
            "format": fmt,
            "content_hash": self._hash_file(path),
            "row_count": int(len(data)),
            "column_list": [str(column) for column in data.columns],
        }

    @staticmethod
    def _empty_consumed_data_snapshot() -> Dict[str, Any]:
        return {
            "schema_version": "consumed_data_snapshot.v1",
            "status": "not_captured",
            "path": None,
            "format": None,
            "content_hash": None,
            "row_count": 0,
            "column_list": [],
        }

    @staticmethod
    def _lineage_sources_with_consumed_snapshot(
        input_sources: List[Dict[str, Any]],
        consumed_data_snapshot: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not isinstance(consumed_data_snapshot, dict):
            return input_sources
        if consumed_data_snapshot.get("status") != "captured":
            return input_sources
        content_hash = consumed_data_snapshot.get("content_hash")
        path = consumed_data_snapshot.get("path")
        updated: List[Dict[str, Any]] = []
        for source in input_sources:
            row = dict(source)
            if row.get("source_type") in {"provider", "generated"}:
                row["content_hash"] = content_hash
                row["cache"] = {
                    "path": path,
                    "content_hash": content_hash,
                    "status": "captured",
                }
                row["notes"] = [
                    note
                    for note in row.get("notes", [])
                    if "not snapshotted" not in str(note).lower()
                    and "did not expose a content snapshot" not in str(note).lower()
                ]
            updated.append(row)
        return updated

    def _finalize_data_lineage_manifest_links(
        self,
        run_id: str,
        manifest: Dict[str, Any],
    ) -> Dict[str, Any]:
        updated = copy.deepcopy(manifest)
        existing = {
            item.get("artifact_type"): item
            for item in updated.get("linked_artifacts", [])
            if isinstance(item, dict)
        }
        manifest_path = self.registry.build_run_paths(run_id)["artifact_manifest"]
        existing["artifact_manifest"] = self._lineage_artifact_ref(
            "artifact_manifest",
            manifest_path,
        )
        updated["linked_artifacts"] = list(existing.values())
        self.registry.write_snapshot_file(run_id, "data_lineage_manifest.json", updated)
        return updated

    def _lineage_input_sources(
        self,
        *,
        dataloader_config: Dict[str, Any],
        raw_config: Dict[str, Any],
        data: pd.DataFrame,
    ) -> List[Dict[str, Any]]:
        raw_data = raw_config.get("data") if isinstance(raw_config.get("data"), dict) else {}
        provider = str(
            raw_data.get("provider")
            or dataloader_config.get("provider")
            or dataloader_config.get("source")
            or "unknown"
        ).strip().lower()
        file_path = (
            raw_data.get("file_path")
            or raw_data.get("path")
            or (raw_data.get("file_config", {}) if isinstance(raw_data.get("file_config"), dict) else {}).get("file_path")
            or (dataloader_config.get("file_config", {}) if isinstance(dataloader_config.get("file_config"), dict) else {}).get("file_path")
            or dataloader_config.get("file_path")
            or dataloader_config.get("path")
            or dataloader_config.get("csv_path")
        )
        if provider in {"file", "csv", "parquet", "local_file"} or file_path:
            return [self._lineage_file_source(file_path, dataloader_config, raw_config, data)]
        if str(dataloader_config.get("source", "")) == "strategy_run_market_data":
            return [
                {
                    "source_id": "market_data",
                    "source_type": "generated",
                    "provider": provider,
                    "uri_or_path": "strategy_run internal market loader",
                    "content_hash": None,
                    "path_hash": None,
                    "identity_hash": self._hash_payload(
                        self._lineage_provider_identity(
                            provider=provider,
                            dataloader_config=dataloader_config,
                            raw_config=raw_config,
                        )
                    ),
                    "symbols": self._lineage_symbols(dataloader_config, raw_config),
                    "requested_start": self._lineage_requested_start(dataloader_config, raw_config),
                    "requested_end": self._lineage_requested_end(dataloader_config, raw_config),
                    "actual_start": self._lineage_data_range(data).get("start"),
                    "actual_end": self._lineage_data_range(data).get("end"),
                    "frequency_requested": self._lineage_frequency(dataloader_config, raw_config),
                    "frequency_resolved": self._lineage_resolved_frequency(dataloader_config, raw_config),
                    "timezone": self._lineage_timezone(raw_config),
                    "adjustment_policy": self._lineage_adjustment_policy(raw_config, dataloader_config),
                    "calendar_policy": self._lineage_calendar_policy(raw_config),
                    "cache": None,
                    "notes": ["Internal strategy_run loader did not expose a content snapshot to AppRuntime."],
                }
            ]
        if provider and provider != "unknown":
            return [
                {
                    "source_id": "market_data",
                    "source_type": "provider",
                    "provider": provider,
                    "uri_or_path": self._lineage_provider_uri(provider, dataloader_config, raw_config),
                    "content_hash": None,
                    "path_hash": None,
                    "identity_hash": self._hash_payload(
                        self._lineage_provider_identity(
                            provider=provider,
                            dataloader_config=dataloader_config,
                            raw_config=raw_config,
                        )
                    ),
                    "symbols": self._lineage_symbols(dataloader_config, raw_config),
                    "requested_start": self._lineage_requested_start(dataloader_config, raw_config),
                    "requested_end": self._lineage_requested_end(dataloader_config, raw_config),
                    "actual_start": self._lineage_data_range(data).get("start"),
                    "actual_end": self._lineage_data_range(data).get("end"),
                    "frequency_requested": self._lineage_frequency(dataloader_config, raw_config),
                    "frequency_resolved": self._lineage_resolved_frequency(dataloader_config, raw_config),
                    "timezone": self._lineage_timezone(raw_config),
                    "adjustment_policy": self._lineage_adjustment_policy(raw_config, dataloader_config),
                    "calendar_policy": self._lineage_calendar_policy(raw_config),
                    "cache": None,
                    "notes": ["Provider identity is recorded, but provider content was not snapshotted."],
                }
            ]
        return [
            {
                "source_id": "market_data",
                "source_type": "unknown",
                "provider": "unknown",
                "uri_or_path": "unknown",
                "content_hash": None,
                "path_hash": None,
                "identity_hash": None,
                "symbols": self._lineage_symbols(dataloader_config, raw_config),
                "requested_start": self._lineage_requested_start(dataloader_config, raw_config),
                "requested_end": self._lineage_requested_end(dataloader_config, raw_config),
                "actual_start": self._lineage_data_range(data).get("start"),
                "actual_end": self._lineage_data_range(data).get("end"),
                "frequency_requested": self._lineage_frequency(dataloader_config, raw_config),
                "frequency_resolved": self._lineage_resolved_frequency(dataloader_config, raw_config),
                "timezone": self._lineage_timezone(raw_config),
                "adjustment_policy": self._lineage_adjustment_policy(raw_config, dataloader_config),
                "calendar_policy": self._lineage_calendar_policy(raw_config),
                "cache": None,
                "notes": ["No resolvable market data source was found in the runtime config."],
            }
        ]

    def _lineage_file_source(
        self,
        file_path: Any,
        dataloader_config: Dict[str, Any],
        raw_config: Dict[str, Any],
        data: pd.DataFrame,
    ) -> Dict[str, Any]:
        raw_text = str(file_path or "").strip()
        config_file = dataloader_config.get("__config_file_path")
        resolved_path: Optional[Path] = None
        if raw_text:
            resolved = resolve_input_path(
                raw_text,
                repo_root=self.repo_root,
                config_file_path=str(config_file) if config_file else None,
            )
            resolved_path = resolved.path
        content_hash = (
            self._hash_file(resolved_path)
            if resolved_path is not None and resolved_path.exists()
            else None
        )
        path_hash = self._hash_text(str(resolved_path)) if resolved_path else None
        data_range = self._lineage_data_range(data)
        return {
            "source_id": "market_data",
            "source_type": "file",
            "provider": "local_file",
            "uri_or_path": self._lineage_display_path(resolved_path) if resolved_path else raw_text or "unknown",
            "content_hash": content_hash,
            "path_hash": self._hash_text(self._lineage_display_path(resolved_path)) if resolved_path else path_hash,
            "identity_hash": self._hash_payload({"path": self._lineage_display_path(resolved_path) if resolved_path else raw_text}),
            "symbols": self._lineage_symbols(dataloader_config, raw_config),
            "requested_start": self._lineage_requested_start(dataloader_config, raw_config),
            "requested_end": self._lineage_requested_end(dataloader_config, raw_config),
            "actual_start": data_range.get("start"),
            "actual_end": data_range.get("end"),
            "frequency_requested": self._lineage_frequency(dataloader_config, raw_config),
            "frequency_resolved": self._lineage_resolved_frequency(dataloader_config, raw_config),
            "timezone": self._lineage_timezone(raw_config),
            "adjustment_policy": self._lineage_adjustment_policy(raw_config, dataloader_config),
            "calendar_policy": self._lineage_calendar_policy(raw_config),
            "cache": None,
            "notes": [] if content_hash else ["Local file path could not be hashed; lineage is partial."],
        }

    @staticmethod
    def _lineage_data_range(data: pd.DataFrame) -> Dict[str, Optional[str]]:
        if not isinstance(data, pd.DataFrame) or data.empty:
            return {"start": None, "end": None}
        for column in ["Time", "Date", "Datetime", "timestamp", "date", "time"]:
            if column not in data.columns:
                continue
            values = pd.to_datetime(data[column], errors="coerce").dropna()
            if values.empty:
                continue
            return {
                "start": values.min().isoformat(),
                "end": values.max().isoformat(),
            }
        return {"start": None, "end": None}

    @staticmethod
    def _lineage_duplicate_timestamp_count(data: pd.DataFrame) -> int:
        if not isinstance(data, pd.DataFrame) or data.empty:
            return 0
        for column in ["Time", "Date", "Datetime", "timestamp", "date", "time"]:
            if column not in data.columns:
                continue
            values = pd.to_datetime(data[column], errors="coerce").dropna()
            return int(values.duplicated().sum())
        return 0

    @staticmethod
    def _lineage_monotonic_time(data: pd.DataFrame) -> bool:
        if not isinstance(data, pd.DataFrame) or data.empty:
            return True
        for column in ["Time", "Date", "Datetime", "timestamp", "date", "time"]:
            if column not in data.columns:
                continue
            values = pd.to_datetime(data[column], errors="coerce").dropna()
            if values.empty:
                return True
            return bool(values.is_monotonic_increasing)
        return True

    @staticmethod
    def _lineage_missing_ratio(data: pd.DataFrame) -> float:
        if not isinstance(data, pd.DataFrame) or data.empty:
            return 0.0
        total_cells = int(data.shape[0] * data.shape[1])
        if total_cells <= 0:
            return 0.0
        return float(data.isna().sum().sum() / total_cells)

    def _lineage_audit(
        self,
        data: pd.DataFrame,
        dataloader_health: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "row_count": int(len(data)) if isinstance(data, pd.DataFrame) else 0,
            "column_list": [str(col) for col in data.columns] if isinstance(data, pd.DataFrame) else [],
            "missing_ratio": float(
                dataloader_health.get("missing_ratio")
                if dataloader_health.get("missing_ratio") is not None
                else self._lineage_missing_ratio(data)
            ),
            "fill_ratio": float(dataloader_health.get("fill_ratio", 0.0) or 0.0),
            "stale_ratio": float(dataloader_health.get("stale_ratio", 0.0) or 0.0),
            "duplicate_timestamp_count": self._lineage_duplicate_timestamp_count(data),
            "monotonic_time": self._lineage_monotonic_time(data),
            "source_audit_id": dataloader_health.get("source_audit_id"),
            "warnings": list(dataloader_health.get("warnings", [])),
            "errors": list(dataloader_health.get("errors", [])),
        }

    @staticmethod
    def _lineage_symbols(dataloader_config: Dict[str, Any], raw_config: Dict[str, Any]) -> List[str]:
        raw_universe = raw_config.get("universe") if isinstance(raw_config.get("universe"), dict) else {}
        raw_data = raw_config.get("data") if isinstance(raw_config.get("data"), dict) else {}
        symbols = raw_universe.get("symbols") or dataloader_config.get("asset_symbols")
        if isinstance(symbols, list):
            return [str(item) for item in symbols]
        if raw_data.get("symbol"):
            return [str(raw_data["symbol"])]
        for nested_key in ["yfinance_config", "binance_config", "coinbase_config"]:
            nested = dataloader_config.get(nested_key)
            if isinstance(nested, dict) and nested.get("symbol"):
                return [str(nested["symbol"])]
        if dataloader_config.get("symbol"):
            return [str(dataloader_config["symbol"])]
        return []

    @staticmethod
    def _lineage_frequency(dataloader_config: Dict[str, Any], raw_config: Dict[str, Any]) -> Optional[str]:
        raw_data = raw_config.get("data") if isinstance(raw_config.get("data"), dict) else {}
        return str(raw_data.get("frequency") or dataloader_config.get("frequency") or "") or None

    @staticmethod
    def _lineage_resolved_frequency(dataloader_config: Dict[str, Any], raw_config: Dict[str, Any]) -> Optional[str]:
        raw_data = raw_config.get("data") if isinstance(raw_config.get("data"), dict) else {}
        for key in ("interval", "frequency"):
            if raw_data.get(key):
                return str(raw_data[key])
        for nested_key in ("yfinance_config", "binance_config", "coinbase_config"):
            nested = dataloader_config.get(nested_key)
            if isinstance(nested, dict) and nested.get("interval"):
                return str(nested["interval"])
        return str(dataloader_config.get("interval") or dataloader_config.get("frequency") or "") or None

    @staticmethod
    def _lineage_requested_start(dataloader_config: Dict[str, Any], raw_config: Dict[str, Any]) -> Optional[str]:
        raw_data = raw_config.get("data") if isinstance(raw_config.get("data"), dict) else {}
        return str(raw_data.get("start_date") or raw_data.get("start") or dataloader_config.get("start_date") or "") or None

    @staticmethod
    def _lineage_requested_end(dataloader_config: Dict[str, Any], raw_config: Dict[str, Any]) -> Optional[str]:
        raw_data = raw_config.get("data") if isinstance(raw_config.get("data"), dict) else {}
        return str(raw_data.get("end_date") or raw_data.get("end") or dataloader_config.get("end_date") or "") or None

    @staticmethod
    def _lineage_timezone(raw_config: Dict[str, Any]) -> Optional[str]:
        raw_data = raw_config.get("data") if isinstance(raw_config.get("data"), dict) else {}
        return str(raw_data.get("timezone") or "") or None

    @staticmethod
    def _lineage_calendar_policy(raw_config: Dict[str, Any]) -> Optional[str]:
        raw_data = raw_config.get("data") if isinstance(raw_config.get("data"), dict) else {}
        return str(raw_data.get("calendar") or raw_data.get("calendar_policy") or raw_data.get("start_policy") or "") or None

    @staticmethod
    def _lineage_adjustment_policy(raw_config: Dict[str, Any], dataloader_config: Dict[str, Any]) -> Optional[str]:
        raw_data = raw_config.get("data") if isinstance(raw_config.get("data"), dict) else {}
        if raw_data.get("adjustment_policy"):
            return str(raw_data["adjustment_policy"])
        if raw_data.get("auto_adjust") is not None:
            return f"auto_adjust_{str(raw_data.get('auto_adjust')).lower()}"
        source = str(dataloader_config.get("source") or raw_data.get("provider") or "").lower()
        if source == "yfinance":
            return "auto_adjust_false"
        if source in {"strategy_run_market_data", "multi_asset"}:
            return "delegated_to_market_loader"
        return None

    @staticmethod
    def _lineage_provider_uri(
        provider: str,
        dataloader_config: Dict[str, Any],
        raw_config: Dict[str, Any],
    ) -> str:
        symbols = AppRuntimeService._lineage_symbols(dataloader_config, raw_config)
        suffix = ",".join(symbols) if symbols else "unknown"
        return f"{provider}:{suffix}"

    @staticmethod
    def _lineage_provider_identity(
        *,
        provider: str,
        dataloader_config: Dict[str, Any],
        raw_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "provider": provider,
            "symbols": AppRuntimeService._lineage_symbols(dataloader_config, raw_config),
            "requested_start": AppRuntimeService._lineage_requested_start(dataloader_config, raw_config),
            "requested_end": AppRuntimeService._lineage_requested_end(dataloader_config, raw_config),
            "frequency": AppRuntimeService._lineage_frequency(dataloader_config, raw_config),
            "interval": AppRuntimeService._lineage_resolved_frequency(dataloader_config, raw_config),
            "timezone": AppRuntimeService._lineage_timezone(raw_config),
            "calendar_policy": AppRuntimeService._lineage_calendar_policy(raw_config),
            "adjustment_policy": AppRuntimeService._lineage_adjustment_policy(raw_config, dataloader_config),
        }

    @staticmethod
    def _hash_text(text: str) -> str:
        return "sha256:" + hashlib.sha256(str(text).encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_payload(payload: Dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _lineage_transformations(
        module: str,
        raw_config: Dict[str, Any],
        dataloader_config: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        rows = [
            {
                "name": "load_market_data",
                "status": "inferred",
                "details": {
                    "stage": "dataloader",
                    "inputs": ["input_sources.market_data"],
                    "outputs": ["runtime.dataframe"],
                },
            }
        ]
        if module == "autorunner":
            rows.extend(
                [
                    {
                        "name": "simulate_strategy",
                        "status": "inferred",
                        "details": {"stage": "backtester", "inputs": ["runtime.dataframe"], "outputs": ["backtester_artifacts"]},
                    },
                    {
                        "name": "derive_performance_metrics",
                        "status": "inferred",
                        "details": {"stage": "metricstracker", "inputs": ["backtester_artifacts"], "outputs": ["metric_artifacts"]},
                    },
                ]
            )
        elif module == "statanalyser":
            rows.append(
                {
                    "name": "derive_factor_statistics",
                    "status": "inferred",
                    "details": {"stage": "statanalyser", "inputs": ["runtime.dataframe"], "outputs": ["statanalyser_artifacts"]},
                }
            )
        elif module == "wfanalyser":
            rows.append(
                {
                    "name": "walk_forward_train_test_windows",
                    "status": "proven",
                    "details": {"stage": "wfanalyser", "inputs": ["runtime.dataframe"], "outputs": ["wfa_artifacts"]},
                }
            )
        if raw_config.get("schema_version") == "strategy_run" or str(dataloader_config.get("source", "")) == "strategy_run_market_data":
            rows.insert(
                0,
                {
                    "name": "strategy_run_to_runtime_sections",
                    "status": "proven",
                    "details": {
                        "stage": "contract_normalization",
                        "inputs": ["run_config"],
                        "outputs": ["dataloader_config", "backtester_config"],
                    },
                },
            )
        return rows

    def _lineage_wfa_windows(
        self,
        *,
        wfa_results: Optional[Dict[str, Any]],
        wfa_engine: Any,
    ) -> List[Dict[str, Any]]:
        rows = self._lineage_wfa_windows_from_selected(wfa_results)
        if rows:
            return rows
        candidates = []
        if isinstance(wfa_results, dict):
            maybe_windows = wfa_results.get("windows")
            if isinstance(maybe_windows, list):
                candidates.extend(maybe_windows)
        engine_windows = getattr(wfa_engine, "windows", None)
        if isinstance(engine_windows, list):
            candidates.extend(engine_windows)
        return self._lineage_wfa_windows_from_dicts(candidates)

    @staticmethod
    def _lineage_wfa_windows_from_selected(wfa_results: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not isinstance(wfa_results, dict):
            return []
        selected = wfa_results.get("selected_optimum")
        if not isinstance(selected, pd.DataFrame) or selected.empty:
            return []
        rows: List[Dict[str, Any]] = []
        for record in selected.to_dict(orient="records"):
            row = AppRuntimeService._lineage_window_row(record)
            if row:
                rows.append(row)
        return rows

    @staticmethod
    def _lineage_wfa_windows_from_dicts(windows: List[Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in windows:
            if isinstance(item, dict):
                row = AppRuntimeService._lineage_window_row(item)
                if row:
                    rows.append(row)
        return rows

    @staticmethod
    def _lineage_window_row(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        train_start = record.get("train_start_date", record.get("train_start"))
        train_end = record.get("train_end_date", record.get("train_end"))
        test_start = record.get("test_start_date", record.get("test_start"))
        test_end = record.get("test_end_date", record.get("test_end"))
        if train_start is None or train_end is None or test_start is None or test_end is None:
            return None
        try:
            window_id = int(record.get("window_id") or len(str(train_start)))
        except (TypeError, ValueError):
            window_id = 1
        return {
            "window_id": max(1, window_id),
            "train_start": AppRuntimeService._json_scalar(train_start),
            "train_end": AppRuntimeService._json_scalar(train_end),
            "test_start": AppRuntimeService._json_scalar(test_start),
            "test_end": AppRuntimeService._json_scalar(test_end),
            "symbols": [str(item) for item in record.get("symbols", [])] if isinstance(record.get("symbols"), list) else [],
            "lineage_status": "partial",
            "source_snapshot": "run_level_input_sources",
        }

    @staticmethod
    def _lineage_windows_with_universe_provenance(
        windows: List[Dict[str, Any]],
        universe_provenance: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if not windows:
            return []
        fallback_symbols = (
            universe_provenance.get("configured_symbols")
            or universe_provenance.get("runtime_symbols")
            or []
        )
        return [
            {
                **window,
                "symbols": (
                    list(window.get("symbols", []))
                    if isinstance(window.get("symbols"), list) and window.get("symbols")
                    else [str(item) for item in fallback_symbols]
                ),
                "universe_provenance": dict(universe_provenance),
            }
            for window in windows
        ]

    @staticmethod
    def _json_scalar(value: Any) -> Any:
        if pd.isna(value):
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value

    @staticmethod
    def _lineage_coverage_level(
        module: str,
        raw_config: Dict[str, Any],
        windows: List[Dict[str, Any]],
    ) -> str:
        if module == "wfanalyser" and windows:
            return "window"
        raw_universe = raw_config.get("universe") if isinstance(raw_config.get("universe"), dict) else {}
        symbols = raw_universe.get("symbols")
        if isinstance(symbols, list) and len(symbols) > 1:
            return "asset"
        if raw_config.get("features"):
            return "feature"
        return "run"

    def _lineage_universe_provenance(
        self,
        *,
        dataloader_config: Dict[str, Any],
        raw_config: Dict[str, Any],
        windows: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        universe = raw_config.get("universe") if isinstance(raw_config.get("universe"), dict) else {}
        symbols = AppRuntimeService._lineage_symbols(dataloader_config, raw_config)
        return AppRuntimeService._lineage_universe_provenance_from_universe(
            universe=universe,
            symbols=symbols,
            windows=windows,
            repo_root=self.repo_root,
            config_file_path=(
                str(dataloader_config.get("__config_file_path"))
                if dataloader_config.get("__config_file_path")
                else None
            ),
        )

    @staticmethod
    def _lineage_universe_provenance_from_universe(
        *,
        universe: Dict[str, Any],
        symbols: List[str],
        windows: Optional[List[Dict[str, Any]]] = None,
        repo_root: Optional[Path] = None,
        config_file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        configured_symbols = (
            [str(item) for item in universe.get("symbols", [])]
            if isinstance(universe.get("symbols"), list)
            else list(symbols)
        )
        policy = str(
            universe.get("universe_policy")
            or universe.get("survivorship_policy")
            or ""
        ).strip().lower()
        source_ref = AppRuntimeService._lineage_universe_source_ref(universe)
        source_type = AppRuntimeService._lineage_universe_source_type(
            universe=universe,
            configured_symbols=configured_symbols,
            source_ref=source_ref,
        )
        as_of_date = AppRuntimeService._lineage_universe_as_of_date(universe)
        constituents_validation = validate_historical_universe_constituents(
            universe=universe,
            configured_symbols=configured_symbols,
            as_of_date=as_of_date,
            repo_root=repo_root or Path.cwd(),
            config_file_path=config_file_path,
        )
        delisted_policy = str(universe.get("delisted_policy") or "").strip().lower()
        point_in_time_claimed = AppRuntimeService._lineage_universe_point_in_time_claimed(
            universe,
            policy,
        )
        current_or_static_source = AppRuntimeService._lineage_universe_source_is_current_or_static(
            source_type
        )
        strong_evidence_present = AppRuntimeService._lineage_universe_strong_evidence_present(
            universe=universe,
            source_type=source_type,
            source_ref=source_ref,
            as_of_date=as_of_date,
            constituents_validation=constituents_validation,
        )
        point_in_time_constituents = bool(
            point_in_time_claimed and strong_evidence_present and not current_or_static_source
        )
        delisted_included = any(
            token in delisted_policy for token in ("include", "historical", "delisted")
        )
        windows = windows or []
        window_source_snapshots = sorted(
            {
                str(item.get("source_snapshot"))
                for item in windows
                if isinstance(item, dict) and item.get("source_snapshot")
            }
        )

        warnings: List[str] = []
        if point_in_time_constituents and delisted_included:
            risk = "low"
            provenance_status = "valid"
        elif point_in_time_claimed:
            risk = "medium"
            provenance_status = "review"
            if not as_of_date:
                warnings.append("point_in_time_universe_claim_missing_as_of_date")
            if not strong_evidence_present:
                warnings.append("point_in_time_universe_claim_missing_evidence")
            if current_or_static_source:
                warnings.append("current_or_static_universe_source_not_point_in_time")
            if not delisted_included:
                warnings.append("delisted_symbol_policy_not_proven")
            if constituents_validation.get("status") in {"invalid", "missing"}:
                warnings.append("historical_constituents_content_validation_failed")
        elif configured_symbols:
            risk = "high"
            provenance_status = "review"
            warnings.append("static_or_current_universe_may_have_survivorship_bias")
        else:
            risk = "unknown"
            provenance_status = "review"
            warnings.append("universe_inferred_from_runtime_data")
        if windows and not point_in_time_constituents:
            warnings.append("wfa_windows_use_run_level_universe_without_point_in_time_constituents")
        warnings.extend(str(item) for item in constituents_validation.get("warnings", []))

        return {
            "schema_version": "universe_provenance.v1",
            "source_type": source_type,
            "source_ref": source_ref,
            "policy": policy or None,
            "as_of_date": as_of_date,
            "configured_symbols": configured_symbols,
            "runtime_symbols": list(symbols),
            "window_count": len(windows),
            "window_source_snapshots": window_source_snapshots,
            "point_in_time_constituents": point_in_time_constituents,
            "constituents_validation": constituents_validation,
            "delisted_policy": delisted_policy or None,
            "survivorship_bias_risk": risk,
            "provenance_status": provenance_status,
            "warnings": warnings,
        }

    @staticmethod
    def _lineage_universe_source_ref(universe: Dict[str, Any]) -> Optional[str]:
        return constituents_source_ref(universe)

    @staticmethod
    def _lineage_universe_source_type(
        *,
        universe: Dict[str, Any],
        configured_symbols: List[str],
        source_ref: Optional[str],
    ) -> str:
        raw = str(universe.get("source_type") or universe.get("source_kind") or "").strip().lower()
        if raw:
            return raw
        if source_ref:
            if constituents_path_declared(universe):
                return "historical_universe_constituents"
            return "declared_source"
        if configured_symbols:
            return "explicit_config_symbols"
        return "runtime_data"

    @staticmethod
    def _lineage_universe_as_of_date(universe: Dict[str, Any]) -> Optional[str]:
        value = (
            universe.get("as_of_date")
            or universe.get("as_of")
            or universe.get("historical_constituents_as_of")
            or universe.get("universe_constituents_as_of")
        )
        if value in (None, ""):
            return None
        return str(value)

    @staticmethod
    def _lineage_universe_point_in_time_claimed(
        universe: Dict[str, Any],
        policy: str,
    ) -> bool:
        if bool(
            universe.get("point_in_time_constituents")
            or universe.get("point_in_time")
        ):
            return True
        return policy in {
            "point_in_time",
            "point_in_time_snapshot",
            "historical_constituents",
            "historical_universe_constituents",
            "pit_universe",
        }

    @staticmethod
    def _lineage_universe_strong_evidence_present(
        *,
        universe: Dict[str, Any],
        source_type: str,
        source_ref: Optional[str],
        as_of_date: Optional[str],
        constituents_validation: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not as_of_date:
            return False
        validation_status = str((constituents_validation or {}).get("status") or "")
        if validation_status == "valid":
            return True
        if constituents_path_declared(universe):
            return False
        if declared_constituents_hash(universe) and source_type in CONSTITUENTS_SOURCE_TYPES:
            return True
        return bool(
            source_ref
            and as_of_date
            and source_type in CONSTITUENTS_SOURCE_TYPES
        )

    @staticmethod
    def _lineage_universe_source_is_current_or_static(source_type: str) -> bool:
        normalized = str(source_type or "").strip().lower()
        return normalized in {
            "all_symbols",
            "configured_symbols",
            "current_list",
            "current_provider_list",
            "current_symbols",
            "declared_current_source",
            "explicit_config_symbols",
            "fixed_list",
            "fixed_symbols",
            "index_current",
            "latest",
            "provider_current",
            "single_asset",
            "static_list",
            "static_symbols",
            "static_universe",
        }

    @staticmethod
    def _lineage_survivorship_known(raw_config: Dict[str, Any]) -> bool:
        universe = raw_config.get("universe") if isinstance(raw_config.get("universe"), dict) else {}
        symbols = [str(item) for item in universe.get("symbols", [])] if isinstance(universe.get("symbols"), list) else []
        provenance = AppRuntimeService._lineage_universe_provenance_from_universe(
            universe=universe,
            symbols=symbols,
            windows=[],
        )
        return provenance.get("survivorship_bias_risk") == "low"


    @staticmethod
    def _lineage_corporate_actions_known(raw_config: Dict[str, Any]) -> bool:
        raw_data = raw_config.get("data") if isinstance(raw_config.get("data"), dict) else {}
        return bool(raw_data.get("corporate_actions_verified") or raw_data.get("corporate_actions_known"))

    @staticmethod
    def _lineage_feature_lag_verified(raw_config: Dict[str, Any]) -> bool:
        execution = raw_config.get("fill_model") if isinstance(raw_config.get("fill_model"), dict) else {}
        if not execution:
            execution = raw_config.get("execution") if isinstance(raw_config.get("execution"), dict) else {}
        timing = str(execution.get("timing") or "").lower()
        if timing == "bar_offset":
            return int(execution.get("entry_delay_bars") or 0) > 0
        return False

    @staticmethod
    def _lineage_lookahead_guard_verified(raw_config: Dict[str, Any]) -> bool:
        if AppRuntimeService._lineage_feature_lag_verified(raw_config):
            return True
        execution = raw_config.get("execution") if isinstance(raw_config.get("execution"), dict) else {}
        return bool(execution.get("lookahead_guard_verified"))

    @staticmethod
    def _lineage_factor_feature_audit(raw_config: Dict[str, Any]) -> Dict[str, Any]:
        pipeline = raw_config.get("factor_pipeline")
        feature_lag_verified = AppRuntimeService._lineage_feature_lag_verified(raw_config)
        lookahead_guard_verified = AppRuntimeService._lineage_lookahead_guard_verified(raw_config)
        if not isinstance(pipeline, dict) or not pipeline:
            return {
                "schema_version": "factor_feature_audit.v1",
                "status": "not_applicable",
                "point_in_time_required": False,
                "feature_lag_verified": feature_lag_verified,
                "lookahead_guard_verified": lookahead_guard_verified,
                "known_at_fields": [],
                "effective_at_fields": [],
                "lag_policy": None,
                "warnings": [],
                "errors": [],
            }

        requirements = pipeline.get("data_requirements", {}) if isinstance(pipeline.get("data_requirements"), dict) else {}
        point_in_time = pipeline.get("point_in_time", {}) if isinstance(pipeline.get("point_in_time"), dict) else {}
        construction = pipeline.get("construction", []) if isinstance(pipeline.get("construction"), list) else []
        point_in_time_required = bool(requirements.get("point_in_time_required"))
        known_at_fields = []
        effective_at_fields = []
        if point_in_time.get("known_at_field"):
            known_at_fields.append(str(point_in_time.get("known_at_field")))
        if point_in_time.get("effective_at_field"):
            effective_at_fields.append(str(point_in_time.get("effective_at_field")))
        lag_policy = point_in_time.get("lag_policy") or point_in_time.get("lag_bars")
        uses_external_factor = False
        for spec in construction:
            if not isinstance(spec, dict):
                continue
            if spec.get("known_at"):
                known_at_fields.append(str(spec.get("known_at")))
            if spec.get("effective_at"):
                effective_at_fields.append(str(spec.get("effective_at")))
            op = str(spec.get("op") or "").lower()
            if any(token in op for token in ("book_to_market", "return_on_equity", "fundamental", "alternative")):
                uses_external_factor = True

        warnings: List[str] = []
        errors: List[str] = []
        if (point_in_time_required or uses_external_factor) and not (known_at_fields or effective_at_fields or lag_policy):
            errors.append("factor_point_in_time_metadata_missing")
        if not feature_lag_verified:
            warnings.append("factor_feature_lag_policy_not_verified")
        if not lookahead_guard_verified:
            warnings.append("factor_lookahead_guard_not_verified")
        status = "valid" if not errors else "invalid"
        return {
            "schema_version": "factor_feature_audit.v1",
            "status": status,
            "point_in_time_required": point_in_time_required,
            "feature_lag_verified": feature_lag_verified and not errors,
            "lookahead_guard_verified": lookahead_guard_verified and not errors,
            "known_at_fields": sorted(set(known_at_fields)),
            "effective_at_fields": sorted(set(effective_at_fields)),
            "lag_policy": lag_policy,
            "warnings": warnings,
            "errors": errors,
        }

    def _lineage_config_refs(
        self,
        *,
        run_id: str,
        raw_config: Dict[str, Any],
        dataloader_config: Dict[str, Any],
    ) -> Dict[str, Dict[str, Optional[str]]]:
        paths = self.registry.build_run_paths(run_id)
        run_config_path = paths["run_config_snapshot"]
        strategy_config_path = paths["snapshot_dir"] / "strategy_run.json"
        wfa_config_path = paths["snapshot_dir"] / "wfa_run.json"
        raw_config_file = str(dataloader_config.get("__config_file_path") or "").strip()
        config_file = Path(raw_config_file) if raw_config_file else None
        if config_file is not None and config_file.is_file():
            run_config_path = config_file.resolve()
        return {
            "run_config": self._lineage_file_ref(run_config_path),
            "strategy_config": self._lineage_file_ref(strategy_config_path),
            "wfa_config": self._lineage_file_ref(wfa_config_path if raw_config.get("schema_version") == "wfa_run" else None),
        }

    def _lineage_file_ref(self, path: Optional[Path]) -> Dict[str, Optional[str]]:
        if path is None:
            return {"path": None, "content_hash": None}
        path = Path(path)
        return {
            "path": self._lineage_display_path(path),
            "content_hash": self._hash_file(path) if path.is_file() else None,
        }

    def _lineage_artifact_ref(self, artifact_type: str, path: Optional[Path]) -> Dict[str, Any]:
        path_obj = Path(path) if path is not None else None
        return {
            "artifact_type": artifact_type,
            "path": self._lineage_display_path(path_obj) if path_obj is not None else "",
            "content_hash": self._hash_file(path_obj) if path_obj is not None and path_obj.exists() else None,
            "status": "ready" if path_obj is not None and path_obj.exists() else "missing",
        }

    def _lineage_display_path(self, path: Optional[Path]) -> str:
        if path is None:
            return ""
        path = Path(path)
        try:
            resolved = path.resolve()
            return str(resolved.relative_to(self.repo_root))
        except Exception:
            return path.name if path.is_absolute() else str(path)

    def _lineage_linked_artifacts(
        self,
        *,
        run_id: str,
        primary_artifact: Optional[Path],
    ) -> List[Dict[str, Any]]:
        paths = self.registry.build_run_paths(run_id)
        rows = [
            self._lineage_artifact_ref("run_snapshot", paths["snapshot_dir"] / "run_snapshot.json"),
            self._lineage_artifact_ref("dataloader_health", paths["dataloader_health"]),
        ]
        if primary_artifact is not None:
            artifact_type, _, _ = self._classify_artifact(primary_artifact)
            rows.append(
                self._lineage_artifact_ref(
                    artifact_type or "primary_artifact",
                    primary_artifact,
                )
            )
        return rows

    def _lineage_derived_from_artifacts(
        self,
        *,
        module: str,
        primary_artifact: Optional[Path],
    ) -> List[Dict[str, Any]]:
        if module not in {"statanalyser", "wfanalyser"} or primary_artifact is None:
            return []
        artifact_type, _, _ = self._classify_artifact(primary_artifact)
        return [self._lineage_artifact_ref(artifact_type or "primary_artifact", primary_artifact)]

    @staticmethod
    def _lineage_proven_claims(
        *,
        input_sources: List[Dict[str, Any]],
        primary_artifact: Optional[Path],
        windows: List[Dict[str, Any]],
    ) -> List[str]:
        claims: List[str] = []
        if any(source.get("content_hash") for source in input_sources):
            claims.append("At least one local input source has a SHA-256 content hash.")
        if primary_artifact is not None and primary_artifact.exists():
            claims.append("Primary output artifact exists and is hashable.")
        if windows:
            claims.append("WFA train/test window boundaries are captured.")
        if not claims:
            claims.append("Run-local lineage manifest was generated.")
        return claims

    @staticmethod
    def _lineage_inferred_claims(
        input_sources: List[Dict[str, Any]],
        data_range: Dict[str, Optional[str]],
    ) -> List[str]:
        claims = []
        if input_sources:
            claims.append("Provider identity, symbols, and requested range are inferred from runtime config.")
        if data_range.get("start") or data_range.get("end"):
            claims.append("Actual date range is inferred from the loaded runtime dataframe.")
        return claims

    @staticmethod
    def _lineage_unknown_claims(
        *,
        input_sources: List[Dict[str, Any]],
        module: str,
        windows: List[Dict[str, Any]],
        raw_config: Dict[str, Any],
        universe_provenance: Optional[Dict[str, Any]] = None,
        factor_feature_audit: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        claims: List[str] = []
        if any(source.get("source_type") == "provider" and not source.get("content_hash") for source in input_sources):
            claims.append("Provider content hash is not available.")
        if any(source.get("source_type") == "generated" and not source.get("content_hash") for source in input_sources):
            claims.append("Internal market loader did not expose a consumed data content snapshot.")
        if any(source.get("source_type") == "file" and not source.get("content_hash") for source in input_sources):
            claims.append("At least one local file source is missing a content hash.")
        if module == "wfanalyser" and not windows:
            claims.append("WFA window-level lineage is missing.")
        if module == "statanalyser":
            claims.append("StatAnalyser outputs are derived artifacts; upstream result lineage is partial unless linked artifacts are present.")
        if universe_provenance is None:
            universe = raw_config.get("universe") if isinstance(raw_config.get("universe"), dict) else {}
            symbols = (
                [str(item) for item in universe.get("symbols", [])]
                if isinstance(universe.get("symbols"), list)
                else []
            )
            universe_provenance = AppRuntimeService._lineage_universe_provenance_from_universe(
                universe=universe,
                symbols=symbols,
                windows=windows,
            )
        if universe_provenance.get("survivorship_bias_risk") != "low":
            claims.append("Point-in-time or survivorship universe evidence is not proven.")
        if universe_provenance.get("survivorship_bias_risk") == "high":
            claims.append("Configured universe symbols may be a current/static list with survivorship bias.")
        if not AppRuntimeService._lineage_feature_lag_verified(raw_config):
            claims.append("Feature lag policy is not proven from execution timing.")
        if factor_feature_audit is None:
            factor_feature_audit = AppRuntimeService._lineage_factor_feature_audit(raw_config)
        if factor_feature_audit.get("status") == "invalid":
            claims.append("Factor point-in-time metadata or feature lag audit is not valid.")
        claims.append("Runtime code version and dependency environment are not captured in this data lineage manifest.")
        if not claims:
            claims.append("No unresolved lineage claims were detected by AppRuntime.")
        return claims

    def _build_artifact_manifest(
        self,
        run_id: str,
        artifacts: List[Path],
    ) -> Dict[str, Any]:
        rows = []
        for path in artifacts:
            artifact_type, required_pages, optional = self._classify_artifact(path)
            if not artifact_type:
                continue
            rows.append(
                {
                    "artifact_type": artifact_type,
                    "path": str(path),
                    "required_by_pages": required_pages,
                    "status": "ready",
                    "generated_at": self._now_iso(),
                    "content_contract": self._content_contract_for_artifact(artifact_type),
                    "source_stage": self._source_stage_for_artifact(artifact_type),
                    "optional": optional,
                    "notes": None,
                }
            )
        return {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-artifact-manifest-v1",
            "run_id": run_id,
            "artifacts": rows,
        }

    def _classify_artifact(
        self,
        path: Path,
    ) -> tuple[Optional[str], List[str], bool]:
        name = path.name.lower()
        parent = path.parent.name.lower()
        if name == "data_lineage_manifest.json":
            return "data_lineage_manifest_json", ["results_library"], True
        if name.endswith("_equity_curve.parquet") or "_portfolio-equity_" in name:
            return "portfolio_equity_curve_parquet", ["metrics_explorer", "results_library"], False
        if name.endswith("_holdings.parquet") or "_portfolio-holdings_" in name:
            return "portfolio_holdings_parquet", ["metrics_explorer"], False
        if name.endswith("_rebalance_audit.parquet") or "_portfolio-rebalance-audit_" in name:
            return "portfolio_rebalance_audit_parquet", ["metrics_explorer"], False
        if name.endswith("_rebalance_trades.parquet") or "_portfolio-rebalance-trades_" in name or "_rebalance-trades_" in name:
            return "portfolio_rebalance_trades_parquet", ["metrics_explorer"], False
        if "_run_validation_report" in name or "_portfolio-run-validation_" in name:
            return "portfolio_run_validation_json", ["metrics_explorer", "results_library"], False
        if (name.endswith("_metadata.json") or "_portfolio-metadata_" in name) and ("portfolio" in name or parent == "portfolio"):
            return "portfolio_metadata_json", ["metrics_explorer", "results_library"], False
        if parent == "backtester":
            if ("audit" in name or "metadata" in name) and name.endswith(".parquet"):
                return "audit_sidecar", ["backtest_explorer"], True
            if ("audit" in name or "metadata" in name) and name.endswith(".json"):
                return "audit_sidecar", ["backtest_explorer", "results_library"], True
            if name.endswith(".parquet"):
                return "backtester_parquet", ["backtest_explorer", "results_library"], False
            if name.endswith(".csv"):
                return "backtester_csv", ["backtest_explorer"], True
            if name.endswith(".xlsx"):
                return "backtester_excel", ["backtest_explorer"], True
        if parent == "metricstracker":
            if "_metrics" in name and name.endswith(".parquet"):
                return "metricstracker_parquet", ["metrics_explorer"], False
            if "metadata" in name and name.endswith(".json"):
                return "metricstracker_metadata", ["metrics_explorer", "backtest_explorer"], True
        if parent == "wfanalyser":
            if (
                ("metadata" in name and name.endswith(".json"))
                or ("audit" in name and name.endswith(".json"))
                or ("audit" in name and name.endswith(".parquet"))
            ):
                return "audit_sidecar", ["wfa_studio", "results_library"], True
            if ("candidate_diagnostics" in name or "candidate-diagnostics" in name) and name.endswith(".parquet"):
                return "wfa_candidate_diagnostics_parquet", ["wfa_studio"], True
            if ("candidate_diagnostics" in name or "candidate-diagnostics" in name) and name.endswith(".csv"):
                return "wfa_candidate_diagnostics_csv", ["wfa_studio"], True
            if ("_ranking_" in name or "_ranking-" in name) and name.endswith(".parquet"):
                return "wfa_ranking_parquet", ["wfa_studio"], True
            if name.endswith(".parquet"):
                return "wfa_parquet", ["wfa_studio", "results_library"], False
            if name.endswith(".csv") and ("_ranking_" in name or "_ranking-" in name):
                return "wfa_ranking_csv", ["wfa_studio"], True
            if name.endswith(".csv"):
                return "wfa_csv", ["wfa_studio"], True
        if parent == "statanalyser":
            if "summary" in name and name.endswith(".json"):
                return "statanalyser_summary_json", ["statanalyser_studio"], False
            if name.endswith(".csv"):
                return "statanalyser_tabular_output", ["statanalyser_studio"], True
            if name.endswith(".md") or name.endswith(".txt") or name.endswith(".json"):
                return "statanalyser_report_file", ["statanalyser_studio"], True
        if parent == "chart_payloads":
            return "chart_payload", ["backtest_explorer", "metrics_explorer", "wfa_studio"], True
        return None, [], True

    def _source_stage_for_artifact(self, artifact_type: str) -> str:
        if artifact_type.startswith("portfolio"):
            return "backtester"
        if artifact_type.startswith("backtester") or artifact_type == "audit_sidecar":
            return "backtester"
        if artifact_type.startswith("metricstracker"):
            return "metricstracker"
        if artifact_type.startswith("wfa"):
            return "wfanalyser"
        if artifact_type.startswith("statanalyser"):
            return "statanalyser"
        if artifact_type == "data_lineage_manifest_json":
            return "app_export"
        return "app_export"

    @staticmethod
    def _content_contract_for_artifact(artifact_type: str) -> str:
        if artifact_type == "data_lineage_manifest_json":
            return "data-lineage-manifest-v1"
        return artifact_type

    def _write_backtest_chart_payloads(
        self,
        run_id: str,
        artifacts: List[Path],
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        metrics_path = self._select_primary_artifact(artifacts, "metricstracker_parquet")
        if not metrics_path:
            return rows

        df = pd.read_parquet(metrics_path)
        if df.empty:
            return rows

        payload = {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-chart-payload-v1",
            "run_id": run_id,
            "chart_type": "asset_curve_compare",
            "title": "Asset Curve Overview",
            "series": [
                {
                    "series_id": "strategy",
                    "label": "Strategy",
                    "x": [str(v) for v in df.get("Time", []).tolist()],
                    "y": [float(v) for v in df.get("Equity_value", []).fillna(0).tolist()],
                    "annotations": [],
                }
            ],
            "axes": {"x": "time", "y": "equity"},
            "legend": ["Strategy"],
            "artifact_source_refs": [str(metrics_path)],
            "generated_at": self._now_iso(),
            "notes": "Generated by App Runtime from metricstracker parquet.",
        }
        if "BAH_Equity" in df.columns:
            payload["series"].append(
                {
                    "series_id": "benchmark",
                    "label": "Buy and Hold",
                    "x": [str(v) for v in df.get("Time", []).tolist()],
                    "y": [float(v) for v in df.get("BAH_Equity", []).fillna(0).tolist()],
                    "annotations": [],
                }
            )
            payload["legend"].append("Buy and Hold")

        chart_path = (
            self.registry.build_run_paths(run_id)["chart_payload_dir"]
            / "asset_curve_compare.json"
        )
        chart_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rows.append(
            {
                "artifact_type": "chart_payload",
                "path": str(chart_path),
                "required_by_pages": ["backtest_explorer", "metrics_explorer"],
                "status": "ready",
                "generated_at": self._now_iso(),
                "content_contract": "chart-payload-v1",
                "source_stage": "app_export",
                "optional": True,
                "notes": "Derived in app runtime from metricstracker parquet.",
            }
        )
        return rows

    def _write_wfa_chart_payloads(
        self,
        run_id: str,
        artifacts: List[Path],
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        primary_wfa = self._select_primary_artifact(artifacts, "wfa_parquet")
        if not primary_wfa:
            return rows
        df = pd.read_parquet(primary_wfa)
        if df.empty or "window_id" not in df.columns:
            return rows

        metric_col = next(
            (
                col
                for col in ["oos_calmar", "oos_sharpe", "oos_total_return"]
                if col in df.columns
            ),
            None,
        )
        if metric_col is None:
            return rows
        grouped = df.groupby("window_id", as_index=False)[metric_col].mean()
        payload = {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-chart-payload-v1",
            "run_id": run_id,
            "chart_type": "wfa_window_metric",
            "title": f"WFA {metric_col} by window",
            "series": [
                {
                    "series_id": metric_col,
                    "label": metric_col,
                    "x": grouped["window_id"].astype(str).tolist(),
                    "y": [float(v) for v in grouped[metric_col].fillna(0).tolist()],
                    "annotations": [],
                }
            ],
            "axes": {"x": "window_id", "y": metric_col},
            "legend": [metric_col],
            "artifact_source_refs": [str(primary_wfa)],
            "generated_at": self._now_iso(),
            "notes": "Generated by App Runtime from primary WFA parquet.",
        }
        chart_path = (
            self.registry.build_run_paths(run_id)["chart_payload_dir"]
            / "wfa_window_metric.json"
        )
        chart_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rows.append(
            {
                "artifact_type": "chart_payload",
                "path": str(chart_path),
                "required_by_pages": ["wfa_studio"],
                "status": "ready",
                "generated_at": self._now_iso(),
                "content_contract": "chart-payload-v1",
                "source_stage": "app_export",
                "optional": True,
                "notes": "Derived in app runtime from WFA parquet.",
            }
        )
        return rows

    @staticmethod
    def _source_list_from_config(dataloader_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        source = str(dataloader_config.get("source", "unknown"))
        rows = [{"source_id": "price", "uri": source, "join_mode": "primary"}]
        predictor_cfg = dataloader_config.get("predictor_config", {})
        predictor_path = predictor_cfg.get("predictor_path")
        if isinstance(predictor_path, str) and predictor_path.strip():
            rows.append(
                {
                    "source_id": "predictor",
                    "uri": predictor_path,
                    "join_mode": "left",
                }
            )
        return rows

    def _select_primary_artifact(
        self,
        artifacts: List[Path],
        artifact_type: str,
    ) -> Optional[Path]:
        candidates = []
        for path in artifacts:
            kind, _, _ = self._classify_artifact(path)
            if kind == artifact_type:
                candidates.append(path)
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)[0]

    @staticmethod
    def _snapshot_output_files(
        directories: List[Path],
    ) -> Dict[str, Dict[str, int]]:
        snapshot: Dict[str, Dict[str, int]] = {}
        for directory in directories:
            snapshot[str(directory)] = {}
            if not directory.exists():
                continue
            for file_path in directory.rglob("*"):
                if file_path.is_file():
                    snapshot[str(directory)][str(file_path.resolve())] = (
                        file_path.stat().st_mtime_ns
                    )
        return snapshot

    @staticmethod
    def _discover_new_artifacts(
        before: Dict[str, Dict[str, int]],
        after: Dict[str, Dict[str, int]],
    ) -> List[Path]:
        rows: List[Path] = []
        for directory, after_files in after.items():
            before_files = before.get(directory, {})
            for file_path, mtime in after_files.items():
                if file_path not in before_files or before_files[file_path] != mtime:
                    rows.append(Path(file_path))
        rows.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return rows

    def _freeze_artifacts(
        self,
        run_id: str,
        artifacts: List[Path],
    ) -> List[Path]:
        managed_dir = self.registry.build_run_paths(run_id)["snapshot_dir"] / "managed_artifacts"
        managed_dir.mkdir(parents=True, exist_ok=True)
        frozen: List[Path] = []
        for path in artifacts:
            target_dir = managed_dir / path.parent.name
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / path.name
            if path.resolve() == target.resolve():
                frozen.append(target)
                continue
            if target.exists():
                target.unlink()
            try:
                path.replace(target)
            except OSError:
                shutil.copy2(path, target)
            frozen.append(target)
        return frozen

    def _list_configs(self, directory: Path, module: str) -> List[Dict[str, Any]]:
        rows = []
        show_hidden = str(os.getenv("LO2CIN4BT_SHOW_HIDDEN_CONFIGS", "")).strip() in {
            "1",
            "true",
            "yes",
        }
        for path in directory.glob("*.json"):
            if "template" in path.stem.lower():
                continue
            raw_text = ""
            try:
                raw_text = path.read_text(encoding="utf-8-sig")
                payload = json.loads(raw_text)
            except Exception:
                payload = {}
            if not show_hidden and self._is_hidden_config_item(path, payload):
                continue
            rows.append(
                decorate_config_item(
                    {
                        "label": path.name,
                        "value": str(path.resolve()),
                        "module": module,
                        "raw_config": payload,
                        "config_hash": hashlib.sha1(
                            raw_text.encode("utf-8", errors="ignore"), usedforsecurity=False
                        ).hexdigest()[:6],
                        "config_mtime": path.stat().st_mtime,
                        "summary": {
                            "source": payload.get("dataloader", {}).get(
                                "source", "unknown"
                            ),
                            "strategy_mode": payload.get("backtester", {}).get(
                                "strategy_mode", "auto"
                            ),
                            "has_strategy_contract": bool(
                                payload.get("backtester", {}).get(
                                    "strategy_contract_path"
                                )
                            ),
                        },
                        "platform": payload.get("platform", {}),
                    },
                    module,
                )
            )
        rows.sort(
            key=lambda item: (
                self._config_sort_date(item),
                float(item.get("config_mtime", 0) or 0),
                str(item.get("display_label") or item.get("label") or ""),
            ),
            reverse=True,
        )
        return rows

    @staticmethod
    def _is_hidden_config_item(path: Path, payload: Dict[str, Any]) -> bool:
        platform = payload.get("platform", {}) if isinstance(payload, dict) else {}
        if not isinstance(platform, dict):
            platform = {}
        visibility = str(platform.get("visibility", "")).strip().lower()
        if visibility in {"hidden", "internal", "dev", "fixture"}:
            return True

        display_label = str(platform.get("display_label", "")).strip().lower()
        stem = path.stem.lower()
        universe = payload.get("universe", {}) if isinstance(payload, dict) else {}
        symbols = universe.get("symbols", []) if isinstance(universe, dict) else []
        symbol_set = {str(symbol).upper() for symbol in symbols if symbol}
        universe_policy = str(universe.get("universe_policy", "")).strip().lower() if isinstance(universe, dict) else ""

        fixture_markers = (
            "sample" in stem
            or "fixture" in stem
            or "hidden from metrics selector" in display_label
            or universe_policy in {"static_sample", "sample", "fixture"}
            or bool(symbol_set) and symbol_set.issubset({"AAA", "BBB", "CCC", "ASSET"})
        )
        return fixture_markers

    @staticmethod
    def _config_sort_date(item: Dict[str, Any]) -> int:
        label = str(
            item.get("canonical_config_filename")
            or item.get("display_label")
            or item.get("filename")
            or item.get("label")
            or ""
        )
        match = re.search(r"(20\d{6})", label)
        if not match:
            return 0
        try:
            return int(match.group(1))
        except ValueError:
            return 0

    def _base_registry(
        self,
        *,
        run_id: str,
        module: str,
        entrypoint: str,
        status: str,
    ) -> Dict[str, Any]:
        return {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-run-registry-v1",
            "run_id": run_id,
            "entrypoint": entrypoint,
            "module": module,
            "status": status,
            "created_at": self._now_iso(),
            "completed_at": None,
            "symbol": "pending",
            "frequency": "pending",
            "strategy_mode": "pending",
            "config_filename": "",
            "semantic_label": "pending",
            "display_label": "pending",
            "run_type": "pending",
            "config_snapshot_dir": "",
            "artifact_manifest_path": "",
            "dataloader_health_path": "",
            "data_lineage_manifest_path": "",
            "lineage_status": "pending",
            "warning_count": 0,
            "error_count": 0,
        }

    @staticmethod
    def _new_stage_status(run_id: str, module: str) -> Dict[str, Any]:
        module_stage_defaults = {
            "autorunner": {
                "wfanalyser": "skipped",
            },
            "statanalyser": {
                "backtester": "skipped",
                "metricstracker": "skipped",
                "wfanalyser": "skipped",
            },
            "wfanalyser": {
                "dataloader": "skipped",
                "backtester": "skipped",
                "metricstracker": "skipped",
                "statanalyser": "skipped",
            },
        }
        defaults = module_stage_defaults.get(str(module), {})
        return {
            "run_id": run_id,
            "module": module,
            "status": "running",
            "current_stage": "config_validation",
            "stages": [
                {
                    "stage": stage,
                    "status": defaults.get(stage, "pending"),
                    "optional": stage in {"metricstracker", "statanalyser"},
                    "message": (
                        "Stage not part of this workflow"
                        if defaults.get(stage) == "skipped"
                        else None
                    ),
                }
                for stage in _STAGE_ORDER
            ],
        }

    @staticmethod
    def _mark_stage(
        stage_status: Dict[str, Any],
        stage_name: str,
        status: str,
        message: str,
    ) -> None:
        stage_status["current_stage"] = stage_name
        for item in stage_status["stages"]:
            if item["stage"] == stage_name:
                item["status"] = status
                item["message"] = message
        if (
            status == "failed"
            and not any(
                stage["optional"]
                for stage in stage_status["stages"]
                if stage["stage"] == stage_name
            )
        ):
            stage_status["status"] = "failed"
            return
        statuses = [stage["status"] for stage in stage_status["stages"]]
        if all(item in {"completed", "skipped"} for item in statuses):
            stage_status["status"] = "completed"
        elif any(item == "failed" for item in statuses):
            stage_status["status"] = "partial"
        else:
            stage_status["status"] = "running"

    def _fail_run(
        self,
        *,
        run_id: str,
        registry_payload: Dict[str, Any],
        stage_status: Dict[str, Any],
        stage_name: str,
        message: str,
    ) -> Dict[str, Any]:
        self._mark_stage(stage_status, stage_name, "failed", message)
        registry_payload["status"] = "failed"
        registry_payload["completed_at"] = self._now_iso()
        registry_payload["error_count"] = 1
        registry_payload["errors"] = [message]
        registry_payload["lineage_status"] = "unknown"
        try:
            self._write_unknown_data_lineage_manifest(
                run_id=run_id,
                module=str(registry_payload.get("module") or "unknown"),
                message=message,
            )
            registry_payload["data_lineage_manifest_path"] = str(
                self.registry.build_run_paths(run_id)["data_lineage_manifest"]
            )
        except Exception as exc:  # pragma: no cover - defensive closeout path
            registry_payload.setdefault("warnings", [])
            if isinstance(registry_payload["warnings"], list):
                registry_payload["warnings"].append(f"Unable to write failed-run lineage manifest: {exc}")
        self.registry.write_stage_status(run_id, stage_status)
        self.registry.write_registry_entry(registry_payload)
        return {"run_id": run_id, "status": "failed"}

    def _write_unknown_data_lineage_manifest(
        self,
        *,
        run_id: str,
        module: str,
        message: str,
    ) -> None:
        payload = {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-data-lineage-manifest-v1",
            "run_id": run_id,
            "module": module,
            "generated_at": self._now_iso(),
            "lineage_status": "unknown",
            "coverage_level": "run",
            "universe_provenance": {
                "schema_version": "universe_provenance.v1",
                "source_type": "unknown",
                "source_ref": None,
                "policy": None,
                "as_of_date": None,
                "configured_symbols": [],
                "runtime_symbols": [],
                "window_count": 0,
                "window_source_snapshots": [],
                "point_in_time_constituents": False,
                "constituents_validation": {
                    "schema_version": "historical_universe_constituents_validation.v1",
                    "status": "not_applicable",
                    "path": None,
                    "warnings": [],
                    "errors": [],
                },
                "delisted_policy": None,
                "survivorship_bias_risk": "unknown",
                "provenance_status": "review",
                "warnings": ["Run failed before universe provenance could be resolved."],
            },
            "input_sources": [
                {
                    "source_id": "market_data",
                    "source_type": "unknown",
                    "provider": "unknown",
                    "uri_or_path": "unknown",
                    "symbols": [],
                    "requested_start": None,
                    "requested_end": None,
                    "actual_start": None,
                    "actual_end": None,
                    "frequency_requested": None,
                    "frequency_resolved": None,
                    "timezone": None,
                    "adjustment_policy": None,
                    "calendar_policy": None,
                    "content_hash": None,
                    "path_hash": None,
                    "identity_hash": None,
                    "cache": None,
                    "notes": ["Run failed before data lineage could be resolved."],
                }
            ],
            "transformations": [],
            "audit": {
                "row_count": 0,
                "column_list": [],
                "missing_ratio": 0.0,
                "fill_ratio": 0.0,
                "stale_ratio": 0.0,
                "duplicate_timestamp_count": 0,
                "monotonic_time": True,
                "source_audit_id": None,
                "warnings": ["Run failed before complete lineage capture."],
                "errors": [message],
            },
            "validity_flags": {
                "point_in_time_known": False,
                "survivorship_known": False,
                "corporate_actions_known": False,
                "feature_lag_verified": False,
                "lookahead_guard_verified": False,
            },
            "lineage_claims": {
                "proven": [],
                "inferred": [],
                "unknown": [message],
            },
            "linked_artifacts": [],
            "windows": [],
            "derived_from_artifacts": [],
        }
        self.registry.write_snapshot_file(run_id, "data_lineage_manifest.json", payload)

    @staticmethod
    def _semantic_label_from_config(config_file: Path) -> str:
        return config_file.stem.replace(".user", "")

    @staticmethod
    def _load_app_config_metadata(config_file: Path) -> Dict[str, str]:
        try:
            payload = json.loads(config_file.read_text(encoding="utf-8-sig"))
            if isinstance(payload, dict):
                config_meta = payload.get("platform", {})
                if isinstance(config_meta, dict):
                    return {
                        "display_label": str(config_meta.get("display_label", "")).strip(),
                        "run_type": str(config_meta.get("run_type", "")).strip().lower(),
                    }
        except Exception:
            pass
        return {"display_label": "", "run_type": ""}

    @staticmethod
    def _extract_symbol(dataloader_config: Dict[str, Any]) -> str:
        source = str(dataloader_config.get("source", "yfinance"))
        if source == "binance":
            return str(
                dataloader_config.get("binance_config", {}).get("symbol", "BTCUSDT")
            )
        if source == "yfinance":
            return str(
                dataloader_config.get("yfinance_config", {}).get("symbol", "AAPL")
            )
        if source == "coinbase":
            return str(
                dataloader_config.get("coinbase_config", {}).get("symbol", "BTC-USD")
            )
        return "X"

    @staticmethod
    def _hash_file(path: Path) -> str:
        if not path.exists() or not path.is_file():
            return ""
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _new_run_id() -> str:
        return datetime.now().strftime("%Y%m%d") + "_" + secrets.token_hex(6)
