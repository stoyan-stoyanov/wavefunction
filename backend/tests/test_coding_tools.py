"""Coding workflows over real local tools and disposable repositories."""

import json
import re
import shlex
import sys
from dataclasses import replace

import pytest
from conftest import ScriptedModel, tool_call_message
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_from_dict, messages_to_dict

from wavefunction.execution_policy import RetryingSummarizationMiddleware
from wavefunction.hands import build_hands
from wavefunction.local_coding import EnvironmentMiddleware
from wavefunction.sandbox import PREVIEW_BYTES, Grant, Sandbox
from wavefunction.toolkit import ToolContext


def hands(root, **kwargs):
    return {
        tool.name: tool
        for tool in build_hands(
            root / "scratch",
            lambda: [Grant(root, writable=True)],
            lambda: "sandboxed",
            working_directory=str(root),
            ensure_access=lambda path, writable: None,
            **kwargs,
        )
    }


@pytest.mark.sandbox
async def test_precise_edit_returns_diff_preserves_bytes_and_rejects_ambiguous_matches(tmp_path):
    source = tmp_path / "code.py"
    source.write_bytes(b"\xef\xbb\xbfanswer = 0\r\nother = 0\r\n")
    tools = hands(tmp_path)
    with pytest.raises(ValueError, match="found 2"):
        await tools["edit_file"].ainvoke({"path": "code.py", "old_text": "= 0", "new_text": "= 42"})
    assert source.read_bytes() == b"\xef\xbb\xbfanswer = 0\r\nother = 0\r\n"
    result = await tools["edit_file"].ainvoke(
        {"path": "code.py", "old_text": "answer = 0", "new_text": "answer = 42"}
    )
    assert "-answer = 0" in result and "+answer = 42" in result
    assert source.read_bytes() == b"\xef\xbb\xbfanswer = 42\r\nother = 0\r\n"
    with pytest.raises(ValueError, match="not found"):
        await tools["edit_file"].ainvoke({"path": "code.py", "old_text": "missing", "new_text": "bad"})
    with pytest.raises(PermissionError, match="disabled"):
        await hands(tmp_path, read_only=True)["edit_file"].ainvoke(
            {"path": "code.py", "old_text": "42", "new_text": "0"}
        )


@pytest.mark.sandbox
async def test_line_reads_page_and_bound_long_lines_without_losing_unicode(tmp_path):
    source = tmp_path / "code.txt"
    source.write_text("first\n第二\nthird\nfourth\n")
    tools = hands(tmp_path)
    read = tools["read_file"]
    page = await read.ainvoke({"path": "code.txt", "start_line": 2, "line_count": 2})
    assert "2: 第二\n3: third\n" in page and "start_line=4" in page
    assert "4: fourth" in await read.ainvoke({"path": "code.txt", "start_line": 4})
    source.write_text("first\n" + "前" * 10000 + "\nlast")
    page = await read.ainvoke({"path": "code.txt", "start_line": 2, "limit": 10})
    assert "2: " + "前" * 10 in page and "offset=16" in page
    assert (await read.ainvoke({"path": "code.txt", "offset": 16, "limit": 10})).startswith("前" * 10)
    with pytest.raises(ValueError):
        await read.ainvoke({"path": "code.txt", "start_line": 0})

    # Context locates a long function without giving a search-only result permission to
    # replace the file. A bounded read and its continuation still cover the whole function.
    source = tmp_path / "calculation.py"
    text = (
        "# unrelated heading\n# calculation\n# amounts are cents\n# include the fee\n"
        "def calculate_total(subtotal, fee):\n"
        + "".join(f"    amount_{index} = {index}  # café subtotal\n" for index in range(220))
        + "    return subtotal + fee\n"
    )
    source.write_text(text)
    query = {"path": "calculation.py", "query": r"^def calculate_\w+\("}
    match = await tools["search_files"].ainvoke(query)
    assert ":5:def calculate_total(subtotal, fee):" in match and "amount_0" not in match
    context = await tools["search_files"].ainvoke({**query, "context_lines": 3})
    assert "-2-# calculation" in context and "-8-    amount_2 = 2  # café subtotal" in context
    assert "unrelated heading" not in context and "amount_3" not in context
    with pytest.raises(ValueError, match="has not been read"):
        await tools["write_file"].ainvoke({"path": "calculation.py", "content": text})
    page = await read.ainvoke({"path": "calculation.py", "start_line": 5})
    continuation = int(re.search(r"continue with offset=(\d+)", page).group(1))
    start = text.index("def calculate_total")
    shown = [line.split(": ", 1)[1] for line in page.splitlines() if re.match(r"\d+: ", line)]
    assert shown == text[start:continuation].splitlines()
    rest = await read.ainvoke({"path": "calculation.py", "offset": continuation, "limit": 40_000})
    assert rest == text[continuation:] and rest.endswith("    return subtotal + fee\n")
    source.write_text(text + "# user change\n")
    with pytest.raises(ValueError, match="changed since it was last read"):
        await tools["write_file"].ainvoke({"path": "calculation.py", "content": text})
    assert source.read_text() == text + "# user change\n"


