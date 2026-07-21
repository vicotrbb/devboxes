import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from kubernetes.client.exceptions import ApiException

from devboxes_controller.config import GpuProfile, Settings
from devboxes_controller.manager import (
    DevboxConflictError,
    DevboxManager,
    DevboxNotFoundError,
    _gpu_allocation,
    _resolved_gpu_profile,
    _service_endpoint,
    _state,
    _ttl_hours,
)
from devboxes_controller.models import CreateDevboxRequest, DevboxState, GpuRequest, Preset
from devboxes_controller.resources import (
    ANNOTATION_CREATED_AT,
    ANNOTATION_EXPIRES_AT,
    ANNOTATION_GPU_CONFIG,
    ANNOTATION_GPU_PROFILE,
    ANNOTATION_INSIGHTS_STATE,
    ANNOTATION_INSTANCE_ID,
    ANNOTATION_PRESET,
    ANNOTATION_STORAGE,
    ANNOTATION_TTL_HOURS,
    LABEL_NAME,
)

from .fakes import sample_devbox


def test_ttl_hours_rejects_missing_invalid_and_out_of_range_values() -> None:
    assert _ttl_hours(None, default=24, maximum=168) == 24
    assert _ttl_hours("invalid", default=24, maximum=168) == 24
    assert _ttl_hours("0", default=24, maximum=168) == 24
    assert _ttl_hours("169", default=24, maximum=168) == 24
    assert _ttl_hours("72", default=24, maximum=168) == 72


def test_readiness_reports_kubernetes_api_failures() -> None:
    apps = Mock()
    manager = DevboxManager(
        Settings(access_token="test-access-token-at-least-32-characters"),
        apps_api=apps,
        core_api=Mock(),
    )

    assert asyncio.run(manager.ready()) is True

    apps.list_namespaced_deployment.side_effect = ApiException(status=503)
    assert asyncio.run(manager.ready()) is False


def test_create_rejects_installation_ttl_limit_and_active_name() -> None:
    apps = Mock()
    manager = DevboxManager(
        Settings(
            access_token="test-access-token-at-least-32-characters",
            max_ttl_hours=24,
        ),
        apps_api=apps,
        core_api=Mock(),
    )

    with pytest.raises(ValueError, match="cannot exceed 24"):
        asyncio.run(manager.create(CreateDevboxRequest(name="atlas", ttl_hours=25)))

    with pytest.raises(DevboxConflictError, match="already exists"):
        asyncio.run(manager.create(CreateDevboxRequest(name="atlas", ttl_hours=24)))


def test_create_removes_deployment_when_service_creation_fails() -> None:
    apps = Mock()
    apps.read_namespaced_deployment.side_effect = ApiException(status=404)
    core = Mock()
    core.read_namespaced_persistent_volume_claim.side_effect = ApiException(status=404)
    core.create_namespaced_service.side_effect = ApiException(status=500)
    manager = DevboxManager(
        Settings(access_token="test-access-token-at-least-32-characters"),
        apps_api=apps,
        core_api=core,
    )

    with pytest.raises(ApiException):
        asyncio.run(manager.create(CreateDevboxRequest(name="atlas")))

    apps.delete_namespaced_deployment.assert_called_once()
    core.create_namespaced_persistent_volume_claim.assert_called_once()


@pytest.mark.parametrize("purge", [False, True])
def test_delete_preserves_or_purges_storage_explicitly(purge: bool) -> None:
    apps = Mock()
    core = Mock()
    manager = DevboxManager(
        Settings(access_token="test-access-token-at-least-32-characters"),
        apps_api=apps,
        core_api=core,
    )

    result = asyncio.run(manager.delete("atlas", purge))

    apps.delete_namespaced_deployment.assert_called_once()
    core.delete_namespaced_service.assert_called_once()
    assert core.delete_namespaced_persistent_volume_claim.called is purge
    assert result.purged is purge
    assert ("deleted" if purge else "retained") in result.message


def test_delete_rejects_unknown_devbox() -> None:
    apps = Mock()
    apps.read_namespaced_deployment.side_effect = ApiException(status=404)
    manager = DevboxManager(
        Settings(access_token="test-access-token-at-least-32-characters"),
        apps_api=apps,
        core_api=Mock(),
    )

    with pytest.raises(DevboxNotFoundError):
        asyncio.run(manager.delete("missing", purge=False))


