import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def test_calendar_materializer_selects_last_friday_of_quarter_months():
    mod = importlib.import_module("backtester.CalendarConditionMaterializer_backtester")
    frame = pd.DataFrame({"Time": pd.date_range("2024-03-01", "2024-06-30", freq="D")})

    mask = mod.CalendarConditionMaterializer(frame).materialize(
        {
            "op": "calendar.last_weekday_of_month",
            "weekday": "friday",
            "months": [3, 6, 9, 12],
        }
    )

    assert frame.loc[mask, "Time"].dt.strftime("%Y-%m-%d").tolist() == [
        "2024-03-29",
        "2024-06-28",
    ]


def test_calendar_materializer_does_not_roll_holiday_nth_weekday_forward():
    mod = importlib.import_module("backtester.CalendarConditionMaterializer_backtester")
    trading_dates = pd.to_datetime(
        [
            "2009-09-01",
            "2009-09-02",
            "2009-09-03",
            "2009-09-04",
            "2009-09-08",
            "2009-09-09",
            "2009-09-10",
            "2009-09-11",
            "2009-09-14",
        ]
    )
    frame = pd.DataFrame({"Time": trading_dates})

    first_monday = mod.CalendarConditionMaterializer(frame).materialize(
        {
            "op": "calendar.nth_weekday_of_month",
            "weekday": "monday",
            "ordinal": 1,
            "months": [9],
        }
    )
    second_monday = mod.CalendarConditionMaterializer(frame).materialize(
        {
            "op": "calendar.nth_weekday_of_month",
            "weekday": "monday",
            "ordinal": 2,
            "months": [9],
        }
    )

    assert frame.loc[first_monday, "Time"].dt.strftime("%Y-%m-%d").tolist() == []
    assert frame.loc[second_monday, "Time"].dt.strftime("%Y-%m-%d").tolist() == [
        "2009-09-14"
    ]


def test_calendar_materializer_does_not_roll_missing_last_weekday_backward():
    mod = importlib.import_module("backtester.CalendarConditionMaterializer_backtester")
    trading_dates = pd.date_range("2024-03-01", "2024-03-28", freq="D")
    frame = pd.DataFrame({"Time": trading_dates})

    mask = mod.CalendarConditionMaterializer(frame).materialize(
        {
            "op": "calendar.last_weekday_of_month",
            "weekday": "friday",
            "months": [3],
        }
    )

    assert frame.loc[mask, "Time"].dt.strftime("%Y-%m-%d").tolist() == []


def test_calendar_event_date_can_adjust_to_previous_or_next_trading_day():
    mod = importlib.import_module("backtester.CalendarConditionMaterializer_backtester")
    frame = pd.DataFrame({"Time": pd.to_datetime(["2024-01-02", "2024-01-04"])})

    previous_mask = mod.CalendarConditionMaterializer(frame).materialize(
        {
            "op": "calendar.event_date",
            "dates": ["2024-01-03"],
            "adjustment_policy": "previous_trading_day",
        }
    )
    next_mask = mod.CalendarConditionMaterializer(frame).materialize(
        {
            "op": "calendar.event_date",
            "dates": ["2024-01-03"],
            "adjustment_policy": "next_trading_day",
        }
    )
    skip_rows = mod.CalendarConditionMaterializer(frame).audit_rows(
        {
            "op": "calendar.event_date",
            "dates": ["2024-01-03"],
        }
    )

    assert frame.loc[previous_mask, "Time"].dt.strftime("%Y-%m-%d").tolist() == ["2024-01-02"]
    assert frame.loc[next_mask, "Time"].dt.strftime("%Y-%m-%d").tolist() == ["2024-01-04"]
    assert skip_rows[0]["triggered"] is False
    assert skip_rows[0]["skip_reason"] == "target_session_missing"


