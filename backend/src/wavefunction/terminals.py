"""Owned, bounded PTY sessions. Transport reconnects never launch another shell."""

from __future__ import annotations

import asyncio
import codecs
import errno
import fcntl
import logging
import os
import pty
import signal
import struct
import subprocess
import sys
import termios
import threading
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from .repository import now_iso
from .sandbox import _end_tagged

BUFFER_CHARS = 1024 * 1024
OUTPUT_CHARS = 64 * 1024
# How often an open terminal records its processes. Closing or exiting looks again, which finds
# everything still in the terminal's session or below its shell; this is for a process that
# starts its own session and loses its parent in between. Each look lists every process of the
# user with /bin/ps, which every half second cost a few percent of a core per terminal.
TRACK_SECONDS = 5


class TerminalSession:
    def __init__(self, thread_id, title, cwd, command, cols, rows, owner, run_id, signature):
        self.id = str(uuid4())
        self.thread_id, self.title, self.cwd = thread_id, title, str(cwd)
        self.command, self.owner, self.run_id = command, owner, run_id
        self.cols, self.rows, self.signature = cols, rows, signature
        self.created_at, self.status, self.exit_code = now_iso(), "running", None
        self.buffer, self.offset = "", 0
        self.changed = asyncio.Event()
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.master, self.process, self.waiter = None, None, None
        self.shell_pid = None
        self.close_lock = asyncio.Lock()
        self.closer = None
        self.input_lock = asyncio.Lock()
        self.marker = uuid4().hex
        self.closed = False
        self.on_exit = None
        self.children = {}
        self.children_lock = threading.Lock()
        self.tracker = None

    def metadata(self):
        return {
            key: getattr(self, key)
            for key in (
                "id",
                "thread_id",
                "title",
                "cwd",
                "command",
                "owner",
                "run_id",
                "cols",
                "rows",
                "created_at",
                "status",
                "exit_code",
            )
        }

    async def launch(self, runner):
        master, slave = pty.openpty()
        ready_read, ready_write = os.pipe()
        self.master = master
        try:
            os.set_blocking(master, False)
            os.set_blocking(ready_read, False)
            self.resize(self.cols, self.rows)
            shell = "/bin/zsh" if Path("/bin/zsh").is_file() else "/bin/bash"
            argv = [shell, "-f", "-i"] if shell.endswith("zsh") else [shell, "--noprofile", "--norc", "-i"]
            if self.command:
                argv = ["/bin/sh", "-c", self.command]
            argv, env = await asyncio.to_thread(runner.prepare_launch, argv, tty=Path(os.ttyname(slave)))
            env.update(
                TERM="xterm-256color",
                COLORTERM="truecolor",
                LANG=env.get("LANG", "en_US.UTF-8"),
                WAVEFUNCTION_COMMAND_ID=self.marker,
                PS1="%1~ %# " if shell.endswith("zsh") else r"\w $ ",
            )
            mode = ["--python", "-I"] if getattr(sys, "frozen", False) else ["-I"]
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                *mode,
                "-m",
                "wavefunction.pty_process",
                str(ready_write),
                *argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=self.cwd,
                env=env,
                pass_fds=(ready_write,),
            )
            os.close(ready_write)
            ready_write = None
            async with asyncio.timeout(15):
                while self.shell_pid is None:
                    try:
                        report = os.read(ready_read, 32)
                    except BlockingIOError:
                        await asyncio.sleep(0.01)
                        continue
                    if not report or not report.strip().isdigit():
                        raise ValueError("The terminal shell could not start.")
                    self.shell_pid = int(report.strip())
            asyncio.get_running_loop().add_reader(master, self._read)
            self.tracker = asyncio.create_task(self._track_children())
            self.waiter = asyncio.create_task(self._wait())
        except BaseException:
            self._close_fd()
            if self.process:
                await self.close()
            raise
        finally:
            os.close(slave)
            os.close(ready_read)
            if ready_write is not None:
                os.close(ready_write)

    def _append(self, text):
        self.buffer += text
        excess = max(0, len(self.buffer) - BUFFER_CHARS)
        if excess:
            self.buffer, self.offset = self.buffer[excess:], self.offset + excess
        self.changed.set()

    def _read(self):
        if self.master is None:
            return
        try:
            data = os.read(self.master, OUTPUT_CHARS)
            if data:
                self._append(self.decoder.decode(data))
                return
        except BlockingIOError:
            return
        except OSError as error:
            if error.errno != errno.EIO:
                self._append(f"\r\n[terminal read failed: {error}]\r\n")
        self._append(self.decoder.decode(b"", final=True))
        self._close_fd()

    def _close_fd(self):
        if self.master is not None:
            asyncio.get_running_loop().remove_reader(self.master)
            os.close(self.master)
            self.master = None

    async def _wait(self):
        self.exit_code = await self.process.wait()
        self.status = "exited" if not self.closed else "closed"
        if self.tracker:
            self.tracker.cancel()
            await asyncio.gather(self.tracker, return_exceptions=True)
        # A shell exiting must not orphan jobs, including ones that made their own groups.
        await asyncio.to_thread(self._signal_children, signal.SIGKILL)
        await asyncio.to_thread(_end_tagged, self.marker)
        if self.on_exit:
            try:
                await self.on_exit()
            except Exception:
                logging.getLogger(__name__).exception("Could not record terminal changes for %s", self.id)
        self.changed.set()

    def _capture_children(self):
        """Track ancestry and the PTY session without depending on readable environments.

        macOS cannot expose the environment of every executable (including /bin/sleep).
        Interactive job control also gives each job its own process group. Record process
        start times so a reused PID never becomes an inherited ownership claim.
        """
        with self.children_lock:
            return self._scan_children()

    def _scan_children(self):
        try:
            result = subprocess.run(
                ["/bin/ps", "-U", str(os.getuid()), "-o", "pid=,ppid=,lstart="],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return {}
        processes = {}
        for line in result.stdout.splitlines():
            fields = line.split(maxsplit=2)
            if len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit():
                processes[int(fields[0])] = (int(fields[1]), fields[2])
        self.children = {
            pid: stamp
            for pid, stamp in self.children.items()
            if pid in processes and processes[pid][1] == stamp
        }
        leaders = {self.process.pid, self.shell_pid}
        owned = {leader for leader in leaders if leader is not None} | self.children.keys()
        for pid in processes:
            if pid in leaders:
                continue
            with suppress(ProcessLookupError, PermissionError):
                if os.getsid(pid) in leaders:
                    owned.add(pid)
        while True:
            descendants = {pid for pid, (parent, _) in processes.items() if parent in owned}
            if descendants <= owned:
                break
            owned.update(descendants)
        self.children.update(
            {pid: processes[pid][1] for pid in owned if pid not in leaders and pid in processes}
        )
        return self.children.copy()

    def _signal_children(self, signum):
        for pid in reversed(list(self._capture_children())):
            with suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signum)

    async def _track_children(self):
        while self.process.returncode is None:
            await asyncio.to_thread(self._capture_children)
            await asyncio.sleep(TRACK_SECONDS)

    async def output(self, cursor=0, wait=0):
        if cursor < 0 or cursor > self.offset + len(self.buffer):
            raise ValueError("Terminal output cursor is invalid")
        self.changed.clear()
        if cursor == self.offset + len(self.buffer) and self.status == "running" and wait:
            with suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait(), min(wait, 20))
        reset = cursor < self.offset
        start = max(cursor, self.offset)
        text = self.buffer[start - self.offset : start - self.offset + OUTPUT_CHARS]
        return {"text": text, "cursor": start + len(text), "reset": reset, **self.metadata()}

    async def write(self, text):
        async with self.input_lock:
            if self.master is None or self.status != "running":
                raise ValueError("This terminal has exited. Open a new terminal.")
            try:
                data = text.encode("utf-8")
            except UnicodeError:
                raise ValueError("Terminal input must be valid UTF-8 text") from None
            while data:
                try:
                    used = os.write(self.master, data)
                    data = data[used:]
                except BlockingIOError:
                    await asyncio.sleep(0.01)

    def resize(self, cols, rows):
        self.cols, self.rows = cols, rows
        if self.master is not None:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    async def close(self):
        # Closing is one owned task. A disconnected HTTP request or cancelled run
        # must not abandon cleanup midway; the next close/shutdown joins it.
        if self.closer is None:
            self.closer = asyncio.create_task(self._close())
        await asyncio.shield(self.closer)

    async def _close(self):
        async with self.close_lock:
            if self.closed:
                return
            self.closed, self.status = True, "closed"
            if self.process:
                # Stop jobs while their shell is still alive to reap them. Interactive
                # job control gives jobs separate groups, so killing the shell's group
                # first otherwise leaves those children to the system's delayed reaper.
                await asyncio.to_thread(self._signal_children, signal.SIGTERM)
                await asyncio.sleep(0.1)
                await asyncio.to_thread(self._signal_children, signal.SIGKILL)
                await asyncio.sleep(0.1)
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(self.shell_pid or self.process.pid, signal.SIGTERM)
                if self.process.returncode is None:
                    with suppress(ProcessLookupError):
                        self.process.terminate()
                    with suppress(TimeoutError):
                        await asyncio.wait_for(self.process.wait(), 0.5)
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(self.shell_pid or self.process.pid, signal.SIGKILL)
                if self.process.returncode is None:
                    with suppress(ProcessLookupError):
                        self.process.kill()
                await self.process.wait()
                await asyncio.to_thread(_end_tagged, self.marker)
            self._read()
            self._close_fd()
            if self.waiter:
                await self.waiter
            self.changed.set()


