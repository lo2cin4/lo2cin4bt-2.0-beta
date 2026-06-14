import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

QQQ_SMA_EXAMPLE = "backtester/contracts/strategy/examples/strategy-run-qqq-yfinance-daily-sma-cross-matrix-example.json"
BTC_MONTHLY_NTH_WEEKDAY_EXAMPLE = "backtester/contracts/strategy/examples/strategy-run-btcusdt-binance-monthly-nth-weekday-same-session-matrix-example.json"
VOO_GLD_ROTATION_EXAMPLE = "backtester/contracts/strategy/examples/strategy-run-voo-gld-yfinance-daily-momentum90-sma250-rotation-example.json"
ANNUAL_FIXED_ETF_EXAMPLE = "backtester/contracts/strategy/examples/strategy-run-vti-avuv-vxus-sgol-dbmf-yfinance-yearly-rebalance-example.json"
QQQ_SMA_WFA_EXAMPLE = "backtester/contracts/strategy/examples/wfa-run-qqq-yfinance-daily-sma-cross-example.json"
VOO_GLD_ROTATION_WFA_EXAMPLE = "backtester/contracts/strategy/examples/wfa-run-voo-gld-yfinance-daily-momentum90-sma250-rotation-example.json"
BTC_MONTHLY_NTH_WEEKDAY_WFA_EXAMPLE = "backtester/contracts/strategy/examples/wfa-run-btcusdt-binance-monthly-nth-weekday-same-session-example.json"
BTCUSDT_PUBLIC_DUAL_MA_EXAMPLE = "backtester/contracts/strategy/examples/strategy-run-btcusdt-binance-daily-dual-ma-example.json"


def _load(path: str):
    return json.loads((_REPO_ROOT / path).read_text(encoding="utf-8-sig"))


def test_strategy_run_schema_declares_universe_provenance_fields():
    schema = _load("backtester/contracts/strategy/strategy-run.schema.json")
    universe_props = schema["properties"]["universe"]["properties"]

    for key in [
        "universe_policy",
        "survivorship_policy",
        "historical_constituents_path",
        "universe_constituents_path",
        "as_of_date",
        "delisted_policy",
        "point_in_time_constituents",
        "historical_constituents_hash",
        "universe_constituents_hash",
    ]:
        assert key in universe_props


def test_strategy_and_wfa_schemas_reject_unknown_top_level_and_path_escape():
    mod = __import__("backtester.StrategyRunConfig_backtester", fromlist=["dummy"])
    strategy_schema = _load("backtester/contracts/strategy/strategy-run.schema.json")
    wfa_schema = _load("backtester/contracts/strategy/wfa-run.schema.json")

    strategy = _load(QQQ_SMA_EXAMPLE)
    strategy["typo_field"] = True
    assert list(Draft202012Validator(strategy_schema).iter_errors(strategy))

    wfa = _load(QQQ_SMA_WFA_EXAMPLE)
    wfa["strategy_config_path"] = "../../outside.json"
    assert list(Draft202012Validator(wfa_schema).iter_errors(wfa))
    with pytest.raises(mod.StrategyRunConfigError, match="parent-directory"):
        mod.normalize_wfa_run_config(wfa)

    wfa = _load(QQQ_SMA_WFA_EXAMPLE)
    wfa["strategy_config_path"] = "D:/outside.json"
    assert list(Draft202012Validator(wfa_schema).iter_errors(wfa))
    with pytest.raises(mod.StrategyRunConfigError, match="repo-relative"):
        mod.normalize_wfa_run_config(wfa)

    wfa = _load(QQQ_SMA_WFA_EXAMPLE)
    wfa["typo_field"] = True
    assert list(Draft202012Validator(wfa_schema).iter_errors(wfa))


