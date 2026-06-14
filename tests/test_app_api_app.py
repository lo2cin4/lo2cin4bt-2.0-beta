from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.api.service import AppAPIService


REPO_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.smoke


def test_app_api_routes_smoke() -> None:
    client = TestClient(create_app(REPO_ROOT))

    assert client.get("/api/app/health").status_code == 200
    command_center = client.get("/api/app/command-center")
    assert command_center.status_code == 200
    assert "recent_runs" in command_center.json()

    wfa_runs = client.get("/api/app/wfa/runs")
    assert wfa_runs.status_code == 200
    rows = wfa_runs.json()
    if rows:
        dashboard = client.get(f"/api/app/wfa/{rows[0]['run_id']}/dashboard")
        assert dashboard.status_code == 200

    configs = client.get("/api/app/run-center/configs")
    assert configs.status_code == 200
    payload = configs.json()
    assert "autorunner" in payload
    assert "wfa" in payload
    assert "statanalyser" in payload


def test_parameter_matrix_cannot_submit_shortlist_to_wfa() -> None:
    client = TestClient(create_app(REPO_ROOT))

    response = client.post(
        "/api/app/metrics/example-run/parameter-matrix/send-to-wfa",
        json={"candidate_rows": [{"params": {"vix_max": 33}}]},
    )

    assert response.status_code in {404, 405}


def test_workspace_target_path_maps_run_center_config_folders(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)

    assert service.workspace_target_path("autorunner") == tmp_path.resolve() / "workspace" / "runs"
    assert service.workspace_target_path("wfa") == tmp_path.resolve() / "workspace" / "wfa"
    assert service.local_folder_target_path("autorunner-output") == tmp_path.resolve() / "outputs" / "app" / "run_snapshots"
    assert service.local_folder_target_path("wfa-output") == tmp_path.resolve() / "outputs" / "app" / "run_snapshots"

    with pytest.raises(ValueError):
        service.workspace_target_path("unknown")


def test_output_target_path_opens_latest_artifact_folder(tmp_path: Path) -> None:
    service = AppAPIService(tmp_path)
    run_id = "20260516_example"
    artifact_path = (
        tmp_path
        / "outputs"
        / "app"
        / "run_snapshots"
        / run_id
        / "managed_artifacts"
        / "portfolio"
        / "result.parquet"
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"PAR1")
    service.registry.write_registry_entry(
        {
            "run_id": run_id,
            "module": "autorunner",
            "status": "completed",
            "created_at": "2026-05-16T01:00:00+08:00",
            "config_filename": "example.json",
        }
    )
    service.registry.write_artifact_manifest(
        run_id,
        {
            "artifacts": [
                {
                    "artifact_type": "portfolio_equity_curve_parquet",
                    "source_stage": "backtester",
                    "status": "ready",
                    "path": str(artifact_path),
                }
            ]
        },
    )

    assert service.local_folder_target_path("autorunner-output") == artifact_path.parent


def test_workspace_open_route_uses_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called: dict[str, str] = {}

    def fake_open(self: AppAPIService, target: str) -> dict[str, str]:
        called["target"] = target
        return {
            "status": "opened",
            "target": target,
            "path": str(tmp_path / "workspace" / "runs"),
            "opener": "test",
        }

    monkeypatch.setattr(AppAPIService, "open_workspace_target", fake_open)
    client = TestClient(create_app(tmp_path))

    response = client.post("/api/app/workspace/open", json={"target": "autorunner"})

    assert response.status_code == 200
    assert response.json()["opener"] == "test"
    assert called["target"] == "autorunner"


def test_folder_open_route_accepts_output_targets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called: dict[str, str] = {}

    def fake_open(self: AppAPIService, target: str) -> dict[str, str]:
        called["target"] = target
        return {
            "status": "opened",
            "target": target,
            "path": str(tmp_path / "outputs" / "app"),
            "opener": "test",
        }

    monkeypatch.setattr(AppAPIService, "open_local_folder_target", fake_open)
    client = TestClient(create_app(tmp_path))

    response = client.post("/api/app/folders/open", json={"target": "wfa-output"})

    assert response.status_code == 200
    assert response.json()["path"].endswith(str(Path("outputs") / "app"))
    assert called["target"] == "wfa-output"


def test_frontend_static_assets_can_appear_after_app_start(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    client = TestClient(create_app(repo))

    missing = client.get("/assets/app.js")
    assert missing.status_code == 404

    dist = repo / "plotter" / "web" / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<script type="module" src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    (assets / "app.js").write_text("window.__lo2cin4bt_test = true;", encoding="utf-8")

    index = client.get("/")
    assert index.status_code == 200
    assert "/assets/app.js" in index.text

    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert "window.__lo2cin4bt_test" in asset.text
