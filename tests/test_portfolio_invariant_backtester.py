from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backtester.PortfolioInvariant_backtester import (
    PortfolioInvariantChecker,
    PortfolioInvariantError,
    PortfolioStateSnapshot,
    snapshots_from_multi_asset_result,
    snapshots_from_single_asset_result,
)
from backtester.TradeSimulator_backtester import TradeSimulator_backtester


def _snapshot(**overrides) -> PortfolioStateSnapshot:
    values = {
        "timestamp": "2026-01-01",
        "cash": 40.0,
        "market_value": 60.0,
        "equity": 100.0,
        "fees_paid": 1.0,
        "positions": {"AAA": 2.0},
        "prices": {"AAA": 30.0},
        "metadata": {"case": "test"},
    }
    values.update(overrides)
    return PortfolioStateSnapshot(**values)


def _codes(result):
    return {violation["code"] for violation in result.violations}


def test_valid_snapshot_passes() -> None:
    result = PortfolioInvariantChecker().check_snapshot(_snapshot())

    assert result.ok
    assert result.violations == []
    assert result.checked_count > 0


def test_equity_mismatch_fails() -> None:
    result = PortfolioInvariantChecker().check_snapshot(_snapshot(equity=99.0))

    assert not result.ok
    assert "equity_cash_market_value_mismatch" in _codes(result)


def test_market_value_mismatch_fails() -> None:
    result = PortfolioInvariantChecker().check_snapshot(_snapshot(market_value=61.0, equity=101.0))

    assert not result.ok
    assert "market_value_positions_prices_mismatch" in _codes(result)


def test_negative_fee_fails() -> None:
    result = PortfolioInvariantChecker().check_snapshot(_snapshot(fees_paid=-0.01))

    assert not result.ok
    assert "negative_fees_paid" in _codes(result)


def test_long_only_negative_position_fails() -> None:
    result = PortfolioInvariantChecker(long_only=True).check_snapshot(
        _snapshot(cash=130.0, market_value=-30.0, positions={"AAA": -1.0}, prices={"AAA": 30.0})
    )

    assert not result.ok
    assert "long_only_negative_position" in _codes(result)


def test_disallow_negative_cash_fails() -> None:
    result = PortfolioInvariantChecker(allow_negative_cash=False).check_snapshot(
        _snapshot(cash=-10.0, market_value=110.0, equity=100.0, positions={"AAA": 11.0}, prices={"AAA": 10.0})
    )

    assert not result.ok
    assert "negative_cash" in _codes(result)


def test_nan_and_inf_values_fail() -> None:
    result = PortfolioInvariantChecker().check_snapshot(
        _snapshot(cash=np.nan, positions={"AAA": np.inf}, prices={"AAA": 30.0})
    )

    assert not result.ok
    assert "non_finite_account_value" in _codes(result)
    assert "non_finite_position_qty" in _codes(result)


def test_tolerance_allows_small_rounding_difference() -> None:
    result = PortfolioInvariantChecker(tolerance=1e-6).check_snapshot(_snapshot(equity=100.0000004))

    assert result.ok


def test_multi_asset_market_value_passes() -> None:
    result = PortfolioInvariantChecker().check_snapshot(
        PortfolioStateSnapshot(
            timestamp="multi",
            cash=20.0,
            market_value=80.0,
            equity=100.0,
            fees_paid=0.0,
            positions={"AAA": 2.0, "BBB": 3.0},
            prices={"AAA": 10.0, "BBB": 20.0},
        )
    )

    assert result.ok


def test_max_gross_exposure_fails() -> None:
    result = PortfolioInvariantChecker(max_gross_exposure=1.0).check_snapshot(
        _snapshot(cash=-20.0, market_value=120.0, equity=100.0, positions={"AAA": 4.0}, prices={"AAA": 30.0})
    )

    assert not result.ok
    assert "max_gross_exposure_exceeded" in _codes(result)