@pytest.mark.sandbox
async def test_commands_distinguish_denial_failure_and_timeout_without_losing_output(tmp_path):
    tool = hands(tmp_path)["run_command"]
    result = await tool.ainvoke({"command": "echo failed-test; echo detail >&2; exit 7"})
    assert "[exit code 7]" in result and "failed-test" in result and "detail" in result

    script = f"import sys,time; print('x'*{PREVIEW_BYTES + 100}, flush=True); print('partial error', file=sys.stderr, flush=True); time.sleep(30)"
    result = await Sandbox([], scratch=tmp_path / "scratch").run(
        shlex.join([sys.executable, "-I", "-c", script]), timeout=0.5, cwd=tmp_path, tail=True
    )
    assert result.exit_code == 124 and result.timed_out
    assert "Showing the last" in result.stdout
    assert result.stdout_file.read_text().strip() == "x" * (PREVIEW_BYTES + 100)
    assert "partial error" in result.stderr and "Timed out" in result.stderr


@pytest.mark.sandbox
async def test_compaction_keeps_tool_confirmed_state_through_multiple_summaries(tmp_path):
    tools = hands(tmp_path)
    (tmp_path / "code.py").write_text("answer = 0\n")
    messages = [HumanMessage(content="Fix code and test it. Do not commit.")]
    for name, args in [
        ("read_file", {"path": "code.py", "start_line": 1}),
        ("edit_file", {"path": "code.py", "old_text": "= 0", "new_text": "= 42"}),
        ("run_command", {"command": "echo test-failed; exit 1"}),
    ]:
        call = {"name": name, "args": args, "id": name, "type": "tool_call"}
        messages += [AIMessage(content="", tool_calls=[call]), await tools[name].ainvoke(call)]
    model = ScriptedModel(script=[AIMessage(content="Continue fixing the tests.")])
    middleware = RetryingSummarizationMiddleware(model)
    first = middleware._build_new_messages(await middleware._acreate_summary(messages))
    # Checkpoint roundtrip and a newly reconstructed middleware must retain the evidence.
    restored = messages_from_dict(json.loads(json.dumps(messages_to_dict(first))))
    middleware = RetryingSummarizationMiddleware(model)
    second = middleware._build_new_messages(await middleware._acreate_summary(restored))
    state = second[0].additional_kwargs["coding_state"]
    assert state["read"] == [str(tmp_path / "code.py")]
    assert state["modified"] == [str(tmp_path / "code.py")]
    assert state["commands"][-1]["exit_code"] == 1
    assert state["commands"][-1]["cwd"] == str(tmp_path)
    assert "test-failed" in second[0].content


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS only")
async def test_batch_edits_use_original_matches_and_reject_whole_invalid_batch(tmp_path):
    source = tmp_path / "code.txt"
    source.write_text("alpha beta gamma")
    edit = hands(tmp_path)["edit_file"]
    for edits in [
        [{"old_text": "alpha", "new_text": "ok"}, {"old_text": "missing", "new_text": "bad"}],
        [{"old_text": "alpha beta", "new_text": "ok"}, {"old_text": "beta", "new_text": "bad"}],
    ]:
        with pytest.raises(ValueError):
            await edit.ainvoke({"path": str(source), "edits": edits})
        assert source.read_text() == "alpha beta gamma"
    await edit.ainvoke(
        {
            "path": str(source),
            "edits": [{"old_text": "alpha", "new_text": "beta"}, {"old_text": "beta", "new_text": "delta"}],
        }
    )
    assert source.read_text() == "beta delta gamma"


