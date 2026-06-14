"""
ParameterOptimizer_wfanalyser.py

【功能說明】
------------------------------------------------------------
本模組負責 WFA 的參數優化功能，在訓練集上尋找最優參數，
優化目標可以是 Sharpe 或 Calmar。

【流程與數據流】
------------------------------------------------------------
- 主流程：生成參數組合 → 執行回測 → 計算績效 → 選擇最優參數
- 數據流：訓練數據 → 參數組合 → 回測結果 → 績效指標 → 最優參數

【維護與擴充重點】
------------------------------------------------------------
- 參數組合生成需要與 NodeIR/native runtime 兼容
- 績效計算需要與 metricstracker 兼容
- 優化邏輯需要高效且準確

【常見易錯點】
------------------------------------------------------------
- 參數組合生成錯誤導致優化失敗
- 績效計算錯誤導致選擇錯誤的最優參數
- 記憶體使用過大導致優化失敗

【範例】
------------------------------------------------------------
- 優化參數：optimizer = ParameterOptimizer(train_data, frequency, config); optimal = optimizer.optimize("sharpe")

【與其他模組的關聯】
------------------------------------------------------------
- 調用 NodeIR/native runtime 執行回測
- 調用 MetricsCalculator 計算績效指標
- 依賴配置數據生成參數組合

【版本與變更記錄】
------------------------------------------------------------
- v1.0: 初始版本，基本優化功能

【參考】
------------------------------------------------------------
- WalkForwardEngine_wfanalyser.py: WFA 核心引擎
- NodeIRExecutor_backtester.py: 向量化回測引擎
- MetricsCalculator_metricstracker.py: 績效指標計算器
- wfanalyser/README.md: WFA 模組詳細說明
"""

import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import optuna

from .OptunaSearchEngine_wfanalyser import OptunaSearchEngine, SearchSpaceField


