"""Opt-in real-model coding verification through Harness's product API.

Uses an existing current ChatGPT sign-in or an OpenRouter key read-only.
Only synthetic source, instructions, and test results are sent to the model.
"""

import argparse
import asyncio
import hashlib
import json
import os
import statistics
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx


async def verify_openrouter_cache(args):
    """Measure real cache reuse within and across two separate synthetic chats."""
    from langchain.agents.middleware import AgentMiddleware
    from wavefunction.secrets import EnvFileSecretStore, MemorySecretStore

    key = (
        EnvFileSecretStore(args.key_file.expanduser()).get("openrouter_api_key")
        if args.key_file
        else os.environ.get("OPENROUTER_API_KEY")
    )
    if not key:
        raise ValueError("Supply --key-file or OPENROUTER_API_KEY for the OpenRouter cache check")
    result = {"provider": "openrouter", "model": args.model, "upstream_requested": args.upstream}
    requests, models, calls, attempts, runs = [], [], [], [], []
    active = {}
    failure = None
    with TemporaryDirectory(prefix="wavefunction-openrouter-cache-") as temporary:
        root = Path(temporary).resolve()
        os.environ["WAVEFUNCTION_DATA_DIR"] = str(root / "data")
        from wavefunction import agent_factory, coding_workspace, github_cli, main
        from wavefunction.config import Settings
        from wavefunction.models import merge_usage

        services = main.Services(Settings(data_dir=root / "data", development_auth_bypass=True))
        main.services = services
        services.secrets = MemorySecretStore()
        services.secrets.set("openrouter_api_key", key)
        services.models.secrets = services.secrets
        discover = coding_workspace.discover
        coding_workspace.discover = lambda *a, **kw: discover(*a, **kw, home=root)

        async def signed_out():
            return False

        github_cli.commands_signed_in = signed_out
        folder = root / "repository"
        folder.mkdir()
        document = '{"queue": "synthetic-cache", "retries": 3}\n'
        (folder / "settings.json").write_text(document)

        class ReadOnlyCheck(AgentMiddleware):
            async def awrap_tool_call(self, request, handler):
                call = request.tool_call
                if call["name"] != "read_file":
                    raise RuntimeError("This synthetic cache check permits only read_file")
                path = Path(call["args"]["path"]).expanduser()
                resolved = (path if path.is_absolute() else folder / path).resolve()
                if resolved != folder / "settings.json":
                    raise RuntimeError("This synthetic cache check permits only settings.json")
                return await handler(request)

        build_middleware = agent_factory.build_middleware
        agent_factory.build_middleware = lambda *a, **kw: [ReadOnlyCheck(), *build_middleware(*a, **kw)]
        build_model = services.models.chat_model

        def encoded(value):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

        def tracked_model(*model_args, **kwargs):
            model = build_model(*model_args, **kwargs)
            if args.upstream:
                model.extra_body = {
                    **(model.extra_body or {}),
                    "provider": {"only": [args.upstream], "allow_fallbacks": False},
                }
            models.append(model)
            chat, turn = active["chat"], active["turn"]

            async def request_hook(request):
                if len(requests) >= 12:
                    raise RuntimeError("Synthetic cache check exceeded twelve provider requests")
                payload = json.loads(request.content)
                session = payload.get("session_id")
                history, system = hashlib.sha256(), hashlib.sha256()
                messages, prefixes = [], []
                for count, message in enumerate(payload["messages"], 1):
                    wire_message = encoded(message)
                    messages.append(hashlib.sha256(wire_message).hexdigest())
                    history.update(wire_message + b"\n")
                    if message.get("role") in {"system", "developer"}:
                        system.update(wire_message + b"\n")
                    prefixes.append({"messages": count, "sha256": history.hexdigest()})
                tools = hashlib.sha256(encoded(payload.get("tools", []))).hexdigest()
                request.extensions["cache_check_request"] = len(requests)
                requests.append(
                    {
                        "request": len(requests) + 1,
                        "chat": chat,
                        "turn": turn,
                        "session_id": session,
                        "tools_sha256": tools,
                        "messages": messages,
                        "cache_diagnostics": {
                            "hash_version": 1,
                            "session_id": session,
                            "system_sha256": system.hexdigest(),
                            "tools_sha256": tools,
                            "history_sha256": history.hexdigest(),
                            "history_messages": len(messages),
                            "history_prefixes": prefixes[-16:],
                        },
                    }
                )

            async def response_hook(response):
                index = response.request.extensions["cache_check_request"]
                requests[index]["http_status"] = response.status_code

            model.root_async_client._client.event_hooks = {
                "request": [request_hook],
                "response": [response_hook],
            }
            return model

        services.models.chat_model = tracked_model
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test"
            ) as client:
                for chat in (1, 2):
                    response = await client.post(
                        "/api/coding/threads",
                        json={
                            "folder": str(folder),
                            "model": {"provider": "openrouter", "model": args.model, "max_tokens": 2048},
                        },
                    )
                    response.raise_for_status()
                    thread = response.json()
                    for turn, prompt in enumerate(
                        (
                            "Read settings.json with read_file. Return only its queue and retries as a JSON object. Do not use other tools.",
                            "From the file you just read, return the same queue and retries JSON object without using tools.",
                        ),
                        1,
                    ):
                        active.update(chat=chat, turn=turn)
                        outcome = {"chat": chat, "turn": turn, "conversation": thread["id"]}
                        run_id = None
                        try:
                            response = await client.post(
                                f"/api/coding/threads/{thread['id']}/messages", json={"content": prompt}
                            )
                            response.raise_for_status()
                            run_id = response.json()["id"]
                            run = await services.runtime.wait(run_id, timeout=180)
                            outcome["status"] = run["status"]
                            if run.get("error"):
                                outcome["error"] = str(run["error"]).replace(key, "[REDACTED]")
                            if run["status"] == "completed":
                                answer = (
                                    run["output"]["text"]
                                    .strip()
                                    .removeprefix("```json")
                                    .removeprefix("```")
                                    .removesuffix("```")
                                    .strip()
                                )
                                outcome["correct_answer"] = json.loads(answer) == json.loads(document)
                        except Exception as error:  # noqa: BLE001 — retain both chats before validating
                            outcome["error"] = str(error).replace(key, "[REDACTED]")
                            if (
                                run_id
                                and run_id in services.runtime.tasks
                                and not services.runtime.tasks[run_id].done()
                            ):
                                await services.runtime.cancel(run_id)
                        events = services.repository.list_run_events(run_id) if run_id else []
                        wire_requests = [
                            item for item in requests if item["chat"] == chat and item["turn"] == turn
                        ]
                        turn_attempts = [
                            event
                            for event in events
                            if event["event_type"] in {"model_call_finished", "model_call_failed"}
                        ]
                        outcome["attempts_match_requests"] = len(turn_attempts) == len(wire_requests)
                        run_usage = {}
                        for index, event in enumerate(turn_attempts):
                            wire = wire_requests[index] if index < len(wire_requests) else None
                            details = {
                                "chat": chat,
                                "turn": turn,
                                "request": wire["request"] if wire else None,
                                "call_id": event["payload"]["call_id"],
                                "cache_diagnostics": event["payload"].get("cache_diagnostics", {}),
                            }
                            attempts.append(
                                {
                                    **details,
                                    "event": event["event_type"],
                                    "error": event["payload"].get("error"),
                                }
                            )
                            if event["event_type"] == "model_call_finished":
                                call_usage = {}
                                for usage in event["payload"]["usage"]:
                                    if usage:
                                        run_usage = merge_usage(run_usage, usage)
                                        call_usage = merge_usage(call_usage, usage)
                                calls.append({**details, "usage": call_usage or None})
                        if outcome.get("status") == "completed":
                            outcome["usage_counted_once"] = all(
                                run_usage.get(name) == run["usage"].get(name)
                                for name in (
                                    "input_tokens",
                                    "output_tokens",
                                    "total_tokens",
                                    "input_token_details",
                                )
                            )
                        runs.append(outcome)
                        # A failed read cannot supply a meaningful same-chat follow-up.
                        # Still collect the independent second chat before validation.
                        if turn == 1 and not outcome.get("correct_answer"):
                            runs.append(
                                {
                                    "chat": chat,
                                    "turn": 2,
                                    "conversation": thread["id"],
                                    "status": "skipped",
                                    "error": "Initial read failed or returned an incorrect answer",
                                }
                            )
                            break
                        # Give the upstream cache time to persist before the next turn/chat.
                        await asyncio.sleep(2)

                def measured(group):
                    usages = [item["usage"] for item in group]
                    inputs = [(usage or {}).get("input_tokens") for usage in usages]
                    cached = [
                        (usage or {}).get("input_token_details", {}).get("cache_read") for usage in usages
                    ]
                    total = sum(inputs) if inputs and all(value is not None for value in inputs) else None
                    cache = sum(cached) if cached and all(value is not None for value in cached) else None
                    return {
                        "requests": [item["request"] for item in group],
                        "input_tokens": total,
                        "cached_tokens": cache,
                        "hit_rate": cache / total if cache is not None and total else None,
                        "hits": sum(value is not None and value > 0 for value in cached),
                        "misses": cached.count(0),
                        "unknown": cached.count(None),
                    }

                checks = {
                    "correct_answers": len(runs) == 4 and all(item.get("correct_answer") for item in runs),
                    "usage_counted_once": all(item.get("usage_counted_once") for item in runs),
                    "fixture_unchanged": (folder / "settings.json").read_text() == document,
                    "attempts_match_requests": all(item.get("attempts_match_requests") for item in runs),
                    "four_turn_models": len(models) == 4,
                }
                first, warm, chat_results = [], [], []
                histories, schemas, sessions = [], [], []
                for chat in (1, 2):
                    wire = [item for item in requests if item["chat"] == chat]
                    cold_turn = [item for item in calls if item["chat"] == chat and item["turn"] == 1]
                    next_turn = [item for item in calls if item["chat"] == chat and item["turn"] == 2]
                    histories.append(
                        bool(wire)
                        and all(
                            right["messages"][: len(left["messages"])] == left["messages"]
                            for left, right in pairwise(wire)
                        )
                    )
                    schemas.append(bool(wire) and len({item["tools_sha256"] for item in wire}) == 1)
                    sessions.append(
                        bool(wire)
                        and bool(wire[0]["session_id"])
                        and len({item["session_id"] for item in wire}) == 1
                    )
                    if wire:
                        first.append(wire[0])
                    warm.extend([*cold_turn[1:], *next_turn])
                    chat_results.append(
                        {
                            "chat": chat,
                            "first_response": measured(cold_turn[:1]),
                            "continuation": measured(cold_turn[1:]),
                            "next_turn": measured(next_turn),
                        }
                    )
                checks.update(
                    stable_wire_history=all(histories),
                    stable_tool_schemas=all(schemas),
                    stable_session_across_turns=all(sessions),
                    distinct_sessions_across_chats=len(first) == 2
                    and first[0]["session_id"] != first[1]["session_id"],
                    identical_first_prompts_and_tools=len(first) == 2
                    and all(first[0][name] == first[1][name] for name in ("messages", "tools_sha256")),
                )
                checks["diagnostics_match_wire"] = checks["attempts_match_requests"] and all(
                    all(
                        attempt["cache_diagnostics"].get(name) == value
                        for name, value in requests[attempt["request"] - 1]["cache_diagnostics"].items()
                    )
                    for attempt in attempts
                    if attempt["request"] is not None
                )
                checks["provider_diagnostics_present"] = bool(calls) and all(
                    item["cache_diagnostics"].get("session_id")
                    and item["cache_diagnostics"].get("upstream_provider")
                    for item in calls
                )
                rates = [m["hit_rate"] for item in warm if (m := measured([item]))["hit_rate"] is not None]
                median = statistics.median(rates) if rates else None
                checks["warm_reuse_at_least_80_percent"] = (
                    len(warm) >= 4 and median is not None and median >= 0.8
                )
                result.update(
                    checks=checks,
                    chats=chat_results,
                    warm=measured(warm),
                    median_warm_hit_rate=median,
                    cross_chat_first=chat_results[1]["first_response"],
                    usage_unknown_attempts=[
                        item["request"]
                        for item in requests
                        if not any(call["request"] == item["request"] and call["usage"] for call in calls)
                    ],
                )
                # Prefix and quality checks deliberately run after collecting both chats.
                # A cache miss is measured, not treated as proof that a prefix changed.
                failed = [name for name, passed in checks.items() if not passed]
                assert not failed, "Cache checks failed: " + ", ".join(failed)
        except Exception as error:  # noqa: BLE001 — save sanitized evidence, clean up, then fail below
            failure = error
            result["error"] = str(error).replace(key, "[REDACTED]")
        finally:
            result.update(requests=requests, calls=calls, attempts=attempts, runs=runs)
            for run_id, task in list(services.runtime.tasks.items()):
                if not task.done():
                    await services.runtime.cancel(run_id)
            await services.runtime.aclose()
            await services.computer.shutdown()
            await services.coding_workspace.terminals.shutdown()
            await services.github_proxy.close()
            for model in models:
                model.root_client.close()
                await model.root_async_client.close()
            services.secrets.values.clear()
    result["cleanup"] = {"runtime_closed": True, "temporary_workspace_removed": not root.exists()}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    if failure:
        raise RuntimeError(result["error"]) from None


