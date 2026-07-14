#!/usr/bin/env bash
set -Eeuo pipefail

project_directory="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
shell_under_test="$project_directory/workspace/devbox-shell"
temporary_directory="$(mktemp -d)"
trap 'rm -rf "$temporary_directory"' EXIT INT TERM

mkdir -p "$temporary_directory/bin" "$temporary_directory/home/workspace"

cat >"$temporary_directory/bin/infocmp" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
candidate="${*: -1}"
case "$candidate" in
  xterm-ghostty | xterm-256color | screen-256color | tmux-256color | xterm | vt100 | xterm-kitty | wezterm | alacritty | iTerm2.app | foot | foot-direct | cygwin | screen | tmux)
    exit 0
    ;;
  *)
    exit 1
    ;;
esac
EOF

cat >"$temporary_directory/bin/tmux" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'TERM=%s\nDEVBOX_ORIGINAL_TERM=%s\nCOLORTERM=%s\nARGS=%s\n' \
  "${TERM-}" "${DEVBOX_ORIGINAL_TERM-}" "${COLORTERM-}" "$*" >"$DEVBOX_TEST_RESULT"
EOF

chmod +x "$temporary_directory/bin/infocmp" "$temporary_directory/bin/tmux"

run_case() {
  local received_term="$1"
  local expected_term="$2"
  local expected_original="$3"
  local expected_warning="$4"
  local result="$temporary_directory/result"
  local stderr="$temporary_directory/stderr"
  rm -f "$result" "$stderr"

  TERM="$received_term" \
  COLORTERM=truecolor \
  HOME="$temporary_directory/home" \
  PATH="$temporary_directory/bin:/usr/bin:/bin" \
  DEVBOX_TEST_RESULT="$result" \
    bash "$shell_under_test" 2>"$stderr"

  grep -Fx "TERM=$expected_term" "$result" >/dev/null
  grep -Fx "DEVBOX_ORIGINAL_TERM=$expected_original" "$result" >/dev/null
  grep -Fx 'COLORTERM=truecolor' "$result" >/dev/null
  grep -Fx "ARGS=new-session -A -s main -c $temporary_directory/home/workspace" "$result" >/dev/null
  if [[ -n "$expected_warning" ]]; then
    [[ "$(wc -l <"$stderr" | tr -d ' ')" == 1 ]]
    grep -Fx "$expected_warning" "$stderr" >/dev/null
  else
    [[ ! -s "$stderr" ]]
  fi
}

for known_term in \
  xterm-ghostty xterm-256color xterm-kitty wezterm alacritty iTerm2.app \
  foot foot-direct cygwin screen screen-256color tmux tmux-256color; do
  run_case "$known_term" "$known_term" "$known_term" ""
done

run_case "" xterm-256color "" \
  'devbox: terminal <empty> is unavailable; using xterm-256color'
run_case completely-unknown-future-terminal xterm-256color \
  completely-unknown-future-terminal \
  'devbox: terminal completely-unknown-future-terminal is unavailable; using xterm-256color'
run_case $'bad\033term' xterm-256color $'bad\033term' \
  'devbox: terminal <invalid> is unavailable; using xterm-256color'
run_case -option xterm-256color -option \
  'devbox: terminal <invalid> is unavailable; using xterm-256color'
run_case "$(printf 'a%.0s' {1..65})" xterm-256color "$(printf 'a%.0s' {1..65})" \
  'devbox: terminal <invalid> is unavailable; using xterm-256color'

if grep -Fq 'missing or unsuitable terminal: xterm-ghostty' "$temporary_directory/stderr"; then
  printf 'xterm-ghostty regression returned the historical tmux failure\n' >&2
  exit 1
fi

printf 'Verified known, unknown, empty, and untrusted TERM handling reaches tmux.\n'
