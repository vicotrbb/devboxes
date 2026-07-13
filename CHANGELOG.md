# Changelog

All notable changes to Devboxes are documented here. The project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses semantic versioning for releases.

## [Unreleased]

### Added

- Public golden path, CLI and API references, operations runbook, troubleshooting guide, and documentation index.
- ESLint, Prettier, Markdownlint, local-link validation, and punctuation validation for browser code and technical documentation.

### Changed

- Tightened Rust and Python quality policy, regression tests, and contributor guidance.
- Removed internal product and design specifications from the public repository after preserving durable requirements in maintained documentation.

## [0.1.0] - 2026-07-12

### Added

- Rust CLI with login, create, list, status, SSH/tmux, start, stop, delete, JSON output, port forwarding, and safe purge confirmation.
- Authenticated FastAPI controller, browser workbench, full in-product documentation, health/readiness endpoints, Prometheus metrics, and automatic TTL stop.
- Prepared multi-architecture workspace image with Rust, Node.js, Python, GitHub CLI, Codex CLI, Claude Code, SSH, tmux, shell tooling, and optional credential bootstrap.
- Persistent home volumes, SSH host identity, retained-volume reuse and expansion, and explicit non-destructive lifecycle semantics.
- Portable Helm chart with values schema, namespace-scoped RBAC, configurable storage, ingress, LoadBalancer or NodePort SSH, ServiceMonitor, and disruption budget.
- macOS and Linux CLI releases, SHA-256 verification installer, GHCR images, OCI chart publishing, image provenance attestations, and clean Kind install CI.

[Unreleased]: https://github.com/vicotrbb/devboxes/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vicotrbb/devboxes/releases/tag/v0.1.0
