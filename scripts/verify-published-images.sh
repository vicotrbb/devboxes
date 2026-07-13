#!/usr/bin/env bash
set -Eeuo pipefail

version="${DEVBOXES_VERSION:-${1:-}}"
repository="${GITHUB_REPOSITORY:-vicotrbb/devboxes}"
owner="${repository%%/*}"
temporary_directory="$(mktemp -d)"
trap 'rm -rf "$temporary_directory"' EXIT INT TERM
mkdir "$temporary_directory/docker"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  printf 'usage: DEVBOXES_VERSION=X.Y.Z %s\n' "$0" >&2
  exit 1
fi

for command in docker jq gh; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'error: %s is required\n' "$command" >&2
    exit 1
  fi
done

for component in controller workspace; do
  image="ghcr.io/$owner/devboxes-$component:$version"
  manifest="$(docker buildx imagetools inspect "$image" --raw)"
  index_digest="$(docker buildx imagetools inspect "$image" --format '{{json .Manifest}}' | jq -r .digest)"

  jq -e '
    [.manifests[] | select(.platform.os == "linux") | .platform.architecture]
    | sort == ["amd64", "arm64"]
  ' <<<"$manifest" >/dev/null

  attestations="$(
    jq -r '.manifests[]
      | select(.annotations["vnd.docker.reference.type"] == "attestation-manifest")
      | .digest' <<<"$manifest"
  )"
  attestation_count="$(awk 'NF {count++} END {print count + 0}' <<<"$attestations")"
  if [[ "$attestation_count" -ne 2 ]]; then
    printf 'error: expected two platform attestation manifests for %s\n' "$image" >&2
    exit 1
  fi
  while IFS= read -r attestation; do
    docker buildx imagetools inspect "ghcr.io/$owner/devboxes-$component@$attestation" --raw \
      | jq -e '
          [.layers[].annotations["in-toto.io/predicate-type"]]
          | contains(["https://spdx.dev/Document", "https://slsa.dev/provenance/v1"])
        ' >/dev/null
  done <<<"$attestations"

  DOCKER_CONFIG="$temporary_directory/docker" docker pull "$image" >/dev/null
  DOCKER_CONFIG="$temporary_directory/docker" docker image rm "$image" >/dev/null
  gh attestation verify "oci://ghcr.io/$owner/devboxes-$component@$index_digest" \
    --repo "$repository" \
    --cert-identity "https://github.com/$repository/.github/workflows/release.yml@refs/tags/v$version" \
    --source-ref "refs/tags/v$version" \
    --format json \
    | jq -e 'length > 0' >/dev/null

  printf 'Verified anonymous pull, amd64/arm64 manifests, SPDX SBOMs, and signed provenance for %s@%s.\n' \
    "$image" "$index_digest"
done
