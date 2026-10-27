"""Authenticated user-only desktop control. Never persist request bodies or frames."""

import asyncio
import hmac
from contextlib import suppress
from typing import cast

from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from . import responses as api
from .computer import COMPUTER_TOOL_NAMES, DesktopUnavailable
from .spec_v2 import load_spec

# The person in control watches and drives the desktop over VNC. The server runs inside the
# container, listens on its loopback only, and runs only while someone is in control; its bytes
# reach the app through `docker exec`, so no port is published for it.
VNC_SERVER = (
    "sh",
    "-c",
    "pidof x11vnc >/dev/null || exec x11vnc -display :0 -localhost -rfbport 5900 "
    "-shared -forever -nopw -quiet -noxrecord",
)
RELAY = r"""
import os, socket, sys, threading, time
for _ in range(50):
    try:
        conn = socket.create_connection(("127.0.0.1", 5900))
        break
    except OSError:
        time.sleep(0.1)
else:
    sys.exit(3)
def upstream():
    while True:
        data = os.read(0, 65536)
        if not data:
            break
        conn.sendall(data)
    conn.shutdown(socket.SHUT_WR)
threading.Thread(target=upstream, daemon=True).start()
out = sys.stdout.buffer
while True:
    data = conn.recv(65536)
    if not data:
        break
    out.write(data)
    out.flush()
"""


