import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def test_multi_asset_engine_accepts_explicit_target_weight_frame():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    close = pd.DataFrame({"QQQ": [100.0, 110.0, 121.0, 121.0, 121.0]}, index=dates)
    target_weight = pd.DataFrame({"QQQ": [1.0, 1.0, 0.0, 0.0, 0.0]}, index=dates)
    config = {
        "strategy_id": "explicit_one_asset_weight_probe",
        "universe": {"symbols": ["QQQ"]},
        "allocation": {"method": "target_weight_frame", "frame": "target_weight"},
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "execution": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0},
    }

    result = mod.MultiAssetPortfolioEngineBacktester(
        {"close": close, "target_weight": target_weight},
        config,
    ).run()

    assert result.equity_curve["Equity_value"].iloc[-1] == pytest.approx(121.0)
    assert result.equity_curve["Weight_QQQ"].iloc[-1] == pytest.approx(0.0)
    assert result.rebalance_trades["Action"].tolist().count("buy") == 1
    assert result.rebalance_trades["Action"].tolist().count("exit") == 1


def test_single_asset_signals_can_run_through_portfolio_accounting_core():
    adapter = importlib.import_module("backtester.SingleAssetPortfolioAdapter_backtester")
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    price_data = pd.DataFrame(
        {
            "Open": [100.0, 109.0, 106.0, 105.0, 119.0],
            "Close": [100.0, 110.0, 105.0, 105.0, 120.0],
        },
        index=dates,
    )
    entry = pd.Series([True, False, False, False, False], index=dates)
    exit_ = pd.Series([False, False, True, False, False], index=dates)

    result = adapter.run_single_asset_signals_as_portfolio(
        price_data=price_data,
        symbol="QQQ",
        entry_signal=entry,
        exit_signal=exit_,
        strategy_id="qqq_signal_unified_probe",
    )

    assert result.strategy_id == "qqq_signal_unified_probe"
    assert result.config["platform"]["strategy_mode_id"] == "single_asset_signal"
    assert result.equity_curve["Equity_value"].iloc[-1] == pytest.approx(105.0)
    assert result.equity_curve["Weight_QQQ"].iloc[-1] == pytest.approx(0.0)
    assert set(result.rebalance_trades["Action"].dropna().unique().tolist()) >= {"buy", "exit"}


def test_signal_target_weight_builder_uses_exit_then_entry_conflict_policy():
    adapter = importlib.import_module("backtester.SingleAssetPortfolioAdapter_backtester")
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    entry = pd.Series([True, False, True], index=dates)
    exit_ = pd.Series([False, True, True], index=dates)

    weights = adapter.build_target_weight_frame_from_signals(
        index=dates,
        symbol="QQQ",
        entry_signal=entry,
        exit_signal=exit_,
    )

    assert weights["QQQ"].tolist() == [1.0, 0.0, 1.0]


def test_autorunner_can_route_single_asset_strategy_to_portfolio_core(tmp_path):
    runner_mod = importlib.import_module("autorunner.BacktestRunner_autorunner")
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    data = pd.DataFrame(
        {
            "Open": [100.0, 109.0, 106.0, 105.0, 119.0],
            "Close": [100.0, 110.0, 105.0, 105.0, 120.0],
            "entry_signal": [True, False, False, False, False],
            "exit_signal": [False, False, True, False, False],
        },
        index=dates,
    )
    config = {
        "dataloader": {
            "source": "yfinance",
            "frequency": "1D",
            "yfinance_config": {"symbol": "QQQ"},
        },
        "backtester": {
            "strategy_mode": "single_asset_portfolio",
            "Backtest_id": "single_unified_probe",
            "entry_signal_column": "entry_signal",
            "exit_signal_column": "exit_signal",
            "export_config": {"output_dir": str(tmp_path), "export_csv": False},
        },
    }

    result = runner_mod.BacktestRunnerAutorunner().run_backtest(data, config)

    assert result["success"] is True
    assert result["strategy_mode"] == "single_asset_portfolio"
    assert result["resolved_engine_mode"] == "unified_vector_hybrid"
    assert result["execution_plan"]["requires_portfolio_accounting"] is True
    assert result["engine_capabilities"]["vector_hybrid"] is True
    assert result["portfolio_result"].equity_curve["Equity_value"].iloc[-1] == pytest.approx(105.0)
    assert any(path.endswith("_metadata.json") for path in result["exported_files"])


def test_multi_asset_engine_can_materialize_single_asset_signal_state():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    close = pd.DataFrame({"QQQ": [10.0, 11.0, 12.0, 11.0, 10.0]}, index=dates)
    config = {
        "strategy_id": "signal_state_single_asset_probe",
        "universe": {"symbols": ["QQQ"]},
        "features": [{"name": "sma_2", "op": "indicator.sma", "source": "close", "period": 2}],
        "signals": {
            "entry": {"field": "close", "op": "gt", "right_field": "sma_2"},
            "exit": {"field": "close", "op": "lt", "right_field": "sma_2"},
            "target_weight": 1.0,
        },
        "allocation": {"method": "signal_state"},
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "execution": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = mod.MultiAssetPortfolioEngineBacktester({"close": close}, config).run()

    assert result.equity_curve["Weight_QQQ"].tolist() == pytest.approx([0.0, 1.0, 1.0, 0.0, 0.0])
    assert result.equity_curve["Equity_value"].iloc[-1] == pytest.approx(100.0)
