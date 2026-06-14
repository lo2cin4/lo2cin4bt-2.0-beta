from __future__ import annotations

from wfanalyser.ConfigValidator_wfanalyser import ConfigValidator
from wfanalyser.HeatmapMatrixBuilder_wfanalyser import HeatmapMatrixBuilder
from wfanalyser.OptunaSearchEngine_wfanalyser import OptunaSearchEngine
from wfanalyser.ParameterOptimizer_wfanalyser import ParameterOptimizer
from wfanalyser.RobustSelector_wfanalyser import RobustSelector
from wfanalyser.WFAAcceptanceEvaluator_wfanalyser import WFAAcceptanceEvaluator


def test_wfa_validator_accepts_optuna_blocks(tmp_path) -> None:
    config_path = tmp_path / "wfa_optuna.json"
    config_path.write_text(
        """
        {
          "platform": {"run_type": "test"},
          "wfa_config": {
            "mode": "standard",
            "train_set_percentage": 0.6,
            "test_set_percentage": 0.2,
            "step_size": 30,
            "optimization_objectives": ["sharpe", "calmar"],
            "optimizer": {
              "type": "optuna",
              "mode": "single_objective",
              "sampler": "tpe",
              "multivariate": true,
              "n_trials": 24,
              "n_startup_trials": 8,
              "timeout_seconds": 300,
              "pruner": "hyperband"
            },
            "acceptance": {
              "min_oos_is_ratio": 0.7,
              "min_trade_count": 3
            },
            "robust_selection": {
              "enabled": true,
              "cluster_method": "kmeans",
              "top_n_candidates": 10,
              "pick": "cluster_median"
            },
            "ranking": {
              "profile": "balanced",
              "weights": {
                "sharpe_weight": 1.0,
                "oos_is_ratio_weight": 0.4
              },
              "sort_priority": ["robust_score", "local_plateau_score", "sharpe"]
            }
          },
          "dataloader": {
            "source": "file",
            "start_date": "2024-01-01",
            "file_config": {"file_path": "workspace/datasets/price.csv"}
          },
          "backtester": {
            "strategy_mode": "semantic",
            "strategy_contract_path": "workspace/strategies/strategy.json"
          },
          "metricstracker": {"enable_metrics_analysis": true}
        }
        """,
        encoding="utf-8",
    )
    validator = ConfigValidator()
    assert validator.validate_config(str(config_path)) is True


def test_heatmap_matrix_builder_builds_payload() -> None:
    builder = HeatmapMatrixBuilder()
    payload = builder.build_payload(
        run_id="run_1",
        param_axes=["fast_ma", "slow_ma"],
        rows=[
            {
                "backtest_id": "a",
                "label": "A",
                "semantic_combo": {"fast_ma": 10, "slow_ma": 20},
                "sharpe": 1.2,
                "total_return": 0.3,
            },
            {
                "backtest_id": "b",
                "label": "B",
                "semantic_combo": {"fast_ma": 15, "slow_ma": 20},
                "sharpe": 1.5,
                "total_return": 0.35,
            },
        ],
    )
    assert payload["contract_id"].endswith("parameter-heatmap-payload")
    assert payload["default_x_axis"] == "fast_ma"
    assert payload["default_y_axis"] == "slow_ma"
    assert payload["rows"]
    assert payload["wfa_pack_previews"]["balanced"]["candidate_count"] >= 1
    assert payload["shortlist_rows"][0]["candidate_key"]

    matrix = builder.build_matrix(
        rows=payload["rows"],
        x_axis="fast_ma",
        y_axis="slow_ma",
        objective="sharpe",
    )
    assert matrix["x_values"] == [10, 15]
    assert matrix["y_values"] == [20]
    assert matrix["z"][0][0] == 1.2


