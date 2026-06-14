from __future__ import annotations

import argparse
import json
import py_compile
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "backtester/contracts/indicator-manifest/indicator-manifest-v1.schema.json"


def _manifest_paths(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    if not target.exists():
        return []
    return sorted(target.rglob("manifest.json"))


def _check_manifest(path: Path, validator: Draft202012Validator) -> list[str]:
    errors: list[str] = []
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{path}: cannot read manifest ({exc})"]

    schema_errors = sorted(validator.iter_errors(manifest), key=lambda error: list(error.path))
    errors.extend(f"{path}: schema error: {error.message}" for error in schema_errors)

    implementation = manifest.get("implementation", {}) if isinstance(manifest, dict) else {}
    for backend in implementation.get("backends", []) if isinstance(implementation, dict) else []:
        artifact = backend.get("artifact_path") if isinstance(backend, dict) else None
        language = backend.get("language") if isinstance(backend, dict) else None
        if not artifact:
            continue
        artifact_path = (path.parent / str(artifact)).resolve()
        if not artifact_path.exists():
            errors.append(f"{path}: missing artifact_path {artifact}")
        elif language == "python":
            try:
                py_compile.compile(str(artifact_path), doraise=True)
            except py_compile.PyCompileError as exc:
                errors.append(f"{path}: python compile failed for {artifact}: {exc.msg}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?", default="workspace/indicators/extensions")
    args = parser.parse_args()

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    target = (ROOT / args.target).resolve()
    manifests = _manifest_paths(target)
    errors: list[str] = []
    seen: dict[str, Path] = {}

    for path in manifests:
        errors.extend(_check_manifest(path, validator))
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        indicator_id = str(manifest.get("indicator_id", "")).strip()
        if indicator_id:
            if indicator_id in seen:
                errors.append(f"duplicate indicator_id {indicator_id}: {seen[indicator_id]} and {path}")
            seen[indicator_id] = path

    if errors:
        print("Indicator doctor failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Indicator doctor passed. Manifests checked: {len(manifests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
