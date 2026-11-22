"""Themes people add themselves: a folder of CSS, images, fonts and a manifest, never code.

Each folder in `<data folder>/themes/` is one theme. The interface cannot load a theme straight
from disk or from here: the webview's CSP admits images and fonts only from its own origin and as
data: URIs, and requests here carry the desktop token. So the engine hands the stylesheet over as
text, each asset it names inlined as a data: URI. A theme cannot reach outside its folder:
`@import` and remote or out-of-folder `url()`s are refused. The CSP would block remote ones in the
app anyway; refusing them here tells the theme's author why.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import Depends, HTTPException
from langchain_core.tools import BaseTool, tool

from . import responses as api

MAX_THEME_BYTES = 2 * 1024 * 1024
ASSET_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}
THEME_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)'"\s]*))\s*\)""", re.IGNORECASE)
IMPORT = re.compile(r"@import\b", re.IGNORECASE)
SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.IGNORECASE)


class ThemeError(ValueError):
    """What is wrong with a theme, in words its author can act on."""


def themes_folder(data_dir: Path) -> Path:
    return data_dir / "themes"


def read_theme(folder: Path) -> tuple[api.Theme, str | None]:
    """The theme in `folder` and its ready stylesheet, or the theme with `error` set and no CSS."""
    theme: api.Theme = {
        "id": folder.name,
        "name": folder.name,
        "description": "",
        "appearances": ["light", "dark"],
        "window_controls": "native",
        "error": None,
    }
    try:
        if not THEME_ID.fullmatch(folder.name):
            raise ThemeError("Rename the folder to lowercase letters, digits and dashes.")
        theme = _manifest(folder)
        return theme, _stylesheet(folder)
    except ThemeError as error:
        theme["error"] = str(error)
        return theme, None


