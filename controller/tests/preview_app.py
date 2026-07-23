from devboxes_controller.app import create_app
from devboxes_controller.config import CustomImagePort, CustomImageProfile, GpuProfile, Settings

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
    custom_images_enabled=True,
    custom_images=[
        CustomImageProfile(
            name="nginx",
            displayName="NGINX preview",
            description="Serve a local static-site preview over the pod network",
            image="docker.io/nginxinc/nginx-unprivileged:1.27.5-alpine",
            mode="sidecar",
            ports=[CustomImagePort(name="http", containerPort=8080)],
        )
    ],
)
app = create_app(settings, FakeManager())  # type: ignore[arg-type]
