"""Compile semantic strategy contracts into execution plan artifacts.

Current scope:
- Validate strategy/feature contracts via StrategyPreview.
- Produce deterministic execution_plan JSON with field catalog and feature DAG.
- Emit the execution plan used by semantic-native runtime paths.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from .StrategyPreview import StrategyPreview


@dataclass
class CompileResult:
    valid: bool
    errors: List[str]
    warnings: List[str]
    execution_plan_path: Optional[str]
    execution_plan: Dict[str, Any]


class StrategyCompiler:
    """Compile strategy contract into deterministic execution plan."""

    PLAN_VERSION = "2.1"
    COMPILER_VERSION = "0.2.0"

    def __init__(self) -> None:
        self.previewer = StrategyPreview()

    def compile_from_paths(
        self,
        strategy_contract_path: str,
        feature_contract_path: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> CompileResult:
        strategy_data = self._load_json(strategy_contract_path)
        feature_data = self._load_json(feature_contract_path) if feature_contract_path else {}

        preview = self.previewer.preview(
            strategy_contract_path=strategy_contract_path,
            feature_contract_path=feature_contract_path,
        )
        if not preview["valid"]:
            return CompileResult(
                valid=False,
                errors=list(preview["errors"]),
                warnings=list(preview.get("warnings", [])),
                execution_plan_path=None,
                execution_plan={},
            )

        field_catalog = self._build_field_catalog(
            preview["summary"].get("fields", []),
            feature_data=feature_data,
        )
        feature_dag = self._build_feature_dag(strategy_data)
        node_ir = self._build_node_ir(strategy_data, field_catalog, feature_dag)
        field_symbol_table = self._build_field_symbol_table(field_catalog)
        feature_eval_order = self._build_feature_eval_order(feature_dag)
        param_axes = self._build_param_axes(strategy_data)
        combo_guard = self._build_combo_guard(strategy_data, param_axes)
        if combo_guard.get("error"):
            return CompileResult(
                valid=False,
                errors=[str(combo_guard["error"])],
                warnings=list(combo_guard.get("warnings", [])),
                execution_plan_path=None,
                execution_plan={},
            )
        signal_kernel_hints = self._build_signal_kernel_hints(node_ir)
        execution = self._build_execution(strategy_data)
        stateful_flags = self._build_stateful_flags(node_ir, execution)

        plan = {
            "plan_version": self.PLAN_VERSION,
            "compiler_version": self.COMPILER_VERSION,
            "strategy_contract_path": str(Path(strategy_contract_path).resolve()),
            "feature_contract_path": (
                str(Path(feature_contract_path).resolve()) if feature_contract_path else None
            ),
            "strategy_id": strategy_data.get("strategy_id", ""),
            "field_catalog": field_catalog,
            "feature_contract_summary": self._build_feature_contract_summary(feature_data),
            "feature_dag": feature_dag,
            "feature_eval_order": feature_eval_order,
            "param_axes": param_axes,
            "combo_guard": combo_guard,
            "node_ir": node_ir,
            "execution": execution,
            "signal_kernel_hints": signal_kernel_hints,
            "stateful_flags": stateful_flags,
            "field_symbol_table": field_symbol_table,
            "engine_resolution": {
                "requested_mode": preview["summary"].get("requested_mode"),
                "resolved_mode": preview["summary"].get("resolved_mode"),
                "sequential_requirements": preview["summary"].get(
                    "sequential_requirements", []
                ),
            },
            "validation_report": {
                "errors": list(preview.get("errors", [])),
                "warnings": list(preview.get("warnings", [])) + list(combo_guard.get("warnings", [])),
                "summary": dict(preview.get("summary", {})),
            },
            "unknown_unknowns": self._collect_unknown_unknowns(strategy_data, feature_data),
        }
        plan["plan_hash"] = self._hash_plan(plan)

        plan_path = self._write_execution_plan(
            strategy_id=strategy_data.get("strategy_id", "strategy"),
            plan=plan,
            output_dir=output_dir,
        )

        return CompileResult(
            valid=True,
            errors=[],
            warnings=list(preview.get("warnings", [])) + list(combo_guard.get("warnings", [])),
            execution_plan_path=plan_path,
            execution_plan=plan,
        )

    @staticmethod
    def _build_combo_guard(strategy_data: Dict[str, Any], param_axes: List[Dict[str, Any]]) -> Dict[str, Any]:
        warnings: List[str] = []
        total = 1
        for axis in param_axes:
            cardinality = int(axis.get("cardinality", 1) or 1)
            total *= max(1, cardinality)

        has_sweep = any(int(axis.get("cardinality", 1) or 1) > 1 for axis in param_axes)
        combo_limits = strategy_data.get("combo_limits", {})
        if not isinstance(combo_limits, dict):
            combo_limits = {}

        raw_hard = strategy_data.get("max_combos")
        if raw_hard is None:
            raw_hard = combo_limits.get("hard_cap_combos")
        hard_cap = int(raw_hard) if isinstance(raw_hard, int) and raw_hard > 0 else None

        raw_warn = combo_limits.get("warn_combos")
        warn_cap = int(raw_warn) if isinstance(raw_warn, int) and raw_warn > 0 else None

        raw_window = combo_limits.get("window_cap_combos")
        window_cap = int(raw_window) if isinstance(raw_window, int) and raw_window > 0 else None

        if has_sweep and hard_cap is None:
            warnings.append("parameter sweep detected without hard_cap_combos/max_combos; consider setting combo_limits.hard_cap_combos")
        if warn_cap is not None and total > warn_cap:
            warnings.append(f"total combos {total} exceeds warn_combos {warn_cap}")

        if hard_cap is not None and total > hard_cap:
            return {
                "total_combos": total,
                "warn_combos": warn_cap,
                "hard_cap_combos": hard_cap,
                "window_cap_combos": window_cap,
                "warnings": warnings + [f"total combos {total} exceeds hard_cap_combos {hard_cap}"],
                "error": f"parameter space too large: total_combos={total} > hard_cap_combos={hard_cap}",
            }
        return {
            "total_combos": total,
            "warn_combos": warn_cap,
            "hard_cap_combos": hard_cap,
            "window_cap_combos": window_cap,
            "warnings": warnings,
            "error": None,
        }

    @staticmethod
    def _load_json(path: Optional[str]) -> Dict[str, Any]:
        if not path:
            return {}
        content = Path(path).read_text(encoding="utf-8-sig")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError(f"{path} must be a JSON object")
        return data

    def _build_field_catalog(
        self,
        fields: Iterable[str],
        *,
        feature_data: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        catalog: Dict[str, Dict[str, Any]] = {}
        feature_index = self._index_feature_contract(feature_data)
        for field in sorted(set(str(item) for item in fields if isinstance(item, str))):
            source_id, raw_column = self._split_field(field)
            feature_spec = feature_index.get(field, {})
            feature_source = (
                feature_spec.get("source", {})
                if isinstance(feature_spec.get("source"), dict)
                else {}
            )
            source_uri = feature_source.get("uri")
            source_column = feature_source.get("column")
            source_source_id = feature_source.get("source_id")
            source_time_field = feature_source.get("time_field")
            source_instrument_field = feature_source.get("instrument_field")
            fid = self._make_fid(source_id=source_id, raw_column=raw_column, role="field")
            catalog[fid] = {
                "fid": fid,
                "field": field,
                "source_id": source_id,
                "raw_column": raw_column,
                "source_uri": source_uri if isinstance(source_uri, str) else None,
                "source_column": source_column if isinstance(source_column, str) else None,
                "source_contract_id": (
                    source_source_id if isinstance(source_source_id, str) else None
                ),
                "source_time_field": (
                    source_time_field if isinstance(source_time_field, str) else None
                ),
                "source_instrument_field": (
                    source_instrument_field
                    if isinstance(source_instrument_field, str)
                    else None
                ),
                "source_type": (
                    feature_source.get("type")
                    if isinstance(feature_source.get("type"), str)
                    else "unknown"
                ),
                "dtype": (
                    feature_spec.get("dtype")
                    if isinstance(feature_spec.get("dtype"), str)
                    else "unknown"
                ),
                "unit": (
                    feature_spec.get("unit")
                    if isinstance(feature_spec.get("unit"), str)
                    else "unknown"
                ),
                "timezone": (
                    feature_spec.get("timezone")
                    if isinstance(feature_spec.get("timezone"), str)
                    else "unknown"
                ),
                "frequency": (
                    feature_spec.get("frequency")
                    if isinstance(feature_spec.get("frequency"), str)
                    else "unknown"
                ),
                "fill_policy": (
                    feature_spec.get("fill_policy")
                    if isinstance(feature_spec.get("fill_policy"), str)
                    else "unknown"
                ),
                "lag_bars": (
                    int(feature_spec.get("lag_bars"))
                    if isinstance(feature_spec.get("lag_bars"), int)
                    else 0
                ),
                "calendar": (
                    feature_spec.get("calendar")
                    if isinstance(feature_spec.get("calendar"), str)
                    else None
                ),
                "staleness_max_bars": (
                    int(feature_spec.get("staleness_max_bars"))
                    if isinstance(feature_spec.get("staleness_max_bars"), int)
                    else None
                ),
            }
        return catalog

    @staticmethod
    def _build_feature_contract_summary(feature_data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(feature_data, dict) or not feature_data:
            return {}
        features = feature_data.get("features", [])
        feature_items = features if isinstance(features, list) else []
        source_ids = sorted(
            {
                str(item.get("source", {}).get("source_id")).strip()
                for item in feature_items
                if isinstance(item, dict)
                and isinstance(item.get("source"), dict)
                and str(item.get("source", {}).get("source_id", "")).strip()
            }
        )
        source_uris = sorted(
            {
                str(item.get("source", {}).get("uri")).strip()
                for item in feature_items
                if isinstance(item, dict)
                and isinstance(item.get("source"), dict)
                and str(item.get("source", {}).get("uri", "")).strip()
            }
        )
        calendars = sorted(
            {
                str(item.get("calendar")).strip()
                for item in feature_items
                if isinstance(item, dict) and str(item.get("calendar", "")).strip()
            }
        )
        alignment = feature_data.get("alignment_policy", {})
        time_semantics = feature_data.get("time_semantics", {})
        return {
            "dataset_id": feature_data.get("dataset_id"),
            "feature_count": len(feature_items),
            "source_count": len(source_uris) if source_uris else len(source_ids),
            "source_ids": source_ids,
            "source_uris": source_uris,
            "multi_source": (len(source_uris) > 1 or len(source_ids) > 1),
            "join_mode": alignment.get("join_mode") if isinstance(alignment, dict) else None,
            "calendar_policy": (
                alignment.get("calendar_policy") if isinstance(alignment, dict) else None
            ),
            "asof_tolerance_bars": (
                alignment.get("asof_tolerance_bars") if isinstance(alignment, dict) else None
            ),
            "signal_observation_time": (
                time_semantics.get("signal_observation_time")
                if isinstance(time_semantics, dict)
                else None
            ),
            "trade_earliest_time": (
                time_semantics.get("trade_earliest_time")
                if isinstance(time_semantics, dict)
                else None
            ),
            "calendars": calendars,
        }

    @staticmethod
    def _index_feature_contract(feature_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        features = feature_data.get("features", [])
        if not isinstance(features, list):
            return {}
        index: Dict[str, Dict[str, Any]] = {}
        for item in features:
            if not isinstance(item, dict):
                continue
            field = item.get("field")
            if isinstance(field, str):
                index[field] = item
        return index

    @staticmethod
    def _build_field_symbol_table(field_catalog: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for fid, item in sorted(field_catalog.items(), key=lambda x: x[1].get("field", "")):
            rows.append(
                {
                    "fid": fid,
                    "field": item.get("field"),
                    "source_id": item.get("source_id"),
                    "source_column": item.get("source_column"),
                    "source_uri": item.get("source_uri"),
                    "frequency": item.get("frequency"),
                    "timezone": item.get("timezone"),
                    "lag_bars": item.get("lag_bars"),
                }
            )
        return rows

    def _build_feature_dag(self, strategy_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        nodes: Dict[str, Dict[str, Any]] = {}

        def walk_expr(expr: Any) -> None:
            if isinstance(expr, dict):
                if isinstance(expr.get("feature"), str):
                    key = self._feature_key(expr)
                    if key not in nodes:
                        nodes[key] = {
                            "feature_key": key,
                            "feature": expr.get("feature"),
                            "source": expr.get("source"),
                            "params": expr.get("params", {}),
                        }
                for value in expr.values():
                    walk_expr(value)
            elif isinstance(expr, list):
                for item in expr:
                    walk_expr(item)

        walk_expr(strategy_data.get("entry"))
        walk_expr(strategy_data.get("exit"))
        return nodes

    def _build_node_ir(
        self,
        strategy_data: Dict[str, Any],
        field_catalog: Dict[str, Dict[str, Any]],
        feature_dag: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        field_to_fid = {item["field"]: fid for fid, item in field_catalog.items()}
        feature_keys = set(feature_dag.keys())

        def transform_expr(expr: Any) -> Any:
            if isinstance(expr, dict):
                if isinstance(expr.get("field"), str):
                    field = expr["field"]
                    return {
                        "field_ref": {
                            "fid": field_to_fid.get(field, ""),
                            "field": field,
                        }
                    }
                if isinstance(expr.get("feature"), str):
                    key = self._feature_key(expr)
                    return {"feature_ref": {"feature_key": key if key in feature_keys else key}}
                return {k: transform_expr(v) for k, v in expr.items()}
            if isinstance(expr, list):
                return [transform_expr(item) for item in expr]
            return expr

        return {
            "entry": transform_expr(strategy_data.get("entry")),
            "exit": transform_expr(strategy_data.get("exit")),
        }

    @staticmethod
    def _build_feature_eval_order(feature_dag: Dict[str, Dict[str, Any]]) -> List[str]:
        if not isinstance(feature_dag, dict):
            return []
        return sorted(str(key) for key in feature_dag.keys())

    @staticmethod
    def _build_param_axes(strategy_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        parameter_domains = strategy_data.get("parameter_domains", {})
        if not isinstance(parameter_domains, dict):
            return []
        axes: List[Dict[str, Any]] = []
        for axis_idx, (name, spec) in enumerate(parameter_domains.items()):
            values = StrategyCompiler._expand_domain_values(spec)
            axes.append(
                {
                    "axis": axis_idx,
                    "name": str(name),
                    "type": (
                        str(spec.get("type", "fixed")).lower()
                        if isinstance(spec, dict)
                        else "fixed"
                    ),
                    "cardinality": len(values),
                    "values": values,
                }
            )
        return axes

    @staticmethod
    def _expand_domain_values(spec: Any) -> List[Any]:
        if not isinstance(spec, dict):
            return [spec]
        domain_type = str(spec.get("type", "fixed")).lower()
        if domain_type == "fixed":
            return [spec.get("value")]
        if domain_type == "set":
            values = spec.get("values", [])
            return list(values) if isinstance(values, list) and values else [None]
        if domain_type == "range":
            start = spec.get("start")
            end = spec.get("end")
            step = spec.get("step")
            if not all(isinstance(v, (int, float)) for v in (start, end, step)) or step == 0:
                return [None]
            values: List[Any] = []
            current = float(start)
            while current <= float(end) + 1e-12:
                if float(current).is_integer():
                    values.append(int(round(current)))
                else:
                    values.append(round(current, 10))
                current += float(step)
            return values or [None]
        return [spec.get("value")]

    @staticmethod
    def _build_signal_kernel_hints(node_ir: Dict[str, Any]) -> Dict[str, Any]:
        ops: Set[str] = set()

        def walk_expr(expr: Any) -> None:
            if isinstance(expr, dict):
                op = expr.get("op")
                if isinstance(op, str):
                    ops.add(op.lower())
                for value in expr.values():
                    walk_expr(value)
            elif isinstance(expr, list):
                for item in expr:
                    walk_expr(item)

        walk_expr(node_ir)
        bitset_safe_ops = {
            "and",
            "or",
            "not",
            "gt",
            "lt",
            "ge",
            "le",
            "eq",
            "ne",
            "cross_up",
            "cross_down",
            "calendar.month_in",
            "calendar.weekday_eq",
            "calendar.last_weekday_of_month",
            "calendar.nth_weekday_of_month",
            "calendar.event_date",
            "session.same_session_close",
        }
        return {
            "ops": sorted(ops),
            "bitset_compatible": all(op in bitset_safe_ops for op in ops),
            "simd_candidate": True,
        }

    @staticmethod
    def _build_execution(strategy_data: Dict[str, Any]) -> Dict[str, Any]:
        execution = strategy_data.get("execution", {})
        return dict(execution) if isinstance(execution, dict) else {}

    @staticmethod
    def _build_stateful_flags(node_ir: Dict[str, Any], execution: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        exit_node = node_ir.get("exit", {}) if isinstance(node_ir, dict) else {}
        has_timer = False
        has_same_session_close = False

        def walk_exit(expr: Any) -> None:
            nonlocal has_timer, has_same_session_close
            if isinstance(expr, dict):
                op = str(expr.get("op", "")).lower()
                if op in {"timer_bars", "time_stop_bars"}:
                    has_timer = True
                if op == "session.same_session_close":
                    has_same_session_close = True
                for value in expr.values():
                    walk_exit(value)
            elif isinstance(expr, list):
                for item in expr:
                    walk_exit(item)

        walk_exit(exit_node)
        execution = execution or {}
        requires_session_adapter = (
            has_same_session_close
            or str(execution.get("session_scope") or "").strip().lower() == "same_session"
            or bool(execution.get("same_session_exit", False))
        )
        return {
            "requires_sequential_exit_state": has_timer or requires_session_adapter,
            "has_timer_bars": has_timer,
            "has_same_session_close": has_same_session_close,
            "requires_session_adapter": requires_session_adapter,
        }

    def _collect_unknown_unknowns(
        self, strategy_data: Dict[str, Any], feature_data: Dict[str, Any]
    ) -> List[str]:
        risks: List[str] = []
        if feature_data:
            features = feature_data.get("features", [])
            unknown_dtypes = [
                f.get("field")
                for f in features
                if isinstance(f, dict) and str(f.get("dtype", "unknown")) == "unknown"
            ]
            if unknown_dtypes:
                risks.append(
                    "feature contract contains unknown dtype; strict type/unit checks may fail"
                )
            distinct_uris = {
                str(f.get("source", {}).get("uri")).strip()
                for f in features
                if isinstance(f, dict)
                and isinstance(f.get("source"), dict)
                and str(f.get("source", {}).get("uri", "")).strip()
            }
            if len(distinct_uris) > 1:
                risks.append(
                    "multi-source feature contract detected; audit alignment_policy/calendar_policy carefully"
                )
        return risks

    def _write_execution_plan(
        self,
        strategy_id: str,
        plan: Dict[str, Any],
        output_dir: Optional[str],
    ) -> str:
        if output_dir:
            root = Path(output_dir)
        else:
            repo_root = Path(__file__).resolve().parents[1]
            root = repo_root / "outputs" / "backtester" / "plans"
        root.mkdir(parents=True, exist_ok=True)

        safe_strategy_id = re_safe(strategy_id or "strategy")
        file_name = f"{safe_strategy_id}_{plan['plan_hash'][:12]}.execution_plan.json"
        path = root / file_name
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    @staticmethod
    def _split_field(field: str) -> tuple[str, str]:
        parts = field.split(".")
        if len(parts) < 2:
            return "default", field
        return parts[0], parts[-1]

    @staticmethod
    def _make_fid(source_id: str, raw_column: str, role: str) -> str:
        seed = f"{source_id}|{raw_column}|{role}".encode("utf-8")
        digest = hashlib.sha1(seed, usedforsecurity=False).hexdigest()[:12]
        return f"fid_{digest}"

    @staticmethod
    def _feature_key(expr: Dict[str, Any]) -> str:
        payload = {
            "feature": expr.get("feature"),
            "source": expr.get("source"),
            "params": expr.get("params", {}),
            "model_id": expr.get("model_id"),
        }
        dumped = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha1(dumped.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
        return f"feat_{digest}"

    @staticmethod
    def _hash_plan(plan: Dict[str, Any]) -> str:
        payload = dict(plan)
        payload.pop("plan_hash", None)
        dumped = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def re_safe(value: str) -> str:
    safe = []
    for ch in value:
        if ch.isalnum() or ch in ("-", "_"):
            safe.append(ch)
        else:
            safe.append("_")
    return "".join(safe).strip("_") or "strategy"
