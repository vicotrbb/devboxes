"""Create and run the Devboxes HTTP application."""

import asyncio
import csv
import io
import json
import logging
import os
import sqlite3
import tempfile
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import uvicorn
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi import Path as ApiPath
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from kubernetes.client.exceptions import ApiException
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from pydantic import ValidationError
from starlette.background import BackgroundTask
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
from .insights_privacy import InsightsPayloadError
from .insights_service import (
    InsightsRateLimitError,
    InsightsService,
    record_insights_rejection,
)
from .insights_store import QueryFilters
from .manager import (
    DevboxConflictError,
    DevboxManager,
    DevboxNotFoundError,
    TenantQuotaExceededError,
)
from .models import (
    Capabilities,
    CliTokenRequest,
    CliTokenResponse,
    CreateDevboxRequest,
    CustomImageCapabilities,
    CustomImagePortSummary,
    CustomImageProfileSummary,
    DeleteResult,
    Devbox,
    DevboxList,
    GpuCapabilities,
    GpuProfileSummary,
    Preset,
    TenancyCapabilities,
    TenantSummary,
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


def _tenant_summaries(auth: AuthContext) -> list[TenantSummary]:
    """Build the tenant switcher contract available to the current identity."""
    return [
        TenantSummary(
            id=tenant.id,
            display_name=tenant.display_name,
            role=tenant.role.value,
        )
        for tenant in auth.tenants
    ]


def _capabilities(settings: Settings, auth: AuthContext) -> Capabilities:
    """Build the safe installation feature contract exposed to clients."""
    tenant = auth.tenant
    gpu_profiles = settings.tenant_gpu_profiles(tenant)
    default_gpu_profile = settings.tenant_default_gpu_profile(tenant)
    image_profiles = settings.tenant_custom_images(tenant)
    tenant_summaries = _tenant_summaries(auth)
    return Capabilities(
        gpu=GpuCapabilities(
            enabled=bool(gpu_profiles),
            default_profile=default_gpu_profile,
            profiles=[
                GpuProfileSummary(
                    name=profile.name,
                    display_name=profile.display_name,
                    description=profile.description,
                    resource_name=profile.resource_name,
                    count=profile.count,
                    default=profile.name == default_gpu_profile,
                )
                for profile in gpu_profiles
            ],
        ),
        images=CustomImageCapabilities(
            enabled=bool(image_profiles),
            profiles=[
                CustomImageProfileSummary(
                    name=profile.name,
                    display_name=profile.display_name,
                    description=profile.description,
                    mode=profile.mode,
                    ports=[
                        CustomImagePortSummary(
                            name=port.name,
                            container_port=port.container_port,
                            protocol=port.protocol,
                        )
                        for port in profile.ports
                    ],
                )
                for profile in image_profiles
            ],
        ),
        tenancy=TenancyCapabilities(
            enabled=settings.multi_tenancy_enabled,
            current=TenantSummary(
                id=tenant.id,
                display_name=tenant.display_name,
                role=auth.role.value,
            ),
            tenants=tenant_summaries,
            max_ttl_hours=settings.tenant_max_ttl_hours(tenant),
            allowed_presets=list(tenant.allowed_presets),
            resource_quota=(
                tenant.resource_quota.hard
                if tenant.resource_quota is not None and tenant.resource_quota.enabled
                else {}
            ),
        ),
    )


def create_app(
    settings: Settings | None = None,
    manager: DevboxManager | None = None,
    insights: InsightsService | None = None,
) -> FastAPI:
    """Create a configured FastAPI application and its lifecycle routes."""
    settings = settings or get_settings()
    manager = manager or DevboxManager(settings)
    insights = insights or InsightsService(settings)
    authenticator = Authenticator(settings)
    authorization_codes = AuthorizationCodeStore(
        ttl_seconds=settings.authorization_code_ttl_seconds,
        maximum_codes=settings.authorization_code_store_size,
    )
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await insights.initialize()
        except (OSError, sqlite3.Error, ValueError):
            logger.exception("failed to initialize the Insights store")
        app.state.cleanup_task = asyncio.create_task(
            _cleanup_loop(manager, settings.cleanup_interval_seconds)
        )
        app.state.insights_task = (
            asyncio.create_task(_insights_maintenance_loop(insights)) if insights.enabled else None
        )
        yield
        app.state.cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.cleanup_task
        if app.state.insights_task is not None:
            app.state.insights_task.cancel()
            with suppress(asyncio.CancelledError):
                await app.state.insights_task

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
            "/insights",
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
        manager_ready, insights_ready = await asyncio.gather(manager.ready(), insights.ready())
        if manager_ready and insights_ready:
            return Response(content='{"status":"ready"}', media_type="application/json")
        return Response(
            content='{"status":"not-ready"}',
            media_type="application/json",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        tenant_boxes = await asyncio.gather(
            *[manager.list(tenant) for tenant in settings.tenant_configs]
        )
        boxes = [box for items in tenant_boxes for box in items]
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
        identity = authenticator.authenticate_access_token(token)
        if identity is None:
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
        session, csrf = authenticator.issue_session(identity.subject)
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
    async def dashboard(
        request: Request,
        tenant_id: Annotated[str | None, Query(alias="tenant", max_length=40)] = None,
    ) -> Response:
        auth = authenticator.browser_context(request, tenant_id)
        if auth is None:
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        capabilities = _capabilities(settings, auth)
        maximum_ttl = settings.tenant_max_ttl_hours(auth.tenant)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "default_ttl_hours": min(settings.default_ttl_hours, maximum_ttl),
                "max_ttl_hours": maximum_ttl,
                "cluster_name": settings.cluster_name,
                "storage_class": (settings.tenant_storage_class(auth.tenant) or "cluster default"),
                "workspace_service_type": settings.workspace_service_type,
                "gpu": capabilities.gpu,
                "images": capabilities.images,
                "tenancy": capabilities.tenancy,
                "can_manage": auth.can_manage,
                "can_purge": auth.can_purge,
                "version": __version__,
            },
        )

    @app.get("/insights", response_class=HTMLResponse, include_in_schema=False)
    async def insights_dashboard(
        request: Request,
        tenant_id: Annotated[str | None, Query(alias="tenant", max_length=40)] = None,
    ) -> Response:
        auth = authenticator.browser_context(request, tenant_id)
        if auth is None:
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        return templates.TemplateResponse(
            request,
            "insights.html",
            {
                "cluster_name": settings.cluster_name,
                "insights_enabled": insights.enabled,
                "tenancy": _capabilities(settings, auth).tenancy,
                "can_purge": auth.can_purge,
                "version": __version__,
            },
        )

    @app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
    async def documentation(
        request: Request,
        tenant_id: Annotated[str | None, Query(alias="tenant", max_length=40)] = None,
    ) -> Response:
        auth = authenticator.browser_context(request, tenant_id)
        if auth is None:
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        capabilities = _capabilities(settings, auth)
        configured_presets = set(auth.tenant.allowed_presets)
        allowed_presets = [preset for preset in PRESETS if preset in configured_presets]
        maximum_ttl = settings.tenant_max_ttl_hours(auth.tenant)
        default_preset = Preset.MEDIUM if Preset.MEDIUM in allowed_presets else allowed_presets[0]
        return templates.TemplateResponse(
            request,
            "docs.html",
            {
                "default_ttl_hours": min(
                    settings.default_ttl_hours,
                    maximum_ttl,
                ),
                "max_ttl_hours": maximum_ttl,
                "example_ttl_hours": min(72, maximum_ttl),
                "default_preset": default_preset.value,
                "smallest_preset": allowed_presets[0].value,
                "largest_preset": allowed_presets[-1].value,
                "allowed_presets": [preset.value for preset in allowed_presets],
                "external_url": settings.external_url,
                "namespace": auth.tenant.namespace,
                "controller_namespace": settings.namespace,
                "storage_class": (settings.tenant_storage_class(auth.tenant) or "cluster default"),
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
                    if preset in allowed_presets
                ],
                "insights_enabled": insights.enabled,
                "gpu": capabilities.gpu,
                "images": capabilities.images,
                "tenancy": capabilities.tenancy,
                "version": __version__,
            },
        )

    @app.get("/api/v1/whoami", tags=["auth"])
    async def whoami(auth: Auth) -> WhoAmI:
        return WhoAmI(
            user=auth.display_name,
            subject=auth.subject,
            mode=auth.mode,
            tenant=auth.tenant.id,
            role=auth.role.value,
            tenants=_tenant_summaries(auth),
        )

    @app.get("/api/v1/capabilities", tags=["system"])
    async def installation_capabilities(auth: Auth) -> Capabilities:
        return _capabilities(settings, auth)

    @app.get("/api/v1/devboxes", tags=["devboxes"])
    async def list_devboxes(auth: Auth) -> DevboxList:
        return DevboxList(items=await manager.list(auth.tenant))

    @app.post(
        "/api/v1/devboxes",
        status_code=status.HTTP_201_CREATED,
        tags=["devboxes"],
    )
    async def create_devbox(payload: CreateDevboxRequest, auth: Auth) -> Devbox:
        _require_manage(auth)
        try:
            return await manager.create(payload, auth.tenant, auth.subject)
        except DevboxConflictError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except TenantQuotaExceededError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error

    @app.get("/api/v1/devboxes/{name}", tags=["devboxes"])
    async def get_devbox(name: DevboxName, auth: Auth) -> Devbox:
        return await _not_found(manager.get(name, auth.tenant), name)

    @app.post("/api/v1/devboxes/{name}/start", tags=["devboxes"])
    async def start_devbox(name: DevboxName, auth: Auth) -> Devbox:
        _require_manage(auth)
        try:
            return await _not_found(manager.scale(name, 1, auth.tenant), name)
        except TenantQuotaExceededError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.post("/api/v1/devboxes/{name}/stop", tags=["devboxes"])
    async def stop_devbox(name: DevboxName, auth: Auth) -> Devbox:
        _require_manage(auth)
        return await _not_found(manager.scale(name, 0, auth.tenant), name)

    @app.delete("/api/v1/devboxes/{name}", tags=["devboxes"])
    async def delete_devbox(
        name: DevboxName,
        auth: Auth,
        purge: Annotated[bool, Query()] = False,
    ) -> DeleteResult:
        _require_manage(auth)
        if purge:
            _require_purge(auth)
        return await _not_found(manager.delete(name, purge, auth.tenant), name)

    @app.post("/internal/v1/insights/batches", include_in_schema=False)
    async def ingest_insights_batch(request: Request) -> dict[str, Any]:
        if not insights.enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        authorization = request.headers.get("Authorization", "")
        credential = (
            authorization.removeprefix("Bearer ").strip()
            if authorization.startswith("Bearer ")
            else ""
        )
        scope = authenticator.validate_insights_token(credential)
        if scope is None:
            record_insights_rejection("authentication")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if request.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            record_insights_rejection("media_type")
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Insights batches must use application/json",
            )
        tenant_id, instance_id, box_name = scope
        try:
            body = await _read_limited_body(request, settings.insights_max_compressed_bytes)
            return await insights.ingest(
                tenant_id=tenant_id,
                instance_id=instance_id,
                box_name=box_name,
                compressed_body=body,
                content_encoding=request.headers.get("Content-Encoding"),
            )
        except HTTPException as error:
            if error.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE:
                record_insights_rejection("size")
            raise
        except InsightsRateLimitError as error:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Insights ingest rate exceeded",
                headers={"Retry-After": "60"},
            ) from error
        except InsightsPayloadError as error:
            code = (
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                if "large" in str(error) or "too many" in str(error)
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(status_code=code, detail=str(error)) from error

    @app.get("/api/v1/insights/summary", tags=["insights"])
    async def insights_summary(
        auth: Auth,
        since: Annotated[str, Query(min_length=2, max_length=64)] = "7d",
        until: Annotated[str | None, Query(max_length=64)] = None,
        box: Annotated[str | None, Query(max_length=40)] = None,
        devbox: Annotated[str | None, Query(max_length=40)] = None,
        instance_id: Annotated[str | None, Query(max_length=36)] = None,
        provider: Annotated[str | None, Query(pattern="^(codex|claude)$")] = None,
        model: Annotated[str | None, Query(max_length=160)] = None,
        repo: Annotated[str | None, Query(max_length=160)] = None,
        repository: Annotated[str | None, Query(max_length=160)] = None,
        group_by: Annotated[
            str | None,
            Query(pattern="^(provider|model|box|repository|repo)$"),
        ] = None,
    ) -> dict[str, Any]:
        if not insights.enabled:
            return insights.disabled_envelope()
        filters = _insights_filters(
            insights,
            auth.tenant.id,
            since,
            until,
            _query_alias(box, devbox, "box", "devbox"),
            provider,
            model,
            _query_alias(repo, repository, "repo", "repository"),
            maximum_days=365,
            instance_id=instance_id,
            group_by="repository" if group_by == "repo" else group_by,
        )
        return await _with_workspace_insights(manager, await insights.summary(filters), filters)

    @app.get("/api/v1/insights/timeseries", tags=["insights"])
    async def insights_timeseries(
        auth: Auth,
        metric: Annotated[str, Query(max_length=32)],
        since: Annotated[str, Query(min_length=2, max_length=64)] = "7d",
        until: Annotated[str | None, Query(max_length=64)] = None,
        box: Annotated[str | None, Query(max_length=40)] = None,
        devbox: Annotated[str | None, Query(max_length=40)] = None,
        instance_id: Annotated[str | None, Query(max_length=36)] = None,
        provider: Annotated[str | None, Query(pattern="^(codex|claude)$")] = None,
        model: Annotated[str | None, Query(max_length=160)] = None,
        repo: Annotated[str | None, Query(max_length=160)] = None,
        repository: Annotated[str | None, Query(max_length=160)] = None,
        bucket: Annotated[str | None, Query(pattern="^(hour|day)$")] = None,
    ) -> dict[str, Any]:
        if not insights.enabled:
            return insights.disabled_envelope()
        filters = _insights_filters(
            insights,
            auth.tenant.id,
            since,
            until,
            _query_alias(box, devbox, "box", "devbox"),
            provider,
            model,
            _query_alias(repo, repository, "repo", "repository"),
            maximum_days=365,
            instance_id=instance_id,
            bucket=bucket,
        )
        try:
            return await _with_workspace_insights(
                manager,
                await insights.timeseries(filters, metric),
                filters,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error

    @app.get("/api/v1/insights/activity", tags=["insights"])
    async def insights_activity(
        auth: Auth,
        since: Annotated[str, Query(min_length=2, max_length=64)] = "7d",
        until: Annotated[str | None, Query(max_length=64)] = None,
        box: Annotated[str | None, Query(max_length=40)] = None,
        devbox: Annotated[str | None, Query(max_length=40)] = None,
        instance_id: Annotated[str | None, Query(max_length=36)] = None,
        provider: Annotated[str | None, Query(pattern="^(codex|claude)$")] = None,
        model: Annotated[str | None, Query(max_length=160)] = None,
        repo: Annotated[str | None, Query(max_length=160)] = None,
        repository: Annotated[str | None, Query(max_length=160)] = None,
        cursor: Annotated[str | None, Query(max_length=128)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        if not insights.enabled:
            return insights.disabled_envelope()
        filters = _insights_filters(
            insights,
            auth.tenant.id,
            since,
            until,
            _query_alias(box, devbox, "box", "devbox"),
            provider,
            model,
            _query_alias(repo, repository, "repo", "repository"),
            maximum_days=365,
            instance_id=instance_id,
        )
        try:
            return await _with_workspace_insights(
                manager,
                await insights.activity(filters, cursor=cursor, limit=limit),
                filters,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error

    @app.get("/api/v1/insights/capabilities", tags=["insights"])
    async def insights_capabilities(
        auth: Auth,
        since: Annotated[str, Query(min_length=2, max_length=64)] = "7d",
        until: Annotated[str | None, Query(max_length=64)] = None,
        box: Annotated[str | None, Query(max_length=40)] = None,
        devbox: Annotated[str | None, Query(max_length=40)] = None,
        instance_id: Annotated[str | None, Query(max_length=36)] = None,
        provider: Annotated[str | None, Query(pattern="^(codex|claude)$")] = None,
        model: Annotated[str | None, Query(max_length=160)] = None,
        repo: Annotated[str | None, Query(max_length=160)] = None,
        repository: Annotated[str | None, Query(max_length=160)] = None,
        group_by: Annotated[
            str | None,
            Query(pattern="^(provider|model|box|repository|repo)$"),
        ] = None,
    ) -> dict[str, Any]:
        if not insights.enabled:
            return insights.disabled_envelope()
        filters = _insights_filters(
            insights,
            auth.tenant.id,
            since,
            until,
            _query_alias(box, devbox, "box", "devbox"),
            provider,
            model,
            _query_alias(repo, repository, "repo", "repository"),
            maximum_days=365,
            instance_id=instance_id,
            group_by="repository" if group_by == "repo" else group_by,
        )
        return await _with_workspace_insights(manager, await insights.status(filters), filters)

    @app.get("/api/v1/insights/export", tags=["insights"])
    async def insights_export(
        auth: Auth,
        format: Annotated[str, Query(pattern="^(json|csv|sqlite)$")] = "json",
        since: Annotated[str, Query(min_length=2, max_length=64)] = "30d",
        until: Annotated[str | None, Query(max_length=64)] = None,
        box: Annotated[str | None, Query(max_length=40)] = None,
        devbox: Annotated[str | None, Query(max_length=40)] = None,
        instance_id: Annotated[str | None, Query(max_length=36)] = None,
        provider: Annotated[str | None, Query(pattern="^(codex|claude)$")] = None,
        model: Annotated[str | None, Query(max_length=160)] = None,
        repo: Annotated[str | None, Query(max_length=160)] = None,
        repository: Annotated[str | None, Query(max_length=160)] = None,
        group_by: Annotated[
            str | None,
            Query(pattern="^(provider|model|box|repository|repo)$"),
        ] = None,
    ) -> Response:
        if not insights.enabled:
            return Response(
                content=json.dumps(insights.disabled_envelope()),
                media_type="application/json",
            )
        filters = _insights_filters(
            insights,
            auth.tenant.id,
            since,
            until,
            _query_alias(box, devbox, "box", "devbox"),
            provider,
            model,
            _query_alias(repo, repository, "repo", "repository"),
            maximum_days=365,
            instance_id=instance_id,
            group_by="repository" if group_by == "repo" else group_by,
        )
        if format == "sqlite":
            descriptor, backup_path = tempfile.mkstemp(
                prefix="devboxes-insights-",
                suffix=".db",
                dir="/tmp",
            )
            os.close(descriptor)
            try:
                await insights.backup(backup_path, auth.tenant.id)
            except (OSError, sqlite3.Error):
                await asyncio.to_thread(Path(backup_path).unlink, missing_ok=True)
                raise
            return FileResponse(
                backup_path,
                media_type="application/vnd.sqlite3",
                filename="devboxes-insights.db",
                background=BackgroundTask(Path(backup_path).unlink, missing_ok=True),
            )
        envelope = await _with_workspace_insights(
            manager,
            await insights.summary(filters),
            filters,
        )
        if format == "json":
            return Response(
                content=json.dumps(envelope, sort_keys=True, separators=(",", ":")),
                media_type="application/json",
                headers={"Content-Disposition": 'attachment; filename="devboxes-insights.json"'},
            )
        return Response(
            content=_summary_csv(envelope),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="devboxes-insights.csv"'},
        )

    @app.delete("/api/v1/insights", tags=["insights"])
    async def purge_insights_query(
        auth: Auth,
        instance_id: Annotated[str | None, Query(max_length=36)] = None,
        box: Annotated[str | None, Query(max_length=40)] = None,
        devbox: Annotated[str | None, Query(max_length=40)] = None,
    ) -> dict[str, Any]:
        _require_purge(auth)
        if not insights.enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Insights is disabled",
            )
        selected_box = _query_alias(box, devbox, "box", "devbox")
        try:
            return await insights.purge(
                selected_box,
                instance_id=instance_id,
                tenant_id=auth.tenant.id,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error

    @app.delete("/api/v1/insights/devboxes/{name}", tags=["insights"])
    async def purge_insights_compatibility(name: DevboxName, auth: Auth) -> dict[str, Any]:
        _require_purge(auth)
        if not insights.enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Insights is disabled",
            )
        return await insights.purge(box_name=name, tenant_id=auth.tenant.id)

    return app


async def _with_workspace_insights(
    manager: DevboxManager,
    envelope: dict[str, Any],
    filters: QueryFilters,
) -> dict[str, Any]:
    """Merge Kubernetes rollout state into collector coverage without blocking queries."""
    try:
        tenant = manager.settings.resolve_tenant(filters.tenant_id)
        boxes = await manager.list(tenant)
    except (ApiException, OSError, ValueError):
        return envelope
    collectors = list(envelope.get("coverage", {}).get("collectors", []))
    represented = {str(item.get("box")) for item in collectors}
    for box in boxes:
        if filters.box and box.name != filters.box:
            continue
        if filters.instance_id and box.instance_id != filters.instance_id:
            continue
        state = getattr(box.insights_state, "value", str(box.insights_state))
        if state == "restart_required":
            collector_state = "restart_required"
            reason = "A normal stop and start is required to install the collector"
        elif state == "collecting" and box.name not in represented:
            collector_state = "partial"
            reason = "Waiting for the first collector batch"
        else:
            continue
        collectors.append(
            {
                "box": box.name,
                "collector": "workspace",
                "version": "unavailable",
                "status": collector_state,
                "capability_reason": reason,
                "last_seen_at": envelope["generated_at"],
                "freshness_seconds": 0,
                "queue_bytes": 0,
                "dropped_batches": 0,
                "dropped_points": 0,
                "provider_versions": {},
                "last_successful_send_at": None,
                "last_error_category": None,
            }
        )
    coverage = envelope.get("coverage", {})
    coverage["collectors"] = collectors
    states = {str(item.get("status")) for item in collectors}
    if "stale" in states:
        coverage["status"] = "stale"
    elif states & {"partial", "restart_required", "data_loss_detected"}:
        coverage["status"] = "partial"
    elif collectors:
        coverage["status"] = "fresh"
    data = envelope.get("data")
    if isinstance(data, dict) and "collectors" in data:
        data["collectors"] = collectors
    return envelope


async def _not_found[T](awaitable: Awaitable[T], name: str) -> T:
    try:
        return await awaitable
    except DevboxNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Devbox {name!r} was not found",
        ) from error


def _require_manage(auth: AuthContext) -> None:
    """Require a role allowed to mutate ordinary tenant resources."""
    if not auth.can_manage:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The current tenant role is read-only",
        )


def _require_purge(auth: AuthContext) -> None:
    """Require an administrative role for irreversible tenant data deletion."""
    if not auth.can_purge:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The current tenant role cannot purge persistent data",
        )


def _append_redirect_query(redirect_uri: str, values: dict[str, str]) -> str:
    parsed = urlsplit(redirect_uri)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(values), ""))


