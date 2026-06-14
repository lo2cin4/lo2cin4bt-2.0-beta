import importlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.api.service import AppAPIService
from backtester.MultiAssetPortfolioExporter_backtester import MultiAssetPortfolioExporterBacktester

pytestmark = pytest.mark.regression


def _engine_mod():
    return importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")


def _run_portfolio(market_data, config, *, transaction_cost=0.0, slippage=0.0):
    cfg = dict(config)
    execution = dict(cfg.get("fill_model", {}))
    execution["cost"] = {"transaction_cost": transaction_cost, "slippage": slippage}
    cfg["fill_model"] = execution
    return _engine_mod().MultiAssetPortfolioEngineBacktester(market_data, cfg).run()


def _assert_nonzero_cost_contract(market_data, config, *, expected_fast_path=None):
    no_cost = _run_portfolio(market_data, config)
    with_cost = _run_portfolio(
        market_data,
        config,
        transaction_cost=0.001,
        slippage=0.0005,
    )

    if expected_fast_path is not None:
        assert with_cost.validation_report["accounting_fast_path"] == expected_fast_path
    assert with_cost.validation_report["cost_accounting"]["status"] == "valid"
    assert with_cost.validation_report["cost_accounting"]["configured_cost_rate"] == pytest.approx(0.0015)
    assert with_cost.equity_curve["Equity_value"].iloc[-1] < no_cost.equity_curve["Equity_value"].iloc[-1]
    assert with_cost.equity_curve["Trade_cost"].sum() > 0.0
    assert with_cost.validation_report["cost_accounting"]["total_trade_cost"] == pytest.approx(
        with_cost.equity_curve["Trade_cost"].sum()
    )
    assert with_cost.rebalance_audit["Trade_cost"].sum() > 0.0
    assert with_cost.rebalance_trades["Allocated_cost"].sum() > 0.0
    assert with_cost.rebalance_trades.loc[
        with_cost.rebalance_trades["Allocated_cost"] > 0.0,
        "Trade_turnover",
    ].sum() > 0.0


def test_cost_accounting_validation_rejects_zero_trade_cost_with_nonzero_cost_rate():
    dates = pd.date_range("2024-01-01", periods=2, freq="D")
    close = pd.DataFrame({"AAA": [100.0, 100.0]}, index=dates)
    engine = _engine_mod().MultiAssetPortfolioEngineBacktester(
        {"close": close},
        {
            "strategy_id": "invalid_cost_accounting_probe",
            "universe": {"symbols": ["AAA"]},
            "rebalance": {"trigger": {"op": "calendar.every_session"}},
            "selection": {"eligible": {"field": "close", "op": "gt", "value": 0}, "rank_by": "close", "top_n": 1},
            "allocation": {"method": "equal_weight", "position_limit": 1.0},
            "fill_model": {"cost": {"transaction_cost": 0.001, "slippage": 0.0005}},
        },
    )
    report = engine._attach_cost_accounting_validation(  # pylint: disable=protected-access
        validation_report={"status": "valid", "errors": []},
        equity_curve=pd.DataFrame({"Turnover": [1.0, 0.0], "Trade_cost": [0.0, 0.0]}),
        cost_rate=0.0015,
    )

    assert report["status"] == "invalid_contract"
    assert report["cost_accounting"]["status"] == "invalid_cost_accounting"
    assert report["cost_accounting"]["configured_cost_rate"] == pytest.approx(0.0015)
    assert report["cost_accounting"]["active_turnover"] == pytest.approx(1.0)
    assert report["cost_accounting"]["total_trade_cost"] == pytest.approx(0.0)
    assert "nonzero_cost_config_produced_zero_trade_cost" in report["errors"]


def test_cost_accounting_validation_allows_nonzero_cost_when_no_turnover():
    dates = pd.date_range("2024-01-01", periods=2, freq="D")
    close = pd.DataFrame({"AAA": [100.0, 100.0]}, index=dates)
    engine = _engine_mod().MultiAssetPortfolioEngineBacktester(
        {"close": close},
        {
            "strategy_id": "no_turnover_cost_accounting_probe",
            "universe": {"symbols": ["AAA"]},
            "rebalance": {"trigger": {"op": "calendar.every_session"}},
            "selection": {"eligible": {"field": "close", "op": "gt", "value": 0}, "rank_by": "close", "top_n": 1},
            "allocation": {"method": "equal_weight", "position_limit": 1.0},
            "fill_model": {"cost": {"transaction_cost": 0.001, "slippage": 0.0005}},
        },
    )
    report = engine._attach_cost_accounting_validation(  # pylint: disable=protected-access
        validation_report={"status": "valid", "errors": []},
        equity_curve=pd.DataFrame({"Turnover": [0.0, 0.0], "Trade_cost": [0.0, 0.0]}),
        cost_rate=0.0015,
    )

    assert report["status"] == "valid"
    assert report["errors"] == []
    assert report["cost_accounting"]["status"] == "no_turnover"
    assert report["cost_accounting"]["configured_cost_rate"] == pytest.approx(0.0015)
    assert report["cost_accounting"]["active_turnover"] == pytest.approx(0.0)
    assert report["cost_accounting"]["total_trade_cost"] == pytest.approx(0.0)


