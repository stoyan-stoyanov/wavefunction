"""Notifications: a finished chat or one that needs the user, unless it is in view."""

import asyncio

from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage
from support import create


async def test_notifications_report_a_chat_that_finished_or_needs_the_user_unless_it_is_in_view(coding):
    """The desktop shell waits for news with a cursor and the chat on screen. A chat's own task notifies
    when it ends or pauses for the user; its helpers and a compaction do not."""
    from wavefunction.coding_workspace import ThreadInput

    client, ws, services = coding

    async def pending(**params):
        response = await client.get("/api/notifications/pending", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    # Without a cursor it starts from now: earlier work never notifies.
    start = await pending()
    assert start["items"] == [] and start["sound"] is True
    parser, research = await create(client), await create(client)
    script_gateway(
        services.models,
        [AIMessage(content="All   tests pass\nnow.")],
        [AIMessage(content="Summary of the parser work")],
        [tool_call_message("ask_user", {"questions": [{"id": "scope", "title": "Which module?"}]}, "ask")],
    )
    run = await ws.send(parser["id"], "Fix the parser")
    await services.runtime.wait(run["id"])
    title = ws.thread(parser["id"])["title"]
    finished = await pending(after=start["cursor"])
    assert finished["items"] == [{"thread_id": parser["id"], "title": title, "body": "All tests pass now."}]
    # The chat on screen, in a focused window, gets none.
    assert (await pending(after=start["cursor"], visible=parser["id"]))["items"] == []

    # Neither a compaction nor a helper's own task notifies.
    await ws.compact(parser["id"])
    compaction = next(
        item
        for item in services.repository.list_conversation_runs(
            ws.agent_id, ws.thread(parser["id"])["conversation_id"]
        )
        if item["kind"] == "compact"
    )
    await services.runtime.wait(compaction["id"])
    helper = ws.create_thread(
        ThreadInput(title="Helper"), parent_thread_id=parser["id"], parent_run_id=run["id"]
    )
    helper_run = services.repository.create_run(
        ws.agent_id,
        {"text": "Check the lexer"},
        helper["conversation_id"],
        spec=None,
        kind="subagent",
        parent_run_id=run["id"],
    )
    services.repository.add_run_event(helper_run["id"], "run_completed", {"text": "Lexer checked"})
    quiet = await pending(after=finished["cursor"])
    assert quiet["items"] == []

    # The shell keeps one request waiting: without news it answers at the end of the wait...
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await pending(after=quiet["cursor"], wait=0.3) == quiet
    assert loop.time() - started >= 0.3
    # ...and as soon as a task pauses for the user, long before it.
    waiting = asyncio.create_task(pending(after=quiet["cursor"], wait=20))
    await asyncio.sleep(0.1)
    assert not waiting.done()
    question = await ws.send(research["id"], "Research the options")
    asking = await asyncio.wait_for(waiting, 10)
    assert [(item["thread_id"], item["body"]) for item in asking["items"]] == [
        (research["id"], "Has a question for you.")
    ]
    await services.runtime.wait(question["id"])

    async def configure(**change):
        current = (await client.get("/api/notifications/settings")).json()
        response = await client.put("/api/notifications/settings", json={**current, **change})
        assert response.status_code == 200 and response.json() == {**current, **change}

    await configure(input=False)
    assert (await pending(after=quiet["cursor"]))["items"] == []
    # Without previews, nothing the chat says or is called is shown.
    await configure(input=True, previews=False, sound=False)
    private = await pending(after=start["cursor"])
    assert private["sound"] is False
    assert [(item["title"], item["body"]) for item in private["items"]] == [
        ("Wavefunction", "A task finished."),
        ("Wavefunction", "A task needs your input."),
    ]
    await configure(desktop=False)
    assert (await pending(after=start["cursor"]))["items"] == []
