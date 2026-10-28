"""The local file tools' operations, run inside the same boundary as a command.

The backend never opens a file for these tools itself. It runs this module as
`python -I -m wavefunction.fileops` inside the agent's sandbox, writes one JSON request to its
standard input and reads one JSON object back: the result, or a refusal with a kind and a plain
sentence. So a folder the sandbox keeps private stays private to the file tools too, file
content never travels on a command line, and only an image read imports Pillow.
"""

from __future__ import annotations

import base64
import bisect
import codecs
import difflib
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import Counter
from contextlib import suppress
from pathlib import Path

# Opened as images by name; other files are told apart by their first bytes.
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".ico"}
IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"II*\0", b"MM\0*")
# How much of a file decides whether it is text: a NUL byte in it means binary, as git decides.
SNIFF = 8000
# Files up to this size are known by a hash of their content, larger ones by size and time:
# nothing that large is rewritten whole, and hashing it on every read would be slow.
HASH_LIMIT = 16 * 1024 * 1024
# The most of a diff a result carries: what one read_file call returns.
DIFF_CHARS = 40_000


class Refused(Exception):
    """The operation declined and changed nothing; `kind` names why."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _encodings(head: bytes) -> tuple[str, ...]:
    """UTF-8, minding a byte-order mark; else Windows-1252, which most Latin-1 text really is;
    else Latin-1, which decodes any bytes at all."""
    return ("utf-8-sig" if head.startswith(codecs.BOM_UTF8) else "utf-8", "cp1252", "latin-1")


def _decode(data: bytes) -> tuple[str, str]:
    *preferred, fallback = _encodings(data)
    for encoding in preferred:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    return data.decode(fallback), fallback


def _scan(path: str, encodings: tuple[str, ...]) -> tuple[str, dict[str, bool]]:
    """The file's version, and which of `encodings` all of it decodes in, in one pass: what a
    read pages through and what an edit decodes must agree on one encoding. A file too large to
    hash is paged as it decodes."""
    with open(path, "rb") as f:
        info = os.fstat(f.fileno())
        if info.st_size > HASH_LIMIT:
            return f"{info.st_size}:{info.st_mtime_ns}", dict.fromkeys(encodings, True)
        digest = hashlib.sha256()
        decoders = {encoding: codecs.getincrementaldecoder(encoding)() for encoding in encodings}
        decodes = dict.fromkeys(encodings, True)

        def feed(chunk, final=False):
            for encoding, decoder in decoders.items():
                if decodes[encoding]:
                    try:
                        decoder.decode(chunk, final)
                    except UnicodeDecodeError:
                        decodes[encoding] = False

        while chunk := f.read(1 << 20):
            digest.update(chunk)
            feed(chunk)
        feed(b"", True)
    return digest.hexdigest(), decodes


def _encode(text: str, encoding: str, path: str) -> bytes:
    try:
        return text.encode(encoding)
    except UnicodeEncodeError as error:
        character = error.object[error.start : error.end]
        raise Refused(
            "encoding",
            f"{path} is encoded as {encoding}, which cannot hold {character!r}. "
            "Keep to characters it can hold, or convert the file first.",
        ) from None


def _crlf(text: str) -> bool:
    """Whether most of the text's lines end in CRLF."""
    crlf = text.count("\r\n")
    return crlf > text.count("\n") - crlf


def _regular(path: str) -> os.stat_result | None:
    """The file's status, or None when nothing is there; a folder or a device is refused."""
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISDIR(info.st_mode):
        raise Refused("folder", f"{path} is a folder, not a file.")
    if not stat.S_ISREG(info.st_mode):
        raise Refused("special", f"{path} is not a regular file.")
    return info


def _version(path: str) -> str:
    """What a later write compares to, to know the file is still the one that was read."""
    with open(path, "rb") as f:
        info = os.fstat(f.fileno())
        if info.st_size > HASH_LIMIT:
            return f"{info.st_size}:{info.st_mtime_ns}"
        return hashlib.file_digest(f, "sha256").hexdigest()


def _diff(path: str, before: str, after: str) -> str:
    """The unified diff, cut at DIFF_CHARS: one changed character in a huge line is otherwise a
    diff twice the file's size."""

    def lines(text: str) -> list[str]:
        return text.replace("\r\n", "\n").splitlines(keepends=True)

    shown, size = [], 0
    for line in difflib.unified_diff(lines(before), lines(after), fromfile=path, tofile=path):
        if size + len(line) > DIFF_CHARS:
            shown += [
                line[: DIFF_CHARS - size],
                f"\n[Diff shortened to {DIFF_CHARS:,} characters; the file "
                "was saved in full. Read it to see the rest.]\n",
            ]
            break
        shown.append(line)
        size += len(line)
    return "".join(shown)


