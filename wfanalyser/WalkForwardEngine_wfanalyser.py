"""
WalkForwardEngine_wfanalyser.py

【功能說明】
------------------------------------------------------------
本模組為 WFA 的核心引擎，負責執行 Walk-Forward Analysis 流程，
包括窗口劃分、參數優化、回測執行等。

【流程與數據流】
------------------------------------------------------------
- 主流程：載入數據 → 劃分窗口 → 參數優化 → 測試窗口回測 → 收集結果
- 數據流：配置數據 → 窗口劃分 → 優化結果 → 回測結果 → WFA 結果

【維護與擴充重點】
------------------------------------------------------------
- 窗口劃分邏輯需要確保數據完整性
- 參數優化需要與 ParameterOptimizer 協調
- 回測執行需要重用 NodeIR/native runtime

【常見易錯點】
------------------------------------------------------------
- 窗口劃分錯誤導致數據不完整
- 參數優化結果未正確傳遞到測試窗口
- 結果收集格式不一致

【範例】
------------------------------------------------------------
- 執行 WFA：engine = WalkForwardEngine(config_data, logger); results = engine.run()

【與其他模組的關聯】
------------------------------------------------------------
- 調用 DataLoaderWFAAnalyser 載入數據
- 調用 ParameterOptimizer 進行參數優化
- 調用 NodeIR/native runtime 執行回測
- 調用 metricstracker 計算績效指標

【版本與變更記錄】
------------------------------------------------------------
- v1.0: 初始版本，基本 WFA 功能

【參考】
------------------------------------------------------------
- Base_wfanalyser.py: WFA 框架核心控制器
- ParameterOptimizer_wfanalyser.py: 參數優化器
- NodeIRExecutor_backtester.py: 向量化回測引擎
- wfanalyser/README.md: WFA 模組詳細說明
"""

import logging
import math
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from wfanalyser.DataLoader_wfanalyser import DataLoaderWFAAnalyser
from utils import show_error, show_info, show_success, show_warning
from wfanalyser.ParameterOptimizer_wfanalyser import ParameterOptimizer
from wfanalyser.utils.ConsoleUtils_utils_wfanalyser import get_console

console = get_console()


