import copy
import importlib
import json
import sys
from pathlib import Path

import numpy as np
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


def test_multi_asset_daily_rotation_selects_top_ranked_asset():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
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

    result = mod.MultiAssetPortfolioEngineBacktester(_price_frames(), config).run()

    selected = result.holdings[result.holdings["Selected"]]
    assert "BBB" in selected["Asset"].tail(10).unique().tolist()
    assert result.equity_curve["Equity_value"].iloc[-1] > 100.0
    assert result.rebalance_audit["Selected_count"].max() == 1
    assert result.validation_report["status"] == "valid"
    assert result.validation_report["expected_symbols"] == ["AAA", "BBB", "CCC"]
    assert result.validation_report["missing_symbols"] == []


def test_multi_asset_engine_rejects_combined_features_and_indicators_direct_path():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    frames = _price_frames()
    config = {
        "strategy_id": "direct_mixed_alias_rejection_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "features": [{"name": "legacy_momentum", "op": "indicator.momentum", "source": "close", "period": 2}],
        "computed_fields": [{"name": "new_momentum", "op": "indicator.momentum", "source": "close", "period": 3}],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "new_momentum",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    with pytest.raises(ValueError, match="removed aliases"):
        mod.MultiAssetPortfolioEngineBacktester(frames, config).run()


def test_multi_asset_engine_rejects_inline_feature_operands_direct_path():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    frames = _price_frames()
    config = {
        "strategy_id": "direct_inline_feature_rejection_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {
                "left": {"feature": "indicator.sma", "source": "close", "period": 2},
                "op": "gt",
                "right_field": "close",
            },
            "rank_by": "close",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    with pytest.raises(ValueError, match="inline feature nodes are not part of the public"):
        mod.MultiAssetPortfolioEngineBacktester(frames, config).run()


def test_multi_asset_indicators_can_compose_rsi_and_macd_conditions():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=80, freq="B")
    close = pd.DataFrame(
        {
            "AAA": [100 + idx * 0.9 for idx in range(len(dates))],
            "BBB": [130 - idx * 0.2 for idx in range(len(dates))],
        },
        index=dates,
    )
    config = {
        "strategy_id": "semantic_rsi_macd_composition_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "computed_fields": [
            {"name": "rsi_14", "op": "indicator.rsi", "source": "close", "period": 14},
            {"name": "macd_line", "op": "indicator.macd", "source": "close", "fastperiod": 12, "slowperiod": 26},
            {
                "name": "macd_signal",
                "op": "indicator.macd",
                "source": "close",
                "fastperiod": 12,
                "slowperiod": 26,
                "signalperiod": 9,
                "output": "signal",
            },
        ],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {
                "all": [
                    {"field": "rsi_14", "op": "gt", "value": 50},
                    {"field": "macd_line", "op": "gt", "right_field": "macd_signal"},
                ]
            },
            "rank_by": "rsi_14",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    result = mod.MultiAssetPortfolioEngineBacktester({"close": close, "open": close.copy()}, config).run()

    assert result.validation_report["status"] == "valid"
    assert result.feature_cache["computed"] >= 3
    assert result.rebalance_audit["Selected_count"].max() <= 1
    assert result.equity_curve.empty is False


def test_multi_asset_feature_builder_supports_public_indicator_ops() -> None:
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=6, freq="B")
    close = pd.DataFrame({"AAA": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]}, index=dates)
    high = pd.DataFrame({"AAA": [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]}, index=dates)
    low = pd.DataFrame({"AAA": [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]}, index=dates)
    builder = mod.MultiAssetFeatureBuilder({"close": close, "high": high, "low": low})

    features = builder.build(
        [
            {"name": "sma_3", "op": "indicator.sma", "source": "close", "period": 3},
            {"name": "ema_3", "op": "indicator.ema", "source": "close", "period": 3},
            {"name": "macd_line", "op": "indicator.macd", "source": "close", "fastperiod": 3, "slowperiod": 5},
            {
                "name": "macd_signal",
                "op": "indicator.macd",
                "source": "close",
                "fastperiod": 3,
                "slowperiod": 5,
                "signalperiod": 2,
                "output": "signal",
            },
            {
                "name": "macd_histogram",
                "op": "indicator.macd",
                "source": "close",
                "fastperiod": 3,
                "slowperiod": 5,
                "signalperiod": 2,
                "output": "histogram",
            },
            {"name": "atr_3", "op": "indicator.atr", "period": 3},
            {"name": "z_3", "op": "indicator.zscore", "source": "close", "period": 3},
            {"name": "p50_3", "op": "indicator.percentile", "source": "close", "period": 3, "percentile": 50},
            {"name": "bb_upper_3", "op": "indicator.bollinger", "source": "close", "period": 3, "band": "upper", "stddev": 2},
        ]
    )

    ema_fast = close["AAA"].ewm(span=3, adjust=False, min_periods=3).mean()
    ema_slow = close["AAA"].ewm(span=5, adjust=False, min_periods=5).mean()
    macd_line = ema_fast - ema_slow
    macd_signal = macd_line.ewm(span=2, adjust=False, min_periods=2).mean()
    true_range = pd.Series([1.0, 1.5, 1.5, 1.5, 1.5, 1.5], index=dates)
    atr = [np.nan, np.nan, true_range.iloc[:3].mean()]
    for value in true_range.iloc[3:]:
        atr.append(((atr[-1] * 2.0) + value) / 3.0)

    assert features["sma_3"]["AAA"].iloc[-1] == pytest.approx(5.0)
    assert features["ema_3"]["AAA"].iloc[-1] == pytest.approx(close["AAA"].ewm(span=3, adjust=False, min_periods=3).mean().iloc[-1])
    assert features["macd_line"]["AAA"].iloc[-1] == pytest.approx(macd_line.iloc[-1])
    assert features["macd_signal"]["AAA"].iloc[-1] == pytest.approx(macd_signal.iloc[-1])
    assert features["macd_histogram"]["AAA"].iloc[-1] == pytest.approx((macd_line - macd_signal).iloc[-1])
    assert features["atr_3"]["AAA"].tolist() == pytest.approx(atr, nan_ok=True)
    assert features["z_3"]["AAA"].iloc[-1] == pytest.approx((6.0 - 5.0) / 1.0)
    assert features["p50_3"]["AAA"].iloc[-1] == pytest.approx(5.0)
    assert features["bb_upper_3"]["AAA"].iloc[-1] == pytest.approx(5.0 + 2.0)


def test_multi_asset_atr_accepts_explicit_ohlc_sources_without_default_close() -> None:
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    settle = pd.DataFrame({"AAA": [10.0, 11.0, 13.0, 12.0]}, index=dates)
    high = pd.DataFrame({"AAA": [11.0, 12.5, 13.5, 12.8]}, index=dates)
    low = pd.DataFrame({"AAA": [9.5, 10.5, 12.2, 11.5]}, index=dates)
    builder = mod.MultiAssetFeatureBuilder({"settle": settle, "session_high": high, "session_low": low})

    atr = builder.compute(
        {
            "name": "atr_2",
            "op": "indicator.atr",
            "high_source": "session_high",
            "low_source": "session_low",
            "close_source": "settle",
            "period": 2,
            "method": "simple",
        }
    )

    true_range = pd.Series([1.5, 2.5, 2.5, 1.5], index=dates)
    expected = true_range.rolling(2, min_periods=2).mean()
    assert atr["AAA"].tolist() == pytest.approx(expected.tolist(), nan_ok=True)


def test_multi_asset_atr_requires_ohlc_source_frames() -> None:
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    close = pd.DataFrame({"AAA": [10.0, 11.0, 13.0, 12.0]}, index=dates)
    builder = mod.MultiAssetFeatureBuilder({"close": close})

    with pytest.raises(KeyError, match="Missing source frame 'high'"):
        builder.compute({"name": "atr_2", "op": "indicator.atr", "period": 2})


@pytest.mark.parametrize(
    "op_name",
    [
        "sma",
        "ta.sma",
        "ema",
        "ta.ema",
        "momentum",
        "return",
        "volatility",
        "rolling_volatility",
        "atr",
        "ta.atr",
        "average_true_range",
        "rsi",
        "ta.rsi",
        "macd",
        "ta.macd",
        "macd_signal",
        "indicator.macd_signal",
        "ta.macd_signal",
        "zscore",
        "ta.zscore",
        "percentile",
        "rolling_percentile",
        "bollinger",
        "bollinger_band",
        "ta.bollinger",
    ],
)
def test_multi_asset_public_indicators_reject_alias_ops(op_name) -> None:
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    close = pd.DataFrame({"AAA": [10.0, 11.0, 13.0, 12.0]}, index=dates)
    builder = mod.MultiAssetFeatureBuilder(
        {"close": close, "high": close + 1.0, "low": close - 1.0}
    )

    with pytest.raises(ValueError, match="Unsupported multi-asset feature op"):
        builder.compute({"name": "alias_probe", "op": op_name, "period": 2})


