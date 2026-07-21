#!/usr/bin/env bash
set -Eeuo pipefail

cluster="${DEVBOXES_E2E_CLUSTER:-devboxes-e2e}"
namespace="${DEVBOXES_NAMESPACE:-devboxes}"
controller_port="${DEVBOXES_E2E_CONTROLLER_PORT:-18000}"
ssh_port="${DEVBOXES_E2E_SSH_PORT:-12222}"
node_image="${DEVBOXES_E2E_NODE_IMAGE:-kindest/node:v1.35.0@sha256:452d707d4862f52530247495d180205e029056831160e22870e37e3f6c1ac31f}"
published_version="${DEVBOXES_E2E_PUBLISHED_VERSION:-}"
released_cli="${DEVBOXES_E2E_CLI:-}"
interactive_ssh="${DEVBOXES_E2E_INTERACTIVE_SSH:-1}"
workspace_timeout="${DEVBOXES_E2E_WORKSPACE_TIMEOUT:-3m}"
token="e2e-access-token-at-least-32-characters"
temporary_directory="$(mktemp -d)"
controller_port_forward=""
ssh_port_forward=""
previous_context=""

for command in kind kubectl helm docker curl jq ssh ssh-keygen nc python3; do
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
if [[ "$interactive_ssh" != 0 && "$interactive_ssh" != 1 ]]; then
  printf 'error: DEVBOXES_E2E_INTERACTIVE_SSH must be 0 or 1\n' >&2
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
    kubectl --context "kind-$cluster" logs -n "$namespace" deployment/devbox-smoke \
      -c insights-agent --tail=200 >&2 || true
    if [[ -f "$temporary_directory/controller-port-forward.log" ]]; then
      cat "$temporary_directory/controller-port-forward.log" >&2
    fi
    if [[ -f "$temporary_directory/ssh-port-forward.log" ]]; then
      cat "$temporary_directory/ssh-port-forward.log" >&2
    fi
    for log_file in \
      "$temporary_directory"/remote-*.log \
      "$temporary_directory"/interactive-*.log \
      "$temporary_directory"/tmux-reconnect.log; do
      if [[ -f "$log_file" ]]; then
        printf '\n%s:\n' "$(basename "$log_file")" >&2
        cat "$log_file" >&2
      fi
    done
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

start_controller_port_forward() {
  if [[ -n "$controller_port_forward" ]]; then
    kill "$controller_port_forward" >/dev/null 2>&1 || true
  fi
  kubectl -n "$namespace" port-forward service/devboxes "$controller_port:8000" \
    >"$temporary_directory/controller-port-forward.log" 2>&1 &
  controller_port_forward=$!
  wait_for_http \
    "http://127.0.0.1:$controller_port/health" \
    "$controller_port_forward" \
    "$temporary_directory/controller-port-forward.log"
}

