"""Define validated request and response models for the controller API."""

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEVBOX_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
REPOSITORY_RE = re.compile(
    r"^(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?)$"
)


class Preset(StrEnum):
    """Identify a supported workspace resource preset."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class DevboxState(StrEnum):
    """Describe the user-visible lifecycle state of a devbox."""

    STARTING = "starting"
    READY = "ready"
    STOPPED = "stopped"
    DEGRADED = "degraded"


class InsightsState(StrEnum):
    """Describe whether a workspace collector is active or awaits a safe restart."""

    DISABLED = "disabled"
    COLLECTING = "collecting"
    RESTART_REQUIRED = "restart_required"


class CreateDevboxRequest(BaseModel):
    """Validate a request to create a devbox."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40)
    preset: Preset = Preset.SMALL
    ttl_hours: int = Field(default=24, ge=1, le=168)
    repository: str | None = Field(default=None, max_length=240)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        """Normalize and validate a Kubernetes-safe devbox name."""
        value = value.strip().lower()
        if not DEVBOX_NAME_RE.fullmatch(value):
            raise ValueError(
                "use 1-40 lowercase letters, digits, or hyphens; start and end alphanumeric"
            )
        return value

    @field_validator("repository")
    @classmethod
    def valid_repository(cls, value: str | None) -> str | None:
        """Validate the optional GitHub repository clone source."""
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not REPOSITORY_RE.fullmatch(value):
            raise ValueError("use owner/repository or an https://github.com/owner/repository URL")
        return value


class Devbox(BaseModel):
    """Represent the observable state of one managed devbox."""

    name: str
    state: DevboxState
    preset: Preset
    created_at: datetime
    expires_at: datetime
    repository: str | None = None
    ssh_host: str | None = None
    ssh_port: int = 22
    ssh_command: str | None = None
    pod_name: str | None = None
    pod_ready: bool = False
    restarts: int = 0
    storage_size: str
    message: str | None = None
    instance_id: str | None = None
    insights_state: InsightsState = InsightsState.DISABLED


class DevboxList(BaseModel):
    """Wrap the devbox collection returned by the list endpoint."""

    items: list[Devbox]


class WhoAmI(BaseModel):
    """Describe the authenticated controller identity and mechanism."""

    user: str
    mode: str


class CliTokenRequest(BaseModel):
    """Validate a native CLI authorization-code exchange."""

    model_config = ConfigDict(extra="forbid")

    grant_type: str = Field(default="authorization_code", max_length=32)
    code: str = Field(min_length=32, max_length=256)
    code_verifier: str = Field(min_length=43, max_length=128)
    client_id: str = Field(min_length=1, max_length=64)
    redirect_uri: str = Field(min_length=1, max_length=256)


class CliTokenResponse(BaseModel):
    """Return a scoped CLI token without refresh-token material."""

    access_token: str
    token_type: str = "Bearer"  # noqa: S105 - OAuth token type, not a credential
    expires_in: int
    scope: str


class DeleteResult(BaseModel):
    """Report the data-retention result of a delete operation."""

    name: str
    purged: bool
    message: str