@pytest.mark.sandbox
async def test_command_streams_before_completion_and_keeps_tail_and_full_log(tmp_path):
    import asyncio

    seen = asyncio.Event()
    updates = []

    def output(stdout, stderr):
        updates.append((stdout, stderr))
        seen.set()

    script = "import time,sys; print('FIRST', flush=True); time.sleep(.5); print('x'*50000); print('LAST'); print('ERROR TAIL',file=sys.stderr)"
    task = asyncio.create_task(
        Sandbox([], scratch=tmp_path / "scratch").run(
            shlex.join([sys.executable, "-I", "-c", script]), tail=True, on_output=output
        )
    )
    try:
        await asyncio.wait_for(seen.wait(), 5)
        assert not task.done() and "FIRST" in updates[0][0]
        result = await task
        assert result.stdout.rstrip().endswith("LAST") and "Showing the last" in result.stdout
        assert result.stdout_file.read_text().startswith("FIRST\n")
        assert result.stdout_file.read_text().rstrip().endswith("LAST")
        assert "ERROR TAIL" in updates[-1][1]
        assert all(len(out) <= 4000 and len(err) <= 4000 for out, err in updates)
        count = len(updates)
        await asyncio.sleep(0.3)
        assert len(updates) == count
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.sandbox
async def test_local_image_read_attaches_pixels_only_at_request_time(tmp_path):
    from langchain_core.messages import ToolMessage
    from PIL import Image

    from wavefunction.local_images import LocalImagesMiddleware

    path = tmp_path / "picture"  # Detect contents, not filename extensions.
    Image.new("RGB", (2400, 1200), "red").save(path, format="JPEG")
    call = {"name": "read_file", "args": {"path": str(path)}, "id": "image", "type": "tool_call"}
    message = await hands(tmp_path)["read_file"].ainvoke(call)
    assert "2048x1024" in message.content
    assert "base64" not in json.dumps(messages_to_dict([message]))
    other = ToolMessage(content="parallel result", tool_call_id="other", name="other")
    request = ModelRequest(
        model=ScriptedModel(script=[]),
        messages=[AIMessage(content="", tool_calls=[call]), message, other],
        tools=[],
    )
    middleware = LocalImagesMiddleware(tmp_path / "scratch" / "images")
    augmented = middleware._augment(request)
    assert augmented.messages[:3] == request.messages
    assert augmented.messages[3].content[1]["mime_type"] == "image/png"
    text_only = request.override(model=ScriptedModel(script=[], profile={"image_inputs": False}))
    assert "does not support image input" in middleware._augment(text_only).messages[-1].text
    # An artifact cannot turn this loader into an unrestricted filesystem reader.
    malicious = ToolMessage(
        content="image", tool_call_id="image", name="read_file", artifact={"local_image": str(path)}
    )
    denied = request.override(messages=[malicious])
    assert middleware._augment(denied) is denied
    # Images stay across the user's messages and are dropped three at a time: an image that
    # vanishes at a new message or at every new read changes the prompt deep in the history and
    # makes everything after it miss the provider's cache.
    from langchain_core.messages import HumanMessage

    def reads(count, prefix):
        calls = [{**call, "id": f"{prefix}{n}"} for n in range(count)]
        return [
            AIMessage(content="", tool_calls=calls),
            *(message.model_copy(update={"tool_call_id": c["id"]}) for c in calls),
        ]

    def shown(count):
        history = [
            HumanMessage(content="earlier task"),
            *reads(1, "old"),
            HumanMessage(content="this task"),
            *reads(count, "new"),
        ]
        augmented = middleware._augment(request.override(messages=history)).messages
        inserted = [
            i for i, m in enumerate(augmented) if m.additional_kwargs.get("lc_source") == "local_image"
        ]
        return len(inserted), 3 in inserted  # Index 3 follows the earlier task's read.

    assert [shown(count) for count in (0, 2, 4, 5, 6)] == [
        (1, True),
        (3, True),
        (5, True),
        (3, False),
        (4, False),
    ]


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS only")
async def test_local_image_reads_use_the_same_boundary_as_text(tmp_path):
    from PIL import Image

    from wavefunction.config import get_settings

    path = tmp_path / "image.png"
    Image.new("RGB", (20, 10), "blue").save(path)
    private = get_settings().data_dir / "synthetic-private.png"
    Image.new("RGB", (20, 10), "red").save(private)
    read = build_hands(
        tmp_path / "scratch",
        lambda: [Grant(tmp_path)],
        lambda: "sandboxed",
        ensure_access=lambda path, writable: None,
    )[0]
    assert "Read image" in await read.ainvoke({"path": str(path)})
    with pytest.raises(PermissionError):
        await read.ainvoke({"path": str(private)})


@pytest.mark.sandbox
async def test_edit_preserves_original_on_replacement_failure_and_symlink_mode(tmp_path, monkeypatch):
    import stat

    from wavefunction import fileops

    source = tmp_path / "script.py"
    source.write_text("original")
    source.chmod(0o751)
    link = tmp_path / "link.py"
    link.symlink_to(source)

    def fail_replace(*_):
        raise OSError("synthetic replacement failure")

    with monkeypatch.context() as patch:
        # The helper's own code, run here so the failure can be injected where it would happen.
        patch.setattr(fileops.os, "replace", fail_replace)
        result = fileops.run(
            {"op": "edit", "path": str(link), "edits": [{"old_text": "original", "new_text": "updated"}]}
        )
    assert not result["ok"] and "synthetic replacement failure" in result["message"]
    assert source.read_text() == "original"
    assert not list(tmp_path.glob(".wavefunction-edit-*"))
    await hands(tmp_path)["edit_file"].ainvoke(
        {"path": str(link), "old_text": "original", "new_text": "updated"}
    )
    assert link.is_symlink() and source.read_text() == "updated"
    assert stat.S_IMODE(source.stat().st_mode) == 0o751


