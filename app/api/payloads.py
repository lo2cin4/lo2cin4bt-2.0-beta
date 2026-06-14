from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.runtime.registry import AppRegistry
from metricstracker.MetricsExporter_metricstracker import MetricsExporter

try:
    from backtester.StrategyRunConfig_backtester import (
        StrategyRunConfigError,
        normalize_strategy_run_config,
        plan_strategy_execution,
    )
except Exception:  # pragma: no cover - optional bridge during partial deployments
    StrategyRunConfigError = ValueError  # type: ignore[assignment]
    normalize_strategy_run_config = None  # type: ignore[assignment]
    plan_strategy_execution = None  # type: ignore[assignment]
from wfanalyser.HeatmapMatrixBuilder_wfanalyser import HeatmapMatrixBuilder
from wfanalyser.RobustSelector_wfanalyser import RobustSelector
from wfanalyser.WFAAcceptanceEvaluator_wfanalyser import WFAAcceptanceEvaluator

CATEGORY_MAP: Dict[str, Dict[str, Any]] = {
    "top_20_sharpe": {"label": "Top 20 Sharpe", "key": "sharpe", "ascending": False},
    "top_20_return": {"label": "Top 20 Return", "key": "total_return", "ascending": False},
    "top_20_cagr": {"label": "Top 20 CAGR", "key": "cagr", "ascending": False},
    "top_20_calmar": {"label": "Top 20 Calmar", "key": "calmar", "ascending": False},
    "top_20_sortino": {"label": "Top 20 Sortino", "key": "sortino", "ascending": False},
    "top_20_recovery_factor": {
        "label": "Top 20 Recovery Factor",
        "key": "recovery_factor",
        "ascending": False,
    },
    "top_20_information_ratio": {
        "label": "Top 20 Information Ratio",
        "key": "information_ratio",
        "ascending": False,
    },
    "top_20_profit_factor": {
        "label": "Top 20 Profit Factor",
        "key": "profit_factor",
        "ascending": False,
    },
    "top_20_lowest_mdd": {
        "label": "Top 20 Lowest MDD",
        "key": "max_drawdown",
        "ascending": False,
    },
    "top_20_excess_return": {
        "label": "Top 20 Excess Return",
        "key": "excess_return",
        "ascending": False,
    },
}

METRICS_OVERVIEW_SCHEMA_VERSION = "1.20"
METRICS_OVERVIEW_MAX_POINTS = 240
PARAMETER_HEATMAP_SCHEMA_VERSION = "3.6"
WFA_DASHBOARD_SCHEMA_VERSION = "3.7"
BACKTEST_DETAIL_SCHEMA_VERSION = "1.17"
AI_READABLE_OUTPUT_SCHEMA_VERSION = "1.0"
AI_REVIEW_NUMERIC_FIELD_LIMIT = 5000
AI_REVIEW_LIST_SAMPLE_LIMIT = 5
AI_REVIEW_ARTIFACT_PROFILE_LIMIT_PER_TYPE = 3

METRIC_KEY_MAP: Dict[str, str] = {
    "total_return": "Total_return",
    "cagr": "Annualized_return (CAGR)",
    "sharpe": "Sharpe",
    "sortino": "Sortino",
    "calmar": "Calmar",
    "max_drawdown": "Max_drawdown",
    "average_drawdown": "Average_drawdown",
    "recovery_factor": "Recovery_factor",
    "std": "Std",
    "annualized_std": "Annualized_std",
    "downside_risk": "Downside_risk",
    "annualized_downside_risk": "Annualized_downside_risk",
    "information_ratio": "Information_ratio",
    "alpha": "Alpha",
    "beta": "Beta",
    "trade_count": "Trade_count",
    "win_rate": "Win_rate",
    "profit_factor": "Profit_factor",
    "avg_trade_return": "Avg_trade_return",
    "max_consecutive_losses": "Max_consecutive_losses",
    "exposure_time": "Exposure_time",
    "max_holding_period_ratio": "Max_holding_period_ratio",
    "bah_total_return": "BAH_Total_return",
    "bah_cagr": "BAH_Annualized_return (CAGR)",
    "bah_sharpe": "BAH_Sharpe",
    "bah_calmar": "BAH_Calmar",
    "bah_max_drawdown": "BAH_Max_drawdown",
}