def _raced(path: str) -> Refused:
    return Refused("changed", f"{path} changed while it was being saved. Read it again before retrying.")


def _save(path: str, data: bytes, unchanged=None, *, mode=None) -> None:
    """Replace the file with `data` in one step, keeping an existing file's metadata.

    The content goes to a temporary file beside it, made as a copy of the original so it keeps
    its mode, ownership, ACLs and extended attributes; the rename happens only once the whole
    write succeeded, and only while `unchanged()` holds, so a save someone made meanwhile is
    not overwritten. Without `unchanged` the file is new: its name is claimed first, which fails
    if it exists by then, so of two saves creating one file only one succeeds. A claim rather
    than a hard link, which some volumes, such as exFAT, do not support.
    """
    try:
        fd, temporary = tempfile.mkstemp(prefix=".wavefunction-edit-", dir=os.path.dirname(path))
    except OSError as error:
        # A folder that may not be changed refuses the temporary file first; say it of the file.
        raise type(error)(error.errno, error.strerror, path) from None
    os.close(fd)
    try:
        if os.path.exists(path):
            if sys.platform == "darwin":
                # macOS cp -p preserves ACLs and extended attributes as well as POSIX metadata.
                subprocess.run(["/bin/cp", "-p", path, temporary], check=True)
            else:
                shutil.copy2(path, temporary)
            original, copied = os.stat(path), os.stat(temporary)
            if (original.st_uid, original.st_gid) != (copied.st_uid, copied.st_gid):
                os.chown(temporary, original.st_uid, original.st_gid)
        else:
            mask = os.umask(0)
            os.umask(mask)
            os.chmod(temporary, 0o666 & ~mask)
        with open(temporary, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        claim = None
        try:
            if unchanged is None:
                try:
                    claim = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    raise _raced(path) from None
                # Locked until the content is in place: a save that finds the empty claim
                # meanwhile waits, then sees the file was replaced, rather than filling what looks
                # like an empty file. One that locked the claim first has replaced it by now.
                fcntl.flock(claim, fcntl.LOCK_EX)
                try:
                    claimed = os.fstat(claim).st_ino == os.stat(path).st_ino
                except FileNotFoundError:
                    claimed = False
                if not claimed:
                    raise _raced(path)
            elif not unchanged():
                raise _raced(path)
            os.replace(temporary, path)
        finally:
            if claim is not None:
                os.close(claim)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _looks_like_image(path: str, head: bytes) -> bool:
    return (
        os.path.splitext(path)[1].lower() in IMAGE_SUFFIXES
        or head.startswith(IMAGE_SIGNATURES)
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )


def _snapshot(path: str, snapshot: str) -> str:
    """Save the image's first frame, upright and fitted to 2048 pixels, as a PNG."""
    from PIL import Image, ImageOps

    with Image.open(path) as source:
        picture = ImageOps.exif_transpose(source).convert("RGBA")
    picture.thumbnail((2048, 2048))
    os.makedirs(os.path.dirname(snapshot), exist_ok=True)
    picture.save(snapshot, format="PNG")
    return f"{picture.width}x{picture.height}"


def _characters(f, offset: int, limit: int) -> str:
    skipped = 0
    while skipped < offset and (chunk := f.read(min(offset - skipped, 65536))):
        skipped += len(chunk)
    text = f.read(limit + 1)
    if not text:
        return (
            f"Nothing to read at offset {offset}: the file has {skipped:,} characters."
            if offset
            else "The file is empty."
        )
    if len(text) > limit:
        return text[:limit] + f"\n[More text available: call read_file with offset={offset + limit}]"
    return text


def _lines(f, start: int, count: int, budget: int) -> str:
    position = lines = 0
    partial = False
    while lines < start - 1:
        # Bounded, so a minified file is not loaded as one enormous line.
        part = f.readline(65536)
        if not part:
            lines += partial
            break
        position += len(part)
        partial = not part.endswith("\n")
        lines += not partial
    shown = []
    for number in range(start, start + count):
        text = f.readline(budget + 1)
        if not text:
            break
        piece = text[:budget]
        line = piece.rstrip("\n")
        shown.append(f"{number}: {line}")
        position += len(piece)
        budget -= len(piece)
        if len(text) > len(piece) or not budget:
            if len(text) > len(piece) or f.read(1):
                shown.append(f"[Character budget reached: continue with offset={position}]")
            break
    else:
        if f.read(1):
            shown.append(f"[More lines available: start_line={start + count}]")
    if not shown:
        return (
            f"Nothing to read at line {start}: the file has {lines:,} lines."
            if lines
            else "The file is empty."
        )
    return "\n".join(shown)


def read(request: dict) -> dict:
    path = request["path"]
    info = _regular(path)
    if info is None:
        raise Refused("not_found", f"File not found: {path}")
    with open(path, "rb") as f:
        head = f.read(SNIFF)
    if request.get("text_only"):
        if b"\0" in head or _looks_like_image(path, head):
            raise Refused(
                "binary", "The code editor supports text files. Inspect this binary file with a tool instead."
            )
        if info.st_size > request["limit"]:
            raise Refused(
                "large",
                "This file is too large for the editor. Use the agent's paginated file tools for a focused edit.",
            )
    encodings = _encodings(head)
    version, decodes = _scan(path, encodings[:-1])
    # A byte-order mark alone is empty too.
    if request.get("text_only") and (not info.st_size or info.st_size <= 4 and not _decode(head)[0]):
        # The editor shows the file's text itself; "The file is empty." is for the agent.
        return {"text": "", "version": version}
    if _looks_like_image(path, head):
        try:
            return {"image": _snapshot(path, request["snapshot"]), "version": version}
        except Exception:  # noqa: BLE001 - what Pillow cannot open is read as text or bytes instead
            pass
    if b"\0" in head:
        return {
            "text": f"{path} is a binary file ({info.st_size:,} bytes), so its content is not "
            "shown. Inspect it with run_command, for example `file` or `xxd | head`.",
            "version": version,
        }
    # The last encoding, Latin-1, decodes any bytes, so one of these returns.
    # One encoding for the whole file, as edits decode it: a legacy byte deep in a mostly
    # UTF-8 file would otherwise make early pages UTF-8 and later ones Windows-1252.
    encodings = tuple(encoding for encoding in encodings[:-1] if decodes[encoding]) + encodings[-1:]
    for encoding in encodings:
        try:
            # Universal newlines: a CRLF file reads as LF, which is how edit_file matches it.
            with open(path, encoding=encoding) as text:
                if request.get("start_line"):
                    page = _lines(text, request["start_line"], request["line_count"], request["limit"])
                else:
                    page = _characters(text, request["offset"], request["limit"])
            return {"text": page, "version": version}
        except UnicodeDecodeError:
            continue
    raise AssertionError("Latin-1 decodes any bytes.")


def _changed(path: str) -> Refused:
    return Refused(
        "changed", f"{path} has changed since it was last read. Read it again before replacing it."
    )


def write(request: dict) -> dict:
    path = os.path.realpath(request["path"])
    content = request["content"]
    if _regular(path) is None:
        if request.get("expected") is not None:
            # Read in this run and deleted since: writing would undo someone else's delete.
            raise _changed(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _save(path, content.encode())
        return {"text": f"Created {path} ({len(content):,} characters).", "version": _version(path)}
    # Opened for writing first, so a file this runner may not change is refused before anything.
    with open(path, "r+b") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        # A save that finished while this one waited for the lock replaced the file: what this
        # descriptor holds, empty or not, is no longer what is there.
        if os.fstat(f.fileno()).st_ino != os.stat(path).st_ino:
            raise _raced(path)
        version, expected = _version(path), request.get("expected")
        # An empty file has nothing to lose, so it needs no reading first; a file emptied since
        # it was read has changed like any other.
        if expected is None and os.fstat(f.fileno()).st_size:
            raise Refused(
                "unread",
                f"{path} already exists and has not been read in this run. Read it "
                "with read_file before replacing it, or change part of it with edit_file.",
            )
        if expected is not None and expected != version:
            raise _changed(path)
        old = f.read()
        if b"\0" in old[:SNIFF]:
            data, diff = content.encode(), f"Replaced the binary file {path}."
        else:
            # The file keeps its encoding, byte-order mark and line endings.
            before, encoding = _decode(old)
            if {_ending(line) for line in _LINES.findall(before)} - {""} not in ({"\n"}, {"\r\n"}, set()):
                after = _mixed_endings(before, content)
            else:
                after = content.replace("\r\n", "\n").replace("\n", "\r\n") if _crlf(before) else content
            data, diff = _encode(after, encoding, path), _diff(path, before, after)
        if data != old:
            _save(path, data, lambda: _version(path) == version)
    return {"text": diff or "No change.", "version": _version(path)}


# A line with its ending: CRLF, a lone CR (classic Mac files, progress output) or LF.
_LINES = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$")


def _ending(line: str) -> str:
    return line[len(line.rstrip("\r\n")) :]


def _mixed_endings(before: str, content: str) -> str:
    """A file whose lines do not all end in LF or all in CRLF keeps each unchanged line's own
    ending; new and changed lines take the ending most of the file uses. The editor's text has
    every line ending in LF."""
    old = _LINES.findall(before)
    new = _LINES.findall(content.replace("\r\n", "\n").replace("\r", "\n"))
    usual = Counter(_ending(line) for line in old if _ending(line)).most_common(1)[0][0]

    def ending(line):
        return line[:-1] + usual if line.endswith("\n") else line

    # The lines before and after the edited part are matched directly: a save that changed one
    # place is linear in the file's size, however repetitive its lines.
    key_old, key_new = [line.rstrip("\r\n") for line in old], [line.rstrip("\n") for line in new]
    start = 0
    while start < min(len(old), len(new)) and key_old[start] == key_new[start]:
        start += 1
    end = 0
    while end < min(len(old), len(new)) - start and key_old[-1 - end] == key_new[-1 - end]:
        end += 1
    kept = []
    middle_old, middle_new = key_old[start : len(old) - end], key_new[start : len(new) - end]
    # Exact matching keeps repeated lines' endings; only a very large edited span falls back to
    # the heuristic that skips popular lines, which would otherwise take quadratic time.
    matcher = difflib.SequenceMatcher(
        None, middle_old, middle_new, autojunk=len(middle_old) + len(middle_new) > 4000
    )
    opcodes = [("equal", 0, start, 0, start)] if start else []
    opcodes += [
        (tag, i1 + start, i2 + start, j1 + start, j2 + start) for tag, i1, i2, j1, j2 in matcher.get_opcodes()
    ]
    opcodes += [("equal", len(old) - end, len(old), len(new) - end, len(new))] if end else []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            # The last line may have gained or lost its newline.
            kept += [
                o if bool(_ending(o)) == n.endswith("\n") else ending(n)
                for o, n in zip(old[i1:i2], new[j1:j2], strict=True)
            ]
        else:
            kept += [ending(line) for line in new[j1:j2]]
    return "".join(kept)


def _replace(text: str, edits: list[dict], path: str) -> str:
    """Apply the edits, matched in the text as read_file shows it — CRLF and a lone CR as LF —
    and spliced into the original, so every line not edited keeps its own ending."""
    # A lone CR (classic Mac line endings) becomes LF in place, so positions only shift for CRLF.
    normal = text.replace("\r\n", "\n").replace("\r", "\n")
    # The index in `normal` of each LF that lost its CR, to map positions back to `text`.
    dropped, start = [], 0
    while (found := text.find("\r\n", start)) >= 0:
        dropped.append(found - len(dropped))
        start = found + 2

    def original(index: int) -> int:
        return index + bisect.bisect_left(dropped, index)

    crlf = _crlf(text)
    cr = not crlf and text.count("\r") - text.count("\r\n") > text.count("\n")
    spans = []
    for number, edit in enumerate(edits, 1):
        old = edit["old_text"].replace("\r\n", "\n")
        new = edit["new_text"].replace("\r\n", "\n")
        if crlf:
            new = new.replace("\n", "\r\n")
        elif cr:
            new = new.replace("\n", "\r")
        # Every occurrence counts toward uniqueness, overlapping ones too: "aa" is twice in "aaa".
        matches, at = [], normal.find(old)
        while at >= 0:
            matches.append(at)
            at = normal.find(old, at + 1)
        if not matches:
            raise Refused(
                "no_match",
                f"Edit {number}: old_text was not found in {path}. Read the file "
                "again and copy the text exactly, including indentation.",
            )
        if len(matches) > 1 and not edit.get("replace_all"):
            raise Refused(
                "ambiguous",
                f"Edit {number}: found {len(matches)} matches for old_text. Include "
                "more surrounding text so it matches once, or set replace_all to change every one.",
            )
        # replace_all changes the occurrences that do not overlap, from the left.
        end = -1
        for at in matches:
            if at >= end:
                spans.append((at, at + len(old), new))
                end = at + len(old)
    spans.sort(key=lambda span: span[0])
    pieces, cursor = [], 0
    for begin, end, new in spans:
        if begin < cursor:
            raise Refused(
                "overlap", "Edits overlap. Combine changes to the same region into one replacement."
            )
        pieces += [text[original(cursor) : original(begin)], new]
        cursor = end
    pieces.append(text[original(cursor) :])
    return "".join(pieces)


def edit(request: dict) -> dict:
    path = os.path.realpath(request["path"])
    if _regular(path) is None:
        raise Refused("not_found", f"File not found: {path}")
    # Opened for writing first, so a file this runner may not change is refused before anything.
    with open(path, "r+b") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        inode = os.fstat(f.fileno()).st_ino
        if inode != os.stat(path).st_ino:
            raise Refused("changed", f"{path} changed during this edit. Read it again before retrying.")
        old = f.read()
        if b"\0" in old[:SNIFF]:
            raise Refused("binary", f"{path} is a binary file; edit_file changes text files only.")
        text, encoding = _decode(old)
        updated = _replace(text, request["edits"], path)
        data = _encode(updated, encoding, path)

        def unchanged() -> bool:
            with open(path, "rb") as current:
                return os.fstat(current.fileno()).st_ino == inode and current.read() == old

        if data != old:
            _save(path, data, unchanged)
    return {"text": _diff(path, text, updated) or "No change.", "version": _version(path)}


def search(request: dict) -> dict:
    """Files by name, or lines by content, through the bundled ripgrep: sorted, bounded, paged."""
    path, offset, limit = request["path"], request["offset"], request["limit"]
    # ripgrep searches a path it is given whatever the exclusions below say, so the same rule
    # applies to every folder on the way to the starting point, and to where a link there leads.
    # In any letter case: on macOS `.GIT/config` opens `.git/config`.
    for candidate in (path, os.path.realpath(path)):
        if any(
            part.casefold() == ".git" or part.casefold().startswith(".env") for part in Path(candidate).parts
        ):
            raise Refused(
                "excluded",
                f"{path} is not searched: Git's own folder and .env files are left "
                "out of find_files and search_files. Read a specific file with read_file instead.",
            )
    if not os.path.lexists(path):
        raise Refused("not_found", f"Path not found: {path}")
    # Surface an unreadable starting point instead of claiming no matches.
    if os.path.isdir(path):
        with os.scandir(path) as entries:
            next(entries, None)
    else:
        with open(path, "rb"):
            pass
    args = [
        request["rg"],
        "--no-config",
        "--no-ignore-global",
        "--no-require-git",
        "--sort",
        "path",
        "--color",
        "never",
        # Hidden files are searched — .github, dotfile configuration — but not Git's own
        # database, nor environment files, where secrets live.
        "--hidden",
        "--iglob",
        "!.git",
        "--iglob",
        "!.env*",
    ]
    if request["glob"]:
        glob = request["glob"]
        if "/" in glob.removeprefix("**/"):
            # The filter below matches file names only, at any depth, so a folder in it would
            # match nothing and read as "No matches." Name an exact retry only for
            # `folder/**/name`, the one form a path plus a file-name glob searches exactly.
            parent, _, name = glob.rpartition("/")
            folders, recursive = parent.removesuffix("/**"), parent.endswith("/**")
            if (
                recursive
                and folders
                and not folders.startswith(("/", "**"))
                and not any(c in folders for c in "*?[{")
            ):
                # From where this search started: a relative path would resolve from the repository.
                raise Refused(
                    "invalid",
                    f"The glob {glob!r} names folders, but globs here match file names only. "
                    f"Give the folder as the path instead: path={os.path.join(path, folders)!r} with the glob {name!r}.",
                )
            raise Refused(
                "invalid",
                f"The glob {glob!r} names folders, but globs here match file names only, at any "
                "depth below path. Give a folder as path and a file-name glob, and check the matches' folders.",
            )
        # Type filters, unlike positive -g globs, do not override ignore rules.
        args += ["--type-add", "wavefunction:" + request["glob"], "--type", "wavefunction"]
    if request["mode"] == "files":
        args += ["--files"]
    else:
        args += ["--line-number", "--with-filename", "--max-columns", "500", "--max-columns-preview"]
        if request.get("case_insensitive"):
            args += ["--ignore-case"]
        if request.get("context_lines"):
            args += ["--context", str(request["context_lines"])]
        args += ["-e", request["query"]]
    args += ["--", path]
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors)
        assert process.stdout is not None
        shown, used, more, exhausted = [], 0, False, False
        try:
            index = 0
            while True:
                raw = process.stdout.readline(2048)
                if not raw:
                    exhausted = True
                    break
                clipped = not raw.endswith(b"\n")
                if clipped:
                    while True:
                        tail = process.stdout.readline(2048)
                        if not tail or tail.endswith(b"\n"):
                            break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if clipped:
                    line += " [snippet shortened; read this file for the full line]"
                if index >= offset:
                    if len(shown) >= limit or used + len(line) > 8000:
                        more = True
                        break
                    shown.append(line)
                    used += len(line) + 1
                index += 1
        finally:
            if not exhausted and process.poll() is None:
                process.terminate()
            process.wait()
            process.stdout.close()
        errors.seek(0)
        error = errors.read(2000).decode("utf-8", errors="replace").strip()
    # ripgrep exits 2 when any path errored, matches or not: an unreadable folder must not throw
    # away what was found elsewhere. With nothing found, the error is the answer.
    if not more and process.returncode not in (0, 1) and not shown:
        raise Refused("search", f"Repository search failed: {error or 'ripgrep did not finish.'}")
    text = "\n".join(shown) if shown else "No matches."
    if more:
        text += f"\n[More results: continue with offset={offset + len(shown)}, or narrow the search.]"
    if error:
        text += f"\n[Some paths could not be searched: {error}]"
    return {"text": text}


def instructions(request: dict) -> dict:
    """The AGENTS.md files from the nearest repository root down to the folder, within a budget.

    Without a repository marker above it, the folder's own AGENTS.md alone.
    """
    folder = Path(request["path"]).resolve()
    root = next((p for p in (folder, *folder.parents) if (p / ".git").exists()), folder)
    chain = [folder]
    while chain[-1] != root:
        chain.append(chain[-1].parent)
    budget, parts, complete = 32000, [], True
    for directory in reversed(chain):
        path = directory / "AGENTS.md"
        try:
            with path.open(encoding="utf-8", errors="replace") as source:
                text = source.read(budget + 1)
        except FileNotFoundError:
            continue
        except OSError as error:
            complete = False
            parts.append(f"Repository instructions could not be read: {path}: {error}")
            continue
        parts.append(f"\nRepository instructions from {path}:\n{text[:budget]}")
        if len(text) > budget:
            parts.append(
                f"[Instructions truncated: use read_file(path={str(path)!r}, offset={budget}) to continue before editing.]"
            )
        budget = max(0, budget - min(len(text), budget))
    return {"text": "\n".join(parts), "complete": complete}


def review_snapshot(request: dict) -> dict:
    """Bounded review evidence, read inside the command's actual permission boundary."""

    def fingerprint(info):
        return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode]

    root = Path(request["root"]).resolve()
    paths = request.get("paths")
    known = request.get("known", {})
    # Reread fresh timestamps on coarse filesystems, as well as every changed stat.
    stable_before = time.time_ns() - 1_000_000_000
    warnings = []
    if paths is None:
        try:
            result = subprocess.run(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "."],
                cwd=root,
                capture_output=True,
                timeout=10,
            )
            if result.returncode:
                raise ValueError("Not a Git repository")
            paths = [os.fsdecode(p) for p in result.stdout.split(b"\0") if p]
        except (ValueError, OSError, subprocess.TimeoutExpired):
            paths = []
            for parent, directories, files in os.walk(
                root,
                followlinks=False,
                onerror=lambda error: warnings.append(f"Could not capture folder: {error}"),
            ):
                directories[:] = sorted(
                    d
                    for d in directories
                    if d not in {".git", "node_modules", ".venv", "target", "dist", "build", "__pycache__"}
                    and not (Path(parent) / d).is_symlink()
                )
                paths.extend(str((Path(parent) / name).relative_to(root)) for name in sorted(files))
                if len(paths) > 2000:
                    break
    paths = sorted(set(paths))
    if len(paths) > 2000:
        warnings.append("Review snapshots are limited to 2,000 files; additional files were omitted.")
    files, budget = {}, 16 * 1024 * 1024
    for relative in paths[:2000]:
        path = root / relative
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            warnings.append(f"Skipped linked/outside file: {relative}")
            continue
        if any(
            part.casefold() == ".git" or part.casefold().startswith(".env") for part in Path(relative).parts
        ):
            continue
        try:
            info = _regular(str(path))
            if info is None:
                files[relative] = {"text": None, "version": "absent", "kind": "absent"}
                continue
            if info.st_size > 512 * 1024 or info.st_size > budget:
                files[relative] = {
                    "text": None,
                    "version": f"{info.st_size}:{info.st_mtime_ns}",
                    "kind": "large",
                }
                continue
            signature = fingerprint(info)
            if known.get(relative) == signature and max(info.st_mtime_ns, info.st_ctime_ns) < stable_before:
                files[relative] = {"cached": True}
                budget -= info.st_size
                continue
            with path.open("rb") as f:
                raw = f.read(512 * 1024 + 1)
                final = os.fstat(f.fileno())
            if (
                len(raw) > 512 * 1024
                or fingerprint(final) != signature
                or fingerprint(path.stat()) != signature
            ):
                warnings.append(f"File changed during review capture: {relative}")
                continue
            budget -= len(raw)
            binary = b"\0" in raw[:SNIFF]
            files[relative] = {
                "text": None if binary else _decode(raw)[0],
                "version": hashlib.sha256(raw).hexdigest(),
                "kind": "binary" if binary else "text",
                "data": base64.b64encode(raw).decode(),
                "mode": stat.S_IMODE(info.st_mode),
                "stat": signature,
            }
        except (OSError, Refused) as error:
            warnings.append(f"Could not capture {relative}: {error}")
    return {"files": files, "warnings": warnings}