def test_compaction_coding_state_is_bounded_without_mutating_prior_summary():
    from copy import deepcopy

    from wavefunction.execution_policy import coding_state

    prior = {
        "read": [f"/repo/file-{i}.py" for i in range(2000)],
        "modified": ["/repo/latest.py"],
        "commands": [{"command": "echo " + "x" * 20000, "cwd": "/repo", "exit_code": 1, "timed_out": False}],
    }
    original = deepcopy(prior)
    messages = [
        HumanMessage(
            content="Summary", additional_kwargs={"lc_source": "summarization", "coding_state": prior}
        )
    ]
    state = coding_state(messages)
    assert len(json.dumps(state)) <= 6000 and state["truncated"]
    assert state["read"][-1] == "/repo/file-1999.py"
    assert state["modified"] == ["/repo/latest.py"]
    assert state["commands"][-1]["exit_code"] == 1
    assert len(state["commands"][-1]["command"]) < 600
    assert prior == original


@pytest.mark.sandbox
async def test_image_reads_prune_old_snapshots_and_keep_current_image(tmp_path):
    from PIL import Image

    from wavefunction.local_images import LocalImagesMiddleware

    folder = tmp_path / "scratch" / "images"
    folder.mkdir(parents=True)
    for index in range(40):
        (folder / f"{index:032x}.png").write_bytes(b"old snapshot")
    unrelated = folder / "notes.png"
    unrelated.write_bytes(b"not a snapshot")
    source = tmp_path / "image.png"
    Image.new("RGB", (4, 4), "blue").save(source)
    call = {"name": "read_file", "args": {"path": str(source)}, "id": "current", "type": "tool_call"}
    message = await hands(tmp_path)["read_file"].ainvoke(call)
    assert len(list(folder.glob("*.png"))) == 33 and unrelated.exists()
    request = ModelRequest(model=ScriptedModel(script=[]), messages=[message], tools=[])
    assert LocalImagesMiddleware(folder)._augment(request).messages[-1].content[1]["mime_type"] == "image/png"


def test_command_tails_coalesce_with_monotonic_reconnect_cursors(stack):
    repo = stack.repository
    agent = stack.agent
    owner = repo.create_run(
        agent["id"], {"text": "Build"}, repo.current_conversation(agent["id"])["id"], spec=None
    )
    add, read = repo.add_run_event, repo.list_run_events
    first = add(owner["id"], "tool_output", {"id": "a", "stdout": "first"})
    add(owner["id"], "tool_output", {"id": "b", "stdout": "other command"})
    for index in range(100):
        latest = add(owner["id"], "tool_output", {"id": "a", "stdout": str(index)})
    assert latest["sequence"] > first["sequence"]
    replay = read(owner["id"], after=first["sequence"])
    assert len(replay) == 2
    assert replay[-1]["payload"]["stdout"] == "99"
    # Completing or interrupting a command must not erase the last observable tail.
    add(owner["id"], "tool_end", {"id": "a"})
    assert len([e for e in read(owner["id"]) if e["event_type"] == "tool_output"]) == 2


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS only")
async def test_atomic_edit_obeys_native_write_grant(tmp_path):
    from wavefunction.sandbox import Grant

    folder = tmp_path / "allowed"
    folder.mkdir()
    source = folder / "code.py"
    source.write_text("old")
    grants = [Grant(folder, writable=True)]
    tools = {
        t.name: t
        for t in build_hands(
            tmp_path / "scratch",
            lambda: grants,
            lambda: "sandboxed",
            ensure_access=lambda path, writable: None,
        )
    }
    await tools["edit_file"].ainvoke({"path": str(source), "old_text": "old", "new_text": "new"})
    assert source.read_text() == "new"
    grants[:] = [Grant(folder)]
    with pytest.raises(PermissionError):
        await tools["edit_file"].ainvoke({"path": str(source), "old_text": "new", "new_text": "denied"})
    assert source.read_text() == "new"


