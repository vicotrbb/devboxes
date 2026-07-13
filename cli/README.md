# Devbox CLI

The `devbox` terminal client creates, inspects, connects to, stops, and deletes development environments managed by a Devboxes installation.

```console
cargo install --locked --git https://github.com/vicotrbb/devboxes devbox-cli
devbox login --url https://devboxes.example.com
devbox create atlas --repo owner/project --ssh
```

See the [CLI reference](../docs/cli.md) for every command, option, environment variable, output contract, and SSH workflow. The [golden path](../docs/golden-path.md) covers the recommended installation and performance setup.
