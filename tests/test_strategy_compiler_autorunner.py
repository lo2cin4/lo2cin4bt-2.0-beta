import importlib
import json
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _contracts_dir() -> Path:
    return _REPO_ROOT / "backtester" / "contracts"


def test_strategy_compiler_semantic_generates_execution_plan(tmp_path):
    compiler_mod = importlib.import_module("autorunner.StrategyCompiler")
    compiler = compiler_mod.StrategyCompiler()

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

    result = compiler.compile_from_paths(
        str(strategy_path),
        str(feature_path),
        output_dir=str(tmp_path),
    )

    assert result.valid is True
    assert result.execution_plan_path is not None
    assert Path(result.execution_plan_path).exists()
    assert result.execution_plan["plan_hash"]
    assert result.execution_plan["plan_version"] == "2.1"
    assert result.execution_plan["field_catalog"]
    assert result.execution_plan["field_symbol_table"]
    assert "node_ir" in result.execution_plan
    assert "feature_eval_order" in result.execution_plan
    assert "param_axes" in result.execution_plan
    assert "combo_guard" in result.execution_plan
    assert "signal_kernel_hints" in result.execution_plan
    assert "stateful_flags" in result.execution_plan
    assert "legacy_adapter" not in result.execution_plan
    assert any(
        row.get("field") == "feature.vix.close" and row.get("source_column") == "Close"
        for row in result.execution_plan["field_symbol_table"]
    )
    vix_fid = next(
        fid
        for fid, meta in result.execution_plan["field_catalog"].items()
        if meta.get("field") == "feature.vix.close"
    )
    vix_meta = result.execution_plan["field_catalog"][vix_fid]
    assert vix_meta.get("fill_policy") == "ffill"
    assert vix_meta.get("lag_bars") == 1
    assert result.execution_plan["feature_contract_summary"]["dataset_id"] == "qqq_price_plus_vix_daily_v1"


def test_strategy_compiler_semantic_captures_multisource_feature_contract_metadata(tmp_path):
    compiler_mod = importlib.import_module("autorunner.StrategyCompiler")
    compiler = compiler_mod.StrategyCompiler()

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
        / "feature-contract-multisource-v1.json"
    )

    result = compiler.compile_from_paths(
        str(strategy_path),
        str(feature_path),
        output_dir=str(tmp_path),
    )

    assert result.valid is True
    feature_summary = result.execution_plan["feature_contract_summary"]
    assert feature_summary["multi_source"] is True
    assert feature_summary["join_mode"] == "asof"
    assert feature_summary["calendar_policy"] == "primary"
    assert sorted(feature_summary["source_ids"]) == ["spy_price", "vix_daily"]

    vix_fid = next(
        fid
        for fid, meta in result.execution_plan["field_catalog"].items()
        if meta.get("field") == "feature.vix.close"
    )
    vix_meta = result.execution_plan["field_catalog"][vix_fid]
    assert vix_meta["source_contract_id"] == "vix_daily"
    assert vix_meta["calendar"] == "CBOE"
    assert vix_meta["staleness_max_bars"] == 1
    assert any("multi-source feature contract detected" in msg for msg in result.execution_plan["unknown_unknowns"])


def test_strategy_compiler_semantic_rejects_parameter_space_over_max_combos(tmp_path):
    compiler_mod = importlib.import_module("autorunner.StrategyCompiler")
    compiler = compiler_mod.StrategyCompiler()

    strategy_path = tmp_path / "strategy-large.json"
    strategy_payload = {
        "schema_version": "strategy_contract",
        "strategy_id": "test.max.combo",
        "data_context": {
            "primary_instrument": "TEST",
            "frequency": "1D",
            "timezone": "UTC",
            "calendar": "XNYS",
        },
        "max_combos": 4,
        "parameter_domains": {
            "a": {"type": "set", "values": [1, 2, 3]},
            "b": {"type": "set", "values": [10, 20]},
        },
        "entry": {"op": "gt", "left": {"field": "price.close"}, "right": 0},
        "exit": {"op": "lt", "left": {"field": "price.close"}, "right": 0},
    }
    strategy_path.write_text(json.dumps(strategy_payload), encoding="utf-8")

    result = compiler.compile_from_paths(str(strategy_path), None, output_dir=str(tmp_path))
    assert result.valid is False
    assert any("parameter space too large" in err for err in result.errors)
    assert any("hard_cap_combos" in err for err in result.errors)


