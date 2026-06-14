from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dataloader.calculator_loader import ReturnCalculator
from dataloader.predictor_loader import PredictorLoader
from metricstracker.Base_metricstracker import BaseMetricTracker
from statanalyser.SeasonalAnalysis_statanalyser import SeasonalAnalysis


def test_return_calculator_initializes_return_arrays() -> None:
    data = pd.DataFrame(
        {
            "Open": [10.0, 11.0, 0.0, 12.0],
            "Close": [100.0, 110.0, 121.0, 0.0],
        }
    )

    result = ReturnCalculator(data).calculate_returns()

    np.testing.assert_allclose(result["open_return"], [0.0, 0.1, -1.0, 0.0])
    np.testing.assert_allclose(result["close_return"], [0.0, 0.1, 0.1, -1.0])
    np.testing.assert_allclose(result["open_logreturn"], [0.0, np.log(1.1), 0.0, 0.0])
    np.testing.assert_allclose(result["close_logreturn"], [0.0, np.log(1.1), np.log(1.1), 0.0])


@pytest.mark.regression
def test_predictor_loader_resolves_explicit_path(tmp_path) -> None:
    predictor_path = tmp_path / "predictor.csv"
    predictor_path.write_text("Time,predictor\n2024-01-01,1.0\n", encoding="utf-8")
    loader = PredictorLoader(pd.DataFrame({"Time": ["2024-01-01"], "Close": [100.0]}))
    loader.predictor_file_path = str(predictor_path)

    assert loader._get_file_path() == str(predictor_path)


@pytest.mark.regression
def test_metric_tracker_base_methods_fail_explicitly() -> None:
    tracker = BaseMetricTracker()

    assert BaseMetricTracker.get_steps()
    with pytest.raises(NotImplementedError):
        tracker.load_data("sample.parquet")
    with pytest.raises(NotImplementedError):
        tracker.calculate_metrics(pd.DataFrame())
    with pytest.raises(NotImplementedError):
        tracker.export(pd.DataFrame(), "out.parquet")


@pytest.mark.regression
def test_seasonal_analysis_short_series_returns_failure() -> None:
    data = pd.DataFrame(
        {
            "Time": pd.date_range("2024-01-01", periods=3, freq="D"),
            "factor": [1.0, 2.0, 3.0],
            "close_return": [0.0, 0.1, -0.1],
        }
    )

    result = SeasonalAnalysis(data, "factor", "close_return").analyze()

    assert result == {"success": False, "has_seasonal": False, "period": 0}
