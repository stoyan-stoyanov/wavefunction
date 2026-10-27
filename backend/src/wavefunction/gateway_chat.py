"""Keep gateway reasoning fields across the existing Chat Completions tool loop.

LangChain's generic OpenAI client deliberately drops third-party response fields. Both of
our gateways require these fields to round-trip, including signed/encrypted reasoning blocks.
Everything else continues through the standard client.

Two more things only the gateways need: Anthropic's models cache a prompt only where the request
marks it, and OpenRouter reports what each call cost beside its token counts.
"""

import asyncio
import hashlib
import hmac
import json
import math
import time
from collections import deque
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from langchain_core.messages import AIMessage
from langchain_core.runnables import ensure_config
from langchain_openai import ChatOpenAI

from .models import gateway_diagnostics, message_usage

REASONING_FIELDS = ("reasoning", "reasoning_content", "reasoning_details")
CACHE_POINT = {"type": "ephemeral"}


def _wire_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _record_request(payload: dict) -> None:
    if (record := gateway_diagnostics.get()) is None:
        return
    # extra_body is merged into the SDK's JSON body; fingerprint what it will actually send.
    wire = {**payload, **(payload.get("extra_body") or {})}
    history, system = hashlib.sha256(), hashlib.sha256()
    prefixes: deque[dict] = deque(maxlen=16)
    messages = wire.get("messages") or []
    for count, message in enumerate(messages, 1):
        encoded = _wire_bytes(message) + b"\n"
        history.update(encoded)
        if message.get("role") in {"system", "developer"}:
            system.update(encoded)
        prefixes.append({"messages": count, "sha256": history.hexdigest()})
    record.update(
        hash_version=1,
        system_sha256=system.hexdigest(),
        tools_sha256=hashlib.sha256(_wire_bytes(wire.get("tools") or [])).hexdigest(),
        history_sha256=history.hexdigest(),
        history_messages=len(messages),
        history_prefixes=list(prefixes),
    )
    if session_id := wire.get("session_id"):
        record["session_id"] = session_id


def _record_provider(data: dict) -> None:
    if (record := gateway_diagnostics.get()) and isinstance(data.get("provider"), str):
        # Some streams repeat this on every delta. Assign it once per event record rather
        # than placing it in LangChain's chunk metadata, whose reducer concatenates strings.
        record["upstream_provider"] = data["provider"][:128]


def stream_diagnostics() -> dict:
    """Constant-size, per-attempt stream evidence; never response text or headers."""
    record = gateway_diagnostics.get() or {}
    stream = dict(record.get("_stream") or {})
    if last := record.get("_last_chunk_at"):
        stream["last_chunk_age_ms"] = max(0, int((time.monotonic() - last) * 1000))
    return stream


def request_diagnostics() -> dict:
    return {key: value for key, value in (gateway_diagnostics.get() or {}).items() if not key.startswith("_")}


def _cache_point(message: dict) -> bool:
    """Mark the message's last text as a place the provider caches the request up to."""
    content = message.get("content")
    if isinstance(content, str) and content:
        message["content"] = [{"type": "text", "text": content, "cache_control": CACHE_POINT}]
        return True
    if isinstance(content, list):
        for index in range(len(content) - 1, -1, -1):
            part = content[index]
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                # A new list and part: these can be the conversation's own, and a mark left on
                # one would stay on every later request, past the four a request may carry.
                message["content"] = [
                    *content[:index],
                    {**part, "cache_control": CACHE_POINT},
                    *content[index + 1 :],
                ]
                return True
    return False


def mark_cache_points(messages: list[dict]) -> None:
    """Anthropic's cache breakpoints: the instructions, and the newest message that has text.

    The instructions (with the tools before them) are the part every request repeats; the newest
    message is where the next request picks up, so each call reads the conversation so far from
    the cache instead of paying for it again. A compaction note, the only other system message,
    changes and is not marked.
    """
    system = next((message for message in messages if message.get("role") == "system"), None)
    if system is not None:
        _cache_point(system)
    for message in reversed(messages):
        if message is not system and _cache_point(message):
            break


def _same_block(left, right):
    if left.get("id") and right.get("id") and left["id"] != right["id"]:
        return False
    if left.get("index") is not None and right.get("index") is not None:
        return left["index"] == right["index"]
    return bool(left.get("id") and left["id"] == right.get("id"))