def test_multi_asset_same_session_rejects_atr_entry_lookahead() -> None:
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    close = pd.DataFrame({"QQQ": [100.0, 101.0, 103.0, 104.0, 102.0]}, index=dates)
    config = {
        "strategy_id": "same_session_atr_lookahead_probe",
        "universe": {"symbols": ["QQQ"]},
        "computed_fields": [{"name": "atr_2", "op": "indicator.atr", "period": 2}],
        "signals": {
            "entry": {"field": "atr_2", "op": "gt", "value": 1.0},
            "exit": {"op": "session.same_session_close"},
            "target_weight": 1.0,
        },
        "allocation": {"method": "signal_state", "target_weight": 1.0},
        "rebalance": {"trigger": {"op": "signal.change"}},
        "fill_model": {"session_scope": "same_session", "entry_price": "open", "exit_price": "close"},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    with pytest.raises(ValueError, match="same-session entry cannot use indicators"):
        mod.MultiAssetPortfolioEngineBacktester(
            {
                "open": close.copy(),
                "close": close,
                "high": close + 1.0,
                "low": close - 1.0,
            },
            config,
        ).run()


def test_multi_asset_same_session_rejects_macd_entry_lookahead() -> None:
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=40, freq="B")
    close = pd.DataFrame({"QQQ": [100.0 + idx * 0.5 for idx in range(len(dates))]}, index=dates)
    config = {
        "strategy_id": "same_session_macd_lookahead_probe",
        "universe": {"symbols": ["QQQ"]},
        "computed_fields": [
            {"name": "macd_line", "op": "indicator.macd", "source": "close", "fastperiod": 3, "slowperiod": 6},
            {
                "name": "macd_signal",
                "op": "indicator.macd",
                "source": "close",
                "fastperiod": 3,
                "slowperiod": 6,
                "signalperiod": 3,
                "output": "signal",
            },
        ],
        "signals": {
            "entry": {"field": "macd_line", "op": "gt", "right_field": "macd_signal"},
            "exit": {"op": "session.same_session_close"},
            "target_weight": 1.0,
        },
        "allocation": {"method": "signal_state", "target_weight": 1.0},
        "rebalance": {"trigger": {"op": "signal.change"}},
        "fill_model": {"session_scope": "same_session", "entry_price": "open", "exit_price": "close"},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    with pytest.raises(ValueError, match="same-session entry cannot use indicators"):
        mod.MultiAssetPortfolioEngineBacktester(
            {
                "open": close.copy(),
                "close": close,
                "high": close + 1.0,
                "low": close - 1.0,
            },
            config,
        ).run()


def test_multi_asset_same_session_rejects_raw_close_entry_lookahead() -> None:
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    close = pd.DataFrame({"QQQ": [100.0, 101.0, 103.0, 104.0, 102.0]}, index=dates)
    config = {
        "strategy_id": "same_session_close_lookahead_probe",
        "universe": {"symbols": ["QQQ"]},
        "computed_fields": [],
        "signals": {
            "entry": {"field": "close", "op": "gt", "value": 100.0},
            "exit": {"op": "session.same_session_close"},
            "target_weight": 1.0,
        },
        "allocation": {"method": "signal_state", "target_weight": 1.0},
        "rebalance": {"trigger": {"op": "signal.change"}},
        "fill_model": {"session_scope": "same_session", "entry_price": "open", "exit_price": "close"},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    with pytest.raises(ValueError, match="same-session entry cannot use current-bar market fields"):
        mod.MultiAssetPortfolioEngineBacktester(
            {
                "open": close.copy(),
                "close": close,
                "high": close + 1.0,
                "low": close - 1.0,
            },
            config,
        ).run()


def test_multi_asset_same_session_accepts_calendar_entry_without_lookahead() -> None:
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-01", periods=10, freq="B")
    open_ = pd.DataFrame({"QQQ": [100.0] * len(dates)}, index=dates)
    close = pd.DataFrame({"QQQ": [101.0] * len(dates)}, index=dates)
    config = {
        "strategy_id": "same_session_calendar_positive_probe",
        "universe": {"symbols": ["QQQ"]},
        "computed_fields": [],
        "signals": {
            "entry": {"op": "calendar.month_start"},
            "exit": {"op": "session.same_session_close"},
            "target_weight": 1.0,
        },
        "allocation": {"method": "signal_state", "target_weight": 1.0},
        "rebalance": {"trigger": {"op": "signal.change"}},
        "fill_model": {
            "session_scope": "same_session",
            "entry_price": "open",
            "exit_price": "close",
            "cost": {"transaction_cost": 0.0, "slippage": 0.0},
        },
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    result = mod.MultiAssetPortfolioEngineBacktester({"open": open_, "close": close}, config).run()

    assert result.validation_report["status"] == "valid"
    assert result.validation_report["accounting_backend"] == "same_session"
    assert result.rebalance_trades["Action"].tolist() == ["buy", "exit"]
    assert result.rebalance_trades["Reason"].tolist() == ["same-session entry", "same-session exit"]
    assert result.rebalance_trades["Trade_return"].dropna().tolist() == pytest.approx([0.01])
    assert result.equity_curve["Equity_value"].iloc[-1] == pytest.approx(101.0)


@pytest.mark.parametrize(
    ("case_id", "indicators", "entry", "exit_"),
    [
        (
            "ma",
            [
                {"name": "sma_fast", "op": "indicator.sma", "source": "close", "period": 3},
                {"name": "sma_slow", "op": "indicator.sma", "source": "close", "period": 6},
            ],
            {"field": "sma_fast", "op": "crosses_above", "right_field": "sma_slow"},
            {"field": "sma_fast", "op": "crosses_below", "right_field": "sma_slow"},
        ),
        (
            "zscore",
            [{"name": "z_5", "op": "indicator.zscore", "source": "close", "period": 5}],
            {"field": "z_5", "op": "gt", "value": 0.8},
            {"field": "z_5", "op": "lt", "value": -0.8},
        ),
        (
            "atr",
            [{"name": "atr_3", "op": "indicator.atr", "period": 3}],
            {"field": "atr_3", "op": "gt", "value": 5.5},
            {"field": "atr_3", "op": "lt", "value": 4.5},
        ),
        (
            "percentile",
            [{"name": "p50_5", "op": "indicator.percentile", "source": "close", "period": 5, "percentile": 50}],
            {"field": "close", "op": "gt", "right_field": "p50_5"},
            {"field": "close", "op": "lt", "right_field": "p50_5"},
        ),
        (
            "bollinger",
            [
                {
                    "name": "bb_pct_b_5",
                    "op": "indicator.bollinger",
                    "source": "close",
                    "period": 5,
                    "band": "percent_b",
                    "stddev": 1.5,
                }
            ],
            {"field": "bb_pct_b_5", "op": "gt", "value": 0.8},
            {"field": "bb_pct_b_5", "op": "lt", "value": 0.2},
        ),
        (
            "rsi",
            [{"name": "rsi_3", "op": "indicator.rsi", "source": "close", "period": 3}],
            {"field": "rsi_3", "op": "lt", "value": 20},
            {"field": "rsi_3", "op": "gt", "value": 80},
        ),
        (
            "macd",
            [
                {"name": "macd_line", "op": "indicator.macd", "source": "close", "fastperiod": 3, "slowperiod": 6},
                {
                    "name": "macd_signal",
                    "op": "indicator.macd",
                    "source": "close",
                    "fastperiod": 3,
                    "slowperiod": 6,
                    "signalperiod": 3,
                    "output": "signal",
                },
            ],
            {"field": "macd_line", "op": "gt", "right_field": "macd_signal"},
            {"field": "macd_line", "op": "lt", "right_field": "macd_signal"},
        ),
    ],
)
def test_public_indicator_configs_run_backtests_with_costs(case_id, indicators, entry, exit_) -> None:
    engine_mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    config_mod = importlib.import_module("backtester.StrategyRunConfig_backtester")
    dates = pd.date_range("2024-01-02", periods=36, freq="B")
    prices = [
        100,
        98,
        96,
        94,
        92,
        90,
        95,
        100,
        105,
        110,
        108,
        106,
        104,
        102,
        100,
        98,
        96,
        94,
        96,
        100,
        105,
        112,
        118,
        120,
        116,
        110,
        105,
        100,
        95,
        90,
        95,
        100,
        105,
        110,
        115,
        120,
    ]
    close = pd.DataFrame({"QQQ": prices}, index=dates)
    high = close + 2.0
    low = close - 2.0
    raw_config = {
        "schema_version": "strategy_run",
        "platform": {"strategy_mode_id": "single_asset_signal", "workflow_id": "single_backtest"},
        "data": {"provider": "local", "frequency": "1D", "calendar": "XNYS", "timezone": "America/New_York"},
        "universe": {"symbols": ["QQQ"], "universe_policy": "single_asset"},
        "computed_fields": indicators,
        "signals": {
            "entry": entry,
            "exit": exit_,
            "target_weight": 1.0,
            "conflict_policy": "exit_then_entry",
        },
        "selection": {},
        "allocation": {"method": "signal_state", "target_weight": 1.0, "cash_policy": "keep_unallocated_cash"},
        "rebalance": {"trigger": {"op": "signal.change"}},
        "fill_model": {"timing": "bar_offset", "entry_price": "open", "entry_delay_bars": 1, "exit_price": "open", "exit_delay_bars": 1, "cost": {"transaction_cost": 0.001, "slippage": 0.0005}},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
        "parameter_domains": {},
        "outputs": {"equity_curve": True, "trade_summary": True},
    }

    normalized = config_mod.normalize_strategy_run_config(raw_config)
    result = engine_mod.MultiAssetPortfolioEngineBacktester(
        {"close": close, "open": close.copy(), "high": high, "low": low},
        normalized,
    ).run()

    assert "features" not in normalized
    assert [item["name"] for item in normalized["computed_fields"]] == [item["name"] for item in indicators]
    assert result.validation_report["status"] == "valid", case_id
    assert result.equity_curve.empty is False
    assert result.feature_cache["computed"] >= len(indicators)
    assert result.rebalance_trades.empty is False
    assert result.equity_curve["Trade_cost"].sum() > 0.0
    assert result.validation_report["cost_accounting"]["status"] == "valid"


def test_multi_asset_qqq_rsi_20_80_oracle_pins_trades_equity_and_costs():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=7, freq="B")
    close = pd.DataFrame(
        {"QQQ": [100.0, 95.0, 90.0, 85.0, 95.0, 105.0, 104.0]},
        index=dates,
    )
    indicator_spec = [{"name": "rsi_3", "op": "indicator.rsi", "source": "close", "period": 3}]
    base_config = {
        "strategy_id": "qqq_rsi_20_80_oracle",
        "universe": {"symbols": ["QQQ"]},
        "computed_fields": indicator_spec,
        "signals": {
            "entry": {"field": "rsi_3", "op": "lt", "value": 20},
            "exit": {"field": "rsi_3", "op": "gt", "value": 80},
            "target_weight": 1.0,
        },
        "allocation": {"method": "signal_state", "target_weight": 1.0},
        "rebalance": {"trigger": {"op": "signal.change"}},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    builder = mod.MultiAssetFeatureBuilder({"close": close})
    rsi = builder.build(indicator_spec)["rsi_3"]["QQQ"]
    assert rsi.tolist() == pytest.approx(
        [np.nan, np.nan, np.nan, 0.0, 50.0, 80.0, 95.2380952381],
        nan_ok=True,
    )
    assert rsi.lt(20).fillna(False).tolist() == [False, False, False, True, False, False, False]
    assert rsi.gt(80).fillna(False).tolist() == [False, False, False, False, False, False, True]

    zero_cost_result = mod.MultiAssetPortfolioEngineBacktester(
        {"close": close, "open": close.copy()},
        {
            **base_config,
            "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        },
    ).run()

    zero_trades = zero_cost_result.rebalance_trades
    assert zero_trades["Time"].tolist() == [dates[3], dates[6]]
    assert zero_trades["Action"].tolist() == ["buy", "exit"]
    assert zero_trades["Before_weight"].tolist() == pytest.approx([0.0, 1.0])
    assert zero_trades["Target_weight"].tolist() == pytest.approx([1.0, 0.0])
    assert zero_trades["Trade_delta"].tolist() == pytest.approx([1.0, -1.0])
    assert zero_trades["Trade_turnover"].tolist() == pytest.approx([1.0, 1.0])
    assert zero_trades["Allocated_cost"].tolist() == pytest.approx([0.0, 0.0])
    assert zero_cost_result.equity_curve["Weight_QQQ"].tolist() == pytest.approx(
        [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0]
    )
    assert zero_cost_result.equity_curve["Cash_weight"].tolist() == pytest.approx(
        [1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    )
    assert zero_cost_result.equity_curve["Trade_cost"].tolist() == pytest.approx([0.0] * 7)
    assert zero_cost_result.equity_curve["Equity_value"].tolist() == pytest.approx(
        [
            100.0,
            100.0,
            100.0,
            100.0,
            111.7647058824,
            123.5294117647,
            122.3529411765,
        ]
    )
    assert zero_cost_result.validation_report["status"] == "valid"
    assert zero_cost_result.validation_report["cost_accounting"]["status"] == "not_configured"

    cost_result = mod.MultiAssetPortfolioEngineBacktester(
        {"close": close, "open": close.copy()},
        {
            **base_config,
            "fill_model": {"cost": {"transaction_cost": 0.001, "slippage": 0.0005}},
        },
    ).run()

    assert cost_result.rebalance_trades["Allocated_cost"].tolist() == pytest.approx(
        [0.15, 0.1832541176]
    )
    assert cost_result.equity_curve["Trade_cost"].tolist() == pytest.approx(
        [0.0, 0.0, 0.0, 0.15, 0.0, 0.0, 0.1832541176]
    )
    assert cost_result.equity_curve["Equity_value"].iloc[-1] == pytest.approx(121.9861576471)
    assert cost_result.equity_curve["Equity_value"].iloc[-1] < zero_cost_result.equity_curve[
        "Equity_value"
    ].iloc[-1]
    assert cost_result.validation_report["status"] == "valid"
    assert cost_result.validation_report["cost_accounting"]["status"] == "valid"


def test_signal_state_next_bar_open_uses_distinct_open_price_oracle():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    close = pd.DataFrame({"QQQ": [100.0, 220.0, 220.0, 220.0]}, index=dates)
    open_ = pd.DataFrame({"QQQ": [100.0, 200.0, 220.0, 220.0]}, index=dates)
    config = {
        "strategy_id": "next_open_oracle",
        "universe": {"symbols": ["QQQ"]},
        "signals": {
            "entry": {"field": "close", "op": "eq", "value": 100.0},
            "exit": {"field": "close", "op": "lt", "value": 0.0},
            "target_weight": 1.0,
        },
        "allocation": {"method": "signal_state", "target_weight": 1.0},
        "rebalance": {"trigger": {"op": "signal.change"}},
        "fill_model": {"timing": "bar_offset", "entry_price": "open", "entry_delay_bars": 1, "exit_price": "open", "exit_delay_bars": 1, "cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    result = mod.MultiAssetPortfolioEngineBacktester({"close": close, "open": open_}, config).run()

    assert result.validation_report["accounting_fast_path"] == "single_asset_next_bar_open_target_weight_numpy"
    assert result.validation_report["fill_semantics"] == "signal_on_bar_t_fills_at_t_plus_1_open"
    assert result.rebalance_trades["Time"].tolist() == [dates[1]]
    assert result.rebalance_trades["Action"].tolist() == ["buy"]
    assert result.equity_curve["Weight_QQQ"].tolist() == pytest.approx([0.0, 1.0, 1.0, 1.0])
    assert result.equity_curve["Equity_value"].tolist() == pytest.approx([100.0, 110.0, 110.0, 110.0])


def test_signal_state_next_bar_open_oracle_includes_nonzero_costs():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    close = pd.DataFrame({"QQQ": [100.0, 220.0, 220.0]}, index=dates)
    open_ = pd.DataFrame({"QQQ": [100.0, 200.0, 220.0]}, index=dates)
    config = {
        "strategy_id": "next_open_cost_oracle",
        "universe": {"symbols": ["QQQ"]},
        "signals": {
            "entry": {"field": "close", "op": "eq", "value": 100.0},
            "exit": {"field": "close", "op": "lt", "value": 0.0},
            "target_weight": 1.0,
        },
        "allocation": {"method": "signal_state", "target_weight": 1.0},
        "rebalance": {"trigger": {"op": "signal.change"}},
        "fill_model": {"timing": "bar_offset", "entry_price": "open", "entry_delay_bars": 1, "exit_price": "open", "exit_delay_bars": 1, "cost": {"transaction_cost": 0.001, "slippage": 0.0005}},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    result = mod.MultiAssetPortfolioEngineBacktester({"close": close, "open": open_}, config).run()

    assert result.rebalance_trades["Time"].tolist() == [dates[1]]
    assert result.rebalance_trades["Allocated_cost"].tolist() == pytest.approx([0.15])
    assert result.equity_curve["Trade_cost"].tolist() == pytest.approx([0.0, 0.15, 0.0])
    assert result.equity_curve["Equity_value"].tolist() == pytest.approx([100.0, 109.835, 109.835])
    assert result.validation_report["cost_accounting"]["status"] == "valid"


def test_bar_offset_close_to_next_open_uses_real_ohlcv_oracle():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    close = pd.DataFrame({"SPY": [100.0, 120.0, 80.0]}, index=dates)
    open_ = pd.DataFrame({"SPY": [100.0, 110.0, 90.0]}, index=dates)
    config = {
        "strategy_id": "close_to_next_open_oracle",
        "universe": {"symbols": ["SPY"]},
        "signals": {"entry": {"op": "calendar.every_session"}, "target_weight": 1.0},
        "allocation": {"method": "signal_target_weight", "target_weight": 1.0},
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "fill_model": {
            "timing": "bar_offset", "entry_price": "close", "entry_delay_bars": 0, "exit_price": "open", "exit_delay_bars": 1,
            "cost": {"transaction_cost": 0.0, "slippage": 0.0},
        },
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    result = mod.MultiAssetPortfolioEngineBacktester(
        {"close": close, "open": open_},
        config,
    ).run()

    assert result.validation_report["accounting_backend"] == "bar_offset_round_trip"
    assert result.validation_report["return_clock"] == "close_plus_0_bars_to_open_plus_1_bars"
    assert result.validation_report["bar_offset"] == {
        "entry_price": "close",
        "entry_delay_bars": 0,
        "exit_price": "open",
        "exit_delay_bars": 1,
    }
    assert result.equity_curve["Portfolio_return"].tolist() == pytest.approx([0.0, 0.10, -0.25])
    assert result.equity_curve["Equity_value"].tolist() == pytest.approx([100.0, 110.0, 82.5])
    assert result.equity_curve["Weight_SPY"].tolist() == pytest.approx([1.0, 1.0, 0.0])
    assert result.equity_curve["Gross_exposure"].tolist() == pytest.approx([1.0, 1.0, 0.0])
    exit_rows = result.rebalance_trades[result.rebalance_trades["Action"] == "exit"]
    assert exit_rows["Entry_price"].tolist() == pytest.approx([100.0, 120.0])
    assert exit_rows["Exit_price"].tolist() == pytest.approx([110.0, 90.0])


def test_bar_offset_close_to_next_open_charges_entry_and_exit_costs():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    close = pd.DataFrame({"SPY": [100.0, 120.0, 80.0]}, index=dates)
    open_ = pd.DataFrame({"SPY": [100.0, 110.0, 90.0]}, index=dates)
    config = {
        "strategy_id": "close_to_next_open_cost_oracle",
        "universe": {"symbols": ["SPY"]},
        "signals": {"entry": {"op": "calendar.event_date", "dates": [dates[0].date().isoformat()]}, "target_weight": 1.0},
        "allocation": {"method": "signal_target_weight", "target_weight": 1.0},
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "fill_model": {
            "timing": "bar_offset",
            "entry_price": "close",
            "entry_delay_bars": 0,
            "exit_price": "open",
            "exit_delay_bars": 1,
            "cost": {"transaction_cost": 0.0005, "slippage": 0.0},
        },
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    result = mod.MultiAssetPortfolioEngineBacktester(
        {"close": close, "open": open_},
        config,
    ).run()

    expected_equity = 100.0 * (1.0 - 0.0005) * 1.10 * (1.0 - 0.0005)
    assert result.equity_curve["Turnover"].tolist() == pytest.approx([1.0, 1.0, 0.0])
    assert result.equity_curve["Trade_cost"].iloc[0] > 0.0
    assert result.equity_curve["Trade_cost"].iloc[1] > 0.0
    assert result.equity_curve["Equity_value"].tolist() == pytest.approx([99.95, expected_equity, expected_equity])
    assert result.validation_report["cost_accounting"]["status"] == "valid"


def test_bar_offset_close_to_next_open_requires_explicit_open_prices():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    close = pd.DataFrame({"SPY": [100.0, 120.0, 80.0]}, index=dates)
    config = {
        "strategy_id": "close_to_next_open_requires_open",
        "universe": {"symbols": ["SPY"]},
        "signals": {"entry": {"op": "calendar.every_session"}, "target_weight": 1.0},
        "allocation": {"method": "signal_target_weight", "target_weight": 1.0},
        "fill_model": {"timing": "bar_offset", "entry_price": "close", "entry_delay_bars": 0, "exit_price": "open", "exit_delay_bars": 1, "cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    with pytest.raises(ValueError, match="requires explicit open price"):
        mod.MultiAssetPortfolioEngineBacktester({"close": close}, config).run()


def test_bar_offset_round_trip_rejects_exit_rules_and_overlaps():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    close = pd.DataFrame({"SPY": [100.0, 120.0, 130.0, 140.0]}, index=dates)
    open_ = pd.DataFrame({"SPY": [100.0, 110.0, 125.0, 135.0]}, index=dates)
    target_weight = pd.DataFrame({"SPY": [1.0, 1.0, 1.0, 0.0]}, index=dates)
    base = {
        "strategy_id": "bar_offset_rejects_unsafe_shapes",
        "universe": {"symbols": ["SPY"]},
        "signals": {"entry": {"op": "calendar.every_session"}, "target_weight": 1.0},
        "allocation": {"method": "signal_target_weight", "target_weight": 1.0},
        "fill_model": {
            "timing": "bar_offset",
            "entry_price": "close",
            "entry_delay_bars": 0,
            "exit_price": "open",
            "exit_delay_bars": 1,
            "cost": {"transaction_cost": 0.0, "slippage": 0.0},
        },
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }
    without_entry = {**base, "signals": {"target_weight": 1.0}}
    with pytest.raises(ValueError, match="requires a signals.entry"):
        mod.MultiAssetPortfolioEngineBacktester(
            {"close": close, "open": open_, "target_weight": target_weight},
            without_entry,
        ).run()

    with_frame_override = {**base, "allocation": {**base["allocation"], "frame": "target_weight"}}
    with pytest.raises(ValueError, match="frame overrides"):
        mod.MultiAssetPortfolioEngineBacktester(
            {"close": close, "open": open_, "target_weight": target_weight},
            with_frame_override,
        ).run()

    overlapping = {**base, "fill_model": {**base["fill_model"], "exit_delay_bars": 2}}
    with pytest.raises(ValueError, match="overlapping positions"):
        mod.MultiAssetPortfolioEngineBacktester({"close": close, "open": open_}, overlapping).run()


def test_signal_state_time_stop_bars_exits_after_fill_count_oracle():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    close = pd.DataFrame({"BTCUSDT": [100.0, 100.0, 110.0, 120.0, 120.0]}, index=dates)
    open_ = close.copy()
    config = {
        "strategy_id": "time_stop_oracle",
        "universe": {"symbols": ["BTCUSDT"]},
        "signals": {
            "entry": {"field": "close", "op": "eq", "value": 100.0},
            "exit": {"op": "time_stop_bars", "value": 2},
            "target_weight": 1.0,
        },
        "allocation": {"method": "signal_state", "target_weight": 1.0},
        "rebalance": {"trigger": {"op": "signal.change"}},
        "fill_model": {"timing": "bar_offset", "entry_price": "open", "entry_delay_bars": 1, "exit_price": "open", "exit_delay_bars": 1, "cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    result = mod.MultiAssetPortfolioEngineBacktester({"close": close, "open": open_}, config).run()

    assert result.rebalance_trades["Time"].tolist() == [dates[1], dates[3]]
    assert result.rebalance_trades["Action"].tolist() == ["buy", "exit"]
    assert result.equity_curve["Weight_BTCUSDT"].tolist() == pytest.approx([0.0, 1.0, 1.0, 0.0, 0.0])
    assert result.equity_curve["Equity_value"].tolist() == pytest.approx([100.0, 100.0, 110.0, 120.0, 120.0])


def test_signal_state_time_stop_bars_respects_all_and_any_semantics():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    close = pd.DataFrame({"QQQ": [100.0, 110.0, 120.0, 130.0, 140.0]}, index=dates)
    base_config = {
        "strategy_id": "time_stop_logic_oracle",
        "universe": {"symbols": ["QQQ"]},
        "signals": {
            "entry": {"field": "close", "op": "eq", "value": 100.0},
            "exit": {},
            "target_weight": 1.0,
        },
        "allocation": {"method": "signal_state", "target_weight": 1.0},
        "rebalance": {"trigger": {"op": "signal.change"}},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0, "long_short": "long_only"},
    }

    all_config = copy.deepcopy(base_config)
    all_config["signals"]["exit"] = {
        "all": [
            {"op": "time_stop_bars", "value": 2},
            {"field": "close", "op": "gt", "value": 125.0},
        ]
    }
    all_result = mod.MultiAssetPortfolioEngineBacktester({"close": close, "open": close.copy()}, all_config).run()
    assert all_result.rebalance_trades["Time"].tolist() == [dates[0], dates[3]]
    assert all_result.rebalance_trades["Action"].tolist() == ["buy", "exit"]

    any_config = copy.deepcopy(base_config)
    any_config["signals"]["exit"] = {
        "any": [
            {"op": "time_stop_bars", "value": 3},
            {"field": "close", "op": "gt", "value": 115.0},
        ]
    }
    any_result = mod.MultiAssetPortfolioEngineBacktester({"close": close, "open": close.copy()}, any_config).run()
    assert any_result.rebalance_trades["Time"].tolist() == [dates[0], dates[2]]
    assert any_result.rebalance_trades["Action"].tolist() == ["buy", "exit"]


def test_multi_asset_month_start_rebalance_respects_position_limit_and_cash():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
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

    result = mod.MultiAssetPortfolioEngineBacktester(_price_frames(), config).run()

    first_rebalance = result.holdings[result.holdings["Selected"]].groupby("Time").first()
    assert len(result.rebalance_audit) == 3
    assert not first_rebalance.empty
    assert result.holdings[result.holdings["Selected"]]["Target_weight"].max() == 0.2
    rebalance_rows = result.equity_curve[result.equity_curve["Turnover"] > 0]
    assert rebalance_rows["Cash_weight"].iloc[0] == pytest.approx(0.4)


def test_multi_asset_configured_universe_symbols_must_exist_in_market_data():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    frames = _price_frames()
    frames["close"] = frames["close"][["AAA", "BBB"]]
    config = {
        "strategy_id": "missing_symbol_guard",
        "universe": {"symbols": ["AAA", "BBB", "CCC"]},
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "close",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    with pytest.raises(ValueError, match="missing configured universe symbols: CCC"):
        mod.MultiAssetPortfolioEngineBacktester(frames, config)


def test_multi_asset_static_universe_reports_survivorship_risk():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    config = {
        "strategy_id": "static_universe_risk_probe",
        "universe": {"symbols": ["AAA", "BBB", "CCC"], "universe_policy": "all_symbols"},
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "selection": {"eligible": {"field": "close", "op": "gt", "value": 0}, "top_n": 2},
        "allocation": {"method": "equal_weight", "position_limit": 0.5},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = mod.MultiAssetPortfolioEngineBacktester(_price_frames(), config).run()
    provenance = result.validation_report["universe_provenance"]

    assert provenance["survivorship_bias_risk"] == "high"
    assert provenance["provenance_status"] == "review"
    assert provenance["point_in_time_constituents"] is False
    assert "static_or_current_universe_may_have_survivorship_bias" in provenance["warnings"]
    assert result.validation_report["warnings"] == provenance["warnings"]


def test_multi_asset_point_in_time_universe_can_be_low_risk(tmp_path):
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    constituents_path = tmp_path / "historical_constituents.csv"
    constituents_path.write_text(
        "symbol,effective_start,effective_end\n"
        "AAA,2020-01-01,\n"
        "BBB,2020-01-01,\n"
        "CCC,2020-01-01,\n",
        encoding="utf-8",
    )
    config = {
        "strategy_id": "pit_universe_probe",
        "universe": {
            "symbols": ["AAA", "BBB", "CCC"],
            "universe_policy": "point_in_time_snapshot",
            "historical_constituents_path": str(constituents_path),
            "as_of_date": "2024-01-02",
            "delisted_policy": "include_when_historically_tradable",
        },
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "selection": {"eligible": {"field": "close", "op": "gt", "value": 0}, "top_n": 2},
        "allocation": {"method": "equal_weight", "position_limit": 0.5},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = mod.MultiAssetPortfolioEngineBacktester(_price_frames(), config).run()
    provenance = result.validation_report["universe_provenance"]

    assert provenance["survivorship_bias_risk"] == "low"
    assert provenance["provenance_status"] == "valid"
    assert provenance["point_in_time_constituents"] is True
    assert provenance["constituents_validation"]["status"] == "valid"
    assert provenance["warnings"] == []


def test_multi_asset_constituents_path_resolves_relative_to_config_file(tmp_path):
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = config_dir / "run.json"
    config_path.write_text("{}", encoding="utf-8")
    constituents_path = config_dir / "historical_constituents.csv"
    constituents_path.write_text(
        "symbol,effective_start,effective_end\n"
        "AAA,2020-01-01,\n"
        "BBB,2020-01-01,\n"
        "CCC,2020-01-01,\n",
        encoding="utf-8",
    )
    config = {
        "__config_file_path": str(config_path),
        "strategy_id": "pit_universe_config_relative_probe",
        "universe": {
            "symbols": ["AAA", "BBB", "CCC"],
            "universe_policy": "point_in_time_snapshot",
            "historical_constituents_path": "historical_constituents.csv",
            "as_of_date": "2024-01-02",
            "delisted_policy": "include_when_historically_tradable",
        },
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "selection": {"eligible": {"field": "close", "op": "gt", "value": 0}, "top_n": 2},
        "allocation": {"method": "equal_weight", "position_limit": 0.5},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = mod.MultiAssetPortfolioEngineBacktester(_price_frames(), config).run()
    validation = result.validation_report["universe_provenance"]["constituents_validation"]

    assert validation["status"] == "valid"
    assert Path(validation["path"]) == constituents_path


def test_multi_asset_snapshot_date_only_constituents_require_exact_as_of(tmp_path):
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    constituents_path = tmp_path / "historical_constituents.csv"
    constituents_path.write_text(
        "symbol,snapshot_date\n"
        "AAA,2020-01-01\n"
        "BBB,2020-01-01\n"
        "CCC,2020-01-01\n",
        encoding="utf-8",
    )
    config = {
        "strategy_id": "stale_snapshot_universe_probe",
        "universe": {
            "symbols": ["AAA", "BBB", "CCC"],
            "universe_policy": "point_in_time_snapshot",
            "historical_constituents_path": str(constituents_path),
            "as_of_date": "2024-01-02",
            "delisted_policy": "include_when_historically_tradable",
        },
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "selection": {"eligible": {"field": "close", "op": "gt", "value": 0}, "top_n": 2},
        "allocation": {"method": "equal_weight", "position_limit": 0.5},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = mod.MultiAssetPortfolioEngineBacktester(_price_frames(), config).run()
    provenance = result.validation_report["universe_provenance"]
    validation = provenance["constituents_validation"]

    assert provenance["survivorship_bias_risk"] == "medium"
    assert provenance["provenance_status"] == "review"
    assert provenance["point_in_time_constituents"] is False
    assert validation["status"] == "invalid"
    assert validation["date_semantics"] == "exact_as_of_snapshot"
    assert "historical_constituents_exact_as_of_snapshot_missing" in validation["errors"]
    assert "historical_constituents_content_validation_failed" in provenance["warnings"]


def test_multi_asset_generic_or_current_source_cannot_prove_pit_universe():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    base_config = {
        "strategy_id": "unproven_pit_source_probe",
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "selection": {"eligible": {"field": "close", "op": "gt", "value": 0}, "top_n": 2},
        "allocation": {"method": "equal_weight", "position_limit": 0.5},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    generic_config = {
        **base_config,
        "universe": {
            "symbols": ["AAA", "BBB", "CCC"],
            "universe_policy": "point_in_time_snapshot",
            "source": "sp500",
            "as_of_date": "2024-01-02",
            "delisted_policy": "include_when_historically_tradable",
        },
    }
    current_provider_config = {
        **base_config,
        "universe": {
            **generic_config["universe"],
            "source_type": "current_provider_list",
        },
    }
    fixed_symbols_config = {
        **base_config,
        "universe": {
            **generic_config["universe"],
            "source_type": "fixed_symbols",
            "historical_constituents_path": "workspace/universe/current_symbols.parquet",
        },
    }
    missing_as_of_config = {
        **base_config,
        "universe": {
            "symbols": ["AAA", "BBB", "CCC"],
            "universe_policy": "point_in_time_snapshot",
            "historical_constituents_path": "workspace/universe/historical_constituents.parquet",
            "delisted_policy": "include_when_historically_tradable",
        },
    }

    generic_result = mod.MultiAssetPortfolioEngineBacktester(_price_frames(), generic_config).run()
    current_result = mod.MultiAssetPortfolioEngineBacktester(
        _price_frames(),
        current_provider_config,
    ).run()
    fixed_result = mod.MultiAssetPortfolioEngineBacktester(
        _price_frames(),
        fixed_symbols_config,
    ).run()
    missing_as_of_result = mod.MultiAssetPortfolioEngineBacktester(
        _price_frames(),
        missing_as_of_config,
    ).run()

    generic_provenance = generic_result.validation_report["universe_provenance"]
    current_provenance = current_result.validation_report["universe_provenance"]
    fixed_provenance = fixed_result.validation_report["universe_provenance"]
    missing_as_of_provenance = missing_as_of_result.validation_report["universe_provenance"]
    assert generic_provenance["survivorship_bias_risk"] == "medium"
    assert generic_provenance["point_in_time_constituents"] is False
    assert "point_in_time_universe_claim_missing_evidence" in generic_provenance["warnings"]
    assert current_provenance["survivorship_bias_risk"] == "medium"
    assert current_provenance["point_in_time_constituents"] is False
    assert "current_or_static_universe_source_not_point_in_time" in current_provenance["warnings"]
    assert fixed_provenance["survivorship_bias_risk"] == "medium"
    assert fixed_provenance["point_in_time_constituents"] is False
    assert "current_or_static_universe_source_not_point_in_time" in fixed_provenance["warnings"]
    assert missing_as_of_provenance["survivorship_bias_risk"] == "medium"
    assert missing_as_of_provenance["point_in_time_constituents"] is False
    assert "point_in_time_universe_claim_missing_as_of_date" in missing_as_of_provenance["warnings"]


def test_multi_asset_ignored_market_data_symbols_are_reported():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    frames = _price_frames()
    frames["close"]["DDD"] = 99.0
    frames["open"]["DDD"] = 99.0
    frames["volume"]["DDD"] = 1_000_000
    config = {
        "strategy_id": "ignored_symbol_probe",
        "universe": {"symbols": ["AAA", "BBB", "CCC"]},
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "selection": {"eligible": {"field": "close", "op": "gt", "value": 0}, "top_n": 2},
        "allocation": {"method": "equal_weight", "position_limit": 0.5},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = mod.MultiAssetPortfolioEngineBacktester(frames, config).run()
    provenance = result.validation_report["universe_provenance"]

    assert result.validation_report["loaded_symbols"] == ["AAA", "BBB", "CCC"]
    assert "DDD" in result.validation_report["available_data_symbols"]
    assert provenance["ignored_data_symbols"] == ["DDD"]
    assert "market_data_contains_unconfigured_symbols_ignored" in provenance["warnings"]


def test_multi_asset_year_start_fixed_weights_rebalances_annually():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2023-11-15", periods=310, freq="B")
    close = pd.DataFrame(
        {
            "AAA": [100 + idx * 0.1 for idx in range(len(dates))],
            "BBB": [80 + idx * 0.05 for idx in range(len(dates))],
        },
        index=dates,
    )
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

    result = mod.MultiAssetPortfolioEngineBacktester({"close": close}, config).run()

    rebalance_times = pd.to_datetime(result.rebalance_audit["Time"]).dt.strftime("%Y-%m-%d").tolist()
    assert rebalance_times == ["2023-11-15", "2024-01-01", "2025-01-01"]
    selected = result.holdings[result.holdings["Selected"]]
    assert set(selected["Asset"].unique().tolist()) == {"AAA", "BBB"}
    assert selected.groupby("Asset")["Target_weight"].first().to_dict() == pytest.approx(
        {"AAA": 0.6, "BBB": 0.4}
    )
    assert result.rebalance_audit["Turnover"].iloc[0] == pytest.approx(1.0)
    assert result.rebalance_audit["Turnover"].iloc[1] > 0.0


def test_multi_asset_fixed_weight_profile_param_ref_selects_profile():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2020-01-01", periods=4, freq="D")
    close = pd.DataFrame(
        {
            "AAA": [100.0, 110.0, 121.0, 133.1],
            "BBB": [100.0, 90.0, 81.0, 72.9],
        },
        index=dates,
    )
    config = {
        "strategy_id": "profile_probe",
        "universe": {"symbols": ["AAA", "BBB"]},
        "selection": {"eligible": {"field": "close", "op": "gt", "value": 0}},
        "allocation": {
            "method": "fixed_weight_profiles",
            "profile_id": {"param_ref": "profile_id"},
            "weight_profiles": {
                "aaa_only": {"AAA": 1.0, "BBB": 0.0},
                "bbb_only": {"AAA": 0.0, "BBB": 1.0},
            },
        },
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "parameter_domains": {"profile_id": {"type": "set", "values": ["aaa_only", "bbb_only"]}},
        "resolved_params": {"profile_id": "bbb_only"},
    }

    result = mod.MultiAssetPortfolioEngineBacktester({"close": close}, config).run()

    first_rebalance = result.rebalance_audit.iloc[0]
    assert first_rebalance["Selected_assets"] == ["BBB"]
    assert result.equity_curve["Weight_AAA"].max() == 0.0
    assert result.equity_curve["Weight_BBB"].max() == 1.0


def test_calendar_event_overlay_flattens_baseline_shorts_event_and_restores_next_open():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.to_datetime(["2024-03-14", "2024-03-15", "2024-03-18"])
    open_ = pd.DataFrame(
        {
            "QQQ": [100.0, 110.0, 120.0],
            "TQQQ": [100.0, 100.0, 100.0],
        },
        index=dates,
    )
    close = pd.DataFrame(
        {
            "QQQ": [105.0, 111.0, 121.0],
            "TQQQ": [100.0, 90.0, 100.0],
        },
        index=dates,
    )
    config = {
        "strategy_id": "qqq_tqqq_quarterly_overlay",
        "universe": {"symbols": ["QQQ", "TQQQ"]},
        "allocation": {
            "method": "calendar_event_overlay",
            "baseline_weights": {"QQQ": 1.0},
            "event_weights": {"TQQQ": -1.0},
            "event": {
                "op": "calendar.nth_weekday_of_month",
                "weekday": "friday",
                "ordinal": 3,
                "months": [3, 6, 9, 12],
            },
        },
        "fill_model": {"entry_price": "open", "exit_price": "close", "cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {"allow_short": True, "max_gross_exposure": 1.0},
    }

    result = mod.MultiAssetPortfolioEngineBacktester(
        {"open": open_, "close": close},
        config,
    ).run()

    assert result.validation_report["accounting_backend"] == "calendar_event_overlay"
    assert result.validation_report["event_overlay"]["event_count"] == 1
    assert result.equity_curve["Equity_value"].round(6).tolist() == [100.0, 121.0, 121.0]
    assert result.equity_curve["Weight_QQQ"].tolist() == [1.0, 0.0, 1.0]
    assert result.equity_curve["Weight_TQQQ"].tolist() == [0.0, 0.0, 0.0]
    assert result.rebalance_trades["Action"].tolist() == [
        "buy",
        "exit",
        "new_short",
        "close_short",
        "buy",
    ]
    assert result.rebalance_trades["Reason"].tolist() == [
        "session open: restore baseline",
        "event open: flatten baseline",
        "event open: enter overlay",
        "event close: exit overlay",
        "session open: restore baseline",
    ]


def test_calendar_event_overlay_charges_costs_on_baseline_and_event_turnover():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.to_datetime(["2024-03-14", "2024-03-15", "2024-03-18"])
    open_ = pd.DataFrame({"QQQ": [100.0, 100.0, 100.0], "TQQQ": [100.0, 100.0, 100.0]}, index=dates)
    close = pd.DataFrame({"QQQ": [100.0, 100.0, 100.0], "TQQQ": [100.0, 100.0, 100.0]}, index=dates)
    config = {
        "strategy_id": "qqq_tqqq_overlay_cost_probe",
        "universe": {"symbols": ["QQQ", "TQQQ"]},
        "allocation": {
            "method": "calendar_event_overlay",
            "baseline_weights": {"QQQ": 1.0},
            "event_weights": {"TQQQ": -1.0},
            "event": {
                "op": "calendar.nth_weekday_of_month",
                "weekday": "friday",
                "ordinal": 3,
                "months": [3],
            },
        },
        "fill_model": {"entry_price": "open", "exit_price": "close", "cost": {"transaction_cost": 0.001, "slippage": 0.0005}},
        "risk": {"allow_short": True, "max_gross_exposure": 1.0},
    }

    result = mod.MultiAssetPortfolioEngineBacktester(
        {"open": open_, "close": close},
        config,
    ).run()

    assert result.equity_curve["Trade_cost"].sum() > 0.0
    assert result.rebalance_trades["Allocated_cost"].sum() > 0.0
    assert result.validation_report["cost_accounting"]["configured_cost_rate"] == pytest.approx(0.0015)


def test_multi_asset_signal_change_does_not_rebalance_unchanged_target_weights():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    close = pd.DataFrame(
        {
            "AAA": [100.0, 120.0, 130.0, 130.0, 130.0],
            "BBB": [100.0, 100.0, 105.0, 110.0, 110.0],
        },
        index=dates,
    )
    target_weight = pd.DataFrame({"AAA": [0.5] * 5, "BBB": [0.5] * 5}, index=dates)
    config = {
        "strategy_id": "signal_change_constant_target",
        "universe": {"symbols": ["AAA", "BBB"]},
        "rebalance": {"trigger": {"op": "signal.change"}},
        "allocation": {"method": "signal_target_weight", "frame": "target_weight"},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = mod.MultiAssetPortfolioEngineBacktester(
        {"close": close, "target_weight": target_weight},
        config,
    ).run()

    assert len(result.rebalance_audit) == 1
    assert result.rebalance_audit["Turnover"].iloc[0] == pytest.approx(1.0)
    assert int((result.equity_curve["Turnover"] > 0.0).sum()) == 1
    assert result.equity_curve["Weight_AAA"].iloc[-1] > 0.5


def test_multi_asset_signal_change_rebalances_when_target_exits_to_cash():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-01", periods=4, freq="D")
    close = pd.DataFrame(
        {
            "AAA": [100.0, 101.0, 102.0, 103.0],
            "BBB": [100.0, 101.0, 102.0, 103.0],
        },
        index=dates,
    )
    target_weight = pd.DataFrame(
        {"AAA": [0.5, 0.5, 0.0, 0.0], "BBB": [0.5, 0.5, 0.0, 0.0]},
        index=dates,
    )
    config = {
        "strategy_id": "signal_change_exit_to_cash",
        "universe": {"symbols": ["AAA", "BBB"]},
        "rebalance": {"trigger": {"op": "signal.change"}},
        "allocation": {"method": "signal_target_weight", "frame": "target_weight"},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = mod.MultiAssetPortfolioEngineBacktester(
        {"close": close, "target_weight": target_weight},
        config,
    ).run()

    assert len(result.rebalance_audit) == 2
    assert result.rebalance_audit["Selected_count"].tolist() == [2, 0]
    assert result.equity_curve["Gross_exposure"].iloc[-1] == pytest.approx(0.0)


def test_single_asset_signal_change_uses_fast_target_weight_path():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    close = pd.DataFrame({"AAA": [100.0, 101.0, 102.0, 103.0, 104.0]}, index=dates)
    target_weight = pd.DataFrame({"AAA": [1.0, 1.0, 0.0, 0.0, 1.0]}, index=dates)
    config = {
        "strategy_id": "single_asset_signal_change_fast_path",
        "universe": {"symbols": ["AAA"]},
        "rebalance": {"trigger": {"op": "signal.change"}},
        "allocation": {"method": "signal_target_weight", "frame": "target_weight"},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = mod.MultiAssetPortfolioEngineBacktester(
        {"close": close, "target_weight": target_weight},
        config,
    ).run()

    assert result.validation_report["accounting_fast_path"] == "single_asset_target_weight_numpy"
    assert len(result.rebalance_audit) == 3
    assert result.rebalance_audit["Selected_count"].tolist() == [1, 0, 1]


def test_multi_asset_exporter_writes_run_validation_report(tmp_path):
    engine_mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    exporter_mod = importlib.import_module("backtester.MultiAssetPortfolioExporter_backtester")
    config = {
        "strategy_id": "validation_export_probe",
        "universe": {"symbols": ["AAA", "BBB", "CCC"]},
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "close",
            "rank_order": "desc",
            "top_n": 2,
        },
        "allocation": {"method": "equal_weight", "position_limit": 0.5},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = engine_mod.MultiAssetPortfolioEngineBacktester(_price_frames(), config).run()
    paths = exporter_mod.MultiAssetPortfolioExporterBacktester(
        result=result,
        output_dir=tmp_path,
        run_id="validation_probe",
    ).export()

    validation_paths = [Path(path) for path in paths if path.endswith("_run_validation_report.json")]
    assert len(validation_paths) == 1
    payload = json.loads(validation_paths[0].read_text(encoding="utf-8"))
    assert payload["status"] == "valid"
    assert payload["expected_symbols"] == ["AAA", "BBB", "CCC"]
    assert payload["universe_provenance"]["survivorship_bias_risk"] == "high"
    assert payload["artifact_consistency"]["equity_rows"] == len(result.equity_curve)
    metadata_paths = [Path(path) for path in paths if path.endswith("_metadata.json")]
    metadata = json.loads(metadata_paths[0].read_text(encoding="utf-8"))
    assert metadata["universe_provenance"] == payload["universe_provenance"]


def test_multi_asset_full_config_example_is_valid_json_and_engine_compatible():
    mod = importlib.import_module("backtester.MultiAssetPortfolioEngine_backtester")
    config_path = (
        _REPO_ROOT
        / "backtester"
        / "contracts"
        / "strategy"
        / "examples"
        / "multi-asset-portfolio-full-config-v1.json"
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["indicator_cache"] = {"mode": "memory"}
    config["universe"] = {"symbols": ["AAA", "BBB", "CCC"]}
    config["selection"]["top_n"] = 2
    config["selection"]["eligible"] = {"field": "close", "op": "gt", "value": 0}
    config["selection"]["rank_by"] = "momentum_63"
    config["allocation"]["position_limit"] = 0.5

    result = mod.MultiAssetPortfolioEngineBacktester(_price_frames(), config).run()

    assert result.strategy_id == "multi_asset_finlab_style_rotation_full"
    assert set(["Equity_value", "Portfolio_return", "Cash_weight", "Gross_exposure"]).issubset(
        result.equity_curve.columns
    )
    assert any(col.startswith("Contribution_") for col in result.equity_curve.columns)
    assert result.feature_cache["computed"] >= 5


def test_autorunner_dispatches_multi_asset_portfolio_and_exports(tmp_path):
    runner_mod = importlib.import_module("autorunner.BacktestRunner_autorunner")
    frames = _price_frames()
    close_path = tmp_path / "close.csv"
    volume_path = tmp_path / "volume.csv"
    frames["close"].reset_index(names="Time").to_csv(close_path, index=False)
    frames["volume"].reset_index(names="Time").to_csv(volume_path, index=False)

    config = {
        "dataloader": {"source": "multi_asset", "frequency": "1D", "start_date": "2024-01-02"},
        "backtester": {
            "strategy_mode": "multi_asset_portfolio",
            "Backtest_id": "ma_test",
            "market_data": {
                "close": {"path": str(close_path), "time_column": "Time"},
                "volume": {"path": str(volume_path), "time_column": "Time"},
            },
            "portfolio_config": {
                "strategy_id": "autorunner_multi_asset_probe",
                "universe": {"symbols": ["AAA", "BBB", "CCC"]},
                "computed_fields": [
                    {
                        "name": "momentum_2",
                        "op": "indicator.momentum",
                        "source": "close",
                        "period": 2,
                    }
                ],
                "rebalance": {"trigger": {"op": "calendar.month_start"}},
                "selection": {
                    "eligible": {"field": "close", "op": "gt", "value": 0},
                    "rank_by": "momentum_2",
                    "rank_order": "desc",
                    "top_n": 2,
                },
                "allocation": {"position_limit": 0.5},
                "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
            },
            "export_config": {
                "output_dir": str(tmp_path / "exports"),
                "export_parquet": True,
                "export_csv": False,
            },
        },
    }

    result = runner_mod.BacktestRunnerAutorunner().run_backtest(None, config)

    assert result["success"] is True
    assert result["strategy_mode"] == "multi_asset_portfolio"
    assert result["resolved_engine_mode"] == "unified_vector_hybrid"
    assert result["execution_plan"]["execution_backend"] == "vector_hybrid"
    assert result["engine_capabilities"]["portfolio_accounting"] is True
    assert any(path.endswith("_equity_curve.parquet") for path in result["exported_files"])
    assert any(path.endswith("_metadata.json") for path in result["exported_files"])


def test_unified_runner_accepts_normalized_strategy_run_portfolio(tmp_path):
    runner_mod = importlib.import_module("backtester.UnifiedBacktestRunner_backtester")
    frames = _price_frames()
    config = {
        "schema_version": "strategy_run",
        "platform": {
            "strategy_mode_id": "multi_asset_portfolio",
            "workflow_id": "single_backtest",
        },
        "data": {"frequency": "1D", "market_data": {"provider": "in_memory"}},
        "universe": {"symbols": ["AAA", "BBB", "CCC"]},
        "computed_fields": [
            {"name": "momentum_2", "op": "indicator.momentum", "source": "close", "period": 2}
        ],
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum_2",
            "rank_order": "desc",
            "top_n": 2,
        },
        "allocation": {"position_limit": 0.5},
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {"max_positions": 2, "max_gross_exposure": 1.0},
        "parameter_domains": {},
        "outputs": {"equity_curve": True},
        "metadata": {"strategy_id": "normalized_portfolio_probe"},
    }

    runner = runner_mod.UnifiedBacktestRunnerBacktester(
        market_data_loader=lambda _spec, _config_file_path: frames,
    )
    result = runner.run(data=None, config=config)

    assert result["success"] is True
    assert result["config"]["strategy_id"] == "normalized_portfolio_probe"
    assert "features" not in result["config"]
    assert [item["name"] for item in result["config"]["computed_fields"]] == ["momentum_2"]
    assert result["execution_plan"]["strategy_mode_id"] == "multi_asset_portfolio"
    assert result["portfolio_result"].equity_curve.empty is False
    assert result["portfolio_result"].feature_cache["computed"] >= 1


def test_unified_runner_rejects_mixed_features_and_indicators_strategy_run():
    runner_mod = importlib.import_module("backtester.UnifiedBacktestRunner_backtester")
    frames = _price_frames()
    config = {
        "schema_version": "strategy_run",
        "platform": {
            "strategy_mode_id": "multi_asset_portfolio",
            "workflow_id": "single_backtest",
        },
        "data": {"frequency": "1D", "market_data": {"provider": "in_memory"}},
        "universe": {"symbols": ["AAA", "BBB", "CCC"]},
        "features": [
            {"name": "legacy_momentum", "op": "indicator.momentum", "source": "close", "period": 2}
        ],
        "computed_fields": [
            {"name": "new_momentum", "op": "indicator.momentum", "source": "close", "period": 3}
        ],
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "new_momentum",
            "rank_order": "desc",
            "top_n": 2,
        },
        "allocation": {"position_limit": 0.5},
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {"max_positions": 2, "max_gross_exposure": 1.0},
        "metadata": {"strategy_id": "mixed_alias_runner_probe"},
    }
    runner = runner_mod.UnifiedBacktestRunnerBacktester(
        market_data_loader=lambda _spec, _config_file_path: frames,
    )

    with pytest.raises(ValueError, match="removed aliases"):
        runner.run(data=None, config=config)


def test_unified_runner_rejects_mixed_fill_model_and_execution_strategy_run():
    runner_mod = importlib.import_module("backtester.UnifiedBacktestRunner_backtester")
    frames = _price_frames()
    config = {
        "schema_version": "strategy_run",
        "platform": {
            "strategy_mode_id": "multi_asset_portfolio",
            "workflow_id": "single_backtest",
        },
        "data": {"frequency": "1D", "market_data": {"provider": "in_memory"}},
        "universe": {"symbols": ["AAA", "BBB", "CCC"]},
        "computed_fields": [
            {"name": "new_momentum", "op": "indicator.momentum", "source": "close", "period": 3}
        ],
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "new_momentum",
            "rank_order": "desc",
            "top_n": 2,
        },
        "allocation": {"position_limit": 0.5},
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "fill_model": {"entry_price": "close", "exit_price": "close", "cost": {"transaction_cost": 0.0}},
        "execution": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {"max_positions": 2, "max_gross_exposure": 1.0},
        "metadata": {"strategy_id": "mixed_fill_model_runner_probe"},
    }
    runner = runner_mod.UnifiedBacktestRunnerBacktester(
        market_data_loader=lambda _spec, _config_file_path: frames,
    )

    with pytest.raises(ValueError, match="fill_model"):
        runner.run(data=None, config=config)


def test_unified_runner_rejects_removed_features_alias_strategy_run():
    runner_mod = importlib.import_module("backtester.UnifiedBacktestRunner_backtester")
    frames = _price_frames()
    config = {
        "schema_version": "strategy_run",
        "platform": {
            "strategy_mode_id": "multi_asset_portfolio",
            "workflow_id": "single_backtest",
        },
        "data": {"frequency": "1D", "market_data": {"provider": "in_memory"}},
        "universe": {"symbols": ["AAA", "BBB", "CCC"]},
        "features": [
            {"name": "legacy_momentum", "op": "indicator.momentum", "source": "close", "period": 2}
        ],
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "legacy_momentum",
            "rank_order": "desc",
            "top_n": 2,
        },
        "allocation": {"position_limit": 0.5},
        "rebalance": {"trigger": {"op": "calendar.month_start"}},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {"max_positions": 2, "max_gross_exposure": 1.0},
        "metadata": {"strategy_id": "legacy_features_runner_probe"},
    }
    runner = runner_mod.UnifiedBacktestRunnerBacktester(
        market_data_loader=lambda _spec, _config_file_path: frames,
    )

    config_mod = importlib.import_module("backtester.StrategyRunConfig_backtester")
    with pytest.raises(config_mod.StrategyRunConfigError, match="removed aliases"):
        config_mod.normalize_strategy_run_config(copy.deepcopy(config))
    with pytest.raises(ValueError, match="removed aliases"):
        runner.run(data=None, config=config)


def test_voo_gld_strategy_run_rejects_removed_indicator_alias():
    config_mod = importlib.import_module("backtester.StrategyRunConfig_backtester")
    config_path = (
        _REPO_ROOT
        / "backtester"
        / "contracts"
        / "strategy"
        / "examples"
        / "strategy-run-voo-gld-yfinance-daily-momentum90-sma250-rotation-example.json"
    )
    new_config = json.loads(config_path.read_text(encoding="utf-8"))
    legacy_config = copy.deepcopy(new_config)
    legacy_config["indicators"] = legacy_config.pop("computed_fields")

    normalized_new = config_mod.normalize_strategy_run_config(copy.deepcopy(new_config))
    assert [item["name"] for item in normalized_new["computed_fields"]] == [
        "return_momentum",
        "sma_filter",
    ]
    with pytest.raises(config_mod.StrategyRunConfigError, match="removed aliases"):
        config_mod.normalize_strategy_run_config(copy.deepcopy(legacy_config))


def test_autorunner_accepts_strategy_run_multi_asset_primary_config(tmp_path):
    runner_mod = importlib.import_module("autorunner.BacktestRunner_autorunner")
    frames = _price_frames()
    close_path = tmp_path / "close.csv"
    frames["close"].reset_index(names="Time").to_csv(close_path, index=False)
    config = {
        "schema_version": "strategy_run",
        "platform": {
            "strategy_mode_id": "multi_asset_portfolio",
            "workflow_id": "parameter_matrix",
        },
        "data": {
            "frequency": "1D",
            "market_data": {"close": {"path": str(close_path), "time_column": "Time"}},
        },
        "universe": {"symbols": ["AAA", "BBB", "CCC"]},
        "computed_fields": [
            {"name": "momentum_2", "op": "indicator.momentum", "source": "close", "period": 2}
        ],
        "selection": {
            "eligible": {"field": "close", "op": "gt", "value": 0},
            "rank_by": "momentum_2",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"position_limit": 1.0},
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {"max_positions": 1, "max_gross_exposure": 1.0},
        "parameter_domains": {
            "lookback": {"type": "range", "start": 2, "end": 4, "step": 2}
        },
        "outputs": {"equity_curve": True},
        "metadata": {"strategy_id": "autorunner_strategy_run_multi_probe"},
    }

    result = runner_mod.BacktestRunnerAutorunner().run_backtest(None, config)

    assert result["success"] is True
    assert result["strategy_mode"] == "multi_asset_portfolio"
    assert result["requested_engine_mode"] == "multi_asset_portfolio"
    assert result["resolved_engine_mode"] == "unified_vector_hybrid"
    assert len(result["portfolio_results"]) == 2
    assert result["execution_plan"]["strategy_mode_id"] == "multi_asset_portfolio"


def test_autorunner_expands_multi_asset_parameter_domains(tmp_path):
    runner_mod = importlib.import_module("autorunner.BacktestRunner_autorunner")
    frames = _price_frames()
    close_path = tmp_path / "close.csv"
    frames["close"].reset_index(names="Time").to_csv(close_path, index=False)

    config = {
        "dataloader": {"source": "multi_asset", "frequency": "1D", "start_date": "2024-01-02"},
        "backtester": {
            "strategy_mode": "multi_asset_portfolio",
            "Backtest_id": "ma_matrix_test",
            "market_data": {"close": {"path": str(close_path), "time_column": "Time"}},
            "portfolio_config": {
                "strategy_id": "autorunner_multi_asset_matrix_probe",
                "universe": {"symbols": ["AAA", "BBB", "CCC"]},
                "parameter_domains": {
                    "lookback": {"type": "range", "start": 2, "end": 4, "step": 2},
                    "top_n": [1, 2],
                },
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
                    "top_n": {"param_ref": "top_n"},
                },
                "allocation": {"position_limit": 1.0},
                "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
            },
            "export_config": {
                "output_dir": str(tmp_path / "exports"),
                "export_parquet": True,
                "export_csv": False,
            },
        },
    }

    result = runner_mod.BacktestRunnerAutorunner().run_backtest(None, config)

    assert result["success"] is True
    assert len(result["portfolio_results"]) == 4
    assert sum(path.endswith("_metadata.json") for path in result["exported_files"]) == 4
    assert all(item.config.get("resolved_params") for item in result["portfolio_results"])
