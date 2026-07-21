import json

import pytest
from pydantic import ValidationError

from devboxes_controller.config import GpuProfile, GpuToleration, Settings


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
