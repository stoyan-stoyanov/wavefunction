"""Web search on a provider's own search, for Anthropic, Gemini and the Vercel AI Gateway keys.

Each runs one request that searches and answers, the way the ChatGPT sign-in and OpenRouter
searches in `models.py` already do, so a chat on these models needs no Tavily key:

- Anthropic: the Messages API's server-side `web_search` tool. Its citations carry each source's
  URL, title and the sentence the answer drew on.
- Gemini: Google Search grounding on `generateContent`. Its sources are Google's redirect links,
  titled with the site's domain; fetching one lands on the page.
- Vercel AI Gateway: its Perplexity search server tool, which runs for any model on the gateway.
  The gateway returns only the answer, so the answer is asked to cite each source's URL.

Each returns the answer followed by `SOURCE:` blocks, the shape the other searches return, and
the usage the request reported, which `ModelGateway` adds to the task's own.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from .models import VERCEL_GATEWAY_BASE_URL, merge_usage, usage_from

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def instructions() -> str:
    # Dated: without it a model took a 2024 release page for the latest and said "as of May 2024".
    return (
        f"Today is {date.today().isoformat()}. Search the web and summarise what the sources say. "
        "Be specific and quote figures and dates as the sources give them. Prefer the newest "
        "sources, and cite each one with its full URL."
    )


# Searches one request may run. A single query rarely needs more, and each one is billed.
MAX_SEARCHES = 3
# Anthropic may pause a long search turn; it resumes by sending the paused turn back.
MAX_CONTINUATIONS = 2


def search_results(answer: str, sources: dict[str, str], query: str, max_results: int) -> str:
    blocks = [answer.strip()] if answer.strip() else []
    for url, detail in list(sources.items())[:max_results]:
        blocks.append(f"SOURCE: {url}" + (f"\n{detail}" if detail else ""))
    return "\n\n".join(blocks) if blocks else f"No web results for {query!r}."


def _failure(provider: str, response: httpx.Response) -> RuntimeError:
    try:
        error = response.json().get("error") or {}
        detail = error.get("message") if isinstance(error, dict) else str(error)
    except ValueError:
        detail = response.text[:300]
    return RuntimeError(f"{provider} web search failed ({response.status_code}): {detail}")


async def anthropic_search(api_key: str, model: str, query: str, max_results: int) -> tuple[str, dict]:
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": 4096,
        "system": instructions(),
        "messages": [{"role": "user", "content": query}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    blocks: list[dict] = []
    usage: dict = {}
    async with httpx.AsyncClient(timeout=180) as client:
        for _ in range(MAX_CONTINUATIONS + 1):
            response = await client.post(ANTHROPIC_MESSAGES_URL, headers=headers, json=request)
            if response.status_code != 200:
                raise _failure("Anthropic", response)
            data = response.json()
            usage = merge_usage(usage, usage_from(data.get("usage")))
            blocks += data.get("content") or []
            if data.get("stop_reason") != "pause_turn":
                break
            request["messages"] = [*request["messages"][:1], {"role": "assistant", "content": blocks}]

    answer, cited, found, failures = [], {}, {}, []
    for block in blocks:
        if block.get("type") == "text":
            answer.append(block.get("text") or "")
            for citation in block.get("citations") or []:
                if url := citation.get("url"):
                    quote = (citation.get("cited_text") or "").strip()
                    cited[url] = "\n".join(filter(None, [cited.get(url) or citation.get("title"), quote]))
        elif block.get("type") == "web_search_tool_result":
            results = block.get("content")
            if isinstance(results, dict):
                failures.append(str(results.get("error_code") or "error"))
            for result in results if isinstance(results, list) else []:
                if url := result.get("url"):
                    found.setdefault(url, result.get("title") or "")
    if failures and not found:
        raise RuntimeError(f"Anthropic web search failed: {', '.join(failures)}")
    # The sources the answer cites first, then whatever else the search found.
    sources = {**cited, **{url: title for url, title in found.items() if url not in cited}}
    return search_results("".join(answer), sources, query, max_results), usage


async def gemini_search(api_key: str, model: str, query: str, max_results: int) -> tuple[str, dict]:
    name = model if model.startswith("models/") else f"models/{model}"
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            f"{GEMINI_BASE_URL}/{name}:generateContent",
            headers={"x-goog-api-key": api_key},
            json={
                "systemInstruction": {"parts": [{"text": instructions()}]},
                "contents": [{"role": "user", "parts": [{"text": query}]}],
                "tools": [{"google_search": {}}],
            },
        )
    if response.status_code != 200:
        raise _failure("Gemini", response)
    data = response.json()
    candidate = (data.get("candidates") or [{}])[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    answer = "".join(part.get("text") or "" for part in parts if not part.get("thought"))
    sources: dict[str, str] = {}
    for chunk in (candidate.get("groundingMetadata") or {}).get("groundingChunks") or []:
        if url := (chunk.get("web") or {}).get("uri"):
            sources.setdefault(url, chunk["web"].get("title") or "")
    counts = data.get("usageMetadata") or {}
    usage = usage_from(
        {
            "input_tokens": counts.get("promptTokenCount"),
            "output_tokens": (counts.get("candidatesTokenCount") or 0)
            + (counts.get("thoughtsTokenCount") or 0),
            "total_tokens": counts.get("totalTokenCount"),
        }
    )
    return search_results(answer, sources, query, max_results), usage


async def gateway_search(
    api_key: str, model: str, query: str, max_results: int, *, zero_data_retention: bool = False
) -> tuple[str, dict]:
    request: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": instructions()}, {"role": "user", "content": query}],
        # The query is the agent's own, so it is searched as written rather than reworded.
        "tools": [
            {"type": "vercel:perplexity_search", "config": {"query": query, "max_results": max_results}}
        ],
        "tool_choice": "required",
        # No output cap: a reasoning model spent a 900-token one thinking and cut its answer off.
    }
    if zero_data_retention:
        # The query comes from the conversation, so it follows the chat's policy.
        request["providerOptions"] = {"gateway": {"zeroDataRetention": True}}
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            f"{VERCEL_GATEWAY_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=request,
        )
    if response.status_code != 200:
        raise _failure("Vercel AI Gateway", response)
    data = response.json()
    message = (data.get("choices") or [{}])[0].get("message") or {}
    sources: dict[str, str] = {}
    for annotation in message.get("annotations") or []:
        if (citation := annotation.get("url_citation")) and citation.get("url"):
            sources.setdefault(citation["url"], citation.get("title") or "")
    return search_results(message.get("content") or "", sources, query, max_results), usage_from(
        data.get("usage")
    )
