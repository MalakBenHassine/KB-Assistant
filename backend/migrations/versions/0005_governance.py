"""Feedback, usage and budgets, a tamper-evident audit log, searched scope on messages.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21

- `feedback`: one rating per person and answer, updatable by its author.
- `tenant_settings`: per-tenant limits; a missing row means the defaults.
- `tenant_usage`: tokens spent per tenant and month, incremented atomically.
- `audit_log` gains `seq`, `prev_hash` and `hash`: a per-tenant hash chain, so a
  deleted or edited row breaks verification (SECURITY.md T11, ADR-0019). Rows
  written before this migration have no hash and are outside the chain.
- `messages.scope_department_ids`: the departments a question searched, so
  analytics can be scoped to a department admin's departments.
"""

import re
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("feedback", "tenant_settings", "tenant_usage")
FEEDBACK_REASONS = "('incorrect', 'incomplete', 'not_relevant', 'outdated', 'other')"


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("app_role", "kb_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"invalid role name: {role!r}")
    return role


def _uuid(name: str) -> sa.Column[Any]:
    return sa.Column(name, pg.UUID(as_uuid=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        "feedback",
        _uuid("id"),
        _uuid("tenant_id"),
        _uuid("message_id"),
        _uuid("membership_id"),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_feedback"),
        sa.UniqueConstraint(
            "message_id", "membership_id", name="uq_feedback_message_id_membership_id"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            ondelete="CASCADE",
            name="fk_feedback_message_id_messages",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_feedback_membership_id_tenant_id_memberships",
        ),
        sa.CheckConstraint("rating IN (-1, 1)", name="ck_feedback_rating_known"),
        sa.CheckConstraint(
            f"reason IS NULL OR reason IN {FEEDBACK_REASONS}", name="ck_feedback_reason_known"
        ),
        sa.CheckConstraint("length(comment) <= 1000", name="ck_feedback_comment_length"),
    )
    op.create_index("ix_feedback_tenant_created", "feedback", ["tenant_id", "created_at"])

    op.create_table(
        "tenant_settings",
        _uuid("tenant_id"),
        sa.Column("monthly_token_budget", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenant_settings"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_tenant_settings_tenant_id_tenants",
        ),
        sa.CheckConstraint(
            "monthly_token_budget IS NULL OR monthly_token_budget >= 0",
            name="ck_tenant_settings_budget_non_negative",
        ),
    )

    op.create_table(
        "tenant_usage",
        _uuid("tenant_id"),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("questions", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "month", name="pk_tenant_usage"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_tenant_usage_tenant_id_tenants",
        ),
    )

    op.add_column("audit_log", sa.Column("seq", sa.BigInteger(), nullable=True))
    op.add_column("audit_log", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column("audit_log", sa.Column("hash", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_audit_log_tenant_id_seq", "audit_log", ["tenant_id", "seq"])

    op.add_column(
        "messages",
        sa.Column(
            "scope_department_ids",
            pg.ARRAY(pg.UUID(as_uuid=True)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.create_index("ix_messages_tenant_created", "messages", ["tenant_id", "created_at"])

    same_tenant = "tenant_id = app_setting_uuid('app.tenant_id')"
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            f" USING ({same_tenant}) WITH CHECK ({same_tenant})"
        )
    role = _app_role()
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON feedback, tenant_settings, tenant_usage TO {role}")


def downgrade() -> None:
    op.drop_index("ix_messages_tenant_created", table_name="messages")
    op.drop_column("messages", "scope_department_ids")
    op.drop_constraint("uq_audit_log_tenant_id_seq", "audit_log")
    for column in ("hash", "prev_hash", "seq"):
        op.drop_column("audit_log", column)
    for table in reversed(TABLES):
        op.drop_table(table)