def test_single_asset_target_weight_fast_path_charges_nonzero_costs():
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    close = pd.DataFrame({"AAA": [100.0] * len(dates)}, index=dates)
    target_weight = pd.DataFrame({"AAA": [1.0, 1.0, 0.0, 0.0, 1.0]}, index=dates)
    config = {
        "strategy_id": "single_asset_cost_probe",
        "universe": {"symbols": ["AAA"]},
        "rebalance": {"trigger": {"op": "signal.change"}},
        "allocation": {"method": "signal_target_weight", "frame": "target_weight"},
    }

    _assert_nonzero_cost_contract(
        {"close": close, "target_weight": target_weight},
        config,
        expected_fast_path="single_asset_target_weight_numpy",
    )


def test_multi_asset_target_weight_fast_path_charges_nonzero_costs():
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    close = pd.DataFrame({"AAA": [100.0] * len(dates), "BBB": [100.0] * len(dates)}, index=dates)
    target_weight = pd.DataFrame(
        {
            "AAA": [0.6, 0.6, 0.2, 0.2, 0.8],
            "BBB": [0.4, 0.4, 0.8, 0.8, 0.2],
        },
        index=dates,
    )
    config = {
        "strategy_id": "multi_asset_target_cost_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "allocation": {"method": "target_weight_frame", "frame": "target_weight"},
    }

    _assert_nonzero_cost_contract(
        {"close": close, "target_weight": target_weight},
        config,
        expected_fast_path="target_weight_numpy",
    )


def test_daily_rank_fast_path_charges_nonzero_costs():
    dates = pd.date_range("2024-01-01", periods=6, freq="D")
    close = pd.DataFrame(
        {
            "AAA": [101.0, 100.0, 101.0, 100.0, 101.0, 100.0],
            "BBB": [100.0, 101.0, 100.0, 101.0, 100.0, 101.0],
        },
        index=dates,
    )
    config = {
        "strategy_id": "daily_rank_cost_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "close",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
    }

    _assert_nonzero_cost_contract(
        {"close": close},
        config,
        expected_fast_path="daily_rank_numpy",
    )


def test_same_session_path_charges_nonzero_entry_and_exit_costs():
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    open_ = pd.DataFrame({"AAA": [100.0, 100.0, 100.0]}, index=dates)
    close = pd.DataFrame({"AAA": [101.0, 101.0, 101.0]}, index=dates)
    target_weight = pd.DataFrame({"AAA": [1.0, 0.0, 1.0]}, index=dates)
    config = {
        "strategy_id": "same_session_cost_probe",
        "universe": {"symbols": ["AAA"]},
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "allocation": {"method": "signal_target_weight", "frame": "target_weight"},
        "fill_model": {"session_scope": "same_session", "entry_price": "open", "exit_price": "close"},
    }

    no_cost = _run_portfolio({"open": open_, "close": close, "target_weight": target_weight}, config)
    with_cost = _run_portfolio(
        {"open": open_, "close": close, "target_weight": target_weight},
        config,
        transaction_cost=0.001,
        slippage=0.0005,
    )

    assert with_cost.validation_report["accounting_backend"] == "same_session"
    assert with_cost.validation_report["cost_accounting"]["status"] == "valid"
    assert with_cost.validation_report["cost_accounting"]["configured_cost_rate"] == pytest.approx(0.0015)
    assert with_cost.equity_curve["Equity_value"].iloc[-1] < no_cost.equity_curve["Equity_value"].iloc[-1]
    assert with_cost.equity_curve["Trade_cost"].sum() > 0.0
    assert with_cost.validation_report["cost_accounting"]["total_trade_cost"] == pytest.approx(
        with_cost.equity_curve["Trade_cost"].sum()
    )
    assert with_cost.rebalance_audit["Trade_cost"].sum() > 0.0
    assert with_cost.rebalance_trades["Allocated_cost"].sum() > 0.0
    assert {"same-session entry", "same-session exit"}.issubset(set(with_cost.rebalance_trades["Reason"]))