write_otlp_fixture() {
  output="$1"
  provider="$2"
  nonce="$3"
  token_total="$4"
  python3 - "$output" "$provider" "$nonce" "$token_total" <<'PY'
import json
import sys
import time

output, provider, nonce, token_total = sys.argv[1:]
observed = str(time.time_ns() + int(nonce))
tokens = int(token_total)


def attribute(key, value):
    return {"key": key, "value": {"stringValue": value}}


def point(value, **attributes):
    result = {"timeUnixNano": observed, "asInt": value}
    if attributes:
        result["attributes"] = [attribute(key, item) for key, item in attributes.items()]
    return result


def sum_metric(name, points, unit="1"):
    return {
        "name": name,
        "unit": unit,
        "sum": {
            "aggregationTemporality": 1,
            "isMonotonic": True,
            "dataPoints": points,
        },
    }


if provider == "codex":
    input_tokens = max(1, tokens * 2 // 3)
    output_tokens = tokens - input_tokens
    metrics = [
        sum_metric("codex.process.start", [point(1, start_type="cli")]),
        sum_metric(
            "codex.turn.token_usage",
            [
                point(input_tokens, token_type="input", model="e2e-codex"),
                point(output_tokens, token_type="output", model="e2e-codex"),
                point(tokens, token_type="total", model="e2e-codex"),
            ],
            "tokens",
        ),
    ]
    service = "codex_cli_rs"
    version = "0.144.0"
else:
    metrics = [
        sum_metric("claude_code.session.count", [point(1)]),
        sum_metric(
            "claude_code.token.usage",
            [
                point(tokens - 3, type="input", model="e2e-claude"),
                point(3, type="output", model="e2e-claude"),
            ],
            "tokens",
        ),
        {
            "name": "claude_code.cost.usage",
            "unit": "USD",
            "sum": {
                "aggregationTemporality": 1,
                "isMonotonic": False,
                "dataPoints": [{"timeUnixNano": observed, "asDouble": 0.02}],
            },
        },
        {
            "name": "claude_code.active_time.total",
            "unit": "s",
            "sum": {
                "aggregationTemporality": 1,
                "isMonotonic": False,
                "dataPoints": [{"timeUnixNano": observed, "asDouble": 60.0}],
            },
        },
        sum_metric("claude_code.lines_of_code.count", [point(4, type="added")], "lines"),
    ]
    service = "claude-code"
    version = "2.1.205"

payload = {
    "resourceMetrics": [
        {
            "resource": {
                "attributes": [
                    attribute("service.name", service),
                    attribute("service.version", version),
                ]
            },
            "scopeMetrics": [{"metrics": metrics}],
        }
    ]
}
with open(output, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
PY
}

send_otlp_fixture() {
  fixture="$1"
  for _ in {1..30}; do
    if kubectl -n "$namespace" exec -i deployment/devbox-smoke -c devbox -- \
      curl -fsS \
        -H 'Content-Type: application/json' \
        --data-binary @- \
        http://127.0.0.1:4318/v1/metrics \
      <"$fixture" >/dev/null; then
      return
    fi
    sleep 1
  done
  printf 'error: local Insights receiver did not accept OTLP metrics\n' >&2
  return 1
}

cli() {
  DEVBOX_URL="http://127.0.0.1:$controller_port" \
  DEVBOX_TOKEN="$token" \
  DEVBOX_CONFIG="$temporary_directory/devbox-config.toml" \
    "$released_cli" "$@"
}

browser_cli() {
  DEVBOX_URL="http://127.0.0.1:$controller_port" \
  DEVBOX_CONFIG="$temporary_directory/browser-login-config.toml" \
    "$released_cli" "$@"
}

file_mode() {
  if stat -c '%a' "$1" >/dev/null 2>&1; then
    stat -c '%a' "$1"
  else
    stat -f '%Lp' "$1"
  fi
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
    --set insights.enabled=true \
    --set insights.agent.scanIntervalSeconds=15 \
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
    --set insights.enabled=true \
    --set insights.agent.scanIntervalSeconds=15 \
    --set gpu.enabled=true \
    --set gpu.defaultProfile=test-gpu \
    --set 'gpu.profiles[0].name=test-gpu' \
    --set-string 'gpu.profiles[0].displayName=Test GPU' \
    --set-string 'gpu.profiles[0].description=Unschedulable E2E accelerator' \
    --set-string 'gpu.profiles[0].resourceName=example.com/gpu' \
    --set 'gpu.profiles[0].count=1' \
    --set 'gpu.profiles[0].supplementalGroups[0]=44' \
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

start_controller_port_forward
api "http://127.0.0.1:$controller_port/api/v1/whoami" >/dev/null
if [[ -z "$published_version" ]]; then
  gpu_capabilities="$(api "http://127.0.0.1:$controller_port/api/v1/capabilities")"
  jq -e '
    .gpu.enabled == true
    and .gpu.default_profile == "test-gpu"
    and .gpu.profiles == [{
      "name": "test-gpu",
      "display_name": "Test GPU",
      "description": "Unschedulable E2E accelerator",
      "resource_name": "example.com/gpu",
      "count": 1,
      "default": true
    }]
  ' <<<"$gpu_capabilities" >/dev/null
  if [[ -n "$released_cli" ]]; then
    cli --json gpu profiles \
      | jq -e '.enabled == true and .default_profile == "test-gpu"' >/dev/null
    cli --json create gpu-smoke --gpu --no-wait \
      | jq -e '.gpu.profile == "test-gpu" and .gpu.resource_name == "example.com/gpu"' \
        >/dev/null
  else
    api \
      -H 'Content-Type: application/json' \
      -d '{"name":"gpu-smoke","gpu":{}}' \
      "http://127.0.0.1:$controller_port/api/v1/devboxes" \
      | jq -e '.gpu.profile == "test-gpu" and .gpu.resource_name == "example.com/gpu"' \
        >/dev/null
  fi
  gpu_deployment="$(kubectl -n "$namespace" get deployment devbox-gpu-smoke -o json)"
  jq -e '
    .metadata.annotations["gpu.devboxes.bonalab.org/profile"] == "test-gpu"
    and .spec.template.spec.securityContext.supplementalGroups == [44]
    and .spec.template.spec.containers[0].resources.requests["example.com/gpu"] == "1"
    and .spec.template.spec.containers[0].resources.limits["example.com/gpu"] == "1"
    and ([
      .spec.template.spec.containers[0].env[]
      | select(.name == "DEVBOX_GPU_SUPPLEMENTAL_GROUPS")
      | .value
    ] == ["44"])
    and ([.spec.template.spec.containers[1:][]?.resources.requests["example.com/gpu"]]
      | all(. == null))
  ' <<<"$gpu_deployment" >/dev/null
  gpu_message=""
  for _ in {1..30}; do
    gpu_message="$(
      api "http://127.0.0.1:$controller_port/api/v1/devboxes/gpu-smoke" \
        | jq -r '.message // ""'
    )"
    [[ "$gpu_message" == *"example.com/gpu"* ]] && break
    sleep 1
  done
  [[ "$gpu_message" == *"example.com/gpu"* ]]
  api -X DELETE \
    "http://127.0.0.1:$controller_port/api/v1/devboxes/gpu-smoke?purge=true" >/dev/null
  kubectl -n "$namespace" wait --for=delete deployment/devbox-gpu-smoke --timeout=2m
  kubectl -n "$namespace" wait --for=delete pvc/devbox-gpu-smoke-home --timeout=2m
fi
if [[ -n "$released_cli" ]]; then
  cli --json list | jq -e 'type == "array"' >/dev/null

  browser_cli login --no-open --timeout 60 \
    >"$temporary_directory/browser-login.out" \
    2>"$temporary_directory/browser-login.err" &
  browser_login_pid=$!
  authorization_url=""
  for _ in {1..40}; do
    authorization_url="$(sed -n '/^http:\/\/127\.0\.0\.1:/p' "$temporary_directory/browser-login.out" | tail -n 1)"
    [[ -n "$authorization_url" ]] && break
    kill -0 "$browser_login_pid" 2>/dev/null || break
    sleep 0.25
  done
  test -n "$authorization_url"

  login_location="$(
    curl -sS -D - -o /dev/null "$authorization_url" \
      | awk 'tolower($1) == "location:" {sub(/\r$/, "", $2); print $2; exit}'
  )"
  next_target="$(
    python3 - "$login_location" <<'PY'
import sys
from urllib.parse import parse_qs, urlsplit

print(parse_qs(urlsplit(sys.argv[1]).query)["next"][0])
PY
  )"
  cookie_jar="$temporary_directory/browser-cookies.txt"
  curl -sS -c "$cookie_jar" -b "$cookie_jar" -o /dev/null \
    --request POST \
    --data-urlencode "token=$token" \
    --data-urlencode "next=$next_target" \
    "http://127.0.0.1:$controller_port/auth/login"
  csrf="$(awk '$6 == "devboxes_csrf" {print $7}' "$cookie_jar")"
  test -n "$csrf"
  authorization_parameters="$(
    python3 - "$authorization_url" <<'PY'
