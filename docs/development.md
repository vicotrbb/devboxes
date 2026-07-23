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

The test fake can preview all lifecycle states, multiple GPU profiles, and an approved non-root, high-port custom image profile without a Kubernetes cluster:

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
| `workspace/` | Workspace image, SSH entrypoint, shell setup, tmux, secret bootstrap, and Insights agent |
| `scripts/` | Installation, verification, release consistency, documentation, and Kind E2E tooling |
| `docs/` | Public installation, usage, architecture, operations, troubleshooting, and development documentation |

Do not commit local plans, product specifications, generated previews, rendered Secrets, build artifacts, or caches. Test helpers stay next to the tests that consume them. User-facing examples and durable operational guidance belong under `docs/`.

## Helm and Kind

```bash
helm lint charts/devboxes --strict
helm template devboxes charts/devboxes --namespace devboxes
kind create cluster --name devboxes
```

The CI Kind job builds both images and the CLI, loads the images into a clean cluster, creates placeholder Secrets, and installs the local chart with Insights enabled. It exercises the authenticated API, SSH, scoped ingest Secret, provider-shaped OTLP batches, batch deduplication, Git baseline and activity, durable outbox during controller downtime, central database persistence, retained identity, CLI output and exports, explicit Insights purge, workspace recreation, host identity, and final PVC purge.

GPU coverage is intentionally layered because ordinary Kind workers do not expose production accelerator hardware. Controller tests prove disabled and named-profile API behavior, exact pod resources and scheduling fields, scheduler diagnostics, allocation reporting, and pinned profile reconciliation. CLI tests prove command parsing and request shape. `scripts/test-helm-gpu.sh` proves disabled, enabled, and invalid chart contracts. The clean-cluster test enables an intentionally unschedulable extended resource and proves capability discovery, CLI and API selection, the generated pod contract, Pending diagnostics, and complete cleanup. A real GPU cluster remains the acceptance environment for vendor driver, runtime, image, and workload compatibility.

Custom image coverage is layered too. Controller tests prove catalog parsing, disabled and unknown-selector rejection before Kubernetes writes, exact sidecar and workspace manifests, secret and volume isolation, response reporting, pinned-profile reconciliation, and GPU workspace-image conflict rejection. CLI tests prove profile discovery and `--image` request shape. `scripts/test-helm-images.sh` proves disabled, enabled, and invalid chart contracts. The clean-cluster test builds a small service fixture, loads it into Kind, verifies the sidecar has no workspace mounts or credentials, reaches it over pod loopback, exercises SSH tunneling, and verifies cleanup. A deployment with real private registries or workspace-mode derivatives also requires the operator's production image, registry, architecture, and SSH lifecycle acceptance checks.

Privacy tests use fixtures derived from exact Codex and Claude Code clients pinned in the workspace image. Fixtures must use synthetic values. Never commit a real prompt, response, command, path, repository name, email address, provider credential, account identifier, or session identifier. The agent and controller sanitizers are separate trust boundaries and both require regression coverage.

## Release contract

The CLI manifest and lockfile, controller package and lockfile, chart `version` and `appVersion`, repository package metadata, installer default, public examples, and static asset cache keys must match. `scripts/check-version.sh` enforces that contract and verifies the current changelog section and comparison link.

A `vX.Y.Z` tag on an already-green `main` commit creates a GitHub Release, publishes four checksummed CLI archives, pushes multi-architecture images with SPDX SBOM and SLSA provenance attestations, and publishes the chart to GHCR. The workflow then verifies anonymous image pulls, both Linux architectures, signed provenance, public chart rendering under Helm 3 and Helm 4, strict Kubernetes schemas, the public checksum installer, and the complete clean-cluster lifecycle using only released chart and image artifacts. Run the same final lifecycle manually with `DEVBOXES_VERSION=X.Y.Z scripts/published-e2e.sh`.
