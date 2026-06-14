"""Shared runtime label and artifact naming helpers."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

MODULE_DISPLAY = {
    "autorunner": "Backtest",
    "backtest": "Backtest",
    "wfa": "Walk-Forward",
    "wfanalyser": "Walk-Forward",
    "statanalyser": "Factor Analysis",
    "predictor": "Factor Analysis",
}

WORKFLOW_SLUG = {
    "autorunner": "backtest",
    "backtest": "backtest",
    "wfa": "wfa",
    "wfanalyser": "wfa",
    "statanalyser": "predictor",
    "predictor": "predictor",
}

MODE_DISPLAY = {
    "matrix": "Parameter Matrix",
    "single": "Single Backtest",
    "windows": "Rolling Windows",
    "summary": "Summary",
}

VALID_RUN_TYPES = {"production", "test"}
LEGACY_TEST_RUN_TYPES = {"smoke", "latest", "sweep", "manual"}

_KNOWN_FACTORS = {
    "calendar": ("CALENDAR", "Calendar"),
    "mmfi": ("MMFI", "MMFI"),
    "vix": ("VIX", "VIX"),
    "price": ("PRICE", "Price"),
}


def infer_label_badges(text: str) -> List[str]:
    return []


def normalize_run_type(run_type: str) -> str:
    normalized = str(run_type or "").strip().lower()
    if normalized == "production":
        return "production"
    if normalized == "test" or normalized in LEGACY_TEST_RUN_TYPES:
        return "test"
    return ""


def display_run_type(run_type: str) -> str:
    normalized = normalize_run_type(run_type)
    if normalized == "production":
        return "Production"
    if normalized == "test":
        return "Test"
    return ""


def config_filename(path_or_label: str) -> str:
    text = str(path_or_label)
    return re.split(r"[\\/]", text)[-1]


def decorate_config_item(item: Dict[str, Any], module: str) -> Dict[str, Any]:
    payload = dict(item)
    label = str(item.get("label", ""))
    config_meta = item.get("platform", {}) if isinstance(item.get("platform"), dict) else {}
    explicit_run_type = str(config_meta.get("run_type", "")).strip().lower()
    normalized_run_type = normalize_run_type(explicit_run_type)
    filename = config_filename(label)
    raw_config = item.get("raw_config") if isinstance(item.get("raw_config"), dict) else {}
    config_hash = item.get("config_hash")
    config_mtime = item.get("config_mtime")
    identity = build_trading_identity(
        module=module,
        config_filename=filename,
        raw_config=raw_config,
        config_hash=str(config_hash or ""),
        config_mtime=config_mtime,
    )
    payload["filename"] = filename
    payload["canonical_filename"] = canonical_config_filename(identity)
    payload["display_label"] = _explicit_config_display_label(
        config_meta,
        identity,
        id_prefix="cfg",
        allow_explicit=str(module).lower() in {"wfa", "wfanalyser"},
    ) or display_identity_label(identity, id_prefix="cfg")
    payload["badges"] = []
    if normalized_run_type:
        payload["badges"].append(display_run_type(explicit_run_type))
    payload["metadata_complete"] = explicit_run_type in VALID_RUN_TYPES
    payload["identity"] = public_identity(identity)
    return payload


def decorate_run_label(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(row)
    module = str(row.get("module", ""))
    explicit_run_type = str(row.get("run_type", "")).strip().lower()
    backtester_config = _dict_or_empty(row.get("backtester_config"))
    portfolio_config = _dict_or_empty(backtester_config.get("portfolio_config"))
    raw_config = portfolio_config if portfolio_config.get("schema_version") == "strategy_run" else {}
    identity = build_trading_identity(
        module=module,
        config_filename=str(row.get("config_filename", "")),
        semantic_label=str(row.get("semantic_label", "")),
        run_id=str(row.get("run_id", "")),
        raw_config=raw_config,
        dataloader_config=_dict_or_empty(row.get("dataloader_config")),
        backtester_config=backtester_config,
        wfa_config=_dict_or_empty(row.get("wfa_config")),
    )
    payload["display_label"] = display_identity_label(identity, id_prefix="run")
    payload["selector_label"] = payload["display_label"]
    payload["canonical_stem"] = canonical_stem(identity)
    payload["label_badges"] = []
    normalized_run_type = normalize_run_type(explicit_run_type)
    if normalized_run_type:
        payload["label_badges"].append(display_run_type(explicit_run_type))
    payload["module_display"] = MODULE_DISPLAY.get(module, module.title())
    payload["metadata_complete"] = explicit_run_type in VALID_RUN_TYPES
    payload["is_legacy_result"] = row.get("semantic_index_complete") is False
    payload["has_incomplete_strategy_labels"] = (
        row.get("strategy_label_mode") == "internal_id_fallback"
    )
    payload["identity"] = public_identity(identity)
    return payload


def load_app_config_metadata(config_path: str, module: str) -> Dict[str, Any]:
    path = Path(config_path).resolve()
    filename = path.name
    payload: Dict[str, Any] = {
        "filename": filename,
        "canonical_filename": filename,
        "display_label": filename,
        "badges": [],
        "run_type": "",
        "metadata_complete": False,
    }
    try:
        raw_text = path.read_text(encoding="utf-8-sig")
        raw = json.loads(raw_text)
    except Exception:
        return payload

    config_meta = raw.get("platform", {}) if isinstance(raw, dict) else {}
    if not isinstance(config_meta, dict):
        config_meta = {}

    explicit_run_type = str(config_meta.get("run_type", "")).strip().lower()
    normalized_run_type = normalize_run_type(explicit_run_type)
    badges: List[str] = []
    if normalized_run_type:
        badges.append(display_run_type(explicit_run_type))

    identity = build_trading_identity(
        module=module,
        config_filename=filename,
        raw_config=raw if isinstance(raw, dict) else {},
        config_hash=_short_hash(raw_text),
        config_mtime=path.stat().st_mtime if path.exists() else None,
    )
    payload.update(
        {
            "canonical_filename": canonical_config_filename(identity),
            "display_label": _explicit_config_display_label(
                config_meta,
                identity,
                id_prefix="cfg",
                allow_explicit=str(module).lower() in {"wfa", "wfanalyser"},
            )
            or display_identity_label(identity, id_prefix="cfg"),
            "badges": badges,
            "run_type": explicit_run_type,
            "metadata_complete": explicit_run_type in VALID_RUN_TYPES,
            "identity": public_identity(identity),
        }
    )
    return payload


def build_trading_identity(
    *,
    module: str,
    config_filename: str = "",
    semantic_label: str = "",
    run_id: str = "",
    raw_config: Optional[Dict[str, Any]] = None,
    dataloader_config: Optional[Dict[str, Any]] = None,
    backtester_config: Optional[Dict[str, Any]] = None,
    wfa_config: Optional[Dict[str, Any]] = None,
    config_hash: str = "",
    config_mtime: Any = None,
) -> Dict[str, str]:
    raw_config = raw_config or {}
    dataloader = dataloader_config or _dict_or_empty(raw_config.get("dataloader"))
    backtester = backtester_config or _dict_or_empty(raw_config.get("backtester"))
    wfa = wfa_config or _dict_or_empty(raw_config.get("wfa_config"))
    workflow = WORKFLOW_SLUG.get(str(module), str(module) or "run")
    source_text = " ".join(
        item
        for item in [
            config_filename,
            semantic_label,
            str(backtester.get("Backtest_id", "")),
            str(backtester.get("strategy_contract_path", "")),
            str(backtester.get("feature_contract_path", "")),
            str(dataloader.get("file_config", {}).get("file_path", ""))
            if isinstance(dataloader.get("file_config"), dict)
            else "",
        ]
        if item
    ).lower()
    compact_date = _compact_date_from_run_id(run_id) or _date_from_text(source_text) or _compact_date_from_mtime(config_mtime)
    if not compact_date:
        compact_date = datetime.now().strftime("%Y%m%d")

    short_id = _short_id_from_run_id(run_id) or _normalize_short_id(config_hash)
    if not short_id:
        short_id = _short_hash(json.dumps(raw_config, sort_keys=True, default=str))

    is_strategy_run = str(raw_config.get("schema_version", "")).lower() == "strategy_run"
    if is_strategy_run:
        asset = _extract_strategy_run_asset(raw_config) or "ASSET"
        factor_slug, factor_display = _extract_strategy_run_factor(raw_config) or ("CONFIG", "Config")
        strategy_slug, strategy_display = _extract_strategy_run_strategy(raw_config) or (
            "strategy",
            "Strategy",
        )
        mode_slug = _extract_strategy_run_mode(raw_config, workflow) or _mode_from_workflow(workflow)
    else:
        asset = _extract_asset(dataloader, backtester, source_text)
        factor_slug, factor_display = _extract_factor(backtester, dataloader, source_text)
        strategy_slug, strategy_display = _extract_strategy(backtester, source_text)
        mode_slug = _extract_mode(workflow, config_filename, source_text, wfa)
    return {
        "workflow": workflow,
        "workflow_display": MODULE_DISPLAY.get(module, MODULE_DISPLAY.get(workflow, workflow.title())),
        "date": compact_date,
        "date_display": _display_date(compact_date),
        "asset": asset,
        "factor_slug": factor_slug,
        "factor_display": factor_display,
        "strategy_slug": strategy_slug,
        "strategy_display": strategy_display,
        "mode": mode_slug,
        "mode_display": MODE_DISPLAY.get(mode_slug, _title_from_slug(mode_slug)),
        "short_id": short_id[:6],
    }


def canonical_stem(identity: Dict[str, str]) -> str:
    return "_".join(
        _safe_part(part)
        for part in [
            identity.get("workflow", "run"),
            identity.get("date", datetime.now().strftime("%Y%m%d")),
            identity.get("asset", "ASSET"),
            identity.get("factor_slug", "FACTOR"),
            identity.get("strategy_slug", "strategy"),
            identity.get("mode", "output"),
            identity.get("short_id", "000000"),
        ]
    )


def canonical_config_filename(identity: Dict[str, str]) -> str:
    return f"{canonical_stem(identity)}.json"


def canonical_artifact_filename(
    *,
    identity: Dict[str, str],
    artifact_type: str,
    source_name: str,
    suffix: str,
) -> str:
    source = source_name.lower()
    prefix = canonical_output_prefix(identity)
    short_id = _safe_part(identity.get("short_id", "000000"))
    ext = Path(source_name).suffix.lower() or ".parquet"
    if artifact_type == "backtester_parquet":
        return f"{prefix}_backtests_{short_id}{ext}"
    if artifact_type == "backtester_csv":
        return f"{prefix}_backtests_{short_id}{ext}"
    if artifact_type == "backtester_excel":
        return f"{prefix}_backtests_{short_id}{ext}"
    if artifact_type == "metricstracker_parquet":
        return f"{prefix}_metrics_{short_id}{ext}"
    if artifact_type == "metricstracker_metadata":
        return f"{prefix}_metrics_metadata_{short_id}.json"
    if artifact_type in {"wfa_parquet", "wfa_csv"}:
        objective = _objective_from_source(source) or "results"
        return f"{prefix}_{objective}_{short_id}{ext}"
    if artifact_type in {"wfa_candidate_diagnostics_parquet", "wfa_candidate_diagnostics_csv"}:
        objective = _objective_from_source(source) or "results"
        return f"{prefix}_candidate-diagnostics-{objective}_{short_id}{ext}"
    if artifact_type in {"wfa_ranking_parquet", "wfa_ranking_csv"}:
        objective = _objective_from_source(source) or "results"
        top_n = _top_n_from_source(source)
        return f"{prefix}_ranking-{objective}_{top_n}_{short_id}{ext}"
    if artifact_type == "statanalyser_summary_json":
        return f"{prefix}_summary_{short_id}.json"
    if artifact_type == "statanalyser_tabular_output":
        return f"{prefix}_summary_{short_id}{ext}"
    if artifact_type == "statanalyser_report_file":
        return f"{prefix}_report_{short_id}{ext}"
    if artifact_type == "portfolio_equity_curve_parquet":
        return _portfolio_artifact_name(prefix, source_name, "portfolio-equity", short_id, ext)
    if artifact_type == "portfolio_holdings_parquet":
        return _portfolio_artifact_name(prefix, source_name, "portfolio-holdings", short_id, ext)
    if artifact_type == "portfolio_rebalance_audit_parquet":
        return _portfolio_artifact_name(prefix, source_name, "portfolio-rebalance-audit", short_id, ext)
    if artifact_type == "portfolio_rebalance_trades_parquet":
        return _portfolio_artifact_name(prefix, source_name, "portfolio-rebalance-trades", short_id, ext)
    if artifact_type == "portfolio_metadata_json":
        return _portfolio_artifact_name(prefix, source_name, "portfolio-metadata", short_id, ".json")
    if artifact_type == "portfolio_run_validation_json":
        return _portfolio_artifact_name(prefix, source_name, "portfolio-run-validation", short_id, ".json")
    if artifact_type == "audit_sidecar":
        return f"{prefix}_{_safe_part(suffix) or 'audit'}_{short_id}{ext}"
    return f"{prefix}_{_safe_part(suffix) or 'artifact'}_{short_id}{ext}"


def _portfolio_artifact_name(
    prefix: str,
    source_name: str,
    artifact_label: str,
    short_id: str,
    ext: str,
) -> str:
    source_stem = _safe_part(Path(source_name).stem)
    source_hash = _short_hash(source_name)
    if len(source_stem) > 120:
        source_stem = source_stem[:120].strip("_")
    return f"{prefix}_{artifact_label}_{source_stem}_{source_hash}_{short_id}{ext}"


def canonical_output_prefix(identity: Dict[str, str]) -> str:
    return "_".join(
        _safe_part(part)
        for part in [
            identity.get("workflow", "run"),
            identity.get("date", datetime.now().strftime("%Y%m%d")),
            identity.get("asset", "ASSET"),
            identity.get("factor_slug", "FACTOR"),
            identity.get("strategy_slug", "strategy"),
            identity.get("mode", "output"),
        ]
    )


def display_identity_label(identity: Dict[str, str], *, id_prefix: str) -> str:
    parts = [
        identity.get("workflow_display") or _title_from_slug(identity.get("workflow", "")),
        identity.get("date_display") or identity.get("date", ""),
        identity.get("asset", ""),
        identity.get("factor_display", ""),
        identity.get("mode_display", ""),
    ]
    label = " | ".join(part for part in parts if str(part).strip())
    short_id = identity.get("short_id", "")
    if short_id:
        label = f"{label} | {id_prefix} {short_id}"
    return label


def _explicit_config_display_label(
    config_meta: Dict[str, Any],
    identity: Dict[str, str],
    *,
    id_prefix: str,
    allow_explicit: bool,
) -> str:
    if not allow_explicit:
        return ""
    label = str(config_meta.get("display_label", "")).strip()
    if not label:
        return ""
    short_id = str(identity.get("short_id", "")).strip()
    if short_id and f"{id_prefix} " not in label.lower():
        label = f"{label} | {id_prefix} {short_id}"
    return label


def public_identity(identity: Dict[str, str]) -> Dict[str, str]:
    keys = [
        "workflow",
        "date",
        "asset",
        "factor_slug",
        "factor_display",
        "strategy_slug",
        "strategy_display",
        "mode",
        "short_id",
    ]
    return {key: identity.get(key, "") for key in keys}


def _dict_or_empty(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_strategy_run_asset(raw_config: Dict[str, Any]) -> str:
    if str(raw_config.get("schema_version", "")).lower() != "strategy_run":
        return ""
    universe = _dict_or_empty(raw_config.get("universe"))
    symbols = universe.get("symbols")
    if isinstance(symbols, list):
        clean = [_safe_part(str(item).upper()) for item in symbols if str(item).strip()]
        if clean:
            return "-".join(clean[:5]) if len(clean) <= 5 else f"{len(clean)}-ASSETS"
    data = _dict_or_empty(raw_config.get("data"))
    symbol = data.get("symbol") or data.get("ticker") or data.get("asset")
    if isinstance(symbol, str) and symbol.strip():
        return _safe_part(symbol.upper()) or "ASSET"
    if str(data.get("provider", "")).lower() in {"file", "csv", "local"}:
        return "DATASET"
    return ""


def _extract_strategy_run_factor(raw_config: Dict[str, Any]) -> Optional[tuple[str, str]]:
    if str(raw_config.get("schema_version", "")).lower() != "strategy_run":
        return None
    platform = _dict_or_empty(raw_config.get("platform"))
    mode = str(platform.get("strategy_mode_id", "")).lower()
    allocation = _dict_or_empty(raw_config.get("allocation"))
    allocation_method = str(allocation.get("method", "")).lower()
    if mode == "calendar_event_session" or allocation_method == "calendar_event_overlay":
        return "CALENDAR", "Calendar"
    computed_fields = raw_config.get("computed_fields")
    if not isinstance(computed_fields, list):
        computed_fields = raw_config.get("indicators")
    if not isinstance(computed_fields, list):
        computed_fields = raw_config.get("features")
    if not isinstance(computed_fields, list):
        computed_fields = []
    indicator_text = " ".join(
        " ".join(str(item.get(key, "")) for key in ("name", "op", "source"))
        for item in computed_fields
        if isinstance(item, dict)
    ).lower()
    factor_pipeline = raw_config.get("factor_pipeline")
    if isinstance(factor_pipeline, dict) and factor_pipeline:
        return "FACTOR", "Factor"
    if "calendar" in indicator_text:
        return "CALENDAR", "Calendar"
    if any(token in indicator_text for token in ("momentum", "sma", "price", "close", "return")):
        return "PRICE", "Price"
    if allocation_method in {"fixed_weight", "fixed_weights", "target_weights"}:
        return "ALLOCATION", "Allocation"
    if mode == "multi_asset_portfolio":
        return "PORTFOLIO", "Portfolio"
    return None


def _extract_strategy_run_strategy(raw_config: Dict[str, Any]) -> Optional[tuple[str, str]]:
    if str(raw_config.get("schema_version", "")).lower() != "strategy_run":
        return None
    allocation = _dict_or_empty(raw_config.get("allocation"))
    if str(allocation.get("method", "")).lower() == "calendar_event_overlay":
        return "calendar-event-overlay", "Calendar Event Overlay"
    metadata = _dict_or_empty(raw_config.get("metadata"))
    platform = _dict_or_empty(raw_config.get("platform"))
    text = " ".join(
        [
            str(metadata.get("strategy_id", "")),
            str(platform.get("display_label", "")),
        ]
    ).lower()
    if "fixed" in text and "rebalance" in text:
        return "fixed-allocation", "Fixed Allocation"
    if "momentum" in text and "sma" in text:
        return "momentum-sma-rotation", "Momentum SMA Rotation"
    if "calendar" in text:
        if "overlay" in text:
            return "calendar-event-overlay", "Calendar Event Overlay"
        return "calendar-event", "Calendar Event"
    if "ma_cross" in text or "ma-cross" in text:
        return "ma-cross", "MA Cross"
    return None


def _extract_strategy_run_mode(raw_config: Dict[str, Any], workflow: str) -> str:
    if str(raw_config.get("schema_version", "")).lower() != "strategy_run":
        return ""
    if workflow in {"predictor", "wfa"}:
        return ""
    platform = _dict_or_empty(raw_config.get("platform"))
    workflow_id = str(platform.get("workflow_id", "")).lower()
    if workflow_id == "parameter_matrix":
        return "matrix"
    if workflow_id == "walk_forward_analysis":
        return "windows"
    if workflow_id == "rolling_validation":
        return "windows"
    if workflow_id == "single_backtest":
        return "single"
    return ""


def _mode_from_workflow(workflow: str) -> str:
    if workflow == "wfa":
        return "windows"
    if workflow == "predictor":
        return "summary"
    return "single"


def _short_hash(text: Any) -> str:
    return hashlib.blake2s(
        str(text).encode("utf-8", errors="ignore"),
        digest_size=8,
    ).hexdigest()[:6]


def _normalize_short_id(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z]", "", str(value or ""))
    return value[:8].lower()


def _short_id_from_run_id(run_id: str) -> str:
    match = re.match(r"^\d{8}_([0-9A-Za-z]+)", str(run_id or ""))
    return match.group(1)[:6].lower() if match else ""


def _compact_date_from_run_id(run_id: str) -> str:
    match = re.match(r"^(\d{8})_", str(run_id or ""))
    return match.group(1) if match else ""


def _date_from_text(text: str) -> str:
    match = re.search(r"(20\d{6})", str(text or ""))
    return match.group(1) if match else ""


def _compact_date_from_mtime(value: Any) -> str:
    if value is None:
        return ""
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y%m%d")
    except Exception:
        return ""


def _display_date(compact: str) -> str:
    if re.match(r"^\d{8}$", str(compact or "")):
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    return str(compact or "")


def _extract_asset(dataloader: Dict[str, Any], backtester: Dict[str, Any], source_text: str) -> str:
    if (
        str(dataloader.get("source", "")).lower() == "multi_asset"
        or str(backtester.get("strategy_mode", "")).lower() == "multi_asset_portfolio"
    ):
        portfolio_config = _dict_or_empty(backtester.get("portfolio_config"))
        universe = _dict_or_empty(portfolio_config.get("universe"))
        symbols = universe.get("symbols")
        if isinstance(symbols, list):
            clean = [_safe_part(str(item).upper()) for item in symbols if str(item).strip()]
            if clean:
                return "-".join(clean[:5]) if len(clean) <= 5 else f"{len(clean)}-ASSETS"
        market_data = _dict_or_empty(backtester.get("market_data"))
        symbols = market_data.get("symbols")
        if isinstance(symbols, list):
            clean = [_safe_part(str(item).upper()) for item in symbols if str(item).strip()]
            if clean:
                return "-".join(clean[:5]) if len(clean) <= 5 else f"{len(clean)}-ASSETS"
        return "MULTI"
    for key in ["yfinance_config", "binance_config", "coinbase_config"]:
        symbol = dataloader.get(key, {}).get("symbol") if isinstance(dataloader.get(key), dict) else None
        if isinstance(symbol, str) and symbol.strip():
            return _safe_part(symbol.upper()) or "ASSET"
    if str(dataloader.get("source", "")).lower() == "file":
        return "DATASET"
    for container in [dataloader, _dict_or_empty(dataloader.get("file_config")), backtester]:
        for key in ["symbol", "ticker", "asset", "instrument", "trading_instrument"]:
            symbol = container.get(key) if isinstance(container, dict) else None
            if isinstance(symbol, str) and symbol.strip() and symbol.upper() not in {"X", "LOCAL", "FILE"}:
                return _safe_part(symbol.upper()) or "ASSET"
    match = re.search(r"(?<![a-z0-9])(spy|qqq|btc|eth|aapl|msft|nvda)(?![a-z0-9])", source_text.lower())
    if match:
        return match.group(1).upper()
    return "ASSET"


def _extract_factor(
    backtester: Dict[str, Any],
    dataloader: Dict[str, Any],
    source_text: str,
) -> tuple[str, str]:
    if str(backtester.get("strategy_mode", "")).lower() == "multi_asset_portfolio":
        return "PRICE", "Price"
    tokens: List[str] = []
    feature_path = str(backtester.get("feature_contract_path", "")).lower()
    predictor = str(backtester.get("selected_predictor", "") or "")
    predictor_cfg = dataloader.get("predictor_config", {})
    predictor_column = ""
    if isinstance(predictor_cfg, dict):
        predictor_column = str(predictor_cfg.get("predictor_column", "") or "")

    searchable = " ".join([feature_path, predictor, predictor_column, source_text]).lower()
    for token in ["calendar", "mmfi", "vix", "price"]:
        if token in searchable and token not in tokens:
            tokens.append(token)
    if not tokens and predictor and predictor.upper() == "X":
        tokens.append("price")
    if not tokens:
        tokens.append("factor")
    slug_parts = [_KNOWN_FACTORS.get(token, (token.upper(), token.title()))[0] for token in tokens]
    display_parts = [_KNOWN_FACTORS.get(token, (token.upper(), token.title()))[1] for token in tokens]
    return "-".join(slug_parts), " + ".join(display_parts)


def _extract_strategy(backtester: Dict[str, Any], source_text: str) -> tuple[str, str]:
    if str(backtester.get("strategy_mode", "")).lower() == "multi_asset_portfolio":
        portfolio_config = _dict_or_empty(backtester.get("portfolio_config"))
        strategy_id = str(portfolio_config.get("strategy_id") or backtester.get("Backtest_id") or "").lower()
        searchable = " ".join([strategy_id, source_text]).lower()
        if "fixed" in searchable and "rebalance" in searchable:
            return "fixed-allocation", "Fixed Allocation"
        if "momentum" in searchable and "sma" in searchable:
            return "momentum-sma-rotation", "Momentum SMA Rotation"
        if "rotation" in searchable:
            return "rotation", "Rotation"
        return "portfolio-selection", "Portfolio Selection"
    strategy_path = str(backtester.get("strategy_contract_path", "")).lower()
    searchable = " ".join([strategy_path, source_text]).lower()
    if "calendar" in searchable:
        return "calendar-event", "Calendar Event"
    if "ma-cross" in searchable or "macross" in searchable:
        return "ma-cross", "MA Cross"
    if "threshold" in searchable and "hold-reset" in searchable:
        return "threshold-hold-reset", "Threshold Hold Reset"
    if "hold-reset" in searchable:
        return "hold-reset", "Hold Reset"
    if "threshold" in searchable:
        return "threshold", "Threshold"
    return "strategy", "Strategy"


def _extract_mode(
    workflow: str,
    config_filename: str,
    source_text: str,
    wfa_config: Dict[str, Any],
) -> str:
    if workflow == "wfa":
        return "windows"
    if workflow == "predictor":
        return "summary"
    if "sweep" in source_text or "matrix" in source_text:
        return "matrix"
    if isinstance(wfa_config, dict) and wfa_config:
        return "windows"
    if "sweep" in str(config_filename).lower():
        return "matrix"
    return "single"


def _objective_from_source(source_name: str) -> str:
    source = str(source_name or "").lower()
    for objective in ["sharpe", "calmar", "return"]:
        if f"_{objective}_" in source or f"-{objective}_" in source:
            return objective
    return ""


def _top_n_from_source(source_name: str) -> str:
    match = re.search(r"top(\d+)", str(source_name or "").lower())
    return f"top{match.group(1)}" if match else "top20"


def _title_from_slug(value: str) -> str:
    return " ".join(part.upper() if len(part) <= 4 else part.title() for part in re.split(r"[-_]+", str(value or "")) if part)


def _safe_part(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("+", "-")
    text = re.sub(r"[^0-9A-Za-z._-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_.")
    return text or "unknown"