@pytest.mark.skipif(sys.platform != "darwin", reason="Native macOS ACLs and xattrs")
async def test_atomic_edit_preserves_acl_xattrs_and_ownership(tmp_path):
    import subprocess

    source = tmp_path / "metadata.py"
    source.write_text("old")
    source.chmod(0o751)
    subprocess.run(["/usr/bin/xattr", "-w", "com.wavefunction.synthetic", "keep-me", str(source)], check=True)
    subprocess.run(["/bin/chmod", "+a", "everyone deny execute", str(source)], check=True)
    before = source.stat()
    await hands(tmp_path)["edit_file"].ainvoke({"path": str(source), "old_text": "old", "new_text": "new"})
    assert source.read_text() == "new"
    assert (
        subprocess.check_output(["/usr/bin/xattr", "-p", "com.wavefunction.synthetic", str(source)]).strip()
        == b"keep-me"
    )
    assert (source.stat().st_uid, source.stat().st_gid) == (before.st_uid, before.st_gid)
    acl = subprocess.check_output(["/bin/ls", "-le", str(source)], text=True)
    assert "everyone deny execute" in acl


@pytest.mark.parametrize("atomic_save", [False, True])
def test_edit_rejects_concurrent_user_save(tmp_path, monkeypatch, atomic_save):
    import os
    import tempfile

    from wavefunction import fileops

    source = tmp_path / "source.py"
    source.write_text("original")
    mkstemp = tempfile.mkstemp

    def concurrent_save(*args, **kwargs):
        # The user saves while the helper is writing its replacement.
        if atomic_save:
            (tmp_path / "user-save").write_text("user changes")
            os.replace(tmp_path / "user-save", source)
        else:
            source.write_text("user changes")
        return mkstemp(*args, **kwargs)

    monkeypatch.setattr(fileops.tempfile, "mkstemp", concurrent_save)
    result = fileops.run(
        {"op": "edit", "path": str(source), "edits": [{"old_text": "original", "new_text": "agent changes"}]}
    )
    assert result["error"] == "changed" and "changed while it was being saved" in result["message"]
    assert source.read_text() == "user changes"
    assert not list(tmp_path.glob(".wavefunction-edit-*"))


def test_a_write_that_waited_for_the_lock_keeps_the_save_before_it(tmp_path, monkeypatch):
    import fcntl
    import os

    from wavefunction import fileops

    target = tmp_path / "empty.txt"
    target.write_text("")
    flock = fcntl.flock

    def first_save_lands(fd, operation):
        # While this write waited, another replaced the empty file it had opened.
        (tmp_path / "first").write_text("first\n")
        os.replace(tmp_path / "first", target)
        return flock(fd, operation)

    monkeypatch.setattr(fileops.fcntl, "flock", first_save_lands)
    result = fileops.run({"op": "write", "path": str(target), "content": "second\n"})
    assert result["error"] == "changed" and target.read_text() == "first\n"


def test_two_saves_creating_one_file_do_not_both_succeed(tmp_path, monkeypatch):
    import contextlib
    import os
    import threading

    from wavefunction import fileops

    target = tmp_path / "new.txt"
    replace, both = os.replace, threading.Barrier(2)

    def meet_at_the_rename(*args):
        # Both saves reach the rename together, where both used to succeed.
        with contextlib.suppress(threading.BrokenBarrierError):
            both.wait(timeout=1)
        return replace(*args)

    monkeypatch.setattr(fileops.os, "replace", meet_at_the_rename)
    results = {}

    def save(name):
        results[name] = fileops.run({"op": "write", "path": str(target), "content": name + "\n"})

    threads = [threading.Thread(target=save, args=(name,)) for name in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    created = [name for name, result in results.items() if result["ok"]]
    assert len(created) == 1 and target.read_text() == created[0] + "\n"
    assert [result["error"] for result in results.values() if not result["ok"]] == ["changed"]
    assert not list(tmp_path.glob(".wavefunction-edit-*"))


def test_a_save_that_finds_a_new_file_being_created_waits_for_it(tmp_path, monkeypatch):
    """A save arriving after the first claimed the name, but before its content landed, took the
    empty claim for an empty file and wrote over it: both reported success, one save was lost."""
    import threading

    from wavefunction import fileops

    target = tmp_path / "new.txt"
    claimed, renaming = threading.Event(), threading.Event()
    replace = fileops.os.replace

    def rename(*args):
        # The first save, its name claimed, holds at its rename while the second saves: long
        # enough for the second to write over the claim if nothing stops it.
        if threading.current_thread().name == "first":
            claimed.set()
            renaming.wait(timeout=1)
        else:
            renaming.set()
        return replace(*args)

    monkeypatch.setattr(fileops.os, "replace", rename)
    results = {}

    def save(name):
        results[name] = fileops.run({"op": "write", "path": str(target), "content": name + "\n"})

    first = threading.Thread(target=save, args=("first",), name="first")
    first.start()
    assert claimed.wait(timeout=5)
    second = threading.Thread(target=save, args=("second",), name="second")
    second.start()
    first.join()
    second.join()
    assert results["first"]["ok"] and not results["second"]["ok"]
    assert results["second"]["error"] == "changed" and target.read_text() == "first\n"
    assert not list(tmp_path.glob(".wavefunction-edit-*"))


@pytest.mark.sandbox
async def test_timeout_returns_when_detached_child_holds_output_pipes(tmp_path):
    import asyncio
    import contextlib
    import os
    import signal

    pid_file = tmp_path / "child.pid"
    child = f"import os,time; from pathlib import Path; Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(30)"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True); print('before timeout',flush=True); time.sleep(30)"
    try:
        result = await asyncio.wait_for(
            Sandbox([], scratch=tmp_path / "scratch").run(
                shlex.join([sys.executable, "-c", parent]), cwd=tmp_path, timeout=0.5, tail=True
            ),
            3,
        )
        assert result.timed_out and result.exit_code == 124 and "before timeout" in result.stdout
    finally:
        if pid_file.exists():
            pid = int(pid_file.read_text())
            # The command's own cleanup may have killed it already. macOS answers a signal to a
            # killed child that is not yet reaped with EPERM rather than ESRCH.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGKILL)
            for _ in range(50):
                try:
                    os.kill(pid, 0)
                except (ProcessLookupError, PermissionError):
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("Synthetic detached child did not exit")


