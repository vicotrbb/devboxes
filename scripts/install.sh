#!/bin/sh
set -eu

release="${DEVBOXES_RELEASE:-devboxes}"
namespace="${DEVBOXES_NAMESPACE:-devboxes}"
version="${DEVBOXES_VERSION:-0.4.0}"
repository="${DEVBOXES_CHART_REPOSITORY:-oci://ghcr.io/vicotrbb/charts/devboxes}"
chart_source="${DEVBOXES_CHART_SOURCE:-auto}"
controller_secret="${DEVBOXES_CONTROLLER_SECRET:-devboxes-auth}"
workspace_secret="${DEVBOXES_WORKSPACE_SECRET:-devboxes-workspace}"

for command in kubectl helm; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'error: %s is required\n' "$command" >&2
    exit 1
  fi
done

script_directory="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
project_directory="$(dirname "$script_directory")"
chart="$repository"
version_arguments="--version $version"
case "$chart_source" in
  auto)
    if [ -f "$project_directory/charts/devboxes/Chart.yaml" ]; then
      chart="$project_directory/charts/devboxes"
      version_arguments=""
    fi
    ;;
  local)
    chart="$project_directory/charts/devboxes"
    version_arguments=""
    if [ ! -f "$chart/Chart.yaml" ]; then
      printf 'error: local chart not found at %s\n' "$chart" >&2
      exit 1
    fi
    ;;
  oci) ;;
  *)
    printf 'error: DEVBOXES_CHART_SOURCE must be auto, local, or oci\n' >&2
    exit 1
    ;;
esac

kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
if [ "${DEVBOXES_BOOTSTRAP_SECRETS:-0}" = 1 ] \
  || ! kubectl -n "$namespace" get secret "$controller_secret" >/dev/null 2>&1 \
  || ! kubectl -n "$namespace" get secret "$workspace_secret" >/dev/null 2>&1; then
  DEVBOXES_NAMESPACE="$namespace" "$script_directory/bootstrap-secrets.sh"
else
  printf 'Using existing Devboxes Secrets in namespace %s.\n' "$namespace"
fi

# Word splitting is intentional for the optional version arguments.
# shellcheck disable=SC2086
helm upgrade --install "$release" "$chart" \
  --namespace "$namespace" \
  --create-namespace \
  $version_arguments \
  --set-string controller.existingSecret="$controller_secret" \
  --set-string workspace.existingSecret="$workspace_secret" \
  --wait \
  --timeout 5m \
  "$@"

printf '\nDevboxes is installed. Follow the Helm notes above to connect.\n'
