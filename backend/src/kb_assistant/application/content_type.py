"""Decide what a file really is from its bytes (SECURITY.md, T7).

The extension and the declared Content-Type are claims made by the client.
The content decides; a claim that contradicts the content is rejected rather
than silently corrected, because a mismatch is how many upload attacks start.
"""

import io
import re
import zipfile
from pathlib import PurePosixPath

from kb_assistant.domain.documents import MimeType
from kb_assistant.domain.errors import UnsupportedDocumentError

_EXTENSIONS = {
    ".pdf": MimeType.PDF,
    ".docx": MimeType.DOCX,
    ".md": MimeType.MARKDOWN,
    ".markdown": MimeType.MARKDOWN,
    ".txt": MimeType.MARKDOWN,  # plain text is valid Markdown
    ".html": MimeType.HTML,
    ".htm": MimeType.HTML,
}
_HTML_SIGNATURE = re.compile(r"^\s*(<!doctype\s+html|<html[\s>]|<head[\s>]|<body[\s>])", re.I)
_SUPPORTED = "PDF, DOCX, Markdown or HTML"


def _unsupported(reason: str) -> UnsupportedDocumentError:
    return UnsupportedDocumentError(f"{reason} Supported formats: {_SUPPORTED}.")


def _is_docx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return "word/document.xml" in archive.namelist()
    except zipfile.BadZipFile:
        return False


def detect_mime_type(data: bytes, filename: str) -> MimeType:
    claimed = _EXTENSIONS.get(PurePosixPath(filename.lower()).suffix)

    if data.startswith(b"%PDF-"):
        detected = MimeType.PDF
    elif data.startswith(b"PK\x03\x04"):
        if not _is_docx(data):
            raise _unsupported("This archive is not a Word document.")
        detected = MimeType.DOCX
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise _unsupported(
                "The file is neither a known binary format nor UTF-8 text."
            ) from None
        if "\x00" in text:
            raise _unsupported("The file contains binary data.")
        if _HTML_SIGNATURE.match(text[:2048]) or claimed is MimeType.HTML:
            detected = MimeType.HTML
        elif claimed is MimeType.MARKDOWN:
            detected = MimeType.MARKDOWN
        else:
            raise _unsupported("Text files must be named .md, .markdown, .txt, .html or .htm.")

    if claimed is not None and claimed is not detected:
        raise _unsupported(f"The file name says {claimed.name} but the content is {detected.name}.")
    if claimed is None:
        raise _unsupported("The file name has no supported extension.")
    return detected
