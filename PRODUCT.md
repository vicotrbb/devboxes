# Product

## Register

product

## Users

Devboxes serves developers who need durable, prepared development environments and Kubernetes
operators who make those environments safe and supportable. Developers work from the CLI or
browser workbench and expect fast creation, reliable SSH reconnection, and explicit data
lifecycle behavior. Operators configure cluster policy, credentials, storage, networking,
images, accelerators, tenancy, and capacity without exposing raw Kubernetes controls to users.

## Product Purpose

Devboxes turns approved Kubernetes capacity into ready-to-use development machines. Compute is
disposable, `/home/dev` is durable until an explicit purge, and the same lifecycle contract is
available through the API, CLI, and dashboard. Success means a developer can understand and
control a workspace without learning Kubernetes internals while an operator can prove which
policy, identity, namespace, credentials, and resources govern it.

## Brand Personality

Clear, operational, trustworthy. The product speaks directly, makes destructive behavior
unmistakable, and presents cluster state without hype or false certainty.

## Anti-references

Devboxes should not resemble a decorative SaaS control panel, a consumer cloud drive, or a
generic generated admin template. Avoid hidden automation, ornamental metrics, vague resource
labels, invented interaction patterns, and UI-only security controls. Do not expose arbitrary
Kubernetes fields merely to make a workflow look flexible.

## Design Principles

1. Keep operator policy separate from user intent. Users select stable, named capabilities;
   operators own namespaces, credentials, scheduling, quotas, and trusted catalogs.
2. Make persistence and destructive actions explicit. Stop, delete, and purge must remain
   visibly distinct in every interface.
3. Preserve terminal, API, and dashboard parity. The same identity, tenancy, capability, and
   lifecycle contract should be discoverable everywhere.
4. Show real state and useful recovery paths. Pending capacity, policy rejection, degraded
   workloads, and retained storage should be described honestly and specifically.
5. Prefer familiar, accessible controls over novelty. Keyboard operation, visible focus,
   semantic markup, reduced motion, readable contrast, and responsive task flows are baseline
   product behavior.

## Accessibility & Inclusion

Preserve the existing keyboard-first and screen-reader-aware interface conventions: skip links,
semantic headings and tables, explicit labels, live regions, focus restoration, and native
dialogs and controls. State must never depend on color alone. Maintain readable contrast,
usable narrow-screen layouts, and reduced-motion behavior across all authenticated surfaces.
