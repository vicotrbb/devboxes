# Devboxes controller

FastAPI controller, authenticated REST API, and server-rendered dashboard for Devboxes.

Authentication supports the master bearer token, signed browser sessions with CSRF, and a
native CLI Authorization Code plus PKCE flow that issues scoped expiring bearer tokens.

```bash
uv sync --extra dev
uv run uvicorn devboxes_controller.app:create_app --factory --reload
uv run pytest
```

For local development, set `DEVBOXES_KUBECONFIG_CONTEXT` to a disposable Kubernetes context and provide a non-production `DEVBOXES_ACCESS_TOKEN`.

See the [API reference](../docs/api.md), [architecture](../docs/architecture.md), [GPU acceleration](../docs/gpu.md), and [operations runbook](../docs/operations.md) for supported behavior and deployment guidance.
