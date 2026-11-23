"""Image models: the pictures an agent generates or edits, on the keys the user connected.

Four providers, each on its own documented API rather than a chat model's: Gemini's
`generateContent` with image output (the "Nano Banana" models), OpenAI's Images API, the same
API on Vercel's AI Gateway for its image models, and OpenRouter's Image API. One request makes
one image: some models take only one per request, and several requests run side by side.

Which models are turned on is a setting of their own (`enabled_image_models`, keys of the form
`provider::model`, the first the default). A task captures that list when it starts, as it
captures its chat model, so a running task keeps the image models it began with.
"""

from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass
from typing import Any

import httpx

from .openrouter import OPENROUTER_BASE_URL
from .secrets import SecretBackend
from .vercel_gateway import VERCEL_GATEWAY_BASE_URL

SETTING = "enabled_image_models"
PROVIDERS = {
    "gemini": ("Gemini", "gemini_api_key"),
    "openai": ("OpenAI", "openai_api_key"),
    "openrouter": ("OpenRouter", "openrouter_api_key"),
    "vercel": ("the Vercel AI Gateway", "vercel_api_key"),
}
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
OPENAI_BASE_URL = "https://api.openai.com/v1"
ASPECT_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
MAX_IMAGES = 4
# Gemini refuses a request over 20 MB, and the inputs travel inside it.
MAX_INPUT_BYTES = 15 * 1024 * 1024
# A high-quality image takes a minute or two.
TIMEOUT = httpx.Timeout(300, connect=30)
FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
RASTER_FORMATS = {"png", "jpeg", "jpg", "webp"}
EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


class MissingKey(ValueError):
    """The provider's key is not saved, so nothing was asked of it."""


@dataclass(frozen=True)
class Picture:
    data: bytes
    media_type: str


def enabled(repository: Any) -> list[str]:
    """The image models turned on in Settings, the default first."""
    stored = repository.get_setting(SETTING, [])
    return [key for key in stored if valid_key(key)] if isinstance(stored, list) else []


def valid_key(key: Any) -> bool:
    provider, _, model = str(key).partition("::")
    return provider in PROVIDERS and bool(model.strip())


