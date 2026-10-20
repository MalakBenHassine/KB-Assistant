"""Documents, versions, chunks, and the pgvector index.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20

Same rules as 0001: every table carries `tenant_id`, has row-level security on
`app.tenant_id`, and the application role gets the least it needs. Chunks and
versions are never deleted by the application (no DELETE grant): archiving is
a flag, and physical deletion is a separate, owner-run retention job.

`documents.current_version_id` has no foreign key: it would make the two
tables mutually dependent. The ingestion use case is the only writer, and it
sets the pointer in the same transaction that marks the version indexed.
"""

import re
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import context, op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSION = 1536
STATUSES = "('pending', 'processing', 'indexed', 'failed')"
TABLES = ("documents", "document_versions", "chunks", "vector_records")


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("app_role", "kb_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"invalid role name: {role!r}")
    return role


def _uuid(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(name, pg.UUID(as_uuid=True), nullable=nullable)


def _ts(name: str, *, nullable: bool = True) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        _uuid("id"),
        _uuid("tenant_id"),
        _uuid("department_id"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("source_type", sa.String(32), server_default=sa.text("'upload'"), nullable=False),
        sa.Column("external_id", sa.String(500), nullable=True),
        _uuid("created_by"),
        _ts("created_at", nullable=False),
        _uuid("current_version_id", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE", name="fk_documents_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["department_id", "tenant_id"],
            ["departments.id", "departments.tenant_id"],
            name="fk_documents_department_id_tenant_id_departments",
        ),
        sa.ForeignKeyConstraint(
            ["created_by", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            name="fk_documents_created_by_tenant_id_memberships",
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_documents_id_tenant_id"),
    )
    op.create_index("ix_documents_tenant_department", "documents", ["tenant_id", "department_id"])

    op.create_table(
        "document_versions",
        _uuid("id"),
        _uuid("document_id"),
        _uuid("tenant_id"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("blob_key", sa.String(300), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_retryable", sa.Boolean(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("chunker_version", sa.String(32), nullable=True),
        sa.Column("embedding_model", sa.String(100), nullable=True),
        sa.Column("embedding_tokens", sa.Integer(), nullable=True),
        _uuid("created_by"),
        _ts("created_at", nullable=False),
        _ts("processing_started_at"),
        _ts("indexed_at"),
        _ts("archived_at"),
        _ts("vectors_deactivated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["documents.id", "documents.tenant_id"],
            ondelete="CASCADE",
            name="fk_document_versions_document_id_tenant_id_documents",
        ),
        sa.UniqueConstraint(
            "document_id", "version", name="uq_document_versions_document_id_version"
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_document_versions_id_tenant_id"),
        sa.CheckConstraint(f"status IN {STATUSES}", name="ck_document_versions_status_known"),
    )
    op.create_index(
        "ix_document_versions_tenant_checksum", "document_versions", ["tenant_id", "checksum"]
    )

    op.create_table(
        "chunks",
        _uuid("id"),
        sa.Column("vector_id", sa.String(80), nullable=False),
        _uuid("tenant_id"),
        _uuid("department_id"),
        _uuid("document_id"),
        _uuid("document_version_id"),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("heading_path", pg.ARRAY(sa.Text()), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        sa.ForeignKeyConstraint(
            ["document_version_id", "tenant_id"],
            ["document_versions.id", "document_versions.tenant_id"],
            ondelete="CASCADE",
            name="fk_chunks_document_version_id_tenant_id_document_versions",
        ),
        sa.UniqueConstraint("vector_id", name="uq_chunks_vector_id"),
        sa.UniqueConstraint(
            "document_version_id", "chunk_index", name="uq_chunks_document_version_id_chunk_index"
        ),
    )
    op.create_index("ix_chunks_tenant_department", "chunks", ["tenant_id", "department_id"])

    op.create_table(
        "vector_records",
        sa.Column("id", sa.String(80), nullable=False),
        _uuid("tenant_id"),
        _uuid("department_id"),
        _uuid("document_id"),
        _uuid("version_id"),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSION), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_vector_records"),
    )
    op.create_index(
        "ix_vector_records_tenant_version", "vector_records", ["tenant_id", "version_id"]
    )
    op.create_index(
        "ix_vector_records_embedding",
        "vector_records",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    same_tenant = "tenant_id = app_setting_uuid('app.tenant_id')"
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            f" USING ({same_tenant}) WITH CHECK ({same_tenant})"
        )

    role = _app_role()
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON documents, document_versions, chunks TO {role}")
    # The vector index is a projection that may be rebuilt: the adapter may delete from it.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON vector_records TO {role}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    # The extension is left in place: other databases on the server may use it.
