# Architecture

Devboxes is a namespaced Kubernetes application with three independently consumable parts: the controller and dashboard image, the workspace image, and the Rust CLI.

## Request path

Automation may authenticate with the master bearer token. Interactive `devbox login` uses
the native-app Authorization Code flow with PKCE S256: the CLI binds a numeric loopback
callback, opens the external browser, validates state, receives a short-lived one-time code,
and exchanges it for a scoped CLI bearer token. The browser exchanges the master token for
a signed, HTTP-only SameSite session plus a separate CSRF token. The controller never sends
the master access token back to either client.

Authorization codes live only in a bounded in-memory store. The controller retains a
SHA-256 digest, client ID, exact loopback redirect URI, PKCE challenge, subject, and expiry.
Codes expire after about two minutes, are consumed atomically once, and are pruned during
store operations. Approval and denial require the browser session's form CSRF token.

CLI tokens are HMAC-signed JWTs with fixed issuer, audience, type, scope, subject, issued
time, expiry, and token ID claims. The default signing key is derived from the master token
with explicit domain separation; an optional dedicated signing key can be supplied from the
existing controller Secret. Rotating the effective signing key revokes all issued CLI tokens.
There are no refresh tokens.

The controller translates lifecycle operations into native Kubernetes resources in its own namespace:

- One `Deployment` per devbox for disposable compute.
- One `Service` per devbox for SSH through `LoadBalancer` or `NodePort`.
- One `PersistentVolumeClaim` per devbox, mounted at `/home/dev`.
- When Insights is enabled, one scoped ingest `Secret` per devbox and one central Insights `PersistentVolumeClaim` for the controller.

Resource names are deterministic (`devbox-NAME`), and labels plus annotations carry controller ownership, creation time, expiry, preset, repository, retained storage size, and any resolved GPU allocation.

## GPU resolution path

GPU acceleration adds an operator-owned policy layer without changing resource ownership:

```text
Helm GPU profiles
       |
       v
validated controller settings
       |
       +---- authenticated capability catalog ----> CLI and dashboard
       |
user selects profile name
       |
       v
resolved pinned snapshot
       |
       v
Deployment pod template ----> scheduler ----> device plugin or DRA-bridged resource
```

The create API accepts only an optional profile name. The controller resolves that name before any Kubernetes write, then applies the trusted image override, RuntimeClass, supplemental groups, node selector, tolerations, and extended resource count. It writes the extended resource to both requests and limits on the main container. Insights and other sidecars do not receive GPU resources. The Insights sidecar also remains on the installation's release workspace image, preserving its pinned privacy sanitizer when the interactive container uses a specialized GPU image.

The resolved profile is stored as a bounded Deployment annotation. Existing boxes therefore retain their allocation across stop, start, TTL expiry, and template reconciliation even if the Helm catalog later changes. Capability discovery publishes only profile names, labels, descriptions, resources, and counts. Scheduling details and images remain operator policy.

## Persistence model

Disconnecting SSH leaves the pod and tmux session running. Stopping scales the Deployment to zero, which ends processes but leaves the PVC. Deleting removes the Deployment and Service while retaining the PVC by default. Purging explicitly deletes the PVC.

The SSH host key lives on the persistent volume. Recreating a previously deleted devbox with the same name therefore retains both files and host identity. A purge intentionally creates a new identity.

TTL expiry is equivalent to `stop`: it scales compute to zero and never deletes data. Starting a stopped devbox renews its original TTL from the new start time.

When Insights is enabled, persistence has two additional layers. A bounded SQLite outbox lives on each workspace home PVC, so accepted local metric batches survive workspace and controller outages. The controller stores sanitized, deduplicated points, Git aggregates, collector health, and time rollups in a separate central SQLite PVC. Ordinary workspace deletion and home purge do not remove central history. Insights history has its own explicit purge operation.

A UUID instance identity is stored on both the Deployment and home PVC. Retaining and reusing the PVC preserves that identity. Purging the PVC and recreating the box creates a new identity. This separates a box name from the lifetime of the storage that produced its data.

## Readiness

