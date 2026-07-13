"""Create and run the Devboxes HTTP application."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi import Path as ApiPath
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from starlette.middleware.base import RequestResponseEndpoint

from . import __version__
from .auth import CSRF_COOKIE, SESSION_COOKIE, AuthContext, Authenticator
from .config import Settings, get_settings
from .manager import DevboxConflictError, DevboxManager, DevboxNotFoundError
from .models import CreateDevboxRequest, DeleteResult, Devbox, DevboxList, WhoAmI
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
        } or request.url.path.startswith("/api/"):
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
    async def login_page(request: Request) -> Response:
        if authenticator.browser_session_valid(request):
            return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": None, "cluster_name": settings.cluster_name},
        )

    @app.post("/auth/login", response_class=HTMLResponse, include_in_schema=False)
    async def login(request: Request, token: Annotated[str, Form()]) -> Response:
        if not authenticator.validate_access_token(token):
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "error": "That access token was not accepted.",
                    "cluster_name": settings.cluster_name,
                },
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        session, csrf = authenticator.issue_session()
        response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
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
        return WhoAmI(user=settings.display_name, mode=auth.mode)

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
