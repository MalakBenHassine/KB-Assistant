"""PDF: text per page, with page numbers kept for citations.

Text extraction only; nothing embedded in the PDF (JavaScript, forms,
attachments) is ever executed or opened. Scanned PDFs yield no text and fail
as `empty_document` further down the pipeline (OCR is future work).
"""

import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from kb_assistant.domain.documents import Block, BlockKind, ParsedDocument
from kb_assistant.domain.errors import UnreadableDocumentError

MAX_PAGES = 2_000
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def _page_blocks(text: str, page: int) -> list[Block]:
    """First and last lines are separate blocks, so running headers/footers can be detected."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    if len(lines) <= 2:
        return [Block(BlockKind.PARAGRAPH, line, page=page) for line in lines]
    body = "\n".join(lines[1:-1])
    blocks = [Block(BlockKind.PARAGRAPH, lines[0], page=page)]
    blocks += [
        Block(BlockKind.PARAGRAPH, part, page=page)
        for part in _PARAGRAPH_BREAK.split(body)
        if part.strip()
    ]
    blocks.append(Block(BlockKind.PARAGRAPH, lines[-1], page=page))
    return blocks


def parse_pdf(data: bytes) -> ParsedDocument:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise UnreadableDocumentError("The PDF is password-protected.")
        if len(reader.pages) > MAX_PAGES:
            raise UnreadableDocumentError(f"PDFs are limited to {MAX_PAGES} pages.")
        blocks: list[Block] = []
        for number, page in enumerate(reader.pages, start=1):
            blocks.extend(_page_blocks(page.extract_text() or "", number))
        return ParsedDocument(blocks=tuple(blocks), page_count=len(reader.pages))
    except UnreadableDocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        raise UnreadableDocumentError("The PDF is damaged or not a valid PDF.") from exc
