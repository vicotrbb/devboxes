# Multi-tenancy

Devboxes can isolate teams or individual users into operator-defined tenants. Multi-tenancy
is opt-in: existing installations keep their single-operator behavior until
`tenancy.enabled=true`.

Each tenant is both an application authorization boundary and a dedicated Kubernetes
namespace. A request is resolved to exactly one tenant before any workspace, capability, or
Insights operation runs. Workspace names are unique only inside that tenant, so two tenants
may both own a box named `atlas` without sharing compute, storage, SSH identity, credentials,
or telemetry.

## Isolation contract

Enabling multi-tenancy provides:

- one Kubernetes namespace, tokenless workspace ServiceAccount, namespaced controller Role,
  and RoleBinding per tenant;
- tenant-local Deployments, Services, PVCs, Insights ingest Secrets, workspace Secrets, and
  stable `/home/dev` volumes;
- authenticated principal memberships with `viewer`, `member`, or `admin` roles;
- tenant-filtered lifecycle, capability, dashboard, CLI, and Insights operations;
- optional tenant `ResourceQuota`, StorageClass, TTL ceiling, preset allowlist, GPU-profile
  allowlist, and custom-image allowlist;
- an optional ingress NetworkPolicy that permits only the workspace SSH listener on TCP port
  2222 for managed workspace pods;
- tenant and creator metadata on controller-owned resources, plus tenant-scoped SSH host-key
  aliases.

The master controller token remains an installation-wide break-glass operator credential.
It has the synthetic `operator` role in every configured tenant. Principal tokens are
independent credentials loaded from an existing Kubernetes Secret and never stored in Helm
values or Helm release history.

Devboxes workspaces are development machines with passwordless `sudo`; they are not a
container sandbox for hostile code. Namespace separation, RBAC, quota, and NetworkPolicy are
defense-in-depth around trusted tenant members. Kubernetes admission policy, runtime
hardening, node separation, Secret encryption, and network controls remain cluster-operator
responsibilities.

## Roles

| Role | Read workspaces and Insights | Create, start, stop, delete | Purge home or Insights data |
| --- | ---: | ---: | ---: |
| `viewer` | Yes | No | No |
| `member` | Yes | Yes | No |
| `admin` | Yes | Yes | Yes |
| `operator` | Yes | Yes | Yes |

`operator` is reserved for the master controller identity and is not accepted in principal
membership configuration. Delete without `purge=true` retains the home PVC, so members may
remove compute without permanently removing data.

Application roles govern the Devboxes API, dashboard, lifecycle, and Insights operations.
They do not grant or revoke a Linux shell: SSH authorization is separately defined by the
public keys in the tenant workspace Secret. In particular, a `viewer` who also has an
authorized SSH key can modify files inside a running workspace even though they cannot mutate
its Kubernetes lifecycle through Devboxes.

Roles apply to the entire selected tenant. For strict per-person ownership and quota, create
one tenant namespace per person. A team tenant intentionally shares its workspace inventory
and aggregate quota among all members.

## Configure Helm

Create tenant namespaces and credentials before the upgrade, or set
`createNamespace: true` to let Helm create and retain the namespaces. The controller namespace
is still the Helm release namespace.

```yaml
tenancy:
  enabled: true
  defaultTenant: platform
  existingPrincipalSecret: devboxes-principals
  networkPolicy:
    enabled: true
  tenants:
    - id: platform
      displayName: Platform Engineering
      namespace: devboxes-platform
      createNamespace: true
      namespaceLabels:
        owner: platform
        pod-security.kubernetes.io/enforce: baseline
      workspaceSecretName: devboxes-platform-workspace
      workspaceServiceAccountName: devboxes-workspace
      storageClass: fast-rwo
      maxTtlHours: 72
      allowedPresets: [small, medium]
      allowedGpuProfiles: [nvidia-l4]
      defaultGpuProfile: nvidia-l4
      allowedImageProfiles: [nginx]
      resourceQuota:
        enabled: true
        hard:
          requests.cpu: "8"
          requests.memory: 32Gi
          requests.storage: 500Gi
          count/deployments.apps: "10"
          count/pods: "10"
    - id: research
      displayName: Research
      namespace: devboxes-research
      createNamespace: true
      workspaceSecretName: devboxes-research-workspace
      maxTtlHours: 168
      allowedPresets: [small, medium, large]
  principals:
    - subject: alice@example.com
      displayName: Alice
      tokenKey: alice-token
      defaultTenant: platform
      memberships:
        - tenant: platform
          role: admin
        - tenant: research
          role: viewer
```

Omitting `allowedGpuProfiles` or `allowedImageProfiles` inherits the installation catalog.
An explicit empty list allows none. `defaultGpuProfile` overrides the installation default
for that tenant and must appear in its GPU allowlist. If neither the tenant default nor the
installation default is available, users must select an explicit allowed profile.
`allowedPresets` must contain at least one preset. `maxTtlHours` may narrow, but never exceed,
`controller.maxTtlHours`. Quota quantities use native Kubernetes `ResourceQuota` keys and
syntax.

The chart validates duplicate tenant IDs, duplicate namespaces, memberships, token keys,
defaults, TTLs, and catalog references before rendering. `createNamespace: true` namespaces
carry `helm.sh/resource-policy: keep`, so uninstalling Devboxes cannot silently delete all
tenant PVCs with their namespace.

