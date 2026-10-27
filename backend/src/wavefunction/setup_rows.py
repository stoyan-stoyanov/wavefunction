"""The setup card a paused task shows while computer use gets ready."""

from __future__ import annotations

from .connector_setup import connector_status


def computer_setup_row(computer=None):
    """Where setting up computer use has got to, as a row of the setup card.

    Not an account, but the same kind of thing to the person waiting on it: something the
    task started, that takes a few minutes, and that the conversation carries on
    from when it is done. What only the person can do, such as installing Docker, is said here.
    """
    row = {
        "id": "computer-use",
        "title": "Computer use",
        "catalog_id": None,
        "local_kind": "computer",
        "auth": "none",
        "state": "setup",
        "error": "",
        "tool_count": 0,
        "build_log": [],
    }
    status = computer.status_cached() if computer is not None else None
    if status is None:
        return {**row, "state": "error", "error": "Computer use is not available in this build."}
    row["build_log"] = list(status.build_log[-15:])
    # What the card's button does next, as Settings › Computer use offers it.
    row["docker_startable"] = bool(status.docker_desktop_available and status.docker_problem)
    row["image_update"] = bool(status.image_update_available)
    row["docker_action"] = status.docker_action
    # Before ready: a restart from Settings leaves the cached status saying the image is ready.
    if status.building or status.docker_action:
        return {**row, "state": "connecting"}
    if status.image_ready:
        return {**row, "state": "ready"}
    if not status.docker_available:
        return {
            **row,
            "state": "error",
            "error": "Docker is not installed. Install Docker Desktop from "
            "docker.com/products/docker-desktop, open it once, and try again.",
        }
    if status.docker_problem:
        return {**row, "state": "error", "error": status.docker_problem}
    failed = next(
        (line for line in reversed(status.build_log) if line.startswith(("Build failed", "Build stopped"))),
        "",
    )
    return {**row, "state": "error", "error": failed} if failed else row


def setup_row_status(registry, row, computer=None):
    """A setup card row's current state: computer use, GitHub sign-in, or a connector the task is
    waiting on."""
    if row.get("local_kind") == "computer" and row["id"] == "computer-use":
        return computer_setup_row(computer)
    if row.get("local_kind") == "github":
        return {**row, "state": "ready" if row.get("account") else "setup"}
    return connector_status(registry, row["id"])
