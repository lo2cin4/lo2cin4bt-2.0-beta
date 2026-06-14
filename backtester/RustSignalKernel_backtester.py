"""Rust signal kernel bridge for node IR boolean masks."""

from __future__ import annotations

import ctypes
import logging
import platform
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np


class RustSignalKernel:
    """Load and call Rust signal mask kernel."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("lo2cin4bt.backtester.rust_signal_kernel")
        self._lib = None
        self._fn = None
        self._lib_path = self._resolve_library_path()
        self._ensure_loaded()

    def is_available(self) -> bool:
        return self._fn is not None

    def binary_mask(self, *, op_code: int, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        if self._fn is None:
            raise RuntimeError("Rust signal kernel not available")
        left_arr = np.ascontiguousarray(left, dtype=np.float64)
        right_arr = np.ascontiguousarray(right, dtype=np.float64)
        if left_arr.ndim != 1 or right_arr.ndim != 1 or left_arr.shape[0] != right_arr.shape[0]:
            raise ValueError("left/right must be same-length 1D arrays")
        out = np.zeros(left_arr.shape[0], dtype=np.uint8)
        rc = self._fn(
            ctypes.c_size_t(int(left_arr.shape[0])),
            ctypes.c_int(int(op_code)),
            left_arr.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            right_arr.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            out.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        )
        if rc != 0:
            raise RuntimeError(f"rust signal_binary_mask returned error code {rc}")
        return out.astype(np.bool_)

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
            fn = self._lib.signal_binary_mask
            fn.argtypes = [
                ctypes.c_size_t,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_uint8),
            ]
            fn.restype = ctypes.c_int
            self._fn = fn
            return True
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.warning("failed to load rust signal kernel: %s", exc)
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
            self.logger.warning("rust signal kernel build failed: %s", exc)

    def _resolve_library_path(self) -> Path:
        crate_dir = self._crate_dir()
        if platform.system().lower().startswith("win"):
            file_name = "rust_sim_kernel_rs.dll"
        elif platform.system().lower() == "darwin":
            file_name = "librust_sim_kernel_rs.dylib"
        else:
            file_name = "librust_sim_kernel_rs.so"
        return crate_dir / "target" / "release" / file_name

    def _crate_dir(self) -> Path:
        return Path(__file__).resolve().parent / "rust_sim_kernel_rs"