## Provision Secrets

Every tenant needs its own workspace Secret in its own namespace. This keeps SSH public keys
and optional GitHub or AI-provider credentials out of other tenant pods.

```bash
kubectl create namespace devboxes-platform
kubectl -n devboxes-platform create secret generic devboxes-platform-workspace \
  --from-file=SSH_AUTHORIZED_KEYS="$HOME/.ssh/platform_authorized_keys"
```

Create principal tokens in one Secret in the controller namespace. Each value must be unique,
different from the master token, and contain at least 32 strong random characters.

```bash
alice_token="$(openssl rand -hex 32)"
kubectl -n devboxes create secret generic devboxes-principals \
  --from-literal=alice-token="$alice_token"
unset alice_token
```

The key must match the principal's `tokenKey`. The chart mounts the Secret read-only for
controller uid/gid 10001 with group-readable mode `0440`; the controller reads it at startup.
Restart the controller after changing token data or principal memberships.

If `workspace.serviceAccount.create=false`, provision every configured
`workspaceServiceAccountName` in its tenant namespace. It should have no RoleBinding and
`automountServiceAccountToken: false`.

## Authenticate and select a tenant

Principal tokens work with the browser login page, direct bearer authentication, and
headless CLI login. The browser session and issued CLI token retain the principal identity
but do not hard-code one tenant; live membership is checked on every request.

```bash
DEVBOX_TOKEN="$ALICE_DEVBOXES_TOKEN" \
  devbox login --url https://devboxes.example.com --tenant platform

devbox tenant list
devbox tenant use research
devbox --tenant platform list
DEVBOX_TENANT=research devbox list
```

CLI tenant precedence is `--tenant`, `DEVBOX_TENANT`, then the saved configuration. A
successful login saves the server-selected tenant. `devbox tenant use` verifies membership
before changing the saved value.

Raw API clients select a tenant with `X-Devboxes-Tenant`. Omitting it uses the identity's
configured default:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $ALICE_DEVBOXES_TOKEN" \
  -H "X-Devboxes-Tenant: platform" \
  https://devboxes.example.com/api/v1/devboxes
```

An unknown or unauthorized tenant returns `403` without revealing whether the tenant exists.
The dashboard uses a tenant switcher and keeps the tenant in navigation links and API
requests. Viewer sessions render lifecycle controls as unavailable instead of relying only
on a server-side error.

## Quota and policy behavior

The create form, authenticated documentation, capabilities API, and CLI catalogs expose only
the selected tenant's effective presets, TTL ceiling, GPU profiles, and custom images.
Server-side validation repeats those checks before any Kubernetes write.

Kubernetes remains authoritative for aggregate quota. A direct `ResourceQuota` admission
denial during create or start returns `409 Conflict`. Pod-level quota enforcement can occur
asynchronously in the Deployment controller; in that case the workspace becomes
`degraded` with the quota diagnostic instead of falsely reporting readiness. The dashboard
shows configured hard limits, while `kubectl describe resourcequota -n TENANT_NAMESPACE`
shows current usage.

The default tenant NetworkPolicy applies only to controller-managed workspace pods and
allows ingress only on the workspace SSH target port, TCP `2222`, from any source that can
already reach the namespace. The external Service may still expose port `22`. This preserves
SSH behavior while blocking arbitrary pod-port ingress. Egress remains open for source
repositories, package registries, provider APIs, DNS, and the controller Insights service.
Enforcement requires a NetworkPolicy-capable CNI. Add cluster-specific source restrictions
or egress policies separately when required.

## Insights and retained data

Every Insights instance, metric point, Git aggregate, rollup, collector status, query,
export, and purge carries a tenant ID. JSON and CSV exports are filtered in the query.
SQLite export creates an online backup and removes every other tenant before returning it.
An administrator can purge only the active tenant.

Upgrading an existing Insights database adds tenant ownership and assigns legacy rows to
`tenancy.defaultTenant`. Choose that default deliberately before enabling multi-tenancy.
Back up the database first and verify legacy history through that tenant after rollout.

Workspace PVCs and SSH host keys are namespaced. The CLI host-key alias includes the
installation, tenant, and box name, preventing a same-named workspace in another tenant from
reusing the local known-host entry.

## Rollout and rollback

Before enabling tenancy:

1. Back up workspace and Insights PVCs.
2. Choose the default tenant that will own existing workspace and Insights records. To adopt
   release-namespace workspaces in place, keep that tenant in the current Helm release
   namespace and leave `createNamespace: false`; otherwise migrate those resources explicitly.
3. Create every tenant namespace and workspace Secret, or configure Helm to create retained
   namespaces.
4. Create the principal-token Secret and verify unique values.
5. Render the chart and inspect Namespace, Role, RoleBinding, ServiceAccount,
   ResourceQuota, and NetworkPolicy resources.
6. Upgrade, wait for controller readiness, and verify each identity with `devbox tenant
   list`.
7. Exercise viewer, member, admin, cross-tenant denial, quota rejection, SSH persistence,
   and tenant-filtered Insights export.

Do not disable multi-tenancy while tenant namespaces still contain workspaces you expect the
single-tenant controller to manage. Disabled mode intentionally returns to the release
namespace and does not merge or move resources. Inventory, back up, and migrate retained
PVCs explicitly before changing that boundary.