def test_stop_expired_stops_only_active_expired_boxes() -> None:
    now = datetime.now(UTC)
    expired = sample_devbox("expired")
    expired.expires_at = now - timedelta(minutes=1)
    stopped = sample_devbox("stopped", DevboxState.STOPPED)
    stopped.expires_at = now - timedelta(hours=1)
    active = sample_devbox("active")
    active.expires_at = now + timedelta(hours=1)
    apps = Mock()
    manager = DevboxManager(
        Settings(access_token="test-access-token-at-least-32-characters"),
        apps_api=apps,
        core_api=Mock(),
    )
    manager.list = AsyncMock(return_value=[expired, stopped, active])  # type: ignore[method-assign]

    assert asyncio.run(manager.stop_expired()) == ["expired"]

    name, namespace, body = apps.patch_namespaced_deployment.call_args.args
    assert (name, namespace) == ("devbox-expired", "devboxes")
    assert body["spec"]["replicas"] == 0


def test_state_reports_readiness_and_known_failures() -> None:
    ready_pod = SimpleNamespace(
        status=SimpleNamespace(phase="Running", conditions=[], container_statuses=[])
    )
    failed_pod = SimpleNamespace(
        status=SimpleNamespace(
            phase="Running",
            conditions=[],
            container_statuses=[
                SimpleNamespace(
                    state=SimpleNamespace(waiting=SimpleNamespace(reason="ImagePullBackOff"))
                )
            ],
        )
    )

    assert _state(0, None, False, None)[0] is DevboxState.STOPPED
    assert _state(1, ready_pod, True, None) == (
        DevboxState.STARTING,
        "Waiting for an SSH service address",
    )
    assert _state(1, failed_pod, False, None) == (
        DevboxState.DEGRADED,
        "ImagePullBackOff",
    )
    assert _state(1, None, False, None) == (
        DevboxState.STARTING,
        "Preparing workspace and SSH",
    )


def test_state_surfaces_scheduler_capacity_failures() -> None:
    pending_pod = SimpleNamespace(
        status=SimpleNamespace(
            phase="Pending",
            conditions=[
                SimpleNamespace(
                    type="PodScheduled",
                    status="False",
                    message="0/3 nodes are available: 3 Insufficient nvidia.com/gpu.",
                )
            ],
            container_statuses=[],
        )
    )

    assert _state(1, pending_pod, False, None) == (
        DevboxState.STARTING,
        "Scheduling blocked: 0/3 nodes are available: 3 Insufficient nvidia.com/gpu.",
    )


def test_start_renews_the_original_ttl_atomically() -> None:
    apps = Mock()
    apps.read_namespaced_deployment.return_value = SimpleNamespace(
        metadata=SimpleNamespace(annotations={ANNOTATION_TTL_HOURS: "72"})
    )
    settings = Settings(
        access_token="test-access-token-at-least-32-characters",
        default_ttl_hours=24,
        max_ttl_hours=168,
    )
    manager = DevboxManager(settings, apps_api=apps, core_api=Mock())
    expected = Mock()
    manager.get = AsyncMock(return_value=expected)  # type: ignore[method-assign]

    before = datetime.now(UTC) + timedelta(hours=71, minutes=59)
    result = asyncio.run(manager.scale("atlas", 1))
    after = datetime.now(UTC) + timedelta(hours=72, minutes=1)

    body = apps.patch_namespaced_deployment.call_args.args[2]
    expiry = datetime.fromisoformat(body["metadata"]["annotations"][ANNOTATION_EXPIRES_AT])
    assert body["spec"]["replicas"] == 1
    assert before < expiry < after
    apps.patch_namespaced_deployment_scale.assert_not_called()
    assert result is expected


def test_stop_uses_the_scale_subresource_without_renewing_ttl() -> None:
    apps = Mock()
    settings = Settings(access_token="test-access-token-at-least-32-characters")
    manager = DevboxManager(settings, apps_api=apps, core_api=Mock())
    manager.get = AsyncMock(return_value=Mock())  # type: ignore[method-assign]

    asyncio.run(manager.scale("atlas", 0))

    apps.patch_namespaced_deployment_scale.assert_called_once_with(
        "devbox-atlas",
        "devboxes",
        {"spec": {"replicas": 0}},
    )
    apps.patch_namespaced_deployment.assert_not_called()