def test_heatmap_shortlist_preserves_portfolio_snapshot_metrics() -> None:
    builder = HeatmapMatrixBuilder()
    payload = builder.build_payload(
        run_id="portfolio_run",
        param_axes=["lookback", "sma_period"],
        rows=[
            {
                "backtest_id": "portfolio_a",
                "label": "Portfolio A",
                "semantic_combo": {"lookback": 20, "sma_period": 60},
                "sharpe": 0.9,
                "total_return": 1.4,
                "cagr": 0.12,
                "calmar": 0.6,
                "max_drawdown": -0.2,
                "trade_count": 18,
                "rebalance_count": 18,
                "exposure_time": 0.85,
                "final_equity": 240.0,
            },
            {
                "backtest_id": "portfolio_b",
                "label": "Portfolio B",
                "semantic_combo": {"lookback": 30, "sma_period": 60},
                "sharpe": 0.8,
                "total_return": 1.1,
                "cagr": 0.1,
                "calmar": 0.5,
                "max_drawdown": -0.22,
                "trade_count": 14,
                "rebalance_count": 14,
                "exposure_time": 0.75,
                "final_equity": 210.0,
            },
        ],
    )

    first = next(row for row in payload["shortlist_rows"] if row["backtest_id"] == "portfolio_a")
    assert first["rebalance_count"] == 18
    assert first["cagr"] == 0.12
    assert first["calmar"] == 0.6
    assert first["exposure_time"] == 0.85
    assert first["final_equity"] == 240.0


def test_acceptance_evaluator_computes_robust_score() -> None:
    evaluator = WFAAcceptanceEvaluator({"min_oos_is_ratio": 0.7})
    result = evaluator.evaluate(
        {
            "mean_is_sharpe": 1.0,
            "mean_oos_sharpe": 0.8,
            "oos_std": 0.1,
            "max_drawdown": -0.2,
        }
    )
    assert result.accepted is True
    assert result.robust_score is not None
    assert result.metrics["oos_is_ratio"] == 0.8


def test_acceptance_evaluator_rejects_negative_oos_even_when_ratio_is_positive() -> None:
    evaluator = WFAAcceptanceEvaluator({"min_oos_is_ratio": 0.7})
    result = evaluator.evaluate(
        {
            "mean_is_sharpe": -1.0,
            "mean_oos_sharpe": -0.8,
            "oos_std": 0.1,
            "max_drawdown": -0.2,
        }
    )
    assert result.accepted is False
    assert "oos_sharpe_not_positive" in result.reasons


def test_robust_selector_clusters_candidates() -> None:
    selector = RobustSelector(random_seed=7)
    summary = selector.cluster_candidates(
        [
            {"label": "A", "params": {"fast_ma": 10, "slow_ma": 20}, "mean_oos_sharpe": 0.8, "robust_score": 1.0},
            {"label": "B", "params": {"fast_ma": 11, "slow_ma": 20}, "mean_oos_sharpe": 0.82, "robust_score": 1.1},
            {"label": "C", "params": {"fast_ma": 40, "slow_ma": 80}, "mean_oos_sharpe": 0.5, "robust_score": 0.6},
        ]
    )
    assert summary["clusters"]
    assert summary["representatives"]


def test_optuna_search_engine_runs_tpe_study(tmp_path) -> None:
    engine = OptunaSearchEngine(
        {
            "mode": "single_objective",
            "sampler": "tpe",
            "multivariate": True,
            "n_trials": 8,
            "n_startup_trials": 3,
            "random_seed": 42,
            "pruner": "none",
        },
        storage_dir=tmp_path,
    )
    payload = engine.optimize(
        study_name="fixture",
        search_space=[
            {"name": "fast_ma", "type": "int", "low": 5, "high": 20},
            {"name": "risk_pct", "type": "float", "low": 0.5, "high": 2.0},
        ],
        objective_fn=lambda params, trial: -abs(params["fast_ma"] - 11) - abs(params["risk_pct"] - 1.2),
    )
    assert payload["completed_trials"] >= 1
    assert "best_params" in payload


def test_parameter_optimizer_builds_optuna_search_space_from_start_end_range() -> None:
    optimizer = ParameterOptimizer.__new__(ParameterOptimizer)
    optimizer.logger = None
    optimizer._load_parameter_domains = lambda: {
        "entry_ma": {"type": "range", "start": 10, "end": 50, "step": 10},
        "risk_pct": {"type": "range", "start": 0.5, "end": 2.0, "step": 0.5},
        "regime": {"type": "set", "values": [25, 30]},
        "fixed_only": {"type": "fixed", "value": 200},
    }

    fields = optimizer._build_optuna_search_space()
    by_name = {field.name: field for field in fields}

    assert by_name["entry_ma"].field_type == "int"
    assert by_name["entry_ma"].low == 10
    assert by_name["entry_ma"].high == 50
    assert by_name["risk_pct"].field_type == "float"
    assert by_name["risk_pct"].low == 0.5
    assert by_name["risk_pct"].high == 2.0
    assert by_name["regime"].field_type == "categorical"
    assert "fixed_only" not in by_name
