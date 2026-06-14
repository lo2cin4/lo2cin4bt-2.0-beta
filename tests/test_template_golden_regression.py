from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from autorunner.BacktestRunner_autorunner import BacktestRunnerAutorunner
from backtester.UnifiedBacktestRunner_backtester import UnifiedBacktestRunnerBacktester


pytestmark = pytest.mark.regression


EXAMPLE_ROOT = _REPO_ROOT / "backtester" / "contracts" / "strategy" / "examples"


GOLDEN_CASES = [
    {
        "case_id": "moving_average_btcusdt_dual_ma_matrix",
        "example": "strategy-run-btcusdt-binance-daily-dual-ma-example.json",
        "strategy_family": "moving_average",
        "variant_count": 187,
        "first_final_equity": 50.3383979654,
        "best_final_equity": 412.6239363037,
        "worst_final_equity": 50.3383979654,
        "first_trade_count": 16,
        "total_trade_count": 2203,
        "first_backend": "single_asset_next_bar_open_target_weight_numpy",
        "first_cost_status": "valid",
    },
    {
        "case_id": "moving_average_qqq_sma_cross_matrix",
        "example": "strategy-run-qqq-yfinance-daily-sma-cross-matrix-example.json",
        "strategy_family": "moving_average",
        "variant_count": 171,
        "first_final_equity": 57.9711865831,
        "best_final_equity": 103.4588044238,
        "worst_final_equity": 57.7865999013,
        "first_trade_count": 13,
        "total_trade_count": 624,
        "first_backend": "single_asset_next_bar_open_target_weight_numpy",
        "first_cost_status": "valid",
    },
    {
        "case_id": "calendar_monthly_nth_weekday_same_session_matrix",
        "example": "strategy-run-btcusdt-binance-monthly-nth-weekday-same-session-matrix-example.json",
        "strategy_family": "calendar_effect",
        "variant_count": 28,
        "first_final_equity": 104.1045838142,
        "best_final_equity": 104.1045838142,
        "worst_final_equity": 100.0,
        "first_trade_count": 40,
        "total_trade_count": 774,
        "first_backend": "same_session",
        "first_cost_status": "valid",
    },
    {
        "case_id": "scheduled_yearly_fixed_allocation_rebalance",
        "example": "strategy-run-vti-avuv-vxus-sgol-dbmf-yfinance-yearly-rebalance-example.json",
        "strategy_family": "scheduled_rebalance",
        "variant_count": 1,
        "first_final_equity": 119.1671103734,
        "best_final_equity": 119.1671103734,
        "worst_final_equity": 119.1671103734,
        "first_trade_count": 10,
        "total_trade_count": 10,
        "first_backend": None,
        "first_cost_status": "valid",
    },
    {
        "case_id": "momentum_voo_gld_rotation",
        "example": "strategy-run-voo-gld-yfinance-daily-momentum90-sma250-rotation-example.json",
        "strategy_family": "momentum_rotation",
        "variant_count": 1,
        "first_final_equity": 90.332488484,
        "best_final_equity": 90.332488484,
        "worst_final_equity": 90.332488484,
        "first_trade_count": 181,
        "total_trade_count": 181,
        "first_backend": "daily_rank_numpy",
        "first_cost_status": "valid",
    },
]


def _load_example(name: str) -> dict:
    return json.loads((EXAMPLE_ROOT / name).read_text(encoding="utf-8"))


def _with_full_matrix_retention(config: dict) -> dict:
    out = copy.deepcopy(config)
    out.setdefault("fill_model", {})["matrix_result_retention"] = 10000
    out["fill_model"]["matrix_workers"] = 1
    return out


def _frames_for_symbols(symbols: list[str], *, periods: int = 420) -> dict[str, pd.DataFrame]:
    dates = pd.date_range("2020-01-02", periods=periods, freq="B")
    close_values: dict[str, list[float]] = {}
    for index, symbol in enumerate(symbols):
        if len(symbols) == 1:
            close_values[symbol] = [
                100.0 + row * 0.03 + (row % 41) * 0.6 - (row % 17) * 0.25
                for row in range(len(dates))
            ]
        else:
            base = 100.0 + 15.0 * index
            close_values[symbol] = [
                base
                + row * (0.04 + 0.01 * index)
                + ((row + index * 7) % 31) * 0.11
                + (row % 13) * 0.03
                for row in range(len(dates))
            ]
    close = pd.DataFrame(close_values, index=dates)
    return {
        "open": close * 0.995,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": pd.DataFrame(1_000_000, index=dates, columns=close.columns),
    }


def _run_example(config: dict) -> dict:
    symbols = [str(item).upper() for item in config["universe"]["symbols"]]
    frames = _frames_for_symbols(symbols)
    autorunner = BacktestRunnerAutorunner()
    runner = UnifiedBacktestRunnerBacktester(
        market_data_loader=lambda _spec, _config_file_path: frames,
        portfolio_variant_expander=autorunner._expand_portfolio_configs,
    )
    return runner.run(data=None, config=copy.deepcopy(config))


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[case["case_id"] for case in GOLDEN_CASES])
def test_public_strategy_templates_match_golden_regression(case: dict) -> None:
    config = _with_full_matrix_retention(_load_example(str(case["example"])))

    result = _run_example(config)
    portfolio_results = result["portfolio_results"]
    final_equities = [
        float(portfolio_result.equity_curve["Equity_value"].iloc[-1])
        for portfolio_result in portfolio_results
    ]
    trade_counts = [
        0
        if portfolio_result.rebalance_trades is None or portfolio_result.rebalance_trades.empty
        else len(portfolio_result.rebalance_trades)
        for portfolio_result in portfolio_results
    ]
    first_report = portfolio_results[0].validation_report
    first_backend = first_report.get("accounting_backend") or first_report.get("accounting_fast_path")
    first_cost_status = (first_report.get("cost_accounting") or {}).get("status")

    assert len(portfolio_results) == case["variant_count"]
    assert sum(1 for item in portfolio_results if item.validation_report.get("status") == "valid") == case["variant_count"]
    assert final_equities[0] == pytest.approx(case["first_final_equity"], abs=1e-9)
    assert max(final_equities) == pytest.approx(case["best_final_equity"], abs=1e-9)
    assert min(final_equities) == pytest.approx(case["worst_final_equity"], abs=1e-9)
    assert trade_counts[0] == case["first_trade_count"]
    assert sum(trade_counts) == case["total_trade_count"]
    assert first_backend == case["first_backend"]
    assert first_cost_status == case["first_cost_status"]


def test_golden_regression_covers_main_public_strategy_families() -> None:
    assert {case["strategy_family"] for case in GOLDEN_CASES} == {
        "moving_average",
        "calendar_effect",
        "scheduled_rebalance",
        "momentum_rotation",
    }