def register_computer_control(app, get_services, authorize):
    def owner(owner_id):
        row = get_services().repository.get_run(owner_id)
        return row, row.get("conversation_id") or owner_id

    def snapshot(owner_id):
        row, key = owner(owner_id)
        control = get_services().computer.control(key)
        session = get_services().computer._sessions.get(key)
        return {
            "available": session is not None,
            "state": control.state,
            "generation": control.generation,
            "waiting": row["status"] == "waiting_computer",
        }

    base = "/api/computer/control/run/{owner_id}"
    dependencies = [Depends(authorize)]

    async def release_dead(row, key, control):
        """A desktop whose container died (Docker restarted or quit). Its entry goes, control goes
        back to the agent, and a task paused on it continues: a paused takeover opens a fresh
        desktop with the chat's home. Left held by the user, the paused task could neither resume
        nor be taken over, and the chat's next task would pause on it too. Whether one was paused."""
        services = get_services()
        await services.computer._drop(key)
        control.state = "agent"
        waiting = [
            run
            for run in services.repository.list_conversation_runs(row["project_id"], key)
            if run["status"] == "waiting_computer"
        ]
        for run in waiting:
            await services.runtime.resume_computer(run["id"])
        return bool(waiting)

    @app.get(base, dependencies=dependencies, response_model=api.ControlStatus)
    async def control_status(owner_id: str):
        return snapshot(owner_id)

    @app.post(base + "/take", dependencies=dependencies, response_model=api.ControlStatus)
    async def take(owner_id: str):
        row, key = owner(owner_id)
        # The desktop belongs to the conversation and outlives the task that opened it, so a
        # finished task's desktop can still be taken over for a look or a follow-up.
        computer = get_services().computer
        session = computer._sessions.get(key)
        if session is None or not await computer._alive(session):
            # A container that died (Docker restarted or quit) leaves its entry behind; taking it
            # over would leave the chat held by the user with nothing to hand back.
            control = computer.control(key)
            try:
                async with control.lock:
                    resumed = await release_dead(row, key, control)
            except (ValueError, DesktopUnavailable) as error:
                raise HTTPException(409, str(error)) from error
            raise HTTPException(
                409,
                "The desktop is no longer running. "
                + (
                    "The agent continues and opens a new one when it needs it."
                    if resumed
                    else "Ask the agent to open it again."
                ),
            )
        control = computer.control(key)
        control.request()
        # A running task with desktop tools hands over at its next checkpoint; otherwise
        # nothing can be driving the desktop and the user has it now.
        repository = get_services().repository

        def drives(run):
            if run["status"] != "running":
                return False
            spec = repository.get_run(run["id"]).get("spec")
            return bool(spec) and any(
                tool.name in COMPUTER_TOOL_NAMES for tool in load_spec(spec).enabled_tools()
            )

        if not any(drives(run) for run in repository.pending_conversation_runs(row["project_id"], key)):
            await control.claim()
        return snapshot(owner_id)

    async def private_session(owner_id, generation):
        _, key = owner(owner_id)
        control = get_services().computer.control(key)
        if control.state != "user" or generation != control.generation:
            raise HTTPException(409, "You no longer control this desktop.")
        session = get_services().computer._sessions.get(key)
        if session is None:
            raise HTTPException(409, "The desktop closed. Start it again before signing in.")
        return control, session

    streams: dict[str, int] = {}
    # Starting and stopping a desktop's VNC server, one at a time: a stream closing just as
    # another opens must not stop the server the new one is about to use.
    vnc_locks: dict[str, asyncio.Lock] = {}
    # Each open stream's relay, so handing control back can cut live input off at once.
    relays: dict[str, set[asyncio.subprocess.Process]] = {}

    @app.websocket(base + "/stream")
    async def stream(websocket: WebSocket, owner_id: str, generation: int):
        """The desktop live, while the person holds control of it.

        A browser cannot put a header on a websocket, so the token arrives as a subprotocol,
        which stays out of URLs and logs. The stream ends when control goes back to the agent.
        """
        settings = get_services().settings
        offered = [part.strip() for part in websocket.headers.get("sec-websocket-protocol", "").split(",")]
        token = next((part.removeprefix("bearer.") for part in offered if part.startswith("bearer.")), "")
        if not settings.development_auth_bypass and not (
            settings.desktop_token and hmac.compare_digest(token, settings.desktop_token)
        ):
            await websocket.close(code=1008)
            return
        try:
            control, session = await private_session(owner_id, generation)
        except (HTTPException, KeyError):
            # Not the person in control (or no such task): no picture of the desktop.
            await websocket.close(code=1008)
            return
        docker = get_services().computer.docker()
        if not docker:
            await websocket.close(code=1011)
            return
        _, key = owner(owner_id)
        await websocket.accept(subprotocol="binary" if "binary" in offered else None)
        lock = vnc_locks.setdefault(key, asyncio.Lock())
        async with lock:
            streams[key] = streams.get(key, 0) + 1
            started = await asyncio.create_subprocess_exec(
                docker,
                "exec",
                "-d",
                session.container_id,
                *VNC_SERVER,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await started.wait()
        relay = None
        try:
            relay = await asyncio.create_subprocess_exec(
                docker,
                "exec",
                "-i",
                session.container_id,
                "python3",
                "-I",
                "-c",
                RELAY,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            relays.setdefault(key, set()).add(relay)
            # Both were opened as pipes.
            relay_in, relay_out = (
                cast(asyncio.StreamWriter, relay.stdin),
                cast(asyncio.StreamReader, relay.stdout),
            )

            async def upstream():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    data = message.get("bytes") or (message.get("text") or "").encode()
                    relay_in.write(data)
                    await relay_in.drain()

            async def downstream():
                while data := await relay_out.read(65536):
                    await websocket.send_bytes(data)

            async def held():
                # The person handed control back, or the desktop closed: the stream ends.
                while (
                    control.state == "user"
                    and control.generation == generation
                    and get_services().computer._sessions.get(key) is session
                ):
                    await asyncio.sleep(0.5)

            tasks = [asyncio.create_task(job()) for job in (upstream, downstream, held)]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            # Either side going away ends the stream; its error needs no report.
            await asyncio.gather(*tasks, return_exceptions=True)
        except (WebSocketDisconnect, OSError):
            pass
        finally:
            if relay is not None:
                relays.get(key, set()).discard(relay)
                if relay.returncode is None:
                    with suppress(ProcessLookupError):
                        relay.kill()
                    await relay.wait()
            async with lock:
                streams[key] -= 1
                if not streams[key]:
                    del streams[key]
                    # Nothing is watching: stop the server rather than leave it encoding frames.
                    stopped = await asyncio.create_subprocess_exec(
                        docker,
                        "exec",
                        session.container_id,
                        "pkill",
                        "-x",
                        "x11vnc",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await stopped.wait()
            with suppress(RuntimeError):
                await websocket.close()

    @app.get(base + "/screen", dependencies=dependencies)
    async def screen(owner_id: str, generation: int):
        control, session = await private_session(owner_id, generation)
        async with control.lock:
            await private_session(owner_id, generation)
            try:
                response = await session.client.get("/screenshot")
                response.raise_for_status()
            except Exception as error:
                raise HTTPException(409, "The desktop is not responding.") from error
        return Response(response.content, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post(base + "/action", dependencies=dependencies, response_model=api.Accepted)
    async def action(owner_id: str, request: Request):
        # Parse privately: schema errors must not echo passwords back into error records.
        try:
            raw = await request.body()
            if len(raw) > 32_000:
                raise ValueError()
            import json

            body = json.loads(raw)
            operation = body["action"]
            payload = body["payload"]
            generation = body["generation"]
            if operation not in {"click", "double_click", "type", "key", "scroll", "drag"} or not isinstance(
                payload, dict
            ):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "Invalid desktop input.") from None
        control, session = await private_session(owner_id, generation)
        async with control.lock:
            await private_session(owner_id, generation)
            try:
                response = await session.client.post("/" + operation, json=payload)
                response.raise_for_status()
            except Exception as error:
                # Never relay subprocess output or echoed input from the action server.
                raise HTTPException(409, "Could not send input to the desktop.") from error
            session.touch()
        return {"ok": True}

    @app.post(base + "/resume", dependencies=dependencies, response_model=api.ControlStatus)
    async def resume(owner_id: str, request: Request):
        try:
            generation = (await request.json())["generation"]
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "Invalid desktop session.") from None
        control, session = await private_session(owner_id, generation)
        async with control.lock:
            await private_session(owner_id, generation)
            row, key = owner(owner_id)
            if not await get_services().computer._alive(session):
                try:
                    resumed = await release_dead(row, key, control)
                except (ValueError, DesktopUnavailable) as error:
                    raise HTTPException(409, str(error)) from error
                if not resumed:
                    raise HTTPException(409, "The desktop closed. Ask the agent to open it again.")
                return snapshot(owner_id)
            # Input still in flight on a live stream must not reach the desktop after the agent
            # has it: end the streams before handing control back.
            for relay in list(relays.get(key, ())):
                with suppress(ProcessLookupError):
                    relay.kill()
                await relay.wait()
            control.state = "agent"
            try:
                # Resume only graphs paused on this conversation's desktop.
                for run in get_services().repository.list_conversation_runs(row["project_id"], key):
                    if run["status"] == "waiting_computer":
                        await get_services().runtime.resume_computer(run["id"])
                session.touch()
            except (ValueError, DesktopUnavailable) as error:
                control.state = "user"
                raise HTTPException(409, str(error)) from error
        return snapshot(owner_id)
