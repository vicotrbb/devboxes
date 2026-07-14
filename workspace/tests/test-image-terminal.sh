#!/usr/bin/env bash
set -Eeuo pipefail

image="${1:-devboxes-workspace:local}"
container="devboxes-terminal-test-$RANDOM"
trap 'docker rm -f "$container" >/dev/null 2>&1 || true' EXIT INT TERM

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

printf 'Verified xterm-ghostty and unknown TERM keep tmux alive in %s.\n' "$image"
