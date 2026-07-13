#!/usr/bin/env bash
set -Eeuo pipefail

cluster="${DEVBOXES_E2E_CLUSTER:-devboxes-e2e}"
namespace="${DEVBOXES_NAMESPACE:-devboxes}"
controller_port="${DEVBOXES_E2E_CONTROLLER_PORT:-18000}"
ssh_port="${DEVBOXES_E2E_SSH_PORT:-12222}"
node_image="${DEVBOXES_E2E_NODE_IMAGE:-kindest/node:v1.35.0@sha256:452d707d4862f52530247495d180205e029056831160e22870e37e3f6c1ac31f}"
published_version="${DEVBOXES_E2E_PUBLISHED_VERSION:-}"
released_cli="${DEVBOXES_E2E_CLI:-}"
workspace_timeout="${DEVBOXES_E2E_WORKSPACE_TIMEOUT:-3m}"
token="e2e-access-token-at-least-32-characters"
temporary_directory="$(mktemp -d)"
controller_port_forward=""
ssh_port_forward=""
previous_context=""

for command in kind kubectl helm docker curl jq ssh ssh-keygen nc; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'error: %s is required\n' "$command" >&2
    exit 1
  fi
done
if [[ -n "$published_version" && ! "$published_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  printf 'error: DEVBOXES_E2E_PUBLISHED_VERSION must be a semantic version without a v prefix\n' >&2
  exit 1
fi
if [[ -n "$published_version" && -z "${DEVBOXES_E2E_WORKSPACE_TIMEOUT:-}" ]]; then
  workspace_timeout=10m
fi
if [[ -n "$released_cli" && ! -x "$released_cli" ]]; then
  printf 'error: DEVBOXES_E2E_CLI is not executable: %s\n' "$released_cli" >&2
  exit 1
fi
previous_context="$(kubectl config current-context 2>/dev/null || true)"

cleanup() {
  status="$1"
  trap - EXIT INT TERM
  if [[ "$status" -ne 0 ]]; then
    printf '\nE2E diagnostics:\n' >&2
    kubectl --context "kind-$cluster" get all,pvc -n "$namespace" -o wide >&2 || true
    kubectl --context "kind-$cluster" describe pods -n "$namespace" >&2 || true
    kubectl --context "kind-$cluster" logs -n "$namespace" deployment/devboxes --tail=200 >&2 || true
    if [[ -f "$temporary_directory/controller-port-forward.log" ]]; then
      cat "$temporary_directory/controller-port-forward.log" >&2
    fi
    if [[ -f "$temporary_directory/ssh-port-forward.log" ]]; then
      cat "$temporary_directory/ssh-port-forward.log" >&2
    fi
  fi
  if [[ -n "$ssh_port_forward" ]]; then
    kill "$ssh_port_forward" >/dev/null 2>&1 || true
  fi
  if [[ -n "$controller_port_forward" ]]; then
    kill "$controller_port_forward" >/dev/null 2>&1 || true
  fi
  if [[ "${DEVBOXES_E2E_KEEP_CLUSTER:-0}" != 1 ]]; then
    kind delete cluster --name "$cluster" >/dev/null 2>&1 || true
  fi
  if [[ -n "$previous_context" ]] && kubectl config get-contexts "$previous_context" >/dev/null 2>&1; then
    kubectl config use-context "$previous_context" >/dev/null 2>&1 || true
  fi
  rm -rf "$temporary_directory"
  exit "$status"
}
trap 'cleanup $?' EXIT INT TERM

wait_for_http() {
  url="$1"
  process_id="$2"
  log_file="$3"
  for _ in {1..30}; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return
    fi
    if ! kill -0 "$process_id" >/dev/null 2>&1; then
      cat "$log_file" >&2
      return 1
    fi
    sleep 1
  done
  cat "$log_file" >&2
  return 1
}

api() {
  curl -fsS -H "Authorization: Bearer $token" "$@"
}

cli() {
  DEVBOX_URL="http://127.0.0.1:$controller_port" \
  DEVBOX_TOKEN="$token" \
  DEVBOX_CONFIG="$temporary_directory/devbox-config.toml" \
    "$released_cli" "$@"
}

kind delete cluster --name "$cluster" >/dev/null 2>&1 || true
kind create cluster --name "$cluster" --image "$node_image" --wait 120s

if [[ -n "$published_version" ]]; then
  case "$(uname -m)" in
    x86_64 | amd64) platform_architecture=amd64 ;;
    arm64 | aarch64) platform_architecture=arm64 ;;
    *)
      printf 'error: unsupported published E2E architecture: %s\n' "$(uname -m)" >&2
      exit 1
      ;;
  esac
  mkdir "$temporary_directory/docker-config"
  for image in \
    "ghcr.io/vicotrbb/devboxes-controller:$published_version" \
    "ghcr.io/vicotrbb/devboxes-workspace:$published_version"; do
    repository="${image%:*}"
    manifest_digest="$(
      docker buildx imagetools inspect "$image" --format '{{json .Manifest}}' \
        | jq -r --arg architecture "$platform_architecture" '
          .manifests[]
          | select(.platform.os == "linux" and .platform.architecture == $architecture)
          | .digest
        '
    )"
    test -n "$manifest_digest"
    DOCKER_CONFIG="$temporary_directory/docker-config" \
      docker pull "$repository@$manifest_digest"
    docker tag "$repository@$manifest_digest" "$image"
  done
  kind load docker-image --name "$cluster" \
    "ghcr.io/vicotrbb/devboxes-controller:$published_version" \
    "ghcr.io/vicotrbb/devboxes-workspace:$published_version"
