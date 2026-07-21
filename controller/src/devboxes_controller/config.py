"""Validate controller configuration loaded from environment variables."""

import re
from functools import lru_cache
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GPU_PROFILE_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
QUALIFIED_NAME_PART_RE = re.compile(r"^[A-Za-z0-9](?:[-_.A-Za-z0-9]{0,61}[A-Za-z0-9])?$")
LABEL_VALUE_RE = re.compile(r"^(?:[A-Za-z0-9](?:[-_.A-Za-z0-9]{0,61}[A-Za-z0-9])?)?$")
SupplementalGroup = Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)]


def _valid_dns_subdomain(value: str) -> bool:
    """Return whether a string follows Kubernetes DNS subdomain syntax."""
    return 1 <= len(value) <= 253 and all(
        DNS_LABEL_RE.fullmatch(label) for label in value.split(".")
    )


def _valid_qualified_name(value: str, *, require_prefix: bool) -> bool:
    """Return whether a string follows Kubernetes qualified-name syntax."""
    if "/" in value:
        prefix, name = value.split("/", 1)
        return bool(_valid_dns_subdomain(prefix) and QUALIFIED_NAME_PART_RE.fullmatch(name))
    return not require_prefix and bool(QUALIFIED_NAME_PART_RE.fullmatch(value))


