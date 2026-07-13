---
name: Devboxes
description: A calm, tactile workbench for ephemeral development environments.
colors:
  canvas: "oklch(1 0 0)"
  surface: "oklch(0.972 0.006 256)"
  surface-strong: "oklch(0.935 0.012 256)"
  ink: "oklch(0.205 0.024 256)"
  muted-ink: "oklch(0.43 0.035 256)"
  line: "oklch(0.84 0.018 256)"
  line-strong: "oklch(0.67 0.035 256)"
  workbench-cobalt: "oklch(0.44 0.145 256)"
  workbench-cobalt-deep: "oklch(0.38 0.14 256)"
  workbench-cobalt-soft: "oklch(0.93 0.035 256)"
  brass-accent: "oklch(0.69 0.15 76)"
  ready-green: "oklch(0.44 0.105 153)"
  ready-green-soft: "oklch(0.94 0.035 153)"
  warning-amber: "oklch(0.47 0.11 67)"
  warning-amber-soft: "oklch(0.95 0.045 82)"
  danger-red: "oklch(0.47 0.16 25)"
  danger-red-deep: "oklch(0.4 0.15 25)"
  danger-red-soft: "oklch(0.95 0.035 25)"
typography:
  display:
    fontFamily: 'Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "2.25rem"
    fontWeight: 720
    lineHeight: 1.15
    letterSpacing: "-0.025em"
  title:
    fontFamily: 'Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.025em"
  body:
    fontFamily: 'Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: 'Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "0.8rem"
    fontWeight: 700
    lineHeight: 1.2
  mono:
    fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", ui-monospace, monospace'
    fontSize: "0.88rem"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  sm: "6px"
  md: "10px"
  lg: "14px"
  pill: "999px"
spacing:
  1: "0.25rem"
  2: "0.5rem"
  3: "0.75rem"
  4: "1rem"
  5: "1.5rem"
  6: "2rem"
  7: "3rem"
components:
  button-primary:
    backgroundColor: "{colors.workbench-cobalt}"
    textColor: "{colors.canvas}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "0.62rem 0.9rem"
    height: "2.55rem"
  button-secondary:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "0.62rem 0.9rem"
    height: "2.55rem"
  button-danger:
    backgroundColor: "{colors.danger-red}"
    textColor: "{colors.canvas}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "0.62rem 0.9rem"
    height: "2.55rem"
  text-field:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0.66rem 0.75rem"
    height: "2.7rem"
  status-chip:
    backgroundColor: "{colors.ready-green-soft}"
    textColor: "{colors.ready-green}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "0.32rem 0.55rem"
---

# Design System: Devboxes

## 1. Overview

**Creative North Star: "The Quiet Workbench"**

Devboxes should feel like the uncluttered bench of an experienced engineer: bright enough to read in mixed daytime light, compact enough to scan at a glance, and tactile enough that every control feels deliberate. Cobalt marks the controls that move work forward; cool, nearly neutral surfaces keep operational information calm and legible.

The browser is an honest companion to the terminal, not a theatrical imitation of it. It exposes lifecycle, readiness, persistence, and connection details with familiar controls and restrained motion. The system explicitly rejects generic SaaS card grids, neon hacker styling, decorative glassmorphism, oversized metrics, and optimistic status copy that conceals operational truth.

**Key Characteristics:**

- Calm, dense-enough operational layouts with generous section rhythm.
- Tactile six-pixel controls and fourteen-pixel work surfaces.
- Restrained cobalt for action, textual status for truth, and color as reinforcement.
- Flat, high-contrast surfaces at rest; elevation reserved for temporary layers.
- Terminal-first language without fake terminal decoration.

## 2. Colors

The palette is a cool-neutral workshop with restrained cobalt controls, green readiness, amber caution, and red destruction.

### Primary

