import json
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from utils import show_success, show_warning

from .MetricsCalculator_metricstracker import (
    MetricsCalculatorMetricTracker,
    _average_drawdown_numba,
)
from .RustMetricsKernel_metricstracker import RustMetricsKernel
from .utils.ConsoleUtils_utils_metricstracker import get_console

console = get_console()


class MetricsExporter:
    _RUST_KERNEL: Optional[RustMetricsKernel] = None

    @staticmethod
    def add_drawdown_bah(df: pd.DataFrame) -> pd.DataFrame:
        if "Drawdown" in df.columns and "BAH_Equity" in df.columns and "BAH_Drawdown" in df.columns:
            return df

        df = df.copy()
        equity = pd.to_numeric(df["Equity_value"], errors="coerce")
        roll_max = equity.cummax()
        df["Drawdown"] = (equity - roll_max) / roll_max

        if "Close" in df.columns:
            initial_equity = equity.iloc[0]
            initial_price = pd.to_numeric(df["Close"], errors="coerce").iloc[0]
            close = pd.to_numeric(df["Close"], errors="coerce")
            df["BAH_Equity"] = initial_equity * (close / initial_price)
            df["BAH_Return"] = df["BAH_Equity"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0)
            bah_roll_max = df["BAH_Equity"].cummax()
            df["BAH_Drawdown"] = (df["BAH_Equity"] - bah_roll_max) / bah_roll_max

        return df

    @classmethod
    def export(cls, df: pd.DataFrame, orig_parquet_path: str, time_unit: int, risk_free_rate: float) -> None:
        try:
            orig_meta = pq.read_schema(orig_parquet_path).metadata or {}
        except Exception as exc:
            show_warning(
                "METRICSTRACKER",
                f"Unable to read source parquet metadata: {exc}\nProceeding with fresh metadata export only.",
            )
            orig_meta = {}

        orig_name = os.path.splitext(os.path.basename(orig_parquet_path))[0]
        out_dir = os.path.join(os.path.dirname(os.path.dirname(orig_parquet_path)), "metricstracker")
        metadata_json_path = os.path.join(out_dir, f"{orig_name}_metadata.json")

        df = cls.add_drawdown_bah(df)
        if "Backtest_id" in df.columns:
            batch_metadata, df = cls._compute_batch_metadata(df, time_unit, risk_free_rate)
        else:
            calc = MetricsCalculatorMetricTracker(df, time_unit, risk_free_rate)
            meta = {}
            meta.update(calc.calc_strategy_metrics())
            meta.update(calc.calc_bah_metrics())
            batch_metadata = [meta]

        preferred_cols = ["Time", "Equity_value", "BAH_Equity", "BAH_Return", "Drawdown", "Backtest_id"]
        available_cols = [column for column in preferred_cols if column in df.columns]
        if available_cols:
            df_out = df[available_cols].copy()
        else:
            fallback = [column for column in ["Time", "Equity_value"] if column in df.columns]
            df_out = df[fallback].copy() if fallback else df.head(0).copy()

        for column in df_out.columns:
            if df_out[column].dtype == "float64":
                df_out[column] = df_out[column].astype("float32")

        os.makedirs(out_dir, exist_ok=True)
        with open(metadata_json_path, "w", encoding="utf-8") as handle:
            json.dump(batch_metadata, handle, ensure_ascii=False, indent=2)

        new_meta = {
            key if isinstance(key, bytes) else str(key).encode(): value
            for key, value in dict(orig_meta).items()
        }
        if b"batch_metadata" in new_meta:
            del new_meta[b"batch_metadata"]
            show_success(
                "METRICSTRACKER",
                "Removed legacy batch_metadata from source parquet metadata; metrics metadata is now stored only in the JSON sidecar.",
            )

        table = pa.Table.from_pandas(df_out, preserve_index=False)
        table = table.replace_schema_metadata(new_meta)
        out_path = os.path.join(out_dir, f"{orig_name}_metrics.parquet")
        pq.write_table(table, out_path)

        show_success(
            "METRICSTRACKER",
            f"Metrics export completed.\nParquet: {out_path}\nMetadata JSON: {metadata_json_path}",
        )

        try:
            pq.read_table(out_path)
            show_success("METRICSTRACKER", "Metric parquet verification passed.")
        except Exception as exc:
            show_warning(
                "METRICSTRACKER",
                f"Metric parquet verification failed: {exc}\nPlease inspect the JSON metadata sidecar.",
            )

        show_success("METRICSTRACKER", "Metrics export finished.")

    @classmethod
    def _compute_batch_metadata(
        cls, df: pd.DataFrame, time_unit: int, risk_free_rate: float
    ) -> Tuple[List[Dict[str, float]], pd.DataFrame]:
        df_grouped = cls._ensure_group_order(df)
        backtest_ids = df_grouped["Backtest_id"].to_numpy()
        if backtest_ids.size == 0:
            return [], df_grouped

        starts, ends = cls._group_boundaries(backtest_ids)
        unique_ids = backtest_ids[starts]

        equity = pd.to_numeric(df_grouped["Equity_value"], errors="coerce").to_numpy(dtype=np.float64, copy=False)
        bah_equity = pd.to_numeric(df_grouped["BAH_Equity"], errors="coerce").to_numpy(dtype=np.float64, copy=False)
        trade_actions = pd.to_numeric(df_grouped.get("Trade_action", pd.Series(np.nan, index=df_grouped.index)), errors="coerce").to_numpy(dtype=np.float64, copy=False)
        trade_returns = pd.to_numeric(df_grouped.get("Trade_return", pd.Series(np.nan, index=df_grouped.index)), errors="coerce").to_numpy(dtype=np.float64, copy=False)
        position_size = pd.to_numeric(df_grouped.get("Position_size", pd.Series(np.nan, index=df_grouped.index)), errors="coerce").to_numpy(dtype=np.float64, copy=False)

        trade_stats = cls._compute_trade_stats_batch(
            trade_actions=trade_actions,
            trade_returns=trade_returns,
            position_size=position_size,
            group_start=starts,
            group_end=ends,
        )

        batch_metadata: List[Dict[str, float]] = []
        rf_per_period = cls._risk_free_per_period(risk_free_rate, time_unit)
        sqrt_time_unit = np.sqrt(time_unit)

        for idx, backtest_id in enumerate(unique_ids):
            start = int(starts[idx])
            end = int(ends[idx])
            if end <= start:
                continue

            eq = equity[start:end]
            bah_eq = bah_equity[start:end]
            returns = cls._pct_change(eq)
            bah_returns = cls._pct_change(bah_eq)
            dd = cls._build_drawdown(eq)
            bah_dd = cls._build_drawdown(bah_eq)
            length = end - start
            years = max(length / time_unit, 1.0)

            total_return = cls._total_return(eq)
            bah_total_return = cls._total_return(bah_eq)
            annualized_return = cls._annualized_total_return(total_return, years)
            bah_annualized_return = cls._annualized_total_return(bah_total_return, years)
            std = cls._std(returns)
            bah_std = cls._std(bah_returns)
            annualized_std = cls._annualized_std(std, sqrt_time_unit)
            bah_annualized_std = cls._annualized_std(bah_std, sqrt_time_unit)
            downside = cls._downside_risk(returns)
            bah_downside = cls._downside_risk(bah_returns)
            annualized_downside = cls._annualized_std(downside, sqrt_time_unit)
            bah_annualized_downside = cls._annualized_std(bah_downside, sqrt_time_unit)
            max_drawdown = cls._nanmin(dd)
            bah_max_drawdown = cls._nanmin(bah_dd)
            average_drawdown = cls._average_drawdown(dd)
            bah_average_drawdown = cls._average_drawdown(bah_dd)
            recovery_factor = cls._safe_div(total_return, abs(max_drawdown), np.nan)
            bah_recovery_factor = cls._safe_div(bah_total_return, abs(bah_max_drawdown), np.nan)
            sharpe = cls._sharpe(returns, rf_per_period, sqrt_time_unit)
            bah_sharpe = cls._sharpe(bah_returns, rf_per_period, sqrt_time_unit)
            sortino = cls._sortino(returns, rf_per_period, sqrt_time_unit)
            bah_sortino = cls._sortino(bah_returns, rf_per_period, sqrt_time_unit)
            calmar = cls._safe_div(annualized_return - risk_free_rate, abs(max_drawdown), np.nan)
            bah_calmar = cls._safe_div(bah_annualized_return - risk_free_rate, abs(bah_max_drawdown), np.nan)
            information_ratio = cls._information_ratio(returns, bah_returns)
            beta = cls._beta(returns, bah_returns)
            alpha = cls._alpha(returns, bah_returns, risk_free_rate, time_unit, beta)

            meta = {
                "Backtest_id": backtest_id,
                "Total_return": total_return,
                "Annualized_return (CAGR)": annualized_return,
                "Std": std,
                "Annualized_std": annualized_std,
                "Downside_risk": downside,
                "Annualized_downside_risk": annualized_downside,
                "Max_drawdown": max_drawdown,
                "Average_drawdown": average_drawdown,
                "Recovery_factor": recovery_factor,
                "Sharpe": sharpe,
                "Sortino": sortino,
                "Calmar": calmar,
                "Information_ratio": information_ratio,
                "Alpha": alpha,
                "Beta": beta,
                "Trade_count": trade_stats["trade_count"][idx],
                "Win_rate": trade_stats["win_rate"][idx],
                "Profit_factor": trade_stats["profit_factor"][idx],
                "Avg_trade_return": trade_stats["avg_trade_return"][idx],
                "Max_consecutive_losses": trade_stats["max_consecutive_losses"][idx],
                "Exposure_time": trade_stats["exposure_time"][idx],
                "Max_holding_period_ratio": trade_stats["max_holding_ratio"][idx],
                "BAH_Total_return": bah_total_return,
                "BAH_Annualized_return (CAGR)": bah_annualized_return,
                "BAH_Std": bah_std,
                "BAH_Annualized_std": bah_annualized_std,
                "BAH_Downside_risk": bah_downside,
                "BAH_Annualized_downside_risk": bah_annualized_downside,
                "BAH_Max_drawdown": bah_max_drawdown,
                "BAH_Average_drawdown": bah_average_drawdown,
                "BAH_Recovery_factor": bah_recovery_factor,
                "BAH_Sharpe": bah_sharpe,
                "BAH_Sortino": bah_sortino,
                "BAH_Calmar": bah_calmar,
            }
            batch_metadata.append(meta)

        return batch_metadata, df_grouped

    @classmethod
    def _compute_trade_stats_batch(
        cls,
        *,
        trade_actions: np.ndarray,
        trade_returns: np.ndarray,
        position_size: np.ndarray,
        group_start: np.ndarray,
        group_end: np.ndarray,
    ) -> Dict[str, np.ndarray]:
        kernel = cls._get_rust_kernel()
        if kernel is not None and kernel.is_available():
            try:
                values = kernel.compute_trade_stats_batch(
                    trade_actions=trade_actions,
                    trade_returns=trade_returns,
                    position_size=position_size,
                    group_start=group_start,
                    group_end=group_end,
                )
                return {
                    "trade_count": values[0],
                    "win_rate": values[1],
                    "profit_factor": values[2],
                    "avg_trade_return": values[3],
                    "max_consecutive_losses": values[4],
                    "exposure_time": values[5],
                    "max_holding_ratio": values[6],
                }
            except Exception as exc:
                show_warning("METRICSTRACKER", f"Rust metrics kernel fallback to numpy: {exc}")

        n_groups = len(group_start)
        trade_count = np.empty(n_groups, dtype=np.float64)
        win_rate = np.empty(n_groups, dtype=np.float64)
        profit_factor = np.empty(n_groups, dtype=np.float64)
        avg_trade_return = np.empty(n_groups, dtype=np.float64)
        max_losses = np.empty(n_groups, dtype=np.float64)
        exposure_time = np.empty(n_groups, dtype=np.float64)
        max_holding_ratio = np.empty(n_groups, dtype=np.float64)

        for idx in range(n_groups):
            start = int(group_start[idx])
            end = int(group_end[idx])
            actions = trade_actions[start:end]
            returns = trade_returns[start:end]
            positions = position_size[start:end]

            trade_count[idx] = np.sum(actions == 1.0)
            closed_trade_returns = returns[(actions == 4.0) & ~np.isnan(returns)]
            if closed_trade_returns.size:
                win_rate[idx] = np.mean(closed_trade_returns > 0.0)
            else:
                win_rate[idx] = np.nan

            valid_returns = returns[~np.isnan(returns)]
            profits = valid_returns[valid_returns > 0.0].sum() if valid_returns.size else 0.0
            losses = valid_returns[valid_returns < 0.0].sum() if valid_returns.size else 0.0
            profit_factor[idx] = np.nan if losses == 0.0 else profits / abs(losses)
            avg_trade_return[idx] = np.mean(valid_returns) if valid_returns.size else np.nan
            max_losses[idx] = cls._max_consecutive_negative(valid_returns)
            exposure_time[idx] = np.mean((~np.isnan(positions)) & (positions != 0.0)) * 100.0 if positions.size else np.nan
            max_holding_ratio[idx] = cls._max_nonzero_run_ratio(positions)

        return {
            "trade_count": trade_count,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "avg_trade_return": avg_trade_return,
            "max_consecutive_losses": max_losses,
            "exposure_time": exposure_time,
            "max_holding_ratio": max_holding_ratio,
        }

    @classmethod
    def _get_rust_kernel(cls) -> Optional[RustMetricsKernel]:
        if cls._RUST_KERNEL is None:
            try:
                cls._RUST_KERNEL = RustMetricsKernel()
            except Exception:
                cls._RUST_KERNEL = None
        return cls._RUST_KERNEL

    @staticmethod
    def _ensure_group_order(df: pd.DataFrame) -> pd.DataFrame:
        if "Backtest_id" not in df.columns or len(df) <= 1:
            return df
        backtest_ids = df["Backtest_id"].to_numpy()
        transitions = np.empty(backtest_ids.size, dtype=bool)
        transitions[0] = True
        transitions[1:] = backtest_ids[1:] != backtest_ids[:-1]
        contiguous_unique = pd.unique(backtest_ids[transitions])
        if contiguous_unique.size == pd.Index(backtest_ids).nunique(dropna=False):
            return df.reset_index(drop=True)
        return df.sort_values("Backtest_id", kind="stable").reset_index(drop=True)

    @staticmethod
    def _group_boundaries(backtest_ids: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        if backtest_ids.size == 0:
            empty = np.empty(0, dtype=np.int64)
            return empty, empty
        change = np.empty(backtest_ids.size, dtype=bool)
        change[0] = True
        change[1:] = backtest_ids[1:] != backtest_ids[:-1]
        starts = np.flatnonzero(change)
        ends = np.empty_like(starts)
        ends[:-1] = starts[1:]
        ends[-1] = backtest_ids.size
        return starts.astype(np.int64), ends.astype(np.int64)

    @staticmethod
    def _pct_change(values: np.ndarray) -> np.ndarray:
        out = np.zeros(values.shape[0], dtype=np.float64)
        if values.shape[0] <= 1:
            return out
        prev = values[:-1]
        curr = values[1:]
        valid = (~np.isnan(prev)) & (~np.isnan(curr)) & (prev != 0.0)
        delta = np.zeros_like(curr, dtype=np.float64)
        delta[valid] = (curr[valid] / prev[valid]) - 1.0
        delta[~np.isfinite(delta)] = 0.0
        out[1:] = delta
        return out

    @staticmethod
    def _build_drawdown(values: np.ndarray) -> np.ndarray:
        if values.size == 0:
            return values.astype(np.float64)
        roll_max = np.maximum.accumulate(values)
        out = np.full(values.shape[0], np.nan, dtype=np.float64)
        valid = (~np.isnan(values)) & (~np.isnan(roll_max)) & (roll_max != 0.0)
        out[valid] = (values[valid] - roll_max[valid]) / roll_max[valid]
        return out

    @staticmethod
    def _total_return(equity: np.ndarray) -> float:
        if equity.size == 0 or np.isnan(equity[0]) or equity[0] == 0.0 or np.isnan(equity[-1]):
            return 0.0
        return (equity[-1] / equity[0]) - 1.0

    @staticmethod
    def _annualized_total_return(total_return: float, years: float) -> float:
        return MetricsExporter._safe_power(1.0 + total_return, 1.0 / years, 0.0) - 1.0

    @staticmethod
    def _cagr(equity: np.ndarray, years: float) -> float:
        if equity.size == 0 or np.isnan(equity[0]) or equity[0] <= 0.0 or np.isnan(equity[-1]) or equity[-1] <= 0.0:
            return 0.0
        return MetricsExporter._safe_power(equity[-1] / equity[0], 1.0 / years, 0.0) - 1.0

    @staticmethod
    def _std(values: np.ndarray) -> float:
        if values.size < 2:
            return np.nan
        return float(np.std(values, ddof=1))

    @staticmethod
    def _annualized_std(std: float, sqrt_time_unit: float) -> float:
        if np.isnan(std):
            return np.nan
        return std * sqrt_time_unit

    @staticmethod
    def _risk_free_per_period(risk_free_rate: float, time_unit: int) -> float:
        return float(risk_free_rate) / float(time_unit)

    @staticmethod
    def _downside_risk(values: np.ndarray, target: float = 0.0) -> float:
        downside = values[values < target]
        if downside.size == 0:
            return 0.0
        return float(np.sqrt(np.mean((downside - target) ** 2)))

    @staticmethod
    def _average_drawdown(drawdown: np.ndarray) -> float:
        if drawdown.size == 0:
            return 0.0
        return float(_average_drawdown_numba(np.asarray(drawdown, dtype=np.float64)))

    @staticmethod
    def _nanmin(values: np.ndarray) -> float:
        valid = values[~np.isnan(values)]
        if valid.size == 0:
            return np.nan
        return float(valid.min())

    @staticmethod
    def _sharpe(returns: np.ndarray, rf_per_period: float, sqrt_time_unit: float) -> float:
        if returns.size < 2:
            return np.nan
        mean = float(np.mean(returns))
        std = float(np.std(returns, ddof=1))
        if std == 0.0 or np.isnan(std):
            return np.nan
        return ((mean - rf_per_period) / std) * sqrt_time_unit

    @staticmethod
    def _sortino(returns: np.ndarray, rf_per_period: float, sqrt_time_unit: float) -> float:
        mean = float(np.mean(returns)) if returns.size else np.nan
        downside = MetricsExporter._downside_risk(returns)
        if downside == 0.0 or np.isnan(downside):
            return np.nan
        return ((mean - rf_per_period) / downside) * sqrt_time_unit

    @staticmethod
    def _information_ratio(strategy_returns: np.ndarray, benchmark_returns: np.ndarray) -> float:
        if strategy_returns.size == 0 or benchmark_returns.size == 0:
            return np.nan
        diff = strategy_returns - benchmark_returns
        tracking_error = float(np.std(diff, ddof=1)) if diff.size >= 2 else np.nan
        if tracking_error == 0.0 or np.isnan(tracking_error):
            return np.nan
        return float(np.mean(diff)) / tracking_error

    @staticmethod
    def _beta(strategy_returns: np.ndarray, benchmark_returns: np.ndarray) -> float:
        if strategy_returns.size < 2 or benchmark_returns.size < 2:
            return np.nan
        cov = np.cov(strategy_returns, benchmark_returns, ddof=1)[0, 1]
        var = np.var(benchmark_returns, ddof=1)
        if var == 0.0 or np.isnan(var):
            return np.nan
        return float(cov / var)

    @staticmethod
    def _alpha(strategy_returns: np.ndarray, benchmark_returns: np.ndarray, risk_free_rate: float, time_unit: int, beta: float) -> float:
        if strategy_returns.size == 0 or benchmark_returns.size == 0 or np.isnan(beta):
            return np.nan
        rf = risk_free_rate / time_unit
        mean_return = float(np.mean(strategy_returns))
        mean_bah = float(np.mean(benchmark_returns))
        return mean_return - (rf + beta * (mean_bah - rf))

    @staticmethod
    def _max_consecutive_negative(values: np.ndarray) -> int:
        max_count = 0
        count = 0
        for value in values:
            if np.isnan(value):
                continue
            if value < 0.0:
                count += 1
                if count > max_count:
                    max_count = count
            else:
                count = 0
        return max_count

    @staticmethod
    def _max_nonzero_run_ratio(values: np.ndarray) -> float:
        if values.size == 0:
            return np.nan
        max_run = 0
        run = 0
        for value in values:
            if np.isnan(value):
                run = 0
                continue
            if value != 0.0:
                run += 1
                if run > max_run:
                    max_run = run
            else:
                run = 0
        return max_run / values.size

    @staticmethod
    def _safe_power(base: float, exponent: float, fallback: float = 0.0) -> float:
        try:
            if base <= 0 and exponent <= 0:
                return fallback
            if base == 0 and exponent > 0:
                return 0.0
            if base == 0 and exponent < 0:
                return fallback
            if np.isnan(base) or np.isnan(exponent):
                return fallback
            if np.isinf(exponent) and base == 1:
                return 1.0
            if np.isinf(exponent) and base != 1:
                return fallback
            if abs(base) < 1e-10 and abs(exponent) > 100:
                return 0.0
            if abs(base - 1) < 1e-10:
                return 1.0
            if abs(exponent) > 1000:
                try:
                    with np.errstate(over="raise", invalid="raise"):
                        log_result = exponent * np.log(base)
                        if log_result > 700:
                            return fallback
                        if log_result < -700:
                            return 0.0
                        result = np.exp(log_result)
                        if np.isnan(result) or np.isinf(result):
                            return fallback
                        return float(result)
                except (ValueError, OverflowError, FloatingPointError):
                    return fallback
            if base > 0:
                with np.errstate(over="ignore", invalid="ignore"):
                    result = np.power(base, exponent)
                    if np.isnan(result) or np.isinf(result):
                        return fallback
                    return float(result)
            return fallback
        except (ValueError, OverflowError):
            return fallback

    @staticmethod
    def _safe_div(numerator: float, denominator: float, fallback: float = 0.0) -> float:
        try:
            if denominator == 0 or np.isnan(denominator) or np.isinf(denominator):
                return fallback
            if np.isnan(numerator) or np.isinf(numerator):
                return fallback
            result = numerator / denominator
            if np.isnan(result) or np.isinf(result):
                return fallback
            return float(result)
        except (ValueError, RuntimeWarning, ZeroDivisionError):
            return fallback