import json
import sys
from urllib.parse import parse_qs, urlsplit

print(json.dumps({key: values[0] for key, values in parse_qs(urlsplit(sys.argv[1]).query).items()}))
PY
  )"
  curl -sS -L -c "$cookie_jar" -b "$cookie_jar" -o /dev/null \
    --data-urlencode "action=approve" \
    --data-urlencode "csrf=$csrf" \
    --data-urlencode "response_type=$(jq -r .response_type <<<"$authorization_parameters")" \
    --data-urlencode "client_id=$(jq -r .client_id <<<"$authorization_parameters")" \
    --data-urlencode "redirect_uri=$(jq -r .redirect_uri <<<"$authorization_parameters")" \
    --data-urlencode "state=$(jq -r .state <<<"$authorization_parameters")" \
    --data-urlencode "code_challenge=$(jq -r .code_challenge <<<"$authorization_parameters")" \
    --data-urlencode "code_challenge_method=$(jq -r .code_challenge_method <<<"$authorization_parameters")" \
    "http://127.0.0.1:$controller_port/auth/cli/authorize"
  wait "$browser_login_pid"
  grep -Fq 'authenticated as operator via cli-bearer' "$temporary_directory/browser-login.out"
  if grep -Fq "$token" "$temporary_directory/browser-login.out" \
    || grep -Fq "$token" "$temporary_directory/browser-login.err"; then
    printf 'error: browser login output exposed the master token\n' >&2
    exit 1
  fi
  test "$(file_mode "$temporary_directory/browser-login-config.toml")" = 600
  browser_cli --json list | jq -e 'type == "array"' >/dev/null
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
container_count="$(
  kubectl -n "$namespace" get deployment devbox-smoke -o json \
    | jq '.spec.template.spec.containers | length'
)"
test "$container_count" = 2
instance_id="$(
  kubectl -n "$namespace" get deployment devbox-smoke -o json \
    | jq -r '.metadata.annotations["insights.devboxes.bonalab.org/instance-id"]'
)"
pvc_instance_id="$(
  kubectl -n "$namespace" get pvc devbox-smoke-home -o json \
    | jq -r '.metadata.annotations["insights.devboxes.bonalab.org/instance-id"]'
)"
test "$instance_id" = "$pvc_instance_id"
test "$instance_id" != null
credential_secret="$(
  kubectl -n "$namespace" get deployment devbox-smoke -o json \
    | jq -r '.spec.template.spec.containers[]
      | select(.name == "insights-agent")
      | .env[]
      | select(.name == "DEVBOXES_INSIGHTS_CREDENTIAL")
      | .valueFrom.secretKeyRef.name'
)"
test "$credential_secret" = devbox-smoke-insights
# The command substitution expands inside the workspace container.
# shellcheck disable=SC2016
kubectl -n "$namespace" exec deployment/devbox-smoke -c insights-agent -- \
  sh -lc 'test "$(stat -c "%u:%g %a" /home/dev/.devbox/insights)" = "1000:1000 700"'
