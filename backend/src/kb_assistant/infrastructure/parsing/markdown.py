"""Markdown: ATX and setext headings, paragraphs, lists, fenced code, tables.

A small line-oriented parser rather than a full CommonMark renderer: chunking
needs structure, not HTML, and every rule here is visible and tested.
"""

import re

from kb_assistant.domain.documents import Block, BlockKind, ParsedDocument

_ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_SETEXT = re.compile(r"^(=+|-+)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|(.*)\|\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _strip_front_matter(lines: list[str]) -> list[str]:
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                return lines[i + 1 :]
    return lines


def parse_markdown(text: str) -> ParsedDocument:
    lines = _strip_front_matter(text.replace("\r\n", "\n").split("\n"))
    blocks: list[Block] = []
    paragraph: list[str] = []
    code: list[str] | None = None
    fence = ""

    def flush() -> None:
        if paragraph:
            blocks.append(Block(BlockKind.PARAGRAPH, "\n".join(paragraph)))
            paragraph.clear()

    for line in lines:
        if code is not None:
            if line.strip().startswith(fence):
                blocks.append(Block(BlockKind.CODE, "\n".join(code)))
                code = None
            else:
                code.append(line)
            continue
        if match := _FENCE.match(line):
            flush()
            fence, code = match.group(1), []
            continue
        if match := _ATX.match(line):
            flush()
            blocks.append(Block(BlockKind.HEADING, match.group(2), level=len(match.group(1))))
            continue
        if _SETEXT.match(line) and len(paragraph) == 1:
            level = 1 if line.strip().startswith("=") else 2
            blocks.append(Block(BlockKind.HEADING, paragraph.pop(), level=level))
            continue
        if _TABLE_SEPARATOR.match(line) and "|" in line:
            continue
        if match := _TABLE_ROW.match(line):
            flush()
            cells = [c.strip() for c in match.group(1).split("|")]
            blocks.append(Block(BlockKind.TABLE_ROW, " | ".join(c for c in cells if c)))
            continue
        if match := _LIST_ITEM.match(line):
            flush()
            blocks.append(Block(BlockKind.LIST_ITEM, match.group(1)))
            continue
        if not line.strip():
            flush()
            continue
        if blocks and blocks[-1].kind is BlockKind.LIST_ITEM and line.startswith((" ", "\t")):
            # Indented continuation of the previous list item.
            last = blocks.pop()
            blocks.append(Block(BlockKind.LIST_ITEM, f"{last.text} {line.strip()}"))
            continue
        paragraph.append(line)

    if code is not None:  # an unclosed fence keeps its content
        blocks.append(Block(BlockKind.CODE, "\n".join(code)))
    flush()
    return ParsedDocument(blocks=tuple(blocks), page_count=None)