- **Workbench Cobalt** (`oklch(0.44 0.145 256)`): Primary actions, active links, focus-adjacent states, and the compact wordmark.
- **Workbench Cobalt Deep** (`oklch(0.38 0.14 256)`): Hover and pressed emphasis where the primary needs a darker mechanical detent.
- **Workbench Cobalt Soft** (`oklch(0.93 0.035 256)`): Quiet action hover, starting status, and selected support surfaces.

### Secondary

- **Brass Accent** (`oklch(0.69 0.15 76)`): A rare warm counterpoint for future exceptional emphasis; never a second default action color.

### Tertiary

- **Ready Green** (`oklch(0.44 0.105 153)`): Confirmed readiness and healthy cluster state, always accompanied by text.
- **Warning Amber** (`oklch(0.47 0.11 67)`): Time-sensitive or cautionary states that do not yet require destructive action.
- **Danger Red** (`oklch(0.47 0.16 25)`): Destructive controls and errors only.

### Neutral

- **Canvas White** (`oklch(1 0 0)`): Page and control background.
- **Cool Surface** (`oklch(0.972 0.006 256)`): Workbenches, subtle row hover, and secondary structure.
- **Strong Cool Surface** (`oklch(0.935 0.012 256)`): Code fragments, stopped states, and quiet button material.
- **Workshop Ink** (`oklch(0.205 0.024 256)`): Primary text and the darkest temporary surface.
- **Muted Workshop Ink** (`oklch(0.43 0.035 256)`): Secondary text that still clears AA contrast on white and cool surfaces.
- **Fine Line** (`oklch(0.84 0.018 256)`): Structural dividers and container boundaries.
- **Strong Line** (`oklch(0.67 0.035 256)`): Interactive control borders.

**The Cobalt Restraint Rule.** Workbench Cobalt occupies no more than roughly ten percent of a screen; its rarity makes primary action unambiguous.

## 3. Typography

- **Display Font:** Inter with the system UI fallback stack
- **Body Font:** Inter with the system UI fallback stack
- **Label/Mono Font:** SFMono-Regular with Consolas, Liberation Mono, and ui-monospace fallbacks

**Character:** One disciplined sans-serif hierarchy keeps the tool immediate and platform-native. Monospace appears only where the content is genuinely executable or machine-shaped.

### Hierarchy

- **Display** (720, `2.25rem`, 1.15): The single page or login proposition; drops to `1.85rem` on narrow screens.
- **Headline** (700, `1.25rem`, 1.15): Major workbench and fleet sections.
- **Title** (700, `1rem`, 1.15): Dialogs and compact component headings.
- **Body** (400, `1rem`, 1.5): Explanatory text capped around 66 characters where reading length matters.
- **Label** (680–720, `0.72rem`–`0.86rem`, normal tracking, sentence case): Controls, fields, table headings, and status chips.

**The Operational Type Rule.** Use monospace only for commands, addresses, identifiers, and terminal samples; never use it to make ordinary interface copy look technical.

## 4. Elevation

The system is flat and tonally layered at rest. Borders and surface changes provide durable structure; shadows appear only when an element temporarily leaves the page plane, such as a dialog, toast, or centered login shell. Sticky navigation uses a subtle functional backdrop blur, not decorative glass material.

### Shadow Vocabulary

- **Temporary Layer** (`box-shadow: 0 8px 24px oklch(0.18 0.03 256 / 0.22)`): Delete dialogs only.
- **Compact Notice** (`box-shadow: 0 4px 8px oklch(0.18 0.03 256 / 0.22)`): Toast notifications.
- **Login Shell** (`box-shadow: 0 8px 24px oklch(0.18 0.03 256 / 0.16)`): The unbordered authentication shell against its tonal page.

**The Flat Until Necessary Rule.** Persistent containers use a border or a tonal shift, never a decorative wide shadow; elevation belongs to temporary interaction layers.

## 5. Components

### Buttons

