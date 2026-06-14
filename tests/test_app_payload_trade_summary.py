from pathlib import Path
import sys

import pandas as pd
import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def test_trade_summary_pnl_uses_round_trip_equity_delta():
    from app.api.payloads import AppPayloadService

    service = AppPayloadService.__new__(AppPayloadService)
    rows = pd.DataFrame(
        [
            {
                "Time": pd.Timestamp("2026-03-20"),
                "Open_time": pd.Timestamp("2026-03-20"),
                "Close_time": pd.NaT,
                "Trade_group_id": "T1",
                "Trade_action": 1,
                "Trading_instrument": "QQQ",
                "Position_type": "new_short",
                "Position_size": -1.0,
                "Open_position_price": 591.06,
                "Close_position_price": 0.0,
                "Holding_period": None,
                "Trade_return": None,
                "Return": -0.0015,
                "Equity_value": 100.582101,
            },
            {
                "Time": pd.Timestamp("2026-03-20"),
                "Open_time": pd.Timestamp("2026-03-20"),
                "Close_time": pd.Timestamp("2026-03-20"),
                "Trade_group_id": "T1",
                "Trade_action": 4,
                "Trading_instrument": "QQQ",
                "Position_type": "close_short",
                "Position_size": 0.0,
                "Open_position_price": 591.06,
                "Close_position_price": 582.06,
                "Holding_period": 1,
                "Trade_return": 0.015227,
                "Return": 0.013705,
                "Equity_value": 101.960585,
            },
        ]
    )

    summary = service._build_trade_summary_rows(rows)  # pylint: disable=protected-access

    assert len(summary) == 1
    assert round(summary[0]["pnl"], 6) == round(101.960585 - (100.582101 / 0.9985), 6)
    assert round(summary[0]["equity_pnl"], 6) == round(summary[0]["pnl"], 6)
    assert round(summary[0]["equity_value"], 6) == 101.960585
    assert round(summary[0]["entry_equity_value"], 6) == 100.582101
    assert round(summary[0]["pre_entry_equity_value"], 6) == round(100.582101 / 0.9985, 6)
    assert round(summary[0]["price_pnl"], 6) == 9.0

    outcome = service._build_trade_outcome_summary(summary)  # pylint: disable=protected-access
    assert outcome["closed_trade_count"] == 1
    assert outcome["win_count"] == 1
    assert outcome["chart_ready"] is False
    assert outcome["insufficient_data"] is True


def test_trade_outcome_summary_classifies_ready_closed_trades():
    from app.api.payloads import AppPayloadService

    service = AppPayloadService.__new__(AppPayloadService)
    rows = [
        {"trade_return": 0.02, "status": "closed", "exit_time": "2026-01-01"},
        {"trade_return": -0.01, "status": "closed", "exit_time": "2026-01-02"},
        {"trade_return": 0.0, "status": "closed", "exit_time": "2026-01-03"},
        {"trade_return": 0.015, "status": "closed", "exit_time": "2026-01-04"},
        {"trade_return": -0.005, "status": "closed", "exit_time": "2026-01-05"},
        {"trade_return": 0.04, "status": "open", "exit_time": ""},
    ]

    outcome = service._build_trade_outcome_summary(rows)  # pylint: disable=protected-access

    assert outcome["available"] is True
    assert outcome["display_state"] == "ready"
    assert outcome["closed_trade_count"] == 5
    assert outcome["win_count"] == 2
    assert outcome["loss_count"] == 2
    assert outcome["breakeven_count"] == 1
    assert outcome["chart_ready"] is True
    assert outcome["profit_factor"] == pytest.approx(0.035 / 0.015)
    assert sum(bucket["count"] for bucket in outcome["histogram_bins"]) == 5


