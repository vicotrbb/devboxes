"""Validate controller configuration loaded from environment variables."""

import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from kubernetes.utils.quantity import parse_quantity
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import Preset

GPU_PROFILE_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
QUALIFIED_NAME_PART_RE = re.compile(r"^[A-Za-z0-9](?:[-_.A-Za-z0-9]{0,61}[A-Za-z0-9])?$")
LABEL_VALUE_RE = re.compile(r"^(?:[A-Za-z0-9](?:[-_.A-Za-z0-9]{0,61}[A-Za-z0-9])?)?$")
PRINCIPAL_SUBJECT_RE = re.compile(r"^[A-Za-z0-9](?:[-_.@A-Za-z0-9]{0,126}[A-Za-z0-9])?$")
SECRET_KEY_RE = re.compile(r"^[A-Za-z0-9](?:[-_.A-Za-z0-9]{0,251}[A-Za-z0-9])?$")
RESOURCE_QUOTA_KEY_RE = re.compile(r"^[A-Za-z0-9](?:[-./A-Za-z0-9]{0,251}[A-Za-z0-9])?$")
SupplementalGroup = Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)]


def _container_image_reference(value: str) -> str:
    """Normalize a container image reference without accepting URL syntax."""
    value = value.strip()
    if not value or any(character.isspace() for character in value) or "://" in value:
        raise ValueError("must be a whitespace-free container image reference without a URL scheme")
    return value


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


class TenantRole(StrEnum):
    """Define the application permissions granted inside one tenant."""

    VIEWER = "viewer"
    MEMBER = "member"
    ADMIN = "admin"
    OPERATOR = "operator"

    @property
    def can_manage(self) -> bool:
        """Return whether the role may change ordinary workspace lifecycle state."""
        return self in {self.MEMBER, self.ADMIN, self.OPERATOR}

    @property
    def can_purge(self) -> bool:
        """Return whether the role may permanently remove retained data."""
        return self in {self.ADMIN, self.OPERATOR}


