"""Unified strategy run config normalizer and execution planner.

This module is the contract bridge for the unified backtest direction.  It
does not replace the existing engines yet; it gives autorunner, WFA, app
payloads, and tests one normalized view of single-asset and multi-asset runs.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional

from backtester.ops.support_checker import (
    StrategyBuildingBlockSupportError,
    validate_strategy_run_support,
)

SCHEMA_VERSION = "strategy_run"
WFA_SCHEMA_VERSION = "wfa_run"

STRATEGY_MODE_IDS = {
    "single_asset_signal",
    "multi_factor_entry_exit_roles",
    "calendar_event_session",
    "multi_asset_portfolio",
    "multi_asset_trigger_selection",
    "dynamic_allocation_rules",
}

WORKFLOW_IDS = {
    "single_backtest",
    "parameter_matrix",
    "walk_forward_analysis",
    "rolling_validation",
    "statanalyser",
}

PORTFOLIO_MODE_IDS = {
    "multi_asset_portfolio",
    "multi_asset_trigger_selection",
    "dynamic_allocation_rules",
}


class StrategyRunConfigError(ValueError):
    """Raised when a strategy run config cannot be normalized or planned."""


@dataclass(frozen=True)
class ExecutionPlan:
    """Decision-complete execution plan for a normalized strategy run config."""

    schema_version: str
    strategy_mode_id: str
    workflow_id: str
    result_type: str
    universe_size: int
    uses_factor_pipeline: bool
    vector_precompute: bool
    accounting_backend: str
    execution_backend: str
    requires_portfolio_accounting: bool
    can_optimize_parameters: bool
    is_rolling_validation: bool
    stages: List[Dict[str, Any]]
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "strategy_mode_id": self.strategy_mode_id,
            "workflow_id": self.workflow_id,
            "result_type": self.result_type,
            "universe_size": self.universe_size,
            "uses_factor_pipeline": self.uses_factor_pipeline,
            "vector_precompute": self.vector_precompute,
            "accounting_backend": self.accounting_backend,
            "execution_backend": self.execution_backend,
            "requires_portfolio_accounting": self.requires_portfolio_accounting,
            "can_optimize_parameters": self.can_optimize_parameters,
            "is_rolling_validation": self.is_rolling_validation,
            "stages": deepcopy(self.stages),
            "reason": self.reason,
        }


def normalize_strategy_run_config(
    raw_config: Mapping[str, Any],
    *,
    source_path: Optional[Path | str] = None,
    repo_root: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Normalize current run configs into StrategyRunConfig.

    Supported inputs:
    - already-normalized ``schema_version = strategy_run``
    - current single-asset semantic configs with ``backtester.strategy_mode = semantic``
    - current multi-asset configs with ``backtester.strategy_mode = multi_asset_portfolio``
    """

    raw = deepcopy(dict(raw_config or {}))
    if raw.get("schema_version") == SCHEMA_VERSION:
        return _finalize_normalized(raw)

    backtester = _dict(raw.get("backtester"))
    if str(backtester.get("strategy_mode", "")).lower() == "multi_asset_portfolio":
        return _normalize_multi_asset_config(raw)
    if str(backtester.get("strategy_mode", "")).lower() == "semantic":
        return _normalize_single_asset_config(raw, source_path=source_path, repo_root=repo_root)

    raise StrategyRunConfigError(
        "Unsupported strategy run config; expected strategy_run, single-asset semantic, "
        "or multi_asset_portfolio."
    )


