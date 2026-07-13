# Configuration

Use a values file for durable installations:

```bash
helm show values oci://ghcr.io/vicotrbb/charts/devboxes --version 0.1.2 > values.yaml
helm upgrade --install devboxes oci://ghcr.io/vicotrbb/charts/devboxes \
  --version 0.1.2 \
  --namespace devboxes \
  --create-namespace \
  --values values.yaml
```

`values.schema.json` rejects unknown top-level and component fields, invalid service types, incomplete NodePort configuration, invalid ports, and out-of-range TTLs before Kubernetes resources are rendered.

When `scripts/install.sh` runs from a source checkout, it uses the local chart by default. Set `DEVBOXES_CHART_SOURCE=oci` and `DEVBOXES_VERSION=X.Y.Z` to force a released OCI chart, or set `DEVBOXES_CHART_SOURCE=local` to require the checkout chart. This prevents release verification from silently falling back to local templates.

## Controller

| Value | Default | Meaning |
| --- | --- | --- |
| `controller.image.repository` | `ghcr.io/vicotrbb/devboxes-controller` | Controller image repository |
| `controller.image.tag` | chart app version | Controller image tag |
| `controller.existingSecret` | `devboxes-auth` | Existing Secret containing the access token |
| `controller.accessTokenKey` | `access-token` | Key inside the controller Secret |
| `controller.externalUrl` | `http://127.0.0.1:8000` | URL shown in documentation and used by CLI examples |
| `controller.displayName` | `operator` | Identity returned by `whoami` |
| `controller.clusterName` | `Kubernetes` | Cluster label shown in the dashboard |
| `controller.cookieSecure` | `false` | Set `true` whenever the external URL uses HTTPS |
| `controller.defaultTtlHours` | `24` | Default compute auto-stop TTL |
| `controller.maxTtlHours` | `168` | Maximum accepted TTL, up to seven days |
| `controller.resources` | requests 100m/128Mi, limit 512Mi | Controller requests and limits |

The chart also exposes controller replicas, pull secrets, session lifetime, cleanup interval, log level, labels, annotations, node selectors, tolerations, and affinity in `values.yaml`.

## Workspace

| Value | Default | Meaning |
| --- | --- | --- |
| `workspace.image.repository` | `ghcr.io/vicotrbb/devboxes-workspace` | Workspace image repository |
| `workspace.image.tag` | chart app version | Workspace image tag |
| `workspace.existingSecret` | `devboxes-workspace` | Existing Secret mounted into every box |
| `workspace.storageClass` | empty | Empty uses the cluster's default StorageClass |
| `workspace.priorityClassName` | empty | Optional existing PriorityClass |
| `workspace.imagePullSecret` | empty | Optional pull Secret for private image mirrors |
| `workspace.serviceAccount.create` | `true` | Create a tokenless workspace ServiceAccount |

The namespace must permit the workspace pod's documented `sudo` capability set. Kubernetes Pod Security `baseline` is compatible; `restricted` is not.

## SSH service

For clusters with load-balancer support:

```yaml
workspace:
  sshService:
    type: LoadBalancer
    host: ""
    annotations: {}
    loadBalancerClass: ""
    externalTrafficPolicy: Cluster
    loadBalancerSourceRanges: []
```

The optional `host` is a fallback used while or when the Service implementation does not publish status ingress. Use it only if the same hostname or IP routes to every per-box Service appropriately.

For NodePort, `host` is required and should be a stable node address or load-balanced node address reachable from every CLI client:

```yaml
workspace:
  sshService:
    type: NodePort
    host: dev-node.example.com
    externalTrafficPolicy: Cluster
```

Kubernetes allocates a distinct NodePort for every devbox. Do not firewall a single fixed port; allow the cluster's configured NodePort range from trusted client networks.

## Dashboard access

Ingress is disabled by default so a fresh installation is reachable safely by port-forward without assuming an ingress class or DNS zone.

```yaml
controller:
  externalUrl: https://devboxes.example.com
  cookieSecure: true
ingress:
  enabled: true
  className: nginx
  host: devboxes.example.com
  annotations: {}
  tls:
    enabled: true
    secretName: devboxes-tls
```

The chart does not create certificates or DNS records. Use cert-manager, your cloud controller, or an existing TLS Secret. Keep the controller on a trusted network unless you have intentionally hardened the surrounding ingress, authentication, and rate limiting for internet exposure.

## Observability

`/health` verifies the process, `/ready` verifies Kubernetes API access, and `/metrics` exposes a Prometheus gauge by devbox state. Set `serviceMonitor.enabled=true` only when the Prometheus Operator CRDs already exist. Add `serviceMonitor.labels` if your Prometheus selector requires them.

## Upgrades

Read [CHANGELOG.md](../CHANGELOG.md), back up important PVCs using your storage provider, then upgrade the chart and images together:

```bash
helm upgrade devboxes oci://ghcr.io/vicotrbb/charts/devboxes \
  --version NEW_VERSION \
  --namespace devboxes \
  --reuse-values
```

Prefer an explicit values file over `--reuse-values` for long-lived GitOps installations because it makes configuration reviewable and reproducible.

Follow the [golden path](golden-path.md) for performance-oriented defaults and the [operations runbook](operations.md) for rollout, backup, and recovery procedures.