def test_risk_diagnostics_builds_serial_concentration_and_recovery_payloads():
    from app.api.payloads import AppPayloadService

    service = AppPayloadService.__new__(AppPayloadService)
    trade_rows = [
        {"trade_return": value, "status": "closed", "exit_time": f"2026-01-{index + 1:02d}"}
        for index, value in enumerate([0.01, 0.03, -0.02, 0.02, -0.01, 0.04])
    ]
    equity_series = [
        {"time": f"2026-02-{index + 1:02d}", "value": value}
        for index, value in enumerate([100.0, 90.0, 95.0, 101.0, 98.0, 102.0, 99.0])
    ]

    diagnostics = service._build_risk_diagnostics(  # pylint: disable=protected-access
        trade_rows=trade_rows,
        equity_series=equity_series,
    )

    serial = diagnostics["serial_correlation"]
    assert serial["available"] is True
    assert serial["observation_count"] == 6
    assert serial["significance_band"] == pytest.approx(1.96 / (6**0.5))
    assert serial["lags"][0]["lag"] == 1
    assert serial["lags"][0]["pair_count"] == 5
    assert serial["lags"][0]["acf"] == pytest.approx(-0.6221532091)

    concentration = diagnostics["profit_concentration"]
    assert concentration["available"] is True
    assert concentration["profitable_trade_count"] == 4
    assert concentration["top_20_count"] == 1
    assert concentration["top_20_contribution"] == pytest.approx(0.04 / 0.10)
    assert concentration["lorenz_curve"][0] == {"trade_share": 0.0, "profit_share": 0.0}
    assert concentration["lorenz_curve"][-1] == {"trade_share": 1.0, "profit_share": 1.0}

    recovery = diagnostics["recovery_time"]
    assert recovery["available"] is True
    assert recovery["recovered_count"] == 2
    assert recovery["unrecovered_count"] == 1
    assert recovery["percentiles"]["p50_periods"] == pytest.approx(2.5)
    assert recovery["percentiles"]["max_periods"] == 3
    assert sum(bucket["count"] for bucket in recovery["histogram_bins"]) == 2


def test_risk_diagnostics_falls_back_to_equity_returns_without_closed_trades():
    from app.api.payloads import AppPayloadService

    service = AppPayloadService.__new__(AppPayloadService)
    equity_series = [
        {"time": f"2026-03-{index + 1:02d}", "value": value}
        for index, value in enumerate([100.0, 103.0, 101.0, 106.0, 104.0, 109.0, 111.0])
    ]

    diagnostics = service._build_risk_diagnostics(  # pylint: disable=protected-access
        trade_rows=[],
        equity_series=equity_series,
    )

    serial = diagnostics["serial_correlation"]
    assert serial["available"] is True
    assert serial["return_source"] == "equity_periods"
    assert serial["observation_count"] == 6
    assert serial["lags"][0]["lag"] == 1

    concentration = diagnostics["profit_concentration"]
    assert concentration["available"] is True
    assert concentration["return_source"] == "equity_periods"
    assert concentration["profitable_trade_count"] == 4
    assert concentration["top_20_count"] == 1
    assert concentration["top_20_contribution"] is not None
    assert diagnostics["available"]["serial_correlation"] is True
    assert diagnostics["available"]["profit_concentration"] is True


def test_risk_diagnostics_does_not_report_acf_for_tiny_closed_trade_sample():
    from app.api.payloads import AppPayloadService

    service = AppPayloadService.__new__(AppPayloadService)
    trade_rows = [
        {"trade_return": value, "status": "closed", "exit_time": f"2026-04-{index + 1:02d}"}
        for index, value in enumerate([0.01, -0.02, 0.03])
    ]
    equity_series = [
        {"time": f"2026-04-{index + 1:02d}", "value": value}
        for index, value in enumerate([100.0, 101.0, 102.0, 101.5, 103.0, 104.0, 104.5])
    ]

    diagnostics = service._build_risk_diagnostics(  # pylint: disable=protected-access
        trade_rows=trade_rows,
        equity_series=equity_series,
    )

    serial = diagnostics["serial_correlation"]
    assert serial["available"] is False
    assert serial["return_source"] == "closed_trades"
    assert serial["reason"] == "insufficient_observations_for_acf"
    assert serial["observation_count"] == 3
    assert serial["lags"] == []
    assert serial["lag1"] is None
    assert diagnostics["available"]["serial_correlation"] is False


