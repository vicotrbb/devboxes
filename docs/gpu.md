# GPU acceleration

Devboxes supports optional GPU workloads through operator-approved profiles. A profile is a stable product contract that maps one name to a Kubernetes extended resource and the scheduling policy needed to reach it. Users choose a profile; they never supply arbitrary pod fields.

CPU-only creation remains the default. Enabling GPU support does not change existing boxes or ordinary CPU workloads.

## Why profiles are the API

GPU clusters vary across vendors, runtimes, node pools, partitioning modes, and sharing policies. Exposing raw Kubernetes fields through the CLI would couple users to those details and would create an unsafe image and scheduling injection surface. Named profiles keep the user workflow stable while operators retain control over:

- the vendor-qualified resource and integer count;
- an optional complete GPU-capable workspace image;
- an optional RuntimeClass;
- up to eight optional non-root device group IDs;
- bounded node selectors and tolerations;
- the meaning of dedicated, partitioned, or shared capacity.

The same profile catalog is returned by the authenticated API, printed by the CLI, and rendered in the dashboard. The API intentionally omits the image, RuntimeClass, supplemental groups, selectors, and tolerations from capability discovery.

## Cluster prerequisites

Before enabling a profile, verify all of the following:

1. GPU nodes are healthy and schedulable.
2. The vendor driver is installed on each matching node.
3. A device plugin, GPU Operator, or Dynamic Resource Allocation driver with a compatible bridge advertises the intended extended resource in node capacity.
4. The container runtime is configured for the vendor. Set `runtimeClassName` only when the runtime is not already the node default.
5. The workspace image contains user-space libraries compatible with the host driver and preserves the Devboxes workspace contract.
6. Any GPU taint has a matching profile toleration, and every profile selector matches at least one GPU node.

Inspect advertised resources before changing Helm values:

```bash
kubectl get nodes -o custom-columns='NAME:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu'
kubectl describe node GPU_NODE_NAME
kubectl get runtimeclass
```

For AMD or another vendor, replace the resource column with the exact resource advertised by that installation. Devboxes does not install cluster-wide drivers, device plugins, RuntimeClasses, or node labels because those are infrastructure responsibilities and usually require cluster-wide privileges.

## Configure profiles with Helm

Use a durable values file. This dedicated NVIDIA example assumes the cluster advertises `nvidia.com/gpu`, has an existing `nvidia` RuntimeClass, and labels L4 nodes with `accelerator=nvidia-l4`:

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

An AMD installation commonly advertises `amd.com/gpu`:

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

The AMD group IDs are illustrative. Use the numeric groups that own the relevant device nodes on your node image, commonly the local `video` or `render` groups. Intel and some AMD configurations need this for non-root device access. Omit the field when the runtime already grants the `dev` user access. Devboxes rejects group zero, duplicate IDs, and more than eight entries.

For every configured ID, Kubernetes adds the group to the pod security context and the Devboxes entrypoint adds the `dev` account to the matching image group, creating a deterministic `devbox-device-ID` group when needed. This second step is required because OpenSSH initializes the login user's groups from the image account database. A derived GPU image must preserve the Devboxes entrypoint for this behavior.

Profile names use 1 to 40 lowercase letters, digits, and hyphens. Resource names must be vendor-qualified. Counts are integers from 1 through 64. The chart supports at most 32 profiles and 16 selectors or tolerations per profile. Helm schema validation and controller startup validation both fail closed.

Apply the configuration and inspect the published catalog:

```bash
helm upgrade --install devboxes ./charts/devboxes \
  --namespace devboxes \
  --values values.yaml

devbox gpu profiles
devbox gpu profiles --json
```

The command above uses the chart in a source checkout. For a published release, use the OCI chart and pin the matching release with `--version` as described in [configuration](configuration.md).

## Build a compatible workspace image

The profile image is the complete workspace image, not an init container or library layer. Start from the Devboxes workspace image matching the controller and chart version, then install the minimum pinned user-space toolkit required by the workloads. Preserve:

- `/usr/local/bin/devbox-entrypoint` and the default entrypoint;
- the `dev` user and `/home/dev` persistent layout;
- OpenSSH, tmux, sudo, and the documented readiness behavior;
- support for the chart's runtime secret mount.

Do not bake credentials into the image. Do not install a host kernel driver in the workspace image. Host drivers belong on GPU nodes; the image contains compatible user-space libraries and application tooling.

When Insights is enabled, its sidecar continues to use the installation's release workspace image rather than the GPU profile override. This keeps the privacy sanitizer on the operator-pinned release artifact and prevents a specialized workload image from replacing it. Only the interactive `devbox` container uses `workspaceImage` and receives the accelerator.

