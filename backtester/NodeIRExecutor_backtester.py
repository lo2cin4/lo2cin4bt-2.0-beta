"""Execute semantic strategy node IR with batched runtime backends."""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .BacktestExecutionRuntime_backtester import BacktestExecutionRuntime
from .CalendarConditionMaterializer_backtester import CalendarConditionMaterializer
from .FeatureContractMaterializer_backtester import (
    FeatureContractMaterializerBacktester,
)
from .RustSignalKernel_backtester import RustSignalKernel
from .RustSimKernel_backtester import RustSimKernel
from .TradeSimulator_backtester import TradeSimulator_backtester


class _PythonNumbaKernelBackend:
    def __init__(self, executor: "NodeIRExecutorBacktester"):
        self.executor = executor

    def run_batch(self, *, plan: Dict[str, Any], data: pd.DataFrame, param_grid: List[Dict[str, Any]], trading_params: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        _ = (plan, data)
        if not param_grid:
            return []
        chunk_size = max(1, int(context.get("chunk_size", 64) or 64))
        max_workers = max(1, int(context.get("max_workers", 1) or 1))
        chunks = [(start, param_grid[start : start + chunk_size]) for start in range(0, len(param_grid), chunk_size)]
        if max_workers == 1 or len(chunks) == 1:
            merged: List[Dict[str, Any]] = []
            for offset, combos in chunks:
                merged.extend(self.executor._run_combo_chunk(offset, combos, trading_params, {**context, "_resolved_backend": "python_numba"}))
            return merged
        all_results: Dict[int, List[Dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            tagged_context = {**context, "_resolved_backend": "python_numba"}
            futures = {pool.submit(self.executor._run_combo_chunk, offset, combos, trading_params, tagged_context): offset for offset, combos in chunks}
            for future, offset in futures.items():
                all_results[offset] = future.result()
        merged: List[Dict[str, Any]] = []
        for offset in sorted(all_results.keys()):
            merged.extend(all_results[offset])
        return merged


class _RustKernelBackend:
    def __init__(self, executor: "NodeIRExecutorBacktester"):
        self.executor = executor
        self._delegate = _PythonNumbaKernelBackend(executor)
        self._kernel = RustSimKernel(logger=executor.logger)

    def run_batch(self, *, plan: Dict[str, Any], data: pd.DataFrame, param_grid: List[Dict[str, Any]], trading_params: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self._kernel.is_available():
            self.executor.logger.info("execution_backend=rust requested but kernel unavailable; fallback to python")
            return self._delegate.run_batch(plan=plan, data=data, param_grid=param_grid, trading_params=trading_params, context=context)
        chunk_size = max(1, int(context.get("chunk_size", 64) or 64))
        merged: List[Dict[str, Any]] = []
        for offset in range(0, len(param_grid), chunk_size):
            combos = param_grid[offset : offset + chunk_size]
            merged.extend(
                self.executor._run_combo_chunk_rust(  # pylint: disable=protected-access
                    combo_offset=offset,
                    combos=combos,
                    trading_params=trading_params,
                    context={**context, "_resolved_backend": "rust_kernel"},
                    kernel=self._kernel,
                    fallback_backend=self._delegate,
                )
            )
        return merged


class _AutoKernelBackend:
    def __init__(self, executor: "NodeIRExecutorBacktester"):
        self.executor = executor
        self._python = _PythonNumbaKernelBackend(executor)
        self._rust: Optional[_RustKernelBackend] = None

    def run_batch(self, *, plan: Dict[str, Any], data: pd.DataFrame, param_grid: List[Dict[str, Any]], trading_params: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        if self._should_force_python_for_stateful(plan=plan, trading_params=trading_params):
            self.executor.logger.info(
                "execution_backend=auto forced to python_numba for stateful timer/reentry semantics"
            )
            return self._python.run_batch(
                plan=plan,
                data=data,
                param_grid=param_grid,
                trading_params=trading_params,
                context=context,
            )
        min_combos = max(1, int((trading_params or {}).get("auto_rust_min_combos", 1) or 1))
        use_rust = False
        if len(param_grid) >= min_combos:
            if self._rust is None:
                self._rust = _RustKernelBackend(self.executor)
            use_rust = self._rust._kernel.is_available()  # pylint: disable=protected-access
        if use_rust:
            return self._rust.run_batch(plan=plan, data=data, param_grid=param_grid, trading_params=trading_params, context=context)
        return self._python.run_batch(plan=plan, data=data, param_grid=param_grid, trading_params=trading_params, context=context)

    @staticmethod
    def _should_force_python_for_stateful(*, plan: Dict[str, Any], trading_params: Dict[str, Any]) -> bool:
        if bool((trading_params or {}).get("reset_timer_on_reentry_signal", False)):
            return True

        stateful_flags = plan.get("stateful_flags", {}) if isinstance(plan, dict) else {}
        if isinstance(stateful_flags, dict) and bool(stateful_flags.get("requires_sequential_exit_state", False)):
            return True

        node_ir = plan.get("node_ir", {}) if isinstance(plan, dict) else {}
        exit_node = node_ir.get("exit") if isinstance(node_ir, dict) else None
        return _AutoKernelBackend._exit_has_timer(exit_node)

    @staticmethod
    def _exit_has_timer(node: Any) -> bool:
        if isinstance(node, dict):
            op = str(node.get("op", "")).lower()
            if op in {"timer_bars", "time_stop_bars"}:
                return True
            return any(_AutoKernelBackend._exit_has_timer(child) for child in node.values())
        if isinstance(node, list):
            return any(_AutoKernelBackend._exit_has_timer(child) for child in node)
        return False


class NodeIRExecutorBacktester:
    def __init__(self, data: pd.DataFrame, logger: Optional[logging.Logger] = None):
        self.data = data.copy()
        self.logger = logger or logging.getLogger("lo2cin4bt.backtester.node_ir")
        self.repo_root = Path(__file__).resolve().parent.parent
        self.runtime = BacktestExecutionRuntime(logger=self.logger)
        self._normalize_price_columns()
        self._series_cache: Dict[str, pd.Series] = {}
        self._feature_cache: Dict[Tuple[str, str, int], pd.Series] = {}
        self._array_cache: Dict[str, np.ndarray] = {}
        self._feature_array_cache: Dict[Tuple[str, str, int], np.ndarray] = {}
        self._cache_lock = RLock()
        self._materialized_fields: set[str] = set()
        self._rust_signal_kernel = RustSignalKernel(logger=self.logger)
        self._calendar_materializer = CalendarConditionMaterializer(
            self.data,
            repo_root=self.repo_root,
        )

    def run_from_paths(self, *, strategy_contract_path: str, feature_contract_path: Optional[str], execution_plan: Dict[str, Any], trading_params: Dict[str, Any], predictor_column: str, symbol: str, backtest_id_prefix: str) -> List[Dict[str, Any]]:
        strategy_contract = self._load_json(strategy_contract_path)
        feature_contract = self._load_json(feature_contract_path) if feature_contract_path else {}
        self._materialize_feature_contract_sources(
            feature_contract=feature_contract,
            feature_contract_path=feature_contract_path,
        )
        semantic_predictor_fields = self._collect_semantic_export_fields(execution_plan)
        combos = self._expand_parameter_domains(strategy_contract.get("parameter_domains", {}))
        if not combos:
            combos = [{}]
        self._prepare_feature_cache(execution_plan, combos, feature_contract)
        audit_index = self._build_semantic_audit_index(
            execution_plan=execution_plan,
            strategy_contract_path=strategy_contract_path,
            feature_contract=feature_contract,
            feature_contract_path=feature_contract_path,
            semantic_predictor_fields=semantic_predictor_fields,
        )
        runtime_trading_params = dict(trading_params or {})
        strategy_execution = strategy_contract.get("execution", {})
        if isinstance(strategy_execution, dict) and strategy_execution:
            runtime_trading_params["strategy_execution"] = strategy_execution
        backend_name = str(runtime_trading_params.get("execution_backend", "auto")).strip().lower()
        if self._plan_requires_session_adapter(execution_plan):
            backend_name = "python_numba"
        backend = self._resolve_backend(backend_name)
        context = {
            "execution_plan": execution_plan,
            "feature_contract": feature_contract,
            "feature_contract_path": feature_contract_path,
            "predictor_column": predictor_column,
            "symbol": symbol,
            "backtest_id_prefix": backtest_id_prefix,
            "semantic_predictor_fields": semantic_predictor_fields,
            "audit_index": audit_index,
            "chunk_size": (trading_params or {}).get("chunk_size", 64),
            "max_workers": (trading_params or {}).get("max_workers", 1),
        }
        return self.runtime.run_batch(plan=execution_plan, data=self.data, param_grid=combos, trading_params=runtime_trading_params, context=context, backend=backend)

    def _materialize_feature_contract_sources(
        self,
        *,
        feature_contract: Dict[str, Any],
        feature_contract_path: Optional[str],
    ) -> None:
        if not isinstance(feature_contract, dict) or not feature_contract.get("features"):
            return
        materializer = FeatureContractMaterializerBacktester(
            base_data=self.data,
            repo_root=self.repo_root,
            logger=self.logger,
        )
        self.data = materializer.materialize(
            feature_contract=feature_contract,
            feature_contract_path=feature_contract_path,
        )
        self._materialized_fields = {
            str(item.get("field"))
            for item in feature_contract.get("features", [])
            if isinstance(item, dict) and isinstance(item.get("field"), str) and str(item.get("field")) in self.data.columns
        }
        self._normalize_price_columns()
        self._series_cache = {}
        self._feature_cache = {}
        self._array_cache = {}
        self._feature_array_cache = {}
        self._calendar_materializer = CalendarConditionMaterializer(
            self.data,
            repo_root=self.repo_root,
        )

    def _resolve_backend(self, backend_name: str) -> Any:
        if backend_name in {"python_numba", "numba", "python"}:
            return _PythonNumbaKernelBackend(self)
        if backend_name == "auto":
            return _AutoKernelBackend(self)
        if backend_name in {"rust", "rust_kernel"}:
            return _RustKernelBackend(self)
        self.logger.warning("Unknown execution_backend '%s', fallback to auto", backend_name)
        return _AutoKernelBackend(self)

    def _run_combo_chunk(self, combo_offset: int, combos: List[Dict[str, Any]], trading_params: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        execution_plan = context["execution_plan"]
        feature_contract = context["feature_contract"]
        predictor_column = context["predictor_column"]
        symbol = context["symbol"]
        backtest_id_prefix = context["backtest_id_prefix"]
        semantic_predictor_fields = context.get("semantic_predictor_fields", [])
        audit_index = context.get("audit_index")
        resolved_backend = str(context.get("_resolved_backend", "python_numba"))
        entry_matrix, exit_matrix, timers = self._evaluate_signals_for_chunk(execution_plan=execution_plan, feature_contract=feature_contract, combos=combos)
        results: List[Dict[str, Any]] = []
        for idx, combo in enumerate(combos):
            timer_bars, timer_requires_exit_signal, timer_combine_mode = timers[idx]
            results.append(
                self._tag_result_backend(self._simulate_single_strategy(
                    combo_idx=combo_offset + idx,
                    combo=combo,
                    entry_signal=entry_matrix[:, idx].astype(np.float64),
                    exit_signal=exit_matrix[:, idx].astype(np.float64),
                    timer_bars=timer_bars,
                    timer_requires_exit_signal=timer_requires_exit_signal,
                    timer_combine_mode=timer_combine_mode,
                    trading_params=trading_params,
                    predictor_column=predictor_column,
                    symbol=symbol,
                    backtest_id_prefix=backtest_id_prefix,
                    node_ir=execution_plan.get("node_ir", {}),
                    semantic_predictor_fields=semantic_predictor_fields,
                    audit_index=audit_index if isinstance(audit_index, dict) else None,
                ), resolved_backend)
            )
        return results

    def _run_combo_chunk_rust(
        self,
        *,
        combo_offset: int,
        combos: List[Dict[str, Any]],
        trading_params: Dict[str, Any],
        context: Dict[str, Any],
        kernel: RustSimKernel,
        fallback_backend: _PythonNumbaKernelBackend,
    ) -> List[Dict[str, Any]]:
        execution_plan = context["execution_plan"]
        feature_contract = context["feature_contract"]
        predictor_column = context["predictor_column"]
        symbol = context["symbol"]
        backtest_id_prefix = context["backtest_id_prefix"]
        semantic_predictor_fields = context.get("semantic_predictor_fields", [])
        audit_index = context.get("audit_index")
        entry_matrix, exit_matrix, timers = self._evaluate_signals_for_chunk(
            execution_plan=execution_plan,
            feature_contract=feature_contract,
            combos=combos,
            use_rust_signal=self._rust_signal_kernel.is_available(),
        )
        timer_long_days = np.array([(int(tb) if isinstance(tb, int) and tb > 0 else 0) for tb, _, _ in timers], dtype=np.int32)
        timer_short_days = np.array([(int(tb) if isinstance(tb, int) and tb > 0 else 0) for tb, _, _ in timers], dtype=np.int32)
        timer_has_exit = np.array([1 if bool(req) else 0 for _, req, _ in timers], dtype=np.int32)
        timer_mode = np.array([self._timer_mode_code(mode) for _, _, mode in timers], dtype=np.int32)

        trade_price = str((trading_params or {}).get("trade_price", "close"))
        try:
            positions, returns, actions, equity = kernel.simulate_batch(
                entry_signals=entry_matrix,
                exit_signals=exit_matrix,
                close_prices=self.data["Close"].to_numpy(dtype=np.float64, copy=False),
                open_prices=self.data["Open"].to_numpy(dtype=np.float64, copy=False),
                transaction_cost=float((trading_params or {}).get("transaction_cost", 0.001) or 0.0),
                slippage=float((trading_params or {}).get("slippage", 0.0005) or 0.0),
                trade_price=trade_price,
                trade_delay=int((trading_params or {}).get("trade_delay", 0) or 0),
                holding_period_days=0,
                nday_exit_long_days=timer_long_days,
                nday_exit_short_days=timer_short_days,
                has_non_nday_exit=timer_has_exit,
                nday_combine_mode=timer_mode,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.logger.warning("rust kernel batch failed; fallback to python: %s", exc)
            return fallback_backend.run_batch(
                plan=execution_plan,
                data=self.data,
                param_grid=combos,
                trading_params=trading_params,
                context={**context, "chunk_size": len(combos), "max_workers": 1},
            )

        results: List[Dict[str, Any]] = []
        for idx, combo in enumerate(combos):
            backtest_id = self._generate_backtest_id()
            effective_predictor = self._resolve_effective_predictor(predictor_column, semantic_predictor_fields)
            simulator = TradeSimulator_backtester(
                self.data,
                pd.Series(entry_matrix[:, idx]),
                pd.Series(exit_matrix[:, idx]),
                transaction_cost=float((trading_params or {}).get("transaction_cost", 0.001) or 0.0),
                slippage=float((trading_params or {}).get("slippage", 0.0005) or 0.0),
                trade_delay=int((trading_params or {}).get("trade_delay", 0) or 0),
                trade_price=trade_price,
                Backtest_id=backtest_id,
                parameter_set_id=None,
                predictor=effective_predictor,
                initial_equity=1.0,
                indicators=None,
                trading_instrument=symbol,
            )
            result = simulator.generate_single_result(
                combo_offset + idx,
                entry_matrix[:, idx],
                exit_matrix[:, idx],
                positions[:, idx],
                returns[:, idx],
                actions[:, idx],
                equity[:, idx],
                effective_predictor,
                backtest_id,
                [],
                [],
                trading_params or {},
                semantic_predictor_fields=semantic_predictor_fields,
            )
            timer_bars, timer_requires_exit_signal, timer_combine_mode = timers[idx]
            self._decorate_semantic_result(
                result=result,
                combo=combo,
                backtest_id_prefix=backtest_id_prefix,
                semantic_predictor_fields=semantic_predictor_fields,
                audit_index=audit_index if isinstance(audit_index, dict) else None,
                symbol=symbol,
                signal_kernel_backend="rust" if self._rust_signal_kernel.is_available() else "numpy",
                timer_bars=timer_bars,
                timer_requires_exit_signal=timer_requires_exit_signal,
                timer_combine_mode=timer_combine_mode,
            )
            if isinstance(result, dict) and isinstance(result.get("params"), dict):
                result["params"]["execution_backend"] = str(context.get("_resolved_backend", "rust_kernel"))
            results.append(result)
        return results

    @staticmethod
    def _tag_result_backend(result: Dict[str, Any], backend: str) -> Dict[str, Any]:
        if isinstance(result, dict) and isinstance(result.get("params"), dict):
            result["params"]["execution_backend"] = backend
        return result

    def _evaluate_signals_for_chunk(self, *, execution_plan: Dict[str, Any], feature_contract: Dict[str, Any], combos: List[Dict[str, Any]], use_rust_signal: bool = False) -> Tuple[np.ndarray, np.ndarray, List[Tuple[Optional[int], bool, str]]]:
        try:
            return self._evaluate_signals_for_chunk_fast(
                execution_plan=execution_plan,
                feature_contract=feature_contract,
                combos=combos,
                use_rust_signal=use_rust_signal,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.logger.warning("node_ir fast signal path fallback to legacy evaluator: %s", exc)
            return self._evaluate_signals_for_chunk_legacy(
                execution_plan=execution_plan,
                feature_contract=feature_contract,
                combos=combos,
            )

    def _evaluate_signals_for_chunk_legacy(self, *, execution_plan: Dict[str, Any], feature_contract: Dict[str, Any], combos: List[Dict[str, Any]]) -> Tuple[np.ndarray, np.ndarray, List[Tuple[Optional[int], bool, str]]]:
        n_time, n_combo = len(self.data), len(combos)
        entry_bool = np.zeros((n_time, n_combo), dtype=np.bool_)
        exit_bool = np.zeros((n_time, n_combo), dtype=np.bool_)
        timers: List[Tuple[Optional[int], bool, str]] = []
        for idx, combo in enumerate(combos):
            entry_signal, exit_signal, timer_bars, timer_requires_exit_signal, timer_combine_mode = self._evaluate_signals_for_combo(execution_plan=execution_plan, feature_contract=feature_contract, combo=combo)
            entry_bool[:, idx] = entry_signal > 0.0
            exit_bool[:, idx] = exit_signal < 0.0
            timers.append((timer_bars, timer_requires_exit_signal, timer_combine_mode))
        _ = (np.packbits(entry_bool, axis=0), np.packbits(exit_bool, axis=0))
        return np.where(entry_bool, 1.0, 0.0).astype(np.float64), np.where(exit_bool, -1.0, 0.0).astype(np.float64), timers

    def _evaluate_signals_for_chunk_fast(self, *, execution_plan: Dict[str, Any], feature_contract: Dict[str, Any], combos: List[Dict[str, Any]], use_rust_signal: bool) -> Tuple[np.ndarray, np.ndarray, List[Tuple[Optional[int], bool, str]]]:
        n_time, n_combo = len(self.data), len(combos)
        entry_bool = np.zeros((n_time, n_combo), dtype=np.bool_)
        exit_bool = np.zeros((n_time, n_combo), dtype=np.bool_)
        timers: List[Tuple[Optional[int], bool, str]] = []
        field_catalog = execution_plan.get("field_catalog", {})
        feature_dag = execution_plan.get("feature_dag", {})
        node_ir = execution_plan.get("node_ir", {})

        for idx, combo in enumerate(combos):
            timer_spec, exit_node_without_timer, timer_requires_exit_signal = self._extract_timer_from_exit_node(node_ir.get("exit", {}))
            timer_bars = self._resolve_timer_bars(timer_spec, combo)
            timer_combine_mode = "none"
            if timer_bars is not None:
                timer_combine_mode = "and" if timer_requires_exit_signal else "timer_only"
                if isinstance(exit_node_without_timer, dict):
                    timer_combine_mode = str(exit_node_without_timer.get("__timer_combine_mode__", timer_combine_mode)).lower()
                    if "__timer_combine_mode__" in exit_node_without_timer:
                        exit_node_without_timer = {k: v for k, v in exit_node_without_timer.items() if k != "__timer_combine_mode__"}

            entry_mask = self._eval_node_array(
                node_ir.get("entry", {}),
                combo=combo,
                feature_contract=feature_contract,
                field_catalog=field_catalog,
                feature_dag=feature_dag,
                use_rust_signal=use_rust_signal,
            )
            if timer_bars is None:
                exit_mask = self._eval_node_array(
                    node_ir.get("exit", {}),
                    combo=combo,
                    feature_contract=feature_contract,
                    field_catalog=field_catalog,
                    feature_dag=feature_dag,
                    use_rust_signal=use_rust_signal,
                )
            elif exit_node_without_timer is None:
                exit_mask = np.zeros(n_time, dtype=np.bool_)
            else:
                exit_mask = self._eval_node_array(
                    exit_node_without_timer,
                    combo=combo,
                    feature_contract=feature_contract,
                    field_catalog=field_catalog,
                    feature_dag=feature_dag,
                    use_rust_signal=use_rust_signal,
                )

            entry_bool[:, idx] = entry_mask
            exit_bool[:, idx] = exit_mask
            timers.append((timer_bars, timer_requires_exit_signal, timer_combine_mode))

        _ = (np.packbits(entry_bool, axis=0), np.packbits(exit_bool, axis=0))
        return np.where(entry_bool, 1.0, 0.0).astype(np.float64), np.where(exit_bool, -1.0, 0.0).astype(np.float64), timers

    def _prepare_feature_cache(self, execution_plan: Dict[str, Any], combos: List[Dict[str, Any]], feature_contract: Dict[str, Any]) -> None:
        self._feature_cache = {}
        self._feature_array_cache = {}
        feature_dag = execution_plan.get("feature_dag", {})
        if not isinstance(feature_dag, dict):
            return
        for feature_spec in feature_dag.values():
            if not isinstance(feature_spec, dict) or str(feature_spec.get("feature", "")).lower() != "ta.sma":
                continue
            source = str(feature_spec.get("source", ""))
            params = feature_spec.get("params", {})
            if not isinstance(params, dict):
                continue
            for period in self._resolve_period_set(params.get("period"), combos):
                cache_key = ("ta.sma", source, period)
                if cache_key in self._feature_cache:
                    continue
                source_series = self._resolve_field_series(source, feature_contract=feature_contract, resolver_cache=self._series_cache)
                self._feature_cache[cache_key] = source_series.rolling(period, min_periods=period).mean()

    @staticmethod
    def _resolve_period_set(period_spec: Any, combos: List[Dict[str, Any]]) -> List[int]:
        if isinstance(period_spec, dict) and isinstance(period_spec.get("param_ref"), str):
            key = period_spec["param_ref"]
            vals = sorted({int(c.get(key, 1)) for c in combos if isinstance(c.get(key, 1), (int, float))})
            return [max(1, v) for v in vals]
        if isinstance(period_spec, (int, float)):
            return [max(1, int(period_spec))]
        return [1]

    def _evaluate_signals_for_combo(self, *, execution_plan: Dict[str, Any], feature_contract: Dict[str, Any], combo: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray, Optional[int], bool, str]:
        field_catalog = execution_plan.get("field_catalog", {})
        feature_dag = execution_plan.get("feature_dag", {})
        node_ir = execution_plan.get("node_ir", {})
        timer_spec, exit_node_without_timer, timer_requires_exit_signal = self._extract_timer_from_exit_node(node_ir.get("exit", {}))
        timer_bars = self._resolve_timer_bars(timer_spec, combo)
        timer_combine_mode = "none"
        if timer_bars is not None:
            timer_combine_mode = "and" if timer_requires_exit_signal else "timer_only"
            if isinstance(exit_node_without_timer, dict):
                timer_combine_mode = str(exit_node_without_timer.get("__timer_combine_mode__", timer_combine_mode)).lower()
                if "__timer_combine_mode__" in exit_node_without_timer:
                    exit_node_without_timer = {k: v for k, v in exit_node_without_timer.items() if k != "__timer_combine_mode__"}
        resolver_cache: Dict[str, pd.Series] = {}
        entry_bool = self._eval_node(node_ir.get("entry", {}), combo=combo, feature_contract=feature_contract, field_catalog=field_catalog, feature_dag=feature_dag, resolver_cache=resolver_cache)
        if timer_bars is None:
            exit_bool = self._eval_node(node_ir.get("exit", {}), combo=combo, feature_contract=feature_contract, field_catalog=field_catalog, feature_dag=feature_dag, resolver_cache=resolver_cache)
        else:
            if exit_node_without_timer is None:
                exit_bool = pd.Series(False, index=self.data.index)
            else:
                exit_bool = self._eval_node(exit_node_without_timer, combo=combo, feature_contract=feature_contract, field_catalog=field_catalog, feature_dag=feature_dag, resolver_cache=resolver_cache)
        entry_signal = np.where(entry_bool.fillna(False).to_numpy(), 1.0, 0.0)
        exit_signal = np.where(exit_bool.fillna(False).to_numpy(), -1.0, 0.0)
        return entry_signal.astype(np.float64), exit_signal.astype(np.float64), timer_bars, timer_requires_exit_signal, timer_combine_mode

    def _eval_node(self, node: Any, *, combo: Dict[str, Any], feature_contract: Dict[str, Any], field_catalog: Dict[str, Any], feature_dag: Dict[str, Any], resolver_cache: Dict[str, pd.Series]) -> pd.Series:
        if not isinstance(node, dict):
            return pd.Series(False, index=self.data.index)
        op = str(node.get("op", "")).lower()
        if CalendarConditionMaterializer.is_calendar_op(op):
            return pd.Series(
                self._calendar_materializer.materialize(
                    self._resolve_param_refs_in_obj(node, combo)
                ),
                index=self.data.index,
            )
        if op == "session.same_session_close":
            return pd.Series(False, index=self.data.index)
        if op == "and":
            children = node.get("nodes", [])
            if not isinstance(children, list) or not children:
                return pd.Series(False, index=self.data.index)
            out = pd.Series(True, index=self.data.index)
            for child in children:
                out = out & self._eval_node(child, combo=combo, feature_contract=feature_contract, field_catalog=field_catalog, feature_dag=feature_dag, resolver_cache=resolver_cache)
            return out
        if op == "or":
            children = node.get("nodes", [])
            if not isinstance(children, list) or not children:
                return pd.Series(False, index=self.data.index)
            out = pd.Series(False, index=self.data.index)
            for child in children:
                out = out | self._eval_node(child, combo=combo, feature_contract=feature_contract, field_catalog=field_catalog, feature_dag=feature_dag, resolver_cache=resolver_cache)
            return out
        if op == "not":
            return ~self._eval_node(node.get("node"), combo=combo, feature_contract=feature_contract, field_catalog=field_catalog, feature_dag=feature_dag, resolver_cache=resolver_cache)
        left = self._as_series(self._eval_value(node.get("left"), combo=combo, feature_contract=feature_contract, field_catalog=field_catalog, feature_dag=feature_dag, resolver_cache=resolver_cache))
        right = self._as_series(self._eval_value(node.get("right"), combo=combo, feature_contract=feature_contract, field_catalog=field_catalog, feature_dag=feature_dag, resolver_cache=resolver_cache))
        if op == "gt":
            return left > right
        if op == "lt":
            return left < right
        if op == "ge":
            return left >= right
        if op == "le":
            return left <= right
        if op == "eq":
            return left == right
        if op == "ne":
            return left != right
        if op == "cross_up":
            return (left > right) & (left.shift(1) <= right.shift(1))
        if op == "cross_down":
            return (left < right) & (left.shift(1) >= right.shift(1))
        return pd.Series(False, index=self.data.index)

    def _eval_node_array(self, node: Any, *, combo: Dict[str, Any], feature_contract: Dict[str, Any], field_catalog: Dict[str, Any], feature_dag: Dict[str, Any], use_rust_signal: bool) -> np.ndarray:
        n_time = len(self.data)
        if not isinstance(node, dict):
            return np.zeros(n_time, dtype=np.bool_)
        op = str(node.get("op", "")).lower()
        if CalendarConditionMaterializer.is_calendar_op(op):
            return self._calendar_materializer.materialize(
                self._resolve_param_refs_in_obj(node, combo)
            )
        if op == "session.same_session_close":
            return np.zeros(n_time, dtype=np.bool_)
        if op == "and":
            children = node.get("nodes", [])
            if not isinstance(children, list) or not children:
                return np.zeros(n_time, dtype=np.bool_)
            out = np.ones(n_time, dtype=np.bool_)
            for child in children:
                out &= self._eval_node_array(child, combo=combo, feature_contract=feature_contract, field_catalog=field_catalog, feature_dag=feature_dag, use_rust_signal=use_rust_signal)
            return out
        if op == "or":
            children = node.get("nodes", [])
            if not isinstance(children, list) or not children:
                return np.zeros(n_time, dtype=np.bool_)
            out = np.zeros(n_time, dtype=np.bool_)
            for child in children:
                out |= self._eval_node_array(child, combo=combo, feature_contract=feature_contract, field_catalog=field_catalog, feature_dag=feature_dag, use_rust_signal=use_rust_signal)
            return out
        if op == "not":
            return ~self._eval_node_array(node.get("node"), combo=combo, feature_contract=feature_contract, field_catalog=field_catalog, feature_dag=feature_dag, use_rust_signal=use_rust_signal)

        left = self._as_array(self._eval_value_array(node.get("left"), combo=combo, feature_contract=feature_contract, field_catalog=field_catalog, feature_dag=feature_dag))
        right = self._as_array(self._eval_value_array(node.get("right"), combo=combo, feature_contract=feature_contract, field_catalog=field_catalog, feature_dag=feature_dag))
        rust_code = self._signal_op_code(op)
        if use_rust_signal and rust_code > 0 and self._rust_signal_kernel.is_available():
            try:
                return self._rust_signal_kernel.binary_mask(op_code=rust_code, left=left, right=right)
            except Exception:  # pragma: no cover - fallback to numpy path
                pass
        with np.errstate(invalid="ignore"):
            if op == "gt":
                out = left > right
            elif op == "lt":
                out = left < right
            elif op == "ge":
                out = left >= right
            elif op == "le":
                out = left <= right
            elif op == "eq":
                out = left == right
            elif op == "ne":
                out = left != right
            elif op == "cross_up":
                out = (left > right) & (np.roll(left, 1) <= np.roll(right, 1))
                out[0] = False
            elif op == "cross_down":
                out = (left < right) & (np.roll(left, 1) >= np.roll(right, 1))
                out[0] = False
            else:
                out = np.zeros(n_time, dtype=np.bool_)
        return np.nan_to_num(out.astype(np.bool_), nan=False)

    def _eval_value(self, value: Any, *, combo: Dict[str, Any], feature_contract: Dict[str, Any], field_catalog: Dict[str, Any], feature_dag: Dict[str, Any], resolver_cache: Dict[str, pd.Series]) -> Any:
        if isinstance(value, (int, float, bool)):
            return value
        if isinstance(value, str):
            return value
        if not isinstance(value, dict):
            return np.nan
        if isinstance(value.get("param_ref"), str):
            return combo.get(value["param_ref"], np.nan)
        if isinstance(value.get("field"), str):
            return self._resolve_field_series(value["field"], feature_contract=feature_contract, resolver_cache=resolver_cache)
        if isinstance(value.get("field_ref"), dict):
            field_name = value["field_ref"].get("field")
            if isinstance(field_name, str):
                return self._resolve_field_series(field_name, feature_contract=feature_contract, resolver_cache=resolver_cache)
        if isinstance(value.get("feature"), str):
            return self._compute_feature_series(value, combo=combo, feature_contract=feature_contract, resolver_cache=resolver_cache)
        if isinstance(value.get("feature_ref"), dict):
            feature_key = value["feature_ref"].get("feature_key")
            feature_spec = feature_dag.get(feature_key, {})
            if isinstance(feature_spec, dict):
                return self._compute_feature_series(feature_spec, combo=combo, feature_contract=feature_contract, resolver_cache=resolver_cache)
        if isinstance(value.get("fid"), str):
            item = field_catalog.get(value["fid"], {})
            field_name = item.get("field")
            if isinstance(field_name, str):
                return self._resolve_field_series(field_name, feature_contract=feature_contract, resolver_cache=resolver_cache)
        return np.nan

    def _eval_value_array(self, value: Any, *, combo: Dict[str, Any], feature_contract: Dict[str, Any], field_catalog: Dict[str, Any], feature_dag: Dict[str, Any]) -> Any:
        if isinstance(value, (int, float, bool)):
            return float(value)
        if not isinstance(value, dict):
            return np.nan
        if isinstance(value.get("param_ref"), str):
            ref = value["param_ref"]
            raw = combo.get(ref, np.nan)
            return float(raw) if isinstance(raw, (int, float, bool)) else np.nan
        if isinstance(value.get("field"), str):
            return self._resolve_field_array(value["field"], feature_contract=feature_contract)
        if isinstance(value.get("field_ref"), dict):
            field_name = value["field_ref"].get("field")
            if isinstance(field_name, str):
                return self._resolve_field_array(field_name, feature_contract=feature_contract)
        if isinstance(value.get("feature"), str):
            return self._compute_feature_array(value, combo=combo, feature_contract=feature_contract)
        if isinstance(value.get("feature_ref"), dict):
            feature_key = value["feature_ref"].get("feature_key")
            feature_spec = feature_dag.get(feature_key, {})
            if isinstance(feature_spec, dict):
                return self._compute_feature_array(feature_spec, combo=combo, feature_contract=feature_contract)
        if isinstance(value.get("fid"), str):
            item = field_catalog.get(value["fid"], {})
            field_name = item.get("field")
            if isinstance(field_name, str):
                return self._resolve_field_array(field_name, feature_contract=feature_contract)
        return np.nan

    def _compute_feature_series(self, feature_spec: Dict[str, Any], *, combo: Dict[str, Any], feature_contract: Dict[str, Any], resolver_cache: Dict[str, pd.Series]) -> pd.Series:
        feature = str(feature_spec.get("feature", "")).lower()
        source = str(feature_spec.get("source", ""))
        params = feature_spec.get("params", {}) if isinstance(feature_spec.get("params"), dict) else {}
        source_series = self._resolve_field_series(source, feature_contract=feature_contract, resolver_cache=resolver_cache)
        if feature == "ta.sma":
            period = self._resolve_period(params.get("period"), combo)
            period = max(1, period)
            pre_col = self._precomputed_feature_column_name(feature=feature, source=source, period=period)
            if pre_col in self.data.columns:
                return pd.to_numeric(self.data[pre_col], errors="coerce")
            cache_key = ("ta.sma", source, period)
            with self._cache_lock:
                if cache_key in self._feature_cache:
                    return self._feature_cache[cache_key]
            out = source_series.rolling(period, min_periods=period).mean()
            with self._cache_lock:
                self._feature_cache[cache_key] = out
            return out
        raise ValueError(f"Unsupported feature operation: {feature_spec.get('feature')}")

    def _compute_feature_array(self, feature_spec: Dict[str, Any], *, combo: Dict[str, Any], feature_contract: Dict[str, Any]) -> np.ndarray:
        feature = str(feature_spec.get("feature", "")).lower()
        source = str(feature_spec.get("source", ""))
        params = feature_spec.get("params", {}) if isinstance(feature_spec.get("params"), dict) else {}
        if feature == "ta.sma":
            period = max(1, self._resolve_period(params.get("period"), combo))
            pre_col = self._precomputed_feature_column_name(feature=feature, source=source, period=period)
            if pre_col in self.data.columns:
                return np.ascontiguousarray(
                    pd.to_numeric(self.data[pre_col], errors="coerce").to_numpy(dtype=np.float64, copy=False),
                    dtype=np.float64,
                )
            cache_key = ("ta.sma", source, period)
            with self._cache_lock:
                if cache_key in self._feature_array_cache:
                    return self._feature_array_cache[cache_key]
                cached_series = self._feature_cache.get(cache_key)
            if cached_series is not None:
                arr = cached_series.to_numpy(dtype=np.float64, copy=False)
            else:
                source_series = self._resolve_field_series(source, feature_contract=feature_contract, resolver_cache=self._series_cache)
                arr = source_series.rolling(period, min_periods=period).mean().to_numpy(dtype=np.float64, copy=False)
                with self._cache_lock:
                    self._feature_cache[cache_key] = pd.Series(arr, index=self.data.index)
            contiguous = np.ascontiguousarray(arr, dtype=np.float64)
            with self._cache_lock:
                self._feature_array_cache[cache_key] = contiguous
                return self._feature_array_cache[cache_key]
        raise ValueError(f"Unsupported feature operation: {feature_spec.get('feature')}")

    @staticmethod
    def _resolve_period(period_spec: Any, combo: Dict[str, Any]) -> int:
        if isinstance(period_spec, dict) and isinstance(period_spec.get("param_ref"), str):
            return int(combo.get(period_spec["param_ref"], 1))
        return int(period_spec or 1)

    def _resolve_field_series(self, field_name: str, *, feature_contract: Dict[str, Any], resolver_cache: Dict[str, pd.Series]) -> pd.Series:
        with self._cache_lock:
            if field_name in resolver_cache:
                return resolver_cache[field_name]
            if field_name in self._series_cache:
                resolver_cache[field_name] = self._series_cache[field_name]
                return resolver_cache[field_name]
        feature_spec = self._feature_contract_spec(feature_contract, field_name)
        if field_name in self._materialized_fields and field_name in self.data.columns:
            s = pd.to_numeric(self.data[field_name], errors="coerce")
        elif isinstance(feature_spec, dict):
            source = feature_spec.get("source", {})
            feature_column = source.get("column") if isinstance(source, dict) else None
            if isinstance(feature_column, str) and feature_column in self.data.columns:
                s = pd.to_numeric(self.data[feature_column], errors="coerce")
                s = self._apply_feature_contract_transform(s, feature_spec=feature_spec)
            else:
                s = None
        else:
            s = None
        if s is None:
            feature_column = self._feature_contract_column(feature_contract, field_name)
            if feature_column and feature_column in self.data.columns:
                s = pd.to_numeric(self.data[feature_column], errors="coerce")
                s = self._apply_feature_contract_transform(s, feature_spec=feature_spec)
        if s is None:
            alias = {"price.open": "Open", "price.high": "High", "price.low": "Low", "price.close": "Close", "price.volume": "Volume"}.get(field_name.lower())
            if alias and alias in self.data.columns:
                s = pd.to_numeric(self.data[alias], errors="coerce")
                s = self._apply_feature_contract_transform(s, feature_spec=feature_spec)
        if s is None:
            lower_map = {str(col).lower(): col for col in self.data.columns}
            if field_name.lower() in lower_map:
                col = lower_map[field_name.lower()]
                s = pd.to_numeric(self.data[col], errors="coerce")
                s = self._apply_feature_contract_transform(s, feature_spec=feature_spec)
        if s is not None:
            with self._cache_lock:
                resolver_cache[field_name] = s
                self._series_cache[field_name] = s
            return s
        raise KeyError(f"Cannot resolve field '{field_name}' to any DataFrame column")

    def _resolve_field_array(self, field_name: str, *, feature_contract: Dict[str, Any]) -> np.ndarray:
        with self._cache_lock:
            if field_name in self._array_cache:
                return self._array_cache[field_name]
        series = self._resolve_field_series(field_name, feature_contract=feature_contract, resolver_cache=self._series_cache)
        arr = np.ascontiguousarray(series.to_numpy(dtype=np.float64, copy=False), dtype=np.float64)
        with self._cache_lock:
            self._array_cache[field_name] = arr
            return self._array_cache[field_name]

    @staticmethod
    def _feature_contract_column(feature_contract: Dict[str, Any], field_name: str) -> Optional[str]:
        features = feature_contract.get("features", [])
        if not isinstance(features, list):
            return None
        for item in features:
            if not isinstance(item, dict) or item.get("field") != field_name:
                continue
            source = item.get("source", {})
            if isinstance(source, dict) and isinstance(source.get("column"), str):
                return source["column"]
        return None

    @staticmethod
    def _feature_contract_spec(feature_contract: Dict[str, Any], field_name: str) -> Optional[Dict[str, Any]]:
        features = feature_contract.get("features", [])
        if not isinstance(features, list):
            return None
        for item in features:
            if not isinstance(item, dict) or item.get("field") != field_name:
                continue
            return item
        return None

    @staticmethod
    def _apply_feature_contract_transform(
        series: pd.Series,
        *,
        feature_spec: Optional[Dict[str, Any]],
    ) -> pd.Series:
        if not isinstance(series, pd.Series) or not isinstance(feature_spec, dict):
            return series
        out = series
        fill_policy = str(feature_spec.get("fill_policy", "none") or "none").strip().lower()
        if fill_policy == "ffill":
            out = out.ffill()
        elif fill_policy == "bfill":
            field_name = str(feature_spec.get("field", "") or "")
            if not field_name.startswith("label."):
                raise ValueError(
                    f"feature field '{field_name}' cannot use bfill because it can leak future values"
                )
            out = out.bfill()
        elif fill_policy == "zero":
            out = out.fillna(0.0)

        lag_bars = feature_spec.get("lag_bars")
        if isinstance(lag_bars, int) and lag_bars > 0:
            out = out.shift(lag_bars)
        return out

    def _as_series(self, value: Any) -> pd.Series:
        if isinstance(value, pd.Series):
            return value.reindex(self.data.index)
        if isinstance(value, (int, float, bool)):
            return pd.Series(value, index=self.data.index)
        return pd.Series(np.nan, index=self.data.index)

    def _as_array(self, value: Any) -> np.ndarray:
        n_time = len(self.data)
        if isinstance(value, np.ndarray):
            if value.shape[0] == n_time:
                return value
            return np.full(n_time, np.nan, dtype=np.float64)
        if isinstance(value, pd.Series):
            return np.ascontiguousarray(value.to_numpy(dtype=np.float64, copy=False), dtype=np.float64)
        if isinstance(value, (int, float, bool)):
            return np.full(n_time, float(value), dtype=np.float64)
        return np.full(n_time, np.nan, dtype=np.float64)

    def _simulate_single_strategy(self, *, combo_idx: int, combo: Dict[str, Any], entry_signal: np.ndarray, exit_signal: np.ndarray, timer_bars: Optional[int], timer_requires_exit_signal: bool, timer_combine_mode: str, trading_params: Dict[str, Any], predictor_column: str, symbol: str, backtest_id_prefix: str, node_ir: Optional[Dict[str, Any]] = None, semantic_predictor_fields: Optional[List[str]] = None, audit_index: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        effective = dict(trading_params)
        if isinstance(timer_bars, int) and timer_bars > 0:
            effective["nday_rules"] = {
                "exit_long_days": timer_bars,
                "exit_short_days": timer_bars,
                "has_non_nday_exit": bool(timer_requires_exit_signal),
                "combine_mode": timer_combine_mode if timer_combine_mode in {"and", "or", "timer_only"} else "timer_only",
            }
        backtest_id = self._generate_backtest_id()
        effective_predictor = self._resolve_effective_predictor(predictor_column, semantic_predictor_fields)
        simulator = TradeSimulator_backtester(
            self.data,
            pd.Series(entry_signal),
            pd.Series(exit_signal),
            transaction_cost=float(effective.get("transaction_cost", 0.001) or 0.0),
            slippage=float(effective.get("slippage", 0.0005) or 0.0),
            trade_delay=int(effective.get("trade_delay", 0) or 0),
            trade_price=str(effective.get("trade_price", "close") or "close"),
            Backtest_id=backtest_id,
            parameter_set_id=None,
            predictor=effective_predictor,
            initial_equity=1.0,
            indicators=None,
            trading_instrument=symbol,
        )
        strategy_execution = effective.get("strategy_execution", {})
        if self._execution_is_same_session(strategy_execution):
            result = simulator.generate_same_session_result(
                combo_idx,
                entry_signal=entry_signal,
                side=str(strategy_execution.get("side", "long")),
                entry_price=str(strategy_execution.get("entry_price", "open")),
                exit_price=str(strategy_execution.get("exit_price", "close")),
                predictor=effective_predictor,
                backtest_id=backtest_id,
                trading_params=effective,
                semantic_predictor_fields=semantic_predictor_fields,
                combo=combo,
            )
        else:
            sim = simulator.simulate_trades_sequential(entry_signal=entry_signal, exit_signal=exit_signal, trading_params=effective)
            result = simulator.generate_single_result(
                combo_idx,
                entry_signal,
                exit_signal,
                sim["positions"],
                sim["returns"],
                sim["trade_actions"],
                sim["equity_values"],
                effective_predictor,
                backtest_id,
                [],
                [],
                effective,
                semantic_predictor_fields=semantic_predictor_fields,
            )
        calendar_audit_rows = self._calendar_materializer.audit_rows(
            [
                self._resolve_param_refs_in_obj((node_ir or {}).get("entry", {}), combo),
                self._resolve_param_refs_in_obj((node_ir or {}).get("exit", {}), combo),
            ],
            combo=combo,
            backtest_id=backtest_id,
            strategy_id=str(result.get("strategy_id", "")),
        )
        if calendar_audit_rows:
            calendar_audit = pd.DataFrame(calendar_audit_rows)
            execution_audit = result.get("calendar_execution_audit")
            if (
                isinstance(execution_audit, pd.DataFrame)
                and not execution_audit.empty
                and "resolved_session_date" in calendar_audit.columns
            ):
                calendar_audit = calendar_audit.merge(
                    execution_audit,
                    on="resolved_session_date",
                    how="left",
                )
            result["calendar_signal_audit"] = calendar_audit
        self._decorate_semantic_result(
            result=result,
            combo=combo,
            backtest_id_prefix=backtest_id_prefix,
            semantic_predictor_fields=semantic_predictor_fields or [],
            audit_index=audit_index,
            symbol=symbol,
            signal_kernel_backend="numpy",
            timer_bars=timer_bars,
            timer_requires_exit_signal=timer_requires_exit_signal,
            timer_combine_mode=timer_combine_mode,
        )
        return result

    @staticmethod
    def _execution_is_same_session(strategy_execution: Any) -> bool:
        if not isinstance(strategy_execution, dict):
            return False
        return (
            str(strategy_execution.get("session_scope") or "").strip().lower()
            == "same_session"
            or bool(strategy_execution.get("same_session_exit", False))
        )

    @classmethod
    def _plan_requires_session_adapter(cls, execution_plan: Any) -> bool:
        if not isinstance(execution_plan, dict):
            return False
        execution = execution_plan.get("execution", {})
        if cls._execution_is_same_session(execution):
            return True
        node_ir = execution_plan.get("node_ir", {})
        exit_node = node_ir.get("exit") if isinstance(node_ir, dict) else None
        return cls._node_has_op(exit_node, "session.same_session_close")

    @classmethod
    def _node_has_op(cls, node: Any, op_name: str) -> bool:
        if isinstance(node, dict):
            if str(node.get("op", "")).strip().lower() == op_name:
                return True
            return any(cls._node_has_op(value, op_name) for value in node.values())
        if isinstance(node, list):
            return any(cls._node_has_op(item, op_name) for item in node)
        return False

    @classmethod
    def _resolve_param_refs_in_obj(cls, obj: Any, combo: Dict[str, Any]) -> Any:
        if isinstance(obj, dict):
            param_ref = obj.get("param_ref")
            if isinstance(param_ref, str):
                return combo.get(param_ref)
            return {key: cls._resolve_param_refs_in_obj(value, combo) for key, value in obj.items()}
        if isinstance(obj, list):
            return [cls._resolve_param_refs_in_obj(item, combo) for item in obj]
        return obj

    @staticmethod
    def _generate_backtest_id() -> str:
        return uuid.uuid4().hex[:12]

    @staticmethod
    def _resolve_effective_predictor(predictor_column: str, semantic_predictor_fields: Optional[List[str]]) -> str:
        if isinstance(semantic_predictor_fields, list) and semantic_predictor_fields:
            return str(semantic_predictor_fields[0])
        return predictor_column

    def _decorate_semantic_result(
        self,
        *,
        result: Dict[str, Any],
        combo: Dict[str, Any],
        backtest_id_prefix: str,
        semantic_predictor_fields: List[str],
        audit_index: Optional[Dict[str, Any]],
        symbol: str,
        signal_kernel_backend: str,
        timer_bars: Optional[int],
        timer_requires_exit_signal: bool,
        timer_combine_mode: str,
    ) -> None:
        if not isinstance(result, dict) or not isinstance(result.get("params"), dict):
            return
        params = result["params"]
        params["strategy_mode"] = "semantic"
        params["semantic_combo"] = dict(combo)
        params["semantic_fields"] = list(semantic_predictor_fields)
        params["semantic_run_label"] = backtest_id_prefix
        params["symbol"] = symbol
        params["signal_kernel_backend"] = signal_kernel_backend
        if isinstance(audit_index, dict):
            for key, value in audit_index.items():
                if value not in (None, "", []):
                    params[key] = value
        if isinstance(timer_bars, int) and timer_bars > 0:
            params["timer_bars"] = timer_bars
            params["timer_requires_exit_signal"] = bool(timer_requires_exit_signal)
            params["timer_combine_mode"] = timer_combine_mode

    @staticmethod
    def _build_semantic_audit_index(
        *,
        execution_plan: Dict[str, Any],
        strategy_contract_path: str,
        feature_contract: Dict[str, Any],
        feature_contract_path: Optional[str],
        semantic_predictor_fields: List[str],
    ) -> Dict[str, Any]:
        feature_contract_hash = ""
        if isinstance(feature_contract, dict) and feature_contract:
            dumped = json.dumps(feature_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            feature_contract_hash = hashlib.sha256(dumped.encode("utf-8")).hexdigest()
        execution_plan_hash = ""
        if isinstance(execution_plan, dict):
            raw_hash = execution_plan.get("plan_hash")
            if isinstance(raw_hash, str) and raw_hash.strip():
                execution_plan_hash = raw_hash.strip()
            else:
                dumped_plan = json.dumps(execution_plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                execution_plan_hash = hashlib.sha256(dumped_plan.encode("utf-8")).hexdigest()
        audit_seed = json.dumps(
            {
                "execution_plan_hash": execution_plan_hash,
                "feature_contract_path": feature_contract_path or "",
                "feature_contract_hash": feature_contract_hash,
                "semantic_fields": semantic_predictor_fields,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        source_audit_id = hashlib.sha256(audit_seed.encode("utf-8")).hexdigest()[:12]
        return {
            "strategy_contract_path": strategy_contract_path,
            "feature_contract_path": feature_contract_path or "",
            "feature_contract_hash": feature_contract_hash,
            "execution_plan_hash": execution_plan_hash,
            "source_audit_id": source_audit_id,
        }

    def _collect_semantic_export_fields(self, execution_plan: Dict[str, Any]) -> List[str]:
        ordered_fields: List[str] = []
        feature_dag = execution_plan.get("feature_dag", {}) if isinstance(execution_plan, dict) else {}
        visited_features: set[str] = set()

        def add_field(field: str) -> None:
            normalized = str(field).strip()
            if normalized and normalized in self.data.columns:
                ordered_fields.append(normalized)

        def resolve_feature_inputs(feature_key: str) -> None:
            normalized_key = str(feature_key).strip()
            if not normalized_key or normalized_key in visited_features:
                return
            visited_features.add(normalized_key)
            feature_spec = feature_dag.get(normalized_key)
            if not isinstance(feature_spec, dict):
                return
            source = feature_spec.get("source")
            if isinstance(source, str):
                add_field(source)
            elif isinstance(source, dict):
                source_field = source.get("field")
                if isinstance(source_field, str):
                    add_field(source_field)
                source_feature = source.get("feature_key")
                if isinstance(source_feature, str):
                    resolve_feature_inputs(source_feature)

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                field_ref = node.get("field_ref")
                if isinstance(field_ref, dict):
                    field = str(field_ref.get("field", "")).strip()
                    add_field(field)
                feature_ref = node.get("feature_ref")
                if isinstance(feature_ref, dict):
                    feature_key = str(feature_ref.get("feature_key", "")).strip()
                    if feature_key:
                        resolve_feature_inputs(feature_key)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        if isinstance(execution_plan, dict):
            walk(execution_plan.get("node_ir", {}))
        deduped: List[str] = []
        seen: set[str] = set()
        for field in ordered_fields:
            if field not in seen:
                seen.add(field)
                deduped.append(field)
        return deduped

    @staticmethod
    def _extract_timer_from_exit_node(exit_node: Any) -> Tuple[Optional[Any], Optional[Dict[str, Any]], bool]:
        if not isinstance(exit_node, dict):
            return None, None, False
        op = str(exit_node.get("op", "")).lower()
        if op in {"timer_bars", "time_stop_bars"}:
            value = exit_node.get("value")
            if isinstance(value, int) and value > 0:
                return value, None, False
            if isinstance(value, dict) and isinstance(value.get("param_ref"), str):
                return value, None, False
            return None, None, False
        if op in {"and", "or"}:
            nodes = exit_node.get("nodes", [])
            if not isinstance(nodes, list) or not nodes:
                return None, exit_node, False
            timer_specs: List[Any] = []
            non_timer_nodes: List[Dict[str, Any]] = []
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                child_op = str(node.get("op", "")).lower()
                if child_op in {"timer_bars", "time_stop_bars"}:
                    value = node.get("value")
                    if isinstance(value, int) and value > 0:
                        timer_specs.append(value)
                    elif isinstance(value, dict) and isinstance(value.get("param_ref"), str):
                        timer_specs.append(value)
                else:
                    non_timer_nodes.append(node)
            if not timer_specs:
                return None, exit_node, False
            int_values = [v for v in timer_specs if isinstance(v, int) and v > 0]
            ref_values = [v for v in timer_specs if isinstance(v, dict) and isinstance(v.get("param_ref"), str)]
            if int_values and not ref_values:
                timer_spec: Any = max(int_values) if op == "and" else min(int_values)
            elif ref_values and not int_values:
                refs = sorted({str(v.get("param_ref")).strip() for v in ref_values if str(v.get("param_ref")).strip()})
                timer_spec = {"param_ref": refs[0]} if len(refs) == 1 else {"param_refs": refs, "op": op}
            else:
                timer_spec = {"int_values": int_values, "param_refs": sorted({str(v.get("param_ref")).strip() for v in ref_values if str(v.get("param_ref")).strip()}), "op": op}
            if len(non_timer_nodes) == 0:
                return timer_spec, None, False
            tag = "and" if op == "and" else "or"
            if len(non_timer_nodes) == 1:
                node = dict(non_timer_nodes[0])
                node["__timer_combine_mode__"] = tag
                return timer_spec, node, op == "and"
            return timer_spec, {"op": op, "nodes": non_timer_nodes, "__timer_combine_mode__": tag}, op == "and"
        return None, exit_node, False

    @staticmethod
    def _resolve_timer_bars(timer_spec: Any, combo: Dict[str, Any]) -> Optional[int]:
        if isinstance(timer_spec, int) and timer_spec > 0:
            return timer_spec
        if isinstance(timer_spec, dict):
            if isinstance(timer_spec.get("param_ref"), str):
                raw = combo.get(str(timer_spec.get("param_ref")))
                if isinstance(raw, (int, float)) and int(raw) > 0:
                    return int(raw)
                return None
            refs = timer_spec.get("param_refs", [])
            op = str(timer_spec.get("op", "")).lower()
            ints = []
            if isinstance(timer_spec.get("int_values"), list):
                ints.extend([int(v) for v in timer_spec.get("int_values") if isinstance(v, int) and int(v) > 0])
            if isinstance(refs, list):
                for ref in refs:
                    raw = combo.get(str(ref))
                    if isinstance(raw, (int, float)) and int(raw) > 0:
                        ints.append(int(raw))
            if not ints:
                return None
            return max(ints) if op == "and" else min(ints)
        return None

    @staticmethod
    def _timer_mode_code(mode: str) -> int:
        mode_l = str(mode or "").lower()
        if mode_l == "timer_only":
            return 1
        if mode_l == "and":
            return 2
        if mode_l == "or":
            return 3
        return 0

    @staticmethod
    def _signal_op_code(op: str) -> int:
        op_l = str(op or "").lower()
        return {
            "gt": 1,
            "lt": 2,
            "ge": 3,
            "le": 4,
            "eq": 5,
            "ne": 6,
            "cross_up": 7,
            "cross_down": 8,
        }.get(op_l, 0)

    @staticmethod
    def _precomputed_feature_column_name(*, feature: str, source: str, period: int) -> str:
        safe_source = str(source).replace(".", "_").replace(" ", "_")
        return f"__pre_{feature.replace('.', '_')}_{safe_source}_{int(period)}"

    def _normalize_price_columns(self) -> None:
        required = ("Open", "High", "Low", "Close", "Volume")
        rename_map: Dict[str, str] = {}
        for col in self.data.columns:
            for req in required:
                if str(col).lower() == req.lower():
                    rename_map[col] = req
        if rename_map:
            self.data = self.data.rename(columns=rename_map)

    @staticmethod
    def _load_json(path: str) -> Dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))

    @staticmethod
    def _expand_parameter_domains(parameter_domains: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(parameter_domains, dict) or not parameter_domains:
            return [{}]
        names: List[str] = []
        value_lists: List[List[Any]] = []
        for name, spec in parameter_domains.items():
            names.append(name)
            value_lists.append(NodeIRExecutorBacktester._expand_domain(spec))
        return [dict(zip(names, values)) for values in itertools.product(*value_lists)]

    @staticmethod
    def _expand_domain(spec: Any) -> List[Any]:
        if not isinstance(spec, dict):
            return [spec]
        domain_type = str(spec.get("type", "fixed")).lower()
        if domain_type == "fixed":
            return [spec.get("value")]
        if domain_type == "set":
            values = spec.get("values", [])
            return list(values) if isinstance(values, list) and values else [None]
        if domain_type == "range":
            start, end, step = spec.get("start"), spec.get("end"), spec.get("step")
            if not all(isinstance(v, (int, float)) for v in (start, end, step)) or step == 0:
                return [None]
            values: List[Any] = []
            current = float(start)
            while current <= float(end) + 1e-12:
                values.append(int(round(current)) if float(current).is_integer() else round(current, 10))
                current += float(step)
            return values or [None]
        return [spec.get("value")]
