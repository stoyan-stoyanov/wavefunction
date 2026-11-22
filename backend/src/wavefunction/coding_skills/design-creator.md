---
name: design-creator
description: "Create or change a design for this app's own look: its colours, fonts, icons and controls. Use when the user asks to restyle, theme or skin the app, or for a look like another app or era."
---

A design replaces the app's look with CSS, images and fonts. It never runs code. Save it with `save_design` (search_available_tools for "designs" if it is not loaded); the user then chooses it in Settings › General › Design. The app's own source is not in the user's repository, so build the design from what is below rather than from files.

What a design can change:
- Tokens. Set these on `:root`, and again on `.dark` for a dark appearance: `--background`, `--foreground`, `--card`, `--popover`, `--primary`, `--secondary`, `--muted`, `--muted-foreground`, `--accent`, `--destructive`, `--success`, `--warning`, `--border`, `--hairline`, `--input`, `--ring`, `--canvas`, the `--sidebar*` family, `--radius`, `--elevation-1` to `--elevation-6`, `--font-sans`, `--font-heading`, `--font-mono`, and the size ladder `--text-2xs` to `--text-3xl`, each with a `--text-*--line-height`.
- Controls. They carry `data-slot` and `data-variant`: `[data-slot="button"][data-variant="ghost"]`, `[data-slot="input"]`, `[data-slot="select-trigger"]`, `[data-slot="switch"]`, `[data-slot="dropdown-menu-content"]`, `[data-slot="dialog-content"]`.
- Areas. Stable classes: `.coding-sidebar` (rows `.new-project`, `.coding-thread-row`, `.thread-select`, `.project-group > button:first-child`, `.sidebar-bottom > button`), `.navigation-topbar` and `.shell-titlebar` (the bands across the top), `.coding-composer` with `.composer-toolbar`, `.model-selector`, `.composer-chip` and `.send-button`, `.composer-footer`, `.user-message-bubble`, `.coding-markdown` with `[data-streamdown="code-block"]`, `.coding-trace` (tool activity), `.coding-workbench` with `.workbench-toolbar` and `.workbench-tab`, `.coding-settings-sidebar`, `.native-view`.
- Icons. Every line icon is `svg.lucide-<name>`, such as `svg.lucide-folder`. To replace one, hide its strokes (`> * { display: none }`) and paint an SVG asset with `background`.
- Window buttons. `window_controls: "theme"` makes the app hide the macOS window buttons and draw its own `.window-controls` with `.window-control[data-control="minimize" | "maximize" | "close"]` for the CSS to style.

Rules:
- The stylesheet comes after the app's own, so a rule as specific as the app's wins. Prefix a rule with `:root[data-design]` to beat a more specific one.
- Assets are files in the design's folder, named by relative path in `url()`: SVG, PNG, JPEG, WebP, GIF, WOFF2, WOFF, TTF or OTF, up to 2 MB in all. No `@import`, nothing remote. Draw icons yourself; never copy another product's icons or bundle fonts whose licence forbids it. A font stack that falls back works without bundling anything.
- `appearances: ["light"]` or `["dark"]` makes the app show only that one.

Read the error `save_design` returns, fix the design and save again. Afterwards tell the user the design's name and that they choose it in Settings › General › Design. Saving the same name again replaces it.
