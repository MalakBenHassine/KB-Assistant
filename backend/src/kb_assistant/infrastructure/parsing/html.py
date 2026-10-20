"""HTML: the readable content, without scripts, navigation or chrome.

Parsed with the standard library parser through BeautifulSoup. Nothing is
fetched: images, stylesheets, iframes and links stay unresolved strings, so a
crafted page cannot make the worker issue requests (SSRF, SECURITY.md T7).
"""

from bs4 import BeautifulSoup, Tag

from kb_assistant.domain.documents import Block, BlockKind, ParsedDocument

_DROP = (
    "script",
    "style",
    "noscript",
    "template",
    "nav",
    "footer",
    "aside",
    "form",
    "iframe",
    "svg",
    "head",
    "button",
)
_HEADINGS = {f"h{n}": n for n in range(1, 7)}
_TARGETS = (*_HEADINGS, "p", "li", "pre", "tr", "blockquote", "dt", "dd")


def _block(element: Tag) -> Block | None:
    name = element.name
    if name in _HEADINGS:
        text = element.get_text(" ", strip=True)
        return Block(BlockKind.HEADING, text, level=_HEADINGS[name]) if text else None
    if name == "pre":
        text = element.get_text().strip("\n")
        return Block(BlockKind.CODE, text) if text.strip() else None
    if name == "tr":
        cells = [c.get_text(" ", strip=True) for c in element.find_all(["td", "th"])]
        cells = [c for c in cells if c]
        return Block(BlockKind.TABLE_ROW, " | ".join(cells)) if cells else None
    text = element.get_text(" ", strip=True)
    if not text:
        return None
    kind = BlockKind.LIST_ITEM if name == "li" else BlockKind.PARAGRAPH
    return Block(kind, text)


def parse_html(text: str) -> ParsedDocument:
    soup = BeautifulSoup(text, "html.parser")
    for element in soup.find_all(_DROP):
        element.decompose()

    blocks: list[Block] = []
    for element in soup.find_all(_TARGETS):
        # A container whose text is already covered by an inner target (a <li>
        # holding a <p>) would be emitted twice; headings are always kept.
        if element.name not in _HEADINGS and element.find(_TARGETS):
            continue
        if element.find_parent("pre") is not None:
            continue
        block = _block(element)
        if block is not None:
            blocks.append(block)

    if not blocks:
        # Text placed directly in <div>s, with none of the usual elements.
        body = soup.body or soup
        for part in body.get_text("\n").split("\n\n"):
            if part.strip():
                blocks.append(Block(BlockKind.PARAGRAPH, part.strip()))
    return ParsedDocument(blocks=tuple(blocks), page_count=None)
