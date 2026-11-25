"""Check the Linux desktop features of an installed Wavefunction under a virtual display.

linux-from-scratch.sh runs this in a bare Ubuntu container, as an ordinary user inside
`dbus-run-session`. It starts Xvfb, a window manager, a notification server (dunst), a screen
recording and a stand-in OpenAI-compatible model, then drives the real app with real key presses
and clicks (xdotool) and reads its page through tauri-driver. Prints a line per check and keeps
screenshots, the recording and logs in --out. Standard library only.
"""

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DISPLAY = ":99"
SIZE = "1920x1080"
MODEL_PORT, UPDATE_PORT, DRIVER_PORT = 8766, 8765, 4444
APPARMOR = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
ELEMENT = "element-6066-11e4-a52e-4f735466cecf"

parser = argparse.ArgumentParser()
parser.add_argument("--app", required=True, help="The installed app's binary.")
parser.add_argument("--update", type=Path, required=True, help="Two AppImages and latest.json.")
parser.add_argument("--driver", required=True, help="tauri-driver.")
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
OUT = args.out
results = []
started = []


def run(*command, check=True):
    return subprocess.run(command, check=check, capture_output=True, text=True).stdout.strip()


def start(name, *command, **kwargs):
    log = (OUT / f"{name}.log").open("w")
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **kwargs)
    started.append(process)
    return process


def until(what, check, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if value := check():
                return value
        except Exception:
            pass
        time.sleep(0.5)
    raise AssertionError(f"timed out after {seconds}s waiting for {what}")


def alive(pid):
    """Whether `pid` runs. A process that has exited is a zombie until its parent reaps it, so reap
    this script's own children first and count a zombie as gone."""
    for process in started:
        process.poll()
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def screen(name):
    run("ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-video_size", SIZE, "-i", DISPLAY,
        "-frames:v", "1", str(OUT / f"{name}.png"), check=False)  # fmt: skip


def attempt(name, check):
    try:
        results.append((name, True, check() or ""))
    except Exception as error:
        traceback.print_exc()
        screen("failed-" + re.sub(r"\W+", "-", name))
        results.append((name, False, f"{type(error).__name__}: {error}"))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def engine():
    """(engine pid, app pid, port, token) of the running app's engine, read from its arguments."""
    for line in run("ps", "-ax", "-o", "pid=,args=").splitlines():
        parent = re.search(r"--parent-pid\s+(\d+)", line)
        port = re.search(r"--port\s+(\d+)", line)
        token = re.search(r"--token\s+([a-f0-9]{64})", line)
        if parent and port and token and alive(int(parent[1])):
            return int(line.split()[0]), int(parent[1]), int(port[1]), token[1]
    return None


class Engine:
    def __init__(self):
        self.pid, self.app, port, self.token = until("the app's engine", engine, 90)
        self.base = f"http://127.0.0.1:{port}"
        until("the engine to answer", lambda: self.call("GET", "/health") is not None, 60)

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            self.base + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
        return json.loads(content) if content else {}

    def stop(self):
        """Quit the app as the system would at logout, and wait for its engine to follow."""
        if alive(self.app):
            os.kill(self.app, signal.SIGTERM)
        until("the app to stop", lambda: not alive(self.app), 20)
        until("its engine to stop", lambda: not alive(self.pid), 20)


class Driver:
    """The few WebDriver calls these checks need, against tauri-driver."""

    def __init__(self, application):
        until("tauri-driver", lambda: socket.create_connection(("127.0.0.1", DRIVER_PORT), 1).close() or True)
        options = {"browserName": "wry", "tauri:options": {"application": application}}
        self.id = self.call("POST", "/session", {"capabilities": {"alwaysMatch": options}})["sessionId"]

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{DRIVER_PORT}{path}",
            method=method,
            data=json.dumps(body or {}).encode() if method == "POST" else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)["value"]
        except urllib.error.HTTPError as error:
            raise AssertionError(f"WebDriver {method} {path}: {error.read().decode()[:400]}") from None

    def script(self, source, *arguments):
        return self.call(
            "POST", f"/session/{self.id}/execute/sync", {"script": source, "args": list(arguments)}
        )

    def find(self, selector, text=None):
        """The first element matching `selector` (whose text contains `text`), or None."""
        return self.script(
            "return [...document.querySelectorAll(arguments[0])]"
            ".find(e => !arguments[1] || e.textContent.includes(arguments[1])) || null",
            selector,
            text,
        )

    def click(self, element):
        """Click through the page: WebKitWebDriver's own element click doesn't reach the app's buttons
        under Xvfb. The checks press their keys and the terminal's double-click with xdotool."""
        self.script("arguments[0].click()", element)

    def rect(self, element):
        return self.call("GET", f"/session/{self.id}/element/{element[ELEMENT]}/rect")

    def close(self):
        with contextlib.suppress(Exception):
            self.call("DELETE", f"/session/{self.id}")


