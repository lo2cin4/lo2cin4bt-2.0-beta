import importlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytestmark = pytest.mark.regression


def _market_data():
    dates = pd.date_range("2023-01-02", periods=90, freq="B")
    close = pd.DataFrame(
        {
            "AAA": [100.0 + idx * 0.4 for idx in range(len(dates))],
            "BBB": [130.0 - idx * 0.15 + max(0, idx - 45) * 0.8 for idx in range(len(dates))],
        },
        index=dates,
    )
    return {"close": close}


def test_unified_portfolio_wfa_exports_selected_optimum_per_window():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    strategy_config = {
        "strategy_id": "unified_wfa_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "parameter_domains": {"lookback": {"type": "range", "start": 2, "end": 4, "step": 2}},
        "computed_fields": [
            {
                "name": "momentum",
                "op": "indicator.momentum",
                "source": "close",
                "period": {"param_ref": "lookback"},
            }
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }
    wfa_config = {
        "windowing": {"train_size": 35, "test_size": 10, "step_size": 20},
        "optimizer": {"objectives": ["sharpe"]},
    }

    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data=_market_data(),
        strategy_config=strategy_config,
        wfa_config=wfa_config,
    ).run()

    assert result.metadata["workflow"] == "walk_forward_analysis"
    assert result.metadata["row_contract"] == "selected_optimum_per_window"
    assert result.metadata["candidate_count"] == 2
    assert result.metadata["candidate_budget_policy"] == "full_grid"
    assert result.metadata["candidate_budget_method"] == "full_grid"
    assert result.metadata["candidate_budget_seed"] is None
    assert result.selected_optimum["wfa_row_type"].unique().tolist() == ["selected_optimum"]
    assert result.selected_optimum.groupby(["window_id", "objective"]).size().max() == 1
    assert set(result.selected_optimum["candidate_count"].unique().tolist()) == {2}
    assert set(result.selected_optimum["candidate_budget_policy"].unique().tolist()) == {"full_grid"}
    assert set(result.selected_optimum["candidate_budget_method"].unique().tolist()) == {"full_grid"}
    assert result.selected_optimum["candidate_budget_seed"].isna().all()
    assert {"accepted", "review_status", "acceptance_reasons", "oos_is_ratio"}.issubset(
        result.selected_optimum.columns
    )
    assert "oos_portfolio_json" in result.selected_optimum.columns
    assert {
        "is_risk_gate_event_count",
        "oos_risk_gate_event_count",
        "oos_risk_gate_summary_json",
    }.issubset(result.selected_optimum.columns)
    assert set(result.selected_optimum["oos_risk_gate_event_count"].unique().tolist()) == {0}
    portfolio_snapshot = json.loads(result.selected_optimum["oos_portfolio_json"].iloc[0])
    assert portfolio_snapshot["asset_count"] == 2
    assert portfolio_snapshot["allocation"]
    assert portfolio_snapshot["contribution"]
    assert portfolio_snapshot["risk_gate_event_count"] == 0
    assert set(result.candidate_diagnostics["wfa_row_type"].unique().tolist()) == {
        "candidate_diagnostic"
    }
    assert result.window_backtests
    first_window_backtest = result.window_backtests[0]
    assert first_window_backtest["backtest_id"].startswith("wfa_window_001_sharpe_")
    assert first_window_backtest["oos_result"].strategy_id == first_window_backtest["backtest_id"]
    assert first_window_backtest["oos_result"].config["metadata"]["source_workflow"] == "walk_forward_analysis"
    assert first_window_backtest["oos_result"].config["metadata"]["wfa_window_id"] == 1


