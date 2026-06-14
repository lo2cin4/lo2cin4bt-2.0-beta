import importlib
import copy
import sys
from pathlib import Path

import pandas as pd
import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _price_frames():
    dates = pd.date_range("2024-01-02", periods=45, freq="B")
    close = pd.DataFrame(
        {
            "AAA": [100 + idx * 0.2 for idx in range(len(dates))],
            "BBB": [100 + idx * 0.8 for idx in range(len(dates))],
            "CCC": [120 - idx * 0.1 for idx in range(len(dates))],
        },
        index=dates,
    )
    volume = pd.DataFrame(1_000_000, index=dates, columns=close.columns)
    return {"close": close, "open": close.copy(), "volume": volume}


def _run_python_portfolio(frames, config):
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    return mod.MultiAssetPortfolioEngineBacktester(frames, config).run()


def _assert_rust_replay_matches_python(frames, result, *, cost_rate=0.0):
    bridge = importlib.import_module("backtester.RustCoreBridge_backtester")
    if not bridge.rust_core_available():
        pytest.skip("Rust core is unavailable")

    payload = bridge.portfolio_result_to_accounting_payload(
        close_frame=frames["close"],
        equity_curve=result.equity_curve,
        cost_rate=cost_rate,
    )
    rust_summary = bridge.run_accounting_via_cli(payload, timeout=60)
    rust_events = rust_summary["events"]
    python_equity = result.equity_curve.reset_index(drop=True)

    assert len(rust_events) == len(python_equity)
    assert rust_summary["final_equity"] == pytest.approx(float(python_equity["Equity_value"].iloc[-1]), rel=1e-9)
    assert rust_summary["active_rebalances"] == int((python_equity["Turnover"] > 1e-12).sum())
    for idx, event in enumerate(rust_events):
        py_row = python_equity.iloc[idx]
        assert event["equity_after_trade"] == pytest.approx(float(py_row["Equity_value"]), rel=1e-9, abs=1e-9)
        assert event["turnover"] == pytest.approx(float(py_row["Turnover"]), rel=1e-9, abs=1e-9)
        assert event["cash_weight"] == pytest.approx(float(py_row["Cash_weight"]), rel=1e-9, abs=1e-9)
        assert event["gross_exposure"] == pytest.approx(float(py_row["Gross_exposure"]), rel=1e-9, abs=1e-9)


