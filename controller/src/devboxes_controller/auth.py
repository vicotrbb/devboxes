"""Authenticate bearer clients and signed browser sessions."""

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from .config import Settings

SESSION_COOKIE = "devboxes_session"
CSRF_COOKIE = "devboxes_csrf"
CSRF_HEADER = "X-Devboxes-CSRF"


@dataclass(frozen=True)
class AuthContext:
    """Describe the authentication mechanism accepted for a request."""

    mode: str


class Authenticator:
    """Issue and validate controller credentials without storing server sessions."""

    def __init__(self, settings: Settings) -> None:
        self._token = settings.access_token.get_secret_value().encode()
        self._session_ttl = settings.session_ttl_seconds

    def issue_session(self) -> tuple[str, str]:
        """Issue a signed browser session and its matching CSRF token."""
        csrf = secrets.token_urlsafe(24)
        issued_at = str(int(time.time()))
        payload = f"{issued_at}:{csrf}".encode()
        signature = hmac.new(self._token, payload, hashlib.sha256).digest()
        return f"{_b64(payload)}.{_b64(signature)}", csrf

    def validate_access_token(self, candidate: str) -> bool:
        """Compare a candidate controller token in constant time."""
        return hmac.compare_digest(candidate.encode(), self._token)

    def validate_session(self, candidate: str) -> str | None:
        """Return the CSRF value for a valid unexpired session."""
        try:
            encoded_payload, encoded_signature = candidate.split(".", maxsplit=1)
            payload = _unb64(encoded_payload)
            signature = _unb64(encoded_signature)
            expected = hmac.new(self._token, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                return None
            issued_at_raw, csrf = payload.decode().split(":", maxsplit=1)
            issued_at = int(issued_at_raw)
        except (ValueError, UnicodeDecodeError):
            return None
        if issued_at > time.time() + 30 or time.time() - issued_at > self._session_ttl:
            return None
        return csrf

    async def require(self, request: Request) -> AuthContext:
        """Require bearer authentication or a valid browser session and CSRF token."""
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            candidate = authorization.removeprefix("Bearer ").strip()
            if self.validate_access_token(candidate):
                return AuthContext(mode="bearer")
            raise _unauthorized()

        session = request.cookies.get(SESSION_COOKIE)
        if not session:
            raise _unauthorized()
        csrf = self.validate_session(session)
        if csrf is None:
            raise _unauthorized()

        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied_csrf = request.headers.get(CSRF_HEADER, "")
            cookie_csrf = request.cookies.get(CSRF_COOKIE, "")
            if not (
                supplied_csrf
                and hmac.compare_digest(supplied_csrf, csrf)
                and hmac.compare_digest(cookie_csrf, csrf)
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Missing or invalid CSRF token",
                )
        return AuthContext(mode="session")

    def browser_session_valid(self, request: Request) -> bool:
        """Return whether a request carries a valid browser session."""
        session = request.cookies.get(SESSION_COOKIE)
        return bool(session and self.validate_session(session))


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