def git_operation(request):
    from .coding_git_ops import operate

    try:
        return operate(request)
    except (ValueError, subprocess.TimeoutExpired) as error:
        raise Refused("changed", str(error)) from None


def directory_tree(request):
    root, target = Path(request["root"]), Path(request["path"])
    return {
        "entries": sorted(
            [
                {"name": f.name, "path": str(f.relative_to(root)), "directory": f.is_dir()}
                for f in target.iterdir()
                if f.name not in {".git", "node_modules", ".venv", "__pycache__", "target", ".DS_Store"}
                and f.resolve().is_relative_to(root)
            ],
            key=lambda f: (not f["directory"], f["name"].lower()),
        )[:1000]
    }


def context_files(request):
    root = Path(request["root"]).resolve()
    query = request.get("query", "").casefold()
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        timeout=15,
    )
    if result.returncode == 0:
        names = sorted(set(os.fsdecode(p) for p in result.stdout.split(b"\0") if p))
    else:
        names = []
        for parent, directories, files in os.walk(root, followlinks=False):
            directories[:] = [
                d
                for d in directories
                if d not in {".git", ".venv", "node_modules", "target", "dist", "__pycache__"}
                and not (Path(parent) / d).is_symlink()
            ]
            names.extend(str((Path(parent) / name).relative_to(root)) for name in files)
            if len(names) >= 10000:
                break
    safe = [
        name
        for name in names
        if query in name.casefold()
        and not any(p.startswith(".env") or p == ".git" for p in Path(name).parts)
        and (root / name).resolve().is_relative_to(root)
        and not (root / name).is_symlink()
    ]
    return {"files": safe[:200], "more": len(safe) > 200}


