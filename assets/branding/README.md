# Wavefunction branding

| File | What it is |
|---|---|
| `wavefunction-mark.svg` | The mark: a brush-lettered navy script W, as a transparent vector. The README shows it, and the landing page and social preview use its copy at `website/public/wavefunction-mark.svg`. Site favicons are transparent PNG exports of the same shape. |
| `wavefunction-icon.svg` | The app icon: the mark on a softly shaded warm porcelain tile. Tauri's icon generator makes the platform icons in `src-tauri/icons` from it. |
| `wavefunction-icon.png` | The app icon's 1024×1024 RGBA export, with transparent corners. |

## Reproduce platform assets

From the repository root:

```sh
./frontend/node_modules/.bin/tauri icon assets/branding/wavefunction-icon.svg --output src-tauri/icons --ios-color '#F3F0EA'
cp src-tauri/icons/128x128.png frontend/public/wavefunction-icon.png
```

`frontend/public/wavefunction-icon.png` is the matching 128px app favicon. Export the 1024 master and the site favicons (192, 180, 256 and 96 px PNGs) with `tauri icon <svg> --output <dir> --png <size>`; the favicons come from the mark on a square transparent canvas.

This makes icon files only. The app shows them after its next build.

The display name is Wavefunction. The `app.harness.coding` bundle identifier and storage keys stay unchanged to preserve user data.