class TenantResourceQuota(BaseModel):
    """Describe the namespace ResourceQuota policy shown to authenticated clients."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    enabled: bool = True
    hard: dict[str, str] = Field(default_factory=dict, max_length=32)

    @field_validator("hard")
    @classmethod
    def hard_limits_are_valid(cls, value: dict[str, str]) -> dict[str, str]:
        """Validate bounded positive Kubernetes resource quantities."""
        normalized: dict[str, str] = {}
        for raw_key, raw_quantity in value.items():
            key = raw_key.strip()
            quantity = str(raw_quantity).strip()
            if not RESOURCE_QUOTA_KEY_RE.fullmatch(key):
                raise ValueError(f"invalid ResourceQuota key {raw_key!r}")
            try:
                if parse_quantity(quantity) <= 0:
                    raise ValueError
            except ValueError as error:
                raise ValueError(f"ResourceQuota {key!r} must be a positive quantity") from error
            normalized[key] = quantity
        return normalized

    @model_validator(mode="after")
    def enabled_quota_has_limits(self) -> Self:
        """Reject a quota object that claims enforcement without any limits."""
        if self.enabled and not self.hard:
            raise ValueError("enabled ResourceQuota requires at least one hard limit")
        return self


class TenantConfig(BaseModel):
    """Define one operator-owned namespace and policy domain."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    id: str = Field(min_length=1, max_length=40)
    display_name: str = Field(alias="displayName", min_length=1, max_length=80)
    namespace: str = Field(min_length=1, max_length=63)
    create_namespace: bool = Field(default=False, alias="createNamespace")
    namespace_labels: dict[str, str] = Field(
        default_factory=dict,
        alias="namespaceLabels",
        max_length=16,
    )
    workspace_secret_name: str | None = Field(
        default=None,
        alias="workspaceSecretName",
        max_length=253,
    )
    workspace_service_account_name: str | None = Field(
        default=None,
        alias="workspaceServiceAccountName",
        max_length=253,
    )
    storage_class: str | None = Field(default=None, alias="storageClass", max_length=253)
    max_ttl_hours: int | None = Field(default=None, alias="maxTtlHours", ge=1, le=168)
    allowed_presets: list[Preset] = Field(
        default_factory=lambda: list(Preset),
        alias="allowedPresets",
        min_length=1,
        max_length=3,
    )
    allowed_gpu_profiles: list[str] | None = Field(
        default=None,
        alias="allowedGpuProfiles",
        max_length=32,
    )
    default_gpu_profile: str | None = Field(
        default=None,
        alias="defaultGpuProfile",
        max_length=40,
    )
    allowed_image_profiles: list[str] | None = Field(
        default=None,
        alias="allowedImageProfiles",
        max_length=32,
    )
    resource_quota: TenantResourceQuota | None = Field(default=None, alias="resourceQuota")

    @field_validator("id")
    @classmethod
    def id_is_safe(cls, value: str) -> str:
        """Keep tenant IDs stable across HTTP, CLI, labels, and token claims."""
        value = value.strip().lower()
        if not GPU_PROFILE_NAME_RE.fullmatch(value):
            raise ValueError(
                "use 1-40 lowercase letters, digits, or hyphens; start and end alphanumeric"
            )
        return value

    @field_validator("display_name")
    @classmethod
    def display_name_is_not_blank(cls, value: str) -> str:
        """Normalize the user-visible tenant name."""
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("namespace")
    @classmethod
    def namespace_is_safe(cls, value: str) -> str:
        """Require the namespace-per-tenant isolation unit to be a DNS label."""
        value = value.strip().lower()
        if not DNS_LABEL_RE.fullmatch(value) or value == "default" or value.startswith("kube-"):
            raise ValueError("must be a valid non-system Kubernetes namespace name")
        return value

    @field_validator("namespace_labels")
    @classmethod
    def namespace_labels_are_safe(cls, value: dict[str, str]) -> dict[str, str]:
        """Validate labels without allowing tenant ownership metadata to be replaced."""
        reserved = {
            "app.kubernetes.io/part-of",
            "devboxes.bonalab.org/tenant",
            "kubernetes.io/metadata.name",
        }
        normalized: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = raw_key.strip()
            label_value = str(raw_value).strip()
            if key in reserved:
                raise ValueError(f"namespaceLabels cannot override reserved label {key!r}")
            if not _valid_qualified_name(key, require_prefix=False):
                raise ValueError(f"invalid namespace label key {raw_key!r}")
            if not LABEL_VALUE_RE.fullmatch(label_value):
                raise ValueError(f"invalid namespace label value for {key!r}")
            normalized[key] = label_value
        return normalized

    @field_validator(
        "workspace_secret_name",
        "workspace_service_account_name",
    )
    @classmethod
    def optional_resource_name_is_safe(cls, value: str | None) -> str | None:
        """Validate optional namespaced Kubernetes resource names."""
        if value is None or not value.strip():
            return None
        value = value.strip().lower()
        if not _valid_dns_subdomain(value):
            raise ValueError("must be a valid Kubernetes DNS subdomain")
        return value

    @field_validator("storage_class")
    @classmethod
    def optional_storage_class_is_safe(cls, value: str | None) -> str | None:
        """Normalize the optional per-tenant StorageClass override."""
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not _valid_dns_subdomain(value):
            raise ValueError("must be a valid Kubernetes DNS subdomain")
        return value

    @field_validator("allowed_presets")
    @classmethod
    def presets_are_unique(cls, value: list[Preset]) -> list[Preset]:
        """Reject ambiguous duplicate policy entries."""
        if len(value) != len(set(value)):
            raise ValueError("allowedPresets must contain unique values")
        return value

    @field_validator("allowed_gpu_profiles", "allowed_image_profiles")
    @classmethod
    def profile_names_are_safe(cls, value: list[str] | None) -> list[str] | None:
        """Normalize optional allowlists; an empty list deliberately allows none."""
        if value is None:
            return None
        normalized = [item.strip().lower() for item in value]
        if any(not GPU_PROFILE_NAME_RE.fullmatch(item) for item in normalized):
            raise ValueError("profile allowlists must contain valid profile names")
        if len(normalized) != len(set(normalized)):
            raise ValueError("profile allowlists must contain unique values")
        return normalized

    @field_validator("default_gpu_profile")
    @classmethod
    def default_gpu_profile_is_safe(cls, value: str | None) -> str | None:
        """Normalize an optional tenant-specific accelerator default."""
        if value is None or not value.strip():
            return None
        value = value.strip().lower()
        if not GPU_PROFILE_NAME_RE.fullmatch(value):
            raise ValueError("defaultGpuProfile must contain a valid profile name")
        return value