def normalize_wfa_run_config(
    raw_config: Mapping[str, Any],
    *,
    source_path: Optional[Path | str] = None,
    repo_root: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Normalize old and new WFA configs into the wfa_run shell."""

    raw = deepcopy(dict(raw_config or {}))
    if raw.get("schema_version") == WFA_SCHEMA_VERSION:
        return _finalize_wfa(raw, source_path=source_path, repo_root=repo_root)

    wfa_config = _dict(raw.get("wfa_config"))
    if not wfa_config:
        raise StrategyRunConfigError("WFA config requires wfa_config or schema_version=wfa_run")

    strategy_config_path = raw.get("strategy_config_path") or wfa_config.get("strategy_config_path")
    normalized: Dict[str, Any] = {
        "schema_version": WFA_SCHEMA_VERSION,
        "strategy_config_path": str(strategy_config_path or ""),
        "windowing": {
            "mode": wfa_config.get("mode", "rolling"),
            "size_mode": wfa_config.get("size_mode", wfa_config.get("window_size_mode")),
            "target_window_count": wfa_config.get("target_window_count"),
            "train_size": wfa_config.get("train_size"),
            "test_size": wfa_config.get("test_size"),
            "train_ratio": wfa_config.get("train_ratio", wfa_config.get("train_set_percentage")),
            "test_ratio": wfa_config.get("test_ratio", wfa_config.get("test_set_percentage")),
            "step_size": wfa_config.get("step_size"),
        },
        "optimizer": deepcopy(wfa_config.get("optimizer", {})),
        "acceptance": deepcopy(wfa_config.get("acceptance", {})),
        "outputs": {
            "selected_optimum": True,
            "candidate_diagnostics": True,
            "window_backtests": bool(wfa_config.get("window_backtests", False)),
        },
        "legacy_embedded_strategy_config": None,
    }
    if wfa_config.get("engine"):
        normalized["engine"] = wfa_config.get("engine")
    if wfa_config.get("runtime"):
        normalized["runtime"] = wfa_config.get("runtime")

    if not strategy_config_path and ("backtester" in raw or "dataloader" in raw):
        normalized["legacy_embedded_strategy_config"] = normalize_strategy_run_config(
            raw,
            source_path=source_path,
            repo_root=repo_root,
        )

    objectives = wfa_config.get("optimization_objectives")
    if objectives and "objectives" not in normalized["optimizer"]:
        normalized["optimizer"]["objectives"] = deepcopy(objectives)
    return _finalize_wfa(normalized, source_path=source_path, repo_root=repo_root)


def _resolve_wfa_strategy_config_path(
    strategy_config_path: str,
    *,
    source_path: Optional[Path | str] = None,
    repo_root: Optional[Path | str] = None,
) -> Optional[Path]:
    if not strategy_config_path or not source_path:
        return None
    source = Path(source_path).resolve()
    if repo_root is not None:
        root = Path(repo_root).resolve()
    else:
        root = source.parent
        for parent in [source.parent, *source.parents]:
            if (parent / "workspace").exists() or (parent / "backtester").exists():
                root = parent
                break
    candidate = Path(strategy_config_path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (root / PurePosixPath(strategy_config_path).as_posix()).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise StrategyRunConfigError("wfa_run strategy_config_path escaped repo root") from exc
    if not resolved.exists():
        raise StrategyRunConfigError(f"wfa_run strategy_config_path not found: {strategy_config_path}")
    return resolved


def _require_wfa_strategy_parameter_domains(config: Dict[str, Any]) -> None:
    parameter_domains = _dict(config.get("parameter_domains"))
    combo_count = _parameter_combo_count(parameter_domains)
    if combo_count <= 1:
        raise StrategyRunConfigError(
            "wfa_run requires the referenced strategy parameter_domains to expand "
            "to at least 2 combinations; use single_backtest for fixed strategies"
        )


def validate_repo_relative_json_path(path_text: Any, *, field_name: str = "path") -> str:
    """Validate a repo-relative JSON config reference.

    Runtime callers use this in addition to JSON Schema because some paths are
    resolved after loading and may bypass schema validation.
    """

    text = str(path_text or "").strip()
    if not text:
        return ""
    path = Path(text)
    normalized = text.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if path.is_absolute() or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise StrategyRunConfigError(f"{field_name} must be a repo-relative JSON path")
    if any(part == ".." for part in parts):
        raise StrategyRunConfigError(f"{field_name} must not contain parent-directory segments")
    if not normalized.lower().endswith(".json"):
        raise StrategyRunConfigError(f"{field_name} must point to a JSON config")
    return text


def plan_strategy_execution(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a vector-hybrid execution plan for a normalized config."""

    normalized = (
        normalize_strategy_run_config(config)
        if dict(config or {}).get("schema_version") != SCHEMA_VERSION
        else _finalize_normalized(dict(config))
    )
    platform = normalized["platform"]
    universe = normalized["universe"]
    mode_id = platform["strategy_mode_id"]
    workflow_id = platform["workflow_id"]
    symbols = list(universe.get("symbols") or [])
    result_type = "portfolio" if mode_id in PORTFOLIO_MODE_IDS or len(symbols) > 1 else "single_asset"
    has_computed_fields = bool(normalized.get("computed_fields"))
    has_factor_pipeline = bool(normalized.get("factor_pipeline"))
    has_signals = bool(normalized.get("signals"))
    has_selection = bool(normalized.get("selection"))
    has_rebalance = bool(normalized.get("rebalance"))
    parameter_domains = _dict(normalized.get("parameter_domains"))
    can_optimize = bool(parameter_domains)
    is_rolling_validation = workflow_id == "rolling_validation" or (
        workflow_id == "walk_forward_analysis" and not can_optimize
    )

    stages = [
        {
            "id": "factor_data_preparation",
            "backend": "vector",
            "enabled": has_factor_pipeline,
            "outputs": ["factor_input_frame", "data_quality"],
        },
        {
            "id": "factor_construction",
            "backend": "vector",
            "enabled": has_factor_pipeline,
            "outputs": ["factor_frame"],
        },
        {
            "id": "factor_preprocessing",
            "backend": "vector",
            "enabled": has_factor_pipeline,
            "outputs": ["clean_factor_frame"],
        },
        {
            "id": "factor_composite",
            "backend": "vector",
            "enabled": has_factor_pipeline,
            "outputs": ["factor_score_frame"],
        },
        {
            "id": "indicator_precompute",
            "backend": "vector",
            "enabled": has_computed_fields or has_factor_pipeline or has_signals or has_selection,
            "outputs": ["indicator_frame"],
        },
        {
            "id": "signal_or_selection",
            "backend": "vector",
            "enabled": has_signals or has_selection,
            "outputs": ["signal_frame", "target_candidates"],
        },
        {
            "id": "target_weight_generation",
            "backend": "vector",
            "enabled": has_signals or has_selection or has_rebalance,
            "outputs": ["target_weight_frame"],
        },
        {
            "id": "portfolio_accounting",
            "backend": "sequential",
            "enabled": True,
            "outputs": [
                "equity_curve",
                "trade_or_rebalance_events",
                "holdings",
                "asset_contribution",
            ],
        },
    ]

    reason = (
        "vector-hybrid: vector precompute for factors/indicators/signals/ranking, sequential "
        "accounting for cash, costs, turnover, holdings, and portfolio state"
    )
    return ExecutionPlan(
        schema_version="execution_plan.v1",
        strategy_mode_id=mode_id,
        workflow_id="rolling_validation" if is_rolling_validation else workflow_id,
        result_type=result_type,
        universe_size=len(symbols),
        uses_factor_pipeline=has_factor_pipeline,
        vector_precompute=any(stage["enabled"] and stage["backend"] == "vector" for stage in stages),
        accounting_backend="sequential",
        execution_backend="vector_hybrid",
        requires_portfolio_accounting=True,
        can_optimize_parameters=can_optimize,
        is_rolling_validation=is_rolling_validation,
        stages=stages,
        reason=reason,
    ).to_dict()


def validate_strategy_run_config(config: Mapping[str, Any]) -> None:
    _finalize_normalized(dict(config or {}))


def _normalize_multi_asset_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    platform = _dict(raw.get("platform"))
    dataloader = _dict(raw.get("dataloader"))
    backtester = _dict(raw.get("backtester"))
    portfolio = _dict(backtester.get("portfolio_config"))
    market_data = _dict(backtester.get("market_data"))
    data_context = _dict(portfolio.get("data_context"))
    benchmark = portfolio.get("benchmark") or market_data.get("benchmark") or {}

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "platform": {
            "strategy_mode_id": platform.get("strategy_mode_id") or "multi_asset_portfolio",
            "workflow_id": _normalize_workflow_id(platform.get("workflow_id") or portfolio.get("workflow")),
            "run_type": platform.get("run_type", ""),
            "display_label": platform.get("display_label", ""),
        },
        "data": {
            "provider": market_data.get("provider") or dataloader.get("source") or "multi_asset",
            "frequency": data_context.get("frequency") or dataloader.get("frequency") or market_data.get("interval"),
            "calendar": data_context.get("calendar"),
            "timezone": data_context.get("timezone"),
            "start_date": market_data.get("start") or dataloader.get("start_date"),
            "start_policy": market_data.get("start_policy"),
            "benchmark": benchmark,
        },
        "universe": deepcopy(portfolio.get("universe") or {"symbols": market_data.get("symbols", [])}),
        "factor_pipeline": deepcopy(portfolio.get("factor_pipeline", {})),
        "computed_fields": _computed_field_specs_from_aliases(portfolio),
        "signals": {},
        "selection": deepcopy(portfolio.get("selection", {})),
        "allocation": deepcopy(portfolio.get("allocation", {})),
        "rebalance": deepcopy(portfolio.get("rebalance", {})),
        "fill_model": deepcopy(_fill_model_from_aliases(portfolio)),
        "risk": deepcopy(portfolio.get("risk", {})),
        "parameter_domains": deepcopy(portfolio.get("parameter_domains", {})),
        "outputs": deepcopy(portfolio.get("outputs", {})),
        "metadata": {
            "source_schema": portfolio.get("schema_version", "multi_asset_portfolio.v1"),
            "strategy_id": portfolio.get("strategy_id") or backtester.get("Backtest_id"),
            "legacy_backtester": deepcopy(backtester),
        },
    }
    return _finalize_normalized(normalized)


