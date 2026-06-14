import sys
import builtins
import importlib
import json
from datetime import datetime as _real_datetime
from pathlib import Path

import pandas as pd
import pytest
from rich.console import Console


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
sys.modules.pop("autorunner", None)

pytestmark = pytest.mark.golden


def _fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "smoke"


def _load_json_obj(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _unexpected_input(*_args, **_kwargs) -> None:
    raise AssertionError("unexpected interactive input in autorunner path")


def _canonical_trade_rows(df: pd.DataFrame) -> list[dict]:
    # Keep only stable columns; convert Time to YYYY-MM-DD.
    trade_actions = pd.to_numeric(df["Trade_action"], errors="coerce")
    trades = df.loc[trade_actions.isin([1, 4])].copy()
    trades["Time"] = pd.to_datetime(trades["Time"]).dt.strftime("%Y-%m-%d")
    cols = [
        "Time",
        "Trade_action",
        "Position_type",
        "Open_position_price",
        "Close_position_price",
    ]
    trades = trades[cols].reset_index(drop=True)
    # Normalize floats for stable JSON comparison.
    for c in ["Open_position_price", "Close_position_price"]:
        trades[c] = trades[c].astype(float).round(6)
    return trades.to_dict(orient="records")


def test_backtest_runner_semantic_exports_trade_records(tmp_path, monkeypatch) -> None:
    """Runner-to-exporter smoke for the public semantic NodeIR path."""
    monkeypatch.setattr(builtins, "input", _unexpected_input)
    monkeypatch.setattr(Console, "input", _unexpected_input)
    runner_mod = importlib.import_module("autorunner.BacktestRunner_autorunner")
    runner = runner_mod.BacktestRunnerAutorunner()
    strategy_path = (
        _REPO_ROOT
        / "verification"
        / "fixtures"
        / "backtester"
        / "strategy-mini-price-timer.json"
    )
    data = pd.DataFrame(
        {
            "Time": pd.date_range("2024-01-17", periods=8, freq="D"),
            "Open": [100, 101, 102, 103, 104, 105, 106, 107],
            "High": [101, 102, 103, 104, 105, 106, 107, 108],
            "Low": [99, 100, 101, 102, 103, 104, 105, 106],
            "Close": [100, 101, 102, 103, 104, 105, 106, 107],
            "Volume": [1000] * 8,
            "X": [100, 101, 102, 103, 104, 105, 106, 107],
        }
    )
    config = {
        "dataloader": {"frequency": "1D", "source": "file"},
        "backtester": {
            "strategy_mode": "semantic",
            "strategy_contract_path": str(strategy_path),
            "engine_mode": "auto",
            "selected_predictor": "X",
            "trading_params": {
                "transaction_cost": 0.0,
                "slippage": 0.0,
                "trade_delay": 1,
                "trade_price": "open",
                "execution_backend": "python_numba",
            },
            "export_config": {
                "export_csv": False,
                "export_excel": False,
                "output_dir": str(tmp_path),
            },
        },
        "metricstracker": {"enable_metrics_analysis": False},
    }

    result = runner.run_backtest(data, config)

    assert result is not None
    assert result["success"] is True
    parquet_files = sorted(tmp_path.glob("*.parquet"))
    assert parquet_files
    exported = pd.read_parquet(parquet_files[-1])
    assert len(exported) == 8
    assert set(exported["strategy_mode"].dropna().unique()) == {"semantic"}
    assert (exported["Trade_action"] != 0).sum() >= 2


def test_exporter_full_snapshot_parquet_csv_excel(tmp_path, monkeypatch) -> None:
    """
    Golden snapshot for exporter outputs.

    Legacy vector engines were removed; this test now feeds a deterministic
    native record fixture directly into the exporter.
    """
    monkeypatch.setattr(builtins, "input", _unexpected_input)
    monkeypatch.setattr(Console, "input", _unexpected_input)
    expected = _load_json_obj(_fixtures_dir() / "expected_trades_ma1_ma4.json")
    records = pd.DataFrame(expected)
    records["Time"] = pd.to_datetime(records["Time"])
    records["Trading_instrument"] = "TEST"
    records["Backtest_id"] = "native_export_fixture"
    result = {
        "Backtest_id": "native_export_fixture",
        "params": {"strategy_mode": "semantic"},
        "records": records,
        "error": None,
    }
    exporter_mod = importlib.import_module("backtester.TradeRecordExporter_backtester")
    exporter = exporter_mod.TradeRecordExporter_backtester(
        trade_records=pd.DataFrame(),
        frequency="1D",
        results=[result],
        data=records,
        Backtest_id="",
        predictor_file_name=None,
        predictor_column="X",
        symbol="TEST",
    )
    exporter.output_dir = str(tmp_path)
    Path(exporter.output_dir).mkdir(parents=True, exist_ok=True)
    exporter.export_to_parquet()
    exporter.export_to_csv()
    exporter.export_to_excel()

    parquet_files = sorted(tmp_path.glob("*.parquet"))
    csv_files = sorted(tmp_path.glob("*.csv"))
    xlsx_files = sorted(tmp_path.glob("*.xlsx"))
    assert len(parquet_files) == 1
    assert len(csv_files) == 1
    assert len(xlsx_files) == 1

    parquet_df = pd.read_parquet(parquet_files[0])
    assert "Trade_action" in parquet_df.columns

    csv_df = pd.read_csv(csv_files[0])
    assert _canonical_trade_rows(csv_df) == expected

    excel_df = pd.read_excel(xlsx_files[0])
    assert _canonical_trade_rows(excel_df) == expected


def test_exporter_uses_semantic_filename_for_csv_and_excel(tmp_path) -> None:
    date_str = _real_datetime.now().strftime("%Y%m%d")
    records = pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "Trade_action": [1, 4],
            "Position_type": ["long", "close_long"],
            "Open_position_price": [100.0, 0.0],
            "Close_position_price": [0.0, 110.0],
            "Trading_instrument": ["SPY", "SPY"],
            "Backtest_id": ["ab12cd34ef56", "ab12cd34ef56"],
            "Predictor_1_name": ["feature.mmfi.close", "feature.mmfi.close"],
            "Predictor_1_value": [10.0, 11.0],
            "Predictor_2_name": ["feature.vix.close", "feature.vix.close"],
            "Predictor_2_value": [35.0, 36.0],
        }
    )
    result = {
        "Backtest_id": "ab12cd34ef56",
        "params": {
            "strategy_mode": "semantic",
            "semantic_run_label": "spy_mmfi_vix_250d_reset",
            "semantic_fields": ["feature.mmfi.close", "feature.vix.close"],
            "predictor": "feature.mmfi.close",
        },
        "records": records,
        "error": None,
    }
    exporter_mod = importlib.import_module("backtester.TradeRecordExporter_backtester")
    exporter = exporter_mod.TradeRecordExporter_backtester(
        trade_records=pd.DataFrame(),
        frequency="1d",
        results=[result],
        data=records,
        Backtest_id="",
        predictor_file_name=None,
        predictor_column="X",
        symbol="SPY",
    )
    exporter.output_dir = str(tmp_path)
    Path(exporter.output_dir).mkdir(parents=True, exist_ok=True)

    exporter.export_to_parquet()
    exporter.export_to_csv()
    exporter.export_to_excel()

    parquet_files = sorted(tmp_path.glob("*.parquet"))
    csv_files = sorted(tmp_path.glob("*.csv"))
    xlsx_files = sorted(tmp_path.glob("*.xlsx"))
    assert len(parquet_files) == 1
    assert len(csv_files) == 1
    assert len(xlsx_files) == 1
    assert parquet_files[0].name == f"{date_str}_1d_semantic_SPY_spy_mmfi_vix_250d_reset_ab12cd34.parquet"
    assert csv_files[0].name == f"{date_str}_1d_semantic_SPY_spy_mmfi_vix_250d_reset_ab12cd34.csv"
    assert xlsx_files[0].name == f"{date_str}_1d_semantic_SPY_spy_mmfi_vix_250d_reset_ab12cd34.xlsx"


