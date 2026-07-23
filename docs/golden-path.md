# Golden path

This is the recommended Devboxes path for predictable startup, strong interactive performance, and low operational overhead. It keeps the supported security and persistence model intact.

## 1. Prepare the cluster

Use Kubernetes 1.29 or newer with enough allocatable CPU and memory for the selected presets. Install a dynamic `ReadWriteOnce` StorageClass backed by low-latency storage. For topology-constrained storage, prefer a StorageClass with `volumeBindingMode: WaitForFirstConsumer`, which lets Kubernetes consider the scheduled pod before binding the volume.

Provide one reliable SSH exposure path:

- Use `LoadBalancer` when the implementation assigns reachable addresses quickly and supports one Service per devbox.
- Use `NodePort` when clients can reach stable node addresses and the cluster NodePort range is allowed from trusted networks.

Keep `externalTrafficPolicy: Cluster` for the portable default. Use `Local` only after confirming that the load balancer health checks and routing send traffic to the node hosting the devbox pod. `Local` can remove an extra node hop, but incorrect health routing makes SSH unavailable.

For GPU workloads, first install and verify the vendor driver, device plugin or Dynamic Resource Allocation driver with a compatible extended-resource bridge, and container runtime outside Devboxes. Confirm the intended extended resource appears in node allocatable capacity. Label and taint GPU pools deliberately, then encode those choices in named Devboxes profiles. Follow [GPU acceleration](gpu.md) before enabling the feature.

## 2. Use a pinned release and durable values

Store installation values in version control without secrets. Pin the chart version and image tags by installing one release version as a unit.

```yaml
controller:
  externalUrl: https://devboxes.example.com
  cookieSecure: true
  defaultTtlHours: 24
  maxTtlHours: 168

workspace:
  storageClass: fast-rwo
  sshService:
    type: LoadBalancer
    externalTrafficPolicy: Cluster
    loadBalancerSourceRanges:
      - 192.0.2.0/24

insights:
  enabled: true
  storage:
    storageClass: fast-rwo
    size: 2Gi
    retainOnDelete: true
```

```bash
kubectl create namespace devboxes
# Create devboxes-auth and devboxes-workspace here, as described below.

helm upgrade --install devboxes oci://ghcr.io/vicotrbb/charts/devboxes \
  --version 0.4.0 \
  --namespace devboxes \
  --values values.yaml
```

Before the Helm command, create `devboxes-auth` and `devboxes-workspace` through your secret manager or the commands in [credentials](credentials.md). Do not place secret values in the Helm values file. Replace the example source range with the actual trusted client network. Insights remains optional; when enabled, use a low-latency RWO volume with reliable SQLite locking.

## 3. Warm the workspace image when startup latency matters

Workspace creation uses `imagePullPolicy: IfNotPresent`. The first devbox scheduled to a node can therefore wait for the workspace image, while later boxes on that node reuse the local image. On clusters you control, pre-pull the exact released workspace image on every eligible node, or use a registry mirror close to the cluster.

Do not depend on a cache existing on only one node. Kubernetes may schedule the next workspace elsewhere. Keep registry credentials valid even when images are pre-pulled.

GPU profiles may select a larger derived workspace image. Pre-pull each configured GPU image only on nodes eligible for that profile, and validate host driver compatibility before making the profile the default.

When using custom image profiles, pre-pull each reviewed sidecar image on every node that can host a regular workspace, or use a nearby registry mirror. Keep profile resource envelopes conservative and verify that an application port is reachable over the pod loopback interface. For a workspace-mode profile, apply the same SSH, retained-home, and restart checks as the release workspace image before publishing it.

## 4. Install and verify the CLI

Use the checksummed release installer, authenticate over HTTPS, and verify the identity returned by the controller.

```bash
curl -fsSLO https://raw.githubusercontent.com/vicotrbb/devboxes/main/scripts/install-devbox-cli.sh
less install-devbox-cli.sh
sh install-devbox-cli.sh

devbox login --url https://devboxes.example.com
devbox --version
```

For local-only controller access, port-forward the Service and use `http://127.0.0.1:8000`. The CLI rejects plaintext HTTP for non-loopback hosts.