def test_create_expands_a_retained_home_volume_for_a_larger_preset() -> None:
    apps = Mock()
    apps.read_namespaced_deployment.side_effect = ApiException(status=404)
    core = Mock()
    core.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
        spec=SimpleNamespace(resources=SimpleNamespace(requests={"storage": "20Gi"}))
    )
    settings = Settings(access_token="test-access-token-at-least-32-characters")
    manager = DevboxManager(settings, apps_api=apps, core_api=core)
    expected = Mock()
    manager.get = AsyncMock(return_value=expected)  # type: ignore[method-assign]

    result = asyncio.run(
        manager.create(CreateDevboxRequest(name="atlas", preset=Preset.LARGE, ttl_hours=24))
    )

    core.create_namespaced_persistent_volume_claim.assert_not_called()
    core.patch_namespaced_persistent_volume_claim.assert_called_once_with(
        "devbox-atlas-home",
        "devboxes",
        {"spec": {"resources": {"requests": {"storage": "50Gi"}}}},
    )
    deployment = apps.create_namespaced_deployment.call_args.args[1]
    assert deployment["metadata"]["annotations"][ANNOTATION_STORAGE] == "50Gi"
    assert result is expected


def test_node_port_endpoint_uses_configured_node_address() -> None:
    service = SimpleNamespace(
        spec=SimpleNamespace(
            type="NodePort",
            ports=[SimpleNamespace(node_port=30222)],
        ),
        status=None,
    )

    assert _service_endpoint(service, "devboxes.example.test") == (
        "devboxes.example.test",
        30222,
    )


def test_load_balancer_endpoint_uses_ingress_hostname() -> None:
    service = SimpleNamespace(
        spec=SimpleNamespace(type="LoadBalancer", ports=[]),
        status=SimpleNamespace(
            load_balancer=SimpleNamespace(
                ingress=[SimpleNamespace(ip=None, hostname="box.example.test")]
            )
        ),
    )

    assert _service_endpoint(service, None) == ("box.example.test", 22)


def test_create_uses_portable_node_port_and_default_storage_configuration() -> None:
    apps = Mock()
    apps.read_namespaced_deployment.side_effect = ApiException(status=404)
    core = Mock()
    core.read_namespaced_persistent_volume_claim.side_effect = ApiException(status=404)
    settings = Settings(
        access_token="test-access-token-at-least-32-characters",
        workspace_service_type="NodePort",
        workspace_service_host="dev-node.example.test",
        workspace_service_annotations={"example.test/private": "true"},
    )
    manager = DevboxManager(settings, apps_api=apps, core_api=core)
    manager.get = AsyncMock(return_value=Mock())  # type: ignore[method-assign]

    asyncio.run(manager.create(CreateDevboxRequest(name="atlas")))

    pvc = core.create_namespaced_persistent_volume_claim.call_args.args[1]
    deployment = apps.create_namespaced_deployment.call_args.args[1]
    service = core.create_namespaced_service.call_args.args[1]
    assert "storageClassName" not in pvc["spec"]
    assert deployment["spec"]["template"]["spec"]["serviceAccountName"] == "devboxes-workspace"
    assert "priorityClassName" not in deployment["spec"]["template"]["spec"]
    assert service["spec"]["type"] == "NodePort"
    assert service["metadata"]["annotations"] == {"example.test/private": "true"}
    assert "nodePort" not in service["spec"]["ports"][0]


def test_create_resolves_the_default_gpu_profile_before_kubernetes_writes() -> None:
    apps = Mock()
    apps.read_namespaced_deployment.side_effect = ApiException(status=404)
    core = Mock()
    core.read_namespaced_persistent_volume_claim.side_effect = ApiException(status=404)
    profile = GpuProfile(
        name="nvidia-l4",
        displayName="NVIDIA L4",
        resourceName="nvidia.com/gpu",
        count=1,
    )
    manager = DevboxManager(
        Settings(
            access_token="test-access-token-at-least-32-characters",
            gpu_enabled=True,
            gpu_default_profile="nvidia-l4",
            gpu_profiles=[profile],
        ),
        apps_api=apps,
        core_api=core,
    )
    manager.get = AsyncMock(return_value=Mock())  # type: ignore[method-assign]

    asyncio.run(manager.create(CreateDevboxRequest(name="atlas", gpu=GpuRequest())))

    deployment = apps.create_namespaced_deployment.call_args.args[1]
    assert deployment["metadata"]["annotations"][ANNOTATION_GPU_PROFILE] == "nvidia-l4"
    assert (
        deployment["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"][
            "nvidia.com/gpu"
        ]
        == 1
    )


def test_create_rejects_gpu_requests_when_the_feature_is_disabled() -> None:
    apps = Mock()
    core = Mock()
    manager = DevboxManager(
        Settings(access_token="test-access-token-at-least-32-characters"),
        apps_api=apps,
        core_api=core,
    )

    with pytest.raises(ValueError, match="disabled by the operator"):
        asyncio.run(manager.create(CreateDevboxRequest(name="atlas", gpu=GpuRequest())))

    apps.read_namespaced_deployment.assert_not_called()
    core.create_namespaced_persistent_volume_claim.assert_not_called()


