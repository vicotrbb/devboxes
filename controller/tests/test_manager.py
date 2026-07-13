import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from kubernetes.client.exceptions import ApiException

from devboxes_controller.config import Settings
from devboxes_controller.manager import DevboxManager, _service_endpoint, _ttl_hours
from devboxes_controller.models import CreateDevboxRequest, Preset
from devboxes_controller.resources import (
    ANNOTATION_CREATED_AT,
    ANNOTATION_EXPIRES_AT,
    ANNOTATION_PRESET,
    ANNOTATION_STORAGE,
    ANNOTATION_TTL_HOURS,
    LABEL_NAME,
)


def test_ttl_hours_rejects_missing_invalid_and_out_of_range_values() -> None:
    assert _ttl_hours(None, default=24, maximum=168) == 24
    assert _ttl_hours("invalid", default=24, maximum=168) == 24
    assert _ttl_hours("0", default=24, maximum=168) == 24
    assert _ttl_hours("169", default=24, maximum=168) == 24
    assert _ttl_hours("72", default=24, maximum=168) == 72


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
