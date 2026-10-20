"""Document upload and management.

Uploads answer `202 Accepted` with the version in `pending`: ingestion runs in
the worker, and the client polls the document to follow its status. Uploading
bytes that are already there answers `200` with the existing version.
"""

import re
import unicodedata
from pathlib import PurePosixPath
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status

from kb_assistant.api.deps import ContainerDep, PrincipalDep, UowDep
from kb_assistant.api.document_schemas import DocumentDetailOut, DocumentOut, VersionOut
from kb_assistant.api.schemas import Page
from kb_assistant.application.documents import (
    GetDocument,
    ListDocuments,
    RetryIngestion,
    UploadDocument,
    UploadNewVersion,
    UploadResult,
)
from kb_assistant.domain.errors import DocumentTooLargeError
from kb_assistant.domain.ids import DepartmentId, DocumentId, VersionId

router = APIRouter(prefix="/v1/documents", tags=["documents"])

_READ_CHUNK = 1024 * 1024
_UNSAFE_FILENAME = re.compile(r"[\x00-\x1f\x7f<>:\"\\|?*]")
_UPLOAD_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"description": "Identical content already uploaded; the existing version is returned"},
    413: {"description": "File too large"},
    415: {"description": "Not a PDF, DOCX, Markdown or HTML file"},
}


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read at most `max_bytes`, without trusting any declared length."""
    parts: list[bytes] = []
    size = 0
    while chunk := await file.read(_READ_CHUNK):
        size += len(chunk)
        if size > max_bytes:
            raise DocumentTooLargeError(f"Files are limited to {max_bytes // (1024 * 1024)} MiB.")
        parts.append(chunk)
    return b"".join(parts)


def _safe_filename(raw: str | None) -> str:
    """Keep only the base name, without path tricks or control characters."""
    name = unicodedata.normalize("NFC", raw or "upload")
    name = PurePosixPath(name.replace("\\", "/")).name
    name = _UNSAFE_FILENAME.sub("_", name).strip(" .")
    return name[:255] or "upload"


def _respond(response: Response, result: UploadResult) -> DocumentOut:
    response.status_code = status.HTTP_202_ACCEPTED if result.created else status.HTTP_200_OK
    response.headers["Location"] = f"/v1/documents/{result.document.id}"
    return DocumentOut.of(result.document, result.version)


@router.post(
    "",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_UPLOAD_RESPONSES,
)
async def upload_document(
    response: Response,
    principal: PrincipalDep,
    uow: UowDep,
    c: ContainerDep,
    file: Annotated[UploadFile, File(description="PDF, DOCX, Markdown or HTML")],
    department_id: Annotated[UUID, Form()],
    title: Annotated[str | None, Form(max_length=300)] = None,
) -> DocumentOut:
    data = await _read_capped(file, c.upload_policy.max_bytes)
    result = await UploadDocument(uow, c.blobs, c.jobs, c.clock, c.upload_policy).execute(
        principal,
        department_id=DepartmentId(department_id),
        title=title,
        filename=_safe_filename(file.filename),
        data=data,
    )
    return _respond(response, result)


@router.post(
    "/{document_id}/versions",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_UPLOAD_RESPONSES,
)
async def upload_version(
    document_id: UUID,
    response: Response,
    principal: PrincipalDep,
    uow: UowDep,
    c: ContainerDep,
    file: Annotated[UploadFile, File(description="PDF, DOCX, Markdown or HTML")],
) -> DocumentOut:
    data = await _read_capped(file, c.upload_policy.max_bytes)
    result = await UploadNewVersion(uow, c.blobs, c.jobs, c.clock, c.upload_policy).execute(
        principal, DocumentId(document_id), filename=_safe_filename(file.filename), data=data
    )
    return _respond(response, result)


@router.get("", response_model=Page[DocumentOut])
async def list_documents(
    principal: PrincipalDep,
    uow: UowDep,
    department_id: Annotated[UUID | None, Query()] = None,
) -> Page[DocumentOut]:
    rows = await ListDocuments(uow).execute(
        principal, department_id=DepartmentId(department_id) if department_id else None
    )
    return Page(items=[DocumentOut.of(d, v) for d, v in rows])


@router.get("/{document_id}", response_model=DocumentDetailOut)
async def get_document(
    document_id: UUID, principal: PrincipalDep, uow: UowDep
) -> DocumentDetailOut:
    detail = await GetDocument(uow).execute(principal, DocumentId(document_id))
    return DocumentDetailOut.of_detail(detail)


@router.post(
    "/{document_id}/versions/{version_id}/retry",
    response_model=VersionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_ingestion(
    document_id: UUID, version_id: UUID, principal: PrincipalDep, uow: UowDep, c: ContainerDep
) -> VersionOut:
    version = await RetryIngestion(uow, c.jobs).execute(
        principal, DocumentId(document_id), VersionId(version_id)
    )
    return VersionOut.of(version)