def test_strategy_compiler_semantic_warns_when_total_exceeds_warn_combos(tmp_path):
    compiler_mod = importlib.import_module("autorunner.StrategyCompiler")
    compiler = compiler_mod.StrategyCompiler()

    strategy_path = tmp_path / "strategy-warn.json"
    strategy_payload = {
        "schema_version": "strategy_contract",
        "strategy_id": "test.warn.combo",
        "data_context": {
            "primary_instrument": "TEST",
            "frequency": "1D",
            "timezone": "UTC",
            "calendar": "XNYS",
        },
        "combo_limits": {"warn_combos": 3, "hard_cap_combos": 100, "window_cap_combos": 2},
        "parameter_domains": {
            "a": {"type": "set", "values": [1, 2]},
            "b": {"type": "set", "values": [10, 20]},
        },
        "entry": {"op": "gt", "left": {"field": "price.close"}, "right": 0},
        "exit": {"op": "lt", "left": {"field": "price.close"}, "right": 0},
    }
    strategy_path.write_text(json.dumps(strategy_payload), encoding="utf-8")

    result = compiler.compile_from_paths(str(strategy_path), None, output_dir=str(tmp_path))
    assert result.valid is True
    assert any("warn_combos" in msg for msg in result.warnings)
    guard = result.execution_plan.get("combo_guard", {})
    assert guard.get("window_cap_combos") == 2


def test_strategy_validator_semantic_rejects_removed_legacy_top_level_key():
    validator_mod = importlib.import_module("autorunner.StrategyContractValidator")
    validator = validator_mod.StrategyContractValidator()

    strategy = json.loads(
        (
            _contracts_dir()
            / "strategy"
            / "examples"
            / "strategy-vix-regime-ma-cross.json"
        ).read_text(encoding="utf-8-sig")
    )
    feature = json.loads(
        (
            _contracts_dir()
            / "feature"
            / "examples"
            / "feature-contract-vix-price-v1.json"
        ).read_text(encoding="utf-8-sig")
    )
    strategy["legacy"] = {"condition_pairs": [{"entry": ["MA1"], "exit": ["MA4"]}]}

    result = validator.validate(strategy, feature)
    assert result.valid is False
    assert any("unsupported top-level keys" in err for err in result.errors)


