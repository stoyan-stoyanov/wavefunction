"""Cards and media for the web links the agent writes in a reply.

The webview's content policy lets it reach only this engine, so the engine reads a page's
OpenGraph and meta tags for its card, and passes the card's pictures and linked media through.
It reaches public addresses only, the same as `fetch_page`. What a page says about itself is
untrusted text: it is cleaned and cut here, and the interface shows it as plain text.
"""

from __future__ import annotations

import asyncio
import codecs
import re
import time
import unicodedata
from collections import OrderedDict
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import Query
from fastapi.responses import Response

from . import responses as api
from .web import open_public

# A card that takes longer than this isn't worth waiting for: the link already shows as text.
PREVIEW_DEADLINE = 5
MEDIA_DEADLINE = 30
# The same cap as a linked file's preview: the webview buffers the whole file.
MAX_MEDIA_BYTES = 25 * 1024 * 1024
# OpenGraph tags live in the head; a page whose head is longer than this has no card.
MAX_HEAD_BYTES = 512 * 1024
CACHE_ENTRIES = 256
CACHE_SECONDS = 60 * 60
# A failure is remembered briefly, so a dead link isn't fetched again on every render.
FAILED_SECONDS = 5 * 60
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Wavefunction link preview)",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
    # No compressed transfer: httpx would decompress transparently, past the byte caps.
    "Accept-Encoding": "identity",
}
# Bidirectional overrides can make a title read as something else.
_HIDDEN = re.compile("[‪-‮⁦-⁩]")


def _text(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    value = _HIDDEN.sub("", value)
    value = " ".join("".join(" " if unicodedata.category(c) == "Cc" else c for c in value).split())
    if len(value) > limit:
        value = value[: limit - 1].rstrip() + "…"
    return value or None


def _web_url(value: str | None, base: str) -> str | None:
    if not value:
        return None
    address = urljoin(base, value.strip())
    return address if urlparse(address).scheme in ("http", "https") and len(address) <= 2048 else None


class _Head(HTMLParser):
    """The tags a card is made of, read until the page's body starts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.icons: list[str] = []
        self.title = ""
        self.done = False
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if self.done:
            return
        values = {name.lower(): value or "" for name, value in attrs}
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").strip().lower()
            if key and "content" in values:
                self.meta.setdefault(key, values["content"])
        elif tag == "link" and "icon" in values.get("rel", "").lower().split() and values.get("href"):
            self.icons.append(values["href"])
        elif tag == "title":
            self._in_title = True
        elif tag == "body":
            self.done = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self.done = True

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.done:
            self.title += data


def _none(url: str) -> api.LinkPreview:
    return {
        "url": url,
        "kind": "none",
        "title": None,
        "description": None,
        "site_name": None,
        "image": None,
        "icon": None,
    }


def _card(head: _Head, url: str, page: str) -> api.LinkPreview:
    def first(*keys: str) -> str | None:
        return next((head.meta[key] for key in keys if head.meta.get(key, "").strip()), None)

    title = _text(first("og:title", "twitter:title") or head.title, 200)
    if not title:
        return _none(url)
    host = (urlparse(page).hostname or "").removeprefix("www.")
    return {
        "url": url,
        "kind": "page",
        "title": title,
        "description": _text(first("og:description", "twitter:description", "description"), 300),
        "site_name": _text(first("og:site_name", "application-name"), 80) or host or None,
        "image": _web_url(
            first("og:image:secure_url", "og:image", "og:image:url", "twitter:image", "twitter:image:src"),
            page,
        ),
        "icon": _web_url(head.icons[0] if head.icons else "/favicon.ico", page),
    }


async def _fetch_card(url: str) -> tuple[api.LinkPreview, bool]:
    """The card for `url`, and whether the fetch succeeded."""
    async with (
        httpx.AsyncClient(timeout=3, follow_redirects=False, headers=HEADERS) as client,
        asyncio.timeout(PREVIEW_DEADLINE),
        open_public(client, url) as (response, page),
    ):
        if response.status_code >= 400:
            return _none(url), False
        media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if media_type.startswith(("image/", "video/")):
            return {**_none(url), "kind": "image" if media_type.startswith("image/") else "video"}, True
        if media_type not in ("text/html", "application/xhtml+xml") or response.headers.get(
            "content-encoding", "identity"
        ) not in ("", "identity"):
            return _none(url), True
        try:
            decoder = codecs.getincrementaldecoder(response.charset_encoding or "utf-8")(errors="replace")
        except LookupError:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        head, read = _Head(), 0
        async for chunk in response.aiter_bytes():
            head.feed(decoder.decode(chunk[: MAX_HEAD_BYTES - read]))
            read += len(chunk)
            if head.done or read >= MAX_HEAD_BYTES:
                break
        return _card(head, url, page), True


async def _fetch_media(url: str) -> tuple[bytes, str]:
    """An image or video from a public address, whole, with its media type."""
    async with (
        httpx.AsyncClient(timeout=10, follow_redirects=False, headers=HEADERS) as client,
        asyncio.timeout(MEDIA_DEADLINE),
        open_public(client, url) as (response, _page),
    ):
        response.raise_for_status()
        media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if not media_type.startswith(("image/", "video/")):
            raise ValueError("That address is not an image or a video.")
        if response.headers.get("content-encoding", "identity") not in ("", "identity"):
            raise ValueError("That server sends only compressed media.")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_MEDIA_BYTES:
                raise ValueError("That file is too large to preview.")
        return bytes(body), media_type


class CardCache:
    """Recent cards by address, bounded in entries and age."""

    def __init__(self, size: int = CACHE_ENTRIES) -> None:
        self.size = size
        self.entries: OrderedDict[str, tuple[float, api.LinkPreview]] = OrderedDict()

    def get(self, url: str) -> api.LinkPreview | None:
        entry = self.entries.get(url)
        if entry is None or entry[0] <= time.monotonic():
            self.entries.pop(url, None)
            return None
        self.entries.move_to_end(url)
        return entry[1]

    def put(self, url: str, card: api.LinkPreview, seconds: float) -> None:
        self.entries[url] = (time.monotonic() + seconds, card)
        self.entries.move_to_end(url)
        while len(self.entries) > self.size:
            self.entries.popitem(last=False)


def register_link_previews(router) -> None:
    # Cards describe public pages, not anything a chat owns, so one cache serves every chat.
    cache = CardCache()

    @router.get("/link-preview", response_model=api.LinkPreview)
    async def link_preview(url: str = Query(min_length=1, max_length=2048)):
        if urlparse(url).scheme not in ("http", "https"):
            raise ValueError("Only web addresses have link previews.")
        if (card := cache.get(url)) is not None:
            return card
        try:
            card, fetched = await _fetch_card(url)
        # A private address, a refusal, a timeout or a broken page: the link stays plain text.
        except (httpx.HTTPError, ValueError, RuntimeError, TimeoutError, OSError):
            card, fetched = _none(url), False
        cache.put(url, card, CACHE_SECONDS if fetched else FAILED_SECONDS)
        return card

    @router.get("/link-preview/media")
    async def link_media(url: str = Query(min_length=1, max_length=2048)):
        try:
            body, media_type = await _fetch_media(url)
        except (httpx.HTTPError, RuntimeError, TimeoutError) as error:
            raise ValueError("That image or video could not be loaded.") from error
        # An SVG shown as an <img> never runs its scripts; the sandbox covers anything else.
        return Response(
            body,
            media_type=media_type,
            headers={"Content-Security-Policy": "sandbox", "Cache-Control": "private, max-age=3600"},
        )
