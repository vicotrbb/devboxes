#!/bin/sh
set -eu

repository="${DEVBOX_REPOSITORY:-vicotrbb/devboxes}"
version="${DEVBOX_VERSION:-latest}"
install_dir="${INSTALL_DIR:-$HOME/.local/bin}"

require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'error: %s is required\n' "$1" >&2
    exit 1
  fi
}

require curl
require tar

case "$(uname -s)" in
  Darwin) platform="apple-darwin" ;;
  Linux) platform="unknown-linux-gnu" ;;
  *)
    printf 'error: unsupported operating system: %s\n' "$(uname -s)" >&2
    exit 1
    ;;
esac

case "$(uname -m)" in
  x86_64 | amd64) architecture="x86_64" ;;
  arm64 | aarch64) architecture="aarch64" ;;
  *)
    printf 'error: unsupported architecture: %s\n' "$(uname -m)" >&2
    exit 1
    ;;
esac

if [ "$version" = "latest" ]; then
  release_url="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$repository/releases/latest")"
  version="$(basename "$release_url")"
fi
case "$version" in
  v*) ;;
  *) version="v$version" ;;
esac

target="$architecture-$platform"
archive="devbox-$target.tar.gz"
checksum="devbox-$target.sha256"
base_url="https://github.com/$repository/releases/download/$version"
temporary_directory="$(mktemp -d)"
trap 'rm -rf "$temporary_directory"' EXIT HUP INT TERM

printf 'Downloading devbox %s for %s…\n' "$version" "$target"
curl -fsSL "$base_url/$archive" -o "$temporary_directory/$archive"
curl -fsSL "$base_url/$checksum" -o "$temporary_directory/$checksum"

expected="$(awk -v archive="$archive" '$2 == archive {print $1}' "$temporary_directory/$checksum")"
if [ -z "$expected" ]; then
  printf 'error: checksum file does not contain %s\n' "$archive" >&2
  exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then
  actual="$(sha256sum "$temporary_directory/$archive" | awk '{print $1}')"
else
  require shasum
  actual="$(shasum -a 256 "$temporary_directory/$archive" | awk '{print $1}')"
fi
if [ "$expected" != "$actual" ]; then
  printf 'error: checksum verification failed for %s\n' "$archive" >&2
  exit 1
fi

mkdir -p "$install_dir"
tar -xzf "$temporary_directory/$archive" -C "$temporary_directory"
install -m 0755 "$temporary_directory/devbox" "$install_dir/devbox"

printf 'Installed devbox to %s/devbox\n' "$install_dir"
case ":$PATH:" in
  *":$install_dir:"*) ;;
  *) printf 'Add %s to PATH before running devbox.\n' "$install_dir" ;;
esac