@pytest.mark.sandbox
async def test_write_file_is_exact_and_replaces_only_what_this_run_read(tmp_path):
    import stat

    tools = hands(tmp_path)
    # A new file, in folders that do not exist yet, holds exactly the content given.
    assert "Created" in await tools["write_file"].ainvoke(
        {"path": "new/deep/config.py", "content": "VALUE = 1\n"}
    )
    assert (tmp_path / "new/deep/config.py").read_bytes() == b"VALUE = 1\n"
    source = tmp_path / "script.py"
    source.write_text("old = 1\n")
    source.chmod(0o751)
    with pytest.raises(ValueError, match="has not been read"):
        await tools["write_file"].ainvoke({"path": "script.py", "content": "new = 2\n"})
    await tools["read_file"].ainvoke({"path": "script.py"})
    source.write_text("user = 3\n")
    with pytest.raises(ValueError, match="changed since it was last read"):
        await tools["write_file"].ainvoke({"path": "script.py", "content": "new = 2\n"})
    assert source.read_text() == "user = 3\n"
    await tools["read_file"].ainvoke({"path": "script.py", "start_line": 1})
    diff = await tools["write_file"].ainvoke({"path": "script.py", "content": "new = 2\n"})
    assert "-user = 3" in diff and "+new = 2" in diff
    assert source.read_bytes() == b"new = 2\n" and stat.S_IMODE(source.stat().st_mode) == 0o751
    # What it saved itself counts as seen.
    await tools["write_file"].ainvoke({"path": "script.py", "content": "new = 3\n"})
    assert source.read_text() == "new = 3\n" and not list(tmp_path.glob(".wavefunction-edit-*"))
    # Deleted since it was read: a write would undo the delete, until a read shows it is gone.
    source.unlink()
    with pytest.raises(ValueError, match="changed since it was last read"):
        await tools["write_file"].ainvoke({"path": "script.py", "content": "new = 4\n"})
    assert not source.exists()
    with pytest.raises(ValueError, match="File not found"):
        await tools["read_file"].ainvoke({"path": "script.py"})
    await tools["write_file"].ainvoke({"path": "script.py", "content": "new = 4\n"})
    assert source.read_text() == "new = 4\n"
    # Emptied since: a change like any other, not an empty file free to overwrite.
    source.write_text("")
    with pytest.raises(ValueError, match="changed since it was last read"):
        await tools["write_file"].ainvoke({"path": "script.py", "content": "new = 5\n"})
    assert source.read_text() == ""


