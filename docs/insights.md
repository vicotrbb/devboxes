# Insights

Insights is an opt-in, single-operator view of locally emitted AI metrics and aggregate Git activity. It is available at `/insights`, through `/api/v1/insights/*`, and through `devbox metrics`. It does not reuse the Prometheus `/metrics` route.

## Enable Insights

Insights is disabled by default. Enable it during install or upgrade:

```yaml
insights:
  enabled: true
  signingKeyKey: ""
  storage:
    existingClaim: ""
    storageClass: ""
    size: 2Gi
    warningBytes: 1717986918
    accessMode: ReadWriteOnce
    retainOnDelete: true
  retention:
    rawDays: 30
    hourlyDays: 90
    dailyDays: 365
  agent:
    scanIntervalSeconds: 60
    repositoryDepth: 4
    maxQueueBytes: 134217728
    maxQueueAgeSeconds: 604800
```

The controller must use one replica while Insights is enabled. The chart enforces this and changes the controller Deployment strategy to `Recreate`. The Insights database needs a block or local filesystem that supports SQLite write-ahead logging. Do not place it on NFS or another network filesystem with unreliable shared locking. `ReadWriteOncePod` is available when the cluster and storage driver support it.

## Data path

Each enabled workspace gets an `insights-agent` sidecar from the same workspace image. Codex and Claude Code send metrics to `127.0.0.1:4318` inside the pod network namespace. The agent accepts only OTLP HTTP JSON metrics at `/v1/metrics`, sanitizes them, and commits them to a bounded SQLite outbox on `/home/dev` before acknowledging the client.

The sidecar forwards gzip-compressed batches to the hidden controller ingest route with a write-only HMAC credential scoped to one UUID workspace instance. The credential is stored in a namespaced Kubernetes Secret and never uses the controller or CLI bearer token. The controller sanitizes the batch again, derives the authoritative instance from the credential, deduplicates the batch and point fingerprints, and commits it to the central SQLite database.

Agent failure is fail-open for SSH. The main workspace readiness probe checks only SSH, so an unavailable collector cannot make the development environment unreachable.

## Metric meanings

| Measurement | Codex 0.144.0 | Claude Code 2.1.205 | Meaning |
| --- | --- | --- | --- |
| Sessions | Approximate | Reported | Codex counts local process starts. Claude reports its session counter. |
| Tokens | Reported | Reported | Input, output, cache, and total categories emitted by the local client. |
| Cost | Not reported | Provider-reported estimate | Claude local telemetry is an estimate and is not a billing record. Devboxes never estimates Codex cost. |
| Active time | Not reported | Reported | Claude active-time categories only. It is not employee time tracking. |
| AI-modified lines | Not reported | Reported | Claude edit telemetry only. It is separate from Git churn. |
| Git commits | Collected independently | Collected independently | All newly observed reachable commits, regardless of AI usage. |
| Git churn | Collected independently | Collected independently | Committed text additions and deletions from Git numstat. |
| Working tree | Collected independently | Collected independently | Latest staged and unstaged tracked changes. |

All safe metric names and supported OTLP point forms are retained, including unknown future metrics. The dashboard exposes the currently defined summaries and capability reasons. Unsupported and absent measurements remain `null` with a reason instead of becoming zero.

Insights never computes a productivity score or an AI-written percentage. Claude line counts are not added to Git churn, and AI-attributed commits are not added to a separate commit total.

## Git collection

The agent polls `/home/dev/workspace` every 60 seconds by default and discovers repositories or linked worktrees to the configured depth. The first scan stores ref tips as a baseline and imports no cloned history. Later scans find commits newly reachable from current refs and deduplicate each repository and SHA.

Merge commits count once, with zero additional churn so merged branch changes are not counted twice. Binary files increment the binary and file counts but have no invented line count. The working-tree snapshot separates staged and unstaged tracked changes.

Polling can miss commits that are created and rewritten or deleted entirely between scans. A rebase can make replacement commits newly reachable and therefore observable. The collector does not install hooks, modify `core.hooksPath`, or store author data, messages, paths, patches, file content, commands, or credential-bearing remotes.

