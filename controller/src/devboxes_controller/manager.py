"""Manage the Kubernetes resources that implement devbox lifecycles."""

import asyncio
import builtins
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.utils.quantity import parse_quantity

from .config import Settings
from .models import (
    CreateDevboxRequest,
    DeleteResult,
    Devbox,
    DevboxState,
    Preset,
)
from .resources import (
    ANNOTATION_AUTO_STOPPED_AT,
    ANNOTATION_CREATED_AT,
    ANNOTATION_EXPIRES_AT,
    ANNOTATION_PRESET,
    ANNOTATION_REPOSITORY,
    ANNOTATION_STORAGE,
    ANNOTATION_TTL_HOURS,
    LABEL_MANAGED_BY,
    LABEL_NAME,
    MANAGED_BY,
    PRESETS,
    build_deployment,
    build_pvc,
    build_service,
    resource_name,
)

logger = logging.getLogger(__name__)


class DevboxNotFoundError(Exception):
    """Signal that a requested managed devbox does not exist."""


class DevboxConflictError(Exception):
    """Signal that a requested devbox name is already active."""


class DevboxManager:
    """Translate lifecycle requests into namespaced Kubernetes resources."""

    def __init__(
        self,
        settings: Settings,
        apps_api: client.AppsV1Api | None = None,
        core_api: client.CoreV1Api | None = None,
    ) -> None:
        self.settings = settings
        if apps_api is None or core_api is None:
            self._load_config(settings)
        self.apps = apps_api or client.AppsV1Api()
        self.core = core_api or client.CoreV1Api()

    @staticmethod
    def _load_config(settings: Settings) -> None:
        try:
            config.load_incluster_config()
            logger.info("loaded in-cluster Kubernetes configuration")
        except config.ConfigException:
            config.load_kube_config(context=settings.kubeconfig_context)
            logger.info("loaded kubeconfig context %s", settings.kubeconfig_context or "current")

    async def ready(self) -> bool:
        """Return whether the controller can list namespaced Deployments."""
        try:
            await asyncio.to_thread(
                self.apps.list_namespaced_deployment,
                self.settings.namespace,
                limit=1,
            )
        except ApiException:
            return False
        return True

    async def create(self, request: CreateDevboxRequest) -> Devbox:
        """Create compute, SSH, and persistent storage for a devbox."""
        if request.ttl_hours > self.settings.max_ttl_hours:
            raise ValueError(f"ttl_hours cannot exceed {self.settings.max_ttl_hours}")

        name = resource_name(request.name)
        if await self._deployment_exists(name):
            raise DevboxConflictError(f"devbox {request.name!r} already exists")

        pvc_name = f"{name}-home"
        desired_storage = PRESETS[request.preset]["storage"]
        existing_pvc = await self._read_pvc(pvc_name)
        storage_size = desired_storage
        if existing_pvc is None:
            pvc = build_pvc(request, self.settings.namespace, self.settings.storage_class)
            await asyncio.to_thread(
                self.core.create_namespaced_persistent_volume_claim,
                self.settings.namespace,
                pvc,
            )
        else:
            current_storage = str(
                (existing_pvc.spec.resources.requests or {}).get("storage", desired_storage)
            )
            if parse_quantity(current_storage) < parse_quantity(desired_storage):
                await asyncio.to_thread(
                    self.core.patch_namespaced_persistent_volume_claim,
                    pvc_name,
                    self.settings.namespace,
                    {"spec": {"resources": {"requests": {"storage": desired_storage}}}},
                )
            else:
                storage_size = current_storage

        deployment = build_deployment(
            request,
            self.settings.namespace,
            self.settings.workspace_image,
            self.settings.workspace_secret_name,
            self.settings.workspace_service_account_name,
            self.settings.workspace_priority_class,
            self.settings.image_pull_secret,
        )
        deployment["metadata"]["annotations"][ANNOTATION_STORAGE] = storage_size
        service = build_service(
            request,
            self.settings.namespace,
            self.settings.workspace_service_type,
            self.settings.workspace_service_annotations,
            None,
            self.settings.workspace_load_balancer_class,
            self.settings.workspace_external_traffic_policy,
            self.settings.workspace_load_balancer_source_ranges,
        )
        await asyncio.to_thread(
            self.apps.create_namespaced_deployment,
            self.settings.namespace,
            deployment,
        )
        try:
            await asyncio.to_thread(
                self.core.create_namespaced_service,
                self.settings.namespace,
                service,
            )
        except Exception:
            await self._delete_deployment(name)
            raise
        return await self.get(request.name)

    async def list(self) -> list[Devbox]:
        """List managed devboxes in reverse creation order."""
        selector = f"{LABEL_MANAGED_BY}={MANAGED_BY}"
        deployments, services, pods = await asyncio.gather(
            asyncio.to_thread(
                self.apps.list_namespaced_deployment,
                self.settings.namespace,
                label_selector=selector,
            ),
            asyncio.to_thread(
                self.core.list_namespaced_service,
                self.settings.namespace,
                label_selector=selector,
            ),
            asyncio.to_thread(
                self.core.list_namespaced_pod,
                self.settings.namespace,
                label_selector=selector,
            ),
        )
        service_by_name = {item.metadata.labels.get(LABEL_NAME): item for item in services.items}
        pod_by_name: dict[str, Any] = {}
        for item in pods.items:
            box_name = item.metadata.labels.get(LABEL_NAME)
            if box_name:
                pod_by_name[box_name] = item

        result = [
            self._to_model(
                deployment,
                service_by_name.get(deployment.metadata.labels.get(LABEL_NAME)),
                pod_by_name.get(deployment.metadata.labels.get(LABEL_NAME)),
            )
            for deployment in deployments.items
        ]
        return sorted(result, key=lambda item: item.created_at, reverse=True)

    async def get(self, name: str) -> Devbox:
        """Return the current model for one managed devbox."""
        resource = resource_name(name)
        try:
            deployment, service, pods = await asyncio.gather(
                asyncio.to_thread(
                    self.apps.read_namespaced_deployment,
                    resource,
                    self.settings.namespace,
                ),
                asyncio.to_thread(
                    self.core.read_namespaced_service,
                    f"{resource}-ssh",
                    self.settings.namespace,
                ),
                asyncio.to_thread(
                    self.core.list_namespaced_pod,
                    self.settings.namespace,
                    label_selector=f"{LABEL_NAME}={name}",
                ),
            )
        except ApiException as error:
            if error.status == 404:
                raise DevboxNotFoundError(name) from error
            raise
        pod = pods.items[0] if pods.items else None
        return self._to_model(deployment, service, pod)

    async def scale(self, name: str, replicas: int) -> Devbox:
        """Start or stop a devbox while preserving its home volume."""
        resource = resource_name(name)
        try:
            if replicas == 1:
                deployment = await asyncio.to_thread(
                    self.apps.read_namespaced_deployment,
                    resource,
                    self.settings.namespace,
                )
                annotations = deployment.metadata.annotations or {}
                ttl_hours = _ttl_hours(
                    annotations.get(ANNOTATION_TTL_HOURS),
                    self.settings.default_ttl_hours,
                    self.settings.max_ttl_hours,
                )
                await asyncio.to_thread(
                    self.apps.patch_namespaced_deployment,
                    resource,
                    self.settings.namespace,
                    {
                        "metadata": {
                            "annotations": {
                                ANNOTATION_AUTO_STOPPED_AT: None,
                                ANNOTATION_EXPIRES_AT: (
                                    datetime.now(UTC) + timedelta(hours=ttl_hours)
                                ).isoformat(),
                            }
                        },
                        "spec": {"replicas": 1},
                    },
                )
            else:
                await asyncio.to_thread(
                    self.apps.patch_namespaced_deployment_scale,
                    resource,
                    self.settings.namespace,
                    {"spec": {"replicas": replicas}},
                )
        except ApiException as error:
            if error.status == 404:
                raise DevboxNotFoundError(name) from error
            raise
        return await self.get(name)

    async def delete(self, name: str, purge: bool) -> DeleteResult:
        """Delete compute and SSH resources, optionally deleting storage."""
        resource = resource_name(name)
        if not await self._deployment_exists(resource):
            raise DevboxNotFoundError(name)
        await asyncio.gather(
            self._delete_deployment(resource),
            self._delete_service(f"{resource}-ssh"),
        )
        if purge:
            await self._delete_pvc(f"{resource}-home")
        return DeleteResult(
            name=name,
            purged=purge,
            message=(
                "Devbox and home volume deleted"
                if purge
                else "Devbox deleted; home volume retained for reuse"
            ),
        )

    async def stop_expired(self) -> builtins.list[str]:
        """Stop every active devbox whose TTL has expired."""
        now = datetime.now(UTC)
        stopped: builtins.list[str] = []
        for box in await self.list():
            if box.state is not DevboxState.STOPPED and box.expires_at <= now:
                resource = resource_name(box.name)
                await asyncio.to_thread(
                    self.apps.patch_namespaced_deployment,
                    resource,
                    self.settings.namespace,
                    {
                        "metadata": {"annotations": {ANNOTATION_AUTO_STOPPED_AT: now.isoformat()}},
                        "spec": {"replicas": 0},
                    },
                )
                stopped.append(box.name)
        return stopped

    async def _deployment_exists(self, name: str) -> bool:
        try:
            await asyncio.to_thread(
                self.apps.read_namespaced_deployment,
                name,
                self.settings.namespace,
            )
        except ApiException as error:
            if error.status == 404:
                return False
            raise
        return True

    async def _read_pvc(self, name: str) -> Any | None:
        try:
            return await asyncio.to_thread(
                self.core.read_namespaced_persistent_volume_claim,
                name,
                self.settings.namespace,
            )
        except ApiException as error:
            if error.status == 404:
                return None
            raise

    async def _delete_deployment(self, name: str) -> None:
        await self._ignore_not_found(
            self.apps.delete_namespaced_deployment,
            name,
            self.settings.namespace,
            body=client.V1DeleteOptions(propagation_policy="Foreground"),
        )

    async def _delete_service(self, name: str) -> None:
        await self._ignore_not_found(
            self.core.delete_namespaced_service,
            name,
            self.settings.namespace,
            body=client.V1DeleteOptions(),
        )

    async def _delete_pvc(self, name: str) -> None:
        await self._ignore_not_found(
            self.core.delete_namespaced_persistent_volume_claim,
            name,
            self.settings.namespace,
            body=client.V1DeleteOptions(),
        )

    @staticmethod
    async def _ignore_not_found(function: Any, *args: Any, **kwargs: Any) -> None:
        try:
            await asyncio.to_thread(function, *args, **kwargs)
        except ApiException as error:
            if error.status != 404:
                raise

    def _to_model(self, deployment: Any, service: Any | None, pod: Any | None) -> Devbox:
        annotations = deployment.metadata.annotations or {}
        box_name = deployment.metadata.labels[LABEL_NAME]
        created_at = _parse_datetime(
            annotations.get(ANNOTATION_CREATED_AT), deployment.metadata.creation_timestamp
        )
        expires_at = _parse_datetime(annotations.get(ANNOTATION_EXPIRES_AT), created_at)
        preset = Preset(annotations.get(ANNOTATION_PRESET, Preset.SMALL.value))
        desired = deployment.spec.replicas or 0
        pod_ready = _pod_ready(pod)
        host, port = _service_endpoint(service, self.settings.workspace_service_host)
        state, message = _state(desired, pod, pod_ready, host)
        restarts = sum(status.restart_count or 0 for status in _container_statuses(pod))
        return Devbox(
            name=box_name,
            state=state,
            preset=preset,
            created_at=created_at,
            expires_at=expires_at,
            repository=annotations.get(ANNOTATION_REPOSITORY),
            ssh_host=host,
            ssh_port=port,
            ssh_command=_ssh_command(host, port),
            pod_name=pod.metadata.name if pod else None,
            pod_ready=pod_ready,
            restarts=restarts,
            storage_size=annotations.get(ANNOTATION_STORAGE, "20Gi"),
            message=message,
        )


