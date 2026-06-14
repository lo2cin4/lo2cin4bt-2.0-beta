#!/usr/bin/env python3
"""Backtest runner for autorunner."""

from __future__ import annotations

import logging
import copy
import itertools
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from autorunner.StrategyCompiler import StrategyCompiler
from backtester.NodeIRExecutor_backtester import NodeIRExecutorBacktester
from utils import show_error, show_info
from utils.path_resolver import resolve_input_path


class BacktestRunnerAutorunner:
    """Run backtests from autorunner config."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("lo2cin4bt.autorunner.backtest")
        self.compiler = StrategyCompiler()
        self.project_root = Path(__file__).resolve().parent.parent

    def run_backtest(self, data, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            if self._is_strategy_run(config):
                from backtester.UnifiedBacktestRunner_backtester import (
                    UnifiedBacktestRunnerBacktester,
                )

                return UnifiedBacktestRunnerBacktester(
                    logger=self.logger,
                    market_data_loader=(
                        lambda spec, config_file_path: self._load_multi_asset_market_data(
                            spec,
                            config_file_path=config_file_path,
                        )
                    ),
                    portfolio_variant_expander=self._expand_portfolio_configs,
                    path_resolver=(
                        lambda raw_path, config_file_path: self._resolve_optional_path(
                            raw_path,
                            config_file_path=config_file_path,
                        )
                    ),
                    symbol_resolver=self._extract_symbol,
                ).run(
                    data=data,
                    config=config,
                )

            backtester_raw = config.get("backtester", {})
            strategy_run_config = backtester_raw.get("strategy_run_config")
            if (
                isinstance(strategy_run_config, dict)
                and strategy_run_config.get("schema_version") == "strategy_run"
            ):
                from backtester.UnifiedBacktestRunner_backtester import (
                    UnifiedBacktestRunnerBacktester,
                )

                return UnifiedBacktestRunnerBacktester(
                    logger=self.logger,
                    market_data_loader=(
                        lambda spec, config_file_path: self._load_multi_asset_market_data(
                            spec,
                            config_file_path=config_file_path,
                        )
                    ),
                    portfolio_variant_expander=self._expand_portfolio_configs,
                    path_resolver=(
                        lambda raw_path, config_file_path: self._resolve_optional_path(
                            raw_path,
                            config_file_path=config_file_path,
                        )
                    ),
                    symbol_resolver=self._extract_symbol,
                ).run(
                    data=data,
                    config=copy.deepcopy(strategy_run_config),
                )

            dataloader_config = config.get("dataloader", {})
            strategy_mode = self._resolve_strategy_mode(backtester_raw)

            if strategy_mode in {"multi_asset_portfolio", "single_asset_portfolio"}:
                from backtester.UnifiedBacktestRunner_backtester import (
                    UnifiedBacktestRunnerBacktester,
                )

                return UnifiedBacktestRunnerBacktester(
                    logger=self.logger,
                    market_data_loader=(
                        lambda spec, config_file_path: self._load_multi_asset_market_data(
                            spec,
                            config_file_path=config_file_path,
                        )
                    ),
                    portfolio_variant_expander=self._expand_portfolio_configs,
                    path_resolver=(
                        lambda raw_path, config_file_path: self._resolve_optional_path(
                            raw_path,
                            config_file_path=config_file_path,
                        )
                    ),
                    symbol_resolver=self._extract_symbol,
                ).run(
                    data=data,
                    config=config,
                )

            compile_result = None
            if strategy_mode == "semantic":
                compile_result = self._compile_strategy_contract(backtester_raw)
                if compile_result is None:
                    return None

            backtester_config = self._convert_config(config, compile_result=compile_result)
            predictor_path = (
                dataloader_config.get("predictor_config", {}).get("predictor_path", "")
            )
            predictor_file_name = Path(predictor_path).stem if predictor_path else None
            predictor_column = backtester_raw.get("selected_predictor", "X")
            symbol = self._extract_symbol(dataloader_config)

            requested_engine_mode = backtester_config.get("engine_mode", "node_ir")
            engine_description = {
                "resolved_mode": compile_result["execution_plan"]
                .get("engine_resolution", {})
                .get("resolved_mode", "node_ir"),
                "sequential_requirements": compile_result["execution_plan"]
                .get("engine_resolution", {})
                .get("sequential_requirements", []),
                "capabilities": {},
            }
            results = self._run_node_ir(
                data=data,
                compile_result=compile_result,
                backtester_config=backtester_config,
                predictor_column=predictor_column,
                symbol=symbol,
            )
            exported_files = self._export_results(
                data=data,
                results=results,
                backtester_config=backtester_config,
                dataloader_config=dataloader_config,
                predictor_file_name=predictor_file_name,
                predictor_column=predictor_column,
                symbol=symbol,
            )

            final_results = {
                "success": True,
                "strategy_mode": strategy_mode,
                "results": results,
                "data_shape": data.shape,
                "config": backtester_config,
                "trading_params": backtester_config.get("trading_params", {}),
                "predictor_column": predictor_column,
                "symbol": symbol,
                "predictor_file_name": predictor_file_name,
                "frequency": dataloader_config.get("frequency", "1D"),
                "export_config": backtester_config.get("export_config", {}),
                "Backtest_id": backtester_config.get("Backtest_id", ""),
                "requested_engine_mode": requested_engine_mode,
                "resolved_engine_mode": engine_description.get("resolved_mode"),
                "sequential_requirements": engine_description.get(
                    "sequential_requirements", []
                ),
                "engine_capabilities": engine_description.get("capabilities", {}),
                "execution_plan_path": (
                    compile_result["execution_plan_path"] if compile_result else None
                ),
                "unknown_unknowns": (
                    compile_result["execution_plan"].get("unknown_unknowns", [])
                    if compile_result
                    else []
                ),
                "field_symbol_table": (
                    compile_result["execution_plan"].get("field_symbol_table", [])
                    if compile_result
                    else []
                ),
                "exported_files": exported_files,
            }
            return final_results
        except Exception as exc:  # pragma: no cover - defensive
            show_error("BACKTESTER", f"回測執行失敗: {exc}")
            return None

    def _run_node_ir(
        self,
        *,
        data: pd.DataFrame,
        compile_result: Dict[str, Any],
        backtester_config: Dict[str, Any],
        predictor_column: str,
        symbol: str,
    ) -> list:
        plan = compile_result["execution_plan"]
        strategy_contract_path = plan.get("strategy_contract_path")
        feature_contract_path = plan.get("feature_contract_path")
        if not isinstance(strategy_contract_path, str) or not strategy_contract_path:
            raise ValueError("execution_plan.strategy_contract_path is required")

        show_info("BACKTESTER", "使用 semantic node_ir 直跑路徑")
        executor = NodeIRExecutorBacktester(data=data, logger=self.logger)
        return executor.run_from_paths(
            strategy_contract_path=strategy_contract_path,
            feature_contract_path=(
                feature_contract_path if isinstance(feature_contract_path, str) else None
            ),
            execution_plan=plan,
            trading_params={
                **(backtester_config.get("trading_params", {}) or {}),
                "execution_backend": backtester_config.get("execution_backend", "auto"),
                "chunk_size": backtester_config.get("chunk_size", 64),
                "max_workers": backtester_config.get("max_workers", 1),
            },
            predictor_column=predictor_column,
            symbol=symbol,
            backtest_id_prefix=backtester_config.get("Backtest_id", "") or "semantic",
        )

    def _compile_strategy_contract(self, backtester_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        strategy_path = backtester_config.get("strategy_contract_path")
        if not isinstance(strategy_path, str) or not strategy_path.strip():
            show_error(
                "BACKTESTER",
                "strategy_mode=semantic 需要設定 backtester.strategy_contract_path",
            )
            return None

        config_file_path = backtester_config.get("__config_file_path")
        resolved_strategy = resolve_input_path(
            strategy_path,
            repo_root=self.project_root,
            config_file_path=config_file_path if isinstance(config_file_path, str) else None,
        )

        feature_path = backtester_config.get("feature_contract_path")
        resolved_feature_path: Optional[str] = None
        if isinstance(feature_path, str) and feature_path.strip():
            resolved_feature = resolve_input_path(
                feature_path,
                repo_root=self.project_root,
                config_file_path=config_file_path if isinstance(config_file_path, str) else None,
            )
            resolved_feature_path = str(resolved_feature.path)

        output_dir = backtester_config.get("execution_plan_output_dir")

        result = self.compiler.compile_from_paths(
            strategy_contract_path=str(resolved_strategy.path),
            feature_contract_path=resolved_feature_path,
            output_dir=output_dir if isinstance(output_dir, str) else None,
        )
        if not result.valid:
            show_error("BACKTESTER", f"semantic compile failed: {'; '.join(result.errors)}")
            return None

        return {
            "execution_plan_path": result.execution_plan_path,
            "execution_plan": result.execution_plan,
        }

    @staticmethod
    def _resolve_strategy_mode(backtester_config: Dict[str, Any]) -> str:
        mode = str(backtester_config.get("strategy_mode", "auto")).strip().lower()
        if mode not in {"semantic", "auto", "multi_asset_portfolio", "single_asset_portfolio"}:
            raise ValueError(
                "backtester.strategy_mode must be semantic/auto/"
                "multi_asset_portfolio/single_asset_portfolio"
            )
        if mode in {"multi_asset_portfolio", "single_asset_portfolio"}:
            return mode
        if mode == "auto":
            if backtester_config.get("strategy_contract_path"):
                return "semantic"
            raise ValueError("backtester.strategy_mode=auto requires strategy_contract_path or portfolio mode")
        return mode

    @staticmethod
    def _is_strategy_run(config: Dict[str, Any]) -> bool:
        return dict(config or {}).get("schema_version") == "strategy_run"

    def _run_single_asset_portfolio(
        self,
        *,
        data: pd.DataFrame,
        backtester_raw: Dict[str, Any],
        dataloader_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        from backtester.MultiAssetPortfolioExporter_backtester import (
            MultiAssetPortfolioExporterBacktester,
        )
        from backtester.SingleAssetPortfolioAdapter_backtester import (
            run_single_asset_signals_as_portfolio,
        )

        if not isinstance(data, pd.DataFrame) or data.empty:
            raise ValueError("single_asset_portfolio requires a non-empty input dataframe")

        entry_col = str(backtester_raw.get("entry_signal_column") or "entry_signal")
        exit_col = str(backtester_raw.get("exit_signal_column") or "exit_signal")
        if entry_col not in data.columns or exit_col not in data.columns:
            raise ValueError(
                "single_asset_portfolio requires entry/exit signal columns: "
                f"{entry_col}, {exit_col}"
            )

        symbol = str(backtester_raw.get("symbol") or self._extract_symbol(dataloader_config))
        execution = backtester_raw.get("execution", {})
        if not isinstance(execution, dict) or not execution:
            execution = {"cost": backtester_raw.get("cost", {"transaction_cost": 0.0, "slippage": 0.0})}

        result = run_single_asset_signals_as_portfolio(
            price_data=data,
            symbol=symbol,
            entry_signal=data[entry_col],
            exit_signal=data[exit_col],
            strategy_id=str(backtester_raw.get("Backtest_id") or "single_asset_portfolio"),
            target_weight=self._float(backtester_raw.get("target_weight", 1.0), default=1.0),
            execution=execution,
        )

        export_config = backtester_raw.get("export_config", {})
        output_dir = export_config.get("output_dir") if isinstance(export_config, dict) else None
        exported_files = MultiAssetPortfolioExporterBacktester(
            result=result,
            output_dir=output_dir,
            run_id=str(backtester_raw.get("Backtest_id") or result.strategy_id),
            export_csv=_as_bool(export_config.get("export_csv", False)) if isinstance(export_config, dict) else False,
        ).export()

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
            "resolved_engine_mode": "unified_portfolio_accounting",
            "sequential_requirements": ["portfolio_accounting"],
            "engine_capabilities": {
                "single_asset_as_portfolio": True,
                "explicit_target_weight_frame": True,
                "portfolio_accounting": True,
            },
            "exported_files": exported_files,
        }

    def _run_multi_asset_portfolio(
        self,
        *,
        backtester_raw: Dict[str, Any],
        dataloader_config: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        from backtester.MultiAssetPortfolioEngine_backtester import (
            MultiAssetPortfolioEngineBacktester,
        )
        from backtester.MultiAssetPortfolioExporter_backtester import (
            MultiAssetPortfolioExporterBacktester,
        )

        portfolio_config = dict(backtester_raw.get("portfolio_config") or {})
        if not portfolio_config:
            raise ValueError("backtester.portfolio_config is required for multi_asset_portfolio")
        config_file_path = backtester_raw.get("__config_file_path") or dataloader_config.get("__config_file_path")
        market_data = self._load_multi_asset_market_data(
            backtester_raw.get("market_data", {}),
            config_file_path=config_file_path if isinstance(config_file_path, str) else None,
        )
        cache_dir = self._resolve_optional_path(
            ((portfolio_config.get("feature_cache") or {}).get("path")),
            config_file_path=config_file_path if isinstance(config_file_path, str) else None,
        )
        export_config = backtester_raw.get("export_config", {})
        output_dir = None
        if isinstance(export_config, dict):
            output_dir = export_config.get("output_dir")
        portfolio_results = []
        exported_files: List[str] = []
        portfolio_variants = self._expand_portfolio_configs(portfolio_config)
        for variant in portfolio_variants:
            variant_config = variant["config"]
            engine = MultiAssetPortfolioEngineBacktester(
                market_data=market_data,
                config=variant_config,
                cache_dir=cache_dir,
            )
            result = engine.run()
            portfolio_results.append(result)
            run_suffix = variant["suffix"]
            export_run_id = "_".join(
                item
                for item in [
                    str(backtester_raw.get("Backtest_id") or portfolio_config.get("strategy_id") or "portfolio"),
                    run_suffix,
                ]
                if item
            )
            exported_files.extend(
                MultiAssetPortfolioExporterBacktester(
                    result=result,
                    output_dir=output_dir,
                    run_id=export_run_id,
                    export_csv=_as_bool(export_config.get("export_csv", False)) if isinstance(export_config, dict) else False,
                ).export()
            )
        result = portfolio_results[0]
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
            "frequency": dataloader_config.get("frequency", portfolio_config.get("data_context", {}).get("frequency", "1D")),
            "export_config": export_config,
            "Backtest_id": str(backtester_raw.get("Backtest_id") or ""),
            "requested_engine_mode": "multi_asset_portfolio",
            "resolved_engine_mode": "portfolio_vector",
            "sequential_requirements": [],
            "engine_capabilities": {
                "multi_asset": True,
                "calendar_rebalance": True,
                "top_n_selection": True,
                "portfolio_accounting": True,
            },
            "exported_files": exported_files,
        }

    def _load_multi_asset_market_data(
        self,
        spec: Any,
        *,
        config_file_path: Optional[str],
    ) -> Dict[str, pd.DataFrame]:
        from dataloader.market_data_loader import MultiAssetMarketDataLoader

        return MultiAssetMarketDataLoader(repo_root=self.project_root).load(
            spec,
            config_file_path=config_file_path,
        )

    def _expand_portfolio_configs(self, portfolio_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        domains = portfolio_config.get("parameter_domains", {})
        if not isinstance(domains, dict) or not domains:
            return [{"config": dict(portfolio_config), "suffix": ""}]
        domain_values: Dict[str, List[Any]] = {}
        for name, spec in domains.items():
            values = self._domain_values(spec)
            if values:
                domain_values[str(name)] = values
        if not domain_values:
            return [{"config": dict(portfolio_config), "suffix": ""}]
        variants: List[Dict[str, Any]] = []
        names = list(domain_values.keys())
        for combo_values in itertools.product(*(domain_values[name] for name in names)):
            params = dict(zip(names, combo_values))
            variant = self._replace_param_refs(copy.deepcopy(portfolio_config), params)
            variant["resolved_params"] = params
            base_strategy_id = str(portfolio_config.get("strategy_id") or "multi_asset_portfolio")
            suffix = "_".join(f"{key}_{self._slug_value(value)}" for key, value in params.items())
            variant["strategy_id"] = f"{base_strategy_id}_{suffix}"
            variants.append({"config": variant, "suffix": suffix})
        return variants

    def _domain_values(self, spec: Any) -> List[Any]:
        if isinstance(spec, list):
            return list(spec)
        if not isinstance(spec, dict):
            return []
        if isinstance(spec.get("values"), list):
            return list(spec["values"])
        if str(spec.get("type", "")).lower() == "range" or {"start", "end"}.issubset(spec.keys()):
            start = int(spec.get("start"))
            end = int(spec.get("end"))
            step = int(spec.get("step") or 1)
            if step == 0:
                return []
            if start <= end and step > 0:
                return list(range(start, end + 1, step))
            if start >= end and step < 0:
                return list(range(start, end - 1, step))
        return []

    def _replace_param_refs(self, value: Any, params: Dict[str, Any]) -> Any:
        if isinstance(value, dict):
            if set(value.keys()) == {"param_ref"}:
                ref = str(value.get("param_ref"))
                return params.get(ref, value)
            return {key: self._replace_param_refs(item, params) for key, item in value.items()}
        if isinstance(value, list):
            return [self._replace_param_refs(item, params) for item in value]
        return value

    @staticmethod
    def _slug_value(value: Any) -> str:
        return str(value).replace(".", "p").replace("-", "m").replace(" ", "_")

    def _resolve_optional_path(
        self,
        raw_path: Any,
        *,
        config_file_path: Optional[str],
    ) -> Optional[Path]:
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        resolved = resolve_input_path(
            raw_path,
            repo_root=self.project_root,
            config_file_path=config_file_path,
        )
        return resolved.path

    def _convert_config(
        self,
        config: Dict[str, Any],
        compile_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        backtester_config = config.get("backtester", {})
        dataloader_config = config.get("dataloader", {})
        strategy_mode = self._resolve_strategy_mode(backtester_config)

        if strategy_mode == "semantic":
            condition_pairs = []
            raw_indicator_params = {}
        else:
            condition_pairs = backtester_config.get("condition_pairs", [])
            raw_indicator_params = backtester_config.get("indicator_params", {})

        trading_params = backtester_config.get("trading_params", {})
        predictors = [backtester_config.get("selected_predictor", "X")]
        processed_indicator_params = {}

        for param_key, param_config in raw_indicator_params.items():
            indicator_type = param_key.split("_strategy_")[0]
            try:
                from backtester.Indicators_backtester import IndicatorsBacktester

                indicators_helper = IndicatorsBacktester(logger=self.logger)
                processed_indicator_params[param_key] = indicators_helper.get_indicator_params(
                    indicator_type,
                    dict(param_config) if isinstance(param_config, dict) else {},
                )
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.error("Failed to process indicator params %s: %s", param_key, exc)
                processed_indicator_params[param_key] = []

        converted = {
            "condition_pairs": condition_pairs,
            "indicator_params": processed_indicator_params,
            "trading_params": trading_params,
            "predictors": predictors,
            "export_config": backtester_config.get("export_config", {}),
            "Backtest_id": backtester_config.get("Backtest_id", ""),
            "engine_mode": backtester_config.get("engine_mode", "auto"),
            "execution_backend": backtester_config.get("execution_backend", "auto"),
            "chunk_size": backtester_config.get("chunk_size", 64),
            "max_workers": backtester_config.get("max_workers", 1),
            "execution_plan_path": (
                compile_result.get("execution_plan_path") if compile_result else None
            ),
            "strategy_mode": strategy_mode,
        }
        if not converted["Backtest_id"] and compile_result:
            converted["Backtest_id"] = compile_result["execution_plan"]["plan_hash"][:12]
        if not converted.get("frequency"):
            converted["frequency"] = dataloader_config.get("frequency", "1D")
        return converted

    def _export_results(
        self,
        *,
        data: pd.DataFrame,
        results: list,
        backtester_config: Dict[str, Any],
        dataloader_config: Dict[str, Any],
        predictor_file_name: Optional[str],
        predictor_column: str,
        symbol: str,
    ) -> list[str]:
        from backtester.TradeRecordExporter_backtester import TradeRecordExporter_backtester

        export_config = backtester_config.get("export_config", {})
        trading_params = backtester_config.get("trading_params", {})
        output_dir = None
        if isinstance(export_config, dict):
            od = export_config.get("output_dir")
            if isinstance(od, str) and od.strip():
                output_dir = od

        exporter = TradeRecordExporter_backtester(
            trade_records=pd.DataFrame(),
            frequency=dataloader_config.get("frequency", "1D"),
            results=results,
            data=data,
            Backtest_id=backtester_config.get("Backtest_id", ""),
            trade_params=trading_params,
            transaction_cost=trading_params.get("transaction_cost"),
            slippage=trading_params.get("slippage"),
            trade_delay=trading_params.get("trade_delay"),
            trade_price=trading_params.get("trade_price"),
            predictor_file_name=predictor_file_name,
            predictor_column=predictor_column,
            symbol=symbol,
            output_dir=output_dir,
        )
        exported_files: list[str] = []
        if _as_bool(export_config.get("export_parquet", True)):
            exported_files.extend(_as_export_list(exporter.export_to_parquet()))
        if _as_bool(export_config.get("export_csv", False)):
            exported_files.extend(_as_export_list(exporter.export_to_csv()))
        if _as_bool(export_config.get("export_excel", False)):
            exported_files.extend(_as_export_list(exporter.export_to_excel()))
        return exported_files

    @staticmethod
    def _float(value: Any, *, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _extract_symbol(dataloader_config: Dict[str, Any]) -> str:
        source = dataloader_config.get("source", "yfinance")
        if source == "binance":
            return dataloader_config.get("binance_config", {}).get("symbol", "BTCUSDT")
        if source == "yfinance":
            return dataloader_config.get("yfinance_config", {}).get("symbol", "AAPL")
        if source == "coinbase":
            return dataloader_config.get("coinbase_config", {}).get("symbol", "BTC-USD")
        return "X"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "y"):
            return True
        if lowered in ("0", "false", "no", "n", ""):
            return False
    return False


def _as_export_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    return [str(value)]