def test_strategy_run_schema_rejects_planned_modes_and_unknown_fill_model_values():
    strategy_schema = _load("backtester/contracts/strategy/strategy-run.schema.json")
    validator = Draft202012Validator(strategy_schema)

    planned = _load(QQQ_SMA_EXAMPLE)
    planned["platform"]["strategy_mode_id"] = "multi_asset_trigger_selection"
    assert list(validator.iter_errors(planned))

    bad_fill = _load(QQQ_SMA_EXAMPLE)
    bad_fill["fill_model"]["timing"] = "magic_fill"
    bad_fill["fill_model"]["price"] = "midpoint"
    assert list(validator.iter_errors(bad_fill))


def test_strategy_run_schema_accepts_native_bar_offset_fill_model():
    strategy_schema = _load("backtester/contracts/strategy/strategy-run.schema.json")
    strategy = _load(QQQ_SMA_EXAMPLE)
    strategy["fill_model"] = {
        "timing": "bar_offset",
        "entry_price": "close",
        "entry_delay_bars": 0,
        "exit_price": "open",
        "exit_delay_bars": 1,
        "cost": {"transaction_cost": 0.0, "slippage": 0.0},
    }

    Draft202012Validator(strategy_schema).validate(strategy)


def test_strategy_run_requires_explicit_fill_model_cost():
    mod = __import__("backtester.StrategyRunConfig_backtester", fromlist=["dummy"])
    strategy_schema = _load("backtester/contracts/strategy/strategy-run.schema.json")
    validator = Draft202012Validator(strategy_schema)
    strategy = _load(QQQ_SMA_EXAMPLE)

    strategy["fill_model"].pop("cost", None)

    assert list(validator.iter_errors(strategy))
    with pytest.raises(mod.StrategyRunConfigError, match="fill_model\\.cost"):
        mod.validate_strategy_run_config(strategy)


def test_strategy_run_enforces_workflow_parameter_shape():
    mod = __import__("backtester.StrategyRunConfig_backtester", fromlist=["dummy"])
    strategy = _load(QQQ_SMA_EXAMPLE)
    strategy["platform"]["workflow_id"] = "parameter_matrix"
    strategy["parameter_domains"] = {
        "short_ma": {"type": "range", "start": 20, "end": 20, "step": 10}
    }

    with pytest.raises(mod.StrategyRunConfigError, match="at least 2 combinations"):
        mod.validate_strategy_run_config(strategy)

    strategy = _load(QQQ_SMA_EXAMPLE)
    strategy["platform"]["workflow_id"] = "single_backtest"
    strategy["parameter_domains"] = {"short_ma": {"values": [20, 30]}}

    with pytest.raises(mod.StrategyRunConfigError, match="single_backtest must not carry"):
        mod.validate_strategy_run_config(strategy)


def test_strategy_and_wfa_examples_validate_against_public_schemas():
    strategy_schema = _load("backtester/contracts/strategy/strategy-run.schema.json")
    wfa_schema = _load("backtester/contracts/strategy/wfa-run.schema.json")

    for example in [
        QQQ_SMA_EXAMPLE,
        BTC_MONTHLY_NTH_WEEKDAY_EXAMPLE,
        VOO_GLD_ROTATION_EXAMPLE,
        ANNUAL_FIXED_ETF_EXAMPLE,
        BTCUSDT_PUBLIC_DUAL_MA_EXAMPLE,
    ]:
        Draft202012Validator(strategy_schema).validate(_load(example))

    for example in [
        QQQ_SMA_WFA_EXAMPLE,
        VOO_GLD_ROTATION_WFA_EXAMPLE,
        BTC_MONTHLY_NTH_WEEKDAY_WFA_EXAMPLE,
    ]:
        Draft202012Validator(wfa_schema).validate(_load(example))