if kubectl -n "$namespace" get deployment devbox-smoke -o yaml | grep -Fq "$token"; then
  printf 'error: workspace Deployment exposed the controller access token\n' >&2
  exit 1
fi

write_otlp_fixture "$temporary_directory/codex-otlp.json" codex 1 15
write_otlp_fixture "$temporary_directory/claude-otlp.json" claude 2 10
send_otlp_fixture "$temporary_directory/codex-otlp.json"
send_otlp_fixture "$temporary_directory/claude-otlp.json"
insights_summary=""
for _ in {1..45}; do
  insights_summary="$(
    api "http://127.0.0.1:$controller_port/api/v1/insights/summary?since=24h"
  )"
  if jq -e '
    .data.ai.totals.sessions == 2
    and .data.ai.totals.tokens == 25
    and .data.ai.totals.provider_reported_cost_usd == 0.02
    and .data.ai.totals.active_seconds == 60
    and .data.ai.totals.ai_lines == 4
  ' <<<"$insights_summary" >/dev/null; then
    break
  fi
  sleep 1
done
jq -e '.data.ai.totals.tokens == 25' <<<"$insights_summary" >/dev/null

send_otlp_fixture "$temporary_directory/codex-otlp.json"
send_otlp_fixture "$temporary_directory/claude-otlp.json"
sleep 3
replayed_summary="$(
  api "http://127.0.0.1:$controller_port/api/v1/insights/summary?since=24h"
)"
jq -e '.data.ai.totals.tokens == 25 and .data.ai.totals.sessions == 2' \
  <<<"$replayed_summary" >/dev/null

