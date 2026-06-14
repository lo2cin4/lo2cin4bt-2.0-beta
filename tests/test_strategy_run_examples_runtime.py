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
from backtester.StrategyRunConfig_backtester import normalize_strategy_run_config
from backtester.UnifiedBacktestRunner_backtester import UnifiedBacktestRunnerBacktester


def _load_example(name: str) -> dict:
    path = _REPO_ROOT / "backtester" / "contracts" / "strategy" / "examples" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _with_full_matrix_retention(config: dict) -> dict:
    out = copy.deepcopy(config)
    out.setdefault("fill_model", {})["matrix_result_retention"] = 10000
    out["fill_model"]["matrix_workers"] = 1
    return out


def _frames_for_symbols(symbols: list[str], *, periods: int = 420) -> dict[str, pd.DataFrame]:
    dates = pd.date_range("2020-01-02", periods=periods, freq="B")
    close_values = {}
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


def _runner_for_frames(frames: dict[str, pd.DataFrame]) -> UnifiedBacktestRunnerBacktester:
    autorunner = BacktestRunnerAutorunner()
    return UnifiedBacktestRunnerBacktester(
        market_data_loader=lambda _spec, _config_file_path: frames,
        portfolio_variant_expander=autorunner._expand_portfolio_configs,
    )


def _run_example(config: dict):
    symbols = [str(item).upper() for item in config["universe"]["symbols"]]
    runner = _runner_for_frames(_frames_for_symbols(symbols))
    return runner.run(data=None, config=copy.deepcopy(config))


def test_btcusdt_dual_ma_example_uses_computed_fields_all_variants():
    config = _with_full_matrix_retention(
        _load_example("strategy-run-btcusdt-binance-daily-dual-ma-example.json")
    )

    result = _run_example(config)

    assert len(result["portfolio_results"]) == 187
    assert result["portfolio_results"][0].feature_cache["computed"] == 2
    assert result["portfolio_results"][0].rebalance_trades.empty is False


def test_qqq_sma_cross_example_uses_computed_fields_all_variants():
    config = _with_full_matrix_retention(
        _load_example("strategy-run-qqq-yfinance-daily-sma-cross-matrix-example.json")
    )

    result = _run_example(config)

    assert len(result["portfolio_results"]) == 171
    assert result["portfolio_results"][0].feature_cache["computed"] == 2
    assert result["portfolio_results"][0].rebalance_trades.empty is False


def test_strategy_run_examples_reject_removed_indicator_aliases():
    config = _load_example("strategy-run-qqq-yfinance-daily-sma-cross-matrix-example.json")
    config["indicators"] = config.pop("computed_fields")

    with pytest.raises(Exception, match="removed aliases"):
        normalize_strategy_run_config(copy.deepcopy(config))


def test_btcusdt_monthly_nth_weekday_same_session_example_runs_full_matrix():
    config = _with_full_matrix_retention(
        _load_example("strategy-run-btcusdt-binance-monthly-nth-weekday-same-session-matrix-example.json")
    )

    result = _run_example(config)

    assert len(result["portfolio_results"]) == 28
    assert result["portfolio_results"][0].feature_cache["computed"] == 0
    assert result["portfolio_results"][0].validation_report["status"] == "valid"
    assert any(not item.rebalance_trades.empty for item in result["portfolio_results"])


def test_vti_avuv_vxus_sgol_dbmf_yearly_rebalance_example_runs():
    config = _with_full_matrix_retention(
        _load_example("strategy-run-vti-avuv-vxus-sgol-dbmf-yfinance-yearly-rebalance-example.json")
    )

    result = _run_example(config)
    portfolio_result = result["portfolio_results"][0]

    assert len(result["portfolio_results"]) == 1
    assert portfolio_result.feature_cache["computed"] == 0
    assert portfolio_result.validation_report["status"] == "valid"
    assert portfolio_result.rebalance_trades.empty is False
    assert float(portfolio_result.equity_curve["Equity_value"].iloc[-1]) > 0.0
