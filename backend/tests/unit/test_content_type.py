"""The bytes decide the type; a name that contradicts them is rejected (SECURITY.md, T7)."""

import io
import zipfile

import pytest

from kb_assistant.application.content_type import detect_mime_type
from kb_assistant.domain.documents import MimeType
from kb_assistant.domain.errors import UnsupportedDocumentError
from tests.sample_documents import LEAVE_POLICY_MD, ONCALL_RUNBOOK_HTML, handbook_pdf, make_docx


@pytest.mark.parametrize(
    ("data", "name", "expected"),
    [
        (handbook_pdf(), "handbook.pdf", MimeType.PDF),
        (make_docx(), "expenses.docx", MimeType.DOCX),
        (LEAVE_POLICY_MD.encode(), "leave.md", MimeType.MARKDOWN),
        (b"plain text is markdown", "notes.txt", MimeType.MARKDOWN),
        (ONCALL_RUNBOOK_HTML.encode(), "runbook.html", MimeType.HTML),
        (b"\xef\xbb\xbf# With a BOM", "bom.md", MimeType.MARKDOWN),
    ],
)
def test_supported_files_are_recognised(data: bytes, name: str, expected: MimeType) -> None:
    assert detect_mime_type(data, name) is expected


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (ONCALL_RUNBOOK_HTML.encode(), "runbook.pdf"),  # extension spoofing
        (handbook_pdf(), "handbook.docx"),
        (b"MZ\x90\x00\x03binary", "setup.pdf"),  # an executable
        (b"\xff\xfe\x00\x00not utf8", "notes.md"),
        (b"# fine markdown", "notes.exe"),
        (b"# fine markdown", "README"),
        (b"text with a \x00 byte", "notes.md"),
    ],
)
def test_unsupported_or_disguised_files_are_rejected(data: bytes, name: str) -> None:
    with pytest.raises(UnsupportedDocumentError):
        detect_mime_type(data, name)


def test_a_zip_that_is_not_a_word_document_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("payload.sh", "rm -rf /")
    with pytest.raises(UnsupportedDocumentError, match="not a Word document"):
        detect_mime_type(buffer.getvalue(), "report.docx")
