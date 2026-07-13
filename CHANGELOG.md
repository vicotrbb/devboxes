# Changelog

All notable changes to Devboxes are documented here. The project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses semantic versioning for releases.

## [Unreleased]

## [0.1.1] - 2026-07-13

### Added

- Public golden path, CLI and API references, operations runbook, troubleshooting guide, and documentation index.
- ESLint, Prettier, Markdownlint, local-link validation, and punctuation validation for browser code and technical documentation.

### Changed

- Tightened Rust and Python quality policy, regression tests, and contributor guidance.
- Removed internal product and design specifications from the public repository after preserving durable requirements in maintained documentation.
- Updated the workspace to Rust 1.97 and uv 0.11.28, the controller image to uv 0.11.28, the CLI TOML parser to 1.1, and pinned GitHub Actions to their current supported releases.
- Added release gates that install the checksum-verified public CLI, inspect multi-architecture image attestations, render the published OCI chart with Helm 3 and Helm 4, and run the full lifecycle against published artifacts.

### Fixed

- Updated pytest to a patched release and expanded CI auditing to cover development dependencies in the Python lockfile.
- Extended the workspace Deployment progress deadline for cold pulls of the prepared image on slower registry paths.

## [0.1.0] - 2026-07-12

### Added

- Rust CLI with login, create, list, status, SSH/tmux, start, stop, delete, JSON output, port forwarding, and safe purge confirmation.
- Authenticated FastAPI controller, browser workbench, full in-product documentation, health/readiness endpoints, Prometheus metrics, and automatic TTL stop.
- Prepared multi-architecture workspace image with Rust, Node.js, Python, GitHub CLI, Codex CLI, Claude Code, SSH, tmux, shell tooling, and optional credential bootstrap.
- Persistent home volumes, SSH host identity, retained-volume reuse and expansion, and explicit non-destructive lifecycle semantics.
- Portable Helm chart with values schema, namespace-scoped RBAC, configurable storage, ingress, LoadBalancer or NodePort SSH, ServiceMonitor, and disruption budget.
- macOS and Linux CLI releases, SHA-256 verification installer, GHCR images, OCI chart publishing, image provenance attestations, and clean Kind install CI.

[Unreleased]: https://github.com/vicotrbb/devboxes/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/vicotrbb/devboxes/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/vicotrbb/devboxes/releases/tag/v0.1.0
