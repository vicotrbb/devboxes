import pytest
from pydantic import ValidationError

from devboxes_controller.models import CreateDevboxRequest, Preset


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
