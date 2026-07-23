# Custom image profiles

Devboxes can run an operator-approved container image with a new devbox. This makes it practical to test a prebuilt open-source service without Docker-in-Docker, while keeping the prepared SSH workspace, persistent home volume, and Kubernetes policy intact.

The feature is disabled by default. An operator defines a small catalog through Helm, and users select a profile through the CLI or browser workbench. The controller never accepts an arbitrary image reference that is not already in that catalog.

## Why profiles are the API

An ordinary application image is not a complete Devboxes workspace. For example, an NGINX image does not contain the Devboxes entrypoint, the `dev` SSH user, persistent-home initialization, or the required secret bootstrap. Replacing the interactive container with it would make SSH readiness fail.

Image profiles make this distinction explicit:

- `sidecar`, the default, runs an application image that is compatible with the non-root sidecar contract beside the prepared Devboxes workspace. This is the right mode for NGINX, databases, emulators, documentation servers, and similar prebuilt services.
- `workspace` replaces the interactive Devboxes image. Use it only for a tested derivative of the matching Devboxes workspace image that preserves the complete SSH and lifecycle contract.

The catalog is returned by the authenticated API, printed by `devbox image profiles`, and rendered in the dashboard. It publishes safe user-facing names, descriptions, modes, and pod-local ports. It does not publish the underlying image reference, pull policy, or resource limits.

## Configure approved images

Set `workspace.customImages.enabled=true` and define one or more profiles in the Helm values file:

```yaml
workspace:
  customImages:
    enabled: true
    profiles:
      - name: nginx
        displayName: NGINX preview
        description: Serve a local static-site preview over the pod network
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
            protocol: TCP
```

Apply the values with the normal Helm upgrade:

```bash
helm upgrade --install devboxes oci://ghcr.io/vicotrbb/charts/devboxes \
  --version VERSION \
  --namespace devboxes \
  --values values.yaml
```

| Value | Meaning |
| --- | --- |
| `workspace.customImages.enabled` | Accept custom image requests and publish the catalog. Defaults to `false`. |
| `workspace.customImages.profiles[].name` | Stable lowercase profile identifier used by clients. |
| `workspace.customImages.profiles[].displayName` | Human-readable label in CLI and dashboard. |
| `workspace.customImages.profiles[].description` | Short task-oriented description. |
| `workspace.customImages.profiles[].image` | Approved container image reference. Pin a tag or digest in durable values. |
| `workspace.customImages.profiles[].mode` | `sidecar` or `workspace`. Omit for the safer `sidecar` default. |
| `workspace.customImages.profiles[].pullPolicy` | `Always`, `IfNotPresent`, or `Never`. Omit for `IfNotPresent`. |
| `workspace.customImages.profiles[].resources` | CPU and memory requests and limits for a sidecar profile. Workspace profiles reject this sidecar-only field. |
| `workspace.customImages.profiles[].ports` | Optional named, pod-local application ports from 1024 through 65535 for status and documentation. |

The chart rejects unknown fields, duplicate profile names, duplicate image references, blank labels, URL-scheme image strings, privileged ports, and enabled configurations without profiles. The controller also validates resource quantities and limits when it starts.

## Use a service image

Discover the catalog first. The profile name is the recommended stable selector:

```bash
devbox image profiles
devbox create docs-preview --image nginx --ssh
```

For direct parity with an existing container reference, the CLI also accepts an exact image reference that matches a configured profile:

```bash
devbox create docs-preview --image docker.io/nginxinc/nginx-unprivileged:1.27.5-alpine --ssh
```

The controller resolves either form to the same profile before it creates a PVC, Deployment, or SSH Service. An unapproved reference fails with `422 Unprocessable Content` and lists only the available profile names.

The image runs as a `custom-image` sidecar in the same pod network namespace as the prepared workspace. It must declare a non-root image user and listen on a port from 1024 through 65535; Kubernetes sets `runAsNonRoot: true`, disables privilege escalation, and drops every Linux capability. It does not receive the Devboxes Secret, a persistent-volume mount, a Kubernetes API token, or a public Kubernetes Service. Reach a declared port from the workspace or tunnel it through SSH:

```bash
devbox ssh docs-preview -- -L 8080:127.0.0.1:8080
# Open http://127.0.0.1:8080 in the local browser.
```

The dashboard exposes the same approved profiles in the create form. Choosing a profile explains whether it is a service sidecar or replacement workspace, and shows any declared pod-local ports. Existing devbox rows and `devbox status` report the resolved profile and ports.

## Use a complete workspace image

Use `mode: workspace` only after proving that the image is a compatible derivative. It replaces the primary interactive container, so it must retain all of these properties:

1. The Devboxes entrypoint and SSH daemon listening on port `2222`.
2. The `dev` user, persistent `/home/dev` layout, and tmux startup flow.
3. Runtime handling for the mounted workspace Secret and optional repository clone.
4. Compatibility with the selected Kubernetes security context and readiness probe.

Start from the matching released Devboxes workspace image, add only the required tooling, then test SSH readiness, retained-home reuse, stop and start, and Insights reconciliation before publishing the profile. Workspace profiles keep the selected Devboxes preset for compute and must not set the sidecar-only `resources` field. Do not declare a generic application image such as NGINX as a `workspace` profile.

A workspace profile can combine with a GPU profile only when that GPU profile does not already select its own `workspaceImage`. This prevents two independent policies from silently competing for the interactive container image. Sidecar profiles can combine with GPU profiles because they do not change the main workspace image or receive GPU resources.

## Security and operations

Custom image profiles are appropriate only for Devboxes' existing trusted single-operator model. The operator chooses and reviews every image, resource envelope, pull policy, and port declaration. Users can select a profile but cannot inject a registry, command, Service, volume, host path, capability, resource request, or Kubernetes scheduling field.

Treat every catalog image as part of the trusted software supply chain:

1. Prefer a digest or a pinned, regularly reviewed tag over `latest`.
2. Scan and provenance-verify the image according to the registry and organization policy.
3. Test image pulls from every eligible node and keep required pull credentials valid.
4. Use bounded CPU and memory values for sidecars, then inspect scheduler and runtime behavior under the intended preset.
5. Keep application ports pod-local unless a separately reviewed exposure path is required.
6. Choose images that already run unprivileged and use high ports. The tested NGINX example is `nginxinc/nginx-unprivileged`; the standard root-oriented NGINX image is intentionally incompatible with this policy.

The fully resolved profile is stored as a Deployment annotation when the devbox is created. Stop, start, TTL expiry, and Insights template reconciliation retain that pinned contract even if Helm values later change. Disabling the feature rejects new requests and hides the catalog, but existing boxes keep their stored sidecar or workspace selection. Delete and recreate a devbox to choose a newer profile while retaining its home volume.

If an approved image cannot pull or its sidecar crashes, Devboxes reports the Kubernetes image or restart failure through `devbox status` and the dashboard. Check the image reference, pull Secret, node network path, image architecture, and application logs before changing the catalog.
