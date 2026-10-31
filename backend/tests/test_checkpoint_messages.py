"""What the model checkpoints save, and that it comes back exactly."""

import aiosqlite
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from wavefunction.checkpoint_messages import MessageSaver


def conversation(exchanges):
    rows = [SystemMessage(content="Synthetic summary", id="summary")]
    for index in range(exchanges):
        rows += [
            HumanMessage(content=[{"type": "text", "text": f"Ask {index}"}], id=f"ask-{index}"),
            AIMessage(
                content="",
                id=f"call-{index}",
                tool_calls=[{"name": "read_file", "args": {"path": "big.txt"}, "id": f"tool-{index}"}],
                usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
                response_metadata={"finish_reason": "tool_calls"},
            ),
            ToolMessage(
                content="synthetic line\n" * 50,
                tool_call_id=f"tool-{index}",
                name="read_file",
                id=f"read-{index}",
            ),
            AIMessage(content=f"Answer {index}", id=f"answer-{index}"),
        ]
    return rows


def state(messages, **others):
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"messages": messages, "todos": ["synthetic"], **others}
    checkpoint["channel_versions"] = {"messages": 1, "todos": 1}
    return checkpoint


async def put(saver, thread, checkpoint):
    return await saver.aput(
        {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}, checkpoint, {"step": 1}, {}
    )


async def latest(saver, thread):
    return (await saver.aget_tuple({"configurable": {"thread_id": thread}})).checkpoint


@pytest.mark.asyncio
async def test_a_saved_conversation_comes_back_exactly(tmp_path):
    connection = await aiosqlite.connect(tmp_path / "checkpoints.sqlite")
    saver = MessageSaver(connection)
    await saver.setup()
    saved = state(conversation(3))
    await put(saver, "chat", saved)
    assert await latest(saver, "chat") == saved
    empty = state([])
    await put(saver, "new-chat", empty)
    assert await latest(saver, "new-chat") == empty
    await connection.close()


@pytest.fixture
async def saver(tmp_path):
    connection = await aiosqlite.connect(tmp_path / "checkpoints.sqlite")
    made = MessageSaver(connection)
    await made.setup()
    yield made
    await connection.close()


@pytest.mark.asyncio
async def test_a_chat_saved_in_full_opens_and_carries_on(tmp_path):
    path = tmp_path / "checkpoints.sqlite"
    connection = await aiosqlite.connect(path)
    old = AsyncSqliteSaver(connection)
    await old.setup()
    before = state(conversation(3))
    point = await put(old, "chat", before)
    await connection.close()

    connection = await aiosqlite.connect(path)
    saver = MessageSaver(connection)
    await saver.setup()
    assert await latest(saver, "chat") == before
    after = state(conversation(5))
    await put(saver, "chat", after)
    assert await latest(saver, "chat") == after
    assert (await saver.aget_tuple(point)).checkpoint == before
    assert [item.checkpoint async for item in saver.alist({"configurable": {"thread_id": "chat"}})] == [
        after,
        before,
    ]
    await connection.close()


@pytest.mark.asyncio
async def test_each_point_stays_exact_and_each_message_is_stored_once(saver):
    points = []
    for exchanges in (1, 2, 3, 4):
        saved = state(conversation(exchanges))
        points.append((await put(saver, "chat", saved), saved))
    for config, saved in points:
        assert (await saver.aget_tuple(config)).checkpoint == saved
    async with saver.conn.execute("SELECT count(*) FROM checkpoint_messages") as cursor:
        assert (await cursor.fetchone())[0] == len(conversation(4))
    # Compaction replaces the conversation; the states before it keep theirs.
    compacted = state([SystemMessage(content="Shorter", id="compacted"), *conversation(4)[-2:]])
    await put(saver, "chat", compacted)
    assert await latest(saver, "chat") == compacted
    assert (await saver.aget_tuple(points[0][0])).checkpoint == points[0][1]


@pytest.mark.asyncio
async def test_deleting_a_chat_leaves_its_fork_whole(saver):
    saved = state(conversation(2))
    await put(saver, "chat", saved)
    await put(saver, "fork", await latest(saver, "chat"))
    await saver.adelete_thread("chat")
    assert await saver.aget_tuple({"configurable": {"thread_id": "chat"}}) is None
    assert await latest(saver, "fork") == saved
    await saver.adelete_thread("fork")
    async with saver.conn.execute("SELECT count(*) FROM checkpoint_messages") as cursor:
        assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_a_crash_before_the_checkpoint_keeps_the_last_state_and_a_lost_message_is_refused(saver):
    saved = state(conversation(2))
    await put(saver, "chat", saved)
    await saver.share("chat", state(conversation(3)))  # Its row never arrived.
    await saver.conn.rollback()
    assert await latest(saver, "chat") == saved
    await saver.conn.execute(
        "DELETE FROM checkpoint_messages WHERE id = (SELECT max(id) FROM checkpoint_messages)"
    )
    await saver.conn.commit()
    with pytest.raises(ValueError, match="missing some of its messages"):
        await latest(saver, "chat")
