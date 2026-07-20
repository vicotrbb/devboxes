from datetime import UTC, datetime

import pytest

from devboxes_controller.config import GpuProfile, GpuToleration
from devboxes_controller.models import CreateDevboxRequest, Preset
from devboxes_controller.resources import (
    ANNOTATION_GPU_CONFIG,
    ANNOTATION_GPU_COUNT,
    ANNOTATION_GPU_PROFILE,
    ANNOTATION_GPU_RESOURCE,
    ANNOTATION_INSTANCE_ID,
    ANNOTATION_TTL_HOURS,
    build_deployment,
    build_pvc,
    build_service,
)


def request() -> CreateDevboxRequest:
    return CreateDevboxRequest(
        name="atlas",
        preset=Preset.MEDIUM,
        ttl_hours=24,
        repository="owner/atlas",
    )


def test_deployment_is_hardened_and_ready_for_tmux_workspace() -> None:
    deployment = build_deployment(
        request(),
        "devboxes",
        "ghcr.io/vicotrbb/devboxes-workspace:test",
        "devboxes-workspace",
        "devboxes-workspace",
        now=datetime(2026, 7, 9, tzinfo=UTC),
    )
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["fsGroup"] == 1000
    assert pod["securityContext"]["fsGroupChangePolicy"] == "OnRootMismatch"
    assert deployment["spec"]["progressDeadlineSeconds"] == 1800
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    assert deployment["metadata"]["annotations"][ANNOTATION_TTL_HOURS] == "24"
    assert container["resources"]["limits"] == {"memory": "8Gi"}
    assert "cpu" not in container["resources"]["limits"]
    assert container["securityContext"]["allowPrivilegeEscalation"] is True
    assert "AUDIT_WRITE" in container["securityContext"]["capabilities"]["add"]
    assert "SYS_CHROOT" in container["securityContext"]["capabilities"]["add"]
    assert "SYS_ADMIN" not in container["securityContext"]["capabilities"]["add"]
    secret_volume = next(
        volume for volume in pod["volumes"] if volume["name"] == "workspace-secrets"
    )
    assert secret_volume["secret"]["defaultMode"] == 0o440


def test_pvc_and_service_support_explicit_storage_and_load_balancer() -> None:
    pvc = build_pvc(request(), "devboxes", "fast-storage")
    service = build_service(request(), "devboxes")

    assert pvc["spec"]["storageClassName"] == "fast-storage"
    assert pvc["spec"]["resources"]["requests"]["storage"] == "30Gi"
    assert service["spec"]["type"] == "LoadBalancer"
    assert service["spec"]["ports"][0]["port"] == 22
    assert service["spec"]["ports"][0]["targetPort"] == "ssh"


def test_pvc_uses_cluster_default_storage_when_class_is_omitted() -> None:
    pvc = build_pvc(request(), "devboxes", None)

    assert "storageClassName" not in pvc["spec"]


def test_service_supports_node_port_and_provider_annotations() -> None:
    service = build_service(
        request(),
        "devboxes",
        service_type="NodePort",
        annotations={"example.com/network": "private"},
        node_port=30222,
        external_traffic_policy="Local",
    )

    assert service["metadata"]["annotations"] == {"example.com/network": "private"}
    assert service["spec"]["type"] == "NodePort"
    assert service["spec"]["externalTrafficPolicy"] == "Local"
    assert service["spec"]["ports"][0]["nodePort"] == 30222