fi

ssh-keygen -q -t ed25519 -N '' -f "$temporary_directory/id_ed25519"
if [[ -n "$published_version" ]]; then
  DEVBOXES_ACCESS_TOKEN="$token" \
  DEVBOXES_SSH_PUBLIC_KEY="$temporary_directory/id_ed25519.pub" \
  DEVBOXES_NAMESPACE="$namespace" \
  DEVBOXES_CHART_SOURCE=oci \
  DEVBOXES_VERSION="$published_version" \
    scripts/install.sh \
    --set workspace.sshService.type=NodePort \
    --set workspace.sshService.host=dev-node.example.test
  controller_image="$(kubectl -n "$namespace" get deployment devboxes -o jsonpath='{.spec.template.spec.containers[0].image}')"
  test "$controller_image" = "ghcr.io/vicotrbb/devboxes-controller:$published_version"
else
  docker build --tag devboxes-controller:e2e controller
  docker build --tag devboxes-workspace:e2e workspace
  kind load docker-image --name "$cluster" devboxes-controller:e2e devboxes-workspace:e2e

  DEVBOXES_ACCESS_TOKEN="$token" \
  DEVBOXES_SSH_PUBLIC_KEY="$temporary_directory/id_ed25519.pub" \
  DEVBOXES_NAMESPACE="$namespace" \
    scripts/install.sh \
    --set controller.image.repository=devboxes-controller \
    --set controller.image.tag=e2e \
    --set controller.image.pullPolicy=Never \
    --set workspace.image.repository=devboxes-workspace \
    --set workspace.image.tag=e2e \
    --set workspace.sshService.type=NodePort \
    --set workspace.sshService.host=dev-node.example.test
fi

GH_TOKEN=preserved-runtime-test-value \
DEVBOXES_SSH_PUBLIC_KEY="$temporary_directory/id_ed25519.pub" \
DEVBOXES_NAMESPACE="$namespace" \
  scripts/bootstrap-secrets.sh >/dev/null
GH_TOKEN='' \
DEVBOXES_SSH_PUBLIC_KEY="$temporary_directory/missing-key.pub" \
DEVBOXES_NAMESPACE="$namespace" \
  scripts/bootstrap-secrets.sh >/dev/null
preserved_github_token="$(
  kubectl -n "$namespace" get secret devboxes-workspace \
    -o 'go-template={{index .data "GH_TOKEN" | base64decode}}'
)"
test "$preserved_github_token" = preserved-runtime-test-value

kubectl -n "$namespace" port-forward service/devboxes "$controller_port:8000" \
  >"$temporary_directory/controller-port-forward.log" 2>&1 &
controller_port_forward=$!
wait_for_http \
  "http://127.0.0.1:$controller_port/health" \
  "$controller_port_forward" \
  "$temporary_directory/controller-port-forward.log"
api "http://127.0.0.1:$controller_port/api/v1/whoami" >/dev/null
if [[ -n "$released_cli" ]]; then
  cli --json list | jq -e 'type == "array"' >/dev/null
fi

if [[ -n "$released_cli" ]]; then
  cli --json create smoke --preset small --ttl 4 --no-wait >/dev/null
