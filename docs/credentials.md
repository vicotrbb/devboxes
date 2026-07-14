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

An installation may add a dedicated signing key to the same Secret and set
`controller.cliSigningKeyKey` to that field. This separates CLI-token rotation from browser
session rotation without adding another Secret. The key is optional and must contain at
least 32 strong random characters. Rotating whichever effective signing key is configured
revokes all issued CLI tokens.

## Insights ingest credentials

When Insights is enabled, the controller creates one namespaced Secret per workspace. It contains a write-only, HMAC-signed credential scoped to that box name and UUID instance. The workspace sidecar can submit sanitized batches but cannot read Insights, control devboxes, or mint another credential.

By default, the ingest signing key is derived from the controller access token with its own versioned domain separation. To rotate it independently, add a strong random key of at least 32 characters to the controller Secret and point `insights.signingKeyKey` at that field. Never put the value in Helm values.

The generated per-workspace Secret is deleted with workspace compute. Its value is referenced through `secretKeyRef`, not embedded in the Deployment. Retaining the home PVC preserves the workspace instance ID and outbox; a normal recreate issues a fresh credential for that same instance.

## Workspace Secret

Create the minimal Secret with an SSH public key:

```bash
kubectl -n devboxes create secret generic devboxes-workspace \
  --from-file=SSH_AUTHORIZED_KEYS="$HOME/.ssh/id_ed25519.pub"
```

Multiple public keys are accepted in the same authorized-keys file. Keep private SSH keys on clients; Devboxes does not need them.

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

Point the chart at any existing Secret name. A secret operator only needs to produce the expected keys in the Devboxes namespace. Recommended controls include encryption at rest, short-lived tokens where supported, audit logging, least-privilege provider scopes, and rotation procedures tested before an incident.

Never commit rendered Secrets, shell histories containing tokens, account JSON, or private keys to this repository.
