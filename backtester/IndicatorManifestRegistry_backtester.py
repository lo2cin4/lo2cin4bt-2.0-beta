"""Indicator manifest registry loader for core and workspace extensions."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


class IndicatorManifestRegistry:
    """Load indicator manifests and expose alias, family, and backend metadata."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        core_manifest_dir: Path | None = None,
        legacy_core_manifest_dir: Path | None = None,
        extension_manifest_dirs: Iterable[Path] | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger("IndicatorManifestRegistry")
        self.repo_root = Path(__file__).resolve().parents[1]
        self.core_manifest_dir = core_manifest_dir or (
            self.repo_root
            / "backtester"
            / "contracts"
            / "indicator-manifest"
            / "manifests"
            / "core"
        )
        self.legacy_core_manifest_dir = legacy_core_manifest_dir or (
            self.repo_root / "docs" / "contracts" / "manifests" / "core"
        )
        if extension_manifest_dirs is None:
            extension_manifest_dirs = [
                self.repo_root / "workspace" / "indicators" / "extensions"
            ]
        self.extension_manifest_dirs = [Path(path) for path in extension_manifest_dirs]

    def load(self) -> Dict[str, Any]:
        manifests = self._load_manifest_records()
        alias_map: Dict[str, Tuple[str, int]] = {}
        family_modules: Dict[str, str] = {}
        family_backend_specs: Dict[str, Dict[str, Any]] = {}
        family_indicator_ids: Dict[str, str] = {}
        manifest_index: Dict[str, Dict[str, Any]] = {}
        manifests_loaded = 0
        extension_manifests_loaded = 0

        for manifest, path, source in manifests:
            family = str(manifest.get("family_code", "")).upper().strip()
            indicator_id = str(manifest.get("indicator_id", "")).strip()
            if not self._is_manifest_supported(manifest, indicator_id, family, path):
                continue

            if indicator_id in manifest_index:
                self.logger.warning(
                    "duplicate indicator manifest id %s at %s (keep first)",
                    indicator_id,
                    path,
                )
                continue
            if family in family_indicator_ids:
                self.logger.warning(
                    "duplicate indicator family_code %s at %s (already owned by %s, keep first)",
                    family,
                    path,
                    family_indicator_ids[family],
                )
                continue

            backend_spec = self._extract_backend_spec(manifest, path)
            manifest_record = dict(manifest)
            manifest_record["manifest_path"] = str(path)
            manifest_record["manifest_source"] = source
            manifest_record["extension_root"] = (
                str(path.parent) if manifest.get("kind") == "extension" else ""
            )
            manifest_index[indicator_id] = manifest_record
            family_indicator_ids[family] = indicator_id
            manifests_loaded += 1
            if manifest.get("kind") == "extension":
                extension_manifests_loaded += 1

            if backend_spec:
                family_backend_specs[family] = backend_spec
                module_name = self._extract_module_name_from_backend(backend_spec)
                if module_name:
                    family_modules[family] = module_name

            for raw_alias in manifest.get("aliases", []) or []:
                alias = str(raw_alias).upper().strip()
                if not alias:
                    continue
                idx = self._extract_alias_index(alias, family)
                if idx is None:
                    continue
                if alias in alias_map:
                    self.logger.warning(
                        "indicator alias conflict in manifests: %s (keep first)", alias
                    )
                    continue
                alias_map[alias] = (family, idx)

        return {
            "alias_map": alias_map,
            "family_modules": family_modules,
            "family_backend_specs": family_backend_specs,
            "manifest_index": manifest_index,
            "manifests_loaded": manifests_loaded,
            "extension_manifests_loaded": extension_manifests_loaded,
        }

    def _load_manifest_records(self) -> List[Tuple[Dict[str, Any], Path, str]]:
        records: List[Tuple[Dict[str, Any], Path, str]] = []
        core_manifest_dir = self.core_manifest_dir
        if not core_manifest_dir.exists():
            core_manifest_dir = self.legacy_core_manifest_dir
        if core_manifest_dir.exists():
            records.extend(
                self._load_manifest_dir(core_manifest_dir, pattern="*.json", source="core")
            )

        for extension_root in self.extension_manifest_dirs:
            if not extension_root.exists():
                continue
            records.extend(
                self._load_manifest_dir(
                    extension_root, pattern="**/manifest.json", source="workspace_extension"
                )
            )

        return records

    def _load_manifest_dir(
        self, manifest_dir: Path, pattern: str, source: str
    ) -> List[Tuple[Dict[str, Any], Path, str]]:
        manifests: List[Tuple[Dict[str, Any], Path, str]] = []
        for path in sorted(manifest_dir.glob(pattern)):
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.warning("failed to parse indicator manifest %s: %s", path, exc)
                continue
            if not isinstance(data, dict):
                self.logger.warning("indicator manifest is not an object: %s", path)
                continue
            manifests.append((data, path, source))
        return manifests

    def _is_manifest_supported(
        self, manifest: Dict[str, Any], indicator_id: str, family: str, path: Path
    ) -> bool:
        if str(manifest.get("schema_version", "")).strip() != "1.0":
            self.logger.warning("unsupported indicator manifest schema_version at %s", path)
            return False
        if not indicator_id:
            self.logger.warning("indicator manifest missing indicator_id: %s", path)
            return False
        if not family:
            self.logger.warning("indicator manifest missing family_code: %s", path)
            return False
        implementation = manifest.get("implementation", {})
        if not isinstance(implementation, dict):
            self.logger.warning("indicator manifest missing implementation: %s", path)
            return False
        if not self._extract_backend_spec(manifest, path):
            self.logger.warning("indicator manifest has no usable backend: %s", path)
            return False
        return True

    @staticmethod
    def _extract_backend_spec(manifest: Dict[str, Any], manifest_path: Path) -> Dict[str, Any]:
        implementation = manifest.get("implementation", {})
        if not isinstance(implementation, dict):
            return {}
        default_backend = implementation.get("default_backend")
        backends = implementation.get("backends", [])
        if not isinstance(backends, list):
            return {}

        backend = None
        for item in backends:
            if not isinstance(item, dict):
                continue
            if item.get("backend_id") == default_backend:
                backend = item
                break
        if backend is None and backends:
            backend = backends[0] if isinstance(backends[0], dict) else None
        if not backend:
            return {}

        artifact_path = str(backend.get("artifact_path", "")).strip()
        artifact_full_path = ""
        if artifact_path:
            artifact_full_path = str((manifest_path.parent / artifact_path).resolve())

        return {
            "backend_id": str(backend.get("backend_id", "")).strip(),
            "language": str(backend.get("language", "")).strip(),
            "entrypoint": str(backend.get("entrypoint", "")).strip(),
            "artifact_path": artifact_path,
            "artifact_full_path": artifact_full_path,
            "manifest_path": str(manifest_path),
            "indicator_id": str(manifest.get("indicator_id", "")).strip(),
            "family_code": str(manifest.get("family_code", "")).upper().strip(),
            "kind": str(manifest.get("kind", "")).strip(),
        }

    @staticmethod
    def _extract_module_name_from_backend(backend_spec: Dict[str, Any]) -> str:
        entrypoint = str(backend_spec.get("entrypoint", ""))
        if not entrypoint:
            return ""
        module_path = entrypoint.split(":", 1)[0]
        return module_path.split(".")[-1]

    @staticmethod
    def _extract_alias_index(alias: str, family: str) -> int | None:
        match = re.match(rf"^{re.escape(family)}(\d+)$", alias)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None
