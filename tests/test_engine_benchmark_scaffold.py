import json
import sys
from pathlib import Path
import importlib


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def test_benchmark_specs_are_loadable():
    specs_dir = _REPO_ROOT / "verification" / "benchmarks" / "fixtures"
    specs = sorted(specs_dir.glob("*.json"))
    assert specs
    for spec in specs:
        payload = json.loads(spec.read_text(encoding="utf-8"))
        assert "benchmark_id" in payload
        assert "modes" in payload
        assert "repeat" in payload


def _assert_benchmark_config_inputs_exist(spec_name: str, config: dict):
    dataloader_config = config.get("dataloader", {})
    file_path = dataloader_config.get("file_config", {}).get("file_path")
    if file_path:
        assert (_REPO_ROOT / file_path).exists(), f"{spec_name} missing {file_path}"

    backtester_config = config.get("backtester", {})
    for key in ["strategy_contract_path", "feature_contract_path"]:
        raw_path = backtester_config.get(key)
        if raw_path:
            assert (_REPO_ROOT / raw_path).exists(), f"{spec_name} missing {raw_path}"


def test_benchmark_specs_reference_existing_repo_inputs():
    specs_dir = _REPO_ROOT / "verification" / "benchmarks" / "fixtures"
    specs = sorted(specs_dir.glob("*.json"))
    assert specs
    for spec_path in specs:
        payload = json.loads(spec_path.read_text(encoding="utf-8"))
        inline_config = payload.get("inline_config")
        if inline_config:
            _assert_benchmark_config_inputs_exist(spec_path.name, inline_config)

        config_path = payload.get("config_path")
        if config_path:
            full_config_path = _REPO_ROOT / config_path
            assert full_config_path.exists(), f"{spec_path.name} missing {config_path}"
            config_payload = json.loads(full_config_path.read_text(encoding="utf-8"))
            _assert_benchmark_config_inputs_exist(spec_path.name, config_payload)


def test_benchmark_script_writes_under_verification_results():
    from verification.scripts import run_engine_benchmark as mod

    output_root = _REPO_ROOT / "verification" / "results" / "benchmarks"
    assert mod.PROJECT_ROOT == _REPO_ROOT
    assert output_root.parent.name == "results"


def test_speed_gate_threshold_profiles_are_defined():
    thresholds_path = (
        _REPO_ROOT
        / "verification"
        / "benchmarks"
        / "thresholds"
        / "node_ir_speed_gate_thresholds.json"
    )
    payload = json.loads(thresholds_path.read_text(encoding="utf-8"))
    assert "auto" in payload
    assert "rust" in payload
    assert payload["auto"]["ratio_key"] == "node_ir_auto_relative_speed"
    assert payload["rust"]["ratio_key"] == "node_ir_relative_speed"


def test_quality_gate_script_exists():
    script_path = _REPO_ROOT / "verification" / "scripts" / "run_quality_gate.py"
    assert script_path.exists()


def test_quality_gate_defaults_to_all_speed_profiles():
    script_path = _REPO_ROOT / "verification" / "scripts" / "run_quality_gate.py"
    source = script_path.read_text(encoding="utf-8")
    assert 'choices=["auto", "rust", "all"]' in source
    assert 'default="all"' in source
    assert '--gate-runs' in source
    assert 'default=3' in source


def test_speed_gate_aggregates_with_median():
    mod = importlib.import_module("verification.scripts.check_speed_gate")
    ratio = mod._aggregate_gate_ratio([1.20, 0.95, 1.00])  # pylint: disable=protected-access
    assert ratio == 1.00