Validate the derived image in a non-production profile before publishing it. At minimum, prove normal SSH readiness, persistent home reuse, and the vendor diagnostic expected for your stack, such as `nvidia-smi` or `rocminfo`.

## Use GPU boxes

Request the operator's default profile:

```bash
devbox create inference --gpu --preset medium --ssh
```

Request an exact profile:

```bash
devbox create training --gpu-profile nvidia-l4 --preset large --ssh
```

The dashboard accelerator selector provides the same choices. CPU only is always explicit and remains the initial selection.

Inspect the resolved allocation:

```bash
devbox list
devbox status inference
devbox status inference --json
```

The controller places the extended resource in both `requests` and `limits` on the main workspace container. Kubernetes therefore treats it as non-overcommittable capacity unless the installed vendor plugin intentionally publishes shared units. Sidecars do not receive the GPU resource.

## Dedicated, partitioned, and shared devices

Devboxes does not reinterpret vendor capacity. It requests the exact extended resource and count declared by the profile:

- A dedicated profile usually requests one `nvidia.com/gpu`, `amd.com/gpu`, or another vendor resource from an exclusively allocated device plugin.
- An NVIDIA MIG profile can request a resource such as `nvidia.com/mig-1g.10gb` when the installed plugin advertises it.
- A time-sliced or virtual-GPU profile can request the shared units advertised by the installed plugin.
- On newer clusters, a Dynamic Resource Allocation driver can participate when it exposes a compatible extended-resource bridge. The Devboxes user contract remains the profile name.

Name and describe shared profiles honestly, for example `nvidia-l4-shared`, so users do not infer exclusive hardware. The profile count means advertised resource units, not necessarily physical boards. Isolation, memory guarantees, and oversubscription behavior come from the vendor driver and plugin configuration.

## Lifecycle and configuration changes

At creation time, the controller resolves the selected profile and stores a bounded, pinned snapshot with the Deployment. That snapshot includes the resource, count, image override, RuntimeClass, supplemental groups, selectors, and tolerations.

- Stop and TTL expiry scale the same Deployment to zero, release live device capacity, and retain its GPU contract.
- Start restores the same pod template and renews the TTL. Kubernetes may assign a different physical device that satisfies the same profile.
- Enabling Insights on an existing box rebuilds from the stored snapshot rather than current Helm defaults.
- Editing, renaming, or removing a Helm profile affects only later creations.
- Delete and recreate a box to select a different profile. The retained home volume remains unless explicitly purged.

This prevents an operator configuration change from silently moving a stopped box to different hardware or dropping its GPU request.

## Scheduling and failure behavior

Creation is asynchronous. The controller validates policy and creates Kubernetes resources, while the scheduler remains the authority on live capacity. A valid request can remain `starting` when no matching device is free.

`devbox status` and the dashboard surface the scheduler's `PodScheduled=False` message. The CLI also includes the latest scheduling reason if its readiness wait expires. Common causes include insufficient extended resources, a selector that matches no node, an unhandled taint, or a missing RuntimeClass.

Do not work around a Pending box by removing the GPU limit, using privileged mode, or mounting host device paths manually. Correct the driver, advertised capacity, profile, or cluster capacity. See [troubleshooting](troubleshooting.md#a-gpu-box-remains-starting) for a bounded diagnosis sequence.

## Security model

A GPU device expands what trusted workspace code can exercise on its assigned node. The workspace user has passwordless sudo and can use every device granted to the container, so GPU profiles belong only in the existing trusted single-operator deployment model.

The GPU feature does not add privileged mode, host PID or network access, hostPath mounts, Kubernetes API credentials, or extra Linux capabilities. Clients cannot choose an image, resource name, RuntimeClass, supplemental group, selector, toleration, or device count. Only an operator changing Helm values can modify those fields.

Treat every profile image and vendor runtime as part of the trusted software supply chain. Pin image tags or digests in durable values, scan derived images, track driver and toolkit compatibility, and roll changes through a test profile before making them the default.

## Disable or retire a profile

Set `gpu.enabled=false` to reject new GPU requests and hide the catalog. Existing GPU Deployments retain their stored specification and can still start. This fail-safe avoids destroying or mutating operator data during a configuration rollback.

To retire one profile:

1. Change the default to another configured profile.
2. Remove the retired profile from Helm values.
3. Identify existing allocations with `devbox list --json`.
4. Ask owners to delete and recreate those boxes when migration is appropriate.

Removing a profile does not reclaim a running device or rewrite existing Deployments.

## Further reading

- [Kubernetes scheduling GPUs](https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/)
- [Kubernetes device plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/)
- [NVIDIA Kubernetes device plugin](https://github.com/NVIDIA/k8s-device-plugin)
- [AMD GPU device plugin](https://instinct.docs.amd.com/projects/k8s-device-plugin/en/latest/)
