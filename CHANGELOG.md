# Changelog

All notable changes to Devboxes are documented here. The project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses semantic versioning for releases.

## [Unreleased]

### Added

- Added opt-in, operator-approved GPU profiles across Helm, the API, CLI, dashboard, and in-product documentation, including default and explicitly named profile selection.
- Added portable extended-resource requests for NVIDIA, AMD, Intel, partitioned, shared, device-plugin, and compatible Dynamic Resource Allocation bridge configurations, with optional per-profile workspace images, RuntimeClasses, supplemental groups, node selectors, and tolerations.
- Added authenticated installation capability discovery, GPU allocation reporting, scheduler diagnostics, strict chart and controller validation, Helm contract tests, and end-to-end controller coverage.

### Changed

- Persist the fully resolved GPU profile on each workspace so stop, start, and Insights reconciliation cannot silently change or remove an existing hardware allocation after Helm configuration changes.
- Show accelerator allocation in CLI list and status output and in the browser workbench while keeping CPU-only creation as the default.

### Fixed

- Made the Insights HTTP integration fixture use the current observation time so the full controller suite remains deterministic after its original fixture date.

### Security

- Restrict clients to named operator-owned profiles instead of accepting arbitrary images, resource names, RuntimeClasses, supplemental groups, selectors, tolerations, privileged mode, or host mounts.

## [0.3.0] - 2026-07-14

### Added

- Added opt-in Devboxes Insights with local Codex and Claude telemetry ingestion, aggregate Git activity, durable workspace outboxes, and a persistent SQLite history store.
- Added an authenticated, responsive Insights dashboard plus CLI summary, collector status, recent activity, CSV/JSON export, and explicit purge workflows.
- Added Helm configuration, schema validation, retained central storage, scoped per-workspace ingest Secrets, workspace sidecars, backups, retention, and operational documentation for Insights.

### Changed

- Assigned every workspace a stable Insights instance identifier while leaving active legacy workspaces running and reporting that a normal restart is required to enable collection.
- Expanded the Kind and release gates to prove provider-shaped ingestion, replay deduplication, Git baselines, controller and workspace restarts, retained history, backups, SSH, and explicit purge behavior.

### Fixed

- Made the Insights agent create its private outbox path before privilege drop, bind its loopback receiver before background work begins, and retry recoverable startup failures.
- Preserved integer token totals across the SQLite API boundary and removed narrow-screen activity-list overflow from the dashboard.

### Security

- Added independent workspace and controller allowlists that reject prompts, responses, logs, commands, paths, Git identities, commit messages, and provider identity attributes before persistence.
- Added scoped HMAC ingest credentials with write-only routes and kept controller and master access tokens out of workspace Deployments.

## [0.2.1] - 2026-07-13

### Fixed

- Restored interactive SSH PTY allocation on capability-enforcing Kubernetes runtimes by granting workspace OpenSSH only `AUDIT_WRITE` in addition to the existing minimal capabilities.
- Re-enabled the real SSH PTY lifecycle in pull-request and published-release cluster gates.

## [0.2.0] - 2026-07-13

### Added

- Added native CLI browser authorization with an external system browser, numeric loopback callback, high-entropy state, PKCE S256, CSRF-protected approval and denial, automatic code exchange, and `--no-open` support.
- Added versioned, scoped, expiring CLI bearer tokens with strict claim validation, an optional dedicated signing key, and a domain-separated key derived from the existing access token by default.
- Added a pinned Ghostty `xterm-ghostty` terminfo source with upstream MIT attribution, broad ncurses terminfo packages, and image-level tmux regression coverage.

### Changed

- Changed interactive `devbox login` to eliminate terminal token prompting while preserving explicit `--token` and `DEVBOX_TOKEN` compatibility for automation and headless use.
- Changed `devbox-shell` to validate and preserve installed terminal capabilities, retain `DEVBOX_ORIGINAL_TERM` and `COLORTERM`, and fall back deterministically for unknown or untrusted terminal names.
- Configured tmux to use the installed `tmux-256color` entry internally and advertise truecolor only for known capable terminal families.

### Security

- Authorization codes are opaque, hash-only at rest, bounded, automatically pruned, bound to the exact client, redirect, PKCE challenge, subject, and expiry, and atomically consumed once.
- CLI authorization and token responses use no-store and referrer protections; login return targets and loopback redirects reject open redirects, non-numeric hosts, unexpected paths, and unsupported PKCE methods.

## [0.1.2] - 2026-07-13

### Fixed

- Made `devbox login` honor `DEVBOX_TOKEN` for non-interactive authentication.
- Made the CLI trust operating-system certificate roots so self-hosted installations can use an administrator-installed private CA.

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

[Unreleased]: https://github.com/vicotrbb/devboxes/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/vicotrbb/devboxes/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/vicotrbb/devboxes/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/vicotrbb/devboxes/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/vicotrbb/devboxes/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/vicotrbb/devboxes/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/vicotrbb/devboxes/releases/tag/v0.1.0
