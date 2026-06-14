from __future__ import annotations

import builtins
import os
import sys
from pathlib import Path

import pytest
from rich.console import Console


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
sys.modules.pop("autorunner", None)

pytestmark = pytest.mark.regression


def _all_input(*_args, **_kwargs) -> str:
    return "all"


def _unexpected_input(*_args, **_kwargs) -> None:
    raise AssertionError("unexpected interactive input")


def test_config_selector_workspace_only(monkeypatch, tmp_path) -> None:
    os.environ.setdefault("NUMBA_DISABLE_JIT", "1")
    os.environ.setdefault("LO2CIN4BT_DISABLE_MULTIPROCESS", "1")
    monkeypatch.setattr(builtins, "input", _all_input)
    monkeypatch.setattr(Console, "input", _unexpected_input)

    workspace_runs = tmp_path / "workspace" / "runs"
    templates = tmp_path / "templates"

    workspace_runs.mkdir(parents=True, exist_ok=True)
    templates.mkdir(parents=True, exist_ok=True)

    (workspace_runs / "same.json").write_text("{}", encoding="utf-8")
    (workspace_runs / "config_template.json").write_text("{}", encoding="utf-8")

    from autorunner.ConfigSelector_autorunner import ConfigSelector

    selector = ConfigSelector(
        workspace_runs,
        templates,
    )
    selected = selector.discover_configs()

    assert selected == [
        str((workspace_runs / "same.json").resolve()),
    ]


def test_base_autorunner_selects_workspace_configs_without_prompt(monkeypatch, tmp_path) -> None:
    os.environ.setdefault("NUMBA_DISABLE_JIT", "1")
    os.environ.setdefault("LO2CIN4BT_DISABLE_MULTIPROCESS", "1")
    monkeypatch.setattr(builtins, "input", _all_input)
    monkeypatch.setattr(Console, "input", _unexpected_input)

    from autorunner.Base_autorunner import BaseAutorunner
    from autorunner.ConfigSelector_autorunner import ConfigSelector

    workspace_runs = tmp_path / "workspace" / "runs"
    templates = tmp_path / "templates"

    workspace_runs.mkdir(parents=True, exist_ok=True)
    templates.mkdir(parents=True, exist_ok=True)

    (workspace_runs / "a.json").write_text("{}", encoding="utf-8")
    (templates / "config_template.json").write_text("{}", encoding="utf-8")

    autorunner = BaseAutorunner()
    autorunner.configs_dir = workspace_runs
    autorunner.templates_dir = templates
    autorunner.config_selector = ConfigSelector(
        workspace_runs,
        templates,
    )

    selected = autorunner._select_configs()  # pylint: disable=protected-access

    assert selected == [
        str((workspace_runs / "a.json").resolve()),
    ]
