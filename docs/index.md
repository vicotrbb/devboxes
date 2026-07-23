# Documentation

Devboxes turns Kubernetes capacity into persistent, SSH-accessible development environments. This documentation covers the supported installation, daily use, operations, security, and contribution paths.

## Start here

- [README](../README.md), product overview, requirements, and the shortest installation path.
- [Golden path](golden-path.md), the recommended production-shaped setup and fastest daily workflow.
- [Configuration](configuration.md), every supported Helm value and deployment mode.
- [Credentials](credentials.md), controller authentication and optional workspace accounts.

## Use Devboxes

- [CLI reference](cli.md), commands, global flags, environment variables, output, and SSH forwarding.
- [API reference](api.md), authentication, endpoints, request and response contracts, and errors.
- [GPU acceleration](gpu.md), profile design, cluster prerequisites, images, scheduling, security, and diagnosis.
- [Custom image profiles](images.md), approved service sidecars, compatible workspace derivatives, security boundaries, and lifecycle behavior.
- [Insights](insights.md), opt-in AI telemetry, aggregate Git activity, privacy boundaries, retention, backup, and purge.
- The authenticated `/docs` page in a running controller, an operator-focused guide rendered with installation-specific values.

## Operate Devboxes

- [Architecture](architecture.md), components, Kubernetes resources, persistence, scheduling, and trust boundaries.
- [Operations](operations.md), health, metrics, capacity, backups, upgrades, recovery, and routine maintenance.
- [Troubleshooting](troubleshooting.md), symptom-led diagnosis for scheduling, storage, SSH, authentication, and image failures.
- [Security policy](../SECURITY.md), supported versions, private reporting, deployment controls, and supply-chain policy.
- [Support](../SUPPORT.md), public support channels and a redacted diagnostic bundle.

## Contribute

- [Development](development.md), toolchains, local workflows, test layers, code layout, and release contract.
- [Contributing](../CONTRIBUTING.md), review expectations, compatibility rules, and pull request requirements.
- [Code of Conduct](../CODE_OF_CONDUCT.md), community behavior and enforcement.

## Supported scope

Devboxes supports one trusted operator or trusted operator group per installation. The controller is namespaced, each workspace has persistent home storage, each SSH Service uses either `LoadBalancer` or `NodePort`, and GPU devices and custom images are optional operator-owned profiles. Multi-tenant authorization, arbitrary untrusted container execution, browser terminals, cluster-wide GPU driver installation, and automatic PVC deletion are outside the current scope.