def test_unified_portfolio_wfa_snapshot_reports_nonzero_cost_drag():
    runner_mod = importlib.import_module("wfanalyser.UnifiedPortfolioWFARunner_wfanalyser")
    dates = pd.date_range("2023-01-02", periods=80, freq="B")
    close = pd.DataFrame(
        {
            "AAA": [100.0 + idx * 0.3 for idx in range(len(dates))],
            "BBB": [130.0 - idx * 0.1 + max(0, idx - 40) * 0.6 for idx in range(len(dates))],
        },
        index=dates,
    )
    strategy_config = {
        "strategy_id": "wfa_cost_probe",
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
        "fill_model": {"cost": {"transaction_cost": 0.001, "slippage": 0.0005}},
    }

    result = runner_mod.UnifiedPortfolioWFARunner(
        market_data={"close": close},
        strategy_config=strategy_config,
        wfa_config={"windowing": {"train_size": 30, "test_size": 10, "step_size": 20}},
    ).run()

    snapshots = [json.loads(value) for value in result.selected_optimum["oos_portfolio_json"]]
    assert snapshots
    assert all(snapshot["total_trade_cost"] > 0.0 for snapshot in snapshots)
    assert all(snapshot["cost_drag"] > 0.0 for snapshot in snapshots)


def test_app_api_portfolio_overview_and_detail_consume_trade_cost(tmp_path: Path):
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    close = pd.DataFrame({"AAA": [100.0] * len(dates), "BBB": [100.0] * len(dates)}, index=dates)
    target_weight = pd.DataFrame(
        {
            "AAA": [0.6, 0.6, 0.2, 0.2, 0.8],
            "BBB": [0.4, 0.4, 0.8, 0.8, 0.2],
        },
        index=dates,
    )
    config = {
        "strategy_id": "app_cost_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "allocation": {"method": "target_weight_frame", "frame": "target_weight"},
    }
    result = _run_portfolio(
        {"close": close, "target_weight": target_weight},
        config,
        transaction_cost=0.001,
        slippage=0.0005,
    )
    service = AppAPIService(tmp_path)
    run_id = "20260515_cost_probe"
    artifact_dir = service.registry.build_run_paths(run_id)["snapshot_dir"] / "managed_artifacts" / "portfolio"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    exported_paths = [
        Path(path)
        for path in MultiAssetPortfolioExporterBacktester(
            result=result,
            output_dir=artifact_dir,
            run_id=run_id,
        ).export()
    ]
    manifest = []
    for path in exported_paths:
        name = path.name
        if name.endswith("_equity_curve.parquet"):
            artifact_type = "portfolio_equity_curve_parquet"
        elif name.endswith("_holdings.parquet"):
            artifact_type = "portfolio_holdings_parquet"
        elif name.endswith("_rebalance_audit.parquet"):
            artifact_type = "portfolio_rebalance_audit_parquet"
        elif name.endswith("_rebalance_trades.parquet"):
            artifact_type = "portfolio_rebalance_trades_parquet"
        elif name.endswith("_metadata.json"):
            artifact_type = "portfolio_metadata_json"
        else:
            continue
        manifest.append({"artifact_type": artifact_type, "path": str(path), "status": "ready"})

    service.registry.write_registry_entry(
        {
            "run_id": run_id,
            "module": "autorunner",
            "entrypoint": "test",
            "status": "completed",
            "created_at": "2026-05-15T00:00:00",
            "completed_at": "2026-05-15T00:00:01",
            "config_filename": "backtest_20260515_cost_probe.json",
            "strategy_mode": "multi_asset_portfolio",
            "run_type": "test",
        }
    )
    service.registry.write_artifact_manifest(run_id, {"schema_version": "1.0", "artifacts": manifest})

    overview = service.metrics_overview(run_id)
    turnover_summary = overview["portfolio"]["runs"][0]["turnover_summary"]
    assert turnover_summary["total_trade_cost"] > 0.0
    assert turnover_summary["trade_cost_drag"] > 0.0

    detail = service.backtest_detail(run_id, "app_cost_probe")
    assert detail["turnover_summary"]["total_trade_cost"] > 0.0
    assert detail["turnover_summary"]["trade_cost_drag"] > 0.0
    assert detail["asset_contribution_summary"]["estimated_cost_drag"] > 0.0