async def _cleanup_loop(manager: DevboxManager, interval: int) -> None:
    while True:
        try:
            reconciled = await manager.reconcile_insights()
            for name in reconciled:
                logger.info("reconciled Insights state for devbox %s", name)
            stopped = await manager.stop_expired()
            for name in stopped:
                logger.info("auto-stopped expired devbox %s", name)
        except Exception:
            logger.exception("failed to check expired devboxes")
        await asyncio.sleep(interval)


async def _insights_maintenance_loop(insights: InsightsService) -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            result = await insights.maintain()
            logger.info("completed Insights maintenance: %s", result)
        except (OSError, sqlite3.Error, ValueError):
            logger.exception("failed to maintain the Insights store")


async def _read_limited_body(request: Request, maximum: int) -> bytes:
    length = request.headers.get("Content-Length")
    if length is not None:
        try:
            if int(length) > maximum:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="compressed batch is too large",
                )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length",
            ) from error
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > maximum:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="compressed batch is too large",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _insights_filters(
    insights: InsightsService,
    tenant_id: str,
    since: str,
    until: str | None,
    box: str | None,
    provider: str | None,
    model: str | None,
    repo: str | None,
    *,
    maximum_days: int,
    instance_id: str | None = None,
    group_by: str | None = None,
    bucket: str | None = None,
) -> QueryFilters:
    try:
        return insights.filters(
            tenant_id=tenant_id,
            since=since,
            until=until,
            box=box,
            provider=provider,
            model=model,
            repo=repo,
            maximum_days=maximum_days,
            instance_id=instance_id,
            group_by=group_by,
            bucket=bucket,
        )
    except (InsightsPayloadError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


def _query_alias(
    short: str | None,
    descriptive: str | None,
    short_name: str,
    descriptive_name: str,
) -> str | None:
    if short is not None and descriptive is not None and short != descriptive:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{short_name} and {descriptive_name} must match when both are supplied",
        )
    return descriptive if descriptive is not None else short


def _summary_csv(envelope: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("category", "provider", "metric", "value"))
    data = envelope.get("data") or {}
    ai = data.get("ai") or {}
    for provider, values in sorted((ai.get("providers") or {}).items()):
        for metric in ("sessions", "total_tokens", "cost_usd", "active_seconds"):
            value = values.get(metric)
            if value is not None:
                writer.writerow(("ai", _csv_cell(provider), metric, _csv_cell(value)))
        for token_type, value in sorted((values.get("tokens") or {}).items()):
            writer.writerow(("ai", _csv_cell(provider), f"tokens.{token_type}", _csv_cell(value)))
    for metric, value in sorted((data.get("code") or {}).items()):
        if isinstance(value, dict):
            for child, child_value in sorted(value.items()):
                writer.writerow(("code", "git", f"{metric}.{child}", _csv_cell(child_value)))
        else:
            writer.writerow(("code", "git", metric, _csv_cell(value)))
    return output.getvalue()


def _csv_cell(value: object) -> str:
    text = str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


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
