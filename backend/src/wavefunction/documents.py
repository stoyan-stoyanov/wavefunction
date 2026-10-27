"""Read Word, spreadsheet, slide, and PDF references for uploads and connectors."""

from __future__ import annotations

from pathlib import Path
from typing import Any

PREVIEW_ROWS = 200
PREVIEW_COLUMNS = 30


def xlsx_to_sheets(path: Path, *, preview: bool = True, max_rows: int | None = None) -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    # Agent reads include formulas, including cells without a cached calculation.
    book = load_workbook(str(path), read_only=True, data_only=preview)
    sheets = []
    for page in book.worksheets:
        rows: list[list[Any]] = []
        for row in page.iter_rows(
            min_row=1,
            max_row=PREVIEW_ROWS if preview else max_rows,
            max_col=PREVIEW_COLUMNS if preview else None,
            values_only=True,
        ):
            values = ["" if value is None else value for value in row]
            # Trailing empty cells carry nothing; a ragged right edge reads better than a grid of blanks.
            while values and values[-1] == "":
                values.pop()
            rows.append(values)
        while rows and not rows[-1]:
            rows.pop()
        sheets.append(
            {
                "name": page.title,
                "rows": rows,
                "truncated": preview and (page.max_row > PREVIEW_ROWS or page.max_column > PREVIEW_COLUMNS),
            }
        )
    book.close()
    return sheets


def pptx_to_slides(path: Path) -> list[dict[str, Any]]:
    """Each slide as its title and body lines, with each line's indent level and whether the
    slide is a title slide, so the page can draw it the way the deck opens."""
    from pptx import Presentation

    deck = Presentation(str(path))
    slides = []
    for slide in deck.slides:
        title = ""
        lines: list[str] = []
        levels: list[int] = []
        layout_name = (slide.slide_layout.name or "").lower()
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            if shape == slide.shapes.title:
                title = shape.text_frame.text.strip()
                continue
            for paragraph in shape.text_frame.paragraphs:
                text = "".join(run.text for run in paragraph.runs).strip() or paragraph.text.strip()
                if not text:
                    continue
                lines.append(text)
                levels.append(int(paragraph.level or 0))
        slides.append(
            {
                "title": title,
                "lines": lines,
                "levels": levels,
                "layout": "title" if "title slide" in layout_name else "content",
            }
        )
    return slides


# ---- readers, for the agents --------------------------------------------------------------
#
# A file read back as Markdown, so an agent can revise what it or another agent made. Our own
# writers produce these files from the same grammar, so a document goes out and comes back
# unchanged; a file from elsewhere comes back as its text with the formatting it lost.


def docx_to_markdown(path: Path, *, include_title: bool = False) -> str:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.hyperlink import Hyperlink
    from docx.text.paragraph import Paragraph

    document = Document(str(path))
    lines: list[str] = []
    numbered = 0

    def inline(paragraph: Any) -> str:
        pieces = []
        # Runs and links in order: `paragraph.runs` leaves out the runs inside a link.
        for item in paragraph.iter_inner_content():
            for run in item.runs if isinstance(item, Hyperlink) else [item]:
                text = run.text
                if text.strip() and run.bold:
                    text = f"**{text.strip()}**" + (" " if text.endswith(" ") else "")
                pieces.append(text)
        return ("".join(pieces) or paragraph.text).strip()

    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            style = (paragraph.style.name if paragraph.style is not None else "") or ""
            text = inline(paragraph)
            if not text:
                continue
            if style == "Title" and not include_title:
                # The title is the artifact's own; the body starts after it.
                continue
            if style.startswith("Heading"):
                level = style.split()[-1]
                marks = "#" * (int(level) if level.isdigit() else 1)
                lines += [f"{marks} {text}", ""]
                numbered = 0
            elif style.startswith("List"):
                if "Number" in style:
                    numbered += 1
                    lines.append(f"{numbered}. {text}")
                else:
                    lines.append(f"- {text}")
            else:
                if lines and lines[-1].startswith(("- ", "1")) and lines[-1] != "":
                    lines.append("")
                lines += [text, ""]
                numbered = 0
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            rows = [[cell.text.strip().replace("|", "\\|") for cell in row.cells] for row in table.rows]
            if rows:
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append("| " + " | ".join(rows[0]) + " |")
                lines.append("|" + "---|" * len(rows[0]))
                for row in rows[1:]:
                    lines.append("| " + " | ".join(row) + " |")
                lines.append("")
    return "\n".join(lines).strip() + "\n"


def xlsx_to_markdown(path: Path, limit: int | None = None) -> str:
    """The workbook as Markdown tables.

    With `limit`, only as many rows as could fill that many characters are read from each
    sheet — every row is at least four characters — and later sheets are skipped once it is
    reached. A reader wanting the first page of a huge sheet then does not pay to expand the
    whole of it, and a small compressed file cannot become an unbounded expansion.
    """
    parts = []
    produced = 0
    for sheet in xlsx_to_sheets(path, preview=False, max_rows=None if limit is None else limit // 4 + 2):
        if limit is not None and produced >= limit:
            break
        rows = sheet["rows"]
        parts.append(f"## {sheet['name']}")
        if rows:
            width = max(len(row) for row in rows)

            def cells(row: list[Any], width: int = width) -> list[str]:
                return [str(row[c]) if c < len(row) else "" for c in range(width)]

            parts.append("| " + " | ".join(cells(rows[0])) + " |")
            parts.append("|" + "---|" * width)
            parts += ["| " + " | ".join(cells(row)) + " |" for row in rows[1:]]
        if sheet.get("truncated"):
            parts.append("\n_(more rows not shown)_")
        parts.append("")
        produced = sum(len(part) + 1 for part in parts)
    return "\n".join(parts).strip() + "\n"


def pptx_to_markdown(path: Path) -> str:
    parts = []
    for index, slide in enumerate(pptx_to_slides(path), start=1):
        parts.append(f"## {slide['title'] or f'Slide {index}'}")
        levels = slide.get("levels") or [0] * len(slide["lines"])
        for line, level in zip(slide["lines"], levels, strict=False):
            parts.append(("  " * level) + "- " + line)
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def artifact_text(path: Path, kind: str, limit: int | None = None) -> str:
    """A stored file as text an agent can read and revise.

    `limit` is how much of the text the caller will use. A workbook and a note honour it by
    not producing more than that; the other kinds are read whole, as their libraries need.
    """
    if kind == "docx":
        return docx_to_markdown(path)
    if kind == "xlsx":
        return xlsx_to_markdown(path, limit)
    if kind == "pptx":
        return pptx_to_markdown(path)
    if kind == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip() + "\n"
    if kind == "image":
        return ""
    with path.open(encoding="utf-8", errors="replace") as handle:
        return handle.read() if limit is None else handle.read(limit)


# ---- edits --------------------------------------------------------------------------------
