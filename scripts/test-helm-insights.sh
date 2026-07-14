#!/usr/bin/env bash
set -Eeuo pipefail

project_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
chart="$project_directory/charts/devboxes"
temporary_directory="$(mktemp -d)"
trap 'rm -rf "$temporary_directory"' EXIT

helm lint "$chart" --strict

helm template devboxes "$chart" --namespace devboxes \
  >"$temporary_directory/disabled.yaml"
grep -Fq 'type: RollingUpdate' "$temporary_directory/disabled.yaml"
if grep -Fq 'app.kubernetes.io/component: insights' "$temporary_directory/disabled.yaml"; then
  printf 'error: disabled render unexpectedly created Insights storage\n' >&2
  exit 1
fi

helm template devboxes "$chart" --namespace devboxes \
  --set insights.enabled=true \
  >"$temporary_directory/enabled.yaml"
grep -Fq 'kind: PersistentVolumeClaim' "$temporary_directory/enabled.yaml"
grep -Fq 'app.kubernetes.io/component: insights' "$temporary_directory/enabled.yaml"
grep -Fq 'type: Recreate' "$temporary_directory/enabled.yaml"
grep -Fq 'mountPath: /var/lib/devboxes' "$temporary_directory/enabled.yaml"
grep -Fq 'readOnlyRootFilesystem: true' "$temporary_directory/enabled.yaml"
grep -Fq 'name: DEVBOXES_INSIGHTS_ENABLED' "$temporary_directory/enabled.yaml"

helm template devboxes "$chart" --namespace devboxes \
  --set insights.enabled=true \
  --set insights.storage.existingClaim=existing-insights \
  >"$temporary_directory/existing.yaml"
grep -Fq 'claimName: existing-insights' "$temporary_directory/existing.yaml"
if grep -Fq 'app.kubernetes.io/component: insights' "$temporary_directory/existing.yaml"; then
  printf 'error: existingClaim render unexpectedly created an Insights PVC\n' >&2
  exit 1
fi

for access_mode in ReadWriteOnce ReadWriteOncePod; do
  helm template devboxes "$chart" --namespace devboxes \
    --set insights.enabled=true \
    --set "insights.storage.accessMode=$access_mode" \
    >"$temporary_directory/$access_mode.yaml"
  grep -Fq -- "- $access_mode" "$temporary_directory/$access_mode.yaml"
done

if helm template devboxes "$chart" --namespace devboxes \
  --set insights.enabled=true \
  --set controller.replicaCount=2 \
  >"$temporary_directory/invalid.yaml" 2>"$temporary_directory/invalid.err"; then
  printf 'error: Insights render accepted multiple controller replicas\n' >&2
  exit 1
fi
grep -Fq 'controller.replicaCount must be exactly 1' "$temporary_directory/invalid.err"

printf 'Verified disabled and enabled Insights Helm contracts.\n'
