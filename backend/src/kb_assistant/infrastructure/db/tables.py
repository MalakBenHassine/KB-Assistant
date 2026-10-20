"""Table definitions.

Migrations are written by hand (`migrations/versions/`), because row-level
security policies and grants are not something autogenerate understands; this
module mirrors them for queries, and a test checks the two never drift.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
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
