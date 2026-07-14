"""Authenticate bearer clients, browser sessions, and native CLI authorization."""

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qs, urlsplit

import jwt
from fastapi import HTTPException, Request, status
from jwt import InvalidTokenError

from .config import Settings

SESSION_COOKIE = "devboxes_session"
CSRF_COOKIE = "devboxes_csrf"
CSRF_HEADER = "X-Devboxes-CSRF"
CLI_CLIENT_ID: Final = "devbox-cli"
CLI_AUDIENCE: Final = "devbox-cli"
CLI_TOKEN_TYPE: Final = "devboxes-cli-v1"  # noqa: S105 - public token type claim
CLI_SCOPE: Final = "devboxes:manage"
CLI_CALLBACK_PATH: Final = "/callback"
CLI_RESPONSE_TYPE: Final = "code"
PKCE_METHOD: Final = "S256"
_AUTHORIZATION_PATH: Final = "/auth/cli/authorize"
_STATE_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_PKCE_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_SIGNING_DOMAIN = b"devboxes:cli-token-signing-key:v1"
_INSIGHTS_SIGNING_DOMAIN = b"devboxes:insights-ingest-signing-key:v1"
_INSIGHTS_TOKEN_RE = re.compile(
    r"^v1\.([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\."
    r"([a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?)\.([A-Za-z0-9_-]{43})$"
)


@dataclass(frozen=True)
class AuthContext:
    """Describe the authentication mechanism accepted for a request."""

    mode: str
    subject: str


@dataclass(frozen=True)
class AuthorizationRequest:
    """Hold one validated native-app authorization request."""

    response_type: str
    client_id: str
    redirect_uri: str
    state: str
    code_challenge: str
    code_challenge_method: str


@dataclass(frozen=True)
class _AuthorizationCode:
    client_id: str
    redirect_uri: str
    code_challenge: str
    subject: str
    expires_at: float