def reasoning_fields(message):
    result = {key: deepcopy(message[key]) for key in REASONING_FIELDS if message.get(key) is not None}
    # Keep deltas as separate records in LangChain's message reducer. Its generic dictionary
    # merger concatenates repeated `format` strings, corrupting signed reasoning metadata.
    for fragment in message.get("gateway_reasoning_chunks", []):
        for key in ("reasoning", "reasoning_content"):
            if isinstance(fragment.get(key), str):
                result[key] = result.get(key, "") + fragment[key]
        for detail in fragment.get("reasoning_details") or []:
            details = result.setdefault("reasoning_details", [])
            previous = next((item for item in details if _same_block(item, detail)), None)
            if previous is None:
                details.append(deepcopy(detail))
                continue
            for key, value in detail.items():
                if key in {"text", "summary", "data", "signature"} and isinstance(value, str):
                    previous[key] = previous.get(key, "") + value
                elif value is not None:
                    previous[key] = value
    return result


def collapse_reasoning(message):
    """Store a streamed reply's reasoning as the merged fields sent back to the provider.

    Streaming keeps one record per delta so LangChain's merger cannot corrupt signed
    metadata. Kept in history, those records were several times the size of the reasoning
    itself, inflating every saved checkpoint and every request estimate built from them.
    """
    kwargs = message.additional_kwargs
    if "gateway_reasoning_chunks" in kwargs:
        merged = reasoning_fields(kwargs)
        message.additional_kwargs = {
            **{k: v for k, v in kwargs.items() if k != "gateway_reasoning_chunks"},
            **merged,
        }
    return message


def _cost(usage) -> dict:
    """OpenRouter's price for a call, which LangChain's token counts leave out."""
    cost = (usage or {}).get("cost") if isinstance(usage, dict) else None
    return {"cost": cost} if isinstance(cost, int | float) and not isinstance(cost, bool) else {}