def _removed_public_wfa_engine(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("Public legacy WFA runtime path has been removed; use strategy_mode=semantic.")


class WalkForwardEngine:
    """
    WFA 核心引擎

    負責執行 Walk-Forward Analysis 流程，包括窗口劃分、
    參數優化、回測執行等。
    """

    def __init__(self, config_data: Any, logger: Optional[logging.Logger] = None):
        """
        初始化 WalkForwardEngine

        Args:
            config_data: WFA 配置數據對象
            logger: 日誌記錄器
        """
        self.config_data = config_data
        self.logger = logger or logging.getLogger("lo2cin4bt.wfanalyser.engine")
        self.data: Optional[pd.DataFrame] = None
        self.frequency: Optional[str] = None
        self.wfa_config = config_data.wfa_config
        self.results: List[Dict[str, Any]] = []
        self.windows: List[Dict[str, Any]] = []  # NOTE: translated to English.
        self.contract_audit: Dict[str, Any] = {}
        self._shared_runtime_cache: Dict[str, Any] = {}

    def run(self) -> Optional[Dict[str, Any]]:
        """
        執行 WFA 流程

        Returns:
            Optional[Dict[str, Any]]: WFA 結果，如果執行失敗則返回 None
        """
        try:
            if self._should_use_unified_portfolio_wfa():
                return self._run_unified_portfolio_wfa()
            show_info("WFANALYSER", "🚀 開始執行 Walk-Forward Analysis")

            # NOTE: translated to English.
            self._load_data()
            if not self._enforce_node_ir_only_policy():
                return None
            self._prepare_shared_strategy_runtime_cache()

            if self.data is None:
                show_error("WFANALYSER", "數據載入失敗，無法繼續執行 WFA")
                return None

            # NOTE: translated to English.
            windows = self._divide_windows()
            self.windows = windows  # NOTE: translated to English.

            if not windows:
                show_error("WFANALYSER", "窗口劃分失敗，無法繼續執行 WFA")
                return None

            # NOTE: translated to English.
            mode = self.wfa_config.get("mode", "standard")
            show_success("WFANALYSER", f"成功劃分 {len(windows)} 個窗口 (模式: {mode})")

            # NOTE: translated to English.
            # NOTE: translated to English.

            # NOTE: translated to English.
            all_window_results = []
            all_window_status = []

            # NOTE: translated to English.
            for window_idx, window in enumerate(windows, 1):
                console.print(
                    f"  [dim]處理窗口 {window_idx}/{len(windows)}[/dim]"
                )

                # NOTE: translated to English.
                window_result, status = self._process_window(
                    window, window_idx, len(windows)
                )

                all_window_status.append(status)
                if window_result:
                    all_window_results.append(window_result)

            # NOTE: translated to English.
            # NOTE: translated to English.
            final_results = self._collect_results(all_window_results)

            # NOTE: translated to English.
            if len(all_window_results) == 0:
                # NOTE: translated to English.
                failure_summary = []
                failure_summary.append(f"⚠️ 所有 {len(windows)} 個窗口處理都失敗")

                # NOTE: translated to English.
                failure_reasons = {}
                for status in all_window_status:
                    # NOTE: translated to English.
                    for objective in ["sharpe", "calmar"]:
                        reason = status.get(f"{objective}_failure_reason")
                        if reason:
                            if reason not in failure_reasons:
                                failure_reasons[reason] = 0
                            failure_reasons[reason] += 1

                    # NOTE: translated to English.
                    if status.get("error"):
                        error_msg = status.get("error", "未知錯誤")
                        if error_msg not in failure_reasons:
                            failure_reasons[error_msg] = 0
                        failure_reasons[error_msg] += 1

                if failure_reasons:
                    failure_summary.append("\n失敗原因統計：")
                    for reason, count in failure_reasons.items():
                        failure_summary.append(f"  • {reason}: {count} 次")
                else:
                    failure_summary.append("\n未找到具體失敗原因，請檢查日誌文件")

                # NOTE: translated to English.
                failure_summary.append("\n前 3 個窗口狀態：")
                for idx, status in enumerate(all_window_status[:3], 1):
                    window_id = status.get("window_id", f"窗口 {idx}")
                    train_size = status.get("train_size", "N/A")
                    test_size = status.get("test_size", "N/A")
                    sharpe_status = status.get("sharpe_status", "未執行")
                    calmar_status = status.get("calmar_status", "未執行")
                    failure_summary.append(
                        f"  {window_id}: 訓練集={train_size}, 測試集={test_size}, "
                        f"Sharpe={sharpe_status}, Calmar={calmar_status}"
                    )

                show_warning("WFANALYSER", "\n".join(failure_summary))
                self.logger.warning(f"WFA 執行完成但所有窗口都失敗: {failure_reasons}")
            else:
                show_success("WFANALYSER",
                    f"WFA 執行完成\n"
                    f"   窗口數: {len(windows)}\n"
                    f"   成功處理: {len(all_window_results)} 個窗口結果"
                )

            return final_results

        except Exception as e:
            show_error("WFANALYSER", f"WFA 執行失敗: {e}")
            self.logger.error(f"WFA 執行失敗: {e}")
            return None

    def _should_use_unified_portfolio_wfa(self) -> bool:
        """Return true when config requests the unified portfolio WFA runtime."""
        backtester_cfg = getattr(self.config_data, "backtester_config", {})
        if not isinstance(backtester_cfg, dict):
            return False
        mode = str(backtester_cfg.get("strategy_mode", "")).strip().lower()
        if mode == "multi_asset_portfolio":
            return True
        engine = str(self.wfa_config.get("engine") or self.wfa_config.get("runtime") or "").strip().lower()
        return engine in {"unified_portfolio", "unified_portfolio_wfa"}

    def _run_unified_portfolio_wfa(self) -> Dict[str, Any]:
        """Run WFA through the unified portfolio engine and export semantic artifacts."""
        from autorunner.BacktestRunner_autorunner import BacktestRunnerAutorunner
        from wfanalyser.UnifiedPortfolioWFAExporter_wfanalyser import UnifiedPortfolioWFAExporter
        from wfanalyser.UnifiedPortfolioWFARunner_wfanalyser import UnifiedPortfolioWFARunner

        backtester_cfg = getattr(self.config_data, "backtester_config", {}) or {}
        config_file_path = (
            backtester_cfg.get("__config_file_path")
            or self.wfa_config.get("__config_file_path")
            or getattr(self.config_data, "file_path", None)
        )
        portfolio_config = dict(
            backtester_cfg.get("portfolio_config")
            or backtester_cfg.get("strategy_config")
            or {}
        )
        if not portfolio_config:
            raise ValueError(
                "unified portfolio WFA requires backtester.portfolio_config or backtester.strategy_config"
            )

        runner = BacktestRunnerAutorunner(logger=self.logger)
        market_data = runner._load_multi_asset_market_data(
            backtester_cfg.get("market_data", {}),
            config_file_path=config_file_path if isinstance(config_file_path, str) else None,
        )
        unified_wfa_config = self._normalized_unified_wfa_config()
        result = UnifiedPortfolioWFARunner(
            market_data=market_data,
            strategy_config=portfolio_config,
            wfa_config=unified_wfa_config,
        ).run()

        output_dir = self._unified_wfa_output_dir(config_file_path)
        if config_file_path:
            default_run_id = Path(str(config_file_path)).stem
        else:
            default_run_id = str(portfolio_config.get("strategy_id") or "unified_portfolio_wfa")
        run_id = str(self.wfa_config.get("run_id") or default_run_id)
        outputs_cfg = self.wfa_config.get("outputs", {}) if isinstance(self.wfa_config.get("outputs"), dict) else {}
        exported_files = UnifiedPortfolioWFAExporter(
            result=result,
            output_dir=output_dir,
            run_id=run_id,
            export_diagnostics=bool(outputs_cfg.get("candidate_diagnostics", True)),
        ).export()

        payload = {
            "wfa_config": unified_wfa_config,
            "total_windows": int(result.metadata.get("window_count", 0)),
            "selected_optimum": result.selected_optimum,
            "candidate_diagnostics": result.candidate_diagnostics,
            "window_backtests": result.window_backtests,
            "metadata": result.metadata,
            "exported_files": exported_files,
            "contract_audit": {
                "runtime": "unified_portfolio_wfa",
                "row_contract": "selected_optimum_per_window",
            },
        }
        self.results = [payload]
        return payload

    def _normalized_unified_wfa_config(self) -> Dict[str, Any]:
        """Translate legacy WFA keys into the unified WFA runner shape."""
        out = dict(self.wfa_config or {})
        windowing = dict(out.get("windowing") or {})
        for key in (
            "target_window_count",
            "step_size",
            "train_size",
            "test_size",
            "train_ratio",
            "test_ratio",
            "size_mode",
            "window_size_mode",
        ):
            if key in out and key not in windowing:
                windowing[key] = out.get(key)
        if "train_ratio" not in windowing and "train_set_percentage" in out:
            windowing["train_ratio"] = out.get("train_set_percentage")
        if "test_ratio" not in windowing and "test_set_percentage" in out:
            windowing["test_ratio"] = out.get("test_set_percentage")
        out["windowing"] = windowing

        optimizer = dict(out.get("optimizer") or {})
        objectives = (
            out.get("optimization_objectives")
            or out.get("objectives")
            or optimizer.get("objectives")
            or ["sharpe", "calmar"]
        )
        optimizer["objectives"] = objectives
        out["optimizer"] = optimizer
        return out

    def _unified_wfa_output_dir(self, config_file_path: Any) -> Path:
        outputs = self.wfa_config.get("outputs", {}) if isinstance(self.wfa_config.get("outputs"), dict) else {}
        configured = outputs.get("output_dir") or self.wfa_config.get("output_dir")
        if isinstance(configured, str) and configured.strip():
            raw = Path(configured)
            if raw.is_absolute():
                return raw
            if isinstance(config_file_path, str) and config_file_path:
                return (Path(config_file_path).parent / raw).resolve()
            return raw.resolve()
        return Path("outputs") / "wfanalyser"

    def _enforce_node_ir_only_policy(self) -> bool:
        """Block legacy runtime in WFA when node_ir_only policy is enabled."""
        node_ir_only = bool(self.wfa_config.get("node_ir_only", True))
        if not node_ir_only:
            return True
        backtester_cfg = getattr(self.config_data, "backtester_config", {})
        if not isinstance(backtester_cfg, dict):
            return True
        mode = str(backtester_cfg.get("strategy_mode", "auto")).strip().lower()
        if mode == "legacy":
            if not bool(backtester_cfg.get("legacy_opt_in", False)):
                show_error("WFANALYSER", "legacy mode requires backtester.legacy_opt_in=true")
                return False
            show_error(
                "WFANALYSER",
                "WFA node_ir_only policy blocks legacy strategy_mode; use semantic/auto with strategy_contract_path",
            )
            return False
        if mode == "auto":
            has_strategy_contract_path = isinstance(backtester_cfg.get("strategy_contract_path"), str) and bool(
                str(backtester_cfg.get("strategy_contract_path")).strip()
            )
            if not has_strategy_contract_path:
                show_error(
                    "WFANALYSER",
                    "WFA node_ir_only policy requires strategy_contract_path when strategy_mode=auto",
                )
                return False
        return True

    def _load_data(self) -> None:
        """載入數據"""
        try:
            data_loader = DataLoaderWFAAnalyser(logger=self.logger)

            # NOTE: translated to English.
            full_dataloader_config = {
                **self.config_data.dataloader_config,
                "predictor_config": self.config_data.predictor_config,
            }

            self.data = data_loader.load_data(full_dataloader_config)
            self.frequency = data_loader.frequency

            if self.data is not None:
                data_loader.display_loading_summary()

        except Exception as e:
            self.logger.error(f"數據載入失敗: {e}")
            raise

    def _prepare_shared_strategy_runtime_cache(self) -> None:
        """Prepare window-safe shared semantic runtime metadata for WFA windows."""
        self._shared_runtime_cache = {}
        if self.data is None or not isinstance(self.data, pd.DataFrame):
            return
        backtester_cfg = getattr(self.config_data, "backtester_config", {})
        if not isinstance(backtester_cfg, dict):
            return
        mode = str(backtester_cfg.get("strategy_mode", "legacy")).strip().lower()
        has_strategy_contract_path = isinstance(backtester_cfg.get("strategy_contract_path"), str) and bool(str(backtester_cfg.get("strategy_contract_path")).strip())
        if mode not in {"semantic", "auto"}:
            return
        if mode == "auto" and not has_strategy_contract_path:
            return
        try:
            bootstrap = ParameterOptimizer(
                self.data,
                self.frequency,
                self.config_data,
                logger=self.logger,
            )
            if bootstrap.backtester_config.get("strategy_mode") != "semantic":
                return
            execution_plan = getattr(bootstrap, "_execution_plan", None)
            if not isinstance(execution_plan, dict):
                return
            self._shared_runtime_cache = {
                "strategy_mode": "semantic",
                "backtester_config": dict(bootstrap.backtester_config),
                "execution_plan": execution_plan,
                "execution_plan_path": getattr(bootstrap, "_execution_plan_path", None),
            }
            self._merge_contract_audit(self._extract_optimizer_contract_audit(bootstrap))
            self.logger.info(
                "semantic shared runtime cache prepared without full-dataset feature precompute; "
                "window-local execution remains authoritative"
            )
        except Exception as exc:
            self.logger.warning("prepare semantic shared runtime cache failed: %s", exc)

    def _precompute_invariant_strategy_features(self) -> int:
        """Precompute reusable feature columns (currently ta.sma) on full dataset."""
        if self.data is None or not isinstance(self.data, pd.DataFrame):
            return 0
        runtime = self._shared_runtime_cache
        if not isinstance(runtime, dict):
            return 0
        execution_plan = runtime.get("execution_plan")
        backtester_cfg = runtime.get("backtester_config")
        if not isinstance(execution_plan, dict) or not isinstance(backtester_cfg, dict):
            return 0
        feature_dag = execution_plan.get("feature_dag", {})
        if not isinstance(feature_dag, dict) or not feature_dag:
            return 0

        strategy_path = backtester_cfg.get("strategy_contract_path")
        param_domains: Dict[str, Any] = {}
        if isinstance(strategy_path, str) and strategy_path.strip():
            try:
                strategy_obj = self._load_json_file(strategy_path)
                if isinstance(strategy_obj.get("parameter_domains"), dict):
                    param_domains = strategy_obj.get("parameter_domains", {})
            except Exception:
                param_domains = {}

        feature_contract_map = self._build_feature_contract_source_map(backtester_cfg.get("feature_contract_path"))

        from backtester.NodeIRExecutor_backtester import NodeIRExecutorBacktester

        created = 0
        for _, feature_spec in feature_dag.items():
            if not isinstance(feature_spec, dict):
                continue
            feature_name = str(feature_spec.get("feature", "")).lower()
            if feature_name != "ta.sma":
                continue
            source = str(feature_spec.get("source", ""))
            params = feature_spec.get("params", {})
            if not isinstance(params, dict):
                continue
            periods = self._resolve_feature_period_values(params.get("period"), param_domains)
            if not periods:
                continue
            source_series = self._resolve_precompute_source_series(source, feature_contract_map)
            if source_series is None:
                continue
            for period in periods:
                col_name = NodeIRExecutorBacktester._precomputed_feature_column_name(
                    feature=feature_name,
                    source=source,
                    period=period,
                )
                if col_name in self.data.columns:
                    continue
                self.data[col_name] = source_series.rolling(int(period), min_periods=int(period)).mean()
                created += 1
        return created

    @staticmethod
    def _resolve_feature_period_values(period_spec: Any, parameter_domains: Dict[str, Any]) -> List[int]:
        from backtester.NodeIRExecutor_backtester import NodeIRExecutorBacktester

        if isinstance(period_spec, (int, float)):
            return [max(1, int(period_spec))]
        if isinstance(period_spec, dict) and isinstance(period_spec.get("param_ref"), str):
            ref = period_spec.get("param_ref")
            domain_spec = parameter_domains.get(ref, {})
            expanded = NodeIRExecutorBacktester._expand_domain(domain_spec)
            out: List[int] = []
            for raw in expanded:
                if isinstance(raw, (int, float)):
                    value = max(1, int(raw))
                    if value not in out:
                        out.append(value)
            return out
        return []

    @staticmethod
    def _load_json_file(path: str) -> Dict[str, Any]:
        import json

        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError(f"{path} must be a JSON object")
        return payload

    def _build_feature_contract_source_map(self, feature_contract_path: Any) -> Dict[str, str]:
        if not isinstance(feature_contract_path, str) or not feature_contract_path.strip():
            return {}
        try:
            payload = self._load_json_file(feature_contract_path)
        except Exception:
            return {}
        out: Dict[str, str] = {}
        features = payload.get("features", [])
        if not isinstance(features, list):
            return out
        for item in features:
            if not isinstance(item, dict):
                continue
            field = item.get("field")
            source = item.get("source", {})
            if not isinstance(field, str) or not isinstance(source, dict):
                continue
            column = source.get("column")
            if isinstance(column, str) and column:
                out[field] = column
        return out

    def _resolve_precompute_source_series(self, source: str, feature_contract_map: Dict[str, str]) -> Optional[pd.Series]:
        if self.data is None:
            return None
        if source in self.data.columns:
            return pd.to_numeric(self.data[source], errors="coerce")
        mapped = feature_contract_map.get(source)
        if isinstance(mapped, str) and mapped in self.data.columns:
            return pd.to_numeric(self.data[mapped], errors="coerce")
        alias = {
            "price.open": "Open",
            "price.high": "High",
            "price.low": "Low",
            "price.close": "Close",
            "price.volume": "Volume",
        }.get(str(source).lower())
        if isinstance(alias, str) and alias in self.data.columns:
            return pd.to_numeric(self.data[alias], errors="coerce")
        lower_map = {str(col).lower(): col for col in self.data.columns}
        if str(source).lower() in lower_map:
            return pd.to_numeric(self.data[lower_map[str(source).lower()]], errors="coerce")
        return None

    def _divide_windows(self) -> List[Dict[str, Any]]:
        """
        劃分窗口

        Returns:
            List[Dict[str, Any]]: 窗口列表，每個窗口包含 train_start, train_end, test_start, test_end
        """
        if self.data is None:
            return []

        total_points = len(self.data)
        mode = self.wfa_config.get("mode", "standard")
        train_pct = self.wfa_config.get("train_set_percentage", 0.6)
        test_pct = self.wfa_config.get("test_set_percentage", 0.2)
        step_size = self.wfa_config.get("step_size", 30)
        target_window_count = self.wfa_config.get("target_window_count")

        # NOTE: translated to English.
        train_size = math.floor(total_points * train_pct)
        test_size = math.floor(total_points * test_pct)
        available_span = total_points - train_size - test_size
        if isinstance(target_window_count, int) and target_window_count > 1 and available_span > 0:
            step_size = max(1, math.floor(available_span / (target_window_count - 1)))

        windows = []

        if mode == "standard":
            # NOTE: translated to English.
            current_start = 0

            while current_start + train_size + test_size <= total_points:
                train_start = current_start
                train_end = train_start + train_size
                test_start = train_end
                test_end = test_start + test_size

                windows.append(
                    {
                        "window_id": len(windows) + 1,
                        "train_start": train_start,
                        "train_end": train_end,
                        "test_start": test_start,
                        "test_end": test_end,
                        "train_data": self.data.iloc[train_start:train_end],
                        "test_data": self.data.iloc[test_start:test_end],
                    }
                )

                # NOTE: translated to English.
                current_start += step_size
                if isinstance(target_window_count, int) and len(windows) >= target_window_count:
                    break

        elif mode == "anchored":
            # NOTE: translated to English.
            # NOTE: translated to English.
            # NOTE: translated to English.
            train_start = 0
            initial_train_size = train_size  # NOTE: translated to English.
            current_train_size = initial_train_size

            # NOTE: translated to English.
            while train_start + current_train_size + test_size <= total_points:
                train_end = train_start + current_train_size
                test_start = train_end
                test_end = test_start + test_size

                windows.append(
                    {
                        "window_id": len(windows) + 1,
                        "train_start": train_start,
                        "train_end": train_end,
                        "test_start": test_start,
                        "test_end": test_end,
                        "train_data": self.data.iloc[train_start:train_end],
                        "test_data": self.data.iloc[test_start:test_end],
                    }
                )

                # NOTE: translated to English.
                current_train_size += step_size

        return windows

    def _process_window(
        self, window: Dict[str, Any], current: int, total: int
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """
        處理單個窗口

        Args:
            window: 窗口數據
            current: 當前窗口編號
            total: 總窗口數

        Returns:
            Tuple[Optional[Dict[str, Any]], Dict[str, Any]]: (窗口處理結果, 狀態信息)
        """
        status = {
            "window_id": window["window_id"],
            "train_size": len(window["train_data"]),
            "test_size": len(window["test_data"]),
            "sharpe_status": "未執行",
            "calmar_status": "未執行",
            "sharpe_metric": None,
            "calmar_metric": None,
            "sharpe_is": None,  # NOTE: translated to English.
            "sharpe_oos": None,  # NOTE: translated to English.
            "sharpe_is_return": None,  # NOTE: translated to English.
            "sharpe_oos_return": None,  # NOTE: translated to English.
            "calmar_is": None,  # NOTE: translated to English.
            "calmar_oos": None,  # NOTE: translated to English.
            "calmar_is_return": None,  # NOTE: translated to English.
            "calmar_oos_return": None,  # NOTE: translated to English.
            "sharpe_failure_reason": None,  # NOTE: translated to English.
            "calmar_failure_reason": None,  # NOTE: translated to English.
        }

        try:
            # NOTE: translated to English.
            optimizer = ParameterOptimizer(
                window["train_data"],
                self.frequency,
                self.config_data,
                logger=self.logger,
                shared_runtime_cache=self._shared_runtime_cache,
                optimizer_context={
                    "window_id": window["window_id"],
                    "train_start": window["train_start"],
                    "train_end": window["train_end"],
                    "test_start": window["test_start"],
                    "test_end": window["test_end"],
                },
            )
            self._merge_contract_audit(self._extract_optimizer_contract_audit(optimizer))

            optimization_objectives = self.wfa_config.get(
                "optimization_objectives", ["sharpe", "calmar"]
            )
            is_semantic_mode = optimizer.backtester_config.get("strategy_mode") == "semantic"

            window_results = {}

            for objective in optimization_objectives:
                if is_semantic_mode:
                    optimal_params, train_metrics = optimizer.optimize_with_is_metrics(
                        objective, silent=True
                    )
                    grid_region = optimizer.get_last_grid_region()
                    all_grid_regions = optimizer.get_all_grid_regions()
                    selected_grid_region = (
                        {"all_params": [optimal_params]} if optimal_params else {}
                    )
                    test_result = optimizer.run_grid_test(
                        window["test_data"],
                        selected_grid_region,
                        optimal_params,
                        objective,
                        silent=True,
                    )

                    if optimal_params is None:
                        status[f"{objective}_status"] = "???"
                        status[f"{objective}_failure_reason"] = (
                            optimizer.get_last_failure_reason() or "semantic optimization failed"
                        )
                        if train_metrics:
                            status[f"{objective}_is"] = train_metrics.get(objective)
                            status[f"{objective}_is_return"] = train_metrics.get(
                                "total_return"
                            )
                        window_results[objective] = {
                            "optimal_params": {},
                            "grid_region": grid_region or {},
                            "all_grid_regions": all_grid_regions or {},
                            "train_metrics": train_metrics,
                            "test_result": {
                                "metrics": {},
                                "individual_results": [],
                                "all_condition_pair_results": {},
                            },
                            "window_info": {
                                "window_id": window["window_id"],
                                "train_start": window["train_start"],
                                "train_end": window["train_end"],
                                "test_start": window["test_start"],
                                "test_end": window["test_end"],
                            },
                        }
                        continue

                    if train_metrics:
                        status[f"{objective}_is"] = train_metrics.get(objective)
                        status[f"{objective}_is_return"] = train_metrics.get("total_return")
                        status["is_mdd"] = train_metrics.get("max_drawdown")
                        if objective == "sharpe" and "calmar" in train_metrics:
                            status["calmar_is"] = train_metrics.get("calmar")
                            status["calmar_is_return"] = train_metrics.get("total_return")
                        elif objective == "calmar" and "sharpe" in train_metrics:
                            status["sharpe_is"] = train_metrics.get("sharpe")
                            status["sharpe_is_return"] = train_metrics.get("total_return")

                    if test_result:
                        metrics = test_result.get("metrics", {})
                        status[f"{objective}_status"] = "???"
                        status[f"{objective}_metric"] = metrics.get(objective)
                        status[f"{objective}_oos"] = metrics.get(objective)
                        status[f"{objective}_oos_return"] = metrics.get("total_return")
                    else:
                        status[f"{objective}_status"] = "???"
                        status[f"{objective}_failure_reason"] = "semantic OOS grid run returned no valid results"
                        test_result = {
                            "metrics": {},
                            "individual_results": [],
                            "all_condition_pair_results": {},
                        }

                    window_results[objective] = {
                        "optimal_params": optimal_params,
                        "grid_region": grid_region or {},
                        "candidate_grid_region": grid_region or {},
                        "selected_grid_region": selected_grid_region,
                        "all_grid_regions": all_grid_regions or {},
                        "train_metrics": train_metrics,
                        "test_result": test_result,
                        "selection_source": "Walk-Forward IS optimization",
                        "selection_rank": 1,
                        "selection_metric": train_metrics.get(objective) if isinstance(train_metrics, dict) else None,
                        "selection_evidence": f"rank=1 by IS {str(objective).title()}",
                        "window_info": {
                            "window_id": window["window_id"],
                            "train_start": window["train_start"],
                            "train_end": window["train_end"],
                            "test_start": window["test_start"],
                            "test_end": window["test_end"],
                        },
                    }
                    continue

                # NOTE: translated to English.
                optimal_params, train_metrics = optimizer.optimize_with_is_metrics(
                    objective, silent=True
                )

                if optimal_params is None:
                    status[f"{objective}_status"] = "失敗"
                    # NOTE: translated to English.
                    failure_reason = optimizer.get_last_failure_reason()
                    status[f"{objective}_failure_reason"] = failure_reason or "未知原因"

                    # NOTE: translated to English.
                    # NOTE: translated to English.
                    all_grid_regions = optimizer.get_all_grid_regions()
                    if all_grid_regions and train_metrics:
                        # NOTE: translated to English.
                        first_strategy_idx = min(all_grid_regions.keys())
                        first_grid_region = all_grid_regions[first_strategy_idx]

                        window_results[objective] = {
                            "optimal_params": {},
                            "grid_region": first_grid_region,
                            "all_grid_regions": all_grid_regions,
                            "train_metrics": train_metrics,  # NOTE: translated to English.
                            "test_result": {
                                "metrics": {},
                                "individual_results": [],
                                "all_condition_pair_results": {},
                            },
                            "window_info": {
                                "window_id": window["window_id"],
                                "train_start": window["train_start"],
                                "train_end": window["train_end"],
                                "test_start": window["test_start"],
                                "test_end": window["test_end"],
                            },
                        }
                    elif train_metrics:
                        # NOTE: translated to English.
                        window_results[objective] = {
                            "optimal_params": {},
                            "grid_region": {},
                            "all_grid_regions": {},
                            "train_metrics": train_metrics,  # NOTE: translated to English.
                            "test_result": {
                                "metrics": {},
                                "individual_results": [],
                                "all_condition_pair_results": {},
                            },
                            "window_info": {
                                "window_id": window["window_id"],
                                "train_start": window["train_start"],
                                "train_end": window["train_end"],
                                "test_start": window["test_start"],
                                "test_end": window["test_end"],
                            },
                        }
                    continue

                # NOTE: translated to English.
                all_grid_regions = optimizer.get_all_grid_regions()
                condition_pairs = self.config_data.backtester_config.get("condition_pairs", [])

                # NOTE: translated to English.
                self.logger.info(
                    f"[DEBUG] 窗口 {current} 目標 {objective}: "
                    f"找到 {len(all_grid_regions)} 個 grid_regions, "
                    f"strategy_idx: {list(all_grid_regions.keys())}"
                )

                # NOTE: translated to English.
                all_condition_pair_results = {}
                all_condition_pair_test_results = {}

                for strategy_idx, pair in enumerate(condition_pairs):
                    self.logger.info(
                        f"[DEBUG] 處理 condition_pair {strategy_idx + 1} "
                        f"({pair.get('entry', [])} + {pair.get('exit', [])}) 的 OOS 測試"
                    )

                    # NOTE: translated to English.
                    grid_region = optimizer.get_last_grid_region(strategy_idx=strategy_idx)

                    if not grid_region:
                        self.logger.warning(
                            f"[DEBUG] condition_pair {strategy_idx + 1} ({pair.get('entry', [])} + {pair.get('exit', [])}) "
                            f"沒有 grid_region，跳過 OOS 測試"
                        )
                        continue

                    self.logger.info(
                        f"[DEBUG] condition_pair {strategy_idx + 1} 找到 grid_region, "
                        f"參數組合數: {len(grid_region.get('all_params', []))}"
                    )

                    # NOTE: translated to English.
                    # NOTE: translated to English.
                    # NOTE: translated to English.
                    # NOTE: translated to English.

                    # NOTE: translated to English.
                    # NOTE: translated to English.
                    self.logger.info(
                        f"[DEBUG] 開始執行 condition_pair {strategy_idx + 1} 的 OOS 測試"
                    )

                    test_result = self._run_grid_test_backtest(
                        window["test_data"], grid_region, optimal_params, objective, silent=True
                    )

                    if test_result:
                        self.logger.info(
                            f"[DEBUG] condition_pair {strategy_idx + 1} OOS 測試成功, "
                            f"metric: {test_result.get('metrics', {}).get(objective, 'N/A')}"
                        )

                        # NOTE: translated to English.
                        pair_params = {}
                        strategy_idx_1based = strategy_idx + 1
                        for key, value in optimal_params.items():
                            if f"_strategy_{strategy_idx_1based}" in key:
                                pair_params[key] = value

                        self.logger.info(
                            f"[DEBUG] condition_pair {strategy_idx + 1} 提取的參數鍵: {list(pair_params.keys())}"
                        )

                        all_condition_pair_results[strategy_idx] = {
                            "grid_region": grid_region,
                            "optimal_params": pair_params,
                            "test_result": test_result,
                        }
                        all_condition_pair_test_results[strategy_idx] = test_result
                    else:
                        # NOTE: translated to English.
                        failure_reason = "未知原因"
                        if hasattr(test_result, 'get') and test_result:
                            failure_reason = test_result.get('failure_reason', '未知原因')
                        elif test_result is None:
                            failure_reason = "回測返回 None"

                        self.logger.warning(
                            f"[DEBUG] condition_pair {strategy_idx + 1} OOS 測試失敗: {failure_reason}"
                        )
                        # NOTE: translated to English.
                        # NOTE: translated to English.
                        pair_params = {}
                        strategy_idx_1based = strategy_idx + 1
                        for key, value in optimal_params.items():
                            if f"_strategy_{strategy_idx_1based}" in key:
                                pair_params[key] = value

                        all_condition_pair_results[strategy_idx] = {
                            "grid_region": grid_region,
                            "optimal_params": pair_params,
                            "test_result": {
                                "metrics": {},
                                "individual_results": [],
                                "all_condition_pair_results": {},
                            },
                        }
                        # NOTE: translated to English.

                # NOTE: translated to English.
                # NOTE: translated to English.
                self.logger.info(
                    f"[DEBUG] 合併結果: 找到 {len(all_condition_pair_test_results)} 個 condition_pair 的測試結果, "
                    f"strategy_idx: {list(all_condition_pair_test_results.keys())}"
                )

                if all_condition_pair_test_results:
                    all_metrics = []
                    all_returns = []
                    for strategy_idx_result, test_result in all_condition_pair_test_results.items():
                        metrics = test_result.get("metrics", {})
                        if metrics:
                            metric_value = metrics.get(objective)
                            return_value = metrics.get("total_return")
                            self.logger.info(
                                f"[DEBUG] condition_pair {strategy_idx_result + 1} 的結果: "
                                f"{objective}={metric_value}, return={return_value}"
                            )
                            all_metrics.append(metric_value)
                            all_returns.append(return_value)

                    avg_metric = sum(all_metrics) / len(all_metrics) if all_metrics else None
                    avg_return = sum(all_returns) / len(all_returns) if all_returns else None

                    self.logger.info(
                        f"[DEBUG] 平均績效: {objective}={avg_metric}, return={avg_return}"
                    )

                    # NOTE: translated to English.
                    if train_metrics:
                        status[f"{objective}_is"] = train_metrics.get(objective)
                        status[f"{objective}_is_return"] = train_metrics.get("total_return")
                        # NOTE: translated to English.
                        status["is_mdd"] = train_metrics.get("max_drawdown")
                        # NOTE: translated to English.
                        if objective == "sharpe" and "calmar" in train_metrics:
                            status["calmar_is"] = train_metrics.get("calmar")
                            status["calmar_is_return"] = train_metrics.get("total_return")
                        elif objective == "calmar" and "sharpe" in train_metrics:
                            status["sharpe_is"] = train_metrics.get("sharpe")
                            status["sharpe_is_return"] = train_metrics.get("total_return")

                    # NOTE: translated to English.
                    first_strategy_idx = min(all_condition_pair_results.keys())
                    first_grid_region = all_condition_pair_results[first_strategy_idx]["grid_region"]
                    if first_grid_region:
                        status[f"{objective}_grid_params"] = first_grid_region.get("all_params")
                        status[f"{objective}_grid_avg_metric"] = first_grid_region.get("avg_metric")
                        all_params = first_grid_region.get("all_params", [])
                        if all_params:
                            status[f"{objective}_display_params"] = all_params[0]

                    status[f"{objective}_status"] = "成功"
                    status[f"{objective}_metric"] = avg_metric
                    status[f"{objective}_oos"] = avg_metric  # NOTE: translated to English.
                    status[f"{objective}_oos_return"] = avg_return  # NOTE: translated to English.

                    # NOTE: translated to English.
                    combined_test_result = {
                        "metrics": {
                            objective: avg_metric,
                            "total_return": avg_return,
                        },
                        "individual_results": [],  # NOTE: translated to English.
                        "all_condition_pair_results": all_condition_pair_results,  # NOTE: translated to English.
                    }

                    window_results[objective] = {
                        "optimal_params": optimal_params,  # NOTE: translated to English.
                        "grid_region": first_grid_region,  # NOTE: translated to English.
                        "all_grid_regions": all_grid_regions,  # NOTE: translated to English.
                        "train_metrics": train_metrics,  # NOTE: translated to English.
                        "test_result": combined_test_result,  # NOTE: translated to English.
                        "window_info": {
                            "window_id": window["window_id"],
                            "train_start": window["train_start"],
                            "train_end": window["train_end"],
                            "test_start": window["test_start"],
                            "test_end": window["test_end"],
                        },
                    }
                else:
                    status[f"{objective}_status"] = "失敗"
                    status[f"{objective}_failure_reason"] = "所有 condition_pairs 的測試回測都失敗"

                    # NOTE: translated to English.
                    if train_metrics:
                        # NOTE: translated to English.
                        # NOTE: translated to English.
                        if not all_condition_pair_results and all_grid_regions:
                            # NOTE: translated to English.
                            # NOTE: translated to English.
                            condition_pairs = self.config_data.backtester_config.get("condition_pairs", [])
                            for strategy_idx, pair in enumerate(condition_pairs):
                                grid_region = all_grid_regions.get(strategy_idx)
                                if grid_region:
                                    # NOTE: translated to English.
                                    pair_params = {}
                                    strategy_idx_1based = strategy_idx + 1
                                    for key, value in (optimal_params or {}).items():
                                        if f"_strategy_{strategy_idx_1based}" in key:
                                            pair_params[key] = value

                                    all_condition_pair_results[strategy_idx] = {
                                        "grid_region": grid_region,
                                        "optimal_params": pair_params,
                                        "test_result": {
                                            "metrics": {},
                                            "individual_results": [],
                                            "all_condition_pair_results": {},
                                        },
                                    }

                        # NOTE: translated to English.
                        first_grid_region = None
                        if all_grid_regions:
                            first_strategy_idx = min(all_grid_regions.keys())
                            first_grid_region = all_grid_regions[first_strategy_idx]

                        window_results[objective] = {
                            "optimal_params": optimal_params if optimal_params else {},
                            "grid_region": first_grid_region if first_grid_region else {},
                            "all_grid_regions": all_grid_regions if all_grid_regions else {},
                            "train_metrics": train_metrics,  # NOTE: translated to English.
                            "test_result": {
                                "metrics": {},
                                "individual_results": [],
                                "all_condition_pair_results": all_condition_pair_results,  # NOTE: translated to English.
                            },
                            "window_info": {
                                "window_id": window["window_id"],
                                "train_start": window["train_start"],
                                "train_end": window["train_end"],
                                "test_start": window["test_start"],
                                "test_end": window["test_end"],
                            },
                        }

            if window_results:
                for objective_name, payload in window_results.items():
                    if not isinstance(payload, dict):
                        continue
                    payload["contract_audit"] = dict(self.contract_audit)
                    payload["window_result_hash"] = self._build_window_result_hash(
                        objective_name=str(objective_name),
                        payload=payload,
                    )

            return (window_results if window_results else None, status)

        except Exception as e:
            self.logger.error(f"處理窗口 {current} 失敗: {e}")
            status["error"] = str(e)
            return (None, status)

    def _run_grid_test_backtest(
        self,
        test_data: pd.DataFrame,
        grid_region: Optional[Dict[str, Any]],
        fallback_params: Dict[str, Any],
        objective: str,
        silent: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        在測試集上使用九宮格參數執行回測，計算平均表現

        Args:
            test_data: 測試集數據
            grid_region: 九宮格區域信息（包含所有9個參數組合）
            fallback_params: 回退參數（如果九宮格失敗時使用）
            objective: 優化目標
            silent: 是否靜默模式

        Returns:
            Optional[Dict[str, Any]]: 回測結果（包含平均績效）
        """
        try:
            raise RuntimeError("Public legacy WFA runtime path has been removed; use strategy_mode=semantic.")
            from metricstracker.MetricsCalculator_metricstracker import (
                MetricsCalculatorMetricTracker,
            )
            import numpy as np
            import io
            import logging
            from contextlib import redirect_stdout, redirect_stderr

            # NOTE: translated to English.
            if not grid_region or "all_params" not in grid_region:
                return self._run_single_test_backtest(test_data, fallback_params, objective, silent)

            all_params = grid_region["all_params"]
            all_metrics = []
            all_equity_curves = []
            all_returns = []
            all_individual_results = []  # NOTE: translated to English.
            valid_count = 0
            failure_reasons = []  # NOTE: translated to English.

            for param_idx, params in enumerate(all_params):
                failure_reason = None

                # NOTE: translated to English.
                backtest_config = self._build_backtest_config(params)

                # NOTE: translated to English.
                engine = _removed_public_wfa_engine(
                    test_data, self.frequency, self.logger, symbol=getattr(self.config_data, "symbol", "X")
                )

                # NOTE: translated to English.
                if silent:
                    old_level = logging.getLogger().level
                    logging.getLogger().setLevel(logging.ERROR)
                    try:
                        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                            results = engine.run_backtests(backtest_config)
                    finally:
                        logging.getLogger().setLevel(old_level)
                else:
                    results = engine.run_backtests(backtest_config)

                if not results or len(results) == 0:
                    failure_reason = "回測結果為空"
                    failure_reasons.append(f"參數組合 {param_idx + 1}: {failure_reason}")
                    all_individual_results.append({
                        "param_index": param_idx,
                        "params": params,
                        "metric": None,
                        "return": None,
                        "success": False,
                        "failure_reason": failure_reason,
                    })
                    continue

                # NOTE: translated to English.
                result = results[0]

                # NOTE: translated to English.
                if result.get("error") is not None:
                    failure_reason = f"回測錯誤: {result.get('error')}"
                    failure_reasons.append(f"參數組合 {param_idx + 1}: {failure_reason}")
                    all_individual_results.append({
                        "param_index": param_idx,
                        "params": params,
                        "metric": None,
                        "return": None,
                        "success": False,
                        "failure_reason": failure_reason,
                    })
                    continue

                # NOTE: translated to English.
                if "records" not in result:
                    failure_reason = "沒有交易記錄"
                    failure_reasons.append(f"參數組合 {param_idx + 1}: {failure_reason}")
                    all_individual_results.append({
                        "param_index": param_idx,
                        "params": params,
                        "metric": None,
                        "return": None,
                        "success": False,
                        "failure_reason": failure_reason,
                    })
                    continue

                records = result["records"]
                if not isinstance(records, pd.DataFrame) or records.empty:
                    failure_reason = "交易記錄為空"
                    failure_reasons.append(f"參數組合 {param_idx + 1}: {failure_reason}")
                    all_individual_results.append({
                        "param_index": param_idx,
                        "params": params,
                        "metric": None,
                        "return": None,
                        "success": False,
                        "failure_reason": failure_reason,
                    })
                    continue

                # NOTE: translated to English.
                if "Trade_action" in records.columns:
                    trade_count = (records["Trade_action"] == 1).sum()
                    if trade_count == 0:
                        failure_reason = f"沒有交易（記錄數: {len(records)}）"
                        failure_reasons.append(f"參數組合 {param_idx + 1}: {failure_reason}")
                        all_individual_results.append({
                            "param_index": param_idx,
                            "params": params,
                            "metric": None,
                            "return": None,
                            "success": False,
                            "failure_reason": failure_reason,
                        })
                        continue

                # NOTE: translated to English.
                metrics_calc = MetricsCalculatorMetricTracker(
                    records,
                    time_unit=365,
                    risk_free_rate=0.04,
                )

                # NOTE: translated to English.
                sharpe_value = metrics_calc.sharpe()
                calmar_value = metrics_calc.calmar()
                sortino_value = metrics_calc.sortino()
                total_return = metrics_calc.total_return()
                max_drawdown = metrics_calc.max_drawdown()

                # NOTE: translated to English.
                metric_value = sharpe_value if objective == "sharpe" else calmar_value

                # NOTE: translated to English.
                oos_return = None
                oos_metric = None
                oos_sharpe = None
                oos_calmar = None
                oos_sortino = None
                oos_mdd = None

                if not pd.isna(metric_value) and metric_value != float("inf") and metric_value != float("-inf"):
                    all_metrics.append(metric_value)
                    valid_count += 1
                    oos_metric = metric_value

                    if not pd.isna(total_return) and total_return != float("inf") and total_return != float("-inf"):
                        all_returns.append(total_return)
                        oos_return = total_return

                    # NOTE: translated to English.
                    if not pd.isna(sharpe_value) and sharpe_value != float("inf") and sharpe_value != float("-inf"):
                        oos_sharpe = sharpe_value
                    if not pd.isna(calmar_value) and calmar_value != float("inf") and calmar_value != float("-inf"):
                        oos_calmar = calmar_value
                    if not pd.isna(sortino_value) and sortino_value != float("inf") and sortino_value != float("-inf"):
                        oos_sortino = sortino_value
                    if not pd.isna(max_drawdown) and max_drawdown != float("inf") and max_drawdown != float("-inf"):
                        oos_mdd = max_drawdown

                    # NOTE: translated to English.
                    if "Equity_value" in records.columns:
                        all_equity_curves.append(records["Equity_value"].values)

                # NOTE: translated to English.
                all_individual_results.append({
                    "param_index": param_idx,
                    "params": params,
                    "metric": oos_metric,
                    "return": oos_return,
                    "sharpe": oos_sharpe,
                    "calmar": oos_calmar,
                    "sortino": oos_sortino,
                    "max_drawdown": oos_mdd,
                    "success": oos_metric is not None,
                })

            if not all_metrics:
                # NOTE: translated to English.
                if failure_reasons:
                    failure_summary = "; ".join(failure_reasons[:5])  # NOTE: translated to English.
                    if len(failure_reasons) > 5:
                        failure_summary += f" ... (共 {len(failure_reasons)} 個參數組合失敗)"
                    self.logger.warning(
                        f"[DEBUG] 所有參數組合的 OOS 測試都失敗。失敗原因: {failure_summary}"
                    )
                else:
                    self.logger.warning(
                        "[DEBUG] 所有參數組合的 OOS 測試都失敗（未記錄具體原因）"
                    )
                # NOTE: translated to English.
                single_result = self._run_single_test_backtest(test_data, fallback_params, objective, silent)
                if single_result is None and failure_reasons:
                    # NOTE: translated to English.
                    self.logger.warning(
                        f"[DEBUG] 單一參數回退測試也失敗。之前失敗原因: {failure_summary if failure_reasons else '未知'}"
                    )
                return single_result

            # NOTE: translated to English.
            avg_metric = sum(all_metrics) / len(all_metrics)
            avg_return = sum(all_returns) / len(all_returns) if all_returns else None

            # NOTE: translated to English.
            all_sharpes = [r.get("sharpe") for r in all_individual_results if r.get("sharpe") is not None]
            all_calmars = [r.get("calmar") for r in all_individual_results if r.get("calmar") is not None]
            all_sortinos = [r.get("sortino") for r in all_individual_results if r.get("sortino") is not None]
            all_mdds = [r.get("max_drawdown") for r in all_individual_results if r.get("max_drawdown") is not None]

            avg_sharpe = sum(all_sharpes) / len(all_sharpes) if all_sharpes else None
            avg_calmar = sum(all_calmars) / len(all_calmars) if all_calmars else None
            avg_sortino = sum(all_sortinos) / len(all_sortinos) if all_sortinos else None
            avg_mdd = sum(all_mdds) / len(all_mdds) if all_mdds else None

            # NOTE: translated to English.
            avg_equity = None
            if all_equity_curves:
                # NOTE: translated to English.
                min_length = min(len(eq) for eq in all_equity_curves)
                # NOTE: translated to English.
                truncated_curves = [eq[:min_length] for eq in all_equity_curves]
                # NOTE: translated to English.
                avg_equity = np.mean(truncated_curves, axis=0)

            metrics = {
                objective: avg_metric,
                "sharpe": avg_sharpe,
                "calmar": avg_calmar,
                "sortino": avg_sortino,
                "total_return": avg_return,
                "max_drawdown": avg_mdd,
                "param_count": valid_count,
            }

            return {
                "backtest_result": None,  # NOTE: translated to English.
                "equity_curve": avg_equity,
                "metrics": metrics,  # NOTE: translated to English.
                "all_metrics": all_metrics,  # NOTE: translated to English.
                "individual_results": all_individual_results,  # NOTE: translated to English.
            }

        except Exception as e:
            self.logger.error(f"九宮格測試集回測失敗: {e}")
            # NOTE: translated to English.
            return self._run_single_test_backtest(test_data, fallback_params, objective, silent)

    def _run_single_test_backtest(
        self, test_data: pd.DataFrame, optimal_params: Dict[str, Any], objective: str, silent: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        在測試集上執行單一參數的回測（回退方法）

        Args:
            test_data: 測試集數據
            optimal_params: 最優參數
            objective: 優化目標
            silent: 是否靜默模式

        Returns:
            Optional[Dict[str, Any]]: 回測結果
        """
        try:
            raise RuntimeError("Public legacy WFA runtime path has been removed; use strategy_mode=semantic.")

            # NOTE: translated to English.
            backtest_config = self._build_backtest_config(optimal_params)

            # NOTE: translated to English.
            engine = _removed_public_wfa_engine(
                test_data, self.frequency, self.logger, symbol=getattr(self.config_data, "symbol", "X")
            )

            # NOTE: translated to English.
            if silent:
                import io
                import logging
                from contextlib import redirect_stdout, redirect_stderr

                # NOTE: translated to English.
                old_level = logging.getLogger().level
                logging.getLogger().setLevel(logging.ERROR)
                try:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        results = engine.run_backtests(backtest_config)
                finally:
                    logging.getLogger().setLevel(old_level)
            else:
                results = engine.run_backtests(backtest_config)

            if results and len(results) > 0:
                # NOTE: translated to English.
                from metricstracker.MetricsCalculator_metricstracker import (
                    MetricsCalculatorMetricTracker,
                )

                # NOTE: translated to English.
                result = results[0]

                # NOTE: translated to English.
                if result.get("error") is not None:
                    return None

                # NOTE: translated to English.
                if "records" not in result:
                    return None

                records = result["records"]
                if not isinstance(records, pd.DataFrame) or records.empty:
                    return None

                # NOTE: translated to English.
                if "Trade_action" in records.columns:
                    trade_count = (records["Trade_action"] == 1).sum()
                    if trade_count == 0:
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

                # NOTE: translated to English.
                equity_curve = None
                if "Equity_value" in records.columns:
                    equity_curve = records["Equity_value"].values

                return {
                    "backtest_result": result,
                    "equity_curve": equity_curve,
                    "metrics": metrics,
                }

            return None

        except Exception as e:
            self.logger.error(f"單一參數測試集回測失敗: {e}")
            return None

    def _build_backtest_config(self, optimal_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        構建回測配置（使用最優參數）

        Args:
            optimal_params: 最優參數

        Returns:
            Dict[str, Any]: 回測配置
        """
        # NOTE: translated to English.
        backtest_config = {
            "condition_pairs": self.config_data.backtester_config.get(
                "condition_pairs", []
            ),
            "indicator_params": optimal_params,  # NOTE: translated to English.
            "predictors": [
                self.config_data.backtester_config.get("selected_predictor", "X")
            ],
            "trading_params": self.config_data.backtester_config.get(
                "trading_params", {}
            ),
        }

        return backtest_config

    def _collect_results(
        self, wfa_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        收集 WFA 結果

        Args:
            wfa_results: 各窗口的結果

        Returns:
            Dict[str, Any]: 最終 WFA 結果
        """
        final_results = {
            "wfa_config": self.wfa_config,
            "total_windows": len(wfa_results),
            "results_by_objective": {},
            "data": self.data,  # NOTE: translated to English.
            "contract_audit": dict(self.contract_audit),
        }

        # NOTE: translated to English.
        objectives = self.wfa_config.get("optimization_objectives", ["sharpe", "calmar"])

        for objective in objectives:
            objective_results = []
            for window_result in wfa_results:
                if objective in window_result:
                    objective_results.append(window_result[objective])

            final_results["results_by_objective"][objective] = objective_results

        return final_results

    def _extract_optimizer_contract_audit(
        self, optimizer: ParameterOptimizer
    ) -> Dict[str, Any]:
        """Extract strategy/feature/execution-plan metadata from optimizer."""
        audit: Dict[str, Any] = {}
        backtester_config = getattr(optimizer, "backtester_config", {})
        if not isinstance(backtester_config, dict):
            backtester_config = {}
        config_data_backtester = getattr(self.config_data, "backtester_config", {})
        if isinstance(config_data_backtester, dict):
            merged_backtester = dict(config_data_backtester)
            merged_backtester.update(backtester_config)
            backtester_config = merged_backtester
        if isinstance(backtester_config, dict):
            strategy_mode = backtester_config.get("strategy_mode")
            strategy_path = backtester_config.get("strategy_contract_path")
            feature_path = backtester_config.get("feature_contract_path")
            if isinstance(strategy_mode, str) and strategy_mode.strip():
                audit["strategy_mode"] = strategy_mode
            if isinstance(strategy_path, str) and strategy_path.strip():
                audit["strategy_contract_path"] = strategy_path
            if isinstance(feature_path, str) and feature_path.strip():
                audit["feature_contract_path"] = feature_path
                try:
                    feature_payload = json.loads(
                        Path(feature_path).read_text(encoding="utf-8-sig")
                    )
                    dumped = json.dumps(
                        feature_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    audit["feature_contract_hash"] = hashlib.sha256(
                        dumped.encode("utf-8")
                    ).hexdigest()
                except Exception:
                    pass

        execution_plan_path = getattr(optimizer, "_execution_plan_path", None)
        if isinstance(execution_plan_path, str) and execution_plan_path.strip():
            audit["execution_plan_path"] = execution_plan_path
        execution_plan = getattr(optimizer, "_execution_plan", None)
        if isinstance(execution_plan, dict):
            plan_hash = execution_plan.get("plan_hash")
            if isinstance(plan_hash, str) and plan_hash.strip():
                audit["execution_plan_hash"] = plan_hash
                audit["execution_plan_id"] = plan_hash[:12]
        if (
            isinstance(audit.get("execution_plan_hash"), str)
            and audit.get("execution_plan_hash")
            and not audit.get("execution_plan_id")
        ):
            audit["execution_plan_id"] = str(audit["execution_plan_hash"])[:12]
        source_audit_seed = json.dumps(
            {
                "execution_plan_hash": audit.get("execution_plan_hash", ""),
                "feature_contract_path": audit.get("feature_contract_path", ""),
                "feature_contract_hash": audit.get("feature_contract_hash", ""),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        audit["source_audit_id"] = hashlib.sha256(source_audit_seed.encode("utf-8")).hexdigest()[:12]
        return audit

    def _merge_contract_audit(self, incoming: Dict[str, Any]) -> None:
        """Persist latest non-empty audit metadata at engine level."""
        if not isinstance(incoming, dict):
            return
        for key, value in incoming.items():
            if isinstance(value, str) and value.strip():
                self.contract_audit[key] = value

    @staticmethod
    def _build_window_result_hash(*, objective_name: str, payload: Dict[str, Any]) -> str:
        """Build deterministic hash for window-level reproducibility audit."""
        if not isinstance(payload, dict):
            return ""
        stable_payload = {
            "objective": objective_name,
            "window_info": payload.get("window_info", {}),
            "optimal_params": payload.get("optimal_params", {}),
            "train_metrics": payload.get("train_metrics", {}),
            "test_metrics": (
                payload.get("test_result", {}).get("metrics", {})
                if isinstance(payload.get("test_result"), dict)
                else {}
            ),
            "contract_audit": payload.get("contract_audit", {}),
        }
        dumped = json.dumps(stable_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()

    def _display_results_summary(
        self, window_status: List[Dict[str, Any]], wfa_results: List[Dict[str, Any]]
    ) -> None:
        """
        顯示結果摘要表格

        Args:
            window_status: 各窗口的狀態信息
            wfa_results: WFA 結果列表
        """
        from rich.table import Table

        table = Table(
            title="📊 WFA 執行結果摘要",
            show_lines=True,
            border_style="#dbac30",
        )
        table.add_column("窗口", style="cyan", no_wrap=True)
        table.add_column("日期範圍", style="white", no_wrap=False)
        table.add_column("訓練集大小", style="white")
        table.add_column("測試集大小", style="white")
        table.add_column("Sharpe 狀態", style="white")
        table.add_column("Sharpe IS", style="#1e90ff")
        table.add_column("Sharpe OOS", style="#1e90ff")
        table.add_column("Sharpe 最佳參數", style="yellow")
        table.add_column("IS Return%", style="#1e90ff")
        table.add_column("OOS Return%", style="#1e90ff")
        table.add_column("Calmar 狀態", style="white")
        table.add_column("Calmar IS", style="#1e90ff")
        table.add_column("Calmar OOS", style="#1e90ff")
        table.add_column("Calmar 最佳參數", style="yellow")
        table.add_column("Calmar IS Return%", style="#1e90ff")
        table.add_column("Calmar OOS Return%", style="#1e90ff")

        # NOTE: translated to English.
        window_result_map = {}
        for window_result in wfa_results:
            for objective in ["sharpe", "calmar"]:
                if objective in window_result:
                    window_id = window_result[objective].get("window_info", {}).get("window_id")
                    if window_id:
                        if window_id not in window_result_map:
                            window_result_map[window_id] = {}
                        window_result_map[window_id][objective] = window_result[objective]

        for status in window_status:
            window_id = status.get("window_id", "N/A")
            train_size = status.get("train_size", 0)
            test_size = status.get("test_size", 0)

            # NOTE: translated to English.
            date_range_str = self._get_date_range_for_window(window_id, window_result_map)
            sharpe_status = status.get("sharpe_status", "未執行")
            sharpe_is = status.get("sharpe_is")
            sharpe_oos = status.get("sharpe_oos") or status.get("sharpe_metric")
            sharpe_is_return = status.get("sharpe_is_return")
            sharpe_oos_return = status.get("sharpe_oos_return")
            sharpe_failure_reason = status.get("sharpe_failure_reason")

            calmar_status = status.get("calmar_status", "未執行")
            calmar_is = status.get("calmar_is")
            calmar_oos = status.get("calmar_oos") or status.get("calmar_metric")
            calmar_is_return = status.get("calmar_is_return")
            calmar_oos_return = status.get("calmar_oos_return")

            calmar_is_return_value = (
                f"[#1e90ff]{calmar_is_return*100:.2f}%[/#1e90ff]"
                if calmar_is_return is not None
                else "N/A"
            )
            calmar_oos_return_value = (
                f"[#1e90ff]{calmar_oos_return*100:.2f}%[/#1e90ff]"
                if calmar_oos_return is not None
                else "N/A"
            )
            calmar_failure_reason = status.get("calmar_failure_reason")

            # NOTE: translated to English.
            sharpe_display_params = status.get("sharpe_display_params")
            if not sharpe_display_params:
                sharpe_result = window_result_map.get(window_id, {}).get("sharpe")
                sharpe_params_str = self._extract_params_for_display(sharpe_result)
            else:
                sharpe_params_str = self._format_params_dict_simple(sharpe_display_params)

            calmar_display_params = status.get("calmar_display_params")
            if not calmar_display_params:
                calmar_result = window_result_map.get(window_id, {}).get("calmar")
                calmar_params_str = self._extract_params_for_display(calmar_result)
            else:
                calmar_params_str = self._format_params_dict_simple(calmar_display_params)

            # NOTE: translated to English.
            if sharpe_status == "成功":
                sharpe_status_display = f"[green]✅ {sharpe_status}[/green]"
            elif sharpe_status == "失敗":
                # NOTE: translated to English.
                reason = sharpe_failure_reason or "未知原因"
                # NOTE: translated to English.
                if len(reason) > 30:
                    reason = reason[:27] + "..."
                sharpe_status_display = f"[red]❌ 失敗 ({reason})[/red]"
            else:
                sharpe_status_display = f"[yellow]⚠️ {sharpe_status}[/yellow]"

            if calmar_status == "成功":
                calmar_status_display = f"[green]✅ {calmar_status}[/green]"
            elif calmar_status == "失敗":
                # NOTE: translated to English.
                reason = calmar_failure_reason or "未知原因"
                # NOTE: translated to English.
                if len(reason) > 30:
                    reason = reason[:27] + "..."
                calmar_status_display = f"[red]❌ 失敗 ({reason})[/red]"
            else:
                calmar_status_display = f"[yellow]⚠️ {calmar_status}[/yellow]"

            # NOTE: translated to English.
            sharpe_is_value = (
                f"[#1e90ff]{sharpe_is:.4f}[/#1e90ff]"
                if sharpe_is is not None
                else "N/A"
            )
            sharpe_oos_value = (
                f"[#1e90ff]{sharpe_oos:.4f}[/#1e90ff]"
                if sharpe_oos is not None
                else "N/A"
            )
            sharpe_is_return_value = (
                f"[#1e90ff]{sharpe_is_return*100:.2f}%[/#1e90ff]"
                if sharpe_is_return is not None
                else "N/A"
            )
            sharpe_oos_return_value = (
                f"[#1e90ff]{sharpe_oos_return*100:.2f}%[/#1e90ff]"
                if sharpe_oos_return is not None
                else "N/A"
            )
            calmar_is_value = (
                f"[#1e90ff]{calmar_is:.4f}[/#1e90ff]"
                if calmar_is is not None
                else "N/A"
            )
            calmar_oos_value = (
                f"[#1e90ff]{calmar_oos:.4f}[/#1e90ff]"
                if calmar_oos is not None
                else "N/A"
            )

            table.add_row(
                str(window_id),
                date_range_str,
                str(train_size),
                str(test_size),
                sharpe_status_display,
                sharpe_is_value,
                sharpe_oos_value,
                sharpe_params_str,
                sharpe_is_return_value,
                sharpe_oos_return_value,
                calmar_status_display,
                    calmar_is_value,
                    calmar_oos_value,
                    calmar_params_str,
                    calmar_is_return_value,
                    calmar_oos_return_value,
                )

        console.print(table)

        # NOTE: translated to English.
        total_windows = len(window_status)
        sharpe_success = sum(
            1 for s in window_status if s.get("sharpe_status") == "成功"
        )
        calmar_success = sum(
            1 for s in window_status if s.get("calmar_status") == "成功"
        )

        show_info("WFANALYSER",
            f"📊 統計信息:\n"
            f"   總窗口數: {total_windows}\n"
            f"   Sharpe 成功: {sharpe_success}/{total_windows}\n"
            f"   Calmar 成功: {calmar_success}/{total_windows}"
        )

    def _extract_params_for_display(self, window_result: Optional[Dict[str, Any]]) -> str:
        """
        從窗口結果中提取參數用於顯示

        Args:
            window_result: 窗口結果字典

        Returns:
            str: 格式化的參數字符串
        """
        if not window_result:
            return "N/A"

        try:
            grid_region = window_result.get("grid_region", {})
            all_params = grid_region.get("all_params", [])

            if not all_params:
                # NOTE: translated to English.
                optimal_params = window_result.get("optimal_params", {})
                if optimal_params:
                    return self._format_params_dict_simple(optimal_params)
                return "N/A"

            # NOTE: translated to English.
            first_params = all_params[0] if all_params else {}
            return self._format_params_dict_simple(first_params)

        except Exception as e:
            self.logger.warning(f"提取參數用於顯示失敗: {e}")
            return "N/A"

    def _format_params_dict_simple(self, params: Dict[str, Any]) -> str:
        """
        簡單格式化參數字典為字符串

        Args:
            params: 參數字典

        Returns:
            str: 格式化的字符串
        """
        try:
            from wfanalyser.ResultsExporter_wfanalyser import ResultsExporter

            # NOTE: translated to English.
            from pathlib import Path
            exporter = ResultsExporter({}, Path("outputs/wfanalyser"), self.logger)
            param_dict = exporter._extract_params_dict(params)
            formatted = exporter._format_params_dict(param_dict)

            # NOTE: translated to English.
            if formatted and formatted != "{}":
                # NOTE: translated to English.
                parts = []
                for key, value in param_dict.items():
                    parts.append(f"{key}:{value}")
                return ", ".join(parts) if parts else "N/A"

            return "N/A"

        except Exception as e:
            self.logger.warning(f"格式化參數失敗: {e}")
            return "N/A"

    def _get_date_range_for_window(self, window_id: int, window_result_map: Optional[Dict[str, Any]] = None) -> str:
        """
        獲取窗口的日期範圍

        Args:
            window_id: 窗口ID
            window_result_map: 窗口結果映射（未使用，保留以保持接口兼容性）

        Returns:
            str: 格式化的日期範圍字符串
        """
        try:
            # NOTE: translated to English.
            train_start = None
            test_end = None

            # NOTE: translated to English.
            if not hasattr(self, 'windows') or not self.windows:
                self.logger.warning(f"窗口 {window_id}: self.windows 不存在或為空")
                return "N/A"

            for window in self.windows:
                if window.get("window_id") == window_id:
                    train_start = window.get("train_start")
                    test_end = window.get("test_end")
                    break

            if train_start is None or test_end is None:
                self.logger.warning(f"窗口 {window_id}: 無法從 self.windows 中找到窗口信息")
                return "N/A"

            if self.data is None:
                self.logger.warning(f"窗口 {window_id}: self.data 為 None")
                return "N/A"

            # NOTE: translated to English.
            date_column = None
            for col in ["Time", "time", "Date", "date", "datetime", "DateTime"]:
                if col in self.data.columns:
                    date_column = col
                    break

            if date_column is None:
                # NOTE: translated to English.
                return f"索引 {train_start}-{test_end-1}"

            # NOTE: translated to English.
            train_start_date = self.data.iloc[train_start][date_column]
            test_end_date = self.data.iloc[test_end - 1][date_column]  # NOTE: translated to English.

            # NOTE: translated to English.
            if isinstance(train_start_date, pd.Timestamp):
                train_date_str = train_start_date.strftime("%Y-%m-%d")
            elif hasattr(train_start_date, 'strftime'):
                train_date_str = train_start_date.strftime("%Y-%m-%d")
            else:
                train_date_str = str(train_start_date)

            if isinstance(test_end_date, pd.Timestamp):
                test_date_str = test_end_date.strftime("%Y-%m-%d")
            elif hasattr(test_end_date, 'strftime'):
                test_date_str = test_end_date.strftime("%Y-%m-%d")
            else:
                test_date_str = str(test_end_date)

            return f"{train_date_str}\n至 {test_date_str}"

        except Exception as e:
            self.logger.warning(f"獲取日期範圍失敗: {e}")
            return "N/A"

    def _get_indicator_configs(self) -> List[Dict[str, Any]]:
        """
        獲取所有指標配置列表（支持舊格式字典和新格式列表）

        Returns:
            List[Dict[str, Any]]: 指標配置列表
        """
        indicator_configs = self.config_data.backtester_config.get("indicator_params", [])

        # NOTE: translated to English.
        if isinstance(indicator_configs, dict):
            indicator_configs = [indicator_configs]

        if not indicator_configs:
            raise ValueError("未找到任何指標配置，請檢查 backtester.indicator_params 配置")

        return indicator_configs

    def _create_temp_config_data(
        self, indicator_params_config: Dict[str, Any], condition_pair_idx: int = 0
    ) -> Any:
        """
        為單一指標配置創建臨時的 config_data

        Args:
            indicator_params_config: 單一指標配置字典
            condition_pair_idx: 對應的 condition_pair 索引（從0開始）

        Returns:
            Any: 臨時的 config_data 對象（具有相同的接口）
        """
        # NOTE: translated to English.
        class TempConfigData:
            def __init__(self, original_config, indicator_params, pair_idx):
                self.wfa_config = original_config.wfa_config
                self.dataloader_config = original_config.dataloader_config
                self.predictor_config = original_config.predictor_config
                self.metricstracker_config = original_config.metricstracker_config

                # NOTE: translated to English.
                self.backtester_config = original_config.backtester_config.copy()

                # NOTE: translated to English.
                self.backtester_config["indicator_params"] = indicator_params

                # NOTE: translated to English.
                all_condition_pairs = original_config.backtester_config.get("condition_pairs", [])
                if pair_idx < len(all_condition_pairs):
                    # NOTE: translated to English.
                    self.backtester_config["condition_pairs"] = [all_condition_pairs[pair_idx]]
                else:
                    # NOTE: translated to English.
                    self.logger.warning(
                        f"condition_pair_idx {pair_idx} 超出範圍，使用第一個 condition_pair"
                    )
                    self.backtester_config["condition_pairs"] = (
                        all_condition_pairs[:1] if all_condition_pairs else []
                    )

                # NOTE: translated to English.
                for attr in ["symbol", "file_name"]:
                    if hasattr(original_config, attr):
                        setattr(self, attr, getattr(original_config, attr))

        return TempConfigData(self.config_data, indicator_params_config, condition_pair_idx)

    def _process_single_config_window(
        self,
        window: Dict[str, Any],
        current: int,
        total: int,
        temp_config_data: Any,
        config_id: str,
        indicator_params_config: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """
        處理單一配置 + 單一窗口（方案 B）

        Args:
            window: 窗口數據
            current: 當前窗口編號
            total: 總窗口數
            temp_config_data: 臨時的配置數據（包含單一指標配置）
            config_id: 配置ID（例如 "config_1"）
            indicator_params_config: 指標參數配置字典

        Returns:
            Tuple[Optional[Dict[str, Any]], Dict[str, Any]]: (窗口處理結果, 狀態信息)
        """
        status = {
            "window_id": window["window_id"],
            "config_id": config_id,
            "train_size": len(window["train_data"]),
            "test_size": len(window["test_data"]),
            "sharpe_status": "未執行",
            "calmar_status": "未執行",
            "sharpe_metric": None,
            "calmar_metric": None,
            "sharpe_is": None,
            "sharpe_oos": None,
            "sharpe_is_return": None,
            "sharpe_oos_return": None,
            "calmar_is": None,
            "calmar_oos": None,
            "calmar_is_return": None,
            "calmar_oos_return": None,
            "sharpe_failure_reason": None,
            "calmar_failure_reason": None,
        }

        try:
            # NOTE: translated to English.
            optimizer = ParameterOptimizer(
                window["train_data"],
                self.frequency,
                temp_config_data,  # NOTE: translated to English.
                logger=self.logger,
                shared_runtime_cache=self._shared_runtime_cache,
                optimizer_context={
                    "window_id": window["window_id"],
                    "train_start": window["train_start"],
                    "train_end": window["train_end"],
                    "test_start": window["test_start"],
                    "test_end": window["test_end"],
                    "config_id": config_id,
                },
            )
            self._merge_contract_audit(self._extract_optimizer_contract_audit(optimizer))

            optimization_objectives = self.wfa_config.get(
                "optimization_objectives", ["sharpe", "calmar"]
            )

            window_results = {}

            for objective in optimization_objectives:
                # NOTE: translated to English.
                optimal_params, train_metrics = optimizer.optimize_with_is_metrics(
                    objective, silent=True
                )

                if optimal_params is None:
                    status[f"{objective}_status"] = "失敗"
                    failure_reason = optimizer.get_last_failure_reason()
                    status[f"{objective}_failure_reason"] = failure_reason or "未知原因"
                    continue

                # NOTE: translated to English.
                if train_metrics:
                    status[f"{objective}_is"] = train_metrics.get(objective)
                    status[f"{objective}_is_return"] = train_metrics.get("total_return")
                    # NOTE: translated to English.
                    status["is_mdd"] = train_metrics.get("max_drawdown")
                    if objective == "sharpe" and "calmar" in train_metrics:
                        status["calmar_is"] = train_metrics.get("calmar")
                        status["calmar_is_return"] = train_metrics.get("total_return")
                    elif objective == "calmar" and "sharpe" in train_metrics:
                        status["sharpe_is"] = train_metrics.get("sharpe")
                        status["sharpe_is_return"] = train_metrics.get("total_return")

                # NOTE: translated to English.
                grid_region = optimizer.get_last_grid_region()
                if grid_region:
                    status[f"{objective}_grid_params"] = grid_region.get("all_params")
                    status[f"{objective}_grid_avg_metric"] = grid_region.get("avg_metric")
                    all_params = grid_region.get("all_params", [])
                    if all_params:
                        status[f"{objective}_display_params"] = all_params[0]

                # NOTE: translated to English.
                test_result = self._run_grid_test_backtest(
                    window["test_data"], grid_region, optimal_params, objective, silent=True
                )

                if test_result:
                    metrics = test_result.get("metrics", {})
                    status[f"{objective}_status"] = "成功"
                    status[f"{objective}_metric"] = metrics.get(objective)
                    status[f"{objective}_oos"] = metrics.get(objective)
                    status[f"{objective}_oos_return"] = metrics.get("total_return")

                    if status.get(f"{objective}_is_return") is None and train_metrics:
                        status[f"{objective}_is_return"] = train_metrics.get("total_return")

                    window_results[objective] = {
                        "indicator_config_id": config_id,  # NOTE: translated to English.
                        "indicator_params": indicator_params_config,
                        "optimal_params": optimal_params,
                        "grid_region": grid_region,
                        "train_metrics": train_metrics,
                        "test_result": test_result,
                        "window_info": {
                            "window_id": window["window_id"],
                            "train_start": window["train_start"],
                            "train_end": window["train_end"],
                            "test_start": window["test_start"],
                            "test_end": window["test_end"],
                        },
                    }
                else:
                    status[f"{objective}_status"] = "失敗"
                    status[f"{objective}_failure_reason"] = "測試回測失敗：無有效結果或無交易"

            return (window_results if window_results else None, status)

        except Exception as e:
            self.logger.error(f"處理配置 {config_id} 窗口 {current} 失敗: {e}")
            status["error"] = str(e)
            return (None, status)

    def _collect_results_by_config(
        self,
        all_config_results: Dict[str, Any],
        windows: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        按配置收集 WFA 結果（方案 B）

        Args:
            all_config_results: 按配置分組的結果 {config_id: {window_results, ...}}
            windows: 窗口列表

        Returns:
            Dict[str, Any]: 最終 WFA 結果
        """
        final_results = {
            "wfa_config": self.wfa_config,
            "total_windows": len(windows),
            "total_configs": len(all_config_results),
            "results_by_objective": {},
            "data": self.data,
            "contract_audit": dict(self.contract_audit),
        }

        # NOTE: translated to English.
        objectives = self.wfa_config.get("optimization_objectives", ["sharpe", "calmar"])

        for objective in objectives:
            objective_results = []

            # NOTE: translated to English.
            for config_id, config_data in all_config_results.items():
                window_results = config_data.get("window_results", [])

                # NOTE: translated to English.
                for window_result in window_results:
                    if objective in window_result:
                        # NOTE: translated to English.
                        objective_results.append(window_result[objective])

            final_results["results_by_objective"][objective] = objective_results

        return final_results

    def _display_results_summary_by_config(
        self,
        all_config_results: Dict[str, Any],
        windows: List[Dict[str, Any]],
    ) -> None:
        """
        顯示結果摘要表格（方案 B：按配置分組）

        Args:
            all_config_results: 按配置分組的結果
            windows: 窗口列表
        """
        from rich.table import Table

        # NOTE: translated to English.
        for config_id, config_data in all_config_results.items():
            window_results = config_data.get("window_results", [])
            window_status = config_data.get("window_status", [])

            # NOTE: translated to English.
            window_result_map = {}
            for window_result in window_results:
                for objective in ["sharpe", "calmar"]:
                    if objective in window_result:
                        window_id = window_result[objective].get("window_info", {}).get("window_id")
                        if window_id:
                            if window_id not in window_result_map:
                                window_result_map[window_id] = {}
                            window_result_map[window_id][objective] = window_result[objective]

            table = Table(
                title=f"📊 WFA 執行結果摘要 - {config_id}",
                show_lines=True,
                border_style="#dbac30",
            )
            table.add_column("窗口", style="cyan", no_wrap=True)
            table.add_column("日期範圍", style="white", no_wrap=False)
            table.add_column("訓練集大小", style="white")
            table.add_column("測試集大小", style="white")
            table.add_column("Sharpe 狀態", style="white")
            table.add_column("Sharpe IS", style="#1e90ff")
            table.add_column("Sharpe OOS", style="#1e90ff")
            table.add_column("Sharpe 最佳參數", style="yellow")
            table.add_column("Sharpe IS Return%", style="#1e90ff")
            table.add_column("Sharpe OOS Return%", style="#1e90ff")
            table.add_column("Calmar 狀態", style="white")
            table.add_column("Calmar IS", style="#1e90ff")
            table.add_column("Calmar OOS", style="#1e90ff")
            table.add_column("Calmar 最佳參數", style="yellow")
            table.add_column("Calmar IS Return%", style="#1e90ff")
            table.add_column("Calmar OOS Return%", style="#1e90ff")

            for status in window_status:
                window_id = status.get("window_id", "N/A")
                train_size = status.get("train_size", 0)
                test_size = status.get("test_size", 0)

                date_range_str = self._get_date_range_for_window(window_id, window_result_map)
                sharpe_status = status.get("sharpe_status", "未執行")
                sharpe_is = status.get("sharpe_is")
                sharpe_oos = status.get("sharpe_oos") or status.get("sharpe_metric")
                sharpe_is_return = status.get("sharpe_is_return")
                sharpe_oos_return = status.get("sharpe_oos_return")

                calmar_status = status.get("calmar_status", "未執行")
                calmar_is = status.get("calmar_is")
                calmar_oos = status.get("calmar_oos") or status.get("calmar_metric")
                calmar_is_return = status.get("calmar_is_return")
                calmar_oos_return = status.get("calmar_oos_return")

                sharpe_display_params = status.get("sharpe_display_params")
                if not sharpe_display_params:
                    sharpe_result = window_result_map.get(window_id, {}).get("sharpe")
                    sharpe_params_str = self._extract_params_for_display(sharpe_result)
                else:
                    sharpe_params_str = self._format_params_dict_simple(sharpe_display_params)

                calmar_display_params = status.get("calmar_display_params")
                if not calmar_display_params:
                    calmar_result = window_result_map.get(window_id, {}).get("calmar")
                    calmar_params_str = self._extract_params_for_display(calmar_result)
                else:
                    calmar_params_str = self._format_params_dict_simple(calmar_display_params)

                # NOTE: translated to English.
                if sharpe_status == "成功":
                    sharpe_status_display = f"[green]✅ {sharpe_status}[/green]"
                elif sharpe_status == "失敗":
                    reason = status.get("sharpe_failure_reason", "未知原因")
                    if len(reason) > 30:
                        reason = reason[:27] + "..."
                    sharpe_status_display = f"[red]❌ 失敗 ({reason})[/red]"
                else:
                    sharpe_status_display = f"[yellow]⚠️ {sharpe_status}[/yellow]"

                if calmar_status == "成功":
                    calmar_status_display = f"[green]✅ {calmar_status}[/green]"
                elif calmar_status == "失敗":
                    reason = status.get("calmar_failure_reason", "未知原因")
                    if len(reason) > 30:
                        reason = reason[:27] + "..."
                    calmar_status_display = f"[red]❌ 失敗 ({reason})[/red]"
                else:
                    calmar_status_display = f"[yellow]⚠️ {calmar_status}[/yellow]"

                sharpe_is_value = (
                    f"[#1e90ff]{sharpe_is:.4f}[/#1e90ff]"
                    if sharpe_is is not None
                    else "N/A"
                )
                sharpe_oos_value = (
                    f"[#1e90ff]{sharpe_oos:.4f}[/#1e90ff]"
                    if sharpe_oos is not None
                    else "N/A"
                )
                sharpe_is_return_value = (
                    f"[#1e90ff]{sharpe_is_return*100:.2f}%[/#1e90ff]"
                    if sharpe_is_return is not None
                    else "N/A"
                )
                sharpe_oos_return_value = (
                    f"[#1e90ff]{sharpe_oos_return*100:.2f}%[/#1e90ff]"
                    if sharpe_oos_return is not None
                    else "N/A"
                )
                calmar_is_value = (
                    f"[#1e90ff]{calmar_is:.4f}[/#1e90ff]"
                    if calmar_is is not None
                    else "N/A"
                )
                calmar_oos_value = (
                    f"[#1e90ff]{calmar_oos:.4f}[/#1e90ff]"
                    if calmar_oos is not None
                    else "N/A"
                )
                calmar_is_return_value = (
                    f"[#1e90ff]{calmar_is_return*100:.2f}%[/#1e90ff]"
                    if calmar_is_return is not None
                    else "N/A"
                )
                calmar_oos_return_value = (
                    f"[#1e90ff]{calmar_oos_return*100:.2f}%[/#1e90ff]"
                    if calmar_oos_return is not None
                    else "N/A"
                )

                table.add_row(
                    str(window_id),
                    date_range_str,
                    str(train_size),
                    str(test_size),
                    sharpe_status_display,
                    sharpe_is_value,
                    sharpe_oos_value,
                    sharpe_params_str,
                    sharpe_is_return_value,
                    sharpe_oos_return_value,
                    calmar_status_display,
                    calmar_is_value,
                    calmar_oos_value,
                    calmar_params_str,
                    calmar_is_return_value,
                    calmar_oos_return_value,
                )

            console.print(table)

            # NOTE: translated to English.
            total_windows = len(window_status)
            sharpe_success = sum(
                1 for s in window_status if s.get("sharpe_status") == "成功"
            )
            calmar_success = sum(
                1 for s in window_status if s.get("calmar_status") == "成功"
            )

            show_info("WFANALYSER",
                f"📊 {config_id} 統計信息:\n"
                f"   總窗口數: {total_windows}\n"
                f"   Sharpe 成功: {sharpe_success}/{total_windows}\n"
                f"   Calmar 成功: {calmar_success}/{total_windows}"
            )