def test_strategy_schema_accepts_computed_fields_without_legacy_aliases() -> None:
    strategy_schema = _load("backtester/contracts/strategy/strategy-run.schema.json")
    strategy = _load(ANNUAL_FIXED_ETF_EXAMPLE)
    strategy.pop("features", None)
    strategy["computed_fields"] = [
        {"name": "ema_20", "op": "indicator.ema", "source": "close", "period": 20}
    ]

    Draft202012Validator(strategy_schema).validate(strategy)


def test_strategy_schema_rejects_removed_legacy_indicators_and_execution_aliases() -> None:
    strategy_schema = _load("backtester/contracts/strategy/strategy-run.schema.json")
    strategy = _load(VOO_GLD_ROTATION_EXAMPLE)
    strategy["indicators"] = strategy.pop("computed_fields")
    strategy["execution"] = strategy.pop("fill_model")

    with pytest.raises(Exception):
        Draft202012Validator(strategy_schema).validate(strategy)


def test_normalizes_single_asset_ma_config_to_strategy_run():
    mod = __import__("backtester.StrategyRunConfig_backtester", fromlist=["dummy"])
    path = _REPO_ROOT / QQQ_SMA_EXAMPLE
    normalized = mod.normalize_strategy_run_config(_load(str(path.relative_to(_REPO_ROOT))), source_path=path)

    assert normalized["schema_version"] == "strategy_run"
    assert normalized["platform"]["strategy_mode_id"] == "single_asset_signal"
    assert normalized["platform"]["workflow_id"] == "parameter_matrix"
    assert normalized["universe"]["symbols"] == ["QQQ"]
    assert set(normalized["parameter_domains"]) == {"short_ma", "long_ma"}
    assert normalized["parameter_domains"]["short_ma"] == {"type": "range", "start": 20, "end": 100, "step": 10}
    assert normalized["parameter_domains"]["long_ma"] == {"type": "range", "start": 120, "end": 300, "step": 10}
    assert normalized["signals"]["entry"]["op"] == "crosses_above"
    assert normalized["signals"]["exit"]["op"] == "crosses_below"
    assert normalized["metadata"]["local_research_only"] is True
    assert any("not a profitability claim" in note for note in normalized["metadata"]["notes"])


def test_public_btcusdt_dual_ma_example_is_strategy_run_contract():
    mod = __import__("backtester.StrategyRunConfig_backtester", fromlist=["dummy"])
    path = _REPO_ROOT / BTCUSDT_PUBLIC_DUAL_MA_EXAMPLE
    config = _load(str(path.relative_to(_REPO_ROOT)))
    normalized = mod.normalize_strategy_run_config(config, source_path=path)
    plan = mod.plan_strategy_execution(normalized)

    assert normalized["schema_version"] == "strategy_run"
    assert normalized["platform"]["strategy_mode_id"] == "single_asset_signal"
    assert normalized["platform"]["workflow_id"] == "parameter_matrix"
    assert normalized["data"]["provider"] == "binance"
    assert normalized["data"]["frequency"] == "1D"
    assert normalized["data"]["calendar"] == "CRYPTO_24_7"
    assert normalized["universe"]["symbols"] == ["BTCUSDT"]
    assert normalized["signals"]["entry"]["op"] == "crosses_above"
    assert normalized["signals"]["exit"]["op"] == "crosses_below"
    assert set(normalized["parameter_domains"]) == {"short_ma", "long_ma"}
    assert normalized["parameter_domains"]["short_ma"] == {"type": "range", "start": 10, "end": 90, "step": 5}
    assert normalized["parameter_domains"]["long_ma"] == {"type": "range", "start": 100, "end": 150, "step": 5}
    assert normalized["combo_limits"]["warn_combos"] == 187
    assert normalized["combo_limits"]["hard_cap_combos"] == 200
    assert normalized["fill_model"]["cost"]["transaction_cost"] == 0.001
    assert normalized["fill_model"]["cost"]["slippage"] == 0.0005
    assert normalized["metadata"]["public_example"] is True
    assert normalized["metadata"]["local_research_only"] is True
    assert plan["result_type"] == "single_asset"