def _removed_public_wfa_engine(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("Public legacy WFA runtime path has been removed; use strategy_mode=semantic.")


@dataclass
class OptimizationDiagnostics:
    """
    通用的優化診斷類，自動分析所有回測結果的失敗原因

    適用於所有技術指標類型，無需為每個指標單獨編寫診斷代碼
    """
    total_results: int = 0
    error_results: List[Dict[str, Any]] = field(default_factory=list)
    no_records_results: List[Dict[str, Any]] = field(default_factory=list)
    empty_records_results: List[Dict[str, Any]] = field(default_factory=list)
    no_trade_action_results: List[Dict[str, Any]] = field(default_factory=list)
    no_trade_results: List[Dict[str, Any]] = field(default_factory=list)
    invalid_metric_results: List[Dict[str, Any]] = field(default_factory=list)
    valid_results: List[Dict[str, Any]] = field(default_factory=list)

    def analyze_results(
        self,
        results: List[Dict[str, Any]],
        objective: str,
        strategy_idx: Optional[int] = None,
        logger: Optional[logging.Logger] = None
    ) -> Dict[str, Any]:
        """
        通用分析所有回測結果，自動分類失敗原因

        Args:
            results: 回測結果列表
            objective: 優化目標（"sharpe" 或 "calmar"）
            strategy_idx: 可選的策略索引
            logger: 日誌記錄器

        Returns:
            Dict[str, Any]: 診斷結果摘要
        """
        from metricstracker.MetricsCalculator_metricstracker import MetricsCalculatorMetricTracker

        # NOTE: translated to English.
        self.total_results = len(results)
        self.error_results = []
        self.no_records_results = []
        self.empty_records_results = []
        self.no_trade_action_results = []
        self.no_trade_results = []
        self.invalid_metric_results = []
        self.valid_results = []

        log = logger or logging.getLogger("lo2cin4bt.wfanalyser.optimizer.diagnostics")

        for result in results:
            # NOTE: translated to English.
            if strategy_idx is not None:
                if not self._matches_strategy_idx(result, strategy_idx):
                    continue

            # NOTE: translated to English.
            if result.get("error") is not None:
                self.error_results.append(result)
                continue

            # NOTE: translated to English.
            if "records" not in result:
                self.no_records_results.append(result)
                continue

            records = result["records"]

            # NOTE: translated to English.
            if not isinstance(records, pd.DataFrame) or records.empty:
                self.empty_records_results.append(result)
                continue

            # NOTE: translated to English.
            if "Trade_action" not in records.columns:
                self.no_trade_action_results.append(result)
                continue

            # NOTE: translated to English.
            trade_count = (records["Trade_action"] == 1).sum()
            if trade_count == 0:
                self.no_trade_results.append(result)
                continue

            # NOTE: translated to English.
            try:
                metrics_calc = MetricsCalculatorMetricTracker(
                    records,
                    time_unit=365,
                    risk_free_rate=0.04,
                )

                if objective == "sharpe":
                    metric_value = metrics_calc.sharpe()
                elif objective == "calmar":
                    metric_value = metrics_calc.calmar()
                else:
                    self.invalid_metric_results.append(result)
                    continue

                # NOTE: translated to English.
                if pd.isna(metric_value) or metric_value == float("inf") or metric_value == float("-inf"):
                    self.invalid_metric_results.append(result)
                    continue

                # NOTE: translated to English.
                result["optimal_metric"] = metric_value
                result["trade_count"] = trade_count
                self.valid_results.append(result)

            except Exception as e:
                log.warning(f"計算績效指標失敗: {e}")
                self.invalid_metric_results.append(result)

        # NOTE: translated to English.
        summary = {
            "total_results": self.total_results,
            "error_count": len(self.error_results),
            "no_records_count": len(self.no_records_results),
            "empty_records_count": len(self.empty_records_results),
            "no_trade_action_count": len(self.no_trade_action_results),
            "no_trade_count": len(self.no_trade_results),
            "invalid_metric_count": len(self.invalid_metric_results),
            "valid_count": len(self.valid_results),
        }

        return summary

    def _matches_strategy_idx(self, result: Dict[str, Any], strategy_idx: int) -> bool:
        """檢查結果是否匹配指定的 strategy_idx"""
        result_strategy_id = result.get("strategy_id", "")
        if not result_strategy_id:
            # NOTE: translated to English.
            return strategy_idx == 0

        try:
            if "_" in result_strategy_id:
                parts = result_strategy_id.split("_")
                result_strategy_idx = None
                for part in reversed(parts):
                    if part.isdigit():
                        result_strategy_idx = int(part) - 1
                        break

                if result_strategy_idx is None:
                    return strategy_idx == 0
                return result_strategy_idx == strategy_idx
            elif result_strategy_id.isdigit():
                return int(result_strategy_id) - 1 == strategy_idx
            else:
                return strategy_idx == 0
        except (ValueError, IndexError):
            return strategy_idx == 0

    def get_failure_reason(self, objective: str, strategy_idx: Optional[int] = None) -> str:
        """
        生成失敗原因的詳細描述

        Args:
            objective: 優化目標
            strategy_idx: 策略索引

        Returns:
            str: 失敗原因描述
        """
        reasons = []

        if self.error_results:
            reasons.append(f"錯誤結果: {len(self.error_results)} 個")

        if self.no_records_results:
            reasons.append(f"無記錄結果: {len(self.no_records_results)} 個")

        if self.empty_records_results:
            reasons.append(f"空記錄結果: {len(self.empty_records_results)} 個")

        if self.no_trade_action_results:
            reasons.append(f"無 Trade_action 欄位: {len(self.no_trade_action_results)} 個")

        if self.no_trade_results:
            reasons.append(f"無交易結果: {len(self.no_trade_results)} 個")

        if self.invalid_metric_results:
            reasons.append(f"無效指標結果: {len(self.invalid_metric_results)} 個")

        if reasons:
            strategy_info = f" (condition_pair {strategy_idx + 1})" if strategy_idx is not None else ""
            return f"{objective.upper()}{strategy_info} 優化失敗: " + ", ".join(reasons)
        else:
            return f"{objective.upper()} 優化失敗: 未知原因"

    def log_diagnostics(self, objective: str, strategy_idx: Optional[int] = None, logger: Optional[logging.Logger] = None):
        """
        記錄診斷信息到日誌

        Args:
            objective: 優化目標
            strategy_idx: 策略索引
            logger: 日誌記錄器
        """
        log = logger or logging.getLogger("lo2cin4bt.wfanalyser.optimizer.diagnostics")

        strategy_info = f"condition_pair {strategy_idx + 1}" if strategy_idx is not None else "所有策略"

        log.info(
            f"[診斷] {objective.upper()} {strategy_info} 結果分析: "
            f"總數={self.total_results}, "
            f"錯誤={len(self.error_results)}, "
            f"無記錄={len(self.no_records_results)}, "
            f"空記錄={len(self.empty_records_results)}, "
            f"無Trade_action={len(self.no_trade_action_results)}, "
            f"無交易={len(self.no_trade_results)}, "
            f"無效指標={len(self.invalid_metric_results)}, "
            f"有效={len(self.valid_results)}"
        )

        if len(self.valid_results) == 0:
            log.warning(self.get_failure_reason(objective, strategy_idx))


class ParameterOptimizer:
    """
    參數優化器

    在訓練集上尋找最優參數，優化目標可以是 Sharpe 或 Calmar。
    """

    def __init__(
        self,
        train_data: pd.DataFrame,
        frequency: str,
        config_data: Any,
        logger: Optional[logging.Logger] = None,
        shared_runtime_cache: Optional[Dict[str, Any]] = None,
        optimizer_context: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化 ParameterOptimizer

        Args:
            train_data: 訓練集數據
            frequency: 數據頻率
            config_data: 配置數據對象
            logger: 日誌記錄器
        """
        self.train_data = train_data
        self.frequency = frequency
        self.config_data = config_data
        self.logger = logger or logging.getLogger("lo2cin4bt.wfanalyser.optimizer")
        self._last_failure_reason: Optional[str] = None
        self._last_grid_region: Optional[Dict[str, Any]] = None  # NOTE: translated to English.
        self._last_grid_regions: Dict[int, Dict[str, Any]] = {}  # NOTE: translated to English.
        self._execution_plan_path: Optional[str] = None
        self._execution_plan: Optional[Dict[str, Any]] = None
        self._last_optuna_summary: Optional[Dict[str, Any]] = None
        self._shared_runtime_cache = shared_runtime_cache or {}
        self._optimizer_context = optimizer_context or {}
        self._optuna_run_token = uuid.uuid4().hex[:8]
        self.wfa_config = getattr(config_data, "wfa_config", {}) or {}
        self.backtester_config = self._resolve_backtester_runtime_config(
            config_data.backtester_config
        )

    def _resolve_backtester_runtime_config(
        self, base_backtester_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Resolve backtester config for WFA runtime (legacy or semantic native)."""
        runtime_config = dict(base_backtester_config or {})
        mode = self._resolve_strategy_mode(runtime_config)
        runtime_config["strategy_mode"] = mode
        if self._use_shared_strategy_runtime():
            cached_cfg = self._shared_runtime_cache.get("backtester_config", {})
            runtime = dict(cached_cfg) if isinstance(cached_cfg, dict) else dict(runtime_config)
            runtime["strategy_mode"] = "semantic"
            runtime.pop("condition_pairs", None)
            runtime.pop("indicator_params", None)
            self._execution_plan_path = self._shared_runtime_cache.get("execution_plan_path")
            cached_plan = self._shared_runtime_cache.get("execution_plan")
            self._execution_plan = dict(cached_plan) if isinstance(cached_plan, dict) else None
            return runtime

        bridged = self._build_runtime_backtester_from_strategy_contract(runtime_config)
        return bridged

    def _use_shared_strategy_runtime(self) -> bool:
        if not isinstance(self._shared_runtime_cache, dict) or not self._shared_runtime_cache:
            return False
        if self._shared_runtime_cache.get("strategy_mode") != "semantic":
            return False
        return isinstance(self._shared_runtime_cache.get("execution_plan"), dict)

    @staticmethod
    def _resolve_strategy_mode(backtester_config: Dict[str, Any]) -> str:
        mode = str(backtester_config.get("strategy_mode", "auto")).strip().lower()
        if mode not in {"semantic", "auto"}:
            raise ValueError("backtester.strategy_mode must be semantic/auto")
        if mode == "auto":
            has_strategy_contract_path = isinstance(
                backtester_config.get("strategy_contract_path"), str
            ) and bool(str(backtester_config.get("strategy_contract_path")).strip())
            if has_strategy_contract_path:
                return "semantic"
            raise ValueError("backtester.strategy_mode=auto requires strategy_contract_path")
        return mode

    def _resolve_contract_path(
        self, raw_path: str, *, config_file_path: Optional[str]
    ) -> str:
        from utils.path_resolver import resolve_input_path

        repo_root = Path(__file__).resolve().parent.parent
        resolved = resolve_input_path(
            raw_path,
            repo_root=repo_root,
            config_file_path=config_file_path,
        )
        return str(resolved.path)

    def _build_runtime_backtester_from_strategy_contract(
        self, backtester_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        from autorunner.StrategyCompiler import StrategyCompiler

        strategy_contract_path = backtester_config.get("strategy_contract_path")
        if not isinstance(strategy_contract_path, str) or not strategy_contract_path.strip():
            raise ValueError("semantic mode requires backtester.strategy_contract_path")

        config_file_path = backtester_config.get("__config_file_path")
        cfg_path = config_file_path if isinstance(config_file_path, str) else None
        resolved_strategy_path = self._resolve_contract_path(
            strategy_contract_path, config_file_path=cfg_path
        )

        feature_contract_path = backtester_config.get("feature_contract_path")
        resolved_feature_path: Optional[str] = None
        if isinstance(feature_contract_path, str) and feature_contract_path.strip():
            resolved_feature_path = self._resolve_contract_path(
                feature_contract_path, config_file_path=cfg_path
            )

        compiler = StrategyCompiler()
        output_dir = backtester_config.get("execution_plan_output_dir")
        compile_result = compiler.compile_from_paths(
            strategy_contract_path=resolved_strategy_path,
            feature_contract_path=resolved_feature_path,
            output_dir=output_dir if isinstance(output_dir, str) else None,
        )
        if not compile_result.valid:
            raise ValueError(
                f"WFA semantic compile failed: {'; '.join(compile_result.errors)}"
            )

        runtime_config = dict(backtester_config)
        runtime_config["strategy_mode"] = "semantic"
        runtime_config["strategy_contract_path"] = resolved_strategy_path
        runtime_config["feature_contract_path"] = resolved_feature_path
        runtime_config.pop("condition_pairs", None)
        runtime_config.pop("indicator_params", None)
        self._execution_plan_path = compile_result.execution_plan_path
        execution_plan = getattr(compile_result, "execution_plan", None)
        self._execution_plan = execution_plan if isinstance(execution_plan, dict) else None
        return runtime_config

    def optimize(self, objective: str, silent: bool = True) -> Optional[Dict[str, Any]]:
        """
        執行參數優化（向後兼容方法）

        Args:
            objective: 優化目標（"sharpe" 或 "calmar"）
            silent: 是否靜默模式（不顯示進度條和詳細輸出）

        Returns:
            Optional[Dict[str, Any]]: 最優參數，如果優化失敗則返回 None
        """
        result, _ = self.optimize_with_is_metrics(objective, silent)
        return result

    def _resolve_optimizer_config(self) -> Dict[str, Any]:
        config = self.wfa_config.get("optimizer", {})
        return config if isinstance(config, dict) else {}

    def _should_use_optuna_search(self) -> bool:
        optimizer_config = self._resolve_optimizer_config()
        return str(optimizer_config.get("type", "")).strip().lower() == "optuna"

    def _load_parameter_domains(self) -> Dict[str, Any]:
        strategy_contract_path = self.backtester_config.get("strategy_contract_path")
        if not isinstance(strategy_contract_path, str) or not strategy_contract_path.strip():
            return {}
        try:
            contract = self._load_json(strategy_contract_path)
        except Exception:
            return {}
        parameter_domains = contract.get("parameter_domains", {})
        return parameter_domains if isinstance(parameter_domains, dict) else {}

    def _build_optuna_search_space(self) -> List[SearchSpaceField]:
        search_space: List[SearchSpaceField] = []
        for name, domain in self._load_parameter_domains().items():
            if not isinstance(domain, dict):
                continue
            domain_type = str(domain.get("type", "")).strip().lower()
            if domain_type == "fixed":
                continue
            if domain_type == "set":
                values = list(domain.get("values", []))
                if not values:
                    continue
                search_space.append(
                    SearchSpaceField(
                        name=str(name),
                        field_type="categorical",
                        choices=values,
                    )
                )
                continue
            if domain_type == "range":
                low = domain.get("low", domain.get("start"))
                high = domain.get("high", domain.get("end"))
                step = domain.get("step")
                if low is None or high is None:
                    self.logger.warning(
                        "optuna search space skipped param=%s because range domain is missing low/high or start/end",
                        name,
                    )
                    continue
                if isinstance(low, int) and isinstance(high, int):
                    search_space.append(
                        SearchSpaceField(
                            name=str(name),
                            field_type="int",
                            low=low,
                            high=high,
                            step=step or 1,
                            log=bool(domain.get("log", False)),
                        )
                    )
                else:
                    search_space.append(
                        SearchSpaceField(
                            name=str(name),
                            field_type="float",
                            low=float(low),
                            high=float(high),
                            step=step,
                            log=bool(domain.get("log", False)),
                        )
                    )
        return search_space

    def optimize_with_is_metrics(
        self, objective: str, silent: bool = True
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        執行參數優化，並返回訓練集績效指標

        Args:
            objective: 優化目標（"sharpe" 或 "calmar"）
            silent: 是否靜默模式（不顯示進度條和詳細輸出）

        Returns:
            Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
            (最優參數, 訓練集績效指標)，如果優化失敗則返回 (None, None)
        """
        self._last_failure_reason = None
        if self.backtester_config.get("strategy_mode") == "semantic":
            return self._optimize_node_ir(objective, silent)
        self._last_failure_reason = "WFA requires strategy_mode=semantic with strategy_contract_path"
        return None, None
        """
        執行參數優化

        Args:
            objective: 優化目標（"sharpe" 或 "calmar"）
            silent: 是否靜默模式（不顯示進度條和詳細輸出）

        Returns:
            Optional[Dict[str, Any]]: 最優參數，如果優化失敗則返回 None
        """
        try:
            # NOTE: translated to English.
            raise RuntimeError("Public legacy WFA optimizer path has been removed; use strategy_mode=semantic.")

            engine = _removed_public_wfa_engine(
                self.train_data, self.frequency, self.logger, symbol=getattr(self.config_data, "symbol", "X")
            )

            # NOTE: translated to English.
            backtest_config = self._build_optimization_config()

            # NOTE: translated to English.
            parameter_combinations = engine.generate_parameter_combinations(backtest_config)

            if not parameter_combinations:
                self.logger.warning(f"未生成任何參數組合，配置: {backtest_config}")
                self._last_failure_reason = "未生成任何參數組合"
                return None, None

            # NOTE: translated to English.
            if silent:
                import io
                from contextlib import redirect_stdout, redirect_stderr
                import logging

                # NOTE: translated to English.
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    # NOTE: translated to English.
                    old_level = logging.getLogger().level
                    logging.getLogger().setLevel(logging.ERROR)
                    try:
                        results = engine.run_backtests(backtest_config)
                    finally:
                        logging.getLogger().setLevel(old_level)
            else:
                results = engine.run_backtests(backtest_config)

            if not results:
                self.logger.warning("回測執行返回空結果")
                self._last_failure_reason = "回測執行返回空結果"
                return None, None

            # NOTE: translated to English.
            diagnostics = OptimizationDiagnostics()
            summary = diagnostics.analyze_results(results, objective, strategy_idx=None, logger=self.logger)

            # NOTE: translated to English.
            diagnostics.log_diagnostics(objective, strategy_idx=None, logger=self.logger)

            # NOTE: translated to English.
            self._last_diagnostics_summary = summary


            # NOTE: translated to English.
            condition_pairs = self.backtester_config.get("condition_pairs", [])
            all_optimal_params = {}
            all_train_metrics = {}
            all_grid_regions = {}

            for strategy_idx, pair in enumerate(condition_pairs):
                # NOTE: translated to English.
                sample_strategy_ids = [r.get("strategy_id", "N/A") for r in results[:5]]
                self.logger.info(
                    f"[DEBUG] condition_pair {strategy_idx + 1} ({pair.get('entry', [])} + {pair.get('exit', [])}): "
                    f"總結果數: {len(results)}, 樣本 strategy_id: {sample_strategy_ids}"
                )

                # NOTE: translated to English.
                pair_results_count = 0
                for r in results:
                    result_strategy_id = r.get("strategy_id", "")
                    if result_strategy_id:
                        try:
                            if "_" in result_strategy_id:
                                parts = result_strategy_id.split("_")
                                result_strategy_idx = None
                                for part in reversed(parts):
                                    if part.isdigit():
                                        result_strategy_idx = int(part) - 1
                                        break
                                if result_strategy_idx == strategy_idx:
                                    pair_results_count += 1
                        except (ValueError, IndexError):
                            pass

                self.logger.info(
                    f"[DEBUG] condition_pair {strategy_idx + 1} 找到 {pair_results_count} 個匹配的結果"
                )

                # NOTE: translated to English.
                optimal_result = self._find_optimal_result(results, objective, strategy_idx=strategy_idx)

                if optimal_result is None:
                    # NOTE: translated to English.
                    sample_strategy_ids = [r.get("strategy_id", "N/A") for r in results[:10]]
                    self.logger.warning(
                        f"[DEBUG] condition_pair {strategy_idx + 1} ({pair.get('entry', [])} + {pair.get('exit', [])}) "
                        f"未找到有效的 {objective.upper()} 結果。"
                        f"樣本 strategy_id: {sample_strategy_ids}, 匹配結果數: {pair_results_count}"
                    )
                    continue

                self.logger.info(
                    f"[DEBUG] condition_pair {strategy_idx + 1} 找到最優結果，strategy_id: {optimal_result.get('strategy_id', 'N/A')}"
                )

                # NOTE: translated to English.
                single_optimal_params = self._extract_optimal_params(optimal_result, strategy_idx=strategy_idx)

                if not single_optimal_params:
                    self.logger.warning(
                        f"condition_pair {strategy_idx + 1} 未能提取參數"
                    )
                    continue

                # NOTE: translated to English.
                # NOTE: translated to English.
                pair_results = []
                for r in results:
                    result_strategy_id = r.get("strategy_id", "")
                    if not result_strategy_id:
                        # NOTE: translated to English.
                        if strategy_idx == 0:
                            pair_results.append(r)
                        continue

                    # NOTE: translated to English.
                    try:
                        if "_" in result_strategy_id:
                            parts = result_strategy_id.split("_")
                            result_strategy_idx = None
                            for part in reversed(parts):
                                if part.isdigit():
                                    result_strategy_idx = int(part) - 1
                                    break

                            if result_strategy_idx == strategy_idx:
                                pair_results.append(r)
                        elif result_strategy_id.isdigit():
                            # NOTE: translated to English.
                            if int(result_strategy_id) - 1 == strategy_idx:
                                pair_results.append(r)
                    except (ValueError, IndexError):
                        # NOTE: translated to English.
                        if strategy_idx == 0:
                            pair_results.append(r)

                self.logger.info(
                    f"[DEBUG] condition_pair {strategy_idx + 1} 用於九宮格的結果數: {len(pair_results)}"
                )

                grid_region = self._find_best_grid_region(
                    single_optimal_params, pair_results, objective, silent, strategy_idx=strategy_idx
                )

                if grid_region:
                    self.logger.info(
                        f"[DEBUG] condition_pair {strategy_idx + 1} 九宮格區域選擇成功，"
                        f"參數組合數: {len(grid_region.get('all_params', []))}"
                    )
                else:
                    self.logger.warning(
                        f"[DEBUG] condition_pair {strategy_idx + 1} 九宮格區域選擇失敗"
                    )

                if grid_region is None:
                    # NOTE: translated to English.
                    self.logger.warning(
                        f"condition_pair {strategy_idx + 1} 九宮格區域選擇失敗，使用單一最優參數"
                    )
                    train_metrics = self._calculate_train_metrics(optimal_result, objective)
                    all_optimal_params.update(single_optimal_params)
                    all_train_metrics.update(train_metrics or {})
                else:
                    # NOTE: translated to English.
                    grid_params = grid_region["params"]
                    grid_train_metrics = grid_region["train_metrics"]
                    grid_all_params = grid_region["all_params"]  # NOTE: translated to English.

                    # NOTE: translated to English.
                    all_optimal_params.update(grid_params)
                    all_train_metrics.update(grid_train_metrics or {})

                    # NOTE: translated to English.
                    if strategy_idx not in all_grid_regions:
                        all_grid_regions[strategy_idx] = {}
                    all_grid_regions[strategy_idx] = {
                        "all_params": grid_all_params,
                        "avg_metric": grid_region.get("avg_metric"),
                        "individual_metrics": grid_region.get("individual_metrics", []),
                        "individual_full_metrics": grid_region.get("individual_full_metrics", []),
                        "train_metrics": grid_region.get("train_metrics"),
                    }

            # NOTE: translated to English.
            if not all_optimal_params:
                # NOTE: translated to English.
                summary = getattr(self, '_last_diagnostics_summary', {})
                error_count = summary.get('error_count', 0)
                no_trade_count = summary.get('no_trade_count', 0)
                valid_count = summary.get('valid_count', 0)

                failure_reason = (
                    f"所有 condition_pairs 都未找到有效的 {objective.upper()} 結果。"
                    f"錯誤: {error_count}, 無交易: {no_trade_count}, 有效: {valid_count}"
                )
                self.logger.warning(failure_reason)
                self._last_failure_reason = failure_reason
                return None, None

            # NOTE: translated to English.
            # NOTE: translated to English.
            self._last_grid_regions = all_grid_regions  # NOTE: translated to English.
            self._last_grid_region = None  # NOTE: translated to English.

            # NOTE: translated to English.
            self.logger.info(
                f"[DEBUG] 保存的 grid_regions: {list(all_grid_regions.keys())}, "
                f"總數: {len(all_grid_regions)}"
            )

            # NOTE: translated to English.
            if all_grid_regions:
                first_strategy_idx = min(all_grid_regions.keys())
                self._last_grid_region = all_grid_regions[first_strategy_idx]
                self.logger.info(
                    f"[DEBUG] 第一個 grid_region 來自 strategy_idx: {first_strategy_idx}"
                )
            else:
                # NOTE: translated to English.
                self._last_grid_region = {
                    "all_params": [],
                    "avg_metric": None,
                    "individual_metrics": [],
                    "individual_full_metrics": [],
                    "train_metrics": all_train_metrics,
                }
                self.logger.warning("[DEBUG] 沒有 grid_regions，創建空的 grid_region")

            # NOTE: translated to English.
            self.logger.info(
                f"[DEBUG] 最終 optimal_params 的鍵: {list(all_optimal_params.keys())[:10]}... "
                f"(總數: {len(all_optimal_params)})"
            )

            return all_optimal_params, all_train_metrics

        except Exception as e:
            self.logger.error(f"參數優化失敗: {e}")
            self._last_failure_reason = f"優化過程異常: {str(e)}"
            return None, None

    def _optimize_node_ir(
        self, objective: str, silent: bool = True
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Optimize semantic strategy directly from node_ir execution plan."""
        if self._should_use_optuna_search():
            return self._optimize_node_ir_with_optuna(objective, silent)
        _ = silent  # keep interface parity with legacy path
        if self.train_data is None or not isinstance(self.train_data, pd.DataFrame):
            self._last_failure_reason = "train_data is not available for semantic optimization"
            return None, None

        results = self._run_node_ir_backtest(
            self.train_data,
            backtest_id_prefix="wfa_is",
        )
        if not results:
            self._last_failure_reason = "semantic node_ir optimization returned no results"
            return None, None

        valid_rows: List[Dict[str, Any]] = []
        for result in results:
            metric_value = self._calculate_metric_from_result(result, objective)
            if metric_value is None:
                continue
            semantic_combo = self._extract_semantic_combo(result)
            full_metrics = self._calculate_individual_full_metrics(result) or {}
            valid_rows.append(
                {
                    "result": result,
                    "metric_value": metric_value,
                    "params": {"semantic_combo": semantic_combo},
                    "full_metrics": full_metrics,
                }
            )

        if not valid_rows:
            self._last_failure_reason = f"no valid semantic result for objective={objective}"
            return None, None

        ranked_rows = sorted(valid_rows, key=lambda row: row["metric_value"], reverse=True)
        best_row = ranked_rows[0]
        window_cap = self._resolve_window_cap_combos()
        selected_rows = ranked_rows
        if isinstance(window_cap, int) and window_cap > 0 and len(selected_rows) > window_cap:
            self.logger.info(
                "semantic window_cap_combos applied: %s -> %s",
                len(selected_rows),
                window_cap,
            )
            selected_rows = selected_rows[:window_cap]

        optimal_params = dict(best_row["params"])
        train_metrics = self._calculate_train_metrics(best_row["result"], objective)

        all_params = [row["params"] for row in selected_rows]
        all_metric_values = [row["metric_value"] for row in selected_rows]
        individual_metrics = [{objective: row["metric_value"]} for row in selected_rows]
        individual_full_metrics = [row["full_metrics"] for row in selected_rows]
        grid_train_metrics = self._calculate_grid_train_metrics(
            [{"result": row["result"]} for row in selected_rows],
            objective,
        ) or train_metrics
        grid_region = {
            "params": optimal_params,
            "all_params": all_params,
            "avg_metric": sum(all_metric_values) / len(all_metric_values),
            "individual_metrics": individual_metrics,
            "individual_full_metrics": individual_full_metrics,
            "train_metrics": grid_train_metrics,
            "window_cap_combos": window_cap,
        }

        self._last_grid_region = grid_region
        self._last_grid_regions = {0: grid_region}
        return optimal_params, train_metrics

    def _optimize_node_ir_with_optuna(
        self,
        objective: str,
        silent: bool = True,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        _ = silent
        search_space = self._build_optuna_search_space()
        if not search_space:
            self.logger.warning("Optuna search requested but no parameter domains were found; falling back to exhaustive semantic search.")
            current = self.wfa_config
            try:
                self.wfa_config = {**current, "optimizer": {}}
                return self._optimize_node_ir(objective, silent)
            finally:
                self.wfa_config = current

        optimizer_config = self._resolve_optimizer_config()
        trial_cache: Dict[int, Dict[str, Any]] = {}
        engine = OptunaSearchEngine(
            optimizer_config,
            storage_dir=Path("outputs") / "wfanalyser" / "optuna_studies",
            logger=self.logger,
        )

        def objective_fn(params: Dict[str, Any], trial: optuna.trial.Trial) -> float:
            semantic_combo = dict(params)
            results = self._run_node_ir_backtest(
                self.train_data,
                backtest_id_prefix=f"wfa_optuna_is_{trial.number}",
                semantic_combos=[semantic_combo],
            )
            if not results:
                trial_cache[trial.number] = {
                    "params": semantic_combo,
                    "metric": None,
                    "full_metrics": {},
                }
                return float("-inf")
            result = results[0]
            metric_value = self._calculate_metric_from_result(result, objective)
            if metric_value is None:
                metric_value = float("-inf")
            full_metrics = self._calculate_individual_full_metrics(result) or {}
            trial_cache[trial.number] = {
                "params": semantic_combo,
                "metric": metric_value,
                "result": result,
                "full_metrics": full_metrics,
            }
            trial.report(metric_value, step=1)
            if trial.should_prune():
                raise optuna.TrialPruned()
            return metric_value

        window_id = self._optimizer_context.get("window_id", "bootstrap")
        train_start = self._optimizer_context.get("train_start", "na")
        train_end = self._optimizer_context.get("train_end", "na")
        strategy_key = abs(hash(str(self.backtester_config.get("strategy_contract_path")))) % 100000
        study_name = (
            f"wfa_{objective}_w{window_id}_"
            f"{train_start}_{train_end}_{strategy_key}_{self._optuna_run_token}"
        )
        study_payload = engine.optimize(
            study_name=study_name,
            search_space=search_space,
            objective_fn=objective_fn,
        )
        self._last_optuna_summary = study_payload
        ranked_trials = [
            trial_cache[item["number"]]
            for item in study_payload.get("trials", [])
            if item.get("state") == "COMPLETE"
            and item.get("number") in trial_cache
            and trial_cache[item.get("number")].get("metric") is not None
        ]
        ranked_trials.sort(key=lambda row: row.get("metric", float("-inf")), reverse=True)
        if not ranked_trials:
            self._last_failure_reason = f"no valid optuna trials for objective={objective}"
            return None, None

        best_row = ranked_trials[0]
        top_n = int((self.wfa_config.get("robust_selection", {}) or {}).get("top_n_candidates", 20))
        selected_rows = ranked_trials[: max(1, top_n)]
        optimal_params = {"semantic_combo": best_row["params"]}
        train_metrics = dict(best_row.get("full_metrics", {}))
        if objective not in train_metrics:
            train_metrics[objective] = best_row.get("metric")
        grid_region = {
            "params": optimal_params,
            "all_params": [{"semantic_combo": row["params"]} for row in selected_rows],
            "avg_metric": float(sum(row["metric"] for row in selected_rows if row.get("metric") not in (None, float("-inf"))) / max(1, len(selected_rows))),
            "individual_metrics": [{objective: row.get("metric")} for row in selected_rows],
            "individual_full_metrics": [row.get("full_metrics", {}) for row in selected_rows],
            "train_metrics": train_metrics,
            "optuna_study_summary": study_payload,
        }
        self._last_grid_region = grid_region
        self._last_grid_regions = {0: grid_region}
        return optimal_params, train_metrics

    def run_grid_test(
        self,
        test_data: pd.DataFrame,
        grid_region: Optional[Dict[str, Any]],
        fallback_params: Dict[str, Any],
        objective: str,
        silent: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Run semantic OOS testing from selected semantic combos."""
        _ = silent  # keep interface parity with legacy path
        if test_data is None or not isinstance(test_data, pd.DataFrame):
            return None

        all_params = []
        if isinstance(grid_region, dict):
            candidate_params = grid_region.get("all_params", [])
            if isinstance(candidate_params, list):
                all_params = [p for p in candidate_params if isinstance(p, dict)]
        if not all_params and isinstance(fallback_params, dict):
            all_params = [fallback_params]
        if not all_params:
            return None
        window_cap = self._resolve_window_cap_combos()
        if isinstance(window_cap, int) and window_cap > 0 and len(all_params) > window_cap:
            self.logger.info(
                "semantic OOS window_cap_combos applied: %s -> %s",
                len(all_params),
                window_cap,
            )
            all_params = all_params[:window_cap]

        all_metrics: List[float] = []
        all_returns: List[float] = []
        all_sharpes: List[float] = []
        all_calmars: List[float] = []
        all_sortinos: List[float] = []
        all_mdds: List[float] = []
        all_equity_curves: List[np.ndarray] = []
        individual_results: List[Dict[str, Any]] = []

        for idx, param_item in enumerate(all_params):
            semantic_combo = self._coerce_semantic_combo(param_item)
            if semantic_combo is None:
                individual_results.append(
                    {
                        "param_index": idx,
                        "params": param_item,
                        "metric": None,
                        "return": None,
                        "success": False,
                        "failure_reason": "missing semantic_combo for semantic OOS run",
                    }
                )
                continue

            results = self._run_node_ir_backtest(
                test_data,
                backtest_id_prefix=f"wfa_oos_{idx}",
                semantic_combos=[semantic_combo],
            )
            if not results:
                individual_results.append(
                    {
                        "param_index": idx,
                        "params": {"semantic_combo": semantic_combo},
                        "metric": None,
                        "return": None,
                        "success": False,
                        "failure_reason": "node_ir returned no OOS result",
                    }
                )
                continue

            result = results[0]
            metric_value = self._calculate_metric_from_result(result, objective)
            full_metrics = self._calculate_individual_full_metrics(result) or {}
            total_return = full_metrics.get("total_return")
            sharpe = full_metrics.get("sharpe")
            calmar = full_metrics.get("calmar")
            sortino = full_metrics.get("sortino")
            max_drawdown = full_metrics.get("max_drawdown")
            equity_curve = self._extract_equity_curve(result)

            if metric_value is not None:
                all_metrics.append(metric_value)
            if isinstance(total_return, (int, float)):
                all_returns.append(float(total_return))
            if isinstance(sharpe, (int, float)):
                all_sharpes.append(float(sharpe))
            if isinstance(calmar, (int, float)):
                all_calmars.append(float(calmar))
            if isinstance(sortino, (int, float)):
                all_sortinos.append(float(sortino))
            if isinstance(max_drawdown, (int, float)):
                all_mdds.append(float(max_drawdown))
            if equity_curve is not None:
                all_equity_curves.append(equity_curve)

            individual_results.append(
                {
                    "param_index": idx,
                    "params": {"semantic_combo": semantic_combo},
                    "metric": metric_value,
                    "return": total_return,
                    "sharpe": sharpe,
                    "calmar": calmar,
                    "sortino": sortino,
                    "max_drawdown": max_drawdown,
                    "success": metric_value is not None,
                }
            )

        if not all_metrics:
            return None

        metrics = {
            objective: sum(all_metrics) / len(all_metrics),
            "sharpe": (sum(all_sharpes) / len(all_sharpes)) if all_sharpes else None,
            "calmar": (sum(all_calmars) / len(all_calmars)) if all_calmars else None,
            "sortino": (sum(all_sortinos) / len(all_sortinos)) if all_sortinos else None,
            "total_return": (sum(all_returns) / len(all_returns)) if all_returns else None,
            "max_drawdown": (sum(all_mdds) / len(all_mdds)) if all_mdds else None,
            "param_count": len(all_metrics),
        }
        return {
            "backtest_result": None,
            "equity_curve": self._average_equity_curves(all_equity_curves),
            "metrics": metrics,
            "all_metrics": all_metrics,
            "individual_results": individual_results,
            "window_cap_combos": window_cap,
        }

    def _resolve_window_cap_combos(self) -> Optional[int]:
        raw_direct = self.backtester_config.get("window_cap_combos")
        if isinstance(raw_direct, int) and raw_direct > 0:
            return int(raw_direct)
        plan = self._execution_plan
        if isinstance(plan, dict):
            guard = plan.get("combo_guard", {})
            if isinstance(guard, dict):
                raw_guard = guard.get("window_cap_combos")
                if isinstance(raw_guard, int) and raw_guard > 0:
                    return int(raw_guard)
        return None

    def _run_node_ir_backtest(
        self,
        data: pd.DataFrame,
        *,
        backtest_id_prefix: str,
        semantic_combos: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        from backtester.NodeIRExecutor_backtester import NodeIRExecutorBacktester

        execution_plan = self._execution_plan
        if not isinstance(execution_plan, dict):
            raise ValueError("semantic execution plan is not available for node_ir runtime")

        strategy_contract_path = self.backtester_config.get("strategy_contract_path")
        if not isinstance(strategy_contract_path, str) or not strategy_contract_path.strip():
            raise ValueError("semantic mode requires resolved strategy_contract_path")

        feature_contract_path = self.backtester_config.get("feature_contract_path")
        trading_params = self.backtester_config.get("trading_params", {})
        predictor_column = self.backtester_config.get("selected_predictor", "X")
        symbol = getattr(self.config_data, "symbol", "X")

        if semantic_combos is not None:
            try:
                strategy_contract = self._load_json(strategy_contract_path)
            except Exception:
                strategy_contract = {}
            strategy_contract["parameter_domains"] = self._parameter_domains_from_semantic_combos(
                semantic_combos
            )
            strategy_contract_path_for_run = self._write_temp_strategy_contract(strategy_contract)
        else:
            strategy_contract_path_for_run = strategy_contract_path

        executor = NodeIRExecutorBacktester(data=data, logger=self.logger)
        return executor.run_from_paths(
            strategy_contract_path=strategy_contract_path_for_run,
            feature_contract_path=(
                feature_contract_path
                if isinstance(feature_contract_path, str) and feature_contract_path.strip()
                else None
            ),
            execution_plan=execution_plan,
            trading_params=trading_params if isinstance(trading_params, dict) else {},
            predictor_column=str(predictor_column),
            symbol=str(symbol),
            backtest_id_prefix=backtest_id_prefix,
        )

    @staticmethod
    def _extract_semantic_combo(result: Dict[str, Any]) -> Dict[str, Any]:
        params = result.get("params", {})
        if not isinstance(params, dict):
            return {}
        combo = params.get("semantic_combo", {})
        if isinstance(combo, dict):
            return dict(combo)
        return {}

    @staticmethod
    def _coerce_semantic_combo(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(params, dict):
            return None
        combo = params.get("semantic_combo", params)
        if not isinstance(combo, dict):
            return None
        return dict(combo)

    @staticmethod
    def _extract_equity_curve(result: Dict[str, Any]) -> Optional[np.ndarray]:
        records = result.get("records")
        if not isinstance(records, pd.DataFrame) or records.empty:
            return None
        if "Equity_value" not in records.columns:
            return None
        return records["Equity_value"].to_numpy(dtype=np.float64, copy=True)

    @staticmethod
    def _average_equity_curves(curves: List[np.ndarray]) -> Optional[np.ndarray]:
        if not curves:
            return None
        min_length = min(len(curve) for curve in curves)
        if min_length <= 0:
            return None
        trimmed = [curve[:min_length] for curve in curves]
        return np.mean(trimmed, axis=0)

    @staticmethod
    def _load_json(path: str) -> Dict[str, Any]:
        import json

        content = Path(path).read_text(encoding="utf-8-sig")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError(f"{path} must be a JSON object")
        return data

    def _write_temp_strategy_contract(self, strategy_contract: Dict[str, Any]) -> str:
        import json
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="wfa_strategy_",
            delete=False,
        ) as temp_file:
            json.dump(strategy_contract, temp_file, ensure_ascii=False, indent=2)
            return temp_file.name

    @staticmethod
    def _parameter_domains_from_semantic_combos(
        combos: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not combos:
            return {}
        keys = sorted({str(key) for combo in combos for key in combo.keys()})
        domains: Dict[str, Any] = {}
        for key in keys:
            values: List[Any] = []
            for combo in combos:
                if key in combo:
                    values.append(combo[key])
            unique_values = []
            for value in values:
                if value not in unique_values:
                    unique_values.append(value)
            if len(unique_values) == 1:
                domains[key] = {"type": "fixed", "value": unique_values[0]}
            else:
                domains[key] = {"type": "set", "values": unique_values}
        return domains

    def _is_variable_param(self, value: Any) -> bool:
        """
        判斷參數是否為可變參數（範圍或逗號分隔的多值）

        Args:
            value: 參數值

        Returns:
            bool: 如果是可變參數返回 True，否則返回 False
        """
        if not isinstance(value, str):
            return False

        # NOTE: translated to English.
        if ":" in value:
            parts = value.split(":")
            if len(parts) == 3:
                try:
                    # NOTE: translated to English.
                    start = float(parts[0])
                    end = float(parts[1])
                    step = float(parts[2])
                    # NOTE: translated to English.
                    if start == end:
                        return False
                    # NOTE: translated to English.
                    if step > 0 and end < start:
                        return False
                    # NOTE: translated to English.
                    if step < 0 and end > start:
                        return False
                    # NOTE: translated to English.
                    if step > 0:
                        num_values = int((end - start) / step) + 1
                    elif step < 0:
                        num_values = int((start - end) / abs(step)) + 1
                    else:
                        return False  # NOTE: translated to English.
                    # NOTE: translated to English.
                    return num_values > 1
                except (ValueError, ZeroDivisionError):
                    return False

        # NOTE: translated to English.
        if "," in value:
            parts = [x.strip() for x in value.split(",")]
            if len(parts) > 1:
                # NOTE: translated to English.
                try:
                    unique_values = set()
                    for part in parts:
                        unique_values.add(float(part))
                    # NOTE: translated to English.
                    return len(unique_values) > 1
                except ValueError:
                    return False

        return False

    def _count_variable_params(self, params_config: Dict[str, Any]) -> int:
        """
        統計參數配置中的可變參數數量

        Args:
            params_config: 參數配置字典

        Returns:
            int: 可變參數的數量
        """
        count = 0
        # NOTE: translated to English.
        excluded_keys = {"strat_idx", "indicator_type", "_help", "_description"}

        for key, value in params_config.items():
            if key in excluded_keys:
                continue
            if self._is_variable_param(value):
                count += 1

        return count

    def _validate_parameter_config(self, params_config: Dict[str, Any], indicator_name: str) -> None:
        """
        驗證參數配置（已棄用，保留以向後兼容）
        現在改用 _count_variable_params 來統計，並在 condition_pair 層級驗證總和

        Args:
            params_config: 參數配置字典
            indicator_name: 指標名稱（用於錯誤提示）
        """
        # NOTE: translated to English.
        variable_count = self._count_variable_params(params_config)
        if variable_count > 0:
            variable_params = []
            excluded_keys = {"strat_idx", "indicator_type", "_help", "_description"}
            for key, value in params_config.items():
                if key not in excluded_keys and self._is_variable_param(value):
                    variable_params.append(key)
            self.logger.debug(
                f"指標 {indicator_name}: 可變參數 {variable_params} ({variable_count} 個)"
            )

    def _build_optimization_config(self) -> Dict[str, Any]:
        """
        構建優化用的回測配置

        Returns:
            Dict[str, Any]: 回測配置
        """
        from backtester.Indicators_backtester import IndicatorsBacktester

        condition_pairs = self.backtester_config.get("condition_pairs", [])
        raw_indicator_params = self.backtester_config.get("indicator_params", {})

        # NOTE: translated to English.
        # NOTE: translated to English.
        # NOTE: translated to English.
        indicators_helper = IndicatorsBacktester(logger=self.logger)
        indicator_params = {}

        for i, pair in enumerate(condition_pairs):
            strategy_idx = i + 1

            # NOTE: translated to English.
            entry_param_configs = []
            exit_param_configs = []

            # NOTE: translated to English.
            for entry_indicator in pair.get("entry", []):
                strategy_alias = f"{entry_indicator}_strategy_{strategy_idx}"
                if strategy_alias in raw_indicator_params:
                    params_config = raw_indicator_params[strategy_alias]
                    entry_param_configs.append((strategy_alias, params_config))

                    # NOTE: translated to English.
                    try:
                        param_list = indicators_helper.get_indicator_params(
                            entry_indicator, params_config
                        )
                        if not param_list:
                            self.logger.warning(
                                f"指標 {entry_indicator} (strategy_alias={strategy_alias}) 未生成任何參數組合，"
                                f"params_config={params_config}"
                            )
                        indicator_params[strategy_alias] = param_list
                    except Exception as e:
                        self.logger.error(
                            f"生成 {entry_indicator} (strategy_alias={strategy_alias}) 參數列表失敗: {e}, "
                            f"params_config={params_config}"
                        )
                        indicator_params[strategy_alias] = []
                else:
                    self.logger.warning(
                        f"未找到指標 {entry_indicator} 的參數配置 (strategy_alias={strategy_alias})，"
                        f"可用的配置鍵: {list(raw_indicator_params.keys())}"
                    )

            # NOTE: translated to English.
            for exit_indicator in pair.get("exit", []):
                strategy_alias = f"{exit_indicator}_strategy_{strategy_idx}"
                if strategy_alias in raw_indicator_params:
                    params_config = raw_indicator_params[strategy_alias]
                    exit_param_configs.append((strategy_alias, params_config))

                    # NOTE: translated to English.
                    try:
                        param_list = indicators_helper.get_indicator_params(
                            exit_indicator, params_config
                        )
                        if not param_list:
                            self.logger.warning(
                                f"指標 {exit_indicator} (strategy_alias={strategy_alias}) 未生成任何參數組合，"
                                f"params_config={params_config}"
                            )
                        indicator_params[strategy_alias] = param_list
                    except Exception as e:
                        self.logger.error(
                            f"生成 {exit_indicator} (strategy_alias={strategy_alias}) 參數列表失敗: {e}, "
                            f"params_config={params_config}"
                        )
                        indicator_params[strategy_alias] = []
                else:
                    self.logger.warning(
                        f"未找到指標 {exit_indicator} 的參數配置 (strategy_alias={strategy_alias})，"
                        f"可用的配置鍵: {list(raw_indicator_params.keys())}"
                    )

            # NOTE: translated to English.
            total_variable_params = 0
            all_indicator_names = []
            for strategy_alias, params_config in entry_param_configs + exit_param_configs:
                variable_count = self._count_variable_params(params_config)
                total_variable_params += variable_count
                all_indicator_names.append(strategy_alias)

            if total_variable_params > 2:
                raise ValueError(
                    f"condition_pair {strategy_idx} (entry + exit) 的可變參數總數超過2個（找到 {total_variable_params} 個）。"
                    f"涉及的指標: {', '.join(all_indicator_names)}"
                    f"WFA 要求 entry + exit 兩個指標的可變參數總和最多只能有2個。"
                    f"請修改配置，將多餘的可變參數改為固定值。"
                )

            if total_variable_params > 0:
                self.logger.info(
                    f"condition_pair {strategy_idx}: entry + exit 總可變參數 {total_variable_params} 個，"
                    f"指標: {', '.join(all_indicator_names)}"
                )

        backtest_config = {
            "condition_pairs": condition_pairs,
            "indicator_params": indicator_params,
            "predictors": [
                self.backtester_config.get("selected_predictor", "X")
            ],
            "trading_params": self.backtester_config.get("trading_params", {}),
        }

        return backtest_config

    def _find_optimal_result(
        self, results: List[Dict[str, Any]], objective: str, strategy_idx: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        從回測結果中找到最優結果（使用通用診斷機制）

        Args:
            results: 回測結果列表
            objective: 優化目標（"sharpe" 或 "calmar"）
            strategy_idx: 可選的策略索引，如果提供則只從該策略的結果中選擇

        Returns:
            Optional[Dict[str, Any]]: 最優結果，如果未找到則返回 None
        """
        # NOTE: translated to English.
        diagnostics = OptimizationDiagnostics()
        diagnostics.analyze_results(results, objective, strategy_idx, self.logger)

        # NOTE: translated to English.
        diagnostics.log_diagnostics(objective, strategy_idx, self.logger)

        # NOTE: translated to English.
        if not diagnostics.valid_results:
            failure_reason = diagnostics.get_failure_reason(objective, strategy_idx)
            self._last_failure_reason = failure_reason
            self.logger.warning(failure_reason)
            return None

        # NOTE: translated to English.
        optimal_result = max(diagnostics.valid_results, key=lambda x: x.get("optimal_metric", float("-inf")))

        return optimal_result

    def _extract_optimal_params(self, optimal_result: Dict[str, Any], strategy_idx: Optional[int] = None) -> Dict[str, Any]:
        """
        從最優結果中提取參數

        Args:
            optimal_result: 最優結果
            strategy_idx: 策略索引（從0開始），如果為None則從結果中推斷

        Returns:
            Dict[str, Any]: 最優參數（格式與 indicator_params 相同）
        """
        # NOTE: translated to English.
        # NOTE: translated to English.
        # {"entry": [...], "exit": [...], "predictor": "..."}

        optimal_params = {}

        if "params" not in optimal_result:
            self.logger.warning("結果中缺少 params 字段")
            return optimal_params

        params = optimal_result["params"]
        entry_params = params.get("entry", [])
        exit_params = params.get("exit", [])

        # NOTE: translated to English.
        # NOTE: translated to English.
        condition_pairs = self.backtester_config.get("condition_pairs", [])

        if not condition_pairs:
            self.logger.warning("配置中缺少 condition_pairs")
            return optimal_params

        # NOTE: translated to English.
        if strategy_idx is None:
            # NOTE: translated to English.
            strategy_id = optimal_result.get("strategy_id", "")
            if strategy_id:
                try:
                    strategy_idx = int(strategy_id.split("_")[-1]) - 1
                except (ValueError, IndexError):
                    strategy_idx = 0
            else:
                strategy_idx = 0

        if strategy_idx >= len(condition_pairs):
            self.logger.warning(f"strategy_idx {strategy_idx} 超出範圍，使用第一個 condition_pair")
            strategy_idx = 0

        # NOTE: translated to English.
        pair = condition_pairs[strategy_idx]
        strategy_idx_1based = strategy_idx + 1  # NOTE: translated to English.

        # NOTE: translated to English.
        entry_indicators = pair.get("entry", [])
        if not entry_indicators:
            self.logger.warning("條件配對中缺少 entry 指標")
        else:
            for i, entry_indicator in enumerate(entry_indicators):
                strategy_alias = f"{entry_indicator}_strategy_{strategy_idx_1based}"
                if i < len(entry_params):
                    param_dict = entry_params[i]
                    # NOTE: translated to English.
                    from backtester.IndicatorParams_backtester import IndicatorParams

                    # NOTE: translated to English.
                    # NOTE: translated to English.
                    # {"indicator_type": "MA", "period": 20, "ma_type": "SMA", ...}
                    indicator_type = param_dict.get("indicator_type", "MA")
                    indicator_param = IndicatorParams(indicator_type)

                    # NOTE: translated to English.
                    for key, value in param_dict.items():
                        if key != "indicator_type" and key not in ["trading_params"]:
                            # NOTE: translated to English.
                            # NOTE: translated to English.
                            indicator_param.add_param(key, value)

                    # NOTE: translated to English.
                    optimal_params[strategy_alias] = [indicator_param]
                else:
                    self.logger.warning(f"entry_params 長度不足: {len(entry_params)} < {i+1}")

        # NOTE: translated to English.
        exit_indicators = pair.get("exit", [])
        if not exit_indicators:
            self.logger.warning("條件配對中缺少 exit 指標")
        else:
            for i, exit_indicator in enumerate(exit_indicators):
                strategy_alias = f"{exit_indicator}_strategy_{strategy_idx_1based}"
                if i < len(exit_params):
                    param_dict = exit_params[i]
                    # NOTE: translated to English.
                    from backtester.IndicatorParams_backtester import IndicatorParams

                    indicator_type = param_dict.get("indicator_type", "MA")
                    indicator_param = IndicatorParams(indicator_type)

                    # NOTE: translated to English.
                    for key, value in param_dict.items():
                        if key != "indicator_type" and key not in ["trading_params"]:
                            indicator_param.add_param(key, value)

                    optimal_params[strategy_alias] = [indicator_param]
                else:
                    self.logger.warning(f"exit_params 長度不足: {len(exit_params)} < {i+1}")

        if not optimal_params:
            self.logger.warning("未能提取任何參數")
        return optimal_params

    def _calculate_train_metrics(
        self, optimal_result: Dict[str, Any], objective: str
    ) -> Optional[Dict[str, Any]]:
        """
        計算訓練集（IS）績效指標

        Args:
            optimal_result: 最優回測結果
            objective: 優化目標

        Returns:
            Optional[Dict[str, Any]]: 訓練集績效指標
        """
        try:
            from metricstracker.MetricsCalculator_metricstracker import (
                MetricsCalculatorMetricTracker,
            )

            if "records" not in optimal_result:
                return None

            records = optimal_result["records"]
            if not isinstance(records, pd.DataFrame) or records.empty:
                return None

            metrics_calc = MetricsCalculatorMetricTracker(
                records,
                time_unit=365,
                risk_free_rate=0.04,
            )

            metrics = {
                "sharpe": metrics_calc.sharpe(),
                "calmar": metrics_calc.calmar(),
                "sortino": metrics_calc.sortino(),
                "total_return": metrics_calc.total_return(),
                "max_drawdown": metrics_calc.max_drawdown(),
            }

            return metrics

        except Exception as e:
            self.logger.warning(f"計算訓練集績效指標失敗: {e}")
            return None

    def get_last_failure_reason(self) -> Optional[str]:
        """
        獲取最後一次失敗的原因

        Returns:
            Optional[str]: 失敗原因
        """
        return getattr(self, "_last_failure_reason", None)

    def get_last_grid_region(self, strategy_idx: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        獲取最後一次優化的九宮格區域信息

        Args:
            strategy_idx: 可選的策略索引，如果提供則返回該策略的 grid_region

        Returns:
            Optional[Dict[str, Any]]: 九宮格區域信息
        """
        # NOTE: translated to English.
        if strategy_idx is not None:
            last_grid_regions = getattr(self, "_last_grid_regions", {})
            return last_grid_regions.get(strategy_idx)

        # NOTE: translated to English.
        return getattr(self, "_last_grid_region", None)

    def get_all_grid_regions(self) -> Dict[int, Dict[str, Any]]:
        """
        獲取所有 condition_pair 的九宮格區域信息

        Returns:
            Dict[int, Dict[str, Any]]: 所有 condition_pair 的 grid_regions
        """
        return getattr(self, "_last_grid_regions", {})

    def _find_best_grid_region(
        self,
        single_optimal_params: Dict[str, Any],
        all_results: List[Dict[str, Any]],
        objective: str,
        silent: bool = True,
        strategy_idx: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        找到總和最大的九宮格區域（使用二維積分圖像加速）

        使用二維積分圖像（2D Prefix Sum）來快速計算所有可能的3x3區域的總和，
        時間複雜度：O(N²)，空間複雜度：O(N²)

        Args:
            single_optimal_params: 單一最優參數（未使用，保留以保持接口兼容性）
            all_results: 所有回測結果
            objective: 優化目標
            silent: 是否靜默模式

        Returns:
            Optional[Dict[str, Any]]: 最佳九宮格區域信息
        """
        try:
            # NOTE: translated to English.
            self.logger.info("構建參數-績效二維矩陣...")
            matrix, param_mapping, result_mapping = self._build_parameter_matrix(
                all_results, objective, strategy_idx=strategy_idx
            )

            if matrix is None or matrix.size == 0:
                self.logger.warning("無法構建參數矩陣")
                return None

            rows, cols = matrix.shape
            self.logger.info(f"參數矩陣大小: {rows} x {cols}")

            # NOTE: translated to English.
            # prefix[i+1][j+1] = sum of all values in rectangle [0,0] to [i,j]
            self.logger.info("構建積分圖像...")
            prefix = np.zeros((rows + 1, cols + 1), dtype=np.float64)

            # NOTE: translated to English.
            for i in range(rows):
                for j in range(cols):
                    prefix[i + 1][j + 1] = (
                        matrix[i][j]
                        + prefix[i][j + 1]
                        + prefix[i + 1][j]
                        - prefix[i][j]
                    )

            # NOTE: translated to English.
            self.logger.info("搜索最佳3x3區域...")
            best_sum = float("-inf")
            best_i, best_j = -1, -1
            grid_size = 3  # NOTE: translated to English.

            # NOTE: translated to English.
            for i in range(rows - grid_size + 1):
                for j in range(cols - grid_size + 1):
                    # NOTE: translated to English.
                    # sum = prefix[i+k][j+k] - prefix[i+k][j] - prefix[i][j+k] + prefix[i][j]
                    region_sum = (
                        prefix[i + grid_size][j + grid_size]
                        - prefix[i + grid_size][j]
                        - prefix[i][j + grid_size]
                        + prefix[i][j]
                    )

                    if region_sum > best_sum:
                        best_sum = region_sum
                        best_i, best_j = i, j

            # NOTE: translated to English.
            del prefix

            if best_i == -1 or best_j == -1:
                self.logger.warning("未找到有效的3x3區域")
                return None

            self.logger.info(
                f"找到最佳3x3區域: 位置=({best_i}, {best_j}), 總和={best_sum:.4f}, 平均={best_sum/9:.4f}"
            )

            # NOTE: translated to English.
            best_params_list = []
            best_results_list = []
            best_metrics_list = []

            for di in range(grid_size):
                for dj in range(grid_size):
                    i_idx = best_i + di
                    j_idx = best_j + dj

                    # NOTE: translated to English.
                    if (i_idx, j_idx) in param_mapping:
                        params = param_mapping[(i_idx, j_idx)]
                        result = result_mapping.get((i_idx, j_idx))

                        if params and result:
                            best_params_list.append(params)
                            best_results_list.append(result)

                            # NOTE: translated to English.
                            full_metrics = self._calculate_individual_full_metrics(result)
                            metric = self._calculate_metric_from_result(result, objective)

                            best_metrics_list.append({
                                "params": params,
                                "result": result,
                                "metric": metric,
                                "full_metrics": full_metrics,
                            })

            if not best_params_list:
                self.logger.warning("無法從最佳區域構建參數組合")
                return None

            # NOTE: translated to English.
            avg_metric = best_sum / len(best_metrics_list) if best_metrics_list else 0.0
            individual_full_metrics_list = [m["full_metrics"] for m in best_metrics_list]

            best_region = {
                "params": best_params_list[0],  # NOTE: translated to English.
                "all_params": best_params_list,  # NOTE: translated to English.
                "avg_metric": avg_metric,
                "individual_metrics": [m["metric"] for m in best_metrics_list],
                "individual_full_metrics": individual_full_metrics_list,
                "train_metrics": self._calculate_grid_train_metrics(best_metrics_list, objective),
            }

            self.logger.info(
                f"最終選擇的最佳九宮格區域: 總和={best_sum:.4f}, 平均={avg_metric:.4f}"
            )

            return best_region

        except Exception as e:
            self.logger.error(f"查找最佳九宮格區域失敗: {e}", exc_info=True)
            return None

    def _get_all_param_keys(self, param_dict: Dict[str, Any]) -> List[str]:
        """
        從參數字典中動態獲取所有參數鍵（排除元數據）

        Args:
            param_dict: 參數字典（從 to_dict() 獲取）

        Returns:
            List[str]: 參數鍵列表
        """
        # NOTE: translated to English.
        excluded_keys = {"indicator_type", "strat_idx", "ma_type", "mode"}
        param_keys = [k for k in param_dict.keys() if k not in excluded_keys]
        return param_keys

    def _get_param_key(self, indicator_type: str, param_dict: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """
        獲取參數鍵名（動態方式）

        優先從 param_dict 中動態獲取，如果沒有提供則使用默認映射

        Args:
            indicator_type: 指標類型
            param_dict: 可選的參數字典（從 to_dict() 獲取）

        Returns:
            str: 參數鍵名，如果無法確定則返回 None
        """
        # NOTE: translated to English.
        if param_dict:
            param_keys = self._get_all_param_keys(param_dict)
            if param_keys:
                # NOTE: translated to English.
                return param_keys[0]

        # NOTE: translated to English.
        indicator_type = indicator_type.upper()
        if indicator_type.startswith("MA"):
            return "period"
        elif indicator_type.startswith("BOLL"):
            return "ma_length"
        elif indicator_type.startswith("HL"):
            return "n_length"
        elif indicator_type.startswith("PERC"):
            return "window"
        elif indicator_type.startswith("VALUE"):
            return "n_length"
        return "period"

    def _map_config_key_to_param_key(self, config_key: str, param_dict: Dict[str, Any]) -> Optional[str]:
        """
        將配置中的參數鍵名映射到 param_dict 中的鍵名

        例如：
        - config_key: "m_range" -> param_key: "m_length" (對於 HL)
        - config_key: "n_range" -> param_key: "n_length" (對於 HL)
        - config_key: "window_range" -> param_key: "window" (對於 PERC)
        - config_key: "ma_range" -> param_key: "ma_length" (對於 BOLL) 或 "period" (對於 MA)

        Args:
            config_key: 配置中的鍵名（如 "m_range", "n_range", "window_range", "ma_range"）
            param_dict: 參數字典（從 to_dict() 獲取）

        Returns:
            Optional[str]: 映射後的鍵名，如果無法映射則返回 None
        """
        # NOTE: translated to English.
        key_mapping = {
            "m_range": "m_length",
            "n_range": "n_length",
            "window_range": "window",
            "percentile_range": "percentile",
            "ma_range": None,  # NOTE: translated to English.
        }

        # NOTE: translated to English.
        if config_key in key_mapping:
            mapped_key = key_mapping[config_key]
            if mapped_key and mapped_key in param_dict:
                return mapped_key

        # NOTE: translated to English.
        if config_key == "ma_range":
            indicator_type = param_dict.get("indicator_type", "")
            if indicator_type == "MA":
                if "period" in param_dict:
                    return "period"
            elif indicator_type == "BOLL":
                if "ma_length" in param_dict:
                    return "ma_length"

        # NOTE: translated to English.
        if config_key.endswith("_range"):
            base_key = config_key.replace("_range", "")
            if base_key in param_dict:
                return base_key

        return None

    def _build_parameter_matrix(
        self, all_results: List[Dict[str, Any]], objective: str, strategy_idx: Optional[int] = None
    ) -> Tuple[Optional[np.ndarray], Dict[Tuple[int, int], Dict[str, Any]], Dict[Tuple[int, int], Dict[str, Any]]]:
        """
        構建參數-績效二維矩陣

        從所有回測結果中提取MA1和MA4的參數值，構建一個二維矩陣，
        其中matrix[i][j]對應MA1=param1_list[i], MA4=param2_list[j]的metric值

        Args:
            all_results: 所有回測結果
            objective: 優化目標

        Returns:
            Tuple[Optional[np.ndarray], Dict, Dict]:
            - 二維矩陣（如果無法構建則返回None）
            - 參數映射：{(i, j): params_dict}
            - 結果映射：{(i, j): result_dict}
        """
        import numpy as np
        from metricstracker.MetricsCalculator_metricstracker import (
            MetricsCalculatorMetricTracker,
        )

        condition_pairs = self.backtester_config.get("condition_pairs", [])
        if not condition_pairs:
            return None, {}, {}

        # NOTE: translated to English.
        if strategy_idx is None:
            strategy_idx = 0
        if strategy_idx >= len(condition_pairs):
            strategy_idx = 0

        pair = condition_pairs[strategy_idx]
        entry_indicators = pair.get("entry", [])
        exit_indicators = pair.get("exit", [])

        if not entry_indicators or not exit_indicators:
            return None, {}, {}

        # NOTE: translated to English.
        param1_values = set()  # NOTE: translated to English.
        param2_values = set()  # NOTE: translated to English.
        param_data = {}  # {(param1, param2): (metric, result, params)}

        for result in all_results:
            # NOTE: translated to English.
            if result.get("error") is not None:
                continue

            # NOTE: translated to English.
            if "records" not in result:
                continue

            records = result["records"]
            if not isinstance(records, pd.DataFrame) or records.empty:
                continue

            # NOTE: translated to English.
            if "Trade_action" not in records.columns:
                continue

            trade_count = (records["Trade_action"] == 1).sum()
            if trade_count == 0:
                continue

            # NOTE: translated to English.
            if "params" not in result:
                continue

            params = result["params"]
            entry_params = params.get("entry", [])
            exit_params = params.get("exit", [])

            if not entry_params or not exit_params:
                continue

            # NOTE: translated to English.
            entry_param_dict = entry_params[0] if isinstance(entry_params[0], dict) else entry_params[0].to_dict() if hasattr(entry_params[0], "to_dict") else {}
            exit_param_dict = exit_params[0] if isinstance(exit_params[0], dict) else exit_params[0].to_dict() if hasattr(exit_params[0], "to_dict") else {}

            # NOTE: translated to English.
            raw_indicator_params = self.backtester_config.get("indicator_params", {})
            strategy_idx_1based = strategy_idx + 1

            entry_indicator_name = entry_indicators[0] if entry_indicators else ""
            exit_indicator_name = exit_indicators[0] if exit_indicators else ""
            entry_strategy_alias = f"{entry_indicator_name}_strategy_{strategy_idx_1based}"
            exit_strategy_alias = f"{exit_indicator_name}_strategy_{strategy_idx_1based}"

            # NOTE: translated to English.
            entry_config = raw_indicator_params.get(entry_strategy_alias, {})
            exit_config = raw_indicator_params.get(exit_strategy_alias, {})

            entry_variable_keys = [k for k in entry_config.keys() if self._is_variable_param(entry_config[k])]
            exit_variable_keys = [k for k in exit_config.keys() if self._is_variable_param(exit_config[k])]

            # NOTE: translated to English.
            if not entry_variable_keys:
                entry_variable_keys = self._get_all_param_keys(entry_param_dict)
            if not exit_variable_keys:
                exit_variable_keys = self._get_all_param_keys(exit_param_dict)

            if len(entry_variable_keys) == 0 or len(exit_variable_keys) == 0:
                self.logger.warning(
                    f"無法獲取可變參數鍵: entry_variable_keys={entry_variable_keys}, "
                    f"exit_variable_keys={exit_variable_keys}, "
                    f"entry_param_dict={entry_param_dict}, exit_param_dict={exit_param_dict}"
                )
                continue

            # NOTE: translated to English.
            param1_key = entry_variable_keys[0]
            param2_key = exit_variable_keys[0]

            # NOTE: translated to English.
            # NOTE: translated to English.
            param1_key_mapped = self._map_config_key_to_param_key(param1_key, entry_param_dict)
            param2_key_mapped = self._map_config_key_to_param_key(param2_key, exit_param_dict)

            if param1_key_mapped:
                param1_key = param1_key_mapped
            if param2_key_mapped:
                param2_key = param2_key_mapped

            param1_value = entry_param_dict.get(param1_key)
            param2_value = exit_param_dict.get(param2_key)

            if param1_value is None or param2_value is None:
                # NOTE: translated to English.
                self.logger.debug(
                    f"參數值為None: entry_indicator={entry_param_dict.get('indicator_type', 'N/A')}, "
                    f"param1_key={param1_key}, param1_value={param1_value}, "
                    f"exit_indicator={exit_param_dict.get('indicator_type', 'N/A')}, param2_key={param2_key}, "
                    f"param2_value={param2_value}, entry_param_dict={entry_param_dict}, "
                    f"exit_param_dict={exit_param_dict}"
                )
                continue

            # NOTE: translated to English.
            try:
                metrics_calc = MetricsCalculatorMetricTracker(
                    records,
                    time_unit=365,
                    risk_free_rate=0.04,
                )

                if objective == "sharpe":
                    metric_value = metrics_calc.sharpe()
                elif objective == "calmar":
                    metric_value = metrics_calc.calmar()
                else:
                    continue

                # NOTE: translated to English.
                if pd.isna(metric_value) or metric_value == float("inf") or metric_value == float("-inf"):
                    continue

                param1_values.add(param1_value)
                param2_values.add(param2_value)

                # NOTE: translated to English.
                params_dict = self._extract_optimal_params(result)

                param_data[(param1_value, param2_value)] = (metric_value, result, params_dict)

            except Exception as e:
                self.logger.warning(f"計算績效指標失敗: {e}")
                continue

        if not param1_values or not param2_values:
            self.logger.warning("未找到有效的參數值")
            return None, {}, {}

        # NOTE: translated to English.
        param1_list = sorted(param1_values)
        param2_list = sorted(param2_values)

        param1_to_idx = {val: idx for idx, val in enumerate(param1_list)}
        param2_to_idx = {val: idx for idx, val in enumerate(param2_list)}

        # NOTE: translated to English.
        rows = len(param1_list)
        cols = len(param2_list)
        matrix = np.full((rows, cols), np.nan, dtype=np.float64)

        param_mapping = {}  # {(i, j): params_dict}
        result_mapping = {}  # {(i, j): result_dict}

        for (param1, param2), (metric_value, result, params_dict) in param_data.items():
            i = param1_to_idx[param1]
            j = param2_to_idx[param2]
            matrix[i][j] = metric_value
            param_mapping[(i, j)] = params_dict
            result_mapping[(i, j)] = result

        # NOTE: translated to English.
        matrix = np.where(np.isnan(matrix), float("-inf"), matrix)

        self.logger.info(
            f"構建參數矩陣完成: {rows}x{cols}, "
            f"MA1範圍={min(param1_list)}-{max(param1_list)}, "
            f"MA4範圍={min(param2_list)}-{max(param2_list)}, "
            f"有效數據點={np.sum(~np.isinf(matrix))}"
        )

        return matrix, param_mapping, result_mapping

    def _get_step_size(self, range_config: Dict[str, Any], indicator_type: str) -> Optional[int]:
        """從配置中獲取步長"""
        indicator_type = indicator_type.upper()

        if indicator_type.startswith("MA"):
            ma_range = range_config.get("ma_range", "")
            if ma_range:
                parts = ma_range.split(":")
                if len(parts) >= 3:
                    return int(parts[2])
        elif indicator_type.startswith("BOLL"):
            ma_range = range_config.get("ma_range", "")
            if ma_range:
                parts = ma_range.split(":")
                if len(parts) >= 3:
                    return int(parts[2])
        elif indicator_type.startswith("HL"):
            n_range = range_config.get("n_range", "")
            if n_range:
                parts = n_range.split(":")
                if len(parts) >= 3:
                    return int(parts[2])
        elif indicator_type.startswith("PERC"):
            window_range = range_config.get("window_range", "")
            if window_range:
                parts = window_range.split(":")
                if len(parts) >= 3:
                    return int(parts[2])
        elif indicator_type.startswith("VALUE"):
            n_range = range_config.get("n_range", "")
            if n_range:
                parts = n_range.split(":")
                if len(parts) >= 3:
                    return int(parts[2])

        return None

    def _build_grid_param_config(
        self,
        entry_indicators: List[str],
        exit_indicators: List[str],
        strategy_idx: int,
        entry_values: Tuple,
        exit_values: Tuple,
        entry_neighbors: List,
        exit_neighbors: List,
    ) -> Optional[Dict[str, Any]]:
        """構建九宮格參數配置"""
        from backtester.IndicatorParams_backtester import IndicatorParams

        grid_params = {}

        # NOTE: translated to English.
        for i, entry_indicator in enumerate(entry_indicators):
            strategy_alias = f"{entry_indicator}_strategy_{strategy_idx}"

            if i < len(entry_neighbors):
                _, _, optimal_param = entry_neighbors[i]

                # NOTE: translated to English.
                indicator_type = optimal_param.indicator_type
                indicator_param = IndicatorParams(indicator_type)

                # NOTE: translated to English.
                for key in optimal_param.params:
                    value = optimal_param.get_param(key)
                    if value is not None:
                        indicator_param.add_param(key, value)

                # NOTE: translated to English.
                if i < len(entry_values):
                    param_key = self._get_param_key(entry_indicator)
                    indicator_param.add_param(param_key, entry_values[i])

                grid_params[strategy_alias] = [indicator_param]

        # NOTE: translated to English.
        for i, exit_indicator in enumerate(exit_indicators):
            strategy_alias = f"{exit_indicator}_strategy_{strategy_idx}"

            if i < len(exit_neighbors):
                _, _, optimal_param = exit_neighbors[i]

                indicator_type = optimal_param.indicator_type
                indicator_param = IndicatorParams(indicator_type)

                for key in optimal_param.params:
                    value = optimal_param.get_param(key)
                    if value is not None:
                        indicator_param.add_param(key, value)

                if i < len(exit_values):
                    param_key = self._get_param_key(exit_indicator)
                    indicator_param.add_param(param_key, exit_values[i])

                grid_params[strategy_alias] = [indicator_param]

        return grid_params if grid_params else None

    def _calculate_metric_from_result(
        self, result: Dict[str, Any], objective: str
    ) -> Optional[float]:
        """從結果中計算績效指標"""
        try:
            from metricstracker.MetricsCalculator_metricstracker import (
                MetricsCalculatorMetricTracker,
            )

            if "records" not in result:
                return None

            records = result["records"]
            if not isinstance(records, pd.DataFrame) or records.empty:
                return None

            metrics_calc = MetricsCalculatorMetricTracker(
                records,
                time_unit=365,
                risk_free_rate=0.04,
            )

            if objective == "sharpe":
                metric_value = metrics_calc.sharpe()
            elif objective == "calmar":
                metric_value = metrics_calc.calmar()
            else:
                return None

            if pd.isna(metric_value) or metric_value == float("inf") or metric_value == float("-inf"):
                return None

            return metric_value

        except Exception as e:
            self.logger.warning(f"計算績效指標失敗: {e}")
            return None

    def _calculate_individual_full_metrics(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        計算單個結果的完整績效指標

        Args:
            result: 回測結果

        Returns:
            Optional[Dict[str, Any]]: 包含 sharpe、calmar、sortino、total_return 的字典
        """
        try:
            from metricstracker.MetricsCalculator_metricstracker import (
                MetricsCalculatorMetricTracker,
            )

            if "records" not in result:
                return None

            records = result["records"]
            if not isinstance(records, pd.DataFrame) or records.empty:
                return None

            metrics_calc = MetricsCalculatorMetricTracker(
                records,
                time_unit=365,
                risk_free_rate=0.04,
            )

            sharpe = metrics_calc.sharpe()
            calmar = metrics_calc.calmar()
            sortino = metrics_calc.sortino()
            total_return = metrics_calc.total_return()
            max_drawdown = metrics_calc.max_drawdown()

            # NOTE: translated to English.
            if pd.isna(sharpe) or sharpe == float("inf") or sharpe == float("-inf"):
                sharpe = None
            if pd.isna(calmar) or calmar == float("inf") or calmar == float("-inf"):
                calmar = None
            if pd.isna(sortino) or sortino == float("inf") or sortino == float("-inf"):
                sortino = None
            if pd.isna(total_return) or total_return == float("inf") or total_return == float("-inf"):
                total_return = None
            if pd.isna(max_drawdown) or max_drawdown == float("inf") or max_drawdown == float("-inf"):
                max_drawdown = None

            return {
                "sharpe": sharpe,
                "calmar": calmar,
                "sortino": sortino,
                "total_return": total_return,
                "max_drawdown": max_drawdown,
            }
        except Exception as e:
            self.logger.warning(f"計算完整績效指標失敗: {e}")
            return None

    def _calculate_grid_train_metrics(
        self, grid_metrics: List[Dict[str, Any]], objective: str
    ) -> Optional[Dict[str, Any]]:
        """計算九宮格區域的平均訓練集績效指標"""
        try:
            from metricstracker.MetricsCalculator_metricstracker import (
                MetricsCalculatorMetricTracker,
            )

            all_sharpe = []
            all_calmar = []
            all_sortino = []
            all_returns = []
            all_equity_curves = []

            for grid_metric in grid_metrics:
                result = grid_metric["result"]
                if "records" not in result:
                    continue

                records = result["records"]
                if not isinstance(records, pd.DataFrame) or records.empty:
                    continue

                metrics_calc = MetricsCalculatorMetricTracker(
                    records,
                    time_unit=365,
                    risk_free_rate=0.04,
                )

                sharpe = metrics_calc.sharpe()
                calmar = metrics_calc.calmar()
                sortino = metrics_calc.sortino()
                total_return = metrics_calc.total_return()

                if not pd.isna(sharpe) and sharpe != float("inf") and sharpe != float("-inf"):
                    all_sharpe.append(sharpe)
                if not pd.isna(calmar) and calmar != float("inf") and calmar != float("-inf"):
                    all_calmar.append(calmar)
                if not pd.isna(sortino) and sortino != float("inf") and sortino != float("-inf"):
                    all_sortino.append(sortino)
                if not pd.isna(total_return) and total_return != float("inf") and total_return != float("-inf"):
                    all_returns.append(total_return)

                # NOTE: translated to English.
                if "Equity_value" in records.columns:
                    all_equity_curves.append(records["Equity_value"].values)

            if not all_sharpe and not all_calmar:
                return None

            # NOTE: translated to English.
            avg_sharpe = sum(all_sharpe) / len(all_sharpe) if all_sharpe else None
            avg_calmar = sum(all_calmar) / len(all_calmar) if all_calmar else None
            avg_sortino = sum(all_sortino) / len(all_sortino) if all_sortino else None
            avg_return = sum(all_returns) / len(all_returns) if all_returns else None

            # NOTE: translated to English.
            avg_equity = None
            if all_equity_curves:
                import numpy as np
                min_length = min(len(eq) for eq in all_equity_curves)
                truncated_curves = [eq[:min_length] for eq in all_equity_curves]
                avg_equity = np.mean(truncated_curves, axis=0)

            metrics = {
                "sharpe": avg_sharpe,
                "calmar": avg_calmar,
                "sortino": avg_sortino,
                "total_return": avg_return,
                "equity_curve": avg_equity,
                "param_count": len(grid_metrics),
            }

            return metrics

        except Exception as e:
            self.logger.warning(f"計算九宮格訓練集績效指標失敗: {e}")
            return None
