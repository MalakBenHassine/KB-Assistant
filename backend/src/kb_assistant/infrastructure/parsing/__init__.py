"""Turn uploaded bytes into structural blocks. Offline only: nothing here fetches anything."""

from kb_assistant.domain.documents import MimeType, ParsedDocument
from kb_assistant.domain.errors import UnreadableDocumentError
from kb_assistant.infrastructure.parsing.docx import parse_docx
from kb_assistant.infrastructure.parsing.html import parse_html
from kb_assistant.infrastructure.parsing.markdown import parse_markdown
from kb_assistant.infrastructure.parsing.pdf import parse_pdf


def _text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UnreadableDocumentError("The file is not valid UTF-8 text.") from exc


class DocumentParserImpl:
    def parse(self, data: bytes, mime_type: MimeType) -> ParsedDocument:
        match mime_type:
            case MimeType.PDF:
                return parse_pdf(data)
            case MimeType.DOCX:
                return parse_docx(data)
            case MimeType.MARKDOWN:
                return parse_markdown(_text(data))
            case MimeType.HTML:
                return parse_html(_text(data))
