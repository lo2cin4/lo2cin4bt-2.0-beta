from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metricstracker.MetricsCalculator_metricstracker import MetricsCalculatorMetricTracker
from metricstracker.MetricsExporter_metricstracker import MetricsExporter


def _sample_df():
    rows = []
    for backtest_id, closes in [("b2", [100, 110, 105, 120]), ("a1", [100, 95, 98, 102])]:
        equity = [100.0]
        actions = [0.0, 1.0, 0.0, 4.0]
        trade_returns = [float("nan"), float("nan"), float("nan"), (closes[-1] / closes[1]) - 1.0]
        positions = [0.0, 1.0, 1.0, 0.0]
        for i, close in enumerate(closes):
            if i > 0:
                equity.append(equity[-1] * (1.0 + ((close / closes[i - 1]) - 1.0) * (1.0 if positions[i - 1] != 0 else 0.0)))
            rows.append(
                {
                    "Time": pd.Timestamp("2020-01-01") + pd.Timedelta(days=i),
                    "Backtest_id": backtest_id,
                    "Equity_value": equity[i],
                    "Close": float(close),
                    "Trade_action": actions[i],
                    "Trade_return": trade_returns[i],
                    "Position_size": positions[i],
                }
            )
    return pd.DataFrame(rows)


def test_ensure_group_order_keeps_contiguous_blocks():
    df = _sample_df()
    ordered = MetricsExporter._ensure_group_order(df)
    assert ordered["Backtest_id"].tolist() == df["Backtest_id"].tolist()


def test_batch_metadata_matches_calculator_for_key_metrics():
    df = MetricsExporter.add_drawdown_bah(_sample_df())
    batch_metadata, ordered = MetricsExporter._compute_batch_metadata(df, 252, 0.02)
    metadata_by_id = {row["Backtest_id"]: row for row in batch_metadata}

    for backtest_id, group in ordered.groupby("Backtest_id"):
        calc = MetricsCalculatorMetricTracker(group, 252, 0.02)
        expected = calc.calc_strategy_metrics()
        actual = metadata_by_id[backtest_id]
        for key in ["Total_return", "Sharpe", "Max_drawdown", "Trade_count", "Win_rate", "Profit_factor"]:
            expected_value = expected[key]
            actual_value = actual[key]
            if expected_value is None:
                assert pd.isna(actual_value)
            else:
                assert actual_value == pytest.approx(expected_value, rel=1e-6, abs=1e-6, nan_ok=True)
