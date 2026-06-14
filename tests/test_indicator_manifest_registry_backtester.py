import importlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from jsonschema import Draft202012Validator


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytestmark = pytest.mark.regression


def _expected_core_aliases() -> set[str]:
    aliases = set()
    aliases.update({f"MA{i}" for i in range(1, 13)})
    aliases.update({f"BOLL{i}" for i in range(1, 5)})
    aliases.update({f"HL{i}" for i in range(1, 5)})
    aliases.update({f"PERC{i}" for i in range(1, 7)})
    aliases.update({f"VALUE{i}" for i in range(1, 7)})
    aliases.update({"NDAY1", "NDAY2"})
    return aliases


def test_all_indicator_manifests_validate_and_core_manifests_have_test_evidence():
    contract_root = _REPO_ROOT / "backtester" / "contracts" / "indicator-manifest"
    schema = json.loads((contract_root / "indicator-manifest-v1.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    manifest_paths = sorted((contract_root / "manifests" / "core").glob("*.json"))
    manifest_paths += sorted((contract_root / "examples").glob("indicator-manifest-*.json"))
    workspace_manifest = _REPO_ROOT / "workspace" / "indicators" / "extensions" / "dual_threshold" / "manifest.json"
    if workspace_manifest.exists():
        manifest_paths.append(workspace_manifest)

    assert manifest_paths, "indicator manifest gate must cover at least one manifest"
    seen_ids: set[str] = set()
    for path in manifest_paths:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        errors = sorted(validator.iter_errors(manifest), key=lambda error: list(error.path))
        assert not errors, f"{path} failed schema validation: {errors}"
        indicator_id = manifest["indicator_id"]
        assert indicator_id not in seen_ids, f"duplicate indicator_id: {indicator_id}"
        seen_ids.add(indicator_id)

        if manifest["kind"] == "core":
            tests = manifest.get("tests") or {}
            assert tests.get("unit_test_path"), f"{path} core manifest must name a unit test path"
            unit_path = _REPO_ROOT / tests["unit_test_path"]
            assert unit_path.exists(), f"{path} unit_test_path does not exist: {unit_path}"


def test_manifest_registry_loads_core_and_workspace_extension_aliases():
    registry_mod = importlib.import_module(
        "backtester.IndicatorManifestRegistry_backtester"
    )
    registry = registry_mod.IndicatorManifestRegistry()
    data = registry.load()

    alias_map = data["alias_map"]
    expected_aliases = _expected_core_aliases() | {"DUAL1"}
    assert set(alias_map.keys()) == expected_aliases
    assert alias_map["MA1"] == ("MA", 1)
    assert alias_map["NDAY2"] == ("NDAY", 2)
    assert alias_map["DUAL1"] == ("DUAL", 1)

    manifest_index = data["manifest_index"]
    extension_manifest = manifest_index["extension.dual-threshold.confirm"]
    assert extension_manifest["kind"] == "extension"
    assert extension_manifest["manifest_source"] == "workspace_extension"

    backend_spec = data["family_backend_specs"]["DUAL"]
    assert backend_spec["language"] == "python"
    assert backend_spec["artifact_full_path"].endswith(
        "workspace\\indicators\\extensions\\dual_threshold\\indicator.py"
    )
    assert data["extension_manifests_loaded"] >= 1


def test_manifest_registry_skips_duplicate_extension_family_code(tmp_path):
    registry_mod = importlib.import_module(
        "backtester.IndicatorManifestRegistry_backtester"
    )
    core_registry = registry_mod.IndicatorManifestRegistry()

    ext_root = tmp_path / "extensions"
    ext_package = ext_root / "duplicate_ma"
    ext_package.mkdir(parents=True)
    (ext_package / "indicator.py").write_text(
        "class DuplicateMAIndicator:\n    pass\n", encoding="utf-8"
    )
    (ext_package / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "indicator_id": "extension.duplicate.ma",
                "family_code": "MA",
                "name": "Duplicate MA",
                "version": "1.0.0",
                "api_version": "indicator-api-1",
                "kind": "extension",
                "entry_supported": True,
                "exit_supported": True,
                "stateful_execution_required": False,
                "signal_contract": {
                    "value_type": "int",
                    "allowed_values": [-1, 0, 1],
                    "semantics": {
                        "long_open": 1,
                        "short_open": -1,
                        "long_close": -1,
                        "short_close": 1,
                    },
                },
                "params_schema": [
                    {"name": "period", "type": "int", "required": True}
                ],
                "implementation": {
                    "default_backend": "py-user",
                    "backends": [
                        {
                            "backend_id": "py-user",
                            "language": "python",
                            "entrypoint": "indicator:DuplicateMAIndicator",
                            "artifact_path": "indicator.py",
                        }
                    ],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    registry = registry_mod.IndicatorManifestRegistry(
        core_manifest_dir=core_registry.core_manifest_dir,
        legacy_core_manifest_dir=core_registry.legacy_core_manifest_dir,
        extension_manifest_dirs=[ext_root],
    )
    data = registry.load()

    assert "extension.duplicate.ma" not in data["manifest_index"]
    assert "MA" in data["family_backend_specs"]
    assert data["family_backend_specs"]["MA"]["kind"] == "core"


def test_indicators_backtester_uses_manifest_registry_for_aliases_and_extension():
    indicators_mod = importlib.import_module("backtester.Indicators_backtester")
    helper = indicators_mod.IndicatorsBacktester()

    aliases = set(helper.get_all_indicator_aliases())
    assert aliases == (_expected_core_aliases() | {"DUAL1"})
    assert helper.new_indicators["MA"] == "MovingAverage_Indicator_backtester"
    assert helper.family_backend_specs["DUAL"]["kind"] == "extension"


def test_extension_indicator_can_generate_multi_column_signals():
    indicators_mod = importlib.import_module("backtester.Indicators_backtester")
    helper = indicators_mod.IndicatorsBacktester()

    params = helper.get_indicator_params(
        "DUAL1",
        {
            "primary_column": "vix_close",
            "confirm_column": "spy_close",
            "primary_threshold": 10,
            "confirm_threshold": 5,
            "primary_op": "gt",
            "confirm_op": "gt",
        },
    )[0]

    data = pd.DataFrame(
        {
            "vix_close": [9.0, 11.0, 12.0, 8.0, 12.0],
            "spy_close": [4.0, 6.0, 7.0, 7.0, 7.0],
        }
    )
    signals = helper.calculate_signals("DUAL", data, params)

    assert signals.tolist() == [0.0, 1.0, 0.0, 0.0, 1.0]