class AppPayloadService:
    def __init__(self, repo_root: Path, registry: AppRegistry):
        self.repo_root = Path(repo_root).resolve()
        self.registry = registry
        self.heatmap_builder = HeatmapMatrixBuilder()
        self.robust_selector = RobustSelector()

    def ensure_run_payloads(
        self,
        run_id: str,
        module: Optional[str] = None,
    ) -> Dict[str, str]:
        registry_entry = self.registry.load_registry_entry(run_id)
        module_name = module or str(registry_entry.get("module", ""))
        created: Dict[str, str] = {}
        if module_name == "autorunner":
            created["metrics_overview"] = str(self.ensure_metrics_overview_payload(run_id))
            try:
                created["parameter_matrix"] = str(self.ensure_parameter_matrix_payload(run_id))
            except FileNotFoundError as exc:
                message = str(exc)
                if (
                    "at least two semantic parameter axes" not in message
                    and "portfolio parameter matrix requires at least two varied parameters" not in message
                ):
                    raise
                created["parameter_matrix"] = "skipped: not a parameter-matrix run"
        elif module_name == "wfanalyser":
            created["wfa_dashboard"] = str(self.ensure_wfa_dashboard_payload(run_id))
        elif module_name == "statanalyser":
            created["statanalyser_summary"] = str(
                self.ensure_statanalyser_summary_payload(run_id)
            )
        created["ai_readable_output"] = str(
            self.ensure_ai_readable_output(run_id, module=module_name)
        )
        return created

    def ensure_metrics_overview_payload(
        self,
        run_id: str,
        *,
        force: bool = False,
    ) -> Path:
        path = self._chart_path(run_id, "metrics_overview_payload.json")
        if path.exists() and not force:
            cached_payload = self._load_json(path, {})
            if (
                isinstance(cached_payload, dict)
                and cached_payload.get("schema_version") == METRICS_OVERVIEW_SCHEMA_VERSION
                and self._payload_source_refs_exist(cached_payload)
            ):
                return path
        metrics_path = self._artifact_path(run_id, "metricstracker_parquet")
        backtest_path = self._artifact_path(run_id, "backtester_parquet")
        if metrics_path is None or backtest_path is None:
            portfolio_metadata_paths = self._artifact_paths(run_id, "portfolio_metadata_json")
            portfolio_equity_paths = self._artifact_paths(run_id, "portfolio_equity_curve_parquet")
            if portfolio_metadata_paths and portfolio_equity_paths:
                payload = self._build_portfolio_metrics_payload(
                    run_id=run_id,
                    metadata_paths=portfolio_metadata_paths,
                    equity_paths=portfolio_equity_paths,
                    holdings_paths=self._artifact_paths(run_id, "portfolio_holdings_parquet"),
                    rebalance_paths=self._artifact_paths(run_id, "portfolio_rebalance_audit_parquet"),
                    rebalance_trade_paths=self._artifact_paths(run_id, "portfolio_rebalance_trades_parquet"),
                )
                self._write_json(path, payload)
                return path
            raise FileNotFoundError(
                "metrics overview payload requires metricstracker and backtester parquet"
            )

        metadata_path = self._metrics_metadata_path(metrics_path)
        metadata_rows = self._load_json(metadata_path, [])
        if not isinstance(metadata_rows, list) or not metadata_rows:
            raise FileNotFoundError(
                "metric metadata json missing for metrics overview payload"
            )

        metric_df = pd.read_parquet(metrics_path, columns=["Time", "Equity_value", "BAH_Equity", "Backtest_id"])
        metric_df["Backtest_id"] = metric_df["Backtest_id"].astype(str)
        valid_backtest_ids = set(metric_df["Backtest_id"].unique().tolist())
        grouped_metrics: Dict[str, pd.DataFrame] = {
            str(backtest_id): group.reset_index(drop=True)
            for backtest_id, group in metric_df.groupby("Backtest_id", sort=False)
        }
        trade_df = pd.read_parquet(
            backtest_path,
            columns=["Backtest_id", "Time", "Trade_action", "Close_time"],
        )
        trade_df["Backtest_id"] = trade_df["Backtest_id"].astype(str)
        last_trade_time_map = self._build_last_trade_time_map(trade_df)
        index_map = self._load_backtest_index_map(run_id)
        rows: List[Dict[str, Any]] = []

        for metric_row in metadata_rows:
            backtest_id = str(metric_row.get("Backtest_id", ""))
            if backtest_id not in valid_backtest_ids:
                continue
            combo = index_map.get(backtest_id, {})
            label = self._build_backtest_label(backtest_id, combo)
            subset = grouped_metrics.get(backtest_id, pd.DataFrame()).copy()
            rows.append(
                self._build_metrics_row(
                    metric_row=metric_row,
                    combo=combo,
                    backtest_id=backtest_id,
                    label=label,
                    subset=subset,
                    last_trade_time=last_trade_time_map.get(backtest_id),
                )
            )
        category_map = self._build_category_map(rows)
        series_ids: set[str] = set()
        for ids in category_map.values():
            series_ids.update(str(value) for value in ids)

        series: List[Dict[str, Any]] = []
        benchmark_series: Optional[Dict[str, Any]] = None
        for backtest_id in series_ids:
            subset = grouped_metrics.get(backtest_id, pd.DataFrame()).copy()
            if subset.empty:
                continue
            combo = index_map.get(backtest_id, {})
            label = self._build_backtest_label(backtest_id, combo)
            series_x = [self._to_iso(v) for v in subset["Time"].tolist()]
            series_y = [
                self._as_float(v)
                for v in subset["Equity_value"].fillna(0).tolist()
            ]
            series_x, series_y = self._downsample_xy(
                series_x,
                series_y,
                max_points=METRICS_OVERVIEW_MAX_POINTS,
            )
            series.append(
                {
                    "backtest_id": backtest_id,
                    "label": label,
                    "x": series_x,
                    "y": series_y,
                }
            )
            if benchmark_series is None and "BAH_Equity" in subset.columns:
                benchmark_x = [self._to_iso(v) for v in subset["Time"].tolist()]
                benchmark_y = [
                    self._as_float(v)
                    for v in subset["BAH_Equity"].fillna(0).tolist()
                ]
                benchmark_x, benchmark_y = self._downsample_xy(
                    benchmark_x,
                    benchmark_y,
                    max_points=METRICS_OVERVIEW_MAX_POINTS,
                )
                benchmark_series = {
                    "series_id": "benchmark",
                    "label": "Benchmark",
                    "x": benchmark_x,
                    "y": benchmark_y,
                }

        payload = {
            "schema_version": METRICS_OVERVIEW_SCHEMA_VERSION,
            "contract_id": "lo2cin4bt-app-metrics-overview-payload-v1",
            "run_id": run_id,
            "strategy_summary": self._strategy_summary(run_id),
            "default_category": "top_20_sharpe",
            "available_categories": [
                {"id": key, "label": value["label"]}
                for key, value in CATEGORY_MAP.items()
            ],
            "rows": rows,
            "series": series,
            "benchmark_series": benchmark_series,
            "categories": category_map,
            "generated_at": self._now_iso(),
            "artifact_source_refs": [
                str(metrics_path),
                str(backtest_path),
                str(metadata_path),
            ],
        }
        self._write_json(path, payload)
        return path

    def _build_portfolio_metrics_payload(
        self,
        *,
        run_id: str,
        metadata_paths: List[Path],
        equity_paths: List[Path],
        holdings_paths: List[Path],
        rebalance_paths: List[Path],
        rebalance_trade_paths: List[Path],
    ) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        series: List[Dict[str, Any]] = []
        portfolio_runs: List[Dict[str, Any]] = []
        source_refs: List[str] = []
        first_metadata: Dict[str, Any] = {}
        benchmark_cache: Dict[str, Dict[str, Any]] = {}
        equity_by_key = self._portfolio_artifacts_by_key(equity_paths, "equity")
        holdings_by_key = self._portfolio_artifacts_by_key(holdings_paths, "holdings")
        rebalance_by_key = self._portfolio_artifacts_by_key(rebalance_paths, "rebalance")
        rebalance_trades_by_key = self._portfolio_artifacts_by_key(rebalance_trade_paths, "rebalance_trades")
        for metadata_path in metadata_paths:
            metadata = self._load_json(metadata_path, {})
            if not isinstance(metadata, dict):
                metadata = {}
            if not first_metadata:
                first_metadata = metadata
            key = self._portfolio_artifact_key(metadata_path, "metadata")
            equity_path = equity_by_key.get(key) or (equity_paths[0] if len(metadata_paths) == 1 else None)
            if equity_path is None:
                continue
            holdings_path = holdings_by_key.get(key)
            rebalance_path = rebalance_by_key.get(key)
            rebalance_trade_path = rebalance_trades_by_key.get(key)
            risk_gate_events_path, risk_gate_summary_path = self._portfolio_risk_artifact_paths(metadata_path)
            built = self._build_single_portfolio_metrics(
                run_id=run_id,
                metadata=metadata,
                metadata_path=metadata_path,
                equity_path=equity_path,
                holdings_path=holdings_path,
                rebalance_path=rebalance_path,
                rebalance_trade_path=rebalance_trade_path,
                risk_gate_events_path=risk_gate_events_path,
                risk_gate_summary_path=risk_gate_summary_path,
                benchmark_cache=benchmark_cache,
            )
            rows.append(built["row"])
            series.append(built["series"])
            portfolio_runs.append(built["portfolio"])
            source_refs.extend(built["source_refs"])
        rows.sort(key=lambda item: item.get("sharpe") or float("-inf"), reverse=True)
        categories = {"portfolio_overview": [str(row.get("backtest_id")) for row in rows]}
        best_id = str(rows[0].get("backtest_id", "")) if rows else ""
        series_keep_ids = {str(row.get("backtest_id")) for row in rows[:20]}
        if best_id:
            series_keep_ids.add(best_id)
        series = [
            item
            for item in series
            if str(item.get("backtest_id", "")) in series_keep_ids
        ]
        best_portfolio = next(
            (
                item
                for item in portfolio_runs
                if str(item.get("summary", {}).get("backtest_id", "")) == best_id
            ),
            portfolio_runs[0] if portfolio_runs else {},
        )
        portfolio_runs = [
            self._compact_portfolio_run_for_overview(item, keep_preview=(
                str(item.get("summary", {}).get("backtest_id", "")) == best_id
            ))
            for item in portfolio_runs
        ]
        manifest_path = self.registry.build_run_paths(run_id)["artifact_manifest"]
        compact_source_refs = [str(manifest_path)] if manifest_path.exists() else []
        compact_source_refs.extend(source_refs[:20])
        return {
            "schema_version": METRICS_OVERVIEW_SCHEMA_VERSION,
            "contract_id": "lo2cin4bt-app-portfolio-metrics-overview-payload-v1",
            "result_type": "portfolio",
            "artifact_type": "multi_asset_portfolio_backtest",
            "run_id": run_id,
            "strategy_summary": self._portfolio_strategy_summary(run_id, first_metadata),
            "default_category": "portfolio_overview",
            "available_categories": [{"id": "portfolio_overview", "label": "Portfolio Overview"}],
            "rows": rows,
            "series": series,
            "benchmark_series": best_portfolio.get("benchmark_series"),
            "categories": categories,
            "portfolio": {
                "summary": rows[0] if rows else {},
                "runs": portfolio_runs,
                "holdings_preview": best_portfolio.get("holdings_preview", []),
                "rebalance_preview": best_portfolio.get("rebalance_preview", []),
                "data_quality": best_portfolio.get("data_quality", {}),
                "universe_provenance": best_portfolio.get("universe_provenance", {}),
                "factor_feature_audit": best_portfolio.get("factor_feature_audit", {}),
                "truth_source_policy": self._truth_source_policy(),
                "truth_warnings": best_portfolio.get("truth_warnings", []),
            },
            "generated_at": self._now_iso(),
            "artifact_source_refs": sorted(set(compact_source_refs)),
        }

    @staticmethod
    def _compact_portfolio_run_for_overview(
        item: Dict[str, Any],
        *,
        keep_preview: bool,
    ) -> Dict[str, Any]:
        compact = {
            "summary": item.get("summary", {}),
            "data_quality": item.get("data_quality", {}),
            "universe_provenance": item.get("universe_provenance", {}),
            "factor_feature_audit": item.get("factor_feature_audit", {}),
            "truth_source_policy": item.get("truth_source_policy", {}),
            "truth_warnings": item.get("truth_warnings", []),
            "turnover_summary": item.get("turnover_summary", {}),
            "risk_gate_summary": item.get("risk_gate_summary", {}),
            "benchmark_label": item.get("benchmark_label"),
            "artifact_paths": item.get("artifact_paths", {}),
        }
        if keep_preview:
            compact["holdings_preview"] = item.get("holdings_preview", [])
            compact["rebalance_preview"] = item.get("rebalance_preview", [])
            compact["benchmark_series"] = item.get("benchmark_series", [])
        return compact

    def _build_single_portfolio_metrics(
        self,
        *,
        run_id: str,
        metadata: Dict[str, Any],
        metadata_path: Path,
        equity_path: Path,
        holdings_path: Optional[Path],
        rebalance_path: Optional[Path],
        rebalance_trade_path: Optional[Path],
        risk_gate_events_path: Optional[Path] = None,
        risk_gate_summary_path: Optional[Path] = None,
        benchmark_cache: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if not isinstance(metadata, dict):
            metadata = {}
        equity_df = self._coerce_equity_frame(pd.read_parquet(equity_path))
        if equity_df.empty:
            raise FileNotFoundError("portfolio equity curve requires parseable Time and Equity_value columns")
        metrics = self._portfolio_metrics(equity_df)
        holdings_preview: List[Dict[str, Any]] = []
        rebalance_preview: List[Dict[str, Any]] = []
        if holdings_path is not None:
            holdings_preview = self._frame_preview(pd.read_parquet(holdings_path), limit=120)
        if rebalance_path is not None:
            rebalance_preview = self._frame_preview(pd.read_parquet(rebalance_path), limit=120)

        summary = metadata.get("summary", {}) if isinstance(metadata.get("summary"), dict) else {}
        data_quality = self._portfolio_data_quality(metadata)
        universe_provenance = self._portfolio_universe_provenance(metadata, data_quality)
        factor_feature_audit = self._portfolio_factor_feature_audit(metadata, data_quality)
        truth_warnings = self._truth_warnings(
            data_quality,
            universe_provenance,
            factor_feature_audit,
        )
        config = metadata.get("config", {}) if isinstance(metadata.get("config"), dict) else {}
        config = self._portfolio_config_with_benchmark_fallback(run_id, config, metadata)
        resolved_params = config.get("resolved_params", {}) if isinstance(config.get("resolved_params"), dict) else {}
        strategy_id = str(metadata.get("strategy_id") or config.get("strategy_id") or "portfolio").strip()
        label = self._portfolio_strategy_table_label(
            run_id=run_id,
            metadata=metadata,
            config=config,
            strategy_id=strategy_id,
            params=resolved_params,
        )
        if isinstance(summary, dict):
            metrics["rebalance_count"] = int(summary.get("rebalance_count") or metrics.get("rebalance_count") or 0)
        metrics["avg_holdings"] = self._finite_or_none(equity_df.get("Selected_count", pd.Series(dtype=float)).mean())
        gross_exposure = equity_df.get("Gross_exposure")
        if gross_exposure is None:
            cash_weight = equity_df.get("Cash_weight", pd.Series(dtype=float))
            gross_exposure = 1.0 - pd.to_numeric(cash_weight, errors="coerce")
        metrics["avg_gross_exposure"] = self._finite_or_none(pd.to_numeric(gross_exposure, errors="coerce").mean())
        metrics["avg_cash_weight"] = self._finite_or_none(equity_df.get("Cash_weight", pd.Series(dtype=float)).mean())
        turnover_series = pd.to_numeric(equity_df.get("Turnover", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        active_turnover = turnover_series[turnover_series > 0.0]
        metrics["avg_turnover"] = self._finite_or_none(
            active_turnover.mean() if not active_turnover.empty else 0.0
        )
        turnover_summary = self._portfolio_turnover_summary(equity_df, pd.DataFrame())
        risk_gate_summary = self._portfolio_risk_gate_summary(metadata, risk_gate_summary_path)
        metrics["risk_gate_event_count"] = int(risk_gate_summary.get("event_count") or 0)
        benchmark = self._portfolio_benchmark_payload(
            config,
            equity_df,
            benchmark_cache=benchmark_cache,
        )
        benchmark_warning = benchmark.get("warning") if isinstance(benchmark, dict) else None
        benchmark_error_type = benchmark.get("error_type") if isinstance(benchmark, dict) else None
        benchmark_metrics = benchmark.get("metrics", {}) if isinstance(benchmark, dict) else {}
        if benchmark_metrics:
            benchmark_series = benchmark.get("series", []) if isinstance(benchmark.get("series"), list) else []
            strategy_series = [
                {"time": self._to_iso(row.Time), "value": self._finite_or_none(row.Equity_value)}
                for row in equity_df.itertuples(index=False)
            ]
            metrics.update(
                {
                    "benchmark_label": benchmark.get("label"),
                    "bah_total_return": benchmark_metrics.get("total_return"),
                    "bah_cagr": benchmark_metrics.get("cagr"),
                    "bah_sharpe": benchmark_metrics.get("sharpe"),
                    "bah_calmar": benchmark_metrics.get("calmar"),
                    "bah_max_drawdown": benchmark_metrics.get("max_drawdown"),
                    "benchmark_correlation": self._benchmark_correlation_from_series(
                        strategy_series,
                        benchmark_series,
                    ),
                }
            )
            if metrics.get("total_return") is not None and benchmark_metrics.get("total_return") is not None:
                metrics["excess_return"] = self._finite_or_none(
                    float(metrics.get("total_return") or 0.0)
                    - float(benchmark_metrics.get("total_return") or 0.0)
                )
        row = {
            "backtest_id": strategy_id,
            "strategy_id": strategy_id,
            "label": label,
            "label_source": "portfolio_display_label",
            "semantic_combo": resolved_params,
            "semantic_fields": list(resolved_params.keys()),
            "date_range_start": self._to_iso(equity_df["Time"].iloc[0]),
            "date_range_end": self._to_iso(equity_df["Time"].iloc[-1]),
            "universe_survivorship_risk": universe_provenance.get("survivorship_bias_risk"),
            "universe_provenance_status": universe_provenance.get("provenance_status"),
            **metrics,
        }
        if benchmark_warning:
            row["benchmark_warning"] = benchmark_warning
        if benchmark_error_type:
            row["benchmark_error_type"] = benchmark_error_type
        series_x = [self._to_iso(value) for value in equity_df["Time"].tolist()]
        series_y = [self._finite_or_none(value) or 0.0 for value in equity_df["Equity_value"].fillna(0).tolist()]
        series_x, series_y = self._downsample_xy(
            series_x,
            series_y,
            max_points=METRICS_OVERVIEW_MAX_POINTS,
        )
        source_refs = [str(metadata_path), str(equity_path)]
        if holdings_path is not None:
            source_refs.append(str(holdings_path))
        if rebalance_path is not None:
            source_refs.append(str(rebalance_path))
        if rebalance_trade_path is not None:
            source_refs.append(str(rebalance_trade_path))
        if risk_gate_events_path is not None:
            source_refs.append(str(risk_gate_events_path))
        if risk_gate_summary_path is not None:
            source_refs.append(str(risk_gate_summary_path))
        return {
            "row": row,
            "series": {
                "backtest_id": strategy_id,
                "label": label,
                "x": series_x,
                "y": series_y,
            },
            "portfolio": {
                "summary": row,
                "metadata": metadata,
                "data_quality": data_quality,
                "universe_provenance": universe_provenance,
                "factor_feature_audit": factor_feature_audit,
                "truth_source_policy": self._truth_source_policy(),
                "truth_warnings": truth_warnings,
                "turnover_summary": turnover_summary,
                "benchmark_series": benchmark.get("series", []) if isinstance(benchmark, dict) else [],
                "benchmark_label": benchmark.get("label") if isinstance(benchmark, dict) else None,
                "benchmark_warning": benchmark_warning,
                "benchmark_error_type": benchmark_error_type,
                "holdings_preview": holdings_preview,
                "rebalance_preview": rebalance_preview,
                "artifact_paths": {
                    "metadata": str(metadata_path),
                    "equity_curve": str(equity_path),
                    "holdings": str(holdings_path) if holdings_path is not None else "",
                    "rebalance_audit": str(rebalance_path) if rebalance_path is not None else "",
                    "rebalance_trades": str(rebalance_trade_path) if rebalance_trade_path is not None else "",
                    "risk_gate_events": str(risk_gate_events_path) if risk_gate_events_path is not None else "",
                    "risk_gate_summary": str(risk_gate_summary_path) if risk_gate_summary_path is not None else "",
                },
                "risk_gate_summary": risk_gate_summary,
            },
            "source_refs": source_refs,
        }

    @staticmethod
    def _portfolio_risk_artifact_paths(metadata_path: Path) -> tuple[Optional[Path], Optional[Path]]:
        stem = metadata_path.stem
        if stem.endswith("_metadata"):
            base = stem[: -len("_metadata")]
        elif "_portfolio-metadata_" in stem:
            base = stem.replace("_portfolio-metadata_", "_portfolio-", 1)
        else:
            base = stem
        events_path = metadata_path.with_name(f"{base}_risk_gate_events.parquet")
        summary_path = metadata_path.with_name(f"{base}_risk_gate_summary.json")
        if not events_path.is_file():
            event_candidates = list(metadata_path.parent.glob("*risk_gate_events.parquet"))
            if len(event_candidates) == 1:
                events_path = event_candidates[0]
        if not summary_path.is_file():
            summary_candidates = list(metadata_path.parent.glob("*risk_gate_summary.json"))
            if len(summary_candidates) == 1:
                summary_path = summary_candidates[0]
        return (
            events_path if events_path.is_file() else None,
            summary_path if summary_path.is_file() else None,
        )

    def _portfolio_risk_gate_summary(
        self,
        metadata: Dict[str, Any],
        summary_path: Optional[Path],
    ) -> Dict[str, Any]:
        if summary_path is not None and summary_path.is_file():
            loaded = self._load_json(summary_path, {})
            if isinstance(loaded, dict):
                return loaded
        validation = metadata.get("run_validation", {}) if isinstance(metadata.get("run_validation"), dict) else {}
        summary = validation.get("risk_gate_summary") if isinstance(validation, dict) else None
        if isinstance(summary, dict):
            return summary
        return {
            "schema_version": "risk_gate_summary.v1",
            "event_count": 0,
            "gates_triggered": [],
        }

    def _portfolio_config_with_benchmark_fallback(
        self,
        run_id: str,
        config: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        resolved = copy.deepcopy(config) if isinstance(config, dict) else {}
        source_config = self._source_strategy_config(run_id)
        source_data = source_config.get("data", {}) if isinstance(source_config.get("data"), dict) else {}
        source_market_data = source_data.get("market_data")
        if isinstance(source_market_data, dict) and "market_data" not in resolved:
            resolved["market_data"] = copy.deepcopy(source_market_data)
        if self._benchmark_cfg_has_symbol(resolved.get("benchmark")):
            return resolved

        source_benchmark = source_data.get("benchmark")
        if not self._benchmark_cfg_has_symbol(source_benchmark):
            source_benchmark = source_config.get("benchmark")
        if self._benchmark_cfg_has_symbol(source_benchmark):
            resolved["benchmark"] = copy.deepcopy(source_benchmark)
            return resolved
        if isinstance(source_benchmark, str) and source_benchmark.strip():
            resolved["benchmark"] = {"symbol": source_benchmark.strip().upper(), "label": source_benchmark.strip().upper()}
            return resolved

        symbols = self._portfolio_config_symbols(resolved)
        if len(symbols) != 1:
            return resolved

        # A one-symbol unified portfolio is the replacement path for legacy
        # single-asset backtests, where buy-and-hold of that same symbol was
        # always the benchmark. Preserve that behavior when no explicit
        # benchmark is recorded in older configs.
        symbol = symbols[0]
        frequency = source_data.get("frequency") or source_data.get("interval") or "1d"
        resolved["benchmark"] = {
            "provider": source_data.get("provider") or source_data.get("source") or resolved.get("provider"),
            "symbol": symbol,
            "label": f"{symbol} Buy & Hold",
            "interval": str(frequency).lower(),
        }
        return resolved

    @staticmethod
    def _benchmark_cfg_has_symbol(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        return bool(str(value.get("symbol") or value.get("ticker") or "").strip())

    @staticmethod
    def _portfolio_config_symbols(config: Dict[str, Any]) -> List[str]:
        universe = config.get("universe", {}) if isinstance(config.get("universe"), dict) else {}
        symbols = universe.get("symbols", []) if isinstance(universe.get("symbols"), list) else []
        return [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]

    def _portfolio_benchmark_payload(
        self,
        config: Dict[str, Any],
        equity_df: pd.DataFrame,
        *,
        benchmark_cache: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        benchmark_cfg = config.get("benchmark", {}) if isinstance(config.get("benchmark"), dict) else {}
        symbol = str(benchmark_cfg.get("symbol") or benchmark_cfg.get("ticker") or "").strip().upper()
        if not symbol or equity_df.empty:
            return self._empty_benchmark_payload(None)
        dates = pd.to_datetime(equity_df["Time"], errors="coerce").dropna()
        if dates.empty:
            return self._empty_benchmark_payload(symbol, warning="benchmark_dates_empty")
        start = dates.min().date().isoformat()
        end = (dates.max() + pd.Timedelta(days=2)).date().isoformat()
        provider = str(benchmark_cfg.get("provider") or config.get("provider") or "yfinance").strip().lower()
        interval = str(benchmark_cfg.get("interval") or config.get("interval") or "1d")
        source_identity = self._benchmark_market_data_cache_identity(config)
        cache_key = f"{provider}|{symbol}|{start}|{end}|{interval}|{len(dates)}|{source_identity}"
        if benchmark_cache is not None and cache_key in benchmark_cache:
            return copy.deepcopy(benchmark_cache[cache_key])
        try:
            close = self._load_benchmark_close_from_config_market_data(
                config=config,
                provider=provider,
                symbol=symbol,
            )
            if close.empty:
                close = self._load_benchmark_close(
                    provider=provider,
                    symbol=symbol,
                    start=start,
                    end=end,
                    interval=interval,
                    benchmark_cfg=benchmark_cfg,
                )
        except Exception as exc:
            return self._empty_benchmark_payload(
                symbol,
                warning="benchmark_load_failed",
                error_type=exc.__class__.__name__,
            )
        if close.empty:
            return self._empty_benchmark_payload(symbol, warning="benchmark_series_empty")
        close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
        aligned = close.reindex(pd.to_datetime(equity_df["Time"]).dt.normalize()).ffill()
        aligned = pd.to_numeric(aligned, errors="coerce")
        first_valid = aligned.dropna()
        if first_valid.empty:
            return self._empty_benchmark_payload(symbol, warning="benchmark_alignment_empty")
        start_equity = float(pd.to_numeric(equity_df["Equity_value"], errors="coerce").dropna().iloc[0])
        benchmark_equity = aligned / float(first_valid.iloc[0]) * start_equity
        benchmark_frame = pd.DataFrame(
            {
                "Time": pd.to_datetime(equity_df["Time"]).to_numpy(),
                "Equity_value": benchmark_equity.ffill().fillna(start_equity).to_numpy(),
            }
        )
        benchmark_frame["Portfolio_return"] = (
            pd.to_numeric(benchmark_frame["Equity_value"], errors="coerce").pct_change().fillna(0.0)
        )
        series = [
            {"time": self._to_iso(row.Time), "value": self._finite_or_none(row.Equity_value) or 0.0}
            for row in benchmark_frame.itertuples(index=False)
        ]
        payload = {
            "label": str(benchmark_cfg.get("label") or symbol),
            "series": series,
            "metrics": self._portfolio_metrics(benchmark_frame),
        }
        if benchmark_cache is not None:
            benchmark_cache[cache_key] = copy.deepcopy(payload)
        return payload

    @staticmethod
    def _empty_benchmark_payload(
        label: Optional[str],
        *,
        warning: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"label": label, "series": [], "metrics": {}}
        if warning:
            payload["warning"] = warning
        if error_type:
            payload["error_type"] = error_type
        return payload

    @staticmethod
    def _benchmark_market_data_cache_identity(config: Dict[str, Any]) -> str:
        market_data = config.get("market_data") if isinstance(config.get("market_data"), dict) else {}
        close_spec = market_data.get("close") if isinstance(market_data, dict) else None
        if close_spec is None:
            return "provider"
        try:
            raw = json.dumps(close_spec, sort_keys=True, ensure_ascii=False, default=str)
        except TypeError:
            raw = str(close_spec)
        return hashlib.blake2s(raw.encode("utf-8"), digest_size=8).hexdigest()

    def _load_benchmark_close_from_config_market_data(
        self,
        *,
        config: Dict[str, Any],
        provider: str,
        symbol: str,
    ) -> pd.Series:
        if provider not in {"file", "csv", "local_csv", "local_file"}:
            return pd.Series(dtype=float)
        market_data = config.get("market_data") if isinstance(config.get("market_data"), dict) else {}
        close_spec = market_data.get("close") if isinstance(market_data, dict) else None
        if not isinstance(close_spec, (dict, str)):
            return pd.Series(dtype=float)
        from dataloader.market_data_loader import MultiAssetMarketDataLoader

        frames = MultiAssetMarketDataLoader(repo_root=self.repo_root).load({"close": close_spec})
        close_frame = frames.get("close")
        if not isinstance(close_frame, pd.DataFrame):
            return pd.Series(dtype=float)
        if symbol in close_frame.columns:
            return pd.to_numeric(close_frame[symbol], errors="coerce")
        if len(close_frame.columns) == 1:
            return pd.to_numeric(close_frame.iloc[:, 0], errors="coerce")
        return pd.Series(dtype=float)

    def _load_benchmark_close(
        self,
        *,
        provider: str,
        symbol: str,
        start: str,
        end: str,
        interval: str,
        benchmark_cfg: Dict[str, Any],
    ) -> pd.Series:
        provider = provider or "yfinance"
        spec = {
            key: copy.deepcopy(value)
            for key, value in benchmark_cfg.items()
            if key not in {"symbol", "ticker", "label"}
        }
        spec.update(
            {
                "provider": provider,
                "symbols": [symbol],
                "start": start,
                "end": end,
                "interval": interval,
            }
        )
        try:
            from dataloader.market_data_loader import MultiAssetMarketDataLoader

            frames = MultiAssetMarketDataLoader(repo_root=self.repo_root).load(spec)
            close_frame = frames.get("close")
            if isinstance(close_frame, pd.DataFrame) and symbol in close_frame.columns:
                return pd.to_numeric(close_frame[symbol], errors="coerce")
            if isinstance(close_frame, pd.DataFrame) and len(close_frame.columns) == 1:
                return pd.to_numeric(close_frame.iloc[:, 0], errors="coerce")
        except Exception:
            if provider not in {"yfinance", "yf"}:
                raise

        import yfinance as yf

        raw = yf.download(
            tickers=[symbol],
            start=start,
            end=end,
            interval=interval,
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        close = raw["Close"] if "Close" in raw else raw
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        return pd.to_numeric(close, errors="coerce")

    def _portfolio_artifacts_by_key(self, paths: List[Path], artifact_kind: str) -> Dict[str, Path]:
        return {
            self._portfolio_artifact_key(path, artifact_kind): path
            for path in paths
        }

    @staticmethod
    def _safe_payload_filename(value: str, *, max_prefix: int = 96) -> str:
        raw = str(value or "")
        digest = hashlib.blake2s(raw.encode("utf-8"), digest_size=8).hexdigest()[:12]
        prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-")
        prefix = prefix[:max_prefix].strip("._-") or "payload"
        return f"{prefix}_{digest}"

    @staticmethod
    def _portfolio_artifact_key(path: Path, artifact_kind: str) -> str:
        stem = path.stem
        canonical_markers = {
            "metadata": "_portfolio-metadata_",
            "equity": "_portfolio-equity_",
            "holdings": "_portfolio-holdings_",
            "rebalance": "_portfolio-rebalance-audit_",
            "rebalance_trades": "_portfolio-rebalance-trades_",
        }
        canonical_marker = canonical_markers.get(artifact_kind)
        if canonical_marker and canonical_marker in stem:
            prefix, suffix = stem.split(canonical_marker, 1)
            return f"{prefix}_portfolio_{AppPayloadService._portfolio_variant_key_from_suffix(suffix, artifact_kind)}"
        if artifact_kind == "rebalance_trades" and "_rebalance-trades_" in stem:
            prefix, suffix = stem.split("_rebalance-trades_", 1)
            return f"{prefix}_portfolio_{AppPayloadService._portfolio_variant_key_from_suffix(suffix, artifact_kind)}"

        raw_suffixes = {
            "metadata": ["_metadata"],
            "equity": ["_equity_curve"],
            "holdings": ["_holdings"],
            "rebalance": ["_rebalance_audit"],
            "rebalance_trades": ["_rebalance_trades"],
        }
        for marker in raw_suffixes.get(artifact_kind, []):
            if stem.endswith(marker):
                return stem[: -len(marker)]
        return stem

    @staticmethod
    def _portfolio_variant_key_from_suffix(suffix: str, artifact_kind: str) -> str:
        # Canonical portfolio artifact names include the original source stem,
        # a source hash, and the app run short id.  Those pieces differ
        # per artifact type, so matching must reduce the suffix back to the
        # common strategy variant stem.
        text = str(suffix or "")
        text = re.sub(r"_[0-9a-fA-F]{6}_[0-9a-fA-F]{6}$", "", text)
        raw_suffixes = {
            "metadata": ["_metadata", "_meta", "_met", "_me"],
            "equity": ["_equity_curve", "_equity", "_equi", "_equ", "_eq"],
            "holdings": ["_holdings", "_holding", "_hold", "_hol", "_ho"],
            "rebalance": [
                "_rebalance_audit",
                "_rebalance_audi",
                "_rebalance_aud",
                "_rebalance_au",
                "_rebalance_a",
                "_rebalance",
                "_reb",
                "_re",
            ],
            "rebalance_trades": [
                "_rebalance_trades",
                "_rebalance_trade",
                "_rebalance_trad",
                "_rebalance_tra",
                "_rebalance_tr",
                "_rebalance-t",
                "_rebalance",
                "_rebalance-trades",
            ],
        }
        for marker in raw_suffixes.get(artifact_kind, []):
            marker_index = text.rfind(marker)
            if marker_index > 0:
                return text[:marker_index]
        return text

    def _portfolio_metrics(
        self,
        equity_df: pd.DataFrame,
        *,
        time_unit: int = 365,
        risk_free_rate: float = 0.04,
    ) -> Dict[str, Any]:
        equity = pd.to_numeric(equity_df["Equity_value"], errors="coerce").dropna()
        if equity.empty:
            return {}
        returns_array = MetricsExporter._pct_change(equity.to_numpy(dtype=np.float64, copy=False))
        returns = pd.Series(returns_array, dtype=float)
        start = float(equity.iloc[0])
        end = float(equity.iloc[-1])
        total_return = (end / start - 1.0) if start else 0.0
        years = max(float(len(equity)) / float(time_unit), 1.0)
        cagr = MetricsExporter._annualized_total_return(total_return, years)
        sqrt_time_unit = math.sqrt(float(time_unit))
        rf_per_period = MetricsExporter._risk_free_per_period(risk_free_rate, time_unit)
        return_std = MetricsExporter._std(returns_array)
        annualized_std = MetricsExporter._annualized_std(return_std, sqrt_time_unit)
        downside_std = MetricsExporter._downside_risk(returns_array)
        annualized_downside = MetricsExporter._annualized_std(downside_std, sqrt_time_unit)
        sharpe = MetricsExporter._sharpe(returns_array, rf_per_period, sqrt_time_unit)
        sortino = MetricsExporter._sortino(returns_array, rf_per_period, sqrt_time_unit)
        rolling_peak = equity.cummax()
        drawdown = equity / rolling_peak - 1.0
        max_drawdown = float(drawdown.min() or 0.0)
        calmar = MetricsExporter._safe_div(cagr - float(risk_free_rate), abs(max_drawdown), np.nan)
        recovery_factor = (total_return / abs(max_drawdown)) if max_drawdown < 0 else 0.0
        rebalance_count = int((pd.to_numeric(equity_df.get("Turnover", pd.Series(dtype=float)), errors="coerce").fillna(0.0) > 0).sum())
        positive_returns = returns[returns > 0.0]
        negative_returns = returns[returns < 0.0]
        gross_profit = float(positive_returns.sum()) if not positive_returns.empty else 0.0
        gross_loss = float(negative_returns.sum()) if not negative_returns.empty else 0.0
        abs_gross_loss = abs(gross_loss)
        average_win = float(positive_returns.mean()) if not positive_returns.empty else None
        average_loss = float(negative_returns.mean()) if not negative_returns.empty else None
        profit_factor = gross_profit / abs_gross_loss if abs_gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
        gain_loss_ratio = (
            abs(float(average_win) / float(average_loss))
            if average_win is not None and average_loss not in {None, 0.0}
            else None
        )
        finite_returns = returns.astype(float)
        return_count = int(len(finite_returns))
        nonzero_returns = finite_returns[finite_returns != 0.0]
        profitable_period_ratio = float((nonzero_returns > 0.0).mean()) if not nonzero_returns.empty else None
        max_consecutive_wins, max_consecutive_losses = self._consecutive_return_streaks(finite_returns)
        monthly_rows = self._period_return_rows(equity_df, "ME")
        monthly_returns = [
            float(row["return"])
            for row in monthly_rows
            if row.get("return") is not None
        ]
        best_month = max(monthly_returns) if monthly_returns else None
        worst_month = min(monthly_returns) if monthly_returns else None
        positive_month_ratio = (
            sum(1 for value in monthly_returns if value > 0.0) / len(monthly_returns)
            if monthly_returns
            else None
        )
        max_dd_duration = self._drawdown_duration(drawdown, equity_df.get("Time"))
        var_95 = self._finite_or_none(finite_returns.quantile(0.05)) if return_count else None
        var_99 = self._finite_or_none(finite_returns.quantile(0.01)) if return_count else None
        cvar_95 = self._tail_mean(finite_returns, var_95)
        cvar_99 = self._tail_mean(finite_returns, var_99)
        return {
            "total_return": total_return,
            "cagr": cagr,
            "sharpe": self._finite_or_none(sharpe),
            "sortino": self._finite_or_none(sortino),
            "calmar": self._finite_or_none(calmar),
            "max_drawdown": max_drawdown,
            "average_drawdown": float(drawdown.mean() or 0.0),
            "max_drawdown_duration_periods": max_dd_duration.get("periods"),
            "max_drawdown_duration_days": max_dd_duration.get("days"),
            "std": self._finite_or_none(return_std),
            "annualized_std": self._finite_or_none(annualized_std),
            "downside_risk": self._finite_or_none(downside_std),
            "annualized_downside_risk": self._finite_or_none(annualized_downside),
            "recovery_factor": recovery_factor,
            "trade_count": rebalance_count,
            "rebalance_count": rebalance_count,
            "return_observation_count": return_count,
            "win_rate": profitable_period_ratio,
            "profitable_period_ratio": profitable_period_ratio,
            "percent_profitable_trades": profitable_period_ratio,
            "profit_factor": self._finite_or_none(profit_factor),
            "avg_trade_return": self._finite_or_none(finite_returns.mean()) if return_count else None,
            "average_win": self._finite_or_none(average_win),
            "average_loss": self._finite_or_none(average_loss),
            "average_win_loss_ratio": self._finite_or_none(gain_loss_ratio),
            "gain_loss_ratio": self._finite_or_none(gain_loss_ratio),
            "gross_profit": self._finite_or_none(gross_profit),
            "gross_loss": self._finite_or_none(gross_loss),
            "max_consecutive_wins": max_consecutive_wins,
            "max_consecutive_losses": max_consecutive_losses,
            "skewness": self._finite_or_none(finite_returns.skew()) if return_count >= 3 else None,
            "kurtosis": self._finite_or_none(finite_returns.kurt()) if return_count >= 4 else None,
            "var_95": var_95,
            "cvar_95": cvar_95,
            "var_99": var_99,
            "cvar_99": cvar_99,
            "best_month": self._finite_or_none(best_month),
            "worst_month": self._finite_or_none(worst_month),
            "positive_month_ratio": self._finite_or_none(positive_month_ratio),
            "exposure_time": float((pd.to_numeric(equity_df.get("Cash_weight", pd.Series(dtype=float)), errors="coerce").fillna(1.0) < 1.0).mean()),
            "final_equity": end,
            "start_equity": start,
        }

    @classmethod
    def _consecutive_return_streaks(cls, returns: pd.Series) -> tuple[int, int]:
        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0
        for value in pd.to_numeric(returns, errors="coerce").dropna().tolist():
            numeric = float(value)
            if numeric > 0:
                current_wins += 1
                current_losses = 0
            elif numeric < 0:
                current_losses += 1
                current_wins = 0
            else:
                current_wins = 0
                current_losses = 0
            max_wins = max(max_wins, current_wins)
            max_losses = max(max_losses, current_losses)
        return max_wins, max_losses

    def _drawdown_duration(self, drawdown: pd.Series, time_values: Any) -> Dict[str, Any]:
        in_drawdown = pd.to_numeric(drawdown, errors="coerce").fillna(0.0) < 0.0
        times = pd.to_datetime(time_values, errors="coerce") if time_values is not None else pd.Series(dtype="datetime64[ns]")
        max_periods = 0
        max_days: Optional[int] = None
        current_start_index: Optional[int] = None
        current_periods = 0
        for index, active in enumerate(in_drawdown.tolist()):
            if active:
                if current_start_index is None:
                    current_start_index = index
                    current_periods = 0
                current_periods += 1
                if current_periods > max_periods:
                    max_periods = current_periods
                    if len(times) > index and current_start_index is not None:
                        start_time = times.iloc[current_start_index]
                        end_time = times.iloc[index]
                        if pd.notna(start_time) and pd.notna(end_time):
                            max_days = int(max(0, (end_time - start_time).days))
            else:
                current_start_index = None
                current_periods = 0
        return {"periods": max_periods, "days": max_days}

    def _tail_mean(self, returns: pd.Series, threshold: Optional[float]) -> Optional[float]:
        if threshold is None:
            return None
        tail = pd.to_numeric(returns, errors="coerce").dropna()
        tail = tail[tail <= float(threshold)]
        if tail.empty:
            return None
        return self._finite_or_none(tail.mean())

    @staticmethod
    def _coerce_datetime_series(values: Any, index: Any = None) -> pd.Series:
        parsed = pd.to_datetime(values, errors="coerce", utc=True)
        if isinstance(parsed, pd.Series):
            return parsed.dt.tz_convert(None)
        return pd.Series(pd.DatetimeIndex(parsed).tz_convert(None), index=index)

    def _coerce_equity_frame(self, equity_df: pd.DataFrame) -> pd.DataFrame:
        if equity_df.empty:
            return pd.DataFrame(columns=["Time", "Equity_value"])
        frame = equity_df.copy()
        time_source: Any = frame["Time"] if "Time" in frame.columns else None
        if time_source is None:
            for candidate in ("time", "Date", "date", "Datetime", "datetime", "Timestamp", "timestamp"):
                if candidate in frame.columns:
                    time_source = frame[candidate]
                    break
        time_series = (
            self._coerce_datetime_series(time_source, index=frame.index)
            if time_source is not None
            else pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
        )
        if time_series.notna().sum() == 0 and not isinstance(frame.index, pd.RangeIndex):
            time_series = self._coerce_datetime_series(frame.index, index=frame.index)
        if "Equity_value" not in frame.columns:
            for candidate in ("equity_value", "Equity", "equity", "Value", "value"):
                if candidate in frame.columns:
                    frame["Equity_value"] = frame[candidate]
                    break
        if "Equity_value" not in frame.columns:
            return pd.DataFrame(columns=["Time", "Equity_value"])
        frame["Time"] = time_series
        frame["Equity_value"] = pd.to_numeric(frame["Equity_value"], errors="coerce")
        return frame.dropna(subset=["Time", "Equity_value"]).sort_values("Time").reset_index(drop=True)

    def _period_return_rows(self, equity_df: pd.DataFrame, freq: str) -> List[Dict[str, Any]]:
        frame = self._coerce_equity_frame(equity_df)
        if frame.empty:
            return []
        indexed = frame.set_index("Time")["Equity_value"].sort_index()
        indexed = indexed[~indexed.index.duplicated(keep="last")]
        if indexed.empty:
            return []
        grouped = {period: values.dropna() for period, values in indexed.resample(freq)}
        period_close = indexed.resample(freq).last().ffill().dropna()
        rows: List[Dict[str, Any]] = []
        previous_close: Optional[float] = None
        for period, end_value in period_close.items():
            values = grouped.get(period, pd.Series(dtype=float))
            end_equity = self._finite_or_none(end_value)
            if end_equity is None:
                continue
            start_equity = previous_close
            if start_equity is None:
                if len(values) < 2:
                    previous_close = end_equity
                    continue
                start_equity = self._finite_or_none(values.iloc[0])
            period_return = (
                float(end_equity / start_equity - 1.0)
                if start_equity not in (None, 0.0)
                else None
            )
            is_monthly = freq.upper().startswith("M")
            rows.append(
                {
                    "period": period.strftime("%Y-%m") if is_monthly else period.strftime("%Y"),
                    "year": int(period.year),
                    "month": int(period.month) if is_monthly else None,
                    "return": self._finite_or_none(period_return),
                    "start_equity": self._finite_or_none(start_equity),
                    "end_equity": end_equity,
                }
            )
            previous_close = end_equity
        return rows

    def _benchmark_correlation_from_series(
        self,
        equity_series: List[Dict[str, Any]],
        benchmark_series: List[Dict[str, Any]],
    ) -> Optional[float]:
        if not equity_series or not benchmark_series:
            return None
        equity_frame = pd.DataFrame(equity_series)
        benchmark_frame = pd.DataFrame(benchmark_series)
        if "time" not in equity_frame.columns or "value" not in equity_frame.columns:
            return None
        if "time" not in benchmark_frame.columns or "value" not in benchmark_frame.columns:
            return None
        equity_frame["time"] = pd.to_datetime(equity_frame["time"], errors="coerce").dt.normalize()
        benchmark_frame["time"] = pd.to_datetime(benchmark_frame["time"], errors="coerce").dt.normalize()
        equity_frame["value"] = pd.to_numeric(equity_frame["value"], errors="coerce")
        benchmark_frame["value"] = pd.to_numeric(benchmark_frame["value"], errors="coerce")
        joined = equity_frame.dropna().merge(
            benchmark_frame.dropna(),
            on="time",
            suffixes=("_strategy", "_benchmark"),
        )
        if len(joined) < 3:
            return None
        strategy_returns = joined["value_strategy"].pct_change()
        benchmark_returns = joined["value_benchmark"].pct_change()
        corr = strategy_returns.corr(benchmark_returns)
        return self._finite_or_none(corr)

    def _portfolio_strategy_summary(self, run_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        base = self._strategy_summary(run_id)
        config = metadata.get("config", {}) if isinstance(metadata.get("config"), dict) else {}
        config = self._portfolio_config_with_benchmark_fallback(run_id, config, metadata)
        universe = config.get("universe", {}) if isinstance(config.get("universe"), dict) else {}
        rebalance = config.get("rebalance", {}) if isinstance(config.get("rebalance"), dict) else {}
        selection = config.get("selection", {}) if isinstance(config.get("selection"), dict) else {}
        allocation = config.get("allocation", {}) if isinstance(config.get("allocation"), dict) else {}
        benchmark = config.get("benchmark", {}) if isinstance(config.get("benchmark"), dict) else {}
        benchmark_label = benchmark.get("label") or benchmark.get("symbol") or base.get("benchmark_label") or ""
        source_config = self._source_strategy_config(run_id)
        source_platform = source_config.get("platform", {}) if isinstance(source_config.get("platform"), dict) else {}
        display_rules = self._strategy_rule_display_overrides(source_config, config)
        strategy_mode_id = str(
            config.get("strategy_mode_id")
            or source_platform.get("strategy_mode_id")
            or base.get("strategy_mode_id")
            or "multi_asset_portfolio"
        ).strip()
        parameter_domains = (
            config.get("parameter_domains", {})
            if isinstance(config.get("parameter_domains"), dict)
            else {}
        )
        symbols = universe.get("symbols", []) if isinstance(universe.get("symbols"), list) else []
        trigger = rebalance.get("trigger", {}) if isinstance(rebalance.get("trigger"), dict) else {}
        workflow_id = (
            "parameter_matrix"
            if parameter_domains
            else "single_backtest"
        )
        uses_target_weight_frame = self._uses_target_weight_frame_allocation(allocation)
        if display_rules.get("parameter_domain_label"):
            selection_label = display_rules["parameter_domain_label"]
        elif uses_target_weight_frame:
            selection_label = self._render_target_weight_frame_parameter_label(parameter_domains)
        elif parameter_domains:
            selection_label = self._render_parameter_domains(parameter_domains)
        elif allocation.get("method") in {"fixed_weight", "fixed_weights", "static_weight", "static_weights"}:
            selection_label = "fixed weights"
        elif isinstance(config.get("signals"), dict) and config.get("signals", {}).get("entry"):
            selection_label = self._render_parameter_domains({})
        else:
            selection_label = self._render_selection_parameter_label(selection, allocation)
        base.update(
            {
                "strategy_id": metadata.get("strategy_id") or config.get("strategy_id") or base.get("strategy_id"),
                "name": metadata.get("strategy_id") or config.get("strategy_id") or base.get("name"),
                "asset_label": ", ".join(str(item) for item in symbols) if symbols else base.get("asset_label", "Multi-asset"),
                "strategy_mode_id": strategy_mode_id,
                "mode_label": display_rules.get("mode_label")
                or self._strategy_mode_label(strategy_mode_id)
                or "Multi-asset portfolio",
                "workflow_label": self._workflow_label(workflow_id),
                "workflow_id": workflow_id,
                "entry_rule": display_rules.get("entry_rule")
                or (
                    "Target weights are loaded from the configured target-weight frame"
                    if uses_target_weight_frame
                    else ""
                )
                or self._render_normalized_entry_rule(config.get("signals", {}), selection, rebalance)
                or f"Rebalance on {trigger.get('op', 'calendar trigger')}",
                "exit_rule": display_rules.get("exit_rule")
                or (
                    "Weights change when the target-weight frame changes"
                    if uses_target_weight_frame
                    else ""
                )
                or self._render_normalized_exit_rule(config.get("signals", {}), rebalance, allocation)
                or "Replaced or resized at next rebalance",
                "parameter_domains": parameter_domains,
                "parameter_domain_label": selection_label,
                "benchmark_label": benchmark_label,
                "source": "multi_asset_portfolio_config",
            }
        )
        return self._attach_strategy_summary_display(base)

    @staticmethod
    def _uses_target_weight_frame_allocation(allocation: Any) -> bool:
        if not isinstance(allocation, dict):
            return False
        method = str(allocation.get("method") or "").strip().lower()
        return method == "target_weight_frame" or bool(
            allocation.get("target_weight_frame") or allocation.get("target_weight")
        )

    def _render_target_weight_frame_parameter_label(self, parameter_domains: Any) -> str:
        if not isinstance(parameter_domains, dict) or not parameter_domains:
            return "Configured target-weight frame"
        rendered = self._render_parameter_domains(parameter_domains)
        return rendered if rendered else "Configured target-weight frame"

    @staticmethod
    def _render_selection_parameter_label(selection: Any, allocation: Any) -> str:
        if not isinstance(selection, dict):
            selection = {}
        if not isinstance(allocation, dict):
            allocation = {}
        rank_by = str(selection.get("rank_by") or "").strip()
        top_n = selection.get("top_n")
        position_limit = allocation.get("position_limit")
        pieces: List[str] = []
        if rank_by and top_n not in {None, ""}:
            pieces.append(f"select top {top_n} by {rank_by}")
        elif rank_by:
            pieces.append(f"rank by {rank_by}")
        elif top_n not in {None, ""}:
            pieces.append(f"select top {top_n}")
        if position_limit not in {None, ""}:
            pieces.append(f"max position {position_limit}")
        return "; ".join(pieces) if pieces else "Configured portfolio selection"

    @classmethod
    def _strategy_rule_display_overrides(cls, *configs: Any) -> Dict[str, str]:
        candidates: List[Any] = []
        for config in configs:
            if not isinstance(config, dict):
                continue
            candidates.extend(
                [
                    config.get("strategy_rules"),
                    config.get("presentation", {}).get("strategy_rules")
                    if isinstance(config.get("presentation"), dict)
                    else None,
                    config.get("display", {}).get("strategy_rules")
                    if isinstance(config.get("display"), dict)
                    else None,
                    config.get("metadata", {}).get("strategy_rules")
                    if isinstance(config.get("metadata"), dict)
                    else None,
                ]
            )
        out: Dict[str, str] = {}
        key_aliases = {
            "mode": "mode_label",
            "entry": "entry_rule",
            "exit": "exit_rule",
            "domain": "parameter_domain_label",
            "parameter_domain": "parameter_domain_label",
        }
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key, value in candidate.items():
                normalized_key = key_aliases.get(str(key), str(key))
                if normalized_key not in {
                    "mode_label",
                    "entry_rule",
                    "exit_rule",
                    "parameter_domain_label",
                    "execution_label",
                    "cost_label",
                    "risk_label",
                }:
                    continue
                if normalized_key in {"entry_rule", "exit_rule"}:
                    if isinstance(value, dict):
                        text = cls._render_rule_node(value).strip()
                    else:
                        text = str(value or "").strip()
                        if text.startswith("{") and text.endswith("}"):
                            try:
                                parsed_rule = json.loads(text)
                            except json.JSONDecodeError:
                                parsed_rule = None
                            if isinstance(parsed_rule, dict):
                                text = cls._render_rule_node(parsed_rule).strip()
                else:
                    text = str(value or "").strip()
                if text:
                    out[normalized_key] = text
        return out

    def _frame_preview(self, frame: pd.DataFrame, *, limit: int) -> List[Dict[str, Any]]:
        if frame.empty:
            return []
        preview = frame.head(limit).copy()
        for column in preview.columns:
            if pd.api.types.is_datetime64_any_dtype(preview[column]):
                preview[column] = preview[column].map(self._to_iso)
            else:
                preview[column] = preview[column].map(self._json_safe_value)
        return preview.where(pd.notna(preview), None).to_dict(orient="records")

    def _finite_or_none(self, value: Any) -> Optional[float]:
        parsed = self._as_float(value)
        return parsed if math.isfinite(parsed) else None

    def _optional_bool(self, value: Any) -> Optional[bool]:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(value, str):
            text = value.strip().lower()
            if not text:
                return None
            if text in {"1", "true", "yes", "y", "on"}:
                return True
            if text in {"0", "false", "no", "n", "off"}:
                return False
        try:
            return bool(int(value))
        except (TypeError, ValueError):
            return bool(value)

    def _json_safe_value(self, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return [self._json_safe_value(item) for item in value]
        if hasattr(value, "tolist"):
            converted = value.tolist()
            if converted is not value:
                return self._json_safe_value(converted)
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value

    def _downsample_xy(
        self,
        x_values: List[Any],
        y_values: List[Any],
        *,
        max_points: int,
    ) -> tuple[List[Any], List[Any]]:
        if len(x_values) <= max_points or len(y_values) <= max_points:
            return x_values, y_values
        if max_points <= 2:
            return [x_values[0], x_values[-1]], [y_values[0], y_values[-1]]
        stride = max(1, len(x_values) // (max_points - 1))
        sampled_x = x_values[::stride]
        sampled_y = y_values[::stride]
        if sampled_x[-1] != x_values[-1]:
            sampled_x.append(x_values[-1])
            sampled_y.append(y_values[-1])
        return sampled_x, sampled_y

    def ensure_parameter_matrix_payload(
        self,
        run_id: str,
        *,
        force: bool = False,
    ) -> Path:
        payload = self._build_parameter_matrix_payload(run_id, force=force)
        path = self._chart_path(run_id, "parameter_heatmap_payload.json")
        self._write_json(path, payload)
        return path

    def build_parameter_matrix_payload(
        self,
        run_id: str,
        *,
        force: bool = False,
        ranking_config_override: Optional[Dict[str, Any]] = None,
        acceptance_config_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._build_parameter_matrix_payload(
            run_id,
            force=force,
            ranking_config_override=ranking_config_override,
            acceptance_config_override=acceptance_config_override,
        )

    def _build_parameter_matrix_payload(
        self,
        run_id: str,
        *,
        force: bool = False,
        ranking_config_override: Optional[Dict[str, Any]] = None,
        acceptance_config_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        path = self._chart_path(run_id, "parameter_heatmap_payload.json")
        if (
            path.exists()
            and not force
            and ranking_config_override is None
            and acceptance_config_override is None
        ):
            cached_payload = self._load_json(path, {})
            if (
                isinstance(cached_payload, dict)
                and cached_payload.get("schema_version") == PARAMETER_HEATMAP_SCHEMA_VERSION
            ):
                return cached_payload
        # Parameter review may be rebuilt with ranking/acceptance overrides on
        # every UI request.  The metrics overview is the expensive parquet
        # aggregation layer, so reuse its validated cache instead of forcing a
        # full portfolio artifact rescan.
        overview_path = self.ensure_metrics_overview_payload(run_id, force=False)
        overview = self._load_json(overview_path, {})
        rows = overview.get("rows", []) if isinstance(overview, dict) else []
        if not rows:
            raise FileNotFoundError(
                "metrics overview rows missing for parameter matrix payload"
            )
        execution_plan_path = self._snapshot_path(run_id, "execution_plan.json")
        if not execution_plan_path.exists() and overview.get("result_type") == "portfolio":
            payload = self._build_portfolio_parameter_matrix_payload(
                run_id=run_id,
                overview=overview,
                overview_path=overview_path,
                ranking_config_override=ranking_config_override,
                acceptance_config_override=acceptance_config_override,
            )
            payload["schema_version"] = PARAMETER_HEATMAP_SCHEMA_VERSION
            payload["generated_at"] = self._now_iso()
            return payload
        if not execution_plan_path.exists():
            return self._build_no_parameter_matrix_payload(
                run_id=run_id,
                overview=overview,
                overview_path=overview_path,
                reason=(
                    "No parameter domain is available for this run. "
                    "Parameter Research only applies to runs with at least two varied parameters."
                ),
                result_type=str(overview.get("result_type") or "single_asset"),
                artifact_type=str(overview.get("artifact_type") or ""),
            )

        plan = self._load_json(execution_plan_path, {})
        index_map = self._load_backtest_index_map(run_id)
        param_axes = [
            axis.get("name")
            for axis in plan.get("param_axes", [])
            if axis.get("name")
        ]
        matrix_rows: List[Dict[str, Any]] = []
        sorted_rows = sorted(
            rows,
            key=lambda item: item.get("sharpe") or float("-inf"),
            reverse=True,
        )
        for rank, row in enumerate(sorted_rows, start=1):
            backtest_id = str(row.get("backtest_id", ""))
            combo = index_map.get(backtest_id, {})
            payload_row: Dict[str, Any] = dict(row)
            payload_row["rank"] = rank
            payload_row["semantic_combo"] = combo.get("semantic_combo", {}) or row.get("semantic_combo", {})
            payload_row["strategy_id"] = combo.get("strategy_id") or row.get("strategy_id")
            payload_row["strategy_display_label"] = combo.get("strategy_display_label") or row.get("label")
            matrix_rows.append(payload_row)

        if len(param_axes) < 2:
            param_axes = self._infer_param_axes(matrix_rows)
        if len(param_axes) < 2:
            return self._build_no_parameter_matrix_payload(
                run_id=run_id,
                overview=overview,
                overview_path=overview_path,
                reason=(
                    "This run does not expose enough varied semantic parameters for Parameter Research. "
                    "Open the Backtests tab to review the fixed strategy result."
                ),
                result_type=str(overview.get("result_type") or "single_asset"),
                artifact_type=str(overview.get("artifact_type") or ""),
            )

        future_live_search_config = self._load_future_live_search_config(run_id)
        ranking_config = copy.deepcopy(future_live_search_config.get("ranking") or {})
        acceptance_config = copy.deepcopy(future_live_search_config.get("acceptance") or {})
        if isinstance(ranking_config_override, dict) and ranking_config_override:
            ranking_config = self._deep_merge(ranking_config, ranking_config_override)
        if isinstance(acceptance_config_override, dict):
            acceptance_config = self._deep_merge(acceptance_config, acceptance_config_override)
        payload = self.heatmap_builder.build_payload(
            run_id=run_id,
            rows=matrix_rows,
            param_axes=param_axes,
            ranking_config=ranking_config,
            acceptance_config=acceptance_config,
        )
        payload["schema_version"] = PARAMETER_HEATMAP_SCHEMA_VERSION
        payload["generated_at"] = self._now_iso()
        payload["dataset_label"] = self._parameter_matrix_dataset_label(run_id, overview, matrix_rows)
        payload["future_live_search_config"] = future_live_search_config
        payload["artifact_source_refs"] = [str(execution_plan_path), str(overview_path)]
        if future_live_search_config.get("config_path"):
            payload["artifact_source_refs"].append(str(future_live_search_config["config_path"]))
        return payload

    def _build_no_parameter_matrix_payload(
        self,
        *,
        run_id: str,
        overview: Dict[str, Any],
        overview_path: Path,
        reason: str,
        result_type: str,
        artifact_type: str,
    ) -> Dict[str, Any]:
        rows = overview.get("rows", []) if isinstance(overview, dict) else []
        return {
            "schema_version": PARAMETER_HEATMAP_SCHEMA_VERSION,
            "contract_id": "lo2cin4bt-app-parameter-heatmap-payload",
            "run_id": run_id,
            "availability": "no_parameter_domain",
            "reason": reason,
            "result_type": result_type,
            "artifact_type": artifact_type,
            "dataset_label": self._parameter_matrix_dataset_label(run_id, overview, []),
            "rows": [],
            "source_row_count": len(rows) if isinstance(rows, list) else 0,
            "shortlist_rows": [],
            "cluster_summary": [],
            "parameter_importance": [],
            "study_summary": {
                "sampler": "not_applicable",
                "mode": "not_applicable",
                "objective": "",
                "n_trials": 0,
                "n_startup_trials": 0,
                "completed_trials": 0,
                "pruned_trials": 0,
                "best_robust_score": None,
                "accepted_candidate_count": 0,
                "cluster_count": 0,
                "warnings": ["no_parameter_domain"],
            },
            "objectives": [],
            "param_axes": [],
            "default_x_axis": "",
            "default_y_axis": "",
            "aggregation_modes": [],
            "reduction_modes": [],
            "axis_values": {},
            "search_source_options": [],
            "default_search_source": "all_existing_results",
            "ml_search_status": "not_applicable",
            "selected_representative_mode": "",
            "future_live_search_config": {
                "label": "No parameter domain",
                "mode": "not_applicable",
                "note": reason,
            },
            "artifact_source_refs": [str(overview_path)],
            "generated_at": self._now_iso(),
        }

    def _build_portfolio_parameter_matrix_payload(
        self,
        *,
        run_id: str,
        overview: Dict[str, Any],
        overview_path: Path,
        ranking_config_override: Optional[Dict[str, Any]],
        acceptance_config_override: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        rows = overview.get("rows", []) if isinstance(overview, dict) else []
        matrix_rows: List[Dict[str, Any]] = []
        sorted_rows = sorted(
            rows,
            key=lambda item: item.get("sharpe") or float("-inf"),
            reverse=True,
        )
        for rank, row in enumerate(sorted_rows, start=1):
            combo = row.get("semantic_combo", {}) if isinstance(row, dict) else {}
            payload_row = dict(row)
            payload_row["rank"] = rank
            payload_row["semantic_combo"] = combo if isinstance(combo, dict) else {}
            payload_row["strategy_display_label"] = row.get("label")
            matrix_rows.append(payload_row)
        param_axes = self._infer_param_axes(matrix_rows)
        if len(param_axes) < 2:
            if len(param_axes) == 1 and len(matrix_rows) > 1:
                return self._build_table_only_parameter_matrix_payload(
                    run_id=run_id,
                    rows=matrix_rows,
                    param_axes=param_axes,
                    overview_path=overview_path,
                    result_type="portfolio",
                    artifact_type=str(overview.get("artifact_type", "multi_asset_portfolio_backtest")),
                )
            return self._build_no_parameter_matrix_payload(
                run_id=run_id,
                overview=overview,
                overview_path=overview_path,
                reason=(
                    "No varied portfolio parameter domain is available for this run. "
                    "Fixed portfolios and single-policy portfolio runs should be reviewed in Backtests."
                ),
                result_type="portfolio",
                artifact_type=str(overview.get("artifact_type", "multi_asset_portfolio_backtest")),
            )
        ranking_config = copy.deepcopy(ranking_config_override or {})
        acceptance_config = copy.deepcopy(acceptance_config_override or {})
        payload = self.heatmap_builder.build_payload(
            run_id=run_id,
            rows=matrix_rows,
            param_axes=param_axes,
            ranking_config=ranking_config,
            acceptance_config=acceptance_config,
        )
        payload["result_type"] = "portfolio"
        payload["artifact_type"] = overview.get("artifact_type", "multi_asset_portfolio_backtest")
        payload["dataset_label"] = self._parameter_matrix_dataset_label(run_id, overview, matrix_rows)
        payload["future_live_search_config"] = {
            "label": "Portfolio parameter matrix",
            "source_filename": "portfolio metrics artifacts",
            "mode": "post_run_review",
            "note": "Derived from multi-asset portfolio variants inside the selected metrics run.",
        }
        payload["artifact_source_refs"] = [str(overview_path)]
        return payload

    def _build_table_only_parameter_matrix_payload(
        self,
        *,
        run_id: str,
        rows: List[Dict[str, Any]],
        param_axes: List[str],
        overview_path: Path,
        result_type: str,
        artifact_type: str,
    ) -> Dict[str, Any]:
        values = sorted(
            {
                str((row.get("semantic_combo") or {}).get(param_axes[0]))
                for row in rows
                if isinstance(row.get("semantic_combo"), dict)
                and (row.get("semantic_combo") or {}).get(param_axes[0]) is not None
            }
        )
        return {
            "schema_version": PARAMETER_HEATMAP_SCHEMA_VERSION,
            "contract_id": "lo2cin4bt-app-parameter-heatmap-payload",
            "run_id": run_id,
            "availability": "table_only_single_axis",
            "reason": "This run varies one parameter axis, so it is shown as ranked candidates rather than a two-axis heatmap.",
            "result_type": result_type,
            "artifact_type": artifact_type,
            "dataset_label": self._parameter_matrix_dataset_label(run_id, {}, rows),
            "rows": rows,
            "source_row_count": len(rows),
            "shortlist_rows": rows[: min(20, len(rows))],
            "cluster_summary": [],
            "parameter_importance": [],
            "study_summary": {
                "sampler": "post_run_review",
                "mode": "single_axis_parameter_review",
                "objective": "sharpe",
                "n_trials": len(rows),
                "n_startup_trials": 0,
                "completed_trials": len(rows),
                "pruned_trials": 0,
                "best_robust_score": rows[0].get("sharpe") if rows else None,
                "accepted_candidate_count": len(rows),
                "cluster_count": 0,
                "warnings": ["single_axis_table_only"],
            },
            "objectives": ["sharpe", "total_return", "cagr", "max_drawdown"],
            "param_axes": param_axes,
            "default_x_axis": param_axes[0] if param_axes else "",
            "default_y_axis": "",
            "aggregation_modes": ["ranked_table"],
            "reduction_modes": [],
            "axis_values": {param_axes[0]: values} if param_axes else {},
            "search_source_options": [],
            "default_search_source": "all_existing_results",
            "ml_search_status": "not_applicable",
            "selected_representative_mode": "ranked_table",
            "future_live_search_config": {
                "label": "Single-axis parameter review",
                "source_filename": "portfolio metrics artifacts",
                "mode": "post_run_review",
                "note": "Derived from variants that differ by one categorical parameter.",
            },
            "artifact_source_refs": [str(overview_path)],
            "generated_at": self._now_iso(),
        }

    def ensure_backtest_detail_payload(
        self,
        run_id: str,
        backtest_id: str,
        *,
        force: bool = False,
    ) -> Path:
        safe_id = str(backtest_id)
        payload_file_id = self._safe_payload_filename(safe_id)
        path = self._chart_path(run_id, f"backtest_detail_{payload_file_id}.json")
        backtest_path = self._artifact_path(run_id, "backtester_parquet")
        metrics_path = self._artifact_path(run_id, "metricstracker_parquet")
        if backtest_path is None:
            portfolio_metadata_paths = self._artifact_paths(run_id, "portfolio_metadata_json")
            portfolio_equity_paths = self._artifact_paths(run_id, "portfolio_equity_curve_parquet")
            if portfolio_metadata_paths and portfolio_equity_paths:
                payload = self._build_portfolio_backtest_detail_payload(run_id, safe_id)
                self._write_json(path, payload)
                return path
            raise FileNotFoundError("backtest detail requires backtester parquet")
        if path.exists() and not force:
            cached_payload = self._load_json(path, {})
            if (
                isinstance(cached_payload, dict)
                and cached_payload.get("schema_version") == BACKTEST_DETAIL_SCHEMA_VERSION
                and self._payload_source_refs_exist(cached_payload)
            ):
                return path

        backtest_df = pd.read_parquet(backtest_path)
        detail_df = backtest_df[backtest_df["Backtest_id"].astype(str) == safe_id].copy()
        if detail_df.empty:
            raise FileNotFoundError(
                f"backtest {safe_id} not found in backtester parquet"
            )

        metric_curve = pd.DataFrame()
        if metrics_path is not None:
            metrics_df = pd.read_parquet(metrics_path)
            metric_curve = metrics_df[metrics_df["Backtest_id"].astype(str) == safe_id].copy()
        period_equity = self._coerce_equity_frame(metric_curve if not metric_curve.empty else detail_df)

        metadata_path = self._metrics_metadata_path(metrics_path) if metrics_path is not None else None
        metrics_meta = (
            self._load_json(metadata_path, [])
            if metadata_path is not None and metadata_path.exists()
            else []
        )
        metrics_row = next(
            (
                item
                for item in metrics_meta
                if str(item.get("Backtest_id", "")) == safe_id
            ),
            {},
        )
        combo = self._load_backtest_index_map(run_id).get(safe_id, {})
        trade_rows = self._build_trade_summary_rows(detail_df)
        equity_series = [
            {"time": self._to_iso(v), "value": self._as_float(y)}
            for v, y in zip(
                (
                    metric_curve["Time"].tolist()
                    if not metric_curve.empty
                    else detail_df["Time"].tolist()
                ),
                (
                    metric_curve["Equity_value"].tolist()
                    if not metric_curve.empty
                    else detail_df["Equity_value"].tolist()
                ),
            )
        ]
        benchmark_series = [
            {"time": self._to_iso(v), "value": self._as_float(y)}
            for v, y in zip(
                metric_curve.get("Time", []).tolist(),
                metric_curve.get("BAH_Equity", []).tolist(),
            )
        ] if not metric_curve.empty and "BAH_Equity" in metric_curve.columns else []
        payload = {
            "schema_version": BACKTEST_DETAIL_SCHEMA_VERSION,
            "contract_id": "lo2cin4bt-app-backtest-detail-payload-v1",
            "run_id": run_id,
            "backtest_id": safe_id,
            "label": self._build_backtest_label(safe_id, combo),
            "ohlc": [
                {
                    "time": self._to_iso(row.Time),
                    "open": self._as_float(row.Open),
                    "high": self._as_float(row.High),
                    "low": self._as_float(row.Low),
                    "close": self._as_float(row.Close),
                }
                for row in detail_df.itertuples(index=False)
            ],
            "buy_markers": [
                {
                    "time": self._to_iso(row.Time),
                    "price": self._as_float(row.Close),
                    "action": "buy",
                }
                for row in detail_df.itertuples(index=False)
                if int(getattr(row, "Trade_action", 0) or 0) == 1
            ],
            "sell_markers": [
                {
                    "time": self._to_iso(row.Time),
                    "price": self._as_float(row.Close),
                    "action": "sell",
                }
                for row in detail_df.itertuples(index=False)
                if int(getattr(row, "Trade_action", 0) or 0) == 4
            ],
            "equity_series": equity_series,
            "benchmark_series": benchmark_series,
            "metrics_matrix": self._build_backtest_metrics_matrix(metrics_row),
            "monthly_return_rows": self._period_return_rows(period_equity, "ME"),
            "yearly_return_rows": self._period_return_rows(period_equity, "YE"),
            "trade_rows": trade_rows,
            "trade_outcome_summary": self._build_trade_outcome_summary(trade_rows),
            "risk_diagnostics": self._build_risk_diagnostics(
                trade_rows=trade_rows,
                equity_series=equity_series,
            ),
            "parameter_summary": combo,
            "semantic_fields": combo.get("semantic_fields", []),
            "generated_at": self._now_iso(),
            "artifact_source_refs": [str(backtest_path)]
            + ([str(metrics_path)] if metrics_path is not None else []),
        }
        self._write_json(path, payload)
        return path

    def _build_portfolio_backtest_detail_payload(self, run_id: str, backtest_id: str) -> Dict[str, Any]:
        overview_path = self.ensure_metrics_overview_payload(run_id, force=False)
        overview = self._load_json(overview_path, {})
        portfolio_runs = overview.get("portfolio", {}).get("runs", []) if isinstance(overview, dict) else []
        selected = next(
            (
                item
                for item in portfolio_runs
                if str(item.get("summary", {}).get("backtest_id", "")) == str(backtest_id)
            ),
            None,
        )
        if not isinstance(selected, dict):
            raise FileNotFoundError(f"portfolio backtest {backtest_id} not found")
        artifacts = selected.get("artifact_paths", {}) if isinstance(selected.get("artifact_paths"), dict) else {}
        equity_path = Path(str(artifacts.get("equity_curve", "")))
        holdings_path = Path(str(artifacts.get("holdings", "")))
        rebalance_path = Path(str(artifacts.get("rebalance_audit", "")))
        raw_rebalance_trade_path = str(artifacts.get("rebalance_trades", "") or "").strip()
        rebalance_trade_path = Path(raw_rebalance_trade_path) if raw_rebalance_trade_path else None
        raw_risk_gate_events_path = str(artifacts.get("risk_gate_events", "") or "").strip()
        raw_risk_gate_summary_path = str(artifacts.get("risk_gate_summary", "") or "").strip()
        risk_gate_events_path = Path(raw_risk_gate_events_path) if raw_risk_gate_events_path else None
        risk_gate_summary_path = Path(raw_risk_gate_summary_path) if raw_risk_gate_summary_path else None
        if not equity_path.is_file():
            raise FileNotFoundError(f"portfolio equity curve missing for {backtest_id}")
        equity_df = self._coerce_equity_frame(pd.read_parquet(equity_path))
        if equity_df.empty:
            raise FileNotFoundError(f"portfolio equity curve has no parseable rows for {backtest_id}")
        holdings_df = pd.read_parquet(holdings_path) if holdings_path.is_file() else pd.DataFrame()
        rebalance_df = pd.read_parquet(rebalance_path) if rebalance_path.is_file() else pd.DataFrame()
        rebalance_trades_df = (
            pd.read_parquet(rebalance_trade_path)
            if rebalance_trade_path is not None and rebalance_trade_path.is_file()
            else pd.DataFrame()
        )
        risk_gate_events_df = (
            pd.read_parquet(risk_gate_events_path)
            if risk_gate_events_path is not None and risk_gate_events_path.is_file()
            else pd.DataFrame()
        )
        risk_gate_summary = self._portfolio_risk_gate_summary(
            selected.get("metadata", {}) if isinstance(selected.get("metadata"), dict) else {},
            risk_gate_summary_path if risk_gate_summary_path is not None and risk_gate_summary_path.is_file() else None,
        )
        asset_contribution_rows = self._portfolio_asset_contribution_rows(equity_df)
        contribution_summary = self._portfolio_contribution_summary(
            equity_df,
            metrics=selected.get("summary", {}) if isinstance(selected.get("summary"), dict) else {},
            contribution_rows=asset_contribution_rows,
        )
        turnover_summary = self._portfolio_turnover_summary(equity_df, rebalance_df, rebalance_trades_df)
        active_rebalance_df = self._active_rebalance_events(rebalance_df)
        monthly_return_rows = self._period_return_rows(equity_df, "ME")
        yearly_return_rows = self._period_return_rows(equity_df, "YE")
        drawdown_series = self._portfolio_drawdown_series(equity_df)
        turnover_distribution = self._portfolio_turnover_distribution_rows(
            equity_df,
            active_rebalance_df,
        )
        portfolio_visual_availability = self._portfolio_visual_availability(
            equity_df=equity_df,
            holdings_df=holdings_df,
            rebalance_df=rebalance_df,
            rebalance_trades_df=rebalance_trades_df,
            contribution_rows=asset_contribution_rows,
            drawdown_series=drawdown_series,
            turnover_distribution=turnover_distribution,
            monthly_return_rows=monthly_return_rows,
            yearly_return_rows=yearly_return_rows,
        )
        trade_rows: List[Dict[str, Any]] = []
        if not holdings_df.empty:
            selected_mask = (
                holdings_df["Selected"].astype(bool)
                if "Selected" in holdings_df.columns
                else pd.Series(False, index=holdings_df.index)
            )
            selected_holdings = holdings_df[selected_mask].copy()
            for rank, row in enumerate(selected_holdings.itertuples(index=False), start=1):
                target_weight = self._as_float(getattr(row, "Target_weight", None))
                trade_rows.append(
                    {
                        "rank": rank,
                        "asset": getattr(row, "Asset", ""),
                        "side": "target_weight",
                        "entry_time": self._to_iso(getattr(row, "Time", None)),
                        "exit_time": "",
                        "entry_price": self._as_float(getattr(row, "Score", None)),
                        "exit_price": None,
                        "holding_period": 1,
                        "price_pnl_unit": None,
                        "trade_return": None,
                        "equity_value": None,
                        "target_weight": target_weight,
                        "score": self._as_float(getattr(row, "Score", None)),
                        "selected": True,
                        "eligible": bool(getattr(row, "Eligible", True)),
                        "status": f"weight={target_weight:.3f}",
                    }
                )
        metrics = dict(selected.get("summary", {}) if isinstance(selected.get("summary"), dict) else {})
        metrics.update(self._portfolio_metrics(equity_df))
        if turnover_summary.get("active_rebalance_events") is not None:
            metrics["rebalance_count"] = turnover_summary.get("active_rebalance_events")
        benchmark_series = selected.get("benchmark_series", []) if isinstance(selected.get("benchmark_series"), list) else []
        if benchmark_series:
            strategy_series = [
                {"time": self._to_iso(row.Time), "value": self._finite_or_none(row.Equity_value)}
                for row in equity_df.itertuples(index=False)
            ]
            metrics["benchmark_correlation"] = self._benchmark_correlation_from_series(
                strategy_series,
                benchmark_series,
            )
        first_time = self._to_iso(equity_df["Time"].iloc[0]) if not equity_df.empty else None
        last_time = self._to_iso(equity_df["Time"].iloc[-1]) if not equity_df.empty else None
        strategy_summary = overview.get("strategy_summary", {})
        timezone_label = str(strategy_summary.get("timezone_label") or "America/New_York")
        rebalance_trades_df = self._annotate_trade_event_display(
            rebalance_trades_df,
            timezone_label=timezone_label,
        )
        active_rebalance_df = self._annotate_rebalance_event_display(
            active_rebalance_df,
            rebalance_trades_df,
            timezone_label=timezone_label,
        )
        closed_trade_rows = self._single_asset_portfolio_trade_rows(equity_df, rebalance_trades_df)
        trade_outcome_summary = self._build_trade_outcome_summary(closed_trade_rows)
        equity_series = [
            {"time": self._to_iso(row.Time), "value": self._as_float(row.Equity_value)}
            for row in equity_df.itertuples(index=False)
        ]
        return {
            "schema_version": BACKTEST_DETAIL_SCHEMA_VERSION,
            "contract_id": "lo2cin4bt-app-portfolio-detail-payload-v1",
            "result_type": "portfolio",
            "run_id": run_id,
            "backtest_id": backtest_id,
            "label": metrics.get("label") or backtest_id,
            "strategy_summary": strategy_summary,
            "data_quality": selected.get("data_quality", {}),
            "universe_provenance": selected.get("universe_provenance", {}),
            "factor_feature_audit": selected.get("factor_feature_audit", {}),
            "truth_source_policy": self._truth_source_policy(),
            "truth_warnings": selected.get("truth_warnings", []),
            "date_range_start": first_time,
            "date_range_end": last_time,
            "ohlc": [],
            "buy_markers": [],
            "sell_markers": [],
            "equity_series": equity_series,
            "benchmark_series": benchmark_series,
            "metrics_matrix": metrics,
            "monthly_return_rows": monthly_return_rows,
            "yearly_return_rows": yearly_return_rows,
            "drawdown_series": drawdown_series,
            "turnover_distribution": turnover_distribution,
            "portfolio_visual_availability": portfolio_visual_availability,
            "net_gross_summary": {
                "net_total_return": metrics.get("total_return"),
                "gross_total_return": None,
                "gross_available": False,
                "note": "Gross/no-cost equity is not recorded in this artifact yet; run a net-vs-gross sensitivity batch to compare before/after costs.",
            },
            "slippage_sensitivity": {
                "available": False,
                "note": "Slippage sensitivity requires rerunning this strategy with alternate slippage assumptions.",
            },
            "risk_gate_summary": risk_gate_summary,
            "risk_gate_rows": self._frame_preview(risk_gate_events_df, limit=240) if not risk_gate_events_df.empty else [],
            "trade_outcome_summary": trade_outcome_summary,
            "closed_trade_rows": closed_trade_rows,
            "risk_diagnostics": self._build_risk_diagnostics(
                trade_rows=closed_trade_rows,
                equity_series=equity_series,
            ),
            "trade_rows": trade_rows,
            "holding_rows": trade_rows,
            "asset_contribution_rows": asset_contribution_rows,
            "asset_contribution_summary": contribution_summary,
            "allocation_change_rows": self._frame_preview(rebalance_trades_df, limit=500) if not rebalance_trades_df.empty else [],
            "rebalance_rows": self._frame_preview(active_rebalance_df, limit=240) if not active_rebalance_df.empty else [],
            "checkpoint_rows": self._frame_preview(rebalance_df, limit=240) if not rebalance_df.empty else [],
            "turnover_summary": turnover_summary,
            "parameter_summary": metrics.get("semantic_combo", {}),
            "semantic_fields": metrics.get("semantic_fields", []),
            "generated_at": self._now_iso(),
            "artifact_source_refs": [
                str(path)
                for path in [
                    equity_path,
                    holdings_path if holdings_path.is_file() else None,
                    rebalance_path if rebalance_path.is_file() else None,
                    rebalance_trade_path if rebalance_trade_path is not None and rebalance_trade_path.is_file() else None,
                    overview_path,
                    risk_gate_events_path if risk_gate_events_path is not None and risk_gate_events_path.is_file() else None,
                    risk_gate_summary_path if risk_gate_summary_path is not None and risk_gate_summary_path.is_file() else None,
                ]
                if path is not None
            ],
        }

    @classmethod
    def _annotate_trade_event_display(
        cls,
        trades_df: pd.DataFrame,
        *,
        timezone_label: str,
    ) -> pd.DataFrame:
        if trades_df.empty or "Time" not in trades_df.columns:
            return trades_df
        frame = trades_df.copy()
        phases = frame.apply(cls._event_phase_from_row, axis=1)
        frame["Event_phase"] = phases
        frame["Event_time_local"] = [
            cls._event_local_time_for_phase(phase) for phase in phases
        ]
        frame["Event_timezone"] = timezone_label or "America/New_York"
        frame["Event_timestamp_local"] = [
            cls._event_timestamp_label(date_value, phase, timezone_label)
            for date_value, phase in zip(frame["Time"], phases)
        ]
        return frame

    @classmethod
    def _annotate_rebalance_event_display(
        cls,
        rebalance_df: pd.DataFrame,
        rebalance_trades_df: pd.DataFrame,
        *,
        timezone_label: str,
    ) -> pd.DataFrame:
        if rebalance_df.empty or "Time" not in rebalance_df.columns:
            return rebalance_df
        frame = rebalance_df.copy()
        frame["_event_date"] = pd.to_datetime(frame["Time"], errors="coerce").dt.normalize()
        frame["_event_order"] = frame.groupby("_event_date", dropna=False).cumcount()
        if not rebalance_trades_df.empty and "Time" in rebalance_trades_df.columns:
            trades = rebalance_trades_df.copy()
            trades["_event_date"] = pd.to_datetime(trades["Time"], errors="coerce").dt.normalize()
            trades["_event_order"] = trades.groupby("_event_date", dropna=False).cumcount()
            merge_cols = [
                col
                for col in ["_event_date", "_event_order", "Asset", "Action", "Reason"]
                if col in trades.columns
            ]
            frame = frame.merge(
                trades[merge_cols].rename(
                    columns={
                        "Asset": "Display_asset",
                        "Action": "Display_action",
                        "Reason": "Display_reason",
                    }
                ),
                on=["_event_date", "_event_order"],
                how="left",
            )
        phases = frame.apply(cls._event_phase_from_row, axis=1)
        frame["Event_phase"] = phases
        frame["Event_time_local"] = [
            cls._event_local_time_for_phase(phase) for phase in phases
        ]
        frame["Event_timezone"] = timezone_label or "America/New_York"
        frame["Event_timestamp_local"] = [
            cls._event_timestamp_label(date_value, phase, timezone_label)
            for date_value, phase in zip(frame["_event_date"], phases)
        ]
        return frame.drop(columns=["_event_date", "_event_order"], errors="ignore")

    @staticmethod
    def _event_phase_from_row(row: pd.Series) -> str:
        reason = str(row.get("Display_reason") or row.get("Reason") or "").lower()
        action = str(row.get("Display_action") or row.get("Action") or "").lower()
        if "event close" in reason or action == "close_short":
            return "event_close"
        if "event open" in reason or action in {"exit", "new_short"}:
            return "event_open"
        if "session open" in reason or action == "buy":
            return "session_open"
        return ""

    @staticmethod
    def _event_local_time_for_phase(phase: str) -> str:
        if phase == "event_close":
            return "16:00"
        if phase in {"event_open", "session_open"}:
            return "09:30"
        return ""

    @classmethod
    def _event_timestamp_label(
        cls,
        date_value: Any,
        phase: str,
        timezone_label: str,
    ) -> str:
        timestamp = pd.to_datetime(date_value, errors="coerce")
        if pd.isna(timestamp):
            return ""
        local_time = cls._event_local_time_for_phase(phase)
        if not local_time:
            return timestamp.date().isoformat()
        suffix = "ET" if str(timezone_label).strip() == "America/New_York" else str(timezone_label).strip()
        return f"{timestamp.date().isoformat()} {local_time} {suffix}".strip()

    def _portfolio_asset_contribution_rows(self, equity_df: pd.DataFrame) -> List[Dict[str, Any]]:
        contribution_cols = [str(col) for col in equity_df.columns if str(col).startswith("Contribution_")]
        rows: List[Dict[str, Any]] = []
        for contribution_col in contribution_cols:
            asset = contribution_col.removeprefix("Contribution_")
            contribution = pd.to_numeric(equity_df.get(contribution_col), errors="coerce")
            weight = pd.to_numeric(equity_df.get(f"Weight_{asset}", pd.Series(dtype=float)), errors="coerce")
            rows.append(
                {
                    "asset": asset,
                    "return_contribution": self._finite_or_none(contribution.sum(skipna=True)),
                    "avg_weight": self._finite_or_none(weight.mean(skipna=True)),
                    "active_days": int((weight.fillna(0.0) > 0.0).sum()),
                }
            )
        rows.sort(
            key=lambda item: abs(float(item.get("return_contribution") or 0.0)),
            reverse=True,
        )
        return rows

    @staticmethod
    def _portfolio_universe_provenance(
        metadata: Dict[str, Any],
        data_quality: Dict[str, Any],
    ) -> Dict[str, Any]:
        candidates = [
            metadata.get("universe_provenance"),
            data_quality.get("universe_provenance") if isinstance(data_quality, dict) else None,
        ]
        validation = metadata.get("run_validation", {}) if isinstance(metadata.get("run_validation"), dict) else {}
        candidates.append(validation.get("universe_provenance"))
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate:
                return dict(candidate)

        config = metadata.get("config", {}) if isinstance(metadata.get("config"), dict) else {}
        universe = config.get("universe", {}) if isinstance(config.get("universe"), dict) else {}
        symbols = universe.get("symbols", []) if isinstance(universe.get("symbols"), list) else []
        source_ref = (
            universe.get("historical_constituents_path")
            or universe.get("universe_constituents_path")
            or universe.get("constituents_path")
            or universe.get("constituents_path")
            or universe.get("source_path")
            or universe.get("universe_path")
            or universe.get("source_ref")
            or universe.get("source")
        )
        return {
            "schema_version": "universe_provenance.v1",
            "source_type": "explicit_config_symbols" if symbols else "unknown",
            "source_ref": str(source_ref) if source_ref not in (None, "", []) else None,
            "policy": str(
                universe.get("universe_policy")
                or universe.get("survivorship_policy")
                or ""
            ) or None,
            "as_of_date": str(
                universe.get("as_of_date") or universe.get("as_of") or ""
            ) or None,
            "configured_symbols": [str(item) for item in symbols],
            "runtime_symbols": [str(item) for item in symbols],
            "window_count": 0,
            "window_source_snapshots": [],
            "point_in_time_constituents": False,
            "constituents_validation": {
                "schema_version": "historical_universe_constituents_validation.v1",
                "status": "not_available",
                "path": str(source_ref) if source_ref not in (None, "", []) else None,
                "warnings": ["portfolio_metadata_missing_constituents_validation"],
                "errors": [],
            },
            "delisted_policy": str(universe.get("delisted_policy") or "") or None,
            "survivorship_bias_risk": "high" if symbols else "unknown",
            "provenance_status": "review",
            "warnings": ["legacy_metadata_missing_universe_provenance"],
        }

    def _portfolio_data_quality(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        validation = metadata.get("run_validation", {}) if isinstance(metadata.get("run_validation"), dict) else {}
        if validation:
            out = dict(validation)
            out["validation_available"] = True
            out["legacy_missing_validation"] = False
            return out
        config = metadata.get("config", {}) if isinstance(metadata.get("config"), dict) else {}
        universe = config.get("universe", {}) if isinstance(config.get("universe"), dict) else {}
        symbols = universe.get("symbols", []) if isinstance(universe.get("symbols"), list) else []
        return {
            "status": "legacy_missing_validation",
            "validation_available": False,
            "legacy_missing_validation": True,
            "expected_symbols": symbols,
            "loaded_symbols": [],
            "missing_symbols": [],
            "message": "This run was produced before portfolio run validation was exported.",
        }

    @staticmethod
    def _portfolio_factor_feature_audit(
        metadata: Dict[str, Any],
        data_quality: Dict[str, Any],
    ) -> Dict[str, Any]:
        validation = metadata.get("run_validation", {}) if isinstance(metadata.get("run_validation"), dict) else {}
        for candidate in (
            metadata.get("factor_feature_audit"),
            validation.get("factor_feature_audit") if isinstance(validation, dict) else None,
            data_quality.get("factor_feature_audit") if isinstance(data_quality, dict) else None,
        ):
            if isinstance(candidate, dict) and candidate:
                return dict(candidate)
        return {
            "schema_version": "factor_feature_audit.v1",
            "status": "not_applicable",
            "point_in_time_required": False,
            "feature_lag_verified": False,
            "lookahead_guard_verified": False,
            "warnings": [],
            "errors": [],
        }

    @staticmethod
    def _truth_source_policy() -> Dict[str, Any]:
        return {
            "schema_version": "truth_source_policy.v1",
            "mode": "artifact_only",
            "source_refs_required": True,
            "silent_recompute_allowed": False,
            "warning_propagation": "required",
        }

    @staticmethod
    def _truth_warnings(*sections: Dict[str, Any]) -> List[str]:
        warnings: List[str] = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            for key in ("warnings", "errors"):
                values = section.get(key, [])
                if isinstance(values, list):
                    warnings.extend(str(value) for value in values if value not in (None, ""))
            nested = section.get("constituents_validation")
            if isinstance(nested, dict):
                for key in ("warnings", "errors"):
                    values = nested.get(key, [])
                    if isinstance(values, list):
                        warnings.extend(str(value) for value in values if value not in (None, ""))
        return sorted(set(warnings))

    def _portfolio_turnover_summary(
        self,
        equity_df: pd.DataFrame,
        rebalance_df: pd.DataFrame,
        rebalance_trades_df: pd.DataFrame | None = None,
    ) -> Dict[str, Any]:
        turnover = pd.to_numeric(equity_df.get("Turnover", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        trade_cost = pd.to_numeric(equity_df.get("Trade_cost", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        active_turnover = turnover[turnover > 0.0]
        scheduled_events = int(len(rebalance_df)) if not rebalance_df.empty else int((turnover > 0.0).sum())
        active_rebalance_events = int((turnover > 0.0).sum())
        trade_events = int((turnover > 0.0).sum())
        if rebalance_trades_df is not None and not rebalance_trades_df.empty:
            trade_turnover_col = next(
                (
                    col
                    for col in rebalance_trades_df.columns
                    if str(col).lower() in {"trade_turnover", "turnover"}
                ),
                None,
            )
            if trade_turnover_col is not None:
                trade_events = int(
                    (
                        pd.to_numeric(rebalance_trades_df[trade_turnover_col], errors="coerce")
                        .fillna(0.0)
                        .abs()
                        > 0.0
                    ).sum()
                )
            else:
                trade_events = int(len(rebalance_trades_df))
        equity_values = pd.to_numeric(equity_df.get("Equity_value", pd.Series(dtype=float)), errors="coerce").dropna()
        start_equity = self._finite_or_none(equity_values.iloc[0]) if not equity_values.empty else None
        total_cost = self._finite_or_none(trade_cost.sum())
        return {
            "scheduled_events": scheduled_events,
            "checkpoint_events": scheduled_events,
            "active_rebalance_events": active_rebalance_events,
            "trade_events": trade_events,
            "total_trade_turnover": self._finite_or_none(turnover.sum()),
            "avg_trade_turnover": self._finite_or_none(active_turnover.mean() if not active_turnover.empty else 0.0),
            "max_trade_turnover": self._finite_or_none(turnover.max() if not turnover.empty else 0.0),
            "total_trade_cost": total_cost,
            "trade_cost_drag": self._finite_or_none((total_cost or 0.0) / start_equity) if start_equity else None,
        }

    def _portfolio_drawdown_series(self, equity_df: pd.DataFrame) -> List[Dict[str, Any]]:
        if equity_df.empty or "Time" not in equity_df.columns or "Equity_value" not in equity_df.columns:
            return []
        frame = equity_df[["Time", "Equity_value"]].copy()
        frame["Time"] = pd.to_datetime(frame["Time"], errors="coerce")
        frame["Equity_value"] = pd.to_numeric(frame["Equity_value"], errors="coerce")
        frame = frame.dropna(subset=["Time", "Equity_value"]).sort_values("Time")
        if frame.empty:
            return []
        rolling_peak = frame["Equity_value"].cummax()
        safe_peak = rolling_peak.where(rolling_peak != 0.0)
        drawdown = frame["Equity_value"].divide(safe_peak).subtract(1.0).fillna(0.0)
        return [
            {
                "time": self._to_iso(row.Time),
                "drawdown": self._finite_or_none(value),
            }
            for row, value in zip(frame.itertuples(index=False), drawdown.tolist())
        ]

    def _portfolio_turnover_distribution_rows(
        self,
        equity_df: pd.DataFrame,
        active_rebalance_df: pd.DataFrame,
    ) -> List[Dict[str, Any]]:
        source = active_rebalance_df if not active_rebalance_df.empty else equity_df
        if source.empty:
            return []
        turnover_col = next(
            (
                col
                for col in source.columns
                if str(col).lower() in {"turnover", "trade_turnover", "rebalance_turnover"}
            ),
            None,
        )
        if turnover_col is None:
            return []
        time_col = next((col for col in source.columns if str(col).lower() == "time"), None)
        turnover = pd.to_numeric(source[turnover_col], errors="coerce").fillna(0.0).abs()
        rows: List[Dict[str, Any]] = []
        for index, value in turnover.items():
            if value <= 0.0:
                continue
            time_value = source.loc[index, time_col] if time_col is not None else None
            rows.append(
                {
                    "time": self._to_iso(time_value) if time_value is not None else "",
                    "turnover": self._finite_or_none(value),
                }
            )
        return rows

    def _portfolio_visual_availability(
        self,
        *,
        equity_df: pd.DataFrame,
        holdings_df: pd.DataFrame,
        rebalance_df: pd.DataFrame,
        rebalance_trades_df: pd.DataFrame,
        contribution_rows: List[Dict[str, Any]],
        drawdown_series: List[Dict[str, Any]],
        turnover_distribution: List[Dict[str, Any]],
        monthly_return_rows: List[Dict[str, Any]],
        yearly_return_rows: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "asset_contribution": bool(contribution_rows),
            "allocation_timeline": not holdings_df.empty,
            "allocation_change_rows": not rebalance_trades_df.empty,
            "rebalance_turnover_distribution": bool(turnover_distribution),
            "monthly_return_heatmap": bool(monthly_return_rows),
            "yearly_return_rows": bool(yearly_return_rows),
            "drawdown_curve": bool(drawdown_series),
            "rolling_metrics": False,
            "source_row_counts": {
                "equity": int(len(equity_df)),
                "holdings": int(len(holdings_df)),
                "rebalance": int(len(rebalance_df)),
                "rebalance_trades": int(len(rebalance_trades_df)),
                "asset_contribution": int(len(contribution_rows)),
                "drawdown": int(len(drawdown_series)),
                "turnover_distribution": int(len(turnover_distribution)),
                "monthly_returns": int(len(monthly_return_rows)),
                "yearly_returns": int(len(yearly_return_rows)),
            },
        }

    @staticmethod
    def _is_trade_style_portfolio(strategy_summary: Dict[str, Any]) -> bool:
        mode = str(strategy_summary.get("strategy_mode_id") or "").strip()
        return mode in {"single_asset_signal", "calendar_event_session"}

    def _single_asset_portfolio_trade_rows(
        self,
        equity_df: pd.DataFrame,
        rebalance_trades_df: pd.DataFrame,
    ) -> List[Dict[str, Any]]:
        if equity_df.empty or rebalance_trades_df.empty:
            return []
        if "Time" not in equity_df.columns or "Equity_value" not in equity_df.columns:
            return []
        trades = rebalance_trades_df.copy()
        if "Time" not in trades.columns:
            return []
        trades["Time"] = pd.to_datetime(trades["Time"], errors="coerce")
        trades = trades.dropna(subset=["Time"]).sort_values("Time")
        if trades.empty:
            return []
        equity = equity_df[["Time", "Equity_value", "Portfolio_return"] if "Portfolio_return" in equity_df.columns else ["Time", "Equity_value"]].copy()
        equity["Time"] = pd.to_datetime(equity["Time"], errors="coerce")
        equity["Equity_value"] = pd.to_numeric(equity["Equity_value"], errors="coerce")
        if "Portfolio_return" in equity.columns:
            equity["Portfolio_return"] = pd.to_numeric(equity["Portfolio_return"], errors="coerce")
        equity = equity.dropna(subset=["Time", "Equity_value"]).sort_values("Time")
        if equity.empty:
            return []

        def equity_at(time_value: Any) -> Optional[float]:
            timestamp = pd.to_datetime(time_value, errors="coerce")
            if pd.isna(timestamp):
                return None
            matches = equity.loc[equity["Time"] <= timestamp, "Equity_value"]
            if matches.empty:
                return None
            return self._finite_or_none(matches.iloc[-1])

        def period_return_at(time_value: Any) -> Optional[float]:
            if "Portfolio_return" not in equity.columns:
                return None
            timestamp = pd.to_datetime(time_value, errors="coerce")
            if pd.isna(timestamp):
                return None
            matches = equity.loc[equity["Time"] == timestamp, "Portfolio_return"]
            if matches.empty:
                return None
            return self._finite_or_none(matches.iloc[-1])

        open_by_asset: Dict[str, Dict[str, Any]] = {}
        rows: List[Dict[str, Any]] = []
        rank = 1
        for record in trades.to_dict("records"):
            asset = str(record.get("Asset") or record.get("asset") or "").strip() or "asset"
            action = str(record.get("Action") or record.get("action") or "").strip().lower()
            before_weight = self._finite_or_none(record.get("Before_weight") or record.get("before_weight")) or 0.0
            target_weight = self._finite_or_none(record.get("Target_weight") or record.get("target_weight")) or 0.0
            is_entry = action in {"buy", "entry", "short", "new_short", "sell_short"} or (
                abs(before_weight) <= 1e-12 and abs(target_weight) > 1e-12
            )
            is_exit = action in {"exit", "sell", "close", "close_short", "cover"} or (
                abs(before_weight) > 1e-12 and abs(target_weight) <= 1e-12
            )
            if is_entry and not is_exit:
                open_by_asset[asset] = record
                continue
            if not is_exit or asset not in open_by_asset:
                continue
            entry = open_by_asset.pop(asset)
            entry_time = entry.get("Time")
            exit_time = record.get("Time")
            entry_equity = equity_at(entry_time)
            exit_equity = equity_at(exit_time)
            entry_price = self._row_price(entry)
            exit_price = self._row_price(record, prefer_exit=True)
            entry_target_weight = self._finite_or_none(
                entry.get("Target_weight") or entry.get("target_weight")
            )
            side = "short" if (entry_target_weight or 0.0) < 0.0 else "long"
            trade_return = self._finite_or_none(record.get("Trade_return") or record.get("trade_return"))
            trade_return_source = "artifact_trade_return" if trade_return is not None else ""
            if trade_return is None and entry_price is not None and exit_price is not None and entry_price != 0.0:
                if side == "short":
                    trade_return = self._finite_or_none(entry_price / exit_price - 1.0) if exit_price != 0.0 else None
                else:
                    trade_return = self._finite_or_none(exit_price / entry_price - 1.0)
                trade_return_source = "entry_exit_price" if trade_return is not None else ""
            same_timestamp = pd.to_datetime(entry_time, errors="coerce") == pd.to_datetime(exit_time, errors="coerce")
            if trade_return is None and same_timestamp:
                trade_return = period_return_at(exit_time)
                trade_return_source = "portfolio_period_return" if trade_return is not None else ""
            if trade_return is None and entry_equity is not None and exit_equity is not None and entry_equity != 0.0:
                trade_return = self._finite_or_none(exit_equity / entry_equity - 1.0)
                trade_return_source = "portfolio_equity_change" if trade_return is not None else ""
            holding_period = None
            entry_ts = pd.to_datetime(entry_time, errors="coerce")
            exit_ts = pd.to_datetime(exit_time, errors="coerce")
            if not pd.isna(entry_ts) and not pd.isna(exit_ts):
                holding_period = max(0, int((exit_ts - entry_ts).days))
            rows.append(
                {
                    "rank": rank,
                    "trade_group_id": f"single_asset_portfolio:{asset}:{rank}",
                    "asset": asset,
                    "side": side,
                    "entry_time": self._to_iso(entry_time),
                    "exit_time": self._to_iso(exit_time),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "position_size": entry_target_weight,
                    "holding_period": holding_period,
                    "trade_return": trade_return,
                    "trade_return_source": trade_return_source,
                    "price_pnl": None,
                    "entry_reason": entry.get("Reason") or entry.get("reason"),
                    "exit_reason": record.get("Reason") or record.get("reason"),
                    "entry_equity_value": entry_equity,
                    "pre_entry_equity_value": entry_equity,
                    "equity_value": exit_equity,
                    "equity_pnl": self._finite_or_none((exit_equity or 0.0) - (entry_equity or 0.0))
                    if entry_equity is not None and exit_equity is not None
                    else None,
                    "pnl": self._finite_or_none((exit_equity or 0.0) - (entry_equity or 0.0))
                    if entry_equity is not None and exit_equity is not None
                    else None,
                    "status": "closed",
                }
            )
            rank += 1
        return rows

    def _row_price(self, row: Dict[str, Any], *, prefer_exit: bool = False) -> Optional[float]:
        keys = (
            ["Exit_price", "exit_price", "Close_price", "close_price", "Price", "price", "Entry_price", "entry_price"]
            if prefer_exit
            else ["Entry_price", "entry_price", "Open_price", "open_price", "Price", "price", "Exit_price", "exit_price"]
        )
        for key in keys:
            parsed = self._finite_or_none(row.get(key))
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _active_rebalance_events(rebalance_df: pd.DataFrame) -> pd.DataFrame:
        if rebalance_df.empty:
            return rebalance_df
        frame = rebalance_df.copy()
        turnover_col = next(
            (
                col
                for col in frame.columns
                if str(col).lower() in {"turnover", "trade_turnover", "rebalance_turnover"}
            ),
            None,
        )
        if turnover_col is None:
            return frame
        turnover = pd.to_numeric(frame[turnover_col], errors="coerce").fillna(0.0).abs()
        return frame.loc[turnover > 0.0].copy()

    def _portfolio_contribution_summary(
        self,
        equity_df: pd.DataFrame,
        *,
        metrics: Dict[str, Any],
        contribution_rows: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        total_contribution = sum(float(row.get("return_contribution") or 0.0) for row in contribution_rows)
        total_abs = sum(abs(float(row.get("return_contribution") or 0.0)) for row in contribution_rows)
        for row in contribution_rows:
            contribution = float(row.get("return_contribution") or 0.0)
            row["contribution_share"] = self._finite_or_none(abs(contribution) / total_abs) if total_abs else None
        portfolio_total_return = self._finite_or_none(metrics.get("total_return"))
        if portfolio_total_return is None and not equity_df.empty:
            portfolio_total_return = self._portfolio_metrics(equity_df).get("total_return")
        trade_cost = pd.to_numeric(equity_df.get("Trade_cost", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        start_equity_series = pd.to_numeric(equity_df.get("Equity_value", pd.Series(dtype=float)), errors="coerce").dropna()
        cost_drag = None
        if not start_equity_series.empty and float(start_equity_series.iloc[0]) != 0.0:
            cost_drag = self._finite_or_none(float(trade_cost.sum()) / float(start_equity_series.iloc[0]))
        residual = None
        if portfolio_total_return is not None:
            residual = self._finite_or_none(float(portfolio_total_return) - total_contribution)
        return {
            "total_asset_contribution": self._finite_or_none(total_contribution),
            "portfolio_total_return": portfolio_total_return,
            "residual_and_compounding": residual,
            "estimated_cost_drag": cost_drag,
            "note": "Asset contribution is an arithmetic daily weight x return estimate; residual includes compounding, cash, and costs.",
        }

    def ensure_wfa_dashboard_payload(
        self,
        run_id: str,
        *,
        force: bool = False,
    ) -> Path:
        path = self._chart_path(run_id, "wfa_dashboard_payload.json")
        if path.exists() and not force:
            cached_payload = self._load_json(path, {})
            if (
                isinstance(cached_payload, dict)
                and cached_payload.get("schema_version") == WFA_DASHBOARD_SCHEMA_VERSION
                and self._payload_source_refs_exist(cached_payload)
            ):
                return path
        wfa_path = self._wfa_dashboard_artifact_path(run_id)
        if wfa_path is None:
            raise FileNotFoundError("wfa dashboard requires selected-optimum WFA parquet")
        wfa_metadata = self._wfa_sidecar_metadata(wfa_path)
        df = pd.read_parquet(wfa_path)
        if df.empty:
            raise FileNotFoundError("wfa parquet is empty")
        source_row_count = int(len(df))
        legacy_grid_detected = False
        diagnostic_rows: List[Dict[str, Any]] = []
        if "wfa_row_type" in df.columns:
            row_type = df["wfa_row_type"].fillna("").astype(str)
            diagnostic_df = df[row_type != "selected_optimum"].copy()
            if not diagnostic_df.empty:
                diagnostic_rows = diagnostic_df.to_dict("records")
            df = df[row_type == "selected_optimum"].copy()
        elif "window_id" in df.columns:
            rows_per_window = df.groupby("window_id").size()
            legacy_grid_detected = bool(
                not rows_per_window.empty and int(rows_per_window.max()) > 1
            )
            if legacy_grid_detected:
                diagnostic_rows = df.to_dict("records")
                df = df.iloc[0:0].copy()
            else:
                df = df.copy()
                df["wfa_row_type"] = "selected_optimum"
        diagnostic_artifacts = [
            str(path)
            for path in sorted(wfa_path.parent.glob("*candidate_diagnostics*.parquet"))
        ]
        diagnostic_artifacts.extend(
            str(path)
            for path in sorted(wfa_path.parent.glob("*candidate-diagnostics*.parquet"))
            if str(path) not in diagnostic_artifacts
        )
        metric_priority = ["oos_sharpe", "oos_calmar", "oos_total_return", "is_sharpe", "is_calmar"]
        objective = "is_sharpe"
        for key in metric_priority:
            if key not in df.columns:
                continue
            numeric = pd.to_numeric(df[key], errors="coerce")
            if numeric.notna().any():
                objective = key
                break
        evidence_metric = "Sharpe" if "sharpe" in objective else "Calmar" if "calmar" in objective else "Metric"
        rows: List[Dict[str, Any]] = []
        for record in df.to_dict("records"):
            combo = self._parse_semantic_combo(record.get("semantic_combo"))
            row_objective = str(record.get("objective") or objective)
            row_selection_metric = record.get("selection_metric") or row_objective
            oos_portfolio = self._parse_json_object(record.get("oos_portfolio_json"))
            oos_risk_gate_summary = self._parse_json_object(record.get("oos_risk_gate_summary_json"))
            rows.append(
                {
                    "window_id": int(record.get("window_id", 0) or 0),
                    "semantic_combo": combo,
                    "objective": row_objective,
                    "is_sharpe": self._as_float(record.get("is_sharpe")),
                    "is_calmar": self._as_float(record.get("is_calmar")),
                    "oos_sharpe": self._as_float(record.get("oos_sharpe")),
                    "oos_calmar": self._as_float(record.get("oos_calmar")),
                    "oos_total_return": self._as_float(record.get("oos_total_return")),
                    "train_start_date": self._to_iso(record.get("train_start_date") or record.get("train_start")),
                    "train_end_date": self._to_iso(record.get("train_end_date") or record.get("train_end")),
                    "test_start_date": self._to_iso(record.get("test_start_date") or record.get("test_start")),
                    "test_end_date": self._to_iso(record.get("test_end_date") or record.get("test_end")),
                    "strategy_mode": record.get("strategy_mode"),
                    "execution_plan_hash": record.get("execution_plan_hash"),
                    "linked_backtest": self._record_backtest_ref(record)
                    or self._match_backtest_ref(
                        execution_plan_hash=record.get("execution_plan_hash"),
                        semantic_combo=combo,
                    ),
                    "selection_source": record.get("selection_source"),
                    "selection_rank": self._as_float(record.get("selection_rank")),
                    "selection_metric": row_selection_metric,
                    "selection_evidence": record.get("selection_evidence") or f"rank=1 by IS {evidence_metric}",
                    "candidate_count": self._as_float(record.get("candidate_count")),
                    "total_candidate_count": self._as_float(record.get("total_candidate_count")),
                    "candidate_budget": self._finite_or_none(record.get("candidate_budget")),
                    "candidate_budget_applied": self._optional_bool(record.get("candidate_budget_applied")),
                    "candidate_budget_policy": record.get("candidate_budget_policy"),
                    "candidate_budget_method": record.get("candidate_budget_method"),
                    "candidate_budget_seed": self._finite_or_none(record.get("candidate_budget_seed")),
                    "selection_pool_count": self._as_float(record.get("selection_pool_count")),
                    "selection_pool_total_count": self._as_float(record.get("selection_pool_total_count")),
                    "selection_constraints_applied": self._optional_bool(
                        record.get("selection_constraints_applied")
                    ) or False,
                    "selection_constraints_fallback": self._optional_bool(
                        record.get("selection_constraints_fallback")
                    ) or False,
                    "candidate_viability_pass": (
                        True
                        if self._optional_bool(record.get("candidate_viability_pass")) is None
                        else self._optional_bool(record.get("candidate_viability_pass"))
                    ),
                    "candidate_viability_reasons": record.get("candidate_viability_reasons"),
                    "is_active_rebalance_count": self._as_float(record.get("is_active_rebalance_count")),
                    "is_exposure_ratio": self._as_float(record.get("is_exposure_ratio")),
                    "is_nonzero_return_days": self._as_float(record.get("is_nonzero_return_days")),
                    "candidate_max_lookback": self._as_float(record.get("candidate_max_lookback")),
                    "accepted": self._optional_bool(record.get("accepted")),
                    "review_status": record.get("review_status"),
                    "acceptance_reasons": record.get("acceptance_reasons"),
                    "wfa_row_type": record.get("wfa_row_type", "selected_optimum"),
                    "workflow": record.get("workflow"),
                    "oos_profit_factor": self._as_float(record.get("oos_profit_factor")),
                    "oos_win_rate": self._as_float(record.get("oos_win_rate")),
                    "oos_max_drawdown": self._as_float(record.get("oos_max_drawdown")),
                    "oos_portfolio": oos_portfolio,
                    "oos_rebalance_count": self._finite_or_none(
                        oos_portfolio.get("active_rebalance_count")
                    ),
                    "oos_avg_exposure": self._finite_or_none(oos_portfolio.get("avg_exposure")),
                    "oos_avg_holdings": self._finite_or_none(oos_portfolio.get("avg_holdings")),
                    "oos_total_turnover": self._finite_or_none(oos_portfolio.get("total_turnover")),
                    "oos_cost_drag": self._finite_or_none(oos_portfolio.get("cost_drag")),
                    "is_risk_gate_event_count": self._finite_or_none(
                        record.get("is_risk_gate_event_count")
                    ),
                    "oos_risk_gate_event_count": self._finite_or_none(
                        record.get("oos_risk_gate_event_count")
                        if record.get("oos_risk_gate_event_count") is not None
                        else oos_portfolio.get("risk_gate_event_count")
                    ),
                    "oos_risk_gate_summary": oos_risk_gate_summary
                    or oos_portfolio.get("risk_gate_summary")
                    or {},
                }
            )
        combo_groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            combo_key = json.dumps(row.get("semantic_combo", {}), sort_keys=True)
            combo_groups.setdefault(combo_key, []).append(row)
        acceptance = WFAAcceptanceEvaluator()
        grouped_rows: List[Dict[str, Any]] = []
        clustering_input: List[Dict[str, Any]] = []
        for combo_key, combo_rows in combo_groups.items():
            summary = {
                "combo_key": combo_key,
                "label": self._semantic_combo_label(combo_rows[0].get("semantic_combo", {})),
                "params": combo_rows[0].get("semantic_combo", {}),
                "mean_is_sharpe": self._mean(combo_rows, "is_sharpe"),
                "mean_is_calmar": self._mean(combo_rows, "is_calmar"),
                "mean_oos_sharpe": self._mean(combo_rows, "oos_sharpe"),
                "mean_oos_calmar": self._mean(combo_rows, "oos_calmar"),
                "profit_factor": self._mean(combo_rows, "oos_profit_factor"),
                "win_rate": self._mean(combo_rows, "oos_win_rate"),
                "trade_count": len(combo_rows),
                "oos_std": self._std(combo_rows, "oos_sharpe"),
                "max_drawdown": self._mean(combo_rows, "oos_max_drawdown"),
                "selection_evidence": combo_rows[0].get("selection_evidence"),
                "selected_window_count": len(combo_rows),
            }
            acceptance_result = acceptance.evaluate(summary)
            selected_row_reasons = [
                str(row.get("acceptance_reasons"))
                for row in combo_rows
                if str(row.get("acceptance_reasons") or "").strip()
            ]
            selected_row_review_gate = any(
                self._optional_bool(row.get("accepted")) is False
                or str(row.get("review_status") or "").strip().lower() == "review"
                or self._optional_bool(row.get("selection_constraints_fallback")) is True
                for row in combo_rows
            )
            selected_fallback_count = sum(
                1
                for row in combo_rows
                if self._optional_bool(row.get("selection_constraints_fallback")) is True
            )
            summary["oos_is_ratio"] = acceptance_result.metrics.get("oos_is_ratio")
            summary["robust_score"] = acceptance_result.robust_score
            summary["accepted"] = bool(acceptance_result.accepted and not selected_row_review_gate)
            summary["review_status"] = "Pass" if summary["accepted"] else "Review"
            summary["acceptance_reasons"] = sorted(
                {
                    *acceptance_result.reasons,
                    *selected_row_reasons,
                    *(["selected_window_review_gate"] if selected_row_review_gate else []),
                }
            )
            summary["selected_window_review_count"] = sum(
                1
                for row in combo_rows
                if self._optional_bool(row.get("accepted")) is False
                or str(row.get("review_status") or "").strip().lower() == "review"
            )
            summary["selection_constraints_fallback_count"] = selected_fallback_count
            grouped_rows.append({**summary, "rows": combo_rows})
            clustering_input.append(summary)
        cluster_summary = self.robust_selector.cluster_candidates(
            clustering_input,
            representative_mode="cluster_median",
        )
        linked_backtest_run_ids = sorted(
            {
                str(row.get("linked_backtest", {}).get("run_id"))
                for row in rows
                if isinstance(row.get("linked_backtest"), dict)
                and str(row.get("linked_backtest", {}).get("run_id", "")).strip()
            }
        )
        cluster_lookup: Dict[str, Dict[str, Any]] = {}
        for cluster in cluster_summary.get("clusters", []):
            cluster_rows = cluster.get("rows", []) if isinstance(cluster, dict) else []
            selected_window_count = 0
            for cluster_row in cluster_rows:
                if not isinstance(cluster_row, dict):
                    continue
                combo_key = cluster_row.get("combo_key")
                if combo_key:
                    cluster_lookup[str(combo_key)] = cluster
                selected_window_count += int(cluster_row.get("selected_window_count") or 0)
            if isinstance(cluster, dict):
                cluster["unique_set_count"] = cluster.get("size", len(cluster_rows))
                cluster["selected_window_count"] = selected_window_count
        enriched_grouped_rows: List[Dict[str, Any]] = []
        for group in grouped_rows:
            cluster = cluster_lookup.get(str(group.get("combo_key")))
            enriched_grouped_rows.append(
                {
                    **group,
                    "representative_type": "IS Window Optimum",
                    "source": "Walk-Forward IS optimization",
                    "cluster_id": cluster.get("cluster_id") if cluster else None,
                    "cluster_size": cluster.get("unique_set_count") if cluster else None,
                    "local_plateau_score": None,
                    "candidate_key": group.get("combo_key"),
                    "wfa_pack_inclusion_reason": group.get("selection_evidence"),
                }
            )

        workflow_values = {
            str(row.get("workflow") or "").strip()
            for row in rows
            if str(row.get("workflow") or "").strip()
        }
        batch_workflow = (
            "rolling_validation"
            if workflow_values == {"rolling_validation"}
            else "window_is_optimization"
        )
        windowing_metadata = self._wfa_windowing_metadata(df, rows, wfa_metadata)
        selection_constraints_metadata = self._wfa_selection_constraints_metadata(df, rows, wfa_metadata)
        candidate_budget_metadata = self._wfa_candidate_budget_metadata(df, rows, wfa_metadata)
        batch_metadata = {
            "workflow": batch_workflow,
            "source_workflows": sorted(workflow_values),
            "row_contract": "selected_optimum_per_window",
            "source_run_id": linked_backtest_run_ids[0] if len(linked_backtest_run_ids) == 1 else None,
            "linked_backtest_run_ids": linked_backtest_run_ids,
            "review_mode": "run_center_wfa",
            "pack_strategy": None,
            "candidate_count": len(grouped_rows),
            "source_row_count": source_row_count,
            "selected_row_count": len(rows),
            "diagnostic_row_count": len(diagnostic_rows),
            "diagnostic_artifacts": diagnostic_artifacts,
            "legacy_grid_detected": legacy_grid_detected,
            "windowing": windowing_metadata,
            "selection_constraints": selection_constraints_metadata,
            "candidate_budget": candidate_budget_metadata,
        }
        metric_columns = [
            col
            for col in metric_priority
            if col in df.columns and pd.to_numeric(df[col], errors="coerce").notna().any()
        ]
        if rows:
            timeline_df = df.groupby("window_id", as_index=False)[metric_columns].mean(
                numeric_only=True
            )
        else:
            timeline_df = pd.DataFrame(columns=["window_id", *metric_columns])
        date_fields = [
            ("train_start_date", ["train_start_date", "train_start"]),
            ("train_end_date", ["train_end_date", "train_end"]),
            ("test_start_date", ["test_start_date", "test_start"]),
            ("test_end_date", ["test_end_date", "test_end"]),
        ]
        for output_field, candidates in date_fields:
            field = next((candidate for candidate in candidates if candidate in df.columns), "")
            if field:
                first_values = (
                    df.groupby("window_id", as_index=False)[field].first().rename(
                        columns={field: f"__{output_field}"}
                    )
                )
                timeline_df = timeline_df.merge(first_values, on="window_id", how="left")
        timeline: List[Dict[str, Any]] = []
        for record in timeline_df.to_dict("records"):
            base_record = {
                key: value for key, value in record.items() if not str(key).startswith("__")
            }
            timeline.append(
                {
                    **base_record,
                    "train_start_date": self._to_iso(record.get("__train_start_date")),
                    "train_end_date": self._to_iso(record.get("__train_end_date")),
                    "test_start_date": self._to_iso(record.get("__test_start_date")),
                    "test_end_date": self._to_iso(record.get("__test_end_date")),
                }
            )
        portfolio_window_summary = self._wfa_portfolio_window_summary(rows)
        payload = {
            "schema_version": WFA_DASHBOARD_SCHEMA_VERSION,
            "contract_id": "lo2cin4bt-app-wfa-dashboard-payload-v1",
            "run_id": run_id,
            "objective": objective,
            "strategy_summary": self._strategy_summary(run_id),
            "rows": rows,
            "combo_groups": enriched_grouped_rows,
            "cluster_summary": cluster_summary,
            "timeline": timeline,
            "portfolio_window_summary": portfolio_window_summary,
            "batch_metadata": batch_metadata,
            "diagnostic_rows": diagnostic_rows[:500],
            "truth_source_policy": self._truth_source_policy(),
            "truth_warnings": sorted(
                {
                    str(value)
                    for row in rows
                    if isinstance(row, dict)
                    for value in row.get("warnings", [])
                    if value not in (None, "")
                }
            ),
            "generated_at": self._now_iso(),
            "artifact_source_refs": [str(wfa_path)],
        }
        self._write_json(path, payload)
        return path

    def ensure_statanalyser_summary_payload(
        self,
        run_id: str,
        *,
        force: bool = False,
    ) -> Path:
        path = self._chart_path(run_id, "statanalyser_summary_payload.json")
        if path.exists() and not force:
            return path
        summary_path = self._snapshot_path(run_id, "statanalyser_summary.json")
        if not summary_path.exists():
            raise FileNotFoundError("statanalyser summary snapshot missing")
        payload = {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-statanalyser-summary-payload-v1",
            "run_id": run_id,
            "summary": self._load_json(summary_path, {}),
            "generated_at": self._now_iso(),
            "artifact_source_refs": [str(summary_path)],
        }
        self._write_json(path, payload)
        return path

    def ensure_ai_readable_output(
        self,
        run_id: str,
        *,
        module: Optional[str] = None,
        force: bool = True,
    ) -> Path:
        path = self.registry.build_run_paths(run_id)["ai_readable_output"]
        if path.exists() and not force:
            return path
        payload = self._build_ai_readable_output(run_id, module=module)
        self._write_json(path, payload)
        self._register_ai_readable_output_artifact(run_id, path)
        return path

    def _build_ai_readable_output(
        self,
        run_id: str,
        *,
        module: Optional[str] = None,
    ) -> Dict[str, Any]:
        paths = self.registry.build_run_paths(run_id)
        registry_entry = self.registry.load_registry_entry(run_id)
        stage_status = self.registry.load_stage_status(run_id)
        artifact_manifest = self.registry.load_artifact_manifest(run_id)
        source_payloads, payload_index = self._load_ai_payload_directory(
            paths["chart_payload_dir"]
        )
        snapshot_payloads, snapshot_index = self._load_ai_snapshot_payloads(
            paths["snapshot_dir"]
        )
        artifact_index, artifact_summary, table_profiles, json_profiles = (
            self._build_ai_artifact_profiles(artifact_manifest)
        )
        metric_field_catalog = self._build_ai_metric_field_catalog(
            {
                "source_payloads": source_payloads,
                "snapshot_payloads": snapshot_payloads,
                "artifact_table_profiles": table_profiles,
                "artifact_json_profiles": json_profiles,
            }
        )
        resolved_module = module or str(registry_entry.get("module", "") or "")
        return {
            "schema_version": AI_READABLE_OUTPUT_SCHEMA_VERSION,
            "contract_id": "lo2cin4bt-app-ai-readable-output-v1",
            "run_id": run_id,
            "module": resolved_module,
            "generated_at": self._now_iso(),
            "auto_inclusion_policy": {
                "chart_payloads": "Every JSON file under this run's chart_payloads directory is embedded under source_payloads.",
                "snapshots": "Every direct JSON snapshot for this run is embedded under snapshot_payloads.",
                "artifacts": "The full artifact manifest is included, and ready table artifacts are schema-profiled by artifact type.",
                "future_metrics": (
                    "New performance scores are automatically present when they appear "
                    "as JSON payload fields or artifact table columns."
                ),
            },
            "review_guidance": {
                "primary_inputs": [
                    "run_registry",
                    "stage_status",
                    "artifact_manifest",
                    "source_payloads",
                    "artifact_table_profiles",
                    "metric_field_catalog",
                ],
                "do_not_infer": (
                    "Do not treat absent metrics as zero. Report them as missing or not generated."
                ),
                "recommended_review_order": [
                    "data health and run status",
                    "strategy summary and execution plan",
                    "headline performance and benchmark comparison",
                    "parameter or WFA robustness evidence",
                    "risk diagnostics and trade/allocation diagnostics",
                    "missing artifacts, warnings, and unsupported views",
                ],
            },
            "source_paths": {
                "run_registry": str(paths["run_registry"]),
                "artifact_manifest": str(paths["artifact_manifest"]),
                "stage_status": str(paths["stage_status"]),
                "run_snapshots": str(paths["snapshot_dir"]),
                "chart_payloads": str(paths["chart_payload_dir"]),
                "ai_readable_output": str(paths["ai_readable_output"]),
            },
            "run_registry": registry_entry,
            "stage_status": stage_status,
            "artifact_manifest": artifact_manifest,
            "artifact_summary": artifact_summary,
            "artifact_index": artifact_index,
            "payload_index": payload_index,
            "snapshot_index": snapshot_index,
            "source_payloads": source_payloads,
            "snapshot_payloads": snapshot_payloads,
            "artifact_table_profiles": table_profiles,
            "artifact_json_profiles": json_profiles,
            "metric_field_catalog": metric_field_catalog,
        }

    def _load_ai_payload_directory(self, directory: Path) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        payloads: Dict[str, Any] = {}
        index: List[Dict[str, Any]] = []
        if not directory.exists():
            return payloads, index
        for path in sorted(directory.glob("*.json")):
            loaded = self._load_json(path, {})
            key = path.stem
            payloads[key] = loaded
            index.append(self._json_payload_index_row(path, loaded))
        return payloads, index

    def _load_ai_snapshot_payloads(self, snapshot_dir: Path) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        payloads: Dict[str, Any] = {}
        index: List[Dict[str, Any]] = []
        if not snapshot_dir.exists():
            return payloads, index
        for path in sorted(snapshot_dir.glob("*.json")):
            loaded = self._load_json(path, {})
            key = path.stem
            payloads[key] = loaded
            index.append(self._json_payload_index_row(path, loaded))
        return payloads, index

    def _json_payload_index_row(self, path: Path, loaded: Any) -> Dict[str, Any]:
        top_level_keys = sorted(loaded.keys()) if isinstance(loaded, dict) else []
        return {
            "name": path.name,
            "path": str(path),
            "size_bytes": self._file_size(path),
            "schema_version": loaded.get("schema_version") if isinstance(loaded, dict) else None,
            "contract_id": loaded.get("contract_id") if isinstance(loaded, dict) else None,
            "top_level_keys": top_level_keys,
        }

    def _build_ai_artifact_profiles(
        self,
        artifact_manifest: Dict[str, Any],
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
        artifacts = artifact_manifest.get("artifacts", []) if isinstance(artifact_manifest, dict) else []
        if not isinstance(artifacts, list):
            artifacts = []
        artifact_index: List[Dict[str, Any]] = []
        summary_by_type: Dict[str, Dict[str, Any]] = {}
        profile_counts: Dict[tuple[str, str], int] = {}
        table_profiles: List[Dict[str, Any]] = []
        json_profiles: List[Dict[str, Any]] = []

        for item in artifacts:
            if not isinstance(item, dict):
                continue
            artifact_type = str(item.get("artifact_type", "") or "unknown")
            status = str(item.get("status", "") or "")
            path_text = str(item.get("path", "") or "")
            path = self._safe_manifest_artifact_path(path_text)
            exists = bool(path is not None)
            suffix = path.suffix.lower() if path is not None else (Path(path_text).suffix.lower() if path_text else "")
            size_bytes = self._file_size(path) if path is not None else None
            artifact_index.append(
                {
                    "artifact_type": artifact_type,
                    "status": status,
                    "path": path_text,
                    "exists": exists,
                    "extension": suffix,
                    "size_bytes": size_bytes,
                    "content_contract": item.get("content_contract"),
                    "source_stage": item.get("source_stage"),
                }
            )
            bucket = summary_by_type.setdefault(
                artifact_type,
                {"count": 0, "ready": 0, "missing": 0, "failed": 0, "extensions": {}},
            )
            bucket["count"] += 1
            if status == "ready":
                bucket["ready"] += 1
            elif status == "missing":
                bucket["missing"] += 1
            elif status == "failed":
                bucket["failed"] += 1
            extensions = bucket["extensions"]
            extensions[suffix or "none"] = int(extensions.get(suffix or "none", 0)) + 1

            if not exists or status != "ready" or artifact_type == "ai_readable_output_json":
                continue
            profile_key = (artifact_type, suffix)
            sampled = profile_counts.get(profile_key, 0)
            if sampled >= AI_REVIEW_ARTIFACT_PROFILE_LIMIT_PER_TYPE:
                continue
            if suffix in {".parquet", ".csv"}:
                table_profiles.append(self._profile_table_artifact(path, item))
                profile_counts[profile_key] = sampled + 1
            elif suffix == ".json":
                json_profiles.append(self._profile_json_artifact(path, item))
                profile_counts[profile_key] = sampled + 1

        return (
            artifact_index,
            {
                "total": len(artifact_index),
                "by_type": summary_by_type,
                "profile_limit_per_type": AI_REVIEW_ARTIFACT_PROFILE_LIMIT_PER_TYPE,
            },
            table_profiles,
            json_profiles,
        )

    def _profile_table_artifact(self, path: Path, manifest_item: Dict[str, Any]) -> Dict[str, Any]:
        profile: Dict[str, Any] = {
            "artifact_type": manifest_item.get("artifact_type"),
            "path": str(path),
            "extension": path.suffix.lower(),
            "status": "profiled",
        }
        try:
            if path.suffix.lower() == ".parquet":
                frame = pd.read_parquet(path)
            else:
                frame = pd.read_csv(path)
            profile.update(
                {
                    "row_count": int(len(frame)),
                    "column_count": int(len(frame.columns)),
                    "columns": [str(column) for column in frame.columns],
                    "dtypes": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
                }
            )
            numeric_summary: Dict[str, Any] = {}
            for column in frame.columns:
                series = pd.to_numeric(frame[column], errors="coerce")
                if series.notna().sum() == 0:
                    continue
                finite = series.dropna()
                numeric_summary[str(column)] = {
                    "count": int(finite.count()),
                    "min": self._finite_or_none(finite.min()),
                    "max": self._finite_or_none(finite.max()),
                    "mean": self._finite_or_none(finite.mean()),
                    "last": self._finite_or_none(finite.iloc[-1]) if not finite.empty else None,
                }
            profile["numeric_summary"] = numeric_summary
        except Exception as exc:
            profile.update({"status": "profile_failed", "error": str(exc)})
        return profile

    def _profile_json_artifact(self, path: Path, manifest_item: Dict[str, Any]) -> Dict[str, Any]:
        loaded = self._load_json(path, {})
        numeric_fields = self._build_ai_metric_field_catalog(loaded)
        return {
            "artifact_type": manifest_item.get("artifact_type"),
            "path": str(path),
            "extension": path.suffix.lower(),
            "status": "profiled" if loaded else "empty_or_unreadable",
            "top_level_keys": sorted(loaded.keys()) if isinstance(loaded, dict) else [],
            "numeric_field_catalog": numeric_fields,
        }

    def _build_ai_metric_field_catalog(self, payload: Any) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}
        self._collect_ai_numeric_fields(payload, "", fields)
        rows = [
            {"path": path, "sample_value": value}
            for path, value in list(fields.items())[:AI_REVIEW_NUMERIC_FIELD_LIMIT]
        ]
        return {
            "numeric_field_count": len(fields),
            "included_count": len(rows),
            "truncated": len(fields) > AI_REVIEW_NUMERIC_FIELD_LIMIT,
            "fields": rows,
        }

    def _collect_ai_numeric_fields(
        self,
        value: Any,
        prefix: str,
        fields: Dict[str, Any],
    ) -> None:
        if len(fields) >= AI_REVIEW_NUMERIC_FIELD_LIMIT:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                child_key = str(key)
                child_prefix = f"{prefix}.{child_key}" if prefix else child_key
                self._collect_ai_numeric_fields(child, child_prefix, fields)
                if len(fields) >= AI_REVIEW_NUMERIC_FIELD_LIMIT:
                    return
        elif isinstance(value, list):
            for child in value[:AI_REVIEW_LIST_SAMPLE_LIMIT]:
                child_prefix = f"{prefix}[]" if prefix else "[]"
                self._collect_ai_numeric_fields(child, child_prefix, fields)
                if len(fields) >= AI_REVIEW_NUMERIC_FIELD_LIMIT:
                    return
        elif isinstance(value, bool):
            return
        elif isinstance(value, (int, float)):
            if math.isfinite(float(value)) and prefix and prefix not in fields:
                fields[prefix] = value

    def _register_ai_readable_output_artifact(self, run_id: str, path: Path) -> None:
        manifest = self.registry.load_artifact_manifest(run_id)
        if not isinstance(manifest, dict):
            manifest = {"schema_version": "1.0", "artifacts": []}
        artifacts = manifest.get("artifacts", [])
        if not isinstance(artifacts, list):
            artifacts = []
        artifacts = [
            item
            for item in artifacts
            if not (
                isinstance(item, dict)
                and item.get("artifact_type") == "ai_readable_output_json"
            )
        ]
        artifacts.append(
            {
                "artifact_type": "ai_readable_output_json",
                "path": str(path),
                "required_by_pages": ["results_library"],
                "status": "ready",
                "generated_at": self._now_iso(),
                "content_contract": "lo2cin4bt-app-ai-readable-output-v1",
                "source_stage": "app_export",
                "optional": True,
                "notes": "AI-readable aggregate pack assembled from app payloads, snapshots, and artifact profiles.",
            }
        )
        manifest["artifacts"] = artifacts
        self.registry.write_artifact_manifest(run_id, manifest)

        registry_entry = self.registry.load_registry_entry(run_id)
        if isinstance(registry_entry, dict) and registry_entry:
            registry_entry["artifacts_total"] = len(artifacts)
            registry_entry["artifacts_ready"] = sum(
                1
                for item in artifacts
                if isinstance(item, dict) and item.get("status") == "ready"
            )
            self.registry.write_registry_entry(registry_entry)

    @staticmethod
    def _file_size(path: Path) -> Optional[int]:
        try:
            return int(path.stat().st_size)
        except Exception:
            return None

    def _build_metrics_row(
        self,
        metric_row: Dict[str, Any],
        combo: Dict[str, Any],
        backtest_id: str,
        label: str,
        subset: pd.DataFrame,
        last_trade_time: Optional[str],
    ) -> Dict[str, Any]:
        total_return = self._metric_float(metric_row, "total_return")
        bah_total_return = self._metric_float(metric_row, "bah_total_return")
        start_time = self._to_iso(subset.iloc[0]["Time"]) if not subset.empty else None
        end_time = self._to_iso(subset.iloc[-1]["Time"]) if not subset.empty else None
        return {
            "backtest_id": backtest_id,
            "label": label,
            "label_source": self._label_source(combo),
            "strategy_id": combo.get("strategy_id"),
            "semantic_combo": combo.get("semantic_combo", {}),
            "semantic_fields": combo.get("semantic_fields", []),
            "total_return": total_return,
            "cagr": self._metric_float(metric_row, "cagr"),
            "sharpe": self._metric_float(metric_row, "sharpe"),
            "sortino": self._metric_float(metric_row, "sortino"),
            "calmar": self._metric_float(metric_row, "calmar"),
            "max_drawdown": self._metric_float(metric_row, "max_drawdown"),
            "average_drawdown": self._metric_float(metric_row, "average_drawdown"),
            "recovery_factor": self._metric_float(metric_row, "recovery_factor"),
            "std": self._metric_float(metric_row, "std"),
            "annualized_std": self._metric_float(metric_row, "annualized_std"),
            "downside_risk": self._metric_float(metric_row, "downside_risk"),
            "annualized_downside_risk": self._metric_float(
                metric_row, "annualized_downside_risk"
            ),
            "information_ratio": self._metric_float(metric_row, "information_ratio"),
            "alpha": self._metric_float(metric_row, "alpha"),
            "beta": self._metric_float(metric_row, "beta"),
            "trade_count": int(metric_row.get(METRIC_KEY_MAP["trade_count"], 0) or 0),
            "win_rate": self._metric_float(metric_row, "win_rate"),
            "profit_factor": self._metric_float(metric_row, "profit_factor"),
            "avg_trade_return": self._metric_float(metric_row, "avg_trade_return"),
            "exposure_time": self._metric_float(metric_row, "exposure_time"),
            "max_consecutive_losses": self._metric_float(
                metric_row, "max_consecutive_losses"
            ),
            "max_holding_period_ratio": self._metric_float(
                metric_row, "max_holding_period_ratio"
            ),
            "bah_total_return": bah_total_return,
            "bah_cagr": self._metric_float(metric_row, "bah_cagr"),
            "bah_sharpe": self._metric_float(metric_row, "bah_sharpe"),
            "bah_calmar": self._metric_float(metric_row, "bah_calmar"),
            "bah_max_drawdown": self._metric_float(metric_row, "bah_max_drawdown"),
            "excess_return": total_return - bah_total_return
            if not math.isnan(total_return) and not math.isnan(bah_total_return)
            else float("nan"),
            "date_range_start": self._to_iso(metric_row.get("Date_start"))
            if metric_row.get("Date_start") is not None
            else start_time,
            "date_range_end": self._to_iso(metric_row.get("Date_end"))
            if metric_row.get("Date_end") is not None
            else end_time,
            "last_trade_time": last_trade_time,
        }

    def _build_backtest_metrics_matrix(self, metrics_row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "total_return": self._metric_float(metrics_row, "total_return"),
            "cagr": self._metric_float(metrics_row, "cagr"),
            "sharpe": self._metric_float(metrics_row, "sharpe"),
            "sortino": self._metric_float(metrics_row, "sortino"),
            "calmar": self._metric_float(metrics_row, "calmar"),
            "mdd": self._metric_float(metrics_row, "max_drawdown"),
            "average_drawdown": self._metric_float(metrics_row, "average_drawdown"),
            "recovery_factor": self._metric_float(metrics_row, "recovery_factor"),
            "std": self._metric_float(metrics_row, "std"),
            "annualized_std": self._metric_float(metrics_row, "annualized_std"),
            "information_ratio": self._metric_float(metrics_row, "information_ratio"),
            "exposure_time": self._metric_float(metrics_row, "exposure_time"),
            "avg_trade_return": self._metric_float(metrics_row, "avg_trade_return"),
            "trade_count": int(metrics_row.get(METRIC_KEY_MAP["trade_count"], 0) or 0),
            "win_rate": self._metric_float(metrics_row, "win_rate"),
            "profit_factor": self._metric_float(metrics_row, "profit_factor"),
            "bah_total_return": self._metric_float(metrics_row, "bah_total_return"),
            "bah_cagr": self._metric_float(metrics_row, "bah_cagr"),
            "bah_sharpe": self._metric_float(metrics_row, "bah_sharpe"),
            "bah_calmar": self._metric_float(metrics_row, "bah_calmar"),
            "excess_return": self._metric_float(metrics_row, "total_return")
            - self._metric_float(metrics_row, "bah_total_return"),
        }

    def _build_trade_summary_rows(self, detail_df: pd.DataFrame) -> List[Dict[str, Any]]:
        if detail_df.empty or "Trade_group_id" not in detail_df.columns:
            return []

        trade_df = detail_df[detail_df["Trade_group_id"].notna()].copy()
        if trade_df.empty:
            return []

        trade_rows: List[Dict[str, Any]] = []
        for rank, (trade_group_id, group) in enumerate(
            trade_df.groupby("Trade_group_id", sort=False),
            start=1,
        ):
            group = group.sort_values("Time")
            entry_rows = group[group["Trade_action"].fillna(0).astype(int) == 1]
            exit_rows = group[group["Trade_action"].fillna(0).astype(int) == 4]
            entry_row = entry_rows.iloc[0] if not entry_rows.empty else group.iloc[0]
            exit_row = exit_rows.iloc[-1] if not exit_rows.empty else group.iloc[-1]
            entry_equity = self._as_float(entry_row.get("Equity_value"))
            exit_equity = self._as_float(exit_row.get("Equity_value"))
            entry_return = self._as_float(entry_row.get("Return"))
            pre_entry_equity = self._pre_trade_equity(entry_equity, entry_return)
            entry_price = self._as_float(
                entry_row.get("Open_position_price") or entry_row.get("Close")
            )
            exit_price = self._as_float(
                exit_row.get("Close_position_price") or exit_row.get("Close")
            )
            position_size = self._as_float(entry_row.get("Position_size"))
            price_pnl = self._price_pnl_per_unit(entry_price, exit_price, position_size)
            equity_pnl = (
                exit_equity - pre_entry_equity
                if not math.isnan(exit_equity) and not math.isnan(pre_entry_equity)
                else float("nan")
            )
            trade_rows.append(
                {
                    "rank": rank,
                    "trade_group_id": str(trade_group_id),
                    "asset": entry_row.get("Trading_instrument"),
                    "side": entry_row.get("Position_type")
                    or self._position_side(entry_row.get("Position_size")),
                    "entry_time": self._to_iso(entry_row.get("Open_time") or entry_row.get("Time")),
                    "exit_time": self._to_iso(exit_row.get("Close_time") or exit_row.get("Time")),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "position_size": position_size,
                    "holding_period": self._as_float(
                        exit_row.get("Holding_period") or exit_row.get("Holding_period_count")
                    ),
                    "trade_return": self._as_float(exit_row.get("Trade_return")),
                    "price_pnl": price_pnl,
                    "entry_equity_value": entry_equity,
                    "pre_entry_equity_value": pre_entry_equity,
                    "equity_value": exit_equity,
                    "equity_pnl": equity_pnl,
                    "pnl": equity_pnl,
                    "status": "closed" if not exit_rows.empty else "open",
                }
            )
        return trade_rows

    def _build_trade_outcome_summary(self, trade_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        has_trade_return_field = any(
            row.get("trade_return") is not None and self._finite_or_none(row.get("trade_return")) is not None
            for row in trade_rows
        )
        closed_returns: List[float] = []
        for row in trade_rows:
            value = self._finite_or_none(row.get("trade_return"))
            if value is None:
                continue
            status = str(row.get("status") or "").strip().lower()
            has_exit = bool(str(row.get("exit_time") or "").strip())
            is_closed = "closed" in status or status == "" or (has_exit and "open" not in status)
            if is_closed:
                closed_returns.append(value)

        wins = [value for value in closed_returns if value > 1e-12]
        losses = [value for value in closed_returns if value < -1e-12]
        breakeven = [
            value
            for value in closed_returns
            if -1e-12 <= value <= 1e-12
        ]
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        closed_count = len(closed_returns)
        display_state = (
            "ready"
            if closed_count >= 5
            else "insufficient_data"
            if closed_count > 0
            else "hidden"
        )
        reason = ""
        if not has_trade_return_field:
            reason = "no_trade_return_field"
        elif closed_count == 0:
            reason = "no_closed_trades"
        elif closed_count < 5:
            reason = "insufficient_closed_trades"
        return {
            "available": bool(has_trade_return_field and closed_count > 0),
            "display_state": display_state,
            "reason": reason,
            "closed_trade_count": closed_count,
            "chart_ready": closed_count >= 5,
            "insufficient_data": 0 < closed_count < 5,
            "win_count": len(wins),
            "loss_count": len(losses),
            "breakeven_count": len(breakeven),
            "win_rate": self._finite_or_none(len(wins) / closed_count) if closed_count else None,
            "average_win": self._finite_or_none(sum(wins) / len(wins)) if wins else None,
            "average_loss": self._finite_or_none(sum(losses) / len(losses)) if losses else None,
            "gross_profit": self._finite_or_none(gross_profit),
            "gross_loss": self._finite_or_none(gross_loss),
            "profit_factor": self._finite_or_none(gross_profit / abs(gross_loss))
            if gross_loss < 0.0
            else None,
            "histogram_bins": self._trade_return_histogram_bins(closed_returns),
        }

    def _trade_return_histogram_bins(self, values: List[float]) -> List[Dict[str, Any]]:
        if not values:
            return []
        finite_values = [value for value in values if math.isfinite(value)]
        if not finite_values:
            return []
        minimum = min(finite_values)
        maximum = max(finite_values)
        bin_count = min(40, max(5, int(math.ceil(math.sqrt(len(finite_values))))))
        if math.isclose(minimum, maximum, rel_tol=0.0, abs_tol=1e-12):
            padding = max(abs(minimum) * 0.05, 0.001)
            minimum -= padding
            maximum += padding
        width = (maximum - minimum) / bin_count
        bins = [
            {
                "lower": self._finite_or_none(minimum + index * width),
                "upper": self._finite_or_none(minimum + (index + 1) * width),
                "count": 0,
            }
            for index in range(bin_count)
        ]
        for value in finite_values:
            index = int((value - minimum) / width) if width > 0.0 else 0
            index = max(0, min(bin_count - 1, index))
            bins[index]["count"] += 1
        return bins

    def _build_risk_diagnostics(
        self,
        *,
        trade_rows: List[Dict[str, Any]],
        equity_series: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        closed_returns = self._closed_trade_returns(trade_rows)
        equity_returns = self._equity_period_returns(equity_series)
        serial_returns = closed_returns if closed_returns else equity_returns
        serial_source = "closed_trades" if closed_returns else "equity_periods"
        concentration_returns = (
            closed_returns
            if any(value > 0.0 for value in closed_returns)
            else equity_returns
        )
        concentration_source = (
            "closed_trades"
            if concentration_returns is closed_returns
            else "equity_periods"
        )
        serial = self._serial_correlation_diagnostic(serial_returns)
        serial["return_source"] = serial_source
        concentration = self._profit_concentration_diagnostic(concentration_returns)
        concentration["return_source"] = concentration_source
        recovery = self._recovery_time_diagnostic(equity_series)
        return {
            "serial_correlation": serial,
            "profit_concentration": concentration,
            "recovery_time": recovery,
            "available": {
                "serial_correlation": bool(serial.get("available")),
                "profit_concentration": bool(concentration.get("available")),
                "recovery_time": bool(recovery.get("episodes")),
            },
        }

    def _closed_trade_returns(self, trade_rows: List[Dict[str, Any]]) -> List[float]:
        returns: List[float] = []
        for row in trade_rows:
            value = self._finite_or_none(row.get("trade_return"))
            if value is None:
                continue
            status = str(row.get("status") or "").strip().lower()
            has_exit = bool(str(row.get("exit_time") or "").strip())
            is_closed = "closed" in status or status == "" or (has_exit and "open" not in status)
            if is_closed:
                returns.append(value)
        return returns

    def _equity_period_returns(self, equity_series: List[Dict[str, Any]]) -> List[float]:
        returns: List[float] = []
        previous: Optional[float] = None
        for item in equity_series:
            value = self._finite_or_none(item.get("value"))
            if value is None:
                continue
            if previous is not None and previous != 0.0:
                period_return = (value / previous) - 1.0
                if math.isfinite(period_return):
                    returns.append(period_return)
            previous = value
        return returns

    def _serial_correlation_diagnostic(
        self,
        returns: List[float],
        *,
        max_lag: int = 10,
        min_observations: int = 6,
        min_pair_count: int = 5,
    ) -> Dict[str, Any]:
        finite = [float(value) for value in returns if math.isfinite(value)]
        observation_count = len(finite)
        base_payload = {
            "observation_count": observation_count,
            "lags": [],
            "significance_band": None,
            "lag1": None,
            "min_observations": min_observations,
            "min_pair_count": min_pair_count,
        }
        if observation_count < min_observations:
            return {
                **base_payload,
                "available": False,
                "reason": "insufficient_observations_for_acf",
            }
        lag_limit = min(max_lag, observation_count - min_pair_count)
        if lag_limit < 1:
            return {
                **base_payload,
                "available": False,
                "reason": "insufficient_pairs_for_acf",
            }
        mean_value = sum(finite) / observation_count
        centered = [value - mean_value for value in finite]
        denominator = sum(value * value for value in centered)
        if denominator <= 1e-18:
            return {
                **base_payload,
                "available": False,
                "reason": "constant_returns",
            }
        rows: List[Dict[str, Any]] = []
        for lag in range(1, lag_limit + 1):
            pair_count = observation_count - lag
            numerator = sum(
                centered[index] * centered[index - lag]
                for index in range(lag, observation_count)
            )
            rows.append(
                {
                    "lag": lag,
                    "acf": self._finite_or_none(numerator / denominator),
                    "pair_count": pair_count,
                }
            )
        band = 1.96 / math.sqrt(observation_count)
        return {
            **base_payload,
            "available": bool(rows),
            "lags": rows,
            "significance_band": self._finite_or_none(band),
            "lag1": rows[0]["acf"] if rows else None,
            "reason": "" if rows else "insufficient_pairs_for_acf",
        }

    def _profit_concentration_diagnostic(self, returns: List[float]) -> Dict[str, Any]:
        profits = sorted(float(value) for value in returns if math.isfinite(value) and value > 0.0)
        profit_count = len(profits)
        total_profit = sum(profits)
        if profit_count == 0 or total_profit <= 0.0:
            return {
                "available": False,
                "reason": "no_profitable_trades",
                "profitable_trade_count": profit_count,
                "total_profit": self._finite_or_none(total_profit),
                "top_20_contribution": None,
                "gini": None,
                "lorenz_curve": [],
            }
        top_count = max(1, int(math.ceil(profit_count * 0.2)))
        top_profit = sum(sorted(profits, reverse=True)[:top_count])
        lorenz = [{"trade_share": 0.0, "profit_share": 0.0}]
        cumulative = 0.0
        for index, value in enumerate(profits, start=1):
            cumulative += value
            lorenz.append(
                {
                    "trade_share": self._finite_or_none(index / profit_count),
                    "profit_share": self._finite_or_none(cumulative / total_profit),
                }
            )
        area = 0.0
        for left, right in zip(lorenz, lorenz[1:]):
            width = float(right["trade_share"] or 0.0) - float(left["trade_share"] or 0.0)
            height = (float(left["profit_share"] or 0.0) + float(right["profit_share"] or 0.0)) / 2.0
            area += width * height
        return {
            "available": True,
            "reason": "",
            "profitable_trade_count": profit_count,
            "total_profit": self._finite_or_none(total_profit),
            "top_20_count": top_count,
            "top_20_contribution": self._finite_or_none(top_profit / total_profit),
            "gini": self._finite_or_none(max(0.0, min(1.0, 1.0 - (2.0 * area)))),
            "lorenz_curve": lorenz,
        }

    def _recovery_time_diagnostic(self, equity_series: List[Dict[str, Any]]) -> Dict[str, Any]:
        points: List[tuple[Optional[pd.Timestamp], float]] = []
        for item in equity_series:
            value = self._finite_or_none(item.get("value"))
            if value is None:
                continue
            raw_time = item.get("time")
            parsed_time = pd.to_datetime(raw_time, errors="coerce") if raw_time is not None else pd.NaT
            points.append((parsed_time if pd.notna(parsed_time) else None, value))
        if len(points) < 2:
            return {
                "available": False,
                "reason": "insufficient_equity_points",
                "episodes": [],
                "percentiles": {},
                "histogram_bins": [],
                "recovered_count": 0,
                "unrecovered_count": 0,
            }

        peak_value = points[0][1]
        peak_index = 0
        peak_time = points[0][0]
        active: Optional[Dict[str, Any]] = None
        episodes: List[Dict[str, Any]] = []

        for index, (time_value, equity_value) in enumerate(points[1:], start=1):
            if equity_value >= peak_value:
                if active is not None:
                    active["recovery_index"] = index
                    active["recovery_time"] = self._to_iso(time_value)
                    active["recovered"] = True
                    active["duration_periods"] = index - int(active["peak_index"])
                    active["duration_days"] = self._duration_days(active.get("peak_time_raw"), time_value)
                    episodes.append(self._public_recovery_episode(active))
                    active = None
                peak_value = equity_value
                peak_index = index
                peak_time = time_value
                continue
            drawdown = (equity_value / peak_value) - 1.0 if peak_value != 0.0 else 0.0
            if active is None:
                active = {
                    "peak_index": peak_index,
                    "peak_time": self._to_iso(peak_time),
                    "peak_time_raw": peak_time,
                    "trough_index": index,
                    "trough_time": self._to_iso(time_value),
                    "trough_depth": self._finite_or_none(drawdown),
                    "recovered": False,
                }
            elif drawdown < float(active.get("trough_depth") or 0.0):
                active["trough_index"] = index
                active["trough_time"] = self._to_iso(time_value)
                active["trough_depth"] = self._finite_or_none(drawdown)

        if active is not None:
            last_index = len(points) - 1
            last_time = points[-1][0]
            active["recovery_index"] = None
            active["recovery_time"] = None
            active["recovered"] = False
            active["duration_periods"] = last_index - int(active["peak_index"])
            active["duration_days"] = self._duration_days(active.get("peak_time_raw"), last_time)
            episodes.append(self._public_recovery_episode(active))

        recovered = [item for item in episodes if item.get("recovered")]
        durations = [
            int(item["duration_periods"])
            for item in recovered
            if item.get("duration_periods") is not None
        ]
        return {
            "available": bool(episodes),
            "reason": "" if episodes else "no_drawdown_episodes",
            "episodes": episodes,
            "recovered_count": len(recovered),
            "unrecovered_count": len(episodes) - len(recovered),
            "percentiles": self._recovery_percentiles(durations),
            "histogram_bins": self._integer_histogram_bins(durations),
        }

    def _public_recovery_episode(self, episode: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "peak_time": episode.get("peak_time"),
            "trough_time": episode.get("trough_time"),
            "recovery_time": episode.get("recovery_time"),
            "recovered": bool(episode.get("recovered")),
            "duration_periods": episode.get("duration_periods"),
            "duration_days": episode.get("duration_days"),
            "trough_depth": episode.get("trough_depth"),
        }

    def _duration_days(self, start: Any, end: Any) -> Optional[int]:
        if start is None or end is None or pd.isna(start) or pd.isna(end):
            return None
        return int(max(0, (end - start).days))

    def _recovery_percentiles(self, durations: List[int]) -> Dict[str, Any]:
        if not durations:
            return {}
        series = pd.Series(durations, dtype="float64")
        return {
            "p50_periods": self._finite_or_none(series.quantile(0.50)),
            "p75_periods": self._finite_or_none(series.quantile(0.75)),
            "p90_periods": self._finite_or_none(series.quantile(0.90)),
            "max_periods": int(max(durations)),
        }

    def _integer_histogram_bins(self, values: List[int]) -> List[Dict[str, Any]]:
        if not values:
            return []
        minimum = min(values)
        maximum = max(values)
        bin_count = min(20, max(1, int(math.ceil(math.sqrt(len(values))))))
        if minimum == maximum:
            return [{"lower": minimum, "upper": maximum, "count": len(values)}]
        width = max(1, int(math.ceil((maximum - minimum + 1) / bin_count)))
        bins: List[Dict[str, Any]] = []
        lower = minimum
        while lower <= maximum:
            upper = min(maximum, lower + width - 1)
            bins.append({"lower": lower, "upper": upper, "count": 0})
            lower = upper + 1
        for value in values:
            index = min(len(bins) - 1, max(0, (value - minimum) // width))
            bins[int(index)]["count"] += 1
        return bins

    @staticmethod
    def _pre_trade_equity(entry_equity: float, entry_return: float) -> float:
        if math.isnan(entry_equity):
            return float("nan")
        if math.isnan(entry_return) or entry_return <= -1.0:
            return entry_equity
        return entry_equity / (1.0 + entry_return)

    @staticmethod
    def _price_pnl_per_unit(entry_price: float, exit_price: float, position_size: float) -> float:
        if math.isnan(entry_price) or math.isnan(exit_price):
            return float("nan")
        direction = -1.0 if position_size < 0 else 1.0
        units = abs(position_size) if not math.isnan(position_size) and position_size != 0 else 1.0
        return (exit_price - entry_price) * direction * units

    def _build_pcp_dimensions(
        self,
        rows: List[Dict[str, Any]],
        param_axes: List[str],
        metric_axes: List[str],
    ) -> List[Dict[str, Any]]:
        dimensions: List[Dict[str, Any]] = []
        for axis in [*param_axes, *metric_axes]:
            values = [row.get(axis) for row in rows]
            numeric_values = [self._as_float(value) for value in values if value is not None]
            numeric_values = [value for value in numeric_values if not math.isnan(value)]
            dimension: Dict[str, Any] = {
                "key": axis,
                "label": axis,
                "values": values,
                "kind": "metric" if axis in metric_axes else "parameter",
            }
            if numeric_values:
                dimension["range"] = [min(numeric_values), max(numeric_values)]
            dimensions.append(dimension)
        return dimensions

    @staticmethod
    def _mean(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
        numeric = [
            AppPayloadService._as_float(row.get(key))
            for row in rows
            if not math.isnan(AppPayloadService._as_float(row.get(key)))
        ]
        if not numeric:
            return None
        return float(sum(numeric) / len(numeric))

    @staticmethod
    def _std(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
        numeric = [
            AppPayloadService._as_float(row.get(key))
            for row in rows
            if not math.isnan(AppPayloadService._as_float(row.get(key)))
        ]
        if not numeric:
            return None
        mean = sum(numeric) / len(numeric)
        variance = sum((value - mean) ** 2 for value in numeric) / len(numeric)
        return float(variance ** 0.5)

    @staticmethod
    def _semantic_combo_label(combo: Dict[str, Any]) -> str:
        if not isinstance(combo, dict) or not combo:
            return "Combo not recorded"
        return " | ".join(f"{key}={combo[key]}" for key in sorted(combo.keys()))

    @classmethod
    def _portfolio_strategy_table_label(
        cls,
        *,
        run_id: str,
        metadata: Dict[str, Any],
        config: Dict[str, Any],
        strategy_id: str,
        params: Dict[str, Any],
    ) -> str:
        asset_label = cls._portfolio_asset_label(metadata, config, strategy_id)
        display_rules = cls._strategy_rule_display_overrides(metadata, config)
        mode_label = (
            display_rules.get("mode_label")
            or cls._portfolio_strategy_family_label(strategy_id, asset_label, params, config)
        )
        param_parts = cls._semantic_combo_parts(params)
        parts = [
            part.strip()
            for part in [asset_label, mode_label, *param_parts]
            if isinstance(part, str) and part.strip()
        ]
        if not parts:
            return f"Strategy {str(run_id or strategy_id)[:8]}"
        deduped: List[str] = []
        seen: set[str] = set()
        for part in parts:
            key = part.lower()
            if key in seen:
                continue
            deduped.append(part)
            seen.add(key)
        return " | ".join(deduped)

    @classmethod
    def _portfolio_asset_label(
        cls,
        metadata: Dict[str, Any],
        config: Dict[str, Any],
        strategy_id: str,
    ) -> str:
        universe = config.get("universe", {}) if isinstance(config.get("universe"), dict) else {}
        symbols = universe.get("symbols", []) if isinstance(universe.get("symbols"), list) else []
        clean_symbols = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
        if clean_symbols:
            return "-".join(clean_symbols)
        summary = metadata.get("summary", {}) if isinstance(metadata.get("summary"), dict) else {}
        summary_symbols = summary.get("symbols", []) if isinstance(summary.get("symbols"), list) else []
        clean_summary_symbols = [
            str(symbol).strip().upper() for symbol in summary_symbols if str(symbol).strip()
        ]
        if clean_summary_symbols:
            return "-".join(clean_summary_symbols)
        known_symbols = {
            "agg",
            "btc",
            "dia",
            "dbc",
            "eem",
            "efa",
            "eth",
            "gld",
            "iau",
            "ief",
            "iwm",
            "qqq",
            "qqqm",
            "shy",
            "slv",
            "smh",
            "soxx",
            "spy",
            "sso",
            "tlt",
            "tmf",
            "tqqq",
            "ung",
            "upro",
            "usd",
            "usdt",
            "uso",
            "voo",
        }
        leading_symbols: List[str] = []
        for token in re.split(r"[^A-Za-z0-9]+", str(strategy_id or "")):
            clean = token.strip().lower()
            if clean in known_symbols:
                leading_symbols.append(clean.upper())
                continue
            break
        if leading_symbols:
            return "-".join(leading_symbols)
        first_token = str(strategy_id or "").split("_", 1)[0].strip()
        return first_token.upper() if first_token else "Portfolio"

    @classmethod
    def _portfolio_strategy_family_label(
        cls,
        strategy_id: str,
        asset_label: str,
        params: Dict[str, Any],
        config: Dict[str, Any],
    ) -> str:
        strategy_mode_id = str(config.get("strategy_mode_id") or "").strip()
        if strategy_mode_id:
            return cls._humanize_strategy_tokens(strategy_mode_id.split("_"))

        tokens = [token for token in re.split(r"[^A-Za-z0-9]+", str(strategy_id or "")) if token]
        if not tokens:
            return "Portfolio Strategy"

        lowered = [token.lower() for token in tokens]
        param_start = len(tokens)
        if isinstance(params, dict):
            for key in params.keys():
                key_tokens = [token for token in str(key).lower().split("_") if token]
                if not key_tokens:
                    continue
                for index in range(0, len(lowered) - len(key_tokens) + 1):
                    if lowered[index : index + len(key_tokens)] == key_tokens:
                        param_start = min(param_start, index)
        core_tokens = tokens[:param_start]

        asset_tokens = {
            token.lower()
            for token in re.split(r"[^A-Za-z0-9]+", str(asset_label or ""))
            if token.strip()
        }
        stop_tokens = {
            "backtest",
            "demo",
            "example",
            "matrix",
            "portfolio",
            "price",
            "prices",
            "run",
            "selection",
            "single",
            "strategy",
            "sweep",
            "test",
            "yf",
            "yfinance",
        }
        filtered = [
            token
            for token in core_tokens
            if token.lower() not in asset_tokens
            and token.lower() not in stop_tokens
            and not re.fullmatch(r"v\d+|\d+", token.lower())
        ]
        if filtered:
            return cls._humanize_strategy_tokens(filtered)

        signals = config.get("signals", {}) if isinstance(config.get("signals"), dict) else {}
        entry = signals.get("entry", {}) if isinstance(signals.get("entry"), dict) else {}
        field_tokens = " ".join(
            str(entry.get(key, ""))
            for key in ("field", "right_field", "op")
        ).lower()
        if "ma" in field_tokens or "moving" in field_tokens:
            return "MA Cross"
        return "Portfolio Strategy"

    @staticmethod
    def _semantic_combo_parts(combo: Dict[str, Any]) -> List[str]:
        if not isinstance(combo, dict) or not combo:
            return []
        preferred_order = [
            "short_ma",
            "long_ma",
            "fast_ma",
            "slow_ma",
            "entry_ma",
            "exit_ma",
            "lookback",
            "sma_period",
            "threshold",
            "vix_threshold",
            "vix_max",
            "target_weight",
        ]
        order_rank = {key: index for index, key in enumerate(preferred_order)}
        original_rank = {str(key): index for index, key in enumerate(combo.keys())}
        keys = sorted(
            [str(key) for key in combo.keys()],
            key=lambda key: (
                order_rank.get(key, len(preferred_order) + original_rank.get(key, 0)),
                original_rank.get(key, 0),
            ),
        )
        return [f"{key}={combo[key]}" for key in keys]

    @staticmethod
    def _humanize_strategy_tokens(tokens: List[str]) -> str:
        acronyms = {
            "adx",
            "atr",
            "btc",
            "ema",
            "etf",
            "gld",
            "ma",
            "macd",
            "ohlcv",
            "qqq",
            "rsi",
            "sma",
            "spy",
            "tqqq",
            "usdt",
            "vix",
            "voo",
            "wfa",
        }
        words: List[str] = []
        for token in tokens:
            clean = str(token).strip()
            if not clean:
                continue
            lower = clean.lower()
            if lower in acronyms:
                words.append(lower.upper())
            else:
                words.append(lower[:1].upper() + lower[1:])
        return " ".join(words).strip()

    @classmethod
    def _clean_strategy_id_label(cls, strategy_id: Any, params: Dict[str, Any]) -> str:
        text = str(strategy_id or "").strip()
        if not text:
            return ""
        return cls._portfolio_strategy_table_label(
            run_id=text,
            metadata={},
            config={},
            strategy_id=text,
            params=params if isinstance(params, dict) else {},
        )

    @staticmethod
    def _looks_like_internal_strategy_label(label: Any) -> bool:
        text = str(label or "").strip()
        if not text or "_" not in text:
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9_-]+", text))

    def _build_category_map(self, rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        mapping: Dict[str, List[str]] = {}
        for category_id, config in CATEGORY_MAP.items():
            key = config["key"]
            sorted_rows = sorted(
                rows,
                key=lambda item: self._sort_value(
                    item.get(key),
                    ascending=bool(config["ascending"]),
                ),
                reverse=not bool(config["ascending"]),
            )
            mapping[category_id] = [
                str(item.get("backtest_id")) for item in sorted_rows[:20]
            ]
        return mapping

    def _parameter_matrix_dataset_label(
        self,
        run_id: str,
        overview: Dict[str, Any],
        rows: List[Dict[str, Any]],
    ) -> str:
        strategy_summary = overview.get("strategy_summary") if isinstance(overview, dict) else {}
        if not isinstance(strategy_summary, dict):
            strategy_summary = {}
        for value in (
            strategy_summary.get("asset_label"),
            overview.get("dataset_label") if isinstance(overview, dict) else "",
            overview.get("asset_label") if isinstance(overview, dict) else "",
        ):
            text = str(value or "").strip()
            if text:
                return text
        for row in rows:
            if not isinstance(row, dict):
                continue
            for value in (row.get("asset_label"), row.get("dataset_label")):
                text = str(value or "").strip()
                if text:
                    return text
        try:
            summary = self._strategy_summary(run_id)
        except Exception:
            summary = {}
        if isinstance(summary, dict):
            return str(summary.get("asset_label") or "").strip()
        return ""

    def _infer_param_axes(self, rows: List[Dict[str, Any]]) -> List[str]:
        axis_counts: Dict[str, int] = {}
        for row in rows:
            combo = row.get("semantic_combo", {})
            if not isinstance(combo, dict):
                continue
            for key, value in combo.items():
                if value is None:
                    continue
                axis_counts[key] = axis_counts.get(key, 0) + 1
        ranked = sorted(axis_counts.items(), key=lambda item: (-item[1], item[0]))
        return [key for key, _count in ranked]

    def _metric_float(self, metric_row: Dict[str, Any], key: str) -> float:
        return self._as_float(metric_row.get(METRIC_KEY_MAP[key]))

    def _extract_last_trade_time(self, subset: pd.DataFrame) -> Optional[str]:
        if subset.empty or "Trade_action" not in subset.columns:
            return None
        closed = subset[subset["Trade_action"].fillna(0).astype(int) == 4]
        if closed.empty:
            return None
        return self._to_iso(closed.iloc[-1]["Time"])

    def _build_last_trade_time_map(self, trade_df: pd.DataFrame) -> Dict[str, Optional[str]]:
        if trade_df.empty:
            return {}
        action_series = trade_df["Trade_action"].fillna(0).astype(int)
        closed = trade_df.loc[action_series == 4, ["Backtest_id", "Time", "Close_time"]].copy()
        if closed.empty:
            return {}
        closed["event_time"] = closed["Close_time"].where(closed["Close_time"].notna(), closed["Time"])
        closed = closed.sort_values(["Backtest_id", "event_time"])
        latest = closed.groupby("Backtest_id", sort=False)["event_time"].last()
        return {str(backtest_id): self._to_iso(value) for backtest_id, value in latest.items()}

    def _sort_value(self, value: Any, *, ascending: bool) -> float:
        cast = self._as_float(value)
        if math.isnan(cast):
            return float("inf") if ascending else float("-inf")
        return cast

    def _build_backtest_label(self, backtest_id: str, combo: Dict[str, Any]) -> str:
        semantic_combo = combo.get("semantic_combo", {}) if isinstance(combo, dict) else {}
        strategy_display_label = combo.get("strategy_display_label") if isinstance(combo, dict) else None
        if (
            isinstance(strategy_display_label, str)
            and strategy_display_label.strip()
            and not strategy_display_label.startswith("Strategy ")
        ):
            if self._looks_like_internal_strategy_label(strategy_display_label):
                return self._clean_strategy_id_label(strategy_display_label, semantic_combo)
            return strategy_display_label
        semantic_run_label = combo.get("semantic_run_label") if isinstance(combo, dict) else None
        if isinstance(semantic_run_label, str) and semantic_run_label.strip():
            if self._looks_like_internal_strategy_label(semantic_run_label):
                return self._clean_strategy_id_label(semantic_run_label, semantic_combo)
            return semantic_run_label
        if isinstance(semantic_combo, dict) and semantic_combo:
            return " | ".join(self._semantic_combo_parts(semantic_combo))
        strategy_id = combo.get("strategy_id") if isinstance(combo, dict) else None
        if (
            isinstance(strategy_id, str)
            and strategy_id.strip()
            and not strategy_id.startswith("_")
        ):
            if self._looks_like_internal_strategy_label(strategy_id):
                return self._clean_strategy_id_label(strategy_id, semantic_combo)
            return str(strategy_id)
        return f"Strategy {str(backtest_id)[:8]}"

    def _label_source(self, combo: Dict[str, Any]) -> str:
        if not isinstance(combo, dict) or not combo:
            return "internal_id_fallback"
        if combo.get("strategy_display_label") and not str(
            combo.get("strategy_display_label")
        ).startswith("Strategy "):
            return "strategy_display_label"
        if combo.get("semantic_run_label"):
            return "semantic_run_label"
        if combo.get("semantic_combo"):
            return "semantic_combo"
        if combo.get("strategy_id") and not str(combo.get("strategy_id")).startswith("_"):
            return "strategy_id"
        return "internal_id_fallback"

    def _load_backtest_index_map(self, run_id: str) -> Dict[str, Dict[str, Any]]:
        index_path = self._snapshot_path(run_id, "backtest_result_index.json")
        payload = self._load_json(index_path, {})
        rows = payload.get("backtests", []) if isinstance(payload, dict) else []
        mapping: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            mapping[str(row.get("backtest_id", ""))] = row
        return mapping

    def _load_future_live_search_config(self, run_id: str) -> Dict[str, Any]:
        wfa_dir = self.repo_root / "workspace" / "wfa"
        candidates = [
            wfa_dir / f"wfa-shortlist-{run_id}.user.json",
            wfa_dir / "wfa-latest.user.json",
        ]
        for path in candidates:
            config = self._load_json(path, None)
            if not isinstance(config, dict):
                continue
            optimizer = config.get("wfa_config", {}).get("optimizer", {})
            if not isinstance(optimizer, dict) or not optimizer:
                continue
            return {
                "label": "Future live-search config",
                "source_filename": path.name,
                "config_path": str(path),
                "mode": str(optimizer.get("mode", "") or "").strip() or None,
                "sampler": str(optimizer.get("sampler", "") or "").strip() or None,
                "n_trials": optimizer.get("n_trials"),
                "n_startup_trials": optimizer.get("n_startup_trials"),
                "multivariate": optimizer.get("multivariate"),
                "timeout_seconds": optimizer.get("timeout_seconds"),
                "note": "This is configuration for a future live search, not something consumed by this completed sweep.",
                "ranking": config.get("wfa_config", {}).get("ranking", {}),
                "acceptance": config.get("wfa_config", {}).get("acceptance", {}),
                "robust_selection": config.get("wfa_config", {}).get("robust_selection", {}),
            }
        return {}

    @staticmethod
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        merged = copy.deepcopy(base or {})
        for key, value in (override or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = AppPayloadService._deep_merge(merged.get(key, {}), value)
            else:
                merged[key] = value
        return merged

    def _match_backtest_ref(
        self,
        execution_plan_hash: Any,
        semantic_combo: Dict[str, Any],
    ) -> Optional[Dict[str, str]]:
        target_hash = str(execution_plan_hash or "")
        if not target_hash:
            return None
        for run in self.registry.list_runs(module="autorunner"):
            if str(run.get("status", "")) not in {"completed", "partial"}:
                continue
            index_map = self._load_backtest_index_map(str(run.get("run_id")))
            for backtest_id, row in index_map.items():
                if str(row.get("execution_plan_hash", "")) != target_hash:
                    continue
                if row.get("semantic_combo", {}) == semantic_combo:
                    return {
                        "run_id": str(run.get("run_id")),
                        "backtest_id": backtest_id,
                }
        return None

    @staticmethod
    def _record_backtest_ref(record: Dict[str, Any]) -> Optional[Dict[str, str]]:
        run_id = str(record.get("linked_backtest_run_id") or "").strip()
        backtest_id = str(record.get("linked_backtest_id") or "").strip()
        if run_id and backtest_id:
            return {"run_id": run_id, "backtest_id": backtest_id}
        linked = record.get("linked_backtest")
        if isinstance(linked, dict):
            run_id = str(linked.get("run_id") or "").strip()
            backtest_id = str(linked.get("backtest_id") or "").strip()
            if run_id and backtest_id:
                return {"run_id": run_id, "backtest_id": backtest_id}
        return None

    @staticmethod
    def _parse_semantic_combo(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            text = raw.strip()
            try:
                value = json.loads(text)
                return value if isinstance(value, dict) else {"value": text}
            except Exception:
                parsed: Dict[str, Any] = {}
                for part in text.split("|"):
                    if "=" not in part:
                        continue
                    key, value = part.split("=", 1)
                    key = key.strip()
                    if key:
                        parsed[key] = value.strip()
                return parsed or {"value": text}
        return {}

    @staticmethod
    def _parse_json_object(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                value = json.loads(raw)
                return value if isinstance(value, dict) else {}
            except Exception:
                return {}
        return {}

    def _wfa_portfolio_window_summary(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        allocation_by_window: List[Dict[str, Any]] = []
        contribution_by_window: List[Dict[str, Any]] = []
        asset_summary: Dict[str, Dict[str, Any]] = {}

        for row in rows:
            snapshot = row.get("oos_portfolio", {}) if isinstance(row.get("oos_portfolio"), dict) else {}
            allocation = snapshot.get("allocation", []) if isinstance(snapshot.get("allocation"), list) else []
            contribution = snapshot.get("contribution", []) if isinstance(snapshot.get("contribution"), list) else []
            if allocation:
                allocation_by_window.append(
                    {
                        "window_id": row.get("window_id"),
                        "semantic_combo": row.get("semantic_combo", {}),
                        "test_start_date": row.get("test_start_date"),
                        "test_end_date": row.get("test_end_date"),
                        "avg_exposure": row.get("oos_avg_exposure"),
                        "avg_holdings": row.get("oos_avg_holdings"),
                        "active_rebalance_count": row.get("oos_rebalance_count"),
                        "risk_gate_event_count": row.get("oos_risk_gate_event_count"),
                        "weights": allocation,
                    }
                )
            if contribution:
                contribution_by_window.append(
                    {
                        "window_id": row.get("window_id"),
                        "semantic_combo": row.get("semantic_combo", {}),
                        "test_start_date": row.get("test_start_date"),
                        "test_end_date": row.get("test_end_date"),
                        "contributions": contribution,
                    }
                )
            for item in allocation:
                if not isinstance(item, dict):
                    continue
                asset = str(item.get("asset") or "").strip()
                if not asset:
                    continue
                bucket = asset_summary.setdefault(
                    asset,
                    {
                        "asset": asset,
                        "avg_weight_values": [],
                        "last_weight_values": [],
                        "active_windows": 0,
                        "return_contribution": 0.0,
                    },
                )
                avg_weight = self._finite_or_none(item.get("avg_weight"))
                last_weight = self._finite_or_none(item.get("last_weight"))
                if avg_weight is not None:
                    bucket["avg_weight_values"].append(avg_weight)
                    if abs(avg_weight) > 1e-12:
                        bucket["active_windows"] += 1
                if last_weight is not None:
                    bucket["last_weight_values"].append(last_weight)
            for item in contribution:
                if not isinstance(item, dict):
                    continue
                asset = str(item.get("asset") or "").strip()
                if not asset:
                    continue
                bucket = asset_summary.setdefault(
                    asset,
                    {
                        "asset": asset,
                        "avg_weight_values": [],
                        "last_weight_values": [],
                        "active_windows": 0,
                        "return_contribution": 0.0,
                    },
                )
                bucket["return_contribution"] += float(self._finite_or_none(item.get("return_contribution")) or 0.0)

        summary_rows: List[Dict[str, Any]] = []
        for bucket in asset_summary.values():
            avg_weights = bucket.pop("avg_weight_values", [])
            last_weights = bucket.pop("last_weight_values", [])
            bucket["mean_avg_weight"] = self._finite_or_none(
                sum(avg_weights) / len(avg_weights) if avg_weights else None
            )
            bucket["mean_last_weight"] = self._finite_or_none(
                sum(last_weights) / len(last_weights) if last_weights else None
            )
            bucket["return_contribution"] = self._finite_or_none(bucket.get("return_contribution"))
            summary_rows.append(bucket)
        summary_rows.sort(
            key=lambda item: abs(float(item.get("return_contribution") or 0.0)),
            reverse=True,
        )
        return {
            "is_portfolio_wfa": any(len(item.get("weights", [])) > 1 for item in allocation_by_window),
            "allocation_by_window": sorted(
                allocation_by_window,
                key=lambda item: int(item.get("window_id") or 0),
            ),
            "contribution_by_window": sorted(
                contribution_by_window,
                key=lambda item: int(item.get("window_id") or 0),
            ),
            "asset_summary": summary_rows,
        }

    def _artifact_path(self, run_id: str, artifact_type: str) -> Optional[Path]:
        manifest = self.registry.load_artifact_manifest(run_id)
        candidates: List[Path] = []
        snapshot_root = self.registry.build_run_paths(run_id)["snapshot_dir"].resolve()
        for artifact in manifest.get("artifacts", []) if isinstance(manifest, dict) else []:
            if artifact.get("artifact_type") != artifact_type:
                continue
            path = self._safe_manifest_artifact_path(str(artifact.get("path", "")))
            if path is not None:
                candidates.append(path)
        if not candidates:
            return None
        candidates.sort(
            key=lambda value: (
                0 if snapshot_root in value.resolve().parents else 1,
                "_audit" in value.name.lower(),
                "_metadata" in value.name.lower(),
                value.name.lower().endswith(".json"),
                value.name.lower(),
            )
        )
        return candidates[0]

    def _artifact_paths(self, run_id: str, artifact_type: str) -> List[Path]:
        manifest = self.registry.load_artifact_manifest(run_id)
        candidates: List[Path] = []
        snapshot_root = self.registry.build_run_paths(run_id)["snapshot_dir"].resolve()
        for artifact in manifest.get("artifacts", []) if isinstance(manifest, dict) else []:
            if artifact.get("artifact_type") != artifact_type:
                continue
            path = self._safe_manifest_artifact_path(str(artifact.get("path", "")))
            if path is not None:
                candidates.append(path)
        candidates.sort(
            key=lambda value: (
                0 if snapshot_root in value.resolve().parents else 1,
                "_audit" in value.name.lower(),
                "_metadata" in value.name.lower(),
                value.name.lower().endswith(".json"),
                value.name.lower(),
            )
        )
        return candidates

    def _safe_manifest_artifact_path(self, path_text: str) -> Optional[Path]:
        text = str(path_text or "").strip()
        if not text:
            return None
        try:
            path = Path(text)
            if not path.is_absolute():
                path = self.repo_root / path
            resolved = path.resolve()
            repo_root = self.repo_root.resolve()
            if resolved != repo_root and repo_root not in resolved.parents:
                return None
            if not resolved.exists() or not resolved.is_file():
                return None
            return resolved
        except Exception:
            return None

    def _wfa_sidecar_metadata(self, wfa_path: Path) -> Dict[str, Any]:
        stem = wfa_path.stem
        candidates = [wfa_path.with_name(f"{stem}_metadata.json")]
        if stem.endswith("_selected_optimum"):
            candidates.insert(0, wfa_path.with_name(f"{stem[:-len('_selected_optimum')]}_metadata.json"))
        candidates.extend(sorted(wfa_path.parent.glob("*metadata*.json")))
        for candidate in candidates:
            loaded = self._load_json(candidate, {})
            if (
                isinstance(loaded, dict)
                and loaded
                and (
                    loaded.get("row_contract") == "selected_optimum_per_window"
                    or "windowing" in loaded
                    or "selection_constraints" in loaded
                )
            ):
                return loaded
        return {}

    def _wfa_windowing_metadata(
        self,
        df: pd.DataFrame,
        rows: List[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        from_metadata = metadata.get("windowing", {}) if isinstance(metadata.get("windowing"), dict) else {}
        if from_metadata:
            return {**from_metadata, "metadata_source": "sidecar_metadata"}

        def first_number(column: str) -> Optional[float]:
            if column not in df.columns:
                return None
            series = pd.to_numeric(df[column], errors="coerce").dropna()
            return float(series.iloc[0]) if not series.empty else None

        train_sizes: List[int] = []
        test_sizes: List[int] = []
        for row in rows:
            train_start = pd.to_datetime(row.get("train_start_date"), errors="coerce")
            train_end = pd.to_datetime(row.get("train_end_date"), errors="coerce")
            test_start = pd.to_datetime(row.get("test_start_date"), errors="coerce")
            test_end = pd.to_datetime(row.get("test_end_date"), errors="coerce")
            if pd.notna(train_start) and pd.notna(train_end):
                train_sizes.append(max(1, int(np.busday_count(train_start.date(), train_end.date())) + 1))
            if pd.notna(test_start) and pd.notna(test_end):
                test_sizes.append(max(1, int(np.busday_count(test_start.date(), test_end.date())) + 1))
        inferred_train = int(round(float(np.median(train_sizes)))) if train_sizes else None
        inferred_test = int(round(float(np.median(test_sizes)))) if test_sizes else None
        return {
            "size_mode": "unknown",
            "sizing_source": "artifact_dates",
            "metadata_source": "artifact_dates",
            "effective_train_size": first_number("effective_train_size") or inferred_train,
            "effective_test_size": first_number("effective_test_size") or inferred_test,
            "effective_step_size": first_number("effective_step_size"),
            "actual_window_count": len(rows),
            "auto_indicators": {},
        }

    def _wfa_selection_constraints_metadata(
        self,
        df: pd.DataFrame,
        rows: List[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        from_metadata = (
            metadata.get("selection_constraints", {})
            if isinstance(metadata.get("selection_constraints"), dict)
            else {}
        )
        if from_metadata:
            return {**from_metadata, "metadata_source": "sidecar_metadata"}
        applied = any(bool(row.get("selection_constraints_applied")) for row in rows)
        pool_counts = [
            self._finite_or_none(row.get("selection_pool_count"))
            for row in rows
            if self._finite_or_none(row.get("selection_pool_count")) is not None
        ]
        total_counts = [
            self._finite_or_none(row.get("selection_pool_total_count"))
            for row in rows
            if self._finite_or_none(row.get("selection_pool_total_count")) is not None
        ]
        return {
            "enabled": applied,
            "metadata_source": "artifact_rows",
            "observed_min_pool_count": min(pool_counts) if pool_counts else None,
            "observed_max_total_count": max(total_counts) if total_counts else None,
        }

    def _wfa_candidate_budget_metadata(
        self,
        df: pd.DataFrame,
        rows: List[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        fields = [
            "candidate_budget",
            "candidate_budget_applied",
            "candidate_budget_policy",
            "candidate_budget_method",
            "candidate_budget_seed",
            "candidate_count",
            "total_candidate_count",
        ]
        from_metadata = {key: metadata.get(key) for key in fields if key in metadata}
        if from_metadata:
            return {**from_metadata, "metadata_source": "sidecar_metadata"}

        def first_present(key: str) -> Any:
            for row in rows:
                value = row.get(key)
                if value is None:
                    continue
                if isinstance(value, float) and math.isnan(value):
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                return value
            return None

        candidate_count_values = [
            self._finite_or_none(row.get("candidate_count"))
            for row in rows
            if self._finite_or_none(row.get("candidate_count")) is not None
        ]
        total_candidate_count_values = [
            self._finite_or_none(row.get("total_candidate_count"))
            for row in rows
            if self._finite_or_none(row.get("total_candidate_count")) is not None
        ]
        applied_values = [
            self._optional_bool(row.get("candidate_budget_applied"))
            for row in rows
            if self._optional_bool(row.get("candidate_budget_applied")) is not None
        ]
        applied = any(value is True for value in applied_values)
        policy = first_present("candidate_budget_policy")
        method = first_present("candidate_budget_method")
        if not policy and (applied_values or candidate_count_values or total_candidate_count_values):
            policy = "seeded_random_sample" if applied else "full_grid"
        if not method and policy:
            method = policy
        return {
            "candidate_budget": first_present("candidate_budget"),
            "candidate_budget_applied": applied if applied_values else None,
            "candidate_budget_policy": policy,
            "candidate_budget_method": method,
            "candidate_budget_seed": first_present("candidate_budget_seed"),
            "candidate_count": max(candidate_count_values) if candidate_count_values else None,
            "total_candidate_count": max(total_candidate_count_values) if total_candidate_count_values else None,
            "metadata_source": "artifact_rows",
        }

    def _wfa_dashboard_artifact_path(self, run_id: str) -> Optional[Path]:
        candidates = self._artifact_paths(run_id, "wfa_parquet")
        if not candidates:
            return None

        legacy_one_row_per_window: Optional[Path] = None
        diagnostic_only: Optional[Path] = None
        unreadable_fallback: Optional[Path] = None

        for path in candidates:
            try:
                df = pd.read_parquet(path)
            except Exception:
                if unreadable_fallback is None:
                    unreadable_fallback = path
                continue
            if df.empty:
                continue
            if "wfa_row_type" in df.columns:
                row_type = df["wfa_row_type"].fillna("").astype(str)
                if (row_type == "selected_optimum").any():
                    return path
                if diagnostic_only is None:
                    diagnostic_only = path
                continue
            if "window_id" in df.columns and df.groupby("window_id").size().max() == 1:
                if legacy_one_row_per_window is None:
                    legacy_one_row_per_window = path
                continue
            if diagnostic_only is None:
                diagnostic_only = path

        return legacy_one_row_per_window or diagnostic_only or unreadable_fallback

    @staticmethod
    def _metrics_metadata_path(metrics_path: Path) -> Path:
        legacy = metrics_path.with_name(
            metrics_path.name.replace("_metrics.parquet", "_metadata.json")
        )
        if legacy != metrics_path and legacy.exists():
            return legacy
        canonical = metrics_path.with_name(
            metrics_path.name.replace("_metrics_", "_metrics_metadata_").replace(
                ".parquet", ".json"
            )
        )
        if canonical.exists():
            return canonical
        candidates = sorted(
            metrics_path.parent.glob("*metrics*metadata*.json"),
            key=lambda item: item.stat().st_mtime if item.exists() else 0,
            reverse=True,
        )
        return candidates[0] if candidates else canonical

    @staticmethod
    def _payload_source_refs_exist(payload: Dict[str, Any]) -> bool:
        refs = payload.get("artifact_source_refs", [])
        if not isinstance(refs, list):
            return True
        return all(Path(str(ref)).exists() for ref in refs if str(ref).strip())

    def _chart_path(self, run_id: str, name: str) -> Path:
        path = self.registry.build_run_paths(run_id)["chart_payload_dir"] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _snapshot_path(self, run_id: str, name: str) -> Path:
        return self.registry.build_run_paths(run_id)["snapshot_dir"] / name

    def _source_strategy_config(self, run_id: str) -> Dict[str, Any]:
        snapshot = self._load_json(self._snapshot_path(run_id, "run_snapshot.json"), {})
        resolved = snapshot.get("resolved_configs", {}) if isinstance(snapshot, dict) else {}
        for key in ("run_config", "strategy_run"):
            ref = resolved.get(key, {}) if isinstance(resolved, dict) else {}
            path = Path(str(ref.get("config_path", ""))) if isinstance(ref, dict) else Path("")
            if path.exists():
                loaded = self._load_json(path, {})
                if isinstance(loaded, dict):
                    return loaded
        return {}

    def _strategy_summary(self, run_id: str) -> Dict[str, Any]:
        snapshot = self._load_json(self._snapshot_path(run_id, "run_snapshot.json"), {})
        resolved = snapshot.get("resolved_configs", {}) if isinstance(snapshot, dict) else {}
        run_config = resolved.get("run_config", {}) if isinstance(resolved, dict) else {}
        run_config_path = (
            Path(str(run_config.get("config_path", ""))) if isinstance(run_config, dict) else Path("")
        )
        source_config = self._load_json(run_config_path, {}) if run_config_path.exists() else {}
        if not isinstance(source_config, dict):
            source_config = {}
        source_is_wfa_run = str(source_config.get("schema_version") or "").strip() == "wfa_run"
        forced_workflow_id = ""
        strategy_source_config = source_config
        strategy_source_path = run_config_path
        if source_is_wfa_run:
            forced_workflow_id = (
                "rolling_validation"
                if "validation" in str(source_config.get("strategy_config_path") or "").lower()
                else "walk_forward_analysis"
            )
            embedded_backtester = (
                resolved.get("backtester_config", {}) if isinstance(resolved, dict) else {}
            )
            if isinstance(embedded_backtester, dict):
                embedded_strategy = (
                    embedded_backtester.get("strategy_run_config")
                    or embedded_backtester.get("strategy_config")
                )
                if isinstance(embedded_strategy, dict) and embedded_strategy:
                    strategy_source_config = embedded_strategy
                    strategy_source_path = Path("__embedded_strategy_run__.json")
        normalized_config = self._normalized_strategy_config(strategy_source_config, strategy_source_path)
        platform_config = source_config.get("platform", {})
        if not isinstance(platform_config, dict):
            platform_config = {}
        dataloader_config = (
            resolved.get("dataloader_config", {}) if isinstance(resolved, dict) else {}
        )
        backtester_config = (
            resolved.get("backtester_config", {}) if isinstance(resolved, dict) else {}
        )
        contract_refs = snapshot.get("contract_refs", {}) if isinstance(snapshot, dict) else {}
        strategy_ref = contract_refs.get("strategy_contract", {}) if isinstance(contract_refs, dict) else {}
        strategy_path = Path(str(strategy_ref.get("path", ""))) if isinstance(strategy_ref, dict) else Path("")
        if not strategy_path.exists():
            raw_path = (
                backtester_config.get("strategy_contract_path")
                if isinstance(backtester_config, dict)
                else None
            )
            if isinstance(raw_path, str) and raw_path.strip():
                candidate = self.repo_root / raw_path
                if candidate.exists():
                    strategy_path = candidate
        strategy = self._load_json(strategy_path, {}) if strategy_path.exists() else {}
        if not isinstance(strategy, dict):
            strategy = {}
        data_context = strategy.get("data_context", {})
        if not isinstance(data_context, dict):
            data_context = {}
        trading_params = (
            backtester_config.get("trading_params", {})
            if isinstance(backtester_config, dict)
            else {}
        )
        if not isinstance(trading_params, dict):
            trading_params = {}
        parameter_domains = strategy.get("parameter_domains", {})
        source = (
            str(dataloader_config.get("source", "")).strip()
            if isinstance(dataloader_config, dict)
            else ""
        )
        yfinance_config = (
            dataloader_config.get("yfinance_config", {})
            if isinstance(dataloader_config, dict)
            else {}
        )
        asset_label = (
            str(data_context.get("primary_instrument") or "").strip()
            or (
                str(yfinance_config.get("symbol") or "").strip()
                if isinstance(yfinance_config, dict)
                else ""
            )
            or ("Dataset" if source and source.lower() != "yfinance" else "")
        )
        frequency_label = (
            str(data_context.get("frequency") or "").strip()
            or (
                str(yfinance_config.get("interval") or "").strip()
                if isinstance(yfinance_config, dict)
                else ""
            )
        )
        period_label = self._render_period_label(dataloader_config)
        transaction_cost = trading_params.get("transaction_cost")
        slippage = trading_params.get("slippage")
        trade_delay = trading_params.get("trade_delay")
        trade_price = trading_params.get("trade_price")
        strategy_platform_config = (
            strategy_source_config.get("platform", {})
            if isinstance(strategy_source_config, dict)
            else {}
        )
        if not isinstance(strategy_platform_config, dict):
            strategy_platform_config = {}
        strategy_mode_id = str(
            strategy_platform_config.get("strategy_mode_id")
            or platform_config.get("strategy_mode_id")
            or platform_config.get("product_mode_id")
            or ""
        ).strip()
        workflow_id = str(platform_config.get("workflow_id") or "").strip()
        if forced_workflow_id:
            workflow_id = forced_workflow_id
        if not workflow_id:
            workflow_id = self._infer_workflow_id(
                run_config_path=run_config_path,
                source_config=source_config,
                parameter_domains=parameter_domains,
            )
        mode_label = self._render_strategy_mode_label(
            strategy=strategy,
            backtester_config=backtester_config,
            parameter_domains=parameter_domains,
            strategy_mode_id=strategy_mode_id,
        )
        summary = {
            "strategy_id": strategy.get("strategy_id"),
            "name": strategy.get("name") or strategy.get("strategy_id") or strategy_path.stem,
            "description": strategy.get("description"),
            "strategy_contract_path": str(strategy_path) if strategy_path.exists() else "",
            "asset_label": asset_label,
            "period_label": period_label,
            "frequency_label": frequency_label,
            "calendar_label": data_context.get("calendar") or data_context.get("market_calendar"),
            "timezone_label": data_context.get("timezone"),
            "strategy_mode_id": strategy_mode_id,
            "mode_label": mode_label,
            "workflow_id": workflow_id,
            "workflow_label": self._workflow_label(workflow_id),
            "execution_label": self._render_execution_label(trade_delay, trade_price),
            "cost_label": self._render_cost_label(transaction_cost, slippage),
            "entry_rule": self._render_rule_node(strategy.get("entry")),
            "exit_rule": self._render_rule_node(strategy.get("exit")),
            "parameter_domains": parameter_domains if isinstance(parameter_domains, dict) else {},
            "parameter_domain_label": self._render_parameter_domains(parameter_domains),
            "mode_registry_path": str(self._mode_registry_path()),
            "available_mode_labels": self._strategy_mode_labels(status="planned"),
            "source": "strategy_contract_semantic",
        }
        if normalized_config:
            summary = self._apply_normalized_strategy_summary(summary, normalized_config)
        source_display_rules = self._strategy_rule_display_overrides(source_config, strategy_source_config)
        if source_display_rules:
            summary.update(source_display_rules)
        source_parameter_domains = (
            strategy_source_config.get("parameter_domains", {})
            if isinstance(strategy_source_config, dict)
            else {}
        )
        if isinstance(source_parameter_domains, dict) and source_parameter_domains:
            summary["parameter_domains"] = source_parameter_domains
            if not source_display_rules.get("parameter_domain_label"):
                summary["parameter_domain_label"] = self._render_parameter_domains(source_parameter_domains)
        if forced_workflow_id:
            summary["workflow_id"] = forced_workflow_id
            summary["workflow_label"] = self._workflow_label(forced_workflow_id)
        return self._attach_strategy_summary_display(summary)

    def _normalized_strategy_config(self, source_config: Any, source_path: Path) -> Dict[str, Any]:
        if normalize_strategy_run_config is None or not isinstance(source_config, dict) or not source_config:
            return {}
        try:
            return normalize_strategy_run_config(
                source_config,
                source_path=source_path if source_path.exists() else None,
                repo_root=self.repo_root,
            )
        except Exception:
            return {}

    def _apply_normalized_strategy_summary(
        self,
        summary: Dict[str, Any],
        normalized: Dict[str, Any],
    ) -> Dict[str, Any]:
        out = dict(summary)
        platform = normalized.get("platform", {}) if isinstance(normalized.get("platform"), dict) else {}
        data = normalized.get("data", {}) if isinstance(normalized.get("data"), dict) else {}
        universe = normalized.get("universe", {}) if isinstance(normalized.get("universe"), dict) else {}
        signals = normalized.get("signals", {}) if isinstance(normalized.get("signals"), dict) else {}
        selection = normalized.get("selection", {}) if isinstance(normalized.get("selection"), dict) else {}
        allocation = normalized.get("allocation", {}) if isinstance(normalized.get("allocation"), dict) else {}
        rebalance = normalized.get("rebalance", {}) if isinstance(normalized.get("rebalance"), dict) else {}
        fill_model = normalized.get("fill_model", {}) if isinstance(normalized.get("fill_model"), dict) else {}
        risk = normalized.get("risk", {}) if isinstance(normalized.get("risk"), dict) else {}
        metadata = normalized.get("metadata", {}) if isinstance(normalized.get("metadata"), dict) else {}
        parameter_domains = (
            normalized.get("parameter_domains", {})
            if isinstance(normalized.get("parameter_domains"), dict)
            else {}
        )
        display_rules = self._strategy_rule_display_overrides(normalized)
        symbols = [str(item).strip().upper() for item in universe.get("symbols", []) if str(item).strip()]
        strategy_mode_id = str(platform.get("strategy_mode_id") or out.get("strategy_mode_id") or "").strip()
        workflow_id = str(platform.get("workflow_id") or out.get("workflow_id") or "").strip()
        cost = fill_model.get("cost", {}) if isinstance(fill_model.get("cost"), dict) else {}
        benchmark = data.get("benchmark")
        if isinstance(benchmark, dict):
            benchmark_label = benchmark.get("label") or benchmark.get("symbol") or ""
        else:
            benchmark_label = str(benchmark or "")

        out.update(
            {
                "strategy_id": metadata.get("strategy_id") or out.get("strategy_id"),
                "asset_label": ", ".join(symbols) if symbols else out.get("asset_label", ""),
                "frequency_label": data.get("frequency") or out.get("frequency_label", ""),
                "calendar_label": data.get("calendar") or out.get("calendar_label"),
                "timezone_label": data.get("timezone") or out.get("timezone_label"),
                "strategy_mode_id": strategy_mode_id,
                "mode_label": display_rules.get("mode_label")
                or self._strategy_mode_label(strategy_mode_id)
                or out.get("mode_label", ""),
                "workflow_id": workflow_id,
                "workflow_label": self._workflow_label(workflow_id),
                "execution_label": self._render_normalized_execution_label(fill_model)
                or out.get("execution_label", ""),
                "cost_label": self._render_cost_label(
                    cost.get("transaction_cost"),
                    cost.get("slippage"),
                ),
                "entry_rule": display_rules.get("entry_rule")
                or self._render_normalized_entry_rule(signals, selection, rebalance),
                "exit_rule": display_rules.get("exit_rule")
                or self._render_normalized_exit_rule(signals, rebalance, allocation),
                "parameter_domains": parameter_domains,
                "parameter_domain_label": display_rules.get("parameter_domain_label")
                or self._render_parameter_domains(parameter_domains),
                "benchmark_label": benchmark_label,
                "risk_label": self._render_risk_label(risk),
                "source": "strategy_run",
            }
        )
        if plan_strategy_execution is not None:
            try:
                out["execution_plan"] = plan_strategy_execution(normalized)
            except Exception as exc:  # pragma: no cover - optional plan preview fallback
                out["execution_plan_error"] = exc.__class__.__name__
        return self._attach_strategy_summary_display(out)

    def _attach_strategy_summary_display(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(summary)
        display = out.get("display") if isinstance(out.get("display"), dict) else {}
        strategy_rules = display.get("strategy_rules") if isinstance(display.get("strategy_rules"), dict) else {}
        en_rules = dict(strategy_rules.get("en") or {})
        zh_rules = dict(strategy_rules.get("zh_Hant") or strategy_rules.get("zh-Hant") or {})
        for key in (
            "asset_label",
            "mode_label",
            "workflow_label",
            "cost_label",
            "entry_rule",
            "exit_rule",
            "parameter_domain_label",
            "calendar_label",
            "timezone_label",
            "benchmark_label",
        ):
            value = str(out.get(key) or "").strip()
            if value:
                en_rules.setdefault(key, value)
                zh_rules.setdefault(key, value)
        execution_label = str(out.get("execution_label") or "").strip()
        if execution_label:
            en_rules.setdefault("execution_label", execution_label)
            zh_rules.setdefault("execution_label", self._render_execution_label_zh(out))
            display["execution"] = {
                "en": en_rules["execution_label"],
                "zh_Hant": zh_rules["execution_label"],
            }
        display["strategy_rules"] = {
            "en": en_rules,
            "zh_Hant": zh_rules,
        }
        out["display"] = display
        return out

    def _mode_registry_path(self) -> Path:
        return self.repo_root / "backtester" / "contracts" / "strategy" / "mode-registry-v1.json"

    @staticmethod
    def _workflow_label(workflow_id: str) -> str:
        labels = {
            "single_backtest": "Single backtest",
            "parameter_matrix": "Parameter matrix",
            "walk_forward_analysis": "Walk-forward analysis",
            "rolling_validation": "Rolling validation",
            "statanalyser": "Stat analyser",
        }
        workflow = str(workflow_id or "").strip()
        return labels.get(workflow, workflow.replace("_", " ").title() if workflow else "")

    @classmethod
    def _infer_workflow_id(cls, *, run_config_path: Path, source_config: Any, parameter_domains: Any) -> str:
        if isinstance(source_config, dict) and isinstance(source_config.get("wfa_config"), dict):
            return "walk_forward_analysis"
        config_name = run_config_path.name.lower() if isinstance(run_config_path, Path) else ""
        if "matrix" in config_name or "sweep" in config_name:
            return "parameter_matrix"
        return "single_backtest"

    def _strategy_mode_labels(self, *, status: str) -> List[str]:
        registry = self._load_json(self._mode_registry_path(), {})
        modes = registry.get("modes", []) if isinstance(registry, dict) else []
        labels: List[str] = []
        for mode in modes:
            if not isinstance(mode, dict):
                continue
            if str(mode.get("status", "")).strip().lower() != status:
                continue
            label = str(mode.get("label", "")).strip()
            if label:
                labels.append(label)
        return labels

    def _strategy_mode_label(self, mode_id: str) -> str:
        if not mode_id:
            return ""
        registry = self._load_json(self._mode_registry_path(), {})
        modes = registry.get("modes", []) if isinstance(registry, dict) else []
        for mode in modes:
            if not isinstance(mode, dict):
                continue
            if str(mode.get("id", "")).strip() == mode_id:
                return str(mode.get("label", "")).strip()
        return mode_id.replace("_", " ").title()

    @staticmethod
    def _render_period_label(dataloader_config: Any) -> str:
        if not isinstance(dataloader_config, dict):
            return ""
        start = str(dataloader_config.get("start_date") or "").strip()
        end = str(dataloader_config.get("end_date") or "").strip()
        if start and end:
            return f"{start} -> {end}"
        if start:
            return f"{start} -> latest available"
        if end:
            return f"through {end}"
        return ""

    def _render_strategy_mode_label(
        self,
        *,
        strategy: Dict[str, Any],
        backtester_config: Any,
        parameter_domains: Any,
        strategy_mode_id: str = "",
    ) -> str:
        explicit_label = self._strategy_mode_label(strategy_mode_id)
        if explicit_label:
            return explicit_label
        family = str(strategy.get("strategy_family") or strategy.get("family") or "").strip()
        if isinstance(parameter_domains, dict) and parameter_domains:
            return "Single-asset signal strategy"
        if family:
            return f"{family} semantic backtest"
        strategy_mode = (
            str(backtester_config.get("strategy_mode") or "").strip()
            if isinstance(backtester_config, dict)
            else ""
        )
        return f"{strategy_mode} semantic backtest" if strategy_mode else "Semantic backtest"

    @classmethod
    def _render_execution_label(cls, trade_delay: Any, trade_price: Any) -> str:
        price = str(trade_price or "").strip() or "configured price"
        delay = cls._as_float(trade_delay)
        if math.isnan(delay):
            return f"Execute at {price}"
        if delay == 0:
            timing = "signal bar"
        elif delay == 1:
            timing = "next bar after signal"
        else:
            timing = f"{int(delay)} bars after signal" if delay.is_integer() else f"{delay:g} bars after signal"
        return f"{timing} at {price}"

    @staticmethod
    def _render_normalized_execution_label(execution: Any) -> str:
        if not isinstance(execution, dict):
            return ""
        timing = str(execution.get("timing") or "").strip()
        if timing == "bar_offset":
            entry_price = str(execution.get("entry_price") or execution.get("price") or "close").strip()
            exit_price = str(execution.get("exit_price") or "close").strip()
            entry_delay = execution.get("entry_delay_bars", 0)
            exit_delay = execution.get("exit_delay_bars", 0)
            if entry_price == "close" and exit_price == "open" and entry_delay == 0 and exit_delay == 1:
                return "signal bar close -> next bar open"
            return (
                f"signal + {entry_delay} bar(s) at {entry_price.replace('_', ' ')} "
                f"-> signal + {exit_delay} bar(s) at {exit_price.replace('_', ' ')}"
            )
        price = str(execution.get("entry_price") or "").strip()
        if timing and price:
            return f"{timing.replace('_', ' ')} at {price.replace('_', ' ')}"
        if timing:
            return timing.replace("_", " ")
        if price:
            return f"Execute at {price.replace('_', ' ')}"
        return ""

    @staticmethod
    def _render_execution_label_zh(summary: Any) -> str:
        if not isinstance(summary, dict):
            return ""
        label = str(summary.get("execution_label") or "").strip()
        if label == "signal bar close -> next bar open":
            return "信號當根 K 線收盤入場，下一根 K 線開盤離場"
        normalized = label.lower()
        replacements = {
            "signal bar": "信號當根 K 線",
            "next bar after signal": "信號後下一根 K 線",
            "bars after signal": "根 K 線後",
            "bar(s)": "根 K 線",
            " at open": "以開盤價執行",
            " at close": "以收盤價執行",
            "same session": "同一交易時段",
            "bar offset": "信號後延遲 K 線",
            "close": "收盤",
            "open": "開盤",
        }
        if not normalized:
            return ""
        rendered = label
        for source, target in replacements.items():
            rendered = re.sub(re.escape(source), target, rendered, flags=re.IGNORECASE)
        return rendered

    @classmethod
    def _render_cost_label(cls, transaction_cost: Any, slippage: Any) -> str:
        cost = cls._as_float(transaction_cost)
        slip = cls._as_float(slippage)
        parts: List[str] = []
        if not math.isnan(cost):
            parts.append(f"transaction cost {cost:.4g}")
        if not math.isnan(slip):
            parts.append(f"slippage {slip:.4g}")
        return "; ".join(parts)

    @classmethod
    def _render_rule_node(cls, node: Any) -> str:
        if not isinstance(node, dict):
            return str(node) if node is not None else ""
        op = str(node.get("op", "") or "").strip()
        if op in {"and", "or"}:
            rows = [cls._render_rule_node(item) for item in node.get("nodes", []) if item is not None]
            rows = [item for item in rows if item]
            joiner = " AND " if op == "and" else " OR "
            return f"({joiner.join(rows)})" if rows else op.upper()
        left = node.get("left")
        if left is None and "field" in node:
            left = node.get("field")
        right = node.get("right")
        if right is None and "right_field" in node:
            right = node.get("right_field")
        if op in {"lt", "lte", "gt", "gte", "eq", "neq"}:
            symbols = {"lt": "<", "lte": "<=", "gt": ">", "gte": ">=", "eq": "=", "neq": "!="}
            return f"{cls._render_operand(left)} {symbols[op]} {cls._render_operand(right)}"
        if op in {"cross_up", "cross_down", "crosses_above", "crosses_below"}:
            verb = "crosses above" if op in {"cross_up", "crosses_above"} else "crosses below"
            return f"{cls._render_operand(left)} {verb} {cls._render_operand(right)}"
        if op == "timer_bars":
            return f"hold for {cls._render_operand(node.get('value'))} bars"
        return json.dumps(node, ensure_ascii=False, sort_keys=True)

    @classmethod
    def _render_normalized_entry_rule(cls, signals: Any, selection: Any, rebalance: Any) -> str:
        if isinstance(signals, dict) and isinstance(signals.get("entry"), dict) and signals.get("entry"):
            rendered = cls._render_rule_node(signals.get("entry"))
            side = str(signals.get("side") or "").replace("_", " ").strip()
            if side:
                return f"{side} entry on {rendered}"
            return rendered
        if isinstance(selection, dict) and selection:
            pieces: List[str] = []
            eligible = selection.get("eligible")
            if eligible:
                pieces.append(f"eligible: {cls._render_rule_node(eligible)}")
            rank_by = selection.get("rank_by")
            top_n = selection.get("top_n")
            if rank_by and top_n not in {None, ""}:
                pieces.append(f"select top {top_n or '-'} by {rank_by or '-'}")
            elif rank_by:
                pieces.append(f"rank by {rank_by}")
            elif top_n not in {None, ""}:
                pieces.append(f"select top {top_n}")
            return "; ".join(pieces)
        if isinstance(rebalance, dict) and isinstance(rebalance.get("trigger"), dict):
            op = rebalance["trigger"].get("op")
            if op:
                return f"Rebalance on {op}"
        return ""

    @classmethod
    def _render_normalized_exit_rule(cls, signals: Any, rebalance: Any, allocation: Any) -> str:
        if isinstance(signals, dict) and isinstance(signals.get("exit"), dict) and signals.get("exit"):
            return cls._render_rule_node(signals.get("exit"))
        if isinstance(rebalance, dict) and rebalance:
            return "Replaced or resized at next rebalance"
        if isinstance(allocation, dict) and allocation.get("method"):
            return f"Target allocation by {allocation.get('method')}"
        return ""

    @staticmethod
    def _render_risk_label(risk: Any) -> str:
        if not isinstance(risk, dict) or not risk:
            return ""
        pieces: List[str] = []
        if risk.get("max_positions") is not None:
            pieces.append(f"max positions {risk.get('max_positions')}")
        if risk.get("max_gross_exposure") is not None:
            pieces.append(f"max gross {risk.get('max_gross_exposure')}")
        if risk.get("long_short"):
            pieces.append(str(risk.get("long_short")).replace("_", " "))
        return "; ".join(pieces)

    @classmethod
    def _render_operand(cls, value: Any) -> str:
        if isinstance(value, dict):
            if "field" in value:
                return str(value.get("field"))
            if "param_ref" in value:
                return f"${value.get('param_ref')}"
            if "feature" in value:
                source = str(value.get("source", "") or "").strip()
                params = value.get("params", {})
                if isinstance(params, dict) and params:
                    rendered_params = ", ".join(
                        f"{key}={cls._render_operand(param_value)}"
                        for key, param_value in sorted(params.items())
                    )
                    if source:
                        return f"{value.get('feature')}({source}, {rendered_params})"
                    return f"{value.get('feature')}({rendered_params})"
                return str(value.get("feature"))
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

    @staticmethod
    def _render_parameter_domains(domains: Any) -> str:
        if not isinstance(domains, dict) or not domains:
            return "No tunable parameter domain recorded."
        rows: List[str] = []
        for name, spec in sorted(domains.items()):
            if not isinstance(spec, dict):
                rows.append(f"{name}: {spec}")
                continue
            if spec.get("type") == "range":
                rows.append(
                    f"{name}: {spec.get('start')} to {spec.get('end')} step {spec.get('step')}"
                )
            elif spec.get("type") == "set":
                values = spec.get("values", [])
                if isinstance(values, list):
                    rows.append(f"{name}: {AppPayloadService._render_parameter_set_values(values)}")
                else:
                    rows.append(f"{name}: {values}")
            else:
                rows.append(f"{name}: {json.dumps(spec, ensure_ascii=False, sort_keys=True)}")
        return "; ".join(rows)

    @staticmethod
    def _render_parameter_set_values(values: List[Any]) -> str:
        if not values:
            return "0 values"
        numeric_values: List[float] = []
        for value in values:
            try:
                numeric_values.append(float(value))
            except (TypeError, ValueError):
                numeric_values = []
                break
        if numeric_values and len(numeric_values) == len(values):
            unique_values = sorted(set(numeric_values))
            if len(unique_values) == 1:
                return AppPayloadService._format_parameter_value(unique_values[0])
            steps = [
                round(unique_values[index] - unique_values[index - 1], 12)
                for index in range(1, len(unique_values))
            ]
            if steps and len(set(steps)) == 1:
                return (
                    f"{AppPayloadService._format_parameter_value(unique_values[0])} to "
                    f"{AppPayloadService._format_parameter_value(unique_values[-1])} step "
                    f"{AppPayloadService._format_parameter_value(steps[0])}"
                )
            if len(unique_values) <= 5:
                return ", ".join(AppPayloadService._format_parameter_value(value) for value in unique_values)
            return (
                f"{len(unique_values)} values "
                f"({AppPayloadService._format_parameter_value(unique_values[0])} to "
                f"{AppPayloadService._format_parameter_value(unique_values[-1])})"
            )
        text_values = [str(value) for value in values]
        if len(text_values) <= 5 and all(len(value) <= 32 for value in text_values):
            return ", ".join(text_values)
        return f"{len(text_values)} values"

    @staticmethod
    def _format_parameter_value(value: float) -> str:
        return str(int(value)) if float(value).is_integer() else f"{value:g}"

    @staticmethod
    def _position_side(position_size: Any) -> str:
        size = AppPayloadService._as_float(position_size)
        if math.isnan(size) or size == 0:
            return "flat"
        return "long" if size > 0 else "short"

    @staticmethod
    def _to_iso(value: Any) -> Optional[str]:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if hasattr(value, "isoformat"):
            try:
                return value.isoformat()
            except Exception:
                return str(value)
        return str(value)

    @staticmethod
    def _as_float(value: Any) -> float:
        if value is None:
            return float("nan")
        try:
            return float(value)
        except Exception:
            return float("nan")

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return default

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            AppPayloadService._json_cache_safe_value(payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        path.write_bytes(encoded)
        path.with_suffix(f"{path.suffix}.gz").write_bytes(
            gzip.compress(encoded, compresslevel=5)
        )

    @staticmethod
    def _json_cache_safe_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): AppPayloadService._json_cache_safe_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [AppPayloadService._json_cache_safe_value(item) for item in value]
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            numeric = float(value)
            return numeric if math.isfinite(numeric) else None
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        try:
            if value is not None and pd.isna(value):
                return None
        except (TypeError, ValueError):
            return value
        return value

    @staticmethod
    def ensure_precompressed_json(path: Path) -> Path:
        gzip_path = path.with_suffix(f"{path.suffix}.gz")
        if (
            not gzip_path.exists()
            or gzip_path.stat().st_mtime < path.stat().st_mtime
        ):
            gzip_path.write_bytes(gzip.compress(path.read_bytes(), compresslevel=5))
        return gzip_path

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")
