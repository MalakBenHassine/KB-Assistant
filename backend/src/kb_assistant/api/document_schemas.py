"""Wire format for documents.

The blob key, the embedding token count and other internals are not exposed:
a client needs the state of a version and what to do about it, nothing more.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from kb_assistant.application.documents import DocumentDetail
from kb_assistant.domain.documents import Document, DocumentVersion, VersionStatus


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class IngestionErrorOut(_Out):
    code: str
    message: str
    retryable: bool


class VersionOut(_Out):
    id: UUID
    version: int
    status: VersionStatus
    filename: str
    mime_type: str
    size_bytes: int
    checksum: str
    chunk_count: int | None
    attempts: int
    error: IngestionErrorOut | None
    created_at: datetime
    indexed_at: datetime | None
    archived_at: datetime | None

    @classmethod
    def of(cls, v: DocumentVersion) -> "VersionOut":
        error = (
            IngestionErrorOut(
                code=v.failure.code, message=v.failure.message, retryable=v.failure.retryable
            )
            if v.failure and v.status is VersionStatus.FAILED
            else None
        )
        return cls(
            id=v.id,
            version=v.version,
            status=v.status,
            filename=v.filename,
            mime_type=v.mime_type.value,
            size_bytes=v.size_bytes,
            checksum=v.checksum,
            chunk_count=v.chunk_count,
            attempts=v.attempts,
            error=error,
            created_at=v.created_at,
            indexed_at=v.indexed_at,
            archived_at=v.archived_at,
        )


class DocumentOut(_Out):
    id: UUID
    title: str
    department_id: UUID
    created_at: datetime
    current_version_id: UUID | None
    latest_version: VersionOut | None

    @classmethod
    def of(cls, document: Document, latest: DocumentVersion | None) -> "DocumentOut":
        return cls(
            id=document.id,
            title=document.title,
            department_id=document.department_id,
            created_at=document.created_at,
            current_version_id=document.current_version_id,
            latest_version=VersionOut.of(latest) if latest else None,
        )


class DocumentDetailOut(DocumentOut):
    versions: list[VersionOut]

    @classmethod
    def of_detail(cls, detail: DocumentDetail) -> "DocumentDetailOut":
        latest = detail.versions[0] if detail.versions else None
        base = DocumentOut.of(detail.document, latest)
        return cls(**base.model_dump(), versions=[VersionOut.of(v) for v in detail.versions])
