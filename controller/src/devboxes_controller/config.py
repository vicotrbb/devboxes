"""Validate controller configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Define validated runtime settings for one Devboxes installation."""

    model_config = SettingsConfigDict(env_prefix="DEVBOXES_", case_sensitive=False)

    namespace: str = "devboxes"
    external_url: str = "http://127.0.0.1:8000"
    display_name: str = "operator"
    cluster_name: str = "Kubernetes"
    workspace_image: str = "ghcr.io/vicotrbb/devboxes-workspace:latest"
    workspace_secret_name: str = "devboxes-workspace"  # noqa: S105 - Kubernetes Secret name
    workspace_service_account_name: str = "devboxes-workspace"
    workspace_priority_class: str | None = None
    image_pull_secret: str | None = None
    storage_class: str | None = None
    workspace_service_type: Literal["LoadBalancer", "NodePort"] = "LoadBalancer"
    workspace_service_host: str | None = None
    workspace_service_annotations: dict[str, str] = Field(default_factory=dict)
    workspace_load_balancer_class: str | None = None
    workspace_external_traffic_policy: Literal["Cluster", "Local"] = "Cluster"
    workspace_load_balancer_source_ranges: list[str] = Field(default_factory=list)
    access_token: SecretStr
    default_ttl_hours: int = Field(default=24, le=168)
    max_ttl_hours: int = Field(default=168, le=168)
    cleanup_interval_seconds: int = 60
    session_ttl_seconds: int = 43_200
    authorization_code_ttl_seconds: int = Field(default=120, ge=30, le=600)
    authorization_code_store_size: int = Field(default=1024, ge=16, le=10_000)
    cli_token_ttl_seconds: int = Field(default=2_592_000, ge=300, le=31_536_000)
    cli_signing_key: SecretStr | None = None
    cookie_secure: bool = True
    kubeconfig_context: str | None = None
    log_level: str = "INFO"

    @field_validator("namespace", "display_name", "cluster_name")
    @classmethod
    def text_is_not_blank(cls, value: str) -> str:
        """Normalize required human-readable values and reject blanks."""
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator(
        "workspace_priority_class",
        "image_pull_secret",
        "storage_class",
        "workspace_service_host",
        "workspace_load_balancer_class",
        "kubeconfig_context",
        "cli_signing_key",
        mode="before",
    )
    @classmethod
    def blank_optional_text_is_none(cls, value: object) -> object:
        """Normalize blank optional strings to missing values."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("external_url")
    @classmethod
    def external_url_is_http(cls, value: str) -> str:
        """Require an absolute HTTP or HTTPS external URL."""
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("must be an absolute http or https URL")
        return value

    @field_validator(
        "default_ttl_hours",
        "max_ttl_hours",
        "cleanup_interval_seconds",
        "authorization_code_ttl_seconds",
        "authorization_code_store_size",
        "cli_token_ttl_seconds",
    )
    @classmethod
    def positive_integer(cls, value: int) -> int:
        """Require positive timing and TTL configuration values."""
        if value <= 0:
            raise ValueError("must be positive")
        return value

    @field_validator("cli_signing_key")
    @classmethod
    def cli_signing_key_is_strong(cls, value: SecretStr | None) -> SecretStr | None:
        """Require a dedicated CLI signing key to carry strong entropy when set."""
        if value is not None and len(value.get_secret_value().strip()) < 32:
            raise ValueError("CLI signing key must contain at least 32 characters")
        return value

    @field_validator("access_token")
    @classmethod
    def access_token_is_not_blank(cls, value: SecretStr) -> SecretStr:
        """Require a controller token long enough to hold strong entropy."""
        token = value.get_secret_value().strip()
        if len(token) < 32:
            raise ValueError("access token must contain at least 32 characters")
        return value

    @model_validator(mode="after")
    def settings_are_consistent(self) -> Self:
        """Validate relationships between independently parsed settings."""
        if self.default_ttl_hours > self.max_ttl_hours:
            raise ValueError("default_ttl_hours cannot exceed max_ttl_hours")
        if self.workspace_service_type == "NodePort" and not self.workspace_service_host:
            raise ValueError("workspace_service_host is required for NodePort services")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""
    # BaseSettings supplies the required access token from DEVBOXES_ACCESS_TOKEN.
    return Settings()  # type: ignore[call-arg]
