from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable, Iterator
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlparse

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from .codex import (
    CODEX_BASE_URL,
    CodexAuth,
    CodexAuthError,
    codex_headers,
    codex_http_clients,
    describe_codex_error,
    search_web,
)
from .ollama_provider import DEFAULT_OLLAMA_URL, ollama_context_window
from .reasoning import reasoning_efforts, reasoning_kwargs, validate_reasoning
from .secrets import SecretBackend
from .spec_common import ModelSpec

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
VERCEL_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"
# Providers whose own search runs on the key a chat already uses. A ChatGPT sign-in searches too.
SEARCH_KEYS = {
    "openrouter": "openrouter_api_key",
    "vercel": "vercel_api_key",
    "anthropic": "anthropic_api_key",
    "gemini": "gemini_api_key",
}


# Pacing for the offline demo model, so its reply streams like a real one.
DEMO_CHUNK_DELAY = 0.015
logger = logging.getLogger(__name__)

# What a model call made inside a tool spent — a web search, a picture — sent to the run the
# tool belongs to, which adds it to what its own calls spent.
NESTED_USAGE = "nested_usage"

# A fresh container per middleware attempt. SDK worker contexts inherit it without
# sharing request state between conversations or changing a model's client defaults.
gateway_diagnostics: ContextVar[dict | None] = ContextVar("gateway_diagnostics", default=None)


def usage_from(raw: Any) -> dict[str, Any]:
    """LangChain's shape of usage from an OpenAI-style `usage` of either API, cost included."""
    if not isinstance(raw, dict):
        return {}
    input_tokens = int(raw.get("prompt_tokens") or raw.get("input_tokens") or 0)
    output_tokens = int(raw.get("completion_tokens") or raw.get("output_tokens") or 0)
    usage: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(raw.get("total_tokens") or input_tokens + output_tokens),
    }
    read = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}
    cached = {
        name: read[key]
        for name, key in (("cache_read", "cached_tokens"), ("cache_creation", "cache_write_tokens"))
        if isinstance(read.get(key), int) and read[key]
    }
    if cached:
        usage["input_token_details"] = cached
    written = raw.get("completion_tokens_details") or raw.get("output_tokens_details") or {}
    if isinstance(written.get("reasoning_tokens"), int) and written["reasoning_tokens"]:
        usage["output_token_details"] = {"reasoning": written["reasoning_tokens"]}
    if isinstance(raw.get("cost"), int | float):
        usage["cost"] = raw["cost"]
    return usage if usage["total_tokens"] or "cost" in usage else {}


def message_usage(message: Any) -> dict[str, Any]:
    """What one reply cost: its token counts, and the price where the gateway gave one."""
    usage = dict(getattr(message, "usage_metadata", None) or {})
    cost = (getattr(message, "response_metadata", None) or {}).get("cost")
    if usage and isinstance(cost, int | float) and not isinstance(cost, bool):
        usage["cost"] = cost
    return usage


def merge_usage(current: Any, added: Any) -> dict[str, Any]:
    """A running total of usage: the three token counts always, and with them whatever else a
    provider reported, such as cached and cache-written input, reasoning and the price."""

    def add(total: dict[str, Any], more: dict[str, Any]) -> dict[str, Any]:
        total = dict(total)
        for key, value in more.items():
            if isinstance(value, dict):
                total[key] = add(total.get(key) or {}, value)
            elif isinstance(value, int | float) and not isinstance(value, bool):
                total[key] = round(total.get(key, 0) + value, 10)
        return total

    return add(
        {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, **(current or {})}, dict(added or {})
    )


def report_usage(raw: Any) -> None:
    """Add what a call inside a tool spent to its run, the way the run's own calls are."""
    from .execution_policy import emit_policy

    if usage := usage_from(raw):
        emit_policy(NESTED_USAGE, usage=usage)


def reasons(model: ModelSpec) -> bool:
    """Whether a model on OpenAI's own API reasons: given an effort, or of a family that does."""
    if model.reasoning_effort:
        return model.reasoning_effort != "none"
    return bool(re.match(r"o\d|gpt-[5-9]", model.model)) and "chat" not in model.model