class GpuToleration(BaseModel):
    """Describe one operator-approved toleration applied to GPU workspaces."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    key: str | None = Field(default=None, max_length=317)
    operator: Literal["Equal", "Exists"] = "Equal"
    value: str | None = Field(default=None, max_length=63)
    effect: Literal["NoSchedule", "PreferNoSchedule", "NoExecute"] | None = None
    toleration_seconds: int | None = Field(
        default=None,
        alias="tolerationSeconds",
        ge=0,
        le=2_147_483_647,
    )

    @field_validator("key")
    @classmethod
    def key_is_qualified(cls, value: str | None) -> str | None:
        """Validate a non-empty Kubernetes label key when supplied."""
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not _valid_qualified_name(value, require_prefix=False):
            raise ValueError("must be a valid Kubernetes label key")
        return value

    @field_validator("value")
    @classmethod
    def value_is_label_value(cls, value: str | None) -> str | None:
        """Validate an optional Kubernetes label value."""
        if value is None:
            return None
        value = value.strip()
        if not LABEL_VALUE_RE.fullmatch(value):
            raise ValueError("must be a valid Kubernetes label value")
        return value or None

    @model_validator(mode="after")
    def fields_are_consistent(self) -> Self:
        """Reject toleration combinations Kubernetes would treat ambiguously."""
        if self.operator == "Equal" and self.key is None:
            raise ValueError("Equal tolerations require a key")
        if self.operator == "Exists" and self.value is not None:
            raise ValueError("Exists tolerations cannot set a value")
        if self.toleration_seconds is not None and self.effect != "NoExecute":
            raise ValueError("tolerationSeconds requires effect=NoExecute")
        return self


class GpuProfile(BaseModel):
    """Define one frozen, operator-approved GPU scheduling profile."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    name: str = Field(min_length=1, max_length=40)
    display_name: str = Field(alias="displayName", min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=160)
    resource_name: str = Field(alias="resourceName", min_length=3, max_length=317)
    count: int = Field(ge=1, le=64)
    workspace_image: str | None = Field(default=None, alias="workspaceImage", max_length=512)
    runtime_class_name: str | None = Field(
        default=None,
        alias="runtimeClassName",
        max_length=253,
    )
    supplemental_groups: list[SupplementalGroup] = Field(
        default_factory=list,
        alias="supplementalGroups",
        max_length=8,
    )
    node_selector: dict[str, str] = Field(default_factory=dict, alias="nodeSelector")
    tolerations: list[GpuToleration] = Field(default_factory=list, max_length=16)

    @field_validator("name")
    @classmethod
    def name_is_safe(cls, value: str) -> str:
        """Require a compact profile identifier safe for API and CLI use."""
        value = value.strip().lower()
        if not GPU_PROFILE_NAME_RE.fullmatch(value):
            raise ValueError(
                "use 1-40 lowercase letters, digits, or hyphens; start and end alphanumeric"
            )
        return value

    @field_validator("display_name")
    @classmethod
    def display_name_is_not_blank(cls, value: str) -> str:
        """Normalize the user-visible profile label."""
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("description", "workspace_image", mode="before")
    @classmethod
    def optional_text_is_normalized(cls, value: object) -> object:
        """Trim optional profile text and treat blank values as absent."""
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("resource_name")
    @classmethod
    def resource_name_is_extended(cls, value: str) -> str:
        """Require the vendor-qualified resource exposed by a device driver."""
        value = value.strip()
        if not _valid_qualified_name(value, require_prefix=True):
            raise ValueError("must be a vendor-qualified Kubernetes extended resource")
        return value

    @field_validator("workspace_image")
    @classmethod
    def workspace_image_is_valid(cls, value: str | None) -> str | None:
        """Reject image values Kubernetes cannot interpret as references."""
        if value is not None and (
            any(character.isspace() for character in value) or "://" in value
        ):
            raise ValueError(
                "must be a whitespace-free container image reference without a URL scheme"
            )
        return value

    @field_validator("runtime_class_name")
    @classmethod
    def runtime_class_name_is_dns_safe(cls, value: str | None) -> str | None:
        """Validate an optional existing Kubernetes RuntimeClass name."""
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not _valid_dns_subdomain(value):
            raise ValueError("must be a valid Kubernetes DNS subdomain")
        return value

    @field_validator("supplemental_groups")
    @classmethod
    def supplemental_groups_are_unique(cls, value: list[int]) -> list[int]:
        """Keep pod-level device group membership explicit and bounded."""
        if len(value) != len(set(value)):
            raise ValueError("must not contain duplicate group IDs")
        return value

    @field_validator("node_selector")
    @classmethod
    def node_selector_is_valid(cls, value: dict[str, str]) -> dict[str, str]:
        """Validate bounded Kubernetes node selector terms."""
        if len(value) > 16:
            raise ValueError("must contain at most 16 entries")
        for key, label_value in value.items():
            if not _valid_qualified_name(key, require_prefix=False):
                raise ValueError(f"invalid node selector key {key!r}")
            if not LABEL_VALUE_RE.fullmatch(label_value):
                raise ValueError(f"invalid node selector value for {key!r}")
        return value


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
    gpu_enabled: bool = False
    gpu_default_profile: str | None = None
    gpu_profiles: list[GpuProfile] = Field(default_factory=list, max_length=32)
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
    insights_enabled: bool = False
    insights_db_path: str = "/var/lib/devboxes/insights.db"
    insights_database_warning_bytes: int = Field(
        default=1_717_986_918,
        ge=1_048_576,
        le=1_099_511_627_776,
    )
    insights_controller_url: str = "http://devboxes:8000"
    insights_signing_key: SecretStr | None = None
    insights_retention_raw_days: int = Field(default=30, ge=1, le=365)
    insights_retention_hourly_days: int = Field(default=90, ge=1, le=730)
    insights_retention_daily_days: int = Field(default=365, ge=1, le=3650)
    insights_agent_scan_interval_seconds: int = Field(default=60, ge=15, le=3600)
    insights_agent_repository_depth: int = Field(default=4, ge=1, le=12)
    insights_agent_max_queue_bytes: int = Field(default=134_217_728, ge=1_048_576, le=2_147_483_648)
    insights_agent_max_queue_age_seconds: int = Field(default=604_800, ge=60, le=31_536_000)
    insights_max_compressed_bytes: int = Field(default=2_097_152, ge=1024, le=16_777_216)
    insights_max_expanded_bytes: int = Field(default=8_388_608, ge=4096, le=67_108_864)
    insights_max_points_per_batch: int = Field(default=10_000, ge=1, le=100_000)
    insights_ingest_rate_per_minute: int = Field(default=120, ge=1, le=10_000)
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
        "insights_signing_key",
        "gpu_default_profile",
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

    @field_validator("insights_signing_key")
    @classmethod
    def insights_signing_key_is_strong(cls, value: SecretStr | None) -> SecretStr | None:
        """Require a dedicated ingest key to carry strong entropy when set."""
        if value is not None and len(value.get_secret_value().strip()) < 32:
            raise ValueError("Insights signing key must contain at least 32 characters")
        return value

    @field_validator("insights_db_path")
    @classmethod
    def insights_db_path_is_absolute(cls, value: str) -> str:
        """Keep the central database on an explicit mounted filesystem path."""
        if not value.startswith("/"):
            raise ValueError("insights_db_path must be absolute")
        return value

    @field_validator("insights_controller_url")
    @classmethod
    def insights_controller_url_is_http(cls, value: str) -> str:
        """Require an absolute internal HTTP URL used only by workspace agents."""
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("insights_controller_url must be an absolute http or https URL")
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
        if not (
            self.insights_retention_raw_days
            <= self.insights_retention_hourly_days
            <= self.insights_retention_daily_days
        ):
            raise ValueError("Insights retention must satisfy rawDays <= hourlyDays <= dailyDays")
        if self.insights_max_compressed_bytes > self.insights_max_expanded_bytes:
            raise ValueError("Insights compressed limit cannot exceed expanded limit")
        profile_names = [profile.name for profile in self.gpu_profiles]
        if len(profile_names) != len(set(profile_names)):
            raise ValueError("GPU profile names must be unique")
        if self.gpu_default_profile is not None:
            self.gpu_default_profile = self.gpu_default_profile.strip().lower()
            if self.gpu_default_profile not in profile_names:
                raise ValueError("gpu_default_profile must name a configured GPU profile")
        if self.gpu_enabled:
            if not profile_names:
                raise ValueError("gpu_enabled requires at least one GPU profile")
            if self.gpu_default_profile is None:
                raise ValueError("gpu_enabled requires gpu_default_profile")
        return self

    def resolve_gpu_profile(self, requested_name: str | None) -> GpuProfile:
        """Resolve a user request to one trusted installation profile."""
        if not self.gpu_enabled:
            raise ValueError("GPU acceleration is disabled by the operator")
        profile_name = requested_name or self.gpu_default_profile
        for profile in self.gpu_profiles:
            if profile.name == profile_name:
                return profile
        available = ", ".join(profile.name for profile in self.gpu_profiles)
        raise ValueError(f"unknown GPU profile {profile_name!r}; available profiles: {available}")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""
    # BaseSettings supplies the required access token from DEVBOXES_ACCESS_TOKEN.
    return Settings()  # type: ignore[call-arg]
