"""Conversations and messages, with everything needed to explain an answer.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20

Each assistant message stores the chunks retrieved and cited, their scores,
the model, the prompt version, the thresholds in effect and the latency
(ARCHITECTURE §1, goal 3). Messages are append-only for the application role.
"""

import re
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("conversations", "messages")


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("app_role", "kb_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"invalid role name: {role!r}")
    return role


def _uuid(name: str) -> sa.Column[Any]:
    return sa.Column(name, pg.UUID(as_uuid=True), nullable=False)


def _jsonb(name: str) -> sa.Column[Any]:
    return sa.Column(name, pg.JSONB(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "conversations",
        _uuid("id"),
        _uuid("tenant_id"),
        _uuid("membership_id"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_conversations_id_tenant_id"),
        sa.ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_conversations_membership_id_tenant_id_memberships",
        ),
    )
    op.create_index(
        "ix_conversations_member_updated", "conversations", ["membership_id", "updated_at"]
    )

    op.create_table(
        "messages",
        _uuid("id"),
        _uuid("tenant_id"),
        _uuid("conversation_id"),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("intent", sa.String(32), nullable=True),
        sa.Column("grade", sa.String(16), nullable=True),
        sa.Column("abstained", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("abstain_reason", sa.String(64), nullable=True),
        sa.Column("retrieved_chunk_ids", pg.ARRAY(pg.UUID(as_uuid=True)), nullable=False),
        sa.Column("cited_chunk_ids", pg.ARRAY(pg.UUID(as_uuid=True)), nullable=False),
        _jsonb("citations"),
        _jsonb("scores"),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        _jsonb("thresholds"),
        _jsonb("token_usage"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.ForeignKeyConstraint(
            ["conversation_id", "tenant_id"],
            ["conversations.id", "conversations.tenant_id"],
            ondelete="CASCADE",
            name="fk_messages_conversation_id_tenant_id_conversations",
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role_known"),
    )
    op.create_index(
        "ix_messages_conversation_created", "messages", ["conversation_id", "created_at"]
    )

    same_tenant = "tenant_id = app_setting_uuid('app.tenant_id')"
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            f" USING ({same_tenant}) WITH CHECK ({same_tenant})"
        )
    role = _app_role()
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON conversations TO {role}")
    op.execute(f"GRANT SELECT, INSERT ON messages TO {role}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
