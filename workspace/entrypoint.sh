#!/usr/bin/env bash
set -Eeuo pipefail

HOME_DIR=/home/dev
SECRETS_DIR=/run/devbox-secrets
AUTHORIZED_KEYS="$SECRETS_DIR/SSH_AUTHORIZED_KEYS"
HOST_KEY_DIR="$HOME_DIR/.devbox/ssh"

log() {
  printf '[devbox] %s\n' "$*"
}

as_dev() {
  runuser -u dev -- env HOME="$HOME_DIR" USER=dev LOGNAME=dev SHELL=/bin/zsh "$@"
}

read_secret() {
  local path="$SECRETS_DIR/$1"
  local value=""
  if [[ -s "$path" ]]; then
    IFS= read -r value < "$path" || true
    printf '%s' "$value"
  fi
}

if [[ ! -s "$AUTHORIZED_KEYS" ]]; then
  log "SSH_AUTHORIZED_KEYS is missing or empty; refusing to start an inaccessible box"
  exit 1
fi

printf '%s\n' "${DEVBOX_NAME:-devbox}" > /run/devbox-name
chmod 0644 /run/devbox-name

mkdir -p \
  "$HOME_DIR" \
  "$HOME_DIR/.cargo/bin" \
  "$HOME_DIR/.local/bin" \
  "$HOME_DIR/.ssh" \
  "$HOST_KEY_DIR" \
  "$HOME_DIR/workspace"
install -o dev -g dev -m 0700 -d "$HOME_DIR/.devbox/insights"
install -o root -g root -m 0755 -d /run/sshd
chown dev:dev "$HOME_DIR"
chown -R dev:dev "$HOME_DIR/.cargo" "$HOME_DIR/.local" "$HOME_DIR/.ssh" "$HOME_DIR/workspace"

if [[ ! -f "$HOME_DIR/.devbox-initialized" ]]; then
  cp -a /etc/devbox/skel/. "$HOME_DIR/"
  touch "$HOME_DIR/.devbox-initialized"
  chown -R dev:dev "$HOME_DIR/.bashrc" "$HOME_DIR/.zshrc" "$HOME_DIR/.tmux.conf" "$HOME_DIR/.devbox-initialized"
fi
chown dev:dev "$HOME_DIR"

install -o dev -g dev -m 0700 -d "$HOME_DIR/.ssh"
install -o dev -g dev -m 0600 "$AUTHORIZED_KEYS" "$HOME_DIR/.ssh/authorized_keys"

install -o root -g root -m 0700 -d "$HOST_KEY_DIR"
if [[ ! -s "$HOST_KEY_DIR/ssh_host_ed25519_key" ]]; then
  ssh-keygen -q -t ed25519 -N '' -f "$HOST_KEY_DIR/ssh_host_ed25519_key"
fi
chmod 0600 "$HOST_KEY_DIR/ssh_host_ed25519_key"
chmod 0644 "$HOST_KEY_DIR/ssh_host_ed25519_key.pub"

git_name="$(read_secret GIT_USER_NAME)"
git_email="$(read_secret GIT_USER_EMAIL)"
if [[ -n "$git_name" ]]; then
  as_dev git config --global user.name "$git_name"
fi
if [[ -n "$git_email" ]]; then
  as_dev git config --global user.email "$git_email"
fi
as_dev git config --global init.defaultBranch main
as_dev git config --global credential.https://github.com.helper '!gh auth git-credential'
as_dev git config --global credential.https://gist.github.com.helper '!gh auth git-credential'

install -o dev -g dev -m 0700 -d "$HOME_DIR/.codex" "$HOME_DIR/.claude"
if [[ "${DEVBOXES_INSIGHTS_ENABLED:-false}" == true ]]; then
  install -o root -g root -m 0755 -d /etc/codex
  install -o root -g root -m 0644 \
    /usr/local/share/devboxes/codex-insights.toml \
    /etc/codex/config.toml
fi
if [[ ! -s "$HOME_DIR/.codex/auth.json" && -s "$SECRETS_DIR/CODEX_AUTH_JSON" ]]; then
  install -o dev -g dev -m 0600 "$SECRETS_DIR/CODEX_AUTH_JSON" "$HOME_DIR/.codex/auth.json"
elif [[ ! -s "$HOME_DIR/.codex/auth.json" && -s "$SECRETS_DIR/CODEX_ACCESS_TOKEN" ]]; then
  if ! as_dev /bin/bash -c 'codex login --with-access-token < /run/devbox-secrets/CODEX_ACCESS_TOKEN' >/tmp/codex-login.log 2>&1; then
    log "Codex access-token bootstrap failed; run 'codex login --device-auth' after connecting"
  fi
elif [[ ! -s "$HOME_DIR/.codex/auth.json" && -s "$SECRETS_DIR/OPENAI_API_KEY" ]]; then
  if ! as_dev /bin/bash -c 'codex login --with-api-key < /run/devbox-secrets/OPENAI_API_KEY' >/tmp/codex-login.log 2>&1; then
    log "Codex API-key bootstrap failed; run 'codex login --device-auth' after connecting"
  fi
fi

if [[ ! -s "$HOME_DIR/.claude/.credentials.json" && -s "$SECRETS_DIR/CLAUDE_CREDENTIALS_JSON" ]]; then
  install -o dev -g dev -m 0600 "$SECRETS_DIR/CLAUDE_CREDENTIALS_JSON" "$HOME_DIR/.claude/.credentials.json"
fi

if [[ -n "${DEVBOX_REPOSITORY:-}" && -z "$(find "$HOME_DIR/workspace" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  log "cloning $DEVBOX_REPOSITORY"
  repository_url="$DEVBOX_REPOSITORY"
  if [[ "$repository_url" != https://* ]]; then
    repository_url="https://github.com/${repository_url%.git}.git"
  fi
  if [[ -s "$SECRETS_DIR/GH_TOKEN" ]]; then
    # The single quotes intentionally defer expansion to the dev user's shell.
    # shellcheck disable=SC2016
    clone_command='source /etc/profile.d/devbox-secrets.sh; gh repo clone "$DEVBOX_REPOSITORY" "$HOME/workspace/project"'
  else
    # The single quotes intentionally defer expansion to the dev user's shell.
    # shellcheck disable=SC2016
    clone_command='git clone -- "$DEVBOX_REPOSITORY" "$HOME/workspace/project"'
  fi
  if ! as_dev env DEVBOX_REPOSITORY="$repository_url" /bin/bash -c "$clone_command"; then
    log "repository clone failed; the box will remain available with an empty workspace"
  fi
fi

chown -R dev:dev "$HOME_DIR/workspace" "$HOME_DIR/.codex" "$HOME_DIR/.claude"
log "${DEVBOX_NAME:-workspace} ready; starting SSH on port 2222"
exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config_devbox
