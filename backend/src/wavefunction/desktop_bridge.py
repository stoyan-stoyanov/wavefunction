"""The desktop's browser opening the servers its chat runs on the Mac, at the same address.

A dev server the agent starts in a terminal listens on the Mac's 127.0.0.1. The desktop is a
container, whose 127.0.0.1 is its own, and it cannot connect out to the Mac. So the desktop's
action server mirrors the ports given here (`Bridge` in sandbox_image/server.py), and this side
keeps a few connections open into it. When the browser opens a mirrored port, one of them
carries that connection, and it is joined here to the same port on the Mac.

Only ports that a server in one of the chat's terminals listens on are mirrored and joined: the
desktop never reaches the app's own engine or anything else on the Mac. Terminals run in the
sandbox, so a page reaches no more of the Mac through such a server than the chat itself can,
and Firefox in the desktop refuses requests to these addresses from pages on the internet
(sandbox_image/firefox.js).
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
from collections.abc import Iterable
from typing import Any

import httpx

# Connections kept open into a desktop that mirrors ports: a page load opens several at once.
IDLE = 4
# A page keeps a handful open at a time. Past this a desktop is misbehaving: it waits rather
# than flooding the engine with connections.
MOST_CONNECTIONS = 64
# The most the desktop mirrors (MAX_MIRRORED in sandbox_image/server.py); the lowest ports win.
MOST_PORTS = 32


def served_ports(terminals: Iterable[Any]) -> dict[int, str]:
    """The TCP ports the processes of these running terminals listen on, each with the loopback
    address that reaches that listener. Exact, not "localhost": another program can hold the
    same port on the other address, as a dev server on *:3000 over IPv6 beside the chat's on
    127.0.0.1:3000, and "localhost" tries ::1 first."""
    pids: set[int] = set()
    for terminal in terminals:
        pids.update(pid for pid in (terminal.process.pid, terminal.shell_pid) if pid)
        # Fresh rather than the 5 s record: a server started a moment ago is the one to open.
        pids.update(terminal._capture_children())
    if not pids:
        return {}
    try:
        listed = subprocess.run(
            [
                "/usr/sbin/lsof",
                "-nP",
                "-a",
                "-p",
                ",".join(map(str, sorted(pids))),
                "-iTCP",
                "-sTCP:LISTEN",
                "-Ftn",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    served: dict[int, str] = {}
    family = ""
    for line in listed.splitlines():
        if line.startswith("t"):  # Each listener's type comes before its name.
            family = line[1:]
        elif line.startswith("n"):
            host, _, port = line[1:].rpartition(":")
            wildcard = "::1" if family == "IPv6" else "127.0.0.1"
            address = {"127.0.0.1": "127.0.0.1", "[::1]": "::1", "*": wildcard}.get(host)
            if address and port.isdigit():
                served.setdefault(int(port), address)
    return served


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (OSError, RuntimeError):
        pass


class DesktopBridge:
    """One desktop's mirrored ports and the connections that carry them to the Mac."""

    def __init__(self, port: int, token: str):
        # The desktop's action server, on the Mac's loopback.
        self.port, self.token = port, token
        # The chat's servers the desktop mirrors, by port, with the address each listens on.
        self.mirrored: dict[int, str] = {}
        self.idle: set[asyncio.Task] = set()
        self.tasks: set[asyncio.Task] = set()
        self.closed = False

    async def mirror(self, client: httpx.AsyncClient, ports: dict[int, str]) -> None:
        """Mirror exactly these ports into the desktop. A desktop that can't is left as it was:
        the action the agent asked for matters more than the pages it may open later. A port it
        refused is asked for again at the next action, as when a restarted server's port is
        still being released."""
        if self.closed:
            return
        ports = dict(sorted(ports.items())[:MOST_PORTS])
        if ports != self.mirrored:
            try:
                response = await client.post("/bridge/ports", json={"ports": sorted(ports)})
                response.raise_for_status()
                # Never more than was asked: the desktop runs whatever its pages run.
                listening = set(response.json()["ports"])
                self.mirrored = {port: address for port, address in ports.items() if port in listening}
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                return
        self._top_up()

    def _top_up(self) -> None:
        """Keep IDLE connections waiting in the desktop while it mirrors any port. One that
        failed is replaced at the next mirror, so a desktop that went away isn't retried in a loop."""
        while (
            not self.closed and self.mirrored and len(self.idle) < IDLE and len(self.tasks) < MOST_CONNECTIONS
        ):
            task = asyncio.get_running_loop().create_task(self._carry())
            self.idle.add(task)
            self.tasks.add(task)
            task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task) -> None:
        self.idle.discard(task)
        self.tasks.discard(task)

    async def _carry(self) -> None:
        """Wait inside the desktop for a browser connection, then join it to the Mac."""
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        except OSError:
            return
        try:
            writer.write(
                f"POST /bridge/connect HTTP/1.1\r\nHost: desktop\r\nAuthorization: Bearer {self.token}\r\n"
                "Content-Length: 0\r\n\r\n".encode()
            )
            await writer.drain()
            if (await reader.readline()).split()[1:2] != [b"200"]:
                return
            while (await reader.readline()).strip():
                pass  # The response's headers.
            wanted = await reader.readline()  # Nothing comes until a browser connection does.
            if current := asyncio.current_task():
                self.idle.discard(current)
            port = int(wanted) if wanted.strip().isdigit() else 0
            if (address := self.mirrored.get(port)) is None:
                return
            self._top_up()
            mac_reader, mac_writer = await asyncio.open_connection(address, port)
            try:
                await asyncio.gather(_pipe(reader, mac_writer), _pipe(mac_reader, writer))
            finally:
                mac_writer.close()
        except (OSError, ValueError):  # ValueError: a line longer than the reader's limit.
            pass
        finally:
            writer.close()

    async def close(self) -> None:
        self.closed = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*tasks, return_exceptions=True)
