#!/bin/sh
set -eu

namespace="${DEVBOXES_NAMESPACE:-devboxes}"
release="${DEVBOXES_RELEASE:-devboxes}"
service="${DEVBOXES_SERVICE:-$release}"
local_port="${DEVBOXES_VERIFY_PORT:-18000}"
token_secret="${DEVBOXES_CONTROLLER_SECRET:-devboxes-auth}"

for command in kubectl helm curl; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'error: %s is required\n' "$command" >&2
    exit 1
  fi
done

decode_base64() {
  if printf 'dGVzdA==' | base64 --decode >/dev/null 2>&1; then
    base64 --decode
  else
    base64 -D
  fi
}

helm status "$release" -n "$namespace" >/dev/null
kubectl -n "$namespace" rollout status "deployment/$release" --timeout=3m
token="$(kubectl -n "$namespace" get secret "$token_secret" -o jsonpath='{.data.access-token}' | decode_base64)"

log_file="$(mktemp)"
kubectl -n "$namespace" port-forward "service/$service" "$local_port:8000" >"$log_file" 2>&1 &
port_forward_pid=$!
trap 'kill "$port_forward_pid" 2>/dev/null || true; rm -f "$log_file"' EXIT HUP INT TERM

attempt=0
until curl -fsS "http://127.0.0.1:$local_port/health" >/dev/null; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ] || ! kill -0 "$port_forward_pid" 2>/dev/null; then
    printf 'error: controller port-forward did not become ready\n' >&2
    cat "$log_file" >&2
    exit 1
  fi
  sleep 1
done

curl -fsS \
  -H "Authorization: Bearer $token" \
  "http://127.0.0.1:$local_port/api/v1/whoami" >/dev/null

printf 'Verified Helm release, controller rollout, health endpoint, and authenticated API.\n'