def test_single_asset_portfolio_rebalance_trades_build_closed_trade_outcomes():
    from app.api.payloads import AppPayloadService

    service = AppPayloadService.__new__(AppPayloadService)
    equity = pd.DataFrame(
        {
            "Time": pd.to_datetime(
                ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05", "2026-01-06"]
            ),
            "Equity_value": [100.0, 105.0, 105.0, 103.0, 103.0, 106.0],
            "Portfolio_return": [0.0, 0.05, 0.0, -0.019047619, 0.0, 0.029126214],
        }
    )
    rebalance_trades = pd.DataFrame(
        {
            "Time": pd.to_datetime(
                ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05", "2026-01-06"]
            ),
            "Asset": ["ETHUSDT"] * 6,
            "Before_weight": [0.0, 1.0, 0.0, 1.0, 0.0, 1.0],
            "Target_weight": [1.0, 0.0, 1.0, 0.0, 1.0, 0.0],
            "Trade_delta": [1.0, -1.0, 1.0, -1.0, 1.0, -1.0],
            "Action": ["buy", "exit", "buy", "exit", "buy", "exit"],
        }
    )

    rows = service._single_asset_portfolio_trade_rows(equity, rebalance_trades)  # pylint: disable=protected-access
    outcome = service._build_trade_outcome_summary(rows)  # pylint: disable=protected-access

    assert len(rows) == 3
    assert rows[0]["status"] == "closed"
    assert rows[0]["trade_return"] == pytest.approx(0.05)
    assert outcome["closed_trade_count"] == 3
    assert outcome["insufficient_data"] is True


def test_portfolio_rebalance_trades_build_price_based_long_and_short_outcomes():
    from app.api.payloads import AppPayloadService

    service = AppPayloadService.__new__(AppPayloadService)
    equity = pd.DataFrame(
        {
            "Time": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "Equity_value": [100.0, 105.0, 103.0],
            "Portfolio_return": [0.0, 0.05, -0.019047619],
        }
    )
    rebalance_trades = pd.DataFrame(
        {
            "Time": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-02", "2026-01-02"]),
            "Asset": ["QQQ", "QQQ", "TQQQ", "TQQQ"],
            "Before_weight": [0.0, 1.0, 0.0, -1.0],
            "Target_weight": [1.0, 0.0, -1.0, 0.0],
            "Trade_delta": [1.0, -1.0, -1.0, 1.0],
            "Action": ["buy", "exit", "new_short", "close_short"],
            "Entry_price": [100.0, 110.0, 50.0, 50.0],
            "Exit_price": [None, None, None, 45.0],
            "Trade_return": [None, None, None, 0.10],
        }
    )

    rows = service._single_asset_portfolio_trade_rows(equity, rebalance_trades)  # pylint: disable=protected-access
    outcome = service._build_trade_outcome_summary(rows)  # pylint: disable=protected-access

    assert len(rows) == 2
    assert rows[0]["asset"] == "QQQ"
    assert rows[0]["side"] == "long"
    assert rows[0]["trade_return"] == pytest.approx(0.10)
    assert rows[0]["trade_return_source"] == "entry_exit_price"
    assert rows[1]["asset"] == "TQQQ"
    assert rows[1]["side"] == "short"
    assert rows[1]["trade_return"] == pytest.approx(0.10)
    assert rows[1]["trade_return_source"] == "artifact_trade_return"
    assert outcome["closed_trade_count"] == 2
    assert outcome["win_count"] == 2