## 5. Start with the medium preset

`medium` is the recommended balanced starting point for interactive builds, language servers, and AI coding tools. It requests 750 millicores and 2 GiB of memory, allows CPU bursts, sets an 8 GiB memory limit, and requests a 30 GiB home volume. The CLI default remains `small`, so select `medium` explicitly.

```bash
devbox create atlas \
  --preset medium \
  --ttl 24 \
  --repo owner/project \
  --ssh
```

The command waits for the pod, OpenSSH, and the Service address, then connects to the persistent tmux session. Disconnecting does not stop the box. Use `devbox stop atlas` when the workday ends, then `devbox start atlas` and `devbox ssh atlas` to resume.

Choose `small` for light editing and administrative work. Choose `large` for memory-heavy builds, multiple language servers, or local inference. If a process is OOM-killed or the pod is evicted under memory pressure, move to a larger preset. A retained PVC expands when a larger preset is used, but it never shrinks.

When GPU profiles are enabled, discover them before creating an accelerated box:

```bash
devbox gpu profiles
devbox create inference --gpu --preset medium --ssh
```

CPU and memory presets remain independent from accelerator selection. Start with the smallest preset that satisfies host-side preprocessing and compilation, then measure before increasing it.

When approved custom images are enabled, use a profile rather than a raw registry reference. A service profile is useful for a local application dependency or preview server while the Devboxes workspace remains your SSH entry point:

```bash
devbox image profiles
devbox create docs-preview --preset small --image nginx --ssh
devbox ssh docs-preview -- -L 8080:127.0.0.1:8080
```

Inspect the profile mode before creation. A sidecar profile can combine with a GPU profile; a workspace profile cannot be paired with a GPU profile that sets its own workspace image. The profile is pinned on the created box, so delete and recreate an intentional test box when you want to verify a catalog revision.

## 6. Tune in the right order

Measure before changing the cluster. Startup and interactive performance usually improve in this order:

1. Storage latency and throughput, especially source trees, package caches, and build outputs under `/home/dev`.
2. Workspace image availability on the scheduled node.
3. Enough allocatable CPU and memory to satisfy the preset request without contention.
4. A direct, healthy SSH network path from the client to the workspace Service.
5. A larger preset when the workload, rather than the platform, is resource-bound.

For a GPU box, also verify advertised device capacity, profile selectors, taints, RuntimeClass availability, and the derived workspace image. A valid profile can remain queued when all matching devices are allocated; the scheduler reason appears in `devbox status`.

Use these checks for a slow or pending box:

```bash
kubectl -n devboxes get pod,pvc,service -l devboxes.bonalab.org/name=atlas -o wide
kubectl -n devboxes describe pod -l devboxes.bonalab.org/name=atlas
kubectl -n devboxes get events --sort-by=.lastTimestamp
kubectl top pod -n devboxes -l devboxes.bonalab.org/name=atlas
```

`kubectl top` requires Metrics Server. Storage and node-level metrics depend on the cluster platform.

## 7. Preserve the fast path

- Keep active repositories and tool caches under `/home/dev`, which persists across stop, delete without `--purge`, and pod replacement.
- Use `devbox stop` instead of deleting a box when you plan to resume with the same environment.
- Avoid `--purge` unless the home volume is intentionally disposable.
- Keep one controller replica unless you have tested the operational behavior of multiple writers. Kubernetes remains the source of truth, but the supported default is one trusted operator and one controller replica.
- When Insights is enabled, keep exactly one controller replica, monitor collector freshness and queue loss, and use the online backup export for its database.
- Track image, chart, and CLI versions together, then read the changelog before every upgrade.

See the Kubernetes documentation for [resource requests and pod quality of service](https://kubernetes.io/docs/concepts/workloads/pods/pod-qos/), [StorageClass binding modes](https://kubernetes.io/docs/concepts/storage/storage-classes/), [container image caching](https://kubernetes.io/docs/concepts/containers/images/), and [Service traffic policies](https://kubernetes.io/docs/concepts/services-networking/service/).
