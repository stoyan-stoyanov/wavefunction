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
import json
import re
from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, HTTPException

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
        asset = (root / re.split(r"[?#]", target, maxsplit=1)[0]).resolve()
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
            inlined[asset] = f'url("data:{kind};base64,{base64.b64encode(data).decode()}")'
        return inlined[asset]

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
