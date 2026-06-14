"""Unified vector-hybrid backtest runner facade.

This facade is the runtime boundary for strategies that are already expressible
as target weights or portfolio policies.  It keeps the autorunner thin while the
NodeIR/native runtime owns supported single-asset execution.
"""

from __future__ import annotations

import copy
import logging
import os
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from backtester.MultiAssetPortfolioEngine_backtester import (
    MultiAssetBacktestResult,
    MultiAssetPortfolioEngineBacktester,
)
from backtester.MultiAssetPortfolioExporter_backtester import (
    MultiAssetPortfolioExporterBacktester,
)
from backtester.SingleAssetPortfolioAdapter_backtester import (
    run_single_asset_signals_as_portfolio,
)
from backtester.StrategyRunConfig_backtester import (
    StrategyRunConfigError,
    normalize_strategy_run_config,
    plan_strategy_execution,
)

MarketDataLoader = Callable[[Any, Optional[str]], Dict[str, pd.DataFrame]]
PortfolioVariantExpander = Callable[[Dict[str, Any]], List[Dict[str, Any]]]
PathResolver = Callable[[Any, Optional[str]], Optional[Path]]
SymbolResolver = Callable[[Dict[str, Any]], str]


class UnifiedBacktestRunnerBacktester:
    """Run single-as-portfolio and multi-asset portfolio strategies."""

    def __init__(
        self,
        *,
        logger: Optional[logging.Logger] = None,
        market_data_loader: Optional[MarketDataLoader] = None,
        portfolio_variant_expander: Optional[PortfolioVariantExpander] = None,
        path_resolver: Optional[PathResolver] = None,
        symbol_resolver: Optional[SymbolResolver] = None,
    ) -> None:
        self.logger = logger or logging.getLogger("lo2cin4bt.backtester.unified")
        self.market_data_loader = market_data_loader
        self.portfolio_variant_expander = portfolio_variant_expander or self._default_variant_expander
        self.path_resolver = path_resolver
        self.symbol_resolver = symbol_resolver or (lambda _dataloader: "X")

    def run(
        self,
        *,
        data: Optional[pd.DataFrame],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        backtester_raw = dict((config or {}).get("backtester") or {})
        dataloader_config = dict((config or {}).get("dataloader") or {})
        mode = self._resolve_mode(config)
        if mode == "single_asset_portfolio":
            return self._run_single_asset_portfolio(
                data=data,
                backtester_raw=backtester_raw,
                dataloader_config=dataloader_config,
                raw_config=config,
            )
        if mode == "multi_asset_portfolio":
            return self._run_multi_asset_portfolio(
                backtester_raw=backtester_raw,
                dataloader_config=dataloader_config,
                raw_config=config,
            )
        raise ValueError(f"UnifiedBacktestRunner does not support mode={mode}")

    def _run_single_asset_portfolio(
        self,
        *,
        data: Optional[pd.DataFrame],
        backtester_raw: Dict[str, Any],
        dataloader_config: Dict[str, Any],
        raw_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized_input = dict(raw_config or {}).get("schema_version") == "strategy_run"
        if (not isinstance(data, pd.DataFrame) or data.empty) and normalized_input:
            data = self._load_single_asset_dataframe_from_normalized(
                raw_config=raw_config,
                dataloader_config=dataloader_config,
                backtester_raw=backtester_raw,
            )
        if not isinstance(data, pd.DataFrame) or data.empty:
            raise ValueError("single_asset_portfolio requires a non-empty input dataframe")

        entry_col = str(backtester_raw.get("entry_signal_column") or "entry_signal")
        exit_col = str(backtester_raw.get("exit_signal_column") or "exit_signal")
        symbol = str(
            backtester_raw.get("symbol")
            or ((raw_config.get("universe") or {}).get("symbols") or [None])[0]
            or self.symbol_resolver(dataloader_config)
        )
        if normalized_input and (entry_col not in data.columns or exit_col not in data.columns):
            return self._run_normalized_single_asset_signal(
                data=data,
                raw_config=raw_config,
                symbol=symbol,
                backtester_raw=backtester_raw,
                dataloader_config=dataloader_config,
            )
        if entry_col not in data.columns or exit_col not in data.columns:
            raise ValueError(
                "single_asset_portfolio requires entry/exit signal columns: "
                f"{entry_col}, {exit_col}"
            )

        execution = backtester_raw.get("execution", {})
        if not isinstance(execution, dict) or not execution:
            execution = {
                "cost": backtester_raw.get(
                    "cost",
                    {"transaction_cost": 0.0, "slippage": 0.0},
                )
            }

        result = run_single_asset_signals_as_portfolio(
            price_data=data,
            symbol=symbol,
            entry_signal=data[entry_col],
            exit_signal=data[exit_col],
            strategy_id=str(backtester_raw.get("Backtest_id") or "single_asset_portfolio"),
            target_weight=self._float(backtester_raw.get("target_weight", 1.0), default=1.0),
            execution=execution,
        )
        export_config = dict(backtester_raw.get("export_config") or {})
        exported_files = self._export_portfolio_result(
            result=result,
            export_config=export_config,
            run_id=str(backtester_raw.get("Backtest_id") or result.strategy_id),
        )
        execution_plan = self._best_effort_execution_plan(raw_config, fallback_mode="single_asset_signal")

        return {
            "success": True,
            "strategy_mode": "single_asset_portfolio",
            "results": [],
            "portfolio_result": result,
            "portfolio_results": [result],
            "data_shape": result.equity_curve.shape,
            "config": result.config,
            "trading_params": {},
            "predictor_column": None,
            "symbol": symbol,
            "predictor_file_name": None,
            "frequency": dataloader_config.get("frequency", "1D"),
            "export_config": export_config,
            "Backtest_id": str(backtester_raw.get("Backtest_id") or ""),
            "requested_engine_mode": "single_asset_portfolio",
            "resolved_engine_mode": "unified_vector_hybrid",
            "sequential_requirements": ["portfolio_accounting"],
            "engine_capabilities": self._capabilities(single_asset=True),
            "execution_plan": execution_plan,
            "exported_files": exported_files,
        }

    def _run_normalized_single_asset_signal(
        self,
        *,
        data: pd.DataFrame,
        raw_config: Dict[str, Any],
        symbol: str,
        backtester_raw: Dict[str, Any],
        dataloader_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        legacy_backtester = dict(
            ((raw_config.get("metadata") or {}).get("legacy_backtester") or {})
            if isinstance(raw_config.get("metadata"), dict)
            else {}
        )
        portfolio_config = self._portfolio_config_from_normalized(raw_config)
        portfolio_config["strategy_id"] = str(
            portfolio_config.get("strategy_id")
            or backtester_raw.get("Backtest_id")
            or legacy_backtester.get("Backtest_id")
            or "single_asset_signal"
        )
        market_data = self._single_asset_market_frames(data, symbol)
        export_config = dict(backtester_raw.get("export_config") or legacy_backtester.get("export_config") or {})
        variants = self._portfolio_variants_for_workflow(
            portfolio_config=portfolio_config,
            raw_config=raw_config,
        )
        portfolio_results, exported_files = self._run_portfolio_variant_batch(
            variants=variants,
            market_data=market_data,
            export_config=export_config,
            run_id_base=str(
                backtester_raw.get("Backtest_id")
                or legacy_backtester.get("Backtest_id")
                or portfolio_config.get("strategy_id")
                or "single_asset_signal"
            ),
            cache_dir=None,
            portfolio_config=portfolio_config,
        )
        if not portfolio_results:
            raise ValueError("single_asset_portfolio produced no portfolio variants")
        result = portfolio_results[0]
        execution_plan = self._best_effort_execution_plan(raw_config, fallback_mode="single_asset_signal")
        return {
            "success": True,
            "strategy_mode": "single_asset_portfolio",
            "results": [],
            "portfolio_result": result,
            "portfolio_results": portfolio_results,
            "data_shape": result.equity_curve.shape,
            "config": portfolio_config,
            "trading_params": {},
            "predictor_column": None,
            "symbol": symbol,
            "predictor_file_name": None,
            "frequency": dataloader_config.get("frequency", raw_config.get("data", {}).get("frequency", "1D")),
            "export_config": export_config,
            "Backtest_id": str(backtester_raw.get("Backtest_id") or ""),
            "requested_engine_mode": "strategy_run",
            "resolved_engine_mode": "unified_vector_hybrid",
            "sequential_requirements": ["portfolio_accounting"],
            "engine_capabilities": self._capabilities(single_asset=True),
            "execution_plan": execution_plan,
            "exported_files": exported_files,
        }

    def _run_multi_asset_portfolio(
        self,
        *,
        backtester_raw: Dict[str, Any],
        dataloader_config: Dict[str, Any],
        raw_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        if self.market_data_loader is None:
            raise ValueError("multi_asset_portfolio requires a market_data_loader callback")
        normalized_input = dict(raw_config or {}).get("schema_version") == "strategy_run"
        legacy_backtester = dict(
            ((raw_config.get("metadata") or {}).get("legacy_backtester") or {})
            if isinstance(raw_config.get("metadata"), dict)
            else {}
        )
        portfolio_config = (
            self._portfolio_config_from_normalized(raw_config)
            if normalized_input
            else dict(backtester_raw.get("portfolio_config") or {})
        )
        if not portfolio_config:
            raise ValueError("backtester.portfolio_config is required for multi_asset_portfolio")

        config_file_path = backtester_raw.get("__config_file_path") or dataloader_config.get("__config_file_path")
        market_data_spec = (
            backtester_raw.get("market_data", {})
            or legacy_backtester.get("market_data", {})
            or raw_config.get("market_data", {})
            or (raw_config.get("data") or {}).get("market_data", {})
            or (
                self._market_data_spec_from_normalized(raw_config)
                if normalized_input
                else {}
            )
        )
        market_data = self.market_data_loader(
            market_data_spec,
            config_file_path if isinstance(config_file_path, str) else None,
        )
        cache_dir = None
        if self.path_resolver is not None:
            cache_config = portfolio_config.get("indicator_cache") or portfolio_config.get("feature_cache") or {}
            cache_dir = self.path_resolver(
                (cache_config.get("path") if isinstance(cache_config, dict) else None),
                config_file_path if isinstance(config_file_path, str) else None,
            )

        export_config = dict(backtester_raw.get("export_config") or legacy_backtester.get("export_config") or {})
        variants = self._portfolio_variants_for_workflow(
            portfolio_config=portfolio_config,
            raw_config=raw_config,
        )
        portfolio_results, exported_files = self._run_portfolio_variant_batch(
            variants=variants,
            market_data=market_data,
            export_config=export_config,
            run_id_base=str(backtester_raw.get("Backtest_id") or portfolio_config.get("strategy_id") or "portfolio"),
            cache_dir=cache_dir,
            portfolio_config=portfolio_config,
        )
        if not portfolio_results:
            raise ValueError("multi_asset_portfolio produced no portfolio variants")
        result = portfolio_results[0]
        execution_plan = self._best_effort_execution_plan(raw_config, fallback_mode="multi_asset_portfolio")

        return {
            "success": True,
            "strategy_mode": "multi_asset_portfolio",
            "results": [],
            "portfolio_result": result,
            "portfolio_results": portfolio_results,
            "data_shape": result.equity_curve.shape,
            "config": portfolio_config,
            "trading_params": {},
            "predictor_column": None,
            "symbol": "PORTFOLIO",
            "predictor_file_name": None,
            "frequency": dataloader_config.get(
                "frequency",
                portfolio_config.get("data_context", {}).get("frequency", "1D"),
            ),
            "export_config": export_config,
            "Backtest_id": str(backtester_raw.get("Backtest_id") or ""),
            "requested_engine_mode": "multi_asset_portfolio",
            "resolved_engine_mode": "unified_vector_hybrid",
            "sequential_requirements": ["portfolio_accounting"],
            "engine_capabilities": self._capabilities(single_asset=False),
            "execution_plan": execution_plan,
            "exported_files": exported_files,
        }

    def _export_portfolio_result(
        self,
        *,
        result: MultiAssetBacktestResult,
        export_config: Dict[str, Any],
        run_id: str,
    ) -> List[str]:
        output_dir = export_config.get("output_dir") if isinstance(export_config, dict) else None
        return MultiAssetPortfolioExporterBacktester(
            result=result,
            output_dir=output_dir,
            run_id=run_id,
            export_csv=_as_bool(export_config.get("export_csv", False)) if isinstance(export_config, dict) else False,
        ).export()

    def _run_portfolio_variant_batch(
        self,
        *,
        variants: List[Dict[str, Any]],
        market_data: Dict[str, pd.DataFrame],
        export_config: Dict[str, Any],
        run_id_base: str,
        cache_dir: Optional[Path],
        portfolio_config: Dict[str, Any],
    ) -> tuple[List[MultiAssetBacktestResult], List[str]]:
        if not variants:
            return [], []

        workers = self._matrix_workers(portfolio_config, len(variants))

        def run_one(variant: Dict[str, Any]) -> tuple[MultiAssetBacktestResult, List[str]]:
            variant_config = dict(variant["config"])
            result = MultiAssetPortfolioEngineBacktester(
                market_data=market_data,
                config=variant_config,
                cache_dir=cache_dir,
            ).run()
            export_run_id = "_".join(
                item
                for item in [
                    str(run_id_base or "portfolio"),
                    str(variant.get("suffix") or ""),
                ]
                if item
            )
            exported = self._export_portfolio_result(
                result=result,
                export_config=export_config,
                run_id=export_run_id,
            )
            return result, exported

        retain_limit = self._matrix_result_retention(portfolio_config, len(variants))
        portfolio_results: List[MultiAssetBacktestResult] = []
        exported_files: List[str] = []

        def collect(result: MultiAssetBacktestResult, paths: List[str], completed: int) -> None:
            if len(portfolio_results) < retain_limit:
                portfolio_results.append(result)
            exported_files.extend(paths)
            if completed % 100 == 0 or completed == len(variants):
                self.logger.info(
                    "Portfolio matrix progress: %s/%s variants exported (%s files)",
                    completed,
                    len(variants),
                    len(exported_files),
                )

        if workers <= 1 or len(variants) <= 1:
            for completed, variant in enumerate(variants, start=1):
                result, paths = run_one(variant)
                collect(result, paths, completed)
        else:
            self.logger.info(
                "Running portfolio matrix batch with %s workers for %s variants",
                workers,
                len(variants),
            )
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="portfolio-matrix") as pool:
                pending: Dict[Future[tuple[MultiAssetBacktestResult, List[str]]], None] = {}
                variant_iter = iter(variants)

                def submit_next() -> bool:
                    try:
                        variant = next(variant_iter)
                    except StopIteration:
                        return False
                    pending[pool.submit(run_one, variant)] = None
                    return True

                inflight_limit = max(1, workers * 2)
                for _ in range(min(inflight_limit, len(variants))):
                    submit_next()

                completed = 0
                while pending:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        pending.pop(future, None)
                        result, paths = future.result()
                        completed += 1
                        collect(result, paths, completed)
                        submit_next()
        return portfolio_results, exported_files

    def _portfolio_variants_for_workflow(
        self,
        *,
        portfolio_config: Dict[str, Any],
        raw_config: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Expand parameters only when the workflow explicitly asks for a matrix.

        A selected single run, WFA OOS pass, or rolling validation may carry
        parameter_domains for auditability.  Those domains must not implicitly
        fan out into a full matrix unless workflow_id=parameter_matrix.
        """

        if dict(raw_config or {}).get("schema_version") != "strategy_run":
            return self.portfolio_variant_expander(portfolio_config)

        platform = raw_config.get("platform") if isinstance(raw_config.get("platform"), dict) else {}
        workflow_id = str(platform.get("workflow_id") or "").strip().lower()
        resolved_params = portfolio_config.get("resolved_params")
        if isinstance(resolved_params, dict) and resolved_params:
            params = dict(resolved_params)
            variant_config = self._replace_param_refs(copy.deepcopy(portfolio_config), params)
            variant_config["resolved_params"] = params
            base_strategy_id = str(portfolio_config.get("strategy_id") or "portfolio")
            suffix = "_".join(f"{key}_{self._slug_value(value)}" for key, value in params.items())
            if suffix and suffix not in base_strategy_id:
                variant_config["strategy_id"] = f"{base_strategy_id}_{suffix}"
            return [{"config": variant_config, "suffix": suffix}]

        if workflow_id != "parameter_matrix":
            return [{"config": dict(portfolio_config), "suffix": ""}]

        return self.portfolio_variant_expander(portfolio_config)

    def _replace_param_refs(self, value: Any, params: Dict[str, Any]) -> Any:
        if isinstance(value, dict):
            if set(value.keys()) == {"param_ref"}:
                ref = str(value.get("param_ref"))
                return params.get(ref, value)
            return {key: self._replace_param_refs(item, params) for key, item in value.items()}
        if isinstance(value, list):
            return [self._replace_param_refs(item, params) for item in value]
        if isinstance(value, str):
            resolved = value
            for key, param_value in params.items():
                resolved = resolved.replace("{" + str(key) + "}", str(param_value))
            return resolved
        return value

    @staticmethod
    def _slug_value(value: Any) -> str:
        return str(value).replace(".", "p").replace("-", "m").replace(" ", "_")

    @staticmethod
    def _matrix_workers(portfolio_config: Dict[str, Any], variant_count: int) -> int:
        if variant_count < 4:
            return 1
        execution = portfolio_config.get("fill_model", {}) if isinstance(portfolio_config.get("fill_model"), dict) else {}
        raw_value = (
            execution.get("matrix_workers")
            or portfolio_config.get("matrix_workers")
            or os.environ.get("LO2CIN4BT_MATRIX_WORKERS")
        )
        if raw_value is not None:
            try:
                parsed = int(raw_value)
            except (TypeError, ValueError):
                parsed = 1
            return max(1, min(parsed, variant_count))
        cpu_count = os.cpu_count() or 2
        return max(1, min(8, cpu_count, variant_count))

    @staticmethod
    def _matrix_result_retention(portfolio_config: Dict[str, Any], variant_count: int) -> int:
        execution = portfolio_config.get("fill_model", {}) if isinstance(portfolio_config.get("fill_model"), dict) else {}
        raw_value = (
            execution.get("matrix_result_retention")
            or portfolio_config.get("matrix_result_retention")
            or os.environ.get("LO2CIN4BT_MATRIX_RESULT_RETENTION")
        )
        if raw_value is not None:
            try:
                parsed = int(raw_value)
            except (TypeError, ValueError):
                parsed = 1
            return max(1, min(parsed, variant_count))
        if variant_count > 500:
            return 25
        return variant_count

    def _best_effort_execution_plan(
        self,
        raw_config: Dict[str, Any],
        *,
        fallback_mode: str,
    ) -> Dict[str, Any]:
        try:
            return plan_strategy_execution(normalize_strategy_run_config(raw_config))
        except (StrategyRunConfigError, ValueError, KeyError):
            return {
                "schema_version": "execution_plan.v1",
                "strategy_mode_id": fallback_mode,
                "execution_backend": "vector_hybrid",
                "accounting_backend": "sequential",
                "requires_portfolio_accounting": True,
                "reason": "runtime facade fallback for pre-normalized autorunner config",
            }

    @staticmethod
    def _resolve_mode(config: Dict[str, Any]) -> str:
        if dict(config or {}).get("schema_version") == "strategy_run":
            mode = str(
                ((config or {}).get("platform") or {}).get("strategy_mode_id") or ""
            ).strip().lower()
            if mode in {"single_asset_signal", "calendar_event_session", "multi_factor_entry_exit_roles"}:
                return "single_asset_portfolio"
            if mode in {"multi_asset_portfolio", "multi_asset_trigger_selection", "dynamic_allocation_rules"}:
                return "multi_asset_portfolio"
        backtester = dict((config or {}).get("backtester") or {})
        mode = str(backtester.get("strategy_mode") or "").strip().lower()
        if mode in {"single_asset_portfolio", "multi_asset_portfolio"}:
            return mode
        raise ValueError("UnifiedBacktestRunner requires a portfolio-accounting strategy mode")

    @staticmethod
    def _portfolio_config_from_normalized(config: Dict[str, Any]) -> Dict[str, Any]:
        metadata = config.get("metadata") if isinstance(config.get("metadata"), dict) else {}
        platform = config.get("platform") if isinstance(config.get("platform"), dict) else {}
        data = config.get("data") if isinstance(config.get("data"), dict) else {}
        legacy_backtester = (
            metadata.get("legacy_backtester")
            if isinstance(metadata.get("legacy_backtester"), dict)
            else {}
        )
        legacy_portfolio = (
            legacy_backtester.get("portfolio_config")
            if isinstance(legacy_backtester.get("portfolio_config"), dict)
            else {}
        )
        fill_model = config.get("fill_model") if isinstance(config.get("fill_model"), dict) else {}
        if config.get("execution"):
            raise ValueError(
                "strategy_run uses fill_model{} only; execution{} is a removed alias."
            )
        execution = dict(fill_model)
        legacy_execution = (
            legacy_portfolio.get("execution")
            if isinstance(legacy_portfolio.get("execution"), dict)
            else {}
        )
        if "accounting_backend" not in execution and isinstance(legacy_execution, dict):
            if legacy_execution.get("accounting_backend"):
                execution["accounting_backend"] = legacy_execution.get("accounting_backend")
        if config.get("features") or config.get("indicators"):
            raise ValueError(
                "strategy_run uses computed_fields[] only; features[] and indicators[] are removed aliases."
            )
        computed_fields = list(config.get("computed_fields") or [])
        return {
            "schema_version": "multi_asset_portfolio.v1",
            "strategy_id": str(metadata.get("strategy_id") or platform.get("display_label") or "strategy_run"),
            "universe": dict(config.get("universe") or {}),
            "benchmark": data.get("benchmark") or legacy_portfolio.get("benchmark"),
            "data_context": {
                "frequency": data.get("frequency"),
                "calendar": data.get("calendar"),
                "timezone": data.get("timezone"),
            },
            "indicator_cache": dict(
                config.get("indicator_cache")
                or legacy_portfolio.get("indicator_cache")
                or legacy_portfolio.get("feature_cache")
                or {}
            ),
            "factor_pipeline": dict(config.get("factor_pipeline") or {}),
            "computed_fields": computed_fields,
            "signals": dict(config.get("signals") or {}),
            "selection": dict(config.get("selection") or {}),
            "allocation": dict(config.get("allocation") or {}),
            "rebalance": dict(config.get("rebalance") or {}),
            "fill_model": execution,
            "risk": dict(config.get("risk") or {}),
            "parameter_domains": dict(config.get("parameter_domains") or {}),
            "resolved_params": dict(config.get("resolved_params") or {}),
            "outputs": dict(config.get("outputs") or {}),
        }

    @staticmethod
    def _market_data_spec_from_normalized(config: Dict[str, Any]) -> Dict[str, Any]:
        data = config.get("data") if isinstance(config.get("data"), dict) else {}
        universe = config.get("universe") if isinstance(config.get("universe"), dict) else {}
        symbols = [
            str(item).strip().upper()
            for item in universe.get("symbols", [])
            if str(item).strip()
        ]
        provider = str(data.get("provider") or data.get("source") or "").strip().lower()
        if not provider or not symbols:
            return {}
        spec: Dict[str, Any] = {
            "provider": provider,
            "symbols": symbols,
            "start_date": data.get("start_date") or data.get("start"),
            "end_date": data.get("end_date") or data.get("end"),
            "interval": data.get("interval") or data.get("frequency"),
            "frequency": data.get("frequency"),
            "start_policy": data.get("start_policy"),
            "calendar": data.get("calendar"),
            "timezone": data.get("timezone"),
        }
        return {key: value for key, value in spec.items() if value not in (None, "", [])}

    def _load_single_asset_dataframe_from_normalized(
        self,
        *,
        raw_config: Dict[str, Any],
        dataloader_config: Dict[str, Any],
        backtester_raw: Dict[str, Any],
    ) -> pd.DataFrame:
        if self.market_data_loader is None:
            raise ValueError("single_asset_signal requires data or a market_data_loader callback")
        symbols = [
            str(item).strip().upper()
            for item in ((raw_config.get("universe") or {}).get("symbols") or [])
            if str(item).strip()
        ]
        if len(symbols) != 1:
            raise ValueError("single_asset_signal expects exactly one universe symbol")
        config_file_path = backtester_raw.get("__config_file_path") or dataloader_config.get("__config_file_path")
        market_data = self.market_data_loader(
            self._market_data_spec_from_normalized(raw_config),
            config_file_path if isinstance(config_file_path, str) else None,
        )
        if not isinstance(market_data, dict) or not market_data:
            raise ValueError("single_asset_signal market data loader returned no frames")
        symbol = symbols[0]
        close_frame = market_data.get("close")
        if not isinstance(close_frame, pd.DataFrame) or symbol not in close_frame.columns:
            raise KeyError(f"single_asset_signal requires close prices for {symbol}")
        out = pd.DataFrame(index=pd.to_datetime(close_frame.index, errors="coerce"))
        for field in ["open", "high", "low", "close", "volume"]:
            frame = market_data.get(field)
            if isinstance(frame, pd.DataFrame) and symbol in frame.columns:
                out[field] = pd.to_numeric(frame[symbol], errors="coerce")
        out = out.loc[~pd.isna(out.index)].sort_index()
        if "close" not in out.columns:
            raise KeyError(f"single_asset_signal requires close prices for {symbol}")
        for fallback_field in ["open", "high", "low"]:
            if fallback_field not in out.columns:
                out[fallback_field] = out["close"]
        return out

    @staticmethod
    def _single_asset_market_frames(data: pd.DataFrame, symbol: str) -> Dict[str, pd.DataFrame]:
        frame = data.copy()
        lower_columns = {str(col).lower(): col for col in frame.columns}
        time_col = lower_columns.get("time") or lower_columns.get("date")
        if time_col is not None:
            frame.index = pd.to_datetime(frame[time_col], errors="coerce")
            frame = frame.loc[~pd.isna(frame.index)].copy()
        else:
            frame.index = pd.to_datetime(frame.index, errors="coerce")
            frame = frame.loc[~pd.isna(frame.index)].copy()
        frame.index = pd.DatetimeIndex(frame.index).tz_localize(None).normalize()
        frame = frame.sort_index()
        frames: Dict[str, pd.DataFrame] = {}
        for field in ["open", "high", "low", "close", "volume"]:
            source_col = lower_columns.get(field)
            if source_col is None and field == "close":
                source_col = lower_columns.get("adj close")
            if source_col is None:
                continue
            frames[field] = pd.DataFrame(
                {str(symbol): pd.to_numeric(frame[source_col], errors="coerce")},
                index=frame.index,
            )
        if "close" not in frames:
            raise KeyError("single-asset strategy_run execution requires a close price column")
        frames.setdefault("open", frames["close"])
        return frames

    @staticmethod
    def _capabilities(*, single_asset: bool) -> Dict[str, Any]:
        return {
            "single_asset_as_portfolio": bool(single_asset),
            "multi_asset": not bool(single_asset),
            "calendar_rebalance": True,
            "explicit_target_weight_frame": True,
            "signal_state": True,
            "top_n_selection": True,
            "factorhandler_rank_by": True,
            "portfolio_accounting": True,
            "vector_hybrid": True,
        }

    @staticmethod
    def _default_variant_expander(portfolio_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [{"config": dict(portfolio_config), "suffix": ""}]

    @staticmethod
    def _float(value: Any, *, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y"}:
            return True
        if lowered in {"0", "false", "no", "n", ""}:
            return False
    return False
