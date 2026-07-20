from devboxes_controller.app import create_app
from devboxes_controller.config import GpuProfile, Settings

from .fakes import FakeManager

settings = Settings(
    access_token="preview-access-token-at-least-32-characters",
    cookie_secure=False,
    cleanup_interval_seconds=3600,
    gpu_enabled=True,
    gpu_default_profile="nvidia-l4",
    gpu_profiles=[
        GpuProfile(
            name="nvidia-l4",
            displayName="NVIDIA L4",
            description="One dedicated GPU for inference and CUDA development",
            resourceName="nvidia.com/gpu",
            count=1,
        ),
        GpuProfile(
            name="nvidia-shared",
            displayName="NVIDIA shared",
            description="One time-sliced GPU allocation for interactive experiments",
            resourceName="nvidia.com/gpu.shared",
            count=1,
        ),
    ],
)
app = create_app(settings, FakeManager())  # type: ignore[arg-type]
