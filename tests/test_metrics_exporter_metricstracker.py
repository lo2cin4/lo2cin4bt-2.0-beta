from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from metricstracker.MetricsCalculator_metricstracker import MetricsCalculatorMetricTracker
from metricstracker.MetricsExporter_metricstracker import MetricsExporter

pytestmark = pytest.mark.regression


def test_metrics_exporter_recomputes_batch_metadata_without_stale_merge(tmp_path: Path) -> None:
    backtester_dir = tmp_path / "outputs" / "backtester"
    backtester_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = backtester_dir / "sample.parquet"

    df = pd.DataFrame(
        {
            "Time": pd.date_range("2024-01-01", periods=6, freq="D"),
            "Equity_value": [100.0, 102.0, 101.0, 104.0, 108.0, 107.0],
            "Close": [100.0, 101.0, 100.0, 103.0, 107.0, 106.0],
            "Backtest_id": ["bt-1"] * 6,
        }
    )

    stale_metadata = [{"Backtest_id": "bt-1", "Sharpe": 999.0}]
    table = pa.Table.from_pandas(df).replace_schema_metadata(
        {b"batch_metadata": json.dumps(stale_metadata).encode("utf-8")}
    )
    pq.write_table(table, parquet_path)

    MetricsExporter.export(df, str(parquet_path), time_unit=252, risk_free_rate=0.0)

    metadata_path = tmp_path / "outputs" / "metricstracker" / "sample_metadata.json"
    metrics_path = tmp_path / "outputs" / "metricstracker" / "sample_metrics.parquet"

    assert metadata_path.exists()
    assert metrics_path.exists()

    rows = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["Backtest_id"] == "bt-1"
    assert rows[0]["Sharpe"] != 999.0

    expected = MetricsCalculatorMetricTracker(
        MetricsExporter.add_drawdown_bah(df),
        time_unit=252,
        risk_free_rate=0.0,
    ).calc_strategy_metrics()["Sharpe"]
    assert abs(rows[0]["Sharpe"] - expected) < 1e-9

    written = pq.read_table(metrics_path)
    assert b"batch_metadata" not in (written.schema.metadata or {})
