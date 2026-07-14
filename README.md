# Devboxes

[![CI](https://github.com/vicotrbb/devboxes/actions/workflows/ci.yml/badge.svg)](https://github.com/vicotrbb/devboxes/actions/workflows/ci.yml)
[![Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Kubernetes 1.29+](https://img.shields.io/badge/kubernetes-1.29%2B-326ce5.svg)](charts/devboxes/Chart.yaml)

Devboxes turns Kubernetes capacity into ready-to-use development machines. Create one from your terminal, connect over SSH, and return to the same tmux session after a disconnect. Compute is disposable; the home volume persists until you explicitly purge it.

For the recommended production-shaped setup and fastest daily workflow, follow the [golden path](docs/golden-path.md). The complete documentation map is in [docs/index.md](docs/index.md).

```console
$ devbox create atlas --preset medium --repo owner/project --ssh
→ preparing atlas…
atlas  ready
  storage:    30Gi
  ssh:        ssh -t dev@192.0.2.40
```

Each workspace includes Rust, Node.js, Python, `uv`, GitHub CLI, Codex CLI, Claude Code, Git, zsh, tmux, compilers, and common terminal tools. GitHub and AI-provider credentials are optional runtime secrets; none are baked into the image.

## What ships

- A Rust `devbox` CLI for create, list, inspect, SSH, start, stop, and delete workflows.
- A FastAPI controller with an authenticated API, accessible browser workbench, documentation, metrics, health checks, and TTL cleanup.
- A versioned Helm chart with values schema validation and namespace-scoped RBAC.
- Multi-architecture controller and workspace images for `linux/amd64` and `linux/arm64`.
- Persistent SSH host identity, shell state, tool installs, account state, and source under `/home/dev`.
- GitHub Releases with macOS and Linux CLI binaries and SHA-256 checksums.

Devboxes is currently a single-operator system: one shared token controls every box in one installation. It is suitable for a trusted personal cluster or trusted operator group, not mutually untrusted tenants.

## Requirements

- Kubernetes 1.29 or newer.
- Helm 3.14+ or Helm 4.
- A default `ReadWriteOnce` StorageClass, or an explicit `workspace.storageClass`.
- One of these ways to reach SSH services:
  - `LoadBalancer` support from your cloud, MetalLB, kube-vip, or another implementation; or
  - reachable Kubernetes nodes and `workspace.sshService.type=NodePort`.
- An SSH public key.
- An ingress controller and TLS certificate only if you expose the dashboard through ingress. Port-forwarding works without either.

The workspace container intentionally supports passwordless `sudo` for the trusted development user. Its pod drops all capabilities and adds back a small set needed by `sudo`, but it is not compatible with the Kubernetes `restricted` Pod Security profile. Use the `baseline` profile or an equivalent policy in the Devboxes namespace.

## Install

The quickest supported path is the repository installer. It creates or updates the required Secrets, installs the local Helm chart, and waits for the controller.

```bash
git clone https://github.com/vicotrbb/devboxes.git
cd devboxes
./scripts/install.sh
```

By default, the dashboard is available only through a local port-forward and each workspace requests a `LoadBalancer` SSH service.

```bash
kubectl -n devboxes port-forward service/devboxes 8000:8000
open http://127.0.0.1:8000  # macOS; use your browser elsewhere
```

Retrieve the generated access token without relying on platform-specific `base64` flags:

```bash
kubectl -n devboxes get secret devboxes-auth \
  -o go-template='{{index .data "access-token" | base64decode}}{{"\n"}}'
```

Run the post-install check:

```bash
./scripts/verify-install.sh
```

### Install from the OCI chart

If you manage Secrets separately, install the published chart directly:

```bash
kubectl create namespace devboxes
kubectl -n devboxes create secret generic devboxes-auth \
  --from-literal=access-token="$(openssl rand -hex 32)"
kubectl -n devboxes create secret generic devboxes-workspace \
  --from-file=SSH_AUTHORIZED_KEYS="$HOME/.ssh/id_ed25519.pub"

helm install devboxes oci://ghcr.io/vicotrbb/charts/devboxes \
  --version 0.2.0 \
  --namespace devboxes
```

The chart never embeds credential values. It references existing Kubernetes Secrets so it also works with External Secrets Operator, Sealed Secrets, SOPS, Infisical, Vault, and other secret-management workflows.

### Configure dashboard ingress

This example uses a generic nginx ingress class and an existing TLS Secret:

```bash
./scripts/install.sh \
  --set ingress.enabled=true \
  --set ingress.className=nginx \
  --set ingress.host=devboxes.example.com \
  --set ingress.tls.enabled=true \
  --set ingress.tls.secretName=devboxes-tls \
  --set controller.externalUrl=https://devboxes.example.com \
  --set controller.cookieSecure=true
```

Use your cluster's ingress class. The controller intentionally does not offer an insecure TLS bypass in the CLI.

### Configure workspace SSH

`LoadBalancer` is the default. Provider-specific annotations and source ranges are supported:

```yaml
workspace:
  sshService:
    type: LoadBalancer
    annotations:
      service.beta.kubernetes.io/example-private-load-balancer: "true"
    externalTrafficPolicy: Local
    loadBalancerSourceRanges:
      - 192.0.2.0/24
```

For clusters without a load balancer, let Kubernetes allocate a distinct NodePort for each box and provide a node address reachable from CLI clients:

```bash
./scripts/install.sh \
  --set workspace.sshService.type=NodePort \
  --set workspace.sshService.host=dev-node.example.com
```

See [configuration](docs/configuration.md) for every supported value and platform examples.

## Install and use the CLI

Release binaries support macOS and Linux on Intel/AMD and ARM64:

```bash
curl -fsSLO https://raw.githubusercontent.com/vicotrbb/devboxes/main/scripts/install-devbox-cli.sh
less install-devbox-cli.sh
sh install-devbox-cli.sh
```

You can also build from source:

```bash
cargo install --locked --git https://github.com/vicotrbb/devboxes devbox-cli
```

Authenticate and create a box:

```bash
devbox login --url https://devboxes.example.com
devbox create atlas --preset medium --ttl 24 --repo owner/project --ssh
```

Login opens the system browser, asks the current Devboxes browser session to approve the
CLI, exchanges a one-time PKCE authorization code, verifies the resulting scoped token,
and stores it without displaying it. If the browser is not already signed in, the existing
operator login page appears first. This removes token pasting from the terminal; it does
not add SSO or unauthenticated LAN trust.

For a machine where the CLI cannot open a browser, print the URL and open it manually:

```bash
devbox login --url https://devboxes.example.com --no-open
```

For a port-forwarded controller, localhost HTTP is deliberately allowed:

```bash
devbox login --url http://127.0.0.1:8000
```

The daily lifecycle is small and explicit:

```bash
devbox list
devbox status atlas
devbox ssh atlas
devbox stop atlas       # ends processes; keeps /home/dev
devbox start atlas      # renews the original TTL
devbox delete atlas     # removes compute; keeps /home/dev
devbox delete atlas --purge  # permanently deletes the home volume
```

Pass OpenSSH options after `--`, for example:

```bash
devbox ssh atlas -- -L 3000:127.0.0.1:3000
```

The CLI stores its configuration at the platform config directory under `devbox/config.toml` with mode `0600` on Unix. `DEVBOX_URL`, `DEVBOX_TOKEN`, and `DEVBOX_CONFIG` support non-interactive and multi-profile workflows.

Existing automation can continue to use the master token through `DEVBOX_TOKEN` or an
explicit `--token`. Browser login receives an expiring CLI token instead of the master
credential.

## Credentials and prepared accounts

Only `SSH_AUTHORIZED_KEYS` is required. Add optional values to the `devboxes-workspace` Secret to prepare GitHub, Git, Codex, or Claude Code. Public GitHub repositories clone without `GH_TOKEN`.

| Secret key | Purpose |
| --- | --- |
| `SSH_AUTHORIZED_KEYS` | Required OpenSSH authorized keys file |
| `GH_TOKEN` | Private GitHub clones and authenticated `gh`/Git operations |
| `GIT_USER_NAME`, `GIT_USER_EMAIL` | Git author identity |
| `CODEX_AUTH_JSON` | Seed Codex account state on a new home volume |
| `CODEX_ACCESS_TOKEN` or `OPENAI_API_KEY` | Codex non-interactive bootstrap alternatives |
| `CLAUDE_CREDENTIALS_JSON` | Seed Claude Code account state on a new home volume |
| `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` | Claude Code runtime authentication alternatives |

Provider sessions can expire, and account-file portability may change. API tokens are the most automation-friendly option. Review your providers' current credential and usage policies before sharing any account material. See [credentials](docs/credentials.md) for safe setup patterns.

## Architecture

```text
devbox CLI / browser
          │ HTTPS + bearer/session auth
          ▼
Devboxes controller ─── Kubernetes API
          │                  │
          │                  ├─ Deployment (disposable compute)
          │                  ├─ Service (LoadBalancer or NodePort SSH)
          │                  └─ PVC (persistent /home/dev)
          ▼
  TTL cleanup and lifecycle state
```

The controller watches only its release namespace and receives namespace-scoped RBAC. Workspace pods do not receive Kubernetes service-account tokens. See [architecture](docs/architecture.md) for resource ownership, persistence, readiness, and threat boundaries.

## Development

```bash
make bootstrap
make lint
make test
make helm
make images
```

CI repeats these gates and performs a clean Kind-cluster Helm install with an authenticated API smoke test. Release tags publish four CLI targets, two multi-architecture images, checksums, provenance attestations for images, and the OCI Helm chart.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change. Security reports belong in a private [GitHub security advisory](https://github.com/vicotrbb/devboxes/security/advisories/new), not a public issue.

## Documentation

- [Golden path](docs/golden-path.md) for a performance-oriented installation and daily workflow.
- [CLI reference](docs/cli.md) and [API reference](docs/api.md) for client contracts.
- [Configuration](docs/configuration.md) and [credentials](docs/credentials.md) for installation details.
- [Operations](docs/operations.md) and [troubleshooting](docs/troubleshooting.md) for production ownership.
- [Architecture](docs/architecture.md) and [development](docs/development.md) for maintainers.

## Project status

Devboxes is at `v0.1`: useful and installable, with an intentionally narrow trust model. Compatibility follows semantic versioning after `v1.0`; before then, minor releases may include documented configuration or API changes. PVC data is never automatically deleted, including at TTL expiry.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
