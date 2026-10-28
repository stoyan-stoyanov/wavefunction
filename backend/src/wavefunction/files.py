from __future__ import annotations

import asyncio
import codecs
import csv
import io
import re
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from .fileops import _decode
from .repository import Repository, new_id, now_iso

# Uploads are streamed to disk in pieces this large. There is no ceiling on a reference's
# size: the old 20 MiB one existed because the whole transfer was held in memory.
UPLOAD_CHUNK = 1 << 20
FILE_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".xml": "application/xml",
    ".html": "text/html",
    ".htm": "text/html",
    ".log": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
ALLOWED_MEDIA_TYPES = set(FILE_TYPES.values()) | {"application/csv", "text/yaml", "text/xml"}


def readable_documents(repository: Repository, agent_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
    """Uploaded reference text of the file IDs a task captured."""
    return repository.get_project_file_texts(agent_id, file_ids)


def safe_filename(value: str) -> str:
    name = Path(value).name
    clean = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return clean[:180] or "upload"


def extract_text(path: Path, media_type: str, filename: str) -> str:
    """The searchable text of an uploaded file, read from disk rather than from one buffer."""
    if media_type.startswith("image/"):
        from PIL import Image

        try:
            with Image.open(path) as image:
                # A camera JPEG with a large embedded preview opens as MPO; it is still a JPEG,
                # and its first frame is the photo.
                if Image.MIME.get(image.format or "") != media_type and not (
                    image.format == "MPO" and media_type == "image/jpeg"
                ):
                    raise ValueError("image type does not match the file")
                width, height = image.size
                image.verify()
            return f"Image: {filename} ({width} × {height}). Read this document to view its image content."
        except Exception as error:
            raise ValueError("Unable to read image. Upload a valid PNG, JPEG, WebP, or GIF.") from error
    from .documents import docx_to_markdown, pptx_to_markdown, xlsx_to_markdown

    for extension, reader in {
        ".docx": docx_to_markdown,
        ".xlsx": xlsx_to_markdown,
        ".pptx": pptx_to_markdown,
    }.items():
        if media_type == FILE_TYPES[extension]:
            try:
                return reader(path, include_title=True) if extension == ".docx" else reader(path)
            except Exception as error:
                raise ValueError(
                    f"Unable to read {extension[1:].upper()}. Upload a valid, unencrypted document."
                ) from error
    if media_type == "application/pdf" or filename.lower().endswith(".pdf"):
        from pypdf import PdfReader

        try:
            with path.open("rb") as handle:
                reader = PdfReader(handle)
                if reader.is_encrypted:
                    raise ValueError("encrypted PDF")
                return "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception as error:
            # Malformed PDFs can raise parser exceptions outside pypdf's own hierarchy.
            raise ValueError("Unable to read PDF. Upload a valid, unencrypted PDF.") from error
    # Decoded as the repository's files are: UTF-8, else Windows-1252 (what Excel on Windows saves
    # a CSV as), and UTF-16 by its byte-order mark, rather than UTF-8 with replacement characters.
    data = path.read_bytes()
    text = (
        data.decode("utf-16")
        if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))
        else _decode(data)[0]
    )
    if media_type in {"text/csv", "application/csv"} or filename.lower().endswith(".csv"):
        return "\n".join(
            " | ".join(cell.strip() for cell in row) for row in csv.reader(io.StringIO(text, newline=""))
        )
    return text


class ProjectFileStore:
    def __init__(self, data_dir: Path, repository: Repository):
        self.data_dir = data_dir
        self.repository = repository

    def project_dir(self, project_id: str) -> Path:
        return self.repository.package_path(project_id)

    async def save_upload(self, project_id: str, upload: UploadFile) -> dict[str, object]:
        filename = safe_filename(upload.filename or "upload")
        # The name the user and the model see keeps its own letters, so two files whose names
        # are not ASCII stay apart; only the file on disk uses the ASCII-safe name.
        display = re.sub(r"[\x00-\x1f\x7f]", "", Path(upload.filename or "").name).strip()[:180] or filename
        # Extensions identify known formats even when the picker sends octet-stream or text/plain.
        media_type = FILE_TYPES.get(
            Path(filename).suffix.lower(), upload.content_type or "application/octet-stream"
        )
        if media_type not in ALLOWED_MEDIA_TYPES:
            raise ValueError(
                "Unsupported file type. Use PNG, JPEG, WebP, GIF, PDF, DOCX, XLSX, PPTX, or a text/data file."
            )

        file_id = new_id()
        relative = Path("sources") / f"{file_id}-{filename}"
        destination = self.project_dir(project_id) / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Straight to disk, a piece at a time: memory does not grow with the file, and an
            # interrupted transfer leaves nothing behind but the partial file removed below.
            size = 0
            with destination.open("wb") as sink:
                while chunk := await upload.read(UPLOAD_CHUNK):
                    sink.write(chunk)
                    size += len(chunk)
            # Parse before publishing; extraction can be expensive and must not block the API loop.
            text = await asyncio.to_thread(extract_text, destination, media_type, display)
            record = {
                "id": file_id,
                "project_id": project_id,
                "name": display,
                "media_type": media_type,
                "relative_path": str(relative),
                "extracted_text": text,
                "size_bytes": size,
                "created_at": now_iso(),
            }
            return self.repository.add_project_file(record)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
