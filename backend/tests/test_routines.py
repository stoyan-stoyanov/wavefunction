"""Routines: scheduled project chats and their reports."""

from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage
from support import create


async def test_a_chat_starts_routines_that_run_on_their_own_and_report_back_only_when_asked(
    coding, tmp_path, monkeypatch
):
    """A routine is a project chat sent its instruction on a schedule. It outlives the chat that
    made it, reports each finished run there only when asked, and is changed from its settings."""
    from datetime import UTC, datetime, timedelta

    from wavefunction.routine_timing import from_words, next_due

    client, ws, services = coding
    folder = tmp_path / "repo"
    folder.mkdir()
    project = (await client.post("/api/coding/projects", json={"name": "Ops", "folder": str(folder)})).json()
    parent = await create(client, project_id=project["id"], title="Deploy")
    script_gateway(
        services.models,
        [
            tool_call_message("search_available_tools", {"query": "routine"}, "find"),
            tool_call_message(
                "create_routine",
                {
                    "title": "Watch logs",
                    "instruction": "Check the logs for errors.",
                    "repeat": "every",
                    "every_minutes": 15,
                    "report_back": True,
                },
                "watch",
            ),
            tool_call_message(
                "create_routine",
                {
                    "title": "Draft replies",
                    "instruction": "Draft replies to new email.",
                    "repeat": "once",
                    "date": "2099-01-05",
                    "time": "09:00",
                },
                "mail",
            ),
            AIMessage(content="Both routines are set."),
        ],
        [AIMessage(content="No errors in the logs.")],
        [AIMessage(content="Noted: the fix holds.")],
    )
    run = await ws.send(parent["id"], "Watch the logs and draft my email replies")
    assert (await services.runtime.wait(run["id"]))["status"] == "completed"
    routines = {thread["title"]: thread for thread in ws.snapshot()["threads"] if thread["routine"]}
    watch, mail = routines["Watch logs"], routines["Draft replies"]
    assert watch["project_id"] == mail["project_id"] == project["id"]
    assert (watch["routine"]["summary"], watch["routine"]["report_to"]) == ("every 15 minutes", parent["id"])
    assert (mail["routine"]["summary"], mail["routine"]["report_to"]) == (
        "once on Mon 5 Jan 2099 at 09:00",
        None,
    )

    # An interval is due at once and runs as a message in the routine's own chat; the next look
    # sends its answer to the chat that asked for reports, once.
    now = datetime.now(UTC)
    started = await ws.routines.tick(now)
    assert len(started) == 1
    # Its report waits for the run's task to let go, not only for its status.
    task = services.runtime.tasks[started[0]]
    fired = await services.runtime.wait(started[0])
    await task
    assert (fired["input"]["text"], fired["output"]["text"]) == (
        "Check the logs for errors.",
        "No errors in the logs.",
    )
    assert services.repository.get_run(started[0])["conversation_id"] == watch["conversation_id"]
    assert await ws.routines.tick(now) == []
    reports = services.repository.list_conversation_runs(ws.agent_id, parent["conversation_id"])[1:]
    assert len(reports) == 1 and reports[0]["input"]["text"].startswith("Routine “Watch logs” finished:")
    assert "No errors in the logs." in reports[0]["input"]["text"]
    # The chat shows it as the routine's report, with the answer alone.
    assert reports[0]["input"]["routine_report"] == {
        "thread_id": watch["id"],
        "title": "Watch logs",
        "status": "completed",
    }
    assert reports[0]["input"]["display_text"] == "No errors in the logs."
    await services.runtime.wait(reports[0]["id"])
    assert await ws.routines.tick(now + timedelta(minutes=5)) == []
    assert len(services.repository.list_conversation_runs(ws.agent_id, parent["conversation_id"])) == 2

    # Paused, it does not run; it reports only to another chat in its project; it keeps running,
    # without reports, once the chat that made it is deleted; stopped, its chat stays.
    url = f"/api/coding/threads/{watch['id']}/routine"
    paused = (await client.patch(url, json={"enabled": False})).json()
    assert paused["enabled"] is False and paused["next_run_at"] is None
    assert await ws.routines.tick(now + timedelta(hours=1)) == []
    assert (await client.patch(url, json={"report_to": watch["id"]})).status_code == 422
    assert (await client.delete(f"/api/coding/threads/{parent['id']}")).status_code == 204
    assert ws.routines.get(watch["id"])["report_to"] is None and ws.routines.get(mail["id"])["enabled"]
    assert (await client.delete(f"/api/coding/threads/{mail['id']}/routine")).status_code == 204
    assert mail["id"] not in ws.routines.all() and ws.thread(mail["id"])["title"] == "Draft replies"
    # A one-off that could not start stays due, with the reason in its settings.
    once = ws.routines.create(
        ws.thread(watch["id"]),
        title="Remind",
        instruction="Remind me.",
        timing=from_words("once", date="2026-01-01", time="09:00"),
        report_to=None,
    )

    async def unavailable(*_args, **_kwargs):
        raise ValueError("The repository is unavailable.")

    with monkeypatch.context() as failing:
        failing.setattr(ws, "send", unavailable)
        assert await ws.routines.tick(now) == []
    reminder = ws.routines.get(once["id"])
    assert (
        reminder["last_error"] == "The repository is unavailable."
        and not reminder["finished"]
        and reminder["next_run_at"]
    )
    # Removing the project hides its chats, so their routines wait for it to come back.
    assert (await client.patch(url, json={"enabled": True})).json()["next_run_at"] is not None
    assert (await client.delete(f"/api/coding/projects/{project['id']}")).status_code == 204
    assert await ws.routines.tick(now + timedelta(hours=1)) == []

    # A time missed while the app was closed runs once when it opens, then at its next time.
    daily, monday = from_words("daily", time="09:00"), datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
    assert next_due(daily, created_at=monday.isoformat(), last_run_at=None, tz=UTC) == monday.replace(hour=9)
    reopened = datetime(2026, 10, 14, 15, 0, tzinfo=UTC).isoformat()
    assert next_due(daily, created_at=monday.isoformat(), last_run_at=reopened, tz=UTC) == datetime(
        2026, 10, 15, 9, 0, tzinfo=UTC
    )