# Variables expand in the workspace shell, not in this process.
# shellcheck disable=SC2016
kubectl -n "$namespace" exec deployment/devbox-smoke -c devbox -- \
  runuser -u dev -- env HOME=/home/dev /bin/bash -lc '
  set -Eeuo pipefail
  repository="$HOME/workspace/insights-e2e"
  mkdir -p "$repository"
  git -C "$repository" init -b main >/dev/null
  git -C "$repository" config user.name "Sensitive E2E Name"
  git -C "$repository" config user.email "sensitive-e2e@example.invalid"
  printf "baseline\n" >"$repository/private-baseline-name.txt"
  git -C "$repository" add .
  git -C "$repository" commit -m "sensitive baseline message" >/dev/null
'
sleep 18
# Variables expand in the workspace shell, not in this process.
# shellcheck disable=SC2016
kubectl -n "$namespace" exec deployment/devbox-smoke -c devbox -- \
  runuser -u dev -- env HOME=/home/dev /bin/bash -lc '
  set -Eeuo pipefail
  repository="$HOME/workspace/insights-e2e"
  printf "baseline\nobserved\n" >"$repository/private-baseline-name.txt"
  git -C "$repository" add .
  git -C "$repository" commit -m "sensitive observed message" >/dev/null
  printf "baseline\nobserved\nworking tree\n" >"$repository/private-baseline-name.txt"
'
for _ in {1..45}; do
  insights_summary="$(
    api "http://127.0.0.1:$controller_port/api/v1/insights/summary?since=24h"
  )"
  if jq -e '
    .data.code.commits == 1
    and .data.code.additions == 1
    and .data.code.working_tree.unstaged_files == 1
  ' <<<"$insights_summary" >/dev/null; then
    break
  fi
  sleep 1
done
jq -e '.data.code.commits == 1 and .data.code.working_tree.unstaged_files == 1' \
  <<<"$insights_summary" >/dev/null

if [[ -n "$released_cli" ]]; then
  cli --json metrics --since 24h \
    | jq -e '.data.ai.totals.tokens == 25 and .data.code.commits == 1' >/dev/null
  cli metrics --since 24h | grep -F 'INSIGHTS' >/dev/null
  cli metrics export --since 24h --format csv \
    | grep -F 'category,provider,metric,value' >/dev/null
fi

api -o "$temporary_directory/insights-backup.db" \
  "http://127.0.0.1:$controller_port/api/v1/insights/export?format=sqlite"
for sensitive_value in \
  'Sensitive E2E Name' \
  'sensitive-e2e@example.invalid' \
  'sensitive baseline message' \
  'sensitive observed message' \
  'private-baseline-name.txt'; do
  if grep -aFq "$sensitive_value" "$temporary_directory/insights-backup.db"; then
    printf 'error: sensitive Git value reached the central Insights backup\n' >&2
    exit 1
  fi
done

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
  'test -d "$HOME/workspace" && sudo -n true && printf "persistent\n" >"$HOME/workspace/e2e-persistence" && printf "end-to-end-ssh-ok\n"'

for terminal in xterm-ghostty completely-unknown-future-terminal; do
  terminal_log="$temporary_directory/remote-$terminal.log"
  TERM="$terminal" ssh \
    -i "$temporary_directory/id_ed25519" \
    -p "$ssh_port" \
    -o SendEnv=TERM \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    dev@127.0.0.1 \
    'printf "remote-term=%s original=%s\n" "$TERM" "$DEVBOX_ORIGINAL_TERM"' \
    >"$terminal_log" 2>&1
  grep -Fq "original=$terminal" "$terminal_log"
done
grep -Fq 'remote-term=xterm-ghostty original=xterm-ghostty' \
  "$temporary_directory/remote-xterm-ghostty.log"
grep -Fq 'devbox: terminal completely-unknown-future-terminal is unavailable; using xterm-256color' \
  "$temporary_directory/remote-completely-unknown-future-terminal.log"
grep -Fq 'remote-term=xterm-256color original=completely-unknown-future-terminal' \
  "$temporary_directory/remote-completely-unknown-future-terminal.log"

