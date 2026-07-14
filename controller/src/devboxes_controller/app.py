"""Create and run the Devboxes HTTP application."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode, urlsplit, urlunsplit

import uvicorn
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi import Path as ApiPath
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from pydantic import ValidationError
from starlette.middleware.base import RequestResponseEndpoint

from . import __version__
from .auth import (
    CLI_CLIENT_ID,
    CLI_SCOPE,
    CSRF_COOKIE,
    SESSION_COOKIE,
    AuthContext,
    Authenticator,
    AuthorizationCodeStore,
    is_loopback_redirect_uri,
    is_safe_login_next,
    validate_authorization_request,
)
from .config import Settings, get_settings
from .manager import DevboxConflictError, DevboxManager, DevboxNotFoundError
from .models import (
    CliTokenRequest,
    CliTokenResponse,
    CreateDevboxRequest,
    DeleteResult,
    Devbox,
    DevboxList,
    WhoAmI,
)
from .resources import PRESETS

logger = logging.getLogger(__name__)
PACKAGE_DIR = Path(__file__).parent
DEVBOX_COUNT = Gauge("devboxes_total", "Current devboxes by state", ["state"])
DevboxName = Annotated[
    str,
    ApiPath(
        min_length=1,
        max_length=40,
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$",
    ),
]


def create_app(
    settings: Settings | None = None,
    manager: DevboxManager | None = None,
) -> FastAPI:
    """Create a configured FastAPI application and its lifecycle routes."""
    settings = settings or get_settings()
    manager = manager or DevboxManager(settings)
    authenticator = Authenticator(settings)
    authorization_codes = AuthorizationCodeStore(
        ttl_seconds=settings.authorization_code_ttl_seconds,
        maximum_codes=settings.authorization_code_store_size,
    )
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.cleanup_task = asyncio.create_task(
            _cleanup_loop(manager, settings.cleanup_interval_seconds)
        )
        yield
        app.state.cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.cleanup_task

    app = FastAPI(
        title="Devboxes",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
            "img-src 'self' data:; object-src 'none'",
        )
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), geolocation=(), microphone=()"
        )
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        if request.url.path in {
            "/",
            "/docs",
            "/login",
            "/auth/login",
        } or request.url.path.startswith(("/api/", "/auth/cli/")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    Auth = Annotated[AuthContext, Depends(authenticator.require)]  # noqa: N806

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["system"])
    async def ready() -> Response:
        if await manager.ready():
            return Response(content='{"status":"ready"}', media_type="application/json")
        return Response(
            content='{"status":"not-ready"}',
            media_type="application/json",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        boxes = await manager.list()
        counts = dict.fromkeys(("starting", "ready", "stopped", "degraded"), 0)
        for box in boxes:
            counts[box.state.value] += 1
        for state_name, count in counts.items():
            DEVBOX_COUNT.labels(state=state_name).set(count)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login_page(
        request: Request,
        next_target: Annotated[str, Query(alias="next", max_length=2048)] = "/",
    ) -> Response:
        if not is_safe_login_next(next_target):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid return path"
            )
        if authenticator.browser_session_valid(request):
            return RedirectResponse(next_target, status_code=status.HTTP_303_SEE_OTHER)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": None, "cluster_name": settings.cluster_name, "next_target": next_target},
        )

    @app.post("/auth/login", response_class=HTMLResponse, include_in_schema=False)
    async def login(
        request: Request,
        token: Annotated[str, Form()],
        next_target: Annotated[str, Form(alias="next", max_length=2048)] = "/",
    ) -> Response:
        if not is_safe_login_next(next_target):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid return path"
            )
        if not authenticator.validate_access_token(token):
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "error": "That access token was not accepted.",
                    "cluster_name": settings.cluster_name,
                    "next_target": next_target,
                },
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        session, csrf = authenticator.issue_session()
        response = RedirectResponse(next_target, status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            SESSION_COOKIE,
            session,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            max_age=settings.session_ttl_seconds,
            path="/",
        )
        response.set_cookie(
            CSRF_COOKIE,
            csrf,
            httponly=False,
            secure=settings.cookie_secure,
            samesite="strict",
            max_age=settings.session_ttl_seconds,
            path="/",
        )
        return response

    @app.get("/auth/cli/authorize", response_class=HTMLResponse, include_in_schema=False)
    async def cli_authorize_page(
        request: Request,
        response_type: str,
        client_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        code_challenge_method: str,
    ) -> Response:
        authorization = validate_authorization_request(
            response_type=response_type,
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
        )
        csrf = authenticator.browser_csrf(request)
        if csrf is None:
            next_target = request.url.path
            if request.url.query:
                next_target += f"?{request.url.query}"
            return RedirectResponse(
                f"/login?{urlencode({'next': next_target})}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        return templates.TemplateResponse(
            request,
            "cli_authorize.html",
            {
                "authorization": authorization,
                "csrf": csrf,
                "cluster_name": settings.cluster_name,
                "display_name": settings.display_name,
            },
        )

    @app.post("/auth/cli/authorize", include_in_schema=False)
    async def cli_authorize_decision(
        request: Request,
        action: Annotated[str, Form(max_length=16)],
        csrf: Annotated[str, Form(min_length=16, max_length=256)],
        response_type: Annotated[str, Form(max_length=16)],
        client_id: Annotated[str, Form(max_length=64)],
        redirect_uri: Annotated[str, Form(max_length=256)],
        state: Annotated[str, Form(max_length=256)],
        code_challenge: Annotated[str, Form(max_length=128)],
        code_challenge_method: Annotated[str, Form(max_length=16)],
    ) -> Response:
        authorization = validate_authorization_request(
            response_type=response_type,
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
        )
        auth = authenticator.require_form_csrf(request, csrf)
        if action == "deny":
            location = _append_redirect_query(
                authorization.redirect_uri,
                {"error": "access_denied", "state": authorization.state},
            )
        elif action == "approve":
            code = await authorization_codes.issue(
                client_id=authorization.client_id,
                redirect_uri=authorization.redirect_uri,
                code_challenge=authorization.code_challenge,
                subject=auth.subject,
            )
            location = _append_redirect_query(
                authorization.redirect_uri,
                {"code": code, "state": authorization.state},
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid authorization decision",
            )
        return RedirectResponse(location, status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/api/v1/auth/cli/token", tags=["auth"])
    async def cli_token_exchange(request: Request) -> CliTokenResponse:
        try:
            payload = CliTokenRequest.model_validate(await request.json())
        except (TypeError, ValueError, ValidationError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid authorization code exchange",
            ) from None
        if (
            payload.grant_type != "authorization_code"
            or payload.client_id != CLI_CLIENT_ID
            or not is_loopback_redirect_uri(payload.redirect_uri)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid authorization code exchange",
            )
        subject = await authorization_codes.consume(
            code=payload.code,
            client_id=payload.client_id,
            redirect_uri=payload.redirect_uri,
            code_verifier=payload.code_verifier,
        )
        if subject is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid authorization code exchange",
            )
        token, expires_in = authenticator.issue_cli_token(subject)
        return CliTokenResponse(
            access_token=token,
            expires_in=expires_in,
            scope=CLI_SCOPE,
        )

    @app.post("/auth/logout", include_in_schema=False)
    async def logout(_: Auth) -> Response:
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard(request: Request) -> Response:
        if not authenticator.browser_session_valid(request):
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "default_ttl_hours": settings.default_ttl_hours,
                "max_ttl_hours": settings.max_ttl_hours,
                "cluster_name": settings.cluster_name,
                "storage_class": settings.storage_class or "cluster default",
                "workspace_service_type": settings.workspace_service_type,
            },
        )

    @app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
    async def documentation(request: Request) -> Response:
        if not authenticator.browser_session_valid(request):
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        return templates.TemplateResponse(
            request,
            "docs.html",
            {
                "default_ttl_hours": settings.default_ttl_hours,
                "max_ttl_hours": settings.max_ttl_hours,
                "external_url": settings.external_url,
                "namespace": settings.namespace,
                "storage_class": settings.storage_class or "cluster default",
                "workspace_service_type": settings.workspace_service_type,
                "cluster_name": settings.cluster_name,
                "presets": [
                    {
                        "name": preset.value,
                        "cpu": resources["cpu_request"],
                        "memory": resources["memory_request"],
                        "memory_limit": resources["memory_limit"],
                        "storage": resources["storage"],
                    }
                    for preset, resources in PRESETS.items()
                ],
            },
        )

    @app.get("/api/v1/whoami", tags=["auth"])
    async def whoami(auth: Auth) -> WhoAmI:
        return WhoAmI(user=auth.subject, mode=auth.mode)

    @app.get("/api/v1/devboxes", tags=["devboxes"])
    async def list_devboxes(_: Auth) -> DevboxList:
        return DevboxList(items=await manager.list())

    @app.post(
        "/api/v1/devboxes",
        status_code=status.HTTP_201_CREATED,
        tags=["devboxes"],
    )
    async def create_devbox(payload: CreateDevboxRequest, _: Auth) -> Devbox:
        try:
            return await manager.create(payload)
        except DevboxConflictError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
            ) from error

    @app.get("/api/v1/devboxes/{name}", tags=["devboxes"])
    async def get_devbox(name: DevboxName, _: Auth) -> Devbox:
        return await _not_found(manager.get(name), name)

    @app.post("/api/v1/devboxes/{name}/start", tags=["devboxes"])
    async def start_devbox(name: DevboxName, _: Auth) -> Devbox:
        return await _not_found(manager.scale(name, 1), name)

    @app.post("/api/v1/devboxes/{name}/stop", tags=["devboxes"])
    async def stop_devbox(name: DevboxName, _: Auth) -> Devbox:
        return await _not_found(manager.scale(name, 0), name)

    @app.delete("/api/v1/devboxes/{name}", tags=["devboxes"])
    async def delete_devbox(
        name: DevboxName,
        _: Auth,
        purge: Annotated[bool, Query()] = False,
    ) -> DeleteResult:
        return await _not_found(manager.delete(name, purge), name)

    return app


async def _not_found[T](awaitable: Awaitable[T], name: str) -> T:
    try:
        return await awaitable
    except DevboxNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Devbox {name!r} was not found",
        ) from error


def _append_redirect_query(redirect_uri: str, values: dict[str, str]) -> str:
    parsed = urlsplit(redirect_uri)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(values), ""))


async def _cleanup_loop(manager: DevboxManager, interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            stopped = await manager.stop_expired()
            for name in stopped:
                logger.info("auto-stopped expired devbox %s", name)
        except Exception:
            logger.exception("failed to check expired devboxes")


def main() -> None:
    """Run the production controller with proxy header support."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        "devboxes_controller.app:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container listener
        port=8000,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
