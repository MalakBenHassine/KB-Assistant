"""Table definitions.

Migrations are written by hand (`migrations/versions/`), because row-level
security policies and grants are not something autogenerate understands; this
module mirrors them for queries, and a test checks the two never drift.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ROLE_VALUES = ("employee", "dept_admin", "tenant_admin")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        UUID: PG_UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
    }


class TenantRow(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    token_version: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)


class DepartmentRow(Base):
    __tablename__ = "departments"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(63))
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint("tenant_id", "slug"),
        # Target of composite foreign keys that pin a row to the same tenant.
        UniqueConstraint("id", "tenant_id"),
    )


class MembershipRow(Base):
    __tablename__ = "memberships"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_used_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id"),
        UniqueConstraint("id", "tenant_id"),
        CheckConstraint(f"role IN {ROLE_VALUES}", name="role_known"),
    )


class MembershipDepartmentRow(Base):
    __tablename__ = "membership_departments"

    membership_id: Mapped[UUID] = mapped_column(primary_key=True)
    department_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]

    __table_args__ = (
        # Composite keys: the membership and the department must share the row's tenant.
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["department_id", "tenant_id"],
            ["departments.id", "departments.tenant_id"],
            ondelete="CASCADE",
        ),
    )


class InvitationRow(Base):
    __tablename__ = "invitations"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(32))
    department_ids: Mapped[list[UUID]] = mapped_column(ARRAY(PG_UUID(as_uuid=True)))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None]
    invited_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["invited_by", "tenant_id"], ["memberships.id", "memberships.tenant_id"]
        ),
        CheckConstraint(f"role IN {ROLE_VALUES}", name="role_known"),
    )


class RefreshTokenRow(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    family_id: Mapped[UUID]
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tenant_id: Mapped[UUID]
    membership_id: Mapped[UUID]
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    issued_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    family_expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    replaced_by: Mapped[UUID | None]

    __table_args__ = (
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
        ),
        Index("ix_refresh_tokens_family_id", "family_id"),
    )


class AuditLogRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    actor_user_id: Mapped[UUID | None]
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str | None] = mapped_column(String(200))
    ip: Mapped[str | None] = mapped_column(INET)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (Index("ix_audit_log_tenant_created", "tenant_id", "created_at"),)


# ----------------------------------------------------------------------------- documents

VERSION_STATUSES = ("pending", "processing", "indexed", "failed")
# Fixed by the index (an HNSW index needs a fixed dimension). Changing the
# embedding model's dimension is a migration plus a re-ingestion.
EMBEDDING_DIMENSION = 1536


class DocumentRow(Base):
    __tablename__ = "documents"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    department_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(String(300))
    source_type: Mapped[str] = mapped_column(String(32), server_default=text("'upload'"))
    external_id: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[UUID]
    created_at: Mapped[datetime]
    current_version_id: Mapped[UUID | None]

    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        ForeignKeyConstraint(
            ["department_id", "tenant_id"], ["departments.id", "departments.tenant_id"]
        ),
        ForeignKeyConstraint(
            ["created_by", "tenant_id"], ["memberships.id", "memberships.tenant_id"]
        ),
        Index("ix_documents_tenant_department", "tenant_id", "department_id"),
    )


class DocumentVersionRow(Base):
    __tablename__ = "document_versions"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID]
    tenant_id: Mapped[UUID]
    version: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    blob_key: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_retryable: Mapped[bool | None]
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    chunk_count: Mapped[int | None]
    chunker_version: Mapped[str | None] = mapped_column(String(32))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_tokens: Mapped[int | None]
    created_by: Mapped[UUID]
    created_at: Mapped[datetime]
    processing_started_at: Mapped[datetime | None]
    indexed_at: Mapped[datetime | None]
    archived_at: Mapped[datetime | None]
    vectors_deactivated_at: Mapped[datetime | None]

    __table_args__ = (
        UniqueConstraint("document_id", "version"),
        UniqueConstraint("id", "tenant_id"),
        ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["documents.id", "documents.tenant_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(f"status IN {VERSION_STATUSES}", name="status_known"),
        Index("ix_document_versions_tenant_checksum", "tenant_id", "checksum"),
    )


class ChunkRow(Base):
    __tablename__ = "chunks"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    vector_id: Mapped[str] = mapped_column(String(80), unique=True)
    tenant_id: Mapped[UUID]
    department_id: Mapped[UUID]
    document_id: Mapped[UUID]
    document_version_id: Mapped[UUID]
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text))
    page_start: Mapped[int | None]
    page_end: Mapped[int | None]
    token_count: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("document_version_id", "chunk_index"),
        ForeignKeyConstraint(
            ["document_version_id", "tenant_id"],
            ["document_versions.id", "document_versions.tenant_id"],
            ondelete="CASCADE",
        ),
        Index("ix_chunks_tenant_department", "tenant_id", "department_id"),
    )


class VectorRecordRow(Base):
    """The pgvector adapter's index (ADR-0002); Pinecone holds the same records in production."""

    __tablename__ = "vector_records"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[UUID]
    department_id: Mapped[UUID]
    document_id: Mapped[UUID]
    version_id: Mapped[UUID]
    chunk_index: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None]
    is_active: Mapped[bool] = mapped_column(Boolean)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIMENSION))

    __table_args__ = (
        Index("ix_vector_records_tenant_version", "tenant_id", "version_id"),
        Index(
            "ix_vector_records_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
