"""Build Kubernetes manifests for managed devbox resources."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import GpuProfile
from .models import CreateDevboxRequest, Preset

MANAGED_BY = "devboxes-controller"
LABEL_MANAGED_BY = "app.kubernetes.io/managed-by"
LABEL_NAME = "devboxes.bonalab.org/name"
ANNOTATION_CREATED_AT = "devboxes.bonalab.org/created-at"
ANNOTATION_EXPIRES_AT = "devboxes.bonalab.org/expires-at"
ANNOTATION_TTL_HOURS = "devboxes.bonalab.org/ttl-hours"
ANNOTATION_REPOSITORY = "devboxes.bonalab.org/repository"
ANNOTATION_PRESET = "devboxes.bonalab.org/preset"
ANNOTATION_STORAGE = "devboxes.bonalab.org/storage-size"
ANNOTATION_AUTO_STOPPED_AT = "devboxes.bonalab.org/auto-stopped-at"
ANNOTATION_INSTANCE_ID = "insights.devboxes.bonalab.org/instance-id"
ANNOTATION_INSIGHTS_STATE = "insights.devboxes.bonalab.org/state"
ANNOTATION_INSIGHTS_TEMPLATE_HASH = "insights.devboxes.bonalab.org/template-hash"
ANNOTATION_GPU_PROFILE = "gpu.devboxes.bonalab.org/profile"
ANNOTATION_GPU_RESOURCE = "gpu.devboxes.bonalab.org/resource"
ANNOTATION_GPU_COUNT = "gpu.devboxes.bonalab.org/count"
ANNOTATION_GPU_CONFIG = "gpu.devboxes.bonalab.org/resolved-config"


PRESETS: dict[Preset, dict[str, str]] = {
    Preset.SMALL: {
        "cpu_request": "250m",
        "memory_request": "512Mi",
        "memory_limit": "4Gi",
        "storage": "20Gi",
    },
    Preset.MEDIUM: {
        "cpu_request": "750m",
        "memory_request": "2Gi",
        "memory_limit": "8Gi",
        "storage": "30Gi",
    },
    Preset.LARGE: {
        "cpu_request": "2",
        "memory_request": "4Gi",
        "memory_limit": "16Gi",
        "storage": "50Gi",
    },
}


def resource_name(name: str) -> str:
    """Return the deterministic Kubernetes resource name for a devbox."""
    return f"devbox-{name}"


def labels(name: str) -> dict[str, str]:
    """Return ownership and lookup labels for a managed devbox."""
    return {
        "app.kubernetes.io/name": "devbox",
        "app.kubernetes.io/instance": resource_name(name),
        "app.kubernetes.io/part-of": "devboxes",
        LABEL_MANAGED_BY: MANAGED_BY,
        LABEL_NAME: name,
    }


def annotations(
    request: CreateDevboxRequest,
    now: datetime | None = None,
    instance_id: str | None = None,
    gpu_profile: GpuProfile | None = None,
) -> dict[str, str]:
    """Return lifecycle and user-input annotations for a new devbox."""
    now = now or datetime.now(UTC)
    result = {
        ANNOTATION_CREATED_AT: now.isoformat(),
        ANNOTATION_EXPIRES_AT: (now + timedelta(hours=request.ttl_hours)).isoformat(),
        ANNOTATION_TTL_HOURS: str(request.ttl_hours),
        ANNOTATION_PRESET: request.preset.value,
        ANNOTATION_STORAGE: PRESETS[request.preset]["storage"],
    }
    if request.repository:
        result[ANNOTATION_REPOSITORY] = request.repository
    if instance_id:
        result[ANNOTATION_INSTANCE_ID] = instance_id
    if gpu_profile is not None:
        result.update(
            {
                ANNOTATION_GPU_PROFILE: gpu_profile.name,
                ANNOTATION_GPU_RESOURCE: gpu_profile.resource_name,
                ANNOTATION_GPU_COUNT: str(gpu_profile.count),
                ANNOTATION_GPU_CONFIG: json.dumps(
                    gpu_profile.model_dump(mode="json", by_alias=True, exclude_none=True),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
    return result


def build_pvc(
    request: CreateDevboxRequest,
    namespace: str,
    storage_class: str | None,
    instance_id: str | None = None,
) -> dict[str, Any]:
    """Build the persistent home volume claim for a devbox."""
    manifest: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": f"{resource_name(request.name)}-home",
            "namespace": namespace,
            "labels": labels(request.name),
            "annotations": (
                {ANNOTATION_INSTANCE_ID: instance_id} if instance_id is not None else {}
            ),
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": PRESETS[request.preset]["storage"]}},
        },
    }
    if storage_class:
        manifest["spec"]["storageClassName"] = storage_class
    return manifest


def build_deployment(
    request: CreateDevboxRequest,
    namespace: str,
    workspace_image: str,
    workspace_secret_name: str,
    workspace_service_account_name: str,
    workspace_priority_class: str | None = None,
    image_pull_secret: str | None = None,
    gpu_profile: GpuProfile | None = None,
    now: datetime | None = None,
    instance_id: str | None = None,
    insights_enabled: bool = False,
    insights_endpoint: str | None = None,
    insights_credential: str | None = None,
    insights_secret_name: str | None = None,
    insights_scan_interval_seconds: int = 60,
    insights_repository_depth: int = 4,
    insights_max_queue_bytes: int = 134_217_728,
    insights_max_queue_age_seconds: int = 604_800,
) -> dict[str, Any]:
    """Build the disposable workspace Deployment for a devbox."""
    name = resource_name(request.name)
    box_labels = labels(request.name)
    effective_workspace_image = (
        gpu_profile.workspace_image
        if gpu_profile is not None and gpu_profile.workspace_image is not None
        else workspace_image
    )
    env = [
        {"name": "DEVBOX_NAME", "value": request.name},
        {"name": "DEVBOX_PRESET", "value": request.preset.value},
    ]
    if request.repository:
        env.append({"name": "DEVBOX_REPOSITORY", "value": request.repository})
    if gpu_profile is not None:
        env.extend(
            [
                {"name": "DEVBOX_GPU_PROFILE", "value": gpu_profile.name},
                {"name": "DEVBOX_GPU_RESOURCE", "value": gpu_profile.resource_name},
                {"name": "DEVBOX_GPU_COUNT", "value": str(gpu_profile.count)},
            ]
        )
        if gpu_profile.supplemental_groups:
            env.append(
                {
                    "name": "DEVBOX_GPU_SUPPLEMENTAL_GROUPS",
                    "value": ",".join(str(group) for group in gpu_profile.supplemental_groups),
                }
            )
    if insights_enabled:
        env.extend(
            [
                {"name": "DEVBOXES_INSIGHTS_ENABLED", "value": "true"},
                {"name": "CLAUDE_CODE_ENABLE_TELEMETRY", "value": "1"},
                {"name": "OTEL_METRICS_EXPORTER", "value": "otlp"},
                {"name": "OTEL_LOGS_EXPORTER", "value": "none"},
                {"name": "OTEL_TRACES_EXPORTER", "value": "none"},
                {"name": "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "value": "http/json"},
                {
                    "name": "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
                    "value": "http://127.0.0.1:4318/v1/metrics",
                },
                {
                    "name": "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
                    "value": "delta",
                },
                {"name": "OTEL_METRICS_INCLUDE_SESSION_ID", "value": "false"},
                {"name": "OTEL_METRICS_INCLUDE_ACCOUNT_UUID", "value": "false"},
                {"name": "OTEL_METRICS_INCLUDE_VERSION", "value": "true"},
                {"name": "OTEL_METRICS_INCLUDE_RESOURCE_ATTRIBUTES", "value": "false"},
                {"name": "OTEL_LOG_USER_PROMPTS", "value": "0"},
                {"name": "OTEL_LOG_ASSISTANT_RESPONSES", "value": "0"},
                {"name": "OTEL_LOG_TOOL_DETAILS", "value": "0"},
                {"name": "OTEL_LOG_TOOL_CONTENT", "value": "0"},
                {"name": "OTEL_LOG_RAW_API_BODIES", "value": "0"},
            ]
        )

    pod_spec: dict[str, Any] = {
        "serviceAccountName": workspace_service_account_name,
        "automountServiceAccountToken": False,
        "terminationGracePeriodSeconds": 20,
        "securityContext": {
            "seccompProfile": {"type": "RuntimeDefault"},
            # Projected Secret files remain owned by root, but Kubernetes grants
            # the unprivileged dev user (gid 1000) read-only group access.
            "fsGroup": 1000,
            "fsGroupChangePolicy": "OnRootMismatch",
        },
        "containers": [
            {
                "name": "devbox",
                "image": effective_workspace_image,
                "imagePullPolicy": "IfNotPresent",
                "ports": [{"name": "ssh", "containerPort": 2222, "protocol": "TCP"}],
                "env": env,
                "resources": {
                    "requests": {
                        "cpu": PRESETS[request.preset]["cpu_request"],
                        "memory": PRESETS[request.preset]["memory_request"],
                    },
                    "limits": {"memory": PRESETS[request.preset]["memory_limit"]},
                },
                "securityContext": {
                    # The trusted dev user needs setuid sudo inside this disposable container.
                    # OpenSSH also writes a login audit record while allocating a PTY.
                    "allowPrivilegeEscalation": True,
                    "capabilities": {
                        "drop": ["ALL"],
                        "add": [
                            "AUDIT_WRITE",
                            "CHOWN",
                            "DAC_OVERRIDE",
                            "FOWNER",
                            "SETGID",
                            "SETUID",
                            "SYS_CHROOT",
                        ],
                    },
                },
                "readinessProbe": {
                    "tcpSocket": {"port": "ssh"},
                    "initialDelaySeconds": 3,
                    "periodSeconds": 5,
                    "timeoutSeconds": 2,
                    "failureThreshold": 12,
                },
                "livenessProbe": {
                    "tcpSocket": {"port": "ssh"},
                    "initialDelaySeconds": 20,
                    "periodSeconds": 30,
                    "timeoutSeconds": 3,
                    "failureThreshold": 3,
                },
                "volumeMounts": [
                    {"name": "home", "mountPath": "/home/dev"},
                    {
                        "name": "workspace-secrets",
                        "mountPath": "/run/devbox-secrets",
                        "readOnly": True,
                    },
                    {"name": "run", "mountPath": "/run/sshd"},
                    {"name": "tmp", "mountPath": "/tmp"},  # noqa: S108
                ],
            }
        ],
        "volumes": [
            {
                "name": "home",
                "persistentVolumeClaim": {"claimName": f"{name}-home"},
            },
            {
                "name": "workspace-secrets",
                "secret": {
                    "secretName": workspace_secret_name,
                    "optional": False,
                    "defaultMode": 0o440,
                },
            },
            {"name": "run", "emptyDir": {"medium": "Memory"}},
            {"name": "tmp", "emptyDir": {}},
        ],
    }
    if workspace_priority_class:
        pod_spec["priorityClassName"] = workspace_priority_class
    if image_pull_secret:
        pod_spec["imagePullSecrets"] = [{"name": image_pull_secret}]
    if gpu_profile is not None:
        main_resources = pod_spec["containers"][0]["resources"]
        main_resources["requests"][gpu_profile.resource_name] = gpu_profile.count
        main_resources["limits"][gpu_profile.resource_name] = gpu_profile.count
        if gpu_profile.runtime_class_name:
            pod_spec["runtimeClassName"] = gpu_profile.runtime_class_name
        if gpu_profile.supplemental_groups:
            pod_spec["securityContext"]["supplementalGroups"] = list(
                gpu_profile.supplemental_groups
            )
        if gpu_profile.node_selector:
            pod_spec["nodeSelector"] = dict(gpu_profile.node_selector)
        if gpu_profile.tolerations:
            pod_spec["tolerations"] = [
                toleration.model_dump(mode="json", by_alias=True, exclude_none=True)
                for toleration in gpu_profile.tolerations
            ]

    if insights_enabled:
        if not all((instance_id, insights_endpoint, insights_credential)):
            raise ValueError(
                "enabled Insights requires instance identity, endpoint, and credential"
            )
        credential_environment: dict[str, Any] = {"name": "DEVBOXES_INSIGHTS_CREDENTIAL"}
        if insights_secret_name:
            credential_environment["valueFrom"] = {
                "secretKeyRef": {
                    "name": insights_secret_name,
                    "key": "credential",
                }
            }
        else:
            credential_environment["value"] = insights_credential
        pod_spec["containers"].append(
            {
                "name": "insights-agent",
                # Keep the privacy boundary on the release workspace image. A
                # profile image overrides only the interactive workspace.
                "image": workspace_image,
                "imagePullPolicy": "IfNotPresent",
                "command": ["python3", "/usr/local/bin/devbox-insights-agent"],
                "env": [
                    {"name": "HOME", "value": "/home/dev"},
                    {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
                    {"name": "DEVBOXES_INSIGHTS_ENDPOINT", "value": insights_endpoint},
                    credential_environment,
                    {
                        "name": "DEVBOXES_INSIGHTS_SCAN_INTERVAL_SECONDS",
                        "value": str(insights_scan_interval_seconds),
                    },
                    {
                        "name": "DEVBOXES_INSIGHTS_REPOSITORY_DEPTH",
                        "value": str(insights_repository_depth),
                    },
                    {
                        "name": "DEVBOXES_INSIGHTS_MAX_QUEUE_BYTES",
                        "value": str(insights_max_queue_bytes),
                    },
                    {
                        "name": "DEVBOXES_INSIGHTS_MAX_QUEUE_AGE_SECONDS",
                        "value": str(insights_max_queue_age_seconds),
                    },
                ],
                "resources": {
                    "requests": {"cpu": "25m", "memory": "32Mi"},
                    "limits": {"cpu": "200m", "memory": "128Mi"},
                },
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 1000,
                    "runAsGroup": 1000,
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"]},
                },
                "volumeMounts": [{"name": "home", "mountPath": "/home/dev"}],
            }
        )

    deployment_annotations = annotations(request, now, instance_id, gpu_profile)
    deployment_annotations[ANNOTATION_INSIGHTS_STATE] = (
        "collecting" if insights_enabled else "disabled"
    )
    template_annotations = {ANNOTATION_INSTANCE_ID: instance_id} if instance_id is not None else {}
    if gpu_profile is not None:
        template_annotations.update(
            {
                ANNOTATION_GPU_PROFILE: gpu_profile.name,
                ANNOTATION_GPU_RESOURCE: gpu_profile.resource_name,
                ANNOTATION_GPU_COUNT: str(gpu_profile.count),
            }
        )
    manifest: dict[str, Any] = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": box_labels,
            "annotations": deployment_annotations,
        },
        "spec": {
            "replicas": 1,
            # The prepared workspace image is intentionally comprehensive and
            # can take longer than Kubernetes' ten-minute default to pull on a
            # cold node or slower registry path.
            "progressDeadlineSeconds": 1800,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {LABEL_NAME: request.name}},
            "template": {
                "metadata": {
                    "labels": box_labels,
                    "annotations": template_annotations,
                },
                "spec": pod_spec,
            },
        },
    }
    if insights_enabled:
        template_hash = hashlib.sha256(
            json.dumps(
                {
                    "pod_spec": manifest["spec"]["template"]["spec"],
                    "credential_fingerprint": hashlib.sha256(
                        str(insights_credential).encode()
                    ).hexdigest(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        manifest["metadata"]["annotations"][ANNOTATION_INSIGHTS_TEMPLATE_HASH] = template_hash
        manifest["spec"]["template"]["metadata"]["annotations"][
            ANNOTATION_INSIGHTS_TEMPLATE_HASH
        ] = template_hash
    return manifest


def build_service(
    request: CreateDevboxRequest,
    namespace: str,
    service_type: str = "LoadBalancer",
    annotations: dict[str, str] | None = None,
    node_port: int | None = None,
    load_balancer_class: str | None = None,
    external_traffic_policy: str = "Cluster",
    load_balancer_source_ranges: list[str] | None = None,
) -> dict[str, Any]:
    """Build the externally reachable SSH Service for a devbox."""
    port: dict[str, Any] = {
        "name": "ssh",
        "port": 22,
        "targetPort": "ssh",
        "protocol": "TCP",
    }
    if node_port is not None:
        port["nodePort"] = node_port

    spec: dict[str, Any] = {
        "type": service_type,
        "externalTrafficPolicy": external_traffic_policy,
        "selector": {LABEL_NAME: request.name},
        "ports": [port],
    }
    if load_balancer_class:
        spec["loadBalancerClass"] = load_balancer_class
    if load_balancer_source_ranges:
        spec["loadBalancerSourceRanges"] = load_balancer_source_ranges

    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": f"{resource_name(request.name)}-ssh",
            "namespace": namespace,
            "labels": labels(request.name),
            "annotations": annotations or {},
        },
        "spec": spec,
    }
