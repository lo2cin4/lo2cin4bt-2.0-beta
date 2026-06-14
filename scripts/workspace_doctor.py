from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autorunner.FeatureContractValidator_v1 import FeatureContractValidatorV1  # noqa: E402
from autorunner.StrategyContractValidator import StrategyContractValidator  # noqa: E402
from backtester.StrategyRunConfig_backtester import (  # noqa: E402
    StrategyRunConfigError,
    normalize_strategy_run_config,
    normalize_wfa_run_config,
    plan_strategy_execution,
    validate_repo_relative_json_path,
)
from backtester.UniverseConstituentsValidator_backtester import (  # noqa: E402
    CONSTITUENTS_PATH_KEYS,
    validate_historical_universe_constituents,
)

FEATURE_CONTRACT_SCHEMA = "backtester/contracts/feature/feature-contract-v1.schema.json"
STRATEGY_RUN_SCHEMA = "backtester/contracts/strategy/strategy-run.schema.json"
WFA_RUN_SCHEMA = "backtester/contracts/strategy/wfa-run.schema.json"
FACTOR_PIPELINE_SCHEMA = "backtester/contracts/strategy/factor-pipeline-v1.schema.json"


@dataclass
class DoctorReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def ok(self, message: str) -> None:
        self.checked.append(message)


def _repo_path(repo_root: Path, raw_path: str, *, must_exist: bool = True) -> Path | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    try:
        validate_repo_relative_json_path(text, field_name="workspace path")
    except StrategyRunConfigError:
        # Some referenced datasets are CSV/parquet, not JSON. Still enforce repo-relative safety.
        path = Path(text)
        normalized = text.replace("\\", "/")
        if path.is_absolute() or normalized.startswith("/") or ".." in Path(normalized).parts:
            raise
    path = (repo_root / text).resolve()
    try:
        path.relative_to(repo_root)
    except ValueError as exc:
        raise StrategyRunConfigError(f"path escaped repo: {raw_path}") from exc
    if must_exist and not path.exists():
        raise FileNotFoundError(str(path))
    return path


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_schema(repo_root: Path, rel_path: str) -> dict[str, Any]:
    data = _load_json(repo_root / rel_path)
    if not isinstance(data, dict):
        raise ValueError(f"schema is not an object: {rel_path}")
    return data


def _iter_json_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.json") if path.is_file())


def _collect_path_values(payload: Any, suffix: str) -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.endswith(suffix) and isinstance(value, str) and value.strip():
                found.append(value.strip())
            found.extend(_collect_path_values(value, suffix))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_collect_path_values(item, suffix))
    return found