class AuthorizationCodeStore:
    """Issue hashed, bounded, expiring, atomically consumed authorization codes."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 120,
        maximum_codes: int = 1024,
        clock: object = time.time,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._maximum_codes = maximum_codes
        self._clock = clock
        self._codes: OrderedDict[str, _AuthorizationCode] = OrderedDict()
        self._lock = asyncio.Lock()

    async def issue(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        subject: str,
    ) -> str:
        """Create an opaque code while retaining only its SHA-256 digest."""
        code = secrets.token_urlsafe(32)
        now = self._now()
        record = _AuthorizationCode(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            subject=subject,
            expires_at=now + self._ttl_seconds,
        )
        async with self._lock:
            self._prune(now)
            while len(self._codes) >= self._maximum_codes:
                self._codes.popitem(last=False)
            self._codes[_code_digest(code)] = record
        return code

    async def consume(
        self,
        *,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> str | None:
        """Atomically validate and consume a code, returning its subject."""
        if not _CODE_RE.fullmatch(code) or not _PKCE_RE.fullmatch(code_verifier):
            return None
        now = self._now()
        digest = _code_digest(code)
        async with self._lock:
            self._prune(now)
            record = self._codes.get(digest)
            if record is None:
                return None
            challenge = pkce_s256(code_verifier)
            if not (
                hmac.compare_digest(record.client_id, client_id)
                and hmac.compare_digest(record.redirect_uri, redirect_uri)
                and hmac.compare_digest(record.code_challenge, challenge)
            ):
                return None
            del self._codes[digest]
            return record.subject

    async def size(self) -> int:
        """Return the pruned code count for diagnostics and tests."""
        async with self._lock:
            self._prune(self._now())
            return len(self._codes)

    def _now(self) -> float:
        return float(self._clock())  # type: ignore[operator]

    def _prune(self, now: float) -> None:
        expired = [digest for digest, code in self._codes.items() if code.expires_at <= now]
        for digest in expired:
            del self._codes[digest]


class Authenticator:
    """Issue and validate controller credentials without storing browser sessions."""

    def __init__(self, settings: Settings) -> None:
        self._token = settings.access_token.get_secret_value().encode()
        self._session_ttl = settings.session_ttl_seconds
        self._issuer = settings.external_url
        self._subject = settings.display_name
        self._cli_token_ttl = settings.cli_token_ttl_seconds
        configured_signing_key = (
            settings.cli_signing_key.get_secret_value().encode()
            if settings.cli_signing_key is not None
            else None
        )
        self._cli_signing_key = (
            configured_signing_key
            or hmac.new(self._token, _SIGNING_DOMAIN, hashlib.sha256).digest()
        )
        configured_insights_key = (
            settings.insights_signing_key.get_secret_value().encode()
            if settings.insights_signing_key is not None
            else None
        )
        self._insights_signing_key = (
            configured_insights_key
            or hmac.new(self._token, _INSIGHTS_SIGNING_DOMAIN, hashlib.sha256).digest()
        )

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

    def issue_cli_token(self, subject: str) -> tuple[str, int]:
        """Issue a signed, scoped, expiring CLI bearer token."""
        now = int(time.time())
        claims = {
            "iss": self._issuer,
            "aud": CLI_AUDIENCE,
            "sub": subject,
            "iat": now,
            "nbf": now,
            "exp": now + self._cli_token_ttl,
            "jti": secrets.token_urlsafe(18),
            "token_type": CLI_TOKEN_TYPE,
            "scope": CLI_SCOPE,
        }
        token = jwt.encode(
            claims,
            self._cli_signing_key,
            algorithm="HS256",
            headers={"typ": "JWT"},
        )
        return token, self._cli_token_ttl

    def validate_cli_token(self, candidate: str) -> str | None:
        """Validate a CLI token and return its subject."""
        if not 64 <= len(candidate) <= 4096:
            return None
        try:
            header = jwt.get_unverified_header(candidate)
            if header.get("typ") != "JWT" or header.get("alg") != "HS256":
                return None
            claims = jwt.decode(
                candidate,
                self._cli_signing_key,
                algorithms=["HS256"],
                audience=CLI_AUDIENCE,
                issuer=self._issuer,
                options={
                    "require": [
                        "iss",
                        "aud",
                        "sub",
                        "iat",
                        "nbf",
                        "exp",
                        "jti",
                        "token_type",
                        "scope",
                    ]
                },
                leeway=30,
            )
            issued_at = int(claims["iat"])
            expires_at = int(claims["exp"])
            if (
                claims.get("token_type") != CLI_TOKEN_TYPE
                or claims.get("scope") != CLI_SCOPE
                or not isinstance(claims.get("sub"), str)
                or not claims["sub"]
                or expires_at <= issued_at
                or expires_at - issued_at > self._cli_token_ttl
                or issued_at > int(time.time()) + 30
            ):
                return None
            return str(claims["sub"])
        except (InvalidTokenError, TypeError, ValueError):
            return None

    def issue_insights_token(self, instance_id: str, box_name: str) -> str:
        """Issue a deterministic, write-only token scoped to one retained instance."""
        payload = f"v1.{instance_id}.{box_name}"
        signature = hmac.new(
            self._insights_signing_key,
            payload.encode(),
            hashlib.sha256,
        ).digest()
        return f"{payload}.{_b64(signature)}"

    def validate_insights_token(self, candidate: str) -> tuple[str, str] | None:
        """Validate an ingest-only credential and return its authoritative scope."""
        match = _INSIGHTS_TOKEN_RE.fullmatch(candidate)
        if match is None:
            return None
        instance_id, box_name, encoded_signature = match.groups()
        payload = f"v1.{instance_id}.{box_name}"
        try:
            signature = _unb64(encoded_signature)
        except ValueError:
            return None
        expected = hmac.new(
            self._insights_signing_key,
            payload.encode(),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected):
            return None
        return instance_id, box_name

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
        """Require a master/CLI bearer token or a CSRF-protected browser session."""
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            candidate = authorization.removeprefix("Bearer ").strip()
            if self.validate_access_token(candidate):
                return AuthContext(mode="master-bearer", subject=self._subject)
            subject = self.validate_cli_token(candidate)
            if subject is not None:
                return AuthContext(mode="cli-bearer", subject=subject)
            raise _unauthorized()

        csrf = self.browser_csrf(request)
        if csrf is None:
            raise _unauthorized()
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied_csrf = request.headers.get(CSRF_HEADER, "")
            if not supplied_csrf or not hmac.compare_digest(supplied_csrf, csrf):
                raise _csrf_error()
        return AuthContext(mode="browser-session", subject=self._subject)

    def browser_csrf(self, request: Request) -> str | None:
        """Return the CSRF value when both browser-session cookies agree."""
        session = request.cookies.get(SESSION_COOKIE)
        cookie_csrf = request.cookies.get(CSRF_COOKIE, "")
        if not session or not cookie_csrf:
            return None
        csrf = self.validate_session(session)
        if csrf is None or not hmac.compare_digest(cookie_csrf, csrf):
            return None
        return csrf

    def require_form_csrf(self, request: Request, supplied_csrf: str) -> AuthContext:
        """Require a browser session and matching HTML form CSRF token."""
        csrf = self.browser_csrf(request)
        if csrf is None:
            raise _unauthorized()
        if not supplied_csrf or not hmac.compare_digest(supplied_csrf, csrf):
            raise _csrf_error()
        return AuthContext(mode="browser-session", subject=self._subject)

    def browser_session_valid(self, request: Request) -> bool:
        """Return whether a request carries a valid browser session."""
        return self.browser_csrf(request) is not None


def validate_authorization_request(
    *,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    code_challenge_method: str,
) -> AuthorizationRequest:
    """Validate the fixed native CLI client and its loopback redirect."""
    if response_type != CLI_RESPONSE_TYPE or client_id != CLI_CLIENT_ID:
        raise _oauth_error("invalid authorization request")
    if code_challenge_method != PKCE_METHOD or not _PKCE_RE.fullmatch(code_challenge):
        raise _oauth_error("invalid authorization request")
    if not _STATE_RE.fullmatch(state):
        raise _oauth_error("invalid authorization request")
    if not is_loopback_redirect_uri(redirect_uri):
        raise _oauth_error("invalid authorization request")
    return AuthorizationRequest(
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )


def is_loopback_redirect_uri(candidate: str) -> bool:
    """Return whether a URI is an exact numeric HTTP loopback callback."""
    if len(candidate) > 256:
        return False
    try:
        parsed = urlsplit(candidate)
        host = parsed.hostname
        address = ipaddress.ip_address(host) if host is not None else None
        port = parsed.port
    except (ValueError, UnicodeError):
        return False
    return bool(
        parsed.scheme == "http"
        and address is not None
        and address.is_loopback
        and port is not None
        and 1024 <= port <= 65535
        and parsed.path == CLI_CALLBACK_PATH
        and not parsed.username
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def is_safe_login_next(candidate: str) -> bool:
    """Allow only the dashboard root or a fully valid CLI authorization request."""
    if candidate == "/":
        return True
    if len(candidate) > 2048:
        return False
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path != _AUTHORIZATION_PATH:
        return False
    try:
        query = parse_qs(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=6,
        )
    except ValueError:
        return False
    expected = {
        "response_type",
        "client_id",
        "redirect_uri",
        "state",
        "code_challenge",
        "code_challenge_method",
    }
    if set(query) != expected or any(len(values) != 1 for values in query.values()):
        return False
    try:
        validate_authorization_request(
            response_type=query["response_type"][0],
            client_id=query["client_id"][0],
            redirect_uri=query["redirect_uri"][0],
            state=query["state"][0],
            code_challenge=query["code_challenge"][0],
            code_challenge_method=query["code_challenge_method"][0],
        )
    except (HTTPException, ValueError):
        return False
    return True


def pkce_s256(verifier: str) -> str:
    """Derive the RFC 7636 S256 challenge for a verifier."""
    return _b64(hashlib.sha256(verifier.encode("ascii")).digest())


def _code_digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _csrf_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Missing or invalid CSRF token",
    )


def _oauth_error(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
