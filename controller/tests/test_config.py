import json

import pytest
from pydantic import ValidationError

from devboxes_controller.config import (
    CustomImagePort,
    CustomImageProfile,
    CustomImageResources,
    GpuProfile,
    GpuToleration,
    Settings,
)


def test_access_token_is_required() -> None:
    with pytest.raises(ValidationError, match="access_token"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_node_port_requires_a_reachable_host() -> None:
    with pytest.raises(ValidationError, match="workspace_service_host"):
        Settings(
            access_token="test-access-token-at-least-32-characters",
            workspace_service_type="NodePort",
            _env_file=None,
        )


def test_blank_storage_class_uses_cluster_default() -> None:
    settings = Settings(
        access_token="test-access-token-at-least-32-characters",
        storage_class="",
        _env_file=None,
    )

    assert settings.storage_class is None


def test_default_ttl_cannot_exceed_maximum() -> None:
    with pytest.raises(ValidationError, match="default_ttl_hours"):
        Settings(
            access_token="test-access-token-at-least-32-characters",
            default_ttl_hours=72,
            max_ttl_hours=24,
            _env_file=None,
        )


def test_short_access_token_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(access_token="too-short", _env_file=None)


def test_cli_signing_key_is_optional_but_strong_when_configured() -> None:
    configured = Settings(
        access_token="test-access-token-at-least-32-characters",
        cli_signing_key="x" * 32,
        _env_file=None,
    )
    assert configured.cli_signing_key is not None

    with pytest.raises(ValidationError, match="signing key"):
        Settings(
            access_token="test-access-token-at-least-32-characters",
            cli_signing_key="too-short",
            _env_file=None,
        )


def test_gpu_profiles_are_disabled_by_default_and_resolve_explicitly() -> None:
    profile = GpuProfile(
        name="nvidia-l4",
        displayName="NVIDIA L4",
        resourceName="nvidia.com/gpu",
        count=1,
        runtimeClassName="nvidia",
        tolerations=[GpuToleration(key="nvidia.com/gpu", operator="Exists", effect="NoSchedule")],
    )
    settings = Settings(
        access_token="test-access-token-at-least-32-characters",
        gpu_enabled=True,
        gpu_default_profile="nvidia-l4",
        gpu_profiles=[profile],
        _env_file=None,
    )

    assert settings.resolve_gpu_profile(None) is profile
    assert settings.resolve_gpu_profile("nvidia-l4") is profile
    with pytest.raises(ValueError, match="unknown GPU profile 'amd-rocm'"):
        settings.resolve_gpu_profile("amd-rocm")


def test_enabled_gpu_configuration_requires_a_valid_unique_default() -> None:
    profile = GpuProfile(
        name="nvidia-l4",
        displayName="NVIDIA L4",
        resourceName="nvidia.com/gpu",
        count=1,
    )

    with pytest.raises(ValidationError, match="gpu_default_profile"):
        Settings(
            access_token="test-access-token-at-least-32-characters",
            gpu_enabled=True,
            gpu_profiles=[profile],
            _env_file=None,
        )

    with pytest.raises(ValidationError, match="unique"):
        Settings(
            access_token="test-access-token-at-least-32-characters",
            gpu_default_profile="nvidia-l4",
            gpu_profiles=[profile, profile],
            _env_file=None,
        )


def test_gpu_configuration_bounds_the_profile_catalog() -> None:
    profiles = [
        GpuProfile(
            name=f"gpu-{index}",
            displayName=f"GPU {index}",
            resourceName="example.com/gpu",
            count=1,
        )
        for index in range(33)
    ]

    with pytest.raises(ValidationError, match="at most 32 items"):
        Settings(
            access_token="test-access-token-at-least-32-characters",
            gpu_profiles=profiles,
            _env_file=None,
        )


def test_gpu_profile_rejects_unqualified_resources_and_ambiguous_tolerations() -> None:
    with pytest.raises(ValidationError, match="vendor-qualified"):
        GpuProfile(
            name="invalid",
            displayName="Invalid",
            resourceName="gpu",
            count=1,
        )

    with pytest.raises(ValidationError, match="cannot set a value"):
        GpuToleration(
            key="nvidia.com/gpu",
            operator="Exists",
            value="present",
        )


def test_gpu_profile_rejects_invalid_kubernetes_names() -> None:
    with pytest.raises(ValidationError, match="vendor-qualified"):
        GpuProfile(
            name="invalid",
            displayName="Invalid",
            resourceName="nvidia..com/gpu",
            count=1,
        )

    with pytest.raises(ValidationError, match="DNS subdomain"):
        GpuProfile(
            name="invalid",
            displayName="Invalid",
            resourceName="nvidia.com/gpu",
            count=1,
            runtimeClassName=f"{'x' * 64}.example",
        )

    with pytest.raises(ValidationError, match="container image reference"):
        GpuProfile(
            name="invalid",
            displayName="Invalid",
            resourceName="nvidia.com/gpu",
            count=1,
            workspaceImage="https://registry.example/workspace:cuda latest",
        )

    with pytest.raises(ValidationError, match="duplicate group IDs"):
        GpuProfile(
            name="invalid",
            displayName="Invalid",
            resourceName="amd.com/gpu",
            count=1,
            supplementalGroups=[44, 44],
        )


def test_gpu_profiles_load_from_the_helm_json_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVBOXES_ACCESS_TOKEN", "test-access-token-at-least-32-characters")
    monkeypatch.setenv("DEVBOXES_GPU_ENABLED", "true")
    monkeypatch.setenv("DEVBOXES_GPU_DEFAULT_PROFILE", "nvidia-l4")
    monkeypatch.setenv(
        "DEVBOXES_GPU_PROFILES",
        json.dumps(
            [
                {
                    "name": "nvidia-l4",
                    "displayName": "NVIDIA L4",
                    "description": "  Dedicated inference  ",
                    "resourceName": "nvidia.com/gpu",
                    "count": 1,
                }
            ]
        ),
    )

    settings = Settings(_env_file=None)

    assert settings.resolve_gpu_profile(None).description == "Dedicated inference"


def test_custom_image_profiles_are_disabled_by_default_and_resolve_exact_images() -> None:
    profile = CustomImageProfile(
        name="nginx",
        displayName="NGINX preview",
        image="docker.io/nginxinc/nginx-unprivileged:1.27.5-alpine",
        ports=[CustomImagePort(name="http", containerPort=8080)],
    )
    settings = Settings(
        access_token="test-access-token-at-least-32-characters",
        custom_images_enabled=True,
        custom_images=[profile],
        _env_file=None,
    )

    assert settings.resolve_custom_image("nginx") is profile
    assert settings.resolve_custom_image(profile.image) is profile

    disabled = Settings(
        access_token="test-access-token-at-least-32-characters",
        custom_images=[profile],
        _env_file=None,
    )
    with pytest.raises(ValueError, match="disabled by the operator"):
        disabled.resolve_custom_image("nginx")


def test_custom_image_profiles_reject_unsafe_or_ambiguous_contracts() -> None:
    with pytest.raises(ValidationError, match="container image reference"):
        CustomImageProfile(
            name="bad-image",
            displayName="Bad image",
            image="https://registry.example/nginx:latest",
        )

    with pytest.raises(ValidationError, match="duplicate port"):
        CustomImageProfile(
            name="duplicate-port",
            displayName="Duplicate port",
            image="registry.example/duplicate:1",
            ports=[
                CustomImagePort(name="http", containerPort=8080),
                CustomImagePort(name="https", containerPort=8080),
            ],
        )

    with pytest.raises(ValidationError, match="cpuLimit"):
        CustomImageResources(cpuRequest="1", cpuLimit="500m")

    with pytest.raises(ValidationError, match="greater than or equal to 1024"):
        CustomImagePort(name="http", containerPort=80)

    with pytest.raises(ValidationError, match="cannot define sidecar resources"):
        CustomImageProfile(
            name="workspace-with-resources",
            displayName="Workspace with resources",
            image="registry.example/devboxes-workspace:1",
            mode="workspace",
            resources=CustomImageResources(),
        )

    profile = CustomImageProfile(
        name="nginx",
        displayName="NGINX",
        image="registry.example/nginx:1",
    )
    with pytest.raises(ValidationError, match="references must be unique"):
        Settings(
            access_token="test-access-token-at-least-32-characters",
            custom_images=[profile, profile.model_copy(update={"name": "nginx-copy"})],
            _env_file=None,
        )


def test_custom_image_profiles_load_from_the_helm_json_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVBOXES_ACCESS_TOKEN", "test-access-token-at-least-32-characters")
    monkeypatch.setenv("DEVBOXES_CUSTOM_IMAGES_ENABLED", "true")
    monkeypatch.setenv(
        "DEVBOXES_CUSTOM_IMAGES",
        json.dumps(
            [
                {
                    "name": "nginx",
                    "displayName": " NGINX preview ",
                    "description": " Serve static content ",
                    "image": "docker.io/nginxinc/nginx-unprivileged:1.27.5-alpine",
                    "mode": "sidecar",
                    "ports": [{"name": "http", "containerPort": 8080}],
                }
            ]
        ),
    )

    settings = Settings(_env_file=None)
    profile = settings.resolve_custom_image("nginx")

    assert profile.display_name == "NGINX preview"
    assert profile.description == "Serve static content"
    assert profile.resources is not None
    assert profile.ports[0].container_port == 8080


def test_multi_tenancy_policy_loads_from_helm_json_and_resolves_effective_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVBOXES_ACCESS_TOKEN", "test-access-token-at-least-32-characters")
    monkeypatch.setenv("DEVBOXES_MULTI_TENANCY_ENABLED", "true")
    monkeypatch.setenv("DEVBOXES_DEFAULT_TENANT", "platform")
    monkeypatch.setenv(
        "DEVBOXES_TENANTS",
        json.dumps(
            [
                {
                    "id": "platform",
                    "displayName": " Platform Engineering ",
                    "namespace": "devboxes-platform",
                    "workspaceSecretName": "platform-workspace",
                    "maxTtlHours": 72,
                    "allowedPresets": ["small", "medium"],
                    "allowedGpuProfiles": [],
                    "resourceQuota": {
                        "enabled": True,
                        "hard": {
                            "requests.cpu": "8",
                            "requests.memory": "32Gi",
                            "requests.nvidia.com/gpu": "2",
                        },
                    },
                },
                {
                    "id": "research",
                    "displayName": "Research",
                    "namespace": "devboxes-research",
                },
            ]
        ),
    )
    monkeypatch.setenv(
        "DEVBOXES_PRINCIPALS",
        json.dumps(
            [
                {
                    "subject": "alice@example.com",
                    "displayName": "Alice",
                    "tokenKey": "alice-token",
                    "defaultTenant": "platform",
                    "memberships": [
                        {"tenant": "platform", "role": "admin"},
                        {"tenant": "research", "role": "viewer"},
                    ],
                }
            ]
        ),
    )

    settings = Settings(_env_file=None)
    platform = settings.resolve_tenant("platform")

    assert platform.display_name == "Platform Engineering"
    assert settings.tenant_max_ttl_hours(platform) == 72
    assert [preset.value for preset in platform.allowed_presets] == ["small", "medium"]
    assert settings.tenant_workspace_secret_name(platform) == "platform-workspace"
    assert platform.resource_quota is not None
    assert platform.resource_quota.hard["requests.nvidia.com/gpu"] == "2"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"default_tenant": "missing"}, "default_tenant"),
        (
            {
                "tenants": [
                    {
                        "id": "platform",
                        "displayName": "Platform",
                        "namespace": "shared",
                    },
                    {
                        "id": "research",
                        "displayName": "Research",
                        "namespace": "shared",
                    },
                ]
            },
            "namespaces must be unique",
        ),
        (
            {
                "principals": [
                    {
                        "subject": "alice",
                        "displayName": "Alice",
                        "tokenKey": "alice-token",
                        "memberships": [{"tenant": "missing", "role": "member"}],
                    }
                ]
            },
            "unknown tenants",
        ),
    ],
)
def test_multi_tenancy_rejects_ambiguous_or_unreachable_policy(
    override: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "access_token": "test-access-token-at-least-32-characters",
        "multi_tenancy_enabled": True,
        "default_tenant": "platform",
        "tenants": [
            {
                "id": "platform",
                "displayName": "Platform",
                "namespace": "devboxes-platform",
            }
        ],
        "_env_file": None,
    }
    values.update(override)

    with pytest.raises(ValidationError, match=message):
        Settings(**values)  # type: ignore[arg-type]


