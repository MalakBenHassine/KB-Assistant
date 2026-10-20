"""Identity, tenancy, refresh tokens and audit log, with row-level security.

Revision ID: 0001
Revises:
Create Date: 2026-10-20

Every tenant-scoped table gets a policy on `app.tenant_id`, a transaction-local
setting written by the API (see `infrastructure/db/uow.py`). Two narrow
exceptions let a transaction see rows before it knows the tenant:

- `memberships`: a user's own memberships in every tenant, when
  `app.cross_tenant_user_id` is set (login, profile);
- `refresh_tokens`, `invitations`: the single row whose hash the caller already
  holds, when `app.token_hash` is set (refresh, logout, register).

`users` and `tenants` are platform-level and carry no tenant data.
The application role is granted the minimum: SELECT on tenants, no DELETE on
users, INSERT-only on the audit log.
"""

import re
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = "('employee', 'dept_admin', 'tenant_admin')"
TENANT_TABLES = (
    "departments",
    "memberships",
    "membership_departments",
    "invitations",
    "refresh_tokens",
    "audit_log",
)


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("app_role", "kb_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"invalid role name: {role!r}")
    return role


def _uuid_pk() -> sa.Column[Any]:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True)


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "tenants",
        _uuid_pk(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("token_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),
    )
    op.create_table(
        "departments",
        _uuid_pk(),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_departments"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_departments_tenant_id_tenants",
        ),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_departments_tenant_id_slug"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_departments_id_tenant_id"),
    )
    op.create_table(
        "memberships",
        _uuid_pk(),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_memberships_user_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_memberships_tenant_id_tenants",
        ),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_memberships_user_id_tenant_id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_memberships_id_tenant_id"),
        sa.CheckConstraint(f"role IN {ROLES}", name="ck_memberships_role_known"),
    )
    op.create_table(
        "membership_departments",
        sa.Column("membership_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("department_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("membership_id", "department_id", name="pk_membership_departments"),
        sa.ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_membership_departments_membership_id_tenant_id_memberships",
        ),
        sa.ForeignKeyConstraint(
            ["department_id", "tenant_id"],
            ["departments.id", "departments.tenant_id"],
            ondelete="CASCADE",
            name="fk_membership_departments_department_id_tenant_id_departments",
        ),
    )
    op.create_table(
        "invitations",
        _uuid_pk(),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("department_ids", pg.ARRAY(pg.UUID(as_uuid=True)), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by", pg.UUID(as_uuid=True), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_invitations"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_invitations_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            name="fk_invitations_invited_by_tenant_id_memberships",
        ),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
        sa.CheckConstraint(f"role IN {ROLES}", name="ck_invitations_role_known"),
    )
    op.create_table(
        "refresh_tokens",
        _uuid_pk(),
        sa.Column("family_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("family_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by", pg.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_refresh_tokens_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_refresh_tokens_membership_id_tenant_id_memberships",
        ),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])
    op.create_table(
        "audit_log",
        _uuid_pk(),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target", sa.String(200), nullable=True),
        sa.Column("ip", pg.INET(), nullable=True),
        sa.Column("details", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_audit_log_tenant_id_tenants",
        ),
    )
    op.create_index("ix_audit_log_tenant_created", "audit_log", ["tenant_id", "created_at"])

    _row_level_security()
    _grants(_app_role())


def _row_level_security() -> None:
    # An unset or reset setting reads as '' (not NULL) once defined in a session;
    # NULLIF makes both mean "no value", which matches no row.
    # One statement per call: asyncpg prepares statements and refuses several at once.
    op.execute(
        "CREATE FUNCTION app_setting_uuid(name text) RETURNS uuid LANGUAGE sql STABLE"
        " AS $$ SELECT NULLIF(current_setting(name, true), '')::uuid $$"
    )
    op.execute(
        "CREATE FUNCTION app_setting_text(name text) RETURNS text LANGUAGE sql STABLE"
        " AS $$ SELECT NULLIF(current_setting(name, true), '') $$"
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

    same_tenant = "tenant_id = app_setting_uuid('app.tenant_id')"
    for table in ("departments", "membership_departments", "audit_log"):
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            f" USING ({same_tenant}) WITH CHECK ({same_tenant})"
        )
    op.execute(
        "CREATE POLICY tenant_isolation ON memberships"
        f" USING ({same_tenant} OR user_id = app_setting_uuid('app.cross_tenant_user_id'))"
        f" WITH CHECK ({same_tenant})"
    )
    for table in ("invitations", "refresh_tokens"):
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            f" USING ({same_tenant} OR token_hash = app_setting_text('app.token_hash'))"
            f" WITH CHECK ({same_tenant})"
        )


def _grants(role: str) -> None:
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    op.execute(f"GRANT SELECT ON tenants TO {role}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON users TO {role}")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON departments, memberships,"
        f" membership_departments, invitations, refresh_tokens TO {role}"
    )
    # Append-only: the API can write audit entries, never change or remove them.
    op.execute(f"GRANT SELECT, INSERT ON audit_log TO {role}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION app_setting_uuid(text), app_setting_text(text) TO {role}"
    )


def downgrade() -> None:
    for table in reversed(("tenants", "users", *TENANT_TABLES)):
        op.drop_table(table)
    op.execute("DROP FUNCTION IF EXISTS app_setting_uuid(text)")
    op.execute("DROP FUNCTION IF EXISTS app_setting_text(text)")
