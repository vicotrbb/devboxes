from __future__ import annotations

from datetime import UTC, datetime, timedelta

from devboxes_controller.manager import DevboxConflictError, DevboxNotFoundError
from devboxes_controller.models import (
    CreateDevboxRequest,
    DeleteResult,
    Devbox,
    DevboxState,
    GpuAllocation,
    Preset,
)


def sample_devbox(
    name: str = "atlas",
    state: DevboxState = DevboxState.READY,
    preset: Preset = Preset.MEDIUM,
    gpu: GpuAllocation | None = None,
) -> Devbox:
    now = datetime.now(UTC)
    ready = state is DevboxState.READY
    return Devbox(
        name=name,
        state=state,
        preset=preset,
        created_at=now - timedelta(hours=2),
        expires_at=now + timedelta(hours=22),
        repository="owner/atlas" if name == "atlas" else None,
        ssh_host="192.0.2.10" if ready else None,
        ssh_command="ssh -t dev@192.0.2.10" if ready else None,
        pod_name=f"devbox-{name}-preview",
        pod_ready=ready,
        restarts=0,
        storage_size="30Gi" if preset is Preset.MEDIUM else "20Gi",
        message=(
            None
            if ready
            else (
                "Compute stopped; home volume retained"
                if state is DevboxState.STOPPED
                else "Preparing workspace and SSH"
            )
        ),
        gpu=gpu,
    )


class FakeManager:
    def __init__(self) -> None:
        self.boxes = [
            sample_devbox(
                gpu=GpuAllocation(
                    profile="nvidia-l4",
                    display_name="NVIDIA L4",
                    resource_name="nvidia.com/gpu",
                    count=1,
                )
            ),
            sample_devbox("paperclip", DevboxState.STOPPED, Preset.SMALL),
            sample_devbox("nightly", DevboxState.STARTING, Preset.LARGE),
        ]

    async def ready(self) -> bool:
        return True

    async def list(self) -> list[Devbox]:
        return self.boxes

    async def get(self, name: str) -> Devbox:
        try:
            return next(box for box in self.boxes if box.name == name)
        except StopIteration as error:
            raise DevboxNotFoundError(name) from error

    async def create(self, request: CreateDevboxRequest) -> Devbox:
        if any(box.name == request.name for box in self.boxes):
            raise DevboxConflictError(f"devbox {request.name!r} already exists")
        box = sample_devbox(request.name, DevboxState.STARTING, request.preset)
        box.repository = request.repository
        if request.gpu is not None:
            profile = request.gpu.profile or "nvidia-l4"
            display_name, resource_name = {
                "nvidia-l4": ("NVIDIA L4", "nvidia.com/gpu"),
                "nvidia-shared": ("NVIDIA shared", "nvidia.com/gpu.shared"),
            }.get(profile, (profile, "nvidia.com/gpu"))
            box.gpu = GpuAllocation(
                profile=profile,
                display_name=display_name,
                resource_name=resource_name,
                count=1,
            )
        box.ssh_host = None
        box.ssh_command = None
        box.pod_ready = False
        box.message = "Preparing workspace and SSH"
        self.boxes.insert(0, box)
        return box

    async def scale(self, name: str, replicas: int) -> Devbox:
        box = await self.get(name)
        box.state = DevboxState.STARTING if replicas else DevboxState.STOPPED
        box.pod_ready = False
        box.message = "Preparing workspace and SSH" if replicas else "Compute stopped"
        if replicas:
            box.expires_at = datetime.now(UTC) + timedelta(hours=24)
        return box

    async def delete(self, name: str, purge: bool) -> DeleteResult:
        self.boxes = [box for box in self.boxes if box.name != name]
        return DeleteResult(name=name, purged=purge, message=f"{name} deleted")

    async def stop_expired(self) -> list[str]:
        return []

    async def reconcile_insights(self) -> list[str]:
        return []