def test_unified_portfolio_wfa_candidate_budget_is_reported():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    strategy_config = {
        "strategy_id": "budget_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "parameter_domains": {"lookback": [2, 4, 6, 8, 10]},
        "computed_fields": [
            {
                "name": "momentum",
                "op": "indicator.momentum",
                "source": "close",
                "period": {"param_ref": "lookback"},
            }
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data=_market_data(),
        strategy_config=strategy_config,
        wfa_config={
            "windowing": {"train_size": 35, "test_size": 10, "step_size": 20},
            "optimizer": {"objectives": ["sharpe"], "n_trials": 2, "random_seed": 7},
        },
    ).run()

    assert result.metadata["candidate_count"] == 2
    assert result.metadata["total_candidate_count"] == 5
    assert result.metadata["candidate_budget"] == 2
    assert result.metadata["candidate_budget_applied"] is True
    assert result.metadata["candidate_budget_policy"] == "seeded_random_sample"
    assert result.metadata["candidate_budget_method"] == "seeded_random_sample"
    assert result.metadata["candidate_budget_seed"] == 7
    assert set(result.selected_optimum["candidate_count"].unique().tolist()) == {2}
    assert set(result.selected_optimum["total_candidate_count"].unique().tolist()) == {5}
    assert result.selected_optimum["candidate_budget_applied"].unique().tolist() == [True]
    assert set(result.selected_optimum["candidate_budget_policy"].unique().tolist()) == {"seeded_random_sample"}
    assert set(result.selected_optimum["candidate_budget_method"].unique().tolist()) == {"seeded_random_sample"}
    assert set(result.selected_optimum["candidate_budget_seed"].unique().tolist()) == {7}
    assert "sampled 2/5 candidates" in result.selected_optimum["selection_evidence"].iloc[0]


def test_unified_portfolio_wfa_sampled_single_candidate_remains_wfa_and_preserves_zero_seed():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    strategy_config = {
        "strategy_id": "budget_one_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "parameter_domains": {"lookback": [2, 4, 6]},
        "computed_fields": [
            {
                "name": "momentum",
                "op": "indicator.momentum",
                "source": "close",
                "period": {"param_ref": "lookback"},
            }
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data=_market_data(),
        strategy_config=strategy_config,
        wfa_config={
            "windowing": {"train_size": 35, "test_size": 10, "step_size": 20},
            "optimizer": {"objectives": ["sharpe"], "max_candidates": 1, "random_seed": 0},
        },
    ).run()

    assert result.metadata["workflow"] == "walk_forward_analysis"
    assert result.metadata["candidate_count"] == 1
    assert result.metadata["total_candidate_count"] == 3
    assert result.metadata["candidate_budget_policy"] == "seeded_random_sample"
    assert result.metadata["candidate_budget_seed"] == 0
    assert result.selected_optimum["workflow"].unique().tolist() == ["walk_forward_analysis"]
    assert result.selected_optimum["candidate_budget_seed"].unique().tolist() == [0]


def test_unified_portfolio_wfa_full_grid_budget_policy_is_reported():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    strategy_config = {
        "strategy_id": "full_grid_budget_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "parameter_domains": {"lookback": [2, 4]},
        "computed_fields": [
            {
                "name": "momentum",
                "op": "indicator.momentum",
                "source": "close",
                "period": {"param_ref": "lookback"},
            }
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data=_market_data(),
        strategy_config=strategy_config,
        wfa_config={"windowing": {"train_size": 35, "test_size": 10, "step_size": 20}},
    ).run()

    assert result.metadata["candidate_budget_policy"] == "full_grid"
    assert result.metadata["candidate_budget_method"] == "full_grid"
    assert result.metadata["candidate_budget_seed"] is None
    assert result.selected_optimum["candidate_budget_policy"].unique().tolist() == ["full_grid"]
    assert result.selected_optimum["candidate_budget_method"].unique().tolist() == ["full_grid"]


def test_unified_portfolio_wfa_manual_ratio_windowing_is_reported():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    strategy_config = {
        "strategy_id": "ratio_window_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "parameter_domains": {"lookback": [2, 4]},
        "computed_fields": [
            {
                "name": "momentum",
                "op": "indicator.momentum",
                "source": "close",
                "period": {"param_ref": "lookback"},
            }
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data=_market_data(),
        strategy_config=strategy_config,
        wfa_config={
            "windowing": {
                "size_mode": "ratio",
                "train_ratio": 0.5,
                "test_ratio": 0.2,
                "step_size": 7,
            },
            "optimizer": {"objectives": ["sharpe"]},
        },
    ).run()

    assert result.metadata["windowing"]["size_mode"] == "manual_ratio"
    assert result.metadata["windowing"]["sizing_source"] == "input_ratios"
    assert result.metadata["windowing"]["effective_train_size"] == 45
    assert result.metadata["windowing"]["effective_test_size"] == 18
    assert result.metadata["windowing"]["effective_step_size"] == 7


def test_unified_portfolio_wfa_auto_windowing_uses_parameter_domain_lookback():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    dates = pd.date_range("2022-01-03", periods=120, freq="B")
    close = pd.DataFrame(
        {
            "AAA": [100.0 + idx * 0.3 for idx in range(len(dates))],
            "BBB": [100.0 + idx * 0.1 for idx in range(len(dates))],
        },
        index=dates,
    )
    strategy_config = {
        "strategy_id": "auto_window_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "parameter_domains": {"lookback": {"type": "range", "start": 10, "end": 30, "step": 10}},
        "computed_fields": [
            {
                "name": "momentum",
                "op": "indicator.momentum",
                "source": "close",
                "period": {"param_ref": "lookback"},
            }
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data={"close": close},
        strategy_config=strategy_config,
        wfa_config={"windowing": {"size_mode": "auto", "target_window_count": 3}},
    ).run()

    assert result.metadata["windowing"]["size_mode"] == "auto"
    assert result.metadata["windowing"]["strategy_max_lookback"] == 30
    assert result.metadata["windowing"]["effective_train_size"] >= 60
    assert result.metadata["windowing"]["auto_indicators"]["min_train_size"] >= 60


def test_unified_portfolio_wfa_filters_low_viability_is_candidates_before_ranking():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    strategy_config = {
        "strategy_id": "viability_filter_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "parameter_domains": {"lookback": [2, 80]},
        "computed_fields": [
            {
                "name": "momentum",
                "op": "indicator.momentum",
                "source": "close",
                "period": {"param_ref": "lookback"},
            }
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data=_market_data(),
        strategy_config=strategy_config,
        wfa_config={
            "windowing": {"train_size": 35, "test_size": 10, "step_size": 20},
            "optimizer": {
                "objectives": ["sharpe"],
                "selection_constraints": {
                    "enabled": True,
                    "max_lookback_fraction_of_train": 0.5,
                },
            },
        },
    ).run()

    assert result.metadata["selection_constraints"]["enabled"] is True
    assert set(result.selected_optimum["semantic_combo"].unique().tolist()) == {"lookback=2"}
    assert set(result.selected_optimum["selection_pool_count"].unique().tolist()) == {1}
    rejected = result.candidate_diagnostics[
        result.candidate_diagnostics["semantic_combo"].eq("lookback=80")
    ]
    assert not rejected.empty
    assert rejected["candidate_viability_pass"].eq(False).all()
    assert rejected["candidate_viability_reasons"].str.contains("lookback_fraction").all()


def test_unified_portfolio_wfa_rejects_selection_constraint_fallback_by_default():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    strategy_config = {
        "strategy_id": "fallback_honesty_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "parameter_domains": {"lookback": [80]},
        "computed_fields": [
            {
                "name": "momentum",
                "op": "indicator.momentum",
                "source": "close",
                "period": {"param_ref": "lookback"},
            }
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data=_market_data(),
        strategy_config=strategy_config,
        wfa_config={
            "windowing": {"train_size": 35, "test_size": 10, "step_size": 20},
            "optimizer": {
                "objectives": ["sharpe"],
                "selection_constraints": {
                    "enabled": True,
                    "max_lookback_fraction_of_train": 0.5,
                },
            },
            "acceptance": {"min_oos_sharpe": -1.0, "min_oos_is_ratio": 0.0},
        },
    ).run()

    row = result.selected_optimum.iloc[0]
    assert bool(row["selection_constraints_fallback"]) is True
    assert bool(row["candidate_viability_pass"]) is False
    assert bool(row["accepted"]) is False
    assert row["review_status"] == "Review"
    assert "selection_constraints_fallback" in row["acceptance_reasons"]


def test_unified_portfolio_wfa_bool_strings_do_not_enable_constraints_or_fallback_acceptance():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    strategy_config = {
        "strategy_id": "bool_string_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "parameter_domains": {"lookback": [80]},
        "computed_fields": [
            {
                "name": "momentum",
                "op": "indicator.momentum",
                "source": "close",
                "period": {"param_ref": "lookback"},
            }
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data=_market_data(),
        strategy_config=strategy_config,
        wfa_config={
            "windowing": {"train_size": 35, "test_size": 10, "step_size": 20},
            "optimizer": {
                "objectives": ["sharpe"],
                "selection_constraints": {
                    "enabled": "false",
                    "max_lookback_fraction_of_train": 0.5,
                },
            },
            "acceptance": {"allow_selection_constraints_fallback_acceptance": "false"},
        },
    ).run()

    row = result.selected_optimum.iloc[0]
    assert result.metadata["selection_constraints"]["enabled"] is False
    assert bool(row["selection_constraints_fallback"]) is False


def test_unified_portfolio_wfa_allows_selection_constraint_fallback_only_with_opt_in():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    strategy_config = {
        "strategy_id": "fallback_opt_in_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "parameter_domains": {"lookback": [80]},
        "computed_fields": [
            {
                "name": "momentum",
                "op": "indicator.momentum",
                "source": "close",
                "period": {"param_ref": "lookback"},
            }
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data=_market_data(),
        strategy_config=strategy_config,
        wfa_config={
            "windowing": {"train_size": 35, "test_size": 10, "step_size": 20},
            "optimizer": {
                "objectives": ["sharpe"],
                "selection_constraints": {
                    "enabled": True,
                    "max_lookback_fraction_of_train": 0.5,
                },
            },
            "acceptance": {
                "min_oos_sharpe": -1.0,
                "min_oos_is_ratio": 0.0,
                "allow_selection_constraints_fallback_acceptance": True,
            },
        },
    ).run()

    row = result.selected_optimum.iloc[0]
    assert bool(row["selection_constraints_fallback"]) is True
    assert bool(row["accepted"]) is True
    assert row["review_status"] == "Pass"
    assert "selection_constraints_fallback explicitly allowed" in row["acceptance_reasons"]


def test_unified_portfolio_wfa_carries_risk_gate_counts():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    strategy_config = {
        "strategy_id": "risk_gate_wfa_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "parameter_domains": {"lookback": [2, 4]},
        "computed_fields": [
            {
                "name": "momentum",
                "op": "indicator.momentum",
                "source": "close",
                "period": {"param_ref": "lookback"},
            }
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum",
            "rank_order": "desc",
            "top_n": 2,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {"gates": {"max_positions": 1, "gate_action": "block_new_orders"}},
    }

    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data=_market_data(),
        strategy_config=strategy_config,
        wfa_config={"windowing": {"train_size": 35, "test_size": 10, "step_size": 20}},
    ).run()

    assert (result.selected_optimum["is_risk_gate_event_count"] > 0).any()
    assert (result.selected_optimum["oos_risk_gate_event_count"] > 0).any()
    snapshot = json.loads(result.selected_optimum["oos_portfolio_json"].iloc[0])
    assert snapshot["risk_gate_event_count"] > 0
    assert snapshot["risk_gate_summary"]["event_count"] == snapshot["risk_gate_event_count"]


def test_unified_portfolio_wfa_treats_fixed_policy_as_rolling_validation():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    strategy_config = {
        "strategy_id": "fixed_policy_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "allocation": {"method": "fixed_weights", "weights": {"AAA": 0.5, "BBB": 0.5}},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }
    wfa_config = {
        "windowing": {"train_size": 30, "test_size": 10, "step_size": 25},
        "optimizer": {"objectives": ["calmar"]},
    }

    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data=_market_data(),
        strategy_config=strategy_config,
        wfa_config=wfa_config,
    ).run()

    assert result.metadata["workflow"] == "rolling_validation"
    assert result.metadata["candidate_count"] == 1
    assert result.selected_optimum["workflow"].unique().tolist() == ["rolling_validation"]
    assert result.selected_optimum["semantic_combo"].unique().tolist() == ["fixed_policy"]


def test_unified_portfolio_wfa_can_optimize_single_asset_signal_state_strategy():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    dates = pd.date_range("2023-01-02", periods=80, freq="B")
    close = pd.DataFrame(
        {"QQQ": [100.0 + idx * 0.2 + (idx % 11) * 0.4 for idx in range(len(dates))]},
        index=dates,
    )
    strategy_config = {
        "strategy_id": "single_signal_wfa_probe",
        "universe": {"symbols": ["QQQ"]},
        "parameter_domains": {"ma_period": [2, 4]},
        "computed_fields": [
            {
                "name": "entry_ma",
                "op": "indicator.sma",
                "source": "close",
                "period": {"param_ref": "ma_period"},
            }
        ],
        "signals": {
            "entry": {"field": "close", "op": "gt", "right_field": "entry_ma"},
            "exit": {"field": "close", "op": "lt", "right_field": "entry_ma"},
            "target_weight": 1.0,
        },
        "allocation": {"method": "signal_state"},
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }
    wfa_config = {
        "windowing": {"train_size": 30, "test_size": 10, "step_size": 20},
        "optimizer": {"objectives": ["sharpe"]},
    }

    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data={"close": close},
        strategy_config=strategy_config,
        wfa_config=wfa_config,
    ).run()

    assert result.metadata["workflow"] == "walk_forward_analysis"
    assert result.metadata["candidate_count"] == 2
    assert set(result.selected_optimum["semantic_combo"].tolist()).issubset(
        {"ma_period=2", "ma_period=4"}
    )
    assert result.selected_optimum["wfa_row_type"].unique().tolist() == ["selected_optimum"]


def test_unified_portfolio_wfa_exporter_separates_selected_and_diagnostics(tmp_path):
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    exporter_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFAExporter_wfanalyser")
    strategy_config = {
        "strategy_id": "export_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "parameter_domains": {"lookback": [2, 4]},
        "computed_fields": [
            {
                "name": "momentum",
                "op": "indicator.momentum",
                "source": "close",
                "period": {"param_ref": "lookback"},
            }
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }
    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data=_market_data(),
        strategy_config=strategy_config,
        wfa_config={"windowing": {"train_size": 35, "test_size": 10, "step_size": 20}},
    ).run()

    paths = exporter_mod.UnifiedPortfolioWFAExporter(
        result=result,
        output_dir=tmp_path,
        run_id="wfa_unified_probe",
    ).export()

    assert any(path.endswith("_selected_optimum.parquet") for path in paths)
    assert any(path.endswith("_candidate_diagnostics.parquet") for path in paths)
    metadata_path = next(path for path in paths if path.endswith("_metadata.json"))
    metadata = exporter_mod.load_unified_wfa_metadata(metadata_path)
    assert metadata["row_contract"] == "selected_optimum_per_window"
    assert metadata["legacy_grid_detected"] is False


def test_unified_portfolio_wfa_requires_positive_oos_for_acceptance():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    dates = pd.date_range("2024-01-02", periods=45, freq="B")
    close = pd.DataFrame(
        {"AAA": [100.0 + idx for idx in range(25)] + [125.0 - idx * 2 for idx in range(20)]},
        index=dates,
    )
    strategy_config = {
        "strategy_id": "negative_oos_probe",
        "universe": {"symbols": ["AAA"]},
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "allocation": {"method": "fixed_weights", "weights": {"AAA": 1.0}},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }
    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data={"close": close},
        strategy_config=strategy_config,
        wfa_config={
            "windowing": {"train_size": 20, "test_size": 10, "step_size": 20},
            "optimizer": {"objectives": ["sharpe"]},
            "acceptance": {"min_oos_sharpe": 0.0, "require_positive_oos": True},
        },
    ).run()

    assert bool(result.selected_optimum["accepted"].iloc[0]) is False
    assert result.selected_optimum["review_status"].iloc[0] == "Review"


def test_walk_forward_engine_routes_multi_asset_config_to_unified_runner(tmp_path):
    config_mod = importlib.import_module("wfanalyser.ConfigLoader_wfanalyser")
    engine_mod = importlib.import_module("wfanalyser.WalkForwardEngine_wfanalyser")
    dates = pd.date_range("2023-01-02", periods=70, freq="B")
    close = pd.DataFrame(
        {
            "Time": dates,
            "AAA": [100.0 + idx * 0.3 for idx in range(len(dates))],
            "BBB": [120.0 - idx * 0.1 + max(0, idx - 35) * 0.7 for idx in range(len(dates))],
        }
    )
    close_path = tmp_path / "close.csv"
    close.to_csv(close_path, index=False)
    output_dir = tmp_path / "wfa_outputs"
    config_payload = {
        "wfa_config": {
            "engine": "unified_portfolio",
            "run_id": "managed_unified_probe",
            "windowing": {"train_size": 30, "test_size": 10, "step_size": 20},
            "optimization_objectives": ["sharpe"],
            "outputs": {"output_dir": str(output_dir), "candidate_diagnostics": True},
        },
        "dataloader": {"source": "multi_asset"},
        "backtester": {
            "strategy_mode": "multi_asset_portfolio",
            "market_data": {"close": {"path": str(close_path), "time_column": "Time"}},
            "portfolio_config": {
                "strategy_id": "managed_unified_wfa_probe",
                "universe": {"symbols": ["AAA", "BBB"]},
                "parameter_domains": {"lookback": [2, 4]},
                "computed_fields": [
                    {
                        "name": "momentum",
                        "op": "indicator.momentum",
                        "source": "close",
                        "period": {"param_ref": "lookback"},
                    }
                ],
                "rebalance": {"trigger": {"op": "calendar.every_session"}},
                "selection": {
                    "eligible": {"field": "close", "op": "gt", "value": 0},
                    "rank_by": "momentum",
                    "rank_order": "desc",
                    "top_n": 1,
                },
                "allocation": {"method": "equal_weight", "position_limit": 1.0},
                "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
            },
        },
        "metricstracker": {},
    }
    config_path = tmp_path / "wfa_config.json"
    config_path.write_text("{}", encoding="utf-8")
    config_data = config_mod.WFAConfigData(config_payload, str(config_path))

    result = engine_mod.WalkForwardEngine(config_data).run()

    assert result["contract_audit"]["runtime"] == "unified_portfolio_wfa"
    assert result["metadata"]["row_contract"] == "selected_optimum_per_window"
    assert result["selected_optimum"]["wfa_row_type"].unique().tolist() == ["selected_optimum"]
    assert any(path.endswith("_selected_optimum.parquet") for path in result["exported_files"])


def test_wfa_legacy_shell_preserves_unified_engine_flag():
    config_mod = importlib.import_module("backtester.StrategyRunConfig_backtester")
    loader_mod = importlib.import_module("wfanalyser.ConfigLoader_wfanalyser")

    normalized = config_mod.normalize_wfa_run_config(
        {
            "schema_version": "wfa_run",
            "engine": "unified_portfolio",
            "strategy_config_path": "workspace/runs/backtest_fixture.json",
            "windowing": {"mode": "rolling", "target_window_count": 3},
            "optimizer": {"objectives": ["sharpe"]},
            "acceptance": {},
            "outputs": {"selected_optimum": True},
        }
    )

    legacy = loader_mod.ConfigLoader._wfa_config_from_wfa_run(normalized)

    assert normalized["engine"] == "unified_portfolio"
    assert legacy["engine"] == "unified_portfolio"