def test_tenant_gpu_default_is_explicit_and_must_remain_inside_the_allowlist() -> None:
    profiles = [
        GpuProfile(
            name=name,
            displayName=name.upper(),
            resourceName=f"example.com/{name}",
            count=1,
        )
        for name in ("shared", "dedicated")
    ]
    settings = Settings(
        access_token="test-access-token-at-least-32-characters",
        gpu_enabled=True,
        gpu_default_profile="shared",
        gpu_profiles=profiles,
        multi_tenancy_enabled=True,
        default_tenant="platform",
        tenants=[
            {
                "id": "platform",
                "displayName": "Platform",
                "namespace": "devboxes-platform",
                "allowedGpuProfiles": ["dedicated"],
                "defaultGpuProfile": "dedicated",
            }
        ],
        _env_file=None,
    )
    tenant = settings.resolve_tenant("platform")

    assert settings.tenant_default_gpu_profile(tenant) == "dedicated"
    assert settings.resolve_gpu_profile(None, tenant).name == "dedicated"

    with pytest.raises(ValidationError, match="included in allowedGpuProfiles"):
        Settings(
            access_token="test-access-token-at-least-32-characters",
            gpu_enabled=True,
            gpu_default_profile="shared",
            gpu_profiles=profiles,
            multi_tenancy_enabled=True,
            default_tenant="platform",
            tenants=[
                {
                    "id": "platform",
                    "displayName": "Platform",
                    "namespace": "devboxes-platform",
                    "allowedGpuProfiles": ["dedicated"],
                    "defaultGpuProfile": "shared",
                }
            ],
            _env_file=None,
        )

    with pytest.raises(ValidationError, match="requires GPU acceleration"):
        Settings(
            access_token="test-access-token-at-least-32-characters",
            gpu_enabled=False,
            gpu_profiles=profiles,
            multi_tenancy_enabled=True,
            default_tenant="platform",
            tenants=[
                {
                    "id": "platform",
                    "displayName": "Platform",
                    "namespace": "devboxes-platform",
                    "defaultGpuProfile": "shared",
                }
            ],
            _env_file=None,
        )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"principal_token_directory": "/"}, "absolute non-root"),
        ({"principal_token_directory": "//"}, "absolute non-root"),
        ({"principal_token_directory": "/var/run/../secrets"}, "absolute non-root"),
        (
            {
                "multi_tenancy_enabled": True,
                "default_tenant": "platform",
                "tenants": [
                    {
                        "id": "platform",
                        "displayName": "Platform",
                        "namespace": "default",
                    }
                ],
            },
            "non-system",
        ),
        (
            {
                "multi_tenancy_enabled": True,
                "default_tenant": "platform",
                "tenants": [
                    {
                        "id": "platform",
                        "displayName": "Platform",
                        "namespace": "devboxes-platform",
                        "namespaceLabels": {
                            "devboxes.bonalab.org/tenant": "other",
                        },
                    }
                ],
            },
            "reserved label",
        ),
        (
            {
                "multi_tenancy_enabled": True,
                "default_tenant": "platform",
                "tenants": [
                    {
                        "id": "platform",
                        "displayName": "Platform",
                        "namespace": "devboxes-platform",
                        "namespaceLabels": {
                            "kubernetes.io/metadata.name": "other",
                        },
                    }
                ],
            },
            "reserved label",
        ),
    ],
)
def test_tenancy_rejects_unsafe_mount_and_namespace_metadata(
    values: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(
            access_token="test-access-token-at-least-32-characters",
            _env_file=None,
            **values,
        )