def still_allowed(launched, current) -> bool:
    """A running terminal keeps the sandbox it was launched with, so a grant added or widened
    later never reaches it and is no reason to stop it. It stops when its folder or access
    changes, or when a grant it was launched with is revoked or made read-only."""
    granted = current.get("grants", {})
    return {k: v for k, v in launched.items() if k != "grants"} == {
        k: v for k, v in current.items() if k != "grants"
    } and all(
        path in granted and (granted[path] or not writable)
        for path, writable in launched.get("grants", {}).items()
    )


class TerminalManager:
    def __init__(self, signature):
        self.sessions = {}
        self.signature = signature
        self.lock = asyncio.Lock()
        self.monitor = None

    async def create(
        self,
        thread_id,
        runner,
        cwd,
        *,
        title="Terminal",
        command=None,
        cols=80,
        rows=24,
        owner="user",
        run_id=None,
    ):
        async with self.lock:
            if (
                len(self.sessions) >= 64
                or sum(s.thread_id == thread_id for s in self.sessions.values()) >= 16
            ):
                raise ValueError("Close a terminal before opening another (16 per conversation).")
            session = TerminalSession(
                thread_id, title, cwd, command, cols, rows, owner, run_id, self.signature(thread_id)
            )
            await session.launch(runner)
            self.sessions[session.id] = session
            if self.monitor is None or self.monitor.done():
                self.monitor = asyncio.create_task(self._monitor())
            return session

    async def get(self, thread_id, session_id, *, writable=False):
        session = self.sessions.get(session_id)
        if session is None or session.thread_id != thread_id:
            raise KeyError("Terminal not found in this conversation")
        try:
            valid = still_allowed(session.signature, self.signature(thread_id))
        except (KeyError, ValueError):
            valid = False
        if not valid:
            await session.close()
            if writable:
                raise ValueError(
                    "Terminal stopped because the folder, mode, or permissions changed. Open a new terminal."
                )
        return session

    async def list(self, thread_id):
        return [
            (await self.get(thread_id, s.id)).metadata()
            for s in list(self.sessions.values())
            if s.thread_id == thread_id
        ]

    async def remove(self, thread_id, session_id):
        session = await self.get(thread_id, session_id)
        await session.close()
        self.sessions.pop(session_id, None)

    async def forget(self, thread_id):
        for session in list(self.sessions.values()):
            if session.thread_id == thread_id:
                await session.close()
                self.sessions.pop(session.id, None)

    async def _monitor(self):
        while any(session.status == "running" for session in self.sessions.values()):
            await asyncio.sleep(0.5)
            for session in list(self.sessions.values()):
                if session.closed:
                    continue
                try:
                    await self.get(session.thread_id, session.id)
                except KeyError:
                    continue

    async def shutdown(self):
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        await asyncio.gather(*(session.close() for session in list(self.sessions.values())))
        self.sessions.clear()