def _normalize_single_asset_config(
    raw: Dict[str, Any],
    *,
    source_path: Optional[Path | str],
    repo_root: Optional[Path | str],
) -> Dict[str, Any]:
    platform = _dict(raw.get("platform"))
    dataloader = _dict(raw.get("dataloader"))
    backtester = _dict(raw.get("backtester"))
    strategy_ref = str(backtester.get("strategy_contract_path") or "").strip()
    strategy_contract = _load_optional_json(strategy_ref, source_path=source_path, repo_root=repo_root)
    data_context = _dict(strategy_contract.get("data_context"))
    trading_params = _dict(backtester.get("trading_params"))
    symbol = (
        data_context.get("primary_instrument")
        or _dict(dataloader.get("yfinance_config")).get("symbol")
        or dataloader.get("symbol")
    )
    execution_contract = _dict(strategy_contract.get("execution"))
    side = execution_contract.get("side", "long")
    trade_delay = trading_params.get("trade_delay")
    trade_delay_bars = 1 if str(trade_delay) == "1" else 0
    entry_price = (
        trading_params.get("trade_price")
        or execution_contract.get("entry_price")
        or "close"
    )
    exit_price = execution_contract.get("exit_price") or entry_price
    same_session_exit = bool(execution_contract.get("same_session_exit", False))

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "platform": {
            "strategy_mode_id": platform.get("strategy_mode_id") or _infer_single_strategy_mode(strategy_contract),
            "workflow_id": _normalize_workflow_id(platform.get("workflow_id")),
            "run_type": platform.get("run_type", ""),
            "display_label": platform.get("display_label", ""),
        },
        "data": {
            "provider": dataloader.get("source"),
            "frequency": data_context.get("frequency") or _dict(dataloader.get("yfinance_config")).get("interval"),
            "calendar": data_context.get("calendar"),
            "timezone": data_context.get("timezone"),
            "start_date": dataloader.get("start_date"),
            "start_policy": dataloader.get("start_policy"),
            "benchmark": backtester.get("benchmark"),
        },
        "universe": {"symbols": [str(symbol)] if symbol else []},
        "factor_pipeline": deepcopy(strategy_contract.get("factor_pipeline", {})),
        "computed_fields": [],
        "signals": {
            "entry": deepcopy(strategy_contract.get("entry", {})),
            "exit": deepcopy(strategy_contract.get("exit", {})),
            "side": side,
            "strategy_contract_path": strategy_ref,
            "feature_contract_path": backtester.get("feature_contract_path")
            or data_context.get("feature_contract_ref"),
        },
        "selection": {},
        "allocation": {
            "method": "signal_target_weight",
            "target_weight": -1.0 if str(side).lower() == "short" else 1.0,
            "cash_policy": "keep_unallocated_cash",
        },
        "rebalance": {"trigger": {"op": "signal.change"}},
        "fill_model": {
            "timing": "same_session" if same_session_exit else "bar_offset",
            "side": side,
            "entry_price": entry_price,
            "entry_delay_bars": 0 if same_session_exit else trade_delay_bars,
            "exit_price": exit_price,
            "exit_delay_bars": 0 if same_session_exit else trade_delay_bars,
            "session_scope": execution_contract.get("session_scope"),
            "same_session_exit": same_session_exit,
            "cost": {
                "transaction_cost": trading_params.get("transaction_cost", 0.0),
                "slippage": trading_params.get("slippage", 0.0),
            },
        },
        "risk": {
            "max_positions": 1,
            "max_gross_exposure": 1.0,
            "long_short": "short_only" if str(side).lower() == "short" else "long_only",
            "allow_short": str(side).lower() == "short",
        },
        "parameter_domains": deepcopy(strategy_contract.get("parameter_domains", {})),
        "outputs": {
            "equity_curve": True,
            "trade_summary": True,
            "entry_exit_markers": True,
        },
        "metadata": {
            "source_schema": strategy_contract.get("schema_version", "single_asset"),
            "strategy_id": strategy_contract.get("strategy_id") or backtester.get("Backtest_id"),
            "legacy_backtester": deepcopy(backtester),
        },
    }
    return _finalize_normalized(normalized)


