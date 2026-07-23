# Configuration

Use a values file for durable installations:

```bash
helm show values oci://ghcr.io/vicotrbb/charts/devboxes --version 0.5.0 > values.yaml
helm upgrade --install devboxes oci://ghcr.io/vicotrbb/charts/devboxes \
  --version 0.5.0 \
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
| `controller.authorizationCodeTtlSeconds` | `120` | Lifetime of one-time CLI authorization codes |
| `controller.authorizationCodeStoreSize` | `1024` | Maximum pending authorization codes retained in memory |
| `controller.cliTokenTtlSeconds` | `2592000` | Scoped CLI token lifetime, 30 days by default |
| `controller.cliSigningKeyKey` | empty | Optional signing-key field in `controller.existingSecret` |
| `controller.resources` | requests 100m/128Mi, limit 512Mi | Controller requests and limits |

The chart also exposes controller replicas, pull secrets, session lifetime, cleanup interval, log level, labels, annotations, node selectors, tolerations, and affinity in `values.yaml`.

When `controller.cliSigningKeyKey` is empty, the controller derives the signing key from the
master access token using a versioned, domain-separated HMAC. Set the field only if the
existing controller Secret contains a dedicated key of at least 32 characters. Rotating the
master token in derived mode, or rotating the dedicated key, revokes all issued CLI tokens.

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

## Custom image profiles

Custom images are disabled by default. Enable a small operator-owned catalog when users need a prebuilt service or a tested Devboxes workspace derivative. The controller accepts only a configured profile name or its exact configured image reference, resolves it before creating Kubernetes resources, and pins the complete profile on the resulting Deployment.

| Value | Default | Meaning |
| --- | --- | --- |
| `workspace.customImages.enabled` | `false` | Publish configured profiles and accept image requests |
| `workspace.customImages.profiles[].name` | none | Stable lowercase identifier exposed to clients |
| `workspace.customImages.profiles[].displayName` | none | Human-readable catalog label |
| `workspace.customImages.profiles[].description` | empty | Short service or workspace purpose |
| `workspace.customImages.profiles[].image` | none | Reviewed container reference, preferably digest-pinned |
| `workspace.customImages.profiles[].mode` | `sidecar` | `sidecar` for application images or `workspace` for a compatible Devboxes derivative |
| `workspace.customImages.profiles[].pullPolicy` | `IfNotPresent` | `Always`, `IfNotPresent`, or `Never` |
| `workspace.customImages.profiles[].resources.cpuRequest` | `25m` | Sidecar CPU request; rejected for a workspace profile |
| `workspace.customImages.profiles[].resources.memoryRequest` | `32Mi` | Sidecar memory request; rejected for a workspace profile |
| `workspace.customImages.profiles[].resources.cpuLimit` | `500m` | Sidecar CPU limit; rejected for a workspace profile |
| `workspace.customImages.profiles[].resources.memoryLimit` | `512Mi` | Sidecar memory limit; rejected for a workspace profile |
| `workspace.customImages.profiles[].ports` | `[]` | Optional named pod-local ports from 1024 through 65535 for discovery and SSH tunnels |

Example service sidecar profile:

```yaml
workspace:
  customImages:
    enabled: true
    profiles:
      - name: nginx
        displayName: NGINX preview
        description: Serve a local static-site preview
        image: docker.io/nginxinc/nginx-unprivileged:1.27.5-alpine
        mode: sidecar
        pullPolicy: IfNotPresent
        resources:
          cpuRequest: 25m
          memoryRequest: 32Mi
          cpuLimit: 500m
          memoryLimit: 512Mi
        ports:
          - name: http
            containerPort: 8080
```

A sidecar receives no Devboxes Secret, persistent-volume mount, Kubernetes service-account token, extra capability, or public Kubernetes Service. It must run as a non-root user on an unprivileged port from 1024 through 65535. It shares only the pod network namespace, so a user can reach a declared port through `devbox ssh NAME -- -L LOCAL:127.0.0.1:PORT`.

`mode: workspace` replaces the interactive container. Use it only for a tested derivative of the matching Devboxes workspace image that preserves the entrypoint, `dev` user, SSH service on port `2222`, persistent-home setup, mounted Secret handling, and readiness behavior. Workspace profiles use the selected Devboxes preset for compute and cannot declare the sidecar-only `resources` envelope. A workspace image cannot combine with a GPU profile that defines `workspaceImage`; a sidecar profile can combine with GPU because it does not alter the primary workspace container.

The chart rejects unknown fields, duplicate profile names or image references, unsafe image strings, privileged ports, more than 32 profiles, and enabled configurations without profiles. The controller validates resource quantities and limits before serving requests. Existing devboxes retain their pinned profiles if the catalog later changes or is disabled. See [custom image profiles](images.md) for the full security and lifecycle model.

## GPU acceleration

GPU acceleration is disabled by default. Operators configure one or more named profiles and choose a default profile for `devbox create --gpu`. CPU-only creation remains available regardless of whether GPU support is enabled.

| Value | Default | Meaning |
| --- | --- | --- |
| `gpu.enabled` | `false` | Publish configured profiles and accept GPU requests |
| `gpu.defaultProfile` | empty | Profile selected by CLI `--gpu`; required when enabled |
| `gpu.profiles[].name` | none | Stable lowercase identifier exposed to clients |
| `gpu.profiles[].displayName` | none | Human-readable accelerator name |
| `gpu.profiles[].description` | empty | Short workload or service-level description |
| `gpu.profiles[].resourceName` | none | Vendor-qualified Kubernetes extended resource |
| `gpu.profiles[].count` | none | Integer resource units requested and limited |
| `gpu.profiles[].workspaceImage` | empty | Optional complete GPU-capable Devboxes workspace image |
| `gpu.profiles[].runtimeClassName` | empty | Optional existing RuntimeClass selected for the pod |
| `gpu.profiles[].supplementalGroups` | `[]` | Up to eight non-root device group IDs required by the node runtime |
| `gpu.profiles[].nodeSelector` | `{}` | Optional operator-owned GPU node labels |
| `gpu.profiles[].tolerations` | `[]` | Optional taint tolerations limited to standard fields |

Example NVIDIA profile:

```yaml
gpu:
  enabled: true
  defaultProfile: nvidia-l4
  profiles:
    - name: nvidia-l4
      displayName: NVIDIA L4
      description: One dedicated L4 for inference and CUDA development
      resourceName: nvidia.com/gpu
      count: 1
      workspaceImage: ghcr.io/example/devboxes-workspace-cuda:12.8
      runtimeClassName: nvidia
      nodeSelector:
        accelerator: nvidia-l4
      tolerations:
        - key: nvidia.com/gpu
          operator: Exists
          effect: NoSchedule
