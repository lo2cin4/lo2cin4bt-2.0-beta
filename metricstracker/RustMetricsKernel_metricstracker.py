"""Rust metrics kernel bridge for grouped trade statistics."""

from __future__ import annotations

import ctypes
import logging
from pathlib import Path
from typing import Optional, Tuple

import numpy as np


class RustMetricsKernel:
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("lo2cin4bt.metricstracker.rust_kernel")
        self._lib = None
        self._fn = None
        self._lib_path = self._resolve_library_path()
        self._ensure_loaded()

    def is_available(self) -> bool:
        return self._fn is not None

    def compute_trade_stats_batch(
        self,
        *,
        trade_actions: np.ndarray,
        trade_returns: np.ndarray,
        position_size: np.ndarray,
        group_start: np.ndarray,
        group_end: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        if self._fn is None:
            raise RuntimeError("Rust metrics kernel not available")

        actions = np.ascontiguousarray(trade_actions, dtype=np.float64)
        returns = np.ascontiguousarray(trade_returns, dtype=np.float64)
        positions = np.ascontiguousarray(position_size, dtype=np.float64)
        starts = np.ascontiguousarray(group_start, dtype=np.uintp)
        ends = np.ascontiguousarray(group_end, dtype=np.uintp)

        n_groups = int(len(starts))
        trade_count = np.empty(n_groups, dtype=np.float64)
        win_rate = np.empty(n_groups, dtype=np.float64)
        profit_factor = np.empty(n_groups, dtype=np.float64)
        avg_trade_return = np.empty(n_groups, dtype=np.float64)
        max_losses = np.empty(n_groups, dtype=np.float64)
        exposure_time = np.empty(n_groups, dtype=np.float64)
        max_holding_ratio = np.empty(n_groups, dtype=np.float64)

        ret_code = self._fn(
            ctypes.c_size_t(int(actions.shape[0])),
            ctypes.c_size_t(n_groups),
            actions.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            returns.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            positions.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            starts.ctypes.data_as(ctypes.POINTER(ctypes.c_size_t)),
            ends.ctypes.data_as(ctypes.POINTER(ctypes.c_size_t)),
            trade_count.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            win_rate.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            profit_factor.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            avg_trade_return.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            max_losses.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            exposure_time.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            max_holding_ratio.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        )
        if ret_code != 0:
            raise RuntimeError(f"rust compute_trade_stats_batch returned error code {ret_code}")

        return (
            trade_count,
            win_rate,
            profit_factor,
            avg_trade_return,
            max_losses,
            exposure_time,
            max_holding_ratio,
        )

    def _ensure_loaded(self) -> None:
        if not self._lib_path.exists():
            return
        try:
            self._lib = ctypes.CDLL(str(self._lib_path))
            fn = self._lib.compute_trade_stats_batch
            fn.argtypes = [
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_size_t),
                ctypes.POINTER(ctypes.c_size_t),
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
            ]
            fn.restype = ctypes.c_int
            self._fn = fn
        except Exception as exc:  # pragma: no cover
            self.logger.warning("failed to load rust metrics kernel: %s", exc)
            self._lib = None
            self._fn = None

    def _resolve_library_path(self) -> Path:
        crate_dir = Path(__file__).resolve().parents[1] / "backtester" / "rust_sim_kernel_rs"
        import platform

        system = platform.system().lower()
        if system.startswith("win"):
            file_name = "rust_sim_kernel_rs.dll"
        elif system == "darwin":
            file_name = "librust_sim_kernel_rs.dylib"
        else:
            file_name = "librust_sim_kernel_rs.so"
        return crate_dir / "target" / "release" / file_name
