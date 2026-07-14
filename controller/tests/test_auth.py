import asyncio
import time
from urllib.parse import urlencode

import jwt
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from devboxes_controller.auth import (
    CLI_AUDIENCE,
    CLI_SCOPE,
    CLI_TOKEN_TYPE,
    CSRF_COOKIE,
    SESSION_COOKIE,
    Authenticator,
    AuthorizationCodeStore,
    is_loopback_redirect_uri,
    is_safe_login_next,
    pkce_s256,
    validate_authorization_request,
)
from devboxes_controller.config import Settings

MASTER_TOKEN = "test-access-token-at-least-32-characters"
SIGNING_KEY = "dedicated-cli-signing-key-at-least-32-characters"


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "access_token": MASTER_TOKEN,
        "cookie_secure": False,
        "session_ttl_seconds": 3600,
        "external_url": "https://devboxes.example.com",
        "cli_signing_key": SIGNING_KEY,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def authorization_parameters() -> dict[str, str]:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    return {
        "response_type": "code",
        "client_id": "devbox-cli",
        "redirect_uri": "http://127.0.0.1:49152/callback",
        "state": "state-value-with-at-least-thirty-two-bytes",
        "code_challenge": pkce_s256(verifier),
        "code_challenge_method": "S256",
    }


def test_session_round_trip() -> None:
    authenticator = Authenticator(settings())

    session, csrf = authenticator.issue_session()

    assert authenticator.validate_session(session) == csrf
    assert authenticator.validate_session(f"{session}tampered") is None


def test_master_cli_and_csrf_authentication_modes() -> None:
    authenticator = Authenticator(settings())
    cli_token, _ = authenticator.issue_cli_token("operator")
    app = FastAPI()

    @app.post("/checked")
    async def checked(request: Request):
        context = await authenticator.require(request)
        return {"mode": context.mode, "subject": context.subject}

    client = TestClient(app)
    master = client.post("/checked", headers={"Authorization": f"Bearer {MASTER_TOKEN}"})
    assert master.json() == {"mode": "master-bearer", "subject": "operator"}

    cli = client.post("/checked", headers={"Authorization": f"Bearer {cli_token}"})
    assert cli.json() == {"mode": "cli-bearer", "subject": "operator"}
    assert (
        client.post("/checked", headers={"Authorization": f"Bearer {cli_token}x"}).status_code
        == 401
    )

    session, csrf = authenticator.issue_session()
    client.cookies.set(SESSION_COOKIE, session)
    client.cookies.set(CSRF_COOKIE, csrf)
    assert client.post("/checked").status_code == 403
    browser = client.post("/checked", headers={"X-Devboxes-CSRF": csrf})
    assert browser.json()["mode"] == "browser-session"