async def verify(args):
    with TemporaryDirectory(prefix="harness-api-live-") as temporary:
        root = Path(temporary).resolve()
        os.environ["WAVEFUNCTION_DATA_DIR"] = str(root / "data")
        from wavefunction import main
        from wavefunction.codex import CodexAuth, CodexAuthError, CodexAuthStore
        from wavefunction.config import Settings
        from wavefunction.models import ModelGateway
        from wavefunction.secrets import MemorySecretStore

        class ReadOnlyAuth(CodexAuth):
            def access_token(self):
                credentials = self.credentials()
                if credentials is None or credentials.expiring():
                    raise CodexAuthError(
                        "A current sign-in is needed. This check never refreshes credentials."
                    )
                return credentials.access

        auth = ReadOnlyAuth(CodexAuthStore(args.auth_file.expanduser()))
        auth.access_token()
        services = main.Services(Settings(data_dir=root / "data", development_auth_bypass=True))
        main.services = services
        services.codex = auth
        services.secrets = MemorySecretStore()
        services.models = ModelGateway(services.secrets, codex=auth)
        services.runtime.models = services.models
        models, requests = [], []
        build_model = services.models.chat_model

        def fingerprint(value):
            return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

        def tracked_model(*model_args, **kwargs):
            model = build_model(*model_args, **kwargs)
            models.append(model)
            turn = len(models)
            route = None
            sent = 0

            async def request_hook(_request):
                nonlocal sent
                sent += 1
                if args.cache_check and sent > 12:
                    raise RuntimeError("Synthetic cache check exceeded its request budget")

            async def response_hook(response):
                nonlocal route
                if not args.cache_check or not response.is_success:
                    return
                request = response.request
                payload = json.loads(request.content)
                key = payload.get("prompt_cache_key")
                assert key and request.headers.get("session-id") == key
                assert request.headers.get("thread-id") == key
                assert request.headers.get("x-codex-turn-state") == route
                if route is None:
                    route = response.headers.get("x-codex-turn-state")
                    assert route, "Provider returned no turn routing token"
                prefix = [fingerprint(item) for item in payload["input"]]
                settings = fingerprint(
                    {k: v for k, v in payload.items() if k not in {"input", "prompt_cache_key"}}
                )
                if requests:
                    previous = requests[-1]
                    assert key == previous["cache_key"], "Conversation cache key changed between turns"
                    assert settings == previous["settings"], "Instructions, tools, or model settings changed"
                    assert prefix[: len(previous["input"])] == previous["input"], "Prior input prefix changed"
                requests.append({"turn": turn, "cache_key": key, "settings": settings, "input": prefix})

            model.http_async_client.event_hooks = {"request": [request_hook], "response": [response_hook]}
            return model

        services.models.chat_model = tracked_model
        folder = root / "repository"
        folder.mkdir()
        (folder / "AGENTS.md").write_text(
            "Make a focused fix. Do not change tests. Run python3 -m unittest -v. End with API-LIVE-VERIFIED.\n"
        )
        original = "def total(price, count):\n    return price + count\n"
        (folder / "pricing.py").write_text(original)
        test = "import unittest\nfrom pricing import total\nclass PricingTests(unittest.TestCase):\n    def test_total(self): self.assertEqual(total(5, 3), 15)\n    def test_zero(self): self.assertEqual(total(8, 0), 0)\n"
        (folder / "test_pricing.py").write_text(test)
        result = {}
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/coding/threads",
                    json={
                        "folder": str(folder),
                        "model": {
                            "provider": "codex",
                            "model": args.model,
                            **({"reasoning_effort": "low"} if args.cache_check else {}),
                        },
                    },
                )
                response.raise_for_status()
                thread = response.json()
                ws = services.coding_workspace
                before = await ws.runner().run("python3 -m unittest -v", cwd=folder)
                assert before.exit_code != 0 and "FAILED" in before.stderr
                response = await client.post(
                    f"/api/coding/threads/{thread['id']}/messages",
                    json={
                        "content": "Fix the incorrect pricing total. A total of price 5 and count 3 must be 15, and zero count must be zero. Follow the repository instructions and test the change."
                    },
                )
                response.raise_for_status()
                run = await services.runtime.wait(response.json()["id"], timeout=360)
                assert run["status"] == "completed", run.get("error")
                after = await ws.runner().run("python3 -m unittest -v", cwd=folder)
                assert after.exit_code == 0, after.stderr
                assert (folder / "test_pricing.py").read_text() == test
                assert (folder / "pricing.py").read_text() != original
                assert "API-LIVE-VERIFIED" in run["output"]["text"]
                events = services.repository.list_run_events(run["id"])
                tools = [
                    e["payload"].get("tool", e["payload"].get("name"))
                    for e in events
                    if e["event_type"] in {"tool_started", "execution_tool_started"}
                ]
                assert "edit_file" in str(events) and "run_command" in str(events)
                response = await client.post(
                    f"/api/coding/threads/{thread['id']}/messages",
                    json={
                        "content": "What function did you just fix, and which tests did you run? Answer briefly from this conversation."
                    },
                )
                response.raise_for_status()
                followup = await services.runtime.wait(response.json()["id"], timeout=180)
                assert followup["status"] == "completed"
                assert "total" in followup["output"]["text"]
                assert len(ws.runs(thread["id"])) == 2
                assert len(services.repository.agent_ids()) == 1
                result = {
                    "model": args.model,
                    "product_api": True,
                    "real_model": True,
                    "code_changed": True,
                    "independent_tests_passed": True,
                    "instructions_followed": True,
                    "followup_memory": True,
                    "one_agent": True,
                    "tool_calls": tools,
                    "usage": run.get("usage", {}),
                }
                if args.cache_check:
                    from wavefunction.models import merge_usage

                    calls = []
                    for completed in (run, followup):
                        usage = {}
                        turn_calls = [
                            item
                            for event in services.repository.list_run_events(completed["id"])
                            if event["event_type"] == "model_call_finished"
                            for item in event["payload"]["usage"]
                            if item
                        ]
                        for item in turn_calls:
                            usage = merge_usage(usage, item)
                        assert usage == completed["usage"], (
                            "Run usage must count each provider response exactly once"
                        )
                        calls.extend({"warm": index > 0, **item} for index, item in enumerate(turn_calls))
                    assert len(calls) == len(requests)
                    warm = [item for item in calls if item["warm"]]
                    cached = sum(item.get("input_token_details", {}).get("cache_read", 0) for item in warm)
                    total = sum(item["input_tokens"] for item in warm)
                    rates = [
                        item.get("input_token_details", {}).get("cache_read", 0) / item["input_tokens"]
                        for item in warm
                    ]
                    median = statistics.median(rates) if rates else 0
                    result["prompt_cache"] = {
                        "calls": calls,
                        "stable_prefixes": True,
                        "correct_turn_routing": True,
                        "new_turn_resets_routing": len(models) == 2,
                        "usage_counted_once": True,
                        "warm_input_tokens": total,
                        "warm_cached_tokens": cached,
                        "warm_hit_rate": cached / total if total else 0,
                        "median_warm_hit_rate": median,
                    }
                    print(json.dumps({"prompt_cache": result["prompt_cache"]}), flush=True)
                    # Provider eviction/overflow can miss despite identical prefixes/routing.
                    # Require repeated substantial reuse, without promising every call hits.
                    assert len(models) == 2 and len(warm) >= 2
                    assert median >= 0.8, (
                        "Typical warm cache reuse was below 80%; inspect provider routing/availability"
                    )
        finally:
            for run_id, task in list(services.runtime.tasks.items()):
                if not task.done():
                    await services.runtime.cancel(run_id)
            await services.runtime.aclose()
            await services.computer.shutdown()
            await services.coding_workspace.terminals.shutdown()
            await services.github_proxy.close()
            for model in models:
                model.root_client.close()
                await model.root_async_client.close()
        result["cleanup"] = {"temporary_workspace_removed": False, "runtime_closed": True}
    result["cleanup"]["temporary_workspace_removed"] = not root.exists()
    assert all(result["cleanup"].values())
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("codex", "openrouter"), default="codex")
    parser.add_argument("--auth-file", type=Path)
    parser.add_argument(
        "--key-file",
        type=Path,
        help="Read-only env file containing openrouter_api_key; otherwise uses OPENROUTER_API_KEY",
    )
    parser.add_argument("--upstream", help="OpenRouter provider slug to pin for this verification only")
    parser.add_argument(
        "--report", type=Path, help="Save the OpenRouter cache check's sanitized measurements"
    )
    parser.add_argument("--model")
    parser.add_argument(
        "--cache-check",
        action="store_true",
        help="Verify real prompt/routing stability and at least 80%% median warm cached input",
    )
    args = parser.parse_args()
    if args.provider == "codex" and not args.auth_file:
        parser.error("--auth-file is required for Codex")
    if args.provider == "openrouter" and not args.cache_check:
        parser.error("OpenRouter verification requires --cache-check")
    args.model = args.model or (
        "gpt-6.1-sol" if args.provider == "codex" else "deepseek/deepseek-v4-pro-0813"
    )
    asyncio.run(verify(args) if args.provider == "codex" else verify_openrouter_cache(args))
