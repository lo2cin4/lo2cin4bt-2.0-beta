from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = {
    "app",
    "autorunner",
    "backtester",
    "dataloader",
    "factorhandler",
    "metricstracker",
    "plotter",
    "statanalyser",
    "utils",
    "verification",
    "wfanalyser",
}
ENGINE_ROOTS = {"autorunner", "backtester", "dataloader", "factorhandler", "metricstracker", "statanalyser", "wfanalyser"}


def _iter_python_files(root: Path) -> Iterable[Path]:
    for top in sorted(PRODUCTION_ROOTS):
        base = root / top
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if any(part in {".venv", "__pycache__", "node_modules", "target"} for part in path.parts):
                continue
            relative_parts = path.relative_to(root).parts
            if len(relative_parts) >= 2 and relative_parts[:2] == ("verification", "scripts"):
                continue
            yield path


def _module_root(module: str) -> str:
    return module.split(".", 1)[0]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(_module_root(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(_module_root(node.module))
    return roots


def analyze_repo(root: Path = ROOT) -> dict[str, object]:
    violations: list[dict[str, str]] = []
    parse_errors: list[dict[str, str]] = []

    for path in _iter_python_files(root):
        rel = path.relative_to(root).as_posix()
        owner = path.relative_to(root).parts[0]
        try:
            imported_roots = _imports(path)
        except SyntaxError as exc:
            parse_errors.append({"file": rel, "error": str(exc)})
            continue

        if owner in ENGINE_ROOTS and "app" in imported_roots:
            violations.append(
                {
                    "rule": "engine_must_not_import_app",
                    "file": rel,
                    "detail": "Engine modules must not depend on UI/API modules.",
                }
            )
        if owner == "utils" and imported_roots.intersection(ENGINE_ROOTS | {"app"}):
            violations.append(
                {
                    "rule": "utils_must_not_import_domain",
                    "file": rel,
                    "detail": "Shared utils must stay below domain modules.",
                }
            )
        text = path.read_text(encoding="utf-8-sig")
        if "\ufeff" in text[:1]:
            violations.append({"rule": "no_utf8_bom", "file": rel, "detail": "Python files must not use BOM."})
        if "sys.path.append" in text or "sys.path.insert" in text:
            violations.append(
                {
                    "rule": "no_production_sys_path_mutation",
                    "file": rel,
                    "detail": "Production code must not mutate sys.path.",
                }
            )

    return {"parse_errors": parse_errors, "violations": violations}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()
    result = analyze_repo(ROOT)
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if not result["parse_errors"] and not result["violations"]:
            print("Architecture audit passed.")
        for item in result["parse_errors"]:
            print(f"PARSE {item['file']}: {item['error']}")
        for item in result["violations"]:
            print(f"{item['rule']} {item['file']}: {item['detail']}")
    return 0 if not result["parse_errors"] and not result["violations"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
