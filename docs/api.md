# API reference

The controller exposes a small JSON API under `/api/v1`. The Rust CLI is the reference client. The browser uses the same lifecycle endpoints through a signed session and CSRF protection.

## Authentication

CLI and automation requests use the shared controller token:

```http
Authorization: Bearer CONTROLLER_ACCESS_TOKEN
Accept: application/json
```

Browser login exchanges the token for an HTTP-only, SameSite `devboxes_session` cookie and a readable `devboxes_csrf` cookie. Every browser mutation sends the CSRF value in `X-Devboxes-CSRF`. API clients using bearer authentication do not need a CSRF header.

The shared token controls every devbox in the installation, including permanent purge. Do not expose it in URLs, shell history, logs, or source control.

## Endpoints

| Method | Path | Success | Purpose |
| --- | --- | ---: | --- |
| `GET` | `/health` | 200 | Process liveness |
| `GET` | `/ready` | 200 or 503 | Kubernetes API readiness |
| `GET` | `/metrics` | 200 | Prometheus metrics |
| `GET` | `/api/v1/whoami` | 200 | Verify authentication and identity |
| `GET` | `/api/v1/devboxes` | 200 | List managed devboxes |
| `POST` | `/api/v1/devboxes` | 201 | Create a devbox |
| `GET` | `/api/v1/devboxes/{name}` | 200 | Read one devbox |
| `POST` | `/api/v1/devboxes/{name}/start` | 200 | Start compute and renew TTL |
| `POST` | `/api/v1/devboxes/{name}/stop` | 200 | Stop compute and retain storage |
| `DELETE` | `/api/v1/devboxes/{name}` | 200 | Delete compute, optionally purge storage |

The list endpoint is not paginated. One installation is intended for a small, trusted operator scope.

## Create a devbox

```bash
curl --fail-with-body \
  --request POST \
  --header "Authorization: Bearer $DEVBOX_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{
    "name": "atlas",
    "preset": "medium",
    "ttl_hours": 24,
    "repository": "owner/project"
  }' \
  https://devboxes.example.com/api/v1/devboxes
```

Request fields:

| Field | Type | Rules |
| --- | --- | --- |
| `name` | string | Required, 1 to 40 lowercase letters, digits, or hyphens, alphanumeric edges |
| `preset` | string | `small`, `medium`, or `large`, default `small` |
| `ttl_hours` | integer | 1 to the configured maximum, default 24 |
| `repository` | string or null | `owner/repository` or an HTTPS GitHub repository URL |

Unknown fields are rejected. Creating an existing name returns `409 Conflict`. Recreating a deleted name reuses its retained PVC, and expands it when the new preset requests more storage.

## Devbox response

```json
{
  "name": "atlas",
  "state": "ready",
  "preset": "medium",
  "created_at": "2026-07-13T12:00:00Z",
  "expires_at": "2026-07-14T12:00:00Z",
  "repository": "owner/project",
  "ssh_host": "192.0.2.40",
  "ssh_port": 22,
  "ssh_command": "ssh -t dev@192.0.2.40",
  "pod_name": "devbox-atlas-7d9c9f7b7d-example",
  "pod_ready": true,
  "restarts": 0,
  "storage_size": "30Gi",
  "message": null
}
```

States are:

- `starting`, the pod or SSH address is not ready.
- `ready`, the pod is Ready and an SSH endpoint is available.
- `stopped`, the Deployment has zero replicas and the home volume remains.
- `degraded`, the pod failed or a known image or restart failure is visible.

Timestamps are RFC 3339 values. `ssh_host`, `ssh_command`, `pod_name`, and `message` can be null while resources converge.

## Lifecycle semantics

Start returns the updated devbox and renews the TTL stored when the box was created. Stop scales the Deployment to zero and does not delete the PVC. Delete without a query removes compute and SSH resources while preserving the PVC.

Purge is explicit:

```bash
curl --fail-with-body \
  --request DELETE \
  --header "Authorization: Bearer $DEVBOX_TOKEN" \
  'https://devboxes.example.com/api/v1/devboxes/atlas?purge=true'
```

The response identifies the name, whether storage was purged, and a human-readable message.

## Errors

Errors use FastAPI's JSON detail field:

```json
{"detail":"Authentication required"}
```

Validation failures contain a list of structured errors. Common status codes are:

| Status | Meaning |
| ---: | --- |
| 401 | Missing or invalid bearer token or browser session |
| 403 | Missing or invalid browser CSRF token |
| 404 | Devbox does not exist |
| 409 | Devbox name already exists |
| 422 | Invalid path, request field, repository, preset, or TTL |
| 503 | Controller cannot reach the Kubernetes API through `/ready` |

Clients should preserve the status code, treat error payload text as diagnostic rather than stable machine data, and retry only transient transport or readiness failures.

## Compatibility

The API is versioned in the path. Within a release line, additive response fields are compatible. Before `v1.0`, minor releases may contain documented contract changes. Keep the CLI, controller images, and chart on the same released version.