def test_check_series_collects_all_violations_with_timestamps() -> None:
    snapshots = [
        _snapshot(timestamp="ok"),
        _snapshot(timestamp="bad-equity", equity=99.0),
        _snapshot(timestamp="bad-fee", fees_paid=-1.0),
    ]

    result = PortfolioInvariantChecker().check_series(snapshots)

    assert not result.ok
    assert len(result.violations) == 2
    assert {item["timestamp"] for item in result.violations} == {"bad-equity", "bad-fee"}


def test_raise_on_error_mode_raises() -> None:
    checker = PortfolioInvariantChecker(raise_on_error=True)

    with pytest.raises(PortfolioInvariantError):
        checker.check_snapshot(_snapshot(equity=1.0))


def test_trade_records_best_effort_checks_fees_and_position_delta() -> None:
    records = pd.DataFrame(
        [
            {"Time": "t1", "Asset": "AAA", "Trade_delta": 1.0, "Allocated_cost": 0.1},
            {"Time": "t2", "Asset": "AAA", "Trade_delta": 1.0, "Allocated_cost": 0.2},
        ]
    )
    snapshots = [_snapshot(positions={"AAA": 2.0}, prices={"AAA": 30.0})]

    result = PortfolioInvariantChecker().check_trade_records(records, snapshots=snapshots)

    assert result.ok
    assert result.checked_count >= 3


def test_trade_records_negative_fee_fails_without_breaking_old_schema() -> None:
    records = pd.DataFrame([{"Time": "t1", "Asset": "AAA", "Allocated_cost": -0.1}])

    result = PortfolioInvariantChecker().check_trade_records(records)

    assert not result.ok
    assert "trade_record_negative_or_non_finite_fee" in _codes(result)


def test_snapshots_from_multi_asset_result_uses_weight_space() -> None:
    result_obj = type(
        "Result",
        (),
        {
            "equity_curve": pd.DataFrame(
                [
                    {
                        "Time": "t1",
                        "Equity_value": 100.0,
                        "Cash_weight": 0.25,
                        "Weight_AAA": 0.50,
                        "Weight_BBB": 0.25,
                        "Trade_cost": 0.1,
                    }
                ]
            )
        },
    )()

    snapshots = snapshots_from_multi_asset_result(result_obj)
    check = PortfolioInvariantChecker(long_only=True).check_series(snapshots)

    assert len(snapshots) == 1
    assert snapshots[0].metadata["position_unit"] == "portfolio_weight"
    assert check.ok


def test_existing_oracle_trade_result_can_be_minimally_checked() -> None:
    data = pd.DataFrame(
        {
            "Open": [100.0, 110.0, 120.0],
            "High": [100.0, 110.0, 120.0],
            "Low": [100.0, 110.0, 120.0],
            "Close": [100.0, 110.0, 120.0],
        }
    )
    entry = pd.Series([1.0, 0.0, 0.0], dtype=np.float64)
    exit_ = pd.Series([0.0, -1.0, 0.0], dtype=np.float64)
    simulator = TradeSimulator_backtester(
        data=data,
        entry_signal=entry,
        exit_signal=exit_,
        transaction_cost=0.0,
        slippage=0.0,
        trade_delay=0,
        trade_price="close",
        Backtest_id="oracle",
        predictor="X",
        trading_instrument="TEST",
    )
    legacy_result = simulator.simulate_trades_sequential(
        entry_signal=entry.to_numpy(dtype=np.float64),
        exit_signal=exit_.to_numpy(dtype=np.float64),
        trading_params={"transaction_cost": 0.0, "slippage": 0.0, "trade_delay": 0, "trade_price": "close"},
    )

    snapshots = snapshots_from_single_asset_result(legacy_result)
    result = PortfolioInvariantChecker().check_series(snapshots)

    assert snapshots
    assert result.ok
    assert result.skipped_count >= 1
