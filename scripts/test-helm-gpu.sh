#!/usr/bin/env bash
set -Eeuo pipefail

project_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
chart="$project_directory/charts/devboxes"
temporary_directory="$(mktemp -d)"
trap 'rm -rf "$temporary_directory"' EXIT

helm lint "$chart" --strict

helm template devboxes "$chart" --namespace devboxes \
  >"$temporary_directory/disabled.yaml"
grep -Fq 'name: DEVBOXES_GPU_ENABLED' "$temporary_directory/disabled.yaml"
grep -Fq 'value: "false"' "$temporary_directory/disabled.yaml"
grep -Fq 'name: DEVBOXES_GPU_PROFILES' "$temporary_directory/disabled.yaml"

gpu_profile=(
  --set gpu.enabled=true
  --set gpu.defaultProfile=nvidia-l4
  --set 'gpu.profiles[0].name=nvidia-l4'
  --set 'gpu.profiles[0].displayName=NVIDIA L4'
  --set 'gpu.profiles[0].description=One dedicated NVIDIA L4 GPU'
  --set-string 'gpu.profiles[0].resourceName=nvidia.com/gpu'
  --set 'gpu.profiles[0].count=1'
  --set-string 'gpu.profiles[0].workspaceImage=registry.example/devboxes-cuda:12.8'
  --set 'gpu.profiles[0].runtimeClassName=nvidia'
  --set 'gpu.profiles[0].supplementalGroups[0]=44'
  --set 'gpu.profiles[0].nodeSelector.accelerator=nvidia'
  --set-string 'gpu.profiles[0].tolerations[0].key=nvidia.com/gpu'
  --set 'gpu.profiles[0].tolerations[0].operator=Exists'
  --set 'gpu.profiles[0].tolerations[0].effect=NoSchedule'
)

helm template devboxes "$chart" --namespace devboxes "${gpu_profile[@]}" \
  >"$temporary_directory/enabled.yaml"
grep -Fq 'name: DEVBOXES_GPU_DEFAULT_PROFILE' "$temporary_directory/enabled.yaml"
grep -Fq 'value: "nvidia-l4"' "$temporary_directory/enabled.yaml"
grep -Fq 'name: DEVBOXES_GPU_PROFILES' "$temporary_directory/enabled.yaml"
grep -Fq 'nvidia.com/gpu' "$temporary_directory/enabled.yaml"
grep -Fq 'registry.example/devboxes-cuda:12.8' "$temporary_directory/enabled.yaml"
grep -Fq 'supplementalGroups' "$temporary_directory/enabled.yaml"

if helm template devboxes "$chart" --namespace devboxes \
  --set gpu.enabled=true \
  >"$temporary_directory/missing-default.yaml" \
  2>"$temporary_directory/missing-default.err"; then
  printf 'error: GPU render accepted an enabled feature without a default profile\n' >&2
  exit 1
fi
grep -Fq 'defaultProfile' "$temporary_directory/missing-default.err"

if helm template devboxes "$chart" --namespace devboxes \
  "${gpu_profile[@]}" \
  --set 'gpu.profiles[1].name=nvidia-l4' \
  --set 'gpu.profiles[1].displayName=Duplicate NVIDIA L4' \
  --set-string 'gpu.profiles[1].resourceName=nvidia.com/gpu' \
  --set 'gpu.profiles[1].count=1' \
  >"$temporary_directory/duplicate.yaml" \
  2>"$temporary_directory/duplicate.err"; then
  printf 'error: GPU render accepted duplicate profile names\n' >&2
  exit 1
fi
grep -Fq 'duplicate name' "$temporary_directory/duplicate.err"

if helm template devboxes "$chart" --namespace devboxes \
  "${gpu_profile[@]}" \
  --set gpu.defaultProfile=missing \
  >"$temporary_directory/missing-profile.yaml" \
  2>"$temporary_directory/missing-profile.err"; then
  printf 'error: GPU render accepted a default that is not configured\n' >&2
  exit 1
fi
grep -Fq 'must name an entry' "$temporary_directory/missing-profile.err"

if helm template devboxes "$chart" --namespace devboxes \
  "${gpu_profile[@]}" \
  --set-string 'gpu.profiles[0].resourceName=nvidia..com/gpu' \
  >"$temporary_directory/invalid-resource.yaml" \
  2>"$temporary_directory/invalid-resource.err"; then
  printf 'error: GPU render accepted an invalid extended resource\n' >&2
  exit 1
fi
grep -Fq 'resourceName' "$temporary_directory/invalid-resource.err"

if helm template devboxes "$chart" --namespace devboxes \
  "${gpu_profile[@]}" \
  --set-string 'gpu.profiles[0].workspaceImage=https://registry.example/devboxes-cuda:12.8' \
  >"$temporary_directory/invalid-image.yaml" \
  2>"$temporary_directory/invalid-image.err"; then
  printf 'error: GPU render accepted a workspace image URL scheme\n' >&2
  exit 1
fi
grep -Fq 'workspaceImage must not contain a URL scheme' \
  "$temporary_directory/invalid-image.err"

if helm template devboxes "$chart" --namespace devboxes \
  "${gpu_profile[@]}" \
  --set 'gpu.profiles[0].tolerations[0].value=unexpected' \
  >"$temporary_directory/invalid-toleration.yaml" \
  2>"$temporary_directory/invalid-toleration.err"; then
  printf 'error: GPU render accepted an Exists toleration with a value\n' >&2
  exit 1
fi
grep -Fq 'tolerations' "$temporary_directory/invalid-toleration.err"

printf 'Verified disabled, enabled, and invalid GPU Helm contracts.\n'
