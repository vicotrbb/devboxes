from datetime import UTC, datetime

from devboxes_controller.models import CreateDevboxRequest, Preset
from devboxes_controller.resources import (
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
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    assert deployment["metadata"]["annotations"][ANNOTATION_TTL_HOURS] == "24"
    assert container["resources"]["limits"] == {"memory": "8Gi"}
    assert "cpu" not in container["resources"]["limits"]
    assert container["securityContext"]["allowPrivilegeEscalation"] is True
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