def _manifest(folder: Path) -> api.Theme:
    try:
        data = json.loads((folder / "theme.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ThemeError("theme.json is missing.") from None
    except (OSError, ValueError) as error:
        raise ThemeError(f"theme.json could not be read: {error}") from None
    if not isinstance(data, dict):
        raise ThemeError("theme.json must be a JSON object.")
    name, description = data.get("name"), data.get("description", "")
    if not isinstance(name, str) or not name.strip():
        raise ThemeError('theme.json needs a "name".')
    if not isinstance(description, str):
        raise ThemeError('"description" must be text.')
    appearances = data.get("appearances", ["light", "dark"])
    if (
        not isinstance(appearances, list)
        or not appearances
        or any(item not in ("light", "dark") for item in appearances)
    ):
        raise ThemeError('"appearances" must list "light", "dark" or both.')
    controls = data.get("windowControls", "native")
    if controls not in ("native", "theme"):
        raise ThemeError('"windowControls" must be "native" or "theme".')
    return {
        "id": folder.name,
        "name": name.strip()[:60],
        "description": description.strip()[:200],
        "appearances": list(dict.fromkeys(appearances)),
        "window_controls": controls,
        "error": None,
    }


def _stylesheet(folder: Path) -> str:
    root = folder.resolve()
    path = root / "theme.css"
    try:
        total = path.stat().st_size
        if total > MAX_THEME_BYTES:
            raise ThemeError("The theme is larger than 2 MB.")
        css = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ThemeError("theme.css is missing.") from None
    except (OSError, ValueError) as error:
        raise ThemeError(f"theme.css could not be read: {error}") from None
    if IMPORT.search(css):
        raise ThemeError("@import is not allowed: put the styles in theme.css.")
    inlined: dict[Path, str] = {}

    def inline(match: re.Match[str]) -> str:
        nonlocal total
        target = next(group for group in match.groups() if group is not None).strip()
        if not target or target.startswith(("data:", "#")):
            return match.group(0)
        if target.startswith("//") or SCHEME.match(target):
            raise ThemeError(f"{target} is not in the theme's folder. Themes cannot load anything remote.")
        # A fragment picks a part of the asset, such as one view of an SVG sprite: it stays on the URL.
        path, _, fragment = target.partition("#")
        asset = (root / path.partition("?")[0]).resolve()
        if not asset.is_relative_to(root):
            raise ThemeError(f"{target} is outside the theme's folder.")
        if asset not in inlined:
            kind = ASSET_TYPES.get(asset.suffix.lower())
            if kind is None:
                raise ThemeError(f"{target}: a theme can use images and fonts only.")
            try:
                total += asset.stat().st_size
                if total > MAX_THEME_BYTES:
                    raise ThemeError("The theme is larger than 2 MB.")
                data = asset.read_bytes()
            except OSError:
                raise ThemeError(f"{target} is missing.") from None
            inlined[asset] = f"data:{kind};base64,{base64.b64encode(data).decode()}"
        return f'url("{inlined[asset]}{"#" + fragment if fragment else ""}")'

    return URL.sub(inline, css)


def register_theme_routes(app, folder: Callable[[], Path], authorize) -> None:
    dependencies = [Depends(authorize)]

    @app.get("/api/themes", response_model=api.Themes, dependencies=dependencies)
    def list_themes() -> api.Themes:
        """The themes in the themes folder, each checked, so a broken one says why up front."""
        root = folder()
        root.mkdir(parents=True, exist_ok=True)
        entries = sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith("."))
        return {"folder": str(root), "themes": [read_theme(path)[0] for path in entries]}

    @app.get(
        "/api/themes/{theme_id}/stylesheet", response_model=api.ThemeStylesheet, dependencies=dependencies
    )
    def theme_stylesheet(theme_id: str) -> api.ThemeStylesheet:
        path = folder() / theme_id
        if not THEME_ID.fullmatch(theme_id) or not path.is_dir():
            raise HTTPException(status_code=404, detail="That theme is not in the themes folder.")
        theme, css = read_theme(path)
        if css is None:
            raise HTTPException(status_code=422, detail=theme["error"])
        return {"css": css}


def build_save_design(folder: Path) -> BaseTool:
    """Let the agent write one of the user's designs: the themes folder is the app's own data, which
    its sandbox cannot write. A design is checked the way Settings loads it before it replaces
    anything, so a broken one never shows up in Settings."""

    @tool("save_design")
    def save_design(
        name: str,
        css: str,
        description: str = "",
        appearances: list[Literal["light", "dark"]] | None = None,
        window_controls: Literal["native", "theme"] = "native",
        assets: dict[str, str] | None = None,
    ) -> str:
        """Create or update one of the user's designs for this app, chosen in Settings > General > Design.

        name: shown in Settings; its lowercase, hyphenated form names the folder. css: the design's
        theme.css, naming its assets by relative path in url(). description: one line shown under the
        picker. appearances: ["light"], ["dark"] or both (the default). window_controls: "theme" to
        have the app draw minimize, maximize and close buttons for the CSS to style. assets: relative
        path -> contents, SVG as text and PNG, JPEG, WebP, GIF or font files as base64. Saving an
        existing name replaces its theme.json and theme.css and keeps assets not given again. If the
        design would not load, nothing is saved and the error says what to fix.
        """
        slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:64].strip("-")
        if not THEME_ID.fullmatch(slug):
            raise ValueError("Give the design a name with letters or digits.")
        target, staging = folder / slug, folder / ".staging" / slug
        if staging.exists():
            shutil.rmtree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            shutil.copytree(target, staging)
        else:
            staging.mkdir()
        try:
            manifest = {
                "name": name.strip(),
                "description": description.strip(),
                "appearances": appearances or ["light", "dark"],
                "windowControls": window_controls,
            }
            (staging / "theme.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            (staging / "theme.css").write_text(css, encoding="utf-8")
            for relative, content in (assets or {}).items():
                _write_asset(staging, relative, content)
            theme, stylesheet = read_theme(staging)
            if stylesheet is None:
                raise ValueError(f"Not saved: {theme['error']} Fix it and call save_design again.")
        except Exception:
            shutil.rmtree(staging)
            raise
        if target.exists():
            replaced = staging.with_name(f"{slug}.replaced")
            if replaced.exists():
                shutil.rmtree(replaced)
            target.rename(replaced)
            staging.rename(target)
            shutil.rmtree(replaced)
        else:
            staging.rename(target)
        return (
            f"Saved the design {theme['name']!r} at {target}. The user chooses it in Settings > General > "
            "Design; the app's look does not change until they do."
        )

    return save_design


def _write_asset(folder: Path, relative: str, content: str) -> None:
    path = (folder / relative).resolve()
    if path == folder.resolve() or not path.is_relative_to(folder.resolve()):
        raise ValueError(f"{relative} is outside the design's folder.")
    if path.suffix.lower() not in ASSET_TYPES:
        raise ValueError(f"{relative}: a design can hold images and fonts only.")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".svg":
        path.write_text(content, encoding="utf-8")
        return
    try:
        path.write_bytes(base64.b64decode(content, validate=True))
    except binascii.Error:
        raise ValueError(f"{relative}: give an image or font other than SVG as base64.") from None
