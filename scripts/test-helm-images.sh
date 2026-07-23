#!/usr/bin/env bash
set -Eeuo pipefail

project_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
chart="$project_directory/charts/devboxes"
temporary_directory="$(mktemp -d)"
trap 'rm -rf "$temporary_directory"' EXIT

helm lint "$chart" --strict

helm template devboxes "$chart" --namespace devboxes \
  >"$temporary_directory/disabled.yaml"
grep -Fq 'name: DEVBOXES_CUSTOM_IMAGES_ENABLED' "$temporary_directory/disabled.yaml"
grep -Fq 'name: DEVBOXES_CUSTOM_IMAGES' "$temporary_directory/disabled.yaml"
grep -Fq 'value: "false"' "$temporary_directory/disabled.yaml"

image_profile=(
  --set workspace.customImages.enabled=true
  --set 'workspace.customImages.profiles[0].name=nginx'
  --set-string 'workspace.customImages.profiles[0].displayName=NGINX preview'
  --set-string 'workspace.customImages.profiles[0].description=Serve a local preview'
  --set-string 'workspace.customImages.profiles[0].image=docker.io/nginxinc/nginx-unprivileged:1.27.5-alpine'
  --set 'workspace.customImages.profiles[0].mode=sidecar'
  --set 'workspace.customImages.profiles[0].pullPolicy=Always'
  --set-string 'workspace.customImages.profiles[0].resources.cpuRequest=25m'
  --set-string 'workspace.customImages.profiles[0].resources.memoryRequest=32Mi'
  --set-string 'workspace.customImages.profiles[0].resources.cpuLimit=500m'
  --set-string 'workspace.customImages.profiles[0].resources.memoryLimit=512Mi'
  --set 'workspace.customImages.profiles[0].ports[0].name=http'
  --set 'workspace.customImages.profiles[0].ports[0].containerPort=8080'
  --set 'workspace.customImages.profiles[0].ports[0].protocol=TCP'
)

helm template devboxes "$chart" --namespace devboxes "${image_profile[@]}" \
  >"$temporary_directory/enabled.yaml"
grep -Fq 'name: DEVBOXES_CUSTOM_IMAGES_ENABLED' "$temporary_directory/enabled.yaml"
grep -Fq 'value: "true"' "$temporary_directory/enabled.yaml"
grep -Fq 'docker.io/nginxinc/nginx-unprivileged:1.27.5-alpine' "$temporary_directory/enabled.yaml"
grep -Fq 'containerPort' "$temporary_directory/enabled.yaml"

if helm template devboxes "$chart" --namespace devboxes \
  --set workspace.customImages.enabled=true \
  >"$temporary_directory/missing.yaml" \
  2>"$temporary_directory/missing.err"; then
  printf 'error: custom image render accepted an enabled feature without profiles\n' >&2
  exit 1
fi
grep -Fq 'profiles' "$temporary_directory/missing.err"

if helm template devboxes "$chart" --namespace devboxes \
  "${image_profile[@]}" \
  --set 'workspace.customImages.profiles[1].name=nginx' \
  --set-string 'workspace.customImages.profiles[1].displayName=Duplicate NGINX' \
  --set-string 'workspace.customImages.profiles[1].image=registry.example/nginx:2' \
  >"$temporary_directory/duplicate.yaml" \
  2>"$temporary_directory/duplicate.err"; then
  printf 'error: custom image render accepted duplicate profile names\n' >&2
  exit 1
fi
grep -Fq 'duplicate name' "$temporary_directory/duplicate.err"

if helm template devboxes "$chart" --namespace devboxes \
  "${image_profile[@]}" \
  --set-string 'workspace.customImages.profiles[0].image=https://registry.example/nginx:1' \
  >"$temporary_directory/invalid-image.yaml" \
  2>"$temporary_directory/invalid-image.err"; then
  printf 'error: custom image render accepted a URL scheme\n' >&2
  exit 1
fi
grep -Fq 'image must not contain a URL scheme' "$temporary_directory/invalid-image.err"

if helm template devboxes "$chart" --namespace devboxes \
  "${image_profile[@]}" \
  --set 'workspace.customImages.profiles[0].ports[0].containerPort=80' \
  >"$temporary_directory/low-port.yaml" \
  2>"$temporary_directory/low-port.err"; then
  printf 'error: custom image render accepted a privileged sidecar port\n' >&2
  exit 1
fi
grep -Fq 'minimum:' "$temporary_directory/low-port.err"

if helm template devboxes "$chart" --namespace devboxes \
  "${image_profile[@]}" \
  --set 'workspace.customImages.profiles[0].mode=workspace' \
  >"$temporary_directory/workspace-resources.yaml" \
  2>"$temporary_directory/workspace-resources.err"; then
  printf 'error: custom image render accepted sidecar resources for a workspace profile\n' >&2
  exit 1
fi

printf 'Verified disabled, enabled, and invalid custom image Helm contracts.\n'
