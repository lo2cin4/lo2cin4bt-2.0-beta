"""Config discovery and interactive selection for autorunner."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List

from rich.table import Table

from utils import show_error, show_info, show_warning
from utils.path_resolver import discover_configs


class ConfigSelector:
    """Select runnable autorunner config files with workspace-first precedence."""

    def __init__(
        self,
        configs_dir_path: Path,
        templates_dir_path: Path,
    ):
        self.configs_dir = Path(configs_dir_path)
        self.templates_dir = Path(templates_dir_path)

    def discover_configs(self) -> List[str]:
        """Backward-compatible alias."""
        return self.select_configs()

    def select_configs(self) -> List[str]:
        config_files = self._scan_config_files()
        if not config_files:
            show_warning("AUTORUNNER", f"No config files found in {self.configs_dir}")
            return []

        self._display_config_list(config_files)
        return self._get_user_selection(config_files)

    def _scan_config_files(self) -> List[str]:
        self.configs_dir.mkdir(parents=True, exist_ok=True)

        discovered = discover_configs(
            primary_dir=self.configs_dir,
        )
        if discovered:
            return [str(path) for path in discovered]

        self._copy_template_config()
        fallback = self.configs_dir / "config_template.json"
        if fallback.exists():
            show_warning(
                "AUTORUNNER",
                f"No runnable config found. Created template: {fallback}",
            )
            return [str(fallback)]

        return []

    def _copy_template_config(self) -> None:
        template_path = self.templates_dir / "config_template.json"
        if template_path.exists():
            shutil.copy2(template_path, self.configs_dir / "config_template.json")
            return
        raise FileNotFoundError(f"Template config missing: {template_path}")

    def _display_config_list(self, config_files: List[str]) -> None:
        table = Table(title="Available Autorunner Configs")
        table.add_column("#", style="cyan", no_wrap=True)
        table.add_column("File", style="magenta")
        table.add_column("Path", style="green")

        for i, file_path in enumerate(config_files, 1):
            table.add_row(str(i), Path(file_path).name, file_path)

        from autorunner.utils import get_console

        get_console().print(table)

    def _get_user_selection(self, config_files: List[str]) -> List[str]:
        show_info(
            "AUTORUNNER",
            "Select config(s): single index, comma list, 'all', or 'q'.",
        )

        while True:
            user_input = input().strip().lower()

            if user_input == "q":
                return []
            if user_input == "all":
                return config_files

            try:
                selected = self._parse_user_input(user_input, config_files)
                if selected:
                    return selected
            except ValueError:
                show_error("AUTORUNNER", "Invalid input. Try again.")

    def _parse_user_input(self, user_input: str, config_files: List[str]) -> List[str]:
        indices = []
        for part in user_input.split(","):
            part = part.strip()
            if not part.isdigit():
                raise ValueError(f"Invalid input: {part}")
            indices.append(int(part))

        selected = []
        for idx in indices:
            if 1 <= idx <= len(config_files):
                selected.append(config_files[idx - 1])
            else:
                raise ValueError(f"Index {idx} out of range")
        return selected

    def get_config_info(self, config_file: str) -> Dict[str, object]:
        """Read lightweight metadata about a config file."""

        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config_data = json.load(f)

            return {
                "file_name": Path(config_file).name,
                "file_path": config_file,
                "dataloader_source": config_data.get("dataloader", {}).get(
                    "source", "unknown"
                ),
                "backtester_pairs": len(
                    config_data.get("backtester", {}).get("condition_pairs", [])
                ),
            }
        except Exception as exc:  # pragma: no cover
            show_error("AUTORUNNER", f"Failed to read config {config_file}: {exc}")
            return {
                "file_name": Path(config_file).name,
                "file_path": config_file,
                "error": str(exc),
            }
