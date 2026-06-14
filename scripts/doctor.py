from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PYTHON_MODULES = {
    "fastapi": "fastapi",
    "numpy": "numpy",
    "pandas": "pandas",
    "plotly": "plotly",
    "pyarrow": "pyarrow",
    "rich": "rich",
    "scikit-learn": "sklearn",
    "uvicorn": "uvicorn",
    "yfinance": "yfinance",
}


def _ok(message: str) -> None:
    print(f"[OK] {message}")


def _warn(message: str) -> None:
    print(f"[WARN] {message}")


def _fail(message: str) -> None:
    print(f"[FAIL] {message}")


def _command_version(command: str) -> str | None:
    executable = shutil.which(command)
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return executable
    return (result.stdout or result.stderr or executable).strip().splitlines()[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check local Lo2cin4BT setup.")
    parser.add_argument("--skip-node", action="store_true", help="Skip Node/npm checks.")
    args = parser.parse_args()

    failures = 0

    if sys.version_info >= (3, 12):
        _ok(f"Python {sys.version.split()[0]}")
    else:
        _fail("Python 3.12+ is required")
        failures += 1

    for label, module in PYTHON_MODULES.items():
        if importlib.util.find_spec(module) is None:
            _fail(f"Missing Python package: {label}")
            failures += 1
        else:
            _ok(f"Python package available: {label}")

    if not args.skip_node:
        node_version = _command_version("node")
        npm_version = _command_version("npm")
        if node_version:
            _ok(f"Node available: {node_version}")
        else:
            _fail("Node.js is required for rebuilding the React frontend")
            failures += 1
        if npm_version:
            _ok(f"npm available: {npm_version}")
        else:
            _fail("npm is required for plotter/web")
            failures += 1

    frontend_root = ROOT / "plotter" / "web"

    if (frontend_root / "package-lock.json").exists():
        _ok("Frontend lockfile exists")
    else:
        _fail("Missing plotter/web/package-lock.json")
        failures += 1

    for workspace_folder in [ROOT / "workspace" / "runs", ROOT / "workspace" / "wfa"]:
        if workspace_folder.exists():
            _ok(f"Workspace config folder exists: {workspace_folder.relative_to(ROOT)}")
        else:
            workspace_folder.mkdir(parents=True, exist_ok=True)
            _ok(f"Created workspace config folder: {workspace_folder.relative_to(ROOT)}")

    if (frontend_root / "dist").exists():
        _ok("Frontend dist exists for python main.py serving")
    else:
        _warn("Frontend dist is absent; run npm run build inside plotter/web before python main.py")

    if failures:
        print(f"\nDoctor finished with {failures} failure(s).")
        return 1
    print("\nDoctor finished successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
