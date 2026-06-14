"""Score-only factor pipeline runtime.

FactorHandler owns factor construction, preprocessing, point-in-time audit,
composite score generation, and cache/report metadata.  It intentionally does
not choose assets or simulate a portfolio; backtester consumes its output
frames through the existing selection/allocation contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import numpy as np
import pandas as pd


class FactorHandlerError(ValueError):
    """Raised when a factor pipeline cannot be materialized safely."""


@dataclass
class FactorHandlerResult:
    factor_frame: Dict[str, pd.DataFrame]
    clean_factor_frame: Dict[str, pd.DataFrame]
    factor_score_frame: Dict[str, pd.DataFrame]
    factor_quality_report: Dict[str, Any]
    point_in_time_audit: Dict[str, Any]
    cache_report: Dict[str, Any]

    def feature_frames(self) -> Dict[str, pd.DataFrame]:
        """Return all frames that can be referenced by backtester selection."""

        frames: Dict[str, pd.DataFrame] = {}
        frames.update(self.factor_frame)
        frames.update(self.clean_factor_frame)
        frames.update(self.factor_score_frame)
        return frames


class FactorHandler:
    """Materialize a factor_pipeline.v1 config into score frames."""

    def __init__(
        self,
        frames: Mapping[str, pd.DataFrame],
        config: Mapping[str, Any],
        *,
        cache_dir: Optional[Path | str] = None,
    ) -> None:
        self.frames = {str(key).lower(): self._normalize_frame(value) for key, value in frames.items()}
        self.config = dict(config or {})
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.cache_report: Dict[str, Any] = {
            "enabled": False,
            "key": self.cache_key(self.frames, self.config),
            "hits": 0,
            "writes": 0,
            "storage": None,
        }

    def run(self) -> FactorHandlerResult:
        pipeline = self.config
        self._validate_pipeline(pipeline)
        self._configure_cache(pipeline)
        cached = self._load_cached_result()
        if cached is not None:
            return cached

        factor_frame = self._construct_factors(pipeline.get("construction", []))
        point_in_time_audit = self._point_in_time_audit(pipeline)
        clean_factor_frame = self._preprocess_factors(
            factor_frame,
            pipeline.get("preprocessing", []),
        )
        factor_score_frame = self._compose_scores(clean_factor_frame, pipeline.get("composite", {}))
        quality_report = self._quality_report(pipeline, factor_frame, clean_factor_frame, factor_score_frame)

        result = FactorHandlerResult(
            factor_frame=factor_frame,
            clean_factor_frame=clean_factor_frame,
            factor_score_frame=factor_score_frame,
            factor_quality_report=quality_report,
            point_in_time_audit=point_in_time_audit,
            cache_report=dict(self.cache_report),
        )
        self._write_cached_result(result)
        result.cache_report = dict(self.cache_report)
        return result

    @staticmethod
    def cache_key(frames: Mapping[str, pd.DataFrame], config: Mapping[str, Any]) -> str:
        frame_fingerprint: Dict[str, Any] = {}
        for key, frame in sorted(frames.items()):
            if not isinstance(frame, pd.DataFrame):
                continue
            columns = [str(col) for col in frame.columns]
            index = pd.to_datetime(frame.index, errors="coerce")
            frame_fingerprint[str(key).lower()] = {
                "rows": int(len(frame)),
                "columns": columns,
                "start": str(index.min()) if len(index) else "",
                "end": str(index.max()) if len(index) else "",
            }
        payload = {
            "schema_version": "factor_cache_key.v1",
            "frames": frame_fingerprint,
            "config": config,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _validate_pipeline(self, pipeline: Mapping[str, Any]) -> None:
        if not isinstance(pipeline, Mapping) or not pipeline:
            raise FactorHandlerError("factor_pipeline config is required")
        if not isinstance(pipeline.get("construction", []), list):
            raise FactorHandlerError("factor_pipeline.construction must be a list")
        if not isinstance(pipeline.get("preprocessing", []), list):
            raise FactorHandlerError("factor_pipeline.preprocessing must be a list")

    def _configure_cache(self, pipeline: Mapping[str, Any]) -> None:
        cache_cfg = pipeline.get("cache", {}) if isinstance(pipeline.get("cache"), Mapping) else {}
        enabled = bool(cache_cfg.get("enabled", False))
        storage = str(cache_cfg.get("storage") or "local_parquet").strip().lower()
        self.cache_report["enabled"] = bool(enabled and self.cache_dir is not None)
        self.cache_report["storage"] = storage if enabled else None
        if self.cache_report["enabled"] and self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _construct_factors(self, specs: Iterable[Mapping[str, Any]]) -> Dict[str, pd.DataFrame]:
        factors: Dict[str, pd.DataFrame] = {}
        for spec in specs or []:
            if not isinstance(spec, Mapping):
                continue
            name = str(spec.get("name") or "").strip()
            if not name:
                raise FactorHandlerError("factor construction item requires name")
            factors[name] = self._construct_one(spec)
        return factors

    def _construct_one(self, spec: Mapping[str, Any]) -> pd.DataFrame:
        op = str(spec.get("op") or "").strip().lower()
        inputs = spec.get("inputs", {}) if isinstance(spec.get("inputs"), Mapping) else {}
        if op in {"identity", "field", "source"}:
            return self._input_frame(inputs.get("field") or inputs.get("source") or spec.get("source") or "close")
        if op in {"factor.price_momentum", "indicator.momentum", "momentum"}:
            close = self._input_frame(inputs.get("close") or spec.get("source") or "close")
            lookback = self._positive_int(inputs.get("lookback", spec.get("period")), default=126)
            skip_recent = self._positive_int(inputs.get("skip_recent", spec.get("skip_recent")), default=0)
            recent = close.shift(skip_recent)
            base = close.shift(skip_recent + lookback)
            return recent / base.replace(0.0, np.nan) - 1.0
        if op in {"factor.realized_volatility", "indicator.volatility", "volatility"}:
            close = self._input_frame(inputs.get("close") or spec.get("source") or "close")
            lookback = self._positive_int(inputs.get("lookback", spec.get("period")), default=126)
            vol = close.pct_change().rolling(lookback, min_periods=lookback).std()
            return vol * np.sqrt(252.0)
        if op in {"factor.book_to_market", "book_to_market"}:
            book_value = self._input_frame(inputs.get("book_value") or "book_value")
            market_cap = self._input_frame(inputs.get("market_cap") or "market_cap")
            return book_value / market_cap.replace(0.0, np.nan)
        if op in {"factor.return_on_equity", "return_on_equity", "roe"}:
            net_income = self._input_frame(inputs.get("net_income") or "net_income")
            book_value = self._input_frame(inputs.get("book_value") or "book_value")
            return net_income / book_value.replace(0.0, np.nan)
        raise FactorHandlerError(f"Unsupported factor construction op: {op}")

    def _preprocess_factors(
        self,
        factors: Dict[str, pd.DataFrame],
        steps: Iterable[Mapping[str, Any]],
    ) -> Dict[str, pd.DataFrame]:
        clean = {name: frame.copy() for name, frame in factors.items()}
        for step in steps or []:
            if not isinstance(step, Mapping):
                continue
            op = str(step.get("op") or "").strip().lower()
            fields = [str(item) for item in step.get("fields", []) or [] if str(item)]
            target_names = fields if fields else list(clean.keys())
            for name in target_names:
                if name not in clean:
                    continue
                if op == "drop_unavailable":
                    clean[name] = clean[name].where(clean[name].notna())
                elif op == "fill_missing":
                    clean[name] = self._fill_missing(clean[name], step)
                elif op == "winsorize":
                    clean[name] = self._winsorize(clean[name], step)
                elif op == "standardize":
                    clean[name] = self._standardize(clean[name])
                elif op == "neutralize":
                    clean[name] = self._neutralize(clean[name], step)
                elif op == "rank":
                    clean[name] = clean[name].rank(axis=1, pct=True)
                elif op == "lag_audit":
                    continue
                else:
                    raise FactorHandlerError(f"Unsupported factor preprocessing op: {op}")
        return clean

    def _compose_scores(
        self,
        clean_factors: Dict[str, pd.DataFrame],
        composite: Mapping[str, Any],
    ) -> Dict[str, pd.DataFrame]:
        if not clean_factors:
            return {}
        method = str((composite or {}).get("method") or "equal_weight").strip().lower()
        inputs = [str(item) for item in (composite or {}).get("inputs", []) or []]
        names = inputs if inputs else list(clean_factors.keys())
        available = [name for name in names if name in clean_factors]
        if not available:
            raise FactorHandlerError("factor composite has no available input factors")
        output_name = str((composite or {}).get("output") or "composite_factor_score").strip()
        if method in {"equal_weight", "none"}:
            weights = {name: 1.0 / float(len(available)) for name in available}
        elif method == "manual_weight":
            raw = (composite or {}).get("weights", {})
            if not isinstance(raw, Mapping):
                raise FactorHandlerError("manual_weight composite requires weights object")
            weights = {name: float(raw.get(name, 0.0)) for name in available}
            total = sum(abs(value) for value in weights.values())
            if total <= 0.0:
                raise FactorHandlerError("manual_weight composite weights sum to zero")
            weights = {name: value / total for name, value in weights.items()}
        elif method in {"ic_weight", "regression_weight", "ranker_model"}:
            raise FactorHandlerError(f"Composite method reserved but not implemented: {method}")
        else:
            raise FactorHandlerError(f"Unsupported factor composite method: {method}")
        score: Optional[pd.DataFrame] = None
        for name, weight in weights.items():
            weighted = clean_factors[name] * float(weight)
            score = weighted if score is None else score.add(weighted, fill_value=0.0)
        return {output_name: score if score is not None else pd.DataFrame()}

    def _point_in_time_audit(self, pipeline: Mapping[str, Any]) -> Dict[str, Any]:
        pit = pipeline.get("point_in_time", {}) if isinstance(pipeline.get("point_in_time"), Mapping) else {}
        fail_on_lookahead = bool(pit.get("fail_on_lookahead", False))
        known_at_fields = set()
        if pit.get("known_at_field"):
            known_at_fields.add(str(pit.get("known_at_field")))
        for spec in pipeline.get("construction", []) or []:
            if isinstance(spec, Mapping) and spec.get("known_at"):
                known_at_fields.add(str(spec.get("known_at")).split(".")[-1])
        violations: List[Dict[str, Any]] = []
        for field in sorted(known_at_fields):
            key = field.lower()
            if key not in self.frames:
                continue
            known_at = self.frames[key].reindex(columns=self._symbols())
            for current_date, row in known_at.iterrows():
                row_dates = pd.to_datetime(row, errors="coerce")
                bad = row_dates[row_dates > pd.Timestamp(current_date)]
                for asset, known_date in bad.items():
                    violations.append(
                        {
                            "date": str(pd.Timestamp(current_date).date()),
                            "asset": str(asset),
                            "known_at": str(pd.Timestamp(known_date).date()),
                            "field": field,
                        }
                    )
        if violations and fail_on_lookahead:
            first = violations[0]
            raise FactorHandlerError(
                "point-in-time lookahead detected: "
                f"{first['asset']} on {first['date']} known at {first['known_at']}"
            )
        return {
            "schema_version": "factor_point_in_time_audit.v1",
            "status": "failed" if violations else "passed",
            "fail_on_lookahead": fail_on_lookahead,
            "violation_count": len(violations),
            "violations": violations[:100],
        }

    def _quality_report(
        self,
        pipeline: Mapping[str, Any],
        factor_frame: Dict[str, pd.DataFrame],
        clean_factor_frame: Dict[str, pd.DataFrame],
        factor_score_frame: Dict[str, pd.DataFrame],
    ) -> Dict[str, Any]:
        requirements = pipeline.get("data_requirements", {})
        missing_fields: List[str] = []
        if isinstance(requirements, Mapping):
            for bucket in [
                "price_fields",
                "fundamental_fields",
                "classification_fields",
                "alternative_fields",
            ]:
                for field in requirements.get(bucket, []) or []:
                    if str(field).lower() not in self.frames:
                        missing_fields.append(str(field))
        coverage: Dict[str, Any] = {}
        for group_name, frames in [
            ("factor_frame", factor_frame),
            ("clean_factor_frame", clean_factor_frame),
            ("factor_score_frame", factor_score_frame),
        ]:
            coverage[group_name] = {
                name: {
                    "rows": int(len(frame)),
                    "assets": int(len(frame.columns)),
                    "missing_ratio": float(frame.isna().mean().mean()) if len(frame) and len(frame.columns) else 1.0,
                }
                for name, frame in frames.items()
            }
        return {
            "schema_version": "factor_quality_report.v1",
            "status": "valid" if not missing_fields else "missing_optional_or_required_data",
            "missing_fields": missing_fields,
            "coverage": coverage,
        }

    def _load_cached_result(self) -> Optional[FactorHandlerResult]:
        if not self.cache_report.get("enabled"):
            return None
        cache_root = self._cache_root()
        manifest = cache_root / "manifest.json"
        if not manifest.exists():
            return None
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            result = FactorHandlerResult(
                factor_frame=self._read_frame_group(cache_root, payload.get("factor_frame", {})),
                clean_factor_frame=self._read_frame_group(cache_root, payload.get("clean_factor_frame", {})),
                factor_score_frame=self._read_frame_group(cache_root, payload.get("factor_score_frame", {})),
                factor_quality_report=payload.get("factor_quality_report", {}),
                point_in_time_audit=payload.get("point_in_time_audit", {}),
                cache_report={**self.cache_report, "hits": 1},
            )
            self.cache_report["hits"] = 1
            return result
        except Exception:
            return None

    def _write_cached_result(self, result: FactorHandlerResult) -> None:
        if not self.cache_report.get("enabled"):
            return
        cache_root = self._cache_root()
        cache_root.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "factor_cache_manifest.v1",
            "factor_frame": self._write_frame_group(cache_root, "factor", result.factor_frame),
            "clean_factor_frame": self._write_frame_group(cache_root, "clean", result.clean_factor_frame),
            "factor_score_frame": self._write_frame_group(cache_root, "score", result.factor_score_frame),
            "factor_quality_report": result.factor_quality_report,
            "point_in_time_audit": result.point_in_time_audit,
        }
        (cache_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self.cache_report["writes"] = 1

    def _write_frame_group(self, cache_root: Path, prefix: str, frames: Dict[str, pd.DataFrame]) -> Dict[str, str]:
        refs: Dict[str, str] = {}
        for name, frame in frames.items():
            filename = f"{prefix}_{self._safe_name(name)}.parquet"
            frame.to_parquet(cache_root / filename)
            refs[name] = filename
        return refs

    def _read_frame_group(self, cache_root: Path, refs: Mapping[str, str]) -> Dict[str, pd.DataFrame]:
        frames: Dict[str, pd.DataFrame] = {}
        for name, filename in refs.items():
            frames[str(name)] = pd.read_parquet(cache_root / str(filename))
        return frames

    def _cache_root(self) -> Path:
        if self.cache_dir is None:
            raise FactorHandlerError("cache_dir is required for cache operations")
        return self.cache_dir / str(self.cache_report["key"])

    def _input_frame(self, name: Any) -> pd.DataFrame:
        key = str(name or "").split(".")[-1].strip().lower()
        if key not in self.frames:
            raise FactorHandlerError(f"Missing factor input frame: {key}")
        return self.frames[key].reindex(columns=self._symbols())

    def _symbols(self) -> List[str]:
        if "close" in self.frames:
            return [str(col) for col in self.frames["close"].columns]
        first = next(iter(self.frames.values()))
        return [str(col) for col in first.columns]

    def _fill_missing(self, frame: pd.DataFrame, step: Mapping[str, Any]) -> pd.DataFrame:
        method = str(step.get("method") or "cross_section_median").strip().lower()
        if method in {"zero", "constant"}:
            return frame.fillna(float(step.get("value", 0.0)))
        if method in {"ffill", "forward_fill"}:
            return frame.ffill()
        med = frame.median(axis=1)
        return frame.T.fillna(med).T

    def _winsorize(self, frame: pd.DataFrame, step: Mapping[str, Any]) -> pd.DataFrame:
        limits = step.get("limits", [0.01, 0.99])
        lower_q = float(limits[0]) if isinstance(limits, list) and limits else 0.01
        upper_q = float(limits[1]) if isinstance(limits, list) and len(limits) > 1 else 0.99
        lower = frame.quantile(lower_q, axis=1)
        upper = frame.quantile(upper_q, axis=1)
        return frame.clip(lower=lower, upper=upper, axis=0)

    def _standardize(self, frame: pd.DataFrame) -> pd.DataFrame:
        mean = frame.mean(axis=1)
        std = frame.std(axis=1).replace(0.0, np.nan)
        return frame.sub(mean, axis=0).div(std, axis=0)

    def _neutralize(self, frame: pd.DataFrame, step: Mapping[str, Any]) -> pd.DataFrame:
        group_by = step.get("group_by")
        groups = group_by if isinstance(group_by, list) else [group_by] if group_by else []
        group_field = str(groups[0]).split(".")[-1].lower() if groups else ""
        if not group_field or group_field not in self.frames:
            return frame.copy()
        group_frame = self.frames[group_field].reindex(index=frame.index, columns=frame.columns)
        out = frame.copy()
        for current_date in frame.index:
            row = frame.loc[current_date]
            group_row = group_frame.loc[current_date]
            adjusted = row.copy()
            for group_value in pd.Series(group_row).dropna().unique().tolist():
                members = group_row[group_row == group_value].index
                adjusted.loc[members] = row.loc[members] - row.loc[members].mean()
            out.loc[current_date] = adjusted
        return out

    @staticmethod
    def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
        out = out.sort_index()
        out.columns = [str(col).upper() for col in out.columns]
        return out

    @staticmethod
    def _positive_int(value: Any, *, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _safe_name(value: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))
        return safe or "factor"