def test_insights_sidecar_is_opt_in_scoped_and_hardened() -> None:
    instance_id = "99999999-9999-4999-8999-999999999999"
    credential = f"v1.{instance_id}.atlas.{'a' * 43}"
    deployment = build_deployment(
        request(),
        "devboxes",
        "ghcr.io/vicotrbb/devboxes-workspace:test",
        "devboxes-workspace",
        "devboxes-workspace",
        instance_id=instance_id,
        insights_enabled=True,
        insights_endpoint="http://devboxes:8000",
        insights_credential=credential,
        insights_scan_interval_seconds=45,
        insights_repository_depth=3,
        insights_max_queue_bytes=1048576,
    )
    pod = deployment["spec"]["template"]["spec"]
    main = pod["containers"][0]
    sidecar = pod["containers"][1]
    main_environment = {item["name"]: item.get("value") for item in main["env"]}
    sidecar_environment = {item["name"]: item.get("value") for item in sidecar["env"]}

    assert deployment["metadata"]["annotations"][ANNOTATION_INSTANCE_ID] == instance_id
    assert main_environment["DEVBOXES_INSIGHTS_ENABLED"] == "true"
    assert main_environment["OTEL_LOGS_EXPORTER"] == "none"
    assert main_environment["OTEL_TRACES_EXPORTER"] == "none"
    assert main_environment["OTEL_METRICS_INCLUDE_SESSION_ID"] == "false"
    assert main_environment["OTEL_METRICS_INCLUDE_ACCOUNT_UUID"] == "false"
    assert main_environment["OTEL_EXPORTER_OTLP_METRICS_PROTOCOL"] == "http/json"
    assert sidecar["image"] == main["image"]
    assert sidecar["command"] == ["python3", "/usr/local/bin/devbox-insights-agent"]
    assert sidecar_environment["DEVBOXES_INSIGHTS_CREDENTIAL"] == credential
    assert sidecar_environment["DEVBOXES_INSIGHTS_ENDPOINT"] == "http://devboxes:8000"
    assert sidecar["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "runAsGroup": 1000,
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert sidecar["volumeMounts"] == [{"name": "home", "mountPath": "/home/dev"}]
    assert all(item["name"] != "DEVBOXES_INSIGHTS_CREDENTIAL" for item in main["env"])


def test_enabled_insights_requires_a_complete_scoped_configuration() -> None:
    with pytest.raises(ValueError, match="requires instance"):
        build_deployment(
            request(),
            "devboxes",
            "workspace:test",
            "devboxes-workspace",
            "devboxes-workspace",
            insights_enabled=True,
        )


def test_gpu_profile_is_applied_only_to_the_workspace_container() -> None:
    instance_id = "99999999-9999-4999-8999-999999999999"
    profile = GpuProfile(
        name="nvidia-l4",
        displayName="NVIDIA L4",
        description="One dedicated inference GPU",
        resourceName="nvidia.com/gpu",
        count=1,
        workspaceImage="registry.example/devboxes-cuda:12.8",
        runtimeClassName="nvidia",
        supplementalGroups=[44, 109],
        nodeSelector={"accelerator": "nvidia"},
        tolerations=[GpuToleration(key="nvidia.com/gpu", operator="Exists", effect="NoSchedule")],
    )
    deployment = build_deployment(
        request(),
        "devboxes",
        "ghcr.io/vicotrbb/devboxes-workspace:test",
        "devboxes-workspace",
        "devboxes-workspace",
        gpu_profile=profile,
        instance_id=instance_id,
        insights_enabled=True,
        insights_endpoint="http://devboxes:8000",
        insights_credential=f"v1.{instance_id}.atlas.{'a' * 43}",
    )
    annotations = deployment["metadata"]["annotations"]
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    environment = {item["name"]: item["value"] for item in container["env"]}

    assert annotations[ANNOTATION_GPU_PROFILE] == "nvidia-l4"
    assert annotations[ANNOTATION_GPU_RESOURCE] == "nvidia.com/gpu"
    assert annotations[ANNOTATION_GPU_COUNT] == "1"
    assert '"runtimeClassName":"nvidia"' in annotations[ANNOTATION_GPU_CONFIG]
    assert container["image"] == "registry.example/devboxes-cuda:12.8"
    assert container["resources"]["requests"]["nvidia.com/gpu"] == 1
    assert container["resources"]["limits"]["nvidia.com/gpu"] == 1
    assert environment["DEVBOX_GPU_PROFILE"] == "nvidia-l4"
    assert environment["DEVBOX_GPU_RESOURCE"] == "nvidia.com/gpu"
    assert environment["DEVBOX_GPU_COUNT"] == "1"
    assert environment["DEVBOX_GPU_SUPPLEMENTAL_GROUPS"] == "44,109"
    assert pod["runtimeClassName"] == "nvidia"
    assert pod["securityContext"]["supplementalGroups"] == [44, 109]
    assert pod["nodeSelector"] == {"accelerator": "nvidia"}
    assert pod["tolerations"] == [
        {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}
    ]
    assert "nvidia.com/gpu" not in pod["containers"][1]["resources"]["requests"]
    assert "nvidia.com/gpu" not in pod["containers"][1]["resources"]["limits"]
    assert pod["containers"][1]["image"] == "ghcr.io/vicotrbb/devboxes-workspace:test"
