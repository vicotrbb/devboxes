# API reference

The controller exposes a small JSON API under `/api/v1`. The Rust CLI is the reference client. The browser uses the same lifecycle endpoints through a signed session and CSRF protection.

## Authentication

Automation requests may use the master controller token. Browser-authorized CLI requests
use a scoped, expiring token with the same bearer header:

```http
Authorization: Bearer CONTROLLER_ACCESS_TOKEN
Accept: application/json
```

Browser login exchanges the master token for an HTTP-only, SameSite `devboxes_session`
cookie and a readable `devboxes_csrf` cookie. Every browser mutation sends the CSRF value
in `X-Devboxes-CSRF`. API clients using bearer authentication do not need a CSRF header.

The shared token controls every devbox in the installation, including permanent purge. Do not expose it in URLs, shell history, logs, or source control.

## Endpoints

| Method | Path | Success | Purpose |
| --- | --- | ---: | --- |
| `GET` | `/health` | 200 | Process liveness |
| `GET` | `/ready` | 200 or 503 | Kubernetes API readiness |
| `GET` | `/metrics` | 200 | Prometheus metrics |
| `GET` | `/auth/cli/authorize` | 200 or 303 | Show approval or return through browser login |
| `POST` | `/auth/cli/authorize` | 303 | Approve or deny a CSRF-protected CLI request |
| `POST` | `/api/v1/auth/cli/token` | 200 | Exchange a one-time code and PKCE verifier |
| `GET` | `/api/v1/whoami` | 200 | Verify authentication and identity |
| `GET` | `/api/v1/devboxes` | 200 | List managed devboxes |
| `POST` | `/api/v1/devboxes` | 201 | Create a devbox |
| `GET` | `/api/v1/devboxes/{name}` | 200 | Read one devbox |
| `POST` | `/api/v1/devboxes/{name}/start` | 200 | Start compute and renew TTL |
| `POST` | `/api/v1/devboxes/{name}/stop` | 200 | Stop compute and retain storage |
| `DELETE` | `/api/v1/devboxes/{name}` | 200 | Delete compute, optionally purge storage |
| `GET` | `/api/v1/insights/summary` | 200 | Read filtered AI and Git aggregates |
| `GET` | `/api/v1/insights/timeseries` | 200 | Read a supported metric in hourly or daily buckets |
| `GET` | `/api/v1/insights/activity` | 200 | Read cursor-paginated aggregate commit activity |
| `GET` | `/api/v1/insights/capabilities` | 200 | Read collector freshness, queue state, loss, and metric availability |
| `GET` | `/api/v1/insights/export` | 200 | Export a filtered JSON or CSV summary, or an online SQLite backup |
| `DELETE` | `/api/v1/insights` | 200 | Explicitly purge history by box or instance ID |

The list endpoint is not paginated. One installation is intended for a small, trusted operator scope.

The CLI authorization route accepts only `client_id=devbox-cli`, an exact numeric HTTP
loopback redirect ending in `/callback`, a high-entropy state, a PKCE challenge, and
`code_challenge_method=S256`. Approval binds the opaque code to all of those values and the
browser subject for about two minutes. The token endpoint accepts JSON fields
`grant_type`, `code`, `code_verifier`, `client_id`, and `redirect_uri`. Errors are generic,
responses are `no-store`, codes are single-use, and no refresh token is returned.

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

## Insights responses

Insights is disabled by default. Authenticated reads return an envelope with `enabled`, `generated_at`, `effective_range`, `filters`, `coverage`, `capabilities`, `storage`, and nullable `data`. Missing or unsupported provider measurements remain null with a capability reason. A zero means the collector reported or derived zero within the selected range.

Read endpoints accept `since`, `until`, `box` or `devbox`, `instance_id`, `provider`, `model`, and `repo` or `repository` where applicable. Summary and capabilities accept `group_by=provider|model|box|repository`. Timeseries requires a supported `metric` and accepts `bucket=hour|day`. Activity accepts an opaque `cursor` and a `limit` from 1 to 200.

Export accepts `format=json|csv|sqlite`. JSON and CSV honor the filters. SQLite returns a consistent online backup of the complete database and intentionally ignores summary filters. Never copy only the live database file while write-ahead logging is active.

Purge requires exactly one selector:

```bash
curl --fail-with-body \
  --request DELETE \
  --header "Authorization: Bearer $DEVBOX_TOKEN" \
  'https://devboxes.example.com/api/v1/insights?box=atlas'
```

The hidden workspace ingest endpoint is not a public operator API. It accepts only the per-instance scoped credential, OTLP HTTP JSON metrics inside the bounded Devboxes batch envelope, and optional gzip compression. Browser and CLI bearer tokens are rejected there.

See [Insights](insights.md) for exact metric semantics, privacy constraints, retention, identity, rollout, and backup procedures.

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