else
  api \
    -H 'Content-Type: application/json' \
    -d '{"name":"smoke","preset":"small","ttl_hours":4,"repository":null}' \
    "http://127.0.0.1:$controller_port/api/v1/devboxes" >/dev/null
fi
kubectl -n "$namespace" rollout status deployment/devbox-smoke --timeout="$workspace_timeout"
if [[ -n "$published_version" ]]; then
  workspace_image="$(kubectl -n "$namespace" get deployment devbox-smoke -o jsonpath='{.spec.template.spec.containers[0].image}')"
  test "$workspace_image" = "ghcr.io/vicotrbb/devboxes-workspace:$published_version"
fi

for _ in {1..30}; do
  state="$(api "http://127.0.0.1:$controller_port/api/v1/devboxes/smoke" | jq -r .state)"
  [[ "$state" == ready ]] && break
  sleep 1
done
test "$state" = ready
node_port="$(kubectl -n "$namespace" get service devbox-smoke-ssh -o jsonpath='{.spec.ports[0].nodePort}')"
test -n "$node_port"
first_host_key="$(kubectl -n "$namespace" exec deployment/devbox-smoke -- cat /home/dev/.devbox/ssh/ssh_host_ed25519_key.pub)"

kubectl -n "$namespace" port-forward service/devbox-smoke-ssh "$ssh_port:22" \
  >"$temporary_directory/ssh-port-forward.log" 2>&1 &
ssh_port_forward=$!
for _ in {1..30}; do
  nc -z 127.0.0.1 "$ssh_port" >/dev/null 2>&1 && break
  sleep 1
done
nc -z 127.0.0.1 "$ssh_port" >/dev/null 2>&1
ssh \
  -i "$temporary_directory/id_ed25519" \
  -p "$ssh_port" \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  dev@127.0.0.1 \
  'test -d "$HOME/workspace" && sudo -n true && printf "end-to-end-ssh-ok\n"'
kill "$ssh_port_forward" >/dev/null 2>&1 || true
ssh_port_forward=""

if [[ -n "$released_cli" ]]; then
  cli --json stop smoke >/dev/null
else
  api -X POST "http://127.0.0.1:$controller_port/api/v1/devboxes/smoke/stop" >/dev/null
fi
for _ in {1..30}; do
  replicas="$(kubectl -n "$namespace" get deployment devbox-smoke -o jsonpath='{.spec.replicas}')"
  [[ "$replicas" == 0 ]] && break
  sleep 1
done
test "$replicas" = 0

if [[ -n "$released_cli" ]]; then
  cli delete smoke >/dev/null
else
  api -X DELETE "http://127.0.0.1:$controller_port/api/v1/devboxes/smoke?purge=false" >/dev/null
fi
kubectl -n "$namespace" wait --for=delete deployment/devbox-smoke --timeout=2m
kubectl -n "$namespace" wait --for=delete service/devbox-smoke-ssh --timeout=2m
kubectl -n "$namespace" get pvc devbox-smoke-home >/dev/null

if [[ -n "$released_cli" ]]; then
  cli --json create smoke --preset small --ttl 4 --no-wait >/dev/null
else
  api \
    -H 'Content-Type: application/json' \
    -d '{"name":"smoke","preset":"small","ttl_hours":4,"repository":null}' \
    "http://127.0.0.1:$controller_port/api/v1/devboxes" >/dev/null
fi
kubectl -n "$namespace" rollout status deployment/devbox-smoke --timeout="$workspace_timeout"
second_host_key="$(kubectl -n "$namespace" exec deployment/devbox-smoke -- cat /home/dev/.devbox/ssh/ssh_host_ed25519_key.pub)"
test "$first_host_key" = "$second_host_key"

if [[ -n "$released_cli" ]]; then
  cli delete smoke --purge --yes >/dev/null
else
  api -X DELETE "http://127.0.0.1:$controller_port/api/v1/devboxes/smoke?purge=true" >/dev/null
fi
kubectl -n "$namespace" wait --for=delete pvc/devbox-smoke-home --timeout=2m

if [[ -n "$published_version" ]]; then
  printf 'Verified published %s chart, images, and CLI through clean install, API, PVC, NodePort %s, SSH, stop, retain, reuse, host identity, and purge.\n' \
    "$published_version" "$node_port"
else
  printf 'Verified clean install, API, PVC, NodePort %s, SSH, stop, retain, reuse, host identity, and purge.\n' "$node_port"
fi
