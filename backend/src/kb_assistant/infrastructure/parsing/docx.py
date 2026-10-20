"""DOCX: headings, paragraphs, lists and tables, in document order.

A DOCX is a ZIP archive, so the archive is checked before python-docx opens
it: entry count, total uncompressed size and compression ratio, which is what
a zip bomb abuses (SECURITY.md, T7). python-docx parses XML with
`resolve_entities=False`, which closes XXE.
"""

import io
import re
import zipfile

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from kb_assistant.domain.documents import Block, BlockKind, ParsedDocument
from kb_assistant.domain.errors import UnreadableDocumentError

MAX_ENTRIES = 5_000
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
_HEADING_STYLE = re.compile(r"^Heading (\d)$")


def _check_archive(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise UnreadableDocumentError("The Word document is damaged.") from exc
    if len(entries) > MAX_ENTRIES:
        raise UnreadableDocumentError("The Word document has too many parts.")
    if sum(e.file_size for e in entries) > MAX_UNCOMPRESSED_BYTES:
        raise UnreadableDocumentError("The Word document is too large once decompressed.")
    for entry in entries:
        if entry.compress_size and entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO:
            raise UnreadableDocumentError("The Word document is suspiciously compressed.")


def _paragraph_block(paragraph: Paragraph) -> Block | None:
    text = paragraph.text.strip()
    if not text:
        return None
    style = paragraph.style.name if paragraph.style is not None else ""
    style = style or ""
    if style == "Title":
        return Block(BlockKind.HEADING, text, level=1)
    if match := _HEADING_STYLE.match(style):
        return Block(BlockKind.HEADING, text, level=int(match.group(1)))
    if "List" in style:
        return Block(BlockKind.LIST_ITEM, text)
    return Block(BlockKind.PARAGRAPH, text)


def _table_blocks(table: Table) -> list[Block]:
    blocks: list[Block] = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            value = cell.text.strip()
            # Merged cells are repeated by python-docx; keep each value once.
            if value and (not cells or cells[-1] != value):
                cells.append(value)
        if cells:
            blocks.append(Block(BlockKind.TABLE_ROW, " | ".join(cells)))
    return blocks


def parse_docx(data: bytes) -> ParsedDocument:
    _check_archive(data)
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises a variety of types for broken files
        raise UnreadableDocumentError("The Word document could not be read.") from exc
    blocks: list[Block] = []
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            block = _paragraph_block(Paragraph(child, document))
            if block is not None:
                blocks.append(block)
        elif child.tag == qn("w:tbl"):
            blocks.extend(_table_blocks(Table(child, document)))
    return ParsedDocument(blocks=tuple(blocks), page_count=None)