def test_exporter_writes_machine_readable_audit_sidecars(tmp_path) -> None:
    date_str = _real_datetime.now().strftime("%Y%m%d")
    records = pd.DataFrame(
        {
            "Time": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "Trade_action": [1, 4],
            "Position_type": ["long", "close_long"],
            "Open_position_price": [100.0, 0.0],
            "Close_position_price": [0.0, 110.0],
            "Trading_instrument": ["SPY", "SPY"],
            "Backtest_id": ["ab12cd34ef56", "ab12cd34ef56"],
        }
    )
    runtime_data = records.copy()
    runtime_data["feature.mmfi.close"] = [10.0, 11.0]
    runtime_data["feature.vix.close"] = [35.0, 34.0]
    runtime_data["__audit__feature_mmfi_close__source_id"] = ["mmfi_daily", "mmfi_daily"]
    runtime_data["__audit__feature_mmfi_close__source_uri"] = ["mmfi.csv", "mmfi.csv"]
    runtime_data["__audit__feature_mmfi_close__source_time"] = pd.to_datetime(["2024-01-01", "2024-01-02"])
    runtime_data["__audit__feature_mmfi_close__join_mode"] = ["left", "left"]
    runtime_data["__audit__feature_mmfi_close__value_origin"] = ["exact", "exact"]
    runtime_data["__audit__feature_mmfi_close__age_bars"] = [0, 0]
    runtime_data["__audit__feature_mmfi_close__was_filled"] = [False, False]
    runtime_data["__audit__feature_mmfi_close__lag_applied"] = [0, 0]
    runtime_data["__audit__feature_mmfi_close__stale_flag"] = [False, False]
    runtime_data["__audit__feature_vix_close__source_id"] = ["vix_daily", "vix_daily"]
    runtime_data["__audit__feature_vix_close__source_uri"] = ["vix.csv", "vix.csv"]
    runtime_data["__audit__feature_vix_close__source_time"] = pd.to_datetime(["2024-01-01", "2024-01-02"])
    runtime_data["__audit__feature_vix_close__join_mode"] = ["left", "left"]
    runtime_data["__audit__feature_vix_close__value_origin"] = ["exact", "exact"]
    runtime_data["__audit__feature_vix_close__age_bars"] = [0, 0]
    runtime_data["__audit__feature_vix_close__was_filled"] = [False, False]
    runtime_data["__audit__feature_vix_close__lag_applied"] = [0, 0]
    runtime_data["__audit__feature_vix_close__stale_flag"] = [False, False]
    result = {
        "Backtest_id": "ab12cd34ef56",
        "params": {
            "strategy_mode": "semantic",
            "semantic_run_label": "spy_mmfi_vix_250d_reset",
            "semantic_fields": ["feature.mmfi.close", "feature.vix.close"],
            "predictor": "feature.mmfi.close",
            "execution_plan_hash": "planhash123",
            "feature_contract_path": "workspace/features/feature.json",
            "feature_contract_hash": "featurehash456",
            "source_audit_id": "audit123abc",
        },
        "records": records,
        "error": None,
    }
    exporter_mod = importlib.import_module("backtester.TradeRecordExporter_backtester")
    exporter = exporter_mod.TradeRecordExporter_backtester(
        trade_records=pd.DataFrame(),
        frequency="1d",
        results=[result],
        data=runtime_data,
        Backtest_id="",
        predictor_file_name=None,
        predictor_column="X",
        symbol="SPY",
    )
    exporter.output_dir = str(tmp_path)
    Path(exporter.output_dir).mkdir(parents=True, exist_ok=True)

    exporter.export_to_parquet()

    base = tmp_path / f"{date_str}_1d_semantic_SPY_spy_mmfi_vix_250d_reset_ab12cd34"
    metadata_path = base.with_name(base.name + "_metadata.json")
    audit_json_path = base.with_name(base.name + "_audit.json")
    audit_parquet_path = base.with_name(base.name + "_audit.parquet")
    assert metadata_path.exists()
    assert audit_json_path.exists()
    assert audit_parquet_path.exists()

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["source_audit_id"] == "audit123abc"
    assert metadata["feature_contract_path"] == "workspace/features/feature.json"
    assert metadata["audit_parquet_compression"] == "zstd"
    audit_json = json.loads(audit_json_path.read_text(encoding="utf-8"))
    assert audit_json["audit_rows_inline"] is True
    assert audit_json["audit_row_chunks"] == []
    audit_df = pd.read_parquet(audit_parquet_path)
    assert set(audit_df["feature_field"].unique()) == {"feature.mmfi.close", "feature.vix.close"}


