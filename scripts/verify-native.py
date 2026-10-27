"""Launch the actual Wavefunction.app, verify its frozen API/tools, restart, and clean up.

Refuses to use pre-existing Wavefunction data. Never prints the per-launch backend token.
"""

import argparse
import json
import os
import plistlib
import re
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def until(operation, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = operation()
            if value:
                return value
        except (httpx.HTTPError, OSError, ValueError):
            pass
        time.sleep(0.2)
    raise RuntimeError("Native app verification timed out.")


def sidecar(parent):
    listing = subprocess.run(
        ["ps", "-ax", "-o", "pid=,args="], capture_output=True, text=True, check=True
    ).stdout
    ids = []
    info = None
    for line in listing.splitlines():
        if not re.search(rf"--parent-pid\s+{parent}(?:\s|$)", line):
            continue
        port = re.search(r"--port\s+(\d+)", line)
        token = re.search(r"--token\s+([a-f0-9]{64})", line)
        if port and token:
            ids.append(int(line.split()[0]))
            info = (int(port[1]), token[1])
    return (ids, *info) if info else None


def main(args):
    app = args.app.resolve()
    metadata = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    executable = app / "Contents/MacOS" / metadata["CFBundleExecutable"]
    assert executable.is_file(), "Build Wavefunction.app first."
    data = Path.home() / "Library/Application Support/app.harness.coding"
    assert not data.exists(), (
        "Existing Wavefunction data is present; refusing to test against a user's workspace."
    )
    extras = [
        Path.home() / p
        for p in [
            "Library/Caches/app.harness.coding",
            "Library/WebKit/app.harness.coding",
            "Library/Preferences/app.harness.coding.plist",
        ]
    ]
    absent_extras = [p for p in extras if not p.exists()]
    owner = str(uuid4())
    data.mkdir(parents=True)
    marker = data / ".verification-owner"
    marker.write_text(owner)
    process = None
    client = None
    sidecar_ids = []
    ports = []
    result = {}
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
            "AI_GATEWAY_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "TAVILY_API_KEY",
        }
        and not key.startswith(("MCP_", "WAVEFUNCTION_"))
    }
    with TemporaryDirectory(prefix="harness-native-") as temporary:
        root = Path(temporary)
        log = (root / "native.log").open("w")

        def start():
            nonlocal process, client
            process = subprocess.Popen([str(executable)], env=env, stdout=log, stderr=log)
            children, port, token = until(lambda: sidecar(process.pid))
            sidecar_ids.extend(children)
            ports.append(port)
            client = httpx.Client(
                base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}, timeout=30
            )
            until(lambda: client.get("/health").is_success)
            return client

        def stop():
            nonlocal client, process
            if client:
                client.close()
                client = None
            if process and process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            process = None
            until(lambda: not any(alive(pid) for pid in sidecar_ids), seconds=15)

        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else None

        try:
            start()
            with httpx.Client(base_url=str(client.base_url)) as unauthenticated:
                assert unauthenticated.get("/api/coding/workspace").status_code == 401
            first = request("GET", "/api/coding/workspace")
            folder = root / "repository"
            folder.mkdir()
            (folder / "app.py").write_text("print('before')\n")
            thread = request(
                "POST", "/api/coding/threads", json={"title": "Native verification", "folder": str(folder)}
            )
            second = request("POST", "/api/coding/threads", json={"title": "Independent thread"})
            assert thread["conversation_id"] != second["conversation_id"]
            workspace = request("GET", "/api/coding/workspace")
            assert workspace["agent_id"] and {thread["id"], second["id"]} <= {
                item["id"] for item in workspace["threads"]
            }
            assert len(request("GET", "/api/skills")) >= 3
            base = f"/api/coding/threads/{thread['id']}"
            content = request("GET", base + "/file", params={"path": "app.py"})
            request(
                "PUT",
                base + "/file",
                json={
                    "path": "app.py",
                    "content": "print('NATIVE-TOOLS-PASS')\n",
                    "version": content["version"],
                },
            )
            command = request("POST", base + "/terminal", json={"command": "python3 app.py"})
            assert command["exit_code"] == 0 and "NATIVE-TOOLS-PASS" in command["stdout"], command
            run = request("POST", base + "/messages", json={"content": "Reply to native verification"})
            completed = until(
                lambda: (
                    row
                    if (row := request("GET", f"/api/runs/{run['id']}"))["status"]
                    in {"completed", "failed", "cancelled"}
                    else None
                )
            )
            assert completed["status"] == "completed" and completed["output"]["text"], completed.get("error")
            stop()
            start()
            persisted = request("GET", base)
            assert len(persisted["runs"]) == 1 and persisted["runs"][0]["status"] == "completed"
            assert request("GET", "/api/coding/workspace")["agent_id"] == first["agent_id"]
            result = {
                "native_app": True,
                "frozen_backend": True,
                "bearer_auth": True,
                "one_agent": True,
                "independent_threads": True,
                "bundled_skills": True,
                "real_file_edit": True,
                "real_terminal": True,
                "conversation_runtime": True,
                "restart_persistence": True,
            }
        finally:
            stop()
            log.close()
            assert marker.read_text() == owner
            shutil.rmtree(data)
            for path in absent_extras:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            for port in ports:
                with socket.socket() as check:
                    assert check.connect_ex(("127.0.0.1", port)) != 0, (
                        f"Native test port {port} remained listening."
                    )
            result["cleanup"] = {
                "app_stopped": process is None,
                "sidecars_stopped": not any(alive(pid) for pid in sidecar_ids),
                "ports_closed": True,
                "synthetic_app_data_removed": not data.exists(),
            }
        assert all(result["cleanup"].values())
    result["cleanup"]["temporary_repository_removed"] = not root.exists()
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--app", type=Path, default=Path("src-tauri/target/release/bundle/macos/Wavefunction.app")
    )
    main(parser.parse_args())
