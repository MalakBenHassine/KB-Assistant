"""Documents, versions, chunks, and what the vector store holds about them."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from kb_assistant.domain.ids import (
    ChunkId,
    DepartmentId,
    DocumentId,
    MembershipId,
    TenantId,
    VersionId,
)


class MimeType(StrEnum):
    PDF = "application/pdf"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    MARKDOWN = "text/markdown"
    HTML = "text/html"


class VersionStatus(StrEnum):
    """`pending → processing → indexed | failed`; `failed → pending` on retry."""

    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE_ROW = "table_row"
    CODE = "code"


@dataclass(frozen=True, slots=True)
class Block:
    """One structural unit produced by a parser, in reading order."""

    kind: BlockKind
    text: str
    page: int | None = None
    level: int = 0  # heading level (1..6); 0 for non-headings


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    blocks: tuple[Block, ...]
    page_count: int | None = None


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """A chunk before it has an identity: the chunker's output."""

    index: int
    text: str
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    token_count: int

    @property
    def embedding_text(self) -> str:
        """What is embedded: the heading path gives short chunks their missing context."""
        if not self.heading_path:
            return self.text
        return " \u203a ".join(self.heading_path) + "\n\n" + self.text


@dataclass(frozen=True, slots=True)
class IngestionFailure:
    code: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class Document:
    id: DocumentId
    tenant_id: TenantId
    department_id: DepartmentId
    title: str
    created_by: MembershipId
    created_at: datetime
    current_version_id: VersionId | None


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    id: VersionId
    document_id: DocumentId
    tenant_id: TenantId
    version: int
    checksum: str
    filename: str
    mime_type: MimeType
    size_bytes: int
    blob_key: str
    status: VersionStatus
    failure: IngestionFailure | None
    attempts: int
    chunk_count: int | None
    embedding_model: str | None
    embedding_tokens: int | None
    created_by: MembershipId
    created_at: datetime
    processing_started_at: datetime | None
    indexed_at: datetime | None
    archived_at: datetime | None
    # Paragraphs removed at ingestion as instructions to the assistant (ADR-0017).
    quarantined_passages: int | None = None


@dataclass(frozen=True, slots=True)
class Chunk:
    id: ChunkId
    vector_id: str
    tenant_id: TenantId
    department_id: DepartmentId
    document_id: DocumentId
    version_id: VersionId
    chunk_index: int
    text: str
    text_hash: str
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    token_count: int


def vector_id(version_id: VersionId, chunk_index: int) -> str:
    """Deterministic: re-running an ingestion overwrites, never duplicates."""
    return f"{version_id}:{chunk_index}"


@dataclass(frozen=True, slots=True)
class VectorRecord:
    id: str
    values: tuple[float, ...]
    tenant_id: TenantId
    department_id: DepartmentId
    document_id: DocumentId
    version_id: VersionId
    chunk_index: int
    page: int | None


@dataclass(frozen=True, slots=True)
class VectorMatch:
    id: str
    score: float


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    """What a query may search: one tenant, some departments (ADR-0003).

    The vector-store port accepts nothing else, so a raw client-supplied list
    can never reach it. Built by the authorization service from a `Principal`.
    """

    tenant_id: TenantId
    department_ids: frozenset[DepartmentId]


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: tuple[tuple[float, ...], ...]
    total_tokens: int