def _provider_metadata(error: BaseException) -> dict[str, Any]:
    body = getattr(error, "body", None)
    metadata = body.get("metadata") if isinstance(body, dict) else None
    return metadata if isinstance(metadata, dict) else {}


def _codex_auth_failure(error: BaseException | None) -> str:
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, CodexAuthError):
            return str(error)
        body = getattr(error, "body", None)
        if isinstance(body, dict):
            detail = body.get("error", body)
            if isinstance(detail, dict) and detail.get("code") == "codex_local_auth":
                return str(detail.get("message") or "Sign in again in Settings › Models.")
        error = error.__cause__ or error.__context__
    return ""


def is_retryable_provider_error(error: BaseException) -> bool:
    """Whether waiting and asking again could plausibly succeed.

    Covers three kinds of transient failure:

    - A dropped connection or a timeout. These carry no status code, so a check based on status
      alone rejected them — which is the wrong answer for the most ordinary outage there is.
    - A provider 5xx.
    - A rate limit belonging to the *shared upstream pool*. That one clears on its own because
      every endpoint for the model is busy. A limit on the user's own key does not, and retrying
      it only burns quota they are already out of, so it stays terminal.

    4xx request errors are never retried: a malformed request is malformed on the second try.
    """
    from anthropic import APIConnectionError as AnthropicConnectionError
    from openai import APIConnectionError, APITimeoutError

    if _codex_auth_failure(error):
        return False
    # DecodingError is a RequestError, not a TransportError: malformed compressed
    # responses are just as transient as dropped connections.
    if isinstance(
        error,
        (
            TimeoutError,
            APIConnectionError,
            APITimeoutError,
            AnthropicConnectionError,
            httpx.TransportError,
            httpx.DecodingError,
        ),
    ) or (isinstance(error, ConnectionError) and "Ollama" in str(error)):
        return True
    status_code = _status_code(error)
    # An error inside a stream that had answered 200, or a stream with no status at all: Anthropic
    # sends an overload as an SSE error event, OpenRouter an upstream disconnect as an error chunk,
    # and the Responses API a failed response. The same overload as an HTTP 529 is retried below.
    if status_code in (None, 200) and _stream_error_kind(error) in {
        "overloaded_error",
        "api_error",
        "server_error",
    }:
        return True
    # 507: a gateway whose own retry of the upstream failed because the request was too large
    # for it to buffer ("exceeded request buffer limit while retrying upstream"). 529: Anthropic
    # is overloaded, which clears on its own.
    if status_code in {500, 502, 503, 504, 507, 529}:
        return True
    if status_code != 429:
        return False
    from .execution_policy import transient_error

    return transient_error(error)


def _stream_error_kind(error: BaseException) -> str:
    """The type or code a provider gave an error: the OpenAI SDK keeps it as `code`, Anthropic's
    body as `{"error": {"type": …}}`."""
    if isinstance(code := getattr(error, "code", None), str):
        return code
    body = getattr(error, "body", None)
    detail = body.get("error", body) if isinstance(body, dict) else None
    return str(detail.get("type") or detail.get("code") or "") if isinstance(detail, dict) else ""


def _google_error(error: BaseException):
    """Gemini's SDK error, which LangChain's Gemini model keeps as the cause of its own."""
    from google.genai.errors import APIError

    return next((item for item in (error, error.__cause__) if isinstance(item, APIError)), None)


def _status_code(error: BaseException) -> int | None:
    """The HTTP status of a provider error. Google's SDK calls it `code`."""
    google = _google_error(error)
    return google.code if google is not None else getattr(error, "status_code", None)


def _error_host(error: BaseException) -> str:
    """The host of the request that failed, or "" when the error carries none."""
    error = _google_error(error) or error
    try:
        request = getattr(error, "request", None)
        if request is None:
            request = getattr(getattr(error, "response", None), "request", None)
        return urlparse(str(getattr(request, "url", ""))).hostname or ""
    except (ValueError, RuntimeError):
        return ""


