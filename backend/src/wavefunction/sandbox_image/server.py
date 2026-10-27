"""The action server that runs inside the desktop container.

Standard library only, on purpose: the image build stays a fast apt-get with no pip step, and
the host's test suite imports this file directly and fakes `subprocess.run` — which is the only
way the endpoint logic gets tested at all, since CI has no Docker.

Every action shells out to xdotool with an argv list, never a shell string, so nothing the
model writes is ever parsed by a shell. Coordinates are clamped to the screen and key names are
whitelisted — not because the container is precious, but because a request that xdotool cannot
parse should fail loudly here rather than time out silently.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import re
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("ACTION_PORT", "8765"))
TOKEN = os.environ.get("ACTION_TOKEN", "")
SCREEN_WIDTH = int(os.environ.get("SCREEN_WIDTH", "1280"))
SCREEN_HEIGHT = int(os.environ.get("SCREEN_HEIGHT", "800"))

COMMAND_TIMEOUT = 15
MAX_BODY = 64_000
MAX_TYPE_TEXT = 5_000
MAX_SCROLL = 20

# xdotool key names: letters, digits, underscores, joined by "+" for chords ("ctrl+shift+t").
# Anything else — spaces, quotes, dashes — is either a typo or an attempt to smuggle options.
# Any number of them: a chord is one token, and a valid sequence is valid at any length.
KEY_TOKEN = re.compile(r"^[A-Za-z0-9_]+(\+[A-Za-z0-9_]+)*$")

SCROLL_BUTTONS = {"up": "4", "down": "5", "left": "6", "right": "7"}


def clamp(value: object, upper: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ActionError(f"not a coordinate: {value!r}") from error
    return max(0, min(upper - 1, number))


class ActionError(ValueError):
    """A request the server understood but refuses to perform."""


def xdotool(*args: str) -> None:
    result = subprocess.run(
        ["xdotool", *args], capture_output=True, timeout=COMMAND_TIMEOUT, env={**os.environ, "DISPLAY": ":0"}
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise ActionError(f"xdotool {args[0]} failed: {detail or 'no output'}")


def do_health(_: dict) -> dict:
    return {"ok": True, "screen": [SCREEN_WIDTH, SCREEN_HEIGHT]}


def do_screenshot(_: dict) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        path = handle.name
    try:
        result = subprocess.run(
            ["scrot", "-o", path],
            capture_output=True,
            timeout=COMMAND_TIMEOUT,
            env={**os.environ, "DISPLAY": ":0"},
        )
        if result.returncode != 0:
            detail = result.stderr.decode(errors="replace").strip()
            raise ActionError(f"screenshot failed: {detail or 'no output'}")
        with open(path, "rb") as image:
            return image.read()
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


def do_click(payload: dict) -> dict:
    x = clamp(payload.get("x"), SCREEN_WIDTH)
    y = clamp(payload.get("y"), SCREEN_HEIGHT)
    button = str(payload.get("button", "left"))
    number = {"left": "1", "middle": "2", "right": "3"}.get(button)
    if number is None:
        raise ActionError(f"unknown button {button!r}; use left, middle or right")
    xdotool("mousemove", str(x), str(y))
    xdotool("click", number)
    return {"ok": True, "x": x, "y": y, "button": button}


def do_double_click(payload: dict) -> dict:
    x = clamp(payload.get("x"), SCREEN_WIDTH)
    y = clamp(payload.get("y"), SCREEN_HEIGHT)
    xdotool("mousemove", str(x), str(y))
    xdotool("click", "--repeat", "2", "--delay", "100", "1")
    return {"ok": True, "x": x, "y": y}


def do_type(payload: dict) -> dict:
    text = payload.get("text")
    if not isinstance(text, str) or not text:
        raise ActionError("type needs a non-empty 'text' string")
    if len(text) > MAX_TYPE_TEXT:
        raise ActionError(f"text longer than {MAX_TYPE_TEXT} characters; type it in pieces")
    # stdin keeps private user input out of process arguments and exception messages.
    result = subprocess.run(
        ["xdotool", "type", "--clearmodifiers", "--delay", "1", "--file", "-"],
        input=text.encode(),
        capture_output=True,
        timeout=COMMAND_TIMEOUT,
        env={**os.environ, "DISPLAY": ":0"},
    )
    if result.returncode != 0:
        raise ActionError("Could not type into the desktop.")
    return {"ok": True, "typed": len(text)}


def do_key(payload: dict) -> dict:
    keys = payload.get("keys")
    if not isinstance(keys, str) or not keys.strip():
        raise ActionError("key needs a 'keys' string, for example 'Return' or 'ctrl+a'")
    tokens = keys.split()
    for token in tokens:
        if not KEY_TOKEN.match(token):
            raise ActionError(f"unsupported key {token!r}; use xdotool names like Return, Tab, ctrl+a")
    xdotool("key", "--delay", "40", "--", *tokens)
    return {"ok": True, "keys": tokens}


def do_scroll(payload: dict) -> dict:
    x = clamp(payload.get("x", SCREEN_WIDTH // 2), SCREEN_WIDTH)
    y = clamp(payload.get("y", SCREEN_HEIGHT // 2), SCREEN_HEIGHT)
    direction = str(payload.get("direction", "down"))
    button = SCROLL_BUTTONS.get(direction)
    if button is None:
        raise ActionError(f"unknown direction {direction!r}; use up, down, left or right")
    try:
        amount = max(1, min(MAX_SCROLL, int(payload.get("amount", 3))))
    except (TypeError, ValueError) as error:
        raise ActionError(f"not an amount: {payload.get('amount')!r}") from error
    xdotool("mousemove", str(x), str(y))
    xdotool("click", "--repeat", str(amount), "--delay", "50", button)
    return {"ok": True, "direction": direction, "amount": amount}


def do_move(payload: dict) -> dict:
    x = clamp(payload.get("x"), SCREEN_WIDTH)
    y = clamp(payload.get("y"), SCREEN_HEIGHT)
    xdotool("mousemove", str(x), str(y))
    return {"ok": True, "x": x, "y": y}


def do_drag(payload: dict) -> dict:
    x1 = clamp(payload.get("x1"), SCREEN_WIDTH)
    y1 = clamp(payload.get("y1"), SCREEN_HEIGHT)
    x2 = clamp(payload.get("x2"), SCREEN_WIDTH)
    y2 = clamp(payload.get("y2"), SCREEN_HEIGHT)
    xdotool("mousemove", str(x1), str(y1))
    xdotool("mousedown", "1")
    try:
        # An intermediate step because some toolkits ignore a drag that teleports.
        xdotool("mousemove", str((x1 + x2) // 2), str((y1 + y2) // 2))
        xdotool("mousemove", str(x2), str(y2))
    finally:
        # The button must come back up even when a move fails, or the desktop is left in a
        # drag state that breaks every later click.
        xdotool("mouseup", "1")
    return {"ok": True, "from": [x1, y1], "to": [x2, y2]}


ROUTES = {
    ("GET", "/health"): do_health,
    ("GET", "/screenshot"): do_screenshot,
    ("POST", "/click"): do_click,
    ("POST", "/double_click"): do_double_click,
    ("POST", "/type"): do_type,
    ("POST", "/key"): do_key,
    ("POST", "/scroll"): do_scroll,
    ("POST", "/move"): do_move,
    ("POST", "/drag"): do_drag,
}


class Handler(BaseHTTPRequestHandler):
    # Silence the default per-request stderr line; container logs stay readable.
    def log_message(self, *_: object) -> None:
        return

    def _authorized(self) -> bool:
        # No token means the container was started wrong; refuse everything rather than
        # running an open desktop-control server, even on a loopback-published port.
        if not TOKEN:
            return False
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, f"Bearer {TOKEN}")

    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_json(self, status: int, payload: dict) -> None:
        self._respond(status, json.dumps(payload).encode(), "application/json")

    def _handle(self, method: str) -> None:
        if not self._authorized():
            self._respond_json(401, {"error": "missing or wrong bearer token"})
            return
        action = ROUTES.get((method, self.path))
        if action is None:
            self._respond_json(404, {"error": f"no such action: {method} {self.path}"})
            return

        payload: dict = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > MAX_BODY:
                self._respond_json(413, {"error": "request body too large"})
                return
            raw = self.rfile.read(length) if length else b"{}"
            try:
                parsed = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._respond_json(400, {"error": "body is not valid JSON"})
                return
            if not isinstance(parsed, dict):
                self._respond_json(400, {"error": "body must be a JSON object"})
                return
            payload = parsed

        try:
            result = action(payload)
        except ActionError as error:
            self._respond_json(400, {"error": str(error)})
            return
        except subprocess.TimeoutExpired:
            self._respond_json(504, {"error": "the desktop did not respond in time"})
            return

        if isinstance(result, bytes):
            self._respond(200, result, "image/png")
        else:
            self._respond_json(200, result)

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        self._handle("POST")


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
