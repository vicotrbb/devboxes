import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from devboxes_controller.app import create_app
from devboxes_controller.auth import Authenticator
from devboxes_controller.config import Settings
from devboxes_controller.insights_service import InsightsService

from .fakes import FakeManager
from .test_app import browser_login

FIXTURES = Path(__file__).parent / "fixtures"
INSTANCE = "88888888-8888-4888-8888-888888888888"
MASTER_TOKEN = "test-access-token-at-least-32-characters"


def enabled_app(tmp_path: Path) -> tuple[TestClient, Settings]:
    settings = Settings(
        access_token=MASTER_TOKEN,
        cookie_secure=False,
        cleanup_interval_seconds=3600,
        insights_enabled=True,
        insights_db_path=str(tmp_path / "insights.db"),
    )
    service = InsightsService(settings)
    return TestClient(create_app(settings, FakeManager(), service)), settings  # type: ignore[arg-type]


def otlp_batch(batch_id: str = "a" * 64) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "batch_id": batch_id,
            "sent_at": datetime.now(UTC).isoformat(),
            "collector": "otel",
            "kind": "otlp",
            "payload": json.loads((FIXTURES / "claude-2.1.205-otlp.json").read_text()),
        }
    ).encode()


def test_insights_dashboard_disabled_state_and_read_contract() -> None:
    settings = Settings(
        access_token=MASTER_TOKEN,
        cookie_secure=False,
        cleanup_interval_seconds=3600,
    )
    with TestClient(create_app(settings, FakeManager())) as client:  # type: ignore[arg-type]
        browser_login(client)
        dashboard = client.get("/insights")
        assert dashboard.status_code == 200
        assert "Insights is off" in dashboard.text
        assert 'aria-current="page">Insights' in dashboard.text
        assert 'data-insights-enabled="false"' in dashboard.text

        headers = {"Authorization": f"Bearer {MASTER_TOKEN}"}
        summary = client.get("/api/v1/insights/summary", headers=headers)
        assert summary.status_code == 200
        assert summary.json()["enabled"] is False
        assert summary.json()["coverage"]["status"] == "disabled"
        assert client.get("/api/v1/insights/capabilities", headers=headers).status_code == 200
        assert client.post("/internal/v1/insights/batches", content=b"{}").status_code == 404
        assert client.delete("/api/v1/insights/devboxes/atlas", headers=headers).status_code == 409