def test_normalizes_calendar_matrix_config_and_preserves_same_session_contract():
    mod = __import__("backtester.StrategyRunConfig_backtester", fromlist=["dummy"])
    path = _REPO_ROOT / BTC_MONTHLY_NTH_WEEKDAY_EXAMPLE
    normalized = mod.normalize_strategy_run_config(_load(str(path.relative_to(_REPO_ROOT))), source_path=path)

    assert normalized["platform"]["strategy_mode_id"] == "calendar_event_session"
    assert normalized["platform"]["workflow_id"] == "parameter_matrix"
    assert normalized["universe"]["symbols"] == ["BTCUSDT"]
    assert normalized["parameter_domains"]["month_week"] == {"type": "range", "start": 1, "end": 4, "step": 1}
    assert normalized["parameter_domains"]["weekday"]["values"] == [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    assert normalized["signals"]["entry"]["op"] == "calendar.nth_weekday_of_month"
    assert normalized["signals"]["entry"]["ordinal"] == {"param_ref": "month_week"}
    assert normalized["signals"]["entry"]["weekday"] == {"param_ref": "weekday"}
    assert normalized["signals"]["exit"]["op"] == "session.same_session_close"
    assert normalized["signals"]["side"] == "long"
    assert normalized["allocation"]["target_weight"] == 1.0
    assert normalized["fill_model"]["session_scope"] == "same_session"
    assert normalized["fill_model"]["entry_price"] == "open"
    assert normalized["fill_model"]["exit_price"] == "close"
    assert normalized["risk"]["allow_short"] is False
    assert normalized["metadata"]["local_research_only"] is True
    assert any("not a profitability claim" in note for note in normalized["metadata"]["notes"])


def test_normalizes_multi_asset_portfolio_matrix_config():
    mod = __import__("backtester.StrategyRunConfig_backtester", fromlist=["dummy"])
    path = _REPO_ROOT / VOO_GLD_ROTATION_EXAMPLE
    normalized = mod.normalize_strategy_run_config(_load(str(path.relative_to(_REPO_ROOT))), source_path=path)

    assert normalized["platform"]["strategy_mode_id"] == "multi_asset_portfolio"
    assert normalized["platform"]["workflow_id"] == "single_backtest"
    assert normalized["universe"]["symbols"] == ["VOO", "GLD"]
    assert normalized["parameter_domains"] == {}
    assert normalized["computed_fields"][0]["period"] == 90
    assert normalized["computed_fields"][1]["period"] == 250
    assert normalized["selection"]["eligible"]["right_field"] == "sma_filter"
    assert normalized["selection"]["rank_by"] == "return_momentum"
    assert normalized["data"]["benchmark"]["symbol"] == "SPY"
    assert normalized["metadata"]["local_research_only"] is True
    assert any("not a profitability claim" in note for note in normalized["metadata"]["notes"])


def test_fixed_allocation_without_parameters_plans_as_rolling_validation_for_wfa():
    mod = __import__("backtester.StrategyRunConfig_backtester", fromlist=["dummy"])
    path = _REPO_ROOT / VOO_GLD_ROTATION_EXAMPLE
    normalized = mod.normalize_strategy_run_config(_load(str(path.relative_to(_REPO_ROOT))), source_path=path)
    normalized["platform"]["workflow_id"] = "walk_forward_analysis"
    plan = mod.plan_strategy_execution(normalized)

    assert normalized["parameter_domains"] == {}
    assert plan["is_rolling_validation"] is True
    assert plan["workflow_id"] == "rolling_validation"
    assert plan["execution_backend"] == "vector_hybrid"
    assert plan["accounting_backend"] == "sequential"


def test_annual_fixed_etf_allocation_example_is_strategy_run_contract():
    mod = __import__("backtester.StrategyRunConfig_backtester", fromlist=["dummy"])
    path = _REPO_ROOT / ANNUAL_FIXED_ETF_EXAMPLE
    normalized = mod.normalize_strategy_run_config(_load(str(path.relative_to(_REPO_ROOT))), source_path=path)
    plan = mod.plan_strategy_execution(normalized)

    assert normalized["platform"]["strategy_mode_id"] == "multi_asset_portfolio"
    assert normalized["platform"]["workflow_id"] == "single_backtest"
    assert normalized["data"]["provider"] == "yfinance"
    assert normalized["data"]["calendar"] == "XNYS"
    assert normalized["data"]["start_policy"] == "common_available"
    assert normalized["data"]["benchmark"]["symbol"] == "VTI"
    assert normalized["universe"]["symbols"] == ["VTI", "AVUV", "VXUS", "SGOL", "DBMF"]
    assert normalized["universe"]["universe_policy"] == "static_public_etf_list"
    assert normalized["allocation"]["method"] == "fixed_weights"
    assert normalized["allocation"]["weights"] == {
        "VTI": 0.3,
        "AVUV": 0.1,
        "VXUS": 0.2,
        "SGOL": 0.2,
        "DBMF": 0.2,
    }
    assert sum(normalized["allocation"]["weights"].values()) == pytest.approx(1.0)
    assert normalized["rebalance"]["trigger"]["op"] == "calendar.year_start"
    assert normalized["outputs"]["rebalance_audit"] is True
    assert normalized["outputs"]["holdings"] is True
    assert normalized["outputs"]["asset_contribution"] is True
    assert normalized["metadata"]["local_research_only"] is True
    assert any("not a profitability claim" in note for note in normalized["metadata"]["notes"])
    assert plan["result_type"] == "portfolio"
    assert plan["requires_portfolio_accounting"] is True


def test_execution_planner_uses_vector_hybrid_for_single_and_portfolio():
    mod = __import__("backtester.StrategyRunConfig_backtester", fromlist=["dummy"])
    single_path = _REPO_ROOT / QQQ_SMA_EXAMPLE
    portfolio_path = _REPO_ROOT / VOO_GLD_ROTATION_EXAMPLE

    single_plan = mod.plan_strategy_execution(
        mod.normalize_strategy_run_config(_load(str(single_path.relative_to(_REPO_ROOT))), source_path=single_path)
    )
    portfolio_plan = mod.plan_strategy_execution(
        mod.normalize_strategy_run_config(_load(str(portfolio_path.relative_to(_REPO_ROOT))), source_path=portfolio_path)
    )

    assert single_plan["result_type"] == "single_asset"
    assert portfolio_plan["result_type"] == "portfolio"
    assert single_plan["requires_portfolio_accounting"] is True
    assert portfolio_plan["requires_portfolio_accounting"] is True
    assert single_plan["stages"][-1]["id"] == "portfolio_accounting"
    assert portfolio_plan["stages"][-1]["backend"] == "sequential"


def test_factor_pipeline_is_first_class_contract_stage():
    mod = __import__("backtester.StrategyRunConfig_backtester", fromlist=["dummy"])
    config = {
        "schema_version": "strategy_run",
        "platform": {
            "strategy_mode_id": "multi_asset_portfolio",
            "workflow_id": "parameter_matrix",
        },
        "data": {
            "provider": "local_parquet",
            "frequency": "1D",
            "calendar": "XNYS",
            "benchmark": "SPY",
        },
        "universe": {"symbols": ["AAA", "BBB", "CCC"]},
        "factor_pipeline": {
            "schema_version": "factor_pipeline.v1",
            "data_requirements": {
                "price_fields": ["close", "volume"],
                "fundamental_fields": ["book_value", "market_cap"],
                "classification_fields": ["sector"],
                "point_in_time_required": True,
            },
            "construction": [
                {"name": "value", "family": "value", "op": "factor.book_to_market"},
                {"name": "momentum", "family": "momentum", "op": "factor.price_momentum"},
            ],
            "preprocessing": [
                {"op": "winsorize", "scope": "cross_section"},
                {"op": "standardize", "scope": "cross_section"},
                {"op": "neutralize", "scope": "cross_section", "group_by": ["sector"]},
                {"op": "lag_audit", "scope": "point_in_time"},
            ],
            "composite": {
                "method": "equal_weight",
                "inputs": ["value", "momentum"],
                "output": "factor_score",
            },
            "point_in_time": {
                "known_at_field": "known_at",
                "fail_on_lookahead": True,
            },
            "cache": {
                "enabled": True,
                "namespace": "lo2cin4bt.factor_pipeline",
                "storage": "local_parquet",
            },
            "outputs": {"factor_score_frame": True, "statanalyser": True},
        },
        "computed_fields": [],
        "selection": {
            "eligible": {"field": "factor_score", "op": "gt", "value": -999999},
            "rank_by": "factor_score",
            "rank_order": "desc",
            "top_n": 10,
        },
        "allocation": {"method": "equal_weight", "position_limit": 0.1},
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "fill_model": {
            "entry_price": "close",
            "exit_price": "close",
            "cost": {"transaction_cost": 0.0, "slippage": 0.0},
        },
        "risk": {"max_positions": 10, "max_gross_exposure": 1.0, "long_short": "long_only"},
        "parameter_domains": {
            "value_weight": {"type": "range", "start": 0.0, "end": 1.0, "step": 0.25}
        },
        "outputs": {"equity_curve": True, "asset_contribution": True},
    }

    normalized = mod.normalize_strategy_run_config(config)
    plan = mod.plan_strategy_execution(normalized)

    assert normalized["factor_pipeline"]["schema_version"] == "factor_pipeline.v1"
    assert plan["uses_factor_pipeline"] is True
    assert plan["vector_precompute"] is True
    assert [stage["id"] for stage in plan["stages"][:4]] == [
        "factor_data_preparation",
        "factor_construction",
        "factor_preprocessing",
        "factor_composite",
    ]
    assert plan["stages"][0]["enabled"] is True
    assert plan["stages"][3]["outputs"] == ["factor_score_frame"]


def test_wfa_config_normalizes_to_wfa_run_with_strategy_reference():
    mod = __import__("backtester.StrategyRunConfig_backtester", fromlist=["dummy"])
    cases = [
        (QQQ_SMA_WFA_EXAMPLE, QQQ_SMA_EXAMPLE, 60, "research diagnostics only"),
        (VOO_GLD_ROTATION_WFA_EXAMPLE, VOO_GLD_ROTATION_EXAMPLE, 1, "rolling validation"),
        (BTC_MONTHLY_NTH_WEEKDAY_WFA_EXAMPLE, BTC_MONTHLY_NTH_WEEKDAY_EXAMPLE, 28, "month_week 1-4"),
    ]

    for wfa_path, strategy_path, candidate_limit, expected_note in cases:
        path = _REPO_ROOT / wfa_path
        normalized = mod.normalize_wfa_run_config(_load(str(path.relative_to(_REPO_ROOT))), source_path=path)

        assert normalized["schema_version"] == "wfa_run"
        assert normalized["engine"] == "unified_portfolio_wfa"
        assert normalized["optimizer"]["objectives"] == ["sharpe", "calmar"]
        assert normalized["optimizer"]["candidate_limit"] == candidate_limit
        assert normalized["outputs"]["selected_optimum"] is True
        assert normalized["strategy_config_path"].endswith(Path(strategy_path).name)
        assert normalized["metadata"]["local_research_only"] is True
        assert any(expected_note in note for note in normalized["metadata"]["notes"])


def test_invalid_strategy_mode_fails_fast():
    mod = __import__("backtester.StrategyRunConfig_backtester", fromlist=["dummy"])
    with pytest.raises(mod.StrategyRunConfigError, match="Unknown strategy_mode_id"):
        mod.validate_strategy_run_config(
            {
                "schema_version": "strategy_run",
                "platform": {"strategy_mode_id": "walk_forward_analysis", "workflow_id": "single_backtest"},
                "data": {},
                "universe": {"symbols": ["QQQ"]},
                "computed_fields": [],
                "allocation": {},
                "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
                "risk": {},
                "parameter_domains": {},
                "outputs": {},
            }
        )


def test_autorunner_validator_accepts_strategy_run_primary_config(tmp_path):
    validator_mod = __import__("autorunner.ConfigValidator_autorunner", fromlist=["dummy"])
    config = {
        "schema_version": "strategy_run",
        "platform": {
            "strategy_mode_id": "multi_asset_portfolio",
            "workflow_id": "parameter_matrix",
        },
        "data": {"provider": "local", "frequency": "1D", "benchmark": "SPY"},
        "universe": {"symbols": ["VOO", "GLD"]},
        "computed_fields": [
            {"name": "return_momentum", "op": "indicator.momentum", "source": "close", "period": 20}
        ],
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "return_momentum",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "fill_model": {
            "entry_price": "close",
            "exit_price": "close",
            "cost": {"transaction_cost": 0.0, "slippage": 0.0},
        },
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
        "parameter_domains": {
            "return_lookback": {"type": "range", "start": 10, "end": 20, "step": 10}
        },
        "outputs": {"equity_curve": True, "holdings": True},
    }
    path = tmp_path / "strategy_run.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    validator = validator_mod.ConfigValidator()

    assert validator.validate_config(str(path)) is True


def test_autorunner_loader_accepts_strategy_run_primary_config(tmp_path):
    loader_mod = __import__("autorunner.ConfigLoader_autorunner", fromlist=["dummy"])
    config = {
        "schema_version": "strategy_run",
        "platform": {
            "strategy_mode_id": "multi_asset_portfolio",
            "workflow_id": "parameter_matrix",
        },
        "data": {"provider": "yfinance", "frequency": "1D", "start_date": "2020-01-01"},
        "universe": {"symbols": ["VOO", "GLD"]},
        "computed_fields": [
            {"name": "return_momentum", "op": "indicator.momentum", "source": "close", "period": 20}
        ],
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "return_momentum",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "fill_model": {
            "entry_price": "close",
            "exit_price": "close",
            "cost": {"transaction_cost": 0.0, "slippage": 0.0},
        },
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
        "parameter_domains": {
            "return_lookback": {"type": "range", "start": 10, "end": 20, "step": 10}
        },
        "outputs": {"equity_curve": True, "holdings": True},
    }
    path = tmp_path / "strategy_run.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    loaded = loader_mod.ConfigLoader().load_config(str(path))

    assert loaded is not None
    assert loaded.dataloader_config["source"] == "multi_asset"
    assert loaded.dataloader_config["asset_symbols"] == ["VOO", "GLD"]
    assert loaded.backtester_config["strategy_mode"] == "multi_asset_portfolio"
    assert loaded.backtester_config["strategy_run_config"]["schema_version"] == "strategy_run"


def test_autorunner_loader_uses_internal_market_loader_for_calendar_example():
    loader_mod = __import__("autorunner.ConfigLoader_autorunner", fromlist=["dummy"])
    path = _REPO_ROOT / BTC_MONTHLY_NTH_WEEKDAY_EXAMPLE

    loaded = loader_mod.ConfigLoader().load_config(str(path))

    assert loaded is not None
    assert loaded.dataloader_config["source"] == "multi_asset"
    assert loaded.dataloader_config["asset_symbols"] == ["BTCUSDT"]
    assert loaded.backtester_config["strategy_mode"] == "single_asset_portfolio"
    assert loaded.backtester_config["strategy_run_config"]["data"]["provider"] == "binance"
