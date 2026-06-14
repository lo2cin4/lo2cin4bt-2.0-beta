import json
import subprocess
import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]


def _check_package_slim(package_name: str, legacy_exports: list[str]) -> None:
    script = f"""
import importlib
import json
import sys
sys.path.insert(0, {str(SOURCE_ROOT)!r})
package = importlib.import_module({package_name!r})
payload = {{
    "all": getattr(package, "__all__", None),
    "legacy": {{name: hasattr(package, name) for name in {legacy_exports!r}}},
}}
print(json.dumps(payload, ensure_ascii=False))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout.strip())
    assert payload["all"] == []
    assert not any(payload["legacy"].values())


def test_backtester_package_no_longer_reexports_legacy_symbols():
    _check_package_slim(
        "backtester",
        [
            "BaseBacktester",
            "DataImporter",
            "IndicatorsBacktester",
            "TradeRecorder_backtester",
            "TradeRecordExporter_backtester",
            "TradeSimulator_backtester",
        ],
    )


def test_dataloader_package_no_longer_reexports_legacy_symbols():
    _check_package_slim(
        "dataloader",
        ["DataLoader", "PredictorLoader", "DataExporter", "CoinbaseLoader"],
    )


def test_metricstracker_package_no_longer_reexports_legacy_symbols():
    _check_package_slim(
        "metricstracker",
        ["BaseMetricTracker", "MetricsCalculatorMetricTracker", "MetricsExporter"],
    )


def test_statanalyser_package_no_longer_reexports_legacy_symbols():
    _check_package_slim(
        "statanalyser",
        [
            "BaseStatAnalyser",
            "CorrelationTest",
            "StationarityTest",
            "AutocorrelationTest",
            "DistributionTest",
            "SeasonalAnalysis",
            "ReportGenerator",
            "select_predictor_factor",
        ],
    )


def test_wfanalyser_package_no_longer_reexports_legacy_symbols():
    _check_package_slim(
        "wfanalyser",
        ["BaseWFAAnalyser"],
    )


def test_utils_packages_no_longer_reexport_console_helpers():
    _check_package_slim("backtester.utils", ["get_console"])
    _check_package_slim("metricstracker.utils", ["get_console"])
    _check_package_slim("wfanalyser.utils", ["get_console"])
