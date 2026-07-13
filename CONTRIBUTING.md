# Contributing

Thank you for helping make Devboxes safer and easier to run on ordinary Kubernetes clusters.

## Before you start

- Use GitHub Discussions for design questions and support requests.
- Search existing issues before filing a bug or feature request.
- Open an issue before a large architectural change so maintainers and contributors can align on scope.
- Report vulnerabilities through a private GitHub security advisory as described in [SECURITY.md](SECURITY.md).

By participating, you agree to follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) and license your contribution under Apache-2.0.

## Development workflow

1. Fork the repository and create a focused branch.
2. Read [docs/development.md](docs/development.md) and the relevant architecture or configuration documentation.
3. Add or update tests for behavioral changes.
4. Keep controller, CLI, Helm, dashboard, and documentation contracts synchronized.
5. Write direct technical prose without em dash punctuation. Prefer commas, periods, or explicit connecting words.
6. Run the local gates:

   ```bash
   make bootstrap
   make lint
   make test
   make helm
   ```

7. Describe the user-visible behavior, risks, migration impact, and validation evidence in the pull request.

## Engineering expectations

- Preserve explicit persistence semantics: TTL and stop never delete a PVC.
- Keep Kubernetes permissions namespace-scoped and workspace service accounts tokenless.
- Do not add plaintext credentials, example secrets, telemetry, or external calls without explicit user control.
- Maintain both `LoadBalancer` and `NodePort` SSH paths.
- Keep the CLI usable in scripts: errors go to stderr, JSON remains stable within a release line, and tokens should not be required on command lines.
- Keep UI workflows fully keyboard accessible, WCAG 2.2 AA, status-text-first, and compatible with reduced motion.
- Keep browser JavaScript dependency-light, lint-clean, progressively enhanced, and free of inline event handlers.
- Keep public Python modules, classes, functions, and methods documented and strictly typed.
- Keep controller test coverage at or above the enforced 85 percent project threshold.
- Keep Rust clean under formatting, standard warnings, Clippy `all`, `pedantic`, and `nursery` lint groups.
- Prefer small, reviewable changes over unrelated cleanup.

## Pull requests

Pull requests should be narrow enough to review and must pass CI. Maintainers may ask for a clean Kind install when packaging, RBAC, resource generation, or startup behavior changes. Breaking changes require documentation in `CHANGELOG.md` and a versioning decision before merge.

Generated files such as `Cargo.lock` and `controller/uv.lock` are committed and must be updated with their manifests.

## Commit messages

Use an imperative subject that describes the outcome, for example:

```text
Add NodePort endpoint discovery
Harden workspace Secret mounts
Document external secret operators
```
