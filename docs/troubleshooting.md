# Troubleshooting

Diagnose from the controller outward. Preserve exact errors, timestamps, and Kubernetes events, but redact tokens, provider credentials, repository names, and public addresses before sharing output.

## Fast triage

```bash
helm status devboxes -n devboxes
kubectl get deployment,service,pvc,pod -n devboxes -o wide
kubectl logs deployment/devboxes -n devboxes --tail=200
kubectl get events -n devboxes --sort-by=.lastTimestamp
devbox --version
```

Then inspect one box:

```bash
kubectl get deployment,service,pvc,pod -n devboxes \
  -l devboxes.bonalab.org/name=atlas -o wide
kubectl describe pod -n devboxes \
  -l devboxes.bonalab.org/name=atlas
```

## Controller is not ready

Symptoms include `/ready` returning 503, CLI transport failures, or the dashboard failing to list boxes.

1. Confirm the controller pod is Ready and inspect its logs.
2. Verify the ServiceAccount, Role, and RoleBinding exist in the same namespace.
3. Check Kubernetes API reachability and service-account token mounting on the controller.
4. Confirm the configured namespace matches the Helm release namespace.
5. Review NetworkPolicy or API-server authorization denials.

The controller needs namespaced access to Deployments, Services, Pods, and PVCs. It does not need cluster-wide RBAC.

## Authentication fails

For `401 Authentication required`, confirm the CLI profile URL and token:

```bash
DEVBOX_CONFIG=/path/to/profile.toml devbox login --url https://devboxes.example.com
```

If the controller Secret changed, restart the controller and log in again. Existing sessions are signed with the old token and become invalid.

For browser `403 Missing or invalid CSRF token`, clear the site cookies and log in again. Confirm that an HTTPS installation sets `controller.cookieSecure=true`. A secure cookie is not sent over plain HTTP.

## TLS or URL validation fails

The CLI rejects non-loopback HTTP, embedded credentials, query strings, and fragments. Use a valid certificate for the controller hostname. There is intentionally no skip-verification option.

For local access:

```bash
kubectl -n devboxes port-forward service/devboxes 8000:8000
devbox login --url http://127.0.0.1:8000
```

For remote access, fix DNS, certificate trust, and the ingress route rather than weakening the client.

## A box remains starting

The `starting` state means the controller is waiting for pod readiness or a usable SSH Service address.

Check the pod's scheduling condition, image pull, volume mounts, and readiness probe. Common event reasons include insufficient CPU or memory, an unbound PVC, an unavailable StorageClass, image pull authentication failure, or a missing workspace Secret.

The workspace entrypoint requires `SSH_AUTHORIZED_KEYS`. Validate the key exists without printing its value:

```bash
kubectl get secret devboxes-workspace -n devboxes \
  -o go-template='{{if index .data "SSH_AUTHORIZED_KEYS"}}present{{else}}missing{{end}}{{"\n"}}'
```

If the pod is Ready but the box still starts, inspect the Service next.

## SSH address remains pending

For `LoadBalancer`, inspect `.status.loadBalancer.ingress` and the load-balancer controller events:

```bash
kubectl get service devbox-atlas-ssh -n devboxes -o yaml
```

Confirm the provider supports one LoadBalancer Service per box, the address quota is not exhausted, and any annotations or `loadBalancerClass` match the installed controller.

For `NodePort`, confirm `workspace.sshService.host` resolves to a reachable node or node load balancer and that the allocated NodePort is allowed from the client network.

## SSH connection is refused or times out

Distinguish network reachability from SSH authentication:

```bash
devbox status atlas
nc -vz SSH_HOST SSH_PORT
ssh -vvv -p SSH_PORT dev@SSH_HOST
```

Check the Service endpoints, firewall rules, load-balancer health, `externalTrafficPolicy`, and pod readiness. With `externalTrafficPolicy: Local`, the load balancer must route only to the node that currently hosts the workspace pod.

If OpenSSH reports a host-key change after an intentional purge, remove only the alias named in the error from `known_hosts`. An unexpected host-key change without a purge should be investigated before connecting.

## Repository clone fails

Public GitHub repositories need no token. Private repositories require a valid `GH_TOKEN` with access to that repository. Inspect `/var/log/devbox-init.log` inside the workspace, confirm the repository value matches the accepted `owner/repository` or HTTPS GitHub form, and verify provider access with `gh auth status`.

A clone runs only when the target home is new and no existing repository occupies the destination. Reused PVCs intentionally preserve their current working tree.

## PVC is pending or cannot expand

```bash
kubectl describe pvc devbox-atlas-home -n devboxes
kubectl get storageclass
```

Confirm a default or configured StorageClass exists, supports `ReadWriteOnce`, has available capacity, and can provision in the scheduled topology. Expansion requires `allowVolumeExpansion: true`. Devboxes never shrinks a retained PVC.

Do not delete a PVC as a troubleshooting shortcut. Back it up and confirm the data-retention decision first.

## Box is degraded

The controller marks known `CrashLoopBackOff`, `ErrImagePull`, `ImagePullBackOff`, and failed pod states as degraded. Inspect pod events, current and previous logs, workspace Secret mounts, and image credentials:

```bash
kubectl logs -n devboxes -l devboxes.bonalab.org/name=atlas
kubectl logs -n devboxes -l devboxes.bonalab.org/name=atlas --previous
```

Resolve the underlying Kubernetes condition, then allow the Deployment to reconcile. Recreating with the same name can reuse the retained home, but it should not replace diagnosis of a repeatable startup failure.

## TTL stopped the box

TTL expiry scales compute to zero and retains storage. Resume normally:

```bash
devbox start atlas
devbox ssh atlas
```

Starting renews the original TTL. Use a longer TTL for sustained work, up to the controller maximum, or stop explicitly when idle.

## Safe support bundle

Follow [SUPPORT.md](../SUPPORT.md). Replace names and addresses where needed, never include Secret manifests or decoded values, and report vulnerabilities through the private channel in [SECURITY.md](../SECURITY.md).