def _rel_text(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _require_under(repo_root: Path, path: Path, prefix: str) -> bool:
    return _rel_text(repo_root, path).startswith(prefix)


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _domain_value_count(domain: Any) -> int:
    if isinstance(domain, list):
        return len(domain)
    if not isinstance(domain, dict):
        return 1
    values = domain.get("values")
    if isinstance(values, list):
        return len(values)
    if domain.get("type") == "range":
        start = domain.get("start")
        end = domain.get("end")
        step = domain.get("step", 1)
        if not all(isinstance(value, (int, float)) for value in (start, end, step)):
            return 1
        if step == 0:
            return 1
        distance = end - start
        if distance == 0:
            return 1
        if (distance > 0 and step < 0) or (distance < 0 and step > 0):
            return 1
        return max(1, int(distance / step) + 1)
    return 1


def _parameter_combo_count(parameter_domains: Any) -> int:
    if not isinstance(parameter_domains, dict) or not parameter_domains:
        return 1
    total = 1
    for domain in parameter_domains.values():
        total *= _domain_value_count(domain)
    return total


def _validate_workflow_parameter_shape(
    config_path: Path,
    payload: dict[str, Any],
    repo_root: Path,
    report: DoctorReport,
) -> None:
    rel = _rel_text(repo_root, config_path)
    platform = _first_dict(payload.get("platform"))
    workflow_id = str(platform.get("workflow_id") or "").strip()
    parameter_domains = payload.get("parameter_domains")
    has_domains = isinstance(parameter_domains, dict) and bool(parameter_domains)
    combo_count = _parameter_combo_count(parameter_domains)
    if workflow_id == "parameter_matrix" and combo_count <= 1:
        report.error(
            f"{rel}: workflow_id=parameter_matrix must define one config with parameter_domains that expand to at least 2 combinations"
        )
    if workflow_id == "single_backtest" and has_domains:
        report.error(
            f"{rel}: workflow_id=single_backtest must not carry parameter_domains; use workflow_id=parameter_matrix for sweeps"
        )


def _validate_json_schema(
    repo_root: Path,
    payload: dict[str, Any],
    schema_rel_path: str,
    rel: str,
    report: DoctorReport,
) -> bool:
    errors = sorted(
        Draft202012Validator(_load_schema(repo_root, schema_rel_path)).iter_errors(payload),
        key=lambda error: list(error.path),
    )
    for error in errors:
        location = ".".join(str(part) for part in error.path) or "<root>"
        report.error(f"{rel}: schema error at {location}: {error.message}")
    return not errors


def _require_feature_time_semantics(
    payload: dict[str, Any],
    rel: str,
    report: DoctorReport,
) -> None:
    time_semantics = payload.get("time_semantics")
    if not isinstance(time_semantics, dict):
        report.error(f"{rel}: time_semantics is required for user-provided feature data")
        return
    if (
        time_semantics.get("signal_observation_time") == "bar_close"
        and time_semantics.get("trade_earliest_time") == "same_bar"
    ):
        report.error(f"{rel}: bar_close observation cannot trade on same_bar")


def _validate_local_market_data_refs(
    repo_root: Path,
    config_path: Path,
    payload: dict[str, Any],
    report: DoctorReport,
) -> None:
    rel = _rel_text(repo_root, config_path)
    market_data = _first_dict(payload.get("data")).get("market_data")
    if not isinstance(market_data, dict):
        market_data = _first_dict(payload.get("backtester")).get("market_data")
    if not isinstance(market_data, dict):
        return
    provider = str(market_data.get("provider") or market_data.get("source") or "").strip().lower()
    if provider in {"yfinance", "yf", "binance", "binance_spot", "coinbase", "coinbase_exchange", "futu", "futu_openapi", "ibkr", "interactive_brokers", "interactivebrokers"}:
        return
    for field_name, field_spec in market_data.items():
        key = str(field_name).strip().lower()
        if key in {"provider", "source", "symbols", "start", "start_date", "end", "end_date", "interval"}:
            continue
        if isinstance(field_spec, str):
            report.error(f"{rel}: data.market_data.{field_name} must declare path and time_column explicitly")
            continue
        if not isinstance(field_spec, dict):
            report.error(f"{rel}: data.market_data.{field_name} must be an object")
            continue
        raw_path = str(field_spec.get("path") or "").strip()
        if not raw_path:
            report.error(f"{rel}: data.market_data.{field_name}.path is required")
            continue
        if not str(field_spec.get("time_column") or "").strip():
            report.error(f"{rel}: data.market_data.{field_name}.time_column is required")
        try:
            path = _repo_path(repo_root, raw_path)
        except Exception as exc:
            report.error(f"{rel}: invalid data.market_data.{field_name}.path {raw_path}: {exc}")
            continue
        if path is not None and not _require_under(repo_root, path, "workspace/datasets/"):
            report.error(f"{rel}: data.market_data.{field_name}.path must be under workspace/datasets: {raw_path}")


def _validate_file_provider_data_ref(
    repo_root: Path,
    config_path: Path,
    payload: dict[str, Any],
    report: DoctorReport,
) -> None:
    rel = _rel_text(repo_root, config_path)
    data = _first_dict(payload.get("data"))
    if not data:
        return
    provider = str(data.get("provider") or data.get("source") or "").strip().lower()
    file_config = _first_dict(data.get("file_config"))
    raw_path = str(data.get("file_path") or data.get("path") or file_config.get("file_path") or "").strip()
    if provider in {"file", "local_csv", "csv"} and not raw_path:
        report.error(f"{rel}: data.provider={provider} requires data.file_path or data.path")
        return
    if not raw_path:
        return
    try:
        path = _repo_path(repo_root, raw_path)
    except Exception as exc:
        report.error(f"{rel}: invalid data file path {raw_path}: {exc}")
        return
    if path is not None and not _require_under(repo_root, path, "workspace/datasets/"):
        report.error(f"{rel}: data file path must be under workspace/datasets: {raw_path}")
    date_column = str(data.get("date_column") or file_config.get("date_column") or "").strip()
    price_column = str(data.get("price_column") or file_config.get("price_column") or "").strip()
    if not date_column:
        report.error(f"{rel}: local file data requires explicit date_column")
    if not price_column:
        report.error(f"{rel}: local file data requires explicit price_column")


def _validate_universe_provenance(
    repo_root: Path,
    config_path: Path,
    payload: dict[str, Any],
    report: DoctorReport,
) -> None:
    rel = _rel_text(repo_root, config_path)
    universe = _first_dict(payload.get("universe"))
    if not universe:
        return
    if not str(universe.get("universe_policy") or "").strip():
        report.error(f"{rel}: universe.universe_policy is required")
    if not str(universe.get("survivorship_policy") or "").strip():
        report.error(f"{rel}: universe.survivorship_policy is required")
    for key in CONSTITUENTS_PATH_KEYS:
        raw_path = str(universe.get(key) or "").strip()
        if not raw_path:
            continue
        try:
            path = _repo_path(repo_root, raw_path)
        except Exception as exc:
            report.error(f"{rel}: invalid universe.{key} {raw_path}: {exc}")
            continue
        if path is not None and not _require_under(repo_root, path, "workspace/datasets/"):
            report.error(f"{rel}: universe.{key} must be under workspace/datasets: {raw_path}")
    if any(universe.get(key) not in (None, "", []) for key in CONSTITUENTS_PATH_KEYS):
        result = validate_historical_universe_constituents(
            universe=universe,
            configured_symbols=universe.get("symbols") or [],
            as_of_date=(
                universe.get("as_of_date")
                or universe.get("as_of")
                or universe.get("historical_constituents_as_of")
                or universe.get("universe_constituents_as_of")
            ),
            repo_root=repo_root,
            config_file_path=str(config_path),
        )
        for message in result.get("errors", []):
            report.error(f"{rel}: universe constituents validation failed: {message}")
        for message in result.get("warnings", []):
            report.warn(f"{rel}: universe constituents warning: {message}")


def _validate_fill_model_assumptions(
    config_path: Path,
    normalized: dict[str, Any],
    repo_root: Path,
    report: DoctorReport,
) -> None:
    rel = _rel_text(repo_root, config_path)
    fill_model = _first_dict(normalized.get("fill_model"), normalized.get("execution"))
    cost = fill_model.get("cost") if isinstance(fill_model, dict) else None
    if not isinstance(cost, dict):
        report.error(f"{rel}: fill_model.cost with transaction_cost and slippage is required")
        return
    for key in ("transaction_cost", "slippage"):
        value = cost.get(key)
        if not isinstance(value, (int, float)):
            report.error(f"{rel}: fill_model.cost.{key} must be numeric")


def _validate_factor_pipeline_ref(
    repo_root: Path,
    config_path: Path,
    payload: dict[str, Any],
    report: DoctorReport,
) -> None:
    rel = _rel_text(repo_root, config_path)
    factor_pipeline = _first_dict(payload.get("factor_pipeline"))
    raw_path = str(factor_pipeline.get("external_config_path") or "").strip()
    if not raw_path:
        return
    try:
        path = _repo_path(repo_root, raw_path)
    except Exception as exc:
        report.error(f"{rel}: invalid factor_pipeline.external_config_path {raw_path}: {exc}")
        return
    if path is None:
        return
    path_rel = _rel_text(repo_root, path)
    if not (path_rel.startswith("workspace/features/") or path_rel.startswith("workspace/strategies/")):
        report.error(f"{rel}: factor_pipeline.external_config_path must be under workspace/features or workspace/strategies: {raw_path}")
        return
    try:
        payload = _load_json(path)
    except Exception as exc:
        report.error(f"{path_rel}: cannot parse factor pipeline JSON: {exc}")
        return
    if not isinstance(payload, dict):
        report.error(f"{path_rel}: factor pipeline config must be a JSON object")
        return
    _validate_json_schema(repo_root, payload, FACTOR_PIPELINE_SCHEMA, path_rel, report)


def validate_feature_contract(repo_root: Path, path: Path, report: DoctorReport) -> None:
    rel = _rel_text(repo_root, path)
    try:
        payload = _load_json(path)
    except Exception as exc:
        report.error(f"{rel}: cannot parse JSON: {exc}")
        return
    if not isinstance(payload, dict):
        report.error(f"{rel}: feature contract must be a JSON object")
        return
    _validate_json_schema(repo_root, payload, FEATURE_CONTRACT_SCHEMA, rel, report)
    _require_feature_time_semantics(payload, rel, report)
    result = FeatureContractValidatorV1().validate(payload)
    for message in result.errors:
        report.error(f"{rel}: {message}")
    for message in result.warnings:
        report.warn(f"{rel}: {message}")
    if result.valid:
        report.ok(f"{rel}: feature contract valid")


def validate_strategy_contract(repo_root: Path, path: Path, report: DoctorReport) -> None:
    rel = _rel_text(repo_root, path)
    try:
        payload = _load_json(path)
    except Exception as exc:
        report.error(f"{rel}: cannot parse JSON: {exc}")
        return
    if not isinstance(payload, dict):
        report.error(f"{rel}: strategy contract must be a JSON object")
        return
    if payload.get("schema_version") != "strategy_contract":
        report.warn(f"{rel}: not a strategy.contract file; kept as design note only")
        return
    feature_payload = None
    feature_ref = str(_first_dict(payload.get("data_context")).get("feature_contract_ref") or "").strip()
    if feature_ref:
        try:
            feature_path = _repo_path(repo_root, feature_ref)
        except Exception as exc:
            report.error(f"{rel}: invalid data_context.feature_contract_ref {feature_ref}: {exc}")
            return
        if feature_path is None:
            return
        if not _require_under(repo_root, feature_path, "workspace/features/"):
            report.error(f"{rel}: data_context.feature_contract_ref must be under workspace/features: {feature_ref}")
            return
        try:
            feature_payload = _load_json(feature_path)
        except Exception as exc:
            report.error(f"{rel}: cannot load feature_contract_ref {feature_ref}: {exc}")
            return
        validate_feature_contract(repo_root, feature_path, report)
    result = StrategyContractValidator().validate(payload, feature_payload)
    for message in result.errors:
        report.error(f"{rel}: {message}")
    for message in result.warnings:
        report.warn(f"{rel}: {message}")
    if result.valid:
        report.ok(f"{rel}: strategy contract valid")


def validate_indicator_manifest(repo_root: Path, path: Path, report: DoctorReport) -> None:
    rel = path.relative_to(repo_root).as_posix()
    try:
        payload = _load_json(path)
    except Exception as exc:
        report.error(f"{rel}: cannot parse JSON: {exc}")
        return
    if not isinstance(payload, dict):
        report.error(f"{rel}: indicator manifest must be a JSON object")
        return
    for key in ("schema_version", "indicator_id", "family_code", "implementation"):
        if key not in payload:
            report.error(f"{rel}: missing {key}")
    if payload.get("schema_version") != "1.0":
        report.error(f"{rel}: schema_version must be 1.0")
    implementation = payload.get("implementation")
    if not isinstance(implementation, dict):
        report.error(f"{rel}: implementation must be an object")
        return
    backends = implementation.get("backends")
    if not isinstance(backends, list) or not backends:
        report.error(f"{rel}: implementation.backends must be a non-empty list")
        return
    default_backend = implementation.get("default_backend")
    selected = None
    for backend in backends:
        if isinstance(backend, dict) and backend.get("backend_id") == default_backend:
            selected = backend
            break
    if selected is None and isinstance(backends[0], dict):
        selected = backends[0]
    if not isinstance(selected, dict):
        report.error(f"{rel}: no usable backend")
        return
    artifact_path = str(selected.get("artifact_path", "")).strip()
    if not artifact_path:
        report.error(f"{rel}: backend missing artifact_path")
        return
    artifact = (path.parent / artifact_path).resolve()
    try:
        artifact.relative_to(path.parent.resolve())
    except ValueError:
        report.error(f"{rel}: backend artifact_path escapes extension folder")
        return
    if not artifact.exists():
        report.error(f"{rel}: backend artifact_path does not exist: {artifact_path}")
        return
    report.ok(f"{rel}: indicator manifest valid")


def _validate_referenced_contracts(repo_root: Path, config_path: Path, payload: dict[str, Any], report: DoctorReport) -> None:
    for value in _collect_path_values(payload, "feature_contract_path"):
        try:
            path = _repo_path(repo_root, value)
        except Exception as exc:
            report.error(f"{config_path.relative_to(repo_root).as_posix()}: invalid feature_contract_path {value}: {exc}")
            continue
        if path is None:
            continue
        if not _require_under(repo_root, path, "workspace/features/"):
            report.error(f"{_rel_text(repo_root, config_path)}: feature_contract_path must be under workspace/features: {value}")
            continue
        validate_feature_contract(repo_root, path, report)

    for value in _collect_path_values(payload, "strategy_contract_path"):
        try:
            path = _repo_path(repo_root, value)
        except Exception as exc:
            report.error(f"{config_path.relative_to(repo_root).as_posix()}: invalid strategy_contract_path {value}: {exc}")
            continue
        if path is None:
            continue
        if not _require_under(repo_root, path, "workspace/strategies/"):
            report.error(f"{_rel_text(repo_root, config_path)}: strategy_contract_path must be under workspace/strategies: {value}")
            continue
        validate_strategy_contract(repo_root, path, report)


def validate_run_config(repo_root: Path, path: Path, report: DoctorReport) -> None:
    rel = _rel_text(repo_root, path)
    try:
        payload = _load_json(path)
    except Exception as exc:
        report.error(f"{rel}: cannot parse JSON: {exc}")
        return
    if not isinstance(payload, dict):
        report.error(f"{rel}: run config must be a JSON object")
        return
    if payload.get("schema_version") != "strategy_run":
        report.error(f"{rel}: workspace/runs configs must use schema_version=strategy_run")
        return
    try:
        Draft202012Validator(_load_schema(repo_root, STRATEGY_RUN_SCHEMA)).validate(payload)
        normalized = normalize_strategy_run_config(payload, source_path=path, repo_root=repo_root)
        plan_strategy_execution(normalized)
    except Exception as exc:
        report.error(f"{rel}: strategy_run validation failed: {exc}")
        return
    _validate_workflow_parameter_shape(path, payload, repo_root, report)
    _validate_universe_provenance(repo_root, path, payload, report)
    _validate_fill_model_assumptions(path, normalized, repo_root, report)
    _validate_file_provider_data_ref(repo_root, path, payload, report)
    _validate_local_market_data_refs(repo_root, path, payload, report)
    _validate_factor_pipeline_ref(repo_root, path, payload, report)
    _validate_referenced_contracts(repo_root, path, payload, report)
    report.ok(f"{rel}: strategy_run runnable contract valid")


def validate_wfa_config(repo_root: Path, path: Path, report: DoctorReport) -> None:
    rel = _rel_text(repo_root, path)
    try:
        payload = _load_json(path)
    except Exception as exc:
        report.error(f"{rel}: cannot parse JSON: {exc}")
        return
    if not isinstance(payload, dict):
        report.error(f"{rel}: WFA config must be a JSON object")
        return
    if payload.get("schema_version") != "wfa_run":
        report.error(f"{rel}: workspace/wfa configs must use schema_version=wfa_run")
        return
    try:
        Draft202012Validator(_load_schema(repo_root, WFA_RUN_SCHEMA)).validate(payload)
        normalized = normalize_wfa_run_config(payload, source_path=path, repo_root=repo_root)
    except Exception as exc:
        report.error(f"{rel}: wfa_run validation failed: {exc}")
        return
    windowing = _first_dict(normalized.get("windowing"))
    if not windowing.get("mode"):
        report.error(f"{rel}: windowing.mode is required")
    if windowing.get("target_window_count") in (None, ""):
        report.error(f"{rel}: windowing.target_window_count is required")
    has_ratio = windowing.get("train_ratio") not in (None, "") and windowing.get("test_ratio") not in (None, "")
    has_size = windowing.get("train_size") not in (None, "") and windowing.get("test_size") not in (None, "")
    if not (has_ratio or has_size):
        report.error(f"{rel}: windowing must declare train/test ratio or train/test size")
    if windowing.get("step_size") in (None, ""):
        report.error(f"{rel}: windowing.step_size is required")
    strategy_config_path = str(normalized.get("strategy_config_path", "")).strip()
    if strategy_config_path:
        if not strategy_config_path.replace("\\", "/").startswith("workspace/runs/"):
            report.error(f"{rel}: strategy_config_path must point under workspace/runs")
            return
        try:
            strategy_path = _repo_path(repo_root, strategy_config_path)
        except Exception as exc:
            report.error(f"{rel}: invalid strategy_config_path {strategy_config_path}: {exc}")
            return
        if strategy_path is not None:
            validate_run_config(repo_root, strategy_path, report)
    report.ok(f"{rel}: wfa_run wrapper valid")


def validate_workspace(repo_root: Path, config_paths: list[str] | None = None) -> DoctorReport:
    repo_root = repo_root.resolve()
    report = DoctorReport()
    if config_paths:
        for raw_path in config_paths:
            try:
                path = _repo_path(repo_root, raw_path)
            except Exception as exc:
                report.error(f"{raw_path}: invalid config path: {exc}")
                continue
            if path is None:
                continue
            rel = path.relative_to(repo_root).as_posix()
            if rel.startswith("workspace/wfa/"):
                validate_wfa_config(repo_root, path, report)
            elif rel.startswith("workspace/runs/"):
                validate_run_config(repo_root, path, report)
            elif rel.startswith("workspace/features/"):
                validate_feature_contract(repo_root, path, report)
            elif rel.startswith("workspace/strategies/"):
                validate_strategy_contract(repo_root, path, report)
            elif rel.startswith("workspace/indicators/extensions/") and path.name == "manifest.json":
                validate_indicator_manifest(repo_root, path, report)
            else:
                report.error(f"{rel}: unsupported doctor target; use workspace/runs, wfa, features, strategies, or indicator manifest")
        return report

    for path in _iter_json_files(repo_root / "workspace" / "features"):
        validate_feature_contract(repo_root, path, report)
    for path in _iter_json_files(repo_root / "workspace" / "strategies"):
        validate_strategy_contract(repo_root, path, report)
    for path in _iter_json_files(repo_root / "workspace" / "runs"):
        validate_run_config(repo_root, path, report)
    for path in _iter_json_files(repo_root / "workspace" / "wfa"):
        validate_wfa_config(repo_root, path, report)
    for path in sorted((repo_root / "workspace" / "indicators" / "extensions").glob("**/manifest.json")):
        validate_indicator_manifest(repo_root, path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate lo2cin4bt workspace files before strategy authoring or backtesting."
    )
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        help="Repo-relative workspace file to validate. Can be repeated. Defaults to scanning workspace contracts.",
    )
    args = parser.parse_args()

    report = validate_workspace(ROOT, args.config or None)
    for message in report.checked:
        print(f"[OK] {message}")
    for message in report.warnings:
        print(f"[WARN] {message}")
    for message in report.errors:
        print(f"[FAIL] {message}")
    if report.errors:
        print(f"\nWorkspace doctor failed with {len(report.errors)} error(s).")
        return 1
    print("\nWorkspace doctor passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
