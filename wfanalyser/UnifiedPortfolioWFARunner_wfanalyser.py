"""Unified WFA runner for portfolio-accounting strategies.

This runner is deliberately backend-only.  It executes the selected-optimum WFA
contract against MultiAssetPortfolioEngineBacktester:

1. enumerate candidate policies from strategy parameter domains;
2. run every candidate inside the IS/train window;
3. select rank 1 by objective;
4. run only that selected policy on the paired OOS/test window.
"""

from __future__ import annotations

from dataclasses import dataclass
import copy
import itertools
import json
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from backtester.MultiAssetPortfolioEngine_backtester import MultiAssetPortfolioEngineBacktester


@dataclass
class UnifiedPortfolioWFAResult:
    selected_optimum: pd.DataFrame
    candidate_diagnostics: pd.DataFrame
    window_backtests: List[Dict[str, Any]]
    metadata: Dict[str, Any]


class UnifiedPortfolioWFARunner:
    """Run walk-forward optimization using the unified portfolio engine."""

    def __init__(
        self,
        *,
        market_data: Dict[str, pd.DataFrame],
        strategy_config: Dict[str, Any],
        wfa_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.market_data = {str(key).lower(): value.copy() for key, value in (market_data or {}).items()}
        if "close" not in self.market_data:
            raise KeyError("UnifiedPortfolioWFARunner requires market_data['close']")
        self.strategy_config = copy.deepcopy(strategy_config or {})
        self.wfa_config = copy.deepcopy(wfa_config or {})
        self.objectives = self._objectives()
        self.selection_constraints = self._resolve_selection_constraints()
        self._last_windowing_metadata: Dict[str, Any] = {}

    def run(self) -> UnifiedPortfolioWFAResult:
        windows = self._windows()
        all_candidates = self._candidate_configs()
        candidates = self._apply_candidate_budget(all_candidates)
        budget_metadata = self._candidate_budget_metadata(all_candidates, candidates)
        workflow = "walk_forward_analysis" if len(all_candidates) > 1 else "rolling_validation"
        selected_rows: List[Dict[str, Any]] = []
        diagnostic_rows: List[Dict[str, Any]] = []
        window_backtests: List[Dict[str, Any]] = []

        for window_id, window in enumerate(windows, start=1):
            train_data = self._slice_market_data(window["train_start"], window["train_end"])
            test_data = self._slice_market_data(window["test_start"], window["test_end"])
            if train_data["close"].empty or test_data["close"].empty:
                continue

            train_results: List[Dict[str, Any]] = []
            for candidate in candidates:
                train_result = MultiAssetPortfolioEngineBacktester(
                    market_data=train_data,
                    config=candidate["config"],
                ).run()
                metrics = self._metrics(train_result.equity_curve)
                viability = self._candidate_viability(
                    candidate=candidate,
                    train_result=train_result,
                    train_size=len(train_data["close"].index),
                )
                train_results.append(
                    {
                        "candidate": candidate,
                        "train_result": train_result,
                        "metrics": metrics,
                        "viability": viability,
                    }
                )
                diagnostic_rows.append(
                    self._candidate_row(
                        window_id=window_id,
                        window=window,
                        candidate=candidate,
                        metrics=metrics,
                        viability=viability,
                    )
                )

            for objective in self.objectives:
                selected = self._select_candidate(train_results, objective)
                test_result = MultiAssetPortfolioEngineBacktester(
                    market_data=test_data,
                    config=selected["candidate"]["config"],
                ).run()
                backtest_id = self._window_backtest_id(
                    window_id=window_id,
                    objective=objective,
                    params=selected["candidate"]["params"],
                )
                self._tag_window_backtest_result(
                    test_result,
                    backtest_id=backtest_id,
                    window_id=window_id,
                    objective=objective,
                    window=window,
                    params=selected["candidate"]["params"],
                )
                oos_metrics = self._metrics(test_result.equity_curve)
                selected_rows.append(
                    self._selected_row(
                        window_id=window_id,
                        window=window,
                        objective=objective,
                        selected=selected,
                        test_result=test_result,
                        oos_metrics=oos_metrics,
                        candidate_count=len(candidates),
                        total_candidate_count=len(all_candidates),
                        candidate_budget_metadata=budget_metadata,
                        workflow=workflow,
                    )
                )
                window_backtests.append(
                    {
                        "window_id": window_id,
                        "objective": objective,
                        "backtest_id": backtest_id,
                        "params": selected["candidate"]["params"],
                        "is_equity_curve": selected["train_result"].equity_curve,
                        "oos_equity_curve": test_result.equity_curve,
                        "oos_portfolio_snapshot": self._portfolio_snapshot(test_result),
                        "oos_result": test_result,
                    }
                )

        selected_frame = pd.DataFrame(selected_rows)
        diagnostic_frame = pd.DataFrame(diagnostic_rows)
        return UnifiedPortfolioWFAResult(
            selected_optimum=selected_frame,
            candidate_diagnostics=diagnostic_frame,
            window_backtests=window_backtests,
            metadata={
                "schema_version": "unified_portfolio_wfa_result.v1",
                "workflow": workflow,
                "row_contract": "selected_optimum_per_window",
                "objectives": self.objectives,
                "candidate_count": len(candidates),
                "total_candidate_count": len(all_candidates),
                **budget_metadata,
                "windowing": self._last_windowing_metadata,
                "selection_constraints": self.selection_constraints,
                "window_count": len(windows),
                "diagnostic_artifacts": ["candidate_diagnostics"],
                "legacy_grid_detected": False,
            },
        )

    def _candidate_configs(self) -> List[Dict[str, Any]]:
        domains = self.strategy_config.get("parameter_domains", {})
        if not isinstance(domains, dict) or not domains:
            return [{"config": copy.deepcopy(self.strategy_config), "params": {}}]
        keys = list(domains.keys())
        values = [self._domain_values(domains[key]) for key in keys]
        candidates: List[Dict[str, Any]] = []
        for combo in itertools.product(*values):
            params = dict(zip(keys, combo))
            variant = self._replace_param_refs(copy.deepcopy(self.strategy_config), params)
            variant["resolved_params"] = params
            base_id = str(self.strategy_config.get("strategy_id") or "unified_portfolio_wfa")
            suffix = "_".join(f"{key}_{self._slug(value)}" for key, value in params.items())
            variant["strategy_id"] = f"{base_id}_{suffix}" if suffix else base_id
            candidates.append({"config": variant, "params": params})
        return candidates

    def _apply_candidate_budget(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        optimizer = self.wfa_config.get("optimizer", {}) if isinstance(self.wfa_config.get("optimizer"), dict) else {}
        raw_budget = self._first_present(
            optimizer.get("max_candidates"),
            optimizer.get("candidate_limit"),
            optimizer.get("n_trials"),
            self.wfa_config.get("max_candidates"),
            self.wfa_config.get("candidate_limit"),
        )
        budget = self._positive_int(raw_budget, default=0)
        if budget <= 0 or budget >= len(candidates):
            return candidates
        seed = self._nonnegative_int(
            self._first_present(optimizer.get("random_seed"), self.wfa_config.get("random_seed")),
            default=42,
        )
        rng = np.random.default_rng(seed)
        selected_indices = sorted(rng.choice(len(candidates), size=budget, replace=False).tolist())
        return [candidates[index] for index in selected_indices]

    def _candidate_budget_metadata(
        self,
        all_candidates: List[Dict[str, Any]],
        candidates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        optimizer = self.wfa_config.get("optimizer", {}) if isinstance(self.wfa_config.get("optimizer"), dict) else {}
        raw_budget = self._first_present(
            optimizer.get("max_candidates"),
            optimizer.get("candidate_limit"),
            optimizer.get("n_trials"),
            self.wfa_config.get("max_candidates"),
            self.wfa_config.get("candidate_limit"),
        )
        budget = self._positive_int(raw_budget, default=0)
        applied = bool(0 < budget < len(all_candidates))
        seed = self._nonnegative_int(
            self._first_present(optimizer.get("random_seed"), self.wfa_config.get("random_seed")),
            default=42,
        )
        method = "seeded_random_sample" if applied else "full_grid"
        return {
            "candidate_budget": budget if budget > 0 else None,
            "candidate_budget_applied": applied,
            "candidate_budget_policy": method,
            "candidate_budget_method": method,
            "candidate_budget_seed": seed if applied else None,
        }

    def _resolve_selection_constraints(self) -> Dict[str, Any]:
        optimizer = self.wfa_config.get("optimizer", {}) if isinstance(self.wfa_config.get("optimizer"), dict) else {}
        raw = optimizer.get("selection_constraints")
        if not isinstance(raw, dict):
            raw = self.wfa_config.get("selection_constraints")
        if not isinstance(raw, dict):
            raw = {}
        enabled = (
            self._bool_config(raw.get("enabled"), default=False)
            if "enabled" in raw
            else bool(raw)
        )
        return {
            "enabled": enabled,
            "min_is_active_rebalances": self._positive_int(raw.get("min_is_active_rebalances"), default=0),
            "min_is_exposure_ratio": self._nonnegative_float(raw.get("min_is_exposure_ratio"), default=0.0),
            "min_is_nonzero_return_days": self._positive_int(raw.get("min_is_nonzero_return_days"), default=0),
            "max_lookback_fraction_of_train": self._nonnegative_float(
                raw.get("max_lookback_fraction_of_train", raw.get("max_lookback_fraction")),
                default=0.0,
            ),
        }

    def _candidate_selection_pool(
        self,
        train_results: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        constraints_applied = bool(self.selection_constraints.get("enabled"))
        if not constraints_applied:
            return list(train_results), {
                "constraints_applied": False,
                "fallback": False,
                "pool_count": len(train_results),
                "total_count": len(train_results),
            }
        passing = [
            item
            for item in train_results
            if bool((item.get("viability") or {}).get("passed", True))
        ]
        if passing:
            return passing, {
                "constraints_applied": True,
                "fallback": False,
                "pool_count": len(passing),
                "total_count": len(train_results),
            }
        return list(train_results), {
            "constraints_applied": True,
            "fallback": True,
            "pool_count": len(train_results),
            "total_count": len(train_results),
        }

    def _candidate_viability(
        self,
        *,
        candidate: Dict[str, Any],
        train_result: Any,
        train_size: int,
    ) -> Dict[str, Any]:
        snapshot = self._portfolio_snapshot(train_result)
        equity_curve = getattr(train_result, "equity_curve", pd.DataFrame())
        active_rebalances = int(snapshot.get("active_rebalance_count") or 0)
        exposure_ratio = self._exposure_ratio(equity_curve)
        nonzero_return_days = self._nonzero_return_days(equity_curve)
        max_lookback = self._strategy_max_lookback_days(candidate.get("config", {}), candidate.get("params", {}))
        reasons: List[str] = []
        if not bool(self.selection_constraints.get("enabled")):
            return {
                "passed": True,
                "reasons": ["selection_constraints_disabled"],
                "active_rebalance_count": active_rebalances,
                "exposure_ratio": exposure_ratio,
                "nonzero_return_days": nonzero_return_days,
                "max_lookback": max_lookback,
            }

        min_active = int(self.selection_constraints.get("min_is_active_rebalances") or 0)
        min_exposure = float(self.selection_constraints.get("min_is_exposure_ratio") or 0.0)
        min_nonzero = int(self.selection_constraints.get("min_is_nonzero_return_days") or 0)
        max_fraction = float(self.selection_constraints.get("max_lookback_fraction_of_train") or 0.0)
        if min_active > 0 and active_rebalances < min_active:
            reasons.append(f"is_active_rebalances_below_{min_active}")
        if min_exposure > 0.0 and exposure_ratio < min_exposure:
            reasons.append(f"is_exposure_ratio_below_{min_exposure:g}")
        if min_nonzero > 0 and nonzero_return_days < min_nonzero:
            reasons.append(f"is_nonzero_return_days_below_{min_nonzero}")
        if max_fraction > 0.0 and train_size > 0 and max_lookback / train_size > max_fraction:
            reasons.append(f"lookback_fraction_above_{max_fraction:g}")
        return {
            "passed": not reasons,
            "reasons": reasons or ["meets_selection_constraints"],
            "active_rebalance_count": active_rebalances,
            "exposure_ratio": exposure_ratio,
            "nonzero_return_days": nonzero_return_days,
            "max_lookback": max_lookback,
        }

    def _windows(self) -> List[Dict[str, pd.Timestamp]]:
        close_index = pd.to_datetime(self.market_data["close"].index).tz_localize(None).normalize()
        close_index = pd.DatetimeIndex(sorted(close_index.unique()))
        windowing = self.wfa_config.get("windowing", {}) if isinstance(self.wfa_config.get("windowing"), dict) else {}
        total = len(close_index)
        target_count = self._positive_int(windowing.get("target_window_count"), default=10)
        train_size_input = self._positive_int(windowing.get("train_size"), default=0)
        test_size_input = self._positive_int(windowing.get("test_size"), default=0)
        step_size_input = self._positive_int(windowing.get("step_size"), default=0)
        train_ratio = self._ratio_or_none(windowing.get("train_ratio"))
        test_ratio = self._ratio_or_none(windowing.get("test_ratio"))
        requested_mode = str(
            windowing.get("size_mode")
            or windowing.get("window_size_mode")
            or windowing.get("sizing")
            or ""
        ).strip().lower()
        strategy_lookback = self._strategy_max_lookback_days(self.strategy_config)

        if requested_mode in {"fixed", "manual", "manual_size", "input", "number", "numbers"}:
            sizing_mode = "manual_size" if train_size_input > 0 and test_size_input > 0 else "manual_ratio"
        elif requested_mode in {"ratio", "manual_ratio", "ratios"}:
            sizing_mode = "manual_ratio"
        elif requested_mode in {"auto", "adaptive"}:
            sizing_mode = "auto"
        elif train_size_input > 0 and test_size_input > 0:
            sizing_mode = "manual_size"
        elif train_ratio is not None and test_ratio is not None:
            sizing_mode = "manual_ratio"
        else:
            sizing_mode = "auto"

        auto_indicators: Dict[str, Any] = {
            "total_observations": total,
            "target_window_count": target_count,
            "train_ratio_hint": train_ratio if train_ratio is not None else 0.6,
            "test_ratio_hint": test_ratio if test_ratio is not None else 0.2,
            "strategy_max_lookback": strategy_lookback,
        }

        if sizing_mode == "manual_size" and train_size_input > 0 and test_size_input > 0:
            train_size = train_size_input
            test_size = test_size_input
            step_size = step_size_input or test_size
            sizing_source = "input_numbers"
        elif sizing_mode == "manual_ratio" and train_ratio is not None and test_ratio is not None and train_ratio + test_ratio <= 1.0:
            train_size = max(1, int(round(total * train_ratio)))
            test_size = max(1, int(round(total * test_ratio)))
            step_size = step_size_input or test_size
            sizing_source = "input_ratios"
        else:
            sizing_mode = "auto"
            sizing_source = "auto"
            ratio_train = train_ratio if train_ratio is not None else 0.6
            ratio_test = test_ratio if test_ratio is not None else 0.2
            ratio_factor = max(ratio_train / max(ratio_test, 1e-9), 1.0)
            test_size = max(1, int(total // max(target_count + ratio_factor, 2.0)))
            train_size = max(test_size, int(round(test_size * ratio_factor)))
            min_train_size = max(test_size, strategy_lookback * 2 if strategy_lookback > 0 else test_size)
            if train_size < min_train_size and min_train_size + test_size <= total:
                train_size = min_train_size
            step_size = step_size_input or test_size
            auto_indicators["ratio_factor"] = ratio_factor
            auto_indicators["min_train_size"] = min_train_size
            auto_indicators["step_size_source"] = "input" if step_size_input else "test_size"

        if train_size + test_size > total and total >= 2:
            test_size = max(1, min(test_size, total // 4 or 1))
            train_size = max(1, total - test_size)
            step_size = min(step_size, test_size) if step_size > 0 else test_size

        windows: List[Dict[str, pd.Timestamp]] = []
        start = 0
        while start + train_size + test_size <= len(close_index):
            train_start = close_index[start]
            train_end = close_index[start + train_size - 1]
            test_start = close_index[start + train_size]
            test_end = close_index[start + train_size + test_size - 1]
            windows.append(
                {
                    "train_start": train_start,
                    "train_end": train_end,
                    "test_start": test_start,
                    "test_end": test_end,
                }
            )
            start += step_size
        self._last_windowing_metadata = {
            "size_mode": sizing_mode,
            "sizing_source": sizing_source,
            "requested_size_mode": requested_mode or None,
            "effective_train_size": train_size,
            "effective_test_size": test_size,
            "effective_step_size": step_size,
            "target_window_count": target_count,
            "actual_window_count": len(windows),
            "total_observations": total,
            "requested_train_size": train_size_input or None,
            "requested_test_size": test_size_input or None,
            "requested_step_size": step_size_input or None,
            "requested_train_ratio": train_ratio,
            "requested_test_ratio": test_ratio,
            "strategy_max_lookback": strategy_lookback,
            "auto_indicators": auto_indicators,
        }
        return windows

    def _slice_market_data(self, start: pd.Timestamp, end: pd.Timestamp) -> Dict[str, pd.DataFrame]:
        sliced: Dict[str, pd.DataFrame] = {}
        for key, frame in self.market_data.items():
            normalized = frame.copy()
            normalized.index = pd.to_datetime(normalized.index).tz_localize(None).normalize()
            mask = (normalized.index >= start) & (normalized.index <= end)
            sliced[key] = normalized.loc[mask].copy()
        return sliced

    def _select_candidate(self, train_results: List[Dict[str, Any]], objective: str) -> Dict[str, Any]:
        metric_key = self._objective_metric_key(objective)
        selection_pool, pool_metadata = self._candidate_selection_pool(train_results)
        ranked = sorted(
            selection_pool,
            key=lambda item: float(item["metrics"].get(metric_key, -np.inf)),
            reverse=True,
        )
        selected = dict(ranked[0])
        selected["selection_pool_metadata"] = pool_metadata
        return selected

    def _selected_row(
        self,
        *,
        window_id: int,
        window: Dict[str, pd.Timestamp],
        objective: str,
        selected: Dict[str, Any],
        test_result: Any,
        oos_metrics: Dict[str, float],
        candidate_count: int,
        total_candidate_count: int,
        candidate_budget_metadata: Dict[str, Any],
        workflow: str,
    ) -> Dict[str, Any]:
        train_metrics = selected["metrics"]
        params = selected["candidate"]["params"]
        viability = selected.get("viability", {}) if isinstance(selected.get("viability"), dict) else {}
        pool_metadata = (
            selected.get("selection_pool_metadata", {})
            if isinstance(selected.get("selection_pool_metadata"), dict)
            else {}
        )
        semantic_combo = self._semantic_combo(params)
        objective_label = self._objective_label(objective)
        selection_constraints_fallback = bool(pool_metadata.get("fallback", False))
        candidate_budget_applied = bool(candidate_budget_metadata.get("candidate_budget_applied", False))
        acceptance = self._acceptance(
            objective=objective,
            train_metrics=train_metrics,
            oos_metrics=oos_metrics,
            selection_constraints_fallback=selection_constraints_fallback,
        )
        train_risk_gate_summary = self._risk_gate_summary(selected.get("train_result"))
        oos_risk_gate_summary = self._risk_gate_summary(test_result)
        portfolio_snapshot = self._portfolio_snapshot(test_result)
        return {
            "window_id": window_id,
            "objective": objective,
            "semantic_combo": semantic_combo,
            "params_json": json.dumps(params, sort_keys=True, ensure_ascii=True),
            "train_start": window["train_start"],
            "train_end": window["train_end"],
            "test_start": window["test_start"],
            "test_end": window["test_end"],
            "is_sharpe": train_metrics.get("sharpe"),
            "is_calmar": train_metrics.get("calmar"),
            "is_total_return": train_metrics.get("total_return"),
            "oos_sharpe": oos_metrics.get("sharpe"),
            "oos_calmar": oos_metrics.get("calmar"),
            "oos_total_return": oos_metrics.get("total_return"),
            "oos_is_ratio": acceptance["oos_is_ratio"],
            "selection_source": "unified_portfolio_wfa",
            "selection_rank": 1,
            "selection_metric": objective,
            "selection_evidence": self._selection_evidence(
                objective_label=objective_label,
                candidate_count=candidate_count,
                total_candidate_count=total_candidate_count,
                candidate_budget_applied=candidate_budget_applied,
                selection_pool_count=pool_metadata.get("pool_count"),
                selection_constraints_applied=pool_metadata.get("constraints_applied"),
            ),
            "candidate_count": candidate_count,
            "total_candidate_count": total_candidate_count,
            "candidate_budget_applied": candidate_budget_applied,
            "candidate_budget": candidate_budget_metadata.get("candidate_budget"),
            "candidate_budget_policy": candidate_budget_metadata.get("candidate_budget_policy"),
            "candidate_budget_method": candidate_budget_metadata.get("candidate_budget_method"),
            "candidate_budget_seed": candidate_budget_metadata.get("candidate_budget_seed"),
            "selection_pool_count": pool_metadata.get("pool_count"),
            "selection_pool_total_count": pool_metadata.get("total_count"),
            "selection_constraints_applied": pool_metadata.get("constraints_applied", False),
            "selection_constraints_fallback": selection_constraints_fallback,
            "candidate_viability_pass": viability.get("passed", True),
            "candidate_viability_reasons": "; ".join(viability.get("reasons", [])),
            "is_active_rebalance_count": viability.get("active_rebalance_count"),
            "is_exposure_ratio": viability.get("exposure_ratio"),
            "is_nonzero_return_days": viability.get("nonzero_return_days"),
            "candidate_max_lookback": viability.get("max_lookback"),
            "oos_portfolio_json": json.dumps(portfolio_snapshot, sort_keys=True, ensure_ascii=True),
            "is_risk_gate_event_count": train_risk_gate_summary.get("event_count", 0),
            "oos_risk_gate_event_count": oos_risk_gate_summary.get("event_count", 0),
            "oos_risk_gate_summary_json": json.dumps(
                oos_risk_gate_summary,
                sort_keys=True,
                ensure_ascii=True,
            ),
            "accepted": acceptance["accepted"],
            "review_status": acceptance["review_status"],
            "acceptance_reasons": "; ".join(acceptance["reasons"]),
            "wfa_row_type": "selected_optimum",
            "workflow": workflow,
        }

    @staticmethod
    def _selection_evidence(
        *,
        objective_label: str,
        candidate_count: int,
        total_candidate_count: int,
        candidate_budget_applied: bool,
        selection_pool_count: Any = None,
        selection_constraints_applied: Any = None,
    ) -> str:
        pool_count = UnifiedPortfolioWFARunner._positive_int(selection_pool_count, default=0)
        if bool(selection_constraints_applied) and pool_count > 0 and pool_count < candidate_count:
            base = f"rank=1 by IS {objective_label} among {pool_count}/{candidate_count} viable candidates"
            if candidate_budget_applied:
                return f"{base} from sampled {candidate_count}/{total_candidate_count} candidates"
            return base
        if candidate_budget_applied:
            return (
                f"rank=1 by IS {objective_label} "
                f"among sampled {candidate_count}/{total_candidate_count} candidates"
            )
        return f"rank=1 by IS {objective_label}"

    def _portfolio_snapshot(self, result: Any) -> Dict[str, Any]:
        equity_curve = getattr(result, "equity_curve", pd.DataFrame())
        rebalance_audit = getattr(result, "rebalance_audit", pd.DataFrame())
        risk_gate_summary = self._risk_gate_summary(result)
        if not isinstance(equity_curve, pd.DataFrame) or equity_curve.empty:
            return {
                "asset_count": 0,
                "allocation": [],
                "contribution": [],
                "active_rebalance_count": 0,
                "checkpoint_count": 0,
                "risk_gate_event_count": risk_gate_summary.get("event_count", 0),
                "risk_gate_summary": risk_gate_summary,
            }

        weight_cols = [str(col) for col in equity_curve.columns if str(col).startswith("Weight_")]
        contribution_cols = [str(col) for col in equity_curve.columns if str(col).startswith("Contribution_")]
        allocation: List[Dict[str, Any]] = []
        for col in weight_cols:
            asset = col.removeprefix("Weight_")
            weights = pd.to_numeric(equity_curve[col], errors="coerce").fillna(0.0)
            last_weight = float(weights.iloc[-1]) if not weights.empty else 0.0
            avg_weight = float(weights.mean()) if not weights.empty else 0.0
            active_days = int((weights.abs() > 1e-12).sum())
            if active_days or abs(last_weight) > 1e-12 or abs(avg_weight) > 1e-12:
                allocation.append(
                    {
                        "asset": asset,
                        "avg_weight": self._finite_float(avg_weight),
                        "last_weight": self._finite_float(last_weight),
                        "active_days": active_days,
                    }
                )

        contribution: List[Dict[str, Any]] = []
        for col in contribution_cols:
            asset = col.removeprefix("Contribution_")
            values = pd.to_numeric(equity_curve[col], errors="coerce").fillna(0.0)
            total = float(values.sum()) if not values.empty else 0.0
            avg_weight = next((item["avg_weight"] for item in allocation if item["asset"] == asset), 0.0)
            if abs(total) > 1e-12 or (avg_weight is not None and abs(float(avg_weight)) > 1e-12):
                contribution.append(
                    {
                        "asset": asset,
                        "return_contribution": self._finite_float(total),
                        "avg_weight": self._finite_float(avg_weight),
                    }
                )

        turnover = pd.to_numeric(equity_curve.get("Turnover", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        trade_cost = pd.to_numeric(equity_curve.get("Trade_cost", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        gross_exposure = pd.to_numeric(
            equity_curve.get("Gross_exposure", pd.Series(dtype=float)),
            errors="coerce",
        ).replace([np.inf, -np.inf], np.nan)
        selected_count = pd.to_numeric(
            equity_curve.get("Selected_count", pd.Series(dtype=float)),
            errors="coerce",
        ).replace([np.inf, -np.inf], np.nan)
        equity_values = pd.to_numeric(equity_curve.get("Equity_value", pd.Series(dtype=float)), errors="coerce").dropna()
        start_equity = float(equity_values.iloc[0]) if not equity_values.empty else 0.0
        total_trade_cost = float(trade_cost.sum()) if not trade_cost.empty else 0.0
        active_turnover = turnover[turnover.abs() > 1e-12]
        allocation.sort(key=lambda item: abs(float(item.get("avg_weight") or 0.0)), reverse=True)
        contribution.sort(key=lambda item: abs(float(item.get("return_contribution") or 0.0)), reverse=True)
        return {
            "asset_count": len(weight_cols),
            "allocation": allocation,
            "contribution": contribution,
            "active_rebalance_count": int((turnover.abs() > 1e-12).sum()),
            "checkpoint_count": int(len(rebalance_audit)) if isinstance(rebalance_audit, pd.DataFrame) else 0,
            "avg_exposure": self._finite_float(float(gross_exposure.mean())) if not gross_exposure.dropna().empty else None,
            "avg_holdings": self._finite_float(float(selected_count.mean())) if not selected_count.dropna().empty else None,
            "avg_turnover": self._finite_float(float(active_turnover.mean())) if not active_turnover.empty else 0.0,
            "total_turnover": self._finite_float(float(turnover.abs().sum())) if not turnover.empty else 0.0,
            "total_trade_cost": self._finite_float(total_trade_cost),
            "cost_drag": self._finite_float(total_trade_cost / start_equity) if start_equity else None,
            "risk_gate_event_count": risk_gate_summary.get("event_count", 0),
            "risk_gate_summary": risk_gate_summary,
        }

    @staticmethod
    def _finite_float(value: Any) -> Optional[float]:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if np.isfinite(parsed) else None

    @staticmethod
    def _risk_gate_summary(result: Any) -> Dict[str, Any]:
        if result is None:
            return {
                "schema_version": "risk_gate_summary.v1",
                "event_count": 0,
                "gates_triggered": [],
            }
        validation_report = getattr(result, "validation_report", {})
        if isinstance(validation_report, dict):
            summary = validation_report.get("risk_gate_summary")
            if isinstance(summary, dict):
                return UnifiedPortfolioWFARunner._json_safe(summary)
        events = getattr(result, "risk_gate_events", pd.DataFrame())
        event_count = int(len(events)) if isinstance(events, pd.DataFrame) else 0
        return {
            "schema_version": "risk_gate_summary.v1",
            "event_count": event_count,
            "gates_triggered": [],
        }

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): UnifiedPortfolioWFARunner._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [UnifiedPortfolioWFARunner._json_safe(item) for item in value]
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, float):
            return value if np.isfinite(value) else None
        return value

    def _candidate_row(
        self,
        *,
        window_id: int,
        window: Dict[str, pd.Timestamp],
        candidate: Dict[str, Any],
        metrics: Dict[str, float],
        viability: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params = candidate["params"]
        viability = viability or {}
        return {
            "window_id": window_id,
            "semantic_combo": self._semantic_combo(params),
            "params_json": json.dumps(params, sort_keys=True, ensure_ascii=True),
            "train_start": window["train_start"],
            "train_end": window["train_end"],
            "is_sharpe": metrics.get("sharpe"),
            "is_calmar": metrics.get("calmar"),
            "is_total_return": metrics.get("total_return"),
            "candidate_viability_pass": viability.get("passed", True),
            "candidate_viability_reasons": "; ".join(viability.get("reasons", [])),
            "is_active_rebalance_count": viability.get("active_rebalance_count"),
            "is_exposure_ratio": viability.get("exposure_ratio"),
            "is_nonzero_return_days": viability.get("nonzero_return_days"),
            "candidate_max_lookback": viability.get("max_lookback"),
            "wfa_row_type": "candidate_diagnostic",
        }

    @staticmethod
    def _metrics(equity_curve: pd.DataFrame) -> Dict[str, float]:
        if equity_curve.empty or "Equity_value" not in equity_curve.columns:
            return {"total_return": 0.0, "sharpe": 0.0, "calmar": 0.0, "max_drawdown": 0.0}
        equity = pd.to_numeric(equity_curve["Equity_value"], errors="coerce").dropna()
        if equity.empty:
            return {"total_return": 0.0, "sharpe": 0.0, "calmar": 0.0, "max_drawdown": 0.0}
        returns = equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0) if equity.iloc[0] else 0.0
        std = float(returns.std(ddof=0)) if not returns.empty else 0.0
        sharpe = float(returns.mean() / std * np.sqrt(252.0)) if std > 0.0 else 0.0
        drawdown = equity / equity.cummax() - 1.0
        max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0
        years = max(len(equity) / 252.0, 1.0 / 252.0)
        cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0) if equity.iloc[0] else 0.0
        calmar = float(cagr / abs(max_drawdown)) if max_drawdown < 0.0 else 0.0
        return {
            "total_return": total_return,
            "sharpe": sharpe,
            "calmar": calmar,
            "max_drawdown": max_drawdown,
        }

    def _objectives(self) -> List[str]:
        optimizer = self.wfa_config.get("optimizer", {}) if isinstance(self.wfa_config.get("optimizer"), dict) else {}
        objectives = optimizer.get("objectives", self.wfa_config.get("objectives", ["sharpe"]))
        if isinstance(objectives, str):
            objectives = [objectives]
        out = [str(item).strip().lower() for item in objectives if str(item).strip()]
        return out or ["sharpe"]

    def _acceptance(
        self,
        *,
        objective: str,
        train_metrics: Dict[str, float],
        oos_metrics: Dict[str, float],
        selection_constraints_fallback: bool = False,
    ) -> Dict[str, Any]:
        acceptance_cfg = (
            self.wfa_config.get("acceptance", {})
            if isinstance(self.wfa_config.get("acceptance"), dict)
            else {}
        )
        metric_key = self._objective_metric_key(objective)
        is_value = float(train_metrics.get(metric_key, 0.0) or 0.0)
        oos_value = float(oos_metrics.get(metric_key, 0.0) or 0.0)
        ratio = np.nan
        if is_value > 0.0 and oos_value > 0.0:
            ratio = oos_value / is_value

        reasons: List[str] = []
        accepted = True
        if metric_key == "sharpe":
            min_oos = float(acceptance_cfg.get("min_oos_sharpe", 0.0))
            if oos_value <= min_oos:
                accepted = False
                reasons.append(f"OOS Sharpe <= {min_oos:g}")
        elif metric_key == "calmar":
            min_oos = float(acceptance_cfg.get("min_oos_calmar", 0.0))
            if oos_value <= min_oos:
                accepted = False
                reasons.append(f"OOS Calmar <= {min_oos:g}")
        elif self._bool_config(acceptance_cfg.get("require_positive_oos"), default=True) and oos_value <= 0.0:
            accepted = False
            reasons.append("OOS metric <= 0")

        min_ratio = float(acceptance_cfg.get("min_oos_is_ratio", 0.7))
        if is_value > 0.0 and oos_value > 0.0 and ratio < min_ratio:
            accepted = False
            reasons.append(f"OOS/IS ratio < {min_ratio:g}")
        elif not (is_value > 0.0 and oos_value > 0.0):
            reasons.append("OOS/IS ratio diagnostic only")

        allow_fallback_acceptance = self._bool_config(
            acceptance_cfg.get("allow_selection_constraints_fallback_acceptance"),
            default=False,
        )
        if selection_constraints_fallback and not allow_fallback_acceptance:
            accepted = False
            reasons.append("selection_constraints_fallback requires explicit acceptance opt-in")
        elif selection_constraints_fallback:
            reasons.append("selection_constraints_fallback explicitly allowed")

        return {
            "accepted": bool(accepted),
            "review_status": "Pass" if accepted else "Review",
            "oos_is_ratio": float(ratio) if pd.notna(ratio) else np.nan,
            "reasons": reasons or ["accepted"],
        }

    @staticmethod
    def _objective_metric_key(objective: str) -> str:
        objective = str(objective).lower()
        if "calmar" in objective:
            return "calmar"
        if "return" in objective:
            return "total_return"
        return "sharpe"

    @staticmethod
    def _objective_label(objective: str) -> str:
        objective = str(objective).lower()
        if "calmar" in objective:
            return "Calmar"
        if "return" in objective:
            return "Total Return"
        return "Sharpe"

    @staticmethod
    def _domain_values(spec: Any) -> List[Any]:
        if isinstance(spec, list):
            return list(spec)
        if isinstance(spec, dict):
            if isinstance(spec.get("values"), list):
                return list(spec["values"])
            if spec.get("type") == "range":
                start = int(spec.get("start", 0))
                end = int(spec.get("end", start))
                step = int(spec.get("step", 1)) or 1
                return list(range(start, end + (1 if step > 0 else -1), step))
        return [spec]

    def _replace_param_refs(self, value: Any, params: Dict[str, Any]) -> Any:
        if isinstance(value, dict):
            if set(value.keys()) == {"param_ref"}:
                key = str(value.get("param_ref"))
                return params.get(key, value)
            return {key: self._replace_param_refs(item, params) for key, item in value.items()}
        if isinstance(value, list):
            return [self._replace_param_refs(item, params) for item in value]
        return value

    @staticmethod
    def _semantic_combo(params: Dict[str, Any]) -> str:
        if not params:
            return "fixed_policy"
        return " | ".join(f"{key}={value}" for key, value in params.items())

    @classmethod
    def _window_backtest_id(
        cls,
        *,
        window_id: int,
        objective: str,
        params: Dict[str, Any],
    ) -> str:
        combo_slug = "_".join(
            f"{cls._slug(key)}_{cls._slug(value)}" for key, value in (params or {}).items()
        )
        if not combo_slug:
            combo_slug = "fixed_policy"
        return f"wfa_window_{int(window_id):03d}_{cls._slug(objective)}_{combo_slug}"

    @staticmethod
    def _tag_window_backtest_result(
        result: Any,
        *,
        backtest_id: str,
        window_id: int,
        objective: str,
        window: Dict[str, pd.Timestamp],
        params: Dict[str, Any],
    ) -> None:
        if result is None:
            return
        result.strategy_id = backtest_id
        config = getattr(result, "config", None)
        if not isinstance(config, dict):
            return
        config["strategy_id"] = backtest_id
        config["resolved_params"] = dict(params or {})
        metadata = config.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.update(
            {
                "source_workflow": "walk_forward_analysis",
                "wfa_window_id": int(window_id),
                "wfa_objective": str(objective),
                "train_start": str(window.get("train_start")),
                "train_end": str(window.get("train_end")),
                "test_start": str(window.get("test_start")),
                "test_end": str(window.get("test_end")),
            }
        )
        config["metadata"] = metadata

    @staticmethod
    def _exposure_ratio(equity_curve: pd.DataFrame) -> float:
        if not isinstance(equity_curve, pd.DataFrame) or equity_curve.empty:
            return 0.0
        if "Gross_exposure" in equity_curve.columns:
            exposure = pd.to_numeric(equity_curve["Gross_exposure"], errors="coerce").fillna(0.0).abs()
            return float((exposure > 1e-12).mean()) if len(exposure) else 0.0
        weight_cols = [col for col in equity_curve.columns if str(col).startswith("Weight_")]
        if weight_cols:
            weights = equity_curve[weight_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).abs()
            active = weights.sum(axis=1) > 1e-12
            return float(active.mean()) if len(active) else 0.0
        return 0.0

    @staticmethod
    def _nonzero_return_days(equity_curve: pd.DataFrame) -> int:
        if not isinstance(equity_curve, pd.DataFrame) or equity_curve.empty or "Equity_value" not in equity_curve.columns:
            return 0
        equity = pd.to_numeric(equity_curve["Equity_value"], errors="coerce").dropna()
        if len(equity) < 2:
            return 0
        returns = equity.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
        return int((returns.abs() > 1e-12).sum())

    @staticmethod
    def _strategy_max_lookback_days(config: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> int:
        values: List[int] = []
        tokens = ("period", "window", "lookback", "sma", "ema", "ma")
        domains = config.get("parameter_domains", {}) if isinstance(config.get("parameter_domains"), dict) else {}

        def domain_max(param_name: str) -> Optional[int]:
            if isinstance(params, dict) and param_name in params:
                try:
                    parsed = int(params[param_name])
                    return parsed if parsed > 0 else None
                except (TypeError, ValueError):
                    return None
            spec = domains.get(param_name)
            raw_values: List[Any] = []
            if isinstance(spec, list):
                raw_values = spec
            elif isinstance(spec, dict):
                if isinstance(spec.get("values"), list):
                    raw_values = spec["values"]
                elif spec.get("type") == "range":
                    raw_values = [spec.get("start"), spec.get("end")]
            numeric: List[int] = []
            for item in raw_values:
                try:
                    parsed = int(item)
                except (TypeError, ValueError):
                    continue
                if parsed > 0:
                    numeric.append(parsed)
            return max(numeric) if numeric else None

        def visit(value: Any, key_hint: str = "") -> None:
            key_lower = str(key_hint or "").lower()
            if isinstance(value, dict):
                if set(value.keys()) == {"param_ref"} and any(token in key_lower for token in tokens):
                    resolved = domain_max(str(value.get("param_ref")))
                    if resolved is not None:
                        values.append(resolved)
                    return
                for key, item in value.items():
                    visit(item, str(key))
                return
            if isinstance(value, list):
                for item in value:
                    visit(item, key_hint)
                return
            if not any(token in key_lower for token in tokens):
                return
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return
            if parsed > 0:
                values.append(parsed)

        visit(config.get("computed_fields", []))
        if isinstance(params, dict):
            visit(params)
        return max(values) if values else 0

    @staticmethod
    def _ratio_or_none(value: Any) -> Optional[float]:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(parsed) or parsed <= 0.0 or parsed >= 1.0:
            return None
        return parsed

    @staticmethod
    def _nonnegative_float(value: Any, *, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if not np.isfinite(parsed):
            return default
        return max(0.0, parsed)

    @staticmethod
    def _first_present(*values: Any) -> Any:
        for value in values:
            if value is not None:
                return value
        return None

    @staticmethod
    def _bool_config(value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        try:
            if pd.isna(value):
                return default
        except (TypeError, ValueError):
            pass
        if isinstance(value, str):
            text = value.strip().lower()
            if not text:
                return default
            if text in {"1", "true", "yes", "y", "on"}:
                return True
            if text in {"0", "false", "no", "n", "off"}:
                return False
        try:
            return bool(int(value))
        except (TypeError, ValueError):
            return bool(value)

    @staticmethod
    def _slug(value: Any) -> str:
        text = str(value).strip().replace(" ", "-").replace("/", "-").replace("\\", "-")
        return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in text).strip("-_") or "value"

    @staticmethod
    def _positive_int(value: Any, *, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _nonnegative_int(value: Any, *, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed >= 0 else default
