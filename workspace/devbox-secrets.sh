#!/bin/sh

_devbox_export_secret() {
  _devbox_variable="$1"
  _devbox_file="/run/devbox-secrets/$2"
  if [ -s "$_devbox_file" ]; then
    _devbox_value="$(cat "$_devbox_file")"
    export "$_devbox_variable=$_devbox_value"
  fi
}

_devbox_export_secret GH_TOKEN GH_TOKEN
_devbox_export_secret OPENAI_API_KEY OPENAI_API_KEY
_devbox_export_secret CODEX_ACCESS_TOKEN CODEX_ACCESS_TOKEN
_devbox_export_secret CLAUDE_CODE_OAUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN
_devbox_export_secret ANTHROPIC_API_KEY ANTHROPIC_API_KEY

export GH_HOST=github.com
export DISABLE_AUTOUPDATER=1
if [ -r /run/devbox-name ]; then
  DEVBOX_NAME="$(cat /run/devbox-name)"
  export DEVBOX_NAME
fi
export RUSTUP_HOME=/usr/local/rustup
export CARGO_HOME="${HOME:-/home/dev}/.cargo"
export NPM_CONFIG_PREFIX="${HOME:-/home/dev}/.local"
export PATH="$CARGO_HOME/bin:$NPM_CONFIG_PREFIX/bin:/usr/local/cargo/bin:/usr/local/bin:$PATH"
unset -f _devbox_export_secret
unset _devbox_variable _devbox_file _devbox_value