A devbox becomes `ready` only when its workspace pod reports Ready and its SSH Service has a usable endpoint. `LoadBalancer` uses the first published IP or hostname, with an optional configured fallback host. `NodePort` combines the configured node host with the port allocated by Kubernetes.

The workspace entrypoint refuses to start without `SSH_AUTHORIZED_KEYS`. It prepares the persistent home, host key, optional credentials, and optional repository clone before starting OpenSSH. The controller's TCP readiness probe therefore represents a usable SSH daemon, not merely a scheduled pod.

## Security boundaries

- Controller RBAC is a Role scoped to the release namespace; it cannot manage cluster-wide resources.
- Workspace service accounts have no RBAC binding and do not mount Kubernetes API tokens.
- GPU clients can choose only a configured profile name. They cannot inject images, device resources, RuntimeClasses, supplemental groups, selectors, tolerations, privileged mode, host paths, or device paths.
- Workspace Secrets are mounted read-only with mode `0440`, scoped to the workspace group, and are not embedded in either image.
- Each Insights ingest credential is HMAC-signed, write-only, scoped to one box and UUID instance, and stored in a dedicated namespaced Secret. It is never a controller, browser, or CLI credential.
- The controller runs as a non-root user with a read-only root filesystem and all Linux capabilities dropped.
- The workspace runs as root during initialization, then exposes only the unprivileged `dev` SSH user. Password login and root login are disabled.
- The trusted `dev` user has passwordless `sudo`. The pod adds only `AUDIT_WRITE`, `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID`, `SETUID`, and `SYS_CHROOT`; `AUDIT_WRITE` lets OpenSSH allocate audited PTYs, while `SYS_ADMIN` and privileged mode are not used.
- SSH host checking uses a stable alias scoped to both the Devboxes installation and box name, preventing collisions across installations.
- CLI callbacks accept only exact HTTP loopback URIs with numeric loopback hosts, explicit non-privileged ports, and `/callback`; login return targets accept only the internal authorization route.

The master controller token is an operator credential. Anyone holding it can create, stop,
delete, or purge every devbox in that installation. A scoped CLI token currently carries the
same lifecycle authority for that single operator, but it expires and cannot mint browser
sessions or new CLI tokens. Devboxes does not currently provide tenant isolation or per-user
authorization.

## Scheduling and storage

Presets specify requests and memory limits while intentionally leaving CPU burstable:

| Preset | CPU request | Memory request | Memory limit | PVC request |
| --- | ---: | ---: | ---: | ---: |
| small | 250m | 512Mi | 4Gi | 20Gi |
| medium | 750m | 2Gi | 8Gi | 30Gi |
| large | 2 | 4Gi | 16Gi | 50Gi |

A retained PVC is expanded when a larger preset is requested, subject to the StorageClass supporting expansion. PVCs are never shrunk.

GPU profiles add one vendor-qualified extended resource with the same integer value in requests and limits. Kubernetes extended resources are not overcommitted unless the installed device plugin intentionally advertises shared units. Optional selectors and tolerations direct boxes to operator-prepared GPU pools; an optional RuntimeClass activates a non-default vendor runtime.

The controller does not guess capacity or reserve a device before creation. Kubernetes scheduling is the source of truth. A box stays `starting` when capacity or constraints cannot be satisfied, and the controller exposes the `PodScheduled=False` reason to the CLI and dashboard. Device plugins remain responsible for allocation and device injection.

## Availability

With Insights disabled, the controller is stateless apart from signed sessions and the short-lived in-memory authorization-code store. Restarting it cancels pending CLI approvals but does not affect issued tokens or devbox state.

With Insights enabled, the controller also owns a stateful SQLite database. The chart requires one controller replica and uses a `Recreate` strategy so one pod owns the database volume at a time. Kubernetes remains the source of truth for workspace lifecycle, while the Insights database is the source of truth for retained telemetry and aggregate activity.

Insights does not restart active legacy workspaces during an upgrade. They expose `restart_required` until a normal stop and start installs the sidecar and its current scoped credential. Stopped workspaces are reconciled in place, and every start reconciles the template before compute is scaled up.
