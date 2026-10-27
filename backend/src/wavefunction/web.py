"""Fetching a web page as readable text, as the `fetch_page` tool.

The extractor is the standard library's HTMLParser rather than a readability dependency. It
does less — no boilerplate removal, no article detection — and that is acceptable: the reader
is a model that skims, and the failure mode of a missing dependency in the frozen sidecar is
worse than some navigation text in the output.
"""

from __future__ import annotations

import asyncio
import ipaddress
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx
from langchain_core.tools import BaseTool, tool

FETCH_TIMEOUT = 30
# The whole fetch — every redirect hop and the body read — under one wall clock. httpx's own
# timeout is per socket operation, which a slow-drip server never trips.
FETCH_DEADLINE = 90
MAX_REDIRECTS = 5


async def _assert_public(url: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Refuse an address that reaches this machine or its network instead of the web.

    The web grant means the internet, not the local API (whose bearer token is the only thing
    guarding it), the user's router, or anything else on the LAN. Every address the hostname
    resolves to must be public, and the check runs again on every redirect hop — a public page
    redirecting to 127.0.0.1 is the classic way around a first-URL-only check.

    Returns one validated address; the fetch connects to exactly that address rather than
    resolving again, because a rebinding DNS server can answer public here and loopback on the
    second lookup.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Not a web address: {url!r}. Use a full http(s) URL.")
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"No host in {url!r}.")
    try:
        # Through the loop's resolver so a slow DNS server stalls this coroutine, not the
        # whole event loop (and with it every other API request and cancellation).
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except OSError as error:
        raise ValueError(f"Could not resolve {host!r}: {error}") from error
    addresses = []
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ValueError(
                f"{host!r} points at a private or local address, which web access does not cover."
            )
        addresses.append(address)
    return addresses[0]


# Elements whose text is not content.
_SKIP = {"script", "style", "noscript", "template", "svg", "head"}
# Elements that imply a line break around their text.
_BLOCK = {
    "p",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "li",
    "ul",
    "ol",
    "table",
    "tr",
    "br",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "pre",
    "figure",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data)


def extract_text(html: str) -> tuple[str, str]:
    """The page's title and its visible text, whitespace collapsed."""
    parser = _TextExtractor()
    parser.feed(html)
    lines = []
    for line in "".join(parser.parts).splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return parser.title.strip(), "\n".join(lines)


def build_fetch_page(_context: Any = None) -> BaseTool:
    @tool("fetch_page")
    async def fetch_page(url: str) -> str:
        """Fetch a web page and return its readable text.

        Use this to read a page you already know the address of — documentation, an article,
        a page a search result pointed to. Use web_search when you do not have an address yet.
        A long page comes back in full; if it is excerpted for you, the excerpt says how to
        read the rest.

        Args:
            url: The full address, for example https://docs.example.com/setup.
        """
        # Redirects are followed by hand so every hop is validated, not just the first URL.
        # The request connects to the exact address the validation approved (the hostname is
        # kept for TLS and the Host header) — resolving twice would let a rebinding DNS server
        # answer public during the check and loopback for the connection. The whole loop runs
        # under one wall-clock deadline, because httpx's timeout bounds individual socket
        # operations and a slow-drip server would otherwise hold the turn open forever.
        target = url
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            follow_redirects=False,
            # No compressed transfer: httpx would decompress transparently, and a small
            # compression bomb could then blow far past the byte cap in one chunk.
            headers={"User-Agent": "Wavefunction/0.1", "Accept-Encoding": "identity"},
        ) as client:
            async with asyncio.timeout(FETCH_DEADLINE):
                for _hop in range(MAX_REDIRECTS + 1):
                    address = await _assert_public(target)
                    parsed = httpx.URL(target)
                    host = parsed.host
                    # The full authority, not just the hostname: a server on a non-default
                    # port routes by `Host: example.com:8443`, and IPv6 keeps its brackets.
                    authority = parsed.netloc.decode("ascii")
                    pinned = parsed.copy_with(host=f"[{address}]" if address.version == 6 else str(address))
                    request_extensions = {"sni_hostname": host} if parsed.scheme == "https" else {}
                    async with client.stream(
                        "GET", pinned, headers={"Host": authority}, extensions=request_extensions
                    ) as response:
                        if response.is_redirect:
                            location = response.headers.get("location", "")
                            target = str(httpx.URL(target).join(location))
                            continue
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                        if response.headers.get("content-encoding", "") not in ("", "identity"):
                            return (
                                "That server insists on compressed transfer, which this tool "
                                "does not read. Tell the user the address instead."
                            )
                        html = content_type in ("text/html", "application/xhtml+xml", "")
                        if not html and not (
                            content_type.startswith("text/")
                            or content_type in ("application/json", "application/xml")
                        ):
                            # Decided before the body is read: a binary is never downloaded
                            # just to be refused.
                            return (
                                f"That page is {content_type or 'binary content'}, which cannot "
                                "be read as text. If it is a document the user needs, tell them "
                                "the address instead."
                            )
                        charset = response.charset_encoding or "utf-8"
                        # Read whole. The 2 MB cut this replaced dropped the end of a long page
                        # silently — the part with the answer, as often as not. The deadline
                        # above bounds how much a server can send; what the model sees of a
                        # long page is bounded by the request budget, which keeps the complete
                        # text readable by offset.
                        collected = bytearray()
                        async for chunk in response.aiter_bytes():
                            collected.extend(chunk)
                        final_url = target
                        break
                else:
                    raise RuntimeError(f"{url} redirected more than {MAX_REDIRECTS} times.")

        # The declared charset, because always-UTF-8 quietly corrupted names and quotes on
        # ISO-8859-1 pages; replacement characters stay for bytes the declared one cannot read.
        try:
            body = collected.decode(charset, errors="replace")
        except LookupError:
            body = collected.decode("utf-8", errors="replace")
        if html:
            title, text = extract_text(body)
            header = f"SOURCE: {final_url}\n{title}".rstrip()
            return f"{header}\n\n{text}"
        return f"SOURCE: {final_url}\n\n{body}"

    return fetch_page


__all__ = ["build_fetch_page", "extract_text"]