def _error_provider_name(error: BaseException) -> str:
    """Identify the actual failing endpoint, including when it is a fallback model."""
    host = _error_host(error)
    return {
        "api.openai.com": "OpenAI",
        "openrouter.ai": "OpenRouter",
        "ai-gateway.vercel.sh": "Vercel AI Gateway",
        "api.anthropic.com": "Anthropic",
        "generativelanguage.googleapis.com": "Google Gemini",
    }.get(host, "The selected provider")


def describe_provider_error(error: BaseException) -> str:
    """A sentence a non-programmer can act on, instead of a nested provider JSON blob."""
    from ollama import ResponseError as OllamaResponseError

    if auth_failure := _codex_auth_failure(error):
        return auth_failure
    if isinstance(error, TimeoutError):
        gap = getattr(error, "timeout_s", None)
        detail = f" No response chunk arrived for {gap:g} seconds." if isinstance(gap, int | float) else ""
        return (
            "The provider's response stream timed out before finishing."
            + detail
            + " Any received partial response is saved. Try again when the provider is responding."
        )
    if isinstance(error, OllamaResponseError):
        if error.status_code == 404:
            return "This model is not installed in Ollama. Download it in Ollama, then refresh models in Settings."
        return f"Ollama could not run this model: {error.error}"
    if isinstance(error, ConnectionError) and "Ollama" in str(error):
        return "Could not connect to Ollama. Start Ollama and check its server address in Settings › Models."
    if isinstance(error, CodexAuthError):
        return str(error)
    # Checked before the status codes below: a ChatGPT usage limit is also a 429, and the
    # OpenRouter sentence for that one names the wrong account and the wrong fix. Only for the
    # ChatGPT sign-in: OpenAI's own API says `rate_limit_exceeded` too, about the user's API key.
    if _error_host(error) in {"", urlparse(CODEX_BASE_URL).hostname} and (
        subscription := describe_codex_error(error)
    ):
        return subscription
    metadata = _provider_metadata(error)
    body = getattr(error, "body", None)
    message = body.get("message") if isinstance(body, dict) else None
    # Anthropic nests it: {"type": "error", "error": {"type": ..., "message": ...}}.
    if not message and isinstance(body, dict) and isinstance(body.get("error"), dict):
        message = body["error"].get("message")
    if (google := _google_error(error)) is not None:
        message = google.message
    status_code = _status_code(error)
    provider = _error_provider_name(error)

    if status_code == 429:
        if metadata.get("limit_source") == "upstream_provider_shared_pool":
            attempted = metadata.get("provider_name") or "every available provider"
            return (
                f"This model is temporarily rate-limited upstream ({attempted}). Every provider "
                "for it is busy right now — wait a moment and try again, pick another model in "
                "Settings, or add your own provider key on OpenRouter."
            )
        if provider == "OpenRouter":
            return (
                "Your OpenRouter account has hit its rate limit. Wait for it to reset, or add credit "
                "or your own provider key on OpenRouter."
            )
        return (
            f"{provider} account has hit its rate limit. Check its quota and billing, or wait and try again."
        )
    if status_code in {401, 403}:
        return (
            f"{provider} rejected the API key or its permissions. Check the connection in Settings → Models."
        )
    if isinstance(error, httpx.DecodingError):
        # The raw text is a zlib complaint about header bytes. True, and no use to anyone.
        return (
            "The provider sent a reply this app could not read — it arrived marked as compressed "
            "but was not. That is usually a blip; try again. If it keeps happening, a VPN or "
            "proxy between you and the provider is the usual cause."
        )
    detail = message or str(error) or "the provider did not return a response"
    # "Provider returned error" is what OpenRouter says when the upstream model refused; the
    # reason is in the metadata, and without it the run records a sentence with no cause in it.
    error_type = metadata.get("error_type")
    if error_type and "returned error" in detail.casefold():
        detail = f"{detail} ({str(error_type).replace('_', ' ')})"
    raw = metadata.get("raw")
    if raw and str(raw)[:200] not in detail:
        detail = f"{detail} — {str(raw)[:200]}"
    return f"The selected model could not complete this request: {detail}"


