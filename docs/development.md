# Development

## Tooling

- Rust stable and Cargo for the CLI.
- Python 3.12+ and `uv` for the controller.
- Docker with BuildKit for images.
- Helm 3.14+ or 4 and `kubectl` for packaging.
- ShellCheck for installation and workspace scripts.
- Kind for the clean-cluster integration test.

Run the full local gates:

```bash
make bootstrap
make lint
make test
make helm
make images
```

## Controller

```bash
cd controller
uv sync --extra dev
DEVBOXES_ACCESS_TOKEN=development-access-token-at-least-32-characters \
DEVBOXES_COOKIE_SECURE=false \
DEVBOXES_KUBECONFIG_CONTEXT=kind-devboxes \
  uv run uvicorn devboxes_controller.app:create_app --factory --reload
```

Use a disposable cluster context. The controller creates and deletes Kubernetes resources in `DEVBOXES_NAMESPACE`.

## CLI

```bash
cd cli
cargo fmt --check
cargo clippy --all-targets --all-features --locked -- -D warnings
cargo test --all-features --locked
cargo run -- login --url http://127.0.0.1:8000
```

Set `DEVBOX_CONFIG` to a temporary path during development to avoid changing your normal CLI profile.

## Dashboard

The server-rendered UI lives under `controller/src/devboxes_controller/templates`, with plain CSS and JavaScript under `static`. Preserve the design contract in [DESIGN.md](../DESIGN.md): WCAG 2.2 AA contrast, complete keyboard operation, visible focus, textual status, and reduced-motion support.

The test fake can preview all lifecycle states without a Kubernetes cluster:

```bash
cd controller
uv run uvicorn tests.preview_app:app --port 8000
```

## Helm and Kind

```bash
helm lint charts/devboxes --strict
helm template devboxes charts/devboxes --namespace devboxes
kind create cluster --name devboxes
```

The CI Kind job builds the controller image, loads it into a clean cluster, creates placeholder Secrets, installs the chart, waits for rollout, and exercises both the health and authenticated API endpoints.

## Release contract

The CLI crate version, controller project version, chart `version`, and chart `appVersion` must match the release tag without the leading `v`. A `vX.Y.Z` tag creates a GitHub Release, publishes checksummed CLI archives, pushes multi-architecture images, attests image provenance, and publishes the chart to GHCR.