def _legacy_deployment(replicas: int) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name="devbox-atlas",
            labels={LABEL_NAME: "atlas"},
            annotations={
                ANNOTATION_CREATED_AT: now.isoformat(),
                ANNOTATION_EXPIRES_AT: (now + timedelta(hours=24)).isoformat(),
                ANNOTATION_PRESET: "small",
                ANNOTATION_STORAGE: "20Gi",
                ANNOTATION_TTL_HOURS: "24",
            },
        ),
        spec=SimpleNamespace(
            replicas=replicas,
            template=SimpleNamespace(
                metadata=SimpleNamespace(annotations={}),
                spec=SimpleNamespace(containers=[SimpleNamespace(name="devbox")]),
            ),
        ),
    )


def test_resolved_gpu_profile_snapshot_survives_later_operator_changes() -> None:
    profile = GpuProfile(
        name="nvidia-l4",
        displayName="NVIDIA L4",
        resourceName="nvidia.com/gpu",
        count=1,
        runtimeClassName="nvidia",
    )
    annotations = {
        ANNOTATION_GPU_PROFILE: profile.name,
        ANNOTATION_GPU_CONFIG: profile.model_dump_json(by_alias=True, exclude_none=True),
    }
    settings = Settings(access_token="test-access-token-at-least-32-characters")

    assert _resolved_gpu_profile(annotations, settings) == profile
    assert _gpu_allocation(annotations).model_dump() == {
        "profile": "nvidia-l4",
        "display_name": "NVIDIA L4",
        "resource_name": "nvidia.com/gpu",
        "count": 1,
    }


def test_insights_create_scopes_identity_to_the_retained_home_volume() -> None:
    apps = Mock()
    apps.read_namespaced_deployment.side_effect = ApiException(status=404)
    core = Mock()
    core.read_namespaced_persistent_volume_claim.side_effect = ApiException(status=404)
    core.read_namespaced_secret.side_effect = ApiException(status=404)
    manager = DevboxManager(
        Settings(
            access_token="test-access-token-at-least-32-characters",
            insights_enabled=True,
        ),
        apps_api=apps,
        core_api=core,
    )
    manager.get = AsyncMock(return_value=Mock())  # type: ignore[method-assign]

    asyncio.run(manager.create(CreateDevboxRequest(name="atlas")))

    pvc = core.create_namespaced_persistent_volume_claim.call_args.args[1]
    deployment = apps.create_namespaced_deployment.call_args.args[1]
    instance_id = pvc["metadata"]["annotations"][ANNOTATION_INSTANCE_ID]
    assert deployment["metadata"]["annotations"][ANNOTATION_INSTANCE_ID] == instance_id
    sidecar = deployment["spec"]["template"]["spec"]["containers"][1]
    credential_environment = next(
        item for item in sidecar["env"] if item["name"] == "DEVBOXES_INSIGHTS_CREDENTIAL"
    )
    assert credential_environment["valueFrom"]["secretKeyRef"] == {
        "name": "devbox-atlas-insights",
        "key": "credential",
    }
    secret = core.create_namespaced_secret.call_args.args[1]
    credential = secret["stringData"]["credential"]
    assert credential.startswith(f"v1.{instance_id}.atlas.")
    assert "test-access-token" not in credential


def test_active_legacy_workspace_is_marked_restart_required_without_template_patch() -> None:
    deployment = _legacy_deployment(1)
    apps = Mock()
    apps.list_namespaced_deployment.return_value = SimpleNamespace(items=[deployment])
    core = Mock()
    core.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
        metadata=SimpleNamespace(annotations={})
    )
    manager = DevboxManager(
        Settings(
            access_token="test-access-token-at-least-32-characters",
            insights_enabled=True,
        ),
        apps_api=apps,
        core_api=core,
    )

    assert asyncio.run(manager.reconcile_insights()) == ["atlas"]

    patch = apps.patch_namespaced_deployment.call_args.args[2]
    assert "spec" not in patch
    assert patch["metadata"]["annotations"][ANNOTATION_INSIGHTS_STATE] == "restart_required"
    assert ANNOTATION_INSTANCE_ID in patch["metadata"]["annotations"]
    core.patch_namespaced_persistent_volume_claim.assert_called_once()


