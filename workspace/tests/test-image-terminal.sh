#!/usr/bin/env bash
set -Eeuo pipefail

image="${1:-devboxes-workspace:local}"
container="devboxes-terminal-test-$RANDOM"
group_container="devboxes-gpu-group-test-$RANDOM"
temporary_directory="$(mktemp -d)"
cleanup() {
  docker rm -f "$container" "$group_container" >/dev/null 2>&1 || true
  rm -rf "$temporary_directory"
}
trap cleanup EXIT INT TERM

docker run -d --name "$container" --entrypoint /bin/bash "$image" -lc \
  'mkdir -p /home/dev/workspace && chown -R dev:dev /home/dev && exec sleep infinity' >/dev/null

for entry in xterm-ghostty xterm-256color screen-256color tmux-256color xterm vt100; do
  docker exec "$container" infocmp -x "$entry" >/dev/null
done

docker exec -dt -u dev -e TERM=xterm-ghostty -e COLORTERM=truecolor \
  "$container" /usr/local/bin/devbox-shell
for _ in {1..20}; do
  if docker exec -u dev "$container" tmux has-session -t main 2>/dev/null; then
    break
  fi
  sleep 0.25
done
docker exec -u dev "$container" tmux has-session -t main
docker exec -u dev "$container" tmux show-options -gv default-terminal | grep -Fx tmux-256color
docker exec -u dev "$container" tmux kill-server

unknown_output="$(
  docker exec -dt -u dev -e TERM=completely-unknown-future-terminal \
    "$container" /usr/local/bin/devbox-shell 2>&1
)"
for _ in {1..20}; do
  if docker exec -u dev "$container" tmux has-session -t main 2>/dev/null; then
    break
  fi
  sleep 0.25
done
docker exec -u dev "$container" tmux has-session -t main
if [[ "$unknown_output" == *'missing or unsuitable terminal'* ]]; then
  printf 'unknown TERM reproduced the historical tmux failure\n' >&2
  exit 1
fi

ssh-keygen -q -t ed25519 -N '' -f "$temporary_directory/id_ed25519"
docker run -d --name "$group_container" \
  --group-add 4242 \
  --group-add 4343 \
  --env DEVBOX_GPU_SUPPLEMENTAL_GROUPS=4242,4343 \
  --volume "$temporary_directory/id_ed25519.pub:/run/devbox-secrets/SSH_AUTHORIZED_KEYS:ro" \
  "$image" >/dev/null
for _ in {1..20}; do
  if docker exec "$group_container" runuser -u dev -- id -G >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
dev_groups="$(docker exec "$group_container" runuser -u dev -- id -G)"
grep -Eq '(^| )4242( |$)' <<< "$dev_groups"
grep -Eq '(^| )4343( |$)' <<< "$dev_groups"

printf 'Verified terminal handling and GPU device groups in %s.\n' "$image"
