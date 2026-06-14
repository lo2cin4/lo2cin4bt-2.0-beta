"""Materialize feature-contract-declared sources into an aligned runtime frame."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from utils.path_resolver import resolve_input_path

_TIME_CANDIDATES = ("Time", "time", "Datetime", "datetime", "Date", "date", "Timestamp", "timestamp")
_INSTRUMENT_CANDIDATES = (
    "Trading_instrument",
    "trading_instrument",
    "Instrument",
    "instrument",
    "Symbol",
    "symbol",
    "Ticker",
    "ticker",
)
_AUDIT_TEMP_SOURCE_TIME = "__audit_source_time__"


@dataclass(frozen=True)
class _FeatureBinding:
    field: str
    source_column: str
    source_type: str
    fill_policy: str
    lag_bars: int
    staleness_max_bars: Optional[int]


class FeatureContractMaterializerBacktester:
    """Join feature contract sources onto a base dataframe for node_ir runtime."""

    def __init__(
        self,
        *,
        base_data: pd.DataFrame,
        repo_root: Path,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.base_data = base_data.copy()
        self.repo_root = Path(repo_root).resolve()
        self.logger = logger or logging.getLogger("lo2cin4bt.backtester.feature_materializer")

    def materialize(
        self,
        *,
        feature_contract: Dict[str, Any],
        feature_contract_path: Optional[str] = None,
    ) -> pd.DataFrame:
        if not isinstance(feature_contract, dict):
            return self.base_data.copy()
        features = feature_contract.get("features", [])
        if not isinstance(features, list) or not features:
            return self.base_data.copy()

        frame = self.base_data.copy()
        base_time_field = self._resolve_base_time_field(frame, feature_contract)
        if base_time_field is None:
            return frame
        frame[base_time_field] = pd.to_datetime(frame[base_time_field], errors="coerce")
        frame = frame.reset_index(drop=True)
        frame["__materializer_row_id__"] = range(len(frame))

        join_mode = str(feature_contract.get("alignment_policy", {}).get("join_mode", "left")).strip().lower()
        if join_mode not in {"left", "inner", "asof"}:
            raise ValueError(f"Unsupported feature alignment join_mode: {join_mode}")

        grouped = self._group_feature_bindings(features)
        for group in grouped:
            frame = self._materialize_group(
                frame=frame,
                base_time_field=base_time_field,
                join_mode=join_mode,
                group=group,
                feature_contract=feature_contract,
                feature_contract_path=feature_contract_path,
            )

        frame = frame.sort_values("__materializer_row_id__").drop(columns=["__materializer_row_id__"])
        return frame.reset_index(drop=True)

    def _materialize_group(
        self,
        *,
        frame: pd.DataFrame,
        base_time_field: str,
        join_mode: str,
        group: Tuple[Tuple[str, str, str, str, str, Optional[bool], str], List[_FeatureBinding]],
        feature_contract: Dict[str, Any],
        feature_contract_path: Optional[str],
    ) -> pd.DataFrame:
        (
            uri,
            source_id,
            source_type,
            source_time_field,
            source_instrument_field,
            source_dayfirst,
            source_time_format,
        ), bindings = group
        _ = source_id

        if not uri:
            for binding in bindings:
                if binding.field in frame.columns:
                    continue
                if binding.source_column in frame.columns:
                    raw_series = pd.to_numeric(frame[binding.source_column], errors="coerce")
                    transformed = self._apply_transform(raw_series, binding=binding)
                    frame[binding.field] = transformed
                    audit_columns = self._build_feature_audit_columns(
                        frame=frame,
                        field_name=binding.field,
                        raw_series=raw_series,
                        transformed_series=transformed,
                        source_time_series=pd.to_datetime(frame[base_time_field], errors="coerce"),
                        base_time_field=base_time_field,
                        base_instrument_field=None,
                        join_mode="base_frame",
                        source_id="base_frame",
                        source_uri="",
                        fill_policy=binding.fill_policy,
                        lag_bars=binding.lag_bars,
                        staleness_max_bars=binding.staleness_max_bars,
                    )
                    for column_name, series in audit_columns.items():
                        frame[column_name] = series
            return frame

        use_base_frame_only = str(source_type or "").strip().lower() == "price" and self._bindings_available_in_frame(
            frame, bindings
        )
        source_frame: Optional[pd.DataFrame] = None
        try:
            if not use_base_frame_only:
                source_frame = self._load_source_frame(
                    uri=uri,
                    feature_contract_path=feature_contract_path,
                )
        except (FileNotFoundError, ValueError):
            source_frame = None
        if (source_frame is None or source_frame.empty) and self._bindings_available_in_frame(frame, bindings):
            self.logger.info(
                "feature materializer fallback to base frame columns for uri=%s",
                uri,
            )
            for binding in bindings:
                if binding.field in frame.columns:
                    continue
                raw_series = pd.to_numeric(frame[binding.source_column], errors="coerce")
                transformed = self._apply_transform(raw_series, binding=binding)
                frame[binding.field] = transformed
                audit_columns = self._build_feature_audit_columns(
                    frame=frame,
                    field_name=binding.field,
                    raw_series=raw_series,
                    transformed_series=transformed,
                    source_time_series=pd.to_datetime(frame[base_time_field], errors="coerce"),
                    base_time_field=base_time_field,
                    base_instrument_field=None,
                    join_mode="fallback_from_base_frame",
                    source_id=source_id or "base_frame",
                    source_uri=uri,
                    fill_policy=binding.fill_policy,
                    lag_bars=binding.lag_bars,
                    staleness_max_bars=binding.staleness_max_bars,
                )
                for column_name, series in audit_columns.items():
                    frame[column_name] = series
            return frame
        if source_frame is None or source_frame.empty:
            self.logger.warning("feature materializer could not load source uri=%s", uri)
            return frame

        source_time = source_time_field or self._resolve_time_field(source_frame) or base_time_field
        if source_time not in source_frame.columns:
            self.logger.warning("feature materializer source missing time field uri=%s time_field=%s", uri, source_time)
            return frame

        source_frame = source_frame.copy()
        source_frame[source_time] = self._parse_time_series(
            source_frame[source_time],
            dayfirst=source_dayfirst,
            time_format=source_time_format or None,
        )
        source_frame = source_frame.dropna(subset=[source_time]).sort_values(source_time)

        base_instrument_field, source_instrument = self._resolve_instrument_fields(
            frame=frame,
            source_frame=source_frame,
            feature_contract=feature_contract,
            source_instrument_field=source_instrument_field,
        )

        selected_columns: List[str] = [source_time]
        if source_instrument:
            selected_columns.append(source_instrument)
        rename_map: Dict[str, str] = {}
        post_bindings: Dict[str, _FeatureBinding] = {}
        for binding in bindings:
            if binding.source_column not in source_frame.columns:
                self.logger.warning("feature materializer source column missing uri=%s column=%s", uri, binding.source_column)
                continue
            selected_columns.append(binding.source_column)
            rename_map[binding.source_column] = binding.field
            post_bindings[binding.field] = binding

        if not post_bindings:
            return frame

        source_slice = source_frame.loc[:, list(dict.fromkeys(selected_columns))].rename(columns=rename_map)
        if _AUDIT_TEMP_SOURCE_TIME in source_slice.columns:
            source_slice = source_slice.drop(columns=[_AUDIT_TEMP_SOURCE_TIME])
        source_slice[_AUDIT_TEMP_SOURCE_TIME] = source_frame.loc[source_slice.index, source_time].to_numpy()
        merged = self._merge_source(
            frame=frame,
            source_slice=source_slice,
            base_time_field=base_time_field,
            source_time_field=source_time,
            base_instrument_field=base_instrument_field,
            source_instrument_field=source_instrument,
            join_mode=join_mode,
            feature_contract=feature_contract,
            bindings=post_bindings.values(),
        )

        for field_name, binding in post_bindings.items():
            if field_name not in merged.columns:
                continue
            raw_series = pd.to_numeric(merged[field_name], errors="coerce")
            transformed = self._apply_transform(
                raw_series,
                binding=binding,
            )
            merged[field_name] = transformed
            source_time_series = pd.to_datetime(merged.get(_AUDIT_TEMP_SOURCE_TIME), errors="coerce")
            audit_columns = self._build_feature_audit_columns(
                frame=merged,
                field_name=field_name,
                raw_series=raw_series,
                transformed_series=transformed,
                source_time_series=source_time_series,
                base_time_field=base_time_field,
                base_instrument_field=base_instrument_field,
                join_mode=join_mode,
                source_id=source_id or "external_source",
                source_uri=uri,
                fill_policy=binding.fill_policy,
                lag_bars=binding.lag_bars,
                staleness_max_bars=binding.staleness_max_bars,
            )
            for column_name, series in audit_columns.items():
                merged[column_name] = series
        if _AUDIT_TEMP_SOURCE_TIME in merged.columns:
            merged = merged.drop(columns=[_AUDIT_TEMP_SOURCE_TIME])
        return merged

    def _merge_source(
        self,
        *,
        frame: pd.DataFrame,
        source_slice: pd.DataFrame,
        base_time_field: str,
        source_time_field: str,
        base_instrument_field: Optional[str],
        source_instrument_field: Optional[str],
        join_mode: str,
        feature_contract: Dict[str, Any],
        bindings: Iterable[_FeatureBinding],
    ) -> pd.DataFrame:
        if join_mode in {"left", "inner"}:
            left_on = [base_time_field]
            right_on = [source_time_field]
            if base_instrument_field and source_instrument_field:
                left_on.append(base_instrument_field)
                right_on.append(source_instrument_field)
            try:
                merged = frame.merge(
                    source_slice,
                    how=join_mode,
                    left_on=left_on,
                    right_on=right_on,
                    sort=False,
                    validate="many_to_one",
                )
            except pd.errors.MergeError as exc:
                raise ValueError(
                    f"feature materializer {join_mode} join requires unique source keys per time/instrument"
                ) from exc
            if source_time_field != base_time_field and source_time_field in merged.columns:
                merged = merged.drop(columns=[source_time_field])
            if (
                source_instrument_field
                and base_instrument_field
                and source_instrument_field != base_instrument_field
                and source_instrument_field in merged.columns
            ):
                merged = merged.drop(columns=[source_instrument_field])
            return merged

        tolerance = self._resolve_asof_tolerance(frame[base_time_field], feature_contract, bindings)
        right = source_slice.copy()
        by_field: Optional[str] = None
        if base_instrument_field and source_instrument_field:
            by_field = base_instrument_field
            if source_instrument_field != base_instrument_field and source_instrument_field in right.columns:
                right = right.rename(columns={source_instrument_field: base_instrument_field})

        left = frame.sort_values([base_time_field, by_field] if by_field else [base_time_field])
        right = right.sort_values([source_time_field, by_field] if by_field else [source_time_field])
        merged = pd.merge_asof(
            left,
            right,
            left_on=base_time_field,
            right_on=source_time_field,
            by=by_field,
            direction="backward",
            tolerance=tolerance,
        )
        if source_time_field != base_time_field and source_time_field in merged.columns:
            merged = merged.drop(columns=[source_time_field])
        return merged.sort_values("__materializer_row_id__")

    def _resolve_asof_tolerance(
        self,
        base_time_series: pd.Series,
        feature_contract: Dict[str, Any],
        bindings: Iterable[_FeatureBinding],
    ) -> Optional[pd.Timedelta]:
        alignment = feature_contract.get("alignment_policy", {})
        bars = alignment.get("asof_tolerance_bars")
        if not isinstance(bars, int) or bars <= 0:
            bar_values = [
                binding.staleness_max_bars
                for binding in bindings
                if isinstance(binding.staleness_max_bars, int) and binding.staleness_max_bars >= 0
            ]
            if bar_values:
                bars = max(bar_values)
        if not isinstance(bars, int) or bars <= 0:
            return None
        time_series = pd.to_datetime(base_time_series, errors="coerce").dropna().sort_values()
        diffs = time_series.diff().dropna()
        diffs = diffs[diffs > pd.Timedelta(0)]
        if diffs.empty:
            return None
        return diffs.median() * bars

    def _load_source_frame(self, *, uri: str, feature_contract_path: Optional[str]) -> Optional[pd.DataFrame]:
        resolved = resolve_input_path(
            uri,
            repo_root=self.repo_root,
            config_file_path=feature_contract_path,
        )
        path = resolved.path
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return pd.read_csv(path)
        if suffix in {".parquet", ".pq"}:
            return pd.read_parquet(path)
        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(path)
        raise ValueError(f"Unsupported feature source file type: {path.suffix}")

    @staticmethod
    def _bindings_available_in_frame(frame: pd.DataFrame, bindings: Iterable[_FeatureBinding]) -> bool:
        bindings_list = list(bindings)
        return bool(bindings_list) and all(binding.source_column in frame.columns for binding in bindings_list)

    @staticmethod
    def _resolve_base_time_field(frame: pd.DataFrame, feature_contract: Dict[str, Any]) -> Optional[str]:
        primary = feature_contract.get("primary_key", {})
        primary_time = primary.get("time_field") if isinstance(primary, dict) else None
        if isinstance(primary_time, str) and primary_time in frame.columns:
            return primary_time
        return FeatureContractMaterializerBacktester._resolve_time_field(frame)

    @staticmethod
    def _resolve_time_field(frame: pd.DataFrame) -> Optional[str]:
        for candidate in _TIME_CANDIDATES:
            if candidate in frame.columns:
                return candidate
        return None

    @staticmethod
    def _resolve_instrument_fields(
        *,
        frame: pd.DataFrame,
        source_frame: pd.DataFrame,
        feature_contract: Dict[str, Any],
        source_instrument_field: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        primary = feature_contract.get("primary_key", {})
        base_instrument = primary.get("instrument_field") if isinstance(primary, dict) else None
        if isinstance(base_instrument, str) and base_instrument in frame.columns:
            if source_instrument_field and source_instrument_field in source_frame.columns:
                return base_instrument, source_instrument_field
        detected_base = FeatureContractMaterializerBacktester._detect_instrument_field(frame)
        detected_source = (
            source_instrument_field
            if source_instrument_field and source_instrument_field in source_frame.columns
            else FeatureContractMaterializerBacktester._detect_instrument_field(source_frame)
        )
        base_is_multi = FeatureContractMaterializerBacktester._frame_is_multi_instrument(
            frame,
            detected_base,
        )
        source_is_multi = FeatureContractMaterializerBacktester._frame_is_multi_instrument(
            source_frame,
            detected_source,
        )
        if base_is_multi or source_is_multi:
            raise ValueError(
                "feature materializer requires explicit primary_key.instrument_field and "
                "source.instrument_field for multi-instrument joins"
            )
        return None, None

    @staticmethod
    def _detect_instrument_field(frame: pd.DataFrame) -> Optional[str]:
        for candidate in _INSTRUMENT_CANDIDATES:
            if candidate in frame.columns:
                return candidate
        return None

    @staticmethod
    def _frame_is_multi_instrument(frame: pd.DataFrame, instrument_field: Optional[str]) -> bool:
        if not instrument_field or instrument_field not in frame.columns:
            return False
        series = frame[instrument_field].dropna()
        if series.empty:
            return False
        return series.astype(str).nunique() > 1

    @staticmethod
    def _group_feature_bindings(
        features: List[Dict[str, Any]]
    ) -> List[Tuple[Tuple[str, str, str, str, str, Optional[bool], str], List[_FeatureBinding]]]:
        grouped: Dict[Tuple[str, str, str, str, str, Optional[bool], str], List[_FeatureBinding]] = {}
        for item in features:
            if not isinstance(item, dict):
                continue
            field = item.get("field")
            source = item.get("source", {})
            if not isinstance(field, str) or not isinstance(source, dict):
                continue
            source_column = source.get("column")
            if not isinstance(source_column, str) or not source_column:
                continue
            key = (
                str(source.get("uri", "") or "").strip(),
                str(source.get("source_id", "") or "").strip(),
                str(source.get("type", "") or "").strip().lower(),
                str(source.get("time_field", "") or "").strip(),
                str(source.get("instrument_field", "") or "").strip(),
                source.get("dayfirst") if isinstance(source.get("dayfirst"), bool) else None,
                str(source.get("time_format", "") or "").strip(),
            )
            grouped.setdefault(key, []).append(
                _FeatureBinding(
                    field=field,
                    source_column=source_column,
                    source_type=str(source.get("type", "") or "").strip().lower(),
                    fill_policy=str(item.get("fill_policy", "none") or "none").strip().lower(),
                    lag_bars=int(item.get("lag_bars", 0) or 0),
                    staleness_max_bars=(
                        int(item.get("staleness_max_bars"))
                        if isinstance(item.get("staleness_max_bars"), int)
                        else None
                    ),
                )
            )
        return list(grouped.items())

    @staticmethod
    def _parse_time_series(
        series: pd.Series,
        *,
        dayfirst: Optional[bool] = None,
        time_format: Optional[str] = None,
    ) -> pd.Series:
        kwargs: Dict[str, Any] = {"errors": "coerce"}
        if isinstance(dayfirst, bool):
            kwargs["dayfirst"] = dayfirst
        if isinstance(time_format, str) and time_format.strip():
            kwargs["format"] = time_format.strip()
        return pd.to_datetime(series, **kwargs)

    @staticmethod
    def _apply_transform(series: pd.Series, *, binding: _FeatureBinding) -> pd.Series:
        if binding.fill_policy == "bfill" and not binding.field.startswith("label."):
            raise ValueError(
                f"feature field '{binding.field}' cannot use bfill because it can leak future values"
            )
        out = series
        if binding.fill_policy == "ffill":
            if isinstance(binding.staleness_max_bars, int) and binding.staleness_max_bars >= 0:
                out = out.ffill(limit=binding.staleness_max_bars)
            else:
                out = out.ffill()
        elif binding.fill_policy == "bfill":
            if isinstance(binding.staleness_max_bars, int) and binding.staleness_max_bars >= 0:
                out = out.bfill(limit=binding.staleness_max_bars)
            else:
                out = out.bfill()
        elif binding.fill_policy == "zero":
            out = out.fillna(0.0)

        if binding.lag_bars > 0:
            out = out.shift(binding.lag_bars)
        return out

    @staticmethod
    def _audit_suffix(field_name: str, suffix: str) -> str:
        safe = "".join(ch if ch.isalnum() else "_" for ch in str(field_name).strip().lower())
        while "__" in safe:
            safe = safe.replace("__", "_")
        safe = safe.strip("_") or "field"
        return f"__audit__{safe}__{suffix}"

    @staticmethod
    def _build_feature_audit_columns(
        *,
        frame: pd.DataFrame,
        field_name: str,
        raw_series: pd.Series,
        transformed_series: pd.Series,
        source_time_series: pd.Series,
        base_time_field: str,
        base_instrument_field: Optional[str],
        join_mode: str,
        source_id: str,
        source_uri: str,
        fill_policy: str,
        lag_bars: int,
        staleness_max_bars: Optional[int],
    ) -> Dict[str, pd.Series]:
        base_time_series = pd.to_datetime(frame[base_time_field], errors="coerce")
        source_time_series = pd.to_datetime(source_time_series, errors="coerce")
        raw_value_origin = FeatureContractMaterializerBacktester._build_raw_value_origin_series(
            join_mode=join_mode,
            raw_series=raw_series,
            raw_source_time_series=source_time_series,
            base_time_series=base_time_series,
        )
        transformed_source_time = FeatureContractMaterializerBacktester._transform_source_time_series(
            source_time_series=source_time_series,
            fill_policy=fill_policy,
            lag_bars=lag_bars,
            staleness_max_bars=staleness_max_bars,
        )
        was_filled = raw_series.isna() & transformed_series.notna()
        value_origin = FeatureContractMaterializerBacktester._build_value_origin_series(
            raw_value_origin=raw_value_origin,
            fill_policy=fill_policy,
            raw_series=raw_series,
            transformed_series=transformed_series,
            lag_bars=lag_bars,
            staleness_max_bars=staleness_max_bars,
        )

        age_bars = FeatureContractMaterializerBacktester._compute_age_bars(
            base_time_series=base_time_series,
            source_time_series=transformed_source_time,
            instrument_series=(frame[base_instrument_field] if base_instrument_field and base_instrument_field in frame.columns else None),
        )
        stale_flag = pd.Series(False, index=frame.index)
        if isinstance(staleness_max_bars, int) and staleness_max_bars >= 0:
            stale_flag = age_bars.gt(float(staleness_max_bars)).fillna(False)

        return {
            FeatureContractMaterializerBacktester._audit_suffix(field_name, "source_id"): pd.Series(str(source_id or ""), index=frame.index),
            FeatureContractMaterializerBacktester._audit_suffix(field_name, "source_uri"): pd.Series(str(source_uri or ""), index=frame.index),
            FeatureContractMaterializerBacktester._audit_suffix(field_name, "source_time"): transformed_source_time,
            FeatureContractMaterializerBacktester._audit_suffix(field_name, "join_mode"): pd.Series(str(join_mode or ""), index=frame.index),
            FeatureContractMaterializerBacktester._audit_suffix(field_name, "value_origin"): value_origin,
            FeatureContractMaterializerBacktester._audit_suffix(field_name, "age_bars"): age_bars,
            FeatureContractMaterializerBacktester._audit_suffix(field_name, "was_filled"): was_filled.astype(bool),
            FeatureContractMaterializerBacktester._audit_suffix(field_name, "lag_applied"): pd.Series(int(lag_bars or 0), index=frame.index),
            FeatureContractMaterializerBacktester._audit_suffix(field_name, "stale_flag"): stale_flag.astype(bool),
        }

    @staticmethod
    def _transform_source_time_series(
        *,
        source_time_series: pd.Series,
        fill_policy: str,
        lag_bars: int,
        staleness_max_bars: Optional[int],
    ) -> pd.Series:
        out = pd.to_datetime(source_time_series, errors="coerce").copy()
        limit = staleness_max_bars if isinstance(staleness_max_bars, int) and staleness_max_bars >= 0 else None
        if fill_policy == "ffill":
            out = out.ffill(limit=limit)
        elif fill_policy == "bfill":
            out = out.bfill(limit=limit)
        elif fill_policy == "zero":
            out = out.where(out.notna(), pd.NaT)
        if lag_bars > 0:
            out = out.shift(lag_bars)
        return pd.to_datetime(out, errors="coerce")

    @staticmethod
    def _build_value_origin_series(
        *,
        raw_value_origin: pd.Series,
        fill_policy: str,
        raw_series: pd.Series,
        transformed_series: pd.Series,
        lag_bars: int,
        staleness_max_bars: Optional[int],
    ) -> pd.Series:
        value_origin = raw_value_origin.copy()
        limit = staleness_max_bars if isinstance(staleness_max_bars, int) and staleness_max_bars >= 0 else None
        fill_mask = value_origin.isna() & transformed_series.notna()
        if fill_policy == "ffill":
            value_origin = value_origin.ffill(limit=limit)
            value_origin.loc[fill_mask & value_origin.notna()] = "ffill"
        elif fill_policy == "bfill":
            value_origin = value_origin.bfill(limit=limit)
            value_origin.loc[fill_mask & value_origin.notna()] = "bfill"
        elif fill_policy == "zero":
            value_origin.loc[fill_mask] = "zero_fill"

        if lag_bars > 0:
            value_origin = value_origin.shift(lag_bars)

        value_origin = value_origin.where(transformed_series.notna(), pd.NA)
        return value_origin.fillna("missing").astype("object")

    @staticmethod
    def _build_raw_value_origin_series(
        *,
        join_mode: str,
        raw_series: pd.Series,
        raw_source_time_series: pd.Series,
        base_time_series: pd.Series,
    ) -> pd.Series:
        value_origin = pd.Series(pd.NA, index=raw_series.index, dtype="object")
        present = raw_series.notna()
        if join_mode in {"base_frame", "fallback_from_base_frame"}:
            value_origin.loc[present] = join_mode
            return value_origin

        exact_match = raw_source_time_series.notna() & (raw_source_time_series == base_time_series)
        matched = raw_source_time_series.notna()
        value_origin.loc[present & matched & exact_match] = "exact"
        value_origin.loc[present & matched & ~exact_match] = "asof"
        return value_origin

    @staticmethod
    def _compute_age_bars(
        *,
        base_time_series: pd.Series,
        source_time_series: pd.Series,
        instrument_series: Optional[pd.Series],
    ) -> pd.Series:
        result = pd.Series(pd.NA, index=base_time_series.index, dtype="Float64")
        if instrument_series is None:
            groups = [(None, base_time_series.index)]
        else:
            groups = instrument_series.groupby(instrument_series.fillna("__nan__")).groups.items()
        for _, group_index in groups:
            if len(group_index) == 0:
                continue
            base_group = pd.to_datetime(base_time_series.loc[group_index], errors="coerce")
            source_group = pd.to_datetime(source_time_series.loc[group_index], errors="coerce")
            base_values = base_group.to_numpy()
            source_values = source_group.to_numpy()
            valid_mask = ~pd.isna(source_values)
            if not valid_mask.any():
                continue
            current_positions = pd.Series(range(len(base_values)), index=base_group.index).to_numpy(dtype="int64")
            source_positions = pd.Index(base_values).searchsorted(source_values, side="right") - 1
            age = current_positions - source_positions
            age = pd.Series(age, index=base_group.index, dtype="Float64")
            age.loc[(source_positions < 0) | ~valid_mask] = pd.NA
            result.loc[group_index] = age
        return result
