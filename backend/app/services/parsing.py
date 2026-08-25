"""
Format-specific parsing. Each parser's job is narrow: turn file bytes into
an ordered list of (text, section_ref) pairs. section_ref is what makes
traceability readable to a human reviewer later ("page 2", "Section 4.2")
rather than just an opaque chunk id.

Chunking is by paragraph/block with a soft character cap, not by fixed
token windows - contract clauses and invoice line items are the natural
unit of meaning here, and splitting mid-clause would break the very
traceability this system exists to provide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import docx
import fitz  # PyMuPDF

_MAX_CHUNK_CHARS = 1500


@dataclass(frozen=True)
class RawChunk:
    content: str
    section_ref: str | None


def _split_long_block(text: str, section_ref: str | None) -> list[RawChunk]:
    if len(text) <= _MAX_CHUNK_CHARS:
        return [RawChunk(text, section_ref)]
    parts = []
    for i in range(0, len(text), _MAX_CHUNK_CHARS):
        parts.append(RawChunk(text[i : i + _MAX_CHUNK_CHARS], section_ref))
    return parts


def parse_pdf(data: bytes) -> list[RawChunk]:
    chunks: list[RawChunk] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page_index, page in enumerate(doc, start=1):
            page_chunks = _parse_pdf_page_by_blocks(page, page_index)
            if not page_chunks:
                # Fallback for the rare page where block detection finds
                # nothing usable but plain text extraction does - better
                # one oversized chunk than silently dropping the page.
                page_chunks = _parse_pdf_page_by_blank_lines(page, page_index)
            chunks.extend(page_chunks)
    return chunks


def _parse_pdf_page_by_blocks(page, page_index: int) -> list[RawChunk]:
    """PyMuPDF's block detection is based on the PDF's actual visual
    layout (distinct positioned text regions), not on whether newline
    characters happen to survive plain-text extraction - it correctly
    separates paragraphs even in PDFs built from directly-positioned text
    runs, where flat "text" mode collapses everything onto single
    newlines and a blank-line-based split would return the whole page as
    one chunk.
    """
    chunks: list[RawChunk] = []
    blocks = page.get_text("blocks")
    for block in blocks:
        x0, y0, x1, y1, text, block_no, block_type = block[:7]
        if block_type != 0:  # 0 = text block; images and other types are skipped
            continue
        text = text.strip()
        if text:
            chunks.extend(_split_long_block(text, f"page {page_index}"))
    return chunks


def _parse_pdf_page_by_blank_lines(page, page_index: int) -> list[RawChunk]:
    chunks: list[RawChunk] = []
    text = page.get_text("text").strip()
    if not text:
        return chunks
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if block:
            chunks.extend(_split_long_block(block, f"page {page_index}"))
    return chunks


def parse_docx(data: bytes) -> list[RawChunk]:
    import io

    document = docx.Document(io.BytesIO(data))
    chunks: list[RawChunk] = []
    current_heading = None
    for i, para in enumerate(document.paragraphs):
        text = para.text.strip()
        if not text:
            continue
        if para.style is not None and para.style.name.lower().startswith("heading"):
            current_heading = text
        section_ref = current_heading or f"paragraph {i + 1}"
        chunks.extend(_split_long_block(text, section_ref))
    for t_index, table in enumerate(document.tables, start=1):
        for r_index, row in enumerate(table.rows, start=1):
            row_text = " | ".join(cell.text.strip() for cell in row.cells)
            if row_text.strip(" |"):
                chunks.append(RawChunk(row_text, f"table {t_index}, row {r_index}"))
    return chunks


def parse_txt(data: bytes) -> list[RawChunk]:
    text = data.decode("utf-8", errors="replace")
    chunks: list[RawChunk] = []
    for i, block in enumerate(re.split(r"\n\s*\n", text), start=1):
        block = block.strip()
        if block:
            chunks.extend(_split_long_block(block, f"block {i}"))
    return chunks


def parse_markdown(data: bytes) -> list[RawChunk]:
    text = data.decode("utf-8", errors="replace")
    chunks: list[RawChunk] = []
    current_heading = None
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        heading_match = re.match(r"^#{1,6}\s+(.+)$", block.splitlines()[0])
        if heading_match:
            current_heading = heading_match.group(1).strip()
        chunks.extend(_split_long_block(block, current_heading))
    return chunks


_PARSERS = {
    "pdf": parse_pdf,
    "docx": parse_docx,
    "txt": parse_txt,
    "md": parse_markdown,
}


def parse_document(data: bytes, doc_format: str) -> list[RawChunk]:
    parser = _PARSERS.get(doc_format)
    if parser is None:
        raise ValueError(f"Unsupported document format: {doc_format!r}")
    chunks = parser(data)
    if not chunks:
        raise ValueError("Parsing produced no extractable text - is the file empty or a scanned image?")
    return chunks