if [[ "$interactive_ssh" == 1 ]]; then
  for terminal in xterm-ghostty completely-unknown-future-terminal; do
    terminal_log="$temporary_directory/interactive-$terminal.log"
    if [[ "$terminal" != xterm-ghostty ]]; then
      ssh \
        -i "$temporary_directory/id_ed25519" \
        -p "$ssh_port" \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        dev@127.0.0.1 \
        'tmux kill-server 2>/dev/null || true'
    fi
    # The variables expand in the remote shell inside tmux, not in this process.
    # shellcheck disable=SC2016
    { printf 'printf "interactive-term=%%s original=%%s\\n" "$TERM" "$DEVBOX_ORIGINAL_TERM"\ntmux detach-client\n'; sleep 1; } \
      | TERM="$terminal" ssh -tt \
        -i "$temporary_directory/id_ed25519" \
        -p "$ssh_port" \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        dev@127.0.0.1 >"$terminal_log" 2>&1
    grep -Fq "original=$terminal" "$terminal_log"
    if grep -Fq 'missing or unsuitable terminal' "$terminal_log"; then
      printf 'error: %s reproduced the historical tmux terminal failure\n' "$terminal" >&2
      exit 1
    fi
  done
  grep -Fq 'interactive-term=tmux-256color original=xterm-ghostty' \
    "$temporary_directory/interactive-xterm-ghostty.log"
  grep -Fq 'devbox: terminal completely-unknown-future-terminal is unavailable; using xterm-256color' \
    "$temporary_directory/interactive-completely-unknown-future-terminal.log"
  grep -Fq 'interactive-term=tmux-256color original=completely-unknown-future-terminal' \
    "$temporary_directory/interactive-completely-unknown-future-terminal.log"
  printf 'printf "tmux-reconnect=ok\n"\ntmux detach-client\n' \
    | TERM=completely-unknown-future-terminal ssh -tt \
      -i "$temporary_directory/id_ed25519" \
      -p "$ssh_port" \
      -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      dev@127.0.0.1 >"$temporary_directory/tmux-reconnect.log" 2>&1
  grep -Fq 'tmux-reconnect=ok' "$temporary_directory/tmux-reconnect.log"
fi
kill "$ssh_port_forward" >/dev/null 2>&1 || true
ssh_port_forward=""

write_otlp_fixture "$temporary_directory/queued-codex-otlp.json" codex 3 2
kubectl -n "$namespace" scale deployment/devboxes --replicas=0 >/dev/null
kubectl -n "$namespace" wait --for=delete pod -l app.kubernetes.io/component=controller --timeout=2m
if [[ -n "$controller_port_forward" ]]; then
  kill "$controller_port_forward" >/dev/null 2>&1 || true
  controller_port_forward=""
fi
send_otlp_fixture "$temporary_directory/queued-codex-otlp.json"
kubectl -n "$namespace" scale deployment/devbox-smoke --replicas=0 >/dev/null
for _ in {1..30}; do
  replicas="$(kubectl -n "$namespace" get deployment devbox-smoke -o jsonpath='{.status.replicas}')"
  [[ -z "$replicas" || "$replicas" == 0 ]] && break
  sleep 1
done
kubectl -n "$namespace" scale deployment/devbox-smoke --replicas=1 >/dev/null
kubectl -n "$namespace" rollout status deployment/devbox-smoke --timeout="$workspace_timeout"
queued_batches="$(
  kubectl -n "$namespace" exec deployment/devbox-smoke -c insights-agent -- \
    python3 -c 'import sqlite3; connection=sqlite3.connect("/home/dev/.devbox/insights/outbox.db"); print(connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0]); connection.close()'
)"
test "$queued_batches" -gt 0
restarted_instance_id="$(
  kubectl -n "$namespace" get deployment devbox-smoke -o json \
    | jq -r '.metadata.annotations["insights.devboxes.bonalab.org/instance-id"]'
)"
test "$restarted_instance_id" = "$instance_id"