def _parse_datetime(value: str | None, fallback: datetime) -> datetime:
    if value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return fallback if fallback.tzinfo else fallback.replace(tzinfo=UTC)


def _ttl_hours(value: str | None, default: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        return default
    return parsed if 1 <= parsed <= maximum else default


def _container_statuses(pod: Any | None) -> Iterable[Any]:
    if pod is None or pod.status is None:
        return []
    return pod.status.container_statuses or []


def _pod_ready(pod: Any | None) -> bool:
    if pod is None or pod.status is None:
        return False
    return any(
        condition.type == "Ready" and condition.status == "True"
        for condition in (pod.status.conditions or [])
    )


def _service_endpoint(service: Any | None, configured_host: str | None) -> tuple[str | None, int]:
    if service is None:
        return None, 22
    service_type = getattr(service.spec, "type", None)
    ports = getattr(service.spec, "ports", None) or []
    if service_type == "NodePort":
        node_port = getattr(ports[0], "node_port", None) if ports else None
        return (configured_host, int(node_port)) if configured_host and node_port else (None, 22)

    load_balancer = getattr(getattr(service, "status", None), "load_balancer", None)
    ingress = getattr(load_balancer, "ingress", None) or []
    if ingress:
        value = ingress[0].ip or ingress[0].hostname
        if value:
            return str(value), 22
    return (configured_host, 22) if configured_host else (None, 22)


def _ssh_command(host: str | None, port: int) -> str | None:
    if not host:
        return None
    port_argument = f" -p {port}" if port != 22 else ""
    return f"ssh -t{port_argument} dev@{host}"


def _state(
    desired: int,
    pod: Any | None,
    pod_ready: bool,
    host: str | None,
) -> tuple[DevboxState, str | None]:
    if desired == 0:
        return DevboxState.STOPPED, "Compute stopped; home volume retained"
    if pod_ready and host:
        return DevboxState.READY, None
    if pod is not None:
        phase = getattr(pod.status, "phase", None)
        waiting_reasons = {
            getattr(getattr(status.state, "waiting", None), "reason", None)
            for status in _container_statuses(pod)
        }
        failure = next(
            (
                reason
                for reason in waiting_reasons
                if reason in {"CrashLoopBackOff", "ErrImagePull", "ImagePullBackOff"}
            ),
            None,
        )
        if phase == "Failed" or failure:
            return DevboxState.DEGRADED, failure or "Pod failed"
    if pod_ready:
        return DevboxState.STARTING, "Waiting for an SSH service address"
    return DevboxState.STARTING, "Preparing workspace and SSH"
