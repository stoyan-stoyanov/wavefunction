"""`generate_image` and `edit_image`: pictures from the image models the user turned on.

They come with a task whose captured spec names image models and that may write files, as the
routine tools come with one outside Plan mode. The images are ordinary files: in the chat's
folder when it has one, where the user's own tools open them, and otherwise attached to the
chat, which keeps them with its history and removes them with it. Either way the transcript
shows them from the tool's result and the agent can read or edit them again by name.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from . import image_models
from .desktop_files import _not_private, _write_mac
from .folder_setup import _covered
from .toolkit import ToolContext

FOLDER = "generated-images"


def _slug(prompt: str) -> str:
    words = re.findall(r"[a-z0-9]+", prompt.casefold())[:6]
    return "-".join(words)[:60].strip("-") or "image"


def _chosen(models: list[str], wanted: str) -> str:
    wanted = wanted.strip()
    if not wanted:
        return models[0]
    for key in models:
        if wanted in (key, key.partition("::")[2]):
            return key
    raise ValueError(f"{wanted} is not turned on. Use one of: {', '.join(models)}.")


def _input(context: ToolContext, name: str) -> image_models.Picture:
    """An image to edit: a file in the chat's folder by path, else an attachment by its name."""
    wanted = name.strip()
    if context.working_directory and wanted:
        root = Path(context.working_directory).expanduser().resolve()
        path = (root / Path(wanted).expanduser()).resolve()
        if path.is_file():
            if not _covered(str(_not_private(path)), False, context.local_grants()):
                raise ValueError(f"{path} is not in a folder this chat may read.")
            if path.stat().st_size > image_models.MAX_INPUT_BYTES:
                raise ValueError(f"{wanted} is too large to send to an image model.")
            return image_models.picture(path.read_bytes(), wanted)
    allowed = {row["id"] for row in context.reference_files or []}
    # An attachment has a name and no folder: generated-images/fox.png is the attachment fox.png.
    names = {wanted.casefold(), Path(wanted).name.casefold()}
    for record in context.repository.list_project_files(context.agent_id):
        if record["id"] in allowed and record["name"].casefold() in names:
            path = context.files.project_dir(context.agent_id) / record["relative_path"]
            if path.stat().st_size > image_models.MAX_INPUT_BYTES:
                raise ValueError(f"{wanted} is too large to send to an image model.")
            return image_models.picture(path.read_bytes(), wanted)
    raise ValueError(
        f"There is no image named {wanted!r}. Give an attached image by the name list_documents "
        "gives, or a file in the chat's folder by its path."
    )


def _names(stem: str, images: list[image_models.Picture]) -> list[str]:
    numbered = len(images) > 1
    return [
        f"{stem}-{index}{image_models.EXTENSIONS[image.media_type]}"
        if numbered
        else f"{stem}{image_models.EXTENSIONS[image.media_type]}"
        for index, image in enumerate(images, 1)
    ]


def _save_in_folder(
    context: ToolContext, images: list[image_models.Picture], path: str, prompt: str
) -> list[dict]:
    root = Path(str(context.working_directory)).expanduser().resolve()
    target = (root / (path.strip() or f"{FOLDER}/{_slug(prompt)}")).resolve()
    if not target.is_relative_to(root) or ".git" in target.relative_to(root).parts:
        raise ValueError("Save images inside the chat's folder, outside .git.")
    grants = context.local_grants()
    if not _covered(str(target.parent), True, grants):
        raise ValueError(f"This chat may not save files in {target.parent}.")
    target.parent.mkdir(parents=True, exist_ok=True)
    saved = []
    for name, image in zip(_names(target.stem or _slug(prompt), images), images, strict=True):
        written = _write_mac(context, target.parent, name, image.data, grants=grants)
        saved.append({"path": str(written.relative_to(root) if written.is_relative_to(root) else written)})
    return saved


