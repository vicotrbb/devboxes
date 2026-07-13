from datetime import UTC, datetime, timedelta
from typing import Any

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
    return f"devbox-{name}"


def labels(name: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": "devbox",
        "app.kubernetes.io/instance": resource_name(name),
        "app.kubernetes.io/part-of": "devboxes",
        LABEL_MANAGED_BY: MANAGED_BY,
        LABEL_NAME: name,
    }


def annotations(request: CreateDevboxRequest, now: datetime | None = None) -> dict[str, str]:
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
    return result


def build_pvc(
    request: CreateDevboxRequest,
    namespace: str,
    storage_class: str | None,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": f"{resource_name(request.name)}-home",
            "namespace": namespace,
            "labels": labels(request.name),
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
    now: datetime | None = None,
) -> dict[str, Any]:
    name = resource_name(request.name)
    box_labels = labels(request.name)
    env = [
        {"name": "DEVBOX_NAME", "value": request.name},
        {"name": "DEVBOX_PRESET", "value": request.preset.value},
    ]
    if request.repository:
        env.append({"name": "DEVBOX_REPOSITORY", "value": request.repository})

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
                "image": workspace_image,
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
                    "allowPrivilegeEscalation": True,
                    "capabilities": {
                        "drop": ["ALL"],
                        "add": [
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

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": box_labels,
            "annotations": annotations(request, now),
        },
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {LABEL_NAME: request.name}},
            "template": {
                "metadata": {"labels": box_labels},
                "spec": pod_spec,
            },
        },
    }


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
