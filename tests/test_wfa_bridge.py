from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import pytest
import pandas as pd

from wfanalyser.ConfigValidator_wfanalyser import ConfigValidator
from wfanalyser.ConfigLoader_wfanalyser import ConfigLoader
from wfanalyser.ParameterOptimizer_wfanalyser import ParameterOptimizer
from wfanalyser.ResultsExporter_wfanalyser import ResultsExporter
from wfanalyser.WalkForwardEngine_wfanalyser import WalkForwardEngine
from backtester.NodeIRExecutor_backtester import NodeIRExecutorBacktester


pytestmark = pytest.mark.regression


def test_wfa_validator_accepts_semantic_config(tmp_path) -> None:
    config_path = tmp_path / "wfa.json"
    config = {
        "platform": {
            "display_label": "WFA Validator Fixture",
            "run_type": "test",
        },
        "wfa_config": {
            "mode": "standard",
            "train_set_percentage": 0.6,
            "test_set_percentage": 0.2,
            "step_size": 30,
            "optimization_objectives": ["sharpe", "calmar"],
        },
        "dataloader": {
            "source": "file",
            "start_date": "2024-01-01",
            "file_config": {"file_path": "workspace/datasets/price.csv"},
        },
        "backtester": {
            "strategy_mode": "semantic",
            "strategy_contract_path": "workspace/strategies/strategy.json",
            "feature_contract_path": "workspace/features/feature-v1.json",
        },
        "metricstracker": {
            "enable_metrics_analysis": True,
        },
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")

    validator = ConfigValidator()
    assert validator.validate_config(str(config_path)) is True


def test_wfa_loader_accepts_wfa_run_primary_config(tmp_path) -> None:
    strategy_path = tmp_path / "strategy_run.json"
    strategy_path.write_text(
        json.dumps(
            {
                "schema_version": "strategy_run",
                "platform": {
                    "strategy_mode_id": "calendar_event_session",
                    "workflow_id": "parameter_matrix",
                    "run_type": "test",
                },
                "data": {
                    "provider": "yfinance",
                    "frequency": "1D",
                    "calendar": "XNYS",
                    "timezone": "America/New_York",
                    "start_date": "2024-01-01",
                },
                "universe": {"symbols": ["QQQ"]},
                "features": [],
                "signals": {
                    "entry": {
                        "op": "calendar.nth_weekday_of_month",
                        "months": [3, 6, 9, 12],
                        "ordinal": {"param_ref": "ordinal"},
                        "weekday": {"param_ref": "weekday"},
                    },
                    "exit": {"op": "session.same_session_close"},
                    "side": "short",
                },
                "selection": {},
                "allocation": {
                    "method": "signal_target_weight",
                    "target_weight": -1.0,
                    "cash_policy": "keep_unallocated_cash",
                },
                "rebalance": {"trigger": {"op": "signal.change"}},
                "execution": {
                    "timing": "same_session",
                    "price": "open",
                    "entry_price": "open",
                    "exit_price": "close",
                    "session_scope": "same_session",
                    "same_session_exit": True,
                    "cost": {"transaction_cost": 0.0, "slippage": 0.0},
                },
                "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "short_only", "allow_short": True},
                "parameter_domains": {
                    "ordinal": {"type": "set", "values": [1, 2]},
                    "weekday": {"type": "set", "values": ["monday", "friday"]},
                },
                "outputs": {"equity_curve": True},
            }
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "wfa_run.json"
    normalized = {
        "schema_version": "wfa_run",
        "strategy_config_path": str(strategy_path.name),
        "platform": {"display_label": "Calendar WFA Fixture", "run_type": "test"},
        "windowing": {"mode": "rolling", "target_window_count": 3, "train_ratio": 0.6, "test_ratio": 0.2, "step_size": 10},
        "optimizer": {"type": "grid", "objectives": ["sharpe"]},
        "outputs": {"selected_optimum": True, "candidate_diagnostics": True},
    }
    config_path.write_text(json.dumps(normalized), encoding="utf-8")

    validator = ConfigValidator()
    loaded = ConfigLoader().load_config(str(config_path))

    assert validator.validate_config(str(config_path)) is True
    assert loaded is not None
    assert loaded.wfa_config["mode"] == "standard"
    assert loaded.dataloader_config["source"] == "yfinance"
    assert loaded.backtester_config["strategy_run_config"]["schema_version"] == "strategy_run"
    assert loaded.backtester_config["strategy_config"]["platform"]["strategy_mode_id"] == "calendar_event_session"
    assert loaded.backtester_config["market_data"]["symbols"] == ["QQQ"]
    assert loaded.raw_config["platform"]["display_label"] == "Calendar WFA Fixture"


def test_wfa_loader_resolves_explicit_workspace_run_strategy_path(tmp_path) -> None:
    repo = tmp_path / "repo"
    runs_dir = repo / "workspace" / "runs"
    wfa_dir = repo / "workspace" / "wfa"
    included_examples_dir = repo / "backtester" / "contracts" / "strategy" / "examples"
    runs_dir.mkdir(parents=True)
    wfa_dir.mkdir(parents=True)
    included_examples_dir.mkdir(parents=True)

    example_root = Path(__file__).resolve().parents[1] / "backtester" / "contracts" / "strategy" / "examples"
    strategy_source = example_root / "strategy-run-qqq-yfinance-daily-sma-cross-matrix-example.json"
    wfa_source = example_root / "wfa-run-qqq-yfinance-daily-sma-cross-example.json"
    (runs_dir / strategy_source.name).write_text(strategy_source.read_text(encoding="utf-8"), encoding="utf-8")
    included_payload = json.loads(strategy_source.read_text(encoding="utf-8"))
    included_payload["universe"]["symbols"] = ["SPY"]
    included_payload["data"]["benchmark"]["symbol"] = "SPY"
    (included_examples_dir / strategy_source.name).write_text(json.dumps(included_payload), encoding="utf-8")
    config_payload = json.loads(wfa_source.read_text(encoding="utf-8"))
    config_payload["strategy_config_path"] = f"workspace/runs/{strategy_source.name}"
    config_path = wfa_dir / wfa_source.name
    config_path.write_text(json.dumps(config_payload), encoding="utf-8")

    loaded = ConfigLoader().load_config(str(config_path))

    assert loaded is not None
    assert loaded.backtester_config["strategy_run_config"]["schema_version"] == "strategy_run"
    assert loaded.backtester_config["market_data"]["symbols"] == ["QQQ"]


def test_wfa_loader_does_not_guess_bare_strategy_filename_from_workspace_runs(tmp_path) -> None:
    repo = tmp_path / "repo"
    runs_dir = repo / "workspace" / "runs"
    wfa_dir = repo / "workspace" / "wfa"
    included_examples_dir = repo / "backtester" / "contracts" / "strategy" / "examples"
    runs_dir.mkdir(parents=True)
    wfa_dir.mkdir(parents=True)
    included_examples_dir.mkdir(parents=True)

    example_root = Path(__file__).resolve().parents[1] / "backtester" / "contracts" / "strategy" / "examples"
    strategy_source = example_root / "strategy-run-qqq-yfinance-daily-sma-cross-matrix-example.json"
    wfa_source = example_root / "wfa-run-qqq-yfinance-daily-sma-cross-example.json"
    (runs_dir / strategy_source.name).write_text(strategy_source.read_text(encoding="utf-8"), encoding="utf-8")
    (included_examples_dir / strategy_source.name).write_text(strategy_source.read_text(encoding="utf-8"), encoding="utf-8")
    config_payload = json.loads(wfa_source.read_text(encoding="utf-8"))
    config_payload["strategy_config_path"] = strategy_source.name
    config_path = wfa_dir / wfa_source.name
    config_path.write_text(json.dumps(config_payload), encoding="utf-8")

    loaded = ConfigLoader().load_config(str(config_path))

    assert loaded is None


def test_wfa_standard_windows_can_target_window_count() -> None:
    engine = WalkForwardEngine(
        config_data=SimpleNamespace(
            wfa_config={
                "mode": "standard",
                "train_set_percentage": 0.5,
                "test_set_percentage": 0.2,
                "step_size": 30,
                "target_window_count": 5,
            },
            dataloader_config={},
            predictor_config={},
            metricstracker_config={},
            backtester_config={},
        )
    )
    engine.data = pd.DataFrame({"Close": list(range(100))})

    windows = engine._divide_windows()  # pylint: disable=protected-access

    assert len(windows) == 5
    assert [window["window_id"] for window in windows] == [1, 2, 3, 4, 5]
    assert windows[0]["train_start"] == 0
    assert windows[-1]["test_end"] <= 100


def test_wfa_validator_rejects_parameter_matrix_handoff_fields(tmp_path) -> None:
    config_path = tmp_path / "wfa_with_handoff_fields.json"
    config = {
        "platform": {
            "display_label": "WFA Handoff Rejection Fixture",
            "run_type": "test",
        },
        "wfa_config": {
            "mode": "standard",
            "train_set_percentage": 0.6,
            "test_set_percentage": 0.2,
            "step_size": 30,
            "optimization_objectives": ["sharpe"],
            "shortlist_candidates": [{"params": {"vix_max": 33}}],
            "review_mode": "review_then_send",
            "shortlist_source_run_id": "metrics-run-1",
            "pack_strategy": "balanced",
            "pack_preview": {"candidate_count": 1},
        },
        "dataloader": {
            "source": "file",
            "start_date": "2024-01-01",
            "file_config": {"file_path": "workspace/datasets/price.csv"},
        },
        "backtester": {
            "strategy_mode": "semantic",
            "strategy_contract_path": "workspace/strategies/strategy.json",
            "feature_contract_path": "workspace/features/feature-v1.json",
        },
        "metricstracker": {
            "enable_metrics_analysis": True,
        },
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")

    validator = ConfigValidator()
    assert validator.validate_config(str(config_path)) is False
    errors = validator.get_validation_errors(str(config_path))
    assert any("forbids Parameter Matrix handoff fields" in error for error in errors)


def test_wfa_validator_rejects_semantic_legacy_condition_pairs(tmp_path) -> None:
    config_path = tmp_path / "wfa_with_legacy_pairs.json"
    config = {
        "wfa_config": {
            "mode": "standard",
            "train_set_percentage": 0.6,
            "test_set_percentage": 0.2,
            "step_size": 30,
            "optimization_objectives": ["sharpe"],
        },
        "dataloader": {
            "source": "file",
            "start_date": "2024-01-01",
            "file_config": {"file_path": "workspace/datasets/price.csv"},
        },
        "backtester": {
            "strategy_mode": "semantic",
            "strategy_contract_path": "workspace/strategies/strategy.json",
            "condition_pairs": [{"entry": ["MA1"], "exit": ["MA4"]}],
        },
        "metricstracker": {"enable_metrics_analysis": True},
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")

    validator = ConfigValidator()
    assert validator.validate_config(str(config_path)) is False
    errors = validator.get_validation_errors(str(config_path))
    assert any("semantic mode forbids legacy fields" in error for error in errors)


def test_wfa_validator_rejects_semantic_legacy_indicator_params(tmp_path) -> None:
    config_path = tmp_path / "wfa_with_legacy_indicator_params.json"
    config = {
        "wfa_config": {
            "mode": "standard",
            "train_set_percentage": 0.6,
            "test_set_percentage": 0.2,
            "step_size": 30,
            "optimization_objectives": ["sharpe"],
        },
        "dataloader": {
            "source": "file",
            "start_date": "2024-01-01",
            "file_config": {"file_path": "workspace/datasets/price.csv"},
        },
        "backtester": {
            "strategy_mode": "semantic",
            "strategy_contract_path": "workspace/strategies/strategy.json",
            "indicator_params": {"MA1_strategy_1": {"period": 10}},
        },
        "metricstracker": {"enable_metrics_analysis": True},
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")

    validator = ConfigValidator()
    assert validator.validate_config(str(config_path)) is False
    errors = validator.get_validation_errors(str(config_path))
    assert any("semantic mode forbids legacy fields" in error for error in errors)


def test_wfa_validator_rejects_legacy_without_opt_in(tmp_path) -> None:
    config_path = tmp_path / "wfa_legacy.json"
    config = {
        "wfa_config": {
            "mode": "standard",
            "train_set_percentage": 0.6,
            "test_set_percentage": 0.2,
            "step_size": 30,
        },
        "dataloader": {
            "source": "file",
            "start_date": "2024-01-01",
            "file_config": {"file_path": "workspace/datasets/price.csv"},
        },
        "backtester": {
            "strategy_mode": "legacy",
            "condition_pairs": [{"entry": ["MA1"], "exit": ["MA4"]}],
        },
        "metricstracker": {
            "enable_metrics_analysis": True,
        },
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    validator = ConfigValidator()
    assert validator.validate_config(str(config_path)) is False


def test_wfa_node_ir_only_policy_blocks_legacy_runtime() -> None:
    config_data = SimpleNamespace(
        wfa_config={
            "mode": "standard",
            "node_ir_only": True,
            "train_set_percentage": 0.6,
            "test_set_percentage": 0.2,
            "step_size": 30,
            "optimization_objectives": ["sharpe"],
        },
        dataloader_config={"source": "file", "start_date": "2024-01-01"},
        predictor_config={},
        metricstracker_config={"enable_metrics_analysis": True},
        backtester_config={
            "strategy_mode": "legacy",
            "legacy_opt_in": True,
            "condition_pairs": [{"entry": ["MA1"], "exit": ["MA4"]}],
        },
    )
    engine = WalkForwardEngine(config_data=config_data)
    engine._load_data = lambda: (setattr(engine, "data", pd.DataFrame({"Close": [1, 2, 3]})), setattr(engine, "frequency", "1D"))  # type: ignore[attr-defined]
    assert engine.run() is None


def test_parameter_optimizer_semantic_native_runtime_no_legacy_injection(monkeypatch) -> None:
    def _fake_compile(self, strategy_contract_path, feature_contract_path=None, output_dir=None):
        return SimpleNamespace(
            valid=True,
            errors=[],
            execution_plan_path="outputs/backtester/plans/mock.execution_plan.json",
            execution_plan={"node_ir": {"entry": {}, "exit": {}}},
        )

    monkeypatch.setattr(
        "autorunner.StrategyCompiler.StrategyCompiler.compile_from_paths",
        _fake_compile,
    )

    config_data = SimpleNamespace(
        backtester_config={
            "strategy_mode": "semantic",
            "strategy_contract_path": "workspace/strategies/strategy.json",
            "feature_contract_path": "workspace/features/feature-v1.json",
            "selected_predictor": "X",
            "trading_params": {"transaction_cost": 0.001},
        },
        symbol="BTCUSDT",
    )

    optimizer = ParameterOptimizer(
        train_data=None,  # not used in this bridge test
        frequency="1d",
        config_data=config_data,
    )

    assert optimizer.backtester_config["strategy_mode"] == "semantic"
    assert "condition_pairs" not in optimizer.backtester_config
    assert "indicator_params" not in optimizer.backtester_config


def test_parameter_optimizer_semantic_strips_stale_legacy_fields_from_input(monkeypatch) -> None:
    def _fake_compile(self, strategy_contract_path, feature_contract_path=None, output_dir=None):
        return SimpleNamespace(
            valid=True,
            errors=[],
            execution_plan_path="outputs/backtester/plans/mock.execution_plan.json",
            execution_plan={"node_ir": {"entry": {}, "exit": {}}},
        )

    monkeypatch.setattr(
        "autorunner.StrategyCompiler.StrategyCompiler.compile_from_paths",
        _fake_compile,
    )

    config_data = SimpleNamespace(
        backtester_config={
            "strategy_mode": "semantic",
            "strategy_contract_path": "workspace/strategies/strategy.json",
            "feature_contract_path": "workspace/features/feature-v1.json",
            "condition_pairs": [{"entry": ["MA1"], "exit": ["MA4"]}],
            "indicator_params": {"MA1_strategy_1": {"ma_type": "SMA"}},
            "selected_predictor": "X",
            "trading_params": {"transaction_cost": 0.001},
        },
        symbol="BTCUSDT",
    )

    optimizer = ParameterOptimizer(
        train_data=None,
        frequency="1d",
        config_data=config_data,
    )

    assert optimizer.backtester_config["strategy_mode"] == "semantic"
    assert "condition_pairs" not in optimizer.backtester_config
    assert "indicator_params" not in optimizer.backtester_config


def test_wfa_collect_results_carries_contract_audit() -> None:
    config_data = SimpleNamespace(
        wfa_config={"optimization_objectives": ["sharpe"]},
        dataloader_config={},
        predictor_config={},
        metricstracker_config={},
        backtester_config={"condition_pairs": [{"entry": ["MA1"], "exit": ["MA4"]}]},
    )
    engine = WalkForwardEngine(config_data=config_data)
    engine.contract_audit = {
        "strategy_mode": "semantic",
        "strategy_contract_path": "workspace/strategies/s.json",
        "feature_contract_path": "workspace/features/f.json",
        "execution_plan_path": "outputs/backtester/plans/p.execution_plan.json",
    }

    result = engine._collect_results(  # pylint: disable=protected-access
        [
            {
                "sharpe": {
                    "window_info": {"window_id": 1},
                    "test_result": {"metrics": {}},
                }
            }
        ]
    )
    assert result["contract_audit"]["strategy_mode"] == "semantic"
    assert result["contract_audit"]["execution_plan_path"].endswith(
        ".execution_plan.json"
    )


def test_results_exporter_audit_columns_merge_global_and_window() -> None:
    exporter = ResultsExporter(
        results={
            "contract_audit": {
                "strategy_mode": "semantic",
                "strategy_contract_path": "workspace/strategies/global.json",
            }
        },
        output_dir=Path("."),
    )
    merged = exporter._build_audit_columns(  # pylint: disable=protected-access
        {
            "contract_audit": {
                "strategy_contract_path": "workspace/strategies/window.json",
                "execution_plan_path": "outputs/backtester/plans/window.execution_plan.json",
                "execution_plan_hash": "abc123def4560000",
                "execution_plan_id": "abc123def456",
            }
        }
    )
    assert merged["strategy_mode"] == "semantic"
    assert merged["strategy_contract_path"] == "workspace/strategies/window.json"
    assert merged["execution_plan_path"].endswith(".execution_plan.json")
    assert merged["execution_plan_hash"] == "abc123def4560000"
    assert merged["execution_plan_id"] == "abc123def456"


def test_results_exporter_audit_columns_fallback_to_config_data() -> None:
    exporter = ResultsExporter(
        results={"contract_audit": {}},
        output_dir=Path("."),
        config_data=SimpleNamespace(
            backtester_config={
                "strategy_mode": "semantic",
                "strategy_contract_path": "workspace/strategies/from-config.json",
                "feature_contract_path": "workspace/features/from-config.json",
            }
        ),
    )
    merged = exporter._build_audit_columns({})  # pylint: disable=protected-access
    assert merged["strategy_mode"] == "semantic"
    assert merged["strategy_contract_path"] == "workspace/strategies/from-config.json"
    assert merged["feature_contract_path"] == "workspace/features/from-config.json"


def test_wfa_window_result_hash_is_deterministic() -> None:
    payload = {
        "window_info": {"window_id": 1, "train_start": 0, "train_end": 10, "test_start": 10, "test_end": 15},
        "optimal_params": {"semantic_combo": {"vix_threshold": 30}},
        "train_metrics": {"sharpe": 1.1, "total_return": 0.12},
        "test_result": {"metrics": {"sharpe": 0.9, "total_return": 0.08}},
        "contract_audit": {"execution_plan_hash": "abc"},
    }
    h1 = WalkForwardEngine._build_window_result_hash(  # pylint: disable=protected-access
        objective_name="sharpe",
        payload=payload,
    )
    h2 = WalkForwardEngine._build_window_result_hash(  # pylint: disable=protected-access
        objective_name="sharpe",
        payload=payload,
    )
    h3 = WalkForwardEngine._build_window_result_hash(  # pylint: disable=protected-access
        objective_name="calmar",
        payload=payload,
    )
    assert isinstance(h1, str) and len(h1) == 64
    assert h1 == h2
    assert h1 != h3


def test_walk_forward_engine_semantic_shared_cache_stays_window_safe(monkeypatch) -> None:
    execution_plan = {
        "feature_dag": {
            "feat_ma_fast": {
                "feature": "ta.sma",
                "source": "price.close",
                "params": {"period": 5},
            }
        },
        "node_ir": {"entry": {}, "exit": {}},
    }

    def _fake_compile(self, strategy_contract_path, feature_contract_path=None, output_dir=None):
        return SimpleNamespace(
            valid=True,
            errors=[],
            execution_plan_path="outputs/backtester/plans/mock.execution_plan.json",
            execution_plan=execution_plan,
        )

    monkeypatch.setattr(
        "autorunner.StrategyCompiler.StrategyCompiler.compile_from_paths",
        _fake_compile,
    )

    config_data = SimpleNamespace(
        backtester_config={
            "strategy_mode": "semantic",
            "strategy_contract_path": "workspace/strategies/strategy.json",
            "feature_contract_path": "workspace/features/feature-v1.json",
            "selected_predictor": "X",
            "trading_params": {"transaction_cost": 0.0},
        },
        wfa_config={"optimization_objectives": ["sharpe"]},
        dataloader_config={},
        predictor_config={},
        metricstracker_config={},
        symbol="SPY",
    )

    engine = WalkForwardEngine(config_data=config_data)
    engine.data = pd.DataFrame(
        {
            "Time": pd.date_range("2024-01-01", periods=20, freq="D"),
            "Close": list(range(20)),
            "price.close": list(range(20)),
        }
    )

    engine._prepare_shared_strategy_runtime_cache()  # pylint: disable=protected-access

    precomputed_col = NodeIRExecutorBacktester._precomputed_feature_column_name(  # pylint: disable=protected-access
        feature="ta.sma",
        source="price.close",
        period=5,
    )
    assert precomputed_col not in engine.data.columns
    assert engine._shared_runtime_cache["strategy_mode"] == "semantic"  # pylint: disable=protected-access


def test_wfa_window_oos_uses_only_is_selected_params(monkeypatch) -> None:
    class FakeOptimizer:
        selected = {"semantic_combo": {"score": 3}}
        full_grid = {
            "all_params": [
                {"semantic_combo": {"score": 1}},
                {"semantic_combo": {"score": 3}},
            ]
        }

        def __init__(
            self,
            train_data,
            frequency,
            config_data,
            logger=None,
            shared_runtime_cache=None,
            optimizer_context=None,
        ):
            self.backtester_config = {"strategy_mode": "semantic"}
            self.received_oos_grid = None

        def optimize_with_is_metrics(self, objective, silent=True):
            return dict(self.selected), {"sharpe": 3.0, "total_return": 0.3}

        def get_last_grid_region(self):
            return dict(self.full_grid)

        def get_all_grid_regions(self):
            return {0: dict(self.full_grid)}

        def run_grid_test(self, test_data, grid_region, fallback_params, objective, silent=True):
            self.received_oos_grid = grid_region
            assert grid_region == {"all_params": [self.selected]}
            assert fallback_params == self.selected
            return {
                "metrics": {"sharpe": 0.9, "total_return": 0.09, "param_count": 1},
                "individual_results": [
                    {"params": self.selected, "metric": 0.9, "success": True}
                ],
                "all_condition_pair_results": {},
            }

        def get_last_failure_reason(self):
            return None

    monkeypatch.setattr(
        "wfanalyser.WalkForwardEngine_wfanalyser.ParameterOptimizer",
        FakeOptimizer,
    )

    engine = WalkForwardEngine(
        config_data=SimpleNamespace(
            wfa_config={"optimization_objectives": ["sharpe"]},
            dataloader_config={},
            predictor_config={},
            metricstracker_config={},
            backtester_config={"strategy_mode": "semantic"},
        )
    )
    engine.frequency = "1d"

    result, status = engine._process_window(  # pylint: disable=protected-access
        {
            "window_id": 1,
            "train_data": pd.DataFrame({"Close": [1, 2, 3]}),
            "test_data": pd.DataFrame({"Close": [4, 5, 6]}),
            "train_start": "2024-01-01",
            "train_end": "2024-01-03",
            "test_start": "2024-01-04",
            "test_end": "2024-01-06",
        },
        current=1,
        total=1,
    )

    assert status["sharpe_metric"] == 0.9
    assert result["sharpe"]["optimal_params"] == {"semantic_combo": {"score": 3}}
    individual_results = result["sharpe"]["test_result"]["individual_results"]
    assert len(individual_results) == 1
    assert individual_results[0]["params"] == {"semantic_combo": {"score": 3}}
    assert result["sharpe"]["grid_region"]["all_params"] == FakeOptimizer.full_grid["all_params"]
    assert result["sharpe"]["test_result"]["metrics"]["param_count"] == 1


def test_results_exporter_exports_ranking_report(tmp_path: Path) -> None:
    exporter = ResultsExporter(
        results={"contract_audit": {"strategy_mode": "semantic"}},
        output_dir=tmp_path,
        config_data=SimpleNamespace(wfa_config={"export_ranking_report": True, "ranking_top_n": 2}),
    )
    df = pd.DataFrame(
        [
            {"window_id": 1, "semantic_combo": '{"vix":30}', "oos_sharpe": 1.2, "is_sharpe": 1.1, "oos_total_return": 0.1},
            {"window_id": 2, "semantic_combo": '{"vix":30}', "oos_sharpe": 1.0, "is_sharpe": 1.0, "oos_total_return": 0.08},
            {"window_id": 1, "semantic_combo": '{"vix":35}', "oos_sharpe": 0.5, "is_sharpe": 0.4, "oos_total_return": 0.03},
        ]
    )
    exporter._export_ranking_report(  # pylint: disable=protected-access
        objective="sharpe",
        df=df,
        filename_base="sample",
    )
    ranking_csv = tmp_path / "sample_top2.csv"
    assert ranking_csv.exists()
    out = pd.read_csv(ranking_csv)
    assert len(out) == 2
    assert out.loc[0, "rank"] == 1
    assert str(out.loc[0, "semantic_combo"]) == '{"vix":30}'


def test_results_exporter_semantic_single_strategy_preserves_oos_metrics_when_all_grid_regions_present(
    tmp_path: Path,
) -> None:
    exporter = ResultsExporter(
        results={
            "results_by_objective": {
                "sharpe": [
                    {
                        "window_info": {
                            "window_id": 1,
                            "train_start": 0,
                            "train_end": 9,
                            "test_start": 10,
                            "test_end": 19,
                        },
                        "train_metrics": {
                            "sharpe": 1.1,
                            "calmar": 0.8,
                            "total_return": 0.12,
                            "max_drawdown": -0.1,
                        },
                        "grid_region": {
                            "all_params": [
                                {
                                    "semantic_combo": {
                                        "fast_ma": 10,
                                        "slow_ma": 20,
                                        "trend_ma": 200,
                                        "vix_threshold": 30,
                                    }
                                },
                                {
                                    "semantic_combo": {
                                        "fast_ma": 10,
                                        "slow_ma": 20,
                                        "trend_ma": 200,
                                        "vix_threshold": 31,
                                    }
                                },
                            ],
                            "individual_metrics": [1.1, 1.2],
                            "individual_full_metrics": [
                                {
                                    "sharpe": 1.1,
                                    "calmar": 0.8,
                                    "sortino": 1.0,
                                    "total_return": 0.12,
                                    "max_drawdown": -0.1,
                                },
                                {
                                    "sharpe": 1.2,
                                    "calmar": 0.85,
                                    "sortino": 1.1,
                                    "total_return": 0.15,
                                    "max_drawdown": -0.09,
                                },
                            ],
                        },
                        # semantic WFA may populate all_grid_regions even when there is only
                        # one semantic sweep, and exporter must not treat that as a
                        # multi-condition legacy layout.
                        "all_grid_regions": {0: {"all_params": ["placeholder"]}},
                        "test_result": {
                            "metrics": {
                                "sharpe": 0.55,
                                "calmar": 0.33,
                                "sortino": 0.4,
                                "total_return": 0.08,
                                "max_drawdown": -0.05,
                            },
                            "individual_results": [
                                {
                                    "param_index": 0,
                                    "sharpe": 0.5,
                                    "calmar": 0.30,
                                    "sortino": 0.35,
                                    "return": 0.07,
                                    "max_drawdown": -0.05,
                                },
                                {
                                    "param_index": 1,
                                    "sharpe": 0.6,
                                    "calmar": 0.36,
                                    "sortino": 0.45,
                                    "return": 0.09,
                                    "max_drawdown": -0.04,
                                },
                            ],
                            "all_condition_pair_results": {},
                        },
                    }
                ]
            },
            "contract_audit": {"strategy_mode": "semantic"},
        },
        output_dir=tmp_path,
        config_data=SimpleNamespace(wfa_config={"output_csv": False}),
    )

    exporter.export()

    parquet_files = sorted(
        p
        for p in tmp_path.glob("*_wfa_sharpe_*.parquet")
        if not p.name.endswith("_audit.parquet") and "_wfa_ranking_" not in p.name
    )
    assert parquet_files
    df = pd.read_parquet(parquet_files[-1])
    assert len(df) == 1
    assert list(df["oos_sharpe"]) == [0.55]
    assert list(df["oos_calmar"]) == [0.33]
    assert list(df["oos_total_return"]) == [0.08]
    assert list(df["wfa_row_type"]) == ["selected_optimum"]
    assert list(df["selection_evidence"]) == ["rank=1 by IS Sharpe"]
    assert list(df["candidate_count"]) == [2]

    diagnostic_files = sorted(
        p
        for p in tmp_path.glob("*_wfa_candidate_diagnostics_sharpe_*.parquet")
        if not p.name.endswith("_audit.parquet")
    )
    assert diagnostic_files
    diagnostic_df = pd.read_parquet(diagnostic_files[-1])
    assert len(diagnostic_df) == 2
    assert set(diagnostic_df["wfa_row_type"]) == {"candidate_diagnostic"}


def test_parameter_optimizer_semantic_node_ir_native_selects_best_combo(monkeypatch) -> None:
    def _fake_compile(self, strategy_contract_path, feature_contract_path=None, output_dir=None):
        return SimpleNamespace(
            valid=True,
            errors=[],
            execution_plan_path="outputs/backtester/plans/mock.execution_plan.json",
            execution_plan={
                "node_ir": {"entry": {}, "exit": {}},
                "field_catalog": {},
                "feature_dag": {},
            },
        )

    def _fake_run_from_paths(
        self,
        *,
        strategy_contract_path,
        feature_contract_path,
        execution_plan,
        trading_params,
        predictor_column,
        symbol,
        backtest_id_prefix,
    ):
        _ = (
            strategy_contract_path,
            feature_contract_path,
            execution_plan,
            trading_params,
            predictor_column,
            symbol,
            backtest_id_prefix,
        )
        records_a = pd.DataFrame(
            {
                "Return": [0.0, 0.01, -0.002, 0.003],
                "Equity_value": [1.0, 1.01, 1.00798, 1.01099],
                "Trade_action": [0, 1, 4, 0],
            }
        )
        records_b = pd.DataFrame(
            {
                "Return": [0.0, 0.02, -0.001, 0.006],
                "Equity_value": [1.0, 1.02, 1.01898, 1.02509],
                "Trade_action": [0, 1, 4, 0],
            }
        )
        return [
            {"params": {"semantic_combo": {"vix_max": 30}}, "records": records_a},
            {"params": {"semantic_combo": {"vix_max": 35}}, "records": records_b},
        ]

    monkeypatch.setattr(
        "autorunner.StrategyCompiler.StrategyCompiler.compile_from_paths",
        _fake_compile,
    )
    monkeypatch.setattr(
        "backtester.NodeIRExecutor_backtester.NodeIRExecutorBacktester.run_from_paths",
        _fake_run_from_paths,
    )

    config_data = SimpleNamespace(
        backtester_config={
            "strategy_mode": "semantic",
            "strategy_contract_path": "workspace/strategies/strategy.json",
            "feature_contract_path": "workspace/features/feature-v1.json",
            "selected_predictor": "X",
            "trading_params": {"transaction_cost": 0.001},
        },
        symbol="BTCUSDT",
    )

    optimizer = ParameterOptimizer(
        train_data=pd.DataFrame({"Close": [1, 2, 3, 4]}),
        frequency="1d",
        config_data=config_data,
    )
    optimal_params, train_metrics = optimizer.optimize_with_is_metrics("sharpe")

    assert optimal_params is not None
    assert optimal_params["semantic_combo"]["vix_max"] == 35
    assert train_metrics is not None
    assert optimizer.get_last_grid_region() is not None


def test_parameter_optimizer_semantic_node_ir_native_oos_grid_returns_metrics(monkeypatch) -> None:
    def _fake_compile(self, strategy_contract_path, feature_contract_path=None, output_dir=None):
        return SimpleNamespace(
            valid=True,
            errors=[],
            execution_plan_path="outputs/backtester/plans/mock.execution_plan.json",
            execution_plan={
                "node_ir": {"entry": {}, "exit": {}},
                "field_catalog": {},
                "feature_dag": {},
            },
        )

    def _fake_run_from_paths(
        self,
        *,
        strategy_contract_path,
        feature_contract_path,
        execution_plan,
        trading_params,
        predictor_column,
        symbol,
        backtest_id_prefix,
    ):
        _ = (
            strategy_contract_path,
            feature_contract_path,
            execution_plan,
            trading_params,
            predictor_column,
            symbol,
            backtest_id_prefix,
        )
        records = pd.DataFrame(
            {
                "Return": [0.0, 0.01, -0.002, 0.004],
                "Equity_value": [1.0, 1.01, 1.00798, 1.01201],
                "Trade_action": [0, 1, 4, 0],
            }
        )
        return [{"params": {"semantic_combo": {"vix_max": 33}}, "records": records}]

    monkeypatch.setattr(
        "autorunner.StrategyCompiler.StrategyCompiler.compile_from_paths",
        _fake_compile,
    )
    monkeypatch.setattr(
        "backtester.NodeIRExecutor_backtester.NodeIRExecutorBacktester.run_from_paths",
        _fake_run_from_paths,
    )

    config_data = SimpleNamespace(
        backtester_config={
            "strategy_mode": "semantic",
            "strategy_contract_path": "workspace/strategies/strategy.json",
            "feature_contract_path": "workspace/features/feature-v1.json",
            "selected_predictor": "X",
            "trading_params": {"transaction_cost": 0.001},
        },
        symbol="BTCUSDT",
    )

    optimizer = ParameterOptimizer(
        train_data=pd.DataFrame({"Close": [1, 2, 3, 4]}),
        frequency="1d",
        config_data=config_data,
    )
    grid_region = {
        "all_params": [{"semantic_combo": {"vix_max": 33}}],
    }
    test_result = optimizer.run_grid_test(
        test_data=pd.DataFrame({"Close": [1, 2, 3, 4]}),
        grid_region=grid_region,
        fallback_params={"semantic_combo": {"vix_max": 33}},
        objective="sharpe",
    )
    assert test_result is not None
    assert test_result["metrics"]["param_count"] >= 1
    assert "sharpe" in test_result["metrics"]


def test_parameter_optimizer_semantic_window_cap_limits_grid_region(monkeypatch) -> None:
    def _fake_compile(self, strategy_contract_path, feature_contract_path=None, output_dir=None):
        return SimpleNamespace(
            valid=True,
            errors=[],
            execution_plan_path="outputs/backtester/plans/mock.execution_plan.json",
            execution_plan={
                "node_ir": {"entry": {}, "exit": {}},
                "field_catalog": {},
                "feature_dag": {},
                "combo_guard": {"window_cap_combos": 2},
            },
        )

    def _fake_run_from_paths(
        self,
        *,
        strategy_contract_path,
        feature_contract_path,
        execution_plan,
        trading_params,
        predictor_column,
        symbol,
        backtest_id_prefix,
    ):
        _ = (
            strategy_contract_path,
            feature_contract_path,
            execution_plan,
            trading_params,
            predictor_column,
            symbol,
            backtest_id_prefix,
        )
        def _records(base: float):
            return pd.DataFrame(
                {
                    "Return": [0.0, base, -0.001, base * 0.5],
                    "Equity_value": [1.0, 1.0 + base, 1.0 + base - 0.001, 1.0 + base - 0.001 + base * 0.5],
                    "Trade_action": [0, 1, 4, 0],
                }
            )
        return [
            {"params": {"semantic_combo": {"vix_max": 25}}, "records": _records(0.01)},
            {"params": {"semantic_combo": {"vix_max": 30}}, "records": _records(0.02)},
            {"params": {"semantic_combo": {"vix_max": 35}}, "records": _records(0.03)},
        ]

    monkeypatch.setattr(
        "autorunner.StrategyCompiler.StrategyCompiler.compile_from_paths",
        _fake_compile,
    )
    monkeypatch.setattr(
        "backtester.NodeIRExecutor_backtester.NodeIRExecutorBacktester.run_from_paths",
        _fake_run_from_paths,
    )

    config_data = SimpleNamespace(
        backtester_config={
            "strategy_mode": "semantic",
            "strategy_contract_path": "workspace/strategies/strategy.json",
            "feature_contract_path": "workspace/features/feature-v1.json",
            "selected_predictor": "X",
            "trading_params": {"transaction_cost": 0.001},
        },
        symbol="BTCUSDT",
    )

    optimizer = ParameterOptimizer(
        train_data=pd.DataFrame({"Close": [1, 2, 3, 4]}),
        frequency="1d",
        config_data=config_data,
    )
    optimal_params, _ = optimizer.optimize_with_is_metrics("sharpe")
    assert optimal_params is not None
    grid_region = optimizer.get_last_grid_region()
    assert grid_region is not None
    assert len(grid_region.get("all_params", [])) == 2
    assert grid_region.get("window_cap_combos") == 2
