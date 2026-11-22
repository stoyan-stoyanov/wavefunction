# Designs (themes)

A design replaces the app's look with CSS, images and fonts. It never runs code. Choose one in Settings › General › Design. Switching swaps one stylesheet in place, so nothing reloads and every chat keeps its state.

## A design is a folder

```
my-theme/
  theme.json
  theme.css
  icons/…, fonts/…   (optional)
```

`theme.json`:

| Key              | Meaning                                                                                                                                                                                                                                                                                       |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`           | Shown in Settings. Required.                                                                                                                                                                                                                                                                  |
| `description`    | One line under the picker.                                                                                                                                                                                                                                                                    |
| `appearances`    | `["light"]`, `["dark"]` or both (the default). With one, the app shows it whatever the Light/Dark setting says.                                                                                                                                                                               |
| `windowControls` | `"native"` (the default) or `"theme"`: the app hides the macOS traffic lights and draws its own minimize, maximize and close buttons (`.window-controls`, `.window-control[data-control]`) for the design to style. Other platforms keep their window frame, and the app draws nothing there. |

The folder name is the design's id: lowercase letters, digits and dashes.

## Where designs live

- **Bundled** designs are folders here, compiled into the app. [nostalgia](nostalgia/) (light, with its own window buttons) and [retro](retro/) (dark, native window buttons) are the references.
- **The agent** makes one when asked: the built-in `design-creator` skill (`backend/src/wavefunction/coding_skills/design-creator.md`) tells it what a design can change, and its `save_design` tool writes the folder, checked the way Settings loads it.
- **Your own** go in the `themes` folder of the app's data folder: `~/Library/Application Support/app.harness.coding/themes/` in the desktop app. Settings shows the path, and lists a design that can't load with the reason. Choosing a design again reads it from disk again.

## Only CSS and assets

The engine hands your stylesheet to the app with each asset it names inlined, because the window loads images and fonts only from itself. It refuses `@import` and any `url()` that leaves the design's folder. Assets can be SVG, PNG, JPEG, WebP, GIF, WOFF2, WOFF, TTF or OTF, up to 2 MB for the whole design.

## What to change

- **Tokens.** Set any variable from `src/index.css` on `:root` (and on `.dark` for a dark appearance): colours, `--radius`, `--elevation-*`, `--font-sans`, `--font-heading`, `--font-mono` and the `--text-*` size ladder.
- **Icons.** Every line icon is `svg.lucide-<name>`. Hide its strokes (`> * { display: none }`) and paint your own with `background`.
- **Components.** Controls carry `data-slot` and `data-variant` (`[data-slot="button"][data-variant="ghost"]`). Areas have stable classes: `.coding-sidebar`, `.shell-titlebar`, `.navigation-topbar`, `.coding-composer`, `.user-message-bubble`, `.coding-trace`, `.coding-markdown [data-streamdown="code-block"]`, `.coding-settings-sidebar`, `.native-view`.
- **Outranking the app.** Your stylesheet comes after the app's, so a rule as specific as the app's wins. Prefix a rule with `:root[data-design]` to outrank one that is more specific.
