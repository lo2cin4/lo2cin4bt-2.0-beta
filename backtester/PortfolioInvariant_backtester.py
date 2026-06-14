"""Portfolio accounting invariant checks for backtester results.

This module is intentionally independent from the engines.  Engines, WFA,
and tests can opt into it without changing existing result contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import pandas as pd


class PortfolioInvariantError(AssertionError):
    """Raised when portfolio invariant checks fail in raise-on-error mode."""

    def __init__(self, result: "PortfolioInvariantResult") -> None:
        self.result = result
        first = result.violations[0] if result.violations else {}
        code = first.get("code", "portfolio_invariant_failed")
        message = first.get("message", "Portfolio invariant check failed")
        super().__init__(f"{code}: {message}")


@dataclass
class PortfolioStateSnapshot:
    """A normalized account/portfolio state at one timestamp."""

    timestamp: Any
    cash: float
    market_value: float
    equity: float
    realized_pnl: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    fees_paid: float = 0.0
    positions: Dict[str, float] = field(default_factory=dict)
    prices: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PortfolioInvariantResult:
    """Collected invariant check result."""

    ok: bool
    violations: List[Dict[str, Any]] = field(default_factory=list)
    checked_count: int = 0
    skipped_count: int = 0


class PortfolioInvariantChecker:
    """Checks simple but extensible account/portfolio invariants."""

    def __init__(
        self,
        *,
        tolerance: float = 1e-8,
        long_only: bool = False,
        allow_negative_cash: bool = True,
        max_gross_exposure: Optional[float] = None,
        raise_on_error: bool = False,
    ) -> None:
        self.tolerance = float(tolerance)
        self.long_only = bool(long_only)
        self.allow_negative_cash = bool(allow_negative_cash)
        self.max_gross_exposure = max_gross_exposure
        self.raise_on_error = bool(raise_on_error)

    def check_snapshot(self, snapshot: PortfolioStateSnapshot) -> PortfolioInvariantResult:
        result = PortfolioInvariantResult(ok=True)
        positions = _float_mapping(snapshot.positions)
        prices = _float_mapping(snapshot.prices)

        scalar_fields = {
            "cash": snapshot.cash,
            "market_value": snapshot.market_value,
            "equity": snapshot.equity,
            "fees_paid": snapshot.fees_paid,
        }
        finite_scalars = True
        for name, value in scalar_fields.items():
            result.checked_count += 1
            if not _is_finite(value):
                finite_scalars = False
                self._violate(
                    result,
                    code="non_finite_account_value",
                    message=f"{name} must be a finite number",
                    timestamp=snapshot.timestamp,
                    expected="finite",
                    actual=value,
                    metadata={"field": name, **snapshot.metadata},
                )

        for asset, quantity in positions.items():
            result.checked_count += 1
            if not _is_finite(quantity):
                self._violate(
                    result,
                    code="non_finite_position_qty",
                    message=f"position quantity for {asset} must be finite",
                    timestamp=snapshot.timestamp,
                    expected="finite",
                    actual=quantity,
                    metadata={"asset": asset, **snapshot.metadata},
                )

        for asset, price in prices.items():
            result.checked_count += 1
            if not _is_finite(price):
                self._violate(
                    result,
                    code="non_finite_price",
                    message=f"price for {asset} must be finite",
                    timestamp=snapshot.timestamp,
                    expected="finite",
                    actual=price,
                    metadata={"asset": asset, **snapshot.metadata},
                )

        result.checked_count += 1
        if _is_finite(snapshot.fees_paid) and float(snapshot.fees_paid) < -self.tolerance:
            self._violate(
                result,
                code="negative_fees_paid",
                message="fees_paid must be non-negative",
                timestamp=snapshot.timestamp,
                expected=">= 0",
                actual=snapshot.fees_paid,
                metadata=snapshot.metadata,
            )

        if self.long_only:
            for asset, quantity in positions.items():
                result.checked_count += 1
                if _is_finite(quantity) and quantity < -self.tolerance:
                    self._violate(
                        result,
                        code="long_only_negative_position",
                        message=f"long_only portfolio cannot hold negative {asset}",
                        timestamp=snapshot.timestamp,
                        expected=">= 0",
                        actual=quantity,
                        metadata={"asset": asset, **snapshot.metadata},
                    )

        result.checked_count += 1
        if (
            not self.allow_negative_cash
            and _is_finite(snapshot.cash)
            and float(snapshot.cash) < -self.tolerance
        ):
            self._violate(
                result,
                code="negative_cash",
                message="cash cannot be negative when allow_negative_cash is false",
                timestamp=snapshot.timestamp,
                expected=">= 0",
                actual=snapshot.cash,
                metadata=snapshot.metadata,
            )

        result.checked_count += 1
        if finite_scalars:
            expected_equity = float(snapshot.cash) + float(snapshot.market_value)
            if not _close(float(snapshot.equity), expected_equity, self.tolerance):
                self._violate(
                    result,
                    code="equity_cash_market_value_mismatch",
                    message="equity must equal cash plus market value",
                    timestamp=snapshot.timestamp,
                    expected=expected_equity,
                    actual=snapshot.equity,
                    metadata=snapshot.metadata,
                )

        if positions:
            missing_prices = [asset for asset in positions if asset not in prices]
            if missing_prices:
                result.skipped_count += 1
            else:
                result.checked_count += 1
                expected_market_value = sum(
                    float(quantity) * float(prices[asset])
                    for asset, quantity in positions.items()
                    if _is_finite(quantity) and _is_finite(prices[asset])
                )
                if _is_finite(snapshot.market_value) and not _close(
                    float(snapshot.market_value),
                    expected_market_value,
                    self.tolerance,
                ):
                    self._violate(
                        result,
                        code="market_value_positions_prices_mismatch",
                        message="market_value must equal sum(position quantity * latest price)",
                        timestamp=snapshot.timestamp,
                        expected=expected_market_value,
                        actual=snapshot.market_value,
                        metadata=snapshot.metadata,
                    )
        else:
            result.skipped_count += 1

        if self.max_gross_exposure is not None:
            has_all_prices = positions and not [asset for asset in positions if asset not in prices]
            if has_all_prices and _is_finite(snapshot.equity) and abs(float(snapshot.equity)) > self.tolerance:
                result.checked_count += 1
                gross_value = sum(abs(float(quantity) * float(prices[asset])) for asset, quantity in positions.items())
                gross_exposure = gross_value / abs(float(snapshot.equity))
                if gross_exposure > float(self.max_gross_exposure) + self.tolerance:
                    self._violate(
                        result,
                        code="max_gross_exposure_exceeded",
                        message="gross exposure exceeds max_gross_exposure",
                        timestamp=snapshot.timestamp,
                        expected=f"<= {self.max_gross_exposure}",
                        actual=gross_exposure,
                        metadata=snapshot.metadata,
                    )
            else:
                result.skipped_count += 1

        self._maybe_raise(result)
        return result

    def check_series(self, snapshots: Iterable[PortfolioStateSnapshot]) -> PortfolioInvariantResult:
        combined = PortfolioInvariantResult(ok=True)
        for snapshot in snapshots:
            result = self.check_snapshot(snapshot)
            combined.checked_count += result.checked_count
            combined.skipped_count += result.skipped_count
            combined.violations.extend(result.violations)
        combined.ok = not combined.violations
        self._maybe_raise(combined)
        return combined

    def check_trade_records(
        self,
        records: Any,
        snapshots: Optional[Sequence[PortfolioStateSnapshot]] = None,
    ) -> PortfolioInvariantResult:
        """Best-effort checks for existing trade/rebalance record formats."""

        result = PortfolioInvariantResult(ok=True)
        frame = _records_to_frame(records)
        if frame.empty:
            result.skipped_count += 1
            return result

        fee_columns = [
            column
            for column in ("Transaction_cost", "Allocated_cost", "Trade_cost", "fee", "fees")
            if column in frame.columns
        ]
        if not fee_columns:
            result.skipped_count += 1
        for column in fee_columns:
            for index, value in frame[column].items():
                result.checked_count += 1
                if not _is_finite(value) or float(value) < -self.tolerance:
                    self._violate(
                        result,
                        code="trade_record_negative_or_non_finite_fee",
                        message=f"{column} must be finite and non-negative",
                        timestamp=_row_timestamp(frame, index),
                        expected="finite and >= 0",
                        actual=value,
                        metadata={"column": column},
                    )

        position_columns = [column for column in ("Position_size", "Position_qty", "Quantity", "qty") if column in frame.columns]
        if self.long_only and position_columns:
            for column in position_columns:
                for index, value in frame[column].items():
                    result.checked_count += 1
                    if _is_finite(value) and float(value) < -self.tolerance:
                        self._violate(
                            result,
                            code="trade_record_long_only_negative_position",
                            message=f"{column} cannot be negative in long_only mode",
                            timestamp=_row_timestamp(frame, index),
                            expected=">= 0",
                            actual=value,
                            metadata={"column": column},
                        )
        elif self.long_only:
            result.skipped_count += 1

        asset_column = _first_existing(frame, ("Asset", "Trading_instrument", "Symbol", "symbol"))
        delta_column = _first_existing(frame, ("Trade_delta", "Position_delta", "Quantity_delta"))
        if snapshots and asset_column and delta_column:
            cumulative: Dict[str, float] = {}
            for _, row in frame.iterrows():
                asset = str(row[asset_column])
                delta = _to_float(row[delta_column])
                if _is_finite(delta):
                    cumulative[asset] = cumulative.get(asset, 0.0) + float(delta)
            latest_positions = snapshots[-1].positions if snapshots else {}
            for asset, expected_qty in latest_positions.items():
                if asset not in cumulative:
                    result.skipped_count += 1
                    continue
                result.checked_count += 1
                if not _close(float(expected_qty), cumulative[asset], self.tolerance):
                    self._violate(
                        result,
                        code="trade_record_position_reconciliation_mismatch",
                        message=f"cumulative trade deltas do not match latest position for {asset}",
                        timestamp=snapshots[-1].timestamp,
                        expected=expected_qty,
                        actual=cumulative[asset],
                        metadata={"asset": asset},
                    )
        else:
            result.skipped_count += 1

        self._maybe_raise(result)
        return result

    def _violate(
        self,
        result: PortfolioInvariantResult,
        *,
        code: str,
        message: str,
        timestamp: Any,
        expected: Any,
        actual: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        result.ok = False
        result.violations.append(
            {
                "code": code,
                "message": message,
                "timestamp": timestamp,
                "expected": expected,
                "actual": actual,
                "tolerance": self.tolerance,
                "metadata": metadata or {},
            }
        )

    def _maybe_raise(self, result: PortfolioInvariantResult) -> None:
        if self.raise_on_error and result.violations:
            raise PortfolioInvariantError(result)


def snapshots_from_multi_asset_result(result: Any) -> List[PortfolioStateSnapshot]:
    """Build best-effort snapshots from MultiAssetBacktestResult.

    Current multi-asset results store target/drift weights rather than share
    quantities.  The helper therefore validates accounting in weight space:
    position quantity is target weight and price is current equity value.
    """

    equity_curve = getattr(result, "equity_curve", None)
    if equity_curve is None or getattr(equity_curve, "empty", True):
        return []

    snapshots: List[PortfolioStateSnapshot] = []
    cumulative_fees = 0.0
    for _, row in equity_curve.iterrows():
        equity = _to_float(row.get("Equity_value", row.get("equity", 0.0)))
        cash_weight = _to_float(row.get("Cash_weight", 0.0))
        cash = equity * cash_weight if _is_finite(equity) and _is_finite(cash_weight) else 0.0
        positions: Dict[str, float] = {}
        for column in equity_curve.columns:
            if str(column).startswith("Weight_"):
                asset = str(column)[len("Weight_") :]
                weight = _to_float(row.get(column))
                if _is_finite(weight) and abs(float(weight)) > 0.0:
                    positions[asset] = float(weight)
        prices = {asset: equity for asset in positions}
        market_value = sum(positions[asset] * prices[asset] for asset in positions)
        trade_cost = _to_float(row.get("Trade_cost", 0.0))
        if _is_finite(trade_cost):
            cumulative_fees += max(float(trade_cost), 0.0)
        snapshots.append(
            PortfolioStateSnapshot(
                timestamp=row.get("Time"),
                cash=cash,
                market_value=market_value,
                equity=cash + market_value,
                fees_paid=cumulative_fees,
                positions=positions,
                prices=prices,
                metadata={
                    "source": "multi_asset_result",
                    "position_unit": "portfolio_weight",
                    "price_unit": "equity_value",
                    "share_level_accounting": "not_available",
                },
            )
        )
    return snapshots


def snapshots_from_single_asset_result(
    result: Any,
    *,
    symbol: str = "ASSET",
    prices: Optional[Sequence[float]] = None,
) -> List[PortfolioStateSnapshot]:
    """Build best-effort snapshots from legacy single-asset result dictionaries."""

    if isinstance(result, Mapping):
        equity_values = _sequence_or_empty(result.get("equity_values"))
        if len(equity_values) == 0:
            equity_values = _sequence_or_empty(result.get("equity_curve"))
        positions = _sequence_or_empty(result.get("positions"))
    else:
        equity_values = _sequence_or_empty(getattr(result, "equity_values", []))
        positions = _sequence_or_empty(getattr(result, "positions", []))

    snapshots: List[PortfolioStateSnapshot] = []
    count = max(len(equity_values), len(positions))
    for index in range(count):
        equity = _to_float(equity_values[index]) if index < len(equity_values) else 0.0
        quantity = _to_float(positions[index]) if index < len(positions) else 0.0
        if prices is not None and index < len(prices) and _is_finite(prices[index]):
            price = float(prices[index])
            market_value = quantity * price if _is_finite(quantity) else 0.0
            cash = equity - market_value if _is_finite(equity) else 0.0
            snapshot_positions = {symbol: float(quantity)}
            snapshot_prices = {symbol: price}
        else:
            cash = equity
            market_value = 0.0
            snapshot_positions = {}
            snapshot_prices = {}
        snapshots.append(
            PortfolioStateSnapshot(
                timestamp=index,
                cash=cash,
                market_value=market_value,
                equity=cash + market_value,
                fees_paid=0.0,
                positions=snapshot_positions,
                prices=snapshot_prices,
                metadata={
                    "source": "single_asset_result",
                    "share_level_accounting": "available_with_prices" if snapshot_prices else "not_available",
                },
            )
        )
    return snapshots


def _float_mapping(values: Optional[Mapping[str, Any]]) -> Dict[str, float]:
    if not values:
        return {}
    return {str(key): _to_float(value) for key, value in values.items()}


def _to_float(value: Any) -> float:
    try:
        if value is None:
            return float("nan")
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _is_finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _close(actual: float, expected: float, tolerance: float) -> bool:
    return math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)


def _records_to_frame(records: Any) -> pd.DataFrame:
    if records is None:
        return pd.DataFrame()
    if isinstance(records, pd.DataFrame):
        return records
    if isinstance(records, Mapping):
        return pd.DataFrame(records)
    try:
        return pd.DataFrame(list(records))
    except TypeError:
        return pd.DataFrame()


def _row_timestamp(frame: pd.DataFrame, index: Any) -> Any:
    if "Time" in frame.columns:
        return frame.loc[index, "Time"]
    if "Entry_time" in frame.columns:
        return frame.loc[index, "Entry_time"]
    if "Exit_time" in frame.columns:
        return frame.loc[index, "Exit_time"]
    return index


def _first_existing(frame: pd.DataFrame, columns: Sequence[str]) -> Optional[str]:
    for column in columns:
        if column in frame.columns:
            return column
    return None


def _sequence_or_empty(value: Any) -> Sequence[Any]:
    if value is None:
        return []
    return value