GitHub remotes become a credential-free `github.com/owner/repository` key. Other remotes and local-only repositories use a stable non-sensitive hash.

## Privacy and trust boundary

Both agent and controller use an attribute allowlist for provider, model, auth mode, session source, app version, token type, tool category, success, status, start type, edit decision, and active-time category. Resource attributes are limited to service name and version.

The pipeline drops email addresses, user, account, and organization identifiers, session IDs, prompts, responses, lengths, commands, paths, file names, URLs, tool inputs and outputs, error bodies, raw API bodies, logs, traces, and arbitrary attributes.

Devboxes remains a single-operator system without secure per-user attribution or tenant isolation. Metrics are self-reported by software running in a workspace controlled by the devbox user, so that user can disable, alter, or fabricate them. Use Insights for personal operational visibility, not compliance, billing, performance evaluation, or adversarial auditing.

## Identity, rollout, and key rotation

The controller stores a UUID instance ID on both the workspace Deployment and home PVC. Deleting a workspace while retaining its PVC preserves the instance ID. Purging the PVC and recreating the workspace creates a new ID.

An upgrade does not restart active legacy workspaces. They report `restart_required` until a normal stop and start installs the sidecar. Stopped templates are reconciled immediately, and every start reconciles a stale template before scaling it up.

Set `insights.signingKeyKey` to a key in `controller.existingSecret` to use a dedicated ingest signing key. Without it, the controller derives a domain-separated key from the access token. Rotating either effective key invalidates existing ingest credentials. Active workspaces become restart-required, while stopped workspaces receive the new credential immediately.

## Storage, retention, and loss

The workspace outbox defaults to 128 MiB and seven days. It retries with exponential backoff and jitter. When its byte or age bound forces deletion, it removes the oldest batch and reports persistent dropped-batch and dropped-point counters. Collector status then becomes `data_loss_detected`, and coverage is partial.

The central database uses SQLite WAL, `synchronous=FULL`, foreign keys, a five-second busy timeout, serialized writes, and short-lived read connections. Raw metric points default to 30 days, hourly rollups to 90 days, and daily rollups plus sparse Git events to 365 days. The dashboard and API expose database bytes and a configurable warning threshold.

## Backup and restore

Request a consistent SQLite snapshot through the authenticated online-backup export:

```bash
curl -fsS \
  -H "Authorization: Bearer $DEVBOX_TOKEN" \
  -o devboxes-insights.db \
  "$DEVBOX_URL/api/v1/insights/export?format=sqlite"
```

This uses the [SQLite online backup API](https://www.sqlite.org/backup.html). Never copy only a live `.db` file while WAL mode is active.

To restore, disable or scale down the controller, replace the database on the Insights PVC with a verified snapshot using an administrative pod, preserve ownership for controller uid and gid 10001, and start the controller. Check `/ready`, the Insights page, and `devbox metrics status` before removing the previous snapshot.

## Disable or purge

Setting `insights.enabled=false` stops central collection and removes the controller data mount from the Deployment. Retained controller and workspace PVC data is not deleted automatically.

Ordinary `devbox delete`, including `--purge`, retains central Insights history. Delete history explicitly:

```bash
devbox metrics purge --box atlas
```

Use `--yes` only in automation. The equivalent authenticated API is `DELETE /api/v1/insights?box=atlas`, or use `instance_id` to remove one retained instance.

## Protocol references

- [Codex advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced)
- [Codex configuration basics](https://learn.chatgpt.com/docs/config-file/config-basic)
- [Claude Code monitoring](https://code.claude.com/docs/en/monitoring-usage)
- [OTLP specification](https://opentelemetry.io/docs/specs/otlp/)
- [OpenTelemetry metrics data model](https://opentelemetry.io/docs/specs/otel/metrics/data-model/)
- [SQLite WAL](https://www.sqlite.org/wal.html) and [PRAGMA reference](https://www.sqlite.org/pragma.html)
- [Kubernetes persistent volumes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)
- [Git diff formats](https://git-scm.com/docs/diff-format) and [Git hooks](https://git-scm.com/docs/githooks)