class ChatGateway(ChatOpenAI):
    zero_data_retention: bool = False

    def _create_chat_result(self, response, generation_info=None):
        info = {key: value for key, value in (generation_info or {}).items() if key != "headers"}
        result = super()._create_chat_result(response, info)
        data = response if isinstance(response, dict) else response.model_dump()
        _record_provider(data)
        for generation, choice in zip(result.generations, data.get("choices", []), strict=False):
            generation.message.additional_kwargs.update(reasoning_fields(choice.get("message", {})))
        if result.generations:
            result.generations[0].message.response_metadata.update(_cost(data.get("usage")))
        return result

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        _record_provider(chunk)
        info = dict(base_generation_info or {})
        headers = info.pop("headers", {})
        record = gateway_diagnostics.get()
        if record is not None:
            stream = record.setdefault(
                "_stream",
                {"chunks_received": 0, "text_chars": 0, "reasoning_chars": 0, "tool_argument_chars": 0},
            )
            stream["chunks_received"] += 1
            record["_last_chunk_at"] = time.monotonic()
            identity = headers.get("x-generation-id") or chunk.get("id")
            if isinstance(identity, str):
                stream["response_id"] = identity[:200]
                if identity.startswith("gen-") and len(identity) <= 200:
                    record["_generation_id"] = identity
                    stream["generation_id"] = identity
        result = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, info)
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        if record is not None and choices:
            delta = choices[0].get("delta") or {}
            stream["text_chars"] += len(delta.get("content") or "")
            stream["reasoning_chars"] += sum(
                len(value) if isinstance(value, str) else len(json.dumps(value))
                for key in REASONING_FIELDS
                if (value := delta.get(key)) is not None
            )
            stream["tool_argument_chars"] += sum(
                len((call.get("function") or {}).get("arguments") or "")
                for call in delta.get("tool_calls") or []
            )
        if result is not None and choices:
            fields = reasoning_fields(choices[0].get("delta") or {})
            if fields:
                result.message.additional_kwargs["gateway_reasoning_chunks"] = [fields]
        if result is not None:
            result.message.response_metadata.update(_cost(chunk.get("usage")))
            # Reuse the SDK's usage normalization, including explicitly reported zeros.
            # This is the same frame the runtime counts, so reconciliation adds nothing.
            if record is not None and (usage := message_usage(result.message)):
                record["_stream_usage"] = usage
        return result

    async def interrupted_usage(self) -> tuple[dict, dict]:
        """One bounded metadata lookup with this model's captured account, only after failure.

        A usage frame already delivered through the SDK was counted by the runtime. Only a
        recovered frame is additional usage; returning both keeps retries from double counting.
        """
        record = gateway_diagnostics.get() or {}
        if reported := record.get("_stream_usage"):
            return reported, {}
        generation = record.get("_generation_id")
        if not generation or urlparse(self.openai_api_base or "").hostname != "openrouter.ai":
            return {}, {}
        try:
            response = await asyncio.wait_for(
                self.root_async_client.get(
                    "/generation",
                    cast_to=dict[str, Any],
                    options={"params": {"id": generation}, "max_retries": 0, "timeout": 3.0},
                ),
                timeout=3.5,
            )
            data = response.get("data") or {}
            if data.get("id") != generation:
                return {}, {}
            usage = {}
            for field, source in (
                ("input_tokens", "native_tokens_prompt"),
                ("output_tokens", "native_tokens_completion"),
            ):
                value = data.get(source)
                if not isinstance(value, int):
                    value = data.get("tokens_prompt" if field == "input_tokens" else "tokens_completion")
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    usage[field] = value
            if "input_tokens" in usage and "output_tokens" in usage:
                usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
            cached = data.get("native_tokens_cached")
            if (
                isinstance(cached, int)
                and not isinstance(cached, bool)
                and 0 <= cached <= usage.get("input_tokens", -1)
            ):
                usage["input_token_details"] = {"cache_read": cached}
            reasoning = data.get("native_tokens_reasoning")
            if (
                isinstance(reasoning, int)
                and not isinstance(reasoning, bool)
                and 0 <= reasoning <= usage.get("output_tokens", -1)
            ):
                usage["output_token_details"] = {"reasoning": reasoning}
            cost = data.get("usage")
            if (
                isinstance(cost, int | float)
                and not isinstance(cost, bool)
                and math.isfinite(cost)
                and cost >= 0
            ):
                usage["cost"] = cost
            return usage, usage
        except Exception as error:
            # Missing/eventually consistent metadata stays unknown. A lookup must neither
            # mask the original failure nor leave an unowned background task on shutdown.
            record.setdefault("_stream", {})["usage_lookup_error"] = type(error).__name__
            return {}, {}

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        for message, wire in zip(
            self._convert_input(input_).to_messages(), payload.get("messages", []), strict=False
        ):
            if isinstance(message, AIMessage):
                wire.update(reasoning_fields(message.additional_kwargs))
        if "ai-gateway.vercel.sh" in (self.openai_api_base or ""):
            # Vercel places Anthropic's cache breakpoints itself when asked, on the newest
            # message and before the newest request, and leaves models that cache on their own
            # untouched. A new dict: the one in the payload is the model's own setting.
            gateway = {"caching": "auto"}
            if self.zero_data_retention:
                # Only providers with a ZDR agreement; a model with none fails with an error.
                gateway["zeroDataRetention"] = True
            payload["extra_body"] = {
                **(payload.get("extra_body") or {}),
                "providerOptions": {"gateway": gateway},
            }
        elif "claude" in self.model_name.lower() and payload.get("messages"):
            mark_cache_points(payload["messages"])
        if urlparse(self.openai_api_base or "").hostname == "openrouter.ai":
            if self.zero_data_retention:
                # Only ZDR endpoints; a model with none fails with an error.
                extra = payload.get("extra_body") or {}
                payload["extra_body"] = {**extra, "provider": {**(extra.get("provider") or {}), "zdr": True}}
            thread = (ensure_config().get("configurable") or {}).get("thread_id")
            if thread:
                # OpenRouter's sticky route is scoped to a conversation and credential.
                # Key rotation deliberately starts a new route; neither identity is logged.
                session = hmac.new(
                    self.openai_api_key.get_secret_value().encode(),
                    f"wavefunction:openrouter:{thread}".encode(),
                    hashlib.sha256,
                ).hexdigest()
                payload["extra_body"] = {**(payload.get("extra_body") or {}), "session_id": session}
            _record_request(payload)
        return payload
