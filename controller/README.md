# Devboxes controller

FastAPI controller, authenticated REST API, and server-rendered dashboard for Devboxes.

```bash
uv sync --extra dev
uv run uvicorn devboxes_controller.app:create_app --factory --reload
uv run pytest
```

For local development, set `DEVBOXES_KUBECONFIG_CONTEXT` to a disposable Kubernetes context and provide a non-production `DEVBOXES_ACCESS_TOKEN`.
