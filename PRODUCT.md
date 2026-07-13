# Product

## Register

product

## Users

Devboxes is for developers and self-hosting operators who want disposable, fully prepared development environments on Kubernetes. The primary job is to create an environment in seconds, enter it over SSH, and return to the same tmux session after a disconnect without reconstructing tools or authentication. Each installation currently uses a single shared operator identity and access token.

## Product Purpose

Devboxes turns spare capacity in any conformant Kubernetes cluster into ready-to-use development machines. Success means the terminal workflow is faster than manually preparing a local checkout, every box starts with Rust, Node.js, Python, GitHub CLI, Codex, and Claude Code tooling, and the dashboard makes lifecycle, health, age, resource use, and connection details immediately understandable.

The terminal is the primary control surface. The browser dashboard is the operational companion for visibility, creation, lifecycle control, and recovery when a terminal command is not the most convenient option.

## Brand Personality

Technical, calm, and tactile. The product should feel like dependable workshop equipment: direct, legible, precise, and satisfying to operate without becoming theatrical.

## Anti-references

- Generic SaaS dashboards made from endless identical cards.
- Neon hacker-terminal aesthetics, glowing cyberpunk decoration, and fake command-line theater.
- Decorative glassmorphism, oversized metrics, or motion that delays the task.
- Unfamiliar controls invented for personality when a standard control is clearer.
- Interfaces that hide operational truth behind optimistic status copy.

## Design Principles

1. Terminal first, browser complete. Every core lifecycle action must work from the Rust CLI; the dashboard must expose the same underlying state and actions.
2. Show operational truth. Status, readiness, expiry, persistence, connection coordinates, and failures must be explicit and current.
3. Ready means ready. A created box must arrive with its toolchains, credentials, workspace, SSH access, and tmux session prepared.
4. Safe ephemerality. Destructive actions must clearly distinguish stopping compute from deleting the persisted workspace.
5. Familiar controls, deliberate character. Use standard product interaction patterns and earn personality through typography, material, copy, and precise feedback.

## Accessibility & Inclusion

Target WCAG 2.2 AA. All workflows must be keyboard accessible, focus must remain visible, body text and controls must maintain AA contrast, status cannot depend on color alone, and all non-essential motion must respect reduced-motion preferences.