@pytest.mark.sandbox
async def test_edits_match_text_as_read_and_keep_line_endings_and_encoding(tmp_path):
    tools = hands(tmp_path)
    windows = tmp_path / "windows.txt"
    windows.write_bytes(b"one\r\ntwo\r\nthree\n")
    assert "\r" not in await tools["read_file"].ainvoke({"path": "windows.txt", "start_line": 1})
    await tools["edit_file"].ainvoke(
        {"path": "windows.txt", "old_text": "one\ntwo", "new_text": "ONE\nTWO\nextra"}
    )
    # Its own endings for what changed; every other line keeps the one it had.
    assert windows.read_bytes() == b"ONE\r\nTWO\r\nextra\r\nthree\n"
    # Replaced whole, as the editor saves it, the lines that did not change keep theirs too.
    from wavefunction import fileops

    read = fileops.read(
        {"path": str(windows), "text_only": True, "offset": 0, "limit": 100_000, "snapshot": None}
    )
    fileops.write(
        {
            "path": str(windows),
            "content": read["text"].replace("extra", "added\nmore"),
            "expected": read["version"],
        }
    )
    assert windows.read_bytes() == b"ONE\r\nTWO\r\nadded\r\nmore\r\nthree\n"
    # Lone CR endings (classic Mac files, progress output) are kept as well.
    windows.write_bytes(b"one\rtwo\rthree\r")
    read = fileops.read(
        {"path": str(windows), "text_only": True, "offset": 0, "limit": 100_000, "snapshot": None}
    )
    fileops.write(
        {"path": str(windows), "content": read["text"].replace("two", "TWO"), "expected": read["version"]}
    )
    assert windows.read_bytes() == b"one\rTWO\rthree\r"
    latin = tmp_path / "latin.txt"
    latin.write_bytes("café “quoted”\n".encode("cp1252"))
    assert "café “quoted”" in await tools["read_file"].ainvoke({"path": "latin.txt"})
    await tools["edit_file"].ainvoke({"path": "latin.txt", "old_text": "café", "new_text": "thé"})
    assert latin.read_bytes() == "thé “quoted”\n".encode("cp1252")
    with pytest.raises(ValueError, match="cannot hold"):
        await tools["edit_file"].ainvoke({"path": "latin.txt", "old_text": "thé", "new_text": "茶"})
    # One legacy byte deep in a mostly UTF-8 file: every page reads in the one encoding edits
    # use, so the first page's text matches an edit and continuation offsets line up.
    mixed = tmp_path / "mixed.txt"
    mixed.write_bytes("".join(f"{i:04d} café\n" for i in range(1, 2001)).encode() + b"don\x92t\n")
    first = await tools["read_file"].ainvoke({"path": "mixed.txt", "start_line": 1, "line_count": 1})
    last = await tools["read_file"].ainvoke({"path": "mixed.txt", "start_line": 2001, "line_count": 1})
    assert "café" not in first and "don’t" in last
    shown = first.split(": ", 1)[1].split("\n")[0]
    await tools["edit_file"].ainvoke({"path": "mixed.txt", "old_text": shown, "new_text": "0001 coffee"})
    assert mixed.read_bytes().startswith(b"0001 coffee\n0002 caf\xc3\xa9")
    repeated = tmp_path / "repeated.py"
    repeated.write_text("x = 1\ny = 2\nx = 1\nx = 1\n")
    with pytest.raises(ValueError, match="found 3 matches"):
        await tools["edit_file"].ainvoke({"path": "repeated.py", "old_text": "x = 1", "new_text": "x = 0"})
    await tools["edit_file"].ainvoke(
        {"path": "repeated.py", "edits": [{"old_text": "x = 1", "new_text": "x = 0", "replace_all": True}]}
    )
    assert repeated.read_text() == "x = 0\ny = 2\nx = 0\nx = 0\n"
    # Overlapping occurrences are two matches; replace_all changes those that do not overlap.
    overlap = tmp_path / "overlap.txt"
    overlap.write_text("aaa")
    with pytest.raises(ValueError, match="found 2 matches"):
        await tools["edit_file"].ainvoke({"path": "overlap.txt", "old_text": "aa", "new_text": "b"})
    assert overlap.read_text() == "aaa"
    await tools["edit_file"].ainvoke(
        {"path": "overlap.txt", "old_text": "aa", "new_text": "b", "replace_all": True}
    )
    assert overlap.read_text() == "ba"
    # One character in a huge line: the diff is cut, the file is not.
    wide = tmp_path / "wide.txt"
    wide.write_text("x" * 100_000 + "y")
    diff = await tools["edit_file"].ainvoke({"path": "wide.txt", "old_text": "y", "new_text": "z"})
    assert len(diff) < 40_200 and "Diff shortened to 40,000 characters" in diff
    assert wide.read_text() == "x" * 100_000 + "z"