def _finalize_normalized(config: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(config)
    out["schema_version"] = SCHEMA_VERSION
    out["platform"] = _dict(out.get("platform"))
    out["data"] = _dict(out.get("data"))
    out["universe"] = _dict(out.get("universe"))
    out["factor_pipeline"] = _dict(out.get("factor_pipeline"))
    if out.get("features") or out.get("indicators"):
        raise StrategyRunConfigError(
            "strategy_run uses computed_fields[] only; features[] and indicators[] are removed aliases."
        )
    computed_fields = list(out.get("computed_fields") or [])
    out["computed_fields"] = computed_fields
    out["signals"] = _dict(out.get("signals"))
    out["selection"] = _dict(out.get("selection"))
    out["allocation"] = _dict(out.get("allocation"))
    out["rebalance"] = _dict(out.get("rebalance"))
    fill_model = _dict(out.get("fill_model"))
    if out.get("execution"):
        raise StrategyRunConfigError(
            "strategy_run uses fill_model{} only; execution{} is a removed alias."
        )
    out["fill_model"] = fill_model
    out["risk"] = _dict(out.get("risk"))
    out["parameter_domains"] = _dict(out.get("parameter_domains"))
    out["outputs"] = _dict(out.get("outputs"))
    out["metadata"] = _dict(out.get("metadata"))

    platform = out["platform"]
    platform["strategy_mode_id"] = str(platform.get("strategy_mode_id") or "").strip()
    platform["workflow_id"] = _normalize_workflow_id(platform.get("workflow_id"))
    if platform["strategy_mode_id"] not in STRATEGY_MODE_IDS:
        raise StrategyRunConfigError(f"Unknown strategy_mode_id: {platform['strategy_mode_id']}")
    if platform["workflow_id"] not in WORKFLOW_IDS:
        raise StrategyRunConfigError(f"Unknown workflow_id: {platform['workflow_id']}")
    _validate_workflow_parameter_shape(platform["workflow_id"], out["parameter_domains"])
    symbols = out["universe"].get("symbols")
    if not isinstance(symbols, list) or not [item for item in symbols if str(item).strip()]:
        raise StrategyRunConfigError("StrategyRunConfig requires universe.symbols")
    out["universe"]["symbols"] = [str(item).strip().upper() for item in symbols if str(item).strip()]
    try:
        validate_strategy_run_support(out)
    except StrategyBuildingBlockSupportError as exc:
        raise StrategyRunConfigError(str(exc)) from exc
    _validate_fill_model_cost(fill_model)
    return out


def _validate_fill_model_cost(fill_model: Dict[str, Any]) -> None:
    cost = fill_model.get("cost")
    if not isinstance(cost, dict):
        raise StrategyRunConfigError(
            "fill_model.cost must explicitly declare transaction_cost and slippage"
        )
    for key in ("transaction_cost", "slippage"):
        value = cost.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise StrategyRunConfigError(f"fill_model.cost.{key} must be a non-negative number")


def _domain_value_count(domain: Any) -> int:
    if isinstance(domain, list):
        return len(domain)
    if not isinstance(domain, dict):
        return 1
    values = domain.get("values")
    if isinstance(values, list):
        return len(values)
    if domain.get("type") == "range":
        start = domain.get("start")
        end = domain.get("end")
        step = domain.get("step", 1)
        if not all(isinstance(value, (int, float)) for value in (start, end, step)):
            return 1
        if step == 0:
            return 1
        distance = end - start
        if distance == 0:
            return 1
        if (distance > 0 and step < 0) or (distance < 0 and step > 0):
            return 1
        return max(1, int(distance / step) + 1)
    return 1


def _parameter_combo_count(parameter_domains: Any) -> int:
    if not isinstance(parameter_domains, dict) or not parameter_domains:
        return 1
    total = 1
    for domain in parameter_domains.values():
        total *= _domain_value_count(domain)
    return total


def _validate_workflow_parameter_shape(workflow_id: str, parameter_domains: Any) -> None:
    has_domains = isinstance(parameter_domains, dict) and bool(parameter_domains)
    combo_count = _parameter_combo_count(parameter_domains)
    if workflow_id == "parameter_matrix" and combo_count <= 1:
        raise StrategyRunConfigError(
            "workflow_id=parameter_matrix requires parameter_domains with at least 2 combinations"
        )
    if workflow_id == "single_backtest" and has_domains:
        raise StrategyRunConfigError(
            "workflow_id=single_backtest must not carry parameter_domains; use parameter_matrix for sweeps"
        )


def _finalize_wfa(
    config: Dict[str, Any],
    *,
    source_path: Optional[Path | str] = None,
    repo_root: Optional[Path | str] = None,
) -> Dict[str, Any]:
    out = deepcopy(config)
    out["schema_version"] = WFA_SCHEMA_VERSION
    out["windowing"] = _dict(out.get("windowing"))
    out["optimizer"] = _dict(out.get("optimizer"))
    out["acceptance"] = _dict(out.get("acceptance"))
    out["outputs"] = _dict(out.get("outputs"))
    out["outputs"].setdefault("selected_optimum", True)
    out["outputs"].setdefault("candidate_diagnostics", True)
    out["outputs"].setdefault("window_backtests", False)
    strategy_config_path = str(out.get("strategy_config_path") or "").strip()
    if strategy_config_path:
        out["strategy_config_path"] = validate_repo_relative_json_path(
            strategy_config_path,
            field_name="strategy_config_path",
        )
    if not out.get("strategy_config_path") and not out.get("legacy_embedded_strategy_config"):
        raise StrategyRunConfigError("wfa_run requires strategy_config_path or legacy_embedded_strategy_config")
    if out.get("legacy_embedded_strategy_config"):
        _require_wfa_strategy_parameter_domains(out["legacy_embedded_strategy_config"])
    else:
        strategy_path = _resolve_wfa_strategy_config_path(
            str(out.get("strategy_config_path") or ""),
            source_path=source_path,
            repo_root=repo_root,
        )
        if strategy_path is not None:
            strategy_config = json.loads(strategy_path.read_text(encoding="utf-8-sig"))
            normalized_strategy = normalize_strategy_run_config(
                strategy_config,
                source_path=strategy_path,
                repo_root=repo_root,
            )
            _require_wfa_strategy_parameter_domains(normalized_strategy)
    return out


def _normalize_workflow_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "": "single_backtest",
        "single": "single_backtest",
        "portfolio_backtest": "single_backtest",
        "portfolio_parameter_matrix": "parameter_matrix",
        "matrix": "parameter_matrix",
        "wfa": "walk_forward_analysis",
        "walk-forward": "walk_forward_analysis",
        "walk_forward": "walk_forward_analysis",
        "rolling": "rolling_validation",
    }
    return mapping.get(text, text)


