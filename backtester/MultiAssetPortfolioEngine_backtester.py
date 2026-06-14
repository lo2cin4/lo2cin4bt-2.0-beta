"""Multi-asset portfolio backtest engine.

The first production slice is intentionally vector-like and data-source neutral:
callers provide normalized wide matrices with dates as index and assets as
columns.  The engine handles feature materialization, eligibility, ranking,
calendar rebalance triggers, allocation, costs, and audit tables.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from backtester.RiskGate_backtester import RiskGateController, summarize_risk_gate_events
from backtester.UniverseConstituentsValidator_backtester import (
    CONSTITUENTS_SOURCE_TYPES,
    constituents_path_declared,
    constituents_source_ref,
    declared_constituents_hash,
    validate_historical_universe_constituents,
)
from utils.calendar_events import CalendarEventResolver

Comparator = Tuple[str, str]
_INLINE_FEATURE_CACHE: "OrderedDict[Tuple[Any, ...], pd.DataFrame]" = OrderedDict()
_INLINE_FEATURE_CACHE_LIMIT = 512
_INLINE_FEATURE_CACHE_LOCK = threading.RLock()
_SAME_BAR_COMPLETED_INDICATOR_OPS = {
    "indicator.atr",
    "indicator.bollinger",
    "indicator.ema",
    "indicator.macd",
    "indicator.momentum",
    "indicator.percentile",
    "indicator.rsi",
    "indicator.sma",
    "indicator.volatility",
    "indicator.zscore",
}
_SAME_SESSION_UNSAFE_MARKET_FIELDS = {"open", "close", "high", "low", "volume"}


@dataclass
class MultiAssetBacktestResult:
    strategy_id: str
    equity_curve: pd.DataFrame
    holdings: pd.DataFrame
    rebalance_audit: pd.DataFrame
    rebalance_trades: pd.DataFrame
    feature_cache: Dict[str, Any]
    config: Dict[str, Any]
    validation_report: Dict[str, Any]
    risk_gate_events: pd.DataFrame = field(default_factory=pd.DataFrame)


class MultiAssetFeatureBuilder:
    """Build date x asset feature matrices with an in-memory/local cache hook."""

    def __init__(
        self,
        frames: Dict[str, pd.DataFrame],
        *,
        cache_dir: Optional[Path] = None,
        use_local_cache: bool = False,
    ) -> None:
        self.frames = {key.lower(): self._normalize_frame(value) for key, value in frames.items()}
        self.cache_dir = cache_dir
        self.use_local_cache = bool(use_local_cache and cache_dir is not None)
        self.memory_cache: Dict[str, pd.DataFrame] = {}
        self.stats = {"memory_hits": 0, "local_hits": 0, "computed": 0}
        if self.use_local_cache and self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def build(self, feature_specs: Iterable[Dict[str, Any]]) -> Dict[str, pd.DataFrame]:
        features: Dict[str, pd.DataFrame] = {}
        for spec in feature_specs or []:
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("name") or "").strip()
            if not name:
                continue
            features[name] = self.compute(spec)
        return features

    def compute(self, spec: Dict[str, Any]) -> pd.DataFrame:
        cache_key = self._cache_key(spec)
        if cache_key in self.memory_cache:
            self.stats["memory_hits"] += 1
            return self.memory_cache[cache_key].copy()

        local_path = self._local_cache_path(cache_key)
        if local_path is not None and local_path.exists():
            self.stats["local_hits"] += 1
            frame = pd.read_parquet(local_path)
            self.memory_cache[cache_key] = frame
            return frame.copy()

        frame = self._compute_uncached(spec)
        self.memory_cache[cache_key] = frame
        self.stats["computed"] += 1
        if local_path is not None:
            frame.to_parquet(local_path)
        return frame.copy()

    def _compute_uncached(self, spec: Dict[str, Any]) -> pd.DataFrame:
        op = str(spec.get("op") or spec.get("type") or "").strip().lower()
        period = self._positive_int(spec.get("period"), default=14)

        if op == "indicator.atr":
            return self._compute_atr(spec)

        source_name = str(spec.get("source") or "close").strip().lower()
        source = self._source_frame(source_name)

        if op in {"identity", "field", "source"}:
            return source.copy()
        if op == "indicator.sma":
            return source.rolling(period, min_periods=period).mean()
        if op == "indicator.ema":
            return source.ewm(span=period, adjust=False, min_periods=period).mean()
        if op == "indicator.momentum":
            return source.pct_change(periods=period)
        if op == "indicator.volatility":
            daily = source.pct_change()
            annualize = bool(spec.get("annualize", True))
            vol = daily.rolling(period, min_periods=period).std()
            return vol * np.sqrt(252.0) if annualize else vol
        if op == "indicator.zscore":
            mean = source.rolling(period, min_periods=period).mean()
            std = source.rolling(period, min_periods=period).std()
            return (source - mean) / std.replace(0.0, np.nan)
        if op == "indicator.percentile":
            percentile = float(spec.get("percentile", 50.0))
            quantile = max(0.0, min(100.0, percentile)) / 100.0
            return source.rolling(period, min_periods=period).quantile(quantile)
        if op == "indicator.bollinger":
            stddev = float(spec.get("stddev", spec.get("num_std", 2.0)))
            band = str(spec.get("band") or "middle").strip().lower()
            mean = source.rolling(period, min_periods=period).mean()
            std = source.rolling(period, min_periods=period).std()
            if band in {"upper", "high"}:
                return mean + (std * stddev)
            if band in {"lower", "low"}:
                return mean - (std * stddev)
            if band in {"width", "bandwidth"}:
                return ((mean + (std * stddev)) - (mean - (std * stddev))) / mean.replace(0.0, np.nan)
            if band in {"percent_b", "pct_b"}:
                upper = mean + (std * stddev)
                lower = mean - (std * stddev)
                return (source - lower) / (upper - lower).replace(0.0, np.nan)
            return mean
        if op == "indicator.rsi":
            delta = source.diff()
            gain = delta.clip(lower=0.0).rolling(period, min_periods=period).mean()
            loss = (-delta.clip(upper=0.0)).rolling(period, min_periods=period).mean()
            rs = gain / loss.replace(0.0, np.nan)
            return 100.0 - (100.0 / (1.0 + rs))
        if op == "indicator.macd":
            fast = self._positive_int(spec.get("fastperiod"), default=12)
            slow = self._positive_int(spec.get("slowperiod"), default=26)
            signal = self._positive_int(spec.get("signalperiod"), default=9)
            ema_fast = source.ewm(span=fast, adjust=False, min_periods=fast).mean()
            ema_slow = source.ewm(span=slow, adjust=False, min_periods=slow).mean()
            macd = ema_fast - ema_slow
            output = str(spec.get("output") or "line").strip().lower()
            if output == "line":
                return macd
            signal_line = macd.ewm(span=signal, adjust=False, min_periods=signal).mean()
            if output == "signal":
                return signal_line
            if output == "histogram":
                return macd - signal_line
            raise ValueError("indicator.macd output must be one of: line, signal, histogram")
        raise ValueError(f"Unsupported multi-asset feature op: {op}")

    def _compute_atr(self, spec: Dict[str, Any]) -> pd.DataFrame:
        period = self._positive_int(spec.get("period"), default=14)
        high_source = str(spec.get("high_source") or spec.get("high") or "high").strip().lower()
        low_source = str(spec.get("low_source") or spec.get("low") or "low").strip().lower()
        close_source = str(spec.get("close_source") or spec.get("source") or "close").strip().lower()
        close = self._source_frame(close_source)
        high = self._source_frame(high_source).reindex(index=close.index, columns=close.columns)
        low = self._source_frame(low_source).reindex(index=close.index, columns=close.columns)
        prev_close = close.shift(1)
        components = np.stack(
            [
                (high - low).to_numpy(dtype=float),
                (high - prev_close).abs().to_numpy(dtype=float),
                (low - prev_close).abs().to_numpy(dtype=float),
            ],
            axis=0,
        )
        true_range = pd.DataFrame(
            np.nanmax(components, axis=0),
            index=close.index,
            columns=close.columns,
        )
        method = str(spec.get("method") or spec.get("average") or "wilder").strip().lower()
        if method in {"simple", "sma", "rolling"}:
            return true_range.rolling(period, min_periods=period).mean()
        if method == "ema":
            return true_range.ewm(span=period, adjust=False, min_periods=period).mean()
        return self._wilder_average(true_range, period)

    @staticmethod
    def _wilder_average(frame: pd.DataFrame, period: int) -> pd.DataFrame:
        seed = frame.rolling(period, min_periods=period).mean()
        out = pd.DataFrame(np.nan, index=frame.index, columns=frame.columns)
        for col_idx, column in enumerate(frame.columns):
            seed_col = seed[column]
            valid_positions = np.flatnonzero(seed_col.notna().to_numpy())
            if len(valid_positions) == 0:
                continue
            first = int(valid_positions[0])
            previous = float(seed_col.iloc[first])
            out.iat[first, col_idx] = previous
            tr_col = frame[column]
            for pos in range(first + 1, len(frame)):
                value = tr_col.iloc[pos]
                if pd.isna(value):
                    continue
                previous = ((previous * (period - 1)) + float(value)) / period
                out.iat[pos, col_idx] = previous
        return out

    def _source_frame(self, name: str) -> pd.DataFrame:
        if name not in self.frames:
            raise KeyError(f"Missing source frame '{name}'")
        return self.frames[name]

    def _local_cache_path(self, cache_key: str) -> Optional[Path]:
        if not self.use_local_cache or self.cache_dir is None:
            return None
        return self.cache_dir / f"{cache_key}.parquet"

    @staticmethod
    def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
        out = out.sort_index()
        out.columns = [str(col) for col in out.columns]
        return out

    @staticmethod
    def _positive_int(value: Any, *, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _cache_key(spec: Dict[str, Any]) -> str:
        raw = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class MultiAssetPortfolioEngineBacktester:
    """Run top-N multi-asset portfolio strategies from a declarative config."""

    def __init__(
        self,
        market_data: Dict[str, pd.DataFrame],
        config: Dict[str, Any],
        *,
        cache_dir: Optional[Path] = None,
    ) -> None:
        self.config = dict(config or {})
        self.repo_root = Path(__file__).resolve().parent.parent
        config_file_path = self.config.get("__config_file_path") or self.config.get("config_file_path")
        self.config_file_path = str(config_file_path) if isinstance(config_file_path, str) else None
        self.cache_dir = cache_dir
        self.market_data = {
            key.lower(): MultiAssetFeatureBuilder._normalize_frame(value)
            for key, value in (market_data or {}).items()
        }
        if "close" not in self.market_data:
            raise KeyError("market_data must include a 'close' wide DataFrame")
        self.close = self.market_data["close"]
        self.available_symbols = [str(col) for col in self.close.columns]
        self.open = self.market_data.get("open", self.close)
        self.symbols = self._resolve_symbols()
        self.close = self.close.reindex(columns=self.symbols)
        self.open = self.open.reindex(index=self.close.index, columns=self.symbols)
        for key, frame in list(self.market_data.items()):
            self.market_data[key] = frame.reindex(index=self.close.index, columns=self.symbols)
        self.validation_report = self._build_validation_report()
        indicator_cache_cfg = self.config.get("indicator_cache")
        feature_cache_cfg = (
            indicator_cache_cfg
            if isinstance(indicator_cache_cfg, dict)
            else self.config.get("feature_cache", {})
        )
        self.feature_builder = MultiAssetFeatureBuilder(
            self.market_data,
            cache_dir=cache_dir,
            use_local_cache=bool(
                isinstance(feature_cache_cfg, dict)
                and str(feature_cache_cfg.get("mode", "")).lower() == "local_parquet"
            ),
        )
        self.factor_result = None

    def _risk_gate_controller(self) -> RiskGateController:
        risk_cfg = self.config.get("risk", {}) if isinstance(self.config.get("risk"), dict) else {}
        return RiskGateController(risk_cfg)

    def _apply_risk_gates(
        self,
        *,
        controller: RiskGateController,
        events: List[Dict[str, Any]],
        timestamp: Any,
        before_weights: Any,
        target_weights: Any,
        equity: float,
        equity_peak: float,
        daily_return: float,
        symbols: Optional[List[str]] = None,
    ) -> Any:
        symbols = symbols or list(self.symbols)
        if not controller.enabled:
            return target_weights
        decision = controller.apply(
            timestamp=timestamp,
            symbols=symbols,
            before_weights=np.asarray(before_weights, dtype=float),
            target_weights=np.asarray(target_weights, dtype=float),
            equity=float(equity),
            equity_peak=float(equity_peak),
            daily_return=float(daily_return),
        )
        events.extend(decision.events)
        if isinstance(target_weights, pd.Series):
            return pd.Series(decision.target_weights, index=target_weights.index, dtype=float)
        return decision.target_weights

    def _risk_validation_report(
        self,
        validation_report: Dict[str, Any],
        risk_gate_events: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        report = dict(validation_report or {})
        controller = self._risk_gate_controller()
        summary = summarize_risk_gate_events(risk_gate_events)
        summary["enabled"] = bool(controller.enabled)
        summary["configured_gates"] = controller.configured_gate_names()
        report["risk_gate_summary"] = summary
        return report

    def run(self) -> MultiAssetBacktestResult:
        fill_model = self._fill_model_config()
        accounting_backend = str(
            self.config.get("accounting_backend")
            or fill_model.get("accounting_backend", "")
        ).strip().lower()
        if accounting_backend == "rust":
            if not self._risk_gate_controller().enabled:
                return self._run_with_rust_accounting()
        if self._execution_is_calendar_event_overlay():
            return self._run_calendar_event_overlay_accounting()
        if self._execution_is_same_session():
            return self._run_same_session_accounting()

        features = self._build_features_for_backtest()
        self._ensure_signal_target_weight_frame(features)
        allocation_cfg = self.config.get("allocation", {})
        cost_rate = self._execution_cost_rate()
        if self._execution_is_bar_offset_round_trip():
            return self._run_bar_offset_round_trip_accounting(
                allocation_cfg=allocation_cfg,
                cost_rate=cost_rate,
            )
        if self._can_use_single_asset_signal_target_fast_path(allocation_cfg):
            return self._run_fast_target_weight_accounting(
                allocation_cfg=allocation_cfg,
                cost_rate=cost_rate,
            )
        selector = self.config.get("selection", {})
        eligible = self._evaluate_condition(selector.get("eligible", {}), features)
        score = self._score_frame(selector, features)
        if self._rebalance_trigger_op() == "signal.change":
            rebalance_dates = self._signal_change_rebalance_dates(
                eligible=eligible,
                score=score,
                allocation_cfg=allocation_cfg,
                selector=selector,
            )
        else:
            rebalance_dates = self._rebalance_dates()
        if self._can_use_fast_target_weight_path(rebalance_dates=rebalance_dates, allocation_cfg=allocation_cfg):
            return self._run_fast_target_weight_accounting(
                allocation_cfg=allocation_cfg,
                cost_rate=cost_rate,
            )
        if self._can_use_fast_daily_rank_path(rebalance_dates=rebalance_dates, allocation_cfg=allocation_cfg):
            return self._run_fast_daily_rank_accounting(
                features=features,
                eligible=eligible,
                score=score,
                selector=selector,
                allocation_cfg=allocation_cfg,
                cost_rate=cost_rate,
            )

        previous_weights = pd.Series(0.0, index=self.symbols, dtype=float)
        equity = 100.0
        equity_peak = equity
        risk_controller = self._risk_gate_controller()
        risk_gate_events: List[Dict[str, Any]] = []
        equity_rows: List[Dict[str, Any]] = []
        holding_rows: List[Dict[str, Any]] = []
        rebalance_rows: List[Dict[str, Any]] = []
        rebalance_trade_rows: List[Dict[str, Any]] = []
        previous_close: Optional[pd.Series] = None
        rebalance_set = {pd.Timestamp(item).normalize() for item in rebalance_dates}

        for current_date, close_row in self.close.iterrows():
            daily_return = 0.0
            asset_contribution = pd.Series(0.0, index=self.symbols, dtype=float)
            if previous_close is not None:
                returns = (close_row / previous_close.replace(0.0, np.nan) - 1.0).replace(
                    [np.inf, -np.inf], np.nan
                ).fillna(0.0)
                pre_return_weights = previous_weights.copy()
                asset_contribution = pre_return_weights * returns
                daily_return = float(asset_contribution.sum())
                equity *= 1.0 + daily_return

                # Let weights drift between scheduled rebalances. Without this, a fixed-weight
                # portfolio appears to have zero turnover after the first rebalance.
                denominator = 1.0 + daily_return
                if denominator > 0.0:
                    previous_weights = (
                        pre_return_weights * (1.0 + returns)
                    ).replace([np.inf, -np.inf], np.nan).fillna(0.0) / denominator
                equity_peak = max(equity_peak, equity)

            turnover = 0.0
            trade_cost = 0.0
            selected_assets: List[str] = []
            if pd.Timestamp(current_date).normalize() in rebalance_set:
                before_weights = previous_weights.copy()
                target_weights, selected_assets, ranked_assets = self._target_weights_for_date(
                    date=current_date,
                    eligible=eligible,
                    score=score,
                    allocation_cfg=allocation_cfg,
                    selector=selector,
                )
                target_weights = self._apply_risk_gates(
                    controller=risk_controller,
                    events=risk_gate_events,
                    timestamp=current_date,
                    before_weights=before_weights,
                    target_weights=target_weights,
                    equity=equity,
                    equity_peak=equity_peak,
                    daily_return=daily_return,
                )
                selected_assets = [
                    asset for asset in self.symbols if abs(float(target_weights.get(asset, 0.0))) > 1e-12
                ]
                turnover = float((target_weights - before_weights).abs().sum())
                if turnover > 0.0 and cost_rate > 0.0:
                    trade_cost = equity * turnover * cost_rate
                    equity *= max(0.0, 1.0 - turnover * cost_rate)
                self._append_rebalance_rows(
                    rows=rebalance_rows,
                    holdings=holding_rows,
                    date=current_date,
                    target_weights=target_weights,
                    selected_assets=selected_assets,
                    ranked_assets=ranked_assets,
                    score=score,
                    eligible=eligible,
                    equity=equity,
                    turnover=turnover,
                    cost_rate=cost_rate,
                    trade_cost=trade_cost,
                )
                self._append_rebalance_trade_rows(
                    rows=rebalance_trade_rows,
                    date=current_date,
                    before_weights=before_weights,
                    target_weights=target_weights,
                    selected_assets=selected_assets,
                    ranked_assets=ranked_assets,
                    score=score,
                    eligible=eligible,
                    selector=selector,
                    turnover=turnover,
                    trade_cost=trade_cost,
                )
                previous_weights = target_weights

            equity_row = {
                "Time": current_date,
                "Equity_value": equity,
                "Portfolio_return": daily_return,
                "Turnover": turnover,
                "Trade_cost": trade_cost,
                "Selected_count": int((previous_weights > 0).sum()),
                "Gross_exposure": float(previous_weights.sum()),
                "Cash_weight": max(0.0, 1.0 - float(previous_weights.sum())),
            }
            for asset in self.symbols:
                equity_row[f"Weight_{asset}"] = float(previous_weights.get(asset, 0.0))
                equity_row[f"Contribution_{asset}"] = float(asset_contribution.get(asset, 0.0))
            equity_rows.append(equity_row)
            previous_close = close_row

        equity_curve = pd.DataFrame(equity_rows)
        validation_report = self._attach_rust_accounting_validation(
            validation_report=self.validation_report,
            equity_curve=equity_curve,
            cost_rate=cost_rate,
        )
        validation_report = self._attach_cost_accounting_validation(
            validation_report=validation_report,
            equity_curve=equity_curve,
            cost_rate=cost_rate,
        )
        validation_report = self._risk_validation_report(validation_report, risk_gate_events)
        return MultiAssetBacktestResult(
            strategy_id=str(self.config.get("strategy_id") or "multi_asset_portfolio"),
            equity_curve=equity_curve,
            holdings=pd.DataFrame(holding_rows),
            rebalance_audit=pd.DataFrame(rebalance_rows),
            rebalance_trades=pd.DataFrame(rebalance_trade_rows),
            feature_cache=dict(self.feature_builder.stats),
            config=self.config,
            validation_report=validation_report,
            risk_gate_events=pd.DataFrame(risk_gate_events),
        )

    def _can_use_fast_daily_rank_path(
        self,
        *,
        rebalance_dates: List[pd.Timestamp],
        allocation_cfg: Dict[str, Any],
    ) -> bool:
        if len(rebalance_dates) != len(self.close.index):
            return False
        allocation_method = str((allocation_cfg or {}).get("method", "equal_weight")).strip().lower()
        if allocation_method not in {"equal_weight", "equal_weights"}:
            return False
        risk_cfg = self.config.get("risk", {}) if isinstance(self.config.get("risk"), dict) else {}
        if bool(risk_cfg.get("allow_short", False)):
            return False
        return True

    def _can_use_fast_target_weight_path(
        self,
        *,
        rebalance_dates: List[pd.Timestamp],
        allocation_cfg: Dict[str, Any],
    ) -> bool:
        allocation_method = str((allocation_cfg or {}).get("method", "")).strip().lower()
        if allocation_method not in {
            "signal_state",
            "signal_target_weight",
            "target_weight_frame",
            "target_weights_frame",
            "explicit_target_weights",
        }:
            return False
        frame_name = str(
            (allocation_cfg or {}).get("frame")
            or (allocation_cfg or {}).get("target_weight_frame")
            or (allocation_cfg or {}).get("target_weights_frame")
            or "target_weight"
        ).strip().lower()
        if frame_name not in self.market_data:
            return False
        risk_cfg = self.config.get("risk", {}) if isinstance(self.config.get("risk"), dict) else {}
        if bool(risk_cfg.get("allow_short", False)):
            return False
        if len(rebalance_dates) == len(self.close.index):
            return True
        if len(self.symbols) == 1 and allocation_method in {"signal_state", "signal_target_weight"}:
            return self._rebalance_trigger_op() == "signal.change"
        return False

    def _can_use_single_asset_signal_target_fast_path(self, allocation_cfg: Dict[str, Any]) -> bool:
        if len(self.symbols) != 1:
            return False
        if self._rebalance_trigger_op() != "signal.change":
            return False
        allocation_method = str((allocation_cfg or {}).get("method", "")).strip().lower()
        if allocation_method not in {"signal_state", "signal_target_weight"}:
            return False
        frame_name = str(
            (allocation_cfg or {}).get("frame")
            or (allocation_cfg or {}).get("target_weight_frame")
            or (allocation_cfg or {}).get("target_weights_frame")
            or "target_weight"
        ).strip().lower()
        if frame_name not in self.market_data:
            return False
        risk_cfg = self.config.get("risk", {}) if isinstance(self.config.get("risk"), dict) else {}
        return not bool(risk_cfg.get("allow_short", False))

    def _execution_is_next_bar_open_signal_state(self, allocation_cfg: Dict[str, Any]) -> bool:
        allocation_method = str((allocation_cfg or {}).get("method", "")).strip().lower()
        if allocation_method not in {"signal_state", "signal_target_weight"}:
            return False
        fill_model = self._fill_model_config()
        timing = str(fill_model.get("timing", "")).strip().lower()
        if timing != "bar_offset":
            return False
        entry_price = str(fill_model.get("entry_price") or "").strip().lower()
        exit_price = str(fill_model.get("exit_price") or entry_price).strip().lower()
        entry_delay = self._non_negative_int(fill_model.get("entry_delay_bars"), default=0)
        exit_delay = self._non_negative_int(fill_model.get("exit_delay_bars"), default=0)
        return entry_price == "open" and exit_price == "open" and entry_delay == 1 and exit_delay == 1

    def _execution_cost_rate(self) -> float:
        cost_cfg = self._fill_model_config().get("cost", {})
        transaction_cost = self._float(cost_cfg.get("transaction_cost", cost_cfg.get("fee_ratio", 0.0)))
        slippage = self._float(cost_cfg.get("slippage", 0.0))
        return max(0.0, transaction_cost + slippage)

    def _run_fast_target_weight_accounting(
        self,
        *,
        allocation_cfg: Dict[str, Any],
        cost_rate: float,
    ) -> MultiAssetBacktestResult:
        """Numpy accounting path for explicit target-weight strategies.

        This is the hot path for single-asset strategies after they are adapted
        into the unified portfolio contract.
        """

        index = pd.DatetimeIndex(self.close.index)
        symbols = list(self.symbols)
        symbol_count = len(symbols)
        close_values = self.close.reindex(columns=symbols).to_numpy(dtype=float, copy=False)
        returns_values = np.zeros_like(close_values, dtype=float)
        if len(close_values) > 1:
            previous = close_values[:-1, :]
            current = close_values[1:, :]
            with np.errstate(divide="ignore", invalid="ignore"):
                returns_values[1:, :] = current / previous - 1.0
            returns_values[~np.isfinite(returns_values)] = 0.0

        frame_name = str(
            (allocation_cfg or {}).get("frame")
            or (allocation_cfg or {}).get("target_weight_frame")
            or (allocation_cfg or {}).get("target_weights_frame")
            or "target_weight"
        ).strip().lower()
        target_values = (
            self.market_data[frame_name]
            .reindex(index=index, columns=symbols)
            .apply(pd.to_numeric, errors="coerce")
            .fillna(0.0)
            .to_numpy(dtype=float, copy=False)
        )
        target_values = np.clip(target_values, 0.0, None)
        risk_cfg = self.config.get("risk", {}) if isinstance(self.config.get("risk"), dict) else {}
        max_gross = self._float(risk_cfg.get("max_gross_exposure", 1.0), default=1.0)
        if max_gross > 0.0:
            gross = np.abs(target_values).sum(axis=1)
            overweight = gross > max_gross
            if np.any(overweight):
                target_values[overweight] *= (max_gross / gross[overweight])[:, None]
        if symbol_count == 1:
            if self._execution_is_next_bar_open_signal_state(allocation_cfg):
                return self._run_fast_single_asset_next_bar_open_target_weight_accounting(
                    index=index,
                    symbols=symbols,
                    close_values=close_values[:, 0],
                    target_values=target_values[:, 0],
                    cost_rate=cost_rate,
                )
            return self._run_fast_single_asset_target_weight_accounting(
                index=index,
                symbols=symbols,
                returns_values=returns_values[:, 0],
                target_values=target_values[:, 0],
                cost_rate=cost_rate,
            )

        previous_weights = np.zeros(symbol_count, dtype=float)
        equity = 100.0
        equity_peak = equity
        risk_controller = self._risk_gate_controller()
        risk_gate_events: List[Dict[str, Any]] = []
        row_count = len(index)
        equity_values = np.zeros(row_count, dtype=float)
        portfolio_returns = np.zeros(row_count, dtype=float)
        turnover_values = np.zeros(row_count, dtype=float)
        trade_cost_values = np.zeros(row_count, dtype=float)
        selected_counts = np.zeros(row_count, dtype=int)
        gross_exposure_values = np.zeros(row_count, dtype=float)
        cash_weight_values = np.zeros(row_count, dtype=float)
        weight_values = np.zeros((row_count, symbol_count), dtype=float)
        contribution_values = np.zeros((row_count, symbol_count), dtype=float)
        holding_rows: List[Dict[str, Any]] = []
        rebalance_rows: List[Dict[str, Any]] = []
        rebalance_trade_rows: List[Dict[str, Any]] = []
        eligible_row = np.ones(symbol_count, dtype=bool)
        selector = {"rank_by": "target_weight"}

        for row_idx, current_date in enumerate(index):
            returns = returns_values[row_idx]
            pre_return_weights = previous_weights.copy()
            contribution = pre_return_weights * returns
            daily_return = float(np.nansum(contribution))
            if row_idx > 0:
                equity *= 1.0 + daily_return
                denominator = 1.0 + daily_return
                if denominator > 0.0:
                    previous_weights = np.nan_to_num(
                        pre_return_weights * (1.0 + returns) / denominator,
                        nan=0.0,
                        posinf=0.0,
                        neginf=0.0,
                    )
                equity_peak = max(equity_peak, equity)

            before_weights = previous_weights.copy()
            target_weights = np.nan_to_num(
                target_values[row_idx].astype(float, copy=True),
                nan=0.0,
                posinf=0.0,
                neginf=0.0,
            )
            target_weights = self._apply_risk_gates(
                controller=risk_controller,
                events=risk_gate_events,
                timestamp=current_date,
                before_weights=before_weights,
                target_weights=target_weights,
                equity=equity,
                equity_peak=equity_peak,
                daily_return=daily_return,
                symbols=symbols,
            )
            selected_indices = np.flatnonzero(np.abs(target_weights) > 1e-12)
            selected_assets = [symbols[int(idx)] for idx in selected_indices]
            ranked_assets = selected_assets
            turnover = float(np.abs(target_weights - before_weights).sum())
            trade_cost = 0.0
            if turnover > 0.0 and cost_rate > 0.0:
                trade_cost = equity * turnover * cost_rate
                equity *= max(0.0, 1.0 - turnover * cost_rate)
            previous_weights = target_weights

            if turnover > 1e-12:
                self._append_fast_rebalance_rows(
                    rebalance_rows=rebalance_rows,
                    holding_rows=holding_rows,
                    trade_rows=rebalance_trade_rows,
                    date=current_date,
                    before_weights=before_weights,
                    target_weights=target_weights,
                    selected_assets=selected_assets,
                    ranked_assets=ranked_assets,
                    score_row=target_weights,
                    eligible_row=eligible_row,
                    selector=selector,
                    equity=equity,
                    turnover=turnover,
                    cost_rate=cost_rate,
                    trade_cost=trade_cost,
                    symbols=symbols,
                )

            gross_exposure = float(previous_weights.sum())
            equity_values[row_idx] = equity
            portfolio_returns[row_idx] = daily_return
            turnover_values[row_idx] = turnover
            trade_cost_values[row_idx] = trade_cost
            selected_counts[row_idx] = int((previous_weights > 0).sum())
            gross_exposure_values[row_idx] = gross_exposure
            cash_weight_values[row_idx] = max(0.0, 1.0 - gross_exposure)
            weight_values[row_idx, :] = previous_weights
            contribution_values[row_idx, :] = contribution

        equity_data: Dict[str, Any] = {
            "Time": index,
            "Equity_value": equity_values,
            "Portfolio_return": portfolio_returns,
            "Turnover": turnover_values,
            "Trade_cost": trade_cost_values,
            "Selected_count": selected_counts,
            "Gross_exposure": gross_exposure_values,
            "Cash_weight": cash_weight_values,
        }
        for asset_idx, asset in enumerate(symbols):
            equity_data[f"Weight_{asset}"] = weight_values[:, asset_idx]
            equity_data[f"Contribution_{asset}"] = contribution_values[:, asset_idx]
        equity_curve = pd.DataFrame(equity_data)
        validation_report = self._attach_rust_accounting_validation(
            validation_report=self.validation_report,
            equity_curve=equity_curve,
            cost_rate=cost_rate,
        )
        validation_report = self._attach_cost_accounting_validation(
            validation_report=validation_report,
            equity_curve=equity_curve,
            cost_rate=cost_rate,
        )
        validation_report = self._risk_validation_report(validation_report, risk_gate_events)
        validation_report["accounting_fast_path"] = "target_weight_numpy"
        return MultiAssetBacktestResult(
            strategy_id=str(self.config.get("strategy_id") or "multi_asset_portfolio"),
            equity_curve=equity_curve,
            holdings=pd.DataFrame(holding_rows),
            rebalance_audit=pd.DataFrame(rebalance_rows),
            rebalance_trades=pd.DataFrame(rebalance_trade_rows),
            feature_cache=dict(self.feature_builder.stats),
            config=self.config,
            validation_report=validation_report,
            risk_gate_events=pd.DataFrame(risk_gate_events),
        )

    def _run_fast_single_asset_next_bar_open_target_weight_accounting(
        self,
        *,
        index: pd.DatetimeIndex,
        symbols: List[str],
        close_values: np.ndarray,
        target_values: np.ndarray,
        cost_rate: float,
    ) -> MultiAssetBacktestResult:
        asset = symbols[0]
        open_frame = self.market_data.get("open")
        if not isinstance(open_frame, pd.DataFrame):
            raise ValueError("fill_model price=open requires open price market data")
        open_values = (
            open_frame.reindex(index=index, columns=symbols)
            .apply(pd.to_numeric, errors="coerce")
            .to_numpy(dtype=float, copy=False)[:, 0]
        )
        close = np.nan_to_num(close_values.astype(float, copy=False), nan=np.nan)
        open_ = np.nan_to_num(open_values.astype(float, copy=False), nan=np.nan)
        if np.isnan(open_).any() or np.isnan(close).any():
            raise ValueError("bar_offset open/open accounting requires finite open and close prices")

        targets = np.nan_to_num(target_values.astype(float, copy=True), nan=0.0, posinf=0.0, neginf=0.0)
        targets = np.clip(targets, 0.0, None)
        row_count = len(index)
        previous_weight = 0.0
        equity = 100.0
        equity_peak = equity
        risk_controller = self._risk_gate_controller()
        risk_gate_events: List[Dict[str, Any]] = []
        equity_values = np.zeros(row_count, dtype=float)
        portfolio_returns = np.zeros(row_count, dtype=float)
        turnover_values = np.zeros(row_count, dtype=float)
        trade_cost_values = np.zeros(row_count, dtype=float)
        selected_counts = np.zeros(row_count, dtype=int)
        gross_exposure_values = np.zeros(row_count, dtype=float)
        cash_weight_values = np.zeros(row_count, dtype=float)
        weight_values = np.zeros(row_count, dtype=float)
        contribution_values = np.zeros(row_count, dtype=float)
        holding_rows: List[Dict[str, Any]] = []
        rebalance_rows: List[Dict[str, Any]] = []
        rebalance_trade_rows: List[Dict[str, Any]] = []

        for row_idx, current_date in enumerate(index):
            start_equity = equity
            overnight_return = 0.0
            if row_idx > 0 and close[row_idx - 1] != 0.0:
                overnight_return = float(open_[row_idx] / close[row_idx - 1] - 1.0)
            overnight_contribution = previous_weight * overnight_return
            if row_idx > 0:
                equity *= 1.0 + overnight_contribution
                denominator = 1.0 + overnight_contribution
                if denominator > 0.0:
                    previous_weight = previous_weight * (1.0 + overnight_return) / denominator
                equity_peak = max(equity_peak, equity)

            before_weight = previous_weight
            target_weight = float(targets[row_idx])
            adjusted = self._apply_risk_gates(
                controller=risk_controller,
                events=risk_gate_events,
                timestamp=current_date,
                before_weights=np.array([before_weight], dtype=float),
                target_weights=np.array([target_weight], dtype=float),
                equity=equity,
                equity_peak=equity_peak,
                daily_return=overnight_contribution,
                symbols=symbols,
            )
            target_weight = float(adjusted[0])
            turnover = abs(target_weight - before_weight)
            trade_cost = 0.0
            if turnover > 0.0 and cost_rate > 0.0:
                trade_cost = equity * turnover * cost_rate
                equity *= max(0.0, 1.0 - turnover * cost_rate)
            previous_weight = target_weight

            if turnover > 1e-12:
                selected_assets = [asset] if target_weight > 1e-12 else []
                self._append_fast_rebalance_rows(
                    rebalance_rows=rebalance_rows,
                    holding_rows=holding_rows,
                    trade_rows=rebalance_trade_rows,
                    date=current_date,
                    before_weights=np.array([before_weight], dtype=float),
                    target_weights=np.array([target_weight], dtype=float),
                    selected_assets=selected_assets,
                    ranked_assets=selected_assets,
                    score_row=np.array([target_weight], dtype=float),
                    eligible_row=np.array([True], dtype=bool),
                    selector={"rank_by": "target_weight"},
                    equity=equity,
                    turnover=turnover,
                    cost_rate=cost_rate,
                    trade_cost=trade_cost,
                    symbols=symbols,
                )

            intraday_return = 0.0
            if open_[row_idx] != 0.0:
                intraday_return = float(close[row_idx] / open_[row_idx] - 1.0)
            intraday_contribution = previous_weight * intraday_return
            equity *= 1.0 + intraday_contribution
            denominator = 1.0 + intraday_contribution
            if denominator > 0.0:
                previous_weight = previous_weight * (1.0 + intraday_return) / denominator
            equity_peak = max(equity_peak, equity)

            total_return = (equity / start_equity - 1.0) if start_equity > 0.0 else 0.0
            equity_values[row_idx] = equity
            portfolio_returns[row_idx] = total_return
            turnover_values[row_idx] = turnover
            trade_cost_values[row_idx] = trade_cost
            selected_counts[row_idx] = 1 if previous_weight > 1e-12 else 0
            gross_exposure_values[row_idx] = previous_weight
            cash_weight_values[row_idx] = max(0.0, 1.0 - previous_weight)
            weight_values[row_idx] = previous_weight
            contribution_values[row_idx] = total_return

        equity_curve = pd.DataFrame(
            {
                "Time": index,
                "Equity_value": equity_values,
                "Portfolio_return": portfolio_returns,
                "Turnover": turnover_values,
                "Trade_cost": trade_cost_values,
                "Selected_count": selected_counts,
                "Gross_exposure": gross_exposure_values,
                "Cash_weight": cash_weight_values,
                f"Weight_{asset}": weight_values,
                f"Contribution_{asset}": contribution_values,
            }
        )
        validation_report = self._attach_rust_accounting_validation(
            validation_report=self.validation_report,
            equity_curve=equity_curve,
            cost_rate=cost_rate,
        )
        validation_report = self._attach_cost_accounting_validation(
            validation_report=validation_report,
            equity_curve=equity_curve,
            cost_rate=cost_rate,
        )
        validation_report = self._risk_validation_report(validation_report, risk_gate_events)
        validation_report["accounting_fast_path"] = "single_asset_next_bar_open_target_weight_numpy"
        validation_report["fill_semantics"] = "signal_on_bar_t_fills_at_t_plus_1_open"
        return MultiAssetBacktestResult(
            strategy_id=str(self.config.get("strategy_id") or "multi_asset_portfolio"),
            equity_curve=equity_curve,
            holdings=pd.DataFrame(holding_rows),
            rebalance_audit=pd.DataFrame(rebalance_rows),
            rebalance_trades=pd.DataFrame(rebalance_trade_rows),
            feature_cache=dict(self.feature_builder.stats),
            config=self.config,
            validation_report=validation_report,
            risk_gate_events=pd.DataFrame(risk_gate_events),
        )

    def _run_fast_single_asset_target_weight_accounting(
        self,
        *,
        index: pd.DatetimeIndex,
        symbols: List[str],
        returns_values: np.ndarray,
        target_values: np.ndarray,
        cost_rate: float,
    ) -> MultiAssetBacktestResult:
        asset = symbols[0]
        row_count = len(index)
        targets = np.nan_to_num(target_values.astype(float, copy=True), nan=0.0, posinf=0.0, neginf=0.0)
        targets = np.clip(targets, 0.0, None)
        returns = np.nan_to_num(returns_values.astype(float, copy=False), nan=0.0, posinf=0.0, neginf=0.0)

        previous_weight = 0.0
        equity = 100.0
        equity_peak = equity
        risk_controller = self._risk_gate_controller()
        risk_gate_events: List[Dict[str, Any]] = []
        equity_values = np.zeros(row_count, dtype=float)
        portfolio_returns = np.zeros(row_count, dtype=float)
        turnover_values = np.zeros(row_count, dtype=float)
        trade_cost_values = np.zeros(row_count, dtype=float)
        selected_counts = np.zeros(row_count, dtype=int)
        gross_exposure_values = np.zeros(row_count, dtype=float)
        cash_weight_values = np.zeros(row_count, dtype=float)
        weight_values = np.zeros(row_count, dtype=float)
        contribution_values = np.zeros(row_count, dtype=float)
        holding_rows: List[Dict[str, Any]] = []
        rebalance_rows: List[Dict[str, Any]] = []
        rebalance_trade_rows: List[Dict[str, Any]] = []

        for row_idx, current_date in enumerate(index):
            ret = float(returns[row_idx])
            contribution = previous_weight * ret
            if row_idx > 0:
                equity *= 1.0 + contribution
                denominator = 1.0 + contribution
                if denominator > 0.0:
                    previous_weight = previous_weight * (1.0 + ret) / denominator
                equity_peak = max(equity_peak, equity)

            before_weight = previous_weight
            target_weight = float(targets[row_idx])
            adjusted = self._apply_risk_gates(
                controller=risk_controller,
                events=risk_gate_events,
                timestamp=current_date,
                before_weights=np.array([before_weight], dtype=float),
                target_weights=np.array([target_weight], dtype=float),
                equity=equity,
                equity_peak=equity_peak,
                daily_return=contribution,
                symbols=symbols,
            )
            target_weight = float(adjusted[0])
            turnover = abs(target_weight - before_weight)
            trade_cost = 0.0
            if turnover > 0.0 and cost_rate > 0.0:
                trade_cost = equity * turnover * cost_rate
                equity *= max(0.0, 1.0 - turnover * cost_rate)
            previous_weight = target_weight

            if turnover > 1e-12:
                selected_assets = [asset] if target_weight > 1e-12 else []
                self._append_fast_rebalance_rows(
                    rebalance_rows=rebalance_rows,
                    holding_rows=holding_rows,
                    trade_rows=rebalance_trade_rows,
                    date=current_date,
                    before_weights=np.array([before_weight], dtype=float),
                    target_weights=np.array([target_weight], dtype=float),
                    selected_assets=selected_assets,
                    ranked_assets=selected_assets,
                    score_row=np.array([target_weight], dtype=float),
                    eligible_row=np.array([True], dtype=bool),
                    selector={"rank_by": "target_weight"},
                    equity=equity,
                    turnover=turnover,
                    cost_rate=cost_rate,
                    trade_cost=trade_cost,
                    symbols=symbols,
                )

            equity_values[row_idx] = equity
            portfolio_returns[row_idx] = contribution
            turnover_values[row_idx] = turnover
            trade_cost_values[row_idx] = trade_cost
            selected_counts[row_idx] = 1 if previous_weight > 1e-12 else 0
            gross_exposure_values[row_idx] = previous_weight
            cash_weight_values[row_idx] = max(0.0, 1.0 - previous_weight)
            weight_values[row_idx] = previous_weight
            contribution_values[row_idx] = contribution

        equity_curve = pd.DataFrame(
            {
                "Time": index,
                "Equity_value": equity_values,
                "Portfolio_return": portfolio_returns,
                "Turnover": turnover_values,
                "Trade_cost": trade_cost_values,
                "Selected_count": selected_counts,
                "Gross_exposure": gross_exposure_values,
                "Cash_weight": cash_weight_values,
                f"Weight_{asset}": weight_values,
                f"Contribution_{asset}": contribution_values,
            }
        )
        validation_report = self._attach_rust_accounting_validation(
            validation_report=self.validation_report,
            equity_curve=equity_curve,
            cost_rate=cost_rate,
        )
        validation_report = self._attach_cost_accounting_validation(
            validation_report=validation_report,
            equity_curve=equity_curve,
            cost_rate=cost_rate,
        )
        validation_report = self._risk_validation_report(validation_report, risk_gate_events)
        validation_report["accounting_fast_path"] = "single_asset_target_weight_numpy"
        return MultiAssetBacktestResult(
            strategy_id=str(self.config.get("strategy_id") or "multi_asset_portfolio"),
            equity_curve=equity_curve,
            holdings=pd.DataFrame(holding_rows),
            rebalance_audit=pd.DataFrame(rebalance_rows),
            rebalance_trades=pd.DataFrame(rebalance_trade_rows),
            feature_cache=dict(self.feature_builder.stats),
            config=self.config,
            validation_report=validation_report,
            risk_gate_events=pd.DataFrame(risk_gate_events),
        )

    def _run_bar_offset_round_trip_accounting(
        self,
        *,
        allocation_cfg: Dict[str, Any],
        cost_rate: float,
    ) -> MultiAssetBacktestResult:
        spec = self._bar_offset_execution_spec()
        index = pd.DatetimeIndex(self.close.index)
        symbols = list(self.symbols)
        frame_overrides = [
            key
            for key in ("frame", "target_weight_frame", "target_weights_frame")
            if (allocation_cfg or {}).get(key)
        ]
        if frame_overrides:
            raise ValueError("bar_offset execution does not support allocation frame overrides")
        frame_name = "target_weight"
        if frame_name not in self.market_data:
            raise ValueError("bar_offset execution requires a signal target_weight frame")
        allocation_method = str((allocation_cfg or {}).get("method", "")).strip().lower()
        if allocation_method not in {"signal_state", "signal_target_weight"}:
            raise ValueError(
                "bar_offset execution currently supports only signals-driven signal_state or signal_target_weight configs"
            )

        target_values = (
            self.market_data[frame_name]
            .reindex(index=index, columns=symbols)
            .apply(pd.to_numeric, errors="coerce")
            .fillna(0.0)
        )
        self._require_explicit_bar_offset_price_frames(spec)
        entry_prices = self._execution_price_frame(spec["entry_price"]).apply(pd.to_numeric, errors="coerce")
        exit_prices = self._execution_price_frame(spec["exit_price"]).apply(pd.to_numeric, errors="coerce")

        risk_cfg = self.config.get("risk", {}) if isinstance(self.config.get("risk"), dict) else {}
        max_gross = self._float(risk_cfg.get("max_gross_exposure", 1.0), default=1.0)
        risk_controller = self._risk_gate_controller()
        risk_gate_events: List[Dict[str, Any]] = []
        scheduled_by_entry: Dict[int, List[Dict[str, Any]]] = {}
        scheduled_by_exit: Dict[int, List[Dict[str, Any]]] = {}
        last_exit_idx = -1
        last_exit_phase = -1

        for signal_idx, signal_date in enumerate(index):
            raw_weights = target_values.iloc[signal_idx].astype(float)
            weights = pd.Series(np.nan_to_num(raw_weights.to_numpy(dtype=float), nan=0.0), index=symbols, dtype=float)
            weights = weights.clip(lower=0.0)
            gross = float(weights.abs().sum())
            if gross <= 1e-12:
                continue
            if max_gross > 0.0 and gross > max_gross:
                weights *= max_gross / gross
                gross = float(weights.abs().sum())
            weights_array = self._apply_risk_gates(
                controller=risk_controller,
                events=risk_gate_events,
                timestamp=signal_date,
                before_weights=np.zeros(len(symbols), dtype=float),
                target_weights=weights.to_numpy(dtype=float),
                equity=100.0,
                equity_peak=100.0,
                daily_return=0.0,
                symbols=symbols,
            )
            weights = pd.Series(weights_array, index=symbols, dtype=float).clip(lower=0.0)
            gross = float(weights.abs().sum())
            if gross <= 1e-12:
                continue

            entry_idx = signal_idx + int(spec["entry_delay_bars"])
            exit_idx = signal_idx + int(spec["exit_delay_bars"])
            if exit_idx >= len(index) or entry_idx >= len(index):
                continue
            entry_phase = self._execution_price_phase(spec["entry_price"])
            exit_phase = self._execution_price_phase(spec["exit_price"])
            if entry_idx < last_exit_idx or (entry_idx == last_exit_idx and entry_phase <= last_exit_phase):
                raise ValueError(
                    "bar_offset execution does not support overlapping positions yet. "
                    "Use a non-overlapping entry/exit schedule or add an explicitly reviewed overlapping execution model."
                )
            last_exit_idx = exit_idx
            last_exit_phase = exit_phase

            entry_row = entry_prices.iloc[entry_idx].replace(0.0, np.nan)
            exit_row = exit_prices.iloc[exit_idx].replace(0.0, np.nan)
            valid = weights.abs() > 1e-12
            valid = valid & entry_row.notna() & exit_row.notna()
            if not bool(valid.any()):
                continue
            weights = weights.where(valid, 0.0)
            gross = float(weights.abs().sum())
            if gross <= 1e-12:
                continue
            with np.errstate(divide="ignore", invalid="ignore"):
                asset_returns = (exit_row / entry_row - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
            weighted_return = float((weights * asset_returns).sum())
            event = {
                "signal_date": signal_date,
                "entry_date": index[entry_idx],
                "exit_date": index[exit_idx],
                "entry_phase": entry_phase,
                "exit_phase": exit_phase,
                "weights": weights,
                "asset_returns": asset_returns,
                "entry_row": entry_row,
                "exit_row": exit_row,
                "gross": gross,
                "weighted_return": weighted_return,
            }
            scheduled_by_entry.setdefault(entry_idx, []).append(event)
            scheduled_by_exit.setdefault(exit_idx, []).append(event)

        equity = 100.0
        equity_values = np.zeros(len(index), dtype=float)
        portfolio_returns = np.zeros(len(index), dtype=float)
        turnover_values = np.zeros(len(index), dtype=float)
        trade_cost_values = np.zeros(len(index), dtype=float)
        selected_counts = np.zeros(len(index), dtype=int)
        gross_exposure_values = np.zeros(len(index), dtype=float)
        cash_weight_values = np.ones(len(index), dtype=float)
        weight_values = np.zeros((len(index), len(symbols)), dtype=float)
        contribution_values = np.zeros((len(index), len(symbols)), dtype=float)
        rebalance_trade_rows: List[Dict[str, Any]] = []
        holding_rows: List[Dict[str, Any]] = []
        rebalance_rows: List[Dict[str, Any]] = []
        current_weights = pd.Series(0.0, index=symbols, dtype=float)

        def process_entry(event: Dict[str, Any]) -> float:
            nonlocal equity, current_weights
            weights = event["weights"]
            gross = float(event["gross"])
            entry_cost = equity * gross * cost_rate if cost_rate > 0.0 else 0.0
            if cost_rate > 0.0:
                equity *= max(0.0, 1.0 - gross * cost_rate)
            current_weights = weights.copy()
            for asset in [symbol for symbol in symbols if abs(float(weights.get(symbol, 0.0))) > 1e-12]:
                asset_weight = float(weights.get(asset, 0.0))
                entry_cost_alloc = entry_cost * abs(asset_weight) / gross if gross > 0.0 else 0.0
                rebalance_trade_rows.append(
                    {
                        "Time": event["entry_date"],
                        "Signal_time": event["signal_date"],
                        "Fill_time": event["entry_date"],
                        "Exit_time": event["exit_date"],
                        "Asset": asset,
                        "Action": "buy",
                        "Before_weight": 0.0,
                        "After_weight": asset_weight,
                        "Allocated_weight": asset_weight,
                        "Allocated_cost": entry_cost_alloc,
                        "Entry_price": float(event["entry_row"].get(asset, np.nan)),
                        "Exit_price": np.nan,
                        "Fill_semantics": spec["return_clock"],
                    }
                )
            return entry_cost

        def process_exit(event: Dict[str, Any]) -> Tuple[float, pd.Series]:
            nonlocal equity, current_weights
            weights = event["weights"]
            gross = float(event["gross"])
            equity *= 1.0 + float(event["weighted_return"])
            exit_cost = equity * gross * cost_rate if cost_rate > 0.0 else 0.0
            if cost_rate > 0.0:
                equity *= max(0.0, 1.0 - gross * cost_rate)
            current_weights = current_weights.where(weights.abs() <= 1e-12, 0.0)
            for asset in [symbol for symbol in symbols if abs(float(weights.get(symbol, 0.0))) > 1e-12]:
                asset_weight = float(weights.get(asset, 0.0))
                exit_cost_alloc = exit_cost * abs(asset_weight) / gross if gross > 0.0 else 0.0
                rebalance_trade_rows.append(
                    {
                        "Time": event["exit_date"],
                        "Signal_time": event["signal_date"],
                        "Fill_time": event["exit_date"],
                        "Exit_time": event["exit_date"],
                        "Asset": asset,
                        "Action": "exit",
                        "Before_weight": asset_weight,
                        "After_weight": 0.0,
                        "Allocated_weight": 0.0,
                        "Allocated_cost": exit_cost_alloc,
                        "Entry_price": float(event["entry_row"].get(asset, np.nan)),
                        "Exit_price": float(event["exit_row"].get(asset, np.nan)),
                        "Fill_semantics": spec["return_clock"],
                    }
                )
            return exit_cost, weights * event["asset_returns"]

        for row_idx, current_date in enumerate(index):
            start_equity = equity
            row_turnover = 0.0
            row_cost = 0.0
            row_contribution = pd.Series(0.0, index=symbols, dtype=float)

            for phase in (0, 1):
                for event in scheduled_by_exit.get(row_idx, []):
                    if int(event["exit_phase"]) != phase:
                        continue
                    exit_cost, contribution = process_exit(event)
                    row_turnover += float(event["gross"])
                    row_cost += exit_cost
                    row_contribution = row_contribution.add(contribution, fill_value=0.0)
                for event in scheduled_by_entry.get(row_idx, []):
                    if int(event["entry_phase"]) != phase:
                        continue
                    entry_cost = process_entry(event)
                    row_turnover += float(event["gross"])
                    row_cost += entry_cost

            row_return = (equity / start_equity - 1.0) if start_equity > 0.0 else 0.0
            equity_values[row_idx] = equity
            portfolio_returns[row_idx] = row_return
            turnover_values[row_idx] = row_turnover
            trade_cost_values[row_idx] = row_cost
            selected_counts[row_idx] = int((current_weights.abs() > 1e-12).sum())
            gross_exposure_values[row_idx] = float(current_weights.abs().sum())
            cash_weight_values[row_idx] = max(0.0, 1.0 - gross_exposure_values[row_idx])
            for asset_idx, asset in enumerate(symbols):
                weight_values[row_idx, asset_idx] = float(current_weights.get(asset, 0.0))
                contribution_values[row_idx, asset_idx] = float(row_contribution.get(asset, 0.0))
                if abs(float(current_weights.get(asset, 0.0))) > 1e-12:
                    holding_rows.append(
                        {
                            "Time": current_date,
                            "Asset": asset,
                            "Weight": float(current_weights.get(asset, 0.0)),
                            "Equity_value": equity,
                        }
                    )

        equity_data: Dict[str, Any] = {
            "Time": index,
            "Equity_value": equity_values,
            "Portfolio_return": portfolio_returns,
            "Turnover": turnover_values,
            "Trade_cost": trade_cost_values,
            "Selected_count": selected_counts,
            "Gross_exposure": gross_exposure_values,
            "Cash_weight": cash_weight_values,
        }
        for asset_idx, asset in enumerate(symbols):
            equity_data[f"Weight_{asset}"] = weight_values[:, asset_idx]
            equity_data[f"Contribution_{asset}"] = contribution_values[:, asset_idx]
        equity_curve = pd.DataFrame(equity_data)
        validation_report = self._attach_rust_accounting_validation(
            validation_report=self.validation_report,
            equity_curve=equity_curve,
            cost_rate=cost_rate,
        )
        validation_report = self._attach_cost_accounting_validation(
            validation_report=validation_report,
            equity_curve=equity_curve,
            cost_rate=cost_rate,
        )
        validation_report = self._risk_validation_report(validation_report, risk_gate_events)
        validation_report["accounting_backend"] = "bar_offset_round_trip"
        validation_report["fill_semantics"] = spec["return_clock"]
        validation_report["return_clock"] = spec["return_clock"]
        validation_report["bar_offset"] = {
            "entry_price": spec["entry_price"],
            "entry_delay_bars": spec["entry_delay_bars"],
            "exit_price": spec["exit_price"],
            "exit_delay_bars": spec["exit_delay_bars"],
        }
        return MultiAssetBacktestResult(
            strategy_id=str(self.config.get("strategy_id") or "multi_asset_portfolio"),
            equity_curve=equity_curve,
            holdings=pd.DataFrame(holding_rows),
            rebalance_audit=pd.DataFrame(rebalance_rows),
            rebalance_trades=pd.DataFrame(rebalance_trade_rows),
            feature_cache=dict(self.feature_builder.stats),
            config=self.config,
            validation_report=validation_report,
            risk_gate_events=pd.DataFrame(risk_gate_events),
        )

    def _run_fast_daily_rank_accounting(
        self,
        *,
        features: Dict[str, pd.DataFrame],
        eligible: pd.DataFrame,
        score: pd.DataFrame,
        selector: Dict[str, Any],
        allocation_cfg: Dict[str, Any],
        cost_rate: float,
    ) -> MultiAssetBacktestResult:
        """Numpy accounting path for daily long-only top-N portfolio policies.

        This keeps the same artifact contract as the generic pandas loop, but
        avoids thousands of per-row Series allocations for parameter matrices.
        """

        index = pd.DatetimeIndex(self.close.index)
        symbols = list(self.symbols)
        symbol_count = len(symbols)
        close_values = self.close.reindex(columns=symbols).to_numpy(dtype=float, copy=False)
        returns_values = np.zeros_like(close_values, dtype=float)
        if len(close_values) > 1:
            previous = close_values[:-1, :]
            current = close_values[1:, :]
            with np.errstate(divide="ignore", invalid="ignore"):
                returns_values[1:, :] = current / previous - 1.0
            returns_values[~np.isfinite(returns_values)] = 0.0

        eligible_values = (
            eligible.reindex(index=index, columns=symbols)
            .fillna(False)
            .to_numpy(dtype=bool, copy=False)
        )
        score_values = (
            score.reindex(index=index, columns=symbols)
            .apply(pd.to_numeric, errors="coerce")
            .to_numpy(dtype=float, copy=False)
        )
        ascending = str(selector.get("rank_order", "desc")).lower() in {"asc", "ascending", "smallest"}
        top_n = min(self._positive_int(selector.get("top_n"), default=symbol_count), symbol_count)
        position_limit = self._float(allocation_cfg.get("position_limit", 1.0), default=1.0)

        previous_weights = np.zeros(symbol_count, dtype=float)
        equity = 100.0
        equity_peak = equity
        risk_controller = self._risk_gate_controller()
        risk_gate_events: List[Dict[str, Any]] = []
        equity_rows: List[Dict[str, Any]] = []
        holding_rows: List[Dict[str, Any]] = []
        rebalance_rows: List[Dict[str, Any]] = []
        rebalance_trade_rows: List[Dict[str, Any]] = []

        for row_idx, current_date in enumerate(index):
            returns = returns_values[row_idx]
            pre_return_weights = previous_weights.copy()
            contribution = pre_return_weights * returns
            daily_return = float(np.nansum(contribution))
            if row_idx > 0:
                equity *= 1.0 + daily_return
                denominator = 1.0 + daily_return
                if denominator > 0.0:
                    previous_weights = np.nan_to_num(
                        pre_return_weights * (1.0 + returns) / denominator,
                        nan=0.0,
                        posinf=0.0,
                        neginf=0.0,
                    )
                equity_peak = max(equity_peak, equity)

            before_weights = previous_weights.copy()
            score_row = score_values[row_idx]
            valid_mask = eligible_values[row_idx] & np.isfinite(score_row)
            valid_indices = np.flatnonzero(valid_mask)
            if len(valid_indices):
                sort_values = score_row[valid_indices]
                order = np.argsort(sort_values, kind="mergesort")
                if not ascending:
                    order = order[::-1]
                ranked_indices = valid_indices[order]
            else:
                ranked_indices = np.array([], dtype=int)
            selected_indices = ranked_indices[:top_n]
            target_weights = np.zeros(symbol_count, dtype=float)
            if len(selected_indices):
                per_asset_weight = min(1.0 / float(len(selected_indices)), position_limit)
                target_weights[selected_indices] = per_asset_weight
            target_weights = self._apply_risk_gates(
                controller=risk_controller,
                events=risk_gate_events,
                timestamp=current_date,
                before_weights=before_weights,
                target_weights=target_weights,
                equity=equity,
                equity_peak=equity_peak,
                daily_return=daily_return,
                symbols=symbols,
            )
            selected_indices = np.flatnonzero(np.abs(target_weights) > 1e-12)
            selected_assets = [symbols[int(idx)] for idx in selected_indices]
            ranked_assets = [symbols[int(idx)] for idx in ranked_indices]
            turnover = float(np.abs(target_weights - before_weights).sum())
            trade_cost = 0.0
            if turnover > 0.0 and cost_rate > 0.0:
                trade_cost = equity * turnover * cost_rate
                equity *= max(0.0, 1.0 - turnover * cost_rate)
            previous_weights = target_weights

            self._append_fast_rebalance_rows(
                rebalance_rows=rebalance_rows,
                holding_rows=holding_rows,
                trade_rows=rebalance_trade_rows,
                date=current_date,
                before_weights=before_weights,
                target_weights=target_weights,
                selected_assets=selected_assets,
                ranked_assets=ranked_assets,
                score_row=score_row,
                eligible_row=eligible_values[row_idx],
                selector=selector,
                equity=equity,
                turnover=turnover,
                cost_rate=cost_rate,
                trade_cost=trade_cost,
                symbols=symbols,
            )

            equity_row = {
                "Time": current_date,
                "Equity_value": equity,
                "Portfolio_return": daily_return,
                "Turnover": turnover,
                "Trade_cost": trade_cost,
                "Selected_count": int((previous_weights > 0).sum()),
                "Gross_exposure": float(previous_weights.sum()),
                "Cash_weight": max(0.0, 1.0 - float(previous_weights.sum())),
            }
            for asset_idx, asset in enumerate(symbols):
                equity_row[f"Weight_{asset}"] = float(previous_weights[asset_idx])
                equity_row[f"Contribution_{asset}"] = float(contribution[asset_idx])
            equity_rows.append(equity_row)

        equity_curve = pd.DataFrame(equity_rows)
        validation_report = self._attach_rust_accounting_validation(
            validation_report=self.validation_report,
            equity_curve=equity_curve,
            cost_rate=cost_rate,
        )
        validation_report = self._attach_cost_accounting_validation(
            validation_report=validation_report,
            equity_curve=equity_curve,
            cost_rate=cost_rate,
        )
        validation_report = self._risk_validation_report(validation_report, risk_gate_events)
        validation_report["accounting_fast_path"] = "daily_rank_numpy"
        return MultiAssetBacktestResult(
            strategy_id=str(self.config.get("strategy_id") or "multi_asset_portfolio"),
            equity_curve=equity_curve,
            holdings=pd.DataFrame(holding_rows),
            rebalance_audit=pd.DataFrame(rebalance_rows),
            rebalance_trades=pd.DataFrame(rebalance_trade_rows),
            feature_cache=dict(self.feature_builder.stats),
            config=self.config,
            validation_report=validation_report,
            risk_gate_events=pd.DataFrame(risk_gate_events),
        )

    def _append_fast_rebalance_rows(
        self,
        *,
        rebalance_rows: List[Dict[str, Any]],
        holding_rows: List[Dict[str, Any]],
        trade_rows: List[Dict[str, Any]],
        date: pd.Timestamp,
        before_weights: np.ndarray,
        target_weights: np.ndarray,
        selected_assets: List[str],
        ranked_assets: List[str],
        score_row: np.ndarray,
        eligible_row: np.ndarray,
        selector: Dict[str, Any],
        equity: float,
        turnover: float,
        cost_rate: float,
        trade_cost: float,
        symbols: List[str],
    ) -> None:
        rebalance_rows.append(
            {
                "Time": date,
                "Rebalance": True,
                "Selected_assets": selected_assets,
                "Selected_count": len(selected_assets),
                "Ranked_candidates": ranked_assets,
                "Turnover": turnover,
                "Cost_rate": cost_rate,
                "Trade_cost": trade_cost,
                "Equity_value": equity,
            }
        )
        rank_lookup = {asset: rank for rank, asset in enumerate(ranked_assets, start=1)}
        selected_lookup = set(selected_assets)
        rank_by = str(selector.get("rank_by") or "").strip()
        for rank, asset in enumerate(ranked_assets, start=1):
            asset_idx = symbols.index(asset)
            holding_rows.append(
                {
                    "Time": date,
                    "Asset": asset,
                    "Rank": rank,
                    "Selected": asset in selected_lookup,
                    "Eligible": bool(eligible_row[asset_idx]),
                    "Score": float(score_row[asset_idx]) if np.isfinite(score_row[asset_idx]) else np.nan,
                    "Target_weight": float(target_weights[asset_idx]),
                }
            )
        active_indices = np.flatnonzero(
            (np.abs(before_weights) > 1e-12)
            | (np.abs(target_weights) > 1e-12)
            | np.array([asset in rank_lookup for asset in symbols], dtype=bool)
        )
        for asset_idx in active_indices:
            asset = symbols[int(asset_idx)]
            before_weight = float(before_weights[asset_idx])
            target_weight = float(target_weights[asset_idx])
            delta = target_weight - before_weight
            abs_delta = abs(delta)
            if abs_delta <= 1e-12 and target_weight <= 1e-12 and before_weight <= 1e-12:
                continue
            if delta > 1e-12:
                action = "buy"
            elif delta < -1e-12 and target_weight <= 1e-12:
                action = "exit"
            elif delta < -1e-12:
                action = "sell"
            else:
                action = "hold"
            eligible_flag = bool(eligible_row[asset_idx])
            reason_parts: List[str] = []
            rank = rank_lookup.get(asset)
            if rank is not None:
                reason_parts.append(f"rank {rank}" + (f" by {rank_by}" if rank_by else ""))
            elif before_weight > 0.0 and target_weight <= 0.0:
                reason_parts.append("not selected at this rebalance")
            if eligible_flag:
                reason_parts.append("eligible")
            elif action != "hold":
                reason_parts.append("not eligible")
            trade_rows.append(
                {
                    "Time": date,
                    "Asset": asset,
                    "Before_weight": before_weight,
                    "Target_weight": target_weight,
                    "Trade_delta": delta,
                    "Action": action,
                    "Trade_turnover": abs_delta,
                    "Allocated_cost": trade_cost * abs_delta / turnover if turnover > 0.0 else 0.0,
                    "Selected": asset in selected_lookup,
                    "Eligible": eligible_flag,
                    "Rank": rank,
                    "Score": float(score_row[asset_idx]) if np.isfinite(score_row[asset_idx]) else np.nan,
                    "Reason": "; ".join(reason_parts) if reason_parts else "target unchanged",
                }
            )

    def _run_same_session_accounting(self) -> MultiAssetBacktestResult:
        features = self._build_features_for_backtest()
        self._ensure_signal_target_weight_frame(features)
        allocation_cfg = self.config.get("allocation", {})
        target_frame_name = str(
            (allocation_cfg or {}).get("frame")
            or (allocation_cfg or {}).get("target_weight_frame")
            or "target_weight"
        ).strip().lower()
        if target_frame_name not in self.market_data:
            raise KeyError("same-session accounting requires a signal target weight frame")

        target_weights = self.market_data[target_frame_name].reindex(
            index=self.close.index,
            columns=self.symbols,
        ).fillna(0.0)
        execution_cfg = self._fill_model_config()
        cost_cfg = execution_cfg.get("cost", {}) if isinstance(execution_cfg.get("cost"), dict) else {}
        transaction_cost = self._float(cost_cfg.get("transaction_cost", cost_cfg.get("fee_ratio", 0.0)))
        slippage = self._float(cost_cfg.get("slippage", 0.0))
        cost_rate = max(0.0, transaction_cost + slippage)
        entry_prices = self._execution_price_frame(execution_cfg.get("entry_price") or execution_cfg.get("price") or "open")
        exit_prices = self._execution_price_frame(execution_cfg.get("exit_price") or "close")

        equity = 100.0
        equity_peak = equity
        risk_controller = self._risk_gate_controller()
        risk_gate_events: List[Dict[str, Any]] = []
        equity_rows: List[Dict[str, Any]] = []
        holding_rows: List[Dict[str, Any]] = []
        rebalance_rows: List[Dict[str, Any]] = []
        rebalance_trade_rows: List[Dict[str, Any]] = []
        for current_date in self.close.index:
            weights = pd.to_numeric(target_weights.loc[current_date], errors="coerce").fillna(0.0)
            weights = self._apply_risk_gates(
                controller=risk_controller,
                events=risk_gate_events,
                timestamp=current_date,
                before_weights=pd.Series(0.0, index=self.symbols, dtype=float),
                target_weights=weights,
                equity=equity,
                equity_peak=equity_peak,
                daily_return=0.0,
            )
            gross = float(weights.abs().sum())
            selected_assets = [asset for asset in self.symbols if abs(float(weights.get(asset, 0.0))) > 1e-12]
            valid_entry = pd.to_numeric(entry_prices.loc[current_date], errors="coerce").replace(0.0, np.nan)
            valid_exit = pd.to_numeric(exit_prices.loc[current_date], errors="coerce")
            asset_returns = (valid_exit / valid_entry - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
            portfolio_return = float((weights * asset_returns).sum()) if selected_assets else 0.0
            entry_cost_factor = max(0.0, 1.0 - gross * slippage) * max(0.0, 1.0 - gross * transaction_cost)
            exit_cost_factor = entry_cost_factor
            entry_cost = equity * (1.0 - entry_cost_factor) if gross > 0.0 else 0.0
            equity_after_entry = equity * entry_cost_factor
            equity_after_return = equity_after_entry * (1.0 + portfolio_return)
            exit_cost = equity_after_return * (1.0 - exit_cost_factor) if gross > 0.0 else 0.0
            equity = equity_after_return * exit_cost_factor
            equity_peak = max(equity_peak, equity)
            trade_cost = entry_cost + exit_cost

            equity_row = {
                "Time": current_date,
                "Equity_value": equity,
                "Portfolio_return": portfolio_return,
                "Turnover": gross * 2.0 if selected_assets else 0.0,
                "Trade_cost": trade_cost,
                "Selected_count": len(selected_assets),
                "Gross_exposure": gross,
                "Cash_weight": max(0.0, 1.0 - gross),
            }
            for asset in self.symbols:
                weight = float(weights.get(asset, 0.0))
                equity_row[f"Weight_{asset}"] = 0.0
                equity_row[f"Contribution_{asset}"] = float(weight * asset_returns.get(asset, 0.0))
            equity_rows.append(equity_row)

            if not selected_assets:
                continue
            ranked_assets = selected_assets
            score = pd.DataFrame(np.nan, index=self.close.index, columns=self.symbols)
            eligible = pd.DataFrame(True, index=self.close.index, columns=self.symbols)
            self._append_rebalance_rows(
                rows=rebalance_rows,
                holdings=holding_rows,
                date=current_date,
                target_weights=weights,
                selected_assets=selected_assets,
                ranked_assets=ranked_assets,
                score=score,
                eligible=eligible,
                equity=equity,
                turnover=gross * 2.0,
                cost_rate=cost_rate,
                trade_cost=trade_cost,
            )
            for asset in selected_assets:
                weight = float(weights.get(asset, 0.0))
                side = "short" if weight < 0 else "long"
                trade_return = float(weight * asset_returns.get(asset, 0.0))
                rebalance_trade_rows.extend(
                    [
                        {
                            "Time": current_date,
                            "Asset": asset,
                            "Before_weight": 0.0,
                            "Target_weight": weight,
                            "Trade_delta": weight,
                            "Action": "new_short" if side == "short" else "buy",
                            "Trade_turnover": abs(weight),
                            "Allocated_cost": entry_cost * abs(weight) / gross if gross > 0.0 else 0.0,
                            "Selected": True,
                            "Eligible": True,
                            "Rank": ranked_assets.index(asset) + 1,
                            "Score": np.nan,
                            "Reason": "same-session entry",
                            "Entry_price": float(valid_entry.get(asset, np.nan)),
                            "Exit_price": np.nan,
                            "Trade_return": np.nan,
                        },
                        {
                            "Time": current_date,
                            "Asset": asset,
                            "Before_weight": weight,
                            "Target_weight": 0.0,
                            "Trade_delta": -weight,
                            "Action": "close_short" if side == "short" else "exit",
                            "Trade_turnover": abs(weight),
                            "Allocated_cost": exit_cost * abs(weight) / gross if gross > 0.0 else 0.0,
                            "Selected": True,
                            "Eligible": True,
                            "Rank": ranked_assets.index(asset) + 1,
                            "Score": np.nan,
                            "Reason": "same-session exit",
                            "Entry_price": float(valid_entry.get(asset, np.nan)),
                            "Exit_price": float(valid_exit.get(asset, np.nan)),
                            "Trade_return": trade_return,
                        },
                    ]
                )

        validation_report = dict(self.validation_report)
        validation_report["accounting_backend"] = "same_session"
        validation_report = self._attach_cost_accounting_validation(
            validation_report=validation_report,
            equity_curve=pd.DataFrame(equity_rows),
            cost_rate=cost_rate,
        )
        validation_report = self._risk_validation_report(validation_report, risk_gate_events)
        return MultiAssetBacktestResult(
            strategy_id=str(self.config.get("strategy_id") or "multi_asset_portfolio"),
            equity_curve=pd.DataFrame(equity_rows),
            holdings=pd.DataFrame(holding_rows),
            rebalance_audit=pd.DataFrame(rebalance_rows),
            rebalance_trades=pd.DataFrame(rebalance_trade_rows),
            feature_cache=dict(self.feature_builder.stats),
            config=self.config,
            validation_report=validation_report,
            risk_gate_events=pd.DataFrame(risk_gate_events),
        )

    def _run_calendar_event_overlay_accounting(self) -> MultiAssetBacktestResult:
        allocation_cfg = self.config.get("allocation", {}) if isinstance(self.config.get("allocation"), dict) else {}
        execution_cfg = self._fill_model_config()
        signals_cfg = self.config.get("signals", {}) if isinstance(self.config.get("signals"), dict) else {}
        risk_cfg = self.config.get("risk", {}) if isinstance(self.config.get("risk"), dict) else {}
        event_node = (
            allocation_cfg.get("event")
            or allocation_cfg.get("event_trigger")
            or signals_cfg.get("entry")
        )
        if not isinstance(event_node, dict) or not event_node:
            raise ValueError("allocation.method=calendar_event_overlay requires allocation.event or signals.entry")

        baseline_weights = self._overlay_weight_series(
            allocation_cfg,
            keys=("baseline_weights", "normal_weights", "default_weights"),
            required=True,
        )
        event_weights = self._overlay_weight_series(
            allocation_cfg,
            keys=("event_weights", "overlay_weights"),
            required=True,
        )
        allow_short = bool(risk_cfg.get("allow_short", False))
        if not allow_short and bool((event_weights < -1e-12).any()):
            raise ValueError("calendar_event_overlay has negative event weights but risk.allow_short is false")
        baseline_weights = self._scale_overlay_weights_if_needed(baseline_weights, risk_cfg)
        event_weights = self._scale_overlay_weights_if_needed(event_weights, risk_cfg)
        baseline_gross = float(baseline_weights.abs().sum())
        event_gross = float(event_weights.abs().sum())
        if baseline_gross <= 1e-12 and event_gross <= 1e-12:
            raise ValueError("calendar_event_overlay requires non-zero baseline or event weights")

        resolved_event = self._resolve_param_refs_in_obj(event_node)
        resolver = CalendarEventResolver(pd.DataFrame({"Time": self.close.index}))
        event_dates = {
            pd.Timestamp(item).normalize()
            for item in resolver.trigger_sessions(resolved_event)
        }
        event_audit = resolver.event_frame(
            resolved_event,
            strategy_id=str(self.config.get("strategy_id") or "calendar_event_overlay"),
            event_role="overlay",
        )
        cost_rate = self._execution_cost_rate()
        entry_prices = self._execution_price_frame(
            execution_cfg.get("entry_price") or execution_cfg.get("price") or "open"
        )
        exit_prices = self._execution_price_frame(execution_cfg.get("exit_price") or "close")

        equity = 100.0
        equity_peak = equity
        baseline_active = False
        previous_baseline_open: Optional[pd.Series] = None
        zero_weights = pd.Series(0.0, index=self.symbols, dtype=float)
        score = pd.DataFrame(np.nan, index=self.close.index, columns=self.symbols)
        eligible = pd.DataFrame(True, index=self.close.index, columns=self.symbols)

        equity_rows: List[Dict[str, Any]] = []
        holding_rows: List[Dict[str, Any]] = []
        rebalance_rows: List[Dict[str, Any]] = []
        rebalance_trade_rows: List[Dict[str, Any]] = []
        risk_gate_events: List[Dict[str, Any]] = []

        def charge_cost(turnover: float) -> float:
            nonlocal equity
            if turnover <= 1e-12 or cost_rate <= 0.0:
                return 0.0
            cost = equity * turnover * cost_rate
            equity *= max(0.0, 1.0 - turnover * cost_rate)
            return cost

        def selected_assets(weights: pd.Series) -> List[str]:
            return [asset for asset in self.symbols if abs(float(weights.get(asset, 0.0))) > 1e-12]

        def append_audit(
            *,
            date: pd.Timestamp,
            before: pd.Series,
            target: pd.Series,
            reason: str,
            turnover: float,
            trade_cost: float,
            entry_row: pd.Series,
            exit_row: Optional[pd.Series] = None,
            trade_returns: Optional[pd.Series] = None,
        ) -> None:
            selected = selected_assets(target)
            ranked = selected or selected_assets(before)
            self._append_rebalance_rows(
                rows=rebalance_rows,
                holdings=holding_rows,
                date=date,
                target_weights=target,
                selected_assets=selected,
                ranked_assets=ranked,
                score=score,
                eligible=eligible,
                equity=equity,
                turnover=turnover,
                cost_rate=cost_rate,
                trade_cost=trade_cost,
            )
            assets = sorted(set(self.symbols) | set(selected_assets(before)) | set(selected_assets(target)))
            for asset in assets:
                before_weight = float(before.get(asset, 0.0))
                target_weight = float(target.get(asset, 0.0))
                delta = target_weight - before_weight
                abs_delta = abs(delta)
                if abs_delta <= 1e-12:
                    continue
                action = self._overlay_trade_action(before_weight, target_weight, delta)
                rebalance_trade_rows.append(
                    {
                        "Time": date,
                        "Asset": asset,
                        "Before_weight": before_weight,
                        "Target_weight": target_weight,
                        "Trade_delta": delta,
                        "Action": action,
                        "Trade_turnover": abs_delta,
                        "Allocated_cost": trade_cost * abs_delta / turnover if turnover > 0.0 else 0.0,
                        "Selected": asset in selected,
                        "Eligible": True,
                        "Rank": ranked.index(asset) + 1 if asset in ranked else None,
                        "Score": np.nan,
                        "Reason": reason,
                        "Entry_price": float(entry_row.get(asset, np.nan)),
                        "Exit_price": float(exit_row.get(asset, np.nan)) if exit_row is not None else np.nan,
                        "Trade_return": (
                            float(trade_returns.get(asset, np.nan))
                            if trade_returns is not None and asset in trade_returns.index
                            else np.nan
                        ),
                    }
                )

        for current_date in self.close.index:
            date_key = pd.Timestamp(current_date).normalize()
            open_row = pd.to_numeric(entry_prices.loc[current_date], errors="coerce").replace(0.0, np.nan)
            close_row = pd.to_numeric(exit_prices.loc[current_date], errors="coerce").replace(0.0, np.nan)
            daily_return = 0.0
            asset_contribution = pd.Series(0.0, index=self.symbols, dtype=float)
            turnover = 0.0
            trade_cost = 0.0

            if baseline_active and previous_baseline_open is not None:
                open_returns = (open_row / previous_baseline_open.replace(0.0, np.nan) - 1.0).replace(
                    [np.inf, -np.inf], np.nan
                ).fillna(0.0)
                baseline_return = float((baseline_weights * open_returns).sum())
                asset_contribution = asset_contribution.add(baseline_weights * open_returns, fill_value=0.0)
                daily_return += baseline_return
                equity *= max(0.0, 1.0 + baseline_return)
                equity_peak = max(equity_peak, equity)

            if date_key in event_dates:
                if baseline_active and baseline_gross > 1e-12:
                    before = baseline_weights.copy()
                    cost = charge_cost(baseline_gross)
                    turnover += baseline_gross
                    trade_cost += cost
                    append_audit(
                        date=current_date,
                        before=before,
                        target=zero_weights,
                        reason="event open: flatten baseline",
                        turnover=baseline_gross,
                        trade_cost=cost,
                        entry_row=open_row,
                    )
                    baseline_active = False
                    previous_baseline_open = None

                if event_gross > 1e-12:
                    entry_cost = charge_cost(event_gross)
                    turnover += event_gross
                    trade_cost += entry_cost
                    append_audit(
                        date=current_date,
                        before=zero_weights,
                        target=event_weights,
                        reason="event open: enter overlay",
                        turnover=event_gross,
                        trade_cost=entry_cost,
                        entry_row=open_row,
                    )

                    event_returns = (close_row / open_row.replace(0.0, np.nan) - 1.0).replace(
                        [np.inf, -np.inf], np.nan
                    ).fillna(0.0)
                    weighted_event_returns = event_weights * event_returns
                    event_return = float(weighted_event_returns.sum())
                    asset_contribution = asset_contribution.add(weighted_event_returns, fill_value=0.0)
                    daily_return += event_return
                    equity *= max(0.0, 1.0 + event_return)
                    equity_peak = max(equity_peak, equity)

                    exit_cost = charge_cost(event_gross)
                    turnover += event_gross
                    trade_cost += exit_cost
                    append_audit(
                        date=current_date,
                        before=event_weights,
                        target=zero_weights,
                        reason="event close: exit overlay",
                        turnover=event_gross,
                        trade_cost=exit_cost,
                        entry_row=open_row,
                        exit_row=close_row,
                        trade_returns=weighted_event_returns,
                    )
            else:
                if not baseline_active and baseline_gross > 1e-12:
                    cost = charge_cost(baseline_gross)
                    turnover += baseline_gross
                    trade_cost += cost
                    append_audit(
                        date=current_date,
                        before=zero_weights,
                        target=baseline_weights,
                        reason="session open: restore baseline",
                        turnover=baseline_gross,
                        trade_cost=cost,
                        entry_row=open_row,
                    )
                    baseline_active = True
                if baseline_active:
                    previous_baseline_open = open_row.reindex(index=self.symbols)

            current_weights = baseline_weights if baseline_active else zero_weights
            gross = float(current_weights.abs().sum())
            equity_row = {
                "Time": current_date,
                "Equity_value": equity,
                "Portfolio_return": daily_return,
                "Turnover": turnover,
                "Trade_cost": trade_cost,
                "Selected_count": int((current_weights.abs() > 1e-12).sum()),
                "Gross_exposure": gross,
                "Cash_weight": max(0.0, 1.0 - gross),
            }
            for asset in self.symbols:
                equity_row[f"Weight_{asset}"] = float(current_weights.get(asset, 0.0))
                equity_row[f"Contribution_{asset}"] = float(asset_contribution.get(asset, 0.0))
            equity_rows.append(equity_row)

        validation_report = dict(self.validation_report)
        validation_report["accounting_backend"] = "calendar_event_overlay"
        validation_report["event_overlay"] = {
            "event_count": len(event_dates),
            "baseline_weights": {
                asset: float(value)
                for asset, value in baseline_weights.items()
                if abs(float(value)) > 1e-12
            },
            "event_weights": {
                asset: float(value)
                for asset, value in event_weights.items()
                if abs(float(value)) > 1e-12
            },
            "return_clock": "baseline_open_to_open_and_event_open_to_close",
            "event_audit_rows": len(event_audit),
        }
        validation_report = self._attach_cost_accounting_validation(
            validation_report=validation_report,
            equity_curve=pd.DataFrame(equity_rows),
            cost_rate=cost_rate,
        )
        validation_report = self._risk_validation_report(validation_report, risk_gate_events)
        return MultiAssetBacktestResult(
            strategy_id=str(self.config.get("strategy_id") or "calendar_event_overlay"),
            equity_curve=pd.DataFrame(equity_rows),
            holdings=pd.DataFrame(holding_rows),
            rebalance_audit=pd.DataFrame(rebalance_rows),
            rebalance_trades=pd.DataFrame(rebalance_trade_rows),
            feature_cache=dict(self.feature_builder.stats),
            config=self.config,
            validation_report=validation_report,
            risk_gate_events=pd.DataFrame(risk_gate_events),
        )

    def _run_with_rust_accounting(self) -> MultiAssetBacktestResult:
        from backtester.RustCoreBridge_backtester import (
            run_accounting_via_cli,
            rust_core_available,
        )

        if not rust_core_available():
            raise RuntimeError("fill_model.accounting_backend=rust requires the Rust core")

        features = self._build_features_for_backtest()
        self._ensure_signal_target_weight_frame(features)
        selector = self.config.get("selection", {})
        eligible = self._evaluate_condition(selector.get("eligible", {}), features)
        score = self._score_frame(selector, features)
        rebalance_dates = self._rebalance_dates()
        allocation_cfg = self.config.get("allocation", {})
        cost_cfg = self._fill_model_config().get("cost", {})
        transaction_cost = self._float(cost_cfg.get("transaction_cost", cost_cfg.get("fee_ratio", 0.0)))
        slippage = self._float(cost_cfg.get("slippage", 0.0))
        cost_rate = max(0.0, transaction_cost + slippage)
        rebalance_set = {pd.Timestamp(item).normalize() for item in rebalance_dates}
        returns_frame = (
            self.close.pct_change()
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
        )

        checkpoints: List[Dict[str, Any]] = []
        selection_by_date: Dict[pd.Timestamp, Dict[str, Any]] = {}
        for current_date, return_row in returns_frame.iterrows():
            date_key = pd.Timestamp(current_date).normalize()
            is_rebalance = date_key in rebalance_set
            target_weights = pd.Series(0.0, index=self.symbols, dtype=float)
            selected_assets: List[str] = []
            ranked_assets: List[str] = []
            if is_rebalance:
                target_weights, selected_assets, ranked_assets = self._target_weights_for_date(
                    date=current_date,
                    eligible=eligible,
                    score=score,
                    allocation_cfg=allocation_cfg,
                    selector=selector,
                )
                selection_by_date[date_key] = {
                    "target_weights": target_weights,
                    "selected_assets": selected_assets,
                    "ranked_assets": ranked_assets,
                }
            checkpoints.append(
                {
                    "time": date_key.date().isoformat(),
                    "rebalance": bool(is_rebalance),
                    "returns": {
                        asset: float(return_row.get(asset, 0.0))
                        for asset in self.symbols
                        if pd.notna(return_row.get(asset, 0.0))
                    },
                    "target_weights": {
                        asset: float(target_weights.get(asset, 0.0))
                        for asset in self.symbols
                        if abs(float(target_weights.get(asset, 0.0))) > 1e-12
                    },
                }
            )

        risk_cfg = self.config.get("risk", {}) if isinstance(self.config.get("risk"), dict) else {}
        payload = {
            "config": {
                "starting_equity": 100.0,
                "cost_rate": cost_rate,
                "max_gross_exposure": self._float(
                    risk_cfg.get("max_gross_exposure", 1.0),
                    default=1.0,
                ),
                "allow_short": bool(risk_cfg.get("allow_short", False)),
            },
            "checkpoints": checkpoints,
        }
        rust_summary = run_accounting_via_cli(
            payload,
            timeout=self._positive_int(
                self._fill_model_config().get("rust_timeout_seconds", 60),
                default=60,
            ),
        )

        equity_rows: List[Dict[str, Any]] = []
        holding_rows: List[Dict[str, Any]] = []
        rebalance_rows: List[Dict[str, Any]] = []
        rebalance_trade_rows: List[Dict[str, Any]] = []
        for event in rust_summary.get("events", []):
            current_date = pd.Timestamp(event["time"])
            target_weights = pd.Series(
                {asset: float(value) for asset, value in event.get("target_weights", {}).items()},
                index=self.symbols,
                dtype=float,
            ).fillna(0.0)
            drift_weights = pd.Series(
                {asset: float(value) for asset, value in event.get("drift_weights", {}).items()},
                index=self.symbols,
                dtype=float,
            ).fillna(0.0)
            contribution = event.get("contribution", {}) if isinstance(event.get("contribution"), dict) else {}
            turnover = float(event.get("turnover", 0.0))
            trade_cost = float(event.get("equity_before_trade", 0.0)) * float(event.get("cost_drag", 0.0))
            date_key = current_date.normalize()
            if date_key in selection_by_date:
                selection_info = selection_by_date[date_key]
                selected_assets = selection_info["selected_assets"]
                ranked_assets = selection_info["ranked_assets"]
                self._append_rebalance_rows(
                    rows=rebalance_rows,
                    holdings=holding_rows,
                    date=current_date,
                    target_weights=target_weights,
                    selected_assets=selected_assets,
                    ranked_assets=ranked_assets,
                    score=score,
                    eligible=eligible,
                    equity=float(event.get("equity_after_trade", 0.0)),
                    turnover=turnover,
                    cost_rate=cost_rate,
                    trade_cost=trade_cost,
                )
                self._append_rebalance_trade_rows(
                    rows=rebalance_trade_rows,
                    date=current_date,
                    before_weights=drift_weights,
                    target_weights=target_weights,
                    selected_assets=selected_assets,
                    ranked_assets=ranked_assets,
                    score=score,
                    eligible=eligible,
                    selector=selector,
                    turnover=turnover,
                    trade_cost=trade_cost,
                )

            equity_row = {
                "Time": current_date,
                "Equity_value": float(event.get("equity_after_trade", 0.0)),
                "Portfolio_return": float(event.get("portfolio_return", 0.0)),
                "Turnover": turnover,
                "Trade_cost": trade_cost,
                "Selected_count": int(event.get("active_positions", 0)),
                "Gross_exposure": float(event.get("gross_exposure", 0.0)),
                "Cash_weight": max(0.0, float(event.get("cash_weight", 0.0))),
            }
            for asset in self.symbols:
                equity_row[f"Weight_{asset}"] = float(target_weights.get(asset, 0.0))
                equity_row[f"Contribution_{asset}"] = float(contribution.get(asset, 0.0))
            equity_rows.append(equity_row)

        validation_report = dict(self.validation_report)
        validation_report["accounting_backend"] = "rust"
        validation_report["rust_accounting_summary"] = {
            "schema_version": "rust_accounting_summary.v1",
            "status": "executed",
            "final_equity": rust_summary.get("final_equity"),
            "active_rebalances": rust_summary.get("active_rebalances"),
            "average_turnover": rust_summary.get("average_turnover"),
        }
        risk_gate_events: List[Dict[str, Any]] = []
        validation_report = self._attach_cost_accounting_validation(
            validation_report=validation_report,
            equity_curve=pd.DataFrame(equity_rows),
            cost_rate=cost_rate,
        )
        validation_report = self._risk_validation_report(validation_report, risk_gate_events)
        return MultiAssetBacktestResult(
            strategy_id=str(self.config.get("strategy_id") or "multi_asset_portfolio"),
            equity_curve=pd.DataFrame(equity_rows),
            holdings=pd.DataFrame(holding_rows),
            rebalance_audit=pd.DataFrame(rebalance_rows),
            rebalance_trades=pd.DataFrame(rebalance_trade_rows),
            feature_cache=dict(self.feature_builder.stats),
            config=self.config,
            validation_report=validation_report,
            risk_gate_events=pd.DataFrame(risk_gate_events),
        )

    def _build_features_for_backtest(self) -> Dict[str, pd.DataFrame]:
        feature_specs = self._indicator_specs_for_backtest()
        features = self.feature_builder.build(feature_specs)
        pipeline = self.config.get("factor_pipeline", {})
        if not isinstance(pipeline, dict) or not pipeline:
            return features

        from factorhandler import FactorHandler

        cache_root = self.cache_dir / "factorhandler" if self.cache_dir is not None else None
        self.factor_result = FactorHandler(self.market_data, pipeline, cache_dir=cache_root).run()
        factor_features = self.factor_result.feature_frames()
        features.update(
            {
                name: frame.reindex(index=self.close.index, columns=self.symbols)
                for name, frame in factor_features.items()
            }
        )
        quality_report = self.factor_result.factor_quality_report
        point_in_time_audit = self.factor_result.point_in_time_audit
        cache_report = self.factor_result.cache_report
        warnings: List[str] = []
        if point_in_time_audit.get("status") != "passed":
            warnings.append("factor_point_in_time_audit_not_passed")
        if quality_report.get("missing_fields"):
            warnings.append("factor_quality_missing_required_fields")
        factor_feature_audit = {
            "schema_version": "factor_feature_audit.v1",
            "quality_report": quality_report,
            "point_in_time_audit": point_in_time_audit,
            "cache_report": cache_report,
            "warnings": warnings,
        }
        self.validation_report["factorhandler"] = {
            "quality_report": self.factor_result.factor_quality_report,
            "point_in_time_audit": self.factor_result.point_in_time_audit,
            "cache_report": self.factor_result.cache_report,
        }
        self.validation_report["factor_feature_audit"] = factor_feature_audit
        if warnings:
            self.validation_report.setdefault("warnings", []).extend(warnings)
        self.feature_builder.stats["factorhandler_frames"] = len(factor_features)
        self.feature_builder.stats["factorhandler_cache_hits"] = int(
            self.factor_result.cache_report.get("hits", 0)
        )
        self.feature_builder.stats["factorhandler_cache_writes"] = int(
            self.factor_result.cache_report.get("writes", 0)
        )
        return features

    def _attach_rust_accounting_validation(
        self,
        *,
        validation_report: Dict[str, Any],
        equity_curve: pd.DataFrame,
        cost_rate: float,
    ) -> Dict[str, Any]:
        report = dict(validation_report or {})
        rust_cfg = self.config.get("rust_validation", {})
        if not isinstance(rust_cfg, dict) or not bool(rust_cfg.get("enabled", False)):
            return report
        try:
            from backtester.RustCoreBridge_backtester import (
                rust_core_available,
                validate_portfolio_result_with_rust,
            )

            if not rust_core_available():
                report["rust_accounting_validation"] = {
                    "schema_version": "rust_accounting_validation.v1",
                    "status": "skipped",
                    "reason": "rust core unavailable",
                }
                return report
            report["rust_accounting_validation"] = validate_portfolio_result_with_rust(
                close_frame=self.close,
                equity_curve=equity_curve,
                cost_rate=cost_rate,
                tolerance=self._float(rust_cfg.get("tolerance", 1e-8), default=1e-8),
                timeout=self._positive_int(rust_cfg.get("timeout_seconds", 60), default=60),
            )
        except Exception as exc:  # pragma: no cover - defensive audit path
            report["rust_accounting_validation"] = {
                "schema_version": "rust_accounting_validation.v1",
                "status": "error",
                "error": str(exc),
            }
        return report

    def _attach_cost_accounting_validation(
        self,
        *,
        validation_report: Dict[str, Any],
        equity_curve: pd.DataFrame,
        cost_rate: float,
    ) -> Dict[str, Any]:
        report = dict(validation_report or {})
        turnover = pd.to_numeric(equity_curve.get("Turnover", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        trade_cost = pd.to_numeric(equity_curve.get("Trade_cost", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        active_turnover = float(turnover.abs().sum()) if not turnover.empty else 0.0
        total_trade_cost = float(trade_cost.sum()) if not trade_cost.empty else 0.0
        configured_cost_rate = max(0.0, float(cost_rate))
        if configured_cost_rate <= 0.0:
            status = "not_configured"
        elif active_turnover <= 1e-12:
            status = "no_turnover"
        elif total_trade_cost > 0.0:
            status = "valid"
        else:
            status = "invalid_cost_accounting"
            errors = list(report.get("errors", []))
            if "nonzero_cost_config_produced_zero_trade_cost" not in errors:
                errors.append("nonzero_cost_config_produced_zero_trade_cost")
            report["errors"] = errors
            report["status"] = "invalid_contract"
        report["cost_accounting"] = {
            "schema_version": "cost_accounting_validation.v1",
            "status": status,
            "configured_cost_rate": configured_cost_rate,
            "active_turnover": active_turnover,
            "total_trade_cost": total_trade_cost,
        }
        return report

    def _resolve_symbols(self) -> List[str]:
        universe = self.config.get("universe", {})
        symbols = universe.get("symbols") if isinstance(universe, dict) else None
        if isinstance(symbols, list) and symbols:
            requested = [str(item) for item in symbols]
            missing = [item for item in requested if item not in self.close.columns]
            if missing:
                raise ValueError(
                    "multi_asset_portfolio market data missing configured universe symbols: "
                    + ", ".join(missing)
                )
            return requested
        return [str(col) for col in self.close.columns]

    def _build_validation_report(self) -> Dict[str, Any]:
        universe = self.config.get("universe", {}) if isinstance(self.config.get("universe"), dict) else {}
        expected_symbols = (
            [str(item) for item in universe.get("symbols", [])]
            if isinstance(universe.get("symbols"), list)
            else [str(col) for col in self.symbols]
        )
        loaded_symbols = [str(col) for col in self.close.columns]
        missing_symbols = [symbol for symbol in expected_symbols if symbol not in loaded_symbols]
        unexpected_symbols = [symbol for symbol in loaded_symbols if symbol not in expected_symbols]
        close_dates = pd.to_datetime(self.close.index, errors="coerce")
        effective_start = close_dates.min() if len(close_dates) else pd.NaT
        effective_end = close_dates.max() if len(close_dates) else pd.NaT
        per_asset: Dict[str, Dict[str, Any]] = {}
        for symbol in expected_symbols:
            asset_report: Dict[str, Any] = {}
            for field_name, frame in self.market_data.items():
                if symbol not in frame.columns:
                    asset_report[field_name] = {
                        "loaded": False,
                        "first_valid": None,
                        "last_valid": None,
                        "missing_ratio": 1.0,
                    }
                    continue
                series = pd.to_numeric(frame[symbol], errors="coerce")
                valid = series.dropna()
                asset_report[field_name] = {
                    "loaded": True,
                    "first_valid": self._timestamp_iso(valid.index[0]) if not valid.empty else None,
                    "last_valid": self._timestamp_iso(valid.index[-1]) if not valid.empty else None,
                    "missing_ratio": float(series.isna().mean()) if len(series) else 1.0,
                }
            per_asset[symbol] = asset_report
        errors: List[str] = []
        if missing_symbols:
            errors.append("missing_configured_symbols")
        if self.close.empty:
            errors.append("empty_close_frame")
        universe_provenance = self._build_universe_provenance(
            universe=universe,
            expected_symbols=expected_symbols,
            loaded_symbols=loaded_symbols,
            missing_symbols=missing_symbols,
        )
        warnings = list(universe_provenance.get("warnings", []))
        return {
            "schema_version": "multi_asset_run_validation.v1",
            "contract_id": "lo2cin4bt-multi-asset-run-validation-v1",
            "status": "valid" if not errors else "invalid_contract",
            "expected_symbols": expected_symbols,
            "loaded_symbols": loaded_symbols,
            "available_data_symbols": list(getattr(self, "available_symbols", loaded_symbols)),
            "missing_symbols": missing_symbols,
            "unexpected_symbols": unexpected_symbols,
            "required_fields": sorted(self.market_data.keys()),
            "effective_start_date": self._timestamp_iso(effective_start),
            "effective_end_date": self._timestamp_iso(effective_end),
            "row_count": int(len(self.close)),
            "per_asset": per_asset,
            "universe_provenance": universe_provenance,
            "errors": errors,
            "warnings": warnings,
        }

    def _build_universe_provenance(
        self,
        *,
        universe: Dict[str, Any],
        expected_symbols: List[str],
        loaded_symbols: List[str],
        missing_symbols: List[str],
    ) -> Dict[str, Any]:
        available_symbols = list(getattr(self, "available_symbols", loaded_symbols))
        configured_symbols = (
            [str(item) for item in universe.get("symbols", [])]
            if isinstance(universe.get("symbols"), list)
            else []
        )
        ignored_symbols = [symbol for symbol in available_symbols if symbol not in expected_symbols]
        policy = str(
            universe.get("universe_policy")
            or universe.get("survivorship_policy")
            or ""
        ).strip().lower()
        source_ref = self._universe_source_ref(universe)
        source_type = self._universe_source_type(universe, configured_symbols, source_ref)
        as_of_date = self._universe_as_of_date(universe)
        constituents_validation = validate_historical_universe_constituents(
            universe=universe,
            configured_symbols=configured_symbols,
            as_of_date=as_of_date,
            repo_root=self.repo_root,
            config_file_path=self.config_file_path,
        )
        delisted_policy = str(universe.get("delisted_policy") or "").strip().lower()
        point_in_time_claimed = self._universe_point_in_time_claimed(universe, policy)
        current_or_static_source = self._universe_source_is_current_or_static(source_type)
        strong_evidence_present = self._universe_strong_evidence_present(
            universe=universe,
            source_type=source_type,
            source_ref=source_ref,
            as_of_date=as_of_date,
            constituents_validation=constituents_validation,
        )
        point_in_time_constituents = bool(
            point_in_time_claimed and strong_evidence_present and not current_or_static_source
        )
        delisted_included = any(token in delisted_policy for token in ("include", "historical", "delisted"))

        warnings: List[str] = []
        if point_in_time_constituents and delisted_included:
            risk = "low"
            provenance_status = "valid"
        elif point_in_time_claimed:
            risk = "medium"
            provenance_status = "review"
            if not as_of_date:
                warnings.append("point_in_time_universe_claim_missing_as_of_date")
            if not strong_evidence_present:
                warnings.append("point_in_time_universe_claim_missing_evidence")
            if current_or_static_source:
                warnings.append("current_or_static_universe_source_not_point_in_time")
            if not delisted_included:
                warnings.append("delisted_symbol_policy_not_proven")
            if constituents_validation.get("status") in {"invalid", "missing"}:
                warnings.append("historical_constituents_content_validation_failed")
        elif configured_symbols:
            risk = "high"
            provenance_status = "review"
            warnings.append("static_or_current_universe_may_have_survivorship_bias")
        else:
            risk = "unknown"
            provenance_status = "review"
            warnings.append("universe_inferred_from_market_data_columns")
        if ignored_symbols:
            warnings.append("market_data_contains_unconfigured_symbols_ignored")
        if missing_symbols:
            warnings.append("configured_universe_symbols_missing_from_market_data")
        warnings.extend(str(item) for item in constituents_validation.get("warnings", []))

        return {
            "schema_version": "universe_provenance.v1",
            "source_type": source_type,
            "source_ref": source_ref,
            "policy": policy or None,
            "as_of_date": as_of_date,
            "configured_symbols": configured_symbols,
            "loaded_symbols": loaded_symbols,
            "available_data_symbols": available_symbols,
            "ignored_data_symbols": ignored_symbols,
            "missing_symbols": list(missing_symbols),
            "point_in_time_constituents": point_in_time_constituents,
            "constituents_validation": constituents_validation,
            "delisted_policy": delisted_policy or None,
            "survivorship_bias_risk": risk,
            "provenance_status": provenance_status,
            "warnings": warnings,
        }

    @staticmethod
    def _universe_source_ref(universe: Dict[str, Any]) -> Optional[str]:
        return constituents_source_ref(universe)

    @staticmethod
    def _universe_source_type(
        universe: Dict[str, Any],
        configured_symbols: List[str],
        source_ref: Optional[str],
    ) -> str:
        raw = str(universe.get("source_type") or universe.get("source_kind") or "").strip().lower()
        if raw:
            return raw
        if source_ref:
            if constituents_path_declared(universe):
                return "historical_universe_constituents"
            return "declared_source"
        if configured_symbols:
            return "explicit_config_symbols"
        return "market_data_columns"

    @staticmethod
    def _universe_as_of_date(universe: Dict[str, Any]) -> Optional[str]:
        value = (
            universe.get("as_of_date")
            or universe.get("as_of")
            or universe.get("historical_constituents_as_of")
            or universe.get("universe_constituents_as_of")
        )
        if value in (None, ""):
            return None
        return str(value)

    @staticmethod
    def _universe_point_in_time_claimed(universe: Dict[str, Any], policy: str) -> bool:
        if bool(
            universe.get("point_in_time_constituents")
            or universe.get("point_in_time")
        ):
            return True
        return policy in {
            "point_in_time",
            "point_in_time_snapshot",
            "historical_constituents",
            "historical_universe_constituents",
            "pit_universe",
        }

    @staticmethod
    def _universe_strong_evidence_present(
        *,
        universe: Dict[str, Any],
        source_type: str,
        source_ref: Optional[str],
        as_of_date: Optional[str],
        constituents_validation: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not as_of_date:
            return False
        validation_status = str((constituents_validation or {}).get("status") or "")
        if validation_status == "valid":
            return True
        if constituents_path_declared(universe):
            return False
        if declared_constituents_hash(universe) and source_type in CONSTITUENTS_SOURCE_TYPES:
            return True
        return bool(
            source_ref
            and as_of_date
            and source_type in CONSTITUENTS_SOURCE_TYPES
        )

    @staticmethod
    def _universe_source_is_current_or_static(source_type: str) -> bool:
        normalized = str(source_type or "").strip().lower()
        return normalized in {
            "all_symbols",
            "configured_symbols",
            "current_list",
            "current_provider_list",
            "current_symbols",
            "declared_current_source",
            "explicit_config_symbols",
            "fixed_list",
            "fixed_symbols",
            "index_current",
            "latest",
            "provider_current",
            "single_asset",
            "static_list",
            "static_symbols",
            "static_universe",
        }

    def _rebalance_trigger(self) -> Dict[str, Any]:
        rebalance = self.config.get("rebalance", {})
        trigger = rebalance.get("trigger", {"op": "calendar.month_start"}) if isinstance(rebalance, dict) else {}
        return trigger if isinstance(trigger, dict) else {}

    def _rebalance_trigger_op(self) -> str:
        return str((self._rebalance_trigger() or {}).get("op", "")).strip().lower()

    def _rebalance_dates(self) -> List[pd.Timestamp]:
        trigger = self._rebalance_trigger()
        op = self._rebalance_trigger_op()
        if op in {"calendar.every_session", "every_session"}:
            return [pd.Timestamp(item).normalize() for item in self.close.index]
        if op == "signal.change":
            return []
        frame = pd.DataFrame({"Time": self.close.index})
        return CalendarEventResolver(frame).trigger_sessions(trigger)

    def _signal_change_rebalance_dates(
        self,
        *,
        eligible: pd.DataFrame,
        score: pd.DataFrame,
        allocation_cfg: Dict[str, Any],
        selector: Dict[str, Any],
    ) -> List[pd.Timestamp]:
        """Return dates where the desired target-weight vector changes.

        A signal-change rebalance means the strategy intent changed. It should not
        reset drifted weights every session when the target vector is unchanged.
        """

        dates: List[pd.Timestamp] = []
        previous_target = pd.Series(0.0, index=self.symbols, dtype=float)
        for current_date in self.close.index:
            target_weights, _, _ = self._target_weights_for_date(
                date=current_date,
                eligible=eligible,
                score=score,
                allocation_cfg=allocation_cfg,
                selector=selector,
            )
            current_target = (
                target_weights.reindex(index=self.symbols)
                .fillna(0.0)
                .astype(float)
                .replace([np.inf, -np.inf], 0.0)
            )
            if not np.allclose(
                current_target.to_numpy(dtype=float),
                previous_target.to_numpy(dtype=float),
                atol=1e-12,
                rtol=0.0,
            ):
                dates.append(pd.Timestamp(current_date).normalize())
                previous_target = current_target
        return dates

    def _score_frame(self, selector: Dict[str, Any], features: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        rank_by = str((selector or {}).get("rank_by") or "").strip()
        if rank_by:
            if rank_by in features:
                return features[rank_by].reindex(index=self.close.index, columns=self.symbols)
            if rank_by in self.market_data:
                return self.market_data[rank_by].reindex(index=self.close.index, columns=self.symbols)
        return self.close.pct_change(20)

    def _evaluate_condition(self, node: Any, features: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        if not node:
            return pd.DataFrame(True, index=self.close.index, columns=self.symbols)
        if isinstance(node, list):
            return self._all([self._evaluate_condition(item, features) for item in node])
        if not isinstance(node, dict):
            return pd.DataFrame(True, index=self.close.index, columns=self.symbols)
        op = str(node.get("op") or "").strip().lower()
        if CalendarEventResolver.is_calendar_op(op):
            resolved_node = self._resolve_param_refs_in_obj(node)
            mask = CalendarEventResolver(pd.DataFrame({"Time": self.close.index})).materialize(resolved_node)
            return pd.DataFrame(
                np.repeat(mask.reshape(-1, 1), len(self.symbols), axis=1),
                index=self.close.index,
                columns=self.symbols,
            ).fillna(False)
        if op == "session.same_session_close":
            return pd.DataFrame(False, index=self.close.index, columns=self.symbols)
        if "all" in node:
            return self._all([self._evaluate_condition(item, features) for item in node.get("all", [])])
        if "any" in node:
            return self._any([self._evaluate_condition(item, features) for item in node.get("any", [])])
        if "not" in node:
            return ~self._evaluate_condition(node.get("not"), features)

        left = self._operand_frame(node.get("left", node.get("field")), features)
        op = str(node.get("op") or "gt").strip().lower()
        right = self._operand_value(node, features)
        if isinstance(right, pd.DataFrame):
            right = right.reindex(index=self.close.index, columns=self.symbols)
        if op in {"cross_up", "crosses_above"}:
            result = (left > right) & (left.shift(1) <= right.shift(1))
        elif op in {"cross_down", "crosses_below"}:
            result = (left < right) & (left.shift(1) >= right.shift(1))
        elif op in {"gt", ">"}:
            result = left > right
        elif op in {"ge", ">="}:
            result = left >= right
        elif op in {"lt", "<"}:
            result = left < right
        elif op in {"le", "<="}:
            result = left <= right
        elif op in {"eq", "=="}:
            result = left == right
        elif op in {"ne", "!="}:
            result = left != right
        else:
            raise ValueError(f"Unsupported selection comparator: {op}")
        return result.fillna(False)

    def _operand_frame(self, name: Any, features: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        if isinstance(name, dict):
            if "feature" in name:
                raise ValueError(
                    "inline feature nodes are not part of the public multi-asset config surface; define the calculation in computed_fields[] and reference it by field name"
                )
            if "field" in name:
                return self._operand_frame(name.get("field"), features)
        key = str(name or "").strip()
        if key in features:
            return features[key].reindex(index=self.close.index, columns=self.symbols)
        source_key = self._normalize_field_name(key)
        if source_key in self.market_data:
            return self.market_data[source_key].reindex(index=self.close.index, columns=self.symbols)
        raise KeyError(f"Unknown multi-asset operand field: {key}")

    def _operand_value(self, node: Dict[str, Any], features: Dict[str, pd.DataFrame]) -> Any:
        if "right_field" in node:
            return self._operand_frame(node.get("right_field"), features)
        if "right" in node:
            right = node.get("right")
            if isinstance(right, (str, dict)):
                return self._operand_frame(right, features)
            return right
        return node.get("value")

    def _inline_feature_frame(self, node: Dict[str, Any], features: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        feature = str(node.get("feature") or node.get("op") or "").strip().lower()
        params = node.get("params") if isinstance(node.get("params"), dict) else {}
        source = self._operand_frame(node.get("source") or params.get("source") or "close", features)
        period = self._positive_int(
            self._resolve_config_value(params.get("period", node.get("period"))),
            default=14,
        )
        cache_key = self._inline_feature_cache_key(feature, source, period)
        with _INLINE_FEATURE_CACHE_LOCK:
            cached = _INLINE_FEATURE_CACHE.get(cache_key)
            if cached is not None:
                _INLINE_FEATURE_CACHE.move_to_end(cache_key)
                return cached
        if feature == "indicator.sma":
            result = source.rolling(period, min_periods=period).mean()
            self._store_inline_feature_cache(cache_key, result)
            return result
        if feature == "indicator.ema":
            result = source.ewm(span=period, adjust=False, min_periods=period).mean()
            self._store_inline_feature_cache(cache_key, result)
            return result
        if feature == "indicator.momentum":
            result = source.pct_change(periods=period)
            self._store_inline_feature_cache(cache_key, result)
            return result
        raise ValueError(f"Unsupported inline feature node: {feature}")

    @staticmethod
    def _inline_feature_cache_key(feature: str, source: pd.DataFrame, period: int) -> Tuple[Any, ...]:
        index = pd.DatetimeIndex(source.index)
        values = source.to_numpy(dtype=float, copy=False)
        finite = values[np.isfinite(values)]
        first_value = float(finite[0]) if len(finite) else np.nan
        last_value = float(finite[-1]) if len(finite) else np.nan
        return (
            feature,
            int(period),
            tuple(str(col) for col in source.columns),
            int(len(source.index)),
            str(index[0]) if len(index) else "",
            str(index[-1]) if len(index) else "",
            round(first_value, 10) if np.isfinite(first_value) else None,
            round(last_value, 10) if np.isfinite(last_value) else None,
        )

    @staticmethod
    def _store_inline_feature_cache(cache_key: Tuple[Any, ...], frame: pd.DataFrame) -> None:
        with _INLINE_FEATURE_CACHE_LOCK:
            _INLINE_FEATURE_CACHE[cache_key] = frame
            _INLINE_FEATURE_CACHE.move_to_end(cache_key)
            while len(_INLINE_FEATURE_CACHE) > _INLINE_FEATURE_CACHE_LIMIT:
                _INLINE_FEATURE_CACHE.popitem(last=False)

    def _resolve_config_value(self, value: Any) -> Any:
        if isinstance(value, dict) and set(value.keys()) == {"param_ref"}:
            ref = str(value.get("param_ref"))
            resolved_params = self.config.get("resolved_params", {})
            if isinstance(resolved_params, dict) and ref in resolved_params:
                return resolved_params[ref]
            domains = self.config.get("parameter_domains", {})
            if isinstance(domains, dict) and isinstance(domains.get(ref), dict):
                spec = domains[ref]
                return spec.get("default", spec.get("start"))
        return value

    def _resolve_param_refs_in_obj(self, value: Any) -> Any:
        if isinstance(value, dict):
            if set(value.keys()) == {"param_ref"}:
                return self._resolve_config_value(value)
            return {key: self._resolve_param_refs_in_obj(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve_param_refs_in_obj(item) for item in value]
        return value

    def _execution_is_same_session(self) -> bool:
        execution = self._fill_model_config()
        signals = self.config.get("signals", {}) if isinstance(self.config.get("signals"), dict) else {}
        exit_node = signals.get("exit")
        return (
            str(execution.get("session_scope") or "").strip().lower() == "same_session"
            or bool(execution.get("same_session_exit", False))
            or self._node_has_op(exit_node, "session.same_session_close")
        )

    def _execution_is_bar_offset_round_trip(self) -> bool:
        timing = str(self._fill_model_config().get("timing") or "").strip().lower()
        if timing != "bar_offset":
            return False
        signals = self.config.get("signals", {}) if isinstance(self.config.get("signals"), dict) else {}
        return not bool(signals.get("exit"))

    def _bar_offset_execution_spec(self) -> Dict[str, Any]:
        fill_model = self._fill_model_config()
        timing = str(fill_model.get("timing") or "").strip().lower()
        if timing != "bar_offset":
            raise ValueError("bar_offset execution requires fill_model.timing=bar_offset")

        entry_price = self._normalize_field_name(fill_model.get("entry_price") or "close")
        exit_price = self._normalize_field_name(fill_model.get("exit_price") or "close")
        if entry_price not in {"open", "close"}:
            raise ValueError("bar_offset entry_price must be 'open' or 'close'")
        if exit_price not in {"open", "close"}:
            raise ValueError("bar_offset exit_price must be 'open' or 'close'")
        entry_delay = self._non_negative_int(fill_model.get("entry_delay_bars"), default=0)
        exit_delay = self._non_negative_int(fill_model.get("exit_delay_bars"), default=0)
        entry_phase = self._execution_price_phase(entry_price)
        exit_phase = self._execution_price_phase(exit_price)
        if exit_delay < entry_delay or (exit_delay == entry_delay and exit_phase <= entry_phase):
            raise ValueError("bar_offset exit time must be later than entry time")
        return {
            "entry_price": entry_price,
            "entry_delay_bars": entry_delay,
            "exit_price": exit_price,
            "exit_delay_bars": exit_delay,
            "return_clock": f"{entry_price}_plus_{entry_delay}_bars_to_{exit_price}_plus_{exit_delay}_bars",
        }

    @staticmethod
    def _execution_price_phase(price_name: Any) -> int:
        key = str(price_name or "").strip().lower()
        if key == "open":
            return 0
        if key == "close":
            return 1
        raise ValueError("bar_offset execution supports only open/close price phases")

    def _require_explicit_bar_offset_price_frames(self, spec: Dict[str, Any]) -> None:
        required = {
            str(spec.get("entry_price") or "").strip().lower(),
            str(spec.get("exit_price") or "").strip().lower(),
        }
        if "open" in required and "open" not in self.market_data:
            raise ValueError("bar_offset execution with open fills requires explicit open price market data")
        if "close" in required and "close" not in self.market_data:
            raise ValueError("bar_offset execution with close fills requires explicit close price market data")

    def _execution_is_calendar_event_overlay(self) -> bool:
        allocation = self.config.get("allocation", {}) if isinstance(self.config.get("allocation"), dict) else {}
        execution = self._fill_model_config()
        allocation_method = str(allocation.get("method") or "").strip().lower()
        execution_mode = str(
            execution.get("mode")
            or execution.get("session_scope")
            or execution.get("timing")
            or ""
        ).strip().lower()
        return allocation_method in {
            "calendar_event_overlay",
            "event_overlay",
            "baseline_event_overlay",
        } or execution_mode in {
            "calendar_event_overlay",
            "event_overlay",
            "baseline_event_overlay",
        }

    @classmethod
    def _node_has_op(cls, node: Any, op_name: str) -> bool:
        if isinstance(node, dict):
            if str(node.get("op") or "").strip().lower() == op_name:
                return True
            return any(cls._node_has_op(value, op_name) for value in node.values())
        if isinstance(node, list):
            return any(cls._node_has_op(item, op_name) for item in node)
        return False

    def _execution_price_frame(self, price_name: Any) -> pd.DataFrame:
        key = self._normalize_field_name(price_name)
        if key not in self.market_data:
            if key == "open":
                return self.open.reindex(index=self.close.index, columns=self.symbols)
            if key == "close":
                return self.close.reindex(index=self.close.index, columns=self.symbols)
            raise KeyError(f"Missing execution price frame: {price_name}")
        return self.market_data[key].reindex(index=self.close.index, columns=self.symbols)

    @staticmethod
    def _normalize_field_name(name: Any) -> str:
        key = str(name or "").strip().lower()
        aliases = {
            "price.open": "open",
            "price.high": "high",
            "price.low": "low",
            "price.close": "close",
            "price.volume": "volume",
        }
        return aliases.get(key, key)

    def _target_weights_for_date(
        self,
        *,
        date: pd.Timestamp,
        eligible: pd.DataFrame,
        score: pd.DataFrame,
        allocation_cfg: Dict[str, Any],
        selector: Dict[str, Any],
    ) -> Tuple[pd.Series, List[str], List[str]]:
        date = pd.Timestamp(date).normalize()
        allocation_method = str((allocation_cfg or {}).get("method", "equal_weight")).strip().lower()
        if allocation_method in {
            "signal_state",
            "signal_target_weight",
            "target_weight_frame",
            "target_weights_frame",
            "explicit_target_weights",
        }:
            weights = self._explicit_target_weights_for_date(date, allocation_cfg)
            selected = [asset for asset in self.symbols if abs(float(weights.get(asset, 0.0))) > 1e-12]
            return weights, selected, selected
        if allocation_method in {
            "fixed_weight",
            "fixed_weights",
            "static_weight",
            "static_weights",
            "fixed_weight_profile",
            "fixed_weight_profiles",
            "weight_profile",
            "weight_profiles",
        }:
            weights = self._fixed_target_weights(allocation_cfg)
            selected = [asset for asset in self.symbols if float(weights.get(asset, 0.0)) > 0.0]
            return weights, selected, selected

        eligible_row = eligible.loc[date].fillna(False) if date in eligible.index else pd.Series(False, index=self.symbols)
        score_row = score.loc[date] if date in score.index else pd.Series(np.nan, index=self.symbols)
        candidates = score_row[eligible_row.astype(bool)].dropna()
        ascending = str(selector.get("rank_order", "desc")).lower() in {"asc", "ascending", "smallest"}
        ranked = candidates.sort_values(ascending=ascending, kind="mergesort")
        top_n = self._positive_int(selector.get("top_n"), default=len(self.symbols))
        selected = [str(item) for item in ranked.head(top_n).index.tolist()]
        weights = pd.Series(0.0, index=self.symbols, dtype=float)
        if selected:
            position_limit = self._float(allocation_cfg.get("position_limit", 1.0), default=1.0)
            raw_weight = 1.0 / float(len(selected))
            per_asset_weight = min(raw_weight, position_limit)
            weights.loc[selected] = per_asset_weight
        return weights, selected, [str(item) for item in ranked.index.tolist()]

    def _explicit_target_weights_for_date(
        self,
        date: pd.Timestamp,
        allocation_cfg: Dict[str, Any],
    ) -> pd.Series:
        frame_name = str(
            allocation_cfg.get("frame")
            or allocation_cfg.get("target_weight_frame")
            or allocation_cfg.get("target_weights_frame")
            or "target_weight"
        ).strip().lower()
        if frame_name not in self.market_data:
            raise KeyError(
                "allocation.method=target_weight_frame requires market_data "
                f"to include '{frame_name}'"
            )
        frame = self.market_data[frame_name].reindex(index=self.close.index, columns=self.symbols)
        weights = pd.Series(0.0, index=self.symbols, dtype=float)
        if date in frame.index:
            weights = pd.to_numeric(frame.loc[date], errors="coerce").fillna(0.0).astype(float)
            weights = weights.reindex(index=self.symbols).fillna(0.0)

        risk_cfg = self.config.get("risk", {}) if isinstance(self.config.get("risk"), dict) else {}
        allow_short = bool(risk_cfg.get("allow_short", False))
        if not allow_short:
            weights = weights.clip(lower=0.0)

        max_gross = self._float(risk_cfg.get("max_gross_exposure", 1.0), default=1.0)
        gross = float(weights.abs().sum())
        if gross > max_gross > 0.0 and bool(allocation_cfg.get("normalize_if_overweight", True)):
            weights = weights * (max_gross / gross)
        return weights

    def _ensure_signal_target_weight_frame(self, features: Dict[str, pd.DataFrame]) -> None:
        allocation_cfg = self.config.get("allocation", {})
        allocation_method = str((allocation_cfg or {}).get("method", "")).strip().lower()
        if allocation_method not in {"signal_state", "signal_target_weight"}:
            return
        signals = self.config.get("signals", {})
        if not isinstance(signals, dict):
            raise ValueError("allocation.method=signal_state requires a signals object")
        if self._execution_is_bar_offset_round_trip():
            if any((allocation_cfg or {}).get(key) for key in ("frame", "target_weight_frame", "target_weights_frame")):
                raise ValueError("bar_offset execution does not support allocation frame overrides")
            if not signals.get("entry"):
                raise ValueError("bar_offset execution requires a signals.entry event source")
            if signals.get("exit"):
                raise ValueError(
                    "bar_offset execution currently models fixed round trips from signals.entry; "
                    "signals.exit is not supported in this execution mode"
                )
            self._reject_bar_offset_lookahead_entry(signals.get("entry"))
            entry = self._evaluate_condition(signals.get("entry"), features)
            target_weight = self._float(
                signals.get("target_weight", allocation_cfg.get("target_weight", 1.0)),
                default=1.0,
            )
            self.market_data["target_weight"] = entry.astype(float) * float(target_weight)
            return
        if "target_weight" in self.market_data:
            return
        if self._execution_is_same_session():
            self._reject_same_session_lookahead_entry(signals.get("entry"))
        entry = self._evaluate_condition(signals.get("entry"), features)
        exit_node, timer_bars, timer_mode = self._split_timer_exit_condition(signals.get("exit"))
        exit_ = (
            self._evaluate_condition(exit_node, features)
            if exit_node
            else pd.DataFrame(False, index=self.close.index, columns=self.symbols)
        )
        target_weight = self._float(
            signals.get("target_weight", allocation_cfg.get("target_weight", 1.0)),
            default=1.0,
        )
        if self._execution_is_same_session():
            self.market_data["target_weight"] = entry.astype(float) * float(target_weight)
            return
        conflict_policy = str(signals.get("conflict_policy") or "exit_then_entry").strip().lower()
        if conflict_policy not in {"exit_then_entry", "entry_then_exit", "flat_on_conflict"}:
            raise ValueError(f"Unsupported signal conflict policy: {conflict_policy}")

        entry = entry.reindex(index=self.close.index, columns=self.symbols).fillna(False).astype(bool)
        exit_ = exit_.reindex(index=self.close.index, columns=self.symbols).fillna(False).astype(bool)
        if timer_bars is not None:
            exit_ = self._apply_timer_exit_mask(
                entry=entry,
                exit_=exit_,
                timer_bars=timer_bars,
                timer_mode=timer_mode,
                reset_on_reentry=bool(signals.get("reset_timer_on_reentry_signal", False)),
            )
        state_events = pd.DataFrame(np.nan, index=self.close.index, columns=self.symbols, dtype=float)
        if conflict_policy == "flat_on_conflict":
            conflict = entry & exit_
            state_events = state_events.mask(entry & ~conflict, 1.0)
            state_events = state_events.mask(exit_ | conflict, 0.0)
        elif conflict_policy == "entry_then_exit":
            state_events = state_events.mask(entry, 1.0)
            state_events = state_events.mask(exit_, 0.0)
        else:
            state_events = state_events.mask(exit_, 0.0)
            state_events = state_events.mask(entry, 1.0)
        if self._execution_is_next_bar_open_signal_state(allocation_cfg):
            state_events = state_events.shift(1)
        self.market_data["target_weight"] = state_events.ffill().fillna(0.0) * float(target_weight)

    def _split_timer_exit_condition(self, node: Any) -> Tuple[Optional[Any], Optional[int], str]:
        if not node:
            return None, None, "timer_only"
        if isinstance(node, list):
            return self._split_timer_children(node, "and")
        if not isinstance(node, dict):
            return node, None, "timer_only"
        op = str(node.get("op") or "").strip().lower()
        if op in {"time_stop_bars", "timer_bars"}:
            return None, self._positive_int(self._resolve_config_value(node.get("value")), default=0), "timer_only"
        if "all" in node:
            children = node.get("all")
            return self._split_timer_children(children if isinstance(children, list) else [], "and")
        if "any" in node:
            children = node.get("any")
            return self._split_timer_children(children if isinstance(children, list) else [], "or")
        return node, None, "timer_only"

    def _split_timer_children(self, children: List[Any], mode: str) -> Tuple[Optional[Any], Optional[int], str]:
        timer_bars: Optional[int] = None
        remainder: List[Any] = []
        for child in children:
            child_remainder, child_timer, _ = self._split_timer_exit_condition(child)
            if child_timer is not None:
                if timer_bars is not None and child_timer != timer_bars:
                    raise ValueError("signals.exit may contain only one time_stop_bars condition")
                timer_bars = child_timer
            elif child_remainder:
                remainder.append(child_remainder)
        if timer_bars is None:
            return ({"all": remainder} if mode == "and" else {"any": remainder}), None, "timer_only"
        if not remainder:
            return None, timer_bars, "timer_only"
        if len(remainder) == 1:
            return remainder[0], timer_bars, mode
        return ({"all": remainder} if mode == "and" else {"any": remainder}), timer_bars, mode

    def _apply_timer_exit_mask(
        self,
        *,
        entry: pd.DataFrame,
        exit_: pd.DataFrame,
        timer_bars: int,
        timer_mode: str,
        reset_on_reentry: bool,
    ) -> pd.DataFrame:
        if timer_bars <= 0:
            raise ValueError("time_stop_bars value must be a positive integer")
        out = pd.DataFrame(False, index=entry.index, columns=entry.columns)
        for column in entry.columns:
            in_position = False
            entry_row: Optional[int] = None
            column_idx = out.columns.get_loc(column)
            for row_idx in range(len(entry.index)):
                entry_signal = bool(entry.iloc[row_idx][column])
                exit_signal = bool(exit_.iloc[row_idx][column])
                if entry_signal and (not in_position or reset_on_reentry):
                    in_position = True
                    entry_row = row_idx
                timer_ready = in_position and entry_row is not None and (row_idx - entry_row) >= timer_bars
                if timer_mode == "and":
                    should_exit = timer_ready and exit_signal
                elif timer_mode == "or":
                    should_exit = timer_ready or exit_signal
                else:
                    should_exit = timer_ready
                if should_exit:
                    out.iloc[row_idx, column_idx] = True
                    in_position = False
                    entry_row = None
        return out

    def _reject_same_session_lookahead_entry(self, entry_node: Any) -> None:
        unsafe_fields = self._same_bar_completed_indicator_names()
        used_fields = self._condition_field_names(entry_node)
        normalized_used_fields = {self._normalize_field_name(field) for field in used_fields}
        overlap = sorted(unsafe_fields & used_fields)
        raw_overlap = sorted(_SAME_SESSION_UNSAFE_MARKET_FIELDS & normalized_used_fields)
        if raw_overlap:
            raise ValueError(
                "same-session entry cannot use current-bar market fields "
                f"({', '.join(raw_overlap)}). Use a calendar/pre-known entry, lag the field, or trade the next bar."
            )
        if not overlap:
            return
        joined = ", ".join(overlap)
        raise ValueError(
            "same-session entry cannot use indicators that require the completed current bar "
            f"({joined}). Use a calendar/pre-known entry, lag the indicator, or trade the next bar."
        )

    def _reject_bar_offset_lookahead_entry(self, entry_node: Any) -> None:
        spec = self._bar_offset_execution_spec()
        if int(spec["entry_delay_bars"]) > 0:
            return
        entry_price = str(spec["entry_price"])
        if entry_price not in {"open", "close"}:
            return
        unsafe_fields = self._same_bar_completed_indicator_names()
        used_fields = self._condition_field_names(entry_node)
        normalized_used_fields = {self._normalize_field_name(field) for field in used_fields}
        raw_overlap = sorted(_SAME_SESSION_UNSAFE_MARKET_FIELDS & normalized_used_fields)
        overlap = sorted(unsafe_fields & used_fields)
        if raw_overlap:
            raise ValueError(
                "bar_offset entry on the signal bar cannot use current-bar market fields "
                f"({', '.join(raw_overlap)}). Use a calendar/pre-known entry or add entry_delay_bars."
            )
        if overlap:
            raise ValueError(
                "bar_offset entry on the signal bar cannot use indicators that require the completed current bar "
                f"({', '.join(overlap)}). Use a calendar/pre-known entry or add entry_delay_bars."
            )

    def _same_bar_completed_indicator_names(self) -> set[str]:
        names: set[str] = set()
        for spec in self._indicator_specs_for_backtest():
            if not isinstance(spec, dict):
                continue
            op = str(spec.get("op") or spec.get("type") or "").strip().lower()
            name = str(spec.get("name") or "").strip()
            if name and op in _SAME_BAR_COMPLETED_INDICATOR_OPS:
                names.add(name)
        return names

    def _indicator_specs_for_backtest(self) -> List[Any]:
        if self.config.get("features") or self.config.get("indicators"):
            raise ValueError(
                "strategy_run uses computed_fields[] only; features[] and indicators[] are removed aliases."
            )
        computed_fields = list(self.config.get("computed_fields", []) or [])
        return computed_fields

    def _fill_model_config(self) -> Dict[str, Any]:
        fill_model = self.config.get("fill_model", {}) if isinstance(self.config.get("fill_model"), dict) else {}
        if self.config.get("execution"):
            raise ValueError(
                "strategy_run uses fill_model{} only; execution{} is a removed alias."
            )
        return fill_model

    def _condition_field_names(self, node: Any) -> set[str]:
        names: set[str] = set()
        self._collect_condition_field_names(node, names)
        return names

    def _collect_condition_field_names(self, node: Any, names: set[str]) -> None:
        if not node:
            return
        if isinstance(node, list):
            for item in node:
                self._collect_condition_field_names(item, names)
            return
        if isinstance(node, str):
            names.add(node.strip())
            return
        if not isinstance(node, dict):
            return
        for key in ("field", "left", "right_field", "right"):
            value = node.get(key)
            if isinstance(value, str):
                names.add(value.strip())
            elif isinstance(value, dict):
                self._collect_condition_field_names(value, names)
            elif isinstance(value, list):
                for item in value:
                    self._collect_condition_field_names(item, names)
        for key in ("all", "any"):
            value = node.get(key)
            if isinstance(value, list):
                for item in value:
                    self._collect_condition_field_names(item, names)
        if "not" in node:
            self._collect_condition_field_names(node.get("not"), names)

    def _fixed_target_weights(self, allocation_cfg: Dict[str, Any]) -> pd.Series:
        raw_weights = allocation_cfg.get("weights", {}) if isinstance(allocation_cfg, dict) else {}
        profiles = allocation_cfg.get("weight_profiles", {}) if isinstance(allocation_cfg, dict) else {}
        profile_id = allocation_cfg.get("profile_id") if isinstance(allocation_cfg, dict) else None
        profile_id = self._resolve_config_value(profile_id)
        if profile_id is not None and isinstance(profiles, dict):
            profile_key = str(profile_id)
            profile_weights = profiles.get(profile_key)
            if profile_weights is None:
                raise ValueError(f"Unknown fixed weight profile_id: {profile_key}")
            raw_weights = profile_weights
        weights = pd.Series(0.0, index=self.symbols, dtype=float)
        if isinstance(raw_weights, dict):
            for asset, raw_weight in raw_weights.items():
                asset_key = str(asset)
                if asset_key not in weights.index:
                    continue
                raw_weight = self._resolve_config_value(raw_weight)
                weights.loc[asset_key] = max(0.0, self._float(raw_weight, default=0.0))
        total = float(weights.sum())
        if total > 1.0 and bool(allocation_cfg.get("normalize_if_overweight", True)):
            weights = weights / total
        return weights

    def _overlay_weight_series(
        self,
        allocation_cfg: Dict[str, Any],
        *,
        keys: Tuple[str, ...],
        required: bool,
    ) -> pd.Series:
        raw_weights: Any = None
        for key in keys:
            if key in allocation_cfg:
                raw_weights = allocation_cfg.get(key)
                break
        weights = pd.Series(0.0, index=self.symbols, dtype=float)
        if isinstance(raw_weights, dict):
            for asset, raw_weight in raw_weights.items():
                asset_key = str(asset).strip().upper()
                if asset_key not in weights.index:
                    continue
                weights.loc[asset_key] = self._float(
                    self._resolve_config_value(raw_weight),
                    default=0.0,
                )
        if required and float(weights.abs().sum()) <= 1e-12:
            raise ValueError(f"calendar_event_overlay requires one of: {', '.join(keys)}")
        return weights

    def _scale_overlay_weights_if_needed(
        self,
        weights: pd.Series,
        risk_cfg: Dict[str, Any],
    ) -> pd.Series:
        max_gross = self._float(risk_cfg.get("max_gross_exposure", 1.0), default=1.0)
        gross = float(weights.abs().sum())
        if gross > max_gross > 0.0:
            allocation_cfg = self.config.get("allocation", {}) if isinstance(self.config.get("allocation"), dict) else {}
            if bool(allocation_cfg.get("normalize_if_overweight", True)):
                return weights * (max_gross / gross)
            raise ValueError("calendar_event_overlay target weights exceed risk.max_gross_exposure")
        return weights

    @staticmethod
    def _overlay_trade_action(before_weight: float, target_weight: float, delta: float) -> str:
        if target_weight < -1e-12 and before_weight >= -1e-12:
            return "new_short"
        if before_weight < -1e-12 and target_weight >= -1e-12:
            return "close_short"
        if delta > 1e-12:
            return "buy"
        if delta < -1e-12 and target_weight <= 1e-12:
            return "exit"
        if delta < -1e-12:
            return "sell"
        return "hold"

    def _append_rebalance_rows(
        self,
        *,
        rows: List[Dict[str, Any]],
        holdings: List[Dict[str, Any]],
        date: pd.Timestamp,
        target_weights: pd.Series,
        selected_assets: List[str],
        ranked_assets: List[str],
        score: pd.DataFrame,
        eligible: pd.DataFrame,
        equity: float,
        turnover: float,
        cost_rate: float,
        trade_cost: float,
    ) -> None:
        rows.append(
            {
                "Time": date,
                "Rebalance": True,
                "Selected_assets": selected_assets,
                "Selected_count": len(selected_assets),
                "Ranked_candidates": ranked_assets,
                "Turnover": turnover,
                "Cost_rate": cost_rate,
                "Trade_cost": trade_cost,
                "Equity_value": equity,
            }
        )
        for rank, asset in enumerate(ranked_assets, start=1):
            holdings.append(
                {
                    "Time": date,
                    "Asset": asset,
                    "Rank": rank,
                    "Selected": asset in selected_assets,
                    "Eligible": bool(eligible.loc[date, asset]) if date in eligible.index else False,
                    "Score": float(score.loc[date, asset]) if date in score.index and pd.notna(score.loc[date, asset]) else np.nan,
                    "Target_weight": float(target_weights.get(asset, 0.0)),
                }
            )

    def _append_rebalance_trade_rows(
        self,
        *,
        rows: List[Dict[str, Any]],
        date: pd.Timestamp,
        before_weights: pd.Series,
        target_weights: pd.Series,
        selected_assets: List[str],
        ranked_assets: List[str],
        score: pd.DataFrame,
        eligible: pd.DataFrame,
        selector: Dict[str, Any],
        turnover: float,
        trade_cost: float,
    ) -> None:
        ranked_lookup = {asset: rank for rank, asset in enumerate(ranked_assets, start=1)}
        assets = sorted(
            set(self.symbols)
            | {str(asset) for asset in selected_assets}
            | {str(asset) for asset in ranked_assets}
            | {str(asset) for asset in before_weights[before_weights.abs() > 1e-12].index.tolist()}
            | {str(asset) for asset in target_weights[target_weights.abs() > 1e-12].index.tolist()}
        )
        rank_by = str(selector.get("rank_by") or "").strip()
        for asset in assets:
            before_weight = float(before_weights.get(asset, 0.0))
            target_weight = float(target_weights.get(asset, 0.0))
            delta = target_weight - before_weight
            abs_delta = abs(delta)
            if abs_delta <= 1e-12 and target_weight <= 1e-12 and before_weight <= 1e-12:
                continue
            if delta > 1e-12:
                action = "buy"
            elif delta < -1e-12 and target_weight <= 1e-12:
                action = "exit"
            elif delta < -1e-12:
                action = "sell"
            else:
                action = "hold"
            allocated_cost = trade_cost * abs_delta / turnover if turnover > 0.0 else 0.0
            rank = ranked_lookup.get(asset)
            eligible_flag = bool(eligible.loc[date, asset]) if date in eligible.index and asset in eligible.columns else False
            score_value = (
                float(score.loc[date, asset])
                if date in score.index and asset in score.columns and pd.notna(score.loc[date, asset])
                else np.nan
            )
            reason_parts: List[str] = []
            if rank is not None:
                reason_parts.append(f"rank {rank}" + (f" by {rank_by}" if rank_by else ""))
            elif before_weight > 0.0 and target_weight <= 0.0:
                reason_parts.append("not selected at this rebalance")
            if eligible_flag:
                reason_parts.append("eligible")
            elif action != "hold":
                reason_parts.append("not eligible")
            rows.append(
                {
                    "Time": date,
                    "Asset": asset,
                    "Before_weight": before_weight,
                    "Target_weight": target_weight,
                    "Trade_delta": delta,
                    "Action": action,
                    "Trade_turnover": abs_delta,
                    "Allocated_cost": allocated_cost,
                    "Selected": asset in selected_assets,
                    "Eligible": eligible_flag,
                    "Rank": rank,
                    "Score": score_value,
                    "Reason": "; ".join(reason_parts) if reason_parts else "target unchanged",
                }
            )

    @staticmethod
    def _all(items: List[pd.DataFrame]) -> pd.DataFrame:
        if not items:
            return pd.DataFrame()
        out = items[0].copy()
        for item in items[1:]:
            out = out & item
        return out.fillna(False)

    @staticmethod
    def _any(items: List[pd.DataFrame]) -> pd.DataFrame:
        if not items:
            return pd.DataFrame()
        out = items[0].copy()
        for item in items[1:]:
            out = out | item
        return out.fillna(False)

    @staticmethod
    def _positive_int(value: Any, *, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _non_negative_int(value: Any, *, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed >= 0 else default

    @staticmethod
    def _float(value: Any, default: float = 0.0) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return parsed if np.isfinite(parsed) else default

    @staticmethod
    def _timestamp_iso(value: Any) -> Optional[str]:
        timestamp = pd.to_datetime(value, errors="coerce")
        if pd.isna(timestamp):
            return None
        return pd.Timestamp(timestamp).tz_localize(None).date().isoformat()


def load_multi_asset_config(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))