def _attach(context: ToolContext, images: list[image_models.Picture], path: str, prompt: str) -> list[dict]:
    """Without a folder, the images join the chat's attachments: the history owns them."""
    if not context.run_id:
        raise ValueError("Choose a folder for this chat, then generate the image again.")
    taken = {str(row.get("name", "")).casefold() for row in context.reference_files or []}
    records = []
    for name in _names(Path(path.strip()).stem or _slug(prompt), images):
        stem, extension = Path(name).stem, Path(name).suffix
        number = 0
        while name.casefold() in taken:
            number += 1
            name = f"{stem} ({number}){extension}"
        taken.add(name.casefold())
        records.append(name)
    saved = [
        context.files.save_image(context.agent_id, name, image.data, image.media_type)
        for name, image in zip(records, images, strict=True)
    ]
    context.repository.capture_run_files(context.run_id, saved)
    if context.reference_files is not None:
        context.reference_files.extend(saved)
    return [{"attachment": record["name"], "id": record["id"]} for record in saved]


def build_image_tools(context: ToolContext, models: list[str]) -> list[BaseTool]:
    """The two image tools, for the image models a task captured (the first is the default)."""
    zero_data_retention = context.repository.get_setting("require_zdr", False) is True
    shared = (
        "    count: How many images, 1 to 4.\n"
        f"    model: One of {', '.join(models)}. Empty is the first.\n"
        "    path: Where to save in the chat's folder, such as assets/hero.png. Empty names it from "
        f"the prompt in {FOLDER}/. Taken names get a number. A chat without a folder attaches "
        "the images instead."
    )
    ratios = ", ".join(image_models.ASPECT_RATIOS)
    generate_description = (
        "Create images from a text prompt with an image model the user turned on. They are saved "
        "as files and the user sees them in the chat.\n\n"
        "Args:\n"
        "    prompt: What the image shows: subject, style, composition and any text in it.\n"
        f"    aspect_ratio: Width:height, one of {ratios}.\n" + shared
    )
    edit_description = (
        "Edit or combine images with an image model the user turned on: change, restyle or merge "
        "them as the prompt says. The results are saved as new files and the user sees them in "
        "the chat.\n\n"
        "Args:\n"
        "    prompt: The change to make, or how to combine the images.\n"
        "    images: The images to start from: attached images by the name list_documents gives, "
        "or files in the chat's folder by path.\n"
        f"    aspect_ratio: Width:height of the result, one of {ratios}. Empty keeps the input's.\n" + shared
    )

    async def make(
        prompt: str, images: list[str], count: int, aspect_ratio: str, model: str, path: str
    ) -> str:
        if context.local_read_only:
            raise ValueError("Saving files is disabled for this task.")
        if not prompt.strip():
            raise ValueError("Say in the prompt what the image should show.")
        if not 1 <= count <= image_models.MAX_IMAGES:
            raise ValueError(f"Make 1 to {image_models.MAX_IMAGES} images at a time.")
        ratio = aspect_ratio.strip()
        if ratio and ratio not in image_models.ASPECT_RATIOS:
            raise ValueError(f"Use an aspect ratio of {ratios}.")
        key = _chosen(models, model)
        inputs = [await asyncio.to_thread(_input, context, name) for name in images]
        made = await image_models.create(
            context.secrets,
            key,
            prompt.strip(),
            count=count,
            aspect_ratio=ratio,
            inputs=inputs,
            zero_data_retention=zero_data_retention,
        )
        save = _save_in_folder if context.working_directory else _attach
        saved = await asyncio.to_thread(save, context, made, path, prompt)
        result: dict[str, Any] = {
            "model": key,
            "images": saved,
            "note": "The user sees these images in the chat.",
        }
        return json.dumps(result)

    @tool("generate_image", description=generate_description)
    async def generate_image(
        prompt: str, count: int = 1, aspect_ratio: str = "1:1", model: str = "", path: str = ""
    ) -> str:
        return await make(prompt, [], count, aspect_ratio, model, path)

    @tool("edit_image", description=edit_description)
    async def edit_image(
        prompt: str,
        images: list[str],
        count: int = 1,
        aspect_ratio: str = "",
        model: str = "",
        path: str = "",
    ) -> str:
        if not images:
            raise ValueError("Name at least one image to edit.")
        return await make(prompt, images, count, aspect_ratio, model, path)

    return [generate_image, edit_image]
