# Architecture

Devboxes is a namespaced Kubernetes application with three independently consumable parts: the controller and dashboard image, the workspace image, and the Rust CLI.

## Request path

The CLI authenticates with a bearer token. The browser exchanges the same access token for a signed, HTTP-only SameSite session plus a separate CSRF token. The controller never sends the raw access token back to either client.

The controller translates lifecycle operations into native Kubernetes resources in its own namespace:

- One `Deployment` per devbox for disposable compute.
- One `Service` per devbox for SSH through `LoadBalancer` or `NodePort`.
- One `PersistentVolumeClaim` per devbox, mounted at `/home/dev`.

Resource names are deterministic (`devbox-NAME`), and labels plus annotations carry controller ownership, creation time, expiry, preset, repository, and retained storage size.

## Persistence model

Disconnecting SSH leaves the pod and tmux session running. Stopping scales the Deployment to zero, which ends processes but leaves the PVC. Deleting removes the Deployment and Service while retaining the PVC by default. Purging explicitly deletes the PVC.

The SSH host key lives on the persistent volume. Recreating a previously deleted devbox with the same name therefore retains both files and host identity. A purge intentionally creates a new identity.

TTL expiry is equivalent to `stop`: it scales compute to zero and never deletes data. Starting a stopped devbox renews its original TTL from the new start time.

## Readiness

A devbox becomes `ready` only when its workspace pod reports Ready and its SSH Service has a usable endpoint. `LoadBalancer` uses the first published IP or hostname, with an optional configured fallback host. `NodePort` combines the configured node host with the port allocated by Kubernetes.

The workspace entrypoint refuses to start without `SSH_AUTHORIZED_KEYS`. It prepares the persistent home, host key, optional credentials, and optional repository clone before starting OpenSSH. The controller's TCP readiness probe therefore represents a usable SSH daemon, not merely a scheduled pod.

## Security boundaries

- Controller RBAC is a Role scoped to the release namespace; it cannot manage cluster-wide resources.
- Workspace service accounts have no RBAC binding and do not mount Kubernetes API tokens.
- Workspace Secrets are mounted read-only with mode `0400` and are not embedded in either image.
- The controller runs as a non-root user with a read-only root filesystem and all Linux capabilities dropped.
- The workspace runs as root during initialization, then exposes only the unprivileged `dev` SSH user. Password login and root login are disabled.
- The trusted `dev` user has passwordless `sudo`. The pod adds only `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID`, `SETUID`, and `SYS_CHROOT`; `SYS_ADMIN` and privileged mode are not used.
- SSH host checking uses a stable alias scoped to both the Devboxes installation and box name, preventing collisions across installations.

The shared controller token is an operator credential. Anyone holding it can create, stop, delete, or purge every devbox in that installation. Devboxes does not currently provide tenant isolation or per-user authorization.

## Scheduling and storage

Presets specify requests and memory limits while intentionally leaving CPU burstable:

| Preset | CPU request | Memory request | Memory limit | PVC request |
| --- | ---: | ---: | ---: | ---: |
| small | 250m | 512Mi | 4Gi | 20Gi |
| medium | 750m | 2Gi | 8Gi | 30Gi |
| large | 2 | 4Gi | 16Gi | 50Gi |

A retained PVC is expanded when a larger preset is requested, subject to the StorageClass supporting expansion. PVCs are never shrunk.

## Availability

The controller is stateless apart from signed sessions derived from its access token. The chart defaults to one replica because create operations are optimized for a single trusted operator. The Kubernetes API remains the source of truth, so a controller restart does not lose devbox state.
