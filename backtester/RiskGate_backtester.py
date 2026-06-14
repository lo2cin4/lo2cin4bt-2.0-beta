"""Unified risk-gate helpers for portfolio accounting paths."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Sequence

import numpy as np


@dataclass
class RiskGateDecision:
    target_weights: np.ndarray
    events: List[Dict[str, Any]] = field(default_factory=list)


class RiskGateController:
    """Apply optional portfolio/order-level risk gates to target weights.

    All gates are disabled by default.  The controller accepts both the planned
    `risk.gates` shape and earlier top-level risk fields for compatibility.
    """

    def __init__(self, risk_config: Dict[str, Any] | None) -> None:
        self.raw_config = risk_config if isinstance(risk_config, dict) else {}
        gates = self.raw_config.get("gates")
        self.gates = gates if isinstance(gates, dict) else self.raw_config
        self.max_positions = _optional_int(self.gates.get("max_positions"))
        self.max_daily_loss = _optional_float(self.gates.get("max_daily_loss"))
        self.max_order_size = _optional_float(self.gates.get("max_order_size"))
        self.max_drawdown = _optional_float(self.gates.get("max_drawdown"))
        self.gate_action = str(self.gates.get("gate_action") or self.raw_config.get("gate_action") or "").strip().lower()
        self.reduce_exposure_factor = _optional_float(self.gates.get("reduce_exposure_factor"))
        if self.reduce_exposure_factor is None:
            self.reduce_exposure_factor = 0.5
        self.enabled = any(
            value is not None
            for value in (
                self.max_positions,
                self.max_daily_loss,
                self.max_order_size,
                self.max_drawdown,
            )
        )

    def configured_gate_names(self) -> List[str]:
        names: List[str] = []
        if self.max_positions is not None:
            names.append("max_positions")
        if self.max_daily_loss is not None:
            names.append("max_daily_loss")
        if self.max_order_size is not None:
            names.append("max_order_size")
        if self.max_drawdown is not None:
            names.append("max_drawdown")
        return names

    def apply(
        self,
        *,
        timestamp: Any,
        symbols: Sequence[str],
        before_weights: Sequence[float],
        target_weights: Sequence[float],
        equity: float,
        equity_peak: float,
        daily_return: float,
    ) -> RiskGateDecision:
        adjusted = np.asarray(target_weights, dtype=float).copy()
        before = np.asarray(before_weights, dtype=float).copy()
        adjusted = np.nan_to_num(adjusted, nan=0.0, posinf=0.0, neginf=0.0)
        before = np.nan_to_num(before, nan=0.0, posinf=0.0, neginf=0.0)
        events: List[Dict[str, Any]] = []

        if not self.enabled:
            return RiskGateDecision(target_weights=adjusted, events=events)

        if self.max_daily_loss is not None and _is_finite(daily_return):
            if float(daily_return) <= -abs(float(self.max_daily_loss)):
                adjusted = self._apply_gate_action(adjusted, before)
                events.append(
                    self._event(
                        timestamp=timestamp,
                        gate="max_daily_loss",
                        threshold=-abs(float(self.max_daily_loss)),
                        observed=float(daily_return),
                        action=self._effective_action(),
                        affected_assets=list(symbols),
                        resulting_target_weights=dict(zip(symbols, adjusted.tolist())),
                    )
                )

        if (
            self.max_drawdown is not None
            and _is_finite(equity)
            and _is_finite(equity_peak)
            and float(equity_peak) > 0.0
        ):
            drawdown = float(equity) / float(equity_peak) - 1.0
            if drawdown <= -abs(float(self.max_drawdown)):
                adjusted = self._apply_gate_action(adjusted, before)
                events.append(
                    self._event(
                        timestamp=timestamp,
                        gate="max_drawdown",
                        threshold=-abs(float(self.max_drawdown)),
                        observed=drawdown,
                        action=self._effective_action(),
                        affected_assets=list(symbols),
                        resulting_target_weights=dict(zip(symbols, adjusted.tolist())),
                    )
                )

        if self.max_positions is not None and self.max_positions >= 0:
            active_indices = np.flatnonzero(np.abs(adjusted) > 1e-12)
            if len(active_indices) > self.max_positions:
                keep_order = active_indices[np.argsort(np.abs(adjusted[active_indices]), kind="mergesort")[::-1]]
                keep = set(int(idx) for idx in keep_order[: self.max_positions])
                removed = [int(idx) for idx in active_indices if int(idx) not in keep]
                for idx in removed:
                    adjusted[idx] = 0.0
                events.append(
                    self._event(
                        timestamp=timestamp,
                        gate="max_positions",
                        threshold=self.max_positions,
                        observed=len(active_indices),
                        action="reduce_selected_positions",
                        affected_assets=[str(symbols[idx]) for idx in removed],
                        resulting_target_weights=dict(zip(symbols, adjusted.tolist())),
                    )
                )

        if self.max_order_size is not None and self.max_order_size >= 0.0:
            deltas = adjusted - before
            oversized = np.flatnonzero(np.abs(deltas) > float(self.max_order_size) + 1e-12)
            if len(oversized):
                for idx in oversized:
                    adjusted[idx] = before[idx] + math.copysign(float(self.max_order_size), deltas[idx])
                events.append(
                    self._event(
                        timestamp=timestamp,
                        gate="max_order_size",
                        threshold=float(self.max_order_size),
                        observed=max(abs(float(deltas[idx])) for idx in oversized),
                        action="clamp_order_delta",
                        affected_assets=[str(symbols[int(idx)]) for idx in oversized],
                        resulting_target_weights=dict(zip(symbols, adjusted.tolist())),
                    )
                )

        return RiskGateDecision(target_weights=adjusted, events=events)

    def _effective_action(self) -> str:
        return self.gate_action if self.gate_action and self.gate_action != "none" else "flatten"

    def _apply_gate_action(self, target: np.ndarray, before: np.ndarray) -> np.ndarray:
        action = self._effective_action()
        if action in {"flatten", "pause_trading"}:
            return np.zeros_like(target)
        if action == "reduce_exposure":
            return target * float(self.reduce_exposure_factor)
        if action == "block_new_orders":
            adjusted = target.copy()
            for idx, value in enumerate(target):
                if abs(value) > abs(before[idx]) and np.sign(value) == np.sign(before[idx] if before[idx] != 0 else value):
                    adjusted[idx] = before[idx]
            return adjusted
        return target

    @staticmethod
    def _event(
        *,
        timestamp: Any,
        gate: str,
        threshold: Any,
        observed: Any,
        action: str,
        affected_assets: List[str],
        resulting_target_weights: Dict[str, float],
    ) -> Dict[str, Any]:
        return {
            "Time": timestamp,
            "Gate": gate,
            "Threshold": threshold,
            "Observed": observed,
            "Action": action,
            "Affected_assets": affected_assets,
            "Resulting_target_weights": resulting_target_weights,
        }


def summarize_risk_gate_events(events: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not events:
        return {
            "schema_version": "risk_gate_summary.v1",
            "event_count": 0,
            "gates_triggered": [],
        }
    gates = [str(event.get("Gate")) for event in events if event.get("Gate")]
    return {
        "schema_version": "risk_gate_summary.v1",
        "event_count": len(events),
        "gates_triggered": sorted(set(gates)),
        "first_event": events[0].get("Time"),
        "last_event": events[-1].get("Time"),
        "events_by_gate": {gate: gates.count(gate) for gate in sorted(set(gates))},
    }


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _optional_int(value: Any) -> int | None:
    parsed = _optional_float(value)
    if parsed is None:
        return None
    return max(0, int(parsed))


def _is_finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
