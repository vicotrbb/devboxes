# Security policy

## Supported versions

Security fixes are provided for the latest released minor version. During the `0.x` series, upgrade to the newest release before reporting a bug that may already be fixed.

## Report a vulnerability

Do not open a public issue. Use a private [GitHub security advisory](https://github.com/vicotrbb/devboxes/security/advisories/new) with:

- affected version and component;
- deployment topology and Kubernetes version;
- reproduction steps or proof of concept;
- expected and observed impact;
- any suggested mitigation.

You should receive an acknowledgement within seven days. Please allow maintainers time to investigate, prepare a fix, and coordinate disclosure. No bug-bounty program is currently offered.

## Security model

Devboxes assumes a trusted single operator or trusted operator group. The shared access token can control and permanently purge every devbox in an installation. Workspaces are development machines with passwordless `sudo`; they are not sandboxes for untrusted code or mutually untrusted users.

Recommended deployment controls:

- expose the controller only through HTTPS or local port-forwarding;
- restrict controller ingress and SSH Services to trusted client networks;
- use a 256-bit or stronger controller token and rotate it after suspected exposure;
- use fine-grained, least-privilege provider tokens;
- enable Kubernetes Secret encryption at rest and an auditable secret manager;
- enforce the chart's namespace-scoped RBAC and tokenless workspace service account;
- back up important PVCs and test restore procedures;
- keep Kubernetes, ingress, CSI, images, chart, and CLI releases current;
- verify release checksums and image provenance attestations.

The CLI refuses plaintext controller URLs except exact loopback hosts. Browser login uses an
external browser, a `127.0.0.1` ephemeral callback, state validation, PKCE S256, one-time
authorization codes, and expiring scoped tokens. It has no skip-TLS-verification option.
Workspace SSH disables password, keyboard-interactive, and root login and uses persistent
Ed25519 host keys. Incoming terminal names are validated against installed terminfo before
tmux starts.

## Dependency and supply-chain policy

Dependabot monitors npm, Cargo, Python, Docker, and GitHub Actions dependencies. Pull requests receive dependency review, and CI audits npm, Python, and Cargo dependencies, builds both images, performs strict JavaScript, documentation, controller, and CLI checks, validates Helm output, and installs into a clean Kind cluster. Release images are published for amd64 and arm64 with GitHub artifact attestations; CLI archives include SHA-256 checksums.
