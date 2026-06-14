import importlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _engine_mod():
    return importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")


def _exporter_mod():
    return importlib.import_module("backtester.MultiAssetPortfolioExporter_backtester")


def _close_frame():
    dates = pd.date_range("2024-01-02", periods=8, freq="B")
    close = pd.DataFrame(
        {
            "AAA": [100, 101, 102, 103, 104, 105, 106, 107],
            "BBB": [100, 103, 106, 109, 112, 115, 118, 121],
            "CCC": [100, 102, 104, 106, 108, 110, 112, 114],
        },
        index=dates,
        dtype=float,
    )
    return close


def _daily_rank_config():
    return {
        "strategy_id": "risk_gate_daily_rank_probe",
        "universe": {"symbols": ["AAA", "BBB", "CCC"]},
        "computed_fields": [{"name": "momentum_1", "op": "indicator.momentum", "source": "close", "period": 1}],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum_1",
            "rank_order": "desc",
            "top_n": 3,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }


def _target_weight_config(symbols=None):
    symbols = symbols or ["AAA"]
    return {
        "strategy_id": "risk_gate_target_weight_probe",
        "universe": {"symbols": symbols},
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "allocation": {"method": "target_weight_frame", "frame": "target_weight"},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }


def test_risk_gates_disabled_preserve_existing_result_shape():
    mod = _engine_mod()
    config = _daily_rank_config()

    base = mod.MultiAssetPortfolioEngineBacktester({"close": _close_frame()}, config).run()
    with_empty_risk = mod.MultiAssetPortfolioEngineBacktester(
        {"close": _close_frame()},
        {**config, "risk": {"gates": {}}},
    ).run()

    assert with_empty_risk.risk_gate_events.empty
    assert with_empty_risk.validation_report["risk_gate_summary"]["event_count"] == 0
    assert with_empty_risk.equity_curve["Equity_value"].tolist() == pytest.approx(
        base.equity_curve["Equity_value"].tolist()
    )


def test_max_positions_gate_keeps_largest_selected_targets():
    mod = _engine_mod()
    config = _daily_rank_config()
    config["risk"] = {"gates": {"max_positions": 1}}

    result = mod.MultiAssetPortfolioEngineBacktester({"close": _close_frame()}, config).run()

    assert not result.risk_gate_events.empty
    assert "max_positions" in result.risk_gate_events["Gate"].unique().tolist()
    assert result.rebalance_audit["Selected_count"].max() == 1
    weight_cols = [col for col in result.equity_curve.columns if str(col).startswith("Weight_")]
    active_counts = (result.equity_curve[weight_cols].abs() > 1e-12).sum(axis=1)
    assert active_counts.max() <= 1


def test_max_order_size_gate_clamps_target_weight_delta():
    mod = _engine_mod()
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    close = pd.DataFrame({"AAA": [100, 102, 104, 106]}, index=dates, dtype=float)
    target_weight = pd.DataFrame({"AAA": [1.0, 1.0, 1.0, 1.0]}, index=dates, dtype=float)
    config = _target_weight_config(["AAA"])
    config["risk"] = {"gates": {"max_order_size": 0.25}}

    result = mod.MultiAssetPortfolioEngineBacktester(
        {"close": close, "target_weight": target_weight},
        config,
    ).run()

    assert result.equity_curve["Weight_AAA"].iloc[0] == pytest.approx(0.25)
    assert not result.risk_gate_events.empty
    assert result.risk_gate_events["Gate"].iloc[0] == "max_order_size"


def test_max_drawdown_gate_flattens_after_breach():
    mod = _engine_mod()
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    close = pd.DataFrame({"AAA": [100, 70, 60, 65, 66]}, index=dates, dtype=float)
    target_weight = pd.DataFrame({"AAA": [1.0, 1.0, 1.0, 1.0, 1.0]}, index=dates, dtype=float)
    config = _target_weight_config(["AAA"])
    config["risk"] = {"gates": {"max_drawdown": 0.1, "gate_action": "flatten"}}

    result = mod.MultiAssetPortfolioEngineBacktester(
        {"close": close, "target_weight": target_weight},
        config,
    ).run()

    assert "max_drawdown" in result.risk_gate_events["Gate"].unique().tolist()
    assert result.equity_curve["Weight_AAA"].iloc[1] == pytest.approx(0.0)
    assert result.validation_report["risk_gate_summary"]["event_count"] >= 1


def test_risk_gate_exporter_writes_events_and_summary(tmp_path):
    mod = _engine_mod()
    exporter_mod = _exporter_mod()
    config = _daily_rank_config()
    config["risk"] = {"gates": {"max_positions": 1}}

    result = mod.MultiAssetPortfolioEngineBacktester({"close": _close_frame()}, config).run()
    paths = exporter_mod.MultiAssetPortfolioExporterBacktester(
        result=result,
        output_dir=tmp_path,
        run_id="risk_gate_probe",
    ).export()

    assert any(path.endswith("_risk_gate_events.parquet") for path in paths)
    summary_paths = [Path(path) for path in paths if path.endswith("_risk_gate_summary.json")]
    assert len(summary_paths) == 1
    payload = json.loads(summary_paths[0].read_text(encoding="utf-8"))
    assert payload["event_count"] == len(result.risk_gate_events)
    assert "max_positions" in payload["gates_triggered"]