class TenantMembership(BaseModel):
    """Grant one principal a role in one configured tenant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant: str = Field(min_length=1, max_length=40)
    role: Literal["viewer", "member", "admin"] = "member"

    @field_validator("tenant")
    @classmethod
    def tenant_is_safe(cls, value: str) -> str:
        """Normalize the referenced tenant ID."""
        value = value.strip().lower()
        if not GPU_PROFILE_NAME_RE.fullmatch(value):
            raise ValueError("must reference a valid tenant ID")
        return value


class PrincipalConfig(BaseModel):
    """Define one human or automation identity without embedding its token."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    subject: str = Field(min_length=1, max_length=128)
    display_name: str = Field(alias="displayName", min_length=1, max_length=80)
    token_key: str = Field(alias="tokenKey", min_length=1, max_length=253)
    memberships: list[TenantMembership] = Field(min_length=1, max_length=32)
    default_tenant: str | None = Field(default=None, alias="defaultTenant", max_length=40)

    @field_validator("subject")
    @classmethod
    def subject_is_safe(cls, value: str) -> str:
        """Keep subjects unambiguous in signed sessions and audit annotations."""
        value = value.strip()
        if not PRINCIPAL_SUBJECT_RE.fullmatch(value):
            raise ValueError(
                "use 1-128 letters, digits, dots, underscores, hyphens, or @ characters"
            )
        return value

    @field_validator("display_name")
    @classmethod
    def display_name_is_not_blank(cls, value: str) -> str:
        """Normalize the user-facing principal name."""
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("token_key")
    @classmethod
    def token_key_is_safe(cls, value: str) -> str:
        """Prevent traversal outside the mounted Kubernetes Secret directory."""
        value = value.strip()
        if not SECRET_KEY_RE.fullmatch(value):
            raise ValueError("must be a valid Kubernetes Secret data key")
        return value

    @field_validator("default_tenant")
    @classmethod
    def default_tenant_is_safe(cls, value: str | None) -> str | None:
        """Normalize an optional per-principal default tenant."""
        if value is None or not value.strip():
            return None
        value = value.strip().lower()
        if not GPU_PROFILE_NAME_RE.fullmatch(value):
            raise ValueError("must reference a valid tenant ID")
        return value

    @model_validator(mode="after")
    def memberships_are_unique(self) -> Self:
        """Require exactly one role per tenant and a reachable default."""
        tenant_ids = [membership.tenant for membership in self.memberships]
        if len(tenant_ids) != len(set(tenant_ids)):
            raise ValueError("principal memberships must reference unique tenants")
        if self.default_tenant is not None and self.default_tenant not in tenant_ids:
            raise ValueError("defaultTenant must name one of the principal memberships")
        return self


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
        return _container_image_reference(value) if value is not None else None

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


class CustomImagePort(BaseModel):
    """Describe one container port exposed only inside a devbox pod."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    name: str = Field(min_length=1, max_length=63)
    container_port: int = Field(alias="containerPort", ge=1024, le=65_535)
    protocol: Literal["TCP", "UDP", "SCTP"] = "TCP"

    @field_validator("name")
    @classmethod
    def name_is_dns_safe(cls, value: str) -> str:
        """Keep port names valid when Kubernetes renders the sidecar."""
        value = value.strip().lower()
        if not DNS_LABEL_RE.fullmatch(value):
            raise ValueError("must be a valid lowercase Kubernetes DNS label")
        return value


class CustomImageResources(BaseModel):
    """Define the bounded compute envelope for an approved service image."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    cpu_request: str = Field(default="25m", alias="cpuRequest", min_length=1, max_length=32)
    memory_request: str = Field(default="32Mi", alias="memoryRequest", min_length=1, max_length=32)
    cpu_limit: str = Field(default="500m", alias="cpuLimit", min_length=1, max_length=32)
    memory_limit: str = Field(default="512Mi", alias="memoryLimit", min_length=1, max_length=32)

    @field_validator("cpu_request", "memory_request", "cpu_limit", "memory_limit")
    @classmethod
    def quantity_is_positive(cls, value: str) -> str:
        """Reject malformed or non-positive Kubernetes resource quantities."""
        value = value.strip()
        try:
            if parse_quantity(value) <= 0:
                raise ValueError
        except ValueError as error:
            raise ValueError("must be a positive Kubernetes resource quantity") from error
        return value

    @model_validator(mode="after")
    def limits_cover_requests(self) -> Self:
        """Avoid a catalog entry Kubernetes would reject at scheduling time."""
        if parse_quantity(self.cpu_request) > parse_quantity(self.cpu_limit):
            raise ValueError("cpuLimit must be greater than or equal to cpuRequest")
        if parse_quantity(self.memory_request) > parse_quantity(self.memory_limit):
            raise ValueError("memoryLimit must be greater than or equal to memoryRequest")
        return self


