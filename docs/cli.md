# CLI reference

The `devbox` CLI is the primary daily interface. It stores one controller profile, calls the authenticated HTTP API, waits for readiness when requested, and delegates interactive connections to the local OpenSSH client.

## Installation

Install a checksummed release binary:

```bash
curl -fsSLO https://raw.githubusercontent.com/vicotrbb/devboxes/main/scripts/install-devbox-cli.sh
less install-devbox-cli.sh
sh install-devbox-cli.sh
```

Or build from source with the committed lockfile:

```bash
cargo install --locked --git https://github.com/vicotrbb/devboxes devbox-cli
```

Release binaries support macOS and Linux on AMD64 and ARM64.

## Global options

| Option | Meaning |
| --- | --- |
| `--url URL` | Override the controller URL for this invocation |
| `--token TOKEN` | Override the access token, prefer `DEVBOX_TOKEN` to avoid shell history |
| `--json` | Print machine-readable JSON for supported read and lifecycle commands |
| `--help` | Show command help |
| `--version` | Show the CLI version |

The CLI accepts HTTPS controller URLs. Plain HTTP is allowed only for exact loopback hosts such as `127.0.0.1`, `::1`, and `localhost`. URLs containing credentials, query strings, or fragments are rejected.

## Configuration and precedence

Run login once per profile:

```bash
devbox login --url https://devboxes.example.com
```

The CLI verifies the token through `/api/v1/whoami`, then writes `config.toml` with mode `0600` on Unix. The default location is the operating system configuration directory under `devbox/config.toml`.

Values resolve in this order:

1. Command options, `--url` and `--token`.
2. Environment variables, `DEVBOX_URL` and `DEVBOX_TOKEN`.
3. The saved configuration file.

Set `DEVBOX_CONFIG` to select another configuration file. This is useful for multiple clusters and automation:

```bash
DEVBOX_CONFIG="$HOME/.config/devbox/lab.toml" devbox list --json
```

Never pass tokens through shared scripts, process listings, logs, or committed files.

## Commands

### `devbox login`

Store and verify controller credentials.

```bash
devbox login --url https://devboxes.example.com
```

The token is prompted without terminal echo unless `--token` or `DEVBOX_TOKEN` supplies it.

### `devbox create NAME`

Create a devbox. Names contain 1 to 40 lowercase letters, digits, or hyphens, and must start and end with a letter or digit.

```bash
devbox create atlas --preset medium --ttl 24 --repo owner/project --ssh
```

| Option | Meaning |
| --- | --- |
| `--preset small\|medium\|large` | Resource and storage preset, default `small` |
| `--ttl HOURS` | Auto-stop interval from 1 to 168 hours, default 24 |
| `--repo OWNER/REPOSITORY` | Clone a GitHub repository on first boot |
| `--no-wait` | Return after the API accepts the request |
| `--ssh` | Wait for readiness, then connect |

`--ssh` takes precedence over `--no-wait` because an SSH connection requires readiness.

### `devbox list`

List boxes sorted by creation time, newest first.

```bash
devbox list
devbox list --json
```

### `devbox status NAME`

Show state, preset, storage, expiry, repository, SSH address, and any readiness message.

```bash
devbox status atlas
devbox status atlas --json
```

### `devbox ssh NAME`

Connect as `dev`, verify the persistent host key, and attach to the `main` tmux session.

```bash
devbox ssh atlas
devbox ssh atlas -- -L 3000:127.0.0.1:3000
devbox ssh atlas -- -A
```

Arguments after `--` are passed to OpenSSH before the destination. The CLI sets a host-key alias scoped to the controller installation and devbox name, uses `StrictHostKeyChecking=accept-new`, and sends keepalives every 30 seconds.

### `devbox stop NAME`

Scale compute to zero while retaining `/home/dev` and the SSH host identity.

```bash
devbox stop atlas
```

### `devbox start NAME`

Start retained compute and renew the original TTL from the current time.

```bash
devbox start atlas
devbox ssh atlas
```

### `devbox delete NAME`

Delete the Deployment and Service while retaining the home PVC by default.

```bash
devbox delete atlas
```

Permanently delete the home volume only with explicit purge confirmation:

```bash
devbox delete atlas --purge
devbox delete atlas --purge --yes
```

Use `--yes` only in automation where permanent data deletion is intended.

## Output and scripting

Human-readable results go to stdout. Progress and connection-wait messages go to stderr. Failures return a nonzero exit status. `--json` emits formatted JSON for list, status, create, start, and stop workflows, which can be consumed with `jq`:

```bash
devbox list --json | jq -r '.[] | select(.state == "ready") | .name'
```

Treat field additions as compatible. Before `v1.0`, documented breaking changes may occur in minor releases. Pin the CLI with the chart and controller release in production automation.