def test_shared_calendar_resolver_supports_rebalance_month_start_events():
    mod = importlib.import_module("utils.calendar_events")
    frame = pd.DataFrame(
        {
            "Time": pd.to_datetime(
                [
                    "2024-01-02",
                    "2024-01-03",
                    "2024-02-01",
                    "2024-02-02",
                    "2024-03-01",
                ]
            )
        }
    )
    resolver = mod.CalendarEventResolver(frame)

    sessions = resolver.trigger_sessions({"op": "calendar.month_start"})
    event_frame = resolver.event_frame(
        {"op": "calendar.month_start"},
        strategy_id="multi_asset.monthly_rotation",
        event_role="rebalance",
    )

    assert [item.strftime("%Y-%m-%d") for item in sessions] == [
        "2024-01-02",
        "2024-02-01",
        "2024-03-01",
    ]
    assert event_frame["audit_row_type"].unique().tolist() == ["calendar_rebalance"]
    assert event_frame["event_role"].unique().tolist() == ["rebalance"]
    assert event_frame["resolved_session_date"].tolist() == [
        "2024-01-02",
        "2024-02-01",
        "2024-03-01",
    ]


def test_calendar_month_end_can_filter_rebalance_months():
    mod = importlib.import_module("utils.calendar_events")
    frame = pd.DataFrame({"Time": pd.to_datetime(["2024-03-28", "2024-04-01", "2024-06-28"])})
    resolver = mod.CalendarEventResolver(frame)

    mask = resolver.materialize({"op": "calendar.month_end", "months": [3, 6]})

    assert frame.loc[mask, "Time"].dt.strftime("%Y-%m-%d").tolist() == [
        "2024-03-28",
        "2024-06-28",
    ]


def test_strategy_validator_accepts_calendar_month_start_rebalance_trigger():
    strategy_mod = importlib.import_module("autorunner.StrategyContractValidator")
    strategy = {
        "schema_version": "strategy_contract",
        "strategy_id": "calendar.month_start_probe",
        "data_context": {
            "primary_instrument": "QQQ",
            "frequency": "1D",
            "timezone": "America/New_York",
            "calendar": "XNYS",
        },
        "parameter_domains": {},
        "execution": {"side": "long", "entry_price": "open", "exit_price": "close"},
        "entry": {"op": "calendar.month_start"},
        "exit": {"op": "timer_bars", "value": 1},
    }

    result = strategy_mod.StrategyContractValidator().validate(strategy)

    assert result.valid, result.errors


def test_calendar_same_session_short_run_emits_entry_and_exit_records(tmp_path):
    executor_mod = importlib.import_module("backtester.NodeIRExecutor_backtester")
    exporter_mod = importlib.import_module("backtester.TradeRecordExporter_backtester")
    metrics_runner_mod = importlib.import_module("autorunner.MetricsRunner_autorunner")
    dates = pd.date_range("2024-03-25", "2024-07-05", freq="D")
    data = pd.DataFrame(
        {
            "Time": dates,
            "Open": np.full(len(dates), 100.0),
            "High": np.full(len(dates), 101.0),
            "Low": np.full(len(dates), 89.0),
            "Close": np.full(len(dates), 90.0),
            "Volume": np.ones(len(dates)),
            "X": np.ones(len(dates)),
        }
    )
    strategy_path = tmp_path / "strategy.json"
    strategy_path.write_text(
        json.dumps(
            {
                "schema_version": "strategy_contract",
                "strategy_id": "calendar.quarterly_last_friday_short",
                "data_context": {
                    "primary_instrument": "QQQ",
                    "frequency": "1D",
                    "timezone": "America/New_York",
                    "calendar": "XNYS",
                },
                "parameter_domains": {},
                "execution": {
                    "side": "short",
                    "entry_price": "open",
                    "exit_price": "close",
                    "session_scope": "same_session",
                    "same_session_exit": True,
                },
                "entry": {
                    "op": "calendar.last_weekday_of_month",
                    "weekday": "friday",
                    "months": [3, 6, 9, 12],
                },
                "exit": {"op": "session.same_session_close"},
                "engine_preferences": {"requested_mode": "auto"},
            }
        ),
        encoding="utf-8",
    )
    feature_path = tmp_path / "feature.json"
    feature_path.write_text(json.dumps({"features": []}), encoding="utf-8")
    plan = {
        "field_catalog": {},
        "feature_dag": {},
        "execution": {
            "side": "short",
            "entry_price": "open",
            "exit_price": "close",
            "session_scope": "same_session",
            "same_session_exit": True,
        },
        "node_ir": {
            "entry": {
                "op": "calendar.last_weekday_of_month",
                "weekday": "friday",
                "months": [3, 6, 9, 12],
            },
            "exit": {"op": "session.same_session_close"},
        },
    }

    results = executor_mod.NodeIRExecutorBacktester(data).run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={"execution_backend": "auto", "transaction_cost": 0.0, "slippage": 0.0},
        predictor_column="X",
        symbol="QQQ",
        backtest_id_prefix="calendar",
    )

    records = results[0]["records"]
    trade_rows = records[records["Trade_action"].isin([1, 4])]
    calendar_audit = results[0]["calendar_signal_audit"]
    assert trade_rows["Trade_action"].tolist() == [1, 4, 1, 4]
    assert trade_rows[trade_rows["Trade_action"] == 4]["Trade_return"].round(6).tolist() == [
        0.1,
        0.1,
    ]
    assert results[0]["params"]["execution_mode"] == "same_session"
    assert calendar_audit["calendar_op"].unique().tolist() == ["calendar.last_weekday_of_month"]
    assert calendar_audit["triggered"].tolist() == [True, True]
    assert calendar_audit["target_date"].tolist() == ["2024-03-29", "2024-06-28"]

    export_dir = tmp_path / "exports"
    exporter = exporter_mod.TradeRecordExporter_backtester(
        trade_records=pd.DataFrame(),
        frequency="1D",
        results=results,
        data=data,
        symbol="QQQ",
        output_dir=str(export_dir),
    )
    exported_paths = exporter.export_to_parquet()
    calendar_exports = [Path(path) for path in exported_paths if "calendar_signal_audit" in Path(path).name]
    assert len(calendar_exports) == 2
    exported_audit = pd.read_parquet([path for path in calendar_exports if path.suffix == ".parquet"][0])
    assert exported_audit["target_date"].tolist() == ["2024-03-29", "2024-06-28"]
    metrics_targets = metrics_runner_mod.MetricsRunnerAutorunner()._collect_target_files(
        exported_paths,
        {"file_selection_mode": "auto"},
    )
    assert len(metrics_targets) == 1
    assert "calendar_signal_audit" not in Path(metrics_targets[0]).name