class CustomImageProfile(BaseModel):
    """Define one operator-approved service or complete workspace image."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    name: str = Field(min_length=1, max_length=40)
    display_name: str = Field(alias="displayName", min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=160)
    image: str = Field(min_length=1, max_length=512)
    mode: Literal["sidecar", "workspace"] = "sidecar"
    pull_policy: Literal["Always", "IfNotPresent", "Never"] = Field(
        default="IfNotPresent",
        alias="pullPolicy",
    )
    resources: CustomImageResources | None = None
    ports: list[CustomImagePort] = Field(default_factory=list, max_length=8)

    @model_validator(mode="before")
    @classmethod
    def populate_sidecar_resource_defaults(cls, value: object) -> object:
        """Persist sidecar defaults so a resolved snapshot cannot drift later."""
        if not isinstance(value, dict):
            return value
        if value.get("mode", "sidecar") == "sidecar" and "resources" not in value:
            return {**value, "resources": {}}
        return value

    @field_validator("name")
    @classmethod
    def name_is_safe(cls, value: str) -> str:
        """Require a compact profile identifier safe for every client surface."""
        value = value.strip().lower()
        if not GPU_PROFILE_NAME_RE.fullmatch(value):
            raise ValueError(
                "use 1-40 lowercase letters, digits, or hyphens; start and end alphanumeric"
            )
        return value

    @field_validator("display_name")
    @classmethod
    def display_name_is_not_blank(cls, value: str) -> str:
        """Normalize the user-visible image label."""
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("description", mode="before")
    @classmethod
    def optional_text_is_normalized(cls, value: object) -> object:
        """Trim optional descriptive text and treat blanks as absent."""
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("image")
    @classmethod
    def image_is_valid(cls, value: str) -> str:
        """Reject image values Kubernetes cannot interpret as references."""
        return _container_image_reference(value)

    @field_validator("ports")
    @classmethod
    def ports_are_unique(cls, value: list[CustomImagePort]) -> list[CustomImagePort]:
        """Keep container-port declarations unambiguous for status and manifests."""
        names = [port.name for port in value]
        bindings = [(port.container_port, port.protocol) for port in value]
        if len(names) != len(set(names)):
            raise ValueError("must not contain duplicate port names")
        if len(bindings) != len(set(bindings)):
            raise ValueError("must not contain duplicate port and protocol bindings")
        return value

    @model_validator(mode="after")
    def mode_has_only_applicable_settings(self) -> Self:
        """Reject sidecar-only scheduling knobs on an interactive workspace image."""
        if self.mode == "workspace" and self.resources is not None:
            raise ValueError("workspace profiles cannot define sidecar resources")
        return self


class Settings(BaseSettings):
    """Define validated runtime settings for one Devboxes installation."""

    model_config = SettingsConfigDict(env_prefix="DEVBOXES_", case_sensitive=False)

    namespace: str = "devboxes"
    external_url: str = "http://127.0.0.1:8000"
    display_name: str = "operator"
    cluster_name: str = "Kubernetes"
    workspace_image: str = "ghcr.io/vicotrbb/devboxes-workspace:latest"
    custom_images_enabled: bool = False
    custom_images: list[CustomImageProfile] = Field(default_factory=list, max_length=32)
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
    multi_tenancy_enabled: bool = False
    default_tenant: str = "default"
    tenants: list[TenantConfig] = Field(default_factory=list, max_length=64)
    principals: list[PrincipalConfig] = Field(default_factory=list, max_length=256)
    principal_token_directory: str = "/var/run/secrets/devboxes/principals"  # noqa: S105 - mounted Secret path
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

    @field_validator("default_tenant")
    @classmethod
    def default_tenant_is_safe(cls, value: str) -> str:
        """Keep the installation default usable as a tenant identifier."""
        value = value.strip().lower()
        if not GPU_PROFILE_NAME_RE.fullmatch(value):
            raise ValueError("default_tenant must be a valid tenant ID")
        return value

    @field_validator("principal_token_directory")
    @classmethod
    def principal_token_directory_is_absolute(cls, value: str) -> str:
        """Keep principal Secret reads inside one explicit mounted directory."""
        value = value.strip().rstrip("/")
        path = Path(value)
        if not value or not path.is_absolute() or ".." in path.parts:
            raise ValueError("principal_token_directory must be an absolute non-root path")
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
        custom_image_names = [profile.name for profile in self.custom_images]
        if len(custom_image_names) != len(set(custom_image_names)):
            raise ValueError("custom image profile names must be unique")
        custom_image_references = [profile.image for profile in self.custom_images]
        if len(custom_image_references) != len(set(custom_image_references)):
            raise ValueError("custom image profile references must be unique")
        if self.custom_images_enabled and not custom_image_names:
            raise ValueError("custom_images_enabled requires at least one custom image profile")
        tenant_ids = [tenant.id for tenant in self.tenants]
        tenant_namespaces = [tenant.namespace for tenant in self.tenants]
        if len(tenant_ids) != len(set(tenant_ids)):
            raise ValueError("tenant IDs must be unique")
        if len(tenant_namespaces) != len(set(tenant_namespaces)):
            raise ValueError("tenant namespaces must be unique")
        principal_subjects = [principal.subject for principal in self.principals]
        principal_token_keys = [principal.token_key for principal in self.principals]
        if len(principal_subjects) != len(set(principal_subjects)):
            raise ValueError("principal subjects must be unique")
        if len(principal_token_keys) != len(set(principal_token_keys)):
            raise ValueError("principal token keys must be unique")
        if self.multi_tenancy_enabled:
            if not self.tenants:
                raise ValueError("multi_tenancy_enabled requires at least one tenant")
            if self.default_tenant not in tenant_ids:
                raise ValueError("default_tenant must name a configured tenant")
            configured_gpu_profiles = set(profile_names)
            configured_image_profiles = set(custom_image_names)
            for tenant in self.tenants:
                tenant_max_ttl = tenant.max_ttl_hours or self.max_ttl_hours
                if tenant_max_ttl > self.max_ttl_hours:
                    raise ValueError(
                        f"tenant {tenant.id!r} maxTtlHours cannot exceed controller maximum"
                    )
                unavailable_gpu = set(tenant.allowed_gpu_profiles or []) - configured_gpu_profiles
                if unavailable_gpu:
                    raise ValueError(
                        f"tenant {tenant.id!r} references unknown GPU profiles: "
                        f"{', '.join(sorted(unavailable_gpu))}"
                    )
                if tenant.default_gpu_profile is not None:
                    if not self.gpu_enabled:
                        raise ValueError(
                            f"tenant {tenant.id!r} defaultGpuProfile requires GPU acceleration"
                        )
                    if tenant.default_gpu_profile not in configured_gpu_profiles:
                        raise ValueError(
                            f"tenant {tenant.id!r} references unknown default GPU profile "
                            f"{tenant.default_gpu_profile!r}"
                        )
                    if (
                        tenant.allowed_gpu_profiles is not None
                        and tenant.default_gpu_profile not in tenant.allowed_gpu_profiles
                    ):
                        raise ValueError(
                            f"tenant {tenant.id!r} defaultGpuProfile must be included in "
                            "allowedGpuProfiles"
                        )
                unavailable_images = (
                    set(tenant.allowed_image_profiles or []) - configured_image_profiles
                )
                if unavailable_images:
                    raise ValueError(
                        f"tenant {tenant.id!r} references unknown image profiles: "
                        f"{', '.join(sorted(unavailable_images))}"
                    )
            configured_tenants = set(tenant_ids)
            for principal in self.principals:
                unavailable_tenants = {
                    membership.tenant for membership in principal.memberships
                } - configured_tenants
                if unavailable_tenants:
                    raise ValueError(
                        f"principal {principal.subject!r} references unknown tenants: "
                        f"{', '.join(sorted(unavailable_tenants))}"
                    )
        elif self.tenants or self.principals:
            raise ValueError(
                "tenants and principals require multi_tenancy_enabled=true to avoid "
                "silently unenforced policy"
            )
        return self

    @property
    def tenant_configs(self) -> tuple[TenantConfig, ...]:
        """Return configured tenants or the backward-compatible installation tenant."""
        if self.multi_tenancy_enabled:
            return tuple(self.tenants)
        return (
            TenantConfig(
                id=self.default_tenant,
                displayName=self.display_name,
                namespace=self.namespace,
                workspaceSecretName=self.workspace_secret_name,
                workspaceServiceAccountName=self.workspace_service_account_name,
                storageClass=self.storage_class,
                maxTtlHours=self.max_ttl_hours,
            ),
        )

    def resolve_tenant(self, tenant_id: str | None) -> TenantConfig:
        """Resolve one configured tenant without falling back on an unknown value."""
        requested = (tenant_id or self.default_tenant).strip().lower()
        for tenant in self.tenant_configs:
            if tenant.id == requested:
                return tenant
        raise ValueError(f"unknown tenant {requested!r}")

    def tenant_max_ttl_hours(self, tenant: TenantConfig) -> int:
        """Return the effective TTL ceiling for one tenant."""
        return min(tenant.max_ttl_hours or self.max_ttl_hours, self.max_ttl_hours)

    def tenant_workspace_secret_name(self, tenant: TenantConfig) -> str:
        """Return the tenant-local Secret mounted into its workspaces."""
        return tenant.workspace_secret_name or self.workspace_secret_name

    def tenant_workspace_service_account_name(self, tenant: TenantConfig) -> str:
        """Return the tenant-local no-RBAC workspace ServiceAccount."""
        return tenant.workspace_service_account_name or self.workspace_service_account_name

    def tenant_storage_class(self, tenant: TenantConfig) -> str | None:
        """Return the per-tenant or installation StorageClass selection."""
        return tenant.storage_class or self.storage_class

    def tenant_gpu_profiles(self, tenant: TenantConfig) -> list[GpuProfile]:
        """Return GPU profiles admitted by installation and tenant policy."""
        allowed = (
            set(tenant.allowed_gpu_profiles) if tenant.allowed_gpu_profiles is not None else None
        )
        return [
            profile
            for profile in self.gpu_profiles
            if self.gpu_enabled and (allowed is None or profile.name in allowed)
        ]

    def tenant_default_gpu_profile(self, tenant: TenantConfig) -> str | None:
        """Return the explicit or inherited GPU default available to one tenant."""
        available = {profile.name for profile in self.tenant_gpu_profiles(tenant)}
        if tenant.default_gpu_profile in available:
            return tenant.default_gpu_profile
        return self.gpu_default_profile if self.gpu_default_profile in available else None

    def tenant_custom_images(self, tenant: TenantConfig) -> list[CustomImageProfile]:
        """Return custom images admitted by installation and tenant policy."""
        allowed = (
            set(tenant.allowed_image_profiles)
            if tenant.allowed_image_profiles is not None
            else None
        )
        return [
            profile
            for profile in self.custom_images
            if self.custom_images_enabled and (allowed is None or profile.name in allowed)
        ]

    def resolve_gpu_profile(
        self,
        requested_name: str | None,
        tenant: TenantConfig | None = None,
    ) -> GpuProfile:
        """Resolve a user request to one trusted installation profile."""
        if not self.gpu_enabled:
            raise ValueError("GPU acceleration is disabled by the operator")
        selected_tenant = tenant or self.resolve_tenant(None)
        profiles = self.tenant_gpu_profiles(selected_tenant)
        profile_name = requested_name or self.tenant_default_gpu_profile(selected_tenant)
        if profile_name is None:
            available = ", ".join(profile.name for profile in profiles) or "none"
            raise ValueError(
                "no default GPU profile is available to this tenant; "
                f"select an explicit profile from: {available}"
            )
        for profile in profiles:
            if profile.name == profile_name:
                return profile
        available = ", ".join(profile.name for profile in profiles) or "none"
        raise ValueError(f"unknown GPU profile {profile_name!r}; available profiles: {available}")

    def resolve_custom_image(
        self,
        selector: str,
        tenant: TenantConfig | None = None,
    ) -> CustomImageProfile:
        """Resolve a profile name or exact approved image reference before pod creation."""
        if not self.custom_images_enabled:
            raise ValueError("custom images are disabled by the operator")
        profiles = self.tenant_custom_images(tenant or self.resolve_tenant(None))
        for profile in profiles:
            if selector in {profile.name, profile.image}:
                return profile
        available = ", ".join(profile.name for profile in profiles) or "none"
        raise ValueError(f"unknown custom image {selector!r}; available profiles: {available}")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""
    # BaseSettings supplies the required access token from DEVBOXES_ACCESS_TOKEN.
    return Settings()  # type: ignore[call-arg]
