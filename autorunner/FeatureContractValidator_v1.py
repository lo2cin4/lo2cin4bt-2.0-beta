"""Feature contract validator for contract-safe multi-source expansion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class FeatureValidationResult:
    valid: bool
    errors: List[str]
    warnings: List[str]
    summary: Dict[str, Any]


class FeatureContractValidatorV1:
    """Validate feature.contract.v1 and summarize multi-source alignment metadata."""

    VALID_JOIN_MODES = {"inner", "left", "asof"}
    VALID_SIGNAL_TIMES = {"bar_open", "bar_close"}
    VALID_TRADE_TIMES = {"same_bar", "next_bar"}
    VALID_CALENDAR_POLICIES = {"primary", "strict", "union"}
    VALID_OBSERVED_AT = {"bar_open", "bar_close", "after_bar_close"}
    VALID_USABLE_FROM = {"same_bar_open", "same_bar_close", "next_bar_open", "next_bar_close"}
    VALID_REVISION_POLICIES = {"point_in_time", "declared_static", "revised_history"}

    def validate(self, feature_contract: Optional[Dict[str, Any]]) -> FeatureValidationResult:
        if not isinstance(feature_contract, dict):
            return FeatureValidationResult(
                valid=False,
                errors=["feature contract must be a JSON object"],
                warnings=[],
                summary={},
            )

        errors: List[str] = []
        warnings: List[str] = []
        self._validate_top_level(feature_contract, errors)
        features = feature_contract.get("features", [])
        feature_items = features if isinstance(features, list) else []
        feature_fields, source_uri_by_id = self._validate_features(feature_items, errors, warnings)
        alignment = self._validate_alignment(feature_contract.get("alignment_policy"), errors)
        time_semantics = self._validate_time_semantics(
            feature_contract.get("time_semantics"), errors, warnings
        )

        source_ids = sorted(
            {
                str(item.get("source", {}).get("source_id")).strip()
                for item in feature_items
                if isinstance(item, dict)
                and isinstance(item.get("source"), dict)
                and str(item.get("source", {}).get("source_id", "")).strip()
            }
        )
        source_uris = sorted(
            {
                str(item.get("source", {}).get("uri")).strip()
                for item in feature_items
                if isinstance(item, dict)
                and isinstance(item.get("source"), dict)
                and str(item.get("source", {}).get("uri", "")).strip()
            }
        )
        calendars = sorted(
            {
                str(item.get("calendar")).strip()
                for item in feature_items
                if isinstance(item, dict) and str(item.get("calendar", "")).strip()
            }
        )
        multi_source = len(source_uris) > 1 or len(source_ids) > 1

        if multi_source:
            missing_source_id = [
                str(item.get("field"))
                for item in feature_items
                if isinstance(item, dict)
                and isinstance(item.get("source"), dict)
                and not str(item.get("source", {}).get("source_id", "")).strip()
            ]
            if missing_source_id:
                warnings.append(
                    "multi-source feature contract detected without explicit source.source_id on: "
                    + ", ".join(sorted(missing_source_id))
                )
            if alignment.get("join_mode") == "inner":
                warnings.append(
                    "multi-source contract uses join_mode=inner; verify row loss is acceptable"
                )
            if not calendars:
                warnings.append(
                    "multi-source contract has no explicit feature calendars; calendar drift may be harder to audit"
                )

        if (
            time_semantics.get("signal_observation_time") == "bar_close"
            and time_semantics.get("trade_earliest_time") == "same_bar"
        ):
            errors.append(
                "time_semantics cannot use bar_close observation with same_bar trading"
            )

        summary = {
            "dataset_id": feature_contract.get("dataset_id"),
            "feature_count": len(feature_fields),
            "fields": sorted(feature_fields),
            "source_count": len(source_uris) if source_uris else len(source_uri_by_id),
            "source_ids": source_ids,
            "source_uris": source_uris,
            "multi_source": multi_source,
            "join_mode": alignment.get("join_mode"),
            "calendar_policy": alignment.get("calendar_policy"),
            "time_semantics": time_semantics,
            "calendars": calendars,
        }
        return FeatureValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
            summary=summary,
        )

    @staticmethod
    def _validate_top_level(feature_contract: Dict[str, Any], errors: List[str]) -> None:
        required = {"schema_version", "dataset_id", "features", "alignment_policy"}
        missing = sorted(required - set(feature_contract.keys()))
        if missing:
            errors.append(f"feature contract missing required keys: {', '.join(missing)}")
        if feature_contract.get("schema_version") != "1.0":
            errors.append("feature contract schema_version must be '1.0'")
        if not isinstance(feature_contract.get("dataset_id"), str) or not str(
            feature_contract.get("dataset_id", "")
        ).strip():
            errors.append("feature contract dataset_id must be a non-empty string")

    def _validate_features(
        self,
        features: List[Any],
        errors: List[str],
        warnings: List[str],
    ) -> Tuple[Set[str], Dict[str, str]]:
        if not isinstance(features, list) or not features:
            errors.append("feature contract features must be a non-empty list")
            return set(), {}

        fields: Set[str] = set()
        source_uri_by_id: Dict[str, str] = {}
        for idx, item in enumerate(features):
            path = f"features[{idx}]"
            if not isinstance(item, dict):
                errors.append(f"{path} must be an object")
                continue
            field = item.get("field")
            if not isinstance(field, str) or not field.strip():
                errors.append(f"{path}.field must be a non-empty string")
            elif field in fields:
                errors.append(f"duplicate feature field '{field}'")
            else:
                fields.add(field)

            source = item.get("source")
            if not isinstance(source, dict):
                errors.append(f"{path}.source must be an object")
                continue

            uri = str(source.get("uri", "")).strip()
            if not uri:
                errors.append(f"{path}.source.uri must be a non-empty string")
            source_id = str(source.get("source_id", "")).strip()
            if source_id:
                previous_uri = source_uri_by_id.get(source_id)
                if previous_uri is None:
                    source_uri_by_id[source_id] = uri
                elif previous_uri != uri:
                    errors.append(
                        f"{path}.source.source_id '{source_id}' maps to multiple uris"
                    )

            dayfirst = source.get("dayfirst")
            if dayfirst is not None and not isinstance(dayfirst, bool):
                errors.append(f"{path}.source.dayfirst must be boolean when provided")

            time_format = source.get("time_format")
            if time_format is not None and (
                not isinstance(time_format, str) or not time_format.strip()
            ):
                errors.append(f"{path}.source.time_format must be a non-empty string when provided")

            staleness = item.get("staleness_max_bars")
            if staleness is not None and (not isinstance(staleness, int) or staleness < 0):
                errors.append(f"{path}.staleness_max_bars must be integer >= 0")

            lag_bars = item.get("lag_bars")
            if not isinstance(lag_bars, int) or lag_bars < 0:
                errors.append(f"{path}.lag_bars must be integer >= 0")

            self._validate_data_availability(item.get("data_availability"), path, errors, warnings)

            if not str(item.get("calendar", "")).strip():
                warnings.append(f"{path}.calendar is not declared")

        return fields, source_uri_by_id

    def _validate_data_availability(
        self,
        availability: Any,
        path: str,
        errors: List[str],
        warnings: List[str],
    ) -> None:
        if not isinstance(availability, dict):
            errors.append(f"{path}.data_availability is required")
            return
        observed_at = availability.get("observed_at")
        usable_from = availability.get("usable_from")
        point_in_time = availability.get("point_in_time")
        revision_policy = availability.get("revision_policy")

        if observed_at not in self.VALID_OBSERVED_AT:
            errors.append(f"{path}.data_availability.observed_at must be bar_open/bar_close/after_bar_close")
        if usable_from not in self.VALID_USABLE_FROM:
            errors.append(
                f"{path}.data_availability.usable_from must be same_bar_open/same_bar_close/next_bar_open/next_bar_close"
            )
        if not isinstance(point_in_time, bool):
            errors.append(f"{path}.data_availability.point_in_time must be boolean")
        if revision_policy not in self.VALID_REVISION_POLICIES:
            errors.append(
                f"{path}.data_availability.revision_policy must be point_in_time/declared_static/revised_history"
            )

        if observed_at == "bar_close" and usable_from == "same_bar_open":
            errors.append(f"{path}.data_availability cannot use bar_close data at same_bar_open")
        if observed_at == "after_bar_close" and usable_from in {"same_bar_open", "same_bar_close"}:
            errors.append(f"{path}.data_availability cannot use after_bar_close data on the same bar")
        if revision_policy == "revised_history" and point_in_time is not True:
            warnings.append(
                f"{path}.data_availability uses revised_history; QuantReview must decide whether this is acceptable"
            )
        if revision_policy == "declared_static" and not str(availability.get("review_note", "")).strip():
            warnings.append(
                f"{path}.data_availability.declared_static should include review_note explaining why the data is not revised"
            )

    def _validate_alignment(self, alignment: Any, errors: List[str]) -> Dict[str, Any]:
        if not isinstance(alignment, dict):
            errors.append("alignment_policy must be an object")
            return {}
        join_mode = alignment.get("join_mode")
        if join_mode not in self.VALID_JOIN_MODES:
            errors.append("alignment_policy.join_mode must be inner/left/asof")

        calendar_policy = alignment.get("calendar_policy", "primary")
        if calendar_policy not in self.VALID_CALENDAR_POLICIES:
            errors.append("alignment_policy.calendar_policy must be primary/strict/union")

        asof_tolerance = alignment.get("asof_tolerance_bars")
        if asof_tolerance is not None and (not isinstance(asof_tolerance, int) or asof_tolerance < 0):
            errors.append("alignment_policy.asof_tolerance_bars must be integer >= 0")

        return {
            "join_mode": join_mode,
            "calendar_policy": calendar_policy,
            "asof_tolerance_bars": asof_tolerance,
        }

    def _validate_time_semantics(
        self, time_semantics: Any, errors: List[str], warnings: List[str]
    ) -> Dict[str, Any]:
        if time_semantics is None:
            warnings.append("feature contract time_semantics is not declared")
            return {}
        if not isinstance(time_semantics, dict):
            errors.append("time_semantics must be an object")
            return {}
        observation = time_semantics.get("signal_observation_time")
        earliest = time_semantics.get("trade_earliest_time")
        default_lag = time_semantics.get("default_feature_lag_bars")
        if observation not in self.VALID_SIGNAL_TIMES:
            errors.append("time_semantics.signal_observation_time must be bar_open/bar_close")
        if earliest not in self.VALID_TRADE_TIMES:
            errors.append("time_semantics.trade_earliest_time must be same_bar/next_bar")
        if default_lag is not None and (not isinstance(default_lag, int) or default_lag < 0):
            errors.append("time_semantics.default_feature_lag_bars must be integer >= 0")
        return {
            "signal_observation_time": observation,
            "trade_earliest_time": earliest,
            "default_feature_lag_bars": default_lag,
        }
