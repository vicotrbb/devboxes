from fastapi.testclient import TestClient

from devboxes_controller.app import create_app
from devboxes_controller.config import Settings

from .fakes import FakeManager


def app_client() -> TestClient:
    settings = Settings(
        access_token="test-access-token-at-least-32-characters",
        cookie_secure=False,
        cleanup_interval_seconds=3600,
    )
    return TestClient(create_app(settings, FakeManager()))  # type: ignore[arg-type]


def test_browser_login_and_dashboard_session() -> None:
    with app_client() as client:
        assert client.get("/", follow_redirects=False).status_code == 303
        assert client.get("/docs", follow_redirects=False).status_code == 303
        response = client.post(
            "/auth/login",
            data={"token": "test-access-token-at-least-32-characters"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert dashboard.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in dashboard.headers["content-security-policy"]
        assert dashboard.headers["x-content-type-options"] == "nosniff"
        assert "Kubernetes connected" in dashboard.text
        assert "cluster default storage" in dashboard.text
        styles = client.get("/static/styles.css?v=0.1.2")
        assert "[hidden]" in styles.text
        assert "display: none !important" in styles.text
        payload = client.get("/api/v1/devboxes").json()
        assert [item["name"] for item in payload["items"]] == [
            "atlas",
            "paperclip",
            "nightly",
        ]


def test_system_endpoints_report_health_readiness_and_metrics() -> None:
    with app_client() as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json() == {"status": "ready"}
        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert 'devboxes_total{state="ready"} 1.0' in metrics.text
        assert 'devboxes_total{state="stopped"} 1.0' in metrics.text


def test_invalid_login_and_authenticated_logout() -> None:
    with app_client() as client:
        rejected = client.post("/auth/login", data={"token": "not-the-token"})
        assert rejected.status_code == 401
        assert "was not accepted" in rejected.text

        client.post(
            "/auth/login",
            data={"token": "test-access-token-at-least-32-characters"},
        )
        csrf = client.cookies.get("devboxes_csrf")
        response = client.post("/auth/logout", headers={"X-Devboxes-CSRF": csrf})
        assert response.status_code == 204
        assert client.get("/", follow_redirects=False).status_code == 303


def test_documentation_teaches_the_terminal_workflow() -> None:
    with app_client() as client:
        client.post("/auth/login", data={"token": "test-access-token-at-least-32-characters"})
        response = client.get("/docs")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "Documentation · Devboxes" in response.text
        assert "devbox create atlas --preset medium --ttl 24" in response.text
        assert "Disconnect SSH" in response.text
        assert "Delete with <code>--purge</code>" in response.text
        assert "devbox ssh atlas -- -L 3000:127.0.0.1:3000" in response.text
        assert 'aria-current="page">Docs' in response.text
        assert "on Kubernetes" in response.text
        assert "http://127.0.0.1:8000" in response.text


def test_cli_bearer_can_create_a_devbox() -> None:
    with app_client() as client:
        response = client.post(
            "/api/v1/devboxes",
            headers={"Authorization": "Bearer test-access-token-at-least-32-characters"},
            json={
                "name": "compiler",
                "preset": "small",
                "ttl_hours": 24,
                "repository": "owner/compiler",
            },
        )
        assert response.status_code == 201
        assert response.json()["name"] == "compiler"
        assert response.json()["state"] == "starting"


def test_cli_bearer_can_inspect_and_control_a_devbox() -> None:
    headers = {"Authorization": "Bearer test-access-token-at-least-32-characters"}
    with app_client() as client:
        assert client.get("/api/v1/whoami", headers=headers).json() == {
            "user": "operator",
            "mode": "bearer",
        }
        assert client.get("/api/v1/devboxes/atlas", headers=headers).status_code == 200

        stopped = client.post("/api/v1/devboxes/atlas/stop", headers=headers)
        assert stopped.json()["state"] == "stopped"

        started = client.post("/api/v1/devboxes/atlas/start", headers=headers)
        assert started.json()["state"] == "starting"

        deleted = client.delete("/api/v1/devboxes/atlas?purge=true", headers=headers)
        assert deleted.json() == {
            "name": "atlas",
            "purged": True,
            "message": "atlas deleted",
        }


def test_api_maps_conflicts_and_missing_names() -> None:
    headers = {"Authorization": "Bearer test-access-token-at-least-32-characters"}
    with app_client() as client:
        conflict = client.post(
            "/api/v1/devboxes",
            headers=headers,
            json={"name": "atlas"},
        )
        assert conflict.status_code == 409
        assert "already exists" in conflict.json()["detail"]

        missing = client.get("/api/v1/devboxes/missing", headers=headers)
        assert missing.status_code == 404
        assert "was not found" in missing.json()["detail"]


def test_api_requires_authentication() -> None:
    with app_client() as client:
        assert client.get("/api/v1/devboxes").status_code == 401


def test_api_rejects_invalid_path_names_before_kubernetes() -> None:
    with app_client() as client:
        response = client.get(
            "/api/v1/devboxes/Invalid",
            headers={"Authorization": "Bearer test-access-token-at-least-32-characters"},
        )
        assert response.status_code == 422


def test_https_responses_enable_hsts() -> None:
    with app_client() as client:
        response = client.get("https://testserver/login")

        assert response.headers["strict-transport-security"] == (
            "max-age=31536000; includeSubDomains"
        )
