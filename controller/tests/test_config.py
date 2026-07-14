import pytest
from pydantic import ValidationError

from devboxes_controller.config import Settings


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
