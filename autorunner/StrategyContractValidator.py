"""Semantic strategy contract validator.

This module provides a lightweight validator and planner precheck for:
- strategy-contract JSON payloads
- optional feature-contract-v1 payloads

The goal is to fail fast before dispatching any heavy backtest work.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
import json
import operator
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .FeatureContractValidator_v1 import FeatureContractValidatorV1


SEQUENTIAL_ONLY_OPS = {"timer_bars", "time_stop_bars", "session.same_session_close"}
VALID_REQUESTED_MODES = {"auto", "node_ir"}
COMPARE_OPS = {"gt", "lt", "ge", "le", "eq", "ne", "cross_up", "cross_down"}
LOGIC_OPS = {"and", "or", "not"}
CALENDAR_OPS = {
    "calendar.every_session",
    "calendar.month_in",
    "calendar.month_start",
    "calendar.month_end",
        "calendar.quarter_start",
        "calendar.quarter_end",
        "calendar.year_start",
        "calendar.year_end",
    "calendar.weekday_eq",
    "calendar.last_weekday_of_month",
    "calendar.nth_weekday_of_month",
    "calendar.event_date",
}


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str]
    warnings: List[str]
    summary: Dict[str, Any]


class StrategyContractValidator:
    """Validate semantic strategy contract and compute preview metadata."""

    def __init__(self) -> None:
        self.feature_validator = FeatureContractValidatorV1()

    def validate(
        self,
        strategy_contract: Dict[str, Any],
        feature_contract: Optional[Dict[str, Any]] = None,
    ) -> ValidationResult:
        errors: List[str] = []
        warnings: List[str] = []

        self._validate_required_top_level(strategy_contract, errors)
        if errors:
            return ValidationResult(False, errors, warnings, {})

        parameter_domains = strategy_contract.get("parameter_domains", {})
        domain_sizes, domain_errors = self._compute_domain_sizes(parameter_domains)
        errors.extend(domain_errors)

        self._validate_nodes(strategy_contract.get("entry"), "entry", errors)
        self._validate_nodes(strategy_contract.get("exit"), "exit", errors)
        self._validate_execution(strategy_contract.get("execution", {}), errors)

        param_refs = self._collect_param_refs(strategy_contract)
        for ref in sorted(param_refs):
            if ref not in parameter_domains:
                errors.append(f"parameter_domains missing key for param_ref '{ref}'")

        fields = self._collect_fields(strategy_contract)
        feature_result = None
        feature_fields = self._collect_feature_fields(feature_contract)
        if feature_contract is not None:
            feature_result = self.feature_validator.validate(feature_contract)
            errors.extend(feature_result.errors)
            warnings.extend(feature_result.warnings)
            for field in sorted(fields):
                if field not in feature_fields:
                    errors.append(f"field '{field}' not defined in feature contract")

        sequential_reqs = sorted(self._collect_sequential_requirements(strategy_contract))
        requested_mode = self._get_requested_mode(strategy_contract, errors)
        resolved_mode = self._resolve_engine_mode(requested_mode, sequential_reqs, errors)

        combination_count = self._count_combinations(domain_sizes)
        max_combinations = (
            strategy_contract.get("engine_preferences", {}) or {}
        ).get("max_combinations")
        if isinstance(max_combinations, int) and max_combinations > 0:
            if combination_count > max_combinations:
                errors.append(
                    f"estimated combinations {combination_count} exceeds max_combinations {max_combinations}"
                )
            elif combination_count > int(max_combinations * 0.8):
                warnings.append(
                    f"estimated combinations {combination_count} is close to max_combinations {max_combinations}"
                )

        summary = {
            "requested_mode": requested_mode,
            "resolved_mode": resolved_mode,
            "sequential_requirements": sequential_reqs,
            "combination_count": combination_count,
            "domain_sizes": domain_sizes,
            "field_count": len(fields),
            "fields": sorted(fields),
            "feature_contract_summary": feature_result.summary if feature_result else {},
        }

        return ValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
            summary=summary,
        )

    def validate_file_paths(
        self,
        strategy_contract_path: str,
        feature_contract_path: Optional[str] = None,
    ) -> ValidationResult:
        strategy = self._load_json(strategy_contract_path)
        feature = self._load_json(feature_contract_path) if feature_contract_path else None
        return self.validate(strategy, feature)

    @staticmethod
    def _load_json(path: Optional[str]) -> Dict[str, Any]:
        if not path:
            raise ValueError("empty path")
        content = Path(path).read_text(encoding="utf-8-sig")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a JSON object")
        return data

    @staticmethod
    def _validate_required_top_level(strategy_contract: Dict[str, Any], errors: List[str]) -> None:
        required = {"schema_version", "strategy_id", "data_context", "entry", "exit"}
        missing = sorted(required - set(strategy_contract.keys()))
        if missing:
            errors.append(f"missing required top-level keys: {', '.join(missing)}")
        if strategy_contract.get("schema_version") != "strategy_contract":
            errors.append("schema_version must be 'strategy_contract'")
        allowed = {
            "schema_version",
            "strategy_id",
            "name",
            "description",
            "tags",
            "data_context",
            "parameter_domains",
            "entry",
            "exit",
            "execution",
            "engine_preferences",
            "risk",
            "max_combos",
            "combo_limits",
        }
        unsupported = sorted(set(strategy_contract.keys()) - allowed)
        if unsupported:
            errors.append(
                f"unsupported top-level keys for strategy.contract: {', '.join(unsupported)}"
            )

    @staticmethod
    def _validate_execution(execution: Any, errors: List[str]) -> None:
        if execution in (None, {}):
            return
        if not isinstance(execution, dict):
            errors.append("execution must be an object")
            return
        allowed = {"side", "entry_price", "exit_price", "session_scope", "same_session_exit"}
        unsupported = sorted(set(execution.keys()) - allowed)
        if unsupported:
            errors.append(f"unsupported execution keys: {', '.join(unsupported)}")
        if "side" in execution and str(execution.get("side")).lower() not in {"long", "short"}:
            errors.append("execution.side must be long/short")
        if "entry_price" in execution and str(execution.get("entry_price")).lower() not in {"open", "close"}:
            errors.append("execution.entry_price must be open/close")
        if "exit_price" in execution and str(execution.get("exit_price")).lower() not in {"open", "close"}:
            errors.append("execution.exit_price must be open/close")
        if "session_scope" in execution and str(execution.get("session_scope")).lower() not in {"multi_session", "same_session"}:
            errors.append("execution.session_scope must be multi_session/same_session")

    @staticmethod
    def _compute_domain_sizes(parameter_domains: Dict[str, Any]) -> Tuple[Dict[str, int], List[str]]:
        if not isinstance(parameter_domains, dict):
            return {}, ["parameter_domains must be an object"]

        sizes: Dict[str, int] = {}
        errors: List[str] = []
        for name, domain in parameter_domains.items():
            if not isinstance(domain, dict):
                errors.append(f"parameter_domains.{name} must be an object")
                continue
            dtype = domain.get("type")
            if dtype == "fixed":
                sizes[name] = 1
            elif dtype == "set":
                values = domain.get("values")
                if not isinstance(values, list) or not values:
                    errors.append(f"parameter_domains.{name}.values must be a non-empty list")
                    continue
                sizes[name] = len(values)
            elif dtype == "range":
                start = domain.get("start")
                end = domain.get("end")
                step = domain.get("step")
                if not all(isinstance(v, (int, float)) for v in (start, end, step)):
                    errors.append(f"parameter_domains.{name} range requires numeric start/end/step")
                    continue
                if step <= 0:
                    errors.append(f"parameter_domains.{name}.step must be > 0")
                    continue
                if end < start:
                    errors.append(f"parameter_domains.{name} end must be >= start")
                    continue
                # Inclusive range count.
                sizes[name] = int(((end - start) // step) + 1)
            else:
                errors.append(
                    f"parameter_domains.{name}.type must be one of fixed/set/range"
                )
        return sizes, errors

    @staticmethod
    def _validate_nodes(node: Any, path: str, errors: List[str]) -> None:
        if not isinstance(node, dict):
            errors.append(f"{path} must be an object")
            return

        op = node.get("op")
        if not isinstance(op, str):
            errors.append(f"{path}.op must be a string")
            return

        if op in {"and", "or"}:
            nodes = node.get("nodes")
            if not isinstance(nodes, list) or len(nodes) < 2:
                errors.append(f"{path}.nodes must be a list with at least 2 nodes for op='{op}'")
                return
            for i, child in enumerate(nodes):
                StrategyContractValidator._validate_nodes(child, f"{path}.nodes[{i}]", errors)
            return

        if op == "not":
            if "node" not in node:
                errors.append(f"{path}.node is required for op='not'")
                return
            StrategyContractValidator._validate_nodes(node.get("node"), f"{path}.node", errors)
            return

        if op in COMPARE_OPS:
            if "left" not in node or "right" not in node:
                errors.append(f"{path}.left and {path}.right are required for op='{op}'")
            return

        if op in SEQUENTIAL_ONLY_OPS:
            if op == "session.same_session_close":
                return
            value = node.get("value")
            is_positive_int = isinstance(value, int) and value >= 1
            is_param_ref = (
                isinstance(value, dict)
                and isinstance(value.get("param_ref"), str)
                and bool(str(value.get("param_ref")).strip())
            )
            if not (is_positive_int or is_param_ref):
                errors.append(
                    f"{path}.value must be int >= 1 or {{\"param_ref\": \"...\"}} for op='{op}'"
                )
            return

        if op in CALENDAR_OPS:
            StrategyContractValidator._validate_calendar_node(node, path, errors)
            return

        errors.append(f"{path}.op '{op}' is not supported")

    @staticmethod
    def _validate_calendar_node(node: Dict[str, Any], path: str, errors: List[str]) -> None:
        op = str(node.get("op", "")).lower()
        if op == "calendar.month_in":
            months = node.get("months")
            if not isinstance(months, list) or not months:
                errors.append(f"{path}.months must be a non-empty list for op='{op}'")
            return
        if op in {
            "calendar.month_start",
            "calendar.month_end",
            "calendar.quarter_start",
            "calendar.quarter_end",
        }:
            months = node.get("months")
            if months is not None and not isinstance(months, list):
                errors.append(f"{path}.months must be a list when provided for op='{op}'")
            return
        if op == "calendar.weekday_eq":
            if node.get("weekday") in (None, ""):
                errors.append(f"{path}.weekday is required for op='{op}'")
            return
        if op == "calendar.last_weekday_of_month":
            if node.get("weekday") in (None, ""):
                errors.append(f"{path}.weekday is required for op='{op}'")
            months = node.get("months")
            if months is not None and not isinstance(months, list):
                errors.append(f"{path}.months must be a list when provided for op='{op}'")
            return
        if op == "calendar.nth_weekday_of_month":
            if node.get("weekday") in (None, ""):
                errors.append(f"{path}.weekday is required for op='{op}'")
            if node.get("ordinal") in (None, "", 0):
                errors.append(f"{path}.ordinal is required for op='{op}'")
            months = node.get("months")
            if months is not None and not isinstance(months, list):
                errors.append(f"{path}.months must be a list when provided for op='{op}'")
            return
        if op == "calendar.event_date":
            if not node.get("dates") and not node.get("path"):
                errors.append(f"{path}.dates or {path}.path is required for op='{op}'")
            return

    @staticmethod
    def _count_combinations(domain_sizes: Dict[str, int]) -> int:
        if not domain_sizes:
            return 1
        return int(reduce(operator.mul, domain_sizes.values(), 1))

    @staticmethod
    def _collect_fields(strategy_contract: Dict[str, Any]) -> Set[str]:
        fields: Set[str] = set()

        def walk_expr(expr: Any) -> None:
            if isinstance(expr, dict):
                if isinstance(expr.get("field"), str):
                    fields.add(expr["field"])
                for value in expr.values():
                    walk_expr(value)
            elif isinstance(expr, list):
                for item in expr:
                    walk_expr(item)

        def walk_node(node: Any) -> None:
            if not isinstance(node, dict):
                return
            op = node.get("op")
            if op in {"and", "or"}:
                for child in node.get("nodes", []):
                    walk_node(child)
                return
            if op == "not":
                walk_node(node.get("node"))
                return
            if op in COMPARE_OPS:
                walk_expr(node.get("left"))
                walk_expr(node.get("right"))
                return
            if op in SEQUENTIAL_ONLY_OPS or op in CALENDAR_OPS:
                return

        walk_node(strategy_contract.get("entry"))
        walk_node(strategy_contract.get("exit"))
        return fields

    @staticmethod
    def _collect_feature_fields(feature_contract: Optional[Dict[str, Any]]) -> Set[str]:
        if not feature_contract:
            return set()
        features = feature_contract.get("features")
        if not isinstance(features, list):
            return set()
        fields = set()
        for feature in features:
            if isinstance(feature, dict) and isinstance(feature.get("field"), str):
                fields.add(feature["field"])
        return fields

    @staticmethod
    def _collect_param_refs(strategy_contract: Dict[str, Any]) -> Set[str]:
        refs: Set[str] = set()

        def walk(obj: Any) -> None:
            if isinstance(obj, dict):
                pref = obj.get("param_ref")
                if isinstance(pref, str):
                    refs.add(pref)
                for value in obj.values():
                    walk(value)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item)

        walk(strategy_contract.get("entry"))
        walk(strategy_contract.get("exit"))
        return refs

    @staticmethod
    def _collect_sequential_requirements(strategy_contract: Dict[str, Any]) -> Set[str]:
        reqs: Set[str] = set()

        def walk_node(node: Any) -> None:
            if not isinstance(node, dict):
                return
            op = node.get("op")
            if op in SEQUENTIAL_ONLY_OPS:
                reqs.add(op)
                return
            if op in {"and", "or"}:
                for child in node.get("nodes", []):
                    walk_node(child)
                return
            if op == "not":
                walk_node(node.get("node"))
                return
            if op in COMPARE_OPS:
                return
            if op in CALENDAR_OPS:
                return

        walk_node(strategy_contract.get("entry"))
        walk_node(strategy_contract.get("exit"))
        execution = strategy_contract.get("execution", {})
        if isinstance(execution, dict) and (
            str(execution.get("session_scope") or "").strip().lower() == "same_session"
            or bool(execution.get("same_session_exit", False))
        ):
            reqs.add("session.same_session_close")
        return reqs

    @staticmethod
    def _get_requested_mode(strategy_contract: Dict[str, Any], errors: List[str]) -> str:
        prefs = strategy_contract.get("engine_preferences", {}) or {}
        requested = prefs.get("requested_mode", "auto")
        if not isinstance(requested, str):
            errors.append("engine_preferences.requested_mode must be a string")
            return "auto"
        requested = requested.strip().lower()
        if requested not in VALID_REQUESTED_MODES:
            errors.append("engine_preferences.requested_mode must be auto/node_ir")
            return "auto"
        return requested

    @staticmethod
    def _resolve_engine_mode(
        requested_mode: str,
        sequential_requirements: Iterable[str],
        errors: List[str],
    ) -> str:
        _ = list(sequential_requirements)
        if requested_mode == "auto":
            return "node_ir"
        return requested_mode