```

Example AMD profile:

```yaml
gpu:
  enabled: true
  defaultProfile: amd-rocm
  profiles:
    - name: amd-rocm
      displayName: AMD ROCm GPU
      description: One GPU for ROCm development
      resourceName: amd.com/gpu
      count: 1
      workspaceImage: ghcr.io/example/devboxes-workspace-rocm:6.4
      supplementalGroups: [44, 109]
      nodeSelector:
        accelerator: amd-rocm
```

The AMD group IDs are examples only. Use the actual `video`, `render`, or vendor device groups required by your node image and runtime. Omit `supplementalGroups` when device permissions already work for the `dev` user. Group zero is rejected.

The pod receives each configured group and the workspace entrypoint adds `dev` to the matching image group, creating a `devbox-device-ID` group when the numeric ID is otherwise unnamed. This ensures OpenSSH sessions retain access after initializing the login user's groups.

`workspaceImage` must preserve the complete interactive Devboxes workspace contract, including the entrypoint, `dev` user, SSH setup, persistent home layout, and runtime secret mount. Derive it from the matching released workspace image and add only the workload's pinned user-space GPU libraries. The Insights sidecar retains the installation's release workspace image, so a profile override cannot replace its privacy sanitizer. A host driver, device plugin, and container runtime remain cluster prerequisites.

The chart rejects unknown fields, duplicate profiles, invalid Kubernetes names, unsafe toleration combinations, missing defaults, more than 32 profiles, and enabled configurations with no profiles. The controller repeats semantic validation at startup. Clients can select only a profile name; they cannot override the trusted scheduling or image policy.

The resolved profile is stored with each Deployment. Changing or removing a Helm profile affects only later creations. Stop and start retain the original allocation. Delete and recreate the box to select a new profile. See [GPU acceleration](gpu.md) for the full model and operational guidance.

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

## Insights

Insights is opt-in and disabled by default. When enabled, the controller uses one replica, a `Recreate` deployment strategy, and a persistent SQLite volume. Each new or normally restarted workspace gets a loopback metrics collector sidecar and a scoped write-only ingest credential.

| Value | Default | Meaning |
| --- | --- | --- |
| `insights.enabled` | `false` | Enable central storage, workspace collection, APIs, CLI queries, and the dashboard |
| `insights.signingKeyKey` | empty | Optional dedicated ingest-signing key in `controller.existingSecret` |
| `insights.storage.existingClaim` | empty | Reuse an existing controller database PVC |
| `insights.storage.storageClass` | empty | Empty uses the cluster default StorageClass |
| `insights.storage.size` | `2Gi` | Requested central database capacity |
| `insights.storage.warningBytes` | `1717986918` | Database-size warning threshold exposed by the API and dashboard |
| `insights.storage.accessMode` | `ReadWriteOnce` | Use `ReadWriteOncePod` when supported and operationally preferred |
| `insights.storage.retainOnDelete` | `true` | Keep the chart-created database PVC when the Helm release is deleted |
| `insights.retention.rawDays` | `30` | Raw metric-point retention |
| `insights.retention.hourlyDays` | `90` | Hourly rollup retention |
| `insights.retention.dailyDays` | `365` | Daily rollup and sparse Git activity retention |
| `insights.agent.scanIntervalSeconds` | `60` | Workspace Git scan and heartbeat interval |
| `insights.agent.repositoryDepth` | `4` | Maximum repository discovery depth below `/home/dev/workspace` |
| `insights.agent.maxQueueBytes` | `134217728` | Per-workspace durable outbox byte limit |
| `insights.agent.maxQueueAgeSeconds` | `604800` | Per-workspace durable outbox age limit |

The central PVC must support SQLite file locking and write-ahead logging. Do not use NFS or another network filesystem with unreliable locking. See [Insights](insights.md) for data semantics, trust boundaries, rollout behavior, and restore procedures.

## Observability

`/health` verifies the process. `/ready` verifies Kubernetes API access and, when enabled, the Insights store. `/metrics` exposes low-cardinality controller and Insights operational metrics. Set `serviceMonitor.enabled=true` only when the Prometheus Operator CRDs already exist. Add `serviceMonitor.labels` if your Prometheus selector requires them.

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
