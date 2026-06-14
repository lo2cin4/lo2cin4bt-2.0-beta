import builtins
import importlib
import sys
from pathlib import Path


def test_main_import_does_not_pull_heavy_legacy_modules(monkeypatch) -> None:
    project_root = Path(__file__).resolve().parents[1]
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)

    sys.modules.pop("main", None)

    real_import = builtins.__import__
    blocked_roots = {
        "numpy",
        "pandas",
        "backtester",
        "metricstracker",
        "statanalyser",
    }

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        root_name = name.split(".", 1)[0]
        if root_name in blocked_roots:
            raise AssertionError(f"main import must not require {root_name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("main")

    assert hasattr(module, "main")


def test_main_launcher_uses_browser_first_app_entry() -> None:
    main_path = Path(__file__).resolve().parents[1] / "main.py"
    source = main_path.read_text(encoding="utf-8")

    assert "BaseDataLoader" not in source
    assert "BaseBacktester" not in source
    assert "BaseMetricTracker" not in source
    assert "BaseStatAnalyser" not in source
    assert "browser-first React + FastAPI workspace" in source
    assert "app.api" in source
    assert "uvicorn.run" in source
    assert "--port" in source
    assert "--no-browser" in source


def test_main_launcher_cli_defaults_and_overrides(monkeypatch) -> None:
    project_root = Path(__file__).resolve().parents[1]
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)

    sys.modules.pop("main", None)
    module = importlib.import_module("main")

    monkeypatch.delenv("LO2CIN4BT_HOST", raising=False)
    monkeypatch.delenv("LO2CIN4BT_PORT", raising=False)
    defaults = module.parse_args([])
    assert defaults.host == "127.0.0.1"
    assert defaults.port == 2424
    assert defaults.no_browser is False

    override = module.parse_args(["--host", "127.0.0.1", "--port", "2425", "--no-browser"])
    assert override.host == "127.0.0.1"
    assert override.port == 2425
    assert override.no_browser is True


def test_main_launcher_can_disable_auto_browser(monkeypatch) -> None:
    project_root = Path(__file__).resolve().parents[1]
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)

    sys.modules.pop("main", None)
    module = importlib.import_module("main")

    monkeypatch.delenv("LO2CIN4BT_AUTO_OPEN_BROWSER", raising=False)
    assert module._auto_open_browser_enabled(module.parse_args([]))
    assert not module._auto_open_browser_enabled(module.parse_args(["--no-browser"]))
    assert not module._auto_open_browser_enabled(module.parse_args(["--no-open-browser"]))

    monkeypatch.setenv("LO2CIN4BT_AUTO_OPEN_BROWSER", "0")
    assert not module._auto_open_browser_enabled(module.parse_args([]))

    monkeypatch.setenv("LO2CIN4BT_AUTO_OPEN_BROWSER", "false")
    assert not module._auto_open_browser_enabled(module.parse_args([]))
