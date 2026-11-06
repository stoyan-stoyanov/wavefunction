"""The action server that runs inside the desktop container.

Standard library only, on purpose: the image build stays a fast apt-get with no pip step, and
the host's test suite imports this file directly and fakes `subprocess.run` — which is the only
way the endpoint logic gets tested at all, since CI has no Docker.

Every action shells out to xdotool with an argv list, never a shell string, so nothing the
model writes is ever parsed by a shell. Coordinates are clamped to the screen and key names are
whitelisted — not because the container is precious, but because a request that xdotool cannot
parse should fail loudly here rather than time out silently.

It also mirrors the servers the chat runs on the Mac (`Bridge`), so the browser here opens them
at the address they have there.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import queue
import re
import select
import socket
import subprocess
import tempfile
import threading
import time
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


# -- the chat's servers ----------------------------------------------------------------------
#
# A server the chat runs on the Mac listens on the Mac's 127.0.0.1, which in here is this
# container, and the container cannot connect out to the Mac. So the app tells this server
# which ports to mirror (POST /bridge/ports) and keeps a few connections open to it
# (POST /bridge/connect). A browser connection to a mirrored port takes one of them, and the
# app joins it to the same port on the Mac. The page keeps its address, Host header and all,
# so a dev server's host check and its live-reload socket work as they do on the Mac.

MIRROR_HOSTS = ("127.0.0.1", "::1")
MAX_MIRRORED = 32
# How long a browser connection waits for one of the app's connections before it is dropped.
HANDOVER_SECONDS = 10


def splice(first: socket.socket, second: socket.socket) -> None:
    """Copy bytes both ways until both sides have finished, passing on each half-close."""
    reading = {first: second, second: first}
    try:
        while reading:
            ready, _, _ = select.select(list(reading), [], [])
            for source in ready:
                data = source.recv(65536)
                if data:
                    reading[source].sendall(data)
                    continue
                with contextlib.suppress(OSError):
                    reading[source].shutdown(socket.SHUT_WR)
                del reading[source]
    except OSError:
        pass
    finally:
        first.close()
        second.close()


def still_open(connection: socket.socket) -> bool:
    """Whether the app's end of an idle connection is still there: it sends nothing until used."""
    try:
        ready, _, _ = select.select([connection], [], [], 0)
        return not ready or connection.recv(1, socket.MSG_PEEK) != b""
    except OSError:
        return False


class Waiting:
    """One of the app's connections, waiting for a browser connection to carry."""

    def __init__(self, connection: socket.socket):
        self.connection = connection
        self.taken = threading.Event()
        self.lock = threading.Lock()
        self.closed = False
        self.browser: socket.socket | None = None
        self.port = 0

    def take(self, browser: socket.socket, port: int) -> bool:
        with self.lock:
            if self.closed or self.taken.is_set() or not still_open(self.connection):
                self.closed = True
                return False
            self.browser, self.port = browser, port
            self.taken.set()
            return True

    def give_up(self) -> bool:
        with self.lock:
            if self.taken.is_set():
                return False
            self.closed = True
            return True


class Bridge:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.listeners: dict[int, list[socket.socket]] = {}
        self.waiting: queue.Queue[Waiting] = queue.Queue()

    def mirror(self, payload: dict) -> dict:
        ports = payload.get("ports")
        if not isinstance(ports, list) or len(ports) > MAX_MIRRORED:
            raise ActionError(f"ports must be a list of at most {MAX_MIRRORED} port numbers")
        if any(not isinstance(port, int) or not 0 < port < 65536 or port == PORT for port in ports):
            raise ActionError(f"a port must be a number from 1 to 65535, other than {PORT}")
        refused = []
        with self.lock:
            for port in set(self.listeners) - set(ports):
                for listener in self.listeners.pop(port):
                    listener.close()
            for port in sorted(set(ports) - set(self.listeners)):
                opened, error = self._listen(port)
                if opened:
                    self.listeners[port] = opened
                else:
                    refused.append({"port": port, "error": error})
            return {"ports": sorted(self.listeners), "refused": refused}

    def _listen(self, port: int) -> tuple[list[socket.socket], str]:
        opened, error = [], ""
        for host in MIRROR_HOSTS:
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            listener = None
            try:
                listener = socket.socket(family, socket.SOCK_STREAM)
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((host, port))
                listener.listen(64)
            except OSError as failure:
                if listener is not None:
                    listener.close()
                error = str(failure)
                continue
            opened.append(listener)
            threading.Thread(target=self._accept, args=(listener, port), daemon=True).start()
        return opened, error

    def _accept(self, listener: socket.socket, port: int) -> None:
        while True:
            try:
                browser, _ = listener.accept()
            except OSError:
                return  # No longer mirrored.
            threading.Thread(target=self._hand_over, args=(browser, port), daemon=True).start()

    def _hand_over(self, browser: socket.socket, port: int) -> None:
        deadline = time.monotonic() + HANDOVER_SECONDS
        while (left := deadline - time.monotonic()) > 0:
            try:
                waiting = self.waiting.get(timeout=left)
            except queue.Empty:
                break
            if waiting.take(browser, port):
                return
        browser.close()

    def carry(self, connection: socket.socket) -> None:
        """Hold one of the app's connections until a browser connection takes it, then join them."""
        waiting = Waiting(connection)
        self.waiting.put(waiting)
        while not waiting.taken.wait(30):
            if not still_open(connection) and waiting.give_up():
                return
        assert waiting.browser is not None
        try:
            connection.sendall(f"{waiting.port}\n".encode())
        except OSError:
            waiting.browser.close()
            return
        splice(waiting.browser, connection)


BRIDGE = Bridge()


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
    ("POST", "/bridge/ports"): BRIDGE.mirror,
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
        if (method, self.path) == ("POST", "/bridge/connect"):
            # The rest of this connection is raw bytes: the port a browser connection wants,
            # then that connection's traffic both ways.
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            BRIDGE.carry(self.connection)
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
