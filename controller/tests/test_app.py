from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi.testclient import TestClient

from devboxes_controller.app import create_app
from devboxes_controller.auth import pkce_s256
from devboxes_controller.config import GpuProfile, Settings

from .fakes import FakeManager


def app_client(settings: Settings | None = None) -> TestClient:
    settings = settings or Settings(
        access_token="test-access-token-at-least-32-characters",
        cookie_secure=False,
        cleanup_interval_seconds=3600,
    )
    return TestClient(create_app(settings, FakeManager()))  # type: ignore[arg-type]


def authorization_parameters(port: int = 49152) -> tuple[dict[str, str], str]:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    return (
        {
            "response_type": "code",
            "client_id": "devbox-cli",
            "redirect_uri": f"http://127.0.0.1:{port}/callback",
            "state": "state-value-with-at-least-thirty-two-bytes",
            "code_challenge": pkce_s256(verifier),
            "code_challenge_method": "S256",
        },
        verifier,
    )


def browser_login(client: TestClient, next_target: str = "/") -> None:
    response = client.post(
        "/auth/login",
        data={
            "token": "test-access-token-at-least-32-characters",
            "next": next_target,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def approve_cli(client: TestClient, port: int = 49152) -> tuple[str, str, dict[str, str]]:
    parameters, verifier = authorization_parameters(port)
    page = client.get(f"/auth/cli/authorize?{urlencode(parameters)}")
    assert page.status_code == 200
    assert "Allow the Devbox CLI" in page.text
    csrf = client.cookies.get("devboxes_csrf")
    response = client.post(
        "/auth/cli/authorize",
        data={**parameters, "csrf": csrf, "action": "approve"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    callback = parse_qs(urlsplit(response.headers["location"]).query)
    assert callback["state"] == [parameters["state"]]
    return callback["code"][0], verifier, parameters


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
        styles = client.get("/static/styles.css?v=0.4.0")
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
            "mode": "master-bearer",
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
        assert client.get("/api/v1/capabilities").status_code == 401


def test_gpu_capabilities_expose_only_safe_profile_metadata() -> None:
    settings = Settings(
        access_token="test-access-token-at-least-32-characters",
        cookie_secure=False,
        cleanup_interval_seconds=3600,
        gpu_enabled=True,
        gpu_default_profile="nvidia-l4",
        gpu_profiles=[
            GpuProfile(
                name="nvidia-l4",
                displayName="NVIDIA L4",
                description="One dedicated inference GPU",
                resourceName="nvidia.com/gpu",
                count=1,
                workspaceImage="private.example/devboxes-cuda:12.8",
                runtimeClassName="nvidia",
                supplementalGroups=[44],
            )
        ],
    )
    headers = {"Authorization": "Bearer test-access-token-at-least-32-characters"}

    with app_client(settings) as client:
        response = client.get("/api/v1/capabilities", headers=headers)
        created = client.post(
            "/api/v1/devboxes",
            headers=headers,
            json={"name": "inference", "gpu": {"profile": "nvidia-l4"}},
        )
        browser_login(client)
        dashboard = client.get("/")
        documentation = client.get("/docs")

    assert response.status_code == 200
    assert response.json() == {
        "gpu": {
            "enabled": True,
            "default_profile": "nvidia-l4",
            "profiles": [
                {
                    "name": "nvidia-l4",
                    "display_name": "NVIDIA L4",
                    "description": "One dedicated inference GPU",
                    "resource_name": "nvidia.com/gpu",
                    "count": 1,
                    "default": True,
                }
            ],
        }
    }
    assert "workspaceImage" not in response.text
    assert "runtimeClassName" not in response.text
    assert "supplementalGroups" not in response.text
    assert created.status_code == 201
    assert created.json()["gpu"] == {
        "profile": "nvidia-l4",
        "display_name": "NVIDIA L4",
        "resource_name": "nvidia.com/gpu",
        "count": 1,
    }
    assert '<option value="nvidia-l4">' in dashboard.text
    assert "NVIDIA L4 · 1 unit · default" in dashboard.text
    assert "GPU profiles" in dashboard.text
    assert "Use an operator-approved GPU" in documentation.text
    assert "devbox create inference --gpu --ssh" in documentation.text


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


def test_unauthenticated_cli_authorization_returns_through_login() -> None:
    parameters, _ = authorization_parameters()
    next_target = f"/auth/cli/authorize?{urlencode(parameters)}"
    with app_client() as client:
        response = client.get(next_target, follow_redirects=False)
        assert response.status_code == 303
        login_location = response.headers["location"]
        assert login_location.startswith("/login?")
        assert parse_qs(urlsplit(login_location).query)["next"] == [next_target]

        login_page = client.get(login_location)
        assert login_page.status_code == 200
        assert next_target.replace("&", "&amp;") in login_page.text

        browser_login(client, next_target)
        approval = client.get(next_target)
        assert approval.status_code == 200
        assert "Approve Devbox CLI" in approval.text


def test_login_rejects_open_redirects() -> None:
    with app_client() as client:
        for target in ["https://evil.example/", "//evil.example/", "/auth/cli/authorize"]:
            assert client.get("/login", params={"next": target}).status_code == 400
            response = client.post(
                "/auth/login",
                data={
                    "token": "test-access-token-at-least-32-characters",
                    "next": target,
                },
            )
            assert response.status_code == 400


def test_cli_approval_exchange_whoami_replay_and_no_store() -> None:
    with app_client() as client:
        browser_login(client)
        code, verifier, parameters = approve_cli(client)
        exchange_payload = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": parameters["client_id"],
            "redirect_uri": parameters["redirect_uri"],
        }
        exchange = client.post("/api/v1/auth/cli/token", json=exchange_payload)
        assert exchange.status_code == 200
        assert exchange.headers["cache-control"] == "no-store"
        payload = exchange.json()
        assert payload["token_type"] == "Bearer"
        assert payload["scope"] == "devboxes:manage"
        assert payload["expires_in"] == 2_592_000
        assert "refresh_token" not in payload
        cli_headers = {"Authorization": f"Bearer {payload['access_token']}"}
        assert client.get("/api/v1/whoami", headers=cli_headers).json() == {
            "user": "operator",
            "mode": "cli-bearer",
        }
        assert client.get("/api/v1/devboxes", headers=cli_headers).status_code == 200

        replay = client.post("/api/v1/auth/cli/token", json=exchange_payload)
        assert replay.status_code == 400
        assert replay.json() == {"detail": "Invalid authorization code exchange"}
        assert code not in replay.text
        assert "test-access-token" not in replay.text


def test_cli_denial_preserves_state_and_issues_no_code() -> None:
    parameters, _ = authorization_parameters()
    with app_client() as client:
        browser_login(client)
        client.get(f"/auth/cli/authorize?{urlencode(parameters)}")
        response = client.post(
            "/auth/cli/authorize",
            data={
                **parameters,
                "csrf": client.cookies.get("devboxes_csrf"),
                "action": "deny",
            },
            follow_redirects=False,
        )
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query == {"error": ["access_denied"], "state": [parameters["state"]]}


def test_cli_approval_enforces_csrf_and_authorization_parameters() -> None:
    parameters, _ = authorization_parameters()
    with app_client() as client:
        browser_login(client)
        rejected = client.post(
            "/auth/cli/authorize",
            data={**parameters, "csrf": "wrong-csrf-token-value", "action": "approve"},
        )
        assert rejected.status_code == 403

        for field, value in [
            ("response_type", "token"),
            ("client_id", "other"),
            ("code_challenge_method", "plain"),
            ("redirect_uri", "https://evil.example/callback"),
        ]:
            invalid = dict(parameters)
            invalid[field] = value
            response = client.get("/auth/cli/authorize", params=invalid)
            assert response.status_code == 400
            assert response.headers["cache-control"] == "no-store"


def test_cli_exchange_rejects_wrong_verifier_redirect_and_client_generically() -> None:
    with app_client() as client:
        browser_login(client)
        for index, (field, value) in enumerate(
            [
                ("code_verifier", "w" * 43),
                ("redirect_uri", "http://127.0.0.1:50000/callback"),
                ("client_id", "other-client"),
            ]
        ):
            code, verifier, parameters = approve_cli(client, 49152 + index)
            payload = {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "client_id": parameters["client_id"],
                "redirect_uri": parameters["redirect_uri"],
            }
            payload[field] = value
            response = client.post("/api/v1/auth/cli/token", json=payload)
            assert response.status_code == 400
            assert response.json() == {"detail": "Invalid authorization code exchange"}
            assert code not in response.text


def test_cli_exchange_rejects_malformed_payload_without_reflecting_secrets() -> None:
    secret = "authorization-code-that-must-never-be-reflected"
    with app_client() as client:
        for payload in [
            {"code": secret},
            {"code": secret, "code_verifier": "too-short"},
            [secret],
        ]:
            response = client.post("/api/v1/auth/cli/token", json=payload)
            assert response.status_code == 400
            assert response.json() == {"detail": "Invalid authorization code exchange"}
            assert secret not in response.text

        invalid_json = client.post(
            "/api/v1/auth/cli/token",
            content=f'{{"code":"{secret}"',
            headers={"Content-Type": "application/json"},
        )
        assert invalid_json.status_code == 400
        assert invalid_json.json() == {"detail": "Invalid authorization code exchange"}
        assert secret not in invalid_json.text
