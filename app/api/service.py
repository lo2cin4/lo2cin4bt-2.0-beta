from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from app.runtime.registry import AppRegistry
from app.runtime.runtime import AppRuntimeService

from .labels import decorate_run_label
from .payloads import AppPayloadService
from .scheduler import AppBatchScheduler


class AppAPIService:
    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root).resolve()
        self.registry = AppRegistry(self.repo_root)
        self.runtime = AppRuntimeService(self.repo_root)
        self.payloads = AppPayloadService(self.repo_root, self.registry)
        self.scheduler = AppBatchScheduler(
            self.runtime,
            self.registry,
            self.payloads,
        )

    def command_center(self) -> Dict[str, Any]:
        runs = self.registry.list_runs()
        visible_runs = [
            row
            for row in runs
            if str(row.get("config_filename", "")).strip()
            or str(row.get("status", "")) in {"completed", "partial"}
        ]
        autorunner_runs = [row for row in runs if row.get("module") == "autorunner"]
        latest_metrics_run = autorunner_runs[0]["run_id"] if autorunner_runs else None
        active_batches = self.scheduler.list_active_batches()
        completed_runs = [row for row in runs if row.get("status") == "completed"]
        failed_runs = [row for row in runs if row.get("status") == "failed"]
        completed_by_module: Dict[str, int] = {}
        failed_by_module: Dict[str, int] = {}
        for row in completed_runs:
            module_name = str(row.get("module", "") or "unknown")
            completed_by_module[module_name] = completed_by_module.get(module_name, 0) + 1
        for row in failed_runs:
            module_name = str(row.get("module", "") or "unknown")
            failed_by_module[module_name] = failed_by_module.get(module_name, 0) + 1
        latest_result = visible_runs[0] if visible_runs else {}
        return {
            "active_batches": active_batches,
            "recent_runs": [self._decorate_run(row) for row in visible_runs[:8]],
            "resource_snapshot": {
                "cpu_count": os.cpu_count() or 1,
                "scheduler_capacity": self.scheduler.capacity,
                "active_batch_count": len(active_batches),
                "successful_runs": len(completed_runs),
                "failed_runs": len(failed_runs),
                "completed_by_module": completed_by_module,
                "failed_by_module": failed_by_module,
                "recent_successful_runs": len(completed_runs[:8]),
                "recent_failed_runs": len(failed_runs[:8]),
                "latest_result_time": str(
                    latest_result.get("completed_at")
                    or latest_result.get("created_at")
                    or ""
                ),
            },
            "latest_metrics_run_id": latest_metrics_run,
        }

    def run_center_configs(self) -> Dict[str, Any]:
        return {
            "autorunner": self.runtime.list_run_configs(),
            "wfa": self.runtime.list_wfa_configs(),
            "statanalyser": self.runtime.list_statanalyser_configs(),
        }

    def local_folder_target_path(self, target: str) -> Path:
        workspace_targets = {
            "autorunner": "runs",
            "backtest": "runs",
            "backtests": "runs",
            "wfa": "wfa",
            "walk-forward": "wfa",
            "statanalyser": "statanalyser",
            "factor": "statanalyser",
            "datasets": "datasets",
            "features": "features",
            "strategies": "strategies",
        }
        output_targets = {
            "output": "outputs/app",
            "outputs": "outputs/app",
            "app-output": "outputs/app",
            "app-outputs": "outputs/app",
            "backtester-raw-output": "outputs/backtester",
            "wfanalyser-raw-output": "outputs/wfanalyser",
        }
        normalized = str(target or "").strip().lower()
        if normalized in workspace_targets:
            path = (self.repo_root / "workspace" / workspace_targets[normalized]).resolve()
        elif normalized in {"autorunner-output", "backtest-output", "backtests-output"}:
            path = self._latest_run_artifact_folder("autorunner")
        elif normalized in {"wfa-output", "walk-forward-output"}:
            path = self._latest_run_artifact_folder("wfanalyser")
        elif normalized in output_targets:
            path = (self.repo_root / output_targets[normalized]).resolve()
        else:
            raise ValueError(f"Unknown local folder target: {target}")
        try:
            path.relative_to(self.repo_root)
        except ValueError as exc:
            raise ValueError(f"Local folder target escaped repo: {target}") from exc
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _latest_run_artifact_folder(self, module: str) -> Path:
        for row in self.registry.list_runs(module=module):
            if str(row.get("status", "")).lower() not in {"completed", "partial"}:
                continue
            run_id = str(row.get("run_id", "")).strip()
            if not run_id:
                continue
            preferred = self._preferred_artifact_folder(run_id, module)
            if preferred is not None:
                return preferred
            run_paths = self.registry.build_run_paths(run_id)
            managed_root = (run_paths["snapshot_dir"] / "managed_artifacts").resolve()
            if managed_root.exists():
                return managed_root
            if run_paths["snapshot_dir"].exists():
                return run_paths["snapshot_dir"].resolve()
        snapshots_root = (self.repo_root / "outputs" / "app" / "run_snapshots").resolve()
        snapshots_root.mkdir(parents=True, exist_ok=True)
        return snapshots_root

    def _preferred_artifact_folder(self, run_id: str, module: str) -> Path | None:
        artifacts = self._load_artifacts(run_id)

        def artifact_priority(item: Dict[str, Any]) -> int:
            artifact_type = str(item.get("artifact_type", "")).lower()
            source_stage = str(item.get("source_stage", "")).lower()
            path_suffix = Path(str(item.get("path", ""))).suffix.lower()
            if path_suffix != ".parquet":
                return 50
            if module == "autorunner":
                if artifact_type.startswith("portfolio_"):
                    return 0
                if artifact_type.startswith("backtester"):
                    return 1
                if source_stage == "backtester":
                    return 2
            if module == "wfanalyser":
                if source_stage == "wfanalyser":
                    return 0
                if artifact_type.startswith("wfa") or artifact_type.startswith("wfanalyser"):
                    return 1
            return 20

        for artifact in sorted(
            [item for item in artifacts if isinstance(item, dict)],
            key=artifact_priority,
        ):
            raw_path = str(artifact.get("path", "")).strip()
            if not raw_path:
                continue
            artifact_path = self._resolve_repo_path(raw_path)
            if artifact_path is None:
                continue
            artifact_dir = artifact_path.parent
            if artifact_dir.exists() or artifact_path.exists():
                return artifact_dir
        return None

    def workspace_target_path(self, target: str) -> Path:
        normalized = str(target or "").strip().lower()
        workspace_aliases = {
            "autorunner",
            "backtest",
            "backtests",
            "wfa",
            "walk-forward",
            "statanalyser",
            "factor",
            "datasets",
            "features",
            "strategies",
        }
        if normalized not in workspace_aliases:
            raise ValueError(f"Unknown workspace target: {target}")
        return self.local_folder_target_path(normalized)

    def open_local_folder_target(self, target: str) -> Dict[str, Any]:
        path = self.local_folder_target_path(target)
        opener = self._open_local_path(path)
        return {
            "status": "opened",
            "target": str(target or "").strip().lower(),
            "path": str(path),
            "opener": opener,
        }

    def open_workspace_target(self, target: str) -> Dict[str, Any]:
        self.workspace_target_path(target)
        return self.open_local_folder_target(target)

    def _open_local_path(self, path: Path) -> str:
        target_path = Path(path).resolve()
        try:
            target_path.relative_to(self.repo_root)
        except ValueError as exc:
            raise ValueError("Local opener path escaped repo") from exc

        editor = os.environ.get("LO2CIN4BT_FILE_EDITOR", "").strip()
        if editor:
            command_parts = shlex.split(editor, posix=sys.platform != "win32")
            if not command_parts:
                raise OSError("LO2CIN4BT_FILE_EDITOR did not include an executable")
            executable = self._resolve_local_opener_executable(command_parts[0])
            command = [executable, *command_parts[1:], str(target_path)]
            # Local-only opener: executable is resolved, shell=False, and path is repo-bound.
            subprocess.Popen(  # nosec B603
                command,
                cwd=str(self.repo_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return command[0]

        code_command = shutil.which("code")
        if code_command:
            # Local-only opener: executable is resolved, shell=False, and path is repo-bound.
            subprocess.Popen(  # nosec B603
                [code_command, str(target_path)],
                cwd=str(self.repo_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return "code"

        if sys.platform.startswith("win"):
            os.startfile(str(target_path))  # type: ignore[attr-defined]
            return "file-explorer"
        if sys.platform == "darwin":
            open_command = self._resolve_local_opener_executable("open")
            # Local-only opener: executable is resolved, shell=False, and path is repo-bound.
            subprocess.Popen(  # nosec B603
                [open_command, str(target_path)],
                cwd=str(self.repo_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return "finder"
        xdg_open_command = self._resolve_local_opener_executable("xdg-open")
        # Local-only opener: executable is resolved, shell=False, and path is repo-bound.
        subprocess.Popen(  # nosec B603
            [xdg_open_command, str(target_path)],
            cwd=str(self.repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return "xdg-open"

    @staticmethod
    def _resolve_local_opener_executable(command: str) -> str:
        executable = str(command or "").strip().strip("\"'")
        if not executable:
            raise OSError("Local opener executable is empty")
        candidate = Path(executable)
        if candidate.is_absolute():
            if candidate.exists():
                return str(candidate)
            raise OSError(f"Local opener executable not found: {executable}")
        resolved = shutil.which(executable)
        if resolved:
            return resolved
        raise OSError(f"Unable to resolve local opener executable: {executable}")

    def metrics_runs(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for row in self.registry.list_run_history(module="autorunner"):
            if (
                row.get("status") in {"completed", "partial"}
                and str(row.get("config_filename", "")).strip()
                and not self._is_hidden_sample_run(row)
                and self._has_metrics_renderable_output(str(row.get("run_id", "")))
            ):
                rows.append(self._decorate_run(row))
        return rows

    def wfa_runs(self) -> List[Dict[str, Any]]:
        return [
            self._decorate_run(row)
            for row in self.registry.list_runs(module="wfanalyser")
            if row.get("status") in {"completed", "partial"}
            and self._has_renderable_wfa(str(row.get("run_id", "")))
        ]

    def stat_runs(self) -> List[Dict[str, Any]]:
        return [
            self._decorate_run(row)
            for row in self.registry.list_runs(module="statanalyser")
            if row.get("status") in {"completed", "partial"}
            and (
                self._has_artifact_type(
                    str(row.get("run_id", "")), "statanalyser_summary_json"
                )
                or self.registry.build_run_paths(str(row.get("run_id", "")))[
                    "snapshot_dir"
                ].joinpath("statanalyser_summary.json").exists()
            )
        ]

    def metrics_overview(self, run_id: str) -> Dict[str, Any]:
        path = self.payloads.ensure_metrics_overview_payload(run_id)
        return AppPayloadService._load_json(path, {})

    def metrics_overview_path(self, run_id: str) -> Path:
        return self.payloads.ensure_metrics_overview_payload(run_id)

    def precompressed_json_path(self, path: Path) -> Path:
        return AppPayloadService.ensure_precompressed_json(path)

    def parameter_matrix(self, run_id: str) -> Dict[str, Any]:
        default_overrides = self._default_parameter_review_overrides()
        return self.payloads.build_parameter_matrix_payload(
            run_id,
            force=True,
            ranking_config_override=default_overrides["ranking"],
            acceptance_config_override=default_overrides["acceptance"],
        )

    def parameter_matrix_review_preview(
        self,
        run_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        defaults = self._default_parameter_review_overrides()
        ranking = copy.deepcopy(defaults["ranking"])
        acceptance = copy.deepcopy(defaults["acceptance"])
        if isinstance(payload, dict):
            incoming_ranking = payload.get("ranking", {})
            incoming_acceptance = payload.get("acceptance", {})
            if isinstance(incoming_ranking, dict):
                ranking.update(incoming_ranking)
            if isinstance(incoming_acceptance, dict):
                acceptance.update(incoming_acceptance)
        return self.payloads.build_parameter_matrix_payload(
            run_id,
            force=True,
            ranking_config_override=ranking,
            acceptance_config_override=acceptance,
        )

    def list_parameter_review_templates(self) -> Dict[str, Any]:
        store = self._load_parameter_review_template_store()
        templates = store.get("templates", []) if isinstance(store, dict) else []
        default_name = str(store.get("default_template_name", "") or "").strip()
        output_templates = []
        for item in templates:
            normalized = dict(item) if isinstance(item, dict) else {}
            normalized["is_default"] = str(normalized.get("name", "")).strip() == default_name
            output_templates.append(normalized)
        return {
            "schema_version": "1.1",
            "default_template_name": default_name,
            "templates": output_templates,
        }

    def save_parameter_review_template(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        name = str(payload.get("name", "") or "").strip()
        if not name:
            raise ValueError("template name is required")
        ranking = payload.get("ranking", {}) if isinstance(payload, dict) else {}
        acceptance = payload.get("acceptance", {}) if isinstance(payload, dict) else {}
        store = self._load_parameter_review_template_store()
        templates = store.get("templates", []) if isinstance(store, dict) else []
        templates = [item for item in templates if str(item.get("name", "")).strip().lower() != name.lower()]
        templates.append(
            {
                "name": name,
                "acceptance": acceptance if isinstance(acceptance, dict) else {},
                "ranking": ranking if isinstance(ranking, dict) else {},
                "updated_at": pd.Timestamp.utcnow().isoformat(),
            }
        )
        templates.sort(key=lambda item: str(item.get("name", "")).lower())
        store["templates"] = templates
        default_name = str(store.get("default_template_name", "") or "").strip()
        if not default_name:
            store["default_template_name"] = name
        self._write_parameter_review_template_store(store)
        return {
            "status": "saved",
            "name": name,
            "default_template_name": str(store.get("default_template_name", "") or "").strip(),
            "template_count": len(templates),
        }

    def delete_parameter_review_template(self, name: str) -> Dict[str, Any]:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise ValueError("template name is required")
        store = self._load_parameter_review_template_store()
        templates = store.get("templates", []) if isinstance(store, dict) else []
        filtered = [
            item
            for item in templates
            if str(item.get("name", "")).strip().lower() != normalized_name.lower()
        ]
        if len(filtered) == len(templates):
            raise ValueError(f"template not found: {normalized_name}")
        store["templates"] = filtered
        default_name = str(store.get("default_template_name", "") or "").strip()
        if default_name and default_name.lower() == normalized_name.lower():
            store["default_template_name"] = str(filtered[0].get("name", "")) if filtered else ""
        self._write_parameter_review_template_store(store)
        return {
            "status": "deleted",
            "name": normalized_name,
            "default_template_name": str(store.get("default_template_name", "") or "").strip(),
            "template_count": len(filtered),
        }

    def set_default_parameter_review_template(self, name: str) -> Dict[str, Any]:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise ValueError("template name is required")
        store = self._load_parameter_review_template_store()
        templates = store.get("templates", []) if isinstance(store, dict) else []
        matched = next(
            (item for item in templates if str(item.get("name", "")).strip().lower() == normalized_name.lower()),
            None,
        )
        if not matched:
            raise ValueError(f"template not found: {normalized_name}")
        store["default_template_name"] = str(matched.get("name", "")).strip()
        self._write_parameter_review_template_store(store)
        return {
            "status": "default_set",
            "name": str(matched.get("name", "")).strip(),
            "template_count": len(templates),
        }

    def backtest_detail(self, run_id: str, backtest_id: str) -> Dict[str, Any]:
        path = self.payloads.ensure_backtest_detail_payload(run_id, backtest_id)
        return AppPayloadService._load_json(path, {})

    def backtest_detail_path(self, run_id: str, backtest_id: str) -> Path:
        return self.payloads.ensure_backtest_detail_payload(run_id, backtest_id)

    def export_backtest_csv(self, run_id: str, backtest_id: str) -> tuple[pd.DataFrame, str]:
        artifact_path = self._artifact_existing_path(run_id, "backtester_parquet")
        if artifact_path is None:
            raise FileNotFoundError(f"Backtester parquet not found for run {run_id}")

        try:
            records = pd.read_parquet(artifact_path, filters=[("Backtest_id", "==", str(backtest_id))])
        except (OSError, ValueError, TypeError, KeyError, ImportError):
            records = pd.read_parquet(artifact_path)
            records = records.loc[records["Backtest_id"].astype(str) == str(backtest_id)].copy()

        if records.empty:
            raise FileNotFoundError(f"Backtest {backtest_id} not found in run {run_id}")

        label = str(self.backtest_detail(run_id, backtest_id).get("label", backtest_id))
        safe_label = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in label).strip("_") or str(backtest_id)
        filename = f"{safe_label}_{backtest_id}.csv"
        return records, filename

    def wfa_dashboard(self, run_id: str) -> Dict[str, Any]:
        path = self.payloads.ensure_wfa_dashboard_payload(run_id)
        return AppPayloadService._load_json(path, {})

    def statanalyser_summary(self, run_id: str) -> Dict[str, Any]:
        path = self.payloads.ensure_statanalyser_summary_payload(run_id)
        return AppPayloadService._load_json(path, {})

    def ai_readable_output(self, run_id: str) -> Dict[str, Any]:
        path = self.payloads.ensure_ai_readable_output(run_id)
        return AppPayloadService._load_json(path, {})

    def _decorate_run(self, row: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(row)
        run_id = str(row.get("run_id", ""))
        snapshot = self.registry.build_run_paths(run_id)["snapshot_dir"] / "run_snapshot.json"
        if snapshot.exists():
            try:
                snapshot_payload = json.loads(snapshot.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                snapshot_payload = {}
            resolved = (
                snapshot_payload.get("resolved_configs", {})
                if isinstance(snapshot_payload, dict)
                else {}
            )
            if isinstance(resolved, dict):
                payload["dataloader_config"] = resolved.get("dataloader_config", {})
                payload["backtester_config"] = resolved.get("backtester_config", {})
                payload["wfa_config"] = resolved.get("wfa_config", {})
        if str(row.get("module", "")) == "autorunner":
            index_path = self.registry.build_run_paths(run_id)["snapshot_dir"] / "backtest_result_index.json"
            payload["semantic_index_complete"] = index_path.exists()
            if not index_path.exists():
                payload["strategy_label_mode"] = "internal_id_fallback"
            payload["strategy_summary"] = self.payloads._strategy_summary(run_id)
        payload = decorate_run_label(payload)
        artifacts = self._load_artifacts(run_id)
        if str(row.get("module", "")) == "autorunner":
            for artifact in artifacts:
                if artifact.get("artifact_type") != "portfolio_metadata_json":
                    continue
                metadata_path = self._existing_repo_artifact_path(artifact.get("path", ""))
                if metadata_path is not None:
                    metadata = AppPayloadService._load_json(metadata_path, {})
                    if isinstance(metadata, dict):
                        payload["strategy_summary"] = self.payloads._portfolio_strategy_summary(run_id, metadata)
                    break
        preferred_types = {
            "autorunner": "metricstracker_parquet",
            "wfanalyser": "wfa_parquet",
            "statanalyser": "statanalyser_summary_json",
        }
        artifact_name = None
        preferred = preferred_types.get(str(row.get("module", "")))
        preferred_candidates = [preferred] if preferred else []
        if str(row.get("module", "")) == "autorunner":
            preferred_candidates.extend(["portfolio_metadata_json", "portfolio_equity_curve_parquet"])
        if preferred_candidates:
            for artifact in artifacts:
                if artifact.get("artifact_type") not in preferred_candidates:
                    continue
                path = self._resolve_repo_path(artifact.get("path", ""))
                if path is None:
                    continue
                name = path.name.lower()
                if "_audit" in name or "_metadata" in name:
                    if artifact.get("artifact_type") != "portfolio_metadata_json":
                        continue
                if artifact.get("artifact_type") == "portfolio_metadata_json":
                    artifact_name = path.name
                    break
                if "_audit" in name or "_metadata" in name:
                    continue
                artifact_name = path.name
                break
        payload["primary_artifact_name"] = artifact_name
        if str(row.get("module", "")) == "wfanalyser":
            payload["selector_label"] = str(payload.get("display_label", "")).strip() or str(artifact_name or run_id)
        return payload

    def _has_artifact_type(self, run_id: str, artifact_type: str) -> bool:
        if not run_id:
            return False
        for artifact in self._load_artifacts(run_id):
            if artifact.get("artifact_type") != artifact_type:
                continue
            if self._existing_repo_artifact_path(artifact.get("path", "")) is not None:
                return True
        return False

    def _has_metrics_renderable_output(self, run_id: str) -> bool:
        if not run_id:
            return False
        has_classic_metrics = (
            self._has_artifact_type(run_id, "backtester_parquet")
            and self._has_artifact_type(run_id, "metricstracker_parquet")
            and self._has_heatmap_axes(run_id)
        )
        if has_classic_metrics:
            return True
        return (
            self._has_artifact_type(run_id, "portfolio_metadata_json")
            and self._has_artifact_type(run_id, "portfolio_equity_curve_parquet")
        )

    @staticmethod
    def _is_hidden_sample_run(row: Dict[str, Any]) -> bool:
        config_name = str(row.get("config_filename", "")).lower()
        label = str(row.get("display_label", "")).lower()
        semantic = str(row.get("semantic_label", "")).lower()
        sample_markers = ("multi_asset_sample", "sample-monthly-top2", "aaa-bbb-ccc")
        return any(marker in value for marker in sample_markers for value in (config_name, label, semantic))

    def _artifact_existing_path(self, run_id: str, artifact_type: str) -> Path | None:
        if not run_id:
            return None
        for artifact in self._load_artifacts(run_id):
            if artifact.get("artifact_type") != artifact_type:
                continue
            path = self._existing_repo_artifact_path(artifact.get("path", ""))
            if path is not None:
                return path
        return None

    def _load_artifacts(self, run_id: str) -> List[Dict[str, Any]]:
        try:
            manifest = self.registry.load_artifact_manifest(run_id)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return []
        artifacts = manifest.get("artifacts", []) if isinstance(manifest, dict) else []
        if not isinstance(artifacts, list):
            return []
        return [item for item in artifacts if isinstance(item, dict)]

    def _resolve_repo_path(self, raw_path: Any) -> Path | None:
        text = str(raw_path or "").strip()
        if not text:
            return None
        try:
            candidate = Path(text)
            if not candidate.is_absolute():
                candidate = self.repo_root / candidate
            resolved = candidate.resolve()
            resolved.relative_to(self.repo_root)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        return resolved

    def _existing_repo_artifact_path(self, raw_path: Any) -> Path | None:
        path = self._resolve_repo_path(raw_path)
        if path is None or not path.exists():
            return None
        return path

    @staticmethod
    def _sanitize_generated_config_block(payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        sanitized = copy.deepcopy(payload)
        sanitized.pop("__config_file_path", None)
        return sanitized

    def _has_renderable_wfa(self, run_id: str) -> bool:
        if self._has_artifact_type(run_id, "wfa_parquet"):
            return True
        payload_path = self.registry.build_run_paths(run_id)["chart_payload_dir"] / "wfa_dashboard_payload.json"
        return payload_path.exists()

    def _has_heatmap_axes(self, run_id: str) -> bool:
        snapshot_dir = self.registry.build_run_paths(run_id)["snapshot_dir"]
        execution_plan_path = snapshot_dir / "execution_plan.json"
        if execution_plan_path.exists():
            try:
                payload = json.loads(execution_plan_path.read_text(encoding="utf-8"))
                axes = [
                    axis.get("name")
                    for axis in payload.get("param_axes", [])
                    if isinstance(axis, dict) and axis.get("name")
                ]
                if len(axes) >= 2:
                    return True
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass

        index_path = snapshot_dir / "backtest_result_index.json"
        if not index_path.exists():
            return False
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return False
        rows = payload.get("backtests", []) if isinstance(payload, dict) else []
        key_counts: Dict[str, int] = {}
        for row in rows:
            combo = row.get("semantic_combo", {}) if isinstance(row, dict) else {}
            if not isinstance(combo, dict):
                continue
            for key, value in combo.items():
                if value is None:
                    continue
                key_counts[key] = key_counts.get(key, 0) + 1
        return len(key_counts) >= 2

    def _parameter_review_templates_path(self) -> Path:
        return self.repo_root / "workspace" / "wfa" / "parameter-review-templates.json"

    def _load_parameter_review_template_store(self) -> Dict[str, Any]:
        path = self._parameter_review_templates_path()
        payload = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else {"schema_version": "1.1", "default_template_name": "", "templates": []}
        )
        if not isinstance(payload, dict):
            payload = {"schema_version": "1.1", "default_template_name": "", "templates": []}
        payload.setdefault("schema_version", "1.1")
        payload.setdefault("default_template_name", "")
        payload.setdefault("templates", [])
        return payload

    def _write_parameter_review_template_store(self, payload: Dict[str, Any]) -> None:
        path = self._parameter_review_templates_path()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _default_parameter_review_overrides(self) -> Dict[str, Dict[str, Any]]:
        store = self._load_parameter_review_template_store()
        default_name = str(store.get("default_template_name", "") or "").strip().lower()
        templates = store.get("templates", []) if isinstance(store, dict) else []
        default_template = next(
            (
                item for item in templates
                if str(item.get("name", "")).strip().lower() == default_name
            ),
            None,
        )
        if not isinstance(default_template, dict):
            return {"acceptance": {}, "ranking": {}}
        acceptance = default_template.get("acceptance", {})
        ranking = default_template.get("ranking", {})
        return {
            "acceptance": acceptance if isinstance(acceptance, dict) else {},
            "ranking": ranking if isinstance(ranking, dict) else {},
        }