def _infer_single_strategy_mode(strategy_contract: Mapping[str, Any]) -> str:
    tags = {str(item).lower() for item in strategy_contract.get("tags", []) or []}
    if "calendar" in tags or str(_dict(strategy_contract.get("entry")).get("op", "")).startswith("calendar."):
        return "calendar_event_session"
    return "single_asset_signal"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _computed_field_specs_from_aliases(config: Mapping[str, Any]) -> List[Any]:
    if config.get("features") or config.get("indicators"):
        raise StrategyRunConfigError(
            "strategy_run uses computed_fields[] only; features[] and indicators[] are removed aliases."
        )
    computed_fields = list(config.get("computed_fields") or [])
    return computed_fields


def _fill_model_from_aliases(config: Mapping[str, Any]) -> Dict[str, Any]:
    fill_model = _dict(config.get("fill_model"))
    if config.get("execution"):
        raise StrategyRunConfigError(
            "strategy_run uses fill_model{} only; execution{} is a removed alias."
        )
    return fill_model


def _load_optional_json(
    path_text: str,
    *,
    source_path: Optional[Path | str],
    repo_root: Optional[Path | str],
) -> Dict[str, Any]:
    if not path_text:
        return {}
    candidates: List[Path] = []
    path = Path(path_text)
    if path.is_absolute():
        candidates.append(path)
    if source_path is not None:
        candidates.append(Path(source_path).resolve().parent / path_text)
    root = Path(repo_root).resolve() if repo_root is not None else Path.cwd().resolve()
    candidates.append(root / path_text)
    for candidate in candidates:
        try:
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # pragma: no cover - defensive error context
            raise StrategyRunConfigError(f"Unable to load strategy contract {candidate}: {exc}") from exc
    return {}
