"""Image models: turned on in Settings, captured by a task, and saved as files the chat shows.

The providers are faked at the network, in the shapes their APIs document, so a request or a
reply that drifted from those shapes fails here rather than on the user's key.
"""

from __future__ import annotations

import base64
import json
from io import BytesIO
from typing import Any

import httpx
import pytest
from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage
from PIL import Image
from support import create, repository

from wavefunction import image_models
from wavefunction.secrets import MemorySecretStore


def png(colour: str, size=(16, 9)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def fake_providers(monkeypatch, replies: dict[str, bytes]):
    """Each provider's documented image API, answering with a solid colour; requests are kept.

    A prompt that asks to be refused gets the provider's error instead.
    """
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        url = str(request.url)
        if url.startswith("https://generativelanguage.googleapis.com/v1beta/models?"):
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "models/gemini-3-flash", "displayName": "Gemini 3 Flash"},
                        {"name": "models/gemini-nano-banana-2.1", "displayName": "Nano Banana 2.1"},
                        {"name": "models/gemini-3-pro-image", "displayName": "Nano Banana Pro"},
                    ]
                },
            )
        if b"Refuse" in request.content:
            return httpx.Response(400, json={"error": {"message": "Rejected by the safety system."}})
        image = base64.b64encode(replies.get(request.url.host, b"")).decode()
        if url.endswith(":generateContent"):
            # Thinking can draw an interim image first; only the final one is the answer.
            thought = base64.b64encode(png("gray")).decode()
            parts = [
                {"inlineData": {"mimeType": "image/png", "data": thought}, "thought": True},
                {"text": "Here it is."},
                {"inlineData": {"mimeType": "image/png", "data": image}},
            ]
            return httpx.Response(200, json={"candidates": [{"content": {"parts": parts}}]})
        if "/images" in url:
            return httpx.Response(200, json={"data": [{"b64_json": image, "media_type": "image/png"}]})
        return httpx.Response(404)

    class Faked(httpx.AsyncClient):  # A class, as libraries that subclass it import it later.
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **{**kwargs, "transport": httpx.MockTransport(handle)})

    monkeypatch.setattr(httpx, "AsyncClient", Faked)
    return sent


def results(services, run_id: str) -> list[dict]:
    return [
        json.loads(event["payload"]["result"])
        for event in services.repository.list_run_events(run_id)
        if event["event_type"] == "tool_completed" and event["payload"]["tool"].endswith("_image")
    ]


@pytest.mark.sandbox  # The chat's AGENTS.md is read by the sandboxed file helper.
async def test_images_made_and_edited_in_a_folder_are_its_files(coding, tmp_path, monkeypatch):
    client, ws, services = coding
    services.secrets.set("gemini_api_key", "synthetic-gemini")
    services.secrets.set("openrouter_api_key", "synthetic-openrouter")
    red, blue = png("red"), png("blue", (9, 16))
    fake_providers(monkeypatch, {"generativelanguage.googleapis.com": red, "openrouter.ai": blue})

    # Settings lists only a provider's image models, and keeps only real keys when saving.
    listed = (await client.get("/api/connections/image-models/gemini")).json()["models"]
    assert [model["id"] for model in listed] == ["gemini-nano-banana-2.1", "gemini-3-pro-image"]
    chosen = ["gemini::gemini-nano-banana-2.1", "openrouter::openai/gpt-image-2", "unknown::model"]
    saved = await client.put("/api/connections/image-models", json={"models": chosen})
    assert saved.json()["models"] == chosen[:2]
    assert (await client.get("/api/connections")).json()["enabled_image_models"] == chosen[:2]

    folder = repository(tmp_path)
    thread = await create(client, folder=str(folder))
    script_gateway(
        services.models,
        [
            tool_call_message(
                "generate_image", {"prompt": "A red fox", "count": 2, "aspect_ratio": "16:9"}, "make"
            ),
            tool_call_message(
                "edit_image",
                {
                    "prompt": "Make it night",
                    "images": ["generated-images/a-red-fox-1.png"],
                    "model": "openai/gpt-image-2",
                    "path": "art/night.png",
                },
                "edit",
            ),
            AIMessage(content="Two foxes and a night version."),
        ],
    )
    run = await ws.send(thread["id"], "Draw a fox, then a night version")
    # The task captured its image models: turning them off now changes nothing for it.
    await client.put("/api/connections/image-models", json={"models": []})
    assert (await services.runtime.wait(run["id"]))["status"] == "completed"

    made, edited = results(services, run["id"])
    assert made["images"] == [
        {"path": "generated-images/a-red-fox-1.png"},
        {"path": "generated-images/a-red-fox-2.png"},
    ]
    assert edited == {
        "model": "openrouter::openai/gpt-image-2",
        "images": [{"path": "art/night.png"}],
        "note": "The user sees these images in the chat.",
    }
    for path in ("generated-images/a-red-fox-1.png", "generated-images/a-red-fox-2.png"):
        assert (folder / path).read_bytes() == red
    assert (folder / "art/night.png").read_bytes() == blue
    shown = await client.get(
        f"/api/coding/threads/{thread['id']}/preview/content", params={"path": "art/night.png"}
    )
    assert shown.content == blue

    # Without an image model turned on, a new task is not offered the tools at all.
    script_gateway(
        services.models,
        [tool_call_message("generate_image", {"prompt": "A cat"}, "none"), AIMessage(content="ok")],
    )
    later = await ws.send(thread["id"], "Draw a cat")
    await services.runtime.wait(later["id"])
    failed = [e for e in services.repository.list_run_events(later["id"]) if e["event_type"] == "tool_failed"]
    assert "generate_image" in failed[0]["payload"]["result"]
    assert not (folder / "generated-images/a-cat.png").exists()


