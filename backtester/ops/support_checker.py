"""Registry-backed support checks for runnable strategy configs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from .registry import (
    MULTI_ASSET_CONDITION,
    MULTI_ASSET_INDICATORS,
    MULTI_ASSET_INLINE_CONDITION_FEATURE,
    MULTI_ASSET_REBALANCE_TRIGGER,
    StrategyBuildingBlockRegistry,
    build_registry,
)

AI_STRATEGY_AUTHORING = "ai.strategy_authoring"
STRATEGY_RUN_FILL_MODEL = "strategy_run.fill_model"
PLANNED_STRATEGY_MODE_IDS = {"multi_asset_trigger_selection", "dynamic_allocation_rules"}
SUPPORTED_FILL_TIMINGS = {
    "same_session",
    "bar_offset",
}
SUPPORTED_FILL_PRICES = {"close", "open"}
TIME_STOP_OPS = {"time_stop_bars", "timer_bars"}


class StrategyBuildingBlockSupportError(ValueError):
    """Raised when a runnable config uses an unsupported building block."""


@dataclass(frozen=True)
class StrategyBuildingBlockIssue:
    path: str
    op: str
    usage_site: str
    reason: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "path": self.path,
            "op": self.op,
            "usage_site": self.usage_site,
            "reason": self.reason,
        }


def strategy_run_support_report(
    config: Mapping[str, Any],
    *,
    registry: Optional[StrategyBuildingBlockRegistry] = None,
) -> Dict[str, Any]:
    """Return a registry-backed support verdict for a strategy_run config."""

    checker = _StrategyRunSupportChecker(registry or build_registry())
    checker.check(config)
    return {
        "supported": not checker.issues,
        "issues": [issue.to_dict() for issue in checker.issues],
    }


def validate_strategy_run_support(
    config: Mapping[str, Any],
    *,
    registry: Optional[StrategyBuildingBlockRegistry] = None,
) -> None:
    """Raise if a runnable strategy config uses unsupported building blocks."""

    report = strategy_run_support_report(config, registry=registry)
    if report["supported"]:
        return
    first = report["issues"][0]
    raise StrategyBuildingBlockSupportError(
        "Unsupported Strategy Building Block at "
        f"{first['path']}: {first['op']!r} for {first['usage_site']}: {first['reason']}"
    )


class _StrategyRunSupportChecker:
    """Validate the strategy_run runtime-facing building block surface."""

    def __init__(self, registry: StrategyBuildingBlockRegistry) -> None:
        self.registry = registry
        self.issues: List[StrategyBuildingBlockIssue] = []

    def check(self, config: Mapping[str, Any]) -> None:
        self._check_platform(_dict(config.get("platform")))
        self._check_fill_model(config)

        features = config.get("features") or []
        indicators = config.get("indicators") or []
        computed_fields = config.get("computed_fields") or []
        if features or indicators:
            self._issue(
                "computed_fields",
                "",
                MULTI_ASSET_INDICATORS,
                "strategy_run uses computed_fields[] only; features[] and indicators[] are removed aliases",
            )
            return
        if isinstance(computed_fields, list):
            for index, indicator in enumerate(computed_fields):
                self._check_indicator(indicator, f"computed_fields[{index}]", section="computed_fields[]")

        signals = _dict(config.get("signals"))
        self._check_condition(signals.get("entry"), "signals.entry")
        self._check_condition(signals.get("exit"), "signals.exit")

        selection = _dict(config.get("selection"))
        self._check_condition(selection.get("eligible"), "selection.eligible")

        rebalance = _dict(config.get("rebalance"))
        trigger = rebalance.get("trigger")
        if trigger:
            self._check_rebalance_trigger(trigger, "rebalance.trigger")

    def _check_indicator(self, indicator: Any, path: str, *, section: str) -> None:
        if not isinstance(indicator, Mapping):
            self._issue(path, "", MULTI_ASSET_INDICATORS, f"{section} entries must be objects")
            return
        op = indicator.get("op") or indicator.get("type")
        self._check_op(op, MULTI_ASSET_INDICATORS, f"{path}.op")
        self._check_registry_param_enums(op, indicator, path, MULTI_ASSET_INDICATORS)

    def _check_condition(self, node: Any, path: str) -> None:
        if node in (None, "", False):
            return
        if isinstance(node, list):
            self._check_op("all", MULTI_ASSET_CONDITION, path)
            for index, child in enumerate(node):
                self._check_condition(child, f"{path}[{index}]")
            return
        if not isinstance(node, Mapping):
            self._issue(path, str(node), MULTI_ASSET_CONDITION, "condition nodes must be objects or lists")
            return

        op = node.get("op")
        if _has_text(op):
            normalized_op = str(op or "").strip().lower()
            if normalized_op in TIME_STOP_OPS and not path.startswith("signals.exit"):
                self._issue(
                    f"{path}.op",
                    normalized_op,
                    MULTI_ASSET_CONDITION,
                    "time_stop_bars is only supported inside signals.exit",
                )
            self._check_op(op, MULTI_ASSET_CONDITION, f"{path}.op")

        if "all" in node:
            self._check_op("all", MULTI_ASSET_CONDITION, f"{path}.all")
            all_children = node.get("all")
            if not isinstance(all_children, list):
                self._issue(f"{path}.all", str(all_children), MULTI_ASSET_CONDITION, "all must be a list")
                return
            for index, child in enumerate(all_children):
                self._check_condition(child, f"{path}.all[{index}]")
            return
        if "any" in node:
            self._check_op("any", MULTI_ASSET_CONDITION, f"{path}.any")
            any_children = node.get("any")
            if not isinstance(any_children, list):
                self._issue(f"{path}.any", str(any_children), MULTI_ASSET_CONDITION, "any must be a list")
                return
            for index, child in enumerate(any_children):
                self._check_condition(child, f"{path}.any[{index}]")
            return
        if "not" in node:
            self._check_op("not", MULTI_ASSET_CONDITION, f"{path}.not")
            self._check_condition(node.get("not"), f"{path}.not")
            return

        if not _has_text(op) and ("field" in node or "left" in node):
            self._check_op("gt", MULTI_ASSET_CONDITION, f"{path}.op(default)")

        self._check_operand(node.get("left", node.get("field")), f"{path}.left")
        if "right_field" in node:
            self._check_operand(node.get("right_field"), f"{path}.right_field")
        if "right" in node:
            self._check_operand(node.get("right"), f"{path}.right")

    def _check_rebalance_trigger(self, trigger: Any, path: str) -> None:
        if not isinstance(trigger, Mapping):
            self._issue(path, "", MULTI_ASSET_REBALANCE_TRIGGER, "rebalance.trigger must be an object")
            return
        op = trigger.get("op")
        self._check_op(op, MULTI_ASSET_REBALANCE_TRIGGER, f"{path}.op")

    def _check_platform(self, platform: Mapping[str, Any]) -> None:
        mode = str(platform.get("strategy_mode_id") or "").strip().lower()
        if mode in PLANNED_STRATEGY_MODE_IDS:
            self._issue(
                "platform.strategy_mode_id",
                mode,
                "strategy_run.platform",
                f"{mode} is reserved/planned and is not a supported runnable strategy_run mode yet",
            )

    def _check_fill_model(self, config: Mapping[str, Any]) -> None:
        if config.get("execution"):
            self._issue(
                "fill_model",
                "",
                STRATEGY_RUN_FILL_MODEL,
                "strategy_run uses fill_model{} only; execution{} is a removed alias",
            )
            return
        fill_model = _dict(config.get("fill_model"))
        if not fill_model:
            return
        timing = str(fill_model.get("timing") or "").strip().lower()
        if timing and timing not in SUPPORTED_FILL_TIMINGS:
            self._issue(
                "fill_model.timing",
                timing,
                STRATEGY_RUN_FILL_MODEL,
                f"timing must be one of: {', '.join(sorted(SUPPORTED_FILL_TIMINGS))}",
            )
        price = str(fill_model.get("price") or "").strip().lower()
        if "price" in fill_model:
            self._issue(
                "fill_model.price",
                price,
                STRATEGY_RUN_FILL_MODEL,
                "use entry_price and exit_price instead of the ambiguous price field",
            )
        elif price and price not in SUPPORTED_FILL_PRICES:
            self._issue(
                "fill_model.price",
                price,
                STRATEGY_RUN_FILL_MODEL,
                f"price must be one of: {', '.join(sorted(SUPPORTED_FILL_PRICES))}",
            )
        entry_price = str(fill_model.get("entry_price") or "").strip().lower()
        if entry_price and entry_price not in SUPPORTED_FILL_PRICES:
            self._issue(
                "fill_model.entry_price",
                entry_price,
                STRATEGY_RUN_FILL_MODEL,
                f"entry_price must be one of: {', '.join(sorted(SUPPORTED_FILL_PRICES))}",
            )
        exit_price = str(fill_model.get("exit_price") or "").strip().lower()
        if exit_price and exit_price not in SUPPORTED_FILL_PRICES:
            self._issue(
                "fill_model.exit_price",
                exit_price,
                STRATEGY_RUN_FILL_MODEL,
                f"exit_price must be one of: {', '.join(sorted(SUPPORTED_FILL_PRICES))}",
            )
        if timing == "bar_offset":
            allocation = _dict(config.get("allocation"))
            method = str(allocation.get("method") or "").strip().lower()
            if method not in {"signal_state", "signal_target_weight"}:
                self._issue(
                    "allocation.method",
                    method,
                    STRATEGY_RUN_FILL_MODEL,
                    "bar_offset currently supports only signals-driven signal_state or signal_target_weight configs",
                )
            if any(allocation.get(key) for key in ("frame", "target_weight_frame", "target_weights_frame")):
                self._issue(
                    "allocation.frame",
                    str(allocation.get("frame") or allocation.get("target_weight_frame") or allocation.get("target_weights_frame")),
                    STRATEGY_RUN_FILL_MODEL,
                    "bar_offset does not support allocation frame overrides",
                )
            signals = _dict(config.get("signals"))
            if not signals.get("entry"):
                self._issue(
                    "signals.entry",
                    "",
                    STRATEGY_RUN_FILL_MODEL,
                    "bar_offset requires a signals.entry event source",
                )
            entry = entry_price or price or "close"
            exit_ = exit_price or "close"
            if entry not in {"open", "close"}:
                self._issue(
                    "fill_model.entry_price",
                    entry,
                    STRATEGY_RUN_FILL_MODEL,
                    "bar_offset entry_price must be open or close",
                )
            if exit_ not in {"open", "close"}:
                self._issue(
                    "fill_model.exit_price",
                    exit_,
                    STRATEGY_RUN_FILL_MODEL,
                    "bar_offset exit_price must be open or close",
                )
            entry_delay = fill_model.get("entry_delay_bars", 0)
            exit_delay = fill_model.get("exit_delay_bars", 0)
            for key, value in (
                ("entry_delay_bars", entry_delay),
                ("exit_delay_bars", exit_delay),
            ):
                if not isinstance(value, int) or value < 0:
                    self._issue(
                        f"fill_model.{key}",
                        str(value),
                        STRATEGY_RUN_FILL_MODEL,
                        f"{key} must be a non-negative integer",
                    )
            if signals.get("exit"):
                if entry != exit_ or entry_delay != exit_delay:
                    self._issue(
                        "fill_model",
                        "bar_offset_signal_state",
                        STRATEGY_RUN_FILL_MODEL,
                        "bar_offset with signals.exit models state changes; entry/exit price and delay must match",
                    )

    def _check_operand(self, operand: Any, path: str) -> None:
        if isinstance(operand, list):
            for index, item in enumerate(operand):
                self._check_operand(item, f"{path}[{index}]")
            return
        if not isinstance(operand, Mapping):
            return
        if "feature" in operand:
            op = operand.get("feature") or operand.get("op")
            self._issue(
                f"{path}.feature",
                str(op or ""),
                MULTI_ASSET_INLINE_CONDITION_FEATURE,
                "inline feature nodes are not part of the public strategy_run config surface; define the calculation in computed_fields[] and reference it by field name",
            )
            if "source" in operand:
                self._check_operand(operand.get("source"), f"{path}.source")
            params = operand.get("params")
            if isinstance(params, Mapping) and "source" in params:
                self._check_operand(params.get("source"), f"{path}.params.source")
            return
        if "field" in operand:
            self._check_operand(operand.get("field"), f"{path}.field")

    def _check_op(self, op: Any, usage_site: str, path: str) -> None:
        normalized = str(op or "").strip().lower()
        if not normalized:
            self._issue(path, normalized, usage_site, "op is required")
            return
        if normalized.startswith("template."):
            authoring_report = self.registry.support_report(normalized, usage_site=AI_STRATEGY_AUTHORING)
            if authoring_report.get("supported"):
                self._issue(
                    path,
                    normalized,
                    usage_site,
                    "template.* building blocks are AI authoring scaffolds only; they are not runtime ops",
                )
                return
        report = self.registry.support_report(normalized, usage_site=usage_site)
        if not report.get("supported"):
            self._issue(path, normalized, usage_site, str(report.get("reason") or "unsupported"))

    def _check_registry_param_enums(self, op: Any, node: Mapping[str, Any], path: str, usage_site: str) -> None:
        normalized = str(op or "").strip().lower()
        if not normalized:
            return
        report = self.registry.support_report(normalized, usage_site=usage_site)
        if not report.get("supported"):
            return
        spec = self.registry.resolve(normalized)
        params_schema = spec.get("params_schema", {}) if isinstance(spec, Mapping) else {}
        properties = params_schema.get("properties", {}) if isinstance(params_schema, Mapping) else {}
        for name, schema in properties.items():
            if name not in node or not isinstance(schema, Mapping) or "enum" not in schema:
                continue
            value = node.get(name)
            if isinstance(value, Mapping) and "param_ref" in value:
                continue
            enum_values = list(schema.get("enum") or [])
            if all(isinstance(item, str) for item in enum_values) and isinstance(value, str):
                candidate = value.strip().lower()
            else:
                candidate = value
            if candidate in enum_values:
                continue
            allowed = ", ".join(str(item) for item in enum_values)
            self._issue(
                f"{path}.{name}",
                str(value or ""),
                usage_site,
                f"{name} must be one of: {allowed}",
            )

    def _issue(self, path: str, op: str, usage_site: str, reason: str) -> None:
        self.issues.append(
            StrategyBuildingBlockIssue(
                path=path,
                op=str(op or ""),
                usage_site=usage_site,
                reason=reason,
            )
        )


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _has_text(value: Any) -> bool:
    return bool(str(value or "").strip())