def test_pkce_s256_matches_rfc_7636_vector() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"

    assert pkce_s256(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_authorization_request_requires_s256_and_numeric_loopback() -> None:
    parameters = authorization_parameters()
    request = validate_authorization_request(**parameters)
    assert request.redirect_uri == "http://127.0.0.1:49152/callback"

    assert is_loopback_redirect_uri("http://[::1]:49152/callback")
    for redirect_uri in [
        "https://127.0.0.1:49152/callback",
        "http://localhost:49152/callback",
        "http://127.0.0.1/callback",
        "http://127.0.0.1:80/callback",
        "http://127.0.0.1:49152/other",
        "http://127.0.0.1:49152/callback?code=bad",
        "http://user@127.0.0.1:49152/callback",
        "http://192.0.2.10:49152/callback",
    ]:
        assert not is_loopback_redirect_uri(redirect_uri)

    for field, value in [
        ("response_type", "token"),
        ("client_id", "other-client"),
        ("code_challenge_method", "plain"),
        ("state", "short"),
        ("code_challenge", "short"),
        ("redirect_uri", "https://evil.example/callback"),
    ]:
        invalid = dict(parameters)
        invalid[field] = value
        try:
            validate_authorization_request(**invalid)
        except Exception as error:
            assert "authorization request" in str(error)
        else:
            raise AssertionError(f"{field} was accepted")


def test_login_next_rejects_open_redirects_and_malformed_requests() -> None:
    parameters = authorization_parameters()
    safe = f"/auth/cli/authorize?{urlencode(parameters)}"

    assert is_safe_login_next("/")
    assert is_safe_login_next(safe)
    for candidate in [
        "https://evil.example/",
        "//evil.example/",
        "/auth/cli/authorize",
        f"/other?{urlencode(parameters)}",
        f"/auth/cli/authorize?{urlencode(parameters)}&client_id=duplicate",
        "/auth/cli/authorize?malformed",
    ]:
        assert not is_safe_login_next(candidate)


def test_authorization_codes_are_bound_single_use_and_concurrency_safe() -> None:
    async def exercise() -> None:
        store = AuthorizationCodeStore()
        verifier = "v" * 43
        code = await store.issue(
            client_id="devbox-cli",
            redirect_uri="http://127.0.0.1:49152/callback",
            code_challenge=pkce_s256(verifier),
            subject="operator",
        )
        assert (
            await store.consume(
                code=code,
                client_id="devbox-cli",
                redirect_uri="http://127.0.0.1:49152/callback",
                code_verifier="w" * 43,
            )
            is None
        )
        results = await asyncio.gather(
            *[
                store.consume(
                    code=code,
                    client_id="devbox-cli",
                    redirect_uri="http://127.0.0.1:49152/callback",
                    code_verifier=verifier,
                )
                for _ in range(8)
            ]
        )
        assert results.count("operator") == 1
        assert results.count(None) == 7

    asyncio.run(exercise())


def test_authorization_code_store_expires_prunes_and_bounds_records() -> None:
    class Clock:
        value = 1000.0

        def __call__(self) -> float:
            return self.value

    async def exercise() -> None:
        clock = Clock()
        store = AuthorizationCodeStore(ttl_seconds=120, maximum_codes=2, clock=clock)
        for index in range(3):
            await store.issue(
                client_id="devbox-cli",
                redirect_uri=f"http://127.0.0.1:{49152 + index}/callback",
                code_challenge=pkce_s256("v" * 43),
                subject="operator",
            )
        assert await store.size() == 2
        clock.value += 121
        assert await store.size() == 0

    asyncio.run(exercise())


def _custom_cli_token(**overrides: object) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": "https://devboxes.example.com",
        "aud": CLI_AUDIENCE,
        "sub": "operator",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
        "jti": "test-token-id",
        "token_type": CLI_TOKEN_TYPE,
        "scope": CLI_SCOPE,
    }
    claims.update(overrides)
    return jwt.encode(claims, SIGNING_KEY, algorithm="HS256", headers={"typ": "JWT"})


def test_cli_tokens_reject_expired_tampered_wrong_audience_and_wrong_type() -> None:
    authenticator = Authenticator(settings())
    valid = _custom_cli_token()

    assert authenticator.validate_cli_token(valid) == "operator"
    assert authenticator.validate_cli_token(f"{valid}tampered") is None
    assert authenticator.validate_cli_token(_custom_cli_token(exp=int(time.time()) - 60)) is None
    assert authenticator.validate_cli_token(_custom_cli_token(aud="other")) is None
    assert authenticator.validate_cli_token(_custom_cli_token(token_type="other")) is None
    assert authenticator.validate_cli_token(_custom_cli_token(scope="read-only")) is None
    assert authenticator.validate_cli_token("x" * 4097) is None


def test_default_signing_key_is_derived_and_rotation_revokes_tokens() -> None:
    first = Authenticator(settings(cli_signing_key=None))
    same = Authenticator(settings(cli_signing_key=None))
    rotated = Authenticator(
        settings(access_token="rotated-master-token-at-least-32-characters", cli_signing_key=None)
    )

    token, expires_in = first.issue_cli_token("operator")

    assert expires_in == 2_592_000
    assert same.validate_cli_token(token) == "operator"
    assert rotated.validate_cli_token(token) is None
