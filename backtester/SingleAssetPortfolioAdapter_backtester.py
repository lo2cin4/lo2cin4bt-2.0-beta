"""Adapter for running single-asset signals through the portfolio engine.

This module is intentionally thin: it converts entry/exit signal state into a
one-asset target-weight frame, then delegates accounting to
MultiAssetPortfolioEngineBacktester.  It is the first bridge toward retiring the
separate single-asset accounting path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from backtester.MultiAssetPortfolioEngine_backtester import (
    MultiAssetBacktestResult,
    MultiAssetFeatureBuilder,
    MultiAssetPortfolioEngineBacktester,
)


def build_target_weight_frame_from_signals(
    *,
    index: pd.Index,
    symbol: str,
    entry_signal: pd.Series,
    exit_signal: pd.Series,
    target_weight: float = 1.0,
    conflict_policy: str = "exit_then_entry",
) -> pd.DataFrame:
    """Create a date x one-asset target-weight frame from entry/exit signals."""

    normalized_index = pd.to_datetime(index).tz_localize(None).normalize()
    entry = _normalize_signal(entry_signal, normalized_index)
    exit_ = _normalize_signal(exit_signal, normalized_index)
    policy = str(conflict_policy or "exit_then_entry").strip().lower()
    if policy not in {"exit_then_entry", "entry_then_exit", "flat_on_conflict"}:
        raise ValueError(f"Unsupported single-asset signal conflict policy: {conflict_policy}")

    in_position = False
    weights = []
    for current_date in normalized_index:
        has_entry = bool(entry.loc[current_date])
        has_exit = bool(exit_.loc[current_date])
        if has_entry and has_exit and policy == "flat_on_conflict":
            in_position = False
        elif policy == "entry_then_exit":
            if has_entry:
                in_position = True
            if has_exit:
                in_position = False
        else:
            if has_exit:
                in_position = False
            if has_entry:
                in_position = True
        weights.append(float(target_weight) if in_position else 0.0)

    return pd.DataFrame({str(symbol): weights}, index=normalized_index)


def run_single_asset_signals_as_portfolio(
    *,
    price_data: pd.DataFrame,
    symbol: str,
    entry_signal: pd.Series,
    exit_signal: pd.Series,
    strategy_id: str = "single_asset_as_portfolio",
    target_weight: float = 1.0,
    execution: Optional[Dict[str, Any]] = None,
    cache_dir: Optional[Path] = None,
) -> MultiAssetBacktestResult:
    """Run one symbol through the unified multi-asset portfolio engine."""

    frames = _price_frames_for_symbol(price_data, symbol)
    frames["target_weight"] = build_target_weight_frame_from_signals(
        index=frames["close"].index,
        symbol=symbol,
        entry_signal=entry_signal,
        exit_signal=exit_signal,
        target_weight=target_weight,
    )
    config = {
        "schema_version": "strategy_run",
        "strategy_id": strategy_id,
        "platform": {
            "strategy_mode_id": "single_asset_signal",
            "workflow_id": "single_backtest",
        },
        "universe": {"symbols": [str(symbol)]},
        "features": [],
        "selection": {},
        "allocation": {
            "method": "target_weight_frame",
            "frame": "target_weight",
            "normalize_if_overweight": True,
        },
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "execution": execution or {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
        "risk": {
            "max_positions": 1,
            "max_gross_exposure": max(1.0, abs(float(target_weight))),
            "long_short": "long_only",
            "allow_short": False,
        },
    }
    return MultiAssetPortfolioEngineBacktester(frames, config, cache_dir=cache_dir).run()


def _price_frames_for_symbol(price_data: pd.DataFrame, symbol: str) -> Dict[str, pd.DataFrame]:
    if not isinstance(price_data, pd.DataFrame) or price_data.empty:
        raise ValueError("price_data must be a non-empty DataFrame")

    normalized = _normalize_price_data_index(price_data)
    normalized = normalized.sort_index()
    lower_columns = {str(col).lower(): col for col in normalized.columns}
    close_col = lower_columns.get("close")
    open_col = lower_columns.get("open", close_col)
    if close_col is None:
        raise KeyError("price_data must include a Close column")

    close = pd.DataFrame({str(symbol): pd.to_numeric(normalized[close_col], errors="coerce")})
    open_ = pd.DataFrame({str(symbol): pd.to_numeric(normalized[open_col], errors="coerce")})
    return {
        "close": MultiAssetFeatureBuilder._normalize_frame(close),
        "open": MultiAssetFeatureBuilder._normalize_frame(open_),
    }


def _normalize_signal(signal: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    series = signal.copy() if isinstance(signal, pd.Series) else pd.Series(False, index=index)
    if isinstance(series.index, pd.DatetimeIndex):
        series.index = pd.to_datetime(series.index).tz_localize(None).normalize()
        return series.reindex(index).fillna(False).astype(bool)
    if len(series) == len(index):
        return pd.Series(series.to_numpy(), index=index).fillna(False).astype(bool)
    parsed_index = pd.to_datetime(series.index, errors="coerce")
    if not pd.isna(parsed_index).any():
        series.index = parsed_index.tz_localize(None).normalize()
        return series.reindex(index).fillna(False).astype(bool)
    return pd.Series(False, index=index)


def _normalize_price_data_index(price_data: pd.DataFrame) -> pd.DataFrame:
    normalized = price_data.copy()
    lower_columns = {str(col).lower(): col for col in normalized.columns}
    time_col = lower_columns.get("time") or lower_columns.get("date")
    if time_col is not None:
        normalized.index = pd.to_datetime(normalized[time_col], errors="coerce")
        normalized = normalized.loc[~pd.isna(normalized.index)].copy()
    else:
        normalized.index = pd.to_datetime(normalized.index, errors="coerce")
        normalized = normalized.loc[~pd.isna(normalized.index)].copy()
    normalized.index = pd.DatetimeIndex(normalized.index).tz_localize(None).normalize()
    return normalized
