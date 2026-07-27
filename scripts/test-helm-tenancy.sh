#!/usr/bin/env bash
set -Eeuo pipefail

project_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
chart="$project_directory/charts/devboxes"
values="$project_directory/scripts/fixtures/tenancy-values.yaml"
temporary_directory="$(mktemp -d)"
trap 'rm -rf "$temporary_directory"' EXIT

helm lint "$chart" --strict

helm template devboxes "$chart" --namespace devboxes \
  >"$temporary_directory/disabled.yaml"
grep -Fq 'name: DEVBOXES_MULTI_TENANCY_ENABLED' "$temporary_directory/disabled.yaml"
grep -Fq 'value: "false"' "$temporary_directory/disabled.yaml"
if grep -Fq 'kind: Namespace' "$temporary_directory/disabled.yaml"; then
  printf 'error: disabled tenancy rendered tenant namespaces\n' >&2
  exit 1
fi

helm template devboxes "$chart" --namespace devboxes -f "$values" \
  >"$temporary_directory/enabled.yaml"
grep -Fq 'name: devboxes-platform' "$temporary_directory/enabled.yaml"
grep -Fq 'name: devboxes-research' "$temporary_directory/enabled.yaml"
grep -Fq 'kind: ResourceQuota' "$temporary_directory/enabled.yaml"
grep -Fq 'kind: NetworkPolicy' "$temporary_directory/enabled.yaml"
grep -Fq 'port: 2222' "$temporary_directory/enabled.yaml"
grep -Fq 'namespace: devboxes-platform' "$temporary_directory/enabled.yaml"
grep -Fq 'namespace: devboxes-research' "$temporary_directory/enabled.yaml"
grep -Fq 'name: DEVBOXES_TENANTS' "$temporary_directory/enabled.yaml"
grep -Fq 'name: DEVBOXES_PRINCIPALS' "$temporary_directory/enabled.yaml"
grep -Fq 'secretName: devboxes-principals' "$temporary_directory/enabled.yaml"
grep -Fq 'http://devboxes.devboxes.svc:8000' "$temporary_directory/enabled.yaml"
grep -Fq 'devboxes.bonalab.org/tenant: "platform"' "$temporary_directory/enabled.yaml"

if helm template devboxes "$chart" --namespace devboxes -f "$values" \
  --set tenancy.defaultTenant=missing \
  >"$temporary_directory/missing-default.yaml" \
  2>"$temporary_directory/missing-default.err"; then
  printf 'error: tenancy accepted an unknown default tenant\n' >&2
  exit 1
fi
grep -Fq 'defaultTenant' "$temporary_directory/missing-default.err"

if helm template devboxes "$chart" --namespace devboxes -f "$values" \
  --set 'tenancy.tenants[1].id=platform' \
  >"$temporary_directory/duplicate.yaml" \
  2>"$temporary_directory/duplicate.err"; then
  printf 'error: tenancy accepted a duplicate tenant ID\n' >&2
  exit 1
fi
grep -Fq 'duplicate id' "$temporary_directory/duplicate.err"

if helm template devboxes "$chart" --namespace devboxes -f "$values" \
  --set tenancy.existingPrincipalSecret= \
  >"$temporary_directory/missing-secret.yaml" \
  2>"$temporary_directory/missing-secret.err"; then
  printf 'error: tenancy accepted principals without a credential Secret\n' >&2
  exit 1
fi
grep -Fq 'existingPrincipalSecret' "$temporary_directory/missing-secret.err"

if helm template devboxes "$chart" --namespace devboxes -f "$values" \
  --set-string 'tenancy.tenants[0].namespaceLabels.kubernetes\\.io/metadata\\.name=hijack' \
  >"$temporary_directory/reserved-namespace-label.yaml" \
  2>"$temporary_directory/reserved-namespace-label.err"; then
  printf 'error: tenancy accepted a reserved Kubernetes namespace label\n' >&2
  exit 1
fi
grep -Fq 'invalid propertyName' "$temporary_directory/reserved-namespace-label.err"

if helm template devboxes "$chart" --namespace devboxes -f "$values" \
  --set 'tenancy.principals[0].memberships[1].tenant=missing' \
  >"$temporary_directory/missing-membership.yaml" \
  2>"$temporary_directory/missing-membership.err"; then
  printf 'error: tenancy accepted a membership for an unknown tenant\n' >&2
  exit 1
fi
grep -Fq 'unknown tenant' "$temporary_directory/missing-membership.err"

if helm template devboxes "$chart" --namespace devboxes -f "$values" \
  --set 'gpu.profiles[0].name=nvidia-l4' \
  --set 'gpu.profiles[0].displayName=NVIDIA L4' \
  --set-string 'gpu.profiles[0].resourceName=nvidia.com/gpu' \
  --set 'gpu.profiles[0].count=1' \
  --set 'tenancy.tenants[0].defaultGpuProfile=nvidia-l4' \
  >"$temporary_directory/disabled-gpu-default.yaml" \
  2>"$temporary_directory/disabled-gpu-default.err"; then
  printf 'error: tenancy accepted a tenant GPU default while GPU support is disabled\n' >&2
  exit 1
fi
grep -Fq 'requires gpu.enabled=true' "$temporary_directory/disabled-gpu-default.err"

if helm template devboxes "$chart" --namespace devboxes -f "$values" \
  --set gpu.enabled=true \
  --set gpu.defaultProfile=nvidia-l4 \
  --set 'gpu.profiles[0].name=nvidia-l4' \
  --set 'gpu.profiles[0].displayName=NVIDIA L4' \
  --set-string 'gpu.profiles[0].resourceName=nvidia.com/gpu' \
  --set 'gpu.profiles[0].count=1' \
  --set 'tenancy.tenants[0].defaultGpuProfile=missing' \
  >"$temporary_directory/missing-gpu-default.yaml" \
  2>"$temporary_directory/missing-gpu-default.err"; then
  printf 'error: tenancy accepted an unknown tenant GPU default\n' >&2
  exit 1
fi
grep -Fq 'default GPU profile' "$temporary_directory/missing-gpu-default.err"

printf 'Verified disabled, isolated, and invalid multi-tenancy Helm contracts.\n'
