"""Backend runtime for batched semantic strategy execution."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol


class KernelBackend(Protocol):
    """Execution backend protocol."""

    def run_batch(
        self,
        *,
        plan: Dict[str, Any],
        data: Any,
        param_grid: List[Dict[str, Any]],
        trading_params: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Run batched backtests and return result list."""


@dataclass
class BacktestExecutionRuntime:
    """Runtime orchestrator for backend selection and execution."""

    logger: logging.Logger

    def run_batch(
        self,
        *,
        plan: Dict[str, Any],
        data: Any,
        param_grid: List[Dict[str, Any]],
        trading_params: Dict[str, Any],
        context: Dict[str, Any],
        backend: KernelBackend,
    ) -> List[Dict[str, Any]]:
        return backend.run_batch(
            plan=plan,
            data=data,
            param_grid=param_grid,
            trading_params=trading_params,
            context=context,
        )