def _spelled(root: Path, relative: str) -> bool:
    """Whether each folder and the file in `relative` exist under exactly these names. On a
    case-insensitive volume, `exists()` also finds a name that differs only in case, which is
    what a case-only rename leaves behind. Compared in one Unicode form, as the volume and Git
    each spell an accented name their own way."""
    folder = root
    for part in Path(relative).parts:
        if unicodedata.normalize("NFC", part) not in {
            unicodedata.normalize("NFC", name) for name in os.listdir(folder)
        }:
            return False
        folder = folder / part
    return True


def restore_files(request):
    """Preflight every version, then restore exact bytes; compensate a failed batch."""
    root = Path(request["root"]).resolve()
    changes = request["files"]
    max_bytes = min(request.get("max_bytes", 512 * 1024), 16 * 1024 * 1024)
    originals, written = {}, []
    # Paths this restore removes, by the name a case-insensitive volume also matches.
    removed = {
        unicodedata.normalize("NFC", change["path"]).casefold()
        for change in changes
        if change["data"] is None
    }

    def inspect(relative):
        path = root / relative
        if (
            Path(relative).is_absolute()
            or not path.resolve().is_relative_to(root)
            or any(p == ".git" for p in Path(relative).parts)
            or path.is_symlink()
        ):
            raise Refused("changed", "Cannot restore a linked or outside file.")
        # A name that exists only in another letter case is absent when this restore removes that
        # other spelling, as for a case-only rename. Otherwise it is a file in the way.
        if (
            not path.exists()
            or not _spelled(root, relative)
            and unicodedata.normalize("NFC", relative).casefold() in removed
        ):
            return path, None, "absent", None
        info = _regular(str(path))
        assert info is not None
        if info.st_size > max_bytes:
            raise Refused("changed", f"{relative} is too large to restore safely.")
        raw = path.read_bytes()
        return path, raw, hashlib.sha256(raw).hexdigest(), stat.S_IMODE(info.st_mode)

    def save(relative, expected, data, mode):
        path, raw, version, current_mode = inspect(relative)
        if version != expected or (
            request.get("check_modes") and current_mode != request["check_modes"].get(relative)
        ):
            raise Refused(
                "changed", f"{relative} changed since this patch. Keep the current edits and refresh."
            )
        if data is None:
            if raw is not None:
                with path.open("rb") as source:
                    fcntl.flock(source.fileno(), fcntl.LOCK_EX)
                    if inspect(relative)[2:] != (expected, current_mode):
                        raise _raced(str(path))
                    path.unlink()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if raw is None:
            _save(str(path), data, mode=mode)
        else:
            with path.open("rb") as source:
                fcntl.flock(source.fileno(), fcntl.LOCK_EX)
                _save(str(path), data, lambda: inspect(relative)[2:] == (expected, current_mode), mode=mode)

    for change in changes:
        relative = change["path"]
        _, raw, version, mode = inspect(relative)
        if request.get("resume") and version == change["target"] and mode == change.get("mode"):
            continue
        if version != change["expected"] or ("expected_mode" in change and mode != change["expected_mode"]):
            raise Refused("changed", f"{relative} changed since this patch. No files were restored.")
        target = base64.b64decode(change["data"], validate=True) if change["data"] is not None else None
        if target is not None and (
            len(target) > max_bytes or hashlib.sha256(target).hexdigest() != change["target"]
        ):
            raise Refused("changed", "Saved recovery content is incomplete.")
        originals[relative] = (raw, version, mode, target)
    if request.get("validate_only"):
        return {"validated": list(originals)}
    try:
        # Removals first: a case-only rename creates the new name after the old one is gone.
        for change in sorted(changes, key=lambda change: change["data"] is not None):
            relative = change["path"]
            if relative not in originals:
                continue
            save(relative, change["expected"], originals[relative][3], change.get("mode"))
            written.append(change)
    except (OSError, Refused):
        for change in reversed(written):
            raw, version, mode, _ = originals[change["path"]]
            # Compensation also compares versions; never overwrite an external save.
            with suppress(OSError, Refused):
                save(change["path"], change["target"], raw, mode)
        raise
    return {"restored": [c["path"] for c in changes]}


