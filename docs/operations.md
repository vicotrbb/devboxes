# Operations

This runbook covers the supported single-operator deployment model. Keep installation values, chart version, controller image, workspace image, and CLI version aligned.

## Routine health check

```bash
helm status devboxes -n devboxes
kubectl get deployment,service,pvc,pod -n devboxes -o wide
kubectl rollout status deployment/devboxes -n devboxes
kubectl logs deployment/devboxes -n devboxes --tail=200
./scripts/verify-install.sh
```

`/health` proves that the HTTP process responds. `/ready` performs a namespaced Deployment list against the Kubernetes API and returns 503 when that dependency is unavailable. Neither endpoint creates resources.

## Metrics and logs

`/metrics` exposes `devboxes_total{state=...}` for `starting`, `ready`, `stopped`, and `degraded` boxes. Enable the chart ServiceMonitor only when Prometheus Operator CRDs already exist:

```yaml
serviceMonitor:
  enabled: true
  interval: 30s
  labels:
    release: monitoring
```

Controller logs record configuration loading, automatic TTL stops, and cleanup errors. Kubernetes events remain the primary source for scheduling, volume, image, and Service allocation failures.

Recommended alerts include controller readiness failure, controller crash loops, degraded boxes, workspace restart growth, PVC capacity pressure, and boxes that remain starting beyond the normal image and volume provisioning window.

## Capacity planning

Add the CPU and memory requests of concurrently running presets, then reserve capacity for the controller and cluster services. CPU has no workspace limit, so boxes can burst when the node has spare capacity. Memory is limited per preset and exceeding it causes container termination.

Stopped boxes consume PVC capacity but no workspace CPU or memory. Include retained volumes in storage forecasts. Track provisioned and used bytes through the CSI provider, because Kubernetes PVC requests do not report filesystem utilization.

Use node selectors, affinity, tolerations, and an existing PriorityClass only when the cluster scheduling policy requires them. A priority class can improve scheduling under pressure, but it can also preempt lower-priority workloads.

## Backups and restore

The home PVC is the durable state. Back up important PVCs with a CSI snapshot system or storage-provider backup that is compatible with the active StorageClass.

Before relying on a backup process:

1. Stop the devbox to quiesce processes and filesystem writes.
2. Snapshot or back up the PVC named `devbox-NAME-home`.
3. Restore into a non-production namespace or isolated cluster.
4. Verify ownership, SSH host keys, repositories, tool state, and provider credentials.
5. Record recovery time and the exact restore procedure.

Devboxes does not create VolumeSnapshots and does not automatically back up or delete PVCs.

## Upgrades

Read [CHANGELOG.md](../CHANGELOG.md), back up important volumes, and render the new chart before applying it.

```bash
helm template devboxes oci://ghcr.io/vicotrbb/charts/devboxes \
  --version NEW_VERSION \
  --namespace devboxes \
  --values values.yaml > /tmp/devboxes-new.yaml

helm upgrade devboxes oci://ghcr.io/vicotrbb/charts/devboxes \
  --version NEW_VERSION \
  --namespace devboxes \
  --values values.yaml \
  --wait
```

Then run `scripts/verify-install.sh`, confirm `/ready`, list existing boxes, create a disposable smoke box, connect over SSH, stop it, start it, and delete it with purge.

Prefer a reviewed values file over `--reuse-values`. It makes removed defaults and configuration drift visible.

## Rollback

Inspect release history and changelog compatibility first:

```bash
helm history devboxes -n devboxes
helm rollback devboxes REVISION -n devboxes --wait
```

A Helm rollback changes controller resources, not the contents of workspace PVCs. Do not roll back across an explicitly incompatible data or resource migration without following that release's instructions.

## Token rotation

Replace the configured controller Secret value, then restart the controller:

```bash
kubectl rollout restart deployment/devboxes -n devboxes
kubectl rollout status deployment/devboxes -n devboxes
```

Rotation invalidates browser sessions and saved CLI tokens. Log in again after the rollout. Rotate immediately after suspected disclosure, then review controller access logs available at the ingress or network boundary.

Rotate workspace provider tokens independently through the workspace Secret. Existing files copied into persistent homes are not overwritten automatically, so revoke compromised provider sessions at the provider and update affected homes explicitly.

## Uninstall and data retention

Uninstalling the chart removes chart-owned controller resources. Per-devbox resources created by the controller and retained PVCs require an explicit data decision.

Inventory before deletion:

```bash
kubectl get deployment,service,pvc -n devboxes \
  -l app.kubernetes.io/managed-by=devboxes-controller
```

Back up required PVCs, delete boxes through the CLI, and use `--purge` only for volumes approved for permanent removal. Verify the namespace contents before deleting the namespace.

## Disaster recovery

Recreate the namespace, controller Secret, workspace Secret, and Helm release from versioned values. Restore home PVCs with their expected `devbox-NAME-home` names before recreating corresponding boxes. Reusing the name reconnects compute to the retained home and its SSH host identity.

If the original controller token is unavailable, issue a new token and authenticate clients again. Kubernetes resources and PVC contents do not depend on browser or CLI sessions.