def test_stopped_legacy_workspace_template_is_reconciled_without_starting_it() -> None:
    deployment = _legacy_deployment(0)
    apps = Mock()
    apps.list_namespaced_deployment.return_value = SimpleNamespace(items=[deployment])
    apps.read_namespaced_deployment.return_value = _legacy_deployment(0)
    core = Mock()
    core.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
        metadata=SimpleNamespace(
            annotations={ANNOTATION_INSTANCE_ID: "99999999-9999-4999-8999-999999999999"}
        )
    )
    manager = DevboxManager(
        Settings(
            access_token="test-access-token-at-least-32-characters",
            insights_enabled=True,
        ),
        apps_api=apps,
        core_api=core,
    )

    assert asyncio.run(manager.reconcile_insights()) == ["atlas"]

    patch = apps.patch_namespaced_deployment.call_args.args[2]
    assert "replicas" not in patch["spec"]
    containers = patch["spec"]["template"]["spec"]["containers"]
    assert [item["name"] for item in containers] == ["devbox", "insights-agent"]
    assert patch["metadata"]["annotations"][ANNOTATION_INSIGHTS_STATE] == "collecting"


def test_insights_reconciliation_uses_the_pinned_gpu_snapshot() -> None:
    profile = GpuProfile(
        name="amd-rocm",
        displayName="AMD ROCm GPU",
        resourceName="amd.com/gpu",
        count=1,
        workspaceImage="registry.example/devboxes-rocm:6.4",
        runtimeClassName="gpu-runtime",
        supplementalGroups=[44, 109],
    )
    deployment = _legacy_deployment(0)
    deployment.metadata.annotations.update(
        {
            ANNOTATION_GPU_PROFILE: profile.name,
            ANNOTATION_GPU_CONFIG: profile.model_dump_json(
                by_alias=True,
                exclude_none=True,
            ),
        }
    )
    apps = Mock()
    apps.list_namespaced_deployment.return_value = SimpleNamespace(items=[deployment])
    apps.read_namespaced_deployment.return_value = _legacy_deployment(0)
    core = Mock()
    core.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
        metadata=SimpleNamespace(
            annotations={ANNOTATION_INSTANCE_ID: "99999999-9999-4999-8999-999999999999"}
        )
    )
    manager = DevboxManager(
        Settings(
            access_token="test-access-token-at-least-32-characters",
            insights_enabled=True,
        ),
        apps_api=apps,
        core_api=core,
    )

    assert asyncio.run(manager.reconcile_insights()) == ["atlas"]

    patch = apps.patch_namespaced_deployment.call_args.args[2]
    pod = patch["spec"]["template"]["spec"]
    main, sidecar = pod["containers"]
    main_environment = {item["name"]: item["value"] for item in main["env"]}
    assert main["image"] == "registry.example/devboxes-rocm:6.4"
    assert main["resources"]["requests"]["amd.com/gpu"] == 1
    assert main["resources"]["limits"]["amd.com/gpu"] == 1
    assert main_environment["DEVBOX_GPU_SUPPLEMENTAL_GROUPS"] == "44,109"
    assert pod["runtimeClassName"] == "gpu-runtime"
    assert pod["securityContext"]["supplementalGroups"] == [44, 109]
    assert sidecar["image"] == manager.settings.workspace_image
    assert "amd.com/gpu" not in sidecar["resources"]["requests"]


def test_node_port_model_includes_allocated_port_in_ssh_command() -> None:
    now = datetime.now(UTC)
    deployment = SimpleNamespace(
        metadata=SimpleNamespace(
            annotations={
                ANNOTATION_CREATED_AT: now.isoformat(),
                ANNOTATION_EXPIRES_AT: (now + timedelta(hours=24)).isoformat(),
                ANNOTATION_PRESET: "small",
                ANNOTATION_STORAGE: "20Gi",
            },
            labels={LABEL_NAME: "atlas"},
            creation_timestamp=now,
        ),
        spec=SimpleNamespace(replicas=1),
    )
    service = SimpleNamespace(
        spec=SimpleNamespace(type="NodePort", ports=[SimpleNamespace(node_port=30222)]),
        status=None,
    )
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="devbox-atlas-test"),
        status=SimpleNamespace(
            phase="Running",
            conditions=[SimpleNamespace(type="Ready", status="True")],
            container_statuses=[],
        ),
    )
    settings = Settings(
        access_token="test-access-token-at-least-32-characters",
        workspace_service_type="NodePort",
        workspace_service_host="dev-node.example.test",
    )
    manager = DevboxManager(settings, apps_api=Mock(), core_api=Mock())

    box = manager._to_model(deployment, service, pod)

    assert box.ssh_host == "dev-node.example.test"
    assert box.ssh_port == 30222
    assert box.ssh_command == "ssh -t -p 30222 dev@dev-node.example.test"
