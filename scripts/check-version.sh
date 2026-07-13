#!/bin/sh
set -eu

project_directory="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"

chart_version="$(awk '/^version:/ {print $2; exit}' "$project_directory/charts/devboxes/Chart.yaml")"
app_version="$(awk '/^appVersion:/ {gsub(/"/, "", $2); print $2; exit}' "$project_directory/charts/devboxes/Chart.yaml")"
cargo_version="$(awk -F '"' '/^version = / {print $2; exit}' "$project_directory/cli/Cargo.toml")"
cargo_lock_version="$(awk -F '"' '$0 == "name = \"devbox-cli\"" {found=1; next} found && /^version = / {print $2; exit}' "$project_directory/cli/Cargo.lock")"
python_version="$(awk -F '"' '/^version = / {print $2; exit}' "$project_directory/controller/pyproject.toml")"
package_version="$(awk -F '"' '/^__version__ = / {print $2; exit}' "$project_directory/controller/src/devboxes_controller/__init__.py")"
python_lock_version="$(awk -F '"' '$0 == "name = \"devboxes-controller\"" {found=1; next} found && /^version = / {print $2; exit}' "$project_directory/controller/uv.lock")"
npm_version="$(awk -F '"' '$2 == "version" {print $4; exit}' "$project_directory/package.json")"
npm_lock_versions="$(awk -F '"' '$2 == "version" {print $4; count++} count == 2 {exit}' "$project_directory/package-lock.json" | sort -u)"
# The single-quoted expression intentionally matches the literal shell default syntax.
# shellcheck disable=SC2016
installer_version="$(sed -n 's/^version="${DEVBOXES_VERSION:-\([0-9][0-9.]*\)}"$/\1/p' "$project_directory/scripts/install.sh")"
asset_versions="$(grep -hEo 'static/[^?]+\?v=[0-9]+\.[0-9]+\.[0-9]+' "$project_directory"/controller/src/devboxes_controller/templates/*.html | sed 's/.*?v=//' | sort -u)"
documented_versions="$(grep -hEo -- '--version [0-9]+\.[0-9]+\.[0-9]+' "$project_directory/README.md" "$project_directory"/docs/*.md | awk '{print $2}' | sort -u)"

for candidate in \
  "$app_version" \
  "$cargo_version" \
  "$cargo_lock_version" \
  "$python_version" \
  "$package_version" \
  "$python_lock_version" \
  "$npm_version" \
  "$npm_lock_versions" \
  "$installer_version" \
  "$documented_versions"; do
  if [ "$candidate" != "$chart_version" ]; then
    printf 'error: release versions do not match chart version %s; found %s\n' \
      "$chart_version" "$candidate" >&2
    exit 1
  fi
done
if [ "$asset_versions" != "$chart_version" ]; then
  printf 'error: static asset version does not match release version: %s\n' "$asset_versions" >&2
  exit 1
fi
if ! grep -Fq "## [$chart_version]" "$project_directory/CHANGELOG.md" \
  || ! grep -Fq "compare/v$chart_version...HEAD" "$project_directory/CHANGELOG.md"; then
  printf 'error: changelog does not contain the %s release and Unreleased comparison link\n' \
    "$chart_version" >&2
  exit 1
fi

printf 'Release versions match: %s\n' "$chart_version"
