#!/bin/sh
set -eu

namespace="${DEVBOXES_NAMESPACE:-devboxes}"
controller_secret="${DEVBOXES_CONTROLLER_SECRET:-devboxes-auth}"
workspace_secret="${DEVBOXES_WORKSPACE_SECRET:-devboxes-workspace}"
public_key="${DEVBOXES_SSH_PUBLIC_KEY:-$HOME/.ssh/id_ed25519.pub}"

if ! command -v kubectl >/dev/null 2>&1; then
  printf 'error: kubectl is required\n' >&2
  exit 1
fi
temporary_directory="$(mktemp -d)"
trap 'rm -rf "$temporary_directory"' EXIT HUP INT TERM
umask 077

kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

decode_base64() {
  if printf 'dGVzdA==' | base64 --decode >/dev/null 2>&1; then
    base64 --decode
  else
    base64 -D
  fi
}

copy_existing_secret_key() {
  key="$1"
  destination="$2"
  if ! kubectl -n "$namespace" get secret "$workspace_secret" >/dev/null 2>&1; then
    return
  fi
  encoded_value="$(
    kubectl -n "$namespace" get secret "$workspace_secret" \
      -o "go-template={{with index .data \"$key\"}}{{.}}{{end}}"
  )"
  if [ -n "$encoded_value" ]; then
    printf '%s' "$encoded_value" | decode_base64 > "$destination"
  fi
}

token="${DEVBOXES_ACCESS_TOKEN:-}"
if [ -z "$token" ] && kubectl -n "$namespace" get secret "$controller_secret" >/dev/null 2>&1; then
  token="$(kubectl -n "$namespace" get secret "$controller_secret" -o jsonpath='{.data.access-token}' | decode_base64)"
fi
if [ -z "$token" ]; then
  if command -v openssl >/dev/null 2>&1; then
    token="$(openssl rand -hex 32)"
  else
    printf 'error: set DEVBOXES_ACCESS_TOKEN or install openssl to generate one\n' >&2
    exit 1
  fi
fi
if [ "${#token}" -lt 32 ]; then
  printf 'error: DEVBOXES_ACCESS_TOKEN must contain at least 32 characters\n' >&2
  exit 1
fi
printf '%s' "$token" > "$temporary_directory/access-token"

workspace_directory="$temporary_directory/workspace"
mkdir "$workspace_directory"
if [ -s "$public_key" ]; then
  cp "$public_key" "$workspace_directory/SSH_AUTHORIZED_KEYS"
else
  copy_existing_secret_key SSH_AUTHORIZED_KEYS "$workspace_directory/SSH_AUTHORIZED_KEYS"
fi
if [ ! -s "$workspace_directory/SSH_AUTHORIZED_KEYS" ]; then
  printf 'error: SSH public key not found at %s and no existing key can be preserved\n' "$public_key" >&2
  printf 'Set DEVBOXES_SSH_PUBLIC_KEY to an existing public key.\n' >&2
  exit 1
fi

write_optional_secret() {
  variable_name="$1"
  file_name="$2"
  variable_value="$(printenv "$variable_name" 2>/dev/null || true)"
  if [ -n "$variable_value" ]; then
    printf '%s' "$variable_value" > "$workspace_directory/$file_name"
  else
    copy_existing_secret_key "$file_name" "$workspace_directory/$file_name"
  fi
}

write_optional_file() {
  variable_name="$1"
  file_name="$2"
  source_file="$(printenv "$variable_name" 2>/dev/null || true)"
  if [ -n "$source_file" ]; then
    if [ ! -s "$source_file" ]; then
      printf 'error: %s does not reference a readable non-empty file\n' "$variable_name" >&2
      exit 1
    fi
    cp "$source_file" "$workspace_directory/$file_name"
  else
    copy_existing_secret_key "$file_name" "$workspace_directory/$file_name"
  fi
}

write_optional_secret GH_TOKEN GH_TOKEN
write_optional_secret GIT_USER_NAME GIT_USER_NAME
write_optional_secret GIT_USER_EMAIL GIT_USER_EMAIL
write_optional_secret OPENAI_API_KEY OPENAI_API_KEY
write_optional_secret CODEX_ACCESS_TOKEN CODEX_ACCESS_TOKEN
write_optional_secret CLAUDE_CODE_OAUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN
write_optional_secret ANTHROPIC_API_KEY ANTHROPIC_API_KEY

write_optional_file CODEX_AUTH_JSON_FILE CODEX_AUTH_JSON
write_optional_file CLAUDE_CREDENTIALS_JSON_FILE CLAUDE_CREDENTIALS_JSON

kubectl -n "$namespace" create secret generic "$controller_secret" \
  --from-file=access-token="$temporary_directory/access-token" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n "$namespace" create secret generic "$workspace_secret" \
  --from-file="$workspace_directory" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

printf 'Created or updated %s and %s in namespace %s.\n' \
  "$controller_secret" "$workspace_secret" "$namespace"
printf 'Retrieve the controller token when needed with:\n'
printf '  kubectl -n %s get secret %s -o go-template='"'"'{{index .data "access-token" | base64decode}}{{"\\n"}}'"'"'\n' \
  "$namespace" "$controller_secret"
