import importlib
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def test_rust_accounting_bridge_computes_rotation_turnover():
    bridge = importlib.import_module("backtester.RustCoreBridge_backtester")
    if not bridge.rust_core_available():
        pytest.skip("Rust core is unavailable")

    payload = {
        "config": {
            "starting_equity": 100.0,
            "cost_rate": 0.0,
            "max_gross_exposure": 1.0,
            "allow_short": False,
        },
        "checkpoints": [
            {
                "time": "2024-01-02",
                "returns": {},
                "target_weights": {"VOO": 1.0},
            },
            {
                "time": "2024-02-01",
                "returns": {"VOO": 0.10, "GLD": 0.0},
                "target_weights": {"GLD": 1.0},
            },
        ],
    }

    summary = bridge.run_accounting_via_cli(payload, timeout=60)

    assert summary["events"][0]["turnover"] == pytest.approx(1.0)
    assert summary["events"][1]["turnover"] == pytest.approx(2.0)
    assert summary["final_equity"] == pytest.approx(110.0)
