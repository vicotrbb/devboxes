#!/usr/bin/env bash
set -Eeuo pipefail

version="${DEVBOXES_VERSION:-${1:-}}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  printf 'usage: DEVBOXES_VERSION=X.Y.Z %s\n' "$0" >&2
  exit 1
fi

project_directory="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
temporary_directory="$(mktemp -d)"
trap 'rm -rf "$temporary_directory"' EXIT INT TERM

INSTALL_DIR="$temporary_directory/bin" \
DEVBOX_VERSION="$version" \
  "$project_directory/scripts/install-devbox-cli.sh"

cli_version="$("$temporary_directory/bin/devbox" --version)"
if [[ "$cli_version" != "devbox $version" ]]; then
  printf 'error: released CLI version mismatch: expected %s, found %s\n' \
    "devbox $version" "$cli_version" >&2
  exit 1
fi

DEVBOXES_E2E_PUBLISHED_VERSION="$version" \
DEVBOXES_E2E_CLI="$temporary_directory/bin/devbox" \
  "$project_directory/scripts/kind-e2e.sh"
