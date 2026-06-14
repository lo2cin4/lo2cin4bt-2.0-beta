from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.path_resolver import (
    build_app_run_paths,
    ensure_app_outputs_structure,
)


class AppRegistry:
    """Filesystem-backed registry for app-managed runs."""

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root).resolve()
        self.app_paths = ensure_app_outputs_structure(self.repo_root)

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8-sig"))

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def build_run_paths(self, run_id: str) -> Dict[str, Path]:
        return build_app_run_paths(self.repo_root, run_id)

    def write_registry_entry(self, payload: Dict[str, Any]) -> Path:
        run_id = str(payload["run_id"])
        paths = self.build_run_paths(run_id)
        self._write_json(paths["run_registry"], payload)
        self._update_latest_runs(payload, paths["run_registry"])
        return paths["run_registry"]

    def write_stage_status(self, run_id: str, payload: Dict[str, Any]) -> Path:
        path = self.build_run_paths(run_id)["stage_status"]
        self._write_json(path, payload)
        return path

    def write_artifact_manifest(self, run_id: str, payload: Dict[str, Any]) -> Path:
        path = self.build_run_paths(run_id)["artifact_manifest"]
        self._write_json(path, payload)
        return path

    def write_snapshot_file(self, run_id: str, name: str, payload: Any) -> Path:
        path = self.build_run_paths(run_id)["snapshot_dir"] / name
        self._write_json(path, payload)
        return path

    def list_runs(
        self,
        *,
        module: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        latest_runs = self._read_json(self.app_paths["latest_runs"], [])
        rows: List[Dict[str, Any]] = []
        for item in latest_runs:
            if not isinstance(item, dict):
                continue
            if module and str(item.get("module", "")) != module:
                continue
            if status and str(item.get("status", "")) != status:
                continue
            rows.append(item)
        return rows

    def list_run_history(
        self,
        *,
        module: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        registry_dir = self.app_paths["run_registry"]
        for path in registry_dir.glob("*.json"):
            item = self._read_json(path, {})
            if not isinstance(item, dict):
                continue
            if module and str(item.get("module", "")) != module:
                continue
            if status and str(item.get("status", "")) != status:
                continue
            item.setdefault("registry_path", str(path))
            rows.append(item)
        rows.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return rows

    def load_registry_entry(self, run_id: str) -> Dict[str, Any]:
        path = self.build_run_paths(run_id)["run_registry"]
        return self._read_json(path, {})

    def load_stage_status(self, run_id: str) -> Dict[str, Any]:
        path = self.build_run_paths(run_id)["stage_status"]
        return self._read_json(path, {})

    def load_artifact_manifest(self, run_id: str) -> Dict[str, Any]:
        path = self.build_run_paths(run_id)["artifact_manifest"]
        return self._read_json(path, {})

    def _update_latest_runs(self, registry_payload: Dict[str, Any], registry_path: Path) -> None:
        latest_runs = self._read_json(self.app_paths["latest_runs"], [])
        if not isinstance(latest_runs, list):
            latest_runs = []

        summary = {
            "run_id": registry_payload.get("run_id"),
            "module": registry_payload.get("module"),
            "entrypoint": registry_payload.get("entrypoint"),
            "status": registry_payload.get("status"),
            "created_at": registry_payload.get("created_at"),
            "completed_at": registry_payload.get("completed_at"),
            "config_filename": registry_payload.get("config_filename"),
            "symbol": registry_payload.get("symbol"),
            "frequency": registry_payload.get("frequency"),
            "strategy_mode": registry_payload.get("strategy_mode"),
            "semantic_label": registry_payload.get("semantic_label"),
            "display_label": registry_payload.get("display_label"),
            "run_type": registry_payload.get("run_type"),
            "data_lineage_manifest_path": registry_payload.get("data_lineage_manifest_path"),
            "lineage_status": registry_payload.get("lineage_status"),
            "warning_count": registry_payload.get("warning_count", 0),
            "error_count": registry_payload.get("error_count", 0),
            "registry_path": str(registry_path),
        }
        latest_runs = [
            item for item in latest_runs if item.get("run_id") != summary["run_id"]
        ]
        latest_runs.insert(0, summary)
        latest_runs.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        self._write_json(self.app_paths["latest_runs"], latest_runs[:100])
