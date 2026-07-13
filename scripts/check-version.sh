#!/bin/sh
set -eu

project_directory="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"

chart_version="$(awk '/^version:/ {print $2; exit}' "$project_directory/charts/devboxes/Chart.yaml")"
app_version="$(awk '/^appVersion:/ {gsub(/\"/, "", $2); print $2; exit}' "$project_directory/charts/devboxes/Chart.yaml")"
cargo_version="$(awk -F '"' '/^version = / {print $2; exit}' "$project_directory/cli/Cargo.toml")"
python_version="$(awk -F '"' '/^version = / {print $2; exit}' "$project_directory/controller/pyproject.toml")"
package_version="$(awk -F '"' '/^__version__ = / {print $2; exit}' "$project_directory/controller/src/devboxes_controller/__init__.py")"
asset_versions="$(grep -hEo 'static/[^?]+\?v=[0-9]+\.[0-9]+\.[0-9]+' "$project_directory"/controller/src/devboxes_controller/templates/*.html | sed 's/.*?v=//' | sort -u)"

for candidate in "$app_version" "$cargo_version" "$python_version" "$package_version"; do
  if [ "$candidate" != "$chart_version" ]; then
    printf 'error: release versions do not match: chart=%s app=%s cargo=%s python=%s package=%s\n' \
      "$chart_version" "$app_version" "$cargo_version" "$python_version" "$package_version" >&2
    exit 1
  fi
done
if [ "$asset_versions" != "$chart_version" ]; then
  printf 'error: static asset version does not match release version: %s\n' "$asset_versions" >&2
  exit 1
fi

printf 'Release versions match: %s\n' "$chart_version"
