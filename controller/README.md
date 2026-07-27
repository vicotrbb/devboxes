# Devboxes controller

FastAPI controller, authenticated REST API, and server-rendered dashboard for Devboxes.

Authentication supports the master bearer token, opt-in Secret-backed tenant principals,
signed browser sessions with CSRF, and a native CLI Authorization Code plus PKCE flow that
issues scoped expiring bearer tokens. Requests resolve one live tenant membership and role
before Kubernetes or Insights access.

```bash
uv sync --extra dev
uv run uvicorn devboxes_controller.app:create_app --factory --reload
uv run pytest
```

For local development, set `DEVBOXES_KUBECONFIG_CONTEXT` to a disposable Kubernetes context and provide a non-production `DEVBOXES_ACCESS_TOKEN`.

See the [API reference](../docs/api.md), [architecture](../docs/architecture.md),
[multi-tenancy](../docs/multi-tenancy.md), [GPU acceleration](../docs/gpu.md), [custom image
profiles](../docs/images.md), and [operations runbook](../docs/operations.md) for supported
behavior and deployment guidance.