def test_strategy_validator_and_compiler_accept_calendar_same_session(tmp_path):
    strategy_mod = importlib.import_module("autorunner.StrategyContractValidator")
    compiler_mod = importlib.import_module("autorunner.StrategyCompiler")
    strategy = {
        "schema_version": "strategy_contract",
        "strategy_id": "calendar.quarterly_last_friday_short",
        "data_context": {
            "primary_instrument": "QQQ",
            "frequency": "1D",
            "timezone": "America/New_York",
            "calendar": "XNYS",
        },
        "parameter_domains": {},
        "execution": {
            "side": "short",
            "entry_price": "open",
            "exit_price": "close",
            "session_scope": "same_session",
            "same_session_exit": True,
        },
        "entry": {
            "op": "calendar.last_weekday_of_month",
            "weekday": "friday",
            "months": [3, 6, 9, 12],
        },
        "exit": {"op": "session.same_session_close"},
        "engine_preferences": {"requested_mode": "auto"},
    }
    strategy_path = tmp_path / "strategy.json"
    strategy_path.write_text(json.dumps(strategy), encoding="utf-8")

    validation = strategy_mod.StrategyContractValidator().validate(strategy)
    assert validation.valid is True
    assert validation.summary["resolved_mode"] == "node_ir"

    compiled = compiler_mod.StrategyCompiler().compile_from_paths(
        str(strategy_path),
        None,
        output_dir=str(tmp_path),
    )
    assert compiled.valid is True
    assert compiled.execution_plan["execution"]["session_scope"] == "same_session"
    assert compiled.execution_plan["stateful_flags"]["requires_session_adapter"] is True


