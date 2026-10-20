"""Normalise extracted text before chunking (ADR-0004, SECURITY.md T2).

Cleaning is part of the security boundary, not only cosmetics:
- zero-width and bidirectional control characters can hide instructions from a
  human reviewer while the model still reads them;
- a document must never be able to close the delimiter that will wrap it in
  the prompt (`<document ...>` in Phase 3), so that tag name is neutralised.
"""

import math
import re
import unicodedata
from collections import Counter

from kb_assistant.domain.documents import Block, ParsedDocument

_INVISIBLE = re.compile("[­​-‏‪-‮⁠-⁤⁦-⁩﻿]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PROMPT_DELIMITER = re.compile(r"<(\s*/?\s*)(document)\b", re.IGNORECASE)
_SPACES = re.compile("[ \t\u00a0]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_DIGITS = re.compile(r"\d+")

# A line repeated at the top or bottom of at least this share of pages is a
# running header or footer, not content.
_FURNITURE_MIN_SHARE = 0.5
_FURNITURE_MIN_PAGES = 3


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    text = _CONTROL.sub(" ", text)
    text = _PROMPT_DELIMITER.sub(lambda m: f"\u2039{m.group(1)}{m.group(2)}", text)
    lines = [_SPACES.sub(" ", line).strip() for line in text.replace("\r\n", "\n").split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _furniture_key(text: str) -> str:
    # "Page 3 of 12" and "Page 4 of 12" are the same footer.
    return _DIGITS.sub("#", text.lower())


def _repeated_page_furniture(blocks: tuple[Block, ...], page_count: int) -> set[str]:
    if page_count < _FURNITURE_MIN_PAGES:
        return set()
    edges: Counter[str] = Counter()
    by_page: dict[int, list[Block]] = {}
    for block in blocks:
        if block.page is not None:
            by_page.setdefault(block.page, []).append(block)
    for page_blocks in by_page.values():
        keys = {_furniture_key(page_blocks[0].text), _furniture_key(page_blocks[-1].text)}
        edges.update(keys)
    threshold = max(_FURNITURE_MIN_PAGES, math.ceil(page_count * _FURNITURE_MIN_SHARE))
    return {key for key, count in edges.items() if count >= threshold}


def clean_document(parsed: ParsedDocument) -> ParsedDocument:
    """Clean every block, drop empty ones and running headers/footers."""
    cleaned = tuple(
        Block(kind=b.kind, text=text, page=b.page, level=b.level)
        for b in parsed.blocks
        if (text := clean_text(b.text))
    )
    furniture = _repeated_page_furniture(cleaned, parsed.page_count or 0)
    if furniture:
        cleaned = tuple(b for b in cleaned if _furniture_key(b.text) not in furniture)
    return ParsedDocument(blocks=cleaned, page_count=parsed.page_count)