@pytest.mark.sandbox
async def test_reads_tell_text_from_images_and_binaries_and_say_where_a_file_ends(tmp_path):
    read = hands(tmp_path)["read_file"]
    # Text that happens to start like an image format is still text.
    for name, text in [("roadmap.md", "P1 roadmap\nship it\n"), ("page.ps", "%!PS-Adobe-3.0\nshowpage\n")]:
        (tmp_path / name).write_text(text)
        assert await read.ainvoke({"path": name}) == text
    (tmp_path / "data.bin").write_bytes(b"\x00\x01binary\xff")
    assert "is a binary file (9 bytes)" in await read.ainvoke({"path": "data.bin"})
    assert "Nothing to read at offset 99" in await read.ainvoke({"path": "roadmap.md", "offset": 99})
    assert "Nothing to read at line 9: the file has 2 lines" in await read.ainvoke(
        {"path": "roadmap.md", "start_line": 9}
    )
    (tmp_path / "long.py").write_text("".join(f"line {i}\n" for i in range(1, 3001)))
    page = await read.ainvoke({"path": "long.py", "start_line": 1})
    assert "200: line 200" in page and "201:" not in page and "start_line=201" in page
    page = await read.ainvoke({"path": "long.py", "start_line": 201})
    assert page.startswith("201: line 201\n") and "400: line 400" in page and "start_line=401" in page
    page = await read.ainvoke({"path": "long.py", "start_line": 1, "line_count": 2000, "limit": 40_000})
    assert "2000: line 2000" in page and "2001:" not in page and "start_line=2001" in page
    wide = "α" * 6_000 + "中" * 6_000 + "ω" * 6_000 + "Z" * 32_000
    (tmp_path / "wide.txt").write_text(wide)
    page = await read.ainvoke({"path": "wide.txt"})
    assert page == wide[:6_000] + "\n[More text available: call read_file with offset=6000]"
    page = await read.ainvoke({"path": "wide.txt", "offset": 6_000})
    assert page == wide[6_000:12_000] + "\n[More text available: call read_file with offset=12000]"
    page = await read.ainvoke({"path": "wide.txt", "limit": 40_000})
    assert page == wide[:40_000] + "\n[More text available: call read_file with offset=40000]"
    assert await read.ainvoke({"path": "wide.txt", "offset": 40_000, "limit": 40_000}) == wide[40_000:]


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS only")
async def test_repository_instructions_retry_after_a_grant_then_stay_fixed_per_run(stack, tmp_path):
    from wavefunction.sandbox import GRANTS_SETTING

    folder = tmp_path / "repository"
    (folder / ".git").mkdir(parents=True)
    (folder / "AGENTS.md").write_text("FIRST rule\n")
    agent = stack.agent
    context = ToolContext(
        agent["id"],
        stack.repository,
        stack.files,
        stack.secrets,
        working_directory=str(folder),
        repository_instructions=True,
        coding_workspace=stack.services.coding_workspace,
    )

    async def told(middleware, messages):
        update = await middleware.abefore_model({"messages": messages}, None)
        return update["messages"][0] if update else None

    middleware = EnvironmentMiddleware(context)
    turn = [HumanMessage(content="Start")]
    denied = await told(middleware, turn)
    assert "could not be read" in denied.content and "FIRST rule" not in denied.content
    assert middleware.instructions is None
    stack.repository.set_setting(GRANTS_SETTING, [{"path": str(folder), "writable": True}])
    # The next turn is told only what changed: the instructions, readable now.
    turn = [*turn, denied, AIMessage(content="Done"), HumanMessage(content="Again")]
    first = await told(middleware, turn)
    assert (
        set(first.additional_kwargs["environment"]) == {"repository_instructions"}
        and "FIRST rule" in first.content
    )
    turn = [*turn, first, AIMessage(content="Done"), HumanMessage(content="Once more")]
    # Another run with nothing changed adds nothing, so the conversation's prefix stays cached.
    assert await told(EnvironmentMiddleware(replace(context, run_id="next-run")), turn) is None
    (folder / "AGENTS.md").write_text("SECOND rule\n")
    # The same instructions for the whole run; the next run reads the file as it is then.
    assert await told(middleware, turn) is None
    assert "SECOND rule" in (await told(EnvironmentMiddleware(context), turn)).content
    # A chat whose folder is removed is told the instructions no longer apply.
    cleared = await told(EnvironmentMiddleware(replace(context, working_directory=None)), turn)
    assert (
        "No repository instructions apply."
        in cleared.additional_kwargs["environment"]["repository_instructions"]
    )
    # A folder chosen mid-turn, as choose_repository_folder does, is told before the next model
    # call, with its instructions, rather than after the turn has edited without them.
    chosen = [
        *turn,
        cleared,
        tool_call_message("choose_repository_folder", {}, "pick"),
        ToolMessage(content="The user chose the folder", tool_call_id="pick"),
    ]
    update = await told(EnvironmentMiddleware(context), chosen)
    assert "SECOND rule" in update.content and "local" in update.additional_kwargs["environment"]
    # The app's data folder stays private to the sandbox even where a grant names it.
    private = stack.services.settings.data_dir / "synthetic-private-repo"
    private.mkdir()
    (private / "AGENTS.md").write_text("PRIVATE rule\n")
    stack.repository.set_setting(GRANTS_SETTING, [{"path": str(private), "writable": False}])
    hidden = (
        await told(
            EnvironmentMiddleware(replace(context, working_directory=str(private))),
            [HumanMessage(content="Start")],
        )
    ).content
    assert "PRIVATE rule" not in hidden and "could not be read" in hidden
