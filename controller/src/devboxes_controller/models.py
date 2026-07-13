import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEVBOX_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
REPOSITORY_RE = re.compile(
    r"^(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?)$"
)


class Preset(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class DevboxState(StrEnum):
    STARTING = "starting"
    READY = "ready"
    STOPPED = "stopped"
    DEGRADED = "degraded"


class CreateDevboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40)
    preset: Preset = Preset.SMALL
    ttl_hours: int = Field(default=24, ge=1, le=168)
    repository: str | None = Field(default=None, max_length=240)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip().lower()
        if not DEVBOX_NAME_RE.fullmatch(value):
            raise ValueError(
                "use 1-40 lowercase letters, digits, or hyphens; start and end alphanumeric"
            )
        return value

    @field_validator("repository")
    @classmethod
    def valid_repository(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not REPOSITORY_RE.fullmatch(value):
            raise ValueError("use owner/repository or an https://github.com/owner/repository URL")
        return value


class Devbox(BaseModel):
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


class DevboxList(BaseModel):
    items: list[Devbox]


class WhoAmI(BaseModel):
    user: str
    mode: str


class DeleteResult(BaseModel):
    name: str
    purged: bool
    message: str