def test_authenticated_ingest_drives_summary_series_export_and_purge(tmp_path: Path) -> None:
    client, settings = enabled_app(tmp_path)
    credential = Authenticator(settings).issue_insights_token(INSTANCE, "atlas")
    ingest_headers = {
        "Authorization": f"Bearer {credential}",
        "Content-Type": "application/json",
        "Content-Encoding": "gzip",
    }
    api_headers = {"Authorization": f"Bearer {MASTER_TOKEN}"}
    with client:
        accepted = client.post(
            "/internal/v1/insights/batches",
            content=gzip.compress(otlp_batch()),
            headers=ingest_headers,
        )
        assert accepted.status_code == 200
        assert accepted.json() == {"accepted": True, "duplicate": False, "points": 7}

        duplicate = client.post(
            "/internal/v1/insights/batches",
            content=gzip.compress(otlp_batch()),
            headers=ingest_headers,
        )
        assert duplicate.json()["duplicate"] is True

        summary = client.get("/api/v1/insights/summary?since=24h", headers=api_headers)
        assert summary.status_code == 200
        assert summary.json()["data"]["ai"]["totals"]["tokens"] == 15
        grouped = client.get(
            f"/api/v1/insights/summary?since=24h&devbox=atlas&instance_id={INSTANCE}"
            "&group_by=provider",
            headers=api_headers,
        )
        assert grouped.status_code == 200
        assert grouped.json()["data"]["groups"][0]["key"] == "claude"
        assert grouped.json()["storage"]["database_bytes"] > 0
        series = client.get(
            "/api/v1/insights/timeseries?metric=tokens&since=24h",
            headers=api_headers,
        )
        assert series.status_code == 200
        assert series.json()["data"]["items"]
        assert (
            client.get(
                "/api/v1/insights/timeseries?metric=productivity_score",
                headers=api_headers,
            ).status_code
            == 422
        )
        assert client.get("/api/v1/insights/activity", headers=api_headers).status_code == 200
        capabilities = client.get("/api/v1/insights/capabilities", headers=api_headers)
        assert capabilities.json()["capabilities"]["codex"]["cost"]["supported"] is False

        exported_json = client.get("/api/v1/insights/export?format=json", headers=api_headers)
        assert exported_json.status_code == 200
        assert exported_json.headers["content-type"].startswith("application/json")
        exported_csv = client.get("/api/v1/insights/export?format=csv", headers=api_headers)
        assert exported_csv.status_code == 200
        assert exported_csv.text.startswith("category,provider,metric,value\n")
        assert "sensitive-user-hash" not in exported_csv.text
        exported_sqlite = client.get("/api/v1/insights/export?format=sqlite", headers=api_headers)
        assert exported_sqlite.status_code == 200
        assert exported_sqlite.content.startswith(b"SQLite format 3\x00")

        browser_login(client)
        dashboard = client.get("/insights")
        assert "Understand the work, not the worker" in dashboard.text
        assert 'data-insights-enabled="true"' in dashboard.text
        csrf = client.cookies.get("devboxes_csrf")
        purged = client.delete(
            "/api/v1/insights?box=atlas",
            headers={"X-Devboxes-CSRF": csrf},
        )
        assert purged.status_code == 200
        assert purged.json()["purged_instances"] == 1


def test_ingest_endpoint_accepts_only_scoped_metrics_json(tmp_path: Path) -> None:
    client, settings = enabled_app(tmp_path)
    credential = Authenticator(settings).issue_insights_token(INSTANCE, "atlas")
    with client:
        assert (
            client.post(
                "/internal/v1/insights/batches",
                content=otlp_batch(),
                headers={"Content-Type": "application/json"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/internal/v1/insights/batches",
                content=otlp_batch(),
                headers={
                    "Authorization": f"Bearer {MASTER_TOKEN}",
                    "Content-Type": "application/json",
                },
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/internal/v1/insights/batches",
                content=otlp_batch(),
                headers={
                    "Authorization": f"Bearer {credential}",
                    "Content-Type": "text/plain",
                },
            ).status_code
            == 415
        )
        invalid = client.post(
            "/internal/v1/insights/batches",
            content=b'{"private":"do-not-reflect"}',
            headers={
                "Authorization": f"Bearer {credential}",
                "Content-Type": "application/json",
            },
        )
        assert invalid.status_code == 400
        assert "do-not-reflect" not in invalid.text
        wrong_instance = json.loads(otlp_batch())
        wrong_instance["instance_id"] = "99999999-9999-4999-8999-999999999999"
        rejected = client.post(
            "/internal/v1/insights/batches",
            content=json.dumps(wrong_instance).encode(),
            headers={
                "Authorization": f"Bearer {credential}",
                "Content-Type": "application/json",
            },
        )
        assert rejected.status_code == 400


def test_insights_query_validation_and_authentication(tmp_path: Path) -> None:
    client, _ = enabled_app(tmp_path)
    headers = {"Authorization": f"Bearer {MASTER_TOKEN}"}
    with client:
        assert client.get("/api/v1/insights/summary").status_code == 401
        assert (
            client.get("/api/v1/insights/summary?since=1000d", headers=headers).status_code == 422
        )
        assert (
            client.get("/api/v1/insights/activity?cursor=invalid", headers=headers).status_code
            == 422
        )
        assert (
            client.get(
                "/api/v1/insights/summary?box=atlas&devbox=other",
                headers=headers,
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/v1/insights/summary?instance_id=not-a-uuid",
                headers=headers,
            ).status_code
            == 422
        )
        assert client.delete("/api/v1/insights", headers=headers).status_code == 422
