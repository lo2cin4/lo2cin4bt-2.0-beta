import copy
import importlib
import json
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _contracts_dir() -> Path:
    return _REPO_ROOT / "backtester" / "contracts"


def test_strategy_preview_semantic_accepts_reference_example():
    mod = importlib.import_module("autorunner.StrategyPreview")
    previewer = mod.StrategyPreview()

    strategy_path = (
        _contracts_dir()
        / "strategy"
        / "examples"
        / "strategy-vix-regime-ma-cross.json"
    )
    feature_path = (
        _contracts_dir()
        / "feature"
        / "examples"
        / "feature-contract-vix-price-v1.json"
    )

    preview = previewer.preview(str(strategy_path), str(feature_path))

    assert preview["valid"] is True
    assert preview["summary"]["resolved_mode"] == "node_ir"
    assert preview["summary"]["combination_count"] == 2
    assert "feature.vix.close" in preview["summary"]["fields"]
    assert "price.close" in preview["summary"]["fields"]


def test_strategy_validator_semantic_rejects_unknown_param_ref():
    validator_mod = importlib.import_module("autorunner.StrategyContractValidator")
    validator = validator_mod.StrategyContractValidator()

    strategy = _load_json(
        _contracts_dir()
        / "strategy"
        / "examples"
        / "strategy-vix-regime-ma-cross.json"
    )
    feature = _load_json(
        _contracts_dir()
        / "feature"
        / "examples"
        / "feature-contract-vix-price-v1.json"
    )
    broken = copy.deepcopy(strategy)
    broken["entry"]["nodes"][0]["right"] = {"param_ref": "missing_domain"}

    result = validator.validate(broken, feature)

    assert result.valid is False
    assert any("missing_domain" in err for err in result.errors)


def test_strategy_validator_semantic_rejects_unsupported_requested_mode():
    validator_mod = importlib.import_module("autorunner.StrategyContractValidator")
    validator = validator_mod.StrategyContractValidator()

    strategy = _load_json(
        _contracts_dir()
        / "strategy"
        / "examples"
        / "strategy-vix-regime-ma-cross.json"
    )
    feature = _load_json(
        _contracts_dir()
        / "feature"
        / "examples"
        / "feature-contract-vix-price-v1.json"
    )
    removed_mode_case = copy.deepcopy(strategy)
    removed_mode_case["engine_preferences"]["requested_mode"] = "unsupported_engine_mode"

    result = validator.validate(removed_mode_case, feature)

    assert result.valid is False
    assert any("auto/node_ir" in err for err in result.errors)


def test_strategy_validator_semantic_accepts_timer_bars_param_ref():
    validator_mod = importlib.import_module("autorunner.StrategyContractValidator")
    validator = validator_mod.StrategyContractValidator()

    strategy = _load_json(
        _contracts_dir()
        / "strategy"
        / "examples"
        / "strategy-vix-regime-ma-cross.json"
    )
    feature = _load_json(
        _contracts_dir()
        / "feature"
        / "examples"
        / "feature-contract-vix-price-v1.json"
    )
    case = copy.deepcopy(strategy)
    case["parameter_domains"]["hold_days"] = {"type": "set", "values": [200, 210]}
    case["exit"] = {"op": "timer_bars", "value": {"param_ref": "hold_days"}}

    result = validator.validate(case, feature)

    assert result.valid is True