kubectl -n "$namespace" scale deployment/devboxes --replicas=1 >/dev/null
kubectl -n "$namespace" rollout status deployment/devboxes --timeout=3m
start_controller_port_forward
for _ in {1..45}; do
  insights_summary="$(
    api "http://127.0.0.1:$controller_port/api/v1/insights/summary?since=24h"
  )"
  if jq -e '
    .data.ai.totals.sessions == 3
    and .data.ai.totals.tokens == 27
    and .data.code.commits == 1
  ' <<<"$insights_summary" >/dev/null; then
    break
  fi
  sleep 1
done
jq -e '.data.ai.totals.tokens == 27 and .data.code.commits == 1' \
  <<<"$insights_summary" >/dev/null

controller_pod="$(
  kubectl -n "$namespace" get pod -l app.kubernetes.io/component=controller \
    -o jsonpath='{.items[0].metadata.name}'
)"
kubectl -n "$namespace" delete pod "$controller_pod" --wait=true >/dev/null
kubectl -n "$namespace" rollout status deployment/devboxes --timeout=3m
start_controller_port_forward
persisted_summary="$(
  api "http://127.0.0.1:$controller_port/api/v1/insights/summary?since=24h"
)"
jq -e '.data.ai.totals.tokens == 27 and .data.code.commits == 1' \
  <<<"$persisted_summary" >/dev/null

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
kubectl -n "$namespace" wait --for=delete secret/devbox-smoke-insights --timeout=2m
retained_summary="$(
  api "http://127.0.0.1:$controller_port/api/v1/insights/summary?since=24h&box=smoke"
)"
jq -e '.data.ai.totals.tokens == 27 and .data.code.commits == 1' \
  <<<"$retained_summary" >/dev/null

if [[ -n "$released_cli" ]]; then
  cli --json create smoke --preset small --ttl 4 --no-wait >/dev/null
else
  api \
    -H 'Content-Type: application/json' \
    -d '{"name":"smoke","preset":"small","ttl_hours":4,"repository":null}' \
    "http://127.0.0.1:$controller_port/api/v1/devboxes" >/dev/null
fi
kubectl -n "$namespace" rollout status deployment/devbox-smoke --timeout="$workspace_timeout"
recreated_instance_id="$(
  kubectl -n "$namespace" get deployment devbox-smoke -o json \
    | jq -r '.metadata.annotations["insights.devboxes.bonalab.org/instance-id"]'
)"
test "$recreated_instance_id" = "$instance_id"
second_host_key="$(kubectl -n "$namespace" exec deployment/devbox-smoke -- cat /home/dev/.devbox/ssh/ssh_host_ed25519_key.pub)"
test "$first_host_key" = "$second_host_key"
kubectl -n "$namespace" exec deployment/devbox-smoke -- \
  grep -Fx persistent /home/dev/workspace/e2e-persistence >/dev/null

if [[ -n "$released_cli" ]]; then
  cli metrics purge --box smoke --yes >/dev/null
else
  api -X DELETE \
    "http://127.0.0.1:$controller_port/api/v1/insights?box=smoke" >/dev/null
fi
purged_summary="$(
  api "http://127.0.0.1:$controller_port/api/v1/insights/summary?since=24h&box=smoke"
)"
jq -e '
  .data.ai.totals.tokens == 0
  and .data.ai.totals.sessions == 0
  and .data.code.commits == 0
' <<<"$purged_summary" >/dev/null

if [[ -n "$released_cli" ]]; then
  cli delete smoke --purge --yes >/dev/null
else
  api -X DELETE "http://127.0.0.1:$controller_port/api/v1/devboxes/smoke?purge=true" >/dev/null
fi
kubectl -n "$namespace" wait --for=delete pvc/devbox-smoke-home --timeout=2m

if [[ -n "$published_version" ]]; then
  printf 'Verified published %s chart, images, and CLI through clean install, Insights ingest, Git aggregation, deduplication, durable outboxes, central restart, API, PVC, NodePort %s, SSH, retain, reuse, and explicit purge.\n' \
    "$published_version" "$node_port"
else
  printf 'Verified clean install, Insights ingest, Git aggregation, deduplication, durable outboxes, central restart, API, PVC, NodePort %s, SSH, retain, reuse, and explicit purge.\n' "$node_port"
fi
