from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from devboxes_controller.auth import CSRF_COOKIE, SESSION_COOKIE, Authenticator
from devboxes_controller.config import Settings


def settings() -> Settings:
    return Settings(
        access_token="test-access-token-at-least-32-characters",
        cookie_secure=False,
        session_ttl_seconds=3600,
    )


def test_session_round_trip() -> None:
    authenticator = Authenticator(settings())

    session, csrf = authenticator.issue_session()

    assert authenticator.validate_session(session) == csrf
    assert authenticator.validate_session(f"{session}tampered") is None


def test_bearer_and_csrf_authentication() -> None:
    authenticator = Authenticator(settings())
    app = FastAPI()

    # Exercise the bound dependency through a small middleware-style endpoint.
    @app.post("/checked")
    async def checked(request: Request):
        await authenticator.require(request)
        return {"ok": True}

    client = TestClient(app)
    assert (
        client.post(
            "/checked",
            headers={"Authorization": "Bearer test-access-token-at-least-32-characters"},
        ).status_code
        == 200
    )

    session, csrf = authenticator.issue_session()
    client.cookies.set(SESSION_COOKIE, session)
    client.cookies.set(CSRF_COOKIE, csrf)
    assert client.post("/checked").status_code == 403
    assert client.post("/checked", headers={"X-Devboxes-CSRF": csrf}).status_code == 200