OPERATIONS = {
    "read": read,
    "write": write,
    "edit": edit,
    "search": search,
    "instructions": instructions,
    "review_snapshot": review_snapshot,
    "restore_files": restore_files,
    "git": git_operation,
    "context_files": context_files,
    "directory_tree": directory_tree,
}


def run(request: dict) -> dict:
    """One operation's result, or why it was refused. Anything unforeseen raises."""
    try:
        return {"ok": True, **OPERATIONS[request["op"]](request)}
    except Refused as refusal:
        return {"ok": False, "error": refusal.kind, "message": str(refusal)}
    except FileNotFoundError as error:
        return {
            "ok": False,
            "error": "not_found",
            "message": f"File not found: {error.filename or request.get('path')}",
        }
    except PermissionError as error:
        path = error.filename or request.get("path")
        if error.errno == errno.EACCES and request["op"] in {"write", "edit"}:
            # The file's own permissions said no; the sandbox's refusal is EPERM.
            return {
                "ok": False,
                "error": "read_only",
                "message": f"{path} is read-only: its file permissions don't allow changes, "
                "and granting folder access won't change that.",
            }
        return {"ok": False, "error": "denied", "message": f"Permission denied: {path}"}
    except OSError as error:
        return {
            "ok": False,
            "error": "os",
            "message": f"{error.strerror or error}: {error.filename or request.get('path')}",
        }


def main() -> None:
    sys.stdout.write(json.dumps(run(json.loads(sys.stdin.buffer.read()))))


if __name__ == "__main__":
    main()