def test_calendar_same_session_invalid_price_is_skipped_and_audited(tmp_path):
    executor_mod = importlib.import_module("backtester.NodeIRExecutor_backtester")
    dates = pd.to_datetime(["2024-03-29"])
    data = pd.DataFrame(
        {
            "Time": dates,
            "Open": [np.nan],
            "High": [101.0],
            "Low": [89.0],
            "Close": [90.0],
            "Volume": [1.0],
            "X": [1.0],
        }
    )
    strategy_path = tmp_path / "strategy.json"
    strategy_path.write_text(
        json.dumps(
            {
                "schema_version": "strategy_contract",
                "strategy_id": "calendar.invalid_price",
                "data_context": {"primary_instrument": "QQQ", "frequency": "1D"},
                "parameter_domains": {},
                "execution": {
                    "side": "short",
                    "entry_price": "open",
                    "exit_price": "close",
                    "session_scope": "same_session",
                },
                "entry": {
                    "op": "calendar.last_weekday_of_month",
                    "weekday": "friday",
                    "months": [3],
                },
                "exit": {"op": "session.same_session_close"},
            }
        ),
        encoding="utf-8",
    )
    feature_path = tmp_path / "feature.json"
    feature_path.write_text(json.dumps({"features": []}), encoding="utf-8")
    plan = {
        "field_catalog": {},
        "feature_dag": {},
        "execution": {"side": "short", "entry_price": "open", "exit_price": "close", "session_scope": "same_session"},
        "node_ir": {
            "entry": {"op": "calendar.last_weekday_of_month", "weekday": "friday", "months": [3]},
            "exit": {"op": "session.same_session_close"},
        },
    }

    result = executor_mod.NodeIRExecutorBacktester(data).run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={"transaction_cost": 0.0, "slippage": 0.0},
        predictor_column="X",
        symbol="QQQ",
        backtest_id_prefix="calendar",
    )[0]

    assert result["records"]["Trade_action"].tolist() == [0]
    assert result["records"]["Calendar_execution_status"].tolist() == ["skipped"]
    assert result["calendar_signal_audit"]["execution_status"].tolist() == ["skipped"]
    assert result["calendar_signal_audit"]["execution_skip_reason"].tolist() == [
        "missing_or_invalid_entry_exit_price"
    ]


def test_calendar_node_accepts_weekday_and_ordinal_param_refs(tmp_path):
    executor_mod = importlib.import_module("backtester.NodeIRExecutor_backtester")
    dates = pd.date_range("2024-03-01", "2024-03-31", freq="D")
    data = pd.DataFrame(
        {
            "Time": dates,
            "Open": np.ones(len(dates)),
            "High": np.ones(len(dates)),
            "Low": np.ones(len(dates)),
            "Close": np.ones(len(dates)),
            "Volume": np.ones(len(dates)),
            "X": np.ones(len(dates)),
        }
    )
    strategy_path = tmp_path / "strategy.json"
    strategy_path.write_text(
        json.dumps(
            {
                "schema_version": "strategy_contract",
                "strategy_id": "calendar.param_ref",
                "data_context": {
                    "primary_instrument": "QQQ",
                    "frequency": "1D",
                    "timezone": "America/New_York",
                },
                "parameter_domains": {
                    "weekday": {"type": "set", "values": ["friday"]},
                    "ordinal": {"type": "set", "values": [3]},
                },
                "execution": {"session_scope": "same_session"},
                "entry": {
                    "op": "calendar.nth_weekday_of_month",
                    "weekday": {"param_ref": "weekday"},
                    "ordinal": {"param_ref": "ordinal"},
                    "months": [3],
                },
                "exit": {"op": "session.same_session_close"},
            }
        ),
        encoding="utf-8",
    )
    feature_path = tmp_path / "feature.json"
    feature_path.write_text(json.dumps({"features": []}), encoding="utf-8")
    plan = {
        "field_catalog": {},
        "feature_dag": {},
        "execution": {"session_scope": "same_session"},
        "node_ir": {
            "entry": {
                "op": "calendar.nth_weekday_of_month",
                "weekday": {"param_ref": "weekday"},
                "ordinal": {"param_ref": "ordinal"},
                "months": [3],
            },
            "exit": {"op": "session.same_session_close"},
        },
    }

    result = executor_mod.NodeIRExecutorBacktester(data).run_from_paths(
        strategy_contract_path=str(strategy_path),
        feature_contract_path=str(feature_path),
        execution_plan=plan,
        trading_params={"transaction_cost": 0.0, "slippage": 0.0},
        predictor_column="X",
        symbol="QQQ",
        backtest_id_prefix="calendar",
    )[0]

    exit_rows = result["records"][result["records"]["Trade_action"] == 4]
    assert pd.to_datetime(exit_rows["Time"]).dt.strftime("%Y-%m-%d").tolist() == [
        "2024-03-15"
    ]
