"""Rust simulation kernel bridge (ctypes + cdylib)."""

from __future__ import annotations

import ctypes
import logging
import subprocess
from pathlib import Path
from typing import Optional, Tuple

import numpy as np


class RustSimKernel:
    """Load and call Rust batch trade-simulation kernel."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("lo2cin4bt.backtester.rust_kernel")
        self._lib = None
        self._fn = None
        self._lib_path = self._resolve_library_path()
        self._ensure_loaded()

    def is_available(self) -> bool:
        return self._fn is not None

    def simulate_batch(
        self,
        *,
        entry_signals: np.ndarray,
        exit_signals: np.ndarray,
        close_prices: np.ndarray,
        open_prices: np.ndarray,
        transaction_cost: float,
        slippage: float,
        trade_price: str,
        trade_delay: int,
        holding_period_days: int,
        nday_exit_long_days: np.ndarray,
        nday_exit_short_days: np.ndarray,
        has_non_nday_exit: np.ndarray,
        nday_combine_mode: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        if self._fn is None:
            raise RuntimeError("Rust kernel not available")

        entry = np.ascontiguousarray(entry_signals, dtype=np.float64)
        exit_ = np.ascontiguousarray(exit_signals, dtype=np.float64)
        close = np.ascontiguousarray(close_prices, dtype=np.float64)
        open_ = np.ascontiguousarray(open_prices, dtype=np.float64)

        if entry.ndim != 2 or exit_.ndim != 2:
            raise ValueError("entry/exit signals must be 2D arrays")
        if close.ndim != 1 or open_.ndim != 1:
            raise ValueError("close/open arrays must be 1D")
        n_time, n_strategies = entry.shape
        if exit_.shape != (n_time, n_strategies):
            raise ValueError("entry and exit shapes must match")
        if len(close) != n_time or len(open_) != n_time:
            raise ValueError("price length must match signal time axis")
        timer_long = np.ascontiguousarray(nday_exit_long_days, dtype=np.int32)
        timer_short = np.ascontiguousarray(nday_exit_short_days, dtype=np.int32)
        timer_has_exit = np.ascontiguousarray(has_non_nday_exit, dtype=np.int32)
        timer_mode = np.ascontiguousarray(nday_combine_mode, dtype=np.int32)
        if timer_long.shape != (n_strategies,):
            raise ValueError("nday_exit_long_days must be strategy-length array")
        if timer_short.shape != (n_strategies,):
            raise ValueError("nday_exit_short_days must be strategy-length array")
        if timer_has_exit.shape != (n_strategies,):
            raise ValueError("has_non_nday_exit must be strategy-length array")
        if timer_mode.shape != (n_strategies,):
            raise ValueError("nday_combine_mode must be strategy-length array")

        positions = np.zeros((n_time, n_strategies), dtype=np.float64)
        returns = np.zeros((n_time, n_strategies), dtype=np.float64)
        actions = np.zeros((n_time, n_strategies), dtype=np.float64)
        equity = np.zeros((n_time, n_strategies), dtype=np.float64)

        price_mode = 1 if str(trade_price).lower() == "open" else 0

        ret_code = self._fn(
            ctypes.c_size_t(n_time),
            ctypes.c_size_t(n_strategies),
            entry.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            exit_.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            close.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            open_.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            ctypes.c_double(float(transaction_cost)),
            ctypes.c_double(float(slippage)),
            ctypes.c_int(int(price_mode)),
            ctypes.c_int(int(trade_delay)),
            ctypes.c_int(int(holding_period_days)),
            timer_long.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
            timer_short.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
            timer_has_exit.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
            timer_mode.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
            positions.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            returns.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            actions.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            equity.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        )
        if ret_code != 0:
            raise RuntimeError(f"rust simulate_batch returned error code {ret_code}")
        return positions, returns, actions, equity

    def _ensure_loaded(self) -> None:
        if self._load_library():
            return
        self._build_library()
        self._load_library()

    def _load_library(self) -> bool:
        if not self._lib_path.exists():
            return False
        try:
            self._lib = ctypes.CDLL(str(self._lib_path))
            fn = self._lib.simulate_trades_batch
            fn.argtypes = [
                ctypes.c_size_t,  # n_time
                ctypes.c_size_t,  # n_strategies
                ctypes.POINTER(ctypes.c_double),  # entry
                ctypes.POINTER(ctypes.c_double),  # exit
                ctypes.POINTER(ctypes.c_double),  # close
                ctypes.POINTER(ctypes.c_double),  # open
                ctypes.c_double,  # transaction_cost
                ctypes.c_double,  # slippage
                ctypes.c_int,  # trade_price_mode
                ctypes.c_int,  # trade_delay
                ctypes.c_int,  # holding_period_days
                ctypes.POINTER(ctypes.c_int32),  # nday_exit_long_days
                ctypes.POINTER(ctypes.c_int32),  # nday_exit_short_days
                ctypes.POINTER(ctypes.c_int32),  # has_non_nday_exit
                ctypes.POINTER(ctypes.c_int32),  # nday_combine_mode
                ctypes.POINTER(ctypes.c_double),  # positions_out
                ctypes.POINTER(ctypes.c_double),  # returns_out
                ctypes.POINTER(ctypes.c_double),  # actions_out
                ctypes.POINTER(ctypes.c_double),  # equity_out
            ]
            fn.restype = ctypes.c_int
            self._fn = fn
            return True
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.warning("failed to load rust kernel: %s", exc)
            self._lib = None
            self._fn = None
            return False

    def _build_library(self) -> None:
        crate_dir = self._crate_dir()
        if not crate_dir.exists():
            return
        try:
            subprocess.run(
                ["cargo", "build", "--release"],
                cwd=str(crate_dir),
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.warning("rust kernel build failed: %s", exc)

    def _resolve_library_path(self) -> Path:
        crate_dir = self._crate_dir()
        if self._is_windows():
            file_name = "rust_sim_kernel_rs.dll"
        elif self._is_macos():
            file_name = "librust_sim_kernel_rs.dylib"
        else:
            file_name = "librust_sim_kernel_rs.so"
        return crate_dir / "target" / "release" / file_name

    def _crate_dir(self) -> Path:
        return Path(__file__).resolve().parent / "rust_sim_kernel_rs"

    @staticmethod
    def _is_windows() -> bool:
        import platform

        return platform.system().lower().startswith("win")

    @staticmethod
    def _is_macos() -> bool:
        import platform

        return platform.system().lower() == "darwin"