class Model(BaseHTTPRequestHandler):
    """A stand-in OpenAI-compatible model whose every answer is one short reply."""

    def log_message(self, format, *arguments):
        with (OUT / "model.log").open("a") as log:
            log.write(format % arguments + "\n")

    def reply(self, status, kind, payload):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        models = {"object": "list", "data": [{"id": "stand-in", "object": "model", "owned_by": "test"}]}
        self.reply(200, "application/json", json.dumps(models).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        message = {"role": "assistant", "content": "Done: a reply from the stand-in model."}
        if not body.get("stream"):
            choice = {"index": 0, "message": message, "finish_reason": "stop"}
            answer = {"id": "x", "object": "chat.completion", "created": 0, "model": "stand-in",
                      "choices": [choice], "usage": usage}  # fmt: skip
            return self.reply(200, "application/json", json.dumps(answer).encode())
        chunks = [
            {"choices": [{"index": 0, "delta": message, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": usage},
        ]
        stream = b"".join(
            b"data: %s\n\n"
            % json.dumps(
                {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "stand-in", **chunk}
            ).encode()
            for chunk in chunks
        )
        self.reply(200, "text/event-stream", stream + b"data: [DONE]\n\n")


def check_sandbox():
    """Ubuntu's AppArmor stops bwrap: the app says so and how to fix it, then works once fixed."""
    restricted = APPARMOR.exists() and APPARMOR.read_text().strip() == "1"
    problem = ""
    if restricted:
        start("app-restricted", args.app)
        api = Engine()
        grants = api.call("GET", "/api/grants")
        problem = grants.get("sandbox_problem") or ""
        screen("sandbox-restricted")
        api.stop()
        assert not grants["sandbox_ok"] and "apparmor" in problem.lower(), grants
        run("sudo", "sysctl", "-w", "kernel.apparmor_restrict_unprivileged_userns=0")
    start("app-sandbox", args.app)
    api = Engine()
    grants = api.call("GET", "/api/grants")
    api.stop()
    assert grants["sandbox_ok"], grants
    return f"said: {problem!r}" if restricted else "this kernel lets bwrap make user namespaces"


def window_id():
    return until(
        "the window", lambda: run("xdotool", "search", "--onlyvisible", "--name", "^Wavefunction$").split()[0]
    )


def check_desktop():
    """Notifications, the terminal's copy and paste, F11 and Ctrl+Q, in one run of the app."""
    start("tauri-driver", args.driver, "--port", str(DRIVER_PORT))
    driver = Driver(args.app)
    api = Engine()
    window = window_id()
    base_url = f"http://127.0.0.1:{MODEL_PORT}/v1"
    api.call("PUT", "/api/connections/default-model",
             {"model": {"provider": "openai-compatible", "model": "stand-in", "base_url": base_url}})  # fmt: skip
    chats = {}
    for title in ("First chat", "Second chat"):
        folder = Path.home() / "projects" / title.split()[0].lower()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "README.md").write_text(f"# {title}\n")
        # Setup isn't done, and the sidebar doesn't show, until there is a project.
        project = api.call("POST", "/api/coding/projects", {"name": title.split()[0], "folder": str(folder)})
        chats[title] = api.call("POST", "/api/coding/threads", {"title": title, "project_id": project["id"]})
    # The page read the model settings at launch.
    driver.call("POST", f"/session/{driver.id}/refresh")
    until("both chats in the sidebar", lambda: driver.find("[aria-current], aside *", "Second chat"), 30)
    screen("chats")

    def notification():
        driver.click(until("the first chat", lambda: driver.find("aside a, aside button", "First chat")))
        monitor = start("dbus-monitor", "dbus-monitor", "--session",
                        "interface='org.freedesktop.Notifications'")  # fmt: skip
        time.sleep(1)
        run("xdotool", "windowminimize", "--sync", window)
        message = api.call(
            "POST", f"/api/coding/threads/{chats['Second chat']['id']}/messages", {"content": "Hello"}
        )
        ended = until(
            "the task to end",
            lambda: (
                (r := api.call("GET", f"/api/runs/{message['id']}"))["status"]
                in {"completed", "failed", "cancelled"}
                and r
            ),
            120,
        )
        until("the notification on screen", lambda: int(run("dunstctl", "count", "displayed")) > 0, 30)
        screen("notification")
        sent = (OUT / "dbus-monitor.log").read_text()
        assert "Second chat" in sent, "the notification doesn't name its chat"
        run("dunstctl", "action", "0")
        until("the window back in front", lambda: int(run("xdotool", "getactivewindow")) == int(window), 20)
        until(
            "the second chat on screen",
            lambda: (
                "Second chat"
                in (driver.script("return document.querySelector('aside [aria-current]')?.textContent") or "")
            ),
            20,
        )
        screen("notification-opened")
        monitor.terminate()
        return f"task {ended['status']}; clicking the notification opened its chat"

    def terminal():
        # The workbench starts closed, and its Terminal tab has no terminal until one is made.
        if not driver.find("[role=tab]", "Terminal"):
            driver.click(until("Show workbench", lambda: driver.find('[aria-label="Show workbench"]'), 20))
        driver.click(until("the Terminal tab", lambda: driver.find("[role=tab]", "Terminal"), 20))
        driver.click(until("New Terminal", lambda: driver.find("button", "New Terminal"), 20))
        until("the terminal", lambda: driver.find(".xterm-screen"), 30)
        time.sleep(3)
        run("xdotool", "windowactivate", "--sync", window)
        driver.script("document.querySelector('.xterm-helper-textarea').focus()")
        pasted = f"PASTED-{secrets.token_hex(3)}"
        subprocess.run(
            ["xclip", "-selection", "clipboard"], input=f"echo {pasted} > pasted.txt", text=True, check=True
        )
        run("xdotool", "key", "ctrl+shift+v")
        time.sleep(1)
        run("xdotool", "key", "Return")
        until(
            "the pasted command to run",
            lambda: any(pasted in p.read_text() for p in (Path.home() / "projects").rglob("pasted.txt")),
            20,
        )
        copied = f"COPIED-{secrets.token_hex(3)}"
        run("xdotool", "type", "--delay", "40", f"clear; echo {copied}")
        run("xdotool", "key", "Return")
        time.sleep(2)
        row = driver.rect(driver.find(".xterm-rows > div"))
        geometry = dict(
            line.split("=") for line in run("xdotool", "getwindowgeometry", "--shell", window).splitlines()
        )
        x, y = int(geometry["X"]) + int(row["x"]) + 30, int(geometry["Y"]) + int(row["y"] + row["height"] / 2)
        run("xdotool", "mousemove", str(x), str(y), "click", "--repeat", "2", "--delay", "100", "1")
        run("xdotool", "key", "ctrl+shift+c")
        until(
            "the copied text on the clipboard",
            lambda: run("xclip", "-o", "-selection", "clipboard") == copied,
            10,
        )
        screen("terminal")
        return "Ctrl+Shift+V pasted and ran a command; Ctrl+Shift+C copied a selection"

    def fullscreen():
        state = lambda: run("xprop", "-id", window, "_NET_WM_STATE")  # noqa: E731
        run("xdotool", "windowactivate", "--sync", window)
        run("xdotool", "key", "F11")
        until("full screen", lambda: "_NET_WM_STATE_FULLSCREEN" in state(), 10)
        screen("fullscreen")
        run("xdotool", "key", "F11")
        until("the window back from full screen", lambda: "_NET_WM_STATE_FULLSCREEN" not in state(), 10)
        return "F11 entered and left full screen, with the terminal focused"

    def quit():
        run("xdotool", "windowactivate", "--sync", window)
        run("xdotool", "key", "ctrl+q")
        until("the app to quit", lambda: not alive(api.app), 20)
        until("its engine to stop", lambda: not alive(api.pid), 20)
        return "Ctrl+Q quit the app and its engine"

    for name, check in [
        ("notification opens its chat", notification),
        ("terminal copy and paste", terminal),
        ("F11 full screen", fullscreen),
        ("Ctrl+Q quits", quit),
    ]:
        attempt(name, check)
    driver.close()
    if engine():
        Engine().stop()


def check_update():
    """The AppImage finds a newer version, replaces itself with it and restarts."""
    latest = json.loads((args.update / "latest.json").read_text())["version"]
    newer = args.update / f"Wavefunction-{latest}.AppImage"
    older = next(p for p in args.update.glob("Wavefunction-*.AppImage") if p != newer)
    app = Path.home() / "Applications/Wavefunction.AppImage"
    app.parent.mkdir(exist_ok=True)
    shutil.copy(older, app)
    app.chmod(0o755)
    start("update-server", "python3", "-m", "http.server", str(UPDATE_PORT), "--bind", "127.0.0.1",
          "--directory", str(args.update))  # fmt: skip
    driver = Driver(str(app))
    Engine()
    button = until("the update button", lambda: driver.find(".sidebar-update"), 90)
    label = driver.script("return arguments[0].textContent", button).strip()
    screen("update-offered")
    driver.click(button)
    until("the AppImage to be replaced", lambda: sha256(app) == sha256(newer), 180)
    driver.close()
    updated = Engine()
    time.sleep(3)
    screen("updated")
    updated.stop()
    return f"{label!r}: the file was replaced and the app restarted"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["DISPLAY"] = DISPLAY
    start("xvfb", "Xvfb", DISPLAY, "-screen", "0", f"{SIZE}x24", "-nolisten", "tcp")
    until("the display", lambda: subprocess.run(["xdpyinfo"], capture_output=True).returncode == 0)
    start("openbox", "openbox")
    start("dunst", "dunst")
    recording = start("recording", "ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-video_size", SIZE,
                      "-framerate", "5", "-i", DISPLAY, "-c:v", "libx264", "-preset", "ultrafast",
                      "-pix_fmt", "yuv420p", str(OUT / "desktop.mp4"), stdin=subprocess.PIPE)  # fmt: skip
    model = ThreadingHTTPServer(("127.0.0.1", MODEL_PORT), Model)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    try:
        attempt("sandbox, and the AppArmor message", check_sandbox)
        attempt("desktop session", check_desktop)
        attempt("AppImage updates itself", check_update)
    finally:
        model.shutdown()
        recording.communicate(b"q", timeout=30)
        for process in reversed(started):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
    width = max(len(name) for name, _, _ in results)
    lines = [f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}" for name, ok, detail in results]
    print("\n".join(lines))
    (OUT / "results.txt").write_text("\n".join(lines) + "\n")
    raise SystemExit(0 if all(ok for _, ok, _ in results) else 1)


if __name__ == "__main__":
    main()