def test_config_validator_accepts_semantic_mode_without_condition_pairs(tmp_path):
    validator_mod = importlib.import_module("autorunner.ConfigValidator_autorunner")
    validator = validator_mod.ConfigValidator()

    strategy_path = (
        _contracts_dir()
        / "strategy"
        / "examples"
        / "strategy-vix-regime-ma-cross.json"
    )

    config = {
        "platform": {
            "display_label": "Validator Fixture",
            "run_type": "test",
        },
        "dataloader": {"source": "file", "start_date": "2020-01-01", "file_config": {"file_path": "x.csv"}},
        "backtester": {
            "strategy_mode": "semantic",
            "strategy_contract_path": str(strategy_path),
            "engine_mode": "auto",
            "trading_params": {"transaction_cost": 0.001, "slippage": 0.0005, "trade_delay": 1},
            "export_config": {"export_csv": False, "export_excel": False},
        },
        "metricstracker": {"enable_metrics_analysis": False},
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    assert validator.validate_config(str(path)) is True


def test_config_validator_rejects_semantic_mode_with_legacy_condition_pairs(tmp_path):
    validator_mod = importlib.import_module("autorunner.ConfigValidator_autorunner")
    validator = validator_mod.ConfigValidator()

    config = {
        "dataloader": {"source": "file", "start_date": "2020-01-01", "file_config": {"file_path": "x.csv"}},
        "backtester": {
            "strategy_mode": "semantic",
            "strategy_contract_path": "workspace/strategies/sample.json",
            "condition_pairs": [{"entry": ["MA1"], "exit": ["MA4"]}],
        },
        "metricstracker": {"enable_metrics_analysis": False},
    }
    path = tmp_path / "config_with_legacy_pairs.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    assert validator.validate_config(str(path)) is False
    errors = validator.get_validation_errors(str(path))
    assert any("semantic mode forbids legacy fields" in error for error in errors)


def test_config_validator_rejects_semantic_mode_with_legacy_indicator_params(tmp_path):
    validator_mod = importlib.import_module("autorunner.ConfigValidator_autorunner")
    validator = validator_mod.ConfigValidator()

    config = {
        "dataloader": {"source": "file", "start_date": "2020-01-01", "file_config": {"file_path": "x.csv"}},
        "backtester": {
            "strategy_mode": "semantic",
            "strategy_contract_path": "workspace/strategies/sample.json",
            "indicator_params": {"MA1_strategy_1": {"period": 10}},
        },
        "metricstracker": {"enable_metrics_analysis": False},
    }
    path = tmp_path / "config_with_legacy_indicator_params.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    assert validator.validate_config(str(path)) is False
    errors = validator.get_validation_errors(str(path))
    assert any("semantic mode forbids legacy fields" in error for error in errors)


def test_config_validator_rejects_legacy_without_opt_in(tmp_path):
    validator_mod = importlib.import_module("autorunner.ConfigValidator_autorunner")
    validator = validator_mod.ConfigValidator()
    config = {
        "dataloader": {"source": "file", "start_date": "2020-01-01", "file_config": {"file_path": "x.csv"}},
        "backtester": {
            "strategy_mode": "legacy",
            "condition_pairs": [{"entry": ["MA1"], "exit": ["MA4"]}],
            "indicator_params": {},
            "engine_mode": "auto",
            "trading_params": {"transaction_cost": 0.001, "slippage": 0.0005, "trade_delay": 1},
            "export_config": {"export_csv": False, "export_excel": False},
        },
        "metricstracker": {"enable_metrics_analysis": False},
    }
    path = tmp_path / "config_legacy_no_optin.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    assert validator.validate_config(str(path)) is False


def test_backtest_runner_semantic_runs_with_node_ir_native_runtime(tmp_path, monkeypatch):
    runner_mod = importlib.import_module("autorunner.BacktestRunner_autorunner")
    runner = runner_mod.BacktestRunnerAutorunner()

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

    # Sample strategy has no legacy.condition_pairs adapter yet, so runtime should
    # fail safely with a clear compile-to-runtime boundary.
    config = {
        "dataloader": {"frequency": "1D", "source": "file"},
        "backtester": {
            "strategy_mode": "semantic",
            "strategy_contract_path": str(strategy_path),
            "feature_contract_path": str(feature_path),
            "execution_plan_output_dir": str(tmp_path),
            "engine_mode": "auto",
            "selected_predictor": "X",
            "trading_params": {"transaction_cost": 0.0, "slippage": 0.0, "trade_delay": 1, "trade_price": "close"},
            "export_config": {"export_csv": False, "export_excel": False},
        },
        "metricstracker": {"enable_metrics_analysis": False},
    }
    monkeypatch.setattr(runner, "_export_results", lambda **_: None)

    data = _load_stub_data()
    result = runner.run_backtest(data, config)
    assert result is not None
    assert result["success"] is True
    assert result["strategy_mode"] == "semantic"
    assert result["results"]
    assert result["config"]["condition_pairs"] == []
    assert result["config"]["indicator_params"] == {}


def test_backtest_runner_semantic_keeps_runtime_config_legacy_free():
    runner_mod = importlib.import_module("autorunner.BacktestRunner_autorunner")
    runner = runner_mod.BacktestRunnerAutorunner()

    compile_result = {
        "execution_plan_path": "outputs/backtester/plans/mock.execution_plan.json",
        "execution_plan": {"plan_hash": "abc123def4567890"},
    }
    config = {
        "dataloader": {"frequency": "1D"},
        "backtester": {
            "strategy_mode": "semantic",
            "strategy_contract_path": "workspace/strategies/strategy.json",
            "selected_predictor": "X",
            "trading_params": {"transaction_cost": 0.0},
        },
    }

    converted = runner._convert_config(config, compile_result=compile_result)  # pylint: disable=protected-access
    assert converted["strategy_mode"] == "semantic"
    assert converted["condition_pairs"] == []
    assert converted["indicator_params"] == {}


def _load_stub_data():
    import pandas as pd

    return pd.DataFrame(
        {
            "Time": pd.date_range("2024-01-01", periods=5, freq="D"),
            "Open": [1, 2, 3, 4, 5],
            "High": [1, 2, 3, 4, 5],
            "Low": [1, 2, 3, 4, 5],
            "Close": [1, 2, 3, 4, 5],
            "Volume": [1, 1, 1, 1, 1],
            "X": [1, 1, 1, 1, 1],
        }
    )
