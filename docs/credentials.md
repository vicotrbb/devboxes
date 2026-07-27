# Credentials

Devboxes separates controller access from workspace credentials. Neither Secret is created by the Helm chart, which avoids placing plaintext credentials in Helm release history.

## Controller access token

Generate at least 256 bits of entropy and store it in the key configured by `controller.accessTokenKey`:

```bash
kubectl -n devboxes create secret generic devboxes-auth \
  --from-literal=access-token="$(openssl rand -hex 32)"
```

By default, rotating this Secret invalidates saved CLI tokens after the controller pod
restarts. Existing browser sessions also become invalid because session signatures derive
from the access token.

The controller token is always the installation-wide `operator` credential. When
multi-tenancy is enabled, reserve it for administration and break-glass recovery rather than
distributing it to tenant users.

An installation may add a dedicated signing key to the same Secret and set
`controller.cliSigningKeyKey` to that field. This separates CLI-token rotation from browser
session rotation without adding another Secret. The key is optional and must contain at
least 32 strong random characters. Rotating whichever effective signing key is configured
revokes all issued CLI tokens.

## Tenant principal tokens

Multi-tenant identities use independent strong tokens from
`tenancy.existingPrincipalSecret` in the controller namespace. Helm values contain only the
Secret name and each principal's `tokenKey`:

```bash
alice_token="$(openssl rand -hex 32)"
bob_token="$(openssl rand -hex 32)"
kubectl -n devboxes create secret generic devboxes-principals \
  --from-literal=alice-token="$alice_token" \
  --from-literal=bob-token="$bob_token"
unset alice_token bob_token
```

Each value must contain at least 32 characters, be unique across principals, and differ from
the master token. The controller mounts the Secret read-only with group-readable mode `0440`
for its dedicated uid/gid and reads tokens at startup. It stores only a short credential
epoch in browser sessions and CLI tokens, so rotating a principal value and restarting the
controller invalidates that principal's existing sessions and CLI credentials without
rotating other identities.

Principal subjects, display names, membership roles, and Secret key references are
non-secret Helm policy. Never place the token value itself in a values file. Update the
Secret, policy, or both and restart the controller to apply a rotation or membership change.

## Insights ingest credentials

When Insights is enabled, the controller creates one namespaced Secret per workspace. It contains a write-only, HMAC-signed credential scoped to that box name and UUID instance. The workspace sidecar can submit sanitized batches but cannot read Insights, control devboxes, or mint another credential.

By default, the ingest signing key is derived from the controller access token with its own versioned domain separation. To rotate it independently, add a strong random key of at least 32 characters to the controller Secret and point `insights.signingKeyKey` at that field. Never put the value in Helm values.

The generated per-workspace Secret is deleted with workspace compute. Its value is referenced through `secretKeyRef`, not embedded in the Deployment. Retaining the home PVC preserves the workspace instance ID and outbox; a normal recreate issues a fresh credential for that same instance.

## Workspace Secrets

Create the minimal Secret with an SSH public key:

```bash
kubectl -n devboxes create secret generic devboxes-workspace \
  --from-file=SSH_AUTHORIZED_KEYS="$HOME/.ssh/id_ed25519.pub"
```

Multiple public keys are accepted in the same authorized-keys file. Keep private SSH keys on clients; Devboxes does not need them.

With tenancy enabled, create a separate Secret in every tenant namespace and reference it
with `tenancy.tenants[].workspaceSecretName`:

```bash
kubectl -n devboxes-platform create secret generic devboxes-platform-workspace \
  --from-file=SSH_AUTHORIZED_KEYS="$HOME/.ssh/platform_authorized_keys"
kubectl -n devboxes-research create secret generic devboxes-research-workspace \
  --from-file=SSH_AUTHORIZED_KEYS="$HOME/.ssh/research_authorized_keys"
```

Do not reuse a credential-bearing workspace Secret across tenants. Namespace scoping then
prevents one tenant's pod specification from referencing another tenant's Secret.

### GitHub and Git identity

```bash
GH_TOKEN="$(gh auth token)" \
GIT_USER_NAME="Your Name" \
GIT_USER_EMAIL="you@example.com" \
./scripts/bootstrap-secrets.sh
```

Public repository cloning works without `GH_TOKEN`. A token enables private repository cloning and authenticated pushes. Use a fine-grained token with access limited to the repositories and operations the workspace needs.

### Codex

Preferred automation options are `OPENAI_API_KEY` or a supported Codex access token:

```bash
OPENAI_API_KEY="$OPENAI_API_KEY" ./scripts/bootstrap-secrets.sh
```

You may seed an existing account file on a new home volume:

```bash
CODEX_AUTH_JSON_FILE="$HOME/.codex/auth.json" ./scripts/bootstrap-secrets.sh
```

The file is copied only when `~/.codex/auth.json` does not already exist. Later login refreshes stay on the persistent volume.

### Claude Code

Use `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` when available, or seed an existing credential file:

```bash
CLAUDE_CREDENTIALS_JSON_FILE="$HOME/.claude/.credentials.json" \
  ./scripts/bootstrap-secrets.sh
```

Credential formats and provider authentication policies can change. Confirm current provider documentation and account terms before copying session files. If a seeded session expires, authenticate interactively inside the devbox; the refreshed state persists under `/home/dev`.

## External secret managers

Point the chart at existing Secret names. A secret operator must produce controller and
principal Secrets in the release namespace and workspace Secrets in their tenant namespaces.
Recommended controls include encryption at rest, short-lived tokens where supported, audit
logging, least-privilege provider scopes, and rotation procedures tested before an
incident.

Never commit rendered Secrets, shell histories containing tokens, account JSON, or private keys to this repository.