def test_exporter_chunks_large_audit_json_and_audit_reader_can_reload(tmp_path) -> None:
    records = pd.DataFrame(
        {
            "Time": pd.date_range("2024-01-01", periods=1205, freq="D"),
            "Trade_action": [1] + [0] * 1203 + [4],
            "Position_type": ["long"] + ["hold"] * 1203 + ["close_long"],
            "Open_position_price": [100.0] * 1205,
            "Close_position_price": [0.0] * 1204 + [110.0],
            "Trading_instrument": ["SPY"] * 1205,
            "Backtest_id": ["ab12cd34ef56"] * 1205,
        }
    )
    runtime_data = records.copy()
    runtime_data["feature.mmfi.close"] = list(range(1205))
    runtime_data["__audit__feature_mmfi_close__source_id"] = ["mmfi_daily"] * 1205
    runtime_data["__audit__feature_mmfi_close__source_uri"] = ["mmfi.csv"] * 1205
    runtime_data["__audit__feature_mmfi_close__source_time"] = records["Time"]
    runtime_data["__audit__feature_mmfi_close__join_mode"] = ["left"] * 1205
    runtime_data["__audit__feature_mmfi_close__value_origin"] = ["exact"] * 1205
    runtime_data["__audit__feature_mmfi_close__age_bars"] = [0] * 1205
    runtime_data["__audit__feature_mmfi_close__was_filled"] = [False] * 1205
    runtime_data["__audit__feature_mmfi_close__lag_applied"] = [0] * 1205
    runtime_data["__audit__feature_mmfi_close__stale_flag"] = [False] * 1205
    result = {
        "Backtest_id": "ab12cd34ef56",
        "params": {
            "strategy_mode": "semantic",
            "semantic_run_label": "spy_mmfi_only",
            "semantic_fields": ["feature.mmfi.close"],
            "predictor": "feature.mmfi.close",
            "execution_plan_hash": "planhash123",
            "feature_contract_path": "workspace/features/feature.json",
            "feature_contract_hash": "featurehash456",
            "source_audit_id": "audit123abc",
        },
        "records": records,
        "error": None,
    }
    exporter_mod = importlib.import_module("backtester.TradeRecordExporter_backtester")
    reader_mod = importlib.import_module("backtester.AuditReader_backtester")
    exporter = exporter_mod.TradeRecordExporter_backtester(
        trade_records=pd.DataFrame(),
        frequency="1d",
        results=[result],
        data=runtime_data,
        Backtest_id="",
        predictor_file_name=None,
        predictor_column="X",
        symbol="SPY",
    )
    exporter.output_dir = str(tmp_path)
    Path(exporter.output_dir).mkdir(parents=True, exist_ok=True)

    exporter.export_to_parquet()

    parquet_path = next(path for path in tmp_path.glob("*.parquet") if not path.stem.endswith("_audit"))
    audit_json_path = parquet_path.with_name(parquet_path.stem + "_audit.json")
    audit_manifest = json.loads(audit_json_path.read_text(encoding="utf-8"))
    assert audit_manifest["audit_rows_inline"] is False
    assert audit_manifest["audit_rows"] == []
    assert len(audit_manifest["audit_row_chunks"]) >= 1

    reader = reader_mod.AuditReaderBacktester()
    audit_frame = reader.load_audit_frame(parquet_path)
    assert len(audit_frame) == 1205
    assert set(audit_frame["feature_field"].unique()) == {"feature.mmfi.close"}
