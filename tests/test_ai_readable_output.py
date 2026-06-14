import json
from pathlib import Path

import pandas as pd
from jsonschema import Draft202012Validator

from app.api.payloads import AppPayloadService
from app.runtime.registry import AppRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_ai_readable_output_auto_embeds_payload_metrics_and_artifact_columns(tmp_path: Path) -> None:
    registry = AppRegistry(tmp_path)
    service = AppPayloadService(tmp_path, registry)
    run_id = "20260515_ai_pack"
    paths = registry.build_run_paths(run_id)

    registry.write_registry_entry(
        {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-run-registry-v1",
            "run_id": run_id,
            "module": "autorunner",
            "entrypoint": "test",
            "status": "completed",
            "created_at": "2026-05-15T00:00:00+08:00",
            "completed_at": "2026-05-15T00:00:01+08:00",
            "config_filename": "example.json",
        }
    )
    registry.write_stage_status(
        run_id,
        {"schema_version": "1.0", "status": "completed", "stages": []},
    )
    chart_payload = {
        "schema_version": "9.9",
        "contract_id": "test-future-payload",
        "run_id": run_id,
        "rows": [
            {
                "total_return": 0.12,
                "future_metric_score": 2.5,
            }
        ],
    }
    chart_path = paths["chart_payload_dir"] / "metrics_overview_payload.json"
    chart_path.write_text(json.dumps(chart_payload), encoding="utf-8")
    registry.write_snapshot_file(
        run_id,
        "data_lineage_manifest.json",
        {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-data-lineage-manifest-v1",
            "run_id": run_id,
            "lineage_status": "partial",
        },
    )

    table_path = paths["snapshot_dir"] / "future_metrics.parquet"
    pd.DataFrame(
        {
            "backtest_id": ["a", "b"],
            "future_artifact_score": [1.1, 1.4],
        }
    ).to_parquet(table_path, index=False)
    registry.write_artifact_manifest(
        run_id,
        {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-artifact-manifest-v1",
            "run_id": run_id,
            "artifacts": [
                {
                    "artifact_type": "metricstracker_parquet",
                    "path": str(table_path),
                    "required_by_pages": ["metrics_explorer"],
                    "status": "ready",
                    "generated_at": "2026-05-15T00:00:00+08:00",
                    "content_contract": "test",
                    "source_stage": "metricstracker",
                    "optional": False,
                    "notes": None,
                }
            ],
        },
    )

    output_path = service.ensure_ai_readable_output(run_id, module="autorunner")
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["contract_id"] == "lo2cin4bt-app-ai-readable-output-v1"
    assert payload["source_payloads"]["metrics_overview_payload"]["rows"][0][
        "future_metric_score"
    ] == 2.5
    assert (
        payload["snapshot_payloads"]["data_lineage_manifest"]["contract_id"]
        == "lo2cin4bt-app-data-lineage-manifest-v1"
    )
    field_paths = {item["path"] for item in payload["metric_field_catalog"]["fields"]}
    assert (
        "source_payloads.metrics_overview_payload.rows[].future_metric_score"
        in field_paths
    )
    table_profile = payload["artifact_table_profiles"][0]
    assert "future_artifact_score" in table_profile["columns"]
    assert table_profile["numeric_summary"]["future_artifact_score"]["max"] == 1.4

    manifest = registry.load_artifact_manifest(run_id)
    assert any(
        item.get("artifact_type") == "ai_readable_output_json"
        for item in manifest["artifacts"]
    )
    ai_artifact = next(
        item
        for item in manifest["artifacts"]
        if item.get("artifact_type") == "ai_readable_output_json"
    )
    assert "required_by_pages" in ai_artifact
    assert "generated_at" in ai_artifact
    assert "description" not in ai_artifact
    schema = json.loads(
        (REPO_ROOT / "app" / "contracts" / "artifact-manifest-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(manifest)
    registry_entry = registry.load_registry_entry(run_id)
    assert registry_entry["artifacts_total"] == 2
    assert registry_entry["artifacts_ready"] == 2


def test_latest_runs_summary_keeps_lineage_registry_fields(tmp_path: Path) -> None:
    registry = AppRegistry(tmp_path)
    run_id = "20260515_lineage_summary"
    paths = registry.build_run_paths(run_id)

    registry.write_registry_entry(
        {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-run-registry-v1",
            "run_id": run_id,
            "module": "autorunner",
            "entrypoint": "test",
            "status": "completed",
            "created_at": "2026-05-15T00:00:00+08:00",
            "completed_at": "2026-05-15T00:00:01+08:00",
            "config_filename": "example.json",
            "symbol": "QQQ",
            "frequency": "1D",
            "strategy_mode": "auto",
            "semantic_label": "example",
            "display_label": "Example",
            "run_type": "test",
            "data_lineage_manifest_path": str(paths["data_lineage_manifest"]),
            "lineage_status": "partial",
            "warning_count": 0,
            "error_count": 0,
        }
    )

    summary = registry.list_runs()[0]
    assert summary["data_lineage_manifest_path"] == str(paths["data_lineage_manifest"])
    assert summary["lineage_status"] == "partial"


def test_run_history_reads_registry_beyond_latest_runs_cap(tmp_path: Path) -> None:
    registry = AppRegistry(tmp_path)
    for index in range(105):
        registry.write_registry_entry(
            {
                "schema_version": "1.0",
                "contract_id": "lo2cin4bt-app-run-registry-v1",
                "run_id": f"20260604_history_{index:03d}",
                "module": "autorunner",
                "entrypoint": "test",
                "status": "completed",
                "created_at": f"2026-06-04T00:{index:02d}:00+08:00",
                "completed_at": f"2026-06-04T00:{index:02d}:01+08:00",
                "config_filename": "strategy-run-history.json",
            }
        )

    assert len(registry.list_runs(module="autorunner")) == 100
    assert len(registry.list_run_history(module="autorunner")) == 105


def test_ai_readable_output_does_not_profile_manifest_paths_outside_repo(tmp_path: Path) -> None:
    registry = AppRegistry(tmp_path)
    service = AppPayloadService(tmp_path, registry)
    run_id = "20260515_malicious_manifest"
    outside = tmp_path.parent / "outside_payload.csv"
    outside.write_text("secret\n1\n", encoding="utf-8")

    registry.write_registry_entry(
        {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-run-registry-v1",
            "run_id": run_id,
            "module": "autorunner",
            "entrypoint": "test",
            "status": "completed",
            "created_at": "2026-05-15T00:00:00+08:00",
            "completed_at": "2026-05-15T00:00:01+08:00",
            "config_filename": "example.json",
        }
    )
    registry.write_stage_status(run_id, {"schema_version": "1.0", "status": "completed", "stages": []})
    registry.write_artifact_manifest(
        run_id,
        {
            "schema_version": "1.0",
            "contract_id": "lo2cin4bt-app-artifact-manifest-v1",
            "run_id": run_id,
            "artifacts": [
                {
                    "artifact_type": "metricstracker_parquet",
                    "path": str(outside),
                    "required_by_pages": ["metrics_explorer"],
                    "status": "ready",
                    "generated_at": "2026-05-15T00:00:00+08:00",
                    "content_contract": "test",
                    "source_stage": "metricstracker",
                    "optional": False,
                    "notes": None,
                }
            ],
        },
    )

    output_path = service.ensure_ai_readable_output(run_id, module="autorunner")
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["artifact_index"][0]["exists"] is False
    assert payload["artifact_table_profiles"] == []
