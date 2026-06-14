from __future__ import annotations

import importlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


pytestmark = pytest.mark.smoke


def test_statanalyser_autorunner_stage_runs_from_config(tmp_path) -> None:
    config_path = tmp_path / "statanalyser_config.json"

    config = {
        "dataloader": {
            "source": "file",
            "start_date": "2020-01-01",
            "file_config": {"file_path": "placeholder.csv"},
        },
            "backtester": {
                "strategy_mode": "semantic",
                "strategy_contract_path": "workspace/strategies/strategy-vix-regime-ma-cross-sweep.user.json",
                "export_config": {"export_csv": False, "export_excel": False},
            },
        "metricstracker": {"enable_metrics_analysis": False},
        "statanalyser": {
            "enabled": True,
            "target": {
                "predictor_column": "X",
                "return_column": "close_return",
                "diff_mode": "none",
            },
            "tests": {
                "stationarity": {
                    "enabled": True,
                    "output": ["summary", "decision"],
                },
                "correlation": {
                    "enabled": True,
                    "output": ["matrix", "summary"],
                },
                "autocorrelation": {
                    "enabled": True,
                    "lags": [1, 2, 3, 4, 5, 6, 7, 8],
                    "output": ["acf", "pacf", "summary"],
                },
                "distribution": {
                    "enabled": True,
                    "output": ["summary", "histogram"],
                },
                "seasonality": {
                    "enabled": True,
                    "output": ["summary"],
                },
            },
            "report": {
                "formats": ["md", "json"],
                "output_dir": str(tmp_path / "statanalyser_output"),
                "include_plots": False,
                "include_raw_tables": False,
                "fail_on_error": False,
            },
        },
    }
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    ConfigValidator = importlib.import_module(
        "autorunner.ConfigValidator_autorunner"
    ).ConfigValidator
    StatAnalyserRunnerAutorunner = importlib.import_module(
        "autorunner.StatAnalyserRunner_autorunner"
    ).StatAnalyserRunnerAutorunner

    assert ConfigValidator().validate_config(str(config_path)) is True

    dates = pd.date_range("2020-01-01", periods=120, freq="D")
    x = np.sin(np.linspace(0, 12 * np.pi, 120)) + np.linspace(0, 1, 120) / 10
    close_return = np.cos(np.linspace(0, 8 * np.pi, 120)) / 10
    data = pd.DataFrame(
        {
            "Time": dates,
            "X": x,
            "close_return": close_return,
        }
    )

    runner = StatAnalyserRunnerAutorunner()
    summary = runner.run(data, config)

    assert summary is not None
    assert summary["enabled"] is True
    assert summary["executed"] is True
    assert summary["failed"] == 0
    assert set(summary["results"]) == {
        "StationarityTest",
        "CorrelationTest",
        "AutocorrelationTest",
        "DistributionTest",
        "SeasonalAnalysis",
    }

    output_dir = Path(summary["report_paths"][0]).parent
    assert output_dir.exists()
    assert (output_dir / "statanalyser_report.md").exists()
    assert (output_dir / "statanalyser_summary.json").exists()
