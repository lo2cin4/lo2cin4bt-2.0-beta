"""Optional bridge to the Rust deterministic core.

The production runtime does not depend on Rust yet.  This bridge is a narrow
development/test seam for validating the Rust accounting contract from Python
without requiring a PyO3 wheel.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List


_REPO_ROOT = Path(__file__).resolve().parents[1]
_CRATE_DIR = _REPO_ROOT / "rust" / "lo2cin4bt_core"
_ACCOUNTING_BIN = _CRATE_DIR / "target" / "debug" / "accounting_cli.exe"


def rust_core_crate_dir() -> Path:
    return _CRATE_DIR


def rust_core_available() -> bool:
    return bool(shutil.which("cargo")) and _CRATE_DIR.exists()


def run_accounting_via_cli(payload: Dict[str, Any], *, timeout: int = 30) -> Dict[str, Any]:
    """Run Rust accounting through the JSON CLI and return the parsed summary."""

    if not rust_core_available():
        raise RuntimeError("Rust core is unavailable; cargo or crate directory is missing")
    command = [str(_ACCOUNTING_BIN)] if _ACCOUNTING_BIN.exists() else [
        "cargo",
        "run",
        "--quiet",
        "--bin",
        "accounting_cli",
    ]
    process = subprocess.run(
        command,
        cwd=_CRATE_DIR,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if process.returncode != 0:
        message = process.stderr.strip() or process.stdout.strip() or "Rust accounting failed"
        raise RuntimeError(message)
    return json.loads(process.stdout)


def portfolio_result_to_accounting_payload(
    *,
    close_frame: Any,
    equity_curve: Any,
    cost_rate: float,
    starting_equity: float = 100.0,
    max_gross_exposure: float = 1.0,
    allow_short: bool = False,
) -> Dict[str, Any]:
    """Build Rust accounting input from an existing portfolio result.

    This is a golden-replay helper.  It does not recompute selection or ranking;
    it replays the target weights already emitted by the Python engine against
    the original close-to-close returns.
    """

    try:
        import pandas as pd
    except Exception as exc:  # pragma: no cover - pandas is required in this repo
        raise RuntimeError("pandas is required to build Rust accounting payload") from exc

    close = close_frame.copy()
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    close = close.sort_index()
    equity = equity_curve.copy()
    if "Time" not in equity.columns:
        raise ValueError("portfolio equity_curve requires a Time column")
    equity["Time"] = pd.to_datetime(equity["Time"]).dt.tz_localize(None).dt.normalize()
    equity = equity.sort_values("Time")

    symbols = [column.removeprefix("Weight_") for column in equity.columns if str(column).startswith("Weight_")]
    if not symbols:
        raise ValueError("portfolio equity_curve requires Weight_<asset> columns")

    returns = close.reindex(columns=symbols).pct_change().replace([float("inf"), float("-inf")], 0.0).fillna(0.0)
    checkpoints = []
    for row in equity.itertuples(index=False):
        time = getattr(row, "Time")
        timestamp = pd.Timestamp(time).normalize()
        return_row = returns.loc[timestamp] if timestamp in returns.index else None
        target_weights = {}
        return_values = {}
        for symbol in symbols:
            weight_value = getattr(row, f"Weight_{symbol}", 0.0)
            if pd.notna(weight_value) and abs(float(weight_value)) > 1e-12:
                target_weights[symbol] = float(weight_value)
            asset_return = 0.0 if return_row is None else return_row.get(symbol, 0.0)
            if pd.notna(asset_return):
                return_values[symbol] = float(asset_return)
        checkpoints.append(
            {
                "time": timestamp.date().isoformat(),
                "returns": return_values,
                "target_weights": target_weights,
            }
        )

    return {
        "config": {
            "starting_equity": float(starting_equity),
            "cost_rate": float(cost_rate),
            "max_gross_exposure": float(max_gross_exposure),
            "allow_short": bool(allow_short),
        },
        "checkpoints": checkpoints,
    }


def validate_portfolio_result_with_rust(
    *,
    close_frame: Any,
    equity_curve: Any,
    cost_rate: float,
    tolerance: float = 1e-8,
    timeout: int = 60,
) -> Dict[str, Any]:
    """Replay a Python portfolio result through Rust accounting and compare rows."""

    payload = portfolio_result_to_accounting_payload(
        close_frame=close_frame,
        equity_curve=equity_curve,
        cost_rate=cost_rate,
    )
    summary = run_accounting_via_cli(payload, timeout=timeout)
    events = summary.get("events", [])
    mismatches: List[Dict[str, Any]] = []
    try:
        pass
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("pandas is required to validate Rust accounting replay") from exc

    equity = equity_curve.copy().reset_index(drop=True)
    if len(events) != len(equity):
        mismatches.append(
            {
                "field": "row_count",
                "python": int(len(equity)),
                "rust": int(len(events)),
                "diff": int(len(events)) - int(len(equity)),
            }
        )

    compare_count = min(len(events), len(equity))
    fields = [
        ("Equity_value", "equity_after_trade"),
        ("Turnover", "turnover"),
        ("Cash_weight", "cash_weight"),
        ("Gross_exposure", "gross_exposure"),
    ]
    for idx in range(compare_count):
        py_row = equity.iloc[idx]
        event = events[idx]
        for python_key, rust_key in fields:
            python_value = float(py_row.get(python_key, 0.0))
            rust_value = float(event.get(rust_key, 0.0))
            diff = rust_value - python_value
            if abs(diff) > tolerance:
                mismatches.append(
                    {
                        "row": idx,
                        "time": str(py_row.get("Time", event.get("time", ""))),
                        "field": python_key,
                        "python": python_value,
                        "rust": rust_value,
                        "diff": diff,
                    }
                )
    final_python = float(equity["Equity_value"].iloc[-1]) if len(equity) else None
    final_rust = summary.get("final_equity")
    return {
        "schema_version": "rust_accounting_validation.v1",
        "status": "matched" if not mismatches else "mismatch",
        "tolerance": tolerance,
        "row_count": int(len(equity)),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:100],
        "final_equity_python": final_python,
        "final_equity_rust": final_rust,
        "active_rebalances_rust": summary.get("active_rebalances"),
        "average_turnover_rust": summary.get("average_turnover"),
    }
