from devboxes_controller.app import create_app
from devboxes_controller.config import CustomImagePort, CustomImageProfile, GpuProfile, Settings
from devboxes_controller.models import DevboxState, Preset

from .fakes import FakeManager, sample_devbox

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
    multi_tenancy_enabled=True,
    default_tenant="platform",
    tenants=[
        {
            "id": "platform",
            "displayName": "Platform Engineering",
            "namespace": "devboxes",
            "allowedPresets": ["small", "medium"],
            "allowedGpuProfiles": ["nvidia-l4"],
            "allowedImageProfiles": ["nginx"],
            "resourceQuota": {
                "enabled": True,
                "hard": {
                    "requests.cpu": "8",
                    "requests.memory": "32Gi",
                },
            },
        },
        {
            "id": "research",
            "displayName": "Research",
            "namespace": "devboxes-research",
            "allowedPresets": ["medium", "large"],
            "allowedGpuProfiles": ["nvidia-shared"],
            "allowedImageProfiles": [],
            "maxTtlHours": 72,
        },
    ],
)
manager = FakeManager(settings)
manager.boxes.extend(
    [
        sample_devbox("simulation", preset=Preset.LARGE, tenant="research"),
        sample_devbox(
            "notebook",
            state=DevboxState.STOPPED,
            preset=Preset.MEDIUM,
            tenant="research",
        ),
    ]
)
app = create_app(settings, manager)  # type: ignore[arg-type]
