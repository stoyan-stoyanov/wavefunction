import ipaddress
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ARTICLE = """<!doctype html><html><head>
<meta charset="utf-8"><title>Fallback title</title>
<meta property="og:title" content="Rich &amp; useful ‮evil‬">
<meta property="og:description" content="Line one
line two {long}">
<meta property="og:site_name" content="Example Docs">
<meta property="og:image" content="/cover.png">
<link rel="shortcut icon" href="https://cdn.site.test/icon.png">
</head><body><meta property="og:title" content="Not in the head"></body></html>"""
PLAIN = "<html><head><title>  Just a\n title </title></head><body>Hello</body></html>"
# A valid 1x1 PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4d80000000049454e44ae426082"
)


@pytest.fixture
def site(monkeypatch):
    """A web server reached as the public host site.test; 127.0.0.1 itself stays private."""
    from wavefunction import link_previews, web

    hits = Counter()
    pages = {
        "/article": ("text/html; charset=utf-8", ARTICLE.replace("{long}", "word " * 100).encode()),
        "/plain": ("text/html", PLAIN.encode()),
        "/photo": ("image/png", PNG),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits[self.path] += 1
            if self.path == "/slow":
                time.sleep(1)
            if self.path == "/to-private":
                self.send_response(302)
                port = self.headers["Host"].rsplit(":", 1)[1]
                self.send_header("Location", f"http://127.0.0.1:{port}/article")
                self.end_headers()
                return
            if self.path not in pages:
                self.send_error(500)
                return
            media_type, body = pages[self.path]
            self.send_response(200)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    original = web._assert_public

    async def public(url):
        if "//site.test:" in url:
            return ipaddress.ip_address("127.0.0.1")
        return await original(url)

    monkeypatch.setattr(web, "_assert_public", public)
    monkeypatch.setattr(link_previews, "PREVIEW_DEADLINE", 0.3)
    yield f"http://site.test:{server.server_address[1]}", hits
    server.shutdown()
    server.server_close()


async def test_link_cards_are_read_from_public_pages_cached_and_otherwise_plain(coding, site):
    from wavefunction.link_previews import CardCache

    client, _, _ = coding
    origin, hits = site
    port = origin.rsplit(":", 1)[1]

    async def card(url):
        response = await client.get("/api/coding/link-preview", params={"url": url})
        assert response.status_code == 200, url
        return response.json()

    # Entities decoded, overrides and line breaks removed, long text cut, addresses made absolute.
    article = await card(f"{origin}/article")
    assert article["kind"] == "page"
    assert article["title"] == "Rich & useful evil"
    assert article["description"].startswith("Line one line two word word")
    assert article["description"].endswith("…") and len(article["description"]) == 300
    assert article["site_name"] == "Example Docs"
    assert article["image"] == f"{origin}/cover.png"
    assert article["icon"] == "https://cdn.site.test/icon.png"
    # A page with only a title still gets a card, named after its host.
    plain = await card(f"{origin}/plain")
    assert (plain["title"], plain["site_name"], plain["icon"], plain["image"]) == (
        "Just a title",
        "site.test",
        f"{origin}/favicon.ico",
        None,
    )
    # A link straight to a picture is shown as one, through the engine.
    assert (await card(f"{origin}/photo"))["kind"] == "image"
    media = await client.get("/api/coding/link-preview/media", params={"url": f"{origin}/photo"})
    assert media.status_code == 200 and media.content == PNG
    assert media.headers["content-type"] == "image/png"
    assert media.headers["content-security-policy"] == "sandbox"
    not_media = await client.get("/api/coding/link-preview/media", params={"url": f"{origin}/article"})
    assert not_media.status_code == 422

    # Rendering a link again reads the card from the cache, and a dead link is remembered too.
    fetched = hits["/article"]
    for _ in range(2):
        assert (await card(f"{origin}/article"))["title"] == "Rich & useful evil"
        assert (await card(f"{origin}/gone"))["kind"] == "none"
    assert hits["/article"] == fetched and hits["/gone"] == 1
    cache = CardCache(size=2)
    for name in ("a", "b", "c"):
        cache.put(name, article, 60)
    assert cache.get("a") is None and cache.get("c") is not None
    cache.put("old", article, 0)
    assert cache.get("old") is None

    # This machine and its network are never fetched, directly or through a redirect.
    assert (await card(f"http://127.0.0.1:{port}/plain"))["kind"] == "none"
    assert (await card(f"{origin}/to-private"))["kind"] == "none"
    assert hits["/plain"] == 1 and hits["/article"] == fetched
    photos = hits["/photo"]
    private = await client.get(
        "/api/coding/link-preview/media", params={"url": f"http://127.0.0.1:{port}/photo"}
    )
    assert private.status_code == 422 and hits["/photo"] == photos
    # A server error or a slow page gives up quickly.
    assert (await card(f"{origin}/broken"))["kind"] == "none"
    started = time.monotonic()
    assert (await card(f"{origin}/slow"))["kind"] == "none"
    assert time.monotonic() - started < 1
    # Only web addresses have cards.
    for url in ("file:///etc/hosts", "javascript:alert(1)"):
        assert (await client.get("/api/coding/link-preview", params={"url": url})).status_code == 422