async def test_a_chat_without_a_folder_keeps_its_images_as_attachments(coding, monkeypatch):
    client, ws, services = coding
    services.secrets.set("openai_api_key", "synthetic-openai")
    white = png("white", (12, 12))
    sent = fake_providers(monkeypatch, {"api.openai.com": white})
    await client.put("/api/connections/image-models", json={"models": ["openai::gpt-image-2.6-flare"]})

    thread = await create(client)
    base = f"/api/coding/threads/{thread['id']}"
    photo = png("black", (20, 10))
    await client.post(base + "/attachments", files={"file": ("photo.png", photo, "image/png")})
    script_gateway(
        services.models,
        [
            tool_call_message("generate_image", {"prompt": "A cat"}, "make"),
            tool_call_message(
                "edit_image",
                {"prompt": "Put the cat in the photo", "images": ["a-cat.png", "photo.png"], "count": 2},
                "edit",
            ),
            AIMessage(content="Done."),
        ],
    )
    run = await ws.send(thread["id"], "Make a cat and put it in my photo")
    assert (await services.runtime.wait(run["id"]))["status"] == "completed"

    made, edited = results(services, run["id"])
    [cat] = made["images"]
    assert cat["attachment"] == "a-cat.png"
    assert [image["attachment"] for image in edited["images"]] == [
        "put-the-cat-in-the-photo-1.png",
        "put-the-cat-in-the-photo-2.png",
    ]
    # The edit read both by their attachment names: the generated cat and the uploaded photo.
    edit = next(r for r in sent if r.url.path == "/v1/images/edits")
    assert white in edit.content and photo in edit.content

    # The chat shows each image small and opens it whole, as its own attachments.
    full = await client.get(f"{base}/attachments/{edited['images'][1]['id']}/content")
    assert full.content == white
    small = await client.get(f"{base}/attachments/{cat['id']}")
    assert small.headers["content-type"] == "image/png"
    stored = services.files.project_dir(ws.agent_id) / "sources"
    assert len(list(stored.iterdir())) == 4
    # They belong to the chat's history and go with it.
    assert (await client.delete(base)).status_code == 204
    assert not list(stored.iterdir())


async def test_each_provider_gets_the_image_request_its_api_documents(monkeypatch):
    colours = {
        "generativelanguage.googleapis.com": "red",
        "openrouter.ai": "blue",
        "ai-gateway.vercel.sh": "green",
        "api.openai.com": "white",
    }
    sent = fake_providers(monkeypatch, {host: png(colour) for host, colour in colours.items()})
    secrets = MemorySecretStore()
    for name in image_models.PROVIDERS:
        secrets.set(f"{name}_api_key", f"synthetic-{name}")
    photo = image_models.picture(png("black", (20, 10)), "photo")
    as_url = "data:image/png;base64," + base64.b64encode(photo.data).decode()

    async def make(key: str, ratio: str, inputs: list) -> tuple[Any, bytes]:
        sent.clear()
        [made] = await image_models.create(
            secrets, key, "A cat", aspect_ratio=ratio, inputs=inputs, zero_data_retention=True
        )
        [request] = sent
        body = (
            json.loads(request.content)
            if request.headers["content-type"] == "application/json"
            else request.content
        )
        return body, made.data

    # Gemini: one generateContent call, the inputs as inline data; the final image, not a thought.
    body, made = await make("gemini::gemini-nano-banana-2.1", "16:9", [photo])
    assert body["contents"][0]["parts"] == [
        {"text": "A cat"},
        {"inline_data": {"mime_type": "image/png", "data": as_url.split(",")[1]}},
    ]
    assert body["generationConfig"] == {
        "responseModalities": ["TEXT", "IMAGE"],
        "imageConfig": {"aspectRatio": "16:9"},
    }
    assert made == png("red")

    # OpenRouter's Image API: the ratio as given, inputs as image references, only ZDR endpoints.
    body, made = await make("openrouter::openai/gpt-image-2", "9:16", [photo])
    assert body == {
        "model": "openai/gpt-image-2",
        "prompt": "A cat",
        "n": 1,
        "aspect_ratio": "9:16",
        "input_references": [{"type": "image_url", "image_url": {"url": as_url}}],
        "provider": {"zdr": True},
    }
    assert made == png("blue")

    # Vercel: no size for a model that is not OpenAI's, and only providers that keep nothing.
    body, _ = await make("vercel::bfl/flux-2-pro", "4:3", [])
    assert body == {
        "model": "bfl/flux-2-pro",
        "prompt": "A cat",
        "n": 1,
        "response_format": "b64_json",
        "providerOptions": {"gateway": {"zeroDataRetention": True}},
    }
    body, _ = await make("vercel::openai/gpt-image-2", "4:3", [photo])
    assert body["size"] == "1536x1024" and body["images"] == [{"image_url": as_url}]

    # OpenAI: the nearest of its sizes, and an edit's inputs as `image[]` files.
    body, made = await make("openai::gpt-image-2.6-flare", "2:3", [])
    assert body == {"model": "gpt-image-2.6-flare", "prompt": "A cat", "n": 1, "size": "1024x1536"}
    body, _ = await make("openai::gpt-image-2.6-flare", "", [photo, photo])
    assert body.count(b'name="image[]"') == 2 and photo.data in body
    assert b'name="size"\r\n\r\nauto' in body
    assert made == png("white")

    # A refusal says what the provider said, as an error the agent may safely try again after.
    with pytest.raises(
        ValueError, match="OpenAI refused the image request \\(400\\): Rejected by the safety"
    ):
        await image_models.create(secrets, "openai::gpt-image-2.6-flare", "Refuse this")
