import importlib
import json
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _feature_example_path(name: str) -> Path:
    return (
        _REPO_ROOT
        / "backtester"
        / "contracts"
        / "feature"
        / "examples"
        / name
    )


def test_feature_contract_validator_accepts_multisource_contract():
    validator_mod = importlib.import_module("autorunner.FeatureContractValidator_v1")
    validator = validator_mod.FeatureContractValidatorV1()

    payload = json.loads(
        _feature_example_path("feature-contract-multisource-v1.json").read_text(
            encoding="utf-8-sig"
        )
    )
    result = validator.validate(payload)

    assert result.valid is True
    assert result.summary["multi_source"] is True
    assert result.summary["join_mode"] == "asof"
    assert result.summary["calendar_policy"] == "primary"
    assert sorted(result.summary["source_ids"]) == ["spy_price", "vix_daily"]


def test_feature_contract_validator_rejects_duplicate_source_id_mapping():
    validator_mod = importlib.import_module("autorunner.FeatureContractValidator_v1")
    validator = validator_mod.FeatureContractValidatorV1()

    payload = json.loads(
        _feature_example_path("feature-contract-multisource-v1.json").read_text(
            encoding="utf-8-sig"
        )
    )
    payload["features"][1]["source"]["source_id"] = "spy_price"

    result = validator.validate(payload)
    assert result.valid is False
    assert any("maps to multiple uris" in err for err in result.errors)


def test_feature_contract_validator_rejects_lookahead_time_semantics():
    validator_mod = importlib.import_module("autorunner.FeatureContractValidator_v1")
    validator = validator_mod.FeatureContractValidatorV1()

    payload = json.loads(
        _feature_example_path("feature-contract-vix-price-v1.json").read_text(
            encoding="utf-8-sig"
        )
    )
    payload["time_semantics"]["trade_earliest_time"] = "same_bar"

    result = validator.validate(payload)
    assert result.valid is False
    assert any("bar_close observation with same_bar" in error for error in result.errors)


def test_feature_contract_validator_accepts_explicit_time_parse_hints():
    validator_mod = importlib.import_module("autorunner.FeatureContractValidator_v1")
    validator = validator_mod.FeatureContractValidatorV1()

    payload = json.loads(
        _feature_example_path("feature-contract-vix-price-v1.json").read_text(
            encoding="utf-8-sig"
        )
    )
    payload["features"][1]["source"]["dayfirst"] = True
    payload["features"][1]["source"]["time_format"] = "%d/%m/%Y"

    result = validator.validate(payload)

    assert result.valid is True
    assert result.errors == []
