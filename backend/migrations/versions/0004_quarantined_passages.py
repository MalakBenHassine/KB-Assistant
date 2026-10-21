"""Count the passages withheld from search at ingestion.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-21

Paragraphs that read as instructions to the assistant are removed from the
indexed text (ADR-0017); the version records how many, so administrators can
review the original. NULL means the version was indexed before this check.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_document_versions_quarantined_passages_non_negative"


def upgrade() -> None:
    op.add_column(
        "document_versions", sa.Column("quarantined_passages", sa.Integer(), nullable=True)
    )
    # Raw DDL: op.create_check_constraint would apply the metadata naming
    # convention a second time and produce a different name.
    op.execute(
        f"ALTER TABLE document_versions ADD CONSTRAINT {CONSTRAINT}"
        " CHECK (quarantined_passages >= 0)"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE document_versions DROP CONSTRAINT {CONSTRAINT}")
    op.drop_column("document_versions", "quarantined_passages")
