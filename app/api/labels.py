"""Compatibility exports for API label helpers."""

from __future__ import annotations

from app.runtime.labels import (
    LEGACY_TEST_RUN_TYPES,
    MODE_DISPLAY,
    MODULE_DISPLAY,
    VALID_RUN_TYPES,
    WORKFLOW_SLUG,
    build_trading_identity,
    canonical_artifact_filename,
    canonical_config_filename,
    canonical_output_prefix,
    canonical_stem,
    config_filename,
    decorate_config_item,
    decorate_run_label,
    display_identity_label,
    display_run_type,
    infer_label_badges,
    load_app_config_metadata,
    normalize_run_type,
    public_identity,
)

__all__ = [
    "LEGACY_TEST_RUN_TYPES",
    "MODE_DISPLAY",
    "MODULE_DISPLAY",
    "VALID_RUN_TYPES",
    "WORKFLOW_SLUG",
    "build_trading_identity",
    "canonical_artifact_filename",
    "canonical_config_filename",
    "canonical_output_prefix",
    "canonical_stem",
    "config_filename",
    "decorate_config_item",
    "decorate_run_label",
    "display_identity_label",
    "display_run_type",
    "infer_label_badges",
    "load_app_config_metadata",
    "normalize_run_type",
    "public_identity",
]