def picture(data: bytes, what: str) -> Picture:
    """Bytes checked to be an image, as PNG, JPEG or WebP: other formats are converted to PNG."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format in FORMATS:
                image.verify()
                return Picture(data, FORMATS[image.format])
            buffer = io.BytesIO()
            image.convert("RGBA").save(buffer, format="PNG")
            return Picture(buffer.getvalue(), "image/png")
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ValueError(f"{what} is not an image.") from error


def _key(secrets: SecretBackend, provider: str) -> str:
    name, secret = PROVIDERS[provider]
    key = secrets.get(secret)
    if not key:
        raise MissingKey(f"Connect {name} in Settings → Models to use its image models.")
    return key


def _headers(provider: str, key: str) -> dict[str, str]:
    if provider == "gemini":
        return {"x-goog-api-key": key}
    headers = {"Authorization": f"Bearer {key}"}
    if provider == "openrouter":
        headers.update({"HTTP-Referer": "https://www.wavefunction.si", "X-OpenRouter-Title": "Wavefunction"})
    return headers


def _refusal(provider: str, response: httpx.Response) -> ValueError:
    """The provider's own reason, which says what to change: a prompt, a model, billing.

    A ValueError: the request was refused and nothing was saved, so trying again is safe.
    """
    try:
        error = response.json().get("error")
        reason = error.get("message") if isinstance(error, dict) else error
    except ValueError:
        reason = None
    reason = str(reason or response.text[:300] or response.reason_phrase).strip()
    return ValueError(
        f"{PROVIDERS[provider][0]} refused the image request ({response.status_code}): {reason}"
    )


async def list_models(provider: str, secrets: SecretBackend) -> list[dict[str, str]]:
    """The image models a provider offers this key, as `{id, name}`, sorted by name."""
    key = _key(secrets, provider)
    async with httpx.AsyncClient(timeout=30, headers=_headers(provider, key)) as client:
        if provider == "gemini":
            response = await client.get(f"{GEMINI_BASE_URL}/models", params={"pageSize": 1000})
        elif provider == "openrouter":
            response = await client.get(f"{OPENROUTER_BASE_URL}/images/models")
        else:
            base = OPENAI_BASE_URL if provider == "openai" else VERCEL_GATEWAY_BASE_URL
            response = await client.get(f"{base}/models")
        response.raise_for_status()
    return parse_models(provider, response.json())


def parse_models(provider: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    found: dict[str, str] = {}
    for raw in payload.get("models" if provider == "gemini" else "data") or []:
        model = str(raw.get("name" if provider == "gemini" else "id") or "").removeprefix("models/")
        if provider == "gemini":
            # Nano Banana: `gemini-…-image` and `gemini-nano-banana-…`. Imagen is shut down.
            keep = model.startswith("gemini-") and ("image" in model or "nano-banana" in model)
            name = raw.get("displayName")
        elif provider == "openai":
            keep = "image" in model and not model.startswith("dall-e")
            name = None
        elif provider == "vercel":
            keep = raw.get("type") == "image"
            name = raw.get("name")
        else:
            # A model that draws only SVG would be paid for and then refused: pictures are PNG,
            # JPEG or WebP. OpenRouter lists a model's output formats when it limits them.
            parameters = raw.get("supported_parameters")
            formats = parameters.get("output_format") if isinstance(parameters, dict) else None
            values = formats.get("values") if isinstance(formats, dict) else None
            keep = not values or any(value in RASTER_FORMATS for value in values)
            name = raw.get("name")
        if keep and model:
            found[model] = str(name or model)
    return sorted(
        ({"id": model, "name": name} for model, name in found.items()), key=lambda m: m["name"].casefold()
    )


def _size(aspect_ratio: str) -> str:
    """The OpenAI size closest to a ratio: every GPT Image model takes these three."""
    if not aspect_ratio:
        return "auto"
    width, height = (int(part) for part in aspect_ratio.split(":"))
    return "1024x1024" if width == height else "1536x1024" if width > height else "1024x1536"


def _data_url(image: Picture) -> str:
    return f"data:{image.media_type};base64,{base64.b64encode(image.data).decode()}"


async def _gemini(client, model, prompt, aspect_ratio, inputs, _zdr) -> list[str]:
    parts: list[dict[str, Any]] = [{"text": prompt}]
    parts += [
        {"inline_data": {"mime_type": image.media_type, "data": base64.b64encode(image.data).decode()}}
        for image in inputs
    ]
    config: dict[str, Any] = {"responseModalities": ["TEXT", "IMAGE"]}
    if aspect_ratio:
        config["imageConfig"] = {"aspectRatio": aspect_ratio}
    response = await client.post(
        f"{GEMINI_BASE_URL}/models/{model}:generateContent",
        json={"contents": [{"role": "user", "parts": parts}], "generationConfig": config},
    )
    if response.is_error:
        raise _refusal("gemini", response)
    body = response.json()
    candidates = body.get("candidates") or []
    found = [
        part["inlineData"]["data"]
        for candidate in candidates
        for part in (candidate.get("content") or {}).get("parts") or []
        # Thinking can draw interim images first; the answer is the last one that is not a thought.
        if (part.get("inlineData") or {}).get("data") and not part.get("thought")
    ]
    if found:
        return found[-1:]
    said = " ".join(
        part["text"]
        for c in candidates
        for part in (c.get("content") or {}).get("parts") or []
        if part.get("text")
    )
    blocked = (body.get("promptFeedback") or {}).get("blockReason") or next(
        (c.get("finishReason") for c in candidates if c.get("finishReason") not in (None, "STOP")), None
    )
    raise RuntimeError(
        "Gemini returned no image"
        + (f" ({blocked})" if blocked else "")
        + (f": {said.strip()}" if said.strip() else ". Try rewording the prompt.")
    )


async def _openai(client, model, prompt, aspect_ratio, inputs, zdr, *, vercel=False) -> list[str]:
    base = VERCEL_GATEWAY_BASE_URL if vercel else OPENAI_BASE_URL
    provider = "vercel" if vercel else "openai"
    # Vercel documents size only for OpenAI's models; the gateway's others take their own options.
    sized = not vercel or model.startswith("openai/")
    fields: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1}
    if sized:
        fields["size"] = _size(aspect_ratio)
    if vercel:
        fields["response_format"] = "b64_json"
        if zdr:
            fields["providerOptions"] = {"gateway": {"zeroDataRetention": True}}
    if not inputs:
        response = await client.post(f"{base}/images/generations", json=fields)
    elif vercel:
        images = [{"image_url": _data_url(image)} for image in inputs]
        response = await client.post(f"{base}/images/edits", json={**fields, "images": images})
    else:
        files = [
            ("image[]", (f"input-{index}{EXTENSIONS[image.media_type]}", image.data, image.media_type))
            for index, image in enumerate(inputs, 1)
        ]
        data = {name: str(value) for name, value in fields.items()}
        response = await client.post(f"{base}/images/edits", data=data, files=files)
    if response.is_error:
        raise _refusal(provider, response)
    return [item["b64_json"] for item in response.json().get("data") or [] if item.get("b64_json")]


async def _openrouter(client, model, prompt, aspect_ratio, inputs, zdr) -> list[str]:
    body: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1}
    if aspect_ratio:
        body["aspect_ratio"] = aspect_ratio
    if inputs:
        body["input_references"] = [{"type": "image_url", "image_url": {"url": _data_url(i)}} for i in inputs]
    if zdr:
        # Only endpoints that keep nothing; a model with none fails with an error.
        body["provider"] = {"zdr": True}
    response = await client.post(f"{OPENROUTER_BASE_URL}/images", json=body)
    if response.is_error:
        raise _refusal("openrouter", response)
    return [item["b64_json"] for item in response.json().get("data") or [] if item.get("b64_json")]


async def _vercel(client, model, prompt, aspect_ratio, inputs, zdr) -> list[str]:
    return await _openai(client, model, prompt, aspect_ratio, inputs, zdr, vercel=True)


CLIENTS = {"gemini": _gemini, "openai": _openai, "openrouter": _openrouter, "vercel": _vercel}


async def create(
    secrets: SecretBackend,
    key: str,
    prompt: str,
    *,
    count: int = 1,
    aspect_ratio: str = "",
    inputs: list[Picture] | None = None,
    zero_data_retention: bool = False,
) -> tuple[list[Picture], list[str]]:
    """`count` images from the model `key` (`provider::model`), edited from `inputs` when given,
    and why any of the requests failed.

    The images that were made are kept when others fail: they were paid for, and trying again
    would make them again. Raises only when none was made.
    """
    provider, _, model = key.partition("::")
    name = PROVIDERS[provider][0]
    async with httpx.AsyncClient(
        timeout=TIMEOUT, headers=_headers(provider, _key(secrets, provider))
    ) as client:
        outcomes = await asyncio.gather(
            *(
                CLIENTS[provider](client, model, prompt, aspect_ratio, inputs or [], zero_data_retention)
                for _ in range(count)
            ),
            return_exceptions=True,
        )
    pictures: list[Picture] = []
    failures: list[Exception] = []
    for outcome in outcomes:
        if isinstance(outcome, httpx.TimeoutException):
            outcome = RuntimeError(f"{name} took too long to return the image.")
        if isinstance(outcome, BaseException):
            if not isinstance(outcome, Exception):
                raise outcome  # Cancelled: the task is stopping.
            failures.append(outcome)
            continue
        if not outcome:
            failures.append(RuntimeError(f"{name} returned no image. Try rewording the prompt."))
        for item in outcome:
            try:
                pictures.append(picture(base64.b64decode(item), "The model's reply"))
            except ValueError as error:
                failures.append(error)
    if not pictures:
        raise failures[0]
    return pictures, [str(error) for error in failures]