def test_rust_accounting_replays_daily_rotation_python_result():
    frames = _price_frames()
    config = {
        "strategy_id": "daily_momentum_top1",
        "universe": {"symbols": ["AAA", "BBB", "CCC"]},
        "computed_fields": [
            {"name": "momentum_2", "op": "indicator.momentum", "source": "close", "period": 2}
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum_2",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = _run_python_portfolio(frames, config)

    _assert_rust_replay_matches_python(frames, result)


def test_rust_accounting_replays_fixed_weight_drift_rebalance_python_result():
    dates = pd.date_range("2023-11-15", periods=310, freq="B")
    close = pd.DataFrame(
        {
            "AAA": [100 + idx * 0.1 for idx in range(len(dates))],
            "BBB": [80 + idx * 0.05 for idx in range(len(dates))],
        },
        index=dates,
    )
    frames = {"close": close}
    config = {
        "strategy_id": "annual_fixed_weights",
        "universe": {"symbols": ["AAA", "BBB"]},
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.year_start"}},
        "allocation": {
            "method": "fixed_weights",
            "weights": {"AAA": 0.6, "BBB": 0.4},
        },
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = _run_python_portfolio(frames, config)

    _assert_rust_replay_matches_python(frames, result)


def test_rust_validation_report_marks_matching_portfolio_result():
    frames = _price_frames()
    config = {
        "strategy_id": "monthly_top3_capped",
        "universe": {"symbols": ["AAA", "BBB", "CCC"]},
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "close",
            "rank_order": "desc",
            "top_n": 3,
        },
        "allocation": {"method": "equal_weight", "position_limit": 0.2},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }
    result = _run_python_portfolio(frames, config)
    bridge = importlib.import_module("backtester.RustCoreBridge_backtester")
    if not bridge.rust_core_available():
        pytest.skip("Rust core is unavailable")

    report = bridge.validate_portfolio_result_with_rust(
        close_frame=frames["close"],
        equity_curve=result.equity_curve,
        cost_rate=0.0,
    )

    assert report["status"] == "matched"
    assert report["mismatch_count"] == 0
    assert report["final_equity_rust"] == pytest.approx(float(result.equity_curve["Equity_value"].iloc[-1]))


def test_multi_asset_engine_can_attach_optional_rust_validation_report():
    bridge = importlib.import_module("backtester.RustCoreBridge_backtester")
    if not bridge.rust_core_available():
        pytest.skip("Rust core is unavailable")
    frames = _price_frames()
    config = {
        "strategy_id": "rust_validated_daily_momentum_top1",
        "universe": {"symbols": ["AAA", "BBB", "CCC"]},
        "computed_fields": [
            {"name": "momentum_2", "op": "indicator.momentum", "source": "close", "period": 2}
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum_2",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "rust_validation": {"enabled": True},
    }

    result = _run_python_portfolio(frames, config)

    rust_report = result.validation_report["rust_accounting_validation"]
    assert rust_report["status"] == "matched"
    assert rust_report["mismatch_count"] == 0


def test_multi_asset_engine_rust_accounting_backend_matches_python_path():
    bridge = importlib.import_module("backtester.RustCoreBridge_backtester")
    if not bridge.rust_core_available():
        pytest.skip("Rust core is unavailable")
    frames = _price_frames()
    config = {
        "strategy_id": "rust_backend_daily_momentum_top1",
        "universe": {"symbols": ["AAA", "BBB", "CCC"]},
        "computed_fields": [
            {"name": "momentum_2", "op": "indicator.momentum", "source": "close", "period": 2}
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum_2",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }
    rust_config = copy.deepcopy(config)
    rust_config["fill_model"]["accounting_backend"] = "rust"

    python_result = _run_python_portfolio(frames, config)
    rust_result = _run_python_portfolio(frames, rust_config)

    assert rust_result.validation_report["accounting_backend"] == "rust"
    assert rust_result.equity_curve["Equity_value"].iloc[-1] == pytest.approx(
        python_result.equity_curve["Equity_value"].iloc[-1],
        rel=1e-9,
    )
    assert rust_result.equity_curve["Turnover"].tolist() == pytest.approx(
        python_result.equity_curve["Turnover"].tolist(),
        rel=1e-9,
        abs=1e-9,
    )
    assert rust_result.rebalance_audit["Turnover"].tolist() == pytest.approx(
        python_result.rebalance_audit["Turnover"].tolist(),
        rel=1e-9,
        abs=1e-9,
    )


def test_multi_asset_engine_rust_accounting_backend_matches_fixed_weight_path():
    bridge = importlib.import_module("backtester.RustCoreBridge_backtester")
    if not bridge.rust_core_available():
        pytest.skip("Rust core is unavailable")
    dates = pd.date_range("2023-11-15", periods=310, freq="B")
    close = pd.DataFrame(
        {
            "AAA": [100 + idx * 0.1 for idx in range(len(dates))],
            "BBB": [80 + idx * 0.05 for idx in range(len(dates))],
        },
        index=dates,
    )
    frames = {"close": close}
    config = {
        "strategy_id": "rust_backend_annual_fixed_weights",
        "universe": {"symbols": ["AAA", "BBB"]},
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.year_start"}},
        "allocation": {
            "method": "fixed_weights",
            "weights": {"AAA": 0.6, "BBB": 0.4},
        },
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }
    rust_config = copy.deepcopy(config)
    rust_config["fill_model"]["accounting_backend"] = "rust"

    python_result = _run_python_portfolio(frames, config)
    rust_result = _run_python_portfolio(frames, rust_config)

    assert rust_result.equity_curve["Equity_value"].iloc[-1] == pytest.approx(
        python_result.equity_curve["Equity_value"].iloc[-1],
        rel=1e-9,
    )
    assert rust_result.equity_curve["Turnover"].tolist() == pytest.approx(
        python_result.equity_curve["Turnover"].tolist(),
        rel=1e-9,
        abs=1e-9,
    )
