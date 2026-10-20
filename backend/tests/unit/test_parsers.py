import pytest

from kb_assistant.domain.documents import BlockKind, MimeType
from kb_assistant.domain.errors import UnreadableDocumentError
from kb_assistant.infrastructure.parsing import DocumentParserImpl
from kb_assistant.infrastructure.parsing.html import parse_html
from kb_assistant.infrastructure.parsing.markdown import parse_markdown
from tests.sample_documents import (
    LEAVE_POLICY_MD,
    ONCALL_RUNBOOK_HTML,
    handbook_pdf,
    make_docx,
    make_pdf,
    zip_bomb_docx,
)

parser = DocumentParserImpl()


def kinds(blocks: object) -> list[tuple[str, str]]:
    return [(b.kind.value, b.text) for b in blocks]  # type: ignore[attr-defined]


def test_markdown_structure() -> None:
    blocks = parse_markdown(LEAVE_POLICY_MD).blocks
    headings = [(b.level, b.text) for b in blocks if b.kind is BlockKind.HEADING]
    assert headings == [
        (1, "Leave policy"),
        (2, "Parental leave"),
        (3, "Eligibility"),
        (3, "How to apply"),
    ]
    assert "owner: hr" not in " ".join(b.text for b in blocks)  # front matter skipped
    assert (BlockKind.LIST_ITEM, "Submit the request in the HR portal.") in [
        (b.kind, b.text) for b in blocks
    ]
    assert (BlockKind.TABLE_ROW, "Annual | 25") in [(b.kind, b.text) for b in blocks]
    assert any(b.kind is BlockKind.CODE and "hr.example" in b.text for b in blocks)


def test_markdown_setext_headings_and_unclosed_fence() -> None:
    blocks = parse_markdown("Title\n=====\n\nSub\n---\n\n```\ncode never closed").blocks
    assert kinds(blocks) == [
        ("heading", "Title"),
        ("heading", "Sub"),
        ("code", "code never closed"),
    ]


def test_html_keeps_content_and_drops_chrome() -> None:
    blocks = parse_html(ONCALL_RUNBOOK_HTML).blocks
    text = " ".join(b.text for b in blocks)
    assert "steal()" not in text  # script
    assert "Runbooks" not in text  # nav
    assert "Engineering handbook" not in text  # footer
    assert "attacker.example" not in text  # images are not followed nor kept
    assert [b.text for b in blocks if b.kind is BlockKind.HEADING] == ["Database failover", "Steps"]
    items = [b.text for b in blocks if b.kind is BlockKind.LIST_ITEM]
    # A <li> wrapping a <p> is emitted once, not twice.
    assert text.count("Freeze deployments") == 1
    assert any("pg_ctl promote" in i for i in items)
    assert any(b.kind is BlockKind.CODE and "kubectl" in b.text for b in blocks)


def test_html_with_text_only_in_divs_still_yields_content() -> None:
    blocks = parse_html("<html><body><div>Just a div.</div></body></html>").blocks
    assert [b.text for b in blocks] == ["Just a div."]


def test_docx_headings_lists_and_tables() -> None:
    blocks = parser.parse(make_docx(), MimeType.DOCX).blocks
    assert ("heading", "Expense policy") in kinds(blocks)
    assert ("list_item", "Book trains for journeys under four hours.") in kinds(blocks)
    assert ("table_row", "Hotel | 150 EUR per night") in kinds(blocks)
    assert [b.level for b in blocks if b.kind is BlockKind.HEADING] == [1, 2]


def test_docx_zip_bomb_is_rejected_before_parsing() -> None:
    with pytest.raises(UnreadableDocumentError, match=r"compressed|large"):
        parser.parse(zip_bomb_docx(), MimeType.DOCX)


def test_pdf_text_keeps_page_numbers() -> None:
    parsed = parser.parse(handbook_pdf(), MimeType.PDF)
    assert parsed.page_count == 3
    by_page = {b.page for b in parsed.blocks if "Security training" in b.text}
    assert by_page == {3}


def test_pdf_with_parentheses_and_backslashes() -> None:
    parsed = parser.parse(make_pdf([["Cost (net) is 10\\20"]]), MimeType.PDF)
    assert "Cost (net) is 10\\20" in " ".join(b.text for b in parsed.blocks)


@pytest.mark.parametrize("data", [b"%PDF-1.4\nthis is not a pdf", b"%PDF-"])
def test_damaged_pdf_is_unreadable_not_a_crash(data: bytes) -> None:
    with pytest.raises(UnreadableDocumentError):
        parser.parse(data, MimeType.PDF)


def test_invalid_utf8_markdown_is_unreadable() -> None:
    with pytest.raises(UnreadableDocumentError):
        parser.parse(b"\xff\xfe broken", MimeType.MARKDOWN)
