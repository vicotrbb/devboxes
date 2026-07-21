import pytest
from pydantic import ValidationError

from devboxes_controller.models import CreateDevboxRequest, GpuRequest, Preset


def test_create_request_normalizes_name_and_repository() -> None:
    request = CreateDevboxRequest(
        name="Atlas-01",
        preset=Preset.MEDIUM,
        repository=" owner/atlas ",
    )

    assert request.name == "atlas-01"
    assert request.repository == "owner/atlas"


@pytest.mark.parametrize("name", ["-bad", "bad-", "Bad_Name", "x" * 41])
def test_create_request_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValidationError):
        CreateDevboxRequest(name=name)


def test_create_request_rejects_arbitrary_clone_urls() -> None:
    with pytest.raises(ValidationError):
        CreateDevboxRequest(name="atlas", repository="ssh://untrusted.example/repository")


def test_gpu_request_supports_default_and_named_operator_profiles() -> None:
    assert CreateDevboxRequest(name="atlas", gpu={}).gpu == GpuRequest()
    request = CreateDevboxRequest(name="atlas", gpu={"profile": " NVIDIA-L4 "})

    assert request.gpu == GpuRequest(profile="nvidia-l4")


def test_gpu_request_rejects_raw_kubernetes_configuration() -> None:
    with pytest.raises(ValidationError):
        CreateDevboxRequest(
            name="atlas",
            gpu={"profile": "nvidia", "resource_name": "nvidia.com/gpu"},
        )