class DemoChatModel(BaseChatModel):
    """Offline stand-in so the app is usable before any provider is connected.

    It answers from whatever context the loop has already gathered and never requests a tool,
    which keeps a demo run finite and deterministic. `bind_tools` is accepted and ignored —
    `create_agent` always binds, and refusing would make the demo provider unusable rather than
    merely limited.
    """

    @property
    def _llm_type(self) -> str:
        return "wavefunction-demo"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs) -> Iterator[ChatGenerationChunk]:
        yield from self._chunks(self._reply(messages))

    async def _astream(
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._chunks(self._reply(messages)):
            # Paced deliberately. Without streaming the demo shows an empty timeline and then a
            # finished answer, which is the least representative version of the product for
            # someone trying it before connecting a model.
            await asyncio.sleep(DEMO_CHUNK_DELAY)
            yield chunk

    @staticmethod
    def _chunks(message: AIMessage) -> list[ChatGenerationChunk]:
        text = message.content if isinstance(message.content, str) else str(message.content)
        pieces = re.findall(r"\S+\s*", text) or [text]
        return [
            ChatGenerationChunk(
                message=AIMessageChunk(
                    content=piece,
                    # Usage belongs on the final chunk so it is counted once.
                    usage_metadata=message.usage_metadata if index == len(pieces) - 1 else None,
                )
            )
            for index, piece in enumerate(pieces)
        ]

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002 - the demo model never calls tools
        return self

    @staticmethod
    def _reply(messages: list[Any]) -> AIMessage:
        request = ""
        gathered: list[str] = []
        for message in messages:
            kind = getattr(message, "type", "")
            content = getattr(message, "content", "")
            if not isinstance(content, str):
                continue
            if kind == "human":
                request = content
            elif kind == "tool" and content.strip():
                gathered.append(content)

        parts = ["**Demo model** — connect a provider in Settings for real reasoning."]
        if request:
            parts.append(f"You asked: {request.strip()[:400]}")
        if gathered:
            sources = re.findall(r"^SOURCE: (.+)$", "\n\n".join(gathered), flags=re.MULTILINE)
            excerpt = re.sub(r"\s+", " ", gathered[0]).strip()[:500]
            parts.append(f"From the material available: {excerpt}")
            if sources:
                parts.append("Sources: " + ", ".join(dict.fromkeys(sources)))
        else:
            parts.append("No tool results were gathered, so there is nothing to cite.")
        answer = "\n\n".join(parts)
        return AIMessage(
            content=answer,
            usage_metadata={
                "input_tokens": max(1, len(request) // 4),
                "output_tokens": max(1, len(answer) // 4),
                "total_tokens": max(2, (len(request) + len(answer)) // 4),
            },
        )


class ModelGateway:
    def __init__(
        self,
        secrets: SecretBackend,
        codex: CodexAuth | None = None,
        ollama_config: Callable[[], dict] | None = None,
        capabilities: Callable[[], dict] | None = None,
        context_lengths: Callable[[str, str], int | None] | None = None,
        zero_data_retention: Callable[[], bool] | None = None,
    ):
        self.secrets = secrets
        self.ollama_config = ollama_config or (lambda: {})
        self.capabilities = capabilities or (lambda: {})
        # "Require zero data retention" in Settings. OpenRouter and the Vercel AI Gateway then
        # route only to endpoints that keep no prompts; other providers set it on the account.
        self.zero_data_retention = zero_data_retention or (lambda: False)
        # What the models.dev registry says a model accepts, for providers that do not say.
        self.context_lengths = context_lengths or (lambda _provider, _model: None)
        # The ChatGPT sign-in, when the app wired one. Absent in tests and scripts, where a
        # `codex` model is simply unavailable rather than an import-time requirement.
        self.codex = codex

    def search_provider(self, model: ModelSpec) -> str:
        """How web search can be done for this model: 'built-in', 'tavily' or 'none'.

        OpenRouter runs the search itself — natively for OpenAI, Anthropic, Google, Perplexity
        and xAI models, through Exa for the rest — so a model on OpenRouter needs no extra key.
        So do Anthropic, Gemini and the Vercel AI Gateway, on the key the chat already runs on
        (`provider_search.py`). Anything else falls back to Tavily, which needs its own key.
        """
        if (key := SEARCH_KEYS.get(model.provider)) and self.secrets.get(key):
            return "built-in"
        # A ChatGPT sign-in already includes search: the endpoint carries OpenAI's own
        # `web_search` tool and every model offered here declares `supports_search_tool`, so no
        # Tavily key is needed for something the user already pays for.
        if model.provider == "codex" and self.codex is not None and self.codex.connected():
            return "built-in"
        if self.secrets.get("tavily_api_key"):
            return "tavily"
        return "none"

    async def web_search(
        self, model: ModelSpec, query: str, max_results: int, *, zero_data_retention: bool = False
    ) -> str:
        """Search the web, and return what the sources said along with where it came from.

        A ChatGPT sign-in, Anthropic, Gemini and the Vercel AI Gateway each search with their own
        tool (`codex.search_web`, `provider_search.py`). On OpenRouter it is a plain chat request
        with the web plugin attached: OpenRouter runs the search, puts the
        results in front of the model, and returns what it cited as `url_citation` annotations.
        Those annotations are the useful part, so the answer text is only a fallback for a
        provider whose native search does not annotate.
        """
        if model.provider == "codex":
            if self.codex is None or not self.codex.connected():
                raise RuntimeError("Sign in with ChatGPT in Settings › Models before searching")
            text, usage = await search_web(self.codex, model.model, query, max_results)
            report_usage(usage)
            return text
        if model.provider in {"anthropic", "gemini", "vercel"}:
            from .provider_search import anthropic_search, gateway_search, gemini_search

            key = self.secrets.get(SEARCH_KEYS[model.provider])
            if not key:
                raise RuntimeError("Connect this model's API key in Settings › Models before searching")
            if model.provider == "anthropic":
                text, usage = await anthropic_search(key, model.model, query, max_results)
            elif model.provider == "gemini":
                text, usage = await gemini_search(key, model.model, query, max_results)
            else:
                text, usage = await gateway_search(
                    key, model.model, query, max_results, zero_data_retention=zero_data_retention
                )
            report_usage(usage)
            return text
        api_key = self.secrets.get("openrouter_api_key")
        if not api_key:
            raise RuntimeError("Connect an OpenRouter API key before using web search")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://www.wavefunction.si",
                    "X-OpenRouter-Title": "Wavefunction",
                },
                json={
                    "model": model.model,
                    "plugins": [{"id": "web", "max_results": max_results}],
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Search the web for this and summarise what the sources say, "
                                f"citing them:\n\n{query}"
                            ),
                        }
                    ],
                    "max_tokens": 900,
                    # The query comes from the conversation, so it follows the chat's policy.
                    **({"provider": {"zdr": True}} if zero_data_retention else {}),
                },
            )
            response.raise_for_status()
            data = response.json()
        # A whole model call, and the search the plugin adds to its price, on the run's account.
        report_usage(data.get("usage"))

        message = (data.get("choices") or [{}])[0].get("message") or {}
        sources = [
            citation
            for annotation in message.get("annotations") or []
            if (citation := annotation.get("url_citation"))
        ]
        # Both halves, because the two engines put the information in different places. Exa
        # returns the page text on each citation; native provider search returns only which
        # sources it used and puts what they said in the answer. Returning the citations alone
        # gave the agent 31 opaque redirect URLs and not one readable sentence.
        blocks = []
        if answer := (message.get("content") or "").strip():
            blocks.append(answer)
        for item in sources[:max_results]:
            lines = [f"SOURCE: {item.get('url', 'unknown')}"]
            if title := (item.get("title") or "").strip():
                lines.append(title)
            if content := (item.get("content") or "").strip():
                lines.append(content)
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks) if blocks else f"No web results for {query!r}."

    def chat_model(self, model: ModelSpec, **kwargs) -> BaseChatModel:
        self.validate_reasoning(model)
        built = self._chat_model(model, **kwargs)
        profile = dict(getattr(built, "profile", None) or {})
        known = self.capabilities().get(model.provider, {}).get(model.model, {})
        # The provider's own figure first, then the registry fetched at startup, then whatever
        # LangChain bundled: the same data, but frozen at the library's release.
        if (capacity := known.get("context_length")) or (
            capacity := self.context_lengths(model.provider, model.model)
        ):
            profile["max_input_tokens"] = capacity
        if "input_modalities" in known and model.provider != "vercel":
            profile["image_inputs"] = "image" in known["input_modalities"]
        if "supported_parameters" in known:
            params = known["supported_parameters"]
            profile["tool_calling"] = "tools" in params
        built.profile = profile or None  # type: ignore[assignment]
        built.metadata = {**(built.metadata or {}), "provider": model.provider}
        return built

    def reasoning_options(self, model: ModelSpec) -> list[str] | None:
        return reasoning_efforts(model.provider, model.model, self._reasoning_metadata(model))

    def _reasoning_metadata(self, model: ModelSpec) -> dict:
        if model.provider == "ollama":
            config = self.ollama_config()
            if model.base_url and model.base_url != config.get("base_url"):
                return {}
            return next((item for item in config.get("models", []) if item["id"] == model.model), {})
        return self.capabilities().get(model.provider, {}).get(model.model, {})

    def validate_reasoning(self, model: ModelSpec) -> None:
        validate_reasoning(
            model.provider, model.model, model.reasoning_effort, self._reasoning_metadata(model)
        )

    def _chat_model(
        self, model: ModelSpec, *, max_tokens: int | None = None, max_retries: int = 2
    ) -> BaseChatModel:
        """Build a chat model for `model`."""
        if model.provider == "demo":
            return DemoChatModel()
        if model.provider == "codex":
            return self._codex_model(model, max_retries=max_retries)
        if model.provider == "anthropic":
            return self._anthropic_model(model, max_tokens=max_tokens, max_retries=max_retries)
        if model.provider == "gemini":
            return self._gemini_model(model, max_tokens=max_tokens, max_retries=max_retries)
        if model.provider == "ollama":
            from langchain_ollama import ChatOllama

            config = self.ollama_config()
            catalogue = (
                config.get("models", [])
                if not model.base_url or model.base_url == config.get("base_url")
                else []
            )
            known = next((item for item in catalogue if item["id"] == model.model), {})
            context = ollama_context_window(config, model.model, model.base_url)
            return ChatOllama(
                model=model.model,
                base_url=model.base_url or config.get("base_url") or DEFAULT_OLLAMA_URL,
                temperature=model.temperature,
                num_predict=max_tokens if max_tokens is not None else model.max_tokens,
                num_ctx=context,
                reasoning=reasoning_kwargs("ollama", model.reasoning_effort).get(
                    "reasoning", True if "thinking" in known.get("capabilities", []) else None
                ),
                client_kwargs={"timeout": 300, "trust_env": False},
                profile={"max_input_tokens": context},
            )
        provider_keys = {"openrouter": "openrouter_api_key", "vercel": "vercel_api_key"}
        api_key = self.secrets.get(provider_keys.get(model.provider, "openai_api_key"))
        if not api_key and model.provider != "openai-compatible":
            provider_name = {"openrouter": "OpenRouter", "vercel": "Vercel AI Gateway"}.get(
                model.provider, "OpenAI"
            )
            raise RuntimeError(f"Connect a {provider_name} API key before using this model")
        kwargs: dict[str, Any] = {
            "model": model.model,
            "api_key": api_key or "not-needed",
            "temperature": model.temperature,
            "timeout": 60,
            "max_retries": max_retries,
            # LangChain asks a stream for its usage only when it talks to OpenAI itself, so a
            # run on a gateway or a compatible server recorded no tokens.
            "stream_usage": True,
        }
        kwargs.update(reasoning_kwargs(model.provider, model.reasoning_effort))
        if model.reasoning_effort and model.reasoning_effort != "none":
            # Some reasoning endpoints reject temperature when thinking is enabled.
            kwargs.pop("temperature", None)
        output_tokens = max_tokens if max_tokens is not None else model.max_tokens
        if model.provider == "openai-compatible" and model.base_url:
            kwargs["base_url"] = model.base_url
        if model.provider == "vercel":
            # The gateway uses the OpenAI protocol and its documented reasoning extension.
            kwargs["base_url"] = VERCEL_GATEWAY_BASE_URL
        if model.provider == "openrouter":
            kwargs.update(
                {
                    "base_url": OPENROUTER_BASE_URL,
                    "default_headers": {
                        "HTTP-Referer": "https://www.wavefunction.si",
                        "X-OpenRouter-Title": "Wavefunction",
                        "X-OpenRouter-Metadata": "enabled",
                    },
                }
            )
            if output_tokens is not None:
                kwargs["max_tokens"] = output_tokens
        elif output_tokens is not None:
            kwargs["max_tokens"] = output_tokens
        if model.provider == "openai":
            from .provider_chat import ChatOpenAIDirect

            if reasons(model):
                # Chat Completions drops a reasoning model's reasoning between its tool calls.
                # The Responses API carries it, encrypted and stored nowhere, as it already does
                # for the ChatGPT sign-in.
                http_client, http_async_client = codex_http_clients()
                kwargs.update(
                    use_responses_api=True,
                    include=["reasoning.encrypted_content"],
                    # The Responses API ends a failed stream with `response.failed`, which
                    # the repairing clients raise as an error (see `repair_sse_line`).
                    http_client=http_client,
                    http_async_client=http_async_client,
                    store=False,
                    output_version="responses/v1",
                )
            return ChatOpenAIDirect(**kwargs)
        if model.provider in {"openrouter", "vercel"}:
            from .gateway_chat import ChatGateway

            # Read only the generation identity from response headers. The gateway adapter
            # removes all headers before a message or checkpoint can retain them.
            if model.provider == "openrouter":
                kwargs["include_response_headers"] = True
            # Read when the chat's model is built, so a running task keeps the policy it began with.
            return ChatGateway(**kwargs, zero_data_retention=self.zero_data_retention())
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(**kwargs)

    def _anthropic_model(
        self, model: ModelSpec, *, max_tokens: int | None, max_retries: int
    ) -> BaseChatModel:
        """Claude on Anthropic's Messages API, on the user's key.

        - The conversation prefix is cached: top-level `cache_control` puts a breakpoint on the
          last block, so each request reads what the one before it wrote. Anthropic caches nothing
          unless asked.
        - `temperature` is never sent; the API refuses it on every model.
        - Thinking is adaptive where the model has it, and its blocks go back unchanged. The API
          checks that a replayed block's earlier history is the one it was written in. This
          harness changes that history between requests (a tool loaded mid-run, a reminder sent
          once, compaction that keeps recent turns), which on newer accounts is a 400;
          `drop_block` has the API drop those blocks for that request instead.
        - A reply needs a ceiling: the model's own output limit, streamed, unless one was set.
        """
        api_key = self.secrets.get("anthropic_api_key")
        if not api_key:
            raise RuntimeError("Connect an Anthropic API key before using this model")
        from langchain_anthropic import ChatAnthropic

        known = self.capabilities().get("anthropic", {}).get(model.model, {})
        betas, extra = [], {}
        # Unknown models (the catalogue not read yet) are taken to be current ones, which all
        # think adaptively.
        if known.get("adaptive_thinking", True):
            extra["thinking"] = {
                "type": "adaptive",
                "block_binding": {"prefix_mismatch_behavior": "drop_block"},
            }
            betas.append("thinking-binding-controls-2026-08-01")
        output_tokens = max_tokens if max_tokens is not None else model.max_tokens
        return ChatAnthropic(
            model=model.model,  # type: ignore[call-arg]
            api_key=api_key,
            max_tokens=output_tokens or known.get("max_output_tokens") or 64000,  # type: ignore[call-arg]
            streaming=True,
            stream_usage=True,
            betas=betas or None,
            model_kwargs={"cache_control": {"type": "ephemeral"}},
            default_request_timeout=300,  # type: ignore[call-arg]
            max_retries=max_retries,
            **extra,
            **reasoning_kwargs("anthropic", model.reasoning_effort),
        )

    def _gemini_model(self, model: ModelSpec, *, max_tokens: int | None, max_retries: int) -> BaseChatModel:
        """Gemini on Google's native `generateContent` API, on the user's key.

        - Thought signatures go back with the tool calls they came with: LangChain's Gemini
          model keeps them in `additional_kwargs`, which checkpoints keep.
        - Caching is implicit: Google caches a repeated prefix on its own, and reports the
          cached part as `cachedContentTokenCount`.
        - No temperature: Google advises against setting one for its thinking models, which
          loop or reason worse with a low one, and LangChain would otherwise send 0.7 to any
          model it does not recognise as Gemini 3 or later.
        """
        api_key = self.secrets.get("gemini_api_key")
        if not api_key:
            raise RuntimeError("Connect a Gemini API key before using this model")
        from langchain_google_genai import ChatGoogleGenerativeAI

        known = self.capabilities().get("gemini", {}).get(model.model, {})
        output_tokens = max_tokens if max_tokens is not None else model.max_tokens
        return ChatGoogleGenerativeAI(
            model=model.model,
            api_key=api_key,
            temperature=None,
            max_output_tokens=output_tokens or known.get("max_output_tokens"),
            streaming=True,
            timeout=300,
            max_retries=max_retries,
            **reasoning_kwargs("gemini", model.reasoning_effort),
        )

    def _codex_model(self, model: ModelSpec, *, max_retries: int = 2) -> BaseChatModel:
        """A model that runs on the user's ChatGPT subscription.

        The transport refreshes authentication per request without blocking the event loop.
        This also keeps a model usable when a long-running conversation outlives its token.

        `temperature` is deliberately not sent. The endpoint serves reasoning models that reject
        it, and LangChain only strips it for ids it recognises as `gpt-5*` — a `codex` id it does
        not recognise would carry the parameter through to a 400.
        """
        if self.codex is None or not self.codex.connected():
            raise RuntimeError("Sign in with ChatGPT in Settings › Models before using this model")
        from .provider_chat import ChatCodex

        credentials = self.codex.credentials()
        assert credentials is not None
        # Authentication is refreshed at request time, on a worker for async calls. Model
        # construction remains synchronous and long-lived models also receive fresh tokens.
        http_client, http_async_client = codex_http_clients(self.codex)
        return ChatCodex(
            model=model.model,
            api_key="managed-by-codex-transport",  # type: ignore[arg-type]
            base_url=CODEX_BASE_URL,
            default_headers=codex_headers(credentials),
            use_responses_api=True,
            # The endpoint refuses a non-streaming request outright ("Stream must be set to
            # true"), so every call streams and LangChain assembles the reply. Without this the
            # eval judge and every non-streaming path answer 400.
            streaming=True,
            # Both clients repair the one-shot tool-argument event; see `codex_http_clients`.
            http_client=http_client,
            http_async_client=http_async_client,
            # Nothing is stored server-side, so reasoning has to travel with the conversation or
            # the model forgets what it just thought between tool calls.
            include=["reasoning.encrypted_content"],
            store=False,
            timeout=120,
            max_retries=max_retries,
            output_version="responses/v1",
            **reasoning_kwargs("codex", model.reasoning_effort),
        )
