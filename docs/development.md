# Development

## Tooling

- Rust 1.96 and Cargo for the CLI, pinned by `rust-toolchain.toml`.
- Python 3.12+ and `uv` for the controller.
- Node.js 24+ and npm for JavaScript, Markdown, and repository checks.
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

The server-rendered UI lives under `controller/src/devboxes_controller/templates`, with plain CSS and JavaScript under `static`. Preserve WCAG 2.2 AA contrast, complete keyboard operation, visible focus, textual status, responsive layouts, and reduced-motion support. Keep inline scripts and handlers out of templates so the Content Security Policy remains strict.

The test fake can preview all lifecycle states without a Kubernetes cluster:

```bash
cd controller
uv run uvicorn tests.preview_app:app --port 8000
```

Run JavaScript, documentation, and formatting gates from the repository root:

```bash
npm ci
npm run lint
```

The documentation check validates local Markdown targets and rejects em dash and en dash punctuation in Markdown and HTML prose. Markdownlint enforces structure, ESLint uses the current flat configuration and recommended correctness rules, and Prettier checks browser and repository JavaScript formatting.

## Code layout

| Path | Responsibility |
| --- | --- |
| `cli/` | Rust command parsing, configuration, API client, SSH process, and output |
| `controller/src/devboxes_controller/` | FastAPI routes, authentication, settings, Kubernetes lifecycle, schemas, and manifests |
| `controller/tests/` | Unit and API regression tests plus the local UI preview fake |
| `charts/devboxes/` | Helm defaults, values schema, namespaced RBAC, and Kubernetes templates |
| `workspace/` | Workspace image, SSH entrypoint, shell setup, tmux, and secret bootstrap |
| `scripts/` | Installation, verification, release consistency, documentation, and Kind E2E tooling |
| `docs/` | Public installation, usage, architecture, operations, troubleshooting, and development documentation |

Do not commit local plans, product specifications, generated previews, rendered Secrets, build artifacts, or caches. Test helpers stay next to the tests that consume them. User-facing examples and durable operational guidance belong under `docs/`.

## Helm and Kind

```bash
helm lint charts/devboxes --strict
helm template devboxes charts/devboxes --namespace devboxes
kind create cluster --name devboxes
```

The CI Kind job builds both images, loads them into a clean cluster, creates placeholder Secrets, installs the local chart, waits for rollout, and exercises the authenticated API, SSH, stop, retention, recreation, host identity, and purge lifecycle.

## Release contract

The CLI manifest and lockfile, controller package and lockfile, chart `version` and `appVersion`, repository package metadata, installer default, public examples, and static asset cache keys must match. `scripts/check-version.sh` enforces that contract and verifies the current changelog section and comparison link.

A `vX.Y.Z` tag on an already-green `main` commit creates a GitHub Release, publishes four checksummed CLI archives, pushes multi-architecture images with SPDX SBOM and SLSA provenance attestations, and publishes the chart to GHCR. The workflow then verifies anonymous image pulls, both Linux architectures, signed provenance, public chart rendering under Helm 3 and Helm 4, strict Kubernetes schemas, the public checksum installer, and the complete clean-cluster lifecycle using only released chart and image artifacts. Run the same final lifecycle manually with `DEVBOXES_VERSION=X.Y.Z scripts/published-e2e.sh`.
