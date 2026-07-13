from devboxes_controller.app import create_app
from devboxes_controller.config import Settings

from .fakes import FakeManager

settings = Settings(
    access_token="preview-access-token-at-least-32-characters",
    cookie_secure=False,
    cleanup_interval_seconds=3600,
)
app = create_app(settings, FakeManager())  # type: ignore[arg-type]