- **Shape:** Compact tactile corners (`6px`) with minimum heights from `2rem` to `2.7rem`.
- **Primary:** Workbench Cobalt background, Canvas White text, and `0.62rem 0.9rem` padding.
- **Hover / Focus:** Hover deepens one cobalt step; active presses down by one pixel; keyboard focus uses a three-pixel cobalt ring with a three-pixel offset.
- **Secondary / Quiet / Danger:** Secondary is white with Strong Line; quiet is borderless Muted Ink; danger is reserved red. Disabled controls retain shape and drop to 58% opacity.

### Chips

- **Style:** Full-pill tonal background with a visible leading dot, semantic text, and sentence-case state name.
- **State:** Ready uses green, starting cobalt, stopped neutral, and degraded red. State is never communicated by color alone.

### Cards / Containers

- **Corner Style:** `14px` for a major workbench, `10px` for compact bounded material.
- **Background:** Cool Surface for creation workbenches; Canvas White for the page and dialogs.
- **Shadow Strategy:** Flat at rest, following the Elevation section.
- **Border:** One-pixel Fine Line; no colored side stripe.
- **Internal Padding:** `1.5rem` desktop, `1rem` narrow-screen minimum.

### Inputs / Fields

- **Style:** Canvas White, one-pixel Strong Line, six-pixel corners, and `2.7rem` minimum height.
- **Focus:** Workbench Cobalt border plus a three-pixel Cobalt Soft ring; placeholder ink remains AA-readable.
- **Error / Disabled:** Errors use Danger Red text under the field; disabled controls retain their label and use 58% opacity.

### Navigation

The sticky header is a single compact row with a cobalt wordmark, Workbench and Docs navigation, textual cluster health, contextual actions, and logout. The current surface uses a quiet cobalt selection state. At phone width, manual refresh yields because the fleet already refreshes automatically, and the wordmark contracts before navigation does. The header uses a fine bottom divider and only enough blur to keep text legible while content scrolls underneath.

### Devbox Fleet Row

Desktop rows align name, textual state, connection coordinate, auto-stop, and actions in a fixed table for rapid comparison. Below `42rem`, each row becomes a linear record with actions last, preserving source order and keyboard order. Stop and delete remain visually and semantically distinct because stopping is reversible while purge is not.

### Documentation Page

The authenticated documentation surface is a task-oriented manual, not a marketing page or a mirror of the repository README. A sticky on-page navigation rail follows the real learning sequence: quick start, persistence model, creation, SSH/tmux, accounts, daily lifecycle, automation, dashboard, command reference, and troubleshooting. Executable examples use flat, high-contrast command blocks with keyboard-accessible copy controls; operational boundaries use tables and definition lists rather than repetitive cards. Below `52rem`, the rail becomes a horizontally scrollable section index while prose remains capped near 72 characters per line.

## 6. Do's and Don'ts

### Do:

- **Do** expose readiness, failure, expiry, persistence, and SSH details as current text, with color as reinforcement.
- **Do** keep primary cobalt below roughly ten percent of the screen and reserve red for errors or destruction.
- **Do** preserve a visible three-pixel focus outline, full keyboard workflows, AA text contrast, and reduced-motion alternatives.
- **Do** use familiar buttons, fields, tables, dialogs, and status labels before inventing a new interaction.
- **Do** distinguish stopping compute, deleting compute, and permanently purging a home volume in both copy and confirmation.
- **Do** keep body copy to 65–75 characters per line and interface labels concise.

### Don't:

- **Don't** build generic SaaS dashboards made from endless identical cards.
- **Don't** use neon hacker-terminal aesthetics, glowing cyberpunk decoration, or fake command-line theater.
- **Don't** use decorative glassmorphism, oversized metrics, or motion that delays the task.
- **Don't** invent unfamiliar controls for personality when a standard control is clearer.
- **Don't** hide operational truth behind optimistic status copy.
- **Don't** add colored side-stripe borders, gradient text, decorative grid backgrounds, or card radii above `16px`.
- **Don't** pair a one-pixel border with a wide decorative shadow; choose structural border or true temporary elevation.
